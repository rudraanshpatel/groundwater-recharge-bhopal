"""Turn the files you downloaded into the exact files the pipeline reads.

Usage (from the repository root, any OS):

    python scripts/prepare_data.py tehsils          # GADM India -> boundaries/tehsils.gpkg
    python scripts/prepare_data.py watersheds       # HydroBASINS lev10 -> boundaries/watersheds.gpkg
    python scripts/prepare_data.py rain             # IMD .grd files -> imd/rain.nc
    python scripts/prepare_data.py soil             # download SoilGrids clips -> soilgrids/*.tif
    python scripts/prepare_data.py geology FILE [FILE ...] [--field COLUMN]
    python scripts/prepare_data.py lineaments FILE [FILE ...]

Every step reads from and writes to data/raw (or GWR_DATA_DIR/raw, or
--data-dir/raw). Afterwards, run scripts/verify_data.py to check the result.
See docs/DATA_DOWNLOAD.md for where each download comes from.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DISTRICTS = ["Bhopal", "Raisen", "Hoshangabad", "Narmadapuram"]
GADM_LAYER = "ADM_ADM_3"


def _find(folder: Path, patterns: list[str]) -> Path | None:
    for pattern in patterns:
        hits = sorted(folder.rglob(pattern))
        if hits:
            return hits[0]
    return None


def _write_gpkg(gdf, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    gdf.to_file(path, driver="GPKG")
    print(f"Wrote {path} ({len(gdf)} features)")


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------
def prepare_tehsils(src: str | None):
    import geopandas as gpd
    from backend import config

    folder = config.TEHSIL_FILE.parent
    path = Path(src) if src else _find(folder, ["gadm41_IND.gpkg", "gadm41_IND_3.shp", "gadm*IND*.gpkg"])
    if path is None or not path.exists():
        sys.exit(f"No GADM file found. Put gadm41_IND.gpkg (or the unzipped gadm41_IND_shp folder) in {folder}")
    print(f"Reading {path}")
    layer = None
    if path.suffix.lower() == ".gpkg":
        layers = list(gpd.list_layers(path)["name"])
        layer = GADM_LAYER if GADM_LAYER in layers else next((l for l in layers if l.endswith("3")), None)
        if layer is None:
            sys.exit(f"No level-3 layer in {path}; layers are {layers}")
    gdf = gpd.read_file(path, layer=layer, bbox=config.STUDY_AREA_BBOX)
    gdf = gdf[(gdf["NAME_1"] == "Madhya Pradesh") & gdf["NAME_2"].isin(DISTRICTS)]
    if gdf.empty:
        sys.exit("No tehsils left after filtering to Bhopal/Raisen/Hoshangabad. Is this GADM level 3?")
    keep = [c for c in ("GID_3", "NAME_1", "NAME_2", "NAME_3", "TYPE_3") if c in gdf.columns]
    gdf = gdf[keep + ["geometry"]].to_crs(config.CRS_GEOGRAPHIC).reset_index(drop=True)
    for district, grp in gdf.groupby("NAME_2"):
        print(f"  {district}: {', '.join(sorted(grp['NAME_3']))}")
    _write_gpkg(gdf, config.TEHSIL_FILE)


def prepare_watersheds(src: str | None):
    import geopandas as gpd
    from backend import config

    folder = config.WATERSHED_FILE.parent
    path = Path(src) if src else _find(folder, ["hybas_as_lev10_v1c.shp", "hybas_*lev10*.shp"])
    if path is None or not path.exists():
        sys.exit(f"No HydroBASINS level-10 shapefile (hybas_as_lev10_v1c.shp) found under {folder}")
    print(f"Reading {path}")
    gdf = gpd.read_file(path, bbox=config.STUDY_AREA_BBOX)
    gdf = gdf.to_crs(config.CRS_GEOGRAPHIC).reset_index(drop=True)
    _write_gpkg(gdf, config.WATERSHED_FILE)


# ---------------------------------------------------------------------------
# Rainfall
# ---------------------------------------------------------------------------
def prepare_rain(start: int | None, end: int | None):
    import imdlib as imd
    from backend import config
    from backend.preprocessing.rainfall import _pick_variable, clean_rainfall, subset_to_study_area

    folder = config.RAW_IMD_DIR
    files = sorted(folder.rglob("*.grd")) + sorted(folder.rglob("*.GRD"))
    years = {}
    for f in files:
        m = re.search(r"(19|20)\d{2}", f.name)
        if m:
            years[int(m.group(0))] = f
    if not years:
        sys.exit(f"No IMD .grd files in {folder}. Download them first (docs/DATA_DOWNLOAD.md section 3).")
    start = start or min(years)
    end = end or max(years)
    missing = [y for y in range(start, end + 1) if y not in years]
    if missing:
        sys.exit(f"Missing years {missing} in {folder}")

    # imdlib expects either IMD's own names (Rainfall_ind2014_rfp25.grd) or <year>.grd
    sample = years[start]
    fn_format = "yearwise" if re.fullmatch(r"\d{4}\.grd", sample.name, re.I) else None
    if fn_format is None and not sample.name.startswith("Rainfall_ind"):
        sys.exit(f"Unrecognised file name {sample.name}: rename files to Rainfall_ind<year>_rfp25.grd")
    file_dir = sample.parent
    if fn_format == "yearwise" and file_dir.name == "rain":
        file_dir = file_dir.parent
    print(f"Reading {start}-{end} from {file_dir}")
    data = imd.open_data("rain", start, end, fn_format, str(file_dir))
    da = subset_to_study_area(clean_rainfall(_pick_variable(data.get_xarray())))
    config.RAW_RAINFALL_FILE.parent.mkdir(parents=True, exist_ok=True)
    da.to_dataset(name="rain").to_netcdf(config.RAW_RAINFALL_FILE)
    print(f"Wrote {config.RAW_RAINFALL_FILE} ({da.sizes['time']} days, "
          f"{da.sizes['lat']} x {da.sizes['lon']} cells)")


# ---------------------------------------------------------------------------
# Soil
# ---------------------------------------------------------------------------
SOIL_VRT = "https://files.isric.org/soilgrids/latest/data/{p}/{p}_{d}_mean.vrt"
SOIL_WCS = "https://maps.isric.org/mapserv"


def soil_wcs_url(prop: str, depth: str, bbox) -> str:
    """ISRIC WCS GetCoverage URL for one layer clipped to bbox, in EPSG:4326."""
    crs = "http://www.opengis.net/def/crs/EPSG/0/4326"
    params = [
        ("map", f"/map/{prop}.map"), ("SERVICE", "WCS"), ("VERSION", "2.0.1"),
        ("REQUEST", "GetCoverage"), ("COVERAGEID", f"{prop}_{depth}_mean"),
        ("FORMAT", "GEOTIFF_INT16"),
        ("SUBSET", f"long({bbox[0]},{bbox[2]})"), ("SUBSET", f"lat({bbox[1]},{bbox[3]})"),
        ("SUBSETTINGCRS", crs), ("OUTPUTCRS", crs),
    ]
    return SOIL_WCS + "?" + urllib.parse.urlencode(params, safe="/:(),")


def clip_raster(src_path: str, dst: Path, bbox, pad_deg: float = 0.05):
    """Read the bbox window of a (possibly remote) raster and save it as GeoTIFF."""
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window, from_bounds

    minx, miny, maxx, maxy = bbox
    with rasterio.open(src_path) as src:
        b = transform_bounds("EPSG:4326", src.crs, minx - pad_deg, miny - pad_deg,
                             maxx + pad_deg, maxy + pad_deg, densify_pts=51)
        win = from_bounds(*b, transform=src.transform).round_offsets().round_lengths()
        win = win.intersection(Window(0, 0, src.width, src.height))
        arr = src.read(1, window=win)
        profile = src.profile.copy()
        for k in ("blockxsize", "blockysize", "tiled", "interleave"):
            profile.pop(k, None)
        profile.update(driver="GTiff", width=arr.shape[1], height=arr.shape[0],
                       transform=src.window_transform(win), compress="deflate")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(dst, "w", **profile) as out:
        out.write(arr, 1)


def prepare_soil(overwrite: bool):
    import rasterio
    from backend import config
    from backend.preprocessing.soil import DEPTHS, FRACTIONS

    bbox = config.STUDY_AREA_BBOX
    failed = []
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", GDAL_HTTP_MAX_RETRY="3",
                      GDAL_HTTP_RETRY_DELAY="2", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".vrt,.tif"):
        for prop in FRACTIONS:
            for depth in DEPTHS:
                dst = config.RAW_SOIL_DIR / f"{prop}_{depth}.tif"
                if dst.exists() and not overwrite:
                    print(f"  {dst.name}: already there, skipped")
                    continue
                print(f"  {dst.name}: downloading ...", end=" ", flush=True)
                try:
                    clip_raster("/vsicurl/" + SOIL_VRT.format(p=prop, d=depth), dst, bbox)
                    print("ok")
                    continue
                except Exception as exc:
                    print(f"file server failed ({exc}); trying the map service ...", end=" ", flush=True)
                try:
                    with urllib.request.urlopen(soil_wcs_url(prop, depth, bbox), timeout=300) as r:
                        body = r.read()
                    if not body.startswith((b"II*\x00", b"MM\x00*")):
                        raise ValueError(body[:200].decode("utf-8", "replace"))
                    dst.write_bytes(body)
                    print("ok")
                except Exception as exc:
                    print(f"FAILED ({exc})")
                    failed.append(dst.name)
    if failed:
        print("\nCould not download: " + ", ".join(failed))
        print("Try again later, or use the browser links in docs/DATA_DOWNLOAD.md section 5.")
        sys.exit(1)
    print(f"All SoilGrids files are in {config.RAW_SOIL_DIR}")


# ---------------------------------------------------------------------------
# Geology and lineaments
# ---------------------------------------------------------------------------
def _read_many(paths: list[str]):
    import geopandas as gpd
    import pandas as pd
    from backend import config

    frames = []
    for p in paths:
        gdf = gpd.read_file(p)
        if gdf.crs is None:
            sys.exit(f"{p} has no coordinate system (missing .prj file?)")
        frames.append(gdf.to_crs(config.CRS_GEOGRAPHIC))
        print(f"Read {p}: {len(gdf)} features")
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=config.CRS_GEOGRAPHIC)


def prepare_geology(paths: list[str], field: str | None):
    from shapely.geometry import box
    from backend import config
    from backend.preprocessing.geology import lithology_rank

    gdf = _read_many(paths)
    import pandas as pd
    text_cols = [c for c in gdf.columns if c != "geometry"
                 and not pd.api.types.is_numeric_dtype(gdf[c])]
    if field is None:
        guesses = [c for c in text_cols if re.search(r"lith|rock|desc|formation|group|unit", c, re.I)]
        print("\nText columns and example values:")
        for c in text_cols:
            vals = gdf[c].dropna().astype(str).unique()[:4]
            print(f"  {c:20} {' | '.join(vals)}")
        hint = f" (likely: {', '.join(guesses)})" if guesses else ""
        sys.exit(f"\nPick the column that names the rock type and run again with --field <column>{hint}")
    if field not in gdf.columns:
        sys.exit(f"No column '{field}'. Columns: {list(gdf.columns)}")
    gdf = gdf[gdf.intersects(box(*config.STUDY_AREA_BBOX))].copy()
    gdf[config.GEOLOGY_FIELD] = gdf[field].astype(str).str.strip()
    gdf = gdf[[config.GEOLOGY_FIELD, "geometry"]].reset_index(drop=True)
    print("\nRank per lithology (5 = best recharge):")
    for name in sorted(gdf[config.GEOLOGY_FIELD].unique()):
        print(f"  {lithology_rank(name)}  {name}")
    _write_gpkg(gdf, config.RAW_GEOLOGY_FILE)


def prepare_lineaments(paths: list[str]):
    from backend import config

    gdf = _read_many(paths)
    gdf = gdf[gdf.geom_type.isin(["LineString", "MultiLineString"])].reset_index(drop=True)
    _write_gpkg(gdf[["geometry"]], config.RAW_LINEAMENT_FILE)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", help="data folder (default: data, or GWR_DATA_DIR)")
    sub = parser.add_subparsers(dest="step", required=True)
    p = sub.add_parser("tehsils", help="GADM India level 3 -> tehsils.gpkg")
    p.add_argument("source", nargs="?", help="GADM .gpkg or gadm41_IND_3.shp (default: search boundaries/)")
    p = sub.add_parser("watersheds", help="HydroBASINS level 10 -> watersheds.gpkg")
    p.add_argument("source", nargs="?", help="hybas_as_lev10_v1c.shp (default: search boundaries/)")
    p = sub.add_parser("rain", help="IMD yearly .grd files -> rain.nc")
    p.add_argument("--start", type=int)
    p.add_argument("--end", type=int)
    p = sub.add_parser("soil", help="download SoilGrids clay/sand/silt clips")
    p.add_argument("--overwrite", action="store_true")
    p = sub.add_parser("geology", help="lithology polygons -> lithology.gpkg")
    p.add_argument("sources", nargs="+")
    p.add_argument("--field", help="column holding the rock type name")
    p = sub.add_parser("lineaments", help="lineament lines -> lineaments.gpkg")
    p.add_argument("sources", nargs="+")
    args = parser.parse_args(argv)
    if args.data_dir:
        os.environ["GWR_DATA_DIR"] = args.data_dir

    if args.step == "tehsils":
        prepare_tehsils(args.source)
    elif args.step == "watersheds":
        prepare_watersheds(args.source)
    elif args.step == "rain":
        prepare_rain(args.start, args.end)
    elif args.step == "soil":
        prepare_soil(args.overwrite)
    elif args.step == "geology":
        prepare_geology(args.sources, args.field)
    elif args.step == "lineaments":
        prepare_lineaments(args.sources)
    return 0


if __name__ == "__main__":
    sys.exit(main())
