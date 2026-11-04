"""Generate SYNTHETIC input data so the whole pipeline and web app can be run
before the real downloads are ready.

Nothing produced here is real data. The terrain, rainfall, land cover, soil,
geology and boundaries are invented, loosely shaped like the study area
(Vindhyan plateau in the north, Narmada valley in the middle, Satpura hills
in the south) so the maps look plausible. Never use these outputs in a report.

Usage (from the repository root):

    python scripts/make_synthetic_data.py                 # writes data/demo/raw
    GWR_DATA_DIR=data/demo GWR_RESOLUTION=250 python -m backend.pipeline all
    GWR_DATA_DIR=data/demo GWR_RESOLUTION=250 uvicorn backend.app:app
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import config  # noqa: E402

BBOX = config.STUDY_AREA_BBOX
PAD = 0.15                     # degrees of padding around the study area
RES = 0.005                    # ~550 m pixels for the synthetic rasters


def _grid():
    minx, miny, maxx, maxy = BBOX[0] - PAD, BBOX[1] - PAD, BBOX[2] + PAD, BBOX[3] + PAD
    w, h = int(round((maxx - minx) / RES)), int(round((maxy - miny) / RES))
    tf = from_origin(minx, maxy, RES, RES)
    lon = minx + RES * (np.arange(w) + 0.5)
    lat = maxy - RES * (np.arange(h) + 0.5)
    return tf, *np.meshgrid(lon, lat)


def _smooth_noise(shape, sigma, rng):
    n = ndimage.gaussian_filter(rng.standard_normal(shape), sigma)
    return (n - n.mean()) / (n.std() + 1e-9)


def _write(path: Path, arr, tf, dtype, nodata=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", width=arr.shape[1], height=arr.shape[0],
                       count=1, dtype=dtype, crs="EPSG:4326", transform=tf, nodata=nodata,
                       compress="deflate") as dst:
        dst.write(arr.astype(dtype), 1)


def narmada_lat(lon):
    """Synthetic river course: a gentle curve through the valley."""
    return 22.75 + 0.12 * np.sin((lon - 76.5) * 1.6)


def make_dem(raw: Path, rng):
    tf, lon, lat = _grid()
    river = narmada_lat(lon)
    d = lat - river
    base = np.where(d > 0, 300 + 220 * np.tanh(d / 0.35),          # rises to the northern plateau
                    300 + 650 * np.tanh(-d / 0.25) ** 2)             # steeper Satpura hills
    base += 180 * np.exp(-((lon - 78.4) ** 2 + (lat - 22.45) ** 2) / 0.05)   # a massif in the SE
    dem = base + 40 * _smooth_noise(lon.shape, 6, rng) + 12 * _smooth_noise(lon.shape, 1.5, rng)
    _write(raw / "dem" / "synthetic_dem.tif", dem, tf, "float32", -9999)
    return lon, lat


def make_rainfall(raw: Path, rng, years=(2015, 2018)):
    lats = np.arange(21.75, 24.26, 0.25)
    lons = np.arange(76.25, 79.26, 0.25)
    time = pd.date_range(f"{years[0]}-01-01", f"{years[1]}-12-31", freq="D")
    LON, LAT = np.meshgrid(lons, lats)
    # More rain in the south-east (Pachmarhi side), less in the north-west.
    spatial = 1.0 + 0.35 * (22.9 - LAT) / 1.0 + 0.10 * (LON - 77.7)
    doy = time.dayofyear.values
    # Seasonal cycle peaking in mid-July (monsoon), small winter rain.
    monsoon = np.exp(-0.5 * ((doy - 200) / 28.0) ** 2)
    clim = 0.3 + 14.0 * monsoon                                     # mm/day
    wet = rng.random((len(time), *LAT.shape)) < (0.08 + 0.6 * monsoon)[:, None, None]
    amount = rng.gamma(0.8, 1.0, size=wet.shape) * clim[:, None, None] / (0.08 + 0.6 * monsoon)[:, None, None]
    rain = np.where(wet, amount, 0.0) * spatial[None]
    rain[:, 0, 0] = -999.0                                          # one IMD-style missing cell
    import xarray as xr
    ds = xr.Dataset({"rain": (("time", "lat", "lon"), rain.astype("float32"))},
                    coords={"time": time, "lat": lats, "lon": lons})
    out = raw / "imd" / "rain.nc"
    out.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(out)


def make_lulc(raw: Path, lon, lat, rng):
    tf, _, _ = _grid()
    n1 = _smooth_noise(lon.shape, 8, rng)
    n2 = _smooth_noise(lon.shape, 4, rng)
    cls = np.full(lon.shape, 40, dtype="uint8")                     # cropland
    hills = (lat - narmada_lat(lon)) < -0.25
    cls[(n1 > 0.6) | (hills & (n1 > -0.4))] = 10                    # forest
    cls[(n2 > 1.2) & (cls == 40)] = 20                              # shrub
    cls[(n2 < -1.5)] = 60                                           # bare
    cls[np.abs(lat - narmada_lat(lon)) < 0.012] = 80                # river
    cls[((lon - 77.41) ** 2 + (lat - 23.26) ** 2) < 0.012] = 50     # Bhopal city
    cls[((lon - 77.36) ** 2 + (lat - 23.24) ** 2) < 0.0015] = 80    # lake
    cls[((lon - 77.72) ** 2 + (lat - 22.75) ** 2) < 0.002] = 50     # Narmadapuram town
    _write(raw / "worldcover" / "synthetic_worldcover.tif", cls, tf, "uint8", 0)


def make_soil(raw: Path, lon, lat, rng):
    tf, _, _ = _grid()
    near_river = np.exp(-((lat - narmada_lat(lon)) / 0.2) ** 2)
    for depth, extra_clay in (("0-5cm", 0), ("5-15cm", 20), ("15-30cm", 40)):
        clay = 380 - 180 * near_river + 60 * _smooth_noise(lon.shape, 5, rng) + extra_clay
        sand = 280 + 260 * near_river + 60 * _smooth_noise(lon.shape, 5, rng)
        clay, sand = np.clip(clay, 50, 700), np.clip(sand, 50, 800)
        silt = np.clip(1000 - clay - sand, 20, 800)
        for name, arr in (("clay", clay), ("sand", sand), ("silt", silt)):
            _write(raw / "soilgrids" / f"{name}_{depth}.tif", arr, tf, "int16", -32768)


def make_vectors(raw: Path):
    import geopandas as gpd
    from shapely.geometry import Polygon, box

    minx, miny, maxx, maxy = BBOX
    xs = np.linspace(minx - PAD, maxx + PAD, 200)
    river = list(zip(xs, narmada_lat(xs)))
    north = Polygon([(minx - PAD, maxy + PAD), *[(x, y + 0.2) for x, y in river], (maxx + PAD, maxy + PAD)])
    valley = Polygon([*[(x, y + 0.2) for x, y in river], *[(x, y - 0.18) for x, y in reversed(river)]])
    south = Polygon([*[(x, y - 0.18) for x, y in river], (maxx + PAD, miny - PAD), (minx - PAD, miny - PAD)])
    plateau_split = box(minx - PAD, 23.45, maxx + PAD, maxy + PAD)
    geo = gpd.GeoDataFrame({
        "lithology": ["Deccan Trap basalt", "Vindhyan sandstone and shale",
                      "Narmada valley alluvium", "Gondwana sandstone (Satpura)"],
        "geometry": [north.difference(plateau_split), north.intersection(plateau_split),
                     valley, south],
    }, crs="EPSG:4326")
    (raw / "geology").mkdir(parents=True, exist_ok=True)
    geo.to_file(raw / "geology" / "lithology.gpkg", driver="GPKG")

    # Synthetic administrative units: a 3 x 3 grid.
    rows, cols = 3, 3
    dx, dy = (maxx - minx) / cols, (maxy - miny) / rows
    tehsils = []
    for r in range(rows):
        for c in range(cols):
            tehsils.append({
                "NAME_2": ["North district", "Central district", "South district"][r] + " (synthetic)",
                "NAME_3": f"Demo tehsil {r * cols + c + 1}",
                "geometry": box(minx + c * dx, maxy - (r + 1) * dy, minx + (c + 1) * dx, maxy - r * dy),
            })
    (raw / "boundaries").mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(tehsils, crs="EPSG:4326").to_file(raw / "boundaries" / "tehsils.gpkg", driver="GPKG")

    sheds = []
    n = 5
    for r in range(n):
        for c in range(n):
            sheds.append({"HYBAS_ID": 9000000000 + r * n + c,
                          "geometry": box(minx + c * (maxx - minx) / n, maxy - (r + 1) * (maxy - miny) / n,
                                          minx + (c + 1) * (maxx - minx) / n, maxy - r * (maxy - miny) / n)})
    gpd.GeoDataFrame(sheds, crs="EPSG:4326").to_file(raw / "boundaries" / "watersheds.gpkg", driver="GPKG")

    # No mapped lineament file is written, so the DEM-based extraction is exercised.


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=str(config.REPO_ROOT / "data" / "demo"),
                   help="data directory to create (raw/ is written inside it)")
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args(argv)
    raw = Path(args.out) / "raw"
    rng = np.random.default_rng(args.seed)
    print(f"Writing SYNTHETIC demo inputs to {raw}")
    lon, lat = make_dem(raw, rng)
    make_rainfall(raw, rng)
    make_lulc(raw, lon, lat, rng)
    make_soil(raw, lon, lat, rng)
    make_vectors(raw)
    print("Done. Next:\n"
          f"  GWR_DATA_DIR={args.out} GWR_RESOLUTION=250 python -m backend.pipeline all\n"
          f"  GWR_DATA_DIR={args.out} GWR_RESOLUTION=250 uvicorn backend.app:app")


if __name__ == "__main__":
    main()
