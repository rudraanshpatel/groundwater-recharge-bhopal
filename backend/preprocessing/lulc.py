"""Land use / land cover layer from ESA WorldCover.

Input:  WorldCover 10 m tiles in data/raw/worldcover/*.tif
Output: processed/layers/lulc.tif (uint8 WorldCover class codes, 0 = nodata)

WorldCover is categorical, so it is resampled to the 30 m grid with MODE
(the most common 10 m class in each 30 m cell), never bilinear. Ranks per
class are defined in config.LULC_RANKS and applied in backend/overlay.py.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from rasterio.enums import Resampling

from backend import config
from backend.grid import ReferenceGrid, reproject_to_grid, write_raster

log = logging.getLogger(__name__)


def reclassify_lulc(classes: np.ndarray, ranks: dict[int, int] = config.LULC_RANKS) -> np.ndarray:
    """Map LULC class codes to recharge ranks 1..5 (unknown codes -> 0 nodata)."""
    out = np.zeros(classes.shape, dtype="uint8")
    for code, rank in ranks.items():
        out[classes == code] = rank
    return out


def class_areas(classes: np.ndarray, pixel_area_km2: float) -> dict[str, float]:
    """Area (km^2) of each LULC class present in the array."""
    codes, counts = np.unique(classes[classes > 0], return_counts=True)
    return {config.LULC_NAMES.get(int(c), str(int(c))): float(n * pixel_area_km2)
            for c, n in zip(codes, counts)}


def process(grid: ReferenceGrid | None = None, lulc_dir: Path = config.RAW_LULC_DIR) -> dict[str, Path]:
    """Mosaic WorldCover tiles onto the reference grid with mode resampling."""
    grid = grid or ReferenceGrid.default()
    config.ensure_dirs()
    tiles = sorted(Path(lulc_dir).glob("*.tif"))
    if not tiles:
        raise FileNotFoundError(f"No WorldCover tiles (*.tif) found in {lulc_dir}")
    log.info("Resampling %d LULC tile(s) with mode", len(tiles))
    classes = reproject_to_grid(tiles, grid, Resampling.mode, dtype="uint8")
    unknown = set(np.unique(classes)) - set(config.LULC_RANKS) - {0}
    if unknown:
        log.warning("LULC codes without a rank (treated as nodata): %s", sorted(unknown))
    return {"lulc": write_raster(config.layer_path("lulc"), classes, grid, nodata=0)}
