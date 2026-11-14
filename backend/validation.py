"""Validate the recharge zones against CGWB well water levels.

Input:  data/raw/wells/wells_raw.csv (from scripts/download_wells.py), one row
        per reading with stationCode, latitude, longitude, dataTime and
        dataValue (depth to water, m).
Output: outputs/validation_wells.csv     one row per well
        outputs/validation_summary.json  per-zone statistics and correlation
        outputs/validation_boxplot.png   water-level rise per recharge zone

Method (the usual check for recharge-potential maps)
1. Depth to water below ground is taken as |dataValue| in metres.
2. For each well and year: pre-monsoon depth = mean of May readings (April if
   there are none), post-monsoon depth = mean of November readings (October
   if none).
3. Seasonal rise = pre - post (m). A larger rise means more monsoon recharge.
   Each well's rise is averaged over the years that have both readings.
4. The annual recharge zone and GWRPI are read at each well. If the map is
   right, the rise should grow from Very Low to Very High zones; the
   Spearman rank correlation between GWRPI and rise measures that.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

from backend import config

log = logging.getLogger(__name__)

WELLS_FILE = config.RAW_DIR / "wells" / "wells_raw.csv"
PRE_MONTHS = (5, 4)      # first month with data wins
POST_MONTHS = (11, 10)
MIN_WELLS = 5


def seasonal_rise(df):
    """Per-well mean pre-monsoon depth, post-monsoon depth and rise (m)."""
    import pandas as pd

    d = df.copy()
    for col in ("stationName", "district", "dataAcquisitionMode"):
        if col not in d:
            d[col] = ""
    d["time"] = pd.to_datetime(d["dataTime"], errors="coerce")
    d["depth"] = pd.to_numeric(d["dataValue"], errors="coerce").abs()
    d = d.dropna(subset=["time", "depth", "latitude", "longitude"])
    d["year"] = d["time"].dt.year
    d["month"] = d["time"].dt.month

    def pick(months):
        parts = []
        taken = None
        for m in months:
            sub = d[d["month"] == m].groupby(["stationCode", "year"])["depth"].mean()
            if taken is not None:
                sub = sub[~sub.index.isin(taken.index)]
            parts.append(sub)
            taken = pd.concat(parts)
        return taken

    pre, post = pick(PRE_MONTHS), pick(POST_MONTHS)
    both = pd.concat({"pre_depth_m": pre, "post_depth_m": post}, axis=1).dropna()
    both["rise_m"] = both["pre_depth_m"] - both["post_depth_m"]
    per_well = both.groupby(level="stationCode").agg(
        pre_depth_m=("pre_depth_m", "mean"), post_depth_m=("post_depth_m", "mean"),
        rise_m=("rise_m", "mean"), years=("rise_m", "size"))
    meta = d.groupby("stationCode").agg(
        stationName=("stationName", "first"), district=("district", "first"),
        latitude=("latitude", "first"), longitude=("longitude", "first"),
        mode=("dataAcquisitionMode", "first"))
    return meta.join(per_well, how="inner").reset_index()


def sample_rasters(lon, lat, paths: dict[str, Path]) -> dict[str, np.ndarray]:
    """Values of each raster at lon/lat points (NaN outside / nodata)."""
    import rasterio
    from rasterio.warp import transform as warp_transform

    out = {}
    for name, path in paths.items():
        with rasterio.open(path) as src:
            xs, ys = warp_transform("EPSG:4326", src.crs, list(lon), list(lat))
            vals = []
            for v in src.sample(zip(xs, ys), masked=True):
                x = v[0]
                vals.append(np.nan if np.ma.is_masked(x) or (src.nodata is not None and x == src.nodata)
                            else float(x))
        out[name] = np.asarray(vals, dtype="float64")
    return out


def validate(season: str = "annual", wells_file: Path = WELLS_FILE,
             out_dir: Path = config.OUTPUT_DIR) -> dict:
    import pandas as pd
    from scipy.stats import spearmanr

    if not Path(wells_file).exists():
        raise FileNotFoundError(f"{wells_file} not found. Run: python scripts/download_wells.py")
    zones_path = config.zones_path(season)
    gwrpi_path = config.ZONES_DIR / f"gwrpi_{season}.tif"
    if not zones_path.exists():
        raise FileNotFoundError(f"{zones_path} not found. Run the pipeline first.")

    raw = pd.read_csv(wells_file, low_memory=False)
    wells = seasonal_rise(raw)
    vals = sample_rasters(wells["longitude"], wells["latitude"],
                          {"zone": zones_path, "gwrpi": gwrpi_path})
    wells["zone"] = vals["zone"]
    wells["gwrpi"] = vals["gwrpi"]
    wells = wells[(wells["zone"] >= 1) & (wells["zone"] <= 5)].copy()
    wells["zone"] = wells["zone"].astype(int)
    wells["zone_name"] = wells["zone"].map(lambda z: config.RECHARGE_CLASSES[z]["name"])

    out_dir.mkdir(parents=True, exist_ok=True)
    wells.sort_values("zone").to_csv(out_dir / "validation_wells.csv", index=False)

    summary = {"season": season, "wells": int(len(wells)),
               "readings_used_years": int(wells["years"].sum()) if len(wells) else 0,
               "zones": []}
    for code, meta in config.RECHARGE_CLASSES.items():
        sub = wells[wells["zone"] == code]
        summary["zones"].append({
            "class": code, "name": meta["name"], "wells": int(len(sub)),
            "mean_rise_m": round(float(sub["rise_m"].mean()), 2) if len(sub) else None,
            "median_rise_m": round(float(sub["rise_m"].median()), 2) if len(sub) else None,
            "mean_post_depth_m": round(float(sub["post_depth_m"].mean()), 2) if len(sub) else None,
        })
    if len(wells) >= MIN_WELLS:
        rho, p = spearmanr(wells["gwrpi"], wells["rise_m"])
        summary["spearman_gwrpi_vs_rise"] = {"rho": round(float(rho), 3), "p_value": round(float(p), 4)}
        rho2, p2 = spearmanr(wells["gwrpi"], wells["post_depth_m"])
        summary["spearman_gwrpi_vs_post_depth"] = {"rho": round(float(rho2), 3), "p_value": round(float(p2), 4)}
    else:
        log.warning("Only %d wells with both seasons inside the map; too few for statistics", len(wells))
    (out_dir / "validation_summary.json").write_text(json.dumps(summary, indent=2))
    _boxplot(wells, out_dir / "validation_boxplot.png", season)
    return summary


def _boxplot(wells, path: Path, season: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    codes = [c for c in config.RECHARGE_CLASSES if (wells["zone"] == c).any()]
    if not codes:
        return
    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)
    data = [wells.loc[wells["zone"] == c, "rise_m"].values for c in codes]
    bp = ax.boxplot(data, patch_artist=True, widths=0.6)
    for patch, c in zip(bp["boxes"], codes):
        patch.set_facecolor(config.RECHARGE_CLASSES[c]["color"])
        patch.set_edgecolor("#444")
    ax.set_xticks(range(1, len(codes) + 1),
                  [f"{config.RECHARGE_CLASSES[c]['name']}\n(n={len(d)})" for c, d in zip(codes, data)])
    ax.axhline(0, color="#888", lw=0.8, ls="--")
    ax.set_ylabel("Pre- to post-monsoon water-level rise (m)")
    ax.set_title(f"CGWB wells vs recharge potential zone ({season})")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
