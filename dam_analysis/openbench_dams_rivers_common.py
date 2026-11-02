"""Shared configuration and helpers for OpenBench dam/river plotting scripts."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

try:
    import h5py
except ImportError:  # OpenBench's Python 3.13 environment uses netCDF4 instead.
    h5py = None
import numpy as np
import pandas as pd

OPENBENCH_OUTPUT_ROOT = Path("/share/home/dq134/wzx/openbench/openbench_1015/output")
OUTPUT_ROOT = Path("/share/home/dq134/wzx/openbench/postprocess/dam_analysis/dam_data")
DEFAULT_INPUT_SUFFIX = Path("comparisons/Diff_Plot")
DEFAULT_DAMS = OUTPUT_ROOT / "dams_global.csv"
DEFAULT_DAM_BUILT_BEFORE = None
FALLBACK_DAM_BUILT_BEFORE = 2005
DEFAULT_ROUTING_FILE = Path(
    "/share/home/dq013/zhwei/colm/data/CoLMruntime/unitcatchment/"
    "grid_routing_data_06min.nc"
)
BASIN_MASK_ROOT = Path(
    "/share/home/dq134/wzx/openbench/postprocess/basin_mask/outputs/basin_masks_06min"
)
STANDARD_BASINS = {
    "amazon": ("Amazon", BASIN_MASK_ROOT / "amazon/amazon_basin.nc"),
    "missouri": ("Missouri", BASIN_MASK_ROOT / "missouri/missouri_basin.nc"),
    "lower_mississippi": (
        "Lower Mississippi",
        BASIN_MASK_ROOT / "lower_mississippi/lower_mississippi_basin.nc",
    ),
    "mekong": ("Mekong", BASIN_MASK_ROOT / "mekong/mekong_basin.nc"),
}
EXTENT_PADDING_FRACTION = 0.02
MIN_EXTENT_PADDING_DEGREES = 0.25
RIVER_DETAIL_CONFIG = {
    "all": {"minimum_uparea_km2": None, "linewidth": 0.35},
    "fine": {"minimum_uparea_km2": 500.0, "linewidth": 0.45},
    "medium": {"minimum_uparea_km2": 5_000.0, "linewidth": 0.65},
    "coarse": {"minimum_uparea_km2": 50_000.0, "linewidth": 0.85},
    "none": {"minimum_uparea_km2": None, "linewidth": 0.0},
}
DEFAULT_RIVER_DETAIL = "fine"


@contextmanager
def open_netcdf(path: Path):
    """Open a NetCDF/HDF5 file with whichever supported backend is installed."""
    if h5py is not None:
        with h5py.File(path, "r") as dataset:
            yield dataset
        return
    from netCDF4 import Dataset
    with Dataset(path, "r") as dataset:
        dataset.set_auto_maskandscale(False)
        yield dataset


def netcdf_variable(dataset, name: str):
    variables = dataset.variables if hasattr(dataset, "variables") else dataset
    if name not in variables:
        raise ValueError(f"NetCDF file is missing required variable: {name}")
    return variables[name]


def netcdf_attribute(variable, name: str, default=None):
    if hasattr(variable, "ncattrs"):
        return variable.getncattr(name) if name in variable.ncattrs() else default
    return variable.attrs.get(name, default)


@dataclass(frozen=True)
class StandardBasinMask:
    key: str
    display_name: str
    mask: np.ndarray
    longitude: np.ndarray
    latitude: np.ndarray
    extent: tuple[float, float, float, float]
    path: Path


def load_standard_basin_mask(key: str) -> StandardBasinMask:
    """Load one standard 06-minute basin mask and derive its active extent."""
    key = key.strip().lower()
    if key not in STANDARD_BASINS:
        raise ValueError(
            f"Unknown standard basin {key!r}; choices: {', '.join(STANDARD_BASINS)}."
        )
    display_name, path = STANDARD_BASINS[key]
    if not path.is_file():
        raise FileNotFoundError(f"Basin mask file not found: {path}")

    with open_netcdf(path) as h5:
        for name in ("basin_mask", "lon", "lat"):
            netcdf_variable(h5, name)
        mask = np.asarray(netcdf_variable(h5, "basin_mask")[...]) > 0
        longitude = np.asarray(netcdf_variable(h5, "lon")[...], dtype=float).reshape(-1)
        latitude = np.asarray(netcdf_variable(h5, "lat")[...], dtype=float).reshape(-1)

    if mask.ndim != 2 or mask.shape != (len(latitude), len(longitude)):
        raise ValueError(
            f"Basin mask shape {mask.shape} does not match "
            f"lat/lon sizes ({len(latitude)}, {len(longitude)}): {path}"
        )
    if len(longitude) < 2 or len(latitude) < 2:
        raise ValueError(f"Basin mask coordinates must each contain at least two values: {path}")
    if not np.isfinite(longitude).all() or not np.isfinite(latitude).all():
        raise ValueError(f"Basin mask coordinates contain non-finite values: {path}")
    if not mask.any():
        raise ValueError(f"Basin mask contains no active cells: {path}")

    active_y, active_x = np.nonzero(mask)
    delta_lon = abs(float(np.median(np.diff(longitude))))
    delta_lat = abs(float(np.median(np.diff(latitude))))
    extent = (
        float(longitude[active_x].min()) - delta_lon / 2.0,
        float(longitude[active_x].max()) + delta_lon / 2.0,
        float(latitude[active_y].min()) - delta_lat / 2.0,
        float(latitude[active_y].max()) + delta_lat / 2.0,
    )
    return StandardBasinMask(
        key=key,
        display_name=display_name,
        mask=mask,
        longitude=longitude,
        latitude=latitude,
        extent=validate_extent(extent),
        path=path,
    )


def basin_grid_membership(
    x_index: np.ndarray,
    y_index: np.ndarray,
    basin: StandardBasinMask,
) -> tuple[np.ndarray, int]:
    """Test one-based routing-grid indices against a standard basin mask."""
    x_values = np.asarray(x_index, dtype=float)
    y_values = np.asarray(y_index, dtype=float)
    if x_values.shape != y_values.shape:
        raise ValueError("Routing x/y index arrays must have the same shape.")
    valid = np.isfinite(x_values) & np.isfinite(y_values)
    valid &= (x_values == np.floor(x_values)) & (y_values == np.floor(y_values))
    valid &= (x_values >= 1) & (x_values <= basin.mask.shape[1])
    valid &= (y_values >= 1) & (y_values <= basin.mask.shape[0])
    inside = np.zeros(x_values.shape, dtype=bool)
    if valid.any():
        x_zero = x_values[valid].astype(np.int64) - 1
        y_zero = y_values[valid].astype(np.int64) - 1
        inside[valid] = basin.mask[y_zero, x_zero]
    return inside, int((~valid).sum())


def basin_boundary_arrays(
    basin: StandardBasinMask,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return a tightly cropped lon/lat/mask grid for drawing the basin boundary."""
    active_y, active_x = np.nonzero(basin.mask)
    y0 = max(0, int(active_y.min()) - 1)
    y1 = min(basin.mask.shape[0], int(active_y.max()) + 2)
    x0 = max(0, int(active_x.min()) - 1)
    x1 = min(basin.mask.shape[1], int(active_x.max()) + 2)
    return basin.longitude[x0:x1], basin.latitude[y0:y1], basin.mask[y0:y1, x0:x1]


