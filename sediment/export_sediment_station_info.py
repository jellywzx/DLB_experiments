#!/usr/bin/env python3
"""Export the stations actually used by OpenBench sediment evaluations.

The evaluation CSV is the source of truth for the selected stations.  The
reference NetCDF is opened only to read one-dimensional station metadata; no
time-series variable is loaded.

Maps use the CoLM routing network, following dam_analysis. Use --routing-file
to select a routing NetCDF and --river-detail to control the upstream-area
threshold (default: fine, >=500 km2). Routing-file access is disabled only when
both --river-detail and --station-trace are none.

使用示例（在终端执行）
======================
运行前需完成对应的 OpenBench 评估；本脚本读取已有评估结果，不运行评估。
使用安装了 numpy、pandas、xarray、matplotlib、cartopy，以及 h5py 或 netCDF4
的 Python 环境，并确保可以导入与评估配置匹配的 openbench 包。

以下为本机环境示例；若使用其他 OpenBench 版本，请相应调整 PYTHONPATH：

    cd /share/home/dq134/wzx/DLB_experiments/sediment
    conda activate openbench
    export PYTHONPATH=/share/home/dq134/wzx/openbench/openbench_1102/src

下面的 /path/to/config.yaml、目录和源名称均为占位符，请替换成实际值。
--config 应指向生成已有评估结果时使用的 OpenBench YAML 配置。

1. 默认导出三个泥沙相关变量的站点 CSV 和 PNG 地图：

    python export_sediment_station_info.py --config /path/to/config.yaml

   默认河网为 CoLM 06 分钟河网，river-detail=fine（上游汇水面积 >=500 km²）。
   默认输出目录为 <case>/data/station_info，PNG 分辨率为 300 dpi。

2. 调整河网疏密，或关闭河网：

    python export_sediment_station_info.py --config /path/to/config.yaml --river-detail all
    python export_sediment_station_info.py --config /path/to/config.yaml --river-detail medium
    python export_sediment_station_info.py --config /path/to/config.yaml --river-detail coarse
    python export_sediment_station_info.py --config /path/to/config.yaml --river-detail none

   all：不设面积阈值；fine：>=500 km²；medium：>=5,000 km²；
   coarse：>=50,000 km²；none：不绘制背景河网。未开启站点追踪时不读取路由文件。

3. 指定其他 CoLM 路由文件，并同时输出 PNG 和 PDF：

    python export_sediment_station_info.py --config /path/to/config.yaml --routing-file /path/to/routing.nc --format both

   默认路由文件为：
   /share/home/dq013/zhwei/colm/data/CoLMruntime/unitcatchment/grid_routing_data_06min.nc
   自定义文件需含 seq、seq_next、seq_lon、seq_lat、topo_uparea，面积单位为 m²。

4. 仅导出指定变量、参考源和模拟方案，并调整输出目录及标注字号：

    python export_sediment_station_info.py --config /path/to/config.yaml --variable Suspended_Sediment_Load --reference REF_NAME --simulation SIM_NAME --output-dir /path/to/station_info --label-fontsize 8

   --variable 可重复指定；不指定时处理 Discharge_For_Sediment、
   Suspended_Sediment_Concentration、Suspended_Sediment_Load。
   REF_NAME 和 SIM_NAME 请使用配置中实际的参考源名称及模拟标签。

5. 评估结果不在配置推导的默认位置时，指定实际 case 目录：

    python export_sediment_station_info.py --config /path/to/config.yaml --case-dir /path/to/evaluation_case

   --case-dir 应为包含 metrics/、scores/ 或最终站点清单的评估目录。

6. 追踪全部评估站点，或仅追踪指定站点的所有上下游河段：

    python export_sediment_station_info.py --config /path/to/config.yaml --station-trace all
    python export_sediment_station_info.py --config /path/to/config.yaml --station-trace selected --highlight-station STATION_A --highlight-station STATION_B

   STATION_A/B 使用导出 CSV 中的 station_id；指定 ID 必须存在于当前评估组。
   可用 --variable、--reference、--simulation 限定需要处理的评估组。
   站点先按当前绘图坐标精确匹配路由节点，否则匹配距离内的最近节点：

    python export_sediment_station_info.py --config /path/to/config.yaml --station-trace all --station-match-max-km 5 --format both

   默认最大匹配距离为 10 km，超距、无效坐标或等距歧义时报错。
   追踪包含文件中记录的分汊，且不受 river-detail 面积阈值限制。
   上游为橙色、下游为紫色，同时属于上下游的河段为绿色；选定站点加黑圈。
   保留原范围图，并额外输出 __station_map__full_trace.png/pdf 完整追踪图。
   --station-trace 默认 none；--river-detail none 可隐藏背景而保留高亮。
   此处追踪表示拓扑连通范围，不计算泥沙影响强度、传播时间或衰减。
   追踪匹配还需要 scipy；日志记录站点匹配 seq、距离及上下游河段数。

7. 针对每个站点单独出图：

    python export_sediment_station_info.py --config /path/to/config.yaml --per-station
    python export_sediment_station_info.py --config /path/to/config.yaml --per-station --station-trace all --format both
    python export_sediment_station_info.py --config /path/to/config.yaml --per-station --station-trace selected --highlight-station STATION_A --highlight-station STATION_B

   --per-station 将合并站点地图替换为逐站地图，每张图只标注当前站点。
   未追踪或 station-trace=all 时处理全部评估站点；selected 时仅处理指定站点。
   开启追踪时，各站独立追踪上下游，并分别输出原范围图和 __full_trace 图。
   配置中的经纬度范围仍用于原范围图；未配置时按当前站点自动计算范围。
   文件名增加 __station__<安全编码的站点ID>，重复 ID 附加行号避免覆盖。
   站点 CSV 仍为完整评估站点表，仅导出一份，不增加逐站 CSV。

查看全部参数：

    python export_sediment_station_info.py --help
"""

