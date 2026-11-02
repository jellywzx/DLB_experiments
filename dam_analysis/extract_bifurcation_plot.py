#!/usr/bin/env python3
"""Plot CoLM bifurcation paths for standard basins or a custom extent."""

from __future__ import annotations

import argparse
from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator

from openbench_dams_rivers_common import (
    DEFAULT_ROUTING_FILE,
    OUTPUT_ROOT,
    STANDARD_BASINS,
    StandardBasinMask,
    basin_boundary_arrays,
    load_standard_basin_mask,
    routing_segment_indices,
    routing_segments,
    validate_extent,
)
from routing_overlay_layers import (
    RoutingOverlaySource,
    load_routing_overlay_source,
    select_routing_overlay_layers,
    standard_basin_membership,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot CoLM bifurcation paths for the four standard basins by default."
    )
    parser.add_argument("--routing-file", type=Path, default=DEFAULT_ROUTING_FILE)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--regions",
        nargs="+",
        choices=tuple(STANDARD_BASINS),
        help="Standard basins to plot; default: all four.",
    )
    mode.add_argument(
        "--extent",
        type=float,
        nargs=4,
        metavar=("WEST", "EAST", "SOUTH", "NORTH"),
        help="Custom single-map extent instead of standard basin masks.",
    )
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_ROOT)
    parser.add_argument(
        "--output",
        type=Path,
        help="Output PNG for --extent mode; invalid in standard-basin mode.",
    )
    parser.add_argument(
        "--min-uparea-km2",
        type=float,
        default=500.0,
        help="Minimum upstream area for the gray main-river background (default: 500).",
    )
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument(
        "--figsize", type=float, nargs=2, metavar=("WIDTH", "HEIGHT"), default=(10.0, 7.0)
    )
    return parser.parse_args()


def load_data(routing_file: Path) -> RoutingOverlaySource:
    return load_routing_overlay_source(
        routing_file, load_levee=False, load_bifurcations=True
    )


def select_paths(
    data: RoutingOverlaySource,
    extent: tuple[float, float, float, float],
    minimum_uparea_km2: float,
    basin: StandardBasinMask | None,
) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    route_inside = None
    invalid_grid_indices = 0
    if basin is not None:
        route_inside, invalid_grid_indices = standard_basin_membership(data, basin)

    safe_destination = np.maximum(data.destination, 0)
    main_mask = data.upstream_area >= minimum_uparea_km2 * 1_000_000.0
    if route_inside is not None:
        main_mask &= route_inside & route_inside[safe_destination]
    main_sources, main_dateline = routing_segment_indices(
        data.longitude, data.latitude, data.destination, extent, main_mask
    )
    if main_sources.size == 0:
        raise ValueError("No main-river segments remain for the selected basin or extent.")
    main_segments = routing_segments(
        data.longitude, data.latitude, main_sources, data.destination
    )

    layers = select_routing_overlay_layers(
        data, extent, show_levee=False, show_bifurcations=True,
        route_inside=route_inside,
    )
    bifurcation_segments = layers.bifurcation_segments
    if not len(bifurcation_segments):
        raise ValueError("No bifurcation paths remain for the selected basin or extent.")
    stats = {
        "bifurcation_total": layers.bifurcation_source_count,
        "bifurcation_plotted": len(bifurcation_segments),
        "bifurcation_dateline": layers.bifurcation_dateline_skipped,
        "main_plotted": len(main_segments),
        "main_dateline": main_dateline,
        "invalid_grid_indices": invalid_grid_indices,
    }
    return main_segments, bifurcation_segments, stats


def _configure_map(ax, extent: tuple[float, float, float, float]) -> None:
    projection = ccrs.PlateCarree()
    if extent == (-180.0, 180.0, -90.0, 90.0):
        ax.set_global()
    else:
        ax.set_extent(extent, crs=projection)
    ax.add_feature(cfeature.LAND.with_scale("50m"), facecolor="#f3f1ec", zorder=0)
    ax.add_feature(cfeature.OCEAN.with_scale("50m"), facecolor="white", zorder=0)
    ax.add_feature(cfeature.LAKES.with_scale("50m"), facecolor="#e6f2f8", zorder=1)
    ax.add_feature(cfeature.BORDERS.with_scale("50m"), edgecolor="0.45", linewidth=0.4, zorder=4)
    ax.add_feature(cfeature.COASTLINE.with_scale("50m"), edgecolor="0.25", linewidth=0.6, zorder=4)
    gridlines = ax.gridlines(
        crs=projection, draw_labels=True, linewidth=0.4, color="0.55",
        alpha=0.55, linestyle="--", x_inline=False, y_inline=False,
    )
    gridlines.top_labels = False
    gridlines.right_labels = False
    gridlines.xlabel_style = {"size": 9}
    gridlines.ylabel_style = {"size": 9}
    gridlines.xlocator = MaxNLocator(7)
    gridlines.ylocator = MaxNLocator(6)