def dam_built_before_from_manifest(input_path: Path) -> tuple[int | None, Path | None]:
    """Return the evaluation end year and manifest found above an input path."""
    input_path = Path(input_path)
    start = input_path if input_path.is_dir() else input_path.parent
    manifest_path = next(
        (
            directory / "run_manifest.json"
            for directory in (start, *start.parents)
            if (directory / "run_manifest.json").is_file()
        ),
        None,
    )
    if manifest_path is None:
        return None, None

    with manifest_path.open("r", encoding="utf-8") as stream:
        manifest = json.load(stream)
    years = (
        manifest.get("canonical_config", {})
        .get("project", {})
        .get("years")
    )
    if not isinstance(years, list) or not years:
        raise ValueError(
            f"run_manifest.json must contain a non-empty "
            f"canonical_config.project.years list: {manifest_path}"
        )
    try:
        end_year = int(years[-1])
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid evaluation end year {years[-1]!r} in {manifest_path}"
        ) from exc
    if not 1800 <= end_year <= 2030:
        raise ValueError(
            f"Evaluation end year must be between 1800 and 2030; "
            f"found {end_year} in {manifest_path}"
        )
    return end_year, manifest_path


def routing_numeric_1d(h5, name: str) -> np.ndarray:
    """Read and decode one numeric one-dimensional routing variable."""
    dataset = netcdf_variable(h5, name)
    values = np.asarray(dataset[...]).reshape(-1)
    if values.dtype.kind not in "iuf":
        raise ValueError(f"Routing variable {name} must be numeric.")
    values = values.astype(float, copy=False)
    for attribute in ("_FillValue", "missing_value"):
        missing_value = netcdf_attribute(dataset, attribute)
        if missing_value is not None:
            missing = float(np.asarray(missing_value).reshape(-1)[0])
            values[values == missing] = np.nan
    scale_factor = netcdf_attribute(dataset, "scale_factor")
    add_offset = netcdf_attribute(dataset, "add_offset")
    if scale_factor is not None:
        values *= float(np.asarray(scale_factor).reshape(-1)[0])
    if add_offset is not None:
        values += float(np.asarray(add_offset).reshape(-1)[0])
    return values