from __future__ import annotations

import argparse
import logging
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from openbench.util.filenames import join_filename_components
from openbench.util.station_ids import read_station_csv, station_id_key

LOG = logging.getLogger("export_sediment_station_info")
DEFAULT_ROUTING_FILE = Path(
    "/share/home/dq013/zhwei/colm/data/CoLMruntime/unitcatchment/"
    "grid_routing_data_06min.nc"
)
RIVER_DETAIL_CONFIG = {
    "all": (None, 0.35),
    "fine": (500.0, 0.45),
    "medium": (5_000.0, 0.65),
    "coarse": (50_000.0, 0.85),
    "none": (None, 0.0),
}
SEDIMENT_VARIABLES = (
    "Discharge_For_Sediment",
    "Suspended_Sediment_Concentration",
    "Suspended_Sediment_Load",
)
_SOURCE_SUFFIX = {
    "Discharge_For_Sediment": "Streamflow",
    "Suspended_Sediment_Concentration": "Suspended_Sediment_Concentration",
    "Suspended_Sediment_Load": "Suspended_Sediment_Load",
}


def _first_column(frame: pd.DataFrame, names: Iterable[str]) -> str | None:
    lookup = {str(column).casefold(): str(column) for column in frame.columns}
    return next((lookup[name.casefold()] for name in names if name.casefold() in lookup), None)


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        frame.to_csv(temporary, index=False)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _candidate_result_names(variable: str, reference: str, simulation: str) -> list[str]:
    legacy = f"{variable}_stn_{reference}_{simulation}_evaluations.csv"
    safe = f"{join_filename_components(variable, 'stn', reference, simulation, 'evaluations')}.csv"
    return list(dict.fromkeys((legacy, safe)))


def find_evaluated_station_file(case_dir: Path, variable: str, reference: str, simulation: str) -> Path:
    """Find successful station results, then fall back to the final station list."""
    names = _candidate_result_names(variable, reference, simulation)
    for category in ("metrics", "scores"):
        for name in names:
            path = case_dir / category / name
            if path.is_file():
                return path

    exact_lists = (
        case_dir / f"stn_{reference}_{simulation}_list.csv",
        case_dir / f"stn_{reference}_{simulation}_list.txt",
    )
    for path in exact_lists:
        if path.is_file():
            return path
    raise FileNotFoundError(
        f"No evaluated station file for {variable} / {reference} / {simulation} under {case_dir}. "
        "Run the OpenBench evaluation first."
    )


def _station_dimension(ds, station_id_var: str) -> tuple[str, str]:
    lookup = {str(name).casefold(): str(name) for name in ds.variables}
    station_key = lookup.get(station_id_var.casefold()) or lookup.get("station")
    if station_key is None:
        raise ValueError(f"Reference dataset has no station identifier variable {station_id_var!r}")
    variable = ds[station_key]
    if variable.ndim != 1:
        raise ValueError(f"Station identifier {station_key!r} must be one-dimensional")
    return station_key, str(variable.dims[0])


def _one_dimensional_values(ds, station_dim: str, names: Iterable[str], size: int, default=None):
    lookup = {str(name).casefold(): str(name) for name in ds.variables}
    for name in names:
        key = lookup.get(name.casefold())
        if key is not None and ds[key].dims == (station_dim,):
            return np.asarray(ds[key].values)
    return np.full(size, default, dtype=object)


def read_reference_station_metadata(
    dataset_path: Path,
    *,
    variable: str,
    grid_resolution: float | None,
    station_id_var: str = "station",
) -> pd.DataFrame:
    """Read station-only metadata without touching any time-series variable."""
    import xarray as xr

    with xr.open_dataset(dataset_path, decode_times=False, cache=False) as ds:
        station_key, station_dim = _station_dimension(ds, station_id_var)
        station_ids = np.asarray(ds[station_key].values)
        size = len(station_ids)

        resolution_suffix = None
        if grid_resolution is not None:
            try:
                from openbench.data.station_matcher import get_resolution_suffix

                resolution_suffix = get_resolution_suffix(grid_resolution)
            except (TypeError, ValueError):
                LOG.warning("No CaMA metadata suffix for grid resolution %r", grid_resolution)

        source_suffix = _SOURCE_SUFFIX.get(variable, variable)
        source_names = (f"source_dataset_{source_suffix}", "source_dataset")
        cama_lon_names = (f"cama_lon_{resolution_suffix}",) if resolution_suffix else ()
        cama_lat_names = (f"cama_lat_{resolution_suffix}",) if resolution_suffix else ()
        cama_area_names = (f"cama_uparea_{resolution_suffix}",) if resolution_suffix else ()
        alloc_error_names = (f"cama_alloc_err_{resolution_suffix}",) if resolution_suffix else ()

        frame = pd.DataFrame(
            {
                "station_id": [str(value) for value in station_ids],
                "station_name": _one_dimensional_values(ds, station_dim, ("station_name",), size, ""),
                "river": _one_dimensional_values(ds, station_dim, ("river",), size, ""),
                "country": _one_dimensional_values(ds, station_dim, ("country",), size, ""),
                "country_code": _one_dimensional_values(ds, station_dim, ("country_code",), size, ""),
                "data_source": _one_dimensional_values(ds, station_dim, source_names, size, ""),
                "source_station_id": _one_dimensional_values(
                    ds, station_dim, ("source_station_id", "source_station_uid"), size, ""
                ),
                "original_lon": _one_dimensional_values(ds, station_dim, ("lon", "longitude"), size, np.nan),
                "original_lat": _one_dimensional_values(ds, station_dim, ("lat", "latitude"), size, np.nan),
                "matched_lon": _one_dimensional_values(ds, station_dim, cama_lon_names, size, np.nan),
                "matched_lat": _one_dimensional_values(ds, station_dim, cama_lat_names, size, np.nan),
                "upstream_area_km2": _one_dimensional_values(
                    ds, station_dim, ("upstream_area", "area"), size, np.nan
                ),
                "matched_upstream_area_km2": _one_dimensional_values(
                    ds, station_dim, cama_area_names, size, np.nan
                ),
                "allocation_error": _one_dimensional_values(ds, station_dim, alloc_error_names, size, np.nan),
            }
        )
    return frame


