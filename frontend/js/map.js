/*
 * Map setup, shared state and the main "generate" flow.
 *
 * GWR is the one global namespace shared by map.js, ahp.js and temporal.js.
 * ahp.js fills GWR.weights; temporal.js fills GWR.state.season.
 */
"use strict";

const GWR = (window.GWR = {
  config: null,
  state: {
    regionType: "study_area",
    regionId: "study_area",
    season: "annual",
    lastResult: null,
  },
  weights: {},          // set by ahp.js
  map: null,
  layers: {},           // Leaflet layers by name
  onReady: [],          // callbacks run after config has loaded
});

/* ---------------------------------------------------------------- API ---- */
GWR.api = async function (path, options = {}) {
  const opts = { headers: { "Content-Type": "application/json" }, ...options };
  if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const err = await res.json();
      if (err.detail) msg = typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail);
    } catch (_) { /* not JSON */ }
    throw new Error(msg);
  }
  return res;
};

GWR.analysisRequest = function (season) {
  return {
    region_type: GWR.state.regionType,
    region_id: GWR.state.regionId,
    season: season || GWR.state.season,
    weights: GWR.weights,
  };
};

GWR.setStatus = function (text, isError = false) {
  const el = document.getElementById("status");
  el.textContent = text;
  el.classList.toggle("error", isError);
};

GWR.escape = function (s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
};

/* ---------------------------------------------------------------- Map ---- */
function initMap() {
  const cfg = GWR.config;
  const map = L.map("map", { zoomControl: true }).setView(cfg.map_center, 8);
  GWR.map = map;

  const osm = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  const imagery = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 18, attribution: "Imagery &copy; Esri" });
  const topo = L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
    maxZoom: 17, attribution: "&copy; OpenTopoMap (CC-BY-SA)" });

  const [minx, miny, maxx, maxy] = cfg.study_area_bbox;
  L.rectangle([[miny, minx], [maxy, maxx]], {
    color: "#444", weight: 1, dashArray: "4 4", fill: false, interactive: false,
  }).addTo(map);
  map.fitBounds([[miny, minx], [maxy, maxx]]);

  // Recharge zones (filled after each analysis)
  GWR.layers.zones = L.geoJSON(null, {
    style: (f) => ({ color: f.properties.color, fillColor: f.properties.color,
                     weight: 0, fillOpacity: 0.75 }),
    onEachFeature: (f, layer) => layer.bindTooltip(f.properties.name, { sticky: true }),
  }).addTo(map);

  // Outline of the selected region
  GWR.layers.region = L.geoJSON(null, {
    style: { color: "#111", weight: 2, fill: false }, interactive: false,
  }).addTo(map);

  // Thematic layers: loaded lazily the first time they are switched on
  const overlays = { "Recharge potential zones": GWR.layers.zones };
  cfg.layers.forEach((layer) => {
    const lyr = L.geoJSON(null, {
      style: (f) => ({ color: f.properties.color, fillColor: f.properties.color,
                       weight: 0, fillOpacity: 0.7 }),
      onEachFeature: (f, l) =>
        l.bindTooltip(`${layer.label}: ${f.properties.label} (rank ${f.properties.rank})`, { sticky: true }),
    });
    lyr._gwrLayer = layer.id;
    GWR.layers[layer.id] = lyr;
    overlays[`${layer.label} (rank)`] = lyr;
  });

  L.control.layers({ "OpenStreetMap": osm, "Satellite": imagery, "Topographic": topo },
                   overlays, { collapsed: true }).addTo(map);
  L.control.scale({ imperial: false }).addTo(map);
  addLegend(map);

  map.on("overlayadd", (e) => {
    if (e.layer._gwrLayer) loadThematicLayer(e.layer);
  });
}

function addLegend(map) {
  const legend = L.control({ position: "bottomright" });
  legend.onAdd = function () {
    const div = L.DomUtil.create("div", "legend");
    const rows = Object.keys(GWR.config.classes)
      .sort((a, b) => b - a)
      .map((k) => {
        const c = GWR.config.classes[k];
        return `<div><span class="swatch" style="background:${c.color}"></span>${GWR.escape(c.name)}</div>`;
      });
    div.innerHTML = `<h4>Recharge potential</h4>${rows.join("")}
      <div class="note">Thematic layers use the same colours<br>for ranks 1 (poor) to 5 (excellent).</div>`;
    return div;
  };
  legend.addTo(map);
}

async function loadThematicLayer(lyr) {
  const id = lyr._gwrLayer;
  const params = new URLSearchParams({
    region_type: GWR.state.regionType,
    region_id: GWR.state.regionId,
    season: GWR.state.season,
  });
  const key = params.toString();
  if (lyr._gwrKey === key) return;          // already loaded for this region/season
  GWR.setStatus(`Loading ${id.replace(/_/g, " ")} layer...`);
  try {
    const res = await GWR.api(`/layers/${id}?${key}`);
    const gj = await res.json();
    lyr.clearLayers();
    lyr.addData(gj);
    lyr._gwrKey = key;
    GWR.setStatus("");
  } catch (err) {
    GWR.setStatus(`Could not load layer: ${err.message}`, true);
  }
}

