"""Central configuration: study area, CRS, file paths, seasons and class rules.

Every other module reads its constants from here, so changing the study area,
working resolution or a reclassification threshold only needs an edit in one
place. A few values can be overridden with environment variables, which is
handy for running a fast low-resolution demo:

    GWR_DATA_DIR     root data folder            (default: <repo>/data)
    GWR_RESOLUTION   working pixel size in metres (default: 30)
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("GWR_DATA_DIR", REPO_ROOT / "data")).resolve()
FRONTEND_DIR = REPO_ROOT / "frontend"

RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
LAYERS_DIR = PROCESSED_DIR / "layers"      # continuous / categorical layers on the reference grid
RANKED_DIR = PROCESSED_DIR / "ranked"      # layers reclassified to ranks 1..5
ZONES_DIR = PROCESSED_DIR / "zones"        # final recharge zone rasters per season
OUTPUT_DIR = DATA_DIR / "outputs"          # exported GeoTIFF / PDF files

# Raw inputs (see README "Data download" for where each comes from)
RAW_DEM_DIR = RAW_DIR / "dem"                      # Copernicus GLO-30 / CartoDEM tiles (*.tif)
RAW_RAINFALL_FILE = RAW_DIR / "imd" / "rain.nc"    # IMD 0.25 deg daily rainfall, NetCDF
RAW_IMD_DIR = RAW_DIR / "imd"                      # imdlib download folder (yearwise .grd)
RAW_LULC_DIR = RAW_DIR / "worldcover"              # ESA WorldCover tiles (*.tif)
RAW_SOIL_DIR = RAW_DIR / "soilgrids"               # {clay,sand,silt}_{depth}.tif
RAW_GEOLOGY_FILE = RAW_DIR / "geology" / "lithology.gpkg"
RAW_LINEAMENT_FILE = RAW_DIR / "lineaments" / "lineaments.gpkg"  # optional (Bhukosh / NRSC)
TEHSIL_FILE = RAW_DIR / "boundaries" / "tehsils.gpkg"
WATERSHED_FILE = RAW_DIR / "boundaries" / "watersheds.gpkg"

# Attribute names inside the vector files
GEOLOGY_FIELD = "lithology"
TEHSIL_NAME_FIELD = "NAME_3"        # GADM level-3 name
WATERSHED_ID_FIELD = "HYBAS_ID"     # HydroBASINS id

# ---------------------------------------------------------------------------
# Study area and grid
# ---------------------------------------------------------------------------
# Bhopal, Raisen and Narmadapuram (Hoshangabad) districts, buffered.
# (min_lon, min_lat, max_lon, max_lat) in EPSG:4326.
STUDY_AREA_BBOX = (76.5, 21.9, 79.0, 24.0)
MAP_CENTER = (23.0, 77.6)           # lat, lon used by the frontend

CRS_GEOGRAPHIC = "EPSG:4326"
CRS_PROJECTED = "EPSG:32643"        # WGS 84 / UTM zone 43N, metres
RESOLUTION = float(os.environ.get("GWR_RESOLUTION", 30))

NODATA_FLOAT = -9999.0
NODATA_RANK = 0                     # ranks are 1..5, 0 means "no data"

# ---------------------------------------------------------------------------
# Thematic layers
# ---------------------------------------------------------------------------
LAYERS = [
    "rainfall",
    "geology",
    "slope",
    "soil",
    "lulc",
    "drainage_density",
    "lineament_density",
]

LAYER_LABELS = {
    "rainfall": "Rainfall",
    "geology": "Geology",
    "slope": "Slope",
    "soil": "Soil texture",
    "lulc": "Land use / land cover",
    "drainage_density": "Drainage density",
    "lineament_density": "Lineament density",
}

# Default weights used when no custom AHP matrix is supplied. backend/ahp.py
# holds a Saaty-scale pairwise matrix that reproduces these closely and passes
# the consistency check.
DEFAULT_WEIGHTS = {
    "rainfall": 0.25,
    "geology": 0.20,
    "slope": 0.15,
    "soil": 0.12,
    "lulc": 0.10,
    "drainage_density": 0.10,
    "lineament_density": 0.08,
}

# ---------------------------------------------------------------------------
# Seasons (the temporal toggle)
# ---------------------------------------------------------------------------
# IMD's official season definitions. Only rainfall changes between seasons at
# this stage; every other layer is static. A season may wrap the year end
# (e.g. [11, 12, 1]); rainfall.py handles that.
SEASONS = {
    "annual": {"label": "Annual", "months": list(range(1, 13))},
    "pre_monsoon": {"label": "Pre-monsoon (Mar-May)", "months": [3, 4, 5]},
    "monsoon": {"label": "Monsoon (Jun-Sep)", "months": [6, 7, 8, 9]},
    "post_monsoon": {"label": "Post-monsoon (Oct-Dec)", "months": [10, 11, 12]},
}
DEFAULT_SEASON = "annual"
RAINFALL_YEARS = (2014, 2024)

# Rainfall is interpolated on a coarse grid first (kriging every 30 m pixel
# would take hours) and then resampled bilinearly to the working grid.
RAINFALL_INTERP_RESOLUTION = 1000.0     # metres
RAINFALL_INTERP_METHOD = "kriging"      # "kriging" or "idw"

# ---------------------------------------------------------------------------
# DEM derivatives
# ---------------------------------------------------------------------------
STREAM_THRESHOLD_KM2 = 1.0          # upstream area that starts a stream
DENSITY_RADIUS_M = 2500.0           # moving-window radius for line densities
LINEAMENT_MIN_LENGTH_M = 1000.0     # discard shorter auto-detected lineaments
HILLSHADE_AZIMUTHS = (0, 45, 90, 135)
HILLSHADE_ALTITUDE = 30.0

# ---------------------------------------------------------------------------
# Reclassification rules: every layer becomes a rank 1..5 (5 = best recharge)
# ---------------------------------------------------------------------------
# Continuous layers: values < breaks[0] get ranks[0], breaks[0] <= v < breaks[1]
# get ranks[1], and so on. len(ranks) == len(breaks) + 1.
CONTINUOUS_RULES = {
    # degrees; gentle slopes hold water longer
    "slope": {"breaks": [2, 5, 10, 20], "ranks": [5, 4, 3, 2, 1]},
    # km/km^2; dense drainage means fast runoff, less infiltration
    "drainage_density": {"breaks": [1.0, 2.0, 3.0, 4.0], "ranks": [5, 4, 3, 2, 1]},
    # km/km^2; fractures are recharge pathways
    "lineament_density": {"breaks": [0.25, 0.5, 1.0, 1.5], "ranks": [1, 2, 3, 4, 5]},
    # mm per season. Used only when RAINFALL_SEASON_MODE = "absolute".
    "rainfall": {"breaks": [100, 400, 800, 1100], "ranks": [1, 2, 3, 4, 5]},
}

# How rainfall is ranked for each season:
#   "relative": quintiles of that season's own rainfall over the study area,
#               so every season map shows where recharge is best IN that
#               season. Pre- and post-monsoon totals (about 10-90 mm) all fall
#               below the lowest absolute break, which would make those maps
#               a uniform "Very Low". Absolute totals are still reported per
#               season (processed/ranked/rainfall_breaks.json, the API and
#               the season comparison table).
#   "absolute": the fixed mm breaks in CONTINUOUS_RULES["rainfall"] for every
#               season.
RAINFALL_SEASON_MODE = os.environ.get("GWR_RAINFALL_MODE", "relative")

# ESA WorldCover class code -> rank
LULC_RANKS = {
    10: 4,   # tree cover
    20: 3,   # shrubland
    30: 3,   # grassland
    40: 4,   # cropland
    50: 1,   # built-up
    60: 2,   # bare / sparse vegetation
    70: 1,   # snow and ice (absent here)
    80: 5,   # permanent water bodies
    90: 5,   # herbaceous wetland
    95: 4,   # mangroves (absent here)
    100: 2,  # moss and lichen (absent here)
}
LULC_NAMES = {
    10: "Tree cover", 20: "Shrubland", 30: "Grassland", 40: "Cropland",
    50: "Built-up", 60: "Bare / sparse", 70: "Snow / ice", 80: "Water",
    90: "Wetland", 95: "Mangroves", 100: "Moss / lichen",
}

# USDA texture class code -> (name, rank). Coarser soils infiltrate faster.
SOIL_TEXTURES = {
    1: ("Sand", 5),
    2: ("Loamy sand", 5),
    3: ("Sandy loam", 4),
    4: ("Loam", 3),
    5: ("Silt loam", 3),
    6: ("Silt", 3),
    7: ("Sandy clay loam", 3),
    8: ("Clay loam", 2),
    9: ("Silty clay loam", 2),
    10: ("Sandy clay", 2),
    11: ("Silty clay", 1),
    12: ("Clay", 1),
}

# Lithology keyword -> rank. Matching is case-insensitive and the FIRST keyword
# found in the lithology text wins, so put specific terms before general ones.
GEOLOGY_RANKS = [
    ("alluvium", 5),
    ("alluvial", 5),
    ("laterite", 4),
    ("weathered", 4),
    ("vesicular", 4),
    ("fractured", 4),
    ("gondwana", 3),
    ("sandstone", 3),
    ("deccan", 3),
    ("basalt", 3),
    ("trap", 3),
    ("limestone", 3),
    ("vindhyan", 2),
    ("shale", 2),
    ("quartzite", 2),
    ("bijawar", 2),
    ("granite", 1),
    ("gneiss", 1),
    ("massive", 1),
]
GEOLOGY_DEFAULT_RANK = 2      # lithology text that matches no keyword

# ---------------------------------------------------------------------------
# Recharge potential classes
# ---------------------------------------------------------------------------
# Zone codes in the output rasters. 1 = Very Low ... 5 = Very High.
RECHARGE_CLASSES = {
    1: {"name": "Very Low", "color": "#d7191c"},
    2: {"name": "Low", "color": "#fdae61"},
    3: {"name": "Moderate", "color": "#ffffbf"},
    4: {"name": "High", "color": "#abd9e9"},
    5: {"name": "Very High", "color": "#2c7bb6"},
}

# How the weighted index (range 1..5) is cut into the five classes.
#   "quantile" : equal-area classes from the annual map (default)
#   "fixed"    : use FIXED_CLASS_BREAKS below
#   "equal"    : equal-interval classes between the data min and max
#   "jenks"    : natural breaks (needs the optional `jenkspy` package)
# For seasonal comparison the pipeline computes breaks once from the annual
# map (saved to processed/zones/class_breaks.json) and reuses them.
CLASSIFICATION_METHOD = "quantile"
FIXED_CLASS_BREAKS = [1.8, 2.6, 3.4, 4.2]

# API safety valve: regions larger than this many pixels are read at reduced
# resolution so a full-study-area request stays fast.
MAX_ANALYSIS_PIXELS = 4_000_000
# GeoJSON polygons smaller than this many pixels are merged into neighbours.
SIEVE_PIXELS = 8


def ensure_dirs() -> None:
    """Create the processed/output directories if they do not exist."""
    for d in (LAYERS_DIR, RANKED_DIR, ZONES_DIR, OUTPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def layer_path(name: str) -> Path:
    """Path of a processed (unranked) layer raster."""
    return LAYERS_DIR / f"{name}.tif"


def ranked_path(name: str, season: str | None = None) -> Path:
    """Path of a ranked raster. Rainfall has one file per season."""
    if name == "rainfall":
        return RANKED_DIR / f"rainfall_{season or DEFAULT_SEASON}_rank.tif"
    return RANKED_DIR / f"{name}_rank.tif"


def zones_path(season: str) -> Path:
    """Path of the full-study-area recharge zone raster for a season."""
    return ZONES_DIR / f"recharge_zones_{season}.tif"