def _numeric_column(frame: pd.DataFrame, names: Iterable[str]) -> np.ndarray:
    column = _first_column(frame, names)
    if column is None:
        return np.full(len(frame), np.nan)
    return pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)


def merge_evaluated_with_metadata(evaluated: pd.DataFrame, metadata: pd.DataFrame) -> pd.DataFrame:
    """Attach metadata with unambiguous ID matching and coordinate fallback."""
    id_column = _first_column(evaluated, ("ID", "station_id", "site_id", "site"))
    if id_column is None:
        raise ValueError("Evaluated station table has no ID/station_id column")

    evaluated = evaluated.copy()
    evaluated_ids = evaluated[id_column].fillna("").astype(str)
    metadata = metadata.copy()
    metadata["_key"] = metadata["station_id"].map(station_id_key)
    key_to_indices: dict[str, list[int]] = {}
    for index, key in enumerate(metadata["_key"]):
        key_to_indices.setdefault(key, []).append(index)

    eval_lon = _numeric_column(evaluated, ("ref_lon", "matched_lon", "lon", "longitude"))
    eval_lat = _numeric_column(evaluated, ("ref_lat", "matched_lat", "lat", "latitude"))
    meta_lon = pd.to_numeric(metadata["matched_lon"], errors="coerce").to_numpy(dtype=float)
    meta_lat = pd.to_numeric(metadata["matched_lat"], errors="coerce").to_numpy(dtype=float)
    raw_lon = pd.to_numeric(metadata["original_lon"], errors="coerce").to_numpy(dtype=float)
    raw_lat = pd.to_numeric(metadata["original_lat"], errors="coerce").to_numpy(dtype=float)

    selected: list[int | None] = []
    statuses: list[str] = []
    for row_index, station_id in enumerate(evaluated_ids):
        key = station_id_key(station_id)
        matches = key_to_indices.get(key, [])
        if len(matches) == 1:
            selected.append(matches[0])
            statuses.append("id")
            continue

        # Source-qualified duplicate IDs are written as SOURCE_<id>.
        suffix_key = station_id_key(station_id.split("_", 1)[-1]) if "_" in station_id else ""
        suffix_matches = key_to_indices.get(suffix_key, []) if suffix_key else []
        candidates = matches or suffix_matches or list(range(len(metadata)))
        lon, lat = eval_lon[row_index], eval_lat[row_index]
        if np.isfinite(lon) and np.isfinite(lat):
            candidate_array = np.asarray(candidates, dtype=int)
            distances = np.minimum(
                np.hypot(meta_lon[candidate_array] - lon, meta_lat[candidate_array] - lat),
                np.hypot(raw_lon[candidate_array] - lon, raw_lat[candidate_array] - lat),
            )
            finite = np.isfinite(distances)
            if finite.any():
                best_position = int(np.nanargmin(distances))
                best_distance = float(distances[best_position])
                tied = int(np.count_nonzero(np.isclose(distances, best_distance, atol=1e-6, rtol=0)))
                if best_distance <= 1e-4 and tied == 1:
                    selected.append(int(candidate_array[best_position]))
                    statuses.append("qualified_id_and_coordinates" if suffix_matches else "coordinates")
                    continue
        selected.append(None)
        statuses.append("ambiguous" if matches or suffix_matches else "not_found")

    public_columns = [column for column in metadata.columns if column != "_key"]
    attached_rows = []
    for index in selected:
        attached_rows.append(
            {column: metadata.iloc[index][column] for column in public_columns} if index is not None else {}
        )
    attached = pd.DataFrame(attached_rows, columns=public_columns)

    result = attached.copy()
    result["station_id"] = evaluated_ids.to_numpy()
    result["matched_lon"] = np.where(np.isfinite(eval_lon), eval_lon, result["matched_lon"])
    result["matched_lat"] = np.where(np.isfinite(eval_lat), eval_lat, result["matched_lat"])
    start_column = _first_column(evaluated, ("use_syear", "start_year", "syear"))
    end_column = _first_column(evaluated, ("use_eyear", "end_year", "eyear"))
    result["use_start_year"] = evaluated[start_column].to_numpy() if start_column else np.nan
    result["use_end_year"] = evaluated[end_column].to_numpy() if end_column else np.nan
    status_column = _first_column(evaluated, ("status",))
    result["evaluation_status"] = evaluated[status_column].to_numpy() if status_column else "ok"
    result["metadata_match_status"] = statuses
    return result


@contextmanager
def _open_routing_file(path: Path):
    """Use the same HDF5/NetCDF backends as the dam analysis scripts."""
    try:
        import h5py
    except ImportError:
        from netCDF4 import Dataset

        with Dataset(path, "r") as dataset:
            dataset.set_auto_maskandscale(False)
            yield dataset
    else:
        with h5py.File(path, "r") as dataset:
            yield dataset


def _routing_variable(dataset, name: str):
    variables = dataset.variables if hasattr(dataset, "variables") else dataset
    if name not in variables:
        raise ValueError(f"Routing file is missing required variable: {name}")
    return variables[name]


def _routing_attribute(variable, name: str, default=None):
    if hasattr(variable, "ncattrs"):
        return variable.getncattr(name) if name in variable.ncattrs() else default
    return variable.attrs.get(name, default)


