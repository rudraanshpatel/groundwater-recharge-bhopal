"""IMD gridded rainfall: seasonal totals and interpolation to the working grid.

Input:  IMD 0.25 deg daily gridded rainfall as NetCDF (data/raw/imd/rain.nc),
        e.g. produced by ``download_imd()`` below, which wraps ``imdlib``.
Output: processed/layers/rainfall_{season}.tif, mean seasonal total in mm,
        one file per season in config.SEASONS.

Steps
1. Mask IMD's missing value (-999) and subset to the study area plus a buffer.
2. For every year, sum the daily rain over the season's months. Seasons that
   wrap the year end (e.g. Nov-Jan) are assigned to the year they start in,
   and incomplete season-years at the ends of the record are dropped.
3. Average the per-year totals: the long-term mean seasonal total per cell.
4. Interpolate the ~0.25 deg cell centres to a 1 km grid with ordinary
   kriging (or IDW), then resample bilinearly to the 30 m reference grid.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import xarray as xr
from pyproj import Transformer
from rasterio.enums import Resampling
from scipy.spatial import cKDTree

from backend import config
from backend.grid import ReferenceGrid, reproject_array, write_raster

log = logging.getLogger(__name__)

_VAR_CANDIDATES = ("rain", "rf", "RAINFALL", "precip", "pr")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def download_imd(start_year: int = config.RAINFALL_YEARS[0],
                 end_year: int = config.RAINFALL_YEARS[1],
                 file_dir: Path = config.RAW_IMD_DIR,
                 out_file: Path = config.RAW_RAINFALL_FILE) -> Path:
    """Download IMD daily rainfall with imdlib and save an AOI subset as NetCDF."""
    try:
        import imdlib as imd
    except ImportError as exc:
        raise ImportError("Install imdlib to download IMD data: pip install imdlib") from exc
    file_dir = Path(file_dir)
    file_dir.mkdir(parents=True, exist_ok=True)
    data = imd.get_data("rain", start_year, end_year, fn_format="yearwise", file_dir=str(file_dir))
    ds = data.get_xarray()
    da = subset_to_study_area(clean_rainfall(_pick_variable(ds)))
    out_file = Path(out_file)
    da.to_dataset(name="rain").to_netcdf(out_file)
    log.info("Saved IMD rainfall %d-%d to %s", start_year, end_year, out_file)
    return out_file


def _pick_variable(ds: xr.Dataset | xr.DataArray) -> xr.DataArray:
    if isinstance(ds, xr.DataArray):
        return ds
    for name in _VAR_CANDIDATES:
        if name in ds.data_vars:
            return ds[name]
    if len(ds.data_vars) == 1:
        return ds[list(ds.data_vars)[0]]
    raise KeyError(f"Could not find a rainfall variable among {list(ds.data_vars)}")


def _standardize_coords(da: xr.DataArray) -> xr.DataArray:
    rename = {}
    for c in da.coords:
        lc = c.lower()
        if lc in ("latitude", "y") and "lat" not in da.coords:
            rename[c] = "lat"
        elif lc in ("longitude", "x") and "lon" not in da.coords:
            rename[c] = "lon"
    da = da.rename(rename)
    for needed in ("time", "lat", "lon"):
        if needed not in da.coords:
            raise KeyError(f"Rainfall data has no '{needed}' coordinate (found {list(da.coords)})")
    return da


def clean_rainfall(da: xr.DataArray) -> xr.DataArray:
    """Standard coordinate names; missing (-999) and negative values -> NaN."""
    da = _standardize_coords(da)
    return da.where(da >= 0)


def subset_to_study_area(da: xr.DataArray, bbox=config.STUDY_AREA_BBOX,
                         buffer_deg: float = 0.5) -> xr.DataArray:
    """Keep cells within the bbox plus a buffer (helps interpolation at edges)."""
    minx, miny, maxx, maxy = bbox
    lat_mask = (da.lat >= miny - buffer_deg) & (da.lat <= maxy + buffer_deg)
    lon_mask = (da.lon >= minx - buffer_deg) & (da.lon <= maxx + buffer_deg)
    return da.sel(lat=da.lat[lat_mask], lon=da.lon[lon_mask])


def load_rainfall(path: Path | str = config.RAW_RAINFALL_FILE) -> xr.DataArray:
    """Open the daily rainfall NetCDF as a clean (time, lat, lon) DataArray."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Rainfall file {path} not found. Run `python -m backend.pipeline download-rain` "
            "or place an IMD NetCDF there."
        )
    with xr.open_dataset(path) as ds:
        da = _pick_variable(ds).load()
    return subset_to_study_area(clean_rainfall(da))


# ---------------------------------------------------------------------------
# Seasonal aggregation
# ---------------------------------------------------------------------------
def season_year(time: xr.DataArray, months: list[int]) -> xr.DataArray:
    """Label each day with the year its season STARTED in.

    For a wrapping season like [11, 12, 1], January belongs to the previous
    year's season. Non-wrapping seasons just use the calendar year.
    """
    year = time.dt.year
    month = time.dt.month
    wraps = any(b < a for a, b in zip(months, months[1:]))
    if not wraps:
        return year
    start = months[0]
    return xr.where(month < start, year - 1, year)