/* Reload any thematic layer currently on the map (after region/season change). */
GWR.refreshThematicLayers = function () {
  GWR.config.layers.forEach((l) => {
    const lyr = GWR.layers[l.id];
    if (GWR.map.hasLayer(lyr)) loadThematicLayer(lyr);
  });
};

/* ------------------------------------------------------------- Regions ---- */
async function loadRegionList() {
  const res = await GWR.api("/api/regions");
  GWR.regions = await res.json();
  fillRegionSelect();
}

function fillRegionSelect() {
  const type = GWR.state.regionType;
  const select = document.getElementById("region-id");
  const items = (GWR.regions && GWR.regions[type]) || [];
  select.innerHTML = items.length
    ? items.map((r) => `<option value="${GWR.escape(r.id)}">${GWR.escape(r.name)}</option>`).join("")
    : `<option value="">No ${type} boundaries found</option>`;
  select.disabled = items.length === 0;
  GWR.state.regionId = items.length ? items[0].id : "";
  showRegionOutline();
}

async function showRegionOutline() {
  GWR.layers.region.clearLayers();
  if (GWR.state.regionType === "study_area" || !GWR.state.regionId) return;
  try {
    if (!GWR.regionShapes) GWR.regionShapes = {};
    const type = GWR.state.regionType;
    if (!GWR.regionShapes[type]) {
      const res = await GWR.api(`/api/regions/${type}`);
      GWR.regionShapes[type] = await res.json();
    }
    const feat = GWR.regionShapes[type].features.find((f) => f.properties.id === GWR.state.regionId);
    if (feat) {
      GWR.layers.region.addData(feat);
      GWR.map.fitBounds(GWR.layers.region.getBounds(), { padding: [20, 20] });
    }
  } catch (err) {
    console.warn("Region outline failed", err);
  }
}

/* -------------------------------------------------------------- Analysis ---- */
GWR.runAnalysis = async function () {
  const btn = document.getElementById("run");
  if (!GWR.state.regionId) {
    GWR.setStatus("Pick a region first.", true);
    return;
  }
  btn.disabled = true;
  GWR.setStatus("Running weighted overlay...");
  try {
    const res = await GWR.api("/analyze", { method: "POST", body: GWR.analysisRequest() });
    const data = await res.json();
    GWR.state.lastResult = data;
    GWR.layers.zones.clearLayers();
    GWR.layers.zones.addData(data.geojson);
    if (!GWR.map.hasLayer(GWR.layers.zones)) GWR.layers.zones.addTo(GWR.map);
    if (data.geojson.features.length) {
      GWR.map.fitBounds(GWR.layers.zones.getBounds(), { padding: [20, 20] });
    }
    renderStats(data);
    GWR.setStatus(`Done. Pixel size ${data.pixel_size_m} m, ${data.layers_used.length} layers.`);
  } catch (err) {
    GWR.setStatus(err.message, true);
  } finally {
    btn.disabled = false;
  }
};

function renderStats(data) {
  document.getElementById("results").hidden = false;
  document.getElementById("result-title").textContent = `${data.region.name}, ${data.season_label}`;
  const body = document.querySelector("#stats-table tbody");
  body.innerHTML = data.stats.slice().reverse().map((s) => `
    <tr><td><span class="swatch" style="background:${s.color}"></span>${GWR.escape(s.name)}</td>
        <td>${s.area_km2.toFixed(1)}</td><td>${s.percent.toFixed(1)}%</td></tr>`).join("");
}

async function download(fmt) {
  GWR.setStatus(`Preparing ${fmt.toUpperCase()}...`);
  try {
    const res = await GWR.api(`/export/${fmt}`, { method: "POST", body: GWR.analysisRequest() });
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    const match = cd.match(/filename="([^"]+)"/);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = match ? match[1] : `recharge.${fmt === "pdf" ? "pdf" : "tif"}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    GWR.setStatus("");
  } catch (err) {
    GWR.setStatus(`Download failed: ${err.message}`, true);
  }
}

/* ---------------------------------------------------------------- Init ---- */
document.addEventListener("DOMContentLoaded", async () => {
  try {
    GWR.config = await (await GWR.api("/api/config")).json();
  } catch (err) {
    GWR.setStatus(`Cannot reach the backend: ${err.message}`, true);
    return;
  }
  GWR.state.season = GWR.config.default_season;
  initMap();
  GWR.onReady.forEach((fn) => fn());

  document.getElementById("region-type").addEventListener("change", (e) => {
    GWR.state.regionType = e.target.value;
    fillRegionSelect();
    GWR.refreshThematicLayers();
  });
  document.getElementById("region-id").addEventListener("change", (e) => {
    GWR.state.regionId = e.target.value;
    showRegionOutline();
    GWR.refreshThematicLayers();
  });
  document.getElementById("run").addEventListener("click", GWR.runAnalysis);
  document.getElementById("download-tif").addEventListener("click", () => download("geotiff"));
  document.getElementById("download-pdf").addEventListener("click", () => download("pdf"));

  try {
    await loadRegionList();
    const status = await (await GWR.api("/api/status")).json();
    if (!status.ready) {
      GWR.setStatus("Some layers are not processed yet. Run `python -m backend.pipeline all` first.", true);
    }
  } catch (err) {
    GWR.setStatus(err.message, true);
  }
});