def _routing_values(dataset, name: str) -> np.ndarray:
    variable = _routing_variable(dataset, name)
    values = np.asarray(variable[...])
    if values.ndim != 1 or values.dtype.kind not in "iuf":
        raise ValueError(f"Routing variable {name} must be numeric and one-dimensional")
    values = values.astype(float)
    for attribute in ("_FillValue", "missing_value"):
        missing = _routing_attribute(variable, attribute)
        if missing is not None:
            values[np.isin(values, np.asarray(missing).reshape(-1))] = np.nan
    values *= float(_routing_attribute(variable, "scale_factor", 1.0))
    values += float(_routing_attribute(variable, "add_offset", 0.0))
    return values


@dataclass(frozen=True)
class RoutingTopology:
    sequence: np.ndarray
    longitude: np.ndarray
    latitude: np.ndarray
    sources: np.ndarray
    destinations: np.ndarray

    @cached_property
    def adjacency(self):
        """Compact edge-index lists in the forward and reverse directions."""
        result = []
        for nodes in (self.sources, self.destinations):
            order = np.argsort(nodes, kind="stable")
            offsets = np.concatenate(([0], np.cumsum(np.bincount(nodes, minlength=len(self.sequence)))))
            result.append((order, offsets))
        return result

    @cached_property
    def spatial_index(self):
        from scipy.spatial import cKDTree

        valid = (
            np.isfinite(self.sequence) & (self.sequence > 0)
            & np.isfinite(self.longitude) & np.isfinite(self.latitude)
            & (np.abs(self.longitude) <= 180) & (np.abs(self.latitude) <= 90)
        )
        indices = np.flatnonzero(valid)
        return indices, cKDTree(_sphere_points(self.longitude[valid], self.latitude[valid]))


@dataclass(frozen=True)
class RoutingNetwork:
    segments: np.ndarray
    upstream_area_m2: np.ndarray
    topology: RoutingTopology | None = None


def _routing_id_indices(sequence: np.ndarray, values: np.ndarray, *, strict: bool) -> np.ndarray:
    """Resolve positive sequence IDs without assuming IDs are row numbers."""
    valid = np.isfinite(sequence) & (sequence > 0)
    indices = np.flatnonzero(valid)
    indices = indices[np.argsort(sequence[indices])]
    sorted_ids = sequence[indices]
    positive = np.isfinite(values) & (values > 0)
    integral = values == np.floor(values)
    if strict and np.any(positive & ~integral):
        raise ValueError("Routing connections contain non-integer positive IDs")
    rows = np.flatnonzero(positive & integral)
    positions = np.searchsorted(sorted_ids, values[rows])
    in_range = positions < len(sorted_ids)
    candidates, positions = rows[in_range], positions[in_range]
    matched = sorted_ids[positions] == values[candidates]
    result = np.full(len(values), -1, dtype=np.int64)
    result[candidates[matched]] = indices[positions[matched]]
    if strict and np.any(positive & (result < 0)):
        missing = np.unique(values[positive & (result < 0)])[:10]
        raise ValueError(f"Routing connections reference missing positive seq IDs: {missing.tolist()}")
    return result


def load_routing_network(path: Path, *, trace: bool = False) -> RoutingNetwork:
    """Read routing arrays once and connect sequence IDs to downstream IDs."""
    if not path.is_file():
        raise FileNotFoundError(f"Routing file not found: {path}")
    with _open_routing_file(path) as dataset:
        sequence, next_sequence, longitude, latitude, area = (
            _routing_values(dataset, name)
            for name in ("seq", "seq_next", "seq_lon", "seq_lat", "topo_uparea")
        )
        units = _routing_attribute(_routing_variable(dataset, "topo_uparea"), "units", "")
        bifurcation = None
        if trace:
            variables = dataset.variables if hasattr(dataset, "variables") else dataset
            present = [name in variables for name in ("bifurcation_upst", "bifurcation_down")]
            if any(present) and not all(present):
                raise ValueError("Routing file must supply both bifurcation_upst and bifurcation_down")
            if all(present):
                bifurcation = tuple(_routing_values(dataset, name)
                                    for name in ("bifurcation_upst", "bifurcation_down"))
            else:
                LOG.warning("Routing file supplies no bifurcation variables; tracing recorded main connections only")
    if len({len(values) for values in (sequence, next_sequence, longitude, latitude, area)}) != 1:
        raise ValueError("Routing variables must have the same length")
    if isinstance(units, bytes):
        units = units.decode("utf-8", errors="replace")
    if str(units).strip().lower() not in {"m2", "m^2", "m²"}:
        raise ValueError(f"Routing variable topo_uparea must use square metres; found units={units!r}")

    valid_ids = np.isfinite(sequence) & (sequence > 0)
    if np.any(sequence[valid_ids] != np.floor(sequence[valid_ids])):
        raise ValueError("Routing variable seq contains non-integer positive IDs")
    indices = np.flatnonzero(valid_ids)
    order = np.argsort(sequence[indices])
    indices = indices[order]
    sorted_ids = sequence[indices]
    if np.any(np.diff(sorted_ids) == 0):
        raise ValueError("Routing variable seq contains duplicate positive IDs")

    destination = _routing_id_indices(sequence, next_sequence, strict=trace)
    sources = np.flatnonzero(valid_ids & (destination >= 0))
    destinations = destination[sources]
    topology = None
    if trace:
        edges = np.column_stack((sources, destinations))
        if bifurcation is not None:
            upstream, downstream = bifurcation
            if len(upstream) != len(downstream):
                raise ValueError("Bifurcation upstream and downstream arrays must have equal length")
            bif_sources = _routing_id_indices(sequence, upstream, strict=True)
            bif_destinations = _routing_id_indices(sequence, downstream, strict=True)
            valid_bif = (bif_sources >= 0) & (bif_destinations >= 0)
            edges = np.concatenate((edges, np.column_stack((bif_sources[valid_bif], bif_destinations[valid_bif]))))
        edges = np.unique(edges[edges[:, 0] != edges[:, 1]], axis=0)
        topology = RoutingTopology(sequence, longitude, latitude, edges[:, 0], edges[:, 1])
    valid_coordinates = (
        np.isfinite(longitude) & np.isfinite(latitude)
        & (np.abs(longitude) <= 180) & (np.abs(latitude) <= 90)
    )
    valid = (
        (sources != destinations) & valid_coordinates[sources] & valid_coordinates[destinations]
        & np.isfinite(area[sources]) & (area[sources] >= 0)
    )
    sources, destinations = sources[valid], destinations[valid]
    segments = np.stack(
        (np.column_stack((longitude[sources], latitude[sources])),
         np.column_stack((longitude[destinations], latitude[destinations]))),
        axis=1,
    )
    LOG.info("Loaded CoLM river network: %s (%d valid segments)", path, len(segments))
    return RoutingNetwork(segments, area[sources], topology)


