#!/usr/bin/env python3
"""Download USGS WBD HUC2=08 and rasterize its polygon to a NetCDF grid.

IMPORTANT: WBD HUC 08 is a hydrologic region, NOT the exact levee-affected
corridor or the upstream contributing area of any particular gauge.

Usage:
  python make_huc08_mask.py --resolution 0.25
  python make_huc08_mask.py --template-nc /path/to/CoLM_history.nc
  python make_huc08_mask.py --geojson /path/to/HUC08.geojson --resolution 0.1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import requests
import xarray as xr
from shapely.geometry import shape
from shapely.ops import unary_union
from shapely import contains_xy

SOURCE_LAYER = "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer/1"
QUERY_ENDPOINT = SOURCE_LAYER + "/query"


def get_source_geojson(local_source: str | None) -> dict:
    if local_source:
        with open(local_source, encoding="utf-8") as f:
            payload = json.load(f)
    else:
        params = {
            "where": "huc2='08'",
            "outFields": "huc2,name",
            "outSR": "4326",
            "returnGeometry": "true",
            "geometryPrecision": "7",
            "f": "geojson",
        }
        try:
            response = requests.get(QUERY_ENDPOINT, params=params, timeout=120)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError(
                "Could not retrieve USGS HUC08 GeoJSON. Try opening the source "
                "layer in a browser / QGIS and supply --geojson <local-file>."
            ) from exc

    if payload.get("type") != "FeatureCollection":
        raise ValueError(f"Expected GeoJSON FeatureCollection, got {list(payload)[:10]}")
    feats = payload.get("features", [])
    if not feats:
        raise ValueError("No HUC08 polygon returned. Check the USGS WBD query or input file.")
    # HUC2 identifier is kept as a string, preserving the leading zero.
    for feat in feats:
        attrs = {str(k).lower(): v for k, v in feat.get("properties", {}).items()}
        if "huc2" in attrs and str(attrs["huc2"]).zfill(2) != "08":
            raise ValueError(f"Unexpected HUC2 {attrs['huc2']!r}; expected '08'.")
    return payload


def load_grid(template_nc: str | None, resolution: float, bounds: tuple):
    if template_nc:
        with xr.open_dataset(template_nc, decode_times=False) as ds:
            lat_key = next((v for v in ("lat", "latitude", "LAT", "Latitude") if v in ds.variables), None)
            lon_key = next((v for v in ("lon", "longitude", "LON", "Longitude") if v in ds.variables), None)
            if not (lat_key and lon_key):
                raise ValueError("Template must include 1D latitude/longitude coordinates (lat/lon or latitude/longitude).")
            lat_da, lon_da = ds[lat_key], ds[lon_key]
            if lat_da.ndim != 1 or lon_da.ndim != 1:
                raise ValueError("Only rectilinear 1D latitude/longitude coordinates supported.")
            lat, lon = lat_da.values.copy(), lon_da.values.copy()
            lat_dim, lon_dim = lat_da.dims[0], lon_da.dims[0]
            lat_attrs, lon_attrs = dict(lat_da.attrs), dict(lon_da.attrs)
        return lat, lon, lat_dim, lon_dim, lat_attrs, lon_attrs

    if resolution <= 0 or 180 / resolution > 100000:
        raise ValueError("Resolution must be positive and reasonable.")
    # Centers on standard global -180..180 and -90..90 geographic grid,
    # then crop to HUC08 bounding box with a one-cell buffer.
    xmin, ymin, xmax, ymax = bounds
    xglobal = -180.0 + resolution / 2 + resolution * np.arange(int(round(360 / resolution)))
    yglobal = -90.0 + resolution / 2 + resolution * np.arange(int(round(180 / resolution)))
    lon = xglobal[(xglobal >= xmin - resolution) & (xglobal <= xmax + resolution)]
    lat = yglobal[(yglobal >= ymin - resolution) & (yglobal <= ymax + resolution)]
    return lat, lon, "lat", "lon", {"units": "degrees_north"}, {"units": "degrees_east"}


def make_mask(polygon, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    # Leave original coordinate arrays intact. Only wrap a COPY of the longitude
    # for the WBD polygon containment test.
    lon_for_test = np.where(lon > 180.0, lon - 360.0, lon)
    mask = np.zeros((len(lat), len(lon)), dtype=np.uint8)
    xmin, ymin, xmax, ymax = polygon.bounds
    ix = np.flatnonzero((lon_for_test >= xmin) & (lon_for_test <= xmax))
    iy = np.flatnonzero((lat >= ymin) & (lat <= ymax))
    if len(ix) and len(iy):
        xx, yy = np.meshgrid(lon_for_test[ix], lat[iy])
        values = contains_xy(polygon, xx, yy).astype(np.uint8)
        mask[np.ix_(iy, ix)] = values
    return mask


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--resolution", type=float, default=0.25, help="Degree spacing, used if --template-nc is absent (default: 0.25)")
    parser.add_argument("--template-nc", type=str, help="Existing CoLM/OpenBench NetCDF to copy exact lat/lon coordinates")
    parser.add_argument("--geojson", type=str, help="Local HUC08 GeoJSON; skip online download if supplied")
    parser.add_argument("--outdir", type=str, default="huc08_mask_output", help="Output directory")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    payload = get_source_geojson(args.geojson)
    poly = unary_union([shape(feat["geometry"]) for feat in payload["features"]])
    if poly.is_empty:
        raise RuntimeError("HUC08 polygon is empty")
    if not poly.is_valid:
        poly = poly.buffer(0)
    if not poly.is_valid:
        raise RuntimeError("HUC08 polygon geometry is invalid")

    geojson_path = outdir / "WBD_HUC2_08_Lower_Mississippi.geojson"
    with open(geojson_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    lat, lon, lat_dim, lon_dim, lat_attrs, lon_attrs = load_grid(args.template_nc, args.resolution, poly.bounds)
    mask = make_mask(poly, np.asarray(lat), np.asarray(lon))
    if not np.any(mask):
        raise RuntimeError("Zero included grid centers. Check resolution, template grid and polygon.")

    out = xr.Dataset(
        data_vars={
            "basin_mask": (
                (lat_dim, lon_dim),
                mask,
                {"long_name": "USGS Lower Mississippi hydrologic region HUC2 08 (cell-center test)", "flag_values": [0, 1], "flag_meanings": "outside inside"},
            )
        },
        coords={lat_dim: (lat_dim, lat, lat_attrs), lon_dim: (lon_dim, lon, lon_attrs)},
        attrs={
            "title": "USGS WBD HUC2=08 Lower Mississippi regional mask",
            "source_url": SOURCE_LAYER,
            "source_query": "huc2='08'",
            "method": "Cell-center inclusion in WBD HUC2=08 polygon; no area fraction",
            "warning": "HUC08 contains coastal/non-mainstem areas; not the exact levee-influenced domain nor upstream gauged watershed",
            "longitude_coords": "Original longitude values preserved; temporary conversion to [-180,180] for geometry test",
        },
    )
    output_nc = outdir / "WBD_HUC2_08_Lower_Mississippi_mask.nc"
    out.to_netcdf(output_nc)
    print("Wrote:", geojson_path)
    print("Wrote:", output_nc)
    print("Grid dims:", mask.shape, "inside cells:", int(mask.sum()))
    print("IMPORTANT: check HUC08 coverage against your model's actual Levee cells before scoring.")

if __name__ == "__main__":
    main()
