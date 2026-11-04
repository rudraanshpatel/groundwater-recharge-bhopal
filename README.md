# Groundwater Recharge Zone Identifier: Bhopal Region

A web application that maps **groundwater recharge potential** for the Bhopal, Raisen and
Narmadapuram (Hoshangabad) districts of Madhya Pradesh. Pick a tehsil or watershed and a
season, and the app runs an **AHP-weighted overlay** of seven thematic layers and returns a
five-class recharge potential map (Very High to Very Low), with area statistics and
GeoTIFF / PDF downloads. No ArcGIS or other desktop GIS is needed.

**What is new here:** most recharge-zone studies (e.g. Shukla & Sarup, 2019) produce one
static map. This tool adds a **temporal toggle**: the rainfall layer is computed separately for
the pre-monsoon, monsoon and post-monsoon seasons from IMD daily gridded data, so you can see
how recharge potential shifts through the year, with class breaks held fixed so the seasons
are directly comparable.

> Screenshots: _add `docs/screenshot-map.png` and `docs/screenshot-seasons.png` here after the
> first run on real data._

---

## Features

- **Region selection:** whole study area, any tehsil (GADM level 3) or watershed (HydroBASINS).
- **Seven thematic layers:** rainfall, geology, slope, soil texture, land use / land cover,
  drainage density and lineament density, each reclassified to ranks 1 (poor) to 5 (excellent).
- **AHP weighting:** Saaty pairwise matrix, principal-eigenvector weights, consistency ratio
  check (CR < 0.10). Weights can be adjusted live with sliders.
- **Season toggle:** Annual, Pre-monsoon (Mar-May), Monsoon (Jun-Sep), Post-monsoon (Oct-Dec),
  plus a side-by-side comparison of class shares across seasons.
- **Interactive map:** Leaflet, with OSM / satellite / topographic basemaps, every thematic layer
  as a toggleable overlay, and a legend.
- **Exports:** palette GeoTIFF of the zones and a two-page PDF report (map, weights, CR, areas).
- **Synthetic demo data** so the whole system runs before the real downloads are ready.

## Tech stack

| Part | Tools |
|---|---|
| Raster / vector processing | rasterio, GDAL, geopandas, shapely, pyproj |
| Terrain analysis | pysheds (flow direction / accumulation), scikit-image (lineaments) |
| Rainfall | imdlib, xarray, pykrige (ordinary kriging), IDW fallback |
| Weighting and overlay | NumPy (AHP eigenvector method, weighted sum) |
| API | FastAPI + uvicorn |
| Frontend | Leaflet 1.9, plain HTML/CSS/JS (no build step) |
| Reports | matplotlib (PDF), rasterio (GeoTIFF) |

## Repository layout

```
groundwater-recharge-bhopal/
├── backend/
│   ├── app.py               FastAPI app (API + serves the frontend)
│   ├── config.py            study area, CRS, paths, seasons, reclassification rules
│   ├── ahp.py               AHP weights and consistency ratio
│   ├── overlay.py           reclassification, weighted overlay, zone classification
│   ├── export.py            GeoJSON, GeoTIFF and PDF outputs
│   ├── grid.py              reference grid and raster I/O helpers
│   ├── regions.py           tehsil / watershed boundaries
│   ├── pipeline.py          command-line pipeline (raw data -> zones)
│   └── preprocessing/
│       ├── dem.py           slope, drainage density, lineament density
│       ├── rainfall.py      IMD seasonal totals + kriging
│       ├── lulc.py          ESA WorldCover land cover
│       ├── soil.py          SoilGrids texture (USDA triangle)
│       └── geology.py       lithology polygons -> ranks
├── frontend/                index.html, css/, js/ (map.js, ahp.js, temporal.js)
├── notebooks/exploration.ipynb
├── scripts/make_synthetic_data.py
├── tests/                   pytest suite (unit + end-to-end)
└── data/                    local data, not committed
```

---

## Setup

Python 3.10 or newer. GDAL comes bundled with the rasterio wheels, so a plain `pip` install
works on Windows, macOS and Linux.