def _sphere_points(longitude: np.ndarray, latitude: np.ndarray) -> np.ndarray:
    lon, lat = np.deg2rad(longitude), np.deg2rad(latitude)
    return np.column_stack((np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)))


@dataclass(frozen=True)
class StationTrace:
    layers: dict[str, np.ndarray]
    station_rows: np.ndarray
    node_indices: np.ndarray
    distances_km: np.ndarray
    bounds: tuple[float, float, float, float]


def _walk_routing(topology: RoutingTopology, seed: int, *, reverse: bool) -> np.ndarray:
    order, offsets = topology.adjacency[int(reverse)]
    neighbors = topology.sources if reverse else topology.destinations
    visited = np.zeros(len(topology.sequence), dtype=bool)
    edges = np.zeros(len(topology.sources), dtype=bool)
    pending = [seed]
    visited[seed] = True
    while pending:
        node = pending.pop()
        connected = order[offsets[node]:offsets[node + 1]]
        edges[connected] = True
        for neighbor in neighbors[connected]:
            if not visited[neighbor]:
                visited[neighbor] = True
                pending.append(int(neighbor))
    return edges


def trace_station_network(
    stations: pd.DataFrame,
    topology: RoutingTopology,
    station_ids: list[str] | None,
    max_distance_km: float,
) -> StationTrace:
    """Match selected station coordinates and trace both directions, including bifurcations."""
    ids = stations["station_id"].astype(str)
    if station_ids is None:
        rows = np.arange(len(stations))
    else:
        missing = sorted(set(station_ids) - set(ids))
        if missing:
            raise ValueError(f"Highlighted station IDs not found in this evaluation: {missing}")
        rows = np.flatnonzero(ids.isin(station_ids))
    if not len(rows):
        raise ValueError("No stations selected for routing trace")
    try:
        lon, lat, valid = _station_coordinates(stations)
    except ValueError as exc:
        raise ValueError(f"Invalid routing-match coordinates for stations: {ids.iloc[rows].tolist()}") from exc
    valid &= (np.abs(lon) <= 180) & (np.abs(lat) <= 90)
    if not valid[rows].all():
        raise ValueError(f"Invalid routing-match coordinates for stations: {ids.iloc[rows[~valid[rows]]].tolist()}")
    indices, tree = topology.spatial_index
    if not len(indices):
        raise ValueError("Routing file contains no nodes with valid matching coordinates")
    distances, positions = tree.query(_sphere_points(lon[rows], lat[rows]), k=2)
    distances = 2 * 6371.0088 * np.arcsin(np.clip(distances / 2, 0, 1))
    problems = []
    for i, row in enumerate(rows):
        if distances[i, 0] > max_distance_km:
            problems.append(f"{ids.iloc[row]}: nearest node is {distances[i, 0]:.6f} km away (limit {max_distance_km:g} km)")
        elif len(indices) > 1 and np.isclose(distances[i, 0], distances[i, 1], atol=1e-6, rtol=0):
            problems.append(f"{ids.iloc[row]}: ambiguous equally near routing nodes ({distances[i, 0]:.6f} km)")
    if problems:
        raise ValueError("Station-to-routing matching failed: " + "; ".join(problems))
    matched_nodes = indices[positions[:, 0]]
    upstream = np.zeros(len(topology.sources), dtype=bool)
    downstream = np.zeros(len(topology.sources), dtype=bool)
    counts = {}
    for i, (row, node) in enumerate(zip(rows, matched_nodes)):
        if node not in counts:
            up = _walk_routing(topology, int(node), reverse=True)
            down = _walk_routing(topology, int(node), reverse=False)
            upstream |= up
            downstream |= down
            counts[node] = (int(up.sum()), int(down.sum()))
        LOG.info("Station %s -> seq=%s, distance=%.6f km, upstream=%d, downstream=%d",
                 ids.iloc[row], topology.sequence[node], distances[i, 0], *counts[node])
    drawable = (
        np.isfinite(topology.longitude) & np.isfinite(topology.latitude)
        & (np.abs(topology.longitude) <= 180) & (np.abs(topology.latitude) <= 90)
    )
    valid_edges = drawable[topology.sources] & drawable[topology.destinations]
    invalid_count = int(((upstream | downstream) & ~valid_edges).sum())
    if invalid_count:
        LOG.warning("%d traced edges have invalid endpoint coordinates and cannot be drawn", invalid_count)
    layers = {}
    for name, mask in (("upstream", upstream & ~downstream),
                       ("downstream", downstream & ~upstream), ("both", upstream & downstream)):
        sources, destinations = topology.sources[mask & valid_edges], topology.destinations[mask & valid_edges]
        layers[name] = np.stack((np.column_stack((topology.longitude[sources], topology.latitude[sources])),
                                 np.column_stack((topology.longitude[destinations], topology.latitude[destinations]))), axis=1)
    selected = upstream | downstream
    nodes = np.unique(np.concatenate((matched_nodes, topology.sources[selected], topology.destinations[selected])))
    nodes = nodes[drawable[nodes]]
    bounds = (float(topology.longitude[nodes].min()), float(topology.longitude[nodes].max()),
              float(topology.latitude[nodes].min()), float(topology.latitude[nodes].max()))
    LOG.info("Traced union: upstream=%d, downstream=%d, both=%d", *(len(layers[key]) for key in layers))
    return StationTrace(layers, rows, matched_nodes, distances[:, 0], bounds)