def seasonal_totals(da: xr.DataArray, months: list[int]) -> xr.DataArray:
    """Per-year seasonal rainfall totals, dims (season_year, lat, lon).

    Season-years missing any of the season's months (the partial seasons at
    the ends of the record) are dropped.
    """
    months = list(months)
    sel = da.where(da.time.dt.month.isin(months), drop=True)
    if sel.sizes.get("time", 0) == 0:
        raise ValueError(f"No rainfall data in months {months}")
    sy = season_year(sel.time, months).rename("season_year")
    sel = sel.assign_coords(season_year=sy)

    totals = sel.groupby("season_year").sum("time", min_count=1)
    sy_vals = sy.values
    month_vals = sel.time.dt.month.values
    complete = [set(month_vals[sy_vals == yr]) == set(months)
                for yr in totals.season_year.values]
    totals = totals.isel(season_year=np.flatnonzero(complete))
    if totals.sizes["season_year"] == 0:
        raise ValueError(f"No complete season-years for months {months}")
    return totals


def mean_seasonal_total(da: xr.DataArray, months: list[int]) -> xr.DataArray:
    """Long-term mean seasonal total (mm) per grid cell."""
    totals = seasonal_totals(da, months)
    mean = totals.mean("season_year", skipna=True)
    mean.attrs.update(units="mm", n_years=int(totals.sizes["season_year"]))
    return mean


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------
def _points_from_grid(field: xr.DataArray, crs) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lon2d, lat2d = np.meshgrid(field.lon.values, field.lat.values)
    vals = field.values
    ok = np.isfinite(vals)
    if ok.sum() < 3:
        raise ValueError("Fewer than 3 valid rainfall points to interpolate")
    tr = Transformer.from_crs(config.CRS_GEOGRAPHIC, crs, always_xy=True)
    x, y = tr.transform(lon2d[ok], lat2d[ok])
    return np.asarray(x), np.asarray(y), vals[ok].astype("float64")


def idw(x: np.ndarray, y: np.ndarray, z: np.ndarray, gx: np.ndarray, gy: np.ndarray,
        power: float = 2.0, k: int = 12) -> np.ndarray:
    """Inverse distance weighting from points onto the mesh gx (cols) x gy (rows)."""
    tree = cKDTree(np.column_stack([x, y]))
    mx, my = np.meshgrid(gx, gy)
    k = min(k, len(z))
    dist, idx = tree.query(np.column_stack([mx.ravel(), my.ravel()]), k=k)
    if k == 1:
        dist, idx = dist[:, None], idx[:, None]
    exact = dist[:, 0] < 1e-9
    w = 1.0 / np.maximum(dist, 1e-9) ** power
    out = (w * z[idx]).sum(axis=1) / w.sum(axis=1)
    out[exact] = z[idx[exact, 0]]
    return out.reshape(my.shape)


def ordinary_kriging(x: np.ndarray, y: np.ndarray, z: np.ndarray, gx: np.ndarray,
                     gy: np.ndarray, variogram_model: str = "spherical") -> np.ndarray:
    """Ordinary kriging (pykrige) onto the mesh gx x gy; rows follow gy's order."""
    from pykrige.ok import OrdinaryKriging

    ok = OrdinaryKriging(x, y, z, variogram_model=variogram_model,
                         verbose=False, enable_plotting=False)
    order = np.argsort(gy)                   # pykrige wants ascending y
    zgrid, _ = ok.execute("grid", gx, gy[order])
    out = np.empty((len(gy), len(gx)))
    out[order] = np.asarray(zgrid)
    return out


def interpolate_to_grid(field: xr.DataArray, grid: ReferenceGrid,
                        method: str = config.RAINFALL_INTERP_METHOD,
                        coarse_resolution: float = config.RAINFALL_INTERP_RESOLUTION) -> np.ndarray:
    """Interpolate a (lat, lon) field onto ``grid`` via a coarse intermediate grid."""
    x, y, z = _points_from_grid(field, grid.crs)
    coarse_res = max(coarse_resolution, grid.resolution)
    left, bottom, right, top = grid.bounds
    gx = np.arange(left + coarse_res / 2, right, coarse_res)
    gy = np.arange(top - coarse_res / 2, bottom, -coarse_res)

    if method == "kriging":
        try:
            coarse = ordinary_kriging(x, y, z, gx, gy)
        except Exception as exc:  # singular matrix, flat field, etc.
            log.warning("Kriging failed (%s); falling back to IDW", exc)
            coarse = idw(x, y, z, gx, gy)
    elif method == "idw":
        coarse = idw(x, y, z, gx, gy)
    else:
        raise ValueError(f"Unknown interpolation method {method!r}")

    coarse = np.clip(coarse, 0, None).astype("float32")
    from rasterio.transform import from_origin
    coarse_tf = from_origin(left, top, coarse_res, coarse_res)
    if coarse_res == grid.resolution and coarse.shape == grid.shape:
        return coarse
    return reproject_array(coarse, coarse_tf, grid.crs, None, grid, Resampling.bilinear)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def process(grid: ReferenceGrid | None = None,
            rainfall_file: Path = config.RAW_RAINFALL_FILE,
            method: str = config.RAINFALL_INTERP_METHOD) -> dict[str, Path]:
    """Write one interpolated mean seasonal rainfall raster per season."""
    grid = grid or ReferenceGrid.default()
    config.ensure_dirs()
    da = load_rainfall(rainfall_file)
    outputs = {}
    for season, meta in config.SEASONS.items():
        field = mean_seasonal_total(da, meta["months"])
        log.info("Rainfall %s: %d years, %.0f-%.0f mm", season, field.attrs["n_years"],
                 float(field.min()), float(field.max()))
        surface = interpolate_to_grid(field, grid, method=method)
        outputs[season] = write_raster(config.layer_path(f"rainfall_{season}"), surface, grid)
    return outputs
