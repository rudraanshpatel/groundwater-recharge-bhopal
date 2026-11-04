/*
 * Season toggle: Annual / Pre-monsoon / Monsoon / Post-monsoon.
 *
 * Switching season re-runs the analysis if a map is already shown, so the
 * change in recharge zones is visible at once. "Compare all seasons" runs
 * the four seasons for the current region and tabulates the class shares.
 * Class breaks are fixed from the annual map, so the seasons are comparable.
 */
"use strict";

(function () {
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function seasonNote(id) {
    const s = GWR.config.seasons[id];
    const months = s.months.length === 12 ? "all months" : s.months.map((m) => MONTHS[m - 1]).join(", ");
    return `Rainfall: mean seasonal total over ${months}. Other layers are static.`;
  }

  function buildButtons() {
    const box = document.getElementById("season-buttons");
    box.innerHTML = Object.entries(GWR.config.seasons).map(([id, s]) => `
      <button type="button" role="radio" data-season="${id}"
              aria-checked="${id === GWR.state.season}">${GWR.escape(s.label.replace(/ \(.*\)/, ""))}</button>`).join("");
    box.querySelectorAll("button").forEach((b) =>
      b.addEventListener("click", () => selectSeason(b.dataset.season)));
    document.getElementById("season-note").textContent = seasonNote(GWR.state.season);
  }

  function selectSeason(id) {
    if (id === GWR.state.season) return;
    GWR.state.season = id;
    document.querySelectorAll("#season-buttons button").forEach((b) =>
      b.setAttribute("aria-checked", String(b.dataset.season === id)));
    document.getElementById("season-note").textContent = seasonNote(id);
    GWR.refreshThematicLayers();
    if (GWR.state.lastResult) GWR.runAnalysis();
  }

  async function compareSeasons() {
    const btn = document.getElementById("compare-seasons");
    const out = document.getElementById("season-compare");
    btn.disabled = true;
    out.innerHTML = '<p class="note">Running all seasons...</p>';
    try {
      const ids = Object.keys(GWR.config.seasons);
      const results = await Promise.all(ids.map(async (id) => {
        const body = { ...GWR.analysisRequest(id), simplify: true };
        return (await GWR.api("/analyze", { method: "POST", body })).json();
      }));
      const classes = Object.keys(GWR.config.classes).sort((a, b) => b - a);
      const head = `<tr><th>Class</th>${ids.map((id) =>
        `<th>${GWR.escape(GWR.config.seasons[id].label.replace(/ \(.*\)/, "").replace("-monsoon", ""))}</th>`).join("")}</tr>`;
      const rows = classes.map((c) => {
        const meta = GWR.config.classes[c];
        const cells = results.map((r) => {
          const s = r.stats.find((x) => String(x.class) === c);
          return `<td>${s ? s.percent.toFixed(1) : "0.0"}%</td>`;
        }).join("");
        return `<tr><td><span class="swatch" style="background:${meta.color}"></span>${GWR.escape(meta.name)}</td>${cells}</tr>`;
      }).join("");
      out.innerHTML = `<table>${head}${rows}</table>
        <p class="note">Share of the region in each class. Breaks are the same for every season.</p>`;
    } catch (err) {
      out.innerHTML = `<p class="note error">${GWR.escape(err.message)}</p>`;
    } finally {
      btn.disabled = false;
    }
  }

  GWR.onReady.push(() => {
    buildButtons();
    document.getElementById("compare-seasons").addEventListener("click", compareSeasons);
  });
})();