def _full_trace_extent(extent, trace: StationTrace) -> tuple[float, float, float, float]:
    west, east, south, north = trace.bounds
    xpad, ypad = max(0.25, (east - west) * 0.02), max(0.25, (north - south) * 0.02)
    return (max(-180.0, min(extent[0], west - xpad)), min(180.0, max(extent[1], east + xpad)),
            max(-90.0, min(extent[2], south - ypad)), min(90.0, max(extent[3], north + ypad)))


def _split_dateline_segments(segments: np.ndarray) -> np.ndarray:
    """Split short crossings at +/-180 instead of drawing across the world."""
    crossing = np.abs(segments[:, 1, 0] - segments[:, 0, 0]) > 180
    if not crossing.any():
        return segments
    pieces = [segments[~crossing]]
    for start, end in segments[crossing]:
        boundary = 180.0 if start[0] > 0 else -180.0
        unwrapped_end = end[0] + (360.0 if start[0] > 0 else -360.0)
        if unwrapped_end == start[0]:
            pieces.append(np.asarray([[start, (start[0], end[1])]]))
            continue
        latitude = start[1] + (end[1] - start[1]) * (boundary - start[0]) / (unwrapped_end - start[0])
        pieces.append(np.asarray([[start, (boundary, latitude)], [(-boundary, latitude), end]]))
    return np.concatenate(pieces)


def select_river_segments(
    network: RoutingNetwork,
    detail: str,
    extent: tuple[float, float, float, float],
) -> np.ndarray:
    """Keep segments whose two endpoints lie inside the map extent."""
    threshold, _ = RIVER_DETAIL_CONFIG[detail]
    west, east, south, north = extent
    longitude, latitude = network.segments[:, :, 0], network.segments[:, :, 1]
    selected = (
        (longitude >= west) & (longitude <= east)
        & (latitude >= south) & (latitude <= north)
    ).all(axis=1)
    if threshold is not None:
        selected &= network.upstream_area_m2 >= threshold * 1_000_000.0
    if detail == "none":
        selected[:] = False
    segments = network.segments[selected]
    if not len(segments) and detail != "none":
        LOG.warning("No CoLM river segments remain for detail=%s, extent=%s", detail, extent)
    LOG.info("Selected %d CoLM river segments (detail=%s, extent=%s)", len(segments), detail, extent)
    return segments


