"""DEM derivatives: slope, drainage density and lineament density.

Inputs:  DEM tiles in data/raw/dem/*.tif (Copernicus GLO-30 or CartoDEM).
Outputs: processed/layers/{dem,slope,drainage_density,lineament_density}.tif

Methods
- Slope: Horn (1981) 3x3 finite differences, in degrees.
- Drainage density: depressions filled and flats resolved, D8 flow direction
  and accumulation (pysheds), streams where upstream area exceeds
  STREAM_THRESHOLD_KM2, then stream length per unit area (km/km^2) in a
  moving window.
- Lineament density: lineaments are auto-extracted from multi-azimuth
  hillshades with Canny edges and a probabilistic Hough transform, and short
  segments are dropped. A mapped line file (e.g. GSI faults) is added to them,
  or used alone, per config.LINEAMENT_SOURCE. Then line length per unit area,
  same window as drainage.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from rasterio.enums import Resampling
from scipy import ndimage

from backend import config
from backend.grid import ReferenceGrid, read_raster, reproject_to_grid, write_raster

log = logging.getLogger(__name__)

# pysheds D8 direction codes: N, NE, E, SE, S, SW, W, NW
D8_DIRMAP = (64, 128, 1, 2, 4, 8, 16, 32)
D8_DIAGONAL = (128, 2, 8, 32)


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------
def _neighbours(z: np.ndarray):
    """The 3x3 neighbourhood a..i of every cell (edge-padded).

        a b c
        d e f
        g h i
    """
    p = np.pad(z, 1, mode="edge")
    a, b, c = p[:-2, :-2], p[:-2, 1:-1], p[:-2, 2:]
    d, f = p[1:-1, :-2], p[1:-1, 2:]
    g, h, i = p[2:, :-2], p[2:, 1:-1], p[2:, 2:]
    return a, b, c, d, f, g, h, i


def gradients(dem: np.ndarray, resolution: float) -> tuple[np.ndarray, np.ndarray]:
    """Horn's method gradients (dz/dx eastward, dz/dy northward), unit m/m."""
    a, b, c, d, f, g, h, i = _neighbours(dem.astype("float64"))
    dzdx = ((c + 2 * f + i) - (a + 2 * d + g)) / (8.0 * resolution)
    dzdy = ((a + 2 * b + c) - (g + 2 * h + i)) / (8.0 * resolution)
    return dzdx, dzdy


def compute_slope(dem: np.ndarray, resolution: float) -> np.ndarray:
    """Slope in degrees. NaN cells in the DEM (and their neighbours) give NaN."""
    if resolution <= 0:
        raise ValueError("resolution must be positive")
    dzdx, dzdy = gradients(dem, resolution)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    slope[~np.isfinite(dem)] = np.nan
    return slope.astype("float32")


def hillshade(dem: np.ndarray, resolution: float, azimuth: float,
              altitude: float = config.HILLSHADE_ALTITUDE) -> np.ndarray:
    """Hillshade in [0, 1] lit from ``azimuth`` (degrees clockwise from north)."""
    dzdx, dzdy = gradients(dem, resolution)
    slope = np.arctan(np.hypot(dzdx, dzdy))
    aspect = np.arctan2(-dzdx, -dzdy)          # downslope direction, clockwise from north
    zenith = np.radians(90.0 - altitude)
    az = np.radians(azimuth)
    hs = np.cos(zenith) * np.cos(slope) + np.sin(zenith) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(hs, 0, 1).astype("float32")


def line_density(length_m: np.ndarray, valid: np.ndarray, resolution: float,
                 radius_m: float = config.DENSITY_RADIUS_M) -> np.ndarray:
    """Line length per unit area (km/km^2) in a square moving window.

    ``length_m`` holds the line length (metres) inside each pixel. The window
    side is 2*radius. Edges and nodata are handled by dividing by the area of
    the valid pixels actually inside the window.
    """
    size = max(3, int(round(2 * radius_m / resolution)) | 1)   # odd window size
    length_km = np.where(valid, length_m, 0.0).astype("float64") / 1000.0
    mean_len = ndimage.uniform_filter(length_km, size=size, mode="constant")
    frac_valid = ndimage.uniform_filter(valid.astype("float64"), size=size, mode="constant")
    pixel_area_km2 = (resolution / 1000.0) ** 2
    with np.errstate(invalid="ignore", divide="ignore"):
        density = mean_len / (frac_valid * pixel_area_km2)
    density = np.clip(density, 0, None)   # remove tiny negative float noise
    density[~valid] = np.nan
    return density.astype("float32")


