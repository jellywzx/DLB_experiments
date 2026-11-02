"""Shared loading and selection for CoLM levee and bifurcation map layers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from openbench_dams_rivers_common import (
    StandardBasinMask,
    basin_grid_membership,
    netcdf_attribute,
    netcdf_variable,
    open_netcdf,
    routing_destination_indices,
    routing_id_indices,
    routing_numeric_1d,
    routing_segment_indices,
    routing_segments,
    routing_sequence_lookup,
)


@dataclass(frozen=True)
class RoutingOverlaySource:
    longitude: np.ndarray
    latitude: np.ndarray
    x_index: np.ndarray
    y_index: np.ndarray
    destination: np.ndarray
    upstream_area: np.ndarray
    levee_fraction: np.ndarray | None
    levee_height: np.ndarray | None
    bifurcation_upstream: np.ndarray | None
    bifurcation_downstream: np.ndarray | None
    bifurcation_upstream_indices: np.ndarray | None
    bifurcation_downstream_indices: np.ndarray | None
    path: Path


@dataclass(frozen=True)
class RoutingOverlayLayers:
    levee_segments: np.ndarray
    levee_protected_percent: np.ndarray
    bifurcation_segments: np.ndarray
    levee_source_count: int
    bifurcation_source_count: int
    levee_dateline_skipped: int
    bifurcation_dateline_skipped: int


def _empty_segments() -> np.ndarray:
    return np.empty((0, 2, 2), dtype=float)


def _uparea_units(h5) -> str:
    units = netcdf_attribute(netcdf_variable(h5, "topo_uparea"), "units", b"")
    if isinstance(units, bytes):
        units = units.decode("utf-8", errors="replace")
    return str(units).strip().lower()


def load_routing_overlay_source(
    routing_file: Path,
    *,
    load_levee: bool,
    load_bifurcations: bool,
) -> RoutingOverlaySource:
    """Read routing data once for the requested overlay layers."""
    routing_file = Path(routing_file)
    if not routing_file.is_file():
        raise FileNotFoundError(f"Routing file not found: {routing_file}")
    if not load_levee and not load_bifurcations:
        raise ValueError("At least one routing overlay layer must be requested.")

    with open_netcdf(routing_file) as h5:
        sequence = routing_numeric_1d(h5, "seq")
        next_sequence = routing_numeric_1d(h5, "seq_next")
        longitude = routing_numeric_1d(h5, "seq_lon")
        latitude = routing_numeric_1d(h5, "seq_lat")
        x_index = routing_numeric_1d(h5, "seq_x")
        y_index = routing_numeric_1d(h5, "seq_y")
        upstream_area = routing_numeric_1d(h5, "topo_uparea")
        area_units = _uparea_units(h5)
        levee_fraction = (
            routing_numeric_1d(h5, "levee_frc") if load_levee else None
        )
        levee_height = (
            routing_numeric_1d(h5, "levee_hgt") if load_levee else None
        )
        bifurcation_upstream = (
            routing_numeric_1d(h5, "bifurcation_upst")
            if load_bifurcations
            else None
        )
        bifurcation_downstream = (
            routing_numeric_1d(h5, "bifurcation_down")
            if load_bifurcations
            else None
        )

    main_length = len(sequence)
    main_arrays = (
        next_sequence,
        longitude,
        latitude,
        x_index,
        y_index,
        upstream_area,
    )
    if any(len(values) != main_length for values in main_arrays):
        raise ValueError("Main-routing variables must have the same length.")
    if area_units not in {"m2", "m^2", "m²"}:
        raise ValueError(f"topo_uparea must use square metres; found units={area_units!r}.")
    if not np.isfinite(upstream_area).all() or np.any(upstream_area < 0):
        raise ValueError("topo_uparea must contain finite, non-negative values.")

    if levee_fraction is not None:
        if levee_height is None or len(levee_fraction) != main_length or len(levee_height) != main_length:
            raise ValueError("levee_frc and levee_hgt must have the same length as seq.")
        nonfinite = int((~np.isfinite(levee_fraction)).sum())
        outside = int(
            (
                np.isfinite(levee_fraction)
                & ((levee_fraction < 0) | (levee_fraction > 1))
            ).sum()
        )
        if nonfinite or outside:
            raise ValueError(
                "levee_frc must be finite and within [0, 1]; "
                f"nonfinite={nonfinite}, outside={outside}."
            )
        nonfinite_height = int((~np.isfinite(levee_height)).sum())
        negative_height = int((np.isfinite(levee_height) & (levee_height < 0)).sum())
        if nonfinite_height or negative_height:
            raise ValueError(
                "levee_hgt must be finite and non-negative; "
                f"nonfinite={nonfinite_height}, negative={negative_height}."
            )

    upstream_indices = None
    downstream_indices = None
    if bifurcation_upstream is not None and bifurcation_downstream is not None:
        if len(bifurcation_upstream) != len(bifurcation_downstream):
            raise ValueError(
                "Bifurcation upstream and downstream variables must have the same length."
            )
        sorted_sequence, sorted_indices = routing_sequence_lookup(sequence)
        upstream_indices = routing_id_indices(
            bifurcation_upstream, sorted_sequence, sorted_indices
        )
        downstream_indices = routing_id_indices(
            bifurcation_downstream, sorted_sequence, sorted_indices
        )

    return RoutingOverlaySource(
        longitude=longitude,
        latitude=latitude,
        x_index=x_index,
        y_index=y_index,
        destination=routing_destination_indices(sequence, next_sequence),
        upstream_area=upstream_area,
        levee_fraction=levee_fraction,
        levee_height=levee_height,
        bifurcation_upstream=bifurcation_upstream,
        bifurcation_downstream=bifurcation_downstream,
        bifurcation_upstream_indices=upstream_indices,
        bifurcation_downstream_indices=downstream_indices,
        path=routing_file,
    )


def standard_basin_membership(
    source: RoutingOverlaySource,
    basin: StandardBasinMask,
) -> tuple[np.ndarray, int]:
    """Return per-routing-unit membership in one standard grid-index basin mask."""
    return basin_grid_membership(source.x_index, source.y_index, basin)


def select_routing_overlay_layers(
    source: RoutingOverlaySource,
    extent: tuple[float, float, float, float],
    *,
    show_levee: bool,
    show_bifurcations: bool,
    route_inside: np.ndarray | None = None,
) -> RoutingOverlayLayers:
    """Select requested overlay segments inside an extent and optional basin mask."""
    if not show_levee and not show_bifurcations:
        raise ValueError("At least one routing overlay layer must be selected.")
    if route_inside is not None:
        route_inside = np.asarray(route_inside, dtype=bool)
        if route_inside.shape != source.longitude.shape:
            raise ValueError("Routing basin-membership mask has the wrong shape.")

    levee_segments = _empty_segments()
    levee_protected_percent = np.empty(0, dtype=float)
    levee_source_count = 0
    levee_dateline = 0
    safe_destination = np.maximum(source.destination, 0)
    if show_levee:
        if source.levee_fraction is None or source.levee_height is None:
            raise ValueError("Levee overlay data were not loaded.")
        levee_mask = (
            (source.levee_height > 0)
            & (source.levee_fraction >= 0)
            & (source.levee_fraction < 1)
        )
        levee_source_count = int(levee_mask.sum())
        if route_inside is not None:
            levee_mask &= route_inside & route_inside[safe_destination]
        levee_sources, levee_dateline = routing_segment_indices(
            source.longitude,
            source.latitude,
            source.destination,
            extent,
            levee_mask,
        )
        if levee_sources.size:
            levee_segments = routing_segments(
                source.longitude,
                source.latitude,
                levee_sources,
                source.destination,
            )
            levee_protected_percent = (
                1.0 - source.levee_fraction[levee_sources]
            ) * 100.0

    bifurcation_segments = _empty_segments()
    bifurcation_source_count = 0
    bifurcation_dateline = 0
    if show_bifurcations:
        upstream = source.bifurcation_upstream_indices
        downstream = source.bifurcation_downstream_indices
        if upstream is None or downstream is None:
            raise ValueError("Bifurcation overlay data were not loaded.")
        bifurcation_source_count = len(upstream)
        safe_upstream = np.maximum(upstream, 0)
        safe_downstream = np.maximum(downstream, 0)
        valid = (upstream >= 0) & (downstream >= 0) & (upstream != downstream)
        valid &= np.isfinite(source.longitude[safe_upstream])
        valid &= np.isfinite(source.latitude[safe_upstream])
        valid &= np.isfinite(source.longitude[safe_downstream])
        valid &= np.isfinite(source.latitude[safe_downstream])
        valid &= (source.longitude[safe_upstream] >= -180.0)
        valid &= (source.longitude[safe_upstream] <= 180.0)
        valid &= (source.latitude[safe_upstream] >= -90.0)
        valid &= (source.latitude[safe_upstream] <= 90.0)
        valid &= (source.longitude[safe_downstream] >= -180.0)
        valid &= (source.longitude[safe_downstream] <= 180.0)
        valid &= (source.latitude[safe_downstream] >= -90.0)
        valid &= (source.latitude[safe_downstream] <= 90.0)
        if route_inside is not None:
            valid &= route_inside[safe_upstream] & route_inside[safe_downstream]

        dateline_mask = valid & (
            np.abs(
                source.longitude[safe_upstream]
                - source.longitude[safe_downstream]
            )
            > 180.0
        )
        bifurcation_dateline = int(dateline_mask.sum())
        valid &= ~dateline_mask
        west, east, south, north = extent
        valid &= (source.longitude[safe_upstream] >= west)
        valid &= (source.longitude[safe_upstream] <= east)
        valid &= (source.latitude[safe_upstream] >= south)
        valid &= (source.latitude[safe_upstream] <= north)
        valid &= (source.longitude[safe_downstream] >= west)
        valid &= (source.longitude[safe_downstream] <= east)
        valid &= (source.latitude[safe_downstream] >= south)
        valid &= (source.latitude[safe_downstream] <= north)
        selected = np.flatnonzero(valid)
        if selected.size:
            bifurcation_segments = np.stack(
                (
                    np.column_stack(
                        (
                            source.longitude[upstream[selected]],
                            source.latitude[upstream[selected]],
                        )
                    ),
                    np.column_stack(
                        (
                            source.longitude[downstream[selected]],
                            source.latitude[downstream[selected]],
                        )
                    ),
                ),
                axis=1,
            )

    return RoutingOverlayLayers(
        levee_segments=levee_segments,
        levee_protected_percent=levee_protected_percent,
        bifurcation_segments=bifurcation_segments,
        levee_source_count=levee_source_count,
        bifurcation_source_count=bifurcation_source_count,
        levee_dateline_skipped=levee_dateline,
        bifurcation_dateline_skipped=bifurcation_dateline,
    )