def _draw_basin_boundary(ax, basin: StandardBasinMask) -> None:
    longitude, latitude, mask = basin_boundary_arrays(basin)
    ax.contour(
        longitude, latitude, mask.astype(float), levels=[0.5], colors=["black"],
        linewidths=1.1, transform=ccrs.PlateCarree(), zorder=4.5,
    )


def plot_paths(
    main_segments: np.ndarray,
    bifurcation_segments: np.ndarray,
    output: Path,
    extent: tuple[float, float, float, float],
    figsize: tuple[float, float],
    dpi: int,
    minimum_uparea_km2: float,
    title: str,
    basin: StandardBasinMask | None,
) -> None:
    projection = ccrs.PlateCarree()
    fig = plt.figure(figsize=figsize)
    ax = fig.add_subplot(1, 1, 1, projection=projection)
    _configure_map(ax, extent)
    ax.add_collection(LineCollection(
        main_segments, colors="#8a9aa3", linewidths=0.35, alpha=0.65,
        transform=projection, zorder=2,
    ))
    ax.add_collection(LineCollection(
        bifurcation_segments, colors="#d73027", linewidths=0.6, alpha=0.9,
        transform=projection, zorder=3,
    ))
    if basin is not None:
        _draw_basin_boundary(ax, basin)
    ax.set_title(title, fontsize=14, pad=10)
    ax.legend(
        handles=[
            Line2D([0], [0], color="#8a9aa3", linewidth=1.2,
                   label=f"Main river (upstream area ≥ {minimum_uparea_km2:,.0f} km²)"),
            Line2D([0], [0], color="#d73027", linewidth=1.5,
                   label=f"Bifurcation paths ({len(bifurcation_segments):,})"),
        ],
        loc="lower left", fontsize=9, frameon=True,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def _validate_args(args: argparse.Namespace) -> None:
    if args.output is not None and args.extent is None:
        raise ValueError("--output is valid only together with --extent.")
    if args.min_uparea_km2 < 0:
        raise ValueError("--min-uparea-km2 must be non-negative.")
    if args.dpi <= 0 or any(value <= 0 for value in args.figsize):
        raise ValueError("--dpi and both --figsize values must be positive.")


def main() -> None:
    args = parse_args()
    _validate_args(args)
    data = load_data(args.routing_file)
    if args.extent is not None:
        extent = validate_extent(tuple(args.extent))
        main_segments, bifurcations, stats = select_paths(
            data, extent, args.min_uparea_km2, None
        )
        output = args.output or args.output_dir / "bifurcation_paths_custom.png"
        plot_paths(
            main_segments, bifurcations, output, extent, tuple(args.figsize), args.dpi,
            args.min_uparea_km2, "CoLM bifurcation paths", None,
        )
        print(
            f"Saved bifurcation map: {output} | main_segments={stats['main_plotted']:,}, "
            f"bifurcations={stats['bifurcation_plotted']:,}"
        )
        return

    regions = args.regions or list(STANDARD_BASINS)
    for key in regions:
        basin = load_standard_basin_mask(key)
        main_segments, bifurcations, stats = select_paths(
            data, basin.extent, args.min_uparea_km2, basin
        )
        output = args.output_dir / f"bifurcation_paths_{key}.png"
        plot_paths(
            main_segments, bifurcations, output, basin.extent, tuple(args.figsize), args.dpi,
            args.min_uparea_km2, f"CoLM bifurcation paths: {basin.display_name}", basin,
        )
        print(
            f"Saved bifurcation map: {output} | basin={key}, "
            f"main_segments={stats['main_plotted']:,}, "
            f"bifurcations={stats['bifurcation_plotted']:,}, "
            f"invalid_grid_indices={stats['invalid_grid_indices']:,}"
        )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from exc
