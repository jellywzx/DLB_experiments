# USGS Lower Mississippi (HUC 08) mask converter

This script **does not include a pre-downloaded USGS boundary**. When run on a machine with internet access, it downloads the official WBD HUC2=08 polygon and writes the GeoJSON plus a NetCDF grid mask. HUC08 is a *hydrologic region*; it is not the Cairo–mouth levee corridor and it is not the entire upstream catchment of Vicksburg or Tarbert Landing.

## Data

- Official live WBD source (HUC2 region layer): <https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer/1>
- USGS download guidance: <https://www.usgs.gov/national-hydrography/access-national-hydrography-products>
- WBD HUC 08 old ZIP (April 2019, ~82 MB): <https://rockyweb.usgs.gov/vdelivery/Datasets/Staged/Hydrography/WBD/HU2/Shape/WBD_08_HU2_Shape.zip>
- More detailed HU4/HU8 polygons can be found at WBD MapServer layers 2 and 4.
- Actual levee location data: <https://nld.sec.usace.army.mil/data-services/services/>

## Setup

```bash
python -m pip install numpy requests 'shapely>=2' xarray scipy
```

## Run

For a standard 0.25 degree regional grid (standard Earth-grid cell centers):

```bash
python make_huc08_mask.py --resolution 0.25 --outdir huc08_025
```

For a 0.1 degree regional grid:

```bash
python make_huc08_mask.py --resolution 0.1 --outdir huc08_010
```

**Strongly recommended**: match the exact coordinates of an existing CoLM/OpenBench file to avoid floating-point alignment and off-by-half-cell mistakes:

```bash
python make_huc08_mask.py \
  --template-nc /path/to/your_model_file.nc \
  --outdir huc08_colm_exact_grid
```

If network access is unavailable on the machine running the script, manually export the USGS HUC2=08 polygon to GeoJSON, then use:

```bash
python make_huc08_mask.py --geojson /path/to/WBD_HUC08.geojson --template-nc /path/to/model.nc
```

Outputs:

1. `WBD_HUC2_08_Lower_Mississippi.geojson`: original downloaded GeoJSON feature collection.
2. `WBD_HUC2_08_Lower_Mississippi_mask.nc`: `basin_mask` values 1 (inside) and 0 (outside), with exact template latitude and longitude coordinates if a template was provided.

The rasterization is **cell-center based**, not fractional areal overlap. It preserves any model-native coordinate order, including descending latitudes and 0..360 longitudes.

## Research interpretation

- For a broad Lower Mississippi regional benchmark, HUC08 works as a first-pass geographic mask.
- For levee *mechanism attribution*, intersect the region with active model levee cells / river-network topology, retaining non-levee comparison cells as needed.
- HUC08 includes the Lower Red–Ouachita and Louisiana Coastal subregions; not every area within it drains directly to the Mississippi mainstem.
- For station-level Q/KGE at Vicksburg or Tarbert Landing, the **upstream contributing catchment** includes major areas outside HUC08. Do not mask away upstream modeling inputs. Use the model network and its own upstream completeness diagnostics.

Source: USGS WBD; derivative created by your script. The public map service indicated it had been refreshed in July 2026 at the time of preparation.