def _station_coordinates(stations: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    lon = pd.to_numeric(stations["matched_lon"], errors="coerce").to_numpy(dtype=float)
    lat = pd.to_numeric(stations["matched_lat"], errors="coerce").to_numpy(dtype=float)
    fallback_lon = pd.to_numeric(stations["original_lon"], errors="coerce").to_numpy(dtype=float)
    fallback_lat = pd.to_numeric(stations["original_lat"], errors="coerce").to_numpy(dtype=float)
    lon = np.where(np.isfinite(lon), lon, fallback_lon)
    lat = np.where(np.isfinite(lat), lat, fallback_lat)
    valid = np.isfinite(lon) & np.isfinite(lat)
    if not valid.any():
        raise ValueError("No finite station coordinates are available for plotting")
    return lon, lat, valid


def _station_extent(stations: pd.DataFrame) -> tuple[float, float, float, float]:
    lon, lat, valid = _station_coordinates(stations)
    xpad = max(1.0, (np.nanmax(lon[valid]) - np.nanmin(lon[valid])) * 0.08)
    ypad = max(1.0, (np.nanmax(lat[valid]) - np.nanmin(lat[valid])) * 0.08)
    return (
        float(np.nanmin(lon[valid]) - xpad), float(np.nanmax(lon[valid]) + xpad),
        float(np.nanmin(lat[valid]) - ypad), float(np.nanmax(lat[valid]) + ypad),
    )


def plot_station_map(
    stations: pd.DataFrame,
    path: Path,
    *,
    title: str,
    extent: tuple[float, float, float, float] | None,
    dpi: int,
    label_fontsize: float,
    river_segments: np.ndarray | None = None,
    river_detail: str = "fine",
    station_trace: StationTrace | None = None,
) -> int:
    """Plot every station and full ID. Returns the number of text labels."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from matplotlib.collections import LineCollection
    from matplotlib.lines import Line2D

    lon, lat, valid = _station_coordinates(stations)

    station_count = int(valid.sum())
    side = min(24.0, max(10.0, 9.0 + np.sqrt(station_count) * 0.22))
    try:
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature
    except ImportError as exc:
        raise RuntimeError("Cartopy is required to draw the station information map") from exc

    fig = plt.figure(figsize=(side * 1.25, side))
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    ax.add_feature(cfeature.LAND, facecolor="#f2f0e9", zorder=0)
    ax.add_feature(cfeature.OCEAN, facecolor="#eaf3f8", zorder=0)
    ax.add_feature(cfeature.COASTLINE, linewidth=0.7, edgecolor="#555555")
    ax.add_feature(cfeature.BORDERS, linewidth=0.4, edgecolor="#888888")
    ax.set_extent(extent if extent is not None else _station_extent(stations), crs=ccrs.PlateCarree())
    legend_handles = []
    if river_detail != "none" and river_segments is not None and len(river_segments):
        threshold, linewidth = RIVER_DETAIL_CONFIG[river_detail]
        ax.add_collection(LineCollection(
            _split_dateline_segments(river_segments), colors="#377fa8", linewidths=linewidth, alpha=0.9,
            transform=ccrs.PlateCarree(), zorder=3,
        ))
        label = "CoLM river network (all)" if threshold is None else f"CoLM river network (≥{threshold:,.0f} km²)"
        legend_handles.append(Line2D([], [], color="#377fa8", linewidth=linewidth, label=label))
    if station_trace is not None:
        for name, color, label in (
            ("upstream", "#e68613", "Upstream of selected stations"),
            ("downstream", "#7b3294", "Downstream of selected stations"),
            ("both", "#1b9e77", "Both upstream and downstream"),
        ):
            segments = station_trace.layers[name]
            if len(segments):
                ax.add_collection(LineCollection(
                    _split_dateline_segments(segments), colors=color, linewidths=1.1,
                    transform=ccrs.PlateCarree(), zorder=4,
                ))
                legend_handles.append(Line2D([], [], color=color, linewidth=1.1, label=label))
        rows = station_trace.station_rows
        ax.scatter(lon[rows], lat[rows], s=80, facecolors="none", edgecolors="black",
                   linewidths=1.1, zorder=5.5)
        legend_handles.append(Line2D([], [], marker="o", color="none", markeredgecolor="black",
                                     markerfacecolor="none", label=f"Traced stations ({len(rows)})"))
    if legend_handles:
        ax.legend(handles=legend_handles, loc="lower left", fontsize=8)
    gridlines = ax.gridlines(draw_labels=True, linewidth=0.35, color="gray", alpha=0.5, linestyle=":")
    gridlines.top_labels = False
    gridlines.right_labels = False
    ax.scatter(lon[valid], lat[valid], s=24, color="#d7301f", edgecolor="white", linewidth=0.45, zorder=5)

    offsets = ((5, 5), (5, -9), (-5, 5), (-5, -9), (9, 0), (-9, 0), (0, 8), (0, -11))
    labels = 0
    for sequence, index in enumerate(np.flatnonzero(valid)):
        dx, dy = offsets[sequence % len(offsets)]
        ax.annotate(
            str(stations.iloc[index]["station_id"]),
            xy=(lon[index], lat[index]),
            xytext=(dx, dy),
            textcoords="offset points",
            fontsize=label_fontsize,
            color="#202020",
            arrowprops={"arrowstyle": "-", "lw": 0.3, "color": "#777777"},
            zorder=6,
        )
        labels += 1
    ax.set_title(f"{title}\nSelected stations: {station_count}", fontsize=13, weight="bold")

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}.tmp{path.suffix}")
    try:
        fig.savefig(temporary, dpi=dpi, bbox_inches="tight", format=path.suffix.lstrip("."))
        os.replace(temporary, path)
    finally:
        plt.close(fig)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return labels


def _case_dir(cfg, config_path: Path, override: str | None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    output = Path(str(cfg.project.output_dir)).expanduser()
    candidates = [output / cfg.project.name]
    if not output.is_absolute():
        candidates.append(config_path.parent / output / cfg.project.name)
    existing = next((candidate for candidate in candidates if candidate.is_dir()), candidates[0])
    return existing.resolve()


def _effective_reference(cfg, resolved):
    from openbench.config.adapter import _apply_reference_override, _source_override, reference_data_dir

    override = _source_override(cfg, resolved.source_name) or _source_override(cfg, resolved.resolved_name)
    ref_ds, var_map = _apply_reference_override(
        resolved.ref_ds, resolved.var_map, resolved.var_name, override
    )
    root, _directory = reference_data_dir(cfg, ref_ds, var_map, override)
    return ref_ds, root


def _validate_trace_options(args: argparse.Namespace) -> None:
    if args.station_trace == "selected" and not args.highlight_station:
        raise ValueError("--station-trace selected requires at least one --highlight-station ID")
    if args.station_trace != "selected" and args.highlight_station:
        raise ValueError("--highlight-station requires --station-trace selected")
    if not np.isfinite(args.station_match_max_km) or args.station_match_max_km <= 0:
        raise ValueError("--station-match-max-km must be a finite positive number")


def _station_map_groups(stations: pd.DataFrame, args: argparse.Namespace):
    """Yield independent map tables with safe, distinct filename suffixes."""
    if not args.per_station:
        yield stations, "", ""
        return
    ids = stations["station_id"].astype(str)
    rows = np.arange(len(stations))
    if args.station_trace == "selected":
        missing = sorted(set(args.highlight_station) - set(ids))
        if missing:
            raise ValueError(f"Highlighted station IDs not found in this evaluation: {missing}")
        rows = np.flatnonzero(ids.isin(args.highlight_station))
    if not len(rows):
        raise ValueError("No stations available for per-station maps")
    duplicates = ids.duplicated(keep=False)
    for row in rows:
        station_id = ids.iloc[row]
        components = ["station", station_id]
        if duplicates.iloc[row]:
            components.append(f"row{row + 1}")
        suffix = "__" + join_filename_components(*components)
        yield stations.iloc[[row]].reset_index(drop=True), suffix, f" | Station {station_id}"


def export(args: argparse.Namespace) -> list[tuple[Path, list[Path]]]:
    _validate_trace_options(args)
    from openbench.cli.run import _expand_config_paths
    from openbench.config import load_config
    from openbench.config.resolver import resolve_all_references
    from openbench.data.registry.manager import get_registry
    from openbench.data.station_matcher import resolve_station_dataset

    config_path = Path(args.config).expanduser().resolve()
    cfg = load_config(str(config_path))
    _expand_config_paths(cfg)
    case_dir = _case_dir(cfg, config_path, args.case_dir)
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else case_dir / "data" / "station_info"
    wanted_variables = tuple(args.variables or SEDIMENT_VARIABLES)
    unknown = [name for name in wanted_variables if name not in SEDIMENT_VARIABLES]
    if unknown:
        raise ValueError(f"Not a built-in sediment variable: {', '.join(unknown)}")

    resolved_refs = resolve_all_references(cfg, get_registry(), strict=False)
    outputs: list[tuple[Path, list[Path]]] = []
    routing_network = None
    river_cache: dict[tuple[float, float, float, float], np.ndarray] = {}
    for resolved in resolved_refs:
        if resolved.status != "ok" or resolved.var_name not in wanted_variables:
            continue
        if args.reference and args.reference not in {resolved.source_name, resolved.resolved_name}:
            continue
        ref_ds, root = _effective_reference(cfg, resolved)
        matching = getattr(ref_ds, "station_matching", None)
        if matching is None:
            LOG.warning("Skipping %s: reference %s has no station_matching", resolved.var_name, resolved.resolved_name)
            continue
        dataset_path = resolve_station_dataset(root, matching.dataset_file)
        if dataset_path is None:
            raise FileNotFoundError(f"Station reference dataset not found below {root}: {matching.dataset_file}")

        for simulation, sim_entry in cfg.simulation.items():
            if args.simulation and simulation != args.simulation:
                continue
            evaluated_path = None
            for output_reference in dict.fromkeys((resolved.resolved_name, resolved.source_name)):
                try:
                    evaluated_path = find_evaluated_station_file(
                        case_dir, resolved.var_name, output_reference, simulation
                    )
                    break
                except FileNotFoundError:
                    continue
            if evaluated_path is None:
                raise FileNotFoundError(
                    f"No evaluated station file for {resolved.var_name} / {resolved.source_name} / "
                    f"{simulation} under {case_dir}. Run the OpenBench evaluation first."
                )
            evaluated = read_station_csv(evaluated_path)
            metadata = read_reference_station_metadata(
                dataset_path,
                variable=resolved.var_name,
                grid_resolution=sim_entry.grid_res or cfg.project.grid_res,
                station_id_var=matching.station_id_var,
            )
            stations = merge_evaluated_with_metadata(evaluated, metadata)
            stem = join_filename_components(
                resolved.var_name, "ref", resolved.resolved_name, "sim", simulation
            )
            csv_path = output_dir / f"{stem}__stations.csv"

            formats = ("png", "pdf") if args.format == "both" else (args.format,)
            figure_paths = []
            configured_extent = None
            if cfg.project.lon_range is not None and cfg.project.lat_range is not None:
                configured_extent = (
                    float(cfg.project.lon_range[0]), float(cfg.project.lon_range[1]),
                    float(cfg.project.lat_range[0]), float(cfg.project.lat_range[1]),
                )
            tracing = args.station_trace != "none"
            if args.river_detail != "none" or tracing:
                if routing_network is None:
                    routing_network = load_routing_network(
                        Path(args.routing_file).expanduser().resolve(), trace=tracing,
                    )
            for map_stations, station_suffix, title_suffix in _station_map_groups(stations, args):
                extent = configured_extent if configured_extent is not None else _station_extent(map_stations)
                station_trace = None
                views = [(extent, "")]
                if tracing:
                    station_trace = trace_station_network(
                        map_stations, routing_network.topology,
                        args.highlight_station if args.station_trace == "selected" and not args.per_station else None,
                        args.station_match_max_km,
                    )
                    views.append((_full_trace_extent(extent, station_trace), "__full_trace"))
                if not figure_paths:
                    _atomic_csv(stations, csv_path)
                for view_extent, suffix in views:
                    river_segments = None
                    if args.river_detail != "none":
                        if view_extent not in river_cache:
                            river_cache[view_extent] = select_river_segments(routing_network, args.river_detail, view_extent)
                        river_segments = river_cache[view_extent]
                    for image_format in formats:
                        figure_path = output_dir / f"{stem}__station_map{station_suffix}{suffix}.{image_format}"
                        plot_station_map(
                            map_stations,
                            figure_path,
                            title=f"{resolved.var_name} | {resolved.resolved_name} | {simulation}"
                                  + title_suffix + (" | Full routing trace" if suffix else ""),
                            extent=view_extent,
                            dpi=args.dpi,
                            label_fontsize=args.label_fontsize,
                            river_segments=river_segments,
                            river_detail=args.river_detail,
                            station_trace=station_trace,
                        )
                        figure_paths.append(figure_path)
            outputs.append((csv_path, figure_paths))
    if not outputs:
        raise RuntimeError("No matching sediment evaluation outputs were found")
    return outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="OpenBench YAML configuration")
    parser.add_argument("--variable", dest="variables", action="append", help="Sediment variable (repeatable)")
    parser.add_argument("--reference", help="Reference source filter")
    parser.add_argument("--simulation", help="Simulation label filter")
    parser.add_argument("--case-dir", help="Override the OpenBench case output directory")
    parser.add_argument("--output-dir", help="Output directory (default: <case>/data/station_info)")
    parser.add_argument("--format", choices=("png", "pdf", "both"), default="png")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--label-fontsize", type=float, default=6.0)
    parser.add_argument("--routing-file", type=Path, default=DEFAULT_ROUTING_FILE,
                        help="CoLM routing NetCDF (default: grid_routing_data_06min.nc)")
    parser.add_argument("--river-detail", choices=tuple(RIVER_DETAIL_CONFIG), default="fine",
                        help="CoLM river detail: all, fine (≥500 km²), medium (≥5,000 km²), "
                             "coarse (≥50,000 km²), or none (default: fine)")
    parser.add_argument("--station-trace", choices=("none", "all", "selected"), default="none",
                        help="Highlight upstream/downstream routing and add a full-extent map (default: none)")
    parser.add_argument("--highlight-station", action="append", metavar="ID",
                        help="Exported station_id to trace; repeatable, requires --station-trace selected")
    parser.add_argument("--station-match-max-km", type=float, default=10.0,
                        help="Maximum station-to-routing-node matching distance in km (default: 10)")
    parser.add_argument("--per-station", action="store_true",
                        help="Output a separate map for each station (selected IDs only in selected trace mode)")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_parser().parse_args(argv)
    if args.dpi < 72:
        raise SystemExit("--dpi must be at least 72")
    if args.label_fontsize <= 0:
        raise SystemExit("--label-fontsize must be positive")
    outputs = export(args)
    for csv_path, figures in outputs:
        print(csv_path)
        for figure in figures:
            print(figure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