```bash
git clone https://github.com/rudraanshpatel/groundwater-recharge-bhopal.git
cd groundwater-recharge-bhopal
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Quick start with synthetic data (2 minutes)

This checks that everything works before you download anything. The data is **invented**,
so never use these maps in a report.

```bash
python scripts/make_synthetic_data.py          # writes data/demo/raw
export GWR_DATA_DIR=data/demo GWR_RESOLUTION=250    # Windows PowerShell: $env:GWR_DATA_DIR="data/demo"; $env:GWR_RESOLUTION="250"
python -m backend.pipeline all
uvicorn backend.app:app --reload
```

Open http://127.0.0.1:8000, pick a region, press **Generate recharge map**, then try the season
buttons. API docs are at http://127.0.0.1:8000/docs.

### Run the tests

```bash
pytest
```

---

## Data download

All files go under `data/raw/` (the default `GWR_DATA_DIR` is `data/`). Download box:
**76.5–79.0°E, 21.9–24.0°N** (WGS84). Analysis grid: **EPSG:32643 (UTM 43N), 30 m**.

| Layer | Source | Put it here |
|---|---|---|
| Boundaries | [GADM 4.1 India](https://gadm.org/download_country.html), level 3, filter to Bhopal / Raisen / Hoshangabad | `data/raw/boundaries/tehsils.gpkg` (field `NAME_3`) |
| Watersheds | [HydroBASINS](https://www.hydrosheds.org/products/hydrobasins) Asia, level 10 | `data/raw/boundaries/watersheds.gpkg` (field `HYBAS_ID`) |
| DEM | Copernicus GLO-30, 9 tiles N21–N23 × E076–E078 (AWS, no login) or Bhuvan CartoDEM | `data/raw/dem/*.tif` |
| Rainfall | IMD 0.25° daily gridded rainfall 2014–2024 | `data/raw/imd/rain.nc` (see below) |
| LULC | [ESA WorldCover 2021](https://esa-worldcover.org/en/data-access), tiles N21E075 and N21E078 | `data/raw/worldcover/*.tif` |
| Soil | [ISRIC SoilGrids](https://www.isric.org/explore/soilgrids) clay / sand / silt, 0–5, 5–15, 15–30 cm | `data/raw/soilgrids/{clay,sand,silt}_{0-5cm,5-15cm,15-30cm}.tif` |
| Geology | [GSI Bhukosh](https://bhukosh.gsi.gov.in/Bhukosh/Public) lithology 1:50K, or polygons digitised from GSI District Resource Maps | `data/raw/geology/lithology.gpkg` (text field `lithology`) |
| Lineaments (optional) | Bhukosh / NRSC lineament lines. If absent, they are auto-extracted from the DEM | `data/raw/lineaments/lineaments.gpkg` |

**DEM tiles:**

```bash
mkdir -p data/raw/dem && cd data/raw/dem
for lat in 21 22 23; do for lon in 076 077 078; do
  n="Copernicus_DSM_COG_10_N${lat}_00_E${lon}_00_DEM"
  curl -O "https://copernicus-dem-30m.s3.amazonaws.com/${n}/${n}.tif"
done; done
```

**Rainfall** (downloads the IMD binary files with `imdlib` and saves a study-area NetCDF):

```bash
python -m backend.pipeline download-rain
```

**SoilGrids** (clips straight from the global VRTs with GDAL):

```bash
mkdir -p data/raw/soilgrids && cd data/raw/soilgrids
for p in clay sand silt; do for d in 0-5cm 5-15cm 15-30cm; do
  gdal_translate -projwin_srs EPSG:4326 -projwin 76.5 24.0 79.0 21.9 \
    /vsicurl/https://files.isric.org/soilgrids/latest/data/${p}/${p}_${d}_mean.vrt ${p}_${d}.tif
done; done
```

**Geology:** the lithology names are matched by keyword (`alluvium`, `basalt`, `sandstone`,
`shale`, `granite` ...) to ranks in `config.GEOLOGY_RANKS`. Unmatched names are logged, so extend
that table if your source uses other terms.

### Run the pipeline on real data

```bash
python -m backend.pipeline all       # or one step at a time: dem, rainfall, lulc, soil, geology, rank, overlay
uvicorn backend.app:app
```

Steps whose inputs are missing are skipped with a warning, so you can add layers as they
arrive. At 30 m the study area is about 8,700 × 7,900 pixels. If your laptop struggles, set
`GWR_RESOLUTION=60` or `90` for a demo run.

---

## Methodology

### 1. Common reference grid

Every layer is reprojected onto one grid (EPSG:32643, 30 m, snapped to the study-area bounding
box). Continuous data use bilinear resampling and categorical data (land cover) use mode, so the
overlay is a plain pixel-by-pixel operation with no misalignment.

### 2. Thematic layers

| Layer | Derived how | Why it matters |
|---|---|---|
| Rainfall | IMD daily grids → per-year seasonal totals → long-term mean → ordinary kriging to 1 km → bilinear to 30 m | Water available for recharge; the only seasonal layer |
| Geology | Lithology polygons rasterised and ranked by rock type | Primary porosity and permeability of the aquifer |
| Slope | Horn's 3×3 method on the DEM, degrees | Gentle slopes hold water long enough to infiltrate |
| Soil | Thickness-weighted 0–30 cm clay/sand/silt → USDA texture class | Infiltration capacity |
| LULC | ESA WorldCover classes | Vegetation and water bodies aid recharge, built-up surfaces block it |
| Drainage density | Depression filling, D8 flow accumulation, streams above 1 km² catchment, stream length per km² | High density means fast runoff, less infiltration |
| Lineament density | Mapped lineaments, or multi-azimuth hillshade + Canny edges + Hough transform; line length per km² | Fractures are preferential recharge paths |

### 3. Reclassification

Each layer is converted to a rank from 1 (poor) to 5 (excellent). The thresholds live in
`backend/config.py` (`CONTINUOUS_RULES`, `LULC_RANKS`, `SOIL_TEXTURES`, `GEOLOGY_RANKS`), for example
slope 0–2° → 5, 2–5° → 4, 5–10° → 3, 10–20° → 2, > 20° → 1. Rainfall uses **the same thresholds
for every season**, so a dry season genuinely ranks lower instead of being re-scaled.

### 4. AHP weights

The Analytic Hierarchy Process (Saaty, 1980) turns pairwise judgements ("rainfall is twice as
important as slope") into weights:

1. Build an n × n reciprocal matrix **A** with aᵢⱼ on the 1–9 scale and aⱼᵢ = 1/aᵢⱼ.
2. The weights are the normalised principal eigenvector of **A**; λmax is its eigenvalue.
3. Consistency index CI = (λmax − n)/(n − 1); consistency ratio CR = CI / RI, with RI = 1.32 for
   n = 7. The matrix is accepted when CR < 0.10.

Default weights (starting values for this study area; check them against the papers you cite):

| Rainfall | Geology | Slope | Soil | LULC | Drainage density | Lineament density |
|---|---|---|---|---|---|---|
| 0.25 | 0.20 | 0.15 | 0.12 | 0.10 | 0.10 | 0.08 |

`backend/ahp.py` contains a Saaty-scale pairwise matrix whose eigenvector reproduces these within
±0.01, with **CR = 0.012**. Run `python -m backend.ahp` to print it. Replace the matrix with your
own judgements and cite the sources you base them on.

### 5. Weighted overlay and zoning

The Groundwater Recharge Potential Index is

```
GWRPI = Σ wᵢ × rankᵢ        (Σ wᵢ = 1, so GWRPI ranges from 1 to 5)
```

GWRPI is split into five classes: Very Low, Low, Moderate, High and Very High. By default the
breaks are the quintiles of the **annual** map, saved once and reused for every season and
region, which keeps all maps comparable. Fixed, equal-interval and Jenks natural breaks are
also available (`--classification` on the pipeline, `classification` in the API).

### 6. Temporal variation

Only rainfall changes between seasons at present; all other layers are static, and the app says
so. Planned extensions: seasonal NDVI from Sentinel-2, season-specific AHP weights, and
validation against CGWB pre- and post-monsoon well levels.

---

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Web map |
| GET | `/api/config` | Seasons, layers, classes, default weights |
| GET | `/api/status` | Which processed layers exist |
| GET | `/api/regions`, `/api/regions/{type}` | Region list and boundaries (GeoJSON) |
| GET / POST | `/api/ahp` | Default AHP result / AHP for a custom matrix |
| POST | `/analyze` | Recharge zones (GeoJSON) and class areas for a region and season |
| GET | `/layers/{layer}` | One thematic layer as ranked GeoJSON |
| POST | `/export/geotiff`, `/export/pdf` | Downloads |

Example:

```bash
curl -X POST http://127.0.0.1:8000/analyze -H "Content-Type: application/json" \
  -d '{"region_type": "tehsil", "region_id": "tehsil-4", "season": "monsoon"}'
```

## Limitations

- GADM tehsil boundaries follow Census 2011, so newer tehsils are missing.
- Auto-extracted lineaments are provisional; mapped NRSC / GSI lineaments are better.
- IMD rainfall is 0.25° (about 27 km); interpolation smooths it, it does not add detail.
- The map shows **potential**, not measured recharge. Validate against CGWB well data before
  drawing conclusions.

## References

- Shukla, S. & Sarup, J. (2019). _Add the full title, journal and DOI of the Bhopal-region recharge
  study this project builds on._
- Saaty, T. L. (1980). _The Analytic Hierarchy Process._ McGraw-Hill.
- Horn, B. K. P. (1981). Hill shading and the reflectance map. _Proc. IEEE_, 69(1), 14–47.
- Pai, D. S. et al. (2014). Development of a new high spatial resolution (0.25° × 0.25°) long period
  daily gridded rainfall data set over India. _Mausam_, 65(1), 1–18.

## License

MIT, see [LICENSE](LICENSE).
