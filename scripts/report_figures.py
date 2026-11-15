"""Make the report's map figures and fill its zone-area tables.

Run this after the pipeline (`python -m backend.pipeline all` and
`python -m backend.pipeline validate`). It reads the processed rasters and
writes, by default into data/outputs/report/:

    tables.md                    Tables 4.2 and 4.3 (and a tehsil-wise table),
                                 ready to paste into the report
    table_4_2_zone_areas.csv     annual zone areas
    table_4_3_seasonal.csv       seasonal rainfall and zone shares
    tehsil_zone_areas.csv        area of every zone in every tehsil
    fig_1_2_location_map.png     study area: districts, tehsils, water bodies
    fig_3_2_rainfall.png ... fig_3_8_lineament_density.png   thematic layers
    fig_4_1_recharge_annual.png  recharge potential zones (annual)
    fig_4_2_recharge_seasonal.png  pre-monsoon, monsoon, post-monsoon
    fig_4_3_wells.png            validation wells over the annual zones
    fig_4_4_validation_boxplot.png  copy of outputs/validation_boxplot.png
    fig_zone_area_bar.png        zone areas by season (bar chart)

All areas and shares are computed inside the union of the tehsil boundaries
(the three districts), at the full working resolution. The rasters cover a
rectangular buffer around the districts, so the pipeline's own
zone_statistics.json (whole rectangle) gives slightly different numbers.

Usage (from the repository root):

    python scripts/report_figures.py
    python scripts/report_figures.py --out D:/report/figures --dpi 300
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from matplotlib.colors import BoundaryNorm, ListedColormap  # noqa: E402
from matplotlib import patheffects  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402
from pyproj import Transformer  # noqa: E402
from rasterio.enums import Resampling  # noqa: E402
from rasterio.features import rasterize  # noqa: E402
from rasterio.transform import from_bounds  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import config  # noqa: E402

SEASON_ORDER = ["annual", "pre_monsoon", "monsoon", "post_monsoon"]
SEASON_LABELS = {
    "annual": "Annual",
    "pre_monsoon": "Pre-monsoon (Mar–May)",
    "monsoon": "Monsoon (Jun–Sep)",
    "post_monsoon": "Post-monsoon (Oct–Dec)",
}
ZONE_CODES = list(config.RECHARGE_CLASSES)
ZONE_NAMES = [config.RECHARGE_CLASSES[c]["name"] for c in ZONE_CODES]
ZONE_COLORS = [config.RECHARGE_CLASSES[c]["color"] for c in ZONE_CODES]

# ESA WorldCover legend colours
WORLDCOVER_COLORS = {
    10: "#006400", 20: "#ffbb22", 30: "#ffff4c", 40: "#f096ff", 50: "#fa0000",
    60: "#b4b4b4", 70: "#f0f0f0", 80: "#0064c8", 90: "#0096a0", 95: "#00cf75",
    100: "#fae6a0",
}
GEOLOGY_GROUPS = {
    5: ("Alluvium, valley sand", "#2c7bb6"),
    4: ("Laterite, weathered / fractured rock", "#abd9e9"),
    3: ("Deccan basalt, sandstone, limestone", "#e6d8a8"),
    2: ("Vindhyan shale, quartzite, tuff", "#fdae61"),
    1: ("Granite, gneiss, dolerite, clay", "#d7191c"),
}
BHOPAL_CITY = (77.4126, 23.2599)   # lon, lat
WATER_COLOR = "#3a7dc9"

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
    "font.size": 9,
    "axes.titlesize": 10,
    "legend.fontsize": 8,
    "legend.title_fontsize": 8,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.08,
})


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------
class Study:
    """Boundaries, the analysis mask and a decimated display grid."""

    def __init__(self, ref_raster: Path, max_display_px: int):
        with rasterio.open(ref_raster) as src:
            self.crs = src.crs
            self.transform = src.transform
            self.shape = (src.height, src.width)
            self.bounds = src.bounds
            self.pixel_area_km2 = abs(src.transform.a * src.transform.e) / 1e6

        tehsils = gpd.read_file(config.TEHSIL_FILE).to_crs(self.crs)
        self.tehsils = tehsils
        name = config.TEHSIL_NAME_FIELD if config.TEHSIL_NAME_FIELD in tehsils else tehsils.columns[0]
        self.tehsil_name = name
        self.districts = tehsils.dissolve(by="NAME_2").reset_index() if "NAME_2" in tehsils else None
        self.area = tehsils.dissolve()

        # Full-resolution mask for statistics
        self.mask = rasterize(((g, 1) for g in self.area.geometry), out_shape=self.shape,
                              transform=self.transform, fill=0, dtype="uint8").astype(bool)

        # Display grid: the boundary extent plus a margin, decimated
        minx, miny, maxx, maxy = self.area.total_bounds
        pad = 0.04 * max(maxx - minx, maxy - miny)
        self.extent_bounds = (minx - pad, miny - pad, maxx + pad, maxy + pad)
        w_m, h_m = self.extent_bounds[2] - self.extent_bounds[0], self.extent_bounds[3] - self.extent_bounds[1]
        scale = max(w_m, h_m) / max_display_px
        self.dshape = (int(round(h_m / scale)), int(round(w_m / scale)))
        self.dtransform = from_bounds(*self.extent_bounds, self.dshape[1], self.dshape[0])
        self.dmask = rasterize(((g, 1) for g in self.area.geometry), out_shape=self.dshape,
                               transform=self.dtransform, fill=0, dtype="uint8").astype(bool)
        self.extent = (self.extent_bounds[0], self.extent_bounds[2],
                       self.extent_bounds[1], self.extent_bounds[3])

    def read_display(self, path: Path, categorical: bool, clip: bool = True) -> np.ndarray:
        """Raster resampled onto the display grid, NaN outside / nodata."""
        from rasterio.warp import reproject
        out = np.full(self.dshape, np.nan, dtype="float32")
        with rasterio.open(path) as src:
            src_nodata = src.nodata
            reproject(
                source=rasterio.band(src, 1), destination=out,
                src_transform=src.transform, src_crs=src.crs, src_nodata=src_nodata,
                dst_transform=self.dtransform, dst_crs=self.crs, dst_nodata=np.nan,
                resampling=Resampling.mode if categorical else Resampling.average,
            )
        if categorical:
            out[out == 0] = np.nan
        if clip:
            out[~self.dmask] = np.nan
        return out


def read_full(path: Path) -> np.ndarray:
    with rasterio.open(path) as src:
        arr = src.read(1)
        nodata = src.nodata
    if np.issubdtype(arr.dtype, np.floating):
        arr = arr.astype("float32")
        if nodata is not None:
            arr[arr == nodata] = np.nan
    return arr


# ---------------------------------------------------------------------------
# Map furniture
# ---------------------------------------------------------------------------
def _graticule(ax, study: Study, step: float = 0.5):
    """Longitude / latitude tick labels on a UTM map frame."""
    to_utm = Transformer.from_crs("EPSG:4326", study.crs, always_xy=True)
    to_geo = Transformer.from_crs(study.crs, "EPSG:4326", always_xy=True)
    x0, x1, y0, y1 = study.extent
    lon_lo, lat_lo = to_geo.transform(x0, y0)
    lon_hi, lat_hi = to_geo.transform(x1, y1)
    lons = np.arange(np.ceil(min(lon_lo, lon_hi) / step) * step, max(lon_lo, lon_hi), step)
    lats = np.arange(np.ceil(min(lat_lo, lat_hi) / step) * step, max(lat_lo, lat_hi), step)
    ymid_lat = to_geo.transform((x0 + x1) / 2, (y0 + y1) / 2)[1]
    xmid_lon = to_geo.transform((x0 + x1) / 2, (y0 + y1) / 2)[0]
    xt = [to_utm.transform(lo, ymid_lat)[0] for lo in lons]
    yt = [to_utm.transform(xmid_lon, la)[1] for la in lats]
    keep_x = [(x, lo) for x, lo in zip(xt, lons) if x0 < x < x1]
    keep_y = [(y, la) for y, la in zip(yt, lats) if y0 < y < y1]
    ax.set_xticks([x for x, _ in keep_x])
    ax.set_xticklabels([f"{lo:.1f}°E" for _, lo in keep_x])
    ax.set_yticks([y for y, _ in keep_y])
    ax.set_yticklabels([f"{la:.1f}°N" for _, la in keep_y], rotation=90, va="center")
    ax.tick_params(labelsize=7, length=3, direction="out")
    ax.grid(color="#999999", lw=0.3, ls=":", alpha=0.7)


def _north_arrow(ax, x=0.94, y=0.90, size=0.07, small=False):
    k = 0.6 if small else 1.0
    ax.annotate("", xy=(x, y), xytext=(x, y - size), xycoords="axes fraction",
                arrowprops=dict(facecolor="black", edgecolor="black", width=2.5 * k,
                                headwidth=8 * k, headlength=8 * k))
    ax.text(x, y + 0.01, "N", transform=ax.transAxes, ha="center", va="bottom",
            fontsize=10 * k, fontweight="bold")


def _scale_bar(ax, study: Study, length_km: float | None = None, loc=(0.05, 0.05), small=False):
    x0, x1, y0, y1 = study.extent
    width_km = (x1 - x0) / 1000
    if length_km is None:
        for cand in (100, 50, 40, 25, 20, 10, 5):
            if cand <= width_km / 4:
                length_km = cand
                break
        else:
            length_km = 5
    bx = x0 + loc[0] * (x1 - x0)
    by = y0 + loc[1] * (y1 - y0)
    h = 0.012 * (y1 - y0)
    seg = length_km * 1000 / 4
    for i in range(4):
        ax.add_patch(Rectangle((bx + i * seg, by), seg, h, facecolor="black" if i % 2 == 0 else "white",
                               edgecolor="black", lw=0.6, zorder=20))
    for frac in ((0, 1.0) if small else (0, 0.5, 1.0)):
        ax.text(bx + frac * length_km * 1000, by + 1.6 * h,
                f"{frac * length_km:g}" + (" km" if frac == 1.0 else ""),
                ha="center", va="bottom", fontsize=5.5 if small else 7, zorder=20,
                path_effects=[patheffects.withStroke(linewidth=2, foreground="white")])


def _boundaries(ax, study: Study, labels: bool = False):
    study.tehsils.boundary.plot(ax=ax, color="#444444", lw=0.35, zorder=10)
    if study.districts is not None:
        study.districts.boundary.plot(ax=ax, color="black", lw=0.9, zorder=11)
    else:
        study.area.boundary.plot(ax=ax, color="black", lw=0.9, zorder=11)
    if labels:
        for _, row in study.tehsils.iterrows():
            p = row.geometry.representative_point()
            ax.text(p.x, p.y, str(row[study.tehsil_name]), fontsize=6, ha="center", va="center",
                    zorder=12, color="#222222",
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.6))


def _frame(ax, study: Study, title: str | None = None, scale=True, north=True, grat=True, small=False):
    ax.set_xlim(study.extent[0], study.extent[1])
    ax.set_ylim(study.extent[2], study.extent[3])
    ax.set_aspect("equal")
    if grat:
        _graticule(ax, study)
    else:
        ax.set_xticks([])
        ax.set_yticks([])
    if north:
        _north_arrow(ax, small=small)
    if scale:
        _scale_bar(ax, study, small=small)
    if title:
        ax.set_title(title)
    ax.set_xlabel("")
    ax.set_ylabel("")
    for s in ax.spines.values():
        s.set_linewidth(0.8)


def _categorical(ax, study, arr, codes, colors, labels, legend_title, loc="lower right", ncol=1):
    lut = {c: i for i, c in enumerate(codes)}
    idx = np.full(arr.shape, np.nan, dtype="float32")
    for c, i in lut.items():
        idx[arr == c] = i
    cmap = ListedColormap(colors)
    cmap.set_bad(alpha=0)
    ax.imshow(np.ma.masked_invalid(idx), extent=study.extent, cmap=cmap, interpolation="nearest",
              norm=BoundaryNorm(np.arange(-0.5, len(codes) + 0.5), len(codes)), zorder=2)
    present = [i for i in range(len(codes)) if np.any(idx == i)]
    handles = [Patch(facecolor=colors[i], edgecolor="#555555", lw=0.4, label=labels[i]) for i in present]
    leg = ax.legend(handles=handles, title=legend_title, loc=loc, frameon=True, framealpha=0.95,
                    edgecolor="#888888", ncol=ncol)
    leg.set_zorder(50)
    return leg


def _classified(ax, study, arr, breaks, colors, unit, legend_title, ranks, loc="lower right"):
    """Continuous layer shown in the classes used for ranking."""
    cls = np.full(arr.shape, np.nan, dtype="float32")
    edges = [-np.inf, *breaks, np.inf]
    labels = []
    for i in range(len(edges) - 1):
        sel = (arr >= edges[i]) & (arr < edges[i + 1])
        cls[sel] = i + 1
        if i == 0:
            rng = f"< {breaks[0]:g}"
        elif i == len(edges) - 2:
            rng = f"≥ {breaks[-1]:g}"
        else:
            rng = f"{edges[i]:g} – {edges[i + 1]:g}"
        labels.append(f"{rng} {unit}  (rank {ranks[i]})")
    return _categorical(ax, study, cls, list(range(1, len(edges))), colors, labels, legend_title, loc=loc)


def _save(fig, out: Path, name: str, dpi: int):
    path = out / name
    fig.savefig(path, dpi=dpi, facecolor="white")
    plt.close(fig)
    print(f"  wrote {path}")
    return path


def _new_map(figsize=(6.3, 6.0)):
    fig, ax = plt.subplots(figsize=figsize)
    return fig, ax


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
def zone_table(zones: np.ndarray, mask: np.ndarray, px_km2: float):
    z = zones[mask]
    valid = z[z > 0]
    total = valid.size
    rows = []
    for c, n in zip(ZONE_CODES, ZONE_NAMES):
        k = int((valid == c).sum())
        rows.append({"class": c, "zone": n, "area_km2": k * px_km2,
                     "percent": 100.0 * k / total if total else 0.0})
    return rows, total * px_km2


def make_tables(study: Study, out: Path) -> dict:
    results = {"seasons": {}}
    breaks_file = config.ZONES_DIR / "class_breaks.json"
    breaks = json.loads(breaks_file.read_text())["breaks"] if breaks_file.exists() else None

    gwrpi_path = config.ZONES_DIR / "gwrpi_annual.tif"
    gmin = gmax = None
    if gwrpi_path.exists():
        g = read_full(gwrpi_path)[study.mask]
        g = g[np.isfinite(g)]
        gmin, gmax = float(g.min()), float(g.max())

    for season in SEASON_ORDER:
        zpath = config.zones_path(season)
        if not zpath.exists():
            print(f"  (no {zpath.name}; skipping {season})")
            continue
        rows, total = zone_table(read_full(zpath), study.mask, study.pixel_area_km2)
        rain = {}
        rpath = config.layer_path(f"rainfall_{season}")
        if rpath.exists():
            r = read_full(rpath)[study.mask]
            r = r[np.isfinite(r)]
            rain = {"mean": float(r.mean()), "min": float(r.min()), "max": float(r.max())}
        results["seasons"][season] = {"rows": rows, "total_km2": total, "rain": rain}

    # Tehsil-wise annual areas
    tehsil_rows = []
    zpath = config.zones_path("annual")
    if zpath.exists():
        zones = read_full(zpath)
        for _, t in study.tehsils.iterrows():
            m = rasterize([(t.geometry, 1)], out_shape=study.shape, transform=study.transform,
                          fill=0, dtype="uint8").astype(bool)
            rows, total = zone_table(zones, m, study.pixel_area_km2)
            tehsil_rows.append({"district": t.get("NAME_2", ""), "tehsil": t[study.tehsil_name],
                                "total_km2": total, "rows": rows})
    results["tehsils"] = tehsil_rows

    # ---- write CSV + markdown ----
    lines = ["# Report tables (computed inside the tehsil boundaries)", ""]
    ann = results["seasons"].get("annual")
    if ann:
        edges = [gmin, *(breaks or [None] * 4), gmax]
        with open(out / "table_4_2_zone_areas.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Zone", "GWRPI from", "GWRPI to", "Area (km2)", "Share (%)"])
            for i, r in enumerate(ann["rows"]):
                w.writerow([r["zone"], _fmt(edges[i], 3), _fmt(edges[i + 1], 3),
                            f"{r['area_km2']:.1f}", f"{r['percent']:.2f}"])
        lines += ["**Table 4.2: Area under each recharge potential zone (annual)**", "",
                  "| Zone | GWRPI Range | Area (km²) | Share of Study Area (%) |", "|---|---|---|---|"]
        for i, r in enumerate(ann["rows"]):
            lo, hi = edges[i], edges[i + 1]
            rng = f"{_fmt(lo, 2)} – {_fmt(hi, 2)}" if lo is not None and hi is not None else "–"
            lines.append(f"| {r['zone']} | {rng} | {r['area_km2']:,.1f} | {r['percent']:.1f} |")
        lines.append(f"| **Total** | | **{ann['total_km2']:,.1f}** | **100.0** |")
        lines.append("")

    with open(out / "table_4_3_seasonal.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Season", "Mean rainfall (mm)", "Min (mm)", "Max (mm)", *[f"{n} (%)" for n in ZONE_NAMES],
                    *[f"{n} (km2)" for n in ZONE_NAMES]])
        for s, d in results["seasons"].items():
            rn = d["rain"]
            w.writerow([SEASON_LABELS[s], _fmt(rn.get("mean"), 1), _fmt(rn.get("min"), 1), _fmt(rn.get("max"), 1),
                        *[f"{r['percent']:.2f}" for r in d["rows"]], *[f"{r['area_km2']:.1f}" for r in d["rows"]]])
    lines += ["**Table 4.3: Seasonal rainfall and zone shares**", "",
              "| Season | Mean Rainfall over Study Area (mm) | Rainfall Range (mm) | "
              + " | ".join(f"{n} (%)" for n in ZONE_NAMES) + " |",
              "|---" * (3 + len(ZONE_NAMES)) + "|"]
    for s, d in results["seasons"].items():
        rn = d["rain"]
        mean = f"{rn['mean']:,.0f}" if rn else "–"
        rng = f"{rn['min']:,.0f} – {rn['max']:,.0f}" if rn else "–"
        lines.append(f"| {SEASON_LABELS[s]} | {mean} | {rng} | "
                     + " | ".join(f"{r['percent']:.1f}" for r in d["rows"]) + " |")
    lines.append("")

    if tehsil_rows:
        with open(out / "tehsil_zone_areas.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["District", "Tehsil", "Total (km2)", *[f"{n} (km2)" for n in ZONE_NAMES],
                        *[f"{n} (%)" for n in ZONE_NAMES]])
            for t in tehsil_rows:
                w.writerow([t["district"], t["tehsil"], f"{t['total_km2']:.1f}",
                            *[f"{r['area_km2']:.1f}" for r in t["rows"]],
                            *[f"{r['percent']:.1f}" for r in t["rows"]]])
        lines += ["**Tehsil-wise share of each zone, annual map (%)**", "",
                  "| District | Tehsil | Area (km²) | " + " | ".join(ZONE_NAMES) + " |",
                  "|---" * (3 + len(ZONE_NAMES)) + "|"]
        for t in sorted(tehsil_rows, key=lambda t: (t["district"], t["tehsil"])):
            lines.append(f"| {t['district']} | {t['tehsil']} | {t['total_km2']:,.0f} | "
                         + " | ".join(f"{r['percent']:.1f}" for r in t["rows"]) + " |")
        lines.append("")

    text = "\n".join(lines)
    (out / "tables.md").write_text(text, encoding="utf-8")
    (out / "tables.json").write_text(json.dumps(results, indent=2, default=float))
    print()
    print(text)
    return results


def _fmt(v, nd):
    return "" if v is None else f"{v:.{nd}f}"


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def fig_location(study: Study, out: Path, dpi: int, india_file: Path | None):
    fig, ax = _new_map((6.3, 6.3))
    dem_path = config.layer_path("dem")
    if dem_path.exists():
        dem = study.read_display(dem_path, categorical=False, clip=False)
        res = (study.extent[1] - study.extent[0]) / study.dshape[1]
        dy, dx = np.gradient(np.nan_to_num(dem, nan=np.nanmean(dem)), res)
        slope = np.pi / 2 - np.arctan(np.hypot(dx, dy))
        aspect = np.arctan2(-dx, dy)
        az, alt = np.radians(315), np.radians(45)
        shade = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
        ax.imshow(shade, extent=study.extent, cmap="Greys_r", alpha=0.35, zorder=1)
        dem_in = np.where(study.dmask, dem, np.nan)
        im = ax.imshow(np.ma.masked_invalid(dem_in), extent=study.extent, cmap="terrain",
                       vmin=np.nanpercentile(dem_in, 1) - 150, vmax=np.nanpercentile(dem_in, 99.5),
                       alpha=0.75, zorder=2)
        cb = fig.colorbar(im, ax=ax, shrink=0.55, pad=0.02)
        cb.set_label("Elevation (m)")
    lulc_path = config.layer_path("lulc")
    if lulc_path.exists():
        lulc = study.read_display(lulc_path, categorical=True, clip=False)
        water = np.where(lulc == 80, 1.0, np.nan)
        ax.imshow(np.ma.masked_invalid(water), extent=study.extent,
                  cmap=ListedColormap([WATER_COLOR]), interpolation="nearest", zorder=3)
    _boundaries(ax, study, labels=True)
    to_utm = Transformer.from_crs("EPSG:4326", study.crs, always_xy=True)
    bx, by = to_utm.transform(*BHOPAL_CITY)
    ax.plot(bx, by, marker="*", ms=11, mfc="#c00000", mec="white", mew=0.8, zorder=15)
    ax.annotate("Bhopal city", (bx, by), xytext=(8, 6), textcoords="offset points", fontsize=8,
                fontweight="bold", zorder=15)
    handles = [Line2D([], [], color="black", lw=1.2, label="District boundary"),
               Line2D([], [], color="#444444", lw=0.5, label="Tehsil boundary"),
               Patch(facecolor=WATER_COLOR, label="Rivers / water bodies"),
               Line2D([], [], marker="*", color="none", mfc="#c00000", mec="white", ms=10, label="Bhopal city")]
    ax.legend(handles=handles, loc="lower right", framealpha=0.95, edgecolor="#888888").set_zorder(50)
    _frame(ax, study, "Study area: Bhopal, Raisen and Narmadapuram districts", north=False)
    _north_arrow(ax, x=0.06)

    india = _load_india(india_file)
    if india is not None:
        ins = ax.inset_axes([0.70, 0.70, 0.30, 0.30])
        ins.set_zorder(60)
        india.plot(ax=ins, facecolor="#f2f2f2", edgecolor="#888888", lw=0.25)
        mp = india[india.iloc[:, 0].astype(str).str.contains("Madhya Pradesh")] if len(india.columns) else None
        if mp is not None and len(mp):
            mp.plot(ax=ins, facecolor="#ffe8a8", edgecolor="#555555", lw=0.4)
        study.area.to_crs("EPSG:4326").plot(ax=ins, facecolor="#c00000", edgecolor="none")
        ins.set_xticks([])
        ins.set_yticks([])
        ins.set_xlabel("")
        ins.set_ylabel("")
        ins.set_aspect("equal")
        ins.set_facecolor("white")
        ins.text(0.5, 0.02, "India", transform=ins.transAxes, ha="center", fontsize=6)
        for s in ins.spines.values():
            s.set_linewidth(0.6)
    return _save(fig, out, "fig_1_2_location_map.png", dpi)


def _load_india(india_file: Path | None):
    """India states (name in the first column) from GADM, or None."""
    cands = [india_file] if india_file else []
    bdir = config.RAW_DIR / "boundaries"
    cands += sorted(bdir.glob("gadm41_IND*.gpkg")) + sorted(bdir.glob("gadm41_IND_1.*"))
    cands += sorted(bdir.glob("**/gadm41_IND_1.shp"))
    for c in cands:
        if not c or not Path(c).exists():
            continue
        try:
            if str(c).endswith(".gpkg"):
                import pyogrio
                layers = [l[0] for l in pyogrio.list_layers(c)]
                layer = "ADM_ADM_1" if "ADM_ADM_1" in layers else next((l for l in layers if l.endswith("1")), None)
                if layer is None:
                    continue
                g = gpd.read_file(c, layer=layer)
            else:
                g = gpd.read_file(c)
            if "NAME_1" in g:
                g = g[["NAME_1", "geometry"]]
            return g.to_crs("EPSG:4326")
        except Exception as exc:  # noqa: BLE001
            print(f"  (could not read {c}: {exc})")
    print("  (no GADM India file in data/raw/boundaries; location map drawn without the India inset)")
    return None


def fig_rainfall(study, out, dpi):
    path = config.layer_path("rainfall_annual")
    if not path.exists():
        return print("  (no rainfall_annual.tif; skipping Fig 3.2)")
    arr = study.read_display(path, categorical=False)
    fig, ax = _new_map()
    im = ax.imshow(np.ma.masked_invalid(arr), extent=study.extent, cmap="YlGnBu", zorder=2)
    cb = fig.colorbar(im, ax=ax, shrink=0.6, pad=0.02)
    cb.set_label("Mean annual rainfall (mm), 2014–2024")
    _boundaries(ax, study)
    _frame(ax, study, "Rainfall (annual mean, 2014–2024)")
    return _save(fig, out, "fig_3_2_rainfall.png", dpi)


def fig_geology(study, out, dpi):
    path = config.layer_path("geology")
    if not path.exists():
        return print("  (no geology.tif; skipping Fig 3.3)")
    arr = study.read_display(path, categorical=True)
    codes = [5, 4, 3, 2, 1]
    fig, ax = _new_map()
    _categorical(ax, study, arr, codes, [GEOLOGY_GROUPS[c][1] for c in codes],
                 [f"{GEOLOGY_GROUPS[c][0]} (rank {c})" for c in codes], "Lithology group")
    _boundaries(ax, study)
    _frame(ax, study, "Geology (lithology)")
    return _save(fig, out, "fig_3_3_geology.png", dpi)


def fig_continuous_classes(study, out, dpi, name, fig_name, title, unit, legend_title, cmap_name,
                           lines_file: Path | None = None):
    path = config.layer_path(name)
    if not path.exists():
        return print(f"  (no {name}.tif; skipping {fig_name})")
    rule = config.CONTINUOUS_RULES[name]
    arr = study.read_display(path, categorical=False)
    n = len(rule["breaks"]) + 1
    colors = [matplotlib.colors.to_hex(c) for c in plt.get_cmap(cmap_name)(np.linspace(0.12, 0.9, n))]
    fig, ax = _new_map()
    leg = _classified(ax, study, arr, rule["breaks"], colors, unit, legend_title, rule["ranks"])
    _boundaries(ax, study)
    if lines_file is not None and lines_file.exists():
        try:
            lines = gpd.read_file(lines_file).to_crs(study.crs)
            lines = gpd.clip(lines, study.area)
            lines.plot(ax=ax, color="black", lw=0.35, zorder=9)
            ax.add_artist(leg)
            ax.legend(handles=[Line2D([], [], color="black", lw=0.8, label="Mapped lineaments / faults (GSI)")],
                      loc="upper left", framealpha=0.95, edgecolor="#888888").set_zorder(50)
        except Exception as exc:  # noqa: BLE001
            print(f"  (could not draw {lines_file.name}: {exc})")
    _frame(ax, study, title)
    return _save(fig, out, fig_name, dpi)


def fig_soil(study, out, dpi):
    path = config.layer_path("soil")
    if not path.exists():
        return print("  (no soil.tif; skipping Fig 3.5)")
    arr = study.read_display(path, categorical=True)
    codes = [c for c in config.SOIL_TEXTURES if np.any(arr == c)]
    # colour by rank: coarse (high rank) sandy tones -> fine clay browns
    rank_colors = {5: "#f6e8a6", 4: "#e7c46b", 3: "#c99a4b", 2: "#9c6a32", 1: "#5e3a1c"}
    shade = {}
    for c in codes:
        r = config.SOIL_TEXTURES[c][1]
        same = [k for k in codes if config.SOIL_TEXTURES[k][1] == r]
        base = np.array(matplotlib.colors.to_rgb(rank_colors[r]))
        f = 1.0 - 0.18 * same.index(c)
        shade[c] = matplotlib.colors.to_hex(np.clip(base * f, 0, 1))
    fig, ax = _new_map()
    _categorical(ax, study, arr, codes, [shade[c] for c in codes],
                 [f"{config.SOIL_TEXTURES[c][0]} (rank {config.SOIL_TEXTURES[c][1]})" for c in codes],
                 "Soil texture (USDA)")
    _boundaries(ax, study)
    _frame(ax, study, "Soil texture")
    return _save(fig, out, "fig_3_5_soil.png", dpi)


def fig_lulc(study, out, dpi):
    path = config.layer_path("lulc")
    if not path.exists():
        return print("  (no lulc.tif; skipping Fig 3.6)")
    arr = study.read_display(path, categorical=True)
    codes = [c for c in config.LULC_NAMES if np.any(arr == c)]
    fig, ax = _new_map()
    _categorical(ax, study, arr, codes, [WORLDCOVER_COLORS[c] for c in codes],
                 [f"{config.LULC_NAMES[c]} (rank {config.LULC_RANKS[c]})" for c in codes],
                 "Land cover (ESA WorldCover 2021)")
    _boundaries(ax, study)
    _frame(ax, study, "Land use / land cover")
    return _save(fig, out, "fig_3_6_lulc.png", dpi)


def _zones_panel(ax, study, season, legend=True):
    path = config.zones_path(season)
    arr = study.read_display(path, categorical=True)
    leg = None
    if legend:
        leg = _categorical(ax, study, arr, ZONE_CODES, ZONE_COLORS, ZONE_NAMES, "Recharge potential")
    else:
        _categorical(ax, study, arr, ZONE_CODES, ZONE_COLORS, ZONE_NAMES, "").remove()
    _boundaries(ax, study)
    return leg


def fig_annual(study, out, dpi):
    if not config.zones_path("annual").exists():
        return print("  (no annual zones; skipping Fig 4.1)")
    fig, ax = _new_map()
    _zones_panel(ax, study, "annual")
    _frame(ax, study, "Groundwater recharge potential zones (annual)")
    return _save(fig, out, "fig_4_1_recharge_annual.png", dpi)


def fig_seasonal(study, out, dpi):
    seasons = [s for s in ("pre_monsoon", "monsoon", "post_monsoon") if config.zones_path(s).exists()]
    if not seasons:
        return print("  (no seasonal zones; skipping Fig 4.2)")
    fig, axes = plt.subplots(1, len(seasons), figsize=(6.6, 2.6))
    axes = np.atleast_1d(axes)
    for i, (ax, s) in enumerate(zip(axes, seasons)):
        _zones_panel(ax, study, s, legend=False)
        _frame(ax, study, f"({chr(97 + i)}) {SEASON_LABELS[s]}", scale=(i == 0), north=(i == len(seasons) - 1),
               grat=False, small=True)
        ax.title.set_fontsize(8.5)
    handles = [Patch(facecolor=c, edgecolor="#555555", lw=0.4, label=n) for c, n in zip(ZONE_COLORS, ZONE_NAMES)]
    fig.legend(handles=handles, loc="lower center", ncol=5, frameon=False, title="Recharge potential",
               bbox_to_anchor=(0.5, -0.06))
    fig.subplots_adjust(wspace=0.04)
    return _save(fig, out, "fig_4_2_recharge_seasonal.png", dpi)


def fig_wells(study, out, dpi):
    wells_csv = config.OUTPUT_DIR / "validation_wells.csv"
    if not wells_csv.exists() or not config.zones_path("annual").exists():
        return print("  (no validation_wells.csv; run `python -m backend.pipeline validate`; skipping Fig 4.3)")
    import pandas as pd
    w = pd.read_csv(wells_csv)
    w["excluded"] = w.get("excluded", "").fillna("")
    g = gpd.GeoDataFrame(w, geometry=gpd.points_from_xy(w["longitude"], w["latitude"]), crs="EPSG:4326")
    g = g.to_crs(study.crs)
    used, excl = g[g["excluded"] == ""], g[g["excluded"] != ""]
    fig, ax = _new_map()
    leg = _zones_panel(ax, study, "annual")
    leg.set_bbox_to_anchor((1.0, 0.0))
    if len(excl):
        excl.plot(ax=ax, marker="x", color="#555555", markersize=14, lw=0.8, zorder=14)
    used.plot(ax=ax, marker="o", facecolor="black", edgecolor="white", linewidth=0.6, markersize=22, zorder=15)
    ax.add_artist(leg)
    hs = [Line2D([], [], marker="o", color="none", mfc="black", mec="white", ms=6,
                 label=f"Wells used ({len(used)})")]
    if len(excl):
        hs.append(Line2D([], [], marker="x", color="#555555", ls="none", ms=5,
                         label=f"Wells excluded ({len(excl)})"))
    ax.legend(handles=hs, loc="upper left", framealpha=0.95, edgecolor="#888888").set_zorder(50)
    _frame(ax, study, "CGWB observation wells used for validation")
    return _save(fig, out, "fig_4_3_wells.png", dpi)


def fig_area_bars(results, out, dpi):
    seasons = list(results["seasons"])
    if not seasons:
        return
    fig, ax = plt.subplots(figsize=(6.3, 3.4))
    n = len(seasons)
    width = 0.8 / n
    x = np.arange(len(ZONE_NAMES))
    season_colors = {"annual": "#333333", "pre_monsoon": "#e08214", "monsoon": "#2166ac",
                     "post_monsoon": "#8073ac"}
    for i, s in enumerate(seasons):
        vals = [r["area_km2"] for r in results["seasons"][s]["rows"]]
        ax.bar(x - 0.4 + width * (i + 0.5), vals, width * 0.92, color=season_colors.get(s, "#777777"),
               label=SEASON_LABELS[s], zorder=3)
    ax.set_xticks(x)
    ax.set_xticklabels(ZONE_NAMES)
    ax.set_ylabel("Area (km²)")
    ax.set_xlabel("Recharge potential zone")
    ax.grid(axis="y", color="#dddddd", lw=0.6, zorder=0)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(frameon=False, ncol=2, loc="upper center", bbox_to_anchor=(0.5, 1.18))
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    return _save(fig, out, "fig_zone_area_bar.png", dpi)


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=str(config.OUTPUT_DIR / "report"), help="output folder")
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--display-px", type=int, default=1800,
                   help="long side of the resampled map image in pixels (300 dpi x 6 in = 1800)")
    p.add_argument("--india", default=None, help="GADM India level-1 file for the location inset (optional)")
    p.add_argument("--tables-only", action="store_true")
    args = p.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ref = config.zones_path("annual")
    if not ref.exists():
        print(f"{ref} not found. Run `python -m backend.pipeline all` first.")
        return 1
    if not config.TEHSIL_FILE.exists():
        print(f"{config.TEHSIL_FILE} not found. Run `python scripts/prepare_data.py tehsils` first.")
        return 1
    print(f"Data folder: {config.DATA_DIR}\nOutput folder: {out}")
    study = Study(ref, args.display_px)
    print(f"Study area inside tehsil boundaries: {study.mask.sum() * study.pixel_area_km2:,.1f} km² "
          f"({study.pixel_area_km2 * 1e6:.0f} m² pixels)")

    results = make_tables(study, out)
    if args.tables_only:
        return 0

    print("\nFigures:")
    fig_location(study, out, args.dpi, Path(args.india) if args.india else None)
    fig_rainfall(study, out, args.dpi)
    fig_geology(study, out, args.dpi)
    fig_continuous_classes(study, out, args.dpi, "slope", "fig_3_4_slope.png", "Slope",
                           "°", "Slope (degrees)", "YlOrBr")
    fig_soil(study, out, args.dpi)
    fig_lulc(study, out, args.dpi)
    fig_continuous_classes(study, out, args.dpi, "drainage_density", "fig_3_7_drainage_density.png",
                           "Drainage density", "km/km²", "Drainage density", "PuBu")
    lines = config.RAW_LINEAMENT_FILE if config.LINEAMENT_SOURCE != "dem" else None
    fig_continuous_classes(study, out, args.dpi, "lineament_density", "fig_3_8_lineament_density.png",
                           "Lineament density", "km/km²", "Lineament density", "RdPu", lines_file=lines)
    fig_annual(study, out, args.dpi)
    fig_seasonal(study, out, args.dpi)
    fig_wells(study, out, args.dpi)
    fig_area_bars(results, out, args.dpi)
    box = config.OUTPUT_DIR / "validation_boxplot.png"
    if box.exists():
        shutil.copy2(box, out / "fig_4_4_validation_boxplot.png")
        print(f"  copied {box.name} -> fig_4_4_validation_boxplot.png")
    print(f"\nDone. Tables: {out / 'tables.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
