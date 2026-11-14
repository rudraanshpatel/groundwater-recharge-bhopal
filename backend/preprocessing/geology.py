"""Geology (lithology) layer.

Input:  a polygon file of lithology units, data/raw/geology/lithology.gpkg,
        with a text attribute config.GEOLOGY_FIELD (e.g. "Deccan Trap basalt",
        "Narmada alluvium"). Source: GSI 1:50K lithology from the National
        Geoscience Data Repository (NGDR), one shapefile per toposheet, merged
        by scripts/prepare_data.py geology.
Output: processed/layers/geology.tif (uint8 recharge rank 1..5, 0 = nodata)

Lithology names vary a lot between sources, so ranks come from keyword
matching (config.GEOLOGY_RANKS, first match wins). Units that match no
keyword get config.GEOLOGY_DEFAULT_RANK and are logged so you can extend the
table.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from rasterio.features import rasterize

from backend import config
from backend.grid import ReferenceGrid, write_raster

log = logging.getLogger(__name__)


def lithology_rank(name: object, rules=config.GEOLOGY_RANKS,
                   default: int = config.GEOLOGY_DEFAULT_RANK) -> int:
    """Recharge rank for a lithology description using keyword rules."""
    if name is None or (isinstance(name, float) and np.isnan(name)):
        return default
    text = str(name).lower()
    for keyword, rank in rules:
        if keyword in text:
            return rank
    return default


def rasterize_geology(path: Path | str, grid: ReferenceGrid,
                      field: str = config.GEOLOGY_FIELD) -> np.ndarray:
    """Rasterize lithology polygons to recharge ranks on the grid."""
    import geopandas as gpd

    gdf = gpd.read_file(path)
    if field not in gdf.columns:
        raise KeyError(f"Field '{field}' not in {path}; available: {list(gdf.columns)}")
    if gdf.crs is None:
        raise ValueError(f"{path} has no CRS defined")
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].to_crs(grid.crs)
    gdf["rank"] = gdf[field].map(lithology_rank)

    unmatched = sorted({str(v) for v in gdf[field]
                        if not any(k in str(v).lower() for k, _ in config.GEOLOGY_RANKS)})
    if unmatched:
        log.warning("Lithologies with no keyword match (rank %d): %s",
                    config.GEOLOGY_DEFAULT_RANK, unmatched)

    shapes = ((geom, int(r)) for geom, r in zip(gdf.geometry, gdf["rank"]))
    return rasterize(shapes, out_shape=grid.shape, transform=grid.transform,
                     fill=config.NODATA_RANK, dtype="uint8")


def process(grid: ReferenceGrid | None = None,
            geology_file: Path = config.RAW_GEOLOGY_FILE) -> dict[str, Path]:
    """Write the ranked geology raster."""
    grid = grid or ReferenceGrid.default()
    config.ensure_dirs()
    if not Path(geology_file).exists():
        raise FileNotFoundError(f"Geology file {geology_file} not found")
    ranks = rasterize_geology(geology_file, grid)
    return {"geology": write_raster(config.layer_path("geology"), ranks, grid, nodata=0)}
