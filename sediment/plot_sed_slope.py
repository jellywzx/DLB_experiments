#!/usr/bin/env python3
"""按 export_sediment_station_info.py 的底图和河段样式绘制 sed_slope。

本机运行：
    /share/home/dq134/.conda/envs/openbench/bin/python plot_sed_slope.py
区域示例（Amazon）：
    python plot_sed_slope.py --extent -82 -44 -22 8 --layer mean

默认输出全部坡面层和算术平均值；mean 仅作概览，不代表模型有效坡度。
每段颜色使用源节点的 sed_slope；不做单位转换。默认输出 300 dpi PNG。
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
from matplotlib.colors import BoundaryNorm
import cartopy.crs as ccrs
import cartopy.feature as cfeature


DEFAULT_ROUTING = Path("/share/home/dq013/zhwei/colm/data/CoLMruntime/unitcatchment/grid_routing_data_06min.nc")
DETAIL = {"all": (None, 0.35), "fine": (500, 0.45),
          "medium": (5000, 0.65), "coarse": (50000, 0.85)}


def read_values(dataset, name):
    return np.ma.filled(np.ma.asarray(dataset[name][:], dtype=float), np.nan)


def load_segments(path, extent, detail):
    with Dataset(path) as ds:
        seq, nxt, lon, lat, area = [read_values(ds, name) for name in
                                   ("seq", "seq_next", "seq_lon", "seq_lat", "topo_uparea")]
        if str(getattr(ds["topo_uparea"], "units", "")).lower() not in ("m2", "m^2", "m²"):
            raise ValueError("topo_uparea must use m²")
        var = ds["sed_slope"]
        slope = read_values(ds, "sed_slope")
        dims = var.dimensions
        if len(dims) != 2 or "slope_layers" not in dims:
            raise ValueError(f"Expected 2D sed_slope with slope_layers; found {dims}")
        slope = np.moveaxis(slope, dims.index("slope_layers"), 1)
        units = str(getattr(var, "units", "unspecified in NetCDF"))
    if slope.shape[0] != len(seq) or any(a.shape != seq.shape for a in (nxt, lon, lat, area)):
        raise ValueError("Routing node dimensions do not match")
    ids = np.flatnonzero(np.isfinite(seq) & (seq > 0))
    ids = ids[np.argsort(seq[ids])]
    if not len(ids) or np.any(np.diff(seq[ids]) == 0):
        raise ValueError("seq must contain unique positive IDs")
    positions = np.searchsorted(seq[ids], nxt)
    candidates = np.flatnonzero(np.isfinite(nxt) & (nxt > 0) & (positions < len(ids)))
    sources = candidates[seq[ids[positions[candidates]]] == nxt[candidates]]
    destinations = ids[positions[sources]]
    west, east, south, north = extent
    valid_coords = (np.isfinite(lon) & np.isfinite(lat) & (lon >= west) & (lon <= east)
                    & (lat >= south) & (lat <= north))
    valid = (valid_coords[sources] & valid_coords[destinations] & (sources != destinations)
             & np.isfinite(area[sources]) & (area[sources] >= 0))
    threshold, _ = DETAIL[detail]
    if threshold is not None:
        valid &= area[sources] >= threshold * 1e6
    sources, destinations = sources[valid], destinations[valid]
    segments = np.stack((np.column_stack((lon[sources], lat[sources])),
                         np.column_stack((lon[destinations], lat[destinations]))), axis=1)
    # Split dateline crossings and copy source values onto both pieces.
    crossing = np.abs(segments[:, 1, 0] - segments[:, 0, 0]) > 180
    pieces, rows = [segments[~crossing]], [sources[~crossing]]
    for segment, source in zip(segments[crossing], sources[crossing]):
        start, end = segment
        boundary = 180.0 if start[0] > 0 else -180.0
        end_lon = end[0] + (360 if start[0] > 0 else -360)
        frac = (boundary - start[0]) / (end_lon - start[0]) if end_lon != start[0] else 0
        latitude = start[1] + frac * (end[1] - start[1])
        pieces.append(np.array([[start, (boundary, latitude)], [(-boundary, latitude), end]]))
        rows.append(np.array([source, source]))
    return np.concatenate(pieces), slope[np.concatenate(rows)], units, len(sources)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--routing-file", type=Path, default=DEFAULT_ROUTING)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "sed_slope_maps")
    parser.add_argument("--extent", nargs=4, type=float, default=(-180, 180, -90, 90),
                        metavar=("WEST", "EAST", "SOUTH", "NORTH"))
    parser.add_argument("--river-detail", choices=DETAIL, default="fine")
    parser.add_argument("--layer", default="all", help="all (default), mean, or a 1-based layer number")
    parser.add_argument("--format", choices=("png", "pdf", "both"), default="png")
    parser.add_argument("--dpi", type=int, default=300)
    args = parser.parse_args()
    west, east, south, north = args.extent
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        parser.error("extent must satisfy -180 <= WEST < EAST <= 180 and -90 <= SOUTH < NORTH <= 90")
    segments, slopes, units, count = load_segments(args.routing_file, args.extent, args.river_detail)
    slopes[~np.isfinite(slopes) | (slopes < 0)] = np.nan
    nlayer = slopes.shape[1]
    if args.layer == "all":
        layers = [*range(nlayer), "mean"]
    elif args.layer == "mean":
        layers = ["mean"]
    elif args.layer.isdigit() and 1 <= int(args.layer) <= nlayer:
        layers = [int(args.layer) - 1]
    else:
        parser.error(f"layer must be all, mean, or 1..{nlayer}")
    if not np.isfinite(slopes).any():
        raise ValueError("No valid sed_slope values in the selected river network")
    boundaries = [0, 0.1, 0.5, 1, 2, 5, 10, 20, 40, 60, 90]
    cmap = plt.get_cmap("YlOrRd", len(boundaries) - 1)
    norm = BoundaryNorm(boundaries, cmap.N)
    extend = "max" if np.nanmax(slopes) > boundaries[-1] else "neither"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = {"routing_file": str(args.routing_file.resolve()), "extent": args.extent,
              "river_detail": args.river_detail, "river_segments": count, "units": units,
              "color_boundaries": boundaries, "mean_definition": "arithmetic mean; all layers required valid",
              "maps": []}
    for layer in layers:
        values = np.mean(slopes, axis=1) if layer == "mean" else slopes[:, layer]
        valid = np.isfinite(values)
        name = "mean" if layer == "mean" else f"layer_{layer + 1:02d}"
        title = f"arithmetic mean of {nlayer} layers" if layer == "mean" else f"slope layer {layer + 1}"
        fig, ax = plt.subplots(figsize=(16, 10), subplot_kw={"projection": ccrs.PlateCarree()})
        ax.add_feature(cfeature.LAND, facecolor="#f2f0e9", zorder=0)
        ax.add_feature(cfeature.OCEAN, facecolor="#eaf3f8", zorder=0)
        ax.add_feature(cfeature.COASTLINE, linewidth=0.7, edgecolor="#555555")
        ax.add_feature(cfeature.BORDERS, linewidth=0.4, edgecolor="#888888")
        ax.set_extent(args.extent, crs=ccrs.PlateCarree())
        collection = LineCollection(segments[valid], cmap=cmap, norm=norm,
                                    linewidths=DETAIL[args.river_detail][1], alpha=0.9,
                                    transform=ccrs.PlateCarree(), zorder=3, rasterized=True)
        collection.set_array(values[valid])
        ax.add_collection(collection)
        grid = ax.gridlines(draw_labels=True, linewidth=0.35, color="gray", alpha=0.5, linestyle=":")
        grid.top_labels = grid.right_labels = False
        threshold = DETAIL[args.river_detail][0]
        selection = "all upstream areas" if threshold is None else f"upstream area ≥ {threshold:,} km²"
        ax.set_title(f"CoLM sed_slope — {title}\n{selection}", fontsize=13, weight="bold")
        colorbar = fig.colorbar(collection, ax=ax, orientation="horizontal", pad=0.08,
                               shrink=0.8, aspect=45, ticks=boundaries, extend=extend)
        colorbar.set_label(f"sed_slope (units: {units}); discrete intervals")
        formats = ("png", "pdf") if args.format == "both" else (args.format,)
        for fmt in formats:
            path = args.output_dir / f"sed_slope__{name}.{fmt}"
            temporary = path.with_name(f".{path.stem}.tmp.{fmt}")
            try:
                fig.savefig(temporary, dpi=args.dpi, bbox_inches="tight", format=fmt)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            print(path, flush=True)
        plt.close(fig)
        finite = values[valid]
        report["maps"].append({"layer": name, "valid_pieces": int(valid.sum()),
                               "min": float(finite.min()) if len(finite) else None,
                               "max": float(finite.max()) if len(finite) else None})
    (args.output_dir / "plot_metadata.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
