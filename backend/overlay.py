"""Reclassification and weighted overlay.

1. Every thematic layer is reclassified to a recharge rank 1..5
   (5 = most favourable), using the rules in config.
2. The groundwater recharge potential index is the weighted sum

       GWRPI = sum_i  w_i * rank_i        (weights sum to 1, so GWRPI is in 1..5)

3. GWRPI is cut into five zones: 1 Very Low, 2 Low, 3 Moderate, 4 High,
   5 Very High.

For seasonal comparison the zone breaks stay the same across seasons; the
pipeline computes them once from the annual map and saves them
(processed/zones/class_breaks.json). Rainfall itself is ranked within each
season by default (config.RAINFALL_SEASON_MODE), because dry-season totals
are far below any threshold that suits the monsoon.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.windows import Window, from_bounds
from rasterio.windows import transform as window_transform

from backend import config
from backend.ahp import normalize_weights
from backend.grid import ReferenceGrid, read_raster, write_raster, zone_colormap
from backend.preprocessing.lulc import reclassify_lulc
from backend.preprocessing.soil import reclassify_soil

log = logging.getLogger(__name__)

BREAKS_FILE = config.ZONES_DIR / "class_breaks.json"
RAINFALL_BREAKS_FILE = config.RANKED_DIR / "rainfall_breaks.json"


# ---------------------------------------------------------------------------
# Reclassification
# ---------------------------------------------------------------------------
def reclassify_continuous(values: np.ndarray, breaks, ranks) -> np.ndarray:
    """Bin continuous values into ranks. NaN -> 0 (nodata).

    ``values < breaks[0]`` gets ``ranks[0]``, ``breaks[k-1] <= v < breaks[k]``
    gets ``ranks[k]`` and ``v >= breaks[-1]`` gets ``ranks[-1]``.
    """
    breaks = list(breaks)
    ranks = list(ranks)
    if len(ranks) != len(breaks) + 1:
        raise ValueError("Need exactly one more rank than breaks")
    if any(b2 <= b1 for b1, b2 in zip(breaks, breaks[1:])):
        raise ValueError("Breaks must be strictly increasing")
    values = np.asarray(values, dtype="float64")
    idx = np.digitize(np.nan_to_num(values, nan=-np.inf), breaks)
    out = np.asarray(ranks, dtype="uint8")[idx]
    out[~np.isfinite(values)] = config.NODATA_RANK
    return out


def rainfall_breaks(values: np.ndarray, mode: str = config.RAINFALL_SEASON_MODE) -> list[float]:
    """Four rainfall breaks (mm): season quintiles ("relative") or the fixed rule."""
    if mode == "absolute":
        return list(config.CONTINUOUS_RULES["rainfall"]["breaks"])
    if mode != "relative":
        raise ValueError(f"Unknown RAINFALL_SEASON_MODE {mode!r}")
    vals = np.asarray(values, dtype="float64")
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        raise ValueError("Rainfall layer has no valid pixels")
    brks = [float(b) for b in np.quantile(vals, [0.2, 0.4, 0.6, 0.8])]
    for i in range(1, len(brks)):
        if brks[i] <= brks[i - 1]:
            brks[i] = math.nextafter(brks[i - 1], math.inf)
    return brks


def load_rainfall_breaks(path: Path | None = None) -> dict:
    """Per-season rainfall classes written by rank_all ({} if not there yet)."""
    path = path or RAINFALL_BREAKS_FILE
    return json.loads(path.read_text()) if path.exists() else {}


def rank_layer(name: str, data: np.ndarray, breaks=None) -> np.ndarray:
    """Reclassify one processed layer to ranks 1..5 (uint8, 0 = nodata).

    ``name`` is a layer key from config.LAYERS. For rainfall, ``breaks``
    overrides the fixed rule (see rainfall_breaks).
    """
    if name == "rainfall" and breaks is not None:
        return reclassify_continuous(data, breaks, [1, 2, 3, 4, 5])
    if name in config.CONTINUOUS_RULES:
        rule = config.CONTINUOUS_RULES[name]
        return reclassify_continuous(data, rule["breaks"], rule["ranks"])
    data = np.nan_to_num(np.asarray(data), nan=0).astype("int32")
    if name == "lulc":
        return reclassify_lulc(data)
    if name == "soil":
        return reclassify_soil(data)
    if name == "geology":     # geology.tif already stores ranks
        out = data.astype("uint8")
        out[(out < 1) | (out > 5)] = config.NODATA_RANK
        return out
    raise KeyError(f"No reclassification rule for layer '{name}'")


def rank_all(grid: ReferenceGrid | None = None) -> dict[str, Path]:
    """Reclassify every processed layer that exists and write the ranked rasters."""
    grid = grid or ReferenceGrid.default()
    config.ensure_dirs()
    written = {}
    rain_info = {}
    jobs = [(n, None) for n in config.LAYERS if n != "rainfall"]
    jobs += [("rainfall", s) for s in config.SEASONS]
    for name, season in jobs:
        src = config.layer_path(f"rainfall_{season}" if season else name)
        if not src.exists():
            log.warning("Skipping %s: %s not found", name if not season else f"rainfall/{season}", src)
            continue
        if not grid.matches(src):
            raise ValueError(f"{src} is not on the reference grid; re-run preprocessing")
        data = read_raster(src)
        breaks = None
        if name == "rainfall":
            breaks = rainfall_breaks(data)
            ok = data[np.isfinite(data)]
            rain_info[season] = {
                "mode": config.RAINFALL_SEASON_MODE, "breaks_mm": [round(b, 1) for b in breaks],
                "min_mm": round(float(ok.min()), 1), "mean_mm": round(float(ok.mean()), 1),
                "max_mm": round(float(ok.max()), 1)}
            log.info("Rainfall %-13s %s classes, breaks %s mm", season, config.RAINFALL_SEASON_MODE,
                     rain_info[season]["breaks_mm"])
        ranks = rank_layer(name, data, breaks)
        dst = config.ranked_path(name, season)
        written[dst.stem] = write_raster(dst, ranks, grid, nodata=config.NODATA_RANK)
    if rain_info:
        RAINFALL_BREAKS_FILE.write_text(json.dumps(rain_info, indent=2))
    return written


# ---------------------------------------------------------------------------
# Overlay and classification
# ---------------------------------------------------------------------------
def weighted_overlay(ranks: Mapping[str, np.ndarray], weights: Mapping[str, float]) -> np.ndarray:
    """Weighted sum of rank rasters -> GWRPI (float32, NaN where any layer is nodata).

    Only layers present in both ``ranks`` and ``weights`` (with weight > 0) are
    used, and their weights are renormalised to sum to 1.
    """
    names = [n for n in ranks if weights.get(n, 0) > 0]
    if not names:
        raise ValueError("No overlapping layers between ranks and weights")
    w = normalize_weights(weights, names)
    shape = ranks[names[0]].shape
    index = np.zeros(shape, dtype="float32")
    valid = np.ones(shape, dtype=bool)
    for n in names:
        r = ranks[n]
        if r.shape != shape:
            raise ValueError(f"Layer '{n}' shape {r.shape} differs from {shape}")
        valid &= r > 0
        index += np.float32(w[n]) * r.astype("float32")
    index[~valid] = np.nan
    return index


def compute_breaks(index: np.ndarray, method: str = config.CLASSIFICATION_METHOD,
                   sample_size: int = 200_000, seed: int = 0) -> list[float]:
    """Four class breaks for the GWRPI using ``method``.

    fixed: config.FIXED_CLASS_BREAKS; quantile: equal-area; equal: equal
    interval between min and max; jenks: natural breaks (needs jenkspy).
    """
    if method == "fixed":
        return list(config.FIXED_CLASS_BREAKS)
    vals = index[np.isfinite(index)]
    if vals.size == 0:
        raise ValueError("Index has no valid pixels")
    if vals.size > sample_size:
        vals = np.random.default_rng(seed).choice(vals, sample_size, replace=False)
    if method == "quantile":
        brks = np.quantile(vals, [0.2, 0.4, 0.6, 0.8])
    elif method == "equal":
        brks = np.linspace(vals.min(), vals.max(), 6)[1:-1]
    elif method == "jenks":
        try:
            import jenkspy
        except ImportError as exc:
            raise ImportError("Jenks classification needs `pip install jenkspy`") from exc
        brks = jenkspy.jenks_breaks(vals.tolist(), n_classes=5)[1:-1]
    else:
        raise ValueError(f"Unknown classification method {method!r}")
    brks = [float(b) for b in brks]
    # Guard against duplicates (e.g. many identical values): nudge upwards.
    for i in range(1, len(brks)):
        if brks[i] <= brks[i - 1]:
            brks[i] = math.nextafter(brks[i - 1], math.inf)
    return brks


def classify_index(index: np.ndarray, breaks) -> np.ndarray:
    """GWRPI -> zone codes 1..5 (uint8, 0 = nodata)."""
    return reclassify_continuous(index, breaks, [1, 2, 3, 4, 5])


def zone_statistics(zones: np.ndarray, pixel_area_km2: float) -> list[dict]:
    """Area (km^2) and share (%) of each recharge class."""
    total = int((zones > 0).sum())
    stats = []
    for code, meta in config.RECHARGE_CLASSES.items():
        n = int((zones == code).sum())
        stats.append({
            "class": code, "name": meta["name"], "color": meta["color"],
            "area_km2": round(n * pixel_area_km2, 3),
            "percent": round(100.0 * n / total, 2) if total else 0.0,
        })
    return stats


def save_breaks(breaks, method: str, path: Path = BREAKS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"method": method, "breaks": [float(b) for b in breaks]}, indent=2))


def load_breaks(path: Path = BREAKS_FILE) -> list[float] | None:
    """Saved breaks, or None if the pipeline has not produced them yet."""
    if not path.exists():
        return None
    return [float(b) for b in json.loads(path.read_text())["breaks"]]


# ---------------------------------------------------------------------------
# Full study-area runs (pipeline)
# ---------------------------------------------------------------------------
def available_layers(season: str = config.DEFAULT_SEASON) -> list[str]:
    """Layers whose ranked raster exists for ``season``."""
    return [n for n in config.LAYERS if config.ranked_path(n, season).exists()]


def run_study_area(grid: ReferenceGrid | None = None,
                   weights: Mapping[str, float] = config.DEFAULT_WEIGHTS,
                   method: str = config.CLASSIFICATION_METHOD) -> dict[str, dict]:
    """Produce recharge zone rasters for every season over the whole study area.

    Breaks come from the annual map and are reused for all seasons.
    """
    grid = grid or ReferenceGrid.default()
    config.ensure_dirs()

    def index_for(season):
        names = available_layers(season)
        if "rainfall" not in names:
            log.warning("No ranked rainfall for season %s", season)
        ranks = {n: read_raster(config.ranked_path(n, season), as_float=False) for n in names}
        return weighted_overlay(ranks, weights), names

    annual_index, names = index_for("annual")
    breaks = compute_breaks(annual_index, method)
    save_breaks(breaks, method)
    log.info("Class breaks (%s, from annual map): %s", method, [round(b, 3) for b in breaks])

    results = {}
    for season in config.SEASONS:
        index, names = (annual_index, names) if season == "annual" else index_for(season)
        zones = classify_index(index, breaks)
        write_raster(config.zones_path(season), zones, grid, nodata=0, colormap=zone_colormap())
        write_raster(config.ZONES_DIR / f"gwrpi_{season}.tif", index, grid)
        results[season] = {"layers": names,
                           "stats": zone_statistics(zones, grid.pixel_area_km2)}
    (config.ZONES_DIR / "zone_statistics.json").write_text(json.dumps(results, indent=2))
    return results


# ---------------------------------------------------------------------------
# Region analysis (API)
# ---------------------------------------------------------------------------
@dataclass
class RegionResult:
    zones: np.ndarray            # uint8 zone codes, 0 outside region / nodata
    index: np.ndarray            # float32 GWRPI, NaN outside
    transform: rasterio.Affine
    crs: rasterio.crs.CRS
    pixel_area_km2: float
    breaks: list[float]
    weights: dict[str, float]
    layers: list[str]
    stats: list[dict]


def _region_window(src, geometry_bounds, max_pixels: int):
    """Window covering the bounds and a decimation factor to stay under max_pixels."""
    win = from_bounds(*geometry_bounds, transform=src.transform)
    win = win.round_offsets(op="floor").round_lengths(op="ceil")
    win = win.intersection(Window(0, 0, src.width, src.height))
    factor = max(1, math.ceil(math.sqrt(win.width * win.height / max_pixels)))
    out_shape = (max(1, int(win.height // factor)), max(1, int(win.width // factor)))
    return win, out_shape


def read_ranked_region(name: str, season: str, geometry_bounds,
                       max_pixels: int = config.MAX_ANALYSIS_PIXELS):
    """Read a ranked raster over a bounding box, decimated if large.

    Returns (array, transform, crs).
    """
    path = config.ranked_path(name, season)
    with rasterio.open(path) as src:
        win, out_shape = _region_window(src, geometry_bounds, max_pixels)
        arr = src.read(1, window=win, out_shape=out_shape, resampling=Resampling.nearest)
        tf = window_transform(win, src.transform)
        tf = tf @ rasterio.Affine.scale(win.width / out_shape[1], win.height / out_shape[0])
        return arr, tf, src.crs


def analyze_region(geometry, season: str = config.DEFAULT_SEASON,
                   weights: Mapping[str, float] | None = None,
                   method: str | None = None,
                   max_pixels: int = config.MAX_ANALYSIS_PIXELS) -> RegionResult:
    """Recharge zones for one region (shapely geometry in the grid CRS)."""
    if season not in config.SEASONS:
        raise ValueError(f"Unknown season {season!r}; choose from {list(config.SEASONS)}")
    names = available_layers(season)
    if not names:
        raise FileNotFoundError("No ranked layers found. Run the preprocessing pipeline first.")
    weights = dict(weights or config.DEFAULT_WEIGHTS)

    ranks, tf, crs = {}, None, None
    for n in names:
        arr, tf_n, crs_n = read_ranked_region(n, season, geometry.bounds, max_pixels)
        if tf is None:
            tf, crs, shape = tf_n, crs_n, arr.shape
        elif arr.shape != shape:
            raise ValueError(f"Ranked layer {n} is misaligned with the others")
        ranks[n] = arr

    inside = geometry_mask([geometry], out_shape=shape, transform=tf, invert=True, all_touched=True)
    if not inside.any():
        raise ValueError("Region does not overlap the processed study area")
    for n in ranks:
        ranks[n] = np.where(inside, ranks[n], 0).astype("uint8")

    used = [n for n in names if weights.get(n, 0) > 0]
    index = weighted_overlay({n: ranks[n] for n in used}, weights)

    if method and method != "saved":
        breaks = compute_breaks(index, method)
    else:
        breaks = load_breaks() or list(config.FIXED_CLASS_BREAKS)
    zones = classify_index(index, breaks)
    pixel_area = abs(tf.a * tf.e) / 1e6
    return RegionResult(zones=zones, index=index, transform=tf, crs=crs,
                        pixel_area_km2=pixel_area, breaks=[float(b) for b in breaks],
                        weights=normalize_weights(weights, used), layers=used,
                        stats=zone_statistics(zones, pixel_area))
