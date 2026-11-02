#!/usr/bin/env python3
"""Plot CoLM levee-protected floodplain fraction for basins or a custom extent."""

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
from matplotlib.colors import Normalize
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
        description="Plot CoLM levee-protected floodplain fraction for standard basins."
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
        help="Minimum upstream area for the gray river background (default: 500).",
    )
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument(
        "--figsize", type=float, nargs=2, metavar=("WIDTH", "HEIGHT"), default=(10.0, 7.0)
    )
    return parser.parse_args()


def load_data(routing_file: Path) -> RoutingOverlaySource:
    return load_routing_overlay_source(
        routing_file, load_levee=True, load_bifurcations=False
    )


def select_levee_segments(
    data: RoutingOverlaySource,
    extent: tuple[float, float, float, float],
    minimum_uparea_km2: float,
    basin: StandardBasinMask | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, float | int]]:
    route_inside = None
    invalid_grid_indices = 0
    if basin is not None:
        route_inside, invalid_grid_indices = standard_basin_membership(data, basin)
    safe_destination = np.maximum(data.destination, 0)

    background_mask = data.upstream_area >= minimum_uparea_km2 * 1_000_000.0
    if route_inside is not None:
        background_mask &= route_inside & route_inside[safe_destination]
    background_sources, background_dateline = routing_segment_indices(
        data.longitude, data.latitude, data.destination, extent, background_mask
    )
    if background_sources.size == 0:
        raise ValueError("No background river segments remain for the selected basin or extent.")
    background_segments = routing_segments(
        data.longitude, data.latitude, background_sources, data.destination
    )

    layers = select_routing_overlay_layers(
        data, extent, show_levee=True, show_bifurcations=False,
        route_inside=route_inside,
    )
    levee_segments = layers.levee_segments
    coverage_percent = layers.levee_protected_percent
    if not len(levee_segments):
        raise ValueError("No valid levee segments remain for the selected basin or extent.")
    stats: dict[str, float | int] = {
        "valid_total": layers.levee_source_count,
        "valid_plotted": len(levee_segments),
        "background_plotted": len(background_segments),
        "background_dateline": background_dateline,
        "levee_dateline": layers.levee_dateline_skipped,
        "invalid_grid_indices": invalid_grid_indices,
        "minimum": float(coverage_percent.min()),
        "median": float(np.median(coverage_percent)),
        "mean": float(coverage_percent.mean()),
        "maximum": float(coverage_percent.max()),
    }
    return background_segments, levee_segments, coverage_percent, stats


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


def plot_levee_coverage(
    background_segments: np.ndarray,
    levee_segments: np.ndarray,
    coverage_percent: np.ndarray,
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
        background_segments, colors="#b8b8b8", linewidths=0.35, alpha=0.55,
        transform=projection, zorder=2,
    ))
    levee_lines = LineCollection(
        levee_segments, cmap="viridis", norm=Normalize(vmin=0.0, vmax=100.0),
        linewidths=0.85, alpha=0.95, transform=projection, zorder=3,
    )
    levee_lines.set_array(coverage_percent)
    ax.add_collection(levee_lines)
    if basin is not None:
        _draw_basin_boundary(ax, basin)
    colorbar = fig.colorbar(
        levee_lines, ax=ax, orientation="horizontal", pad=0.06, fraction=0.05
    )
    colorbar.set_label("Protected floodplain fraction (%)", fontsize=11)
    ax.set_title(title, fontsize=14, pad=10)
    ax.legend(
        handles=[
            Line2D([0], [0], color="#b8b8b8", linewidth=1.2,
                   label=f"River background (upstream area ≥ {minimum_uparea_km2:,.0f} km²)"),
            Line2D([0], [0], color="#2a788e", linewidth=1.8,
                   label=f"Levee-protected floodplain ({len(levee_segments):,} segments)"),
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
        background, levees, coverage, stats = select_levee_segments(
            data, extent, args.min_uparea_km2, None
        )
        output = args.output or args.output_dir / "levee_coverage_custom.png"
        plot_levee_coverage(
            background, levees, coverage, output, extent, tuple(args.figsize), args.dpi,
            args.min_uparea_km2, "CoLM levee-protected floodplain fraction", None,
        )
        print(
            f"Saved levee map: {output} | valid_segments={stats['valid_plotted']:,}, "
            f"protected_fraction_mean={stats['mean']:.3f}%"
        )
        return

    regions = args.regions or list(STANDARD_BASINS)
    for key in regions:
        basin = load_standard_basin_mask(key)
        background, levees, coverage, stats = select_levee_segments(
            data, basin.extent, args.min_uparea_km2, basin
        )
        output = args.output_dir / f"levee_coverage_{key}.png"
        plot_levee_coverage(
            background, levees, coverage, output, basin.extent, tuple(args.figsize), args.dpi,
            args.min_uparea_km2,
            f"CoLM levee-protected floodplain fraction: {basin.display_name}", basin,
        )
        print(
            f"Saved levee map: {output} | basin={key}, "
            f"valid_segments={stats['valid_plotted']:,}, "
            f"protected_fraction_mean={stats['mean']:.3f}%, "
            f"invalid_grid_indices={stats['invalid_grid_indices']:,}"
        )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from exc
