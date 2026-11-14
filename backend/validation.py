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
5. Raw rise is misleading where rock types differ: the same recharge lifts
   the water table about five times more in basalt (specific yield ~0.02)
   than in alluvium (~0.10). When data/raw/geology/lithology.gpkg exists,
   each well also gets recharge = rise x specific yield (config.SPECIFIC_YIELD,
   after GEC-2015), and the zones are checked against that instead.

Wells that do not show the shallow water table are left out: deep
piezometers (names with "(D)" or "Pz"), wells with fewer than MIN_YEARS
years of paired readings, and implausible rises (|rise| > MAX_ABS_RISE_M).
The reason is kept in validation_wells.csv ("excluded" column).
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
MIN_YEARS = 3
MAX_ABS_RISE_M = 20.0
DEEP_WELL_PATTERN = r"\((?:D|d)\)|\bPz\b|Pz$"


def exclusion_reason(row) -> str:
    """Why a well is left out of the statistics ('' if it is kept)."""
    import re
    if re.search(DEEP_WELL_PATTERN, str(row.get("stationName", ""))):
        return "deep well / piezometer"
    if row["years"] < MIN_YEARS:
        return f"fewer than {MIN_YEARS} years"
    if abs(row["rise_m"]) > MAX_ABS_RISE_M:
        return f"rise beyond {MAX_ABS_RISE_M:.0f} m"
    return ""


def specific_yield_at(lon, lat, geology_file: Path = config.RAW_GEOLOGY_FILE):
    """(lithology, specific yield) at each point, or None if no geology file."""
    if not Path(geology_file).exists():
        return None
    import geopandas as gpd

    geo = gpd.read_file(geology_file).to_crs(config.CRS_GEOGRAPHIC)
    pts = gpd.GeoDataFrame(geometry=gpd.points_from_xy(lon, lat), crs=config.CRS_GEOGRAPHIC)
    hit = gpd.sjoin(pts, geo[[config.GEOLOGY_FIELD, "geometry"]], how="left", predicate="within")
    hit = hit[~hit.index.duplicated()]
    names = hit[config.GEOLOGY_FIELD].reindex(pts.index)

    def sy(name):
        text = str(name).lower()
        for keyword, value in config.SPECIFIC_YIELD:
            if keyword in text:
                return value
        return config.SPECIFIC_YIELD_DEFAULT
    return names.tolist(), [np.nan if not isinstance(n, str) else sy(n) for n in names]


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
    wells["excluded"] = wells.apply(exclusion_reason, axis=1)

    sy = specific_yield_at(wells["longitude"].values, wells["latitude"].values)
    if sy is not None:
        wells["lithology"], wells["specific_yield"] = sy
        wells["recharge_m"] = wells["rise_m"] * wells["specific_yield"]

    out_dir.mkdir(parents=True, exist_ok=True)
    wells.sort_values(["excluded", "zone"]).to_csv(out_dir / "validation_wells.csv", index=False)

    used = wells[wells["excluded"] == ""]
    metric = "recharge_m" if "recharge_m" in used and used["recharge_m"].notna().any() else "rise_m"
    summary = {"season": season, "wells_in_map": int(len(wells)), "wells": int(len(used)),
               "excluded": wells.loc[wells["excluded"] != "", "excluded"].value_counts().to_dict(),
               "metric": metric, "zones": []}
    for code, meta in config.RECHARGE_CLASSES.items():
        s = used[used["zone"] == code]
        z = {"class": code, "name": meta["name"], "wells": int(len(s))}
        for col in ("rise_m", "recharge_m", "post_depth_m"):
            if col in s and len(s) and s[col].notna().any():
                z[f"median_{col}"] = round(float(s[col].median()), 3)
        summary["zones"].append(z)
    if len(used) >= MIN_WELLS:
        for col in ("rise_m", "recharge_m", "post_depth_m"):
            if col in used and used[col].notna().sum() >= MIN_WELLS:
                ok = used[["gwrpi", col]].dropna()
                rho, p = spearmanr(ok["gwrpi"], ok[col])
                summary[f"spearman_gwrpi_vs_{col}"] = {"rho": round(float(rho), 3),
                                                       "p_value": round(float(p), 4), "n": int(len(ok))}
    else:
        log.warning("Only %d usable wells inside the map; too few for statistics", len(used))
    (out_dir / "validation_summary.json").write_text(json.dumps(summary, indent=2))
    _boxplot(used, out_dir / "validation_boxplot.png", season, metric)
    return summary


def _boxplot(wells, path: Path, season: str, metric: str = "rise_m"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    codes = [c for c in config.RECHARGE_CLASSES if (wells["zone"] == c).any()]
    if not codes:
        return
    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)
    data = [wells.loc[wells["zone"] == c, metric].dropna().values for c in codes]
    bp = ax.boxplot(data, patch_artist=True, widths=0.6)
    for patch, c in zip(bp["boxes"], codes):
        patch.set_facecolor(config.RECHARGE_CLASSES[c]["color"])
        patch.set_edgecolor("#444")
    ax.set_xticks(range(1, len(codes) + 1),
                  [f"{config.RECHARGE_CLASSES[c]['name']}\n(n={len(d)})" for c, d in zip(codes, data)])
    ax.axhline(0, color="#888", lw=0.8, ls="--")
    ax.set_ylabel("Recharge = rise x specific yield (m)" if metric == "recharge_m"
                  else "Pre- to post-monsoon water-level rise (m)")
    ax.set_title(f"CGWB wells vs recharge potential zone ({season})")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
