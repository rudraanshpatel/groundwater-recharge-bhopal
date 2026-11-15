# Groundwater Recharge Zone Identifier: Bhopal Region

A web application that maps **groundwater recharge potential** for the Bhopal, Raisen and
Narmadapuram (Hoshangabad) districts of Madhya Pradesh, India.

Seven thematic layers are combined with an **AHP-weighted overlay** on a common 30 m grid to give
a five-class map (Very Low to Very High). Pick the whole study area, a tehsil or a watershed, pick a
season, and the app returns the zone map, area statistics and GeoTIFF / PDF downloads. The whole
pipeline is open-source Python: no ArcGIS or other desktop GIS is needed.

**What is new:** most recharge-zone studies produce one static map. This tool adds a **season
toggle** (Annual, Pre-monsoon, Monsoon, Post-monsoon) built from IMD daily rainfall, with class
breaks held fixed so the seasons are directly comparable, and lets you edit the AHP weights live.

**Live app:** https://rudraanshpatel.in/projects/groundwater-recharge/ (hosted on a Hugging Face
Space, see [docs/DEPLOY.md](docs/DEPLOY.md)).

## Results at a glance

| | |
|---|---|
| Study area | 76.5–79.0°E, 21.9–24.0°N; 15 tehsils, 483 watersheds |
| Grid | WGS 84 / UTM zone 43N (EPSG:32643), 30 m |
| AHP consistency | λmax = 7.091, CI = 0.015, **CR = 0.012** (limit 0.10) |
| Validation | 60 CGWB observation wells, Spearman ρ = **0.36**, p = 0.005 |

Median well recharge (water-level rise × specific yield, pre- to post-monsoon) rises through every
class:

| Zone | Very Low | Low | Moderate | High | Very High |
|---|---|---|---|---|---|
| Median recharge (m) | 0.089 | 0.111 | 0.122 | 0.136 | 0.238 |

## Features

- **Region selection:** whole study area, any tehsil (GADM level 3) or watershed (HydroBASINS level 10).
- **Seven thematic layers:** rainfall, geology, slope, soil texture, land use / land cover,
  drainage density and lineament density, each reclassified to ranks 1 (poor) to 5 (excellent).
- **AHP weighting:** Saaty pairwise matrix, principal-eigenvector weights and consistency ratio,
  adjustable live with sliders.
- **Season toggle** with a side-by-side comparison of class shares and rainfall across seasons.
- **Interactive map:** Leaflet with OSM, satellite and topographic basemaps, every thematic layer as
  a toggleable overlay, and a legend.
- **Exports:** palette GeoTIFF of the zones and a two-page PDF report (map, weights, CR, areas).
- **Validation** against CGWB well levels using the water table fluctuation method.
- **Synthetic demo data** so the whole system runs before any real download.

## Tech stack

| Part | Tools |
|---|---|
| Raster / vector processing | rasterio (GDAL), geopandas, shapely, pyproj |
| Terrain analysis | pysheds (flow direction / accumulation), scikit-image (lineaments) |
| Rainfall | imdlib, xarray, pykrige (ordinary kriging), IDW fallback |
| Weighting and overlay | NumPy (AHP eigenvector method, weighted sum) |
| Validation | pandas, SciPy (Spearman), matplotlib |
| API | FastAPI + uvicorn |
| Frontend | Leaflet 1.9, plain HTML / CSS / JS (no build step) |

## Repository layout