# ---------------------------------------------------------------------------
# DEM preparation
# ---------------------------------------------------------------------------
def prepare_dem(grid: ReferenceGrid, dem_dir: Path = config.RAW_DEM_DIR,
                out_path: Path | None = None) -> np.ndarray:
    """Mosaic raw DEM tiles, reproject to the reference grid and save."""
    tiles = sorted(Path(dem_dir).glob("*.tif"))
    if not tiles:
        raise FileNotFoundError(f"No DEM tiles (*.tif) found in {dem_dir}")
    log.info("Mosaicking %d DEM tile(s)", len(tiles))
    dem = reproject_to_grid(tiles, grid, Resampling.bilinear)
    write_raster(out_path or config.layer_path("dem"), dem, grid)
    return dem


# ---------------------------------------------------------------------------
# Drainage
# ---------------------------------------------------------------------------
def flow_accumulation(dem_path: Path | str) -> tuple[np.ndarray, np.ndarray]:
    """D8 flow direction and accumulation (cell counts) using pysheds."""
    try:
        from pysheds.grid import Grid
    except ImportError as exc:  # pragma: no cover
        raise ImportError("pysheds is required for drainage analysis: pip install pysheds") from exc
    # pysheds 0.5 still calls np.in1d, which NumPy 2.4 removed. np.isin is the
    # drop-in replacement.
    if not hasattr(np, "in1d"):
        np.in1d = np.isin

    pgrid = Grid.from_raster(str(dem_path))
    dem = pgrid.read_raster(str(dem_path))
    filled = pgrid.fill_pits(dem)
    filled = pgrid.fill_depressions(filled)
    inflated = pgrid.resolve_flats(filled)
    fdir = pgrid.flowdir(inflated, dirmap=D8_DIRMAP)
    acc = pgrid.accumulation(fdir, dirmap=D8_DIRMAP)
    return np.asarray(fdir), np.asarray(acc, dtype="float64")


def stream_length(fdir: np.ndarray, acc: np.ndarray, resolution: float,
                  threshold_km2: float = config.STREAM_THRESHOLD_KM2) -> np.ndarray:
    """Stream length (m) inside each pixel; 0 off-stream.

    A pixel is a stream when its upstream area reaches ``threshold_km2``.
    Diagonal flow steps are sqrt(2) times longer than cardinal ones.
    """
    cell_km2 = (resolution / 1000.0) ** 2
    threshold_cells = max(1.0, threshold_km2 / cell_km2)
    streams = acc >= threshold_cells
    step = np.where(np.isin(fdir, D8_DIAGONAL), resolution * np.sqrt(2.0), resolution)
    return np.where(streams, step, 0.0)


