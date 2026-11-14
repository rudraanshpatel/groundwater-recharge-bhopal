"""Selectable analysis regions: the whole study area, tehsils and watersheds."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from shapely.geometry import box, mapping
import shapely
from pyproj import Transformer

from backend import config

STUDY_AREA_ID = "study_area"


class RegionNotFound(KeyError):
    pass


def _source(region_type: str) -> tuple[Path, str]:
    if region_type == "tehsil":
        return config.TEHSIL_FILE, config.TEHSIL_NAME_FIELD
    if region_type == "watershed":
        return config.WATERSHED_FILE, config.WATERSHED_ID_FIELD
    raise RegionNotFound(f"Unknown region type {region_type!r}")


@lru_cache(maxsize=4)
def _load(region_type: str, mtime: float):
    """Load a boundary file in EPSG:4326 (cached per file modification time)."""
    import geopandas as gpd

    path, field = _source(region_type)
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        raise ValueError(f"{path} has no CRS")
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].to_crs(config.CRS_GEOGRAPHIC)
    # Keep only units that touch the study area.
    gdf = gdf[gdf.intersects(box(*config.STUDY_AREA_BBOX))].reset_index(drop=True)
    if field not in gdf.columns:
        raise KeyError(f"Field '{field}' not in {path}; available: {list(gdf.columns)}")
    gdf["region_id"] = [f"{region_type}-{i}" for i in range(len(gdf))]
    label = gdf[field].astype(str)
    if region_type == "tehsil" and "NAME_2" in gdf.columns:   # add district for clarity
        label = label + " (" + gdf["NAME_2"].astype(str) + ")"
    gdf["region_name"] = label
    return gdf


def load_regions(region_type: str):
    path, _ = _source(region_type)
    if not path.exists():
        return None
    return _load(region_type, path.stat().st_mtime)


def list_regions() -> dict:
    """All selectable regions grouped by type (names and ids only)."""
    out = {"study_area": [{"id": STUDY_AREA_ID, "name": "Whole study area"}]}
    for rtype in ("tehsil", "watershed"):
        gdf = load_regions(rtype)
        out[rtype] = [] if gdf is None else [
            {"id": rid, "name": name}
            for rid, name in sorted(zip(gdf["region_id"], gdf["region_name"]), key=lambda t: t[1])
        ]
    return out


def regions_geojson(region_type: str) -> dict:
    """Boundaries of one region type as GeoJSON (EPSG:4326)."""
    if region_type == "study_area":
        return {"type": "FeatureCollection", "features": [{
            "type": "Feature", "geometry": mapping(box(*config.STUDY_AREA_BBOX)),
            "properties": {"id": STUDY_AREA_ID, "name": "Whole study area"}}]}
    gdf = load_regions(region_type)
    if gdf is None:
        return {"type": "FeatureCollection", "features": []}
    gdf = gdf[["region_id", "region_name", "geometry"]].rename(
        columns={"region_id": "id", "region_name": "name"})
    gdf["geometry"] = gdf.geometry.simplify(0.001, preserve_topology=True)
    return gdf.__geo_interface__


def get_region(region_type: str, region_id: str):
    """Return (name, geometry in EPSG:4326) for a region."""
    if region_type == "study_area" or region_id == STUDY_AREA_ID:
        return "Whole study area", box(*config.STUDY_AREA_BBOX)
    gdf = load_regions(region_type)
    if gdf is None:
        raise RegionNotFound(f"No {region_type} boundary file found at {_source(region_type)[0]}")
    match = gdf[gdf["region_id"] == region_id]
    if match.empty:
        raise RegionNotFound(f"{region_type} '{region_id}' not found")
    row = match.iloc[0]
    return row["region_name"], row.geometry


def to_crs(geometry, dst_crs, src_crs=config.CRS_GEOGRAPHIC):
    """Reproject a shapely geometry."""
    tr = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    return shapely.transform(geometry, lambda x, y: tr.transform(x, y), interleaved=False)