def routing_sequence_lookup(sequence: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return sorted sequence IDs and their original array indices."""
    sequence = np.asarray(sequence)
    valid = np.isfinite(sequence) & (sequence > 0) & (sequence == np.floor(sequence))
    if not valid.all():
        raise ValueError("Routing variable seq must contain positive integer IDs only.")
    sequence_ids = sequence.astype(np.int64)
    order = np.argsort(sequence_ids)
    sorted_sequence = sequence_ids[order]
    if np.any(np.diff(sorted_sequence) == 0):
        raise ValueError("Routing variable seq contains duplicate sequence IDs.")
    return sorted_sequence, order


def routing_id_indices(
    values: np.ndarray,
    sorted_sequence: np.ndarray,
    sorted_indices: np.ndarray,
) -> np.ndarray:
    """Map positive integer sequence IDs to array indices; use -1 when absent."""
    values = np.asarray(values)
    result = np.full(values.shape, -1, dtype=np.int64)
    valid = np.isfinite(values) & (values > 0) & (values == np.floor(values))
    if not valid.any():
        return result

    value_ids = values[valid].astype(np.int64)
    positions = np.searchsorted(sorted_sequence, value_ids)
    in_range = positions < len(sorted_sequence)
    matched = np.zeros(len(value_ids), dtype=bool)
    matched[in_range] = sorted_sequence[positions[in_range]] == value_ids[in_range]
    mapped = np.full(len(value_ids), -1, dtype=np.int64)
    mapped[matched] = sorted_indices[positions[matched]]
    result[valid] = mapped
    return result


def routing_destination_indices(sequence: np.ndarray, next_sequence: np.ndarray) -> np.ndarray:
    """Map each routing element's downstream sequence ID to an array index."""
    if len(sequence) != len(next_sequence):
        raise ValueError("Routing variables seq and seq_next must have the same length.")
    sorted_sequence, sorted_indices = routing_sequence_lookup(sequence)
    return routing_id_indices(next_sequence, sorted_sequence, sorted_indices)


def routing_segment_indices(
    longitude: np.ndarray,
    latitude: np.ndarray,
    destination: np.ndarray,
    extent: tuple[float, float, float, float],
    source_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, int]:
    """Select plottable source indices and count dateline-crossing segments skipped."""
    longitude = np.asarray(longitude, dtype=float)
    latitude = np.asarray(latitude, dtype=float)
    destination = np.asarray(destination, dtype=np.int64)
    lengths = {len(longitude), len(latitude), len(destination)}
    if len(lengths) != 1:
        raise ValueError("Routing coordinates and destination indices must have the same length.")

    source = np.arange(len(destination), dtype=np.int64)
    valid = destination >= 0
    valid &= destination != source
    if source_mask is not None:
        source_mask = np.asarray(source_mask, dtype=bool)
        if len(source_mask) != len(destination):
            raise ValueError("Routing segment source mask has the wrong length.")
        valid &= source_mask

    safe_destination = np.maximum(destination, 0)
    valid &= np.isfinite(longitude) & np.isfinite(latitude)
    valid &= np.isfinite(longitude[safe_destination]) & np.isfinite(latitude[safe_destination])
    valid &= (longitude >= -180.0) & (longitude <= 180.0)
    valid &= (latitude >= -90.0) & (latitude <= 90.0)
    valid &= (longitude[safe_destination] >= -180.0) & (longitude[safe_destination] <= 180.0)
    valid &= (latitude[safe_destination] >= -90.0) & (latitude[safe_destination] <= 90.0)

    dateline = valid & (np.abs(longitude - longitude[safe_destination]) > 180.0)
    skipped_dateline = int(dateline.sum())
    valid &= ~dateline

    west, east, south, north = extent
    valid &= (longitude >= west) & (longitude <= east)
    valid &= (latitude >= south) & (latitude <= north)
    valid &= (longitude[safe_destination] >= west) & (longitude[safe_destination] <= east)
    valid &= (latitude[safe_destination] >= south) & (latitude[safe_destination] <= north)
    return np.flatnonzero(valid), skipped_dateline


def routing_segments(
    longitude: np.ndarray,
    latitude: np.ndarray,
    source_indices: np.ndarray,
    destination: np.ndarray,
) -> np.ndarray:
    """Build LineCollection-compatible endpoint pairs for routing segments."""
    source_indices = np.asarray(source_indices, dtype=np.int64)
    destination_indices = np.asarray(destination, dtype=np.int64)[source_indices]
    return np.stack(
        (
            np.column_stack((longitude[source_indices], latitude[source_indices])),
            np.column_stack((longitude[destination_indices], latitude[destination_indices])),
        ),
        axis=1,
    )


def region_name_from_evaluation(evaluation_name: str) -> str:
    """Return the display-region name encoded in an OpenBench evaluation name."""
    normalized = evaluation_name.strip().lower()
    if normalized.startswith("lowermississippi_"):
        return "Lower Mississippi"
    if normalized.startswith("missouri_"):
        return "Missouri"
    if normalized.startswith("amazon_"):
        return "Amazon"
    if normalized.startswith("mekong_"):
        return "Mekong"
    if normalized.startswith("sa_") or normalized.startswith("streamflow_sa_"):
        return "SA"

    raw_region = re.split(r"_(?:inundation|streamflow)_", evaluation_name, maxsplit=1)[0]
    raw_region = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw_region)
    return raw_region.replace("_", " ").strip() or evaluation_name