```
groundwater-recharge-bhopal/
├── backend/
│   ├── app.py               FastAPI app (API + serves the frontend)
│   ├── config.py            study area, CRS, paths, seasons, ranks, specific yields
│   ├── ahp.py               AHP weights and consistency ratio
│   ├── overlay.py           reclassification, weighted overlay, zone classification
│   ├── validation.py        comparison with CGWB well levels
│   ├── export.py            GeoJSON, GeoTIFF and PDF outputs
│   ├── grid.py              reference grid and raster I/O
│   ├── regions.py           tehsil / watershed boundaries
│   ├── pipeline.py          command-line pipeline (raw data -> zones -> validation)
│   └── preprocessing/       dem.py, rainfall.py, lulc.py, soil.py, geology.py
├── frontend/                index.html, css/, js/ (map.js, ahp.js, temporal.js)
├── scripts/
│   ├── prepare_data.py      turns raw downloads into the files the pipeline reads
│   ├── verify_data.py       PASS / WARN / FAIL coverage check per layer
│   ├── download_wells.py    CGWB well levels from the India-WRIS API
│   └── make_synthetic_data.py
├── notebooks/exploration.ipynb   figures of every layer, zones and the GWRPI histogram
├── docs/DATA_DOWNLOAD.md    step-by-step data download guide (Windows / PowerShell)
├── tests/                   pytest suite (unit + end-to-end on synthetic data)
└── data/                    processed results committed; raw downloads on Google Drive
```

## Setup

Python 3.10 or newer. GDAL comes bundled with the rasterio wheels, so a plain `pip` install works
on Windows, macOS and Linux.

