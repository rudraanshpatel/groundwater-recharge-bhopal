"""Soil texture layer from ISRIC SoilGrids 250 m.

Input:  data/raw/soilgrids/{clay,sand,silt}_{0-5cm,5-15cm,15-30cm}.tif
        (g/kg, as clipped from the SoilGrids WebDAV VRTs)
Output: processed/layers/soil.tif (uint8 USDA texture class code 1..12)

The three depth slices are averaged with thickness weights (5, 10, 15 cm)
to a 0-30 cm value, converted to percent, normalised so sand+silt+clay =
100, and classified with the USDA texture triangle. Ranks per texture class
are in config.SOIL_TEXTURES (coarse, sandy soils infiltrate fastest).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from rasterio.enums import Resampling

from backend import config
from backend.grid import ReferenceGrid, reproject_to_grid, write_raster

log = logging.getLogger(__name__)

DEPTHS = {"0-5cm": 5.0, "5-15cm": 10.0, "15-30cm": 15.0}   # slice -> thickness (cm)
FRACTIONS = ("clay", "sand", "silt")


def usda_texture_class(sand: np.ndarray, silt: np.ndarray, clay: np.ndarray) -> np.ndarray:
    """USDA texture class codes (see config.SOIL_TEXTURES); 0 where input is NaN.

    Inputs are percentages. They are renormalised to sum to 100 first. The
    rules follow the USDA soil texture triangle boundaries.
    """
    sand, silt, clay = (np.asarray(a, dtype="float64") for a in (sand, silt, clay))
    total = sand + silt + clay
    valid = np.isfinite(total) & (total > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        sa, si, cl = (np.where(valid, a * 100.0 / total, np.nan) for a in (sand, silt, clay))

    conditions = [
        (si + 1.5 * cl) < 15,                                                      # sand
        ((si + 1.5 * cl) >= 15) & ((si + 2 * cl) < 30),                            # loamy sand
        ((cl >= 7) & (cl < 20) & (sa > 52) & ((si + 2 * cl) >= 30))
        | ((cl < 7) & (si < 50) & ((si + 2 * cl) >= 30)),                          # sandy loam
        (cl >= 7) & (cl < 27) & (si >= 28) & (si < 50) & (sa <= 52),               # loam
        ((si >= 50) & (cl >= 12) & (cl < 27)) | ((si >= 50) & (si < 80) & (cl < 12)),  # silt loam
        (si >= 80) & (cl < 12),                                                    # silt
        (cl >= 20) & (cl < 35) & (si < 28) & (sa > 45),                            # sandy clay loam
        (cl >= 27) & (cl < 40) & (sa > 20) & (sa <= 45),                           # clay loam
        (cl >= 27) & (cl < 40) & (sa <= 20),                                       # silty clay loam
        (cl >= 35) & (sa > 45),                                                    # sandy clay
        (cl >= 40) & (si >= 40),                                                   # silty clay
        (cl >= 40) & (sa <= 45) & (si < 40),                                       # clay
    ]
    codes = np.select(conditions, list(range(1, 13)), default=0).astype("uint8")
    codes[~valid] = 0
    return codes


def reclassify_soil(texture: np.ndarray) -> np.ndarray:
    """Map texture class codes to recharge ranks 1..5 (0 stays nodata)."""
    out = np.zeros(texture.shape, dtype="uint8")
    for code, (_, rank) in config.SOIL_TEXTURES.items():
        out[texture == code] = rank
    return out


def depth_weighted_fraction(grid: ReferenceGrid, fraction: str,
                            soil_dir: Path = config.RAW_SOIL_DIR) -> np.ndarray:
    """0-30 cm thickness-weighted mean of one fraction, in percent."""
    acc = np.zeros(grid.shape, dtype="float64")
    wsum = np.zeros(grid.shape, dtype="float64")
    found = 0
    for depth, thickness in DEPTHS.items():
        path = Path(soil_dir) / f"{fraction}_{depth}.tif"
        if not path.exists():
            log.warning("Missing %s", path)
            continue
        found += 1
        arr = reproject_to_grid(path, grid, Resampling.bilinear)
        ok = np.isfinite(arr)
        acc[ok] += arr[ok] * thickness
        wsum[ok] += thickness
    if not found:
        raise FileNotFoundError(f"No SoilGrids '{fraction}' rasters in {soil_dir}")
    with np.errstate(invalid="ignore", divide="ignore"):
        gkg = np.where(wsum > 0, acc / wsum, np.nan)
    return gkg / 10.0     # g/kg -> %


def process(grid: ReferenceGrid | None = None, soil_dir: Path = config.RAW_SOIL_DIR) -> dict[str, Path]:
    """Build the USDA texture class raster on the reference grid."""
    grid = grid or ReferenceGrid.default()
    config.ensure_dirs()
    frac = {f: depth_weighted_fraction(grid, f, soil_dir) for f in FRACTIONS}
    texture = usda_texture_class(frac["sand"], frac["silt"], frac["clay"])
    return {"soil": write_raster(config.layer_path("soil"), texture, grid, nodata=0)}
