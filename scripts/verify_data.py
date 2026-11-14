"""Check that the raw downloads are in the right place and cover the study area.

Usage (from the repository root, any OS):

    python scripts/verify_data.py              # check every layer
    python scripts/verify_data.py dem soil     # check only some layers
    python scripts/verify_data.py --data-dir data/demo

Layers: tehsils, watersheds, dem, rainfall, lulc, soil, geology, lineaments.

Each layer gets PASS, WARN or FAIL. Coverage is measured by sampling a grid
of points (every 0.05 deg) over the three districts (the tehsil polygons, or
the whole download box if tehsils.gpkg is not there yet) and checking that
every point has a valid value. The script only reads files; it never changes
anything.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LAYERS = ("tehsils", "watersheds", "dem", "rainfall", "lulc", "soil", "geology", "lineaments")
DISTRICTS = {"Bhopal", "Raisen", "Hoshangabad", "Narmadapuram"}
SAMPLE_STEP = 0.05          # degrees between coverage sample points
MIN_COVERAGE = 99.0         # percent of sample points that must have data


class Report:
    def __init__(self, layer: str):
        self.layer = layer
        self.lines: list[tuple[str, str]] = []

    def ok(self, msg):
        self.lines.append(("PASS", msg))

    def warn(self, msg):
        self.lines.append(("WARN", msg))

    def fail(self, msg):
        self.lines.append(("FAIL", msg))

    def info(self, msg):
        self.lines.append(("    ", msg))

    @property
    def status(self) -> str:
        tags = {t for t, _ in self.lines}
        return "FAIL" if "FAIL" in tags else "WARN" if "WARN" in tags else "PASS"

    def print(self):
        print(f"\n== {self.layer} [{self.status}]")
        for tag, msg in self.lines:
            print(f"  {tag}  {msg}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _area_of_interest():
    """(geometry, label) used for coverage checks: tehsil union or the bbox."""
    from shapely.geometry import box
    from backend import config

    if config.TEHSIL_FILE.exists():
        try:
            import geopandas as gpd
            gdf = gpd.read_file(config.TEHSIL_FILE).to_crs(config.CRS_GEOGRAPHIC)
            if len(gdf):
                return gdf.geometry.union_all(), "the three districts (tehsils.gpkg)"
        except Exception:
            pass
    return box(*config.STUDY_AREA_BBOX), "the download box"


def _sample_points(aoi):
    import shapely
    minx, miny, maxx, maxy = aoi.bounds
    xs = np.arange(minx + SAMPLE_STEP / 2, maxx, SAMPLE_STEP)
    ys = np.arange(miny + SAMPLE_STEP / 2, maxy, SAMPLE_STEP)
    gx, gy = np.meshgrid(xs, ys)
    gx, gy = gx.ravel(), gy.ravel()
    inside = shapely.contains_xy(aoi, gx, gy)
    return gx[inside], gy[inside]


def _raster_coverage(paths, lon, lat, valid=lambda v: np.isfinite(v)):
    """Fraction of points with a valid value in any raster, and the values found."""
    import rasterio
    from rasterio.warp import transform as warp_transform

    values = np.full(lon.shape, np.nan)
    for path in paths:
        with rasterio.open(path) as src:
            if src.crs is None:
                continue
            xs, ys = warp_transform("EPSG:4326", src.crs, lon.tolist(), lat.tolist())
            xs, ys = np.asarray(xs), np.asarray(ys)
            b = src.bounds
            todo = np.isnan(values) & (xs >= b.left) & (xs <= b.right) & (ys >= b.bottom) & (ys <= b.top)
            if not todo.any():
                continue
            got = []
            for v in src.sample(zip(xs[todo], ys[todo]), masked=True):
                got.append(np.nan if np.ma.is_masked(v[0]) else float(v[0]))
            values[np.flatnonzero(todo)] = got
    ok = np.isfinite(values) & valid(np.nan_to_num(values, nan=-1e30))
    return ok.mean() * 100 if len(ok) else 0.0, values[ok]


def _check_coverage(rep: Report, pct: float, where: str):
    msg = f"{pct:.1f}% of sample points over {where} have data"
    if pct >= MIN_COVERAGE:
        rep.ok(msg)
    elif pct >= 90:
        rep.warn(msg + " (a tile may be missing or have gaps)")
    else:
        rep.fail(msg)


def _check_crs(rep: Report, crs, path):
    if crs is None:
        rep.fail(f"{path.name} has no coordinate system (CRS)")
        return False
    return True


# ---------------------------------------------------------------------------
# Vector layers
# ---------------------------------------------------------------------------
def check_tehsils(rep: Report):
    import geopandas as gpd
    from backend import config

    path = config.TEHSIL_FILE
    if not path.exists():
        rep.fail(f"{path} not found. Run: python scripts/prepare_data.py tehsils")
        return
    gdf = gpd.read_file(path)
    if not _check_crs(rep, gdf.crs, path):
        return
    rep.info(f"{len(gdf)} polygons, CRS {gdf.crs.to_string()}")
    if config.TEHSIL_NAME_FIELD not in gdf.columns:
        rep.fail(f"field '{config.TEHSIL_NAME_FIELD}' missing; columns are {list(gdf.columns)}")
        return
    rep.ok(f"field '{config.TEHSIL_NAME_FIELD}' present")
    if "NAME_2" in gdf.columns:
        found = set(gdf["NAME_2"].astype(str))
        for d in sorted(found):
            names = sorted(gdf.loc[gdf["NAME_2"] == d, config.TEHSIL_NAME_FIELD].astype(str))
            rep.info(f"{d}: {len(names)} tehsils ({', '.join(names)})")
        missing = {"Bhopal", "Raisen"} - found
        if not found & {"Hoshangabad", "Narmadapuram"}:
            missing.add("Hoshangabad/Narmadapuram")
        if missing:
            rep.fail(f"districts missing: {', '.join(sorted(missing))}")
        else:
            rep.ok("all three districts present")
        extra = found - DISTRICTS
        if extra:
            rep.warn(f"other districts included too: {', '.join(sorted(extra))}")
    if not 15 <= len(gdf) <= 40:
        rep.warn(f"expected about 20-30 tehsils, found {len(gdf)}")
    b = gdf.to_crs(config.CRS_GEOGRAPHIC).total_bounds
    rep.info(f"extent lon {b[0]:.2f}-{b[2]:.2f}, lat {b[1]:.2f}-{b[3]:.2f}")
    bx = config.STUDY_AREA_BBOX
    if b[0] < bx[0] or b[1] < bx[1] or b[2] > bx[2] or b[3] > bx[3]:
        rep.warn("tehsils reach outside the download box; other layers will not cover the edges")
    else:
        rep.ok("tehsils sit inside the download box")


def check_watersheds(rep: Report):
    import geopandas as gpd
    from shapely.geometry import box
    from backend import config

    path = config.WATERSHED_FILE
    if not path.exists():
        shp = sorted(config.WATERSHED_FILE.parent.rglob("hybas_*lev10*.shp"))
        hint = f" Found {shp[0].name}; convert it with" if shp else " Run"
        rep.fail(f"{path} not found.{hint}: python scripts/prepare_data.py watersheds")
        return
    gdf = gpd.read_file(path)
    if not _check_crs(rep, gdf.crs, path):
        return
    if config.WATERSHED_ID_FIELD not in gdf.columns:
        rep.fail(f"field '{config.WATERSHED_ID_FIELD}' missing; columns are {list(gdf.columns)}")
        return
    gdf = gdf.to_crs(config.CRS_GEOGRAPHIC)
    n = int(gdf.intersects(box(*config.STUDY_AREA_BBOX)).sum())
    rep.info(f"{len(gdf)} polygons, {n} touch the download box")
    (rep.ok if n >= 50 else rep.fail)(f"{n} watersheds in the study area (level 10 gives several hundred)")
    aoi, where = _area_of_interest()
    covered = gdf.geometry.union_all().intersection(aoi).area / aoi.area * 100
    (rep.ok if covered >= MIN_COVERAGE else rep.warn)(f"watersheds cover {covered:.1f}% of {where}")


def check_geology(rep: Report):
    import geopandas as gpd
    from backend import config
    from backend.preprocessing.geology import lithology_rank

    path = config.RAW_GEOLOGY_FILE
    if not path.exists():
        rep.fail(f"{path} not found. See docs/DATA_DOWNLOAD.md section 6")
        return
    gdf = gpd.read_file(path)
    if not _check_crs(rep, gdf.crs, path):
        return
    field = config.GEOLOGY_FIELD
    if field not in gdf.columns:
        rep.fail(f"text field '{field}' missing; columns are {list(gdf.columns)}. "
                 "Fix with: python scripts/prepare_data.py geology <file> --field <column>")
        return
    rep.info(f"{len(gdf)} polygons, CRS {gdf.crs.to_string()}")
    gdf = gdf.to_crs(config.CRS_GEOGRAPHIC)
    aoi, where = _area_of_interest()
    covered = gdf.geometry.union_all().intersection(aoi).area / aoi.area * 100
    msg = f"polygons cover {covered:.1f}% of {where}"
    (rep.ok if covered >= 98 else rep.warn if covered >= 85 else rep.fail)(msg)
    names = gdf[field].astype(str)
    unmatched = sorted({n for n in names if not any(k in n.lower() for k, _ in config.GEOLOGY_RANKS)})
    for name in sorted(set(names))[:25]:
        rep.info(f"rank {lithology_rank(name)}  {name}")
    if len(set(names)) > 25:
        rep.info(f"... and {len(set(names)) - 25} more names")
    if unmatched:
        rep.warn(f"{len(unmatched)} names match no keyword and get the default rank "
                 f"{config.GEOLOGY_DEFAULT_RANK}; add keywords to GEOLOGY_RANKS in backend/config.py")
    else:
        rep.ok("every lithology name matches a keyword in GEOLOGY_RANKS")


def check_lineaments(rep: Report):
    import geopandas as gpd
    from backend import config

    path = config.RAW_LINEAMENT_FILE
    if not path.exists():
        rep.ok("not present (optional): lineaments will be extracted from the DEM")
        return
    gdf = gpd.read_file(path)
    if not _check_crs(rep, gdf.crs, path):
        return
    types = set(gdf.geom_type)
    (rep.ok if types <= {"LineString", "MultiLineString"} else rep.fail)(f"geometry types: {sorted(types)}")
    g = gdf.to_crs(config.CRS_PROJECTED)
    rep.info(f"{len(gdf)} lines, total length {g.length.sum() / 1000:.0f} km")


# ---------------------------------------------------------------------------
# Raster layers
# ---------------------------------------------------------------------------
def check_dem(rep: Report):
    from backend import config

    tiles = sorted(config.RAW_DEM_DIR.glob("*.tif"))
    if not tiles:
        rep.fail(f"no .tif files in {config.RAW_DEM_DIR}")
        return
    rep.info(f"{len(tiles)} tiles")
    expected = {f"N{la}_00_E{lo}_00" for la in (21, 22, 23) for lo in ("076", "077", "078")}
    names = " ".join(t.name for t in tiles)
    if "Copernicus_DSM" in names:
        missing = sorted(e for e in expected if e not in names)
        (rep.fail if missing else rep.ok)(
            "missing Copernicus tiles: " + ", ".join(missing) if missing else "all 9 Copernicus tiles present")
    aoi, where = _area_of_interest()
    lon, lat = _sample_points(aoi)
    pct, vals = _raster_coverage(tiles, lon, lat, valid=lambda v: v > -500)
    _check_coverage(rep, pct, where)
    if len(vals):
        lo, hi = np.percentile(vals, [0, 100])
        rep.info(f"elevation {lo:.0f}-{hi:.0f} m (expected roughly 250-1350 m)")
        if lo < 100 or hi > 1500:
            rep.warn("elevation range looks odd: check units and nodata")
        else:
            rep.ok("elevation range is plausible")


def check_lulc(rep: Report):
    from backend import config

    tiles = sorted(config.RAW_LULC_DIR.glob("*.tif"))
    if not tiles:
        rep.fail(f"no .tif files in {config.RAW_LULC_DIR}")
        return
    rep.info(f"{len(tiles)} tiles: {', '.join(t.name for t in tiles)}")
    aoi, where = _area_of_interest()
    lon, lat = _sample_points(aoi)
    pct, vals = _raster_coverage(tiles, lon, lat, valid=lambda v: v > 0)
    _check_coverage(rep, pct, where)
    if len(vals):
        codes, counts = np.unique(vals.astype(int), return_counts=True)
        unknown = [int(c) for c in codes if c not in config.LULC_RANKS]
        (rep.fail if unknown else rep.ok)(
            f"unknown class codes {unknown} (is this ESA WorldCover?)" if unknown
            else "all values are WorldCover class codes")
        for c, n in sorted(zip(codes, counts), key=lambda t: -t[1]):
            rep.info(f"{config.LULC_NAMES.get(int(c), c)}: {n / counts.sum() * 100:.1f}%")


def check_soil(rep: Report):
    from backend import config
    from backend.preprocessing.soil import DEPTHS, FRACTIONS

    missing = [f"{f}_{d}.tif" for f in FRACTIONS for d in DEPTHS
               if not (config.RAW_SOIL_DIR / f"{f}_{d}.tif").exists()]
    if missing:
        rep.fail(f"missing in {config.RAW_SOIL_DIR}: {', '.join(missing)}")
        if len(missing) == 9:
            return
    else:
        rep.ok("all 9 files present")
    aoi, where = _area_of_interest()
    lon, lat = _sample_points(aoi)
    sums, coverage = {}, {}
    for depth in DEPTHS:
        parts = []
        for frac in FRACTIONS:
            path = config.RAW_SOIL_DIR / f"{frac}_{depth}.tif"
            if not path.exists():
                continue
            vals = _raster_values(path, lon, lat)
            coverage[path.name] = np.isfinite(vals).mean() * 100 if len(vals) else 0.0
            if np.isfinite(vals).any() and np.nanmax(vals) <= 100:
                rep.warn(f"{path.name} max is {np.nanmax(vals):.0f}: expected g/kg (0-1000), looks like %")
            parts.append(vals)
        if len(parts) == 3:
            sums[depth] = np.nansum(parts, axis=0)[np.all(np.isfinite(parts), axis=0)]
    if coverage:
        worst = min(coverage, key=coverage.get)
        pct = coverage[worst]
        if 90 <= pct < MIN_COVERAGE:
            rep.ok(f"{pct:.1f}% of sample points over {where} have data. SoilGrids leaves water and "
                   "built-up areas blank; the pipeline fills them from the nearest soil")
        else:
            _check_coverage(rep, pct, f"{where} (worst file: {worst})")
    for depth, s in sums.items():
        if len(s):
            med = np.median(s)
            msg = f"{depth}: clay+sand+silt median {med:.0f} g/kg (should be about 1000)"
            (rep.ok if 950 <= med <= 1050 else rep.warn)(msg)


def _raster_values(path, lon, lat):
    import rasterio
    from rasterio.warp import transform as warp_transform
    with rasterio.open(path) as src:
        xs, ys = warp_transform("EPSG:4326", src.crs, lon.tolist(), lat.tolist())
        out = []
        for v in src.sample(zip(xs, ys), masked=True):
            out.append(np.nan if np.ma.is_masked(v[0]) or v[0] < 0 else float(v[0]))
    return np.asarray(out, dtype="float64")


def check_rainfall(rep: Report):
    import xarray as xr
    from backend import config
    from backend.preprocessing.rainfall import _pick_variable, clean_rainfall

    path = config.RAW_RAINFALL_FILE
    if not path.exists():
        grd = sorted(config.RAW_IMD_DIR.rglob("*.grd"))
        hint = (f" Found {len(grd)} .grd files; build it with: python scripts/prepare_data.py rain"
                if grd else " See docs/DATA_DOWNLOAD.md section 3")
        rep.fail(f"{path} not found.{hint}")
        return
    with xr.open_dataset(path) as ds:
        da = clean_rainfall(_pick_variable(ds)).load()
    t0, t1 = str(da.time.values[0])[:10], str(da.time.values[-1])[:10]
    rep.info(f"{da.sizes['time']} days, {t0} to {t1}, grid {da.sizes['lat']} x {da.sizes['lon']}")
    y0, y1 = config.RAINFALL_YEARS
    if t0 <= f"{y0}-01-01" and t1 >= f"{y1}-12-31":
        rep.ok(f"covers {y0}-{y1}")
    else:
        rep.warn(f"expected {y0}-01-01 to {y1}-12-31")
    minx, miny, maxx, maxy = config.STUDY_AREA_BBOX
    lat, lon = da.lat.values, da.lon.values
    if lon.min() <= minx and lon.max() >= maxx and lat.min() <= miny and lat.max() >= maxy:
        rep.ok(f"grid lon {lon.min():.2f}-{lon.max():.2f}, lat {lat.min():.2f}-{lat.max():.2f} covers the box")
    else:
        rep.fail(f"grid lon {lon.min():.2f}-{lon.max():.2f}, lat {lat.min():.2f}-{lat.max():.2f} "
                 "does not cover the box")
    box_da = da.sel(lat=slice(miny, maxy), lon=slice(minx, maxx))
    missing = float(box_da.isnull().all("time").mean()) * 100
    (rep.ok if missing < 1 else rep.fail)(f"{missing:.1f}% of cells in the box have no data")
    years = box_da.time.dt.year
    annual = box_da.groupby(years).sum("time", min_count=1).mean(("lat", "lon"))
    mean_annual = float(annual.mean())
    msg = f"mean annual rainfall over the box {mean_annual:.0f} mm (expected about 1000-1400 mm)"
    (rep.ok if 700 <= mean_annual <= 1800 else rep.warn)(msg)


CHECKS = {
    "tehsils": check_tehsils, "watersheds": check_watersheds, "dem": check_dem,
    "rainfall": check_rainfall, "lulc": check_lulc, "soil": check_soil,
    "geology": check_geology, "lineaments": check_lineaments,
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("layers", nargs="*", metavar="layer",
                        help=f"layers to check (default: all). One of: {', '.join(LAYERS)}")
    parser.add_argument("--data-dir", help="data folder (default: data, or GWR_DATA_DIR)")
    args = parser.parse_args(argv)
    bad = [x for x in args.layers if x not in LAYERS]
    if bad:
        parser.error(f"unknown layer(s) {bad}; choose from {', '.join(LAYERS)}")
    if args.data_dir:
        os.environ["GWR_DATA_DIR"] = args.data_dir

    from backend import config
    print(f"Data folder: {config.RAW_DIR}")
    print(f"Download box: lon {config.STUDY_AREA_BBOX[0]}-{config.STUDY_AREA_BBOX[2]}, "
          f"lat {config.STUDY_AREA_BBOX[1]}-{config.STUDY_AREA_BBOX[3]}")

    reports = []
    for layer in args.layers or LAYERS:
        rep = Report(layer)
        try:
            CHECKS[layer](rep)
        except Exception as exc:      # report and carry on with the other layers
            rep.fail(f"could not read: {type(exc).__name__}: {exc}")
        rep.print()
        reports.append(rep)

    print("\nSummary")
    for rep in reports:
        print(f"  {rep.status:4}  {rep.layer}")
    return 1 if any(r.status == "FAIL" for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
