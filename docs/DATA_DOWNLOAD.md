# Downloading the real data (Windows guide)

This guide walks through every input layer: which website to open, what to click, where to save
the file, and one command that checks the file covers the study area. All commands are for
**PowerShell**, run from the repository folder with the virtual environment active:

```powershell
cd groundwater-recharge-bhopal
.venv\Scripts\activate
```

**Download box:** longitude **76.5 to 79.0 °E**, latitude **21.9 to 24.0 °N** (WGS84). Where a
website asks for a box, enter `Xmin = 76.5`, `Ymin = 21.9`, `Xmax = 79.0`, `Ymax = 24.0`.

When you are done, everything sits under `data\raw\`:

```
data\raw\
├── boundaries\   tehsils.gpkg, watersheds.gpkg   (+ the original GADM / HydroBASINS downloads)
├── dem\          one or more .tif files
├── imd\          rain.nc                          (+ the yearly .grd files)
├── worldcover\   2 .tif tiles
├── soilgrids\    9 .tif files
├── geology\      lithology.gpkg
└── lineaments\   lineaments.gpkg                  (mapped lines, combined with DEM lineaments)
```

Check everything at once at any time:

```powershell
python scripts\verify_data.py
```

Each layer gets **PASS**, **WARN** or **FAIL** with a reason. Coverage is checked over the three
districts (from `tehsils.gpkg`, so do step 1 first), or over the whole box until that exists.

| # | Layer | Website | Login | Time |
|---|---|---|---|---|
| 1 | Tehsils | gadm.org | No | 5 min |
| 1b | Watersheds | already downloaded, one command to convert | No | 1 min |
| 2 | DEM | OpenTopography (or AWS links) | No | 10 min |
| 3 | Rainfall | IMD Pune | No | 20 min |
| 4 | Land cover | ESA WorldCover | No | 10 min |
| 5 | Soil | ISRIC SoilGrids (one command) | No | 10 min |
| 6 | Geology | GSI NGDR (geodataindia.gov.in) | Free registration | 30 min |
| 7 | Lineaments + faults | GSI NGDR | Free registration | 10 min |

---

## 1. Tehsil boundaries (GADM 4.1)

1. Open <https://gadm.org/download_country.html>.
2. Choose **India** in the country list.
3. Click **GeoPackage** (the file is `gadm41_IND.gpkg`, about 50 MB to download). If it arrives as a
   `.zip`, extract it.
4. Move `gadm41_IND.gpkg` into `data\raw\boundaries\`.
5. Cut it down to the three districts (this writes `data\raw\boundaries\tehsils.gpkg`):

   ```powershell
   python scripts\prepare_data.py tehsils
   ```

   It prints the tehsils it kept for each district. GADM still calls Narmadapuram "Hoshangabad".
6. Verify:

   ```powershell
   python scripts\verify_data.py tehsils
   ```

   Expect PASS with Bhopal, Raisen and Hoshangabad listed and about 20 to 30 tehsils in total.

GADM tehsils follow Census 2011, so tehsils created since then are missing. Mention this in the
report.

## 1b. Watersheds (HydroBASINS, already downloaded)

The HydroBASINS Asia download is already in `data\raw\boundaries\`, but the app reads
`watersheds.gpkg`, not the shapefile. Convert it (finds `hybas_as_lev10_v1c.shp` on its own and
keeps only the watersheds in the box):

```powershell
python scripts\prepare_data.py watersheds
python scripts\verify_data.py watersheds
```

Expect PASS with several hundred watersheds (about 483).

## 2. DEM (Copernicus GLO-30)

**Option A, OpenTopography (one file, in the browser):**

1. Open <https://portal.opentopography.org/raster?opentopoID=OTSDEM.032021.4326.3>.
2. Tick **Manually enter selection coordinates** and enter the box (76.5, 21.9, 79.0, 24.0). Click
   **Validate coordinates**: it should say about 59,771 km².
3. Output format **GeoTiff**, layer **Digital Surface Model (DSM)** ticked. Leave all the
   visualisation boxes unticked.
4. Scroll down and click **Submit**. No login or email is needed. After a few minutes the results
   page shows a download link.
5. Extract the download and put the `.tif` (e.g. `output_COP30.tif`) in `data\raw\dem\`.

**Option B, AWS (9 tiles, no form):** click each link to download, then put all 9 files in
`data\raw\dem\`. Do not mix this with Option A: use one or the other.

| | E076 | E077 | E078 |
|---|---|---|---|
| N23 | [N23 E076](https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N23_00_E076_00_DEM/Copernicus_DSM_COG_10_N23_00_E076_00_DEM.tif) | [N23 E077](https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N23_00_E077_00_DEM/Copernicus_DSM_COG_10_N23_00_E077_00_DEM.tif) | [N23 E078](https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N23_00_E078_00_DEM/Copernicus_DSM_COG_10_N23_00_E078_00_DEM.tif) |
| N22 | [N22 E076](https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N22_00_E076_00_DEM/Copernicus_DSM_COG_10_N22_00_E076_00_DEM.tif) | [N22 E077](https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N22_00_E077_00_DEM/Copernicus_DSM_COG_10_N22_00_E077_00_DEM.tif) | [N22 E078](https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N22_00_E078_00_DEM/Copernicus_DSM_COG_10_N22_00_E078_00_DEM.tif) |
| N21 | [N21 E076](https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N21_00_E076_00_DEM/Copernicus_DSM_COG_10_N21_00_E076_00_DEM.tif) | [N21 E077](https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N21_00_E077_00_DEM/Copernicus_DSM_COG_10_N21_00_E077_00_DEM.tif) | [N21 E078](https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N21_00_E078_00_DEM/Copernicus_DSM_COG_10_N21_00_E078_00_DEM.tif) |

Each tile is about 40 MB.

**Verify:**

```powershell
python scripts\verify_data.py dem
```

Expect 100% coverage and an elevation range of roughly 250 to 1,350 m (Narmada valley floor to
Dhoopgarh in the Satpuras).

## 3. Rainfall (IMD 0.25° gridded daily)

1. Open <https://www.imdpune.gov.in/cmpg/Griddata/Rainfall_25_Bin.html>
   ("Gridded Rainfall (0.25 x 0.25) - Binary").
2. Download each year from **2014 to 2024** (11 files). Keep IMD's file names, e.g.
   `Rainfall_ind2014_rfp25.grd` (about 26 MB each).
3. Put all 11 `.grd` files in `data\raw\imd\`.
4. Turn them into one study-area file, `data\raw\imd\rain.nc`:

   ```powershell
   python scripts\prepare_data.py rain
   ```

5. Verify:

   ```powershell
   python scripts\verify_data.py rainfall
   ```

   Expect 2014-01-01 to 2024-12-31, no empty cells in the box, and a mean annual rainfall of
   roughly 1,000 to 1,400 mm.

**Shortcut:** `python -m backend.pipeline download-rain` downloads and converts everything in one
go (it uses `imdlib`). Use the website route if that command fails.

## 4. Land cover (ESA WorldCover 2021)

Click both links to download (about 100 MB each), then put them in `data\raw\worldcover\`:

- [ESA_WorldCover_10m_2021_v200_N21E075_Map.tif](https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N21E075_Map.tif) (75 to 78 °E)
- [ESA_WorldCover_10m_2021_v200_N21E078_Map.tif](https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_N21E078_Map.tif) (78 to 81 °E)

(Same files as the download page at <https://esa-worldcover.org/en/data-access>.)

```powershell
python scripts\verify_data.py lulc
```

Expect 100% coverage and cropland as the largest class. With only one tile you get about 85%:
that means the other tile is missing.

## 5. Soil (ISRIC SoilGrids)

SoilGrids has no "draw a box and download" page, so one command does it. It clips clay, sand and
silt for 0–5, 5–15 and 15–30 cm to the box and saves 9 small files in `data\raw\soilgrids\`:

```powershell
python scripts\prepare_data.py soil
python scripts\verify_data.py soil
```

Expect all 9 files, 100% coverage, and clay + sand + silt adding up to about 1,000 g/kg.

**If the command fails** (ISRIC's servers are sometimes down), try later, or open each link below
in the browser and save the file under the name shown, in `data\raw\soilgrids\`:

- `clay_0-5cm.tif`: <https://maps.isric.org/mapserv?map=/map/clay.map&SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=clay_0-5cm_mean&FORMAT=GEOTIFF_INT16&SUBSET=long(76.5,79.0)&SUBSET=lat(21.9,24.0)&SUBSETTINGCRS=http://www.opengis.net/def/crs/EPSG/0/4326&OUTPUTCRS=http://www.opengis.net/def/crs/EPSG/0/4326>
- `clay_5-15cm.tif`: <https://maps.isric.org/mapserv?map=/map/clay.map&SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=clay_5-15cm_mean&FORMAT=GEOTIFF_INT16&SUBSET=long(76.5,79.0)&SUBSET=lat(21.9,24.0)&SUBSETTINGCRS=http://www.opengis.net/def/crs/EPSG/0/4326&OUTPUTCRS=http://www.opengis.net/def/crs/EPSG/0/4326>
- `clay_15-30cm.tif`: <https://maps.isric.org/mapserv?map=/map/clay.map&SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=clay_15-30cm_mean&FORMAT=GEOTIFF_INT16&SUBSET=long(76.5,79.0)&SUBSET=lat(21.9,24.0)&SUBSETTINGCRS=http://www.opengis.net/def/crs/EPSG/0/4326&OUTPUTCRS=http://www.opengis.net/def/crs/EPSG/0/4326>
- `sand_0-5cm.tif`: <https://maps.isric.org/mapserv?map=/map/sand.map&SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=sand_0-5cm_mean&FORMAT=GEOTIFF_INT16&SUBSET=long(76.5,79.0)&SUBSET=lat(21.9,24.0)&SUBSETTINGCRS=http://www.opengis.net/def/crs/EPSG/0/4326&OUTPUTCRS=http://www.opengis.net/def/crs/EPSG/0/4326>
- `sand_5-15cm.tif`: <https://maps.isric.org/mapserv?map=/map/sand.map&SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=sand_5-15cm_mean&FORMAT=GEOTIFF_INT16&SUBSET=long(76.5,79.0)&SUBSET=lat(21.9,24.0)&SUBSETTINGCRS=http://www.opengis.net/def/crs/EPSG/0/4326&OUTPUTCRS=http://www.opengis.net/def/crs/EPSG/0/4326>
- `sand_15-30cm.tif`: <https://maps.isric.org/mapserv?map=/map/sand.map&SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=sand_15-30cm_mean&FORMAT=GEOTIFF_INT16&SUBSET=long(76.5,79.0)&SUBSET=lat(21.9,24.0)&SUBSETTINGCRS=http://www.opengis.net/def/crs/EPSG/0/4326&OUTPUTCRS=http://www.opengis.net/def/crs/EPSG/0/4326>
- `silt_0-5cm.tif`: <https://maps.isric.org/mapserv?map=/map/silt.map&SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=silt_0-5cm_mean&FORMAT=GEOTIFF_INT16&SUBSET=long(76.5,79.0)&SUBSET=lat(21.9,24.0)&SUBSETTINGCRS=http://www.opengis.net/def/crs/EPSG/0/4326&OUTPUTCRS=http://www.opengis.net/def/crs/EPSG/0/4326>
- `silt_5-15cm.tif`: <https://maps.isric.org/mapserv?map=/map/silt.map&SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=silt_5-15cm_mean&FORMAT=GEOTIFF_INT16&SUBSET=long(76.5,79.0)&SUBSET=lat(21.9,24.0)&SUBSETTINGCRS=http://www.opengis.net/def/crs/EPSG/0/4326&OUTPUTCRS=http://www.opengis.net/def/crs/EPSG/0/4326>
- `silt_15-30cm.tif`: <https://maps.isric.org/mapserv?map=/map/silt.map&SERVICE=WCS&VERSION=2.0.1&REQUEST=GetCoverage&COVERAGEID=silt_15-30cm_mean&FORMAT=GEOTIFF_INT16&SUBSET=long(76.5,79.0)&SUBSET=lat(21.9,24.0)&SUBSETTINGCRS=http://www.opengis.net/def/crs/EPSG/0/4326&OUTPUTCRS=http://www.opengis.net/def/crs/EPSG/0/4326>

## 6. Geology (GSI NGDR, 1:50K lithology)

1. Open <https://geodataindia.gov.in> (National Geoscience Data Repository) and **register** (free).
2. Log in and open the **NGDR Map Service**. Open the **Search** panel (magnifier icon).
3. Choose **Map services: Geology**, **Sub group: Geology 50k**, **Layers: LITHOLOGY**,
   **State: Madhya Pradesh**, then the districts **Bhopal, Raisen, Hoshangabad**.
4. Download the results as shapefiles. They arrive as one file per 1:50K toposheet, for example
   `lithology_gcs_ngdr_55E07.shp`.
5. Put every downloaded file (or the unzipped folders) in `data\raw\geology\ngdr\`. Merge them
   and pick the rock-type column (`lithologic` in the NGDR files):

   ```powershell
   python scripts\prepare_data.py geology data\raw\geology\ngdr --field lithologic
   ```

   Run it once without `--field` to list every text column with example values. The command
   writes `data\raw\geology\lithology.gpkg` and prints the recharge rank (1 to 5) each rock name
   will get.
6. Verify:

   ```powershell
   python scripts\verify_data.py geology
   ```

   Expect about 98% coverage of the districts. A WARN lists rock names that match no keyword:
   they get rank 2 by default. Add keywords for them to `GEOLOGY_RANKS` in `backend\config.py` if
   the default is wrong.

## 7. Lineaments and faults (GSI NGDR, 1:250K)

1. In the same NGDR **Search** panel choose **Map services: Geomorphology**, layer
   **LINEAMENT_250K**, **State: Madhya Pradesh**, and download the shapefile. Download the
   **FAULT** layer the same way.
2. Put both in `data\raw\lineaments\ngdr\` and merge them:

   ```powershell
   python scripts\prepare_data.py lineaments data\raw\lineaments\ngdr
   python scripts\verify_data.py lineaments
   ```

By default the pipeline **combines** these mapped lines with lineaments it extracts from the DEM
(`GWR_LINEAMENT_SOURCE=combine`). Set it to `mapped` to use only the GSI lines, or `dem` to ignore
them. Without a mapped file, only the DEM lineaments are used.

---

## After everything passes

```powershell
python -m backend.pipeline all
uvicorn backend.app:app
```

Then open <http://127.0.0.1:8000>. Steps with missing inputs are skipped with a warning, so you
can run the pipeline before geology arrives and re-run it later.