```bash
git clone https://github.com/rudraanshpatel/groundwater-recharge-bhopal.git
cd groundwater-recharge-bhopal
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Get the data

**To see the results, nothing needs downloading.** The repository includes the processed results
for the real data: the ranked thematic layers (`data/processed/ranked/`), the recharge zone maps for
every season (`data/processed/zones/`), the tehsil and watershed boundaries, the validation outputs
(`data/outputs/validation_*`) and the report figures and tables (`data/outputs/report/`). After
[Setup](#setup), start the app with `uvicorn backend.app:app` and open http://127.0.0.1:8000.

The raw downloads and the intermediate float layers are too large for GitHub. To rerun the
pipeline from scratch, download the data folder from Google Drive:

**[Project data (Google Drive)](https://drive.google.com/drive/folders/1fQPq_4LZCuQLyWbn8kxlXo7qyZnqQWAV?usp=drive_link)**

Extract it into the repository so the files sit under `data/` (for example `data/raw/dem/`,
`data/raw/geology/lithology.gpkg`). Check the layout with `python scripts/verify_data.py`, then
follow [Run on real data](#run-on-real-data). To download every layer from its original source
instead, see [docs/DATA_DOWNLOAD.md](docs/DATA_DOWNLOAD.md).

### Quick start with synthetic data

Checks that everything works before you download anything. The data is **invented**, so never use
these maps in a report.

```bash
python scripts/make_synthetic_data.py                  # writes data/demo/raw
export GWR_DATA_DIR=data/demo GWR_RESOLUTION=250       # PowerShell: $env:GWR_DATA_DIR="data/demo"; $env:GWR_RESOLUTION="250"
python -m backend.pipeline all
uvicorn backend.app:app --reload
```

Open http://127.0.0.1:8000, pick a region, press **Generate recharge map**, then try the season
buttons. API docs are at http://127.0.0.1:8000/docs.

### Report figures and tables

```bash
python scripts/report_figures.py     # -> data/outputs/report/
```

Writes the zone-area tables (`tables.md`, CSVs, also by tehsil) and every map in the report:
location map, the seven thematic layers, the annual and seasonal recharge maps, the validation
wells and the zone-area bar chart, at 300 dpi.

### Tests

```bash
pytest
```

## Data

The quickest route is the [Google Drive folder](https://drive.google.com/drive/folders/1fQPq_4LZCuQLyWbn8kxlXo7qyZnqQWAV?usp=drive_link) above. The full download walkthrough from the original sources is in [docs/DATA_DOWNLOAD.md](docs/DATA_DOWNLOAD.md). Everything goes
under `data/raw/`; `scripts/prepare_data.py` converts each download and `scripts/verify_data.py`
checks it covers the three districts.

| Layer | Source | Native resolution |
|---|---|---|
| Tehsil boundaries | [GADM 4.1](https://gadm.org/download_country.html), level 3 | vector |
| Watersheds | [HydroBASINS](https://www.hydrosheds.org/products/hydrobasins), level 10 | vector |
| DEM | Copernicus GLO-30 (via OpenTopography or AWS) | 30 m |
| Rainfall | IMD daily gridded rainfall, 2014–2024 | 0.25° |
| Land use / land cover | [ESA WorldCover 2021](https://esa-worldcover.org) | 10 m |
| Soil texture | [ISRIC SoilGrids](https://www.isric.org/explore/soilgrids) clay / sand / silt, 0–30 cm | 250 m |
| Geology | GSI 1:50K lithology, [NGDR](https://geodataindia.gov.in) | vector |
| Lineaments | GSI 1:250K lineaments and faults (NGDR), combined with DEM-extracted lineaments | vector |
| Validation wells | CGWB groundwater levels via India-WRIS | points |

### Run on real data

```bash
python -m backend.pipeline all       # or one step: dem, rainfall, lulc, soil, geology, rank, overlay
python scripts/download_wells.py     # CGWB well levels -> data/raw/wells/wells_raw.csv
python -m backend.pipeline validate  # -> data/outputs/validation_{wells.csv,summary.json,boxplot.png}
uvicorn backend.app:app
```

Steps whose inputs are missing are skipped with a warning, so layers can be added as they arrive.
At 30 m the study area is about 8,700 × 7,900 pixels: a full run takes about 11 minutes and peaks at
about 6 GB of RAM (mostly drainage and lineament extraction). On a machine with 8 GB or less, set
`GWR_RESOLUTION=60`.

| Environment variable | Default | Purpose |
|---|---|---|
| `GWR_DATA_DIR` | `data` | data folder |
| `GWR_RESOLUTION` | `30` | grid size in metres |
| `GWR_RAINFALL_MODE` | `relative` | rank rainfall within each season, or `absolute` mm breaks |
| `GWR_LINEAMENT_SOURCE` | `combine` | `combine` (mapped + DEM), `mapped` or `dem` |

## Methodology

### 1. Common reference grid

Every layer is reprojected onto one grid (EPSG:32643, 30 m, snapped to whole pixels over the study
area box). Continuous data use bilinear resampling, land cover uses mode, and vector layers are
rasterized onto the same grid, so the overlay is a plain pixel-by-pixel operation.

### 2. Thematic layers

| Layer | Derived how | Why it matters |
|---|---|---|
| Rainfall | Per-year seasonal totals → long-term mean → ordinary kriging to 1 km → bilinear to 30 m | Water available for recharge; the only seasonal layer |
| Geology | Lithology polygons rasterized and ranked by rock type | Porosity and permeability of the aquifer |
| Slope | Horn's 3 × 3 method on the DEM, degrees | Gentle slopes hold water long enough to infiltrate |
| Soil | Thickness-weighted 0–30 cm clay / sand / silt → USDA texture class; gaps over water and built-up filled from the nearest class | Infiltration capacity |
| LULC | ESA WorldCover classes | Vegetation and water bodies aid recharge, built-up surfaces block it |
| Drainage density | Sink filling, D8 flow accumulation, streams above 1 km² catchment, length per km² in a 5 km window | Dense drainage means fast runoff |
| Lineament density | GSI lineaments and faults plus multi-azimuth hillshade → Canny edges → Hough lines; length per km² | Fractures are preferential recharge paths |

### 3. Reclassification

Each layer becomes a rank from 1 (poor) to 5 (excellent). Thresholds live in `backend/config.py`
(`CONTINUOUS_RULES`, `LULC_RANKS`, `SOIL_TEXTURES`, `GEOLOGY_RANKS`); for example slope 0–2° → 5,
2–5° → 4, 5–10° → 3, 10–20° → 2, > 20° → 1.

Rainfall is ranked **within each season** (quintiles of that season's rainfall). Pre- and
post-monsoon totals are only about 10–90 mm against 760–1,580 mm in the monsoon, so fixed
thresholds would make every dry-season map uniformly Very Low. Absolute totals are still reported
in the season comparison.

### 4. AHP weights

The Analytic Hierarchy Process (Saaty, 1980) turns pairwise judgements into weights:

1. Build a 7 × 7 reciprocal matrix **A** on Saaty's 1–9 scale, with aⱼᵢ = 1/aᵢⱼ.
2. The weights are the normalised principal eigenvector of **A**; λmax is its eigenvalue.
3. CI = (λmax − n)/(n − 1) and CR = CI / RI, with RI = 1.32 for n = 7. Accepted when CR < 0.10.

| Rainfall | Geology | Slope | Soil | LULC | Drainage density | Lineament density |
|---|---|---|---|---|---|---|
| 0.25 | 0.20 | 0.15 | 0.12 | 0.10 | 0.10 | 0.08 |

The matrix in `backend/ahp.py` reproduces these weights within ±0.01 with CR = 0.012. Run
`python -m backend.ahp` to print it.

### 5. Weighted overlay and zoning

```
GWRPI = Σ wᵢ × rankᵢ        (Σ wᵢ = 1, so GWRPI ranges from 1 to 5)
```

GWRPI is split into five classes using the quintiles of the **annual** map, saved once and reused
for every season and region so all maps stay comparable. Fixed, equal-interval and Jenks breaks
are also available (`--classification` on the pipeline, `classification` in the API).

### 6. Temporal variation

Only rainfall changes between seasons; the other six layers are static. Seasons follow IMD:
Pre-monsoon (Mar–May), Monsoon (Jun–Sep), Post-monsoon (Oct–Dec), plus Annual.

### 7. Validation

For each CGWB well, the mean May (pre-monsoon) minus November (post-monsoon) depth to water gives
the seasonal rise. Raw rise is misleading across rock types: the same recharge lifts the water
table about five times more in basalt (specific yield ≈ 0.02) than in alluvium (≈ 0.10). So each
well's rise is multiplied by the specific yield of its lithology (GEC-2015 norms, in
`config.SPECIFIC_YIELD`), the water table fluctuation method. Deep wells and piezometers, wells
with fewer than 3 years of paired readings and implausible rises are excluded, leaving 60 of 94
wells. The Spearman correlation between GWRPI and recharge is 0.36 (p = 0.005); raw rise alone
gives no agreement (ρ = −0.15).

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

```bash
curl -X POST http://127.0.0.1:8000/analyze -H "Content-Type: application/json" \
  -d '{"region_type": "tehsil", "region_id": "tehsil-4", "season": "monsoon"}'
