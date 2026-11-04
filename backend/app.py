"""FastAPI application: serves the web map and the analysis API.

Run from the repository root:

    uvicorn backend.app:app --reload

Endpoints
    GET  /                          the Leaflet frontend
    GET  /api/config                seasons, layers, classes, defaults for the UI
    GET  /api/status                which processed layers are available
    GET  /api/regions               selectable tehsils / watersheds
    GET  /api/regions/{type}        boundaries of one region type as GeoJSON
    GET  /api/ahp                   default pairwise matrix, weights and CR
    POST /api/ahp                   weights and CR for a custom pairwise matrix
    POST /analyze                   recharge zones for a region and season (GeoJSON + stats)
    GET  /layers/{layer}            one thematic layer, ranked 1..5, as GeoJSON
    POST /export/geotiff            recharge zones as a GeoTIFF download
    POST /export/pdf                recharge zones as a PDF report
"""

from __future__ import annotations

import logging
import re
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import config, export, regions
from backend.ahp import AHPError, compute_ahp, default_result, normalize_weights
from backend.overlay import (
    analyze_region, available_layers, read_ranked_region, zone_statistics,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("gwr.api")

app = FastAPI(
    title="Groundwater Recharge Zone Identifier - Bhopal region",
    description="Weighted overlay (AHP) groundwater recharge potential mapping "
                "for Bhopal, Raisen and Narmadapuram districts.",
    version="0.1.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SeasonName = Literal["annual", "pre_monsoon", "monsoon", "post_monsoon"]
RegionType = Literal["study_area", "tehsil", "watershed"]


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class AHPRequest(BaseModel):
    matrix: list[list[float]] = Field(..., description="n x n reciprocal pairwise matrix")
    criteria: Optional[list[str]] = Field(None, description="Row/column order; defaults to config.LAYERS")


class AnalyzeRequest(BaseModel):
    region_type: RegionType = "study_area"
    region_id: str = regions.STUDY_AREA_ID
    season: SeasonName = "annual"
    weights: Optional[dict[str, float]] = Field(
        None, description="Layer weights (normalised automatically). Default: config.DEFAULT_WEIGHTS")
    ahp_matrix: Optional[list[list[float]]] = Field(
        None, description="Pairwise matrix in config.LAYERS order; overrides `weights`")
    classification: Literal["saved", "fixed", "quantile", "equal", "jenks"] = Field(
        "saved", description="'saved' reuses the study-area breaks so seasons stay comparable")
    simplify: bool = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _resolve_weights(req: AnalyzeRequest) -> tuple[dict[str, float], Optional[dict]]:
    """Weights to use and, if a matrix was sent, its AHP result."""
    if req.ahp_matrix is not None:
        try:
            res = compute_ahp(req.ahp_matrix, config.LAYERS)
        except AHPError as exc:
            raise HTTPException(400, f"Invalid AHP matrix: {exc}") from exc
        return res.weights, res.to_dict()
    if req.weights:
        unknown = set(req.weights) - set(config.LAYERS)
        if unknown:
            raise HTTPException(400, f"Unknown layer(s) in weights: {sorted(unknown)}")
        try:
            return normalize_weights(req.weights, config.LAYERS), None
        except AHPError as exc:
            raise HTTPException(400, str(exc)) from exc
    return dict(config.DEFAULT_WEIGHTS), None


def _run(req: AnalyzeRequest):
    try:
        name, geom = regions.get_region(req.region_type, req.region_id)
    except regions.RegionNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    weights, ahp = _resolve_weights(req)
    try:
        grid_crs = _grid_crs()
        result = analyze_region(regions.to_crs(geom, grid_crs), req.season, weights,
                                None if req.classification == "saved" else req.classification)
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc
    except (ValueError, ImportError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return name, result, ahp


def _grid_crs():
    """CRS of the processed rasters (falls back to the configured one)."""
    import rasterio
    for n in config.LAYERS:
        p = config.ranked_path(n, config.DEFAULT_SEASON)
        if p.exists():
            with rasterio.open(p) as src:
                return src.crs
    return config.CRS_PROJECTED


def _safe_filename(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_") or "region"


# ---------------------------------------------------------------------------
# Metadata endpoints
# ---------------------------------------------------------------------------
@app.get("/api/config")
def get_config():
    """Static settings the frontend needs."""
    return {
        "map_center": config.MAP_CENTER,
        "study_area_bbox": config.STUDY_AREA_BBOX,
        "seasons": {k: {"label": v["label"], "months": v["months"]} for k, v in config.SEASONS.items()},
        "default_season": config.DEFAULT_SEASON,
        "layers": [{"id": n, "label": config.LAYER_LABELS[n]} for n in config.LAYERS],
        "default_weights": config.DEFAULT_WEIGHTS,
        "classes": config.RECHARGE_CLASSES,
    }


@app.get("/api/status")
def get_status():
    """Which processed layers exist, per season."""
    return {
        "data_dir": str(config.DATA_DIR),
        "seasons": {s: available_layers(s) for s in config.SEASONS},
        "zones": {s: config.zones_path(s).exists() for s in config.SEASONS},
        "ready": all("rainfall" in available_layers(s) for s in config.SEASONS)
                 and len(available_layers()) == len(config.LAYERS),
    }


@app.get("/api/regions")
def get_regions():
    try:
        return regions.list_regions()
    except (KeyError, ValueError) as exc:
        raise HTTPException(500, f"Could not read boundary files: {exc}") from exc


@app.get("/api/regions/{region_type}")
def get_region_geojson(region_type: RegionType):
    return regions.regions_geojson(region_type)


@app.get("/api/ahp")
def get_ahp():
    """The default pairwise matrix with its weights and consistency ratio."""
    return default_result().to_dict()


@app.post("/api/ahp")
def post_ahp(req: AHPRequest):
    try:
        return compute_ahp(req.matrix, req.criteria or config.LAYERS).to_dict()
    except AHPError as exc:
        raise HTTPException(400, str(exc)) from exc


# ---------------------------------------------------------------------------
# Analysis endpoints
# ---------------------------------------------------------------------------
@app.post("/analyze")
@app.post("/api/analyze", include_in_schema=False)
def analyze(req: AnalyzeRequest):
    """Recharge potential zones for a region and season."""
    name, res, ahp = _run(req)
    geojson = export.zones_to_geojson(
        res.zones, res.transform, res.crs,
        sieve_pixels=config.SIEVE_PIXELS if req.simplify else 0,
        simplify_tolerance=None if req.simplify else 0)
    return {
        "region": {"type": req.region_type, "id": req.region_id, "name": name},
        "season": req.season,
        "season_label": config.SEASONS[req.season]["label"],
        "layers_used": res.layers,
        "weights": res.weights,
        "ahp": ahp,
        "class_breaks": res.breaks,
        "pixel_size_m": round(abs(res.transform.a), 2),
        "stats": res.stats,
        "geojson": geojson,
    }


@app.get("/layers/{layer}")
@app.get("/api/layers/{layer}", include_in_schema=False)
def get_layer(layer: str,
              region_type: RegionType = "study_area",
              region_id: str = regions.STUDY_AREA_ID,
              season: SeasonName = "annual",
              max_pixels: int = Query(1_000_000, ge=10_000, le=config.MAX_ANALYSIS_PIXELS)):
    """A thematic layer reclassified to ranks 1 (poor) .. 5 (excellent), as GeoJSON."""
    if layer not in config.LAYERS:
        raise HTTPException(404, f"Unknown layer '{layer}'. Choose from {config.LAYERS}")
    if not config.ranked_path(layer, season).exists():
        raise HTTPException(503, f"Layer '{layer}' has not been processed yet")
    try:
        _, geom = regions.get_region(region_type, region_id)
    except regions.RegionNotFound as exc:
        raise HTTPException(404, str(exc)) from exc

    import numpy as np
    from rasterio.features import geometry_mask

    geom = regions.to_crs(geom, _grid_crs())
    arr, tf, crs = read_ranked_region(layer, season, geom.bounds, max_pixels)
    inside = geometry_mask([geom], out_shape=arr.shape, transform=tf, invert=True, all_touched=True)
    arr = np.where(inside, arr, 0).astype("uint8")
    labels = {r: {"label": ["", "Very poor", "Poor", "Moderate", "Good", "Very good"][r],
                  "color": config.RECHARGE_CLASSES[r]["color"]} for r in range(1, 6)}
    gj = export.raster_to_geojson(arr, tf, crs, "rank", labels)
    gj["layer"] = layer
    gj["label"] = config.LAYER_LABELS[layer]
    gj["stats"] = zone_statistics(arr, abs(tf.a * tf.e) / 1e6)
    return gj


@app.post("/export/{fmt}")
@app.post("/api/export/{fmt}", include_in_schema=False)
def export_result(fmt: Literal["geotiff", "pdf"], req: AnalyzeRequest):
    """Download the analysis result as a GeoTIFF or PDF report."""
    name, res, ahp = _run(req)
    fname = f"recharge_{_safe_filename(name)}_{req.season}"
    if fmt == "geotiff":
        data = export.export_geotiff(res.zones, res.transform, res.crs)
        return Response(data, media_type="image/tiff",
                        headers={"Content-Disposition": f'attachment; filename="{fname}.tif"'})
    data = export.generate_pdf_report(
        res.zones, res.transform, name, req.season, res.stats, res.weights,
        ahp=ahp or (default_result().to_dict() if not req.weights else None),
        breaks=res.breaks)
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{fname}.pdf"'})


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(config.FRONTEND_DIR / "index.html")


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


for _sub in ("css", "js"):
    _dir = config.FRONTEND_DIR / _sub
    if _dir.is_dir():
        app.mount(f"/{_sub}", StaticFiles(directory=_dir), name=_sub)
