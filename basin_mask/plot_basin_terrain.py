#!/usr/bin/env python3
"""Draw four production basin masks over local MERIT Hydro surface elevations.

Run with /share/home/dq134/.conda/envs/openbench/bin/python.
DEM display uses every sixth native pixel (1.5 arcmin); no area averaging.
Mask membership is nearest-cell sampling of basin_mask > 0, including partial
boundary cells. Output contains four PNG/PDF pairs, a shared-scale 2x2 panel,
and metadata. Existing files require --overwrite.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import BoundaryNorm, LightSource, LinearSegmentedColormap
from matplotlib.cm import ScalarMappable
import cartopy.crs as ccrs
import cartopy.feature as cfeature

ROOT = Path(__file__).resolve().parent
DEM = Path("/share/home/dq013/zhwei/colm/data/CoLMrawdata/topography_MERITHydro.nc")
ROUTING = Path("/share/home/dq013/zhwei/colm/data/CoLMruntime/unitcatchment/grid_routing_data_06min.nc")
BASINS = {"amazon": "Amazon", "missouri": "Missouri",
          "lower_mississippi": "Lower Mississippi", "mekong": "Mekong"}
BOUNDS = [-100, 0, 50, 100, 200, 500, 1000, 2000, 3000, 4000, 5000, 6000, 7000]
CMAP = LinearSegmentedColormap.from_list("land_elevation", [
    "#347c56", "#6da66d", "#b9c88a", "#e4d29a", "#c5a478",
    "#a37c5c", "#8c7770", "#c6bdb5", "#ffffff"], N=len(BOUNDS) - 1)
NORM = BoundaryNorm(BOUNDS, CMAP.N)


def values(var):
    return np.asarray(np.ma.filled(np.ma.asarray(var[:], dtype=float), np.nan))


def mask_at(mask, lon, lat, xs, ys):
    """Sample uniform pixel-center grids; works for ascending/descending axes."""
    col = np.floor((xs - lon[0]) / (lon[1] - lon[0]) + 0.5).astype(int)
    row = np.floor((ys - lat[0]) / (lat[1] - lat[0]) + 0.5).astype(int)
    if np.any((col < 0) | (col >= len(lon))) or np.any((row < 0) | (row >= len(lat))):
        raise ValueError("Requested points lie outside the global mask grid")
    return mask[row, col]


def load_rivers():
    with Dataset(ROUTING) as ds:
        seq, nxt, lon, lat, area = [values(ds[k]) for k in
                                   ("seq", "seq_next", "seq_lon", "seq_lat", "topo_uparea")]
    rows = np.flatnonzero(np.isfinite(seq) & (seq > 0))
    rows = rows[np.argsort(seq[rows])]
    pos = np.searchsorted(seq[rows], nxt)
    src = np.flatnonzero((nxt > 0) & (pos < len(rows)) & (area >= 5e9))
    src = src[seq[rows[pos[src]]] == nxt[src]]
    dst = rows[pos[src]]
    valid = ((src != dst) & np.isfinite(lon[src]) & np.isfinite(lat[src])
             & np.isfinite(lon[dst]) & np.isfinite(lat[dst]))
    src, dst = src[valid], dst[valid]
    segments = np.stack((np.column_stack((lon[src], lat[src])),
                         np.column_stack((lon[dst], lat[dst]))), axis=1)
    return segments


def prepare(name, mask_dir, dem, rivers, stride):
    path = mask_dir / name / f"{name}_basin.nc"
    with Dataset(path) as ds:
        mask = values(ds["basin_mask"]) > 0
        lon, lat = values(ds["lon"]), values(ds["lat"])
        definition = ds.definition
        source_routing = ds.source_routing_file
    if Path(source_routing).name != ROUTING.name:
        raise ValueError(f"Mask uses a different routing dataset: {source_routing}")
    rr, cc = np.where(mask)
    if not len(rr):
        raise ValueError(f"Empty mask: {path}")
    dx, dy = abs(lon[1] - lon[0]), abs(lat[1] - lat[0])
    extent = [float(lon[cc].min() - dx / 2 - 0.7), float(lon[cc].max() + dx / 2 + 0.7),
              float(lat[rr].min() - dy / 2 - 0.7), float(lat[rr].max() + dy / 2 + 0.7)]
    xs, ys = values(dem["lon"]), values(dem["lat"])
    ic = np.flatnonzero((xs >= extent[0]) & (xs <= extent[1]))
    ir = np.flatnonzero((ys >= extent[2]) & (ys <= extent[3]))
    cs, rs = slice(ic[0], ic[-1] + 1, stride), slice(ir[0], ir[-1] + 1, stride)
    x, y = xs[cs], ys[rs]
    z = np.ma.filled(dem["elevation"][rs, cs], np.nan).astype(float)
    inside = mask_at(mask, lon, lat, x[None, :], y[:, None])
    valid = inside & np.isfinite(z)
    if not valid.any():
        raise ValueError(f"No valid DEM elevations inside {name}")
    spacing_x = abs(x[1] - x[0]) * 111320 * np.cos(np.deg2rad(y.mean()))
    spacing_y = abs(y[1] - y[0]) * 111320
    # Finite surroundings are used for hillshade; missing cells remain hidden.
    shade_input = np.where(np.isfinite(z), z, 0)
    shade = LightSource(azdeg=315, altdeg=45).hillshade(shade_input, dx=spacing_x, dy=spacing_y)
    rgb = CMAP(NORM(np.nan_to_num(z)))[..., :3]
    rgb = LightSource().blend_soft_light(rgb, shade[..., None])
    rgba = np.dstack((rgb, valid.astype(float)))
    image_extent = [x[0] - (x[1] - x[0]) / 2, x[-1] + (x[1] - x[0]) / 2,
                    y[-1] - abs(y[1] - y[0]) / 2, y[0] + abs(y[1] - y[0]) / 2]
    visible = ((rivers[:, :, 0] >= extent[0]) & (rivers[:, :, 0] <= extent[1])
               & (rivers[:, :, 1] >= extent[2]) & (rivers[:, :, 1] <= extent[3])).all(axis=1)
    segments = rivers[visible]
    # Both endpoints must belong to the selected mask.
    visible = mask_at(mask, lon, lat, segments[:, :, 0], segments[:, :, 1]).all(axis=1)
    segments = segments[visible]
    r0, r1, c0, c1 = max(0, rr.min()-1), min(len(lat), rr.max()+2), max(0, cc.min()-1), min(len(lon), cc.max()+2)
    metadata = {"basin": name, "mask": str(path), "definition": definition,
                "extent": extent, "mask_cells": int(mask.sum()), "dem_display_valid_pixels": int(valid.sum()),
                "dem_display_missing_pixels_inside_mask": int((inside & ~np.isfinite(z)).sum()),
                "display_elevation_min_m": float(z[valid].min()), "display_elevation_max_m": float(z[valid].max()),
                "river_segments": len(segments)}
    return dict(name=name, rgba=rgba, image_extent=image_extent, extent=extent,
                mask=mask[r0:r1, c0:c1], lon=lon[c0:c1], lat=lat[r0:r1],
                rivers=segments, metadata=metadata)


def draw(ax, data, label=""):
    ax.set_extent(data["extent"], crs=ccrs.PlateCarree())
    ax.add_feature(cfeature.LAND.with_scale("50m"), facecolor="#f2f0e9", zorder=0)
    ax.add_feature(cfeature.OCEAN.with_scale("50m"), facecolor="#eaf3f8", zorder=0)
    ax.add_feature(cfeature.COASTLINE.with_scale("50m"), linewidth=0.6, edgecolor="#555555")
    ax.add_feature(cfeature.BORDERS.with_scale("50m"), linewidth=0.4, edgecolor="#999999")
    ax.imshow(data["rgba"], extent=data["image_extent"], origin="upper",
              transform=ccrs.PlateCarree(), interpolation="nearest", zorder=1)
    ax.contour(data["lon"], data["lat"], data["mask"].astype(float), levels=[0.5],
               colors="#343434", linewidths=0.65, transform=ccrs.PlateCarree(), zorder=4)
    ax.add_collection(LineCollection(data["rivers"], colors="#377fa8", linewidths=0.35,
                                     alpha=0.8, transform=ax.transData, zorder=3))
    grid = ax.gridlines(draw_labels=True, linewidth=0.35, color="gray", alpha=0.5, linestyle=":")
    grid.top_labels = grid.right_labels = False
    grid.xlabel_style = grid.ylabel_style = {"size": 9}
    ax.set_title(f"{label}{BASINS[data['name']]}", fontsize=13, weight="bold", pad=10)


def save(fig, stem, output):
    for ext in ("png", "pdf"):
        path = output / f"{stem}.{ext}"
        tmp = path.with_name(f".{stem}.tmp.{ext}")
        try:
            fig.savefig(tmp, dpi=300, bbox_inches="tight", format=ext)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        print(path, flush=True)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mask-dir", type=Path, default=ROOT / "outputs/basin_masks_06min")
    parser.add_argument("--dem-file", type=Path, default=DEM)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/basin_terrain_06min")
    parser.add_argument("--dem-stride", type=int, default=6)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.dem_stride < 1:
        parser.error("dem-stride must be positive")
    stems = [*(f"{name}__terrain" for name in BASINS), "four_basins__terrain"]
    if not args.overwrite and any((args.output_dir / f"{s}.{ext}").exists()
                                  for s in stems for ext in ("png", "pdf")):
        parser.error("Output exists; use --overwrite or a different output directory")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rivers = load_rivers()
    datasets = []
    with Dataset(args.dem_file) as dem:
        if dem["elevation"].units != "m":
            raise ValueError("DEM elevation units must be metres")
        report = {"dem_file": str(args.dem_file), "dem_source": dem.source,
                  "dem_native_resolution_arcsec": 15, "dem_stride": args.dem_stride,
                  "sampling": "nearest displayed native DEM pixel; no averaging",
                  "mask_rule": "basin_mask > 0; nearest grid cell; full partial cells retained",
                  "routing_file": str(ROUTING), "river_min_upstream_area_km2": 5000,
                  "color_boundaries_m": BOUNDS, "hillshade": {"azimuth": 315, "altitude": 45}, "basins": []}
        for name in BASINS:
            data = prepare(name, args.mask_dir, dem, rivers, args.dem_stride)
            datasets.append(data)
            report["basins"].append(data["metadata"])
            fig, ax = plt.subplots(figsize=(10, 9), subplot_kw={"projection": ccrs.PlateCarree()})
            draw(ax, data)
            cb = fig.colorbar(ScalarMappable(norm=NORM, cmap=CMAP), ax=ax, shrink=0.85, pad=0.06,
                              ticks=[0, 100, 500, 1000, 2000, 3000, 4000, 5000, 6000, 7000])
            cb.set_label("Elevation (m); shaded relief")
            fig.text(0.5, 0.02, "MERIT Hydro surface elevation · CoLM 06min basin mask · rivers ≥5,000 km²", ha="center", fontsize=8)
            save(fig, f"{name}__terrain", args.output_dir)
    fig, axes = plt.subplots(2, 2, figsize=(15, 12), subplot_kw={"projection": ccrs.PlateCarree()})
    fig.subplots_adjust(left=0.06, right=0.93, top=0.93, bottom=0.19, wspace=0.15, hspace=0.2)
    for i, (ax, data) in enumerate(zip(axes.flat, datasets)):
        draw(ax, data, f"({chr(97+i)}) ")
    cax = fig.add_axes([0.2, 0.095, 0.6, 0.018])
    cb = fig.colorbar(ScalarMappable(norm=NORM, cmap=CMAP), cax=cax, orientation="horizontal",
                      ticks=[0, 100, 500, 1000, 2000, 3000, 4000, 5000, 6000, 7000])
    cb.set_label("Elevation (m); shaded relief")
    fig.suptitle("Terrain of the four study basins", fontsize=17, weight="bold")
    fig.text(0.5, 0.012, "MERIT Hydro surface elevation · CoLM 06min basin masks · rivers ≥5,000 km²\n"
             "Lower Mississippi: mouth upstream catchment minus Cairo upstream catchment", ha="center", fontsize=9)
    save(fig, "four_basins__terrain", args.output_dir)
    (args.output_dir / "plot_metadata.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
