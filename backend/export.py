"""Exports: GeoJSON polygons for the web map, GeoTIFF and a PDF report."""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path
from typing import Mapping

import numpy as np
import rasterio
from rasterio.features import shapes, sieve
from rasterio.warp import transform_geom

from backend import config
from backend.grid import zone_colormap


# ---------------------------------------------------------------------------
# GeoJSON
# ---------------------------------------------------------------------------
def raster_to_geojson(classes: np.ndarray, transform, crs, value_name: str = "class",
                      labels: Mapping[int, dict] | None = None,
                      sieve_pixels: int = config.SIEVE_PIXELS,
                      simplify_tolerance: float | None = None) -> dict:
    """Polygonise a small-integer class raster into a GeoJSON FeatureCollection (EPSG:4326).

    Zeros are treated as nodata. Patches smaller than ``sieve_pixels`` are
    merged into their neighbours first, which keeps the GeoJSON small.
    """
    from shapely.geometry import mapping, shape

    data = np.ascontiguousarray(classes.astype("uint8"))
    mask = data > 0
    if sieve_pixels and sieve_pixels > 1 and mask.any():
        data = sieve(data, size=sieve_pixels, connectivity=8, mask=mask)
        data[~mask] = 0
        mask = data > 0
    if simplify_tolerance is None:
        simplify_tolerance = abs(transform.a) * 0.5

    features = []
    for geom, value in shapes(data, mask=mask, transform=transform, connectivity=8):
        value = int(value)
        if value == 0:
            continue
        if simplify_tolerance:
            geom = mapping(shape(geom).simplify(simplify_tolerance, preserve_topology=True))
        geom = transform_geom(crs, config.CRS_GEOGRAPHIC, geom)
        props = {value_name: value}
        if labels and value in labels:
            props.update({k: v for k, v in labels[value].items()})
        features.append({"type": "Feature", "geometry": geom, "properties": props})
    return {"type": "FeatureCollection", "features": features}


def zones_to_geojson(zones: np.ndarray, transform, crs, **kwargs) -> dict:
    """Recharge zones -> GeoJSON with class name and colour in the properties."""
    return raster_to_geojson(zones, transform, crs, "class", config.RECHARGE_CLASSES, **kwargs)


# ---------------------------------------------------------------------------
# GeoTIFF
# ---------------------------------------------------------------------------
def export_geotiff(zones: np.ndarray, transform, crs, path: Path | str | None = None) -> bytes:
    """Write recharge zones as a palette GeoTIFF. Returns the file bytes.

    If ``path`` is given the file is also saved there.
    """
    profile = {
        "driver": "GTiff", "dtype": "uint8", "count": 1, "crs": crs,
        "transform": transform, "width": zones.shape[1], "height": zones.shape[0],
        "nodata": 0, "compress": "deflate", "photometric": "palette",
    }
    with rasterio.MemoryFile() as mem:
        with mem.open(**profile) as dst:
            dst.write_colormap(1, zone_colormap())
            dst.write(zones.astype("uint8"), 1)
            dst.update_tags(
                classes=";".join(f"{k}={v['name']}" for k, v in config.RECHARGE_CLASSES.items()),
                description="Groundwater recharge potential zones",
            )
        data = mem.read()
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(data)
    return data


# ---------------------------------------------------------------------------
# PDF report
# ---------------------------------------------------------------------------
def generate_pdf_report(zones: np.ndarray, transform, region_name: str, season: str,
                        stats: list[dict], weights: Mapping[str, float],
                        ahp: Mapping | None = None, breaks=None,
                        path: Path | str | None = None) -> bytes:
    """Two-page PDF: the zone map with legend, then weights and class areas."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch

    colors = ["#ffffff00"] + [config.RECHARGE_CLASSES[c]["color"] for c in range(1, 6)]
    cmap = ListedColormap(colors)
    left, top = transform.c, transform.f
    right = left + transform.a * zones.shape[1]
    bottom = top + transform.e * zones.shape[0]
    season_label = config.SEASONS.get(season, {}).get("label", season)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    buf = io.BytesIO()
    with PdfPages(buf) as pdf:
        # Page 1: map
        fig, ax = plt.subplots(figsize=(8.27, 11.69))   # A4 portrait
        ax.imshow(zones, cmap=cmap, vmin=0, vmax=5, interpolation="nearest",
                  extent=(left / 1000, right / 1000, bottom / 1000, top / 1000))
        ax.set_xlabel("Easting (km, UTM 43N)")
        ax.set_ylabel("Northing (km, UTM 43N)")
        ax.set_title(f"Groundwater recharge potential\n{region_name} - {season_label}", fontsize=14)
        handles = [Patch(color=config.RECHARGE_CLASSES[c]["color"],
                         label=config.RECHARGE_CLASSES[c]["name"]) for c in range(5, 0, -1)]
        ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0, -0.22), ncol=5,
                  frameon=False, fontsize=9)
        fig.text(0.02, 0.01, f"Generated {stamp}", fontsize=7, color="grey")
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # Page 2: tables
        fig = plt.figure(figsize=(8.27, 11.69))
        fig.suptitle("Method summary", fontsize=14, y=0.97)

        ax1 = fig.add_axes([0.1, 0.62, 0.8, 0.28]); ax1.axis("off")
        ax1.set_title("AHP layer weights", loc="left")
        rows = [[config.LAYER_LABELS.get(k, k), f"{v:.3f}"]
                for k, v in sorted(weights.items(), key=lambda kv: -kv[1])]
        ax1.table(cellText=rows, colLabels=["Layer", "Weight"], loc="upper left",
                  colWidths=[0.6, 0.25]).scale(1, 1.4)
        if ahp:
            ax1.text(0, -0.05, f"lambda_max = {ahp['lambda_max']:.3f}   "
                     f"CI = {ahp['consistency_index']:.4f}   CR = {ahp['consistency_ratio']:.4f}"
                     f"  ({'consistent' if ahp['is_consistent'] else 'NOT consistent'}, CR < 0.10)",
                     transform=ax1.transAxes, fontsize=9)

        ax2 = fig.add_axes([0.1, 0.25, 0.8, 0.28]); ax2.axis("off")
        ax2.set_title("Recharge class areas", loc="left")
        rows = [[s["name"], f"{s['area_km2']:.2f}", f"{s['percent']:.1f}"] for s in reversed(stats)]
        tbl = ax2.table(cellText=rows, colLabels=["Class", "Area (km²)", "Share (%)"],
                        loc="upper left", colWidths=[0.4, 0.25, 0.2])
        tbl.scale(1, 1.4)
        for i, s in enumerate(reversed(stats), start=1):
            tbl[i, 0].set_facecolor(s["color"])
        if breaks is not None:
            ax2.text(0, -0.05, "GWRPI class breaks: " + ", ".join(f"{b:.2f}" for b in breaks),
                     transform=ax2.transAxes, fontsize=9)

        fig.text(0.1, 0.12,
                 "GWRPI = sum(weight_i x rank_i), ranks 1 (poor) to 5 (excellent) per layer.\n"
                 "Layers: rainfall, geology, slope, soil texture, LULC, drainage density,\n"
                 "lineament density. Only rainfall varies by season.", fontsize=9, va="top")
        pdf.savefig(fig)
        plt.close(fig)

    data = buf.getvalue()
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(data)
    return data
