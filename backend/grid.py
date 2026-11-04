"""Reference grid and raster I/O helpers.

Every thematic layer is reprojected onto ONE reference grid (same CRS,
transform and shape). That makes the weighted overlay a plain pixel-by-pixel
sum and avoids the most common source of overlay bugs: misaligned rasters.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.merge import merge
from rasterio.transform import Affine, from_origin
from rasterio.warp import reproject, transform_bounds

from backend import config


@dataclass(frozen=True)
class ReferenceGrid:
    """A north-up raster grid: CRS, affine transform and shape."""

    crs: CRS
    transform: Affine
    width: int
    height: int

    @classmethod
    def from_bbox(cls, bbox=config.STUDY_AREA_BBOX, crs=config.CRS_PROJECTED,
                  resolution: float = config.RESOLUTION,
                  bbox_crs=config.CRS_GEOGRAPHIC) -> "ReferenceGrid":
        """Grid covering ``bbox`` (given in ``bbox_crs``), snapped to ``resolution``."""
        dst_crs = CRS.from_user_input(crs)
        left, bottom, right, top = transform_bounds(
            CRS.from_user_input(bbox_crs), dst_crs, *bbox, densify_pts=21
        )
        # Snap outward to whole pixels so grids built at different times match.
        left = math.floor(left / resolution) * resolution
        bottom = math.floor(bottom / resolution) * resolution
        right = math.ceil(right / resolution) * resolution
        top = math.ceil(top / resolution) * resolution
        width = int(round((right - left) / resolution))
        height = int(round((top - bottom) / resolution))
        return cls(dst_crs, from_origin(left, top, resolution, resolution), width, height)

    @classmethod
    def default(cls) -> "ReferenceGrid":
        """The study-area grid defined in config."""
        return cls.from_bbox()

    @property
    def resolution(self) -> float:
        return abs(self.transform.a)

    @property
    def shape(self) -> tuple[int, int]:
        return self.height, self.width

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        left, top = self.transform.c, self.transform.f
        return (left, top - self.height * self.resolution,
                left + self.width * self.resolution, top)

    @property
    def pixel_area_km2(self) -> float:
        return (self.resolution ** 2) / 1e6

    def profile(self, dtype="float32", nodata=config.NODATA_FLOAT) -> dict:
        """A rasterio write profile for a single-band tiled, compressed GeoTIFF."""
        prof = {
            "driver": "GTiff", "dtype": dtype, "count": 1, "crs": self.crs,
            "transform": self.transform, "width": self.width, "height": self.height,
            "nodata": nodata, "compress": "deflate",
        }
        if self.width >= 256 and self.height >= 256:
            prof.update(tiled=True, blockxsize=256, blockysize=256)
        return prof

    def xy_coords(self) -> tuple[np.ndarray, np.ndarray]:
        """1-D arrays of pixel-centre x (columns) and y (rows) coordinates."""
        res = self.resolution
        xs = self.transform.c + res * (np.arange(self.width) + 0.5)
        ys = self.transform.f - res * (np.arange(self.height) + 0.5)
        return xs, ys

    def matches(self, path: Path | str) -> bool:
        """True if the raster at ``path`` sits exactly on this grid."""
        with rasterio.open(path) as src:
            return (src.crs == self.crs and src.width == self.width
                    and src.height == self.height
                    and src.transform.almost_equals(self.transform))


def write_raster(path: Path | str, array: np.ndarray, grid: ReferenceGrid,
                 nodata=None, colormap: dict | None = None) -> Path:
    """Write a 2-D array to a GeoTIFF on ``grid``.

    NaN in a float array is written as ``nodata``. Returns the path.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if array.shape != grid.shape:
        raise ValueError(f"Array shape {array.shape} does not match grid {grid.shape}")
    dtype = array.dtype
    if nodata is None:
        nodata = config.NODATA_FLOAT if np.issubdtype(dtype, np.floating) else config.NODATA_RANK
    data = array
    if np.issubdtype(dtype, np.floating):
        data = np.where(np.isfinite(array), array, nodata).astype("float32")
    profile = grid.profile(dtype=data.dtype.name, nodata=nodata)
    if colormap:
        profile["photometric"] = "palette"
    with rasterio.open(path, "w", **profile) as dst:
        if colormap:
            dst.write_colormap(1, colormap)
        dst.write(data, 1)
    return path


def read_raster(path: Path | str, as_float: bool = True) -> np.ndarray:
    """Read band 1. With ``as_float`` nodata becomes NaN."""
    with rasterio.open(path) as src:
        arr = src.read(1)
        nodata = src.nodata
    if as_float:
        arr = arr.astype("float32")
        if nodata is not None:
            arr[arr == nodata] = np.nan
    return arr


def mosaic_files(paths: Iterable[Path | str]) -> tuple[np.ndarray, dict]:
    """Merge several tiles (same CRS) into one array. Returns (array, profile)."""
    paths = [str(p) for p in paths]
    if not paths:
        raise FileNotFoundError("No raster tiles to mosaic")
    srcs = [rasterio.open(p) for p in paths]
    try:
        arr, transform = merge(srcs)
        profile = srcs[0].profile.copy()
        profile.update(height=arr.shape[1], width=arr.shape[2], transform=transform, count=1)
    finally:
        for s in srcs:
            s.close()
    return arr[0], profile


def reproject_array(src: np.ndarray, src_transform: Affine, src_crs, src_nodata,
                    grid: ReferenceGrid, resampling: Resampling,
                    dtype="float32", dst_nodata=None) -> np.ndarray:
    """Reproject an in-memory array onto ``grid``."""
    if dst_nodata is None:
        dst_nodata = np.nan if np.dtype(dtype).kind == "f" else config.NODATA_RANK
    dst = np.full(grid.shape, dst_nodata, dtype=dtype)
    reproject(
        source=src, destination=dst,
        src_transform=src_transform, src_crs=src_crs, src_nodata=src_nodata,
        dst_transform=grid.transform, dst_crs=grid.crs, dst_nodata=dst_nodata,
        resampling=resampling,
    )
    return dst


def reproject_to_grid(paths: Path | str | Iterable[Path | str], grid: ReferenceGrid,
                      resampling: Resampling = Resampling.bilinear,
                      dtype="float32") -> np.ndarray:
    """Mosaic one or more rasters and reproject them onto ``grid``.

    Use ``Resampling.bilinear`` for continuous data (DEM, soil fractions) and
    ``Resampling.mode`` or ``nearest`` for categorical data (LULC).
    """
    if isinstance(paths, (str, Path)):
        paths = [paths]
    paths = list(paths)
    if len(paths) == 1:
        with rasterio.open(paths[0]) as src:
            arr, transform, crs, nodata = src.read(1), src.transform, src.crs, src.nodata
    else:
        arr, prof = mosaic_files(paths)
        transform, crs, nodata = prof["transform"], prof["crs"], prof.get("nodata")
    if np.dtype(dtype).kind == "f" and arr.dtype.kind != "f":
        arr = arr.astype("float32")
    return reproject_array(arr, transform, crs, nodata, grid, resampling, dtype=dtype)


def hex_to_rgba(color: str) -> tuple[int, int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16), 255


def zone_colormap() -> dict[int, tuple[int, int, int, int]]:
    """GeoTIFF palette for recharge zone rasters (0 = transparent)."""
    cmap = {0: (0, 0, 0, 0)}
    for code, meta in config.RECHARGE_CLASSES.items():
        cmap[code] = hex_to_rgba(meta["color"])
    return cmap