def compute_drainage_density(grid: ReferenceGrid, dem_path: Path | str,
                             dem: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (drainage density km/km^2, stream mask)."""
    fdir, acc = flow_accumulation(dem_path)
    length = stream_length(fdir, acc, grid.resolution)
    valid = np.isfinite(dem)
    length[~valid] = 0
    return line_density(length, valid, grid.resolution), length > 0


# ---------------------------------------------------------------------------
# Lineaments
# ---------------------------------------------------------------------------
def extract_lineaments(dem: np.ndarray, resolution: float,
                       azimuths=config.HILLSHADE_AZIMUTHS,
                       min_length_m: float = config.LINEAMENT_MIN_LENGTH_M,
                       tile: int = 2048) -> np.ndarray:
    """Auto-detect lineaments; returns a boolean raster of lineament pixels.

    Processed in tiles to bound memory, so a lineament crossing a tile edge is
    split in two. Mark the result "provisional" and prefer a mapped lineament
    layer when one is available.
    """
    from skimage.draw import line as draw_line
    from skimage.feature import canny
    from skimage.transform import probabilistic_hough_line

    valid = np.isfinite(dem)
    filled = np.where(valid, dem, np.nanmean(dem) if valid.any() else 0.0)
    min_len_px = max(5, int(round(min_length_m / resolution)))
    out = np.zeros(dem.shape, dtype=bool)

    for az in azimuths:
        hs = hillshade(filled, resolution, az)
        for r0 in range(0, dem.shape[0], tile):
            for c0 in range(0, dem.shape[1], tile):
                sl = (slice(r0, r0 + tile), slice(c0, c0 + tile))
                vmask = ndimage.binary_erosion(valid[sl], iterations=2)
                if vmask.sum() < min_len_px * 4:
                    continue
                # Quantile thresholds adapt to each tile's contrast: keep the
                # strongest 10% of gradients as candidate edges.
                edges = canny(hs[sl], sigma=2.0, mask=vmask, low_threshold=0.80,
                              high_threshold=0.90, use_quantiles=True)
                if not edges.any():
                    continue
                segments = probabilistic_hough_line(
                    edges, threshold=10, line_length=min_len_px, line_gap=3, rng=0
                )
                for (x0, y0), (x1, y1) in segments:
                    rr, cc = draw_line(y0, x0, y1, x1)
                    out[sl][rr, cc] = True
    out &= valid
    return out


def rasterize_lineaments(path: Path | str, grid: ReferenceGrid) -> np.ndarray:
    """Burn a mapped lineament line file onto the grid (boolean raster)."""
    import geopandas as gpd
    from rasterio.features import rasterize

    gdf = gpd.read_file(path).to_crs(grid.crs)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    if gdf.empty:
        return np.zeros(grid.shape, dtype=bool)
    burned = rasterize(((g, 1) for g in gdf.geometry), out_shape=grid.shape,
                       transform=grid.transform, fill=0, all_touched=True, dtype="uint8")
    return burned.astype(bool)


def compute_lineament_density(grid: ReferenceGrid, dem: np.ndarray,
                              lineament_file: Path = config.RAW_LINEAMENT_FILE) -> np.ndarray:
    """Lineament density (km/km^2) from DEM-extracted and/or mapped lines."""
    source = config.LINEAMENT_SOURCE
    mapped = Path(lineament_file).exists() and source != "dem"
    if mapped and source == "mapped":
        log.info("Using mapped lineaments only from %s", lineament_file)
        lines = rasterize_lineaments(lineament_file, grid)
    else:
        log.info("Auto-extracting lineaments from DEM hillshades%s",
                 "" if mapped else " (provisional; no mapped line file)")
        lines = extract_lineaments(dem, grid.resolution)
        if mapped:
            extra = rasterize_lineaments(lineament_file, grid)
            log.info("Adding mapped lines from %s (%.0f km)", lineament_file,
                     extra.sum() * grid.resolution / 1000)
            lines = lines | extra
    valid = np.isfinite(dem)
    return line_density(np.where(lines, grid.resolution, 0.0), valid, grid.resolution)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def process(grid: ReferenceGrid | None = None) -> dict[str, Path]:
    """Run all DEM derivatives and write them to processed/layers."""
    grid = grid or ReferenceGrid.default()
    config.ensure_dirs()
    dem_path = config.layer_path("dem")
    dem = prepare_dem(grid, out_path=dem_path)
    dem = read_raster(dem_path)  # re-read so nodata handling matches the file

    outputs = {}
    log.info("Computing slope")
    outputs["slope"] = write_raster(config.layer_path("slope"),
                                    compute_slope(dem, grid.resolution), grid)

    log.info("Computing drainage density")
    dd, _ = compute_drainage_density(grid, dem_path, dem)
    outputs["drainage_density"] = write_raster(config.layer_path("drainage_density"), dd, grid)

    log.info("Computing lineament density")
    ld = compute_lineament_density(grid, dem)
    outputs["lineament_density"] = write_raster(config.layer_path("lineament_density"), ld, grid)
    return outputs