def openbench_input_dir(evaluation_name: str, input_suffix: Path) -> Path:
    """Join an evaluation and configurable input suffix under the OpenBench root."""
    evaluation_name = evaluation_name.strip()
    if not evaluation_name:
        raise ValueError("The evaluation name must not be empty.")
    input_suffix = Path(input_suffix)
    if input_suffix.is_absolute() or ".." in input_suffix.parts:
        raise ValueError("The input suffix must be a relative path below the evaluation directory.")
    return OPENBENCH_OUTPUT_ROOT / evaluation_name / input_suffix


def infer_region_name(path: Path, configured_region: str | None) -> str | None:
    """Return an explicit region or infer it from an OpenBench output path."""
    if configured_region is not None and configured_region.strip():
        return configured_region.strip()
    for parent in path.parents:
        if parent.parent.name == "output":
            return region_name_from_evaluation(parent.name)
    return None


def validate_extent(
    extent: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    west, east, south, north = extent
    if not np.isfinite(extent).all():
        raise ValueError("--extent values must all be finite.")
    if west >= east or south >= north:
        raise ValueError("--extent requires WEST < EAST and SOUTH < NORTH.")
    if west < -180 or east > 180 or south < -90 or north > 90:
        raise ValueError("--extent must stay within longitude [-180, 180] and latitude [-90, 90].")
    return extent


def filter_dams_by_year(
    dams: pd.DataFrame,
    built_before: int | None,
    built_by: int | None,
) -> tuple[pd.DataFrame, str | None]:
    if built_before is None and built_by is None:
        return dams, None

    valid_year = dams["year"].between(1800, 2030)
    if built_before is not None:
        filtered = dams.loc[valid_year & (dams["year"] < built_before)].copy()
        label = f"dam construction year < {built_before}"
    else:
        filtered = dams.loc[valid_year & (dams["year"] <= built_by)].copy()
        label = f"dam construction year ≤ {built_by}"
    if filtered.empty:
        raise ValueError(f"No dams remain after applying: {label}.")
    return filtered, label


def filter_dams_to_extent(
    dams: pd.DataFrame,
    extent: tuple[float, float, float, float],
) -> pd.DataFrame:
    west, east, south, north = extent
    visible = dams["lon"].between(west, east) & dams["lat"].between(south, north)
    return dams.loc[visible].copy()


def dam_marker_sizes(storage: pd.Series) -> np.ndarray:
    values = pd.to_numeric(storage, errors="coerce").fillna(0).clip(lower=0).to_numpy(float)
    scaled = np.log10(values + 1.0)
    upper = np.nanmax(scaled)
    if not np.isfinite(upper) or upper == 0:
        return np.full(values.shape, 35.0)
    return 28.0 + 115.0 * scaled / upper


def storage_marker_size(storage_mcm: float, maximum_storage_mcm: float) -> float:
    maximum = np.log10(maximum_storage_mcm + 1.0)
    if maximum <= 0:
        return 35.0
    return 28.0 + 115.0 * np.log10(storage_mcm + 1.0) / maximum