```

## Deployment

The app is packaged as a Docker image (`Dockerfile`, `requirements-app.txt`) and deployed to a
Hugging Face Space on every push to `main` by `.github/workflows/deploy-space.yml`. Setup steps
are in [docs/DEPLOY.md](docs/DEPLOY.md).

## Limitations

- AHP weights are expert judgement; a sensitivity analysis would show how much the map depends on them.
- Inputs range from 10 m (land cover) to about 27 km (rainfall); the map is best read at watershed scale.
- Copernicus GLO-30 is a surface model, so trees and buildings slightly bias slope and drainage.
- DEM-extracted lineaments can include roads and field edges; they are combined with GSI mapped lines.
- Specific yield in the validation comes from the geology layer, which is also a map input.
- GADM tehsil boundaries follow Census 2011, so newer tehsils are missing.
- The map shows recharge **potential**, not measured recharge.

## References

- Saaty, T. L. (1980). _The Analytic Hierarchy Process._ McGraw-Hill.
- Horn, B. K. P. (1981). Hill shading and the reflectance map. _Proceedings of the IEEE_, 69(1), 14–47.
- Pai, D. S. et al. (2014). Development of a new high spatial resolution (0.25° × 0.25°) long period
  daily gridded rainfall data set over India. _Mausam_, 65(1), 1–18.
- Central Ground Water Board (2017). _Report of the Ground Water Resource Estimation Committee
  (GEC-2015)._ Ministry of Water Resources, Government of India.

## License

MIT, see [LICENSE](LICENSE).
