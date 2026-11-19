"""Lightweight routing tests; run with the OpenBench Python environment.

PYTHONPATH=/path/to/openbench/src python -m unittest discover -s tests -p test_sediment_station_trace.py -v
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from netCDF4 import Dataset

SCRIPT = Path(__file__).resolve().parents[1] / "sediment/export_sediment_station_info.py"
SPEC = importlib.util.spec_from_file_location("sediment_station_export_test", SCRIPT)
exporter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = exporter
SPEC.loader.exec_module(exporter)


class StationTraceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="station-trace-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "routing.nc"
        # 70->10->30->40->50, 20->30, 60->40; bifurcation 30->90.
        # 80 is isolated; input rows intentionally do not follow sequence order.
        with Dataset(self.path, "w") as ds:
            ds.createDimension("node", 9)
            ds.createDimension("bifurcation", 2)
            for name, values in {
                "seq": [30, 10, 20, 40, 50, 60, 70, 80, 90],
                "seq_next": [40, 30, 30, 50, -9, 40, 10, -9, -9],
                "seq_lon": [0, -2, -2, 2, 4, 2, -6, 0, 5],
                "seq_lat": [0, 1, -1, 0, 0, -2, 2, 2, 3],
                "topo_uparea": [1e6] * 9,
            }.items():
                var = ds.createVariable(name, "f8", ("node",))
                var[:] = values
                if name == "topo_uparea":
                    var.units = "m2"
            # Repeated edges are deduplicated.
            for name, values in {"bifurcation_upst": [30, 30], "bifurcation_down": [90, 90]}.items():
                ds.createVariable(name, "f8", ("bifurcation",))[:] = values
        self.stations = self.table(["A", "B"], [0, 2], [0, 0])

    @staticmethod
    def table(ids, lon, lat):
        return pd.DataFrame({"station_id": ids, "matched_lon": lon, "matched_lat": lat,
                             "original_lon": lon, "original_lat": lat})

    def network(self):
        return exporter.load_routing_network(self.path, trace=True)

    def trace(self, ids=None, stations=None, maximum=10):
        return exporter.trace_station_network(
            self.stations if stations is None else stations, self.network().topology, ids, maximum,
        )

    def test_directional_paths_and_bifurcation(self):
        network = self.network()
        self.assertEqual(len(network.topology.sources), 7)
        trace = exporter.trace_station_network(self.stations, network.topology, ["A"], 10)
        self.assertEqual({name: len(lines) for name, lines in trace.layers.items()},
                         {"upstream": 3, "downstream": 3, "both": 0})
        starts = trace.layers["downstream"][:, 0, :]
        self.assertFalse(np.any(np.all(starts == [2, -2], axis=1)))
        self.assertEqual(network.topology.sequence[trace.node_indices].tolist(), [30])
        self.assertEqual(trace.distances_km.tolist(), [0])

    def test_all_stations_and_overlap(self):
        trace = self.trace()
        self.assertEqual({name: len(lines) for name, lines in trace.layers.items()},
                         {"upstream": 4, "downstream": 2, "both": 1})
        np.testing.assert_array_equal(trace.layers["both"], [[[0, 0], [2, 0]]])
        self.assertEqual(trace.station_rows.tolist(), [0, 1])

    def test_area_threshold_does_not_remove_trace(self):
        network = self.network()
        with self.assertLogs(exporter.LOG, level="WARNING"):
            background = exporter.select_river_segments(network, "fine", (-10, 10, -5, 5))
        self.assertEqual(len(background), 0)
        self.assertEqual(sum(map(len, self.trace(["A"]).layers.values())), 6)

    def test_cycles_terminate_and_classify_overlap(self):
        with Dataset(self.path, "a") as ds:
            ds["seq_next"][8] = 30
        trace = self.trace(["A"])
        self.assertEqual(len(trace.layers["both"]), 2)

    def test_outlet_and_isolated_node(self):
        for station, lon, lat, expected_up in (("outlet", 4, 0, 6), ("isolated", 0, 2, 0)):
            trace = self.trace(stations=self.table([station], [lon], [lat]))
            self.assertEqual(len(trace.layers["upstream"]), expected_up)
            self.assertEqual(len(trace.layers["downstream"]), 0)

    def test_duplicate_station_positions(self):
        trace = self.trace(stations=self.table(["X", "Y"], [0, 0], [0, 0]))
        self.assertEqual(trace.node_indices.tolist(), [0, 0])
        self.assertEqual(len(trace.layers["upstream"]), 3)

    def test_nearest_match_and_limit(self):
        stations = self.table(["near"], [0.02], [0])
        trace = self.trace(stations=stations)
        self.assertAlmostEqual(trace.distances_km[0], 2.2239, places=3)
        with self.assertRaisesRegex(ValueError, "near: nearest node.*limit 1 km"):
            self.trace(stations=stations, maximum=1)

    def test_equal_distance_is_error(self):
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.trace(stations=self.table(["tie"], [0], [1]), maximum=200)

    def test_duplicate_node_coordinates_are_error(self):
        with Dataset(self.path, "a") as ds:
            ds["seq_lat"][7] = 0
        with self.assertRaisesRegex(ValueError, "A: ambiguous"):
            self.trace(["A"])

    def test_invalid_and_missing_station(self):
        with self.assertRaisesRegex(ValueError, "not found.*unknown"):
            self.trace(["unknown"])
        for longitude in (np.nan, 181):
            with self.assertRaisesRegex(ValueError, "Invalid.*bad"):
                self.trace(stations=self.table(["bad"], [longitude], [0]))

    def test_coordinate_fallback(self):
        stations = self.stations.copy()
        stations.loc[0, ["matched_lon", "matched_lat"]] = np.nan
        trace = self.trace(["A"], stations=stations)
        self.assertEqual(trace.node_indices.tolist(), [0])

    def test_invalid_drawing_coordinates_do_not_break_topology(self):
        with Dataset(self.path, "a") as ds:
            ds["seq_lon"][3] = np.nan
        with self.assertLogs(exporter.LOG, level="WARNING"):
            trace = self.trace(["A"])
        self.assertEqual(len(trace.layers["downstream"]), 1)
        self.assertGreaterEqual(trace.bounds[1], 4)

    def test_missing_or_incomplete_bifurcations(self):
        with Dataset(self.path, "a") as ds:
            ds.renameVariable("bifurcation_upst", "other_upst")
        with self.assertRaisesRegex(ValueError, "both bifurcation"):
            self.network()
        with Dataset(self.path, "a") as ds:
            ds.renameVariable("bifurcation_down", "other_down")
        with self.assertLogs(exporter.LOG, level="WARNING"):
            network = self.network()
        self.assertEqual(len(network.topology.sources), 6)

    def test_dangling_ids_are_error_only_for_tracing(self):
        for name, index in (("seq_next", 0), ("bifurcation_down", 0)):
            with self.subTest(name=name):
                with Dataset(self.path, "a") as ds:
                    original = ds[name][index]
                    ds[name][index] = 999
                with self.assertRaisesRegex(ValueError, "missing positive seq.*999"):
                    self.network()
                self.assertIsNone(exporter.load_routing_network(self.path).topology)
                with Dataset(self.path, "a") as ds:
                    ds[name][index] = original

    def test_full_extent_contains_original_and_entire_trace(self):
        extent = (-1, 1, -1, 1)
        full = exporter._full_trace_extent(extent, self.trace(["A"]))
        self.assertLess(full[0], -6)
        self.assertGreater(full[1], 5)
        self.assertLessEqual(full[2], extent[2])
        self.assertGreater(full[3], 3)

    def test_dateline_split_and_spherical_matching(self):
        split = exporter._split_dateline_segments(np.array([[[179., 0], [-179., 2]]]))
        np.testing.assert_array_equal(split, [[[179, 0], [180, 1]], [[-180, 1], [-179, 2]]])
        meridian = exporter._split_dateline_segments(np.array([[[180., 0], [-180., 2]]]))
        self.assertTrue(np.isfinite(meridian).all())
        topology = exporter.RoutingTopology(np.array([10.]), np.array([179.99]), np.array([0.]),
                                            np.array([], dtype=int), np.array([], dtype=int))
        trace = exporter.trace_station_network(self.table(["date"], [-179.99], [0]), topology, None, 10)
        self.assertAlmostEqual(trace.distances_km[0], 2.2239, places=3)

    def test_option_validation(self):
        parser = exporter.build_parser()
        invalid = [
            ["--station-trace", "selected"], ["--highlight-station", "A"],
            ["--station-trace", "all", "--highlight-station", "A"],
            ["--station-match-max-km", "nan"], ["--station-match-max-km", "0"],
        ]
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(ValueError):
                exporter._validate_trace_options(parser.parse_args(["--config", "dummy", *options]))

    def test_export_modes_views_cache_and_csv(self):
        refs = [NS(status="ok", var_name=v, source_name="ref", resolved_name="ref")
                for v in exporter.SEDIMENT_VARIABLES[:2]]
        ref_ds = NS(station_matching=NS(dataset_file="ref.nc", station_id_var="station"))
        cfg = NS(project=NS(lon_range=[-1, 1], lat_range=[-1, 1], grid_res=0.1),
                 simulation={"sim": NS(grid_res=0.1)})
        evaluated = pd.DataFrame({"ID": ["A", "B"], "ref_lon": [0, 2], "ref_lat": [0, 0]})
        modules = {
            "openbench.cli.run": NS(_expand_config_paths=lambda cfg: None),
            "openbench.config": NS(load_config=lambda path: cfg),
            "openbench.config.resolver": NS(resolve_all_references=lambda *a, **kw: refs),
            "openbench.data.registry.manager": NS(get_registry=lambda: None),
            "openbench.data.station_matcher": NS(resolve_station_dataset=lambda *a: self.path),
        }
        for mode, per_station in (("none", False), ("all", False), ("selected", False),
                                  ("none", True), ("all", True), ("selected", True)):
            label = mode + ("_per_station" if per_station else "")
            cfg.project.lon_range = None if per_station and mode == "none" else [-1, 1]
            cfg.project.lat_range = None if per_station and mode == "none" else [-1, 1]
            args = exporter.build_parser().parse_args([
                "--config", "dummy.yaml", "--output-dir", str(self.root / label),
                "--routing-file", str(self.path), "--station-trace", mode, "--river-detail", "none",
                "--format", "both", *(["--highlight-station", "A"] if mode == "selected" else []),
                *(["--per-station"] if per_station else []),
            ])
            with patch.dict(sys.modules, modules), \
                 patch.object(exporter, "_case_dir", return_value=self.root), \
                 patch.object(exporter, "_effective_reference", return_value=(ref_ds, self.root)), \
                 patch.object(exporter, "find_evaluated_station_file", return_value=self.path), \
                 patch.object(exporter, "read_station_csv", return_value=evaluated), \
                 patch.object(exporter, "read_reference_station_metadata", return_value=self.stations), \
                 patch.object(exporter, "load_routing_network", wraps=exporter.load_routing_network) as load, \
                 patch.object(exporter, "trace_station_network", wraps=exporter.trace_station_network) as trace, \
                 patch.object(exporter, "plot_station_map", return_value=2) as plot:
                outputs = exporter.export(args)
                map_count = 2 if per_station and mode != "selected" else 1
                views = 1 if mode == "none" else 2
                self.assertEqual(load.call_count, 0 if mode == "none" else 1)
                self.assertEqual(trace.call_count, 0 if mode == "none" else 2 * map_count)
                self.assertEqual(plot.call_count, 4 * map_count * views)
                for csv_path, figures in outputs:
                    self.assertEqual(pd.read_csv(csv_path).station_id.tolist(), ["A", "B"])
                    self.assertEqual(len(figures), 2 * map_count * views)
                    self.assertEqual(len(set(figures)), len(figures))
                    if mode != "none":
                        for group in range(map_count):
                            self.assertTrue(all("__full_trace" in p.name for p in figures[4 * group + 2:4 * group + 4]))
                if per_station:
                    for call in plot.call_args_list:
                        self.assertEqual(len(call.args[0]), 1)
                        self.assertIn("__station__", call.args[1].name)
                        if mode != "none":
                            self.assertEqual(call.kwargs["station_trace"].station_rows.tolist(), [0])
                            self.assertEqual(len(call.kwargs["station_trace"].layers["both"]), 0)
                        if mode == "selected":
                            self.assertEqual(call.args[0].station_id.tolist(), ["A"])
                self.assertEqual(plot.call_args_list[0].kwargs["extent"], (-1, 1, -1, 1))
                if per_station and mode == "none":
                    self.assertEqual(plot.call_args_list[2].kwargs["extent"], (1, 3, -1, 1))
                if mode != "none":
                    calls = plot.call_args_list
                    self.assertIs(calls[0].kwargs["station_trace"], calls[3].kwargs["station_trace"])
                    self.assertLess(calls[2].kwargs["extent"][0], -6)
        for baseline in (self.root / "none").glob("*.csv"):
            for mode in ("all", "selected", "none_per_station", "all_per_station", "selected_per_station"):
                self.assertEqual(baseline.read_bytes(), (self.root / mode / baseline.name).read_bytes())

    def test_per_station_safe_filenames_and_duplicate_ids(self):
        stations = self.table(["A/B", "A:B", "duplicate", "duplicate"], [0, 1, 2, 3], [0] * 4)
        args = exporter.build_parser().parse_args(["--config", "dummy", "--per-station"])
        groups = list(exporter._station_map_groups(stations, args))
        suffixes = [suffix for _, suffix, _ in groups]
        self.assertEqual(len(set(suffixes)), 4)
        self.assertTrue(all("/" not in name and ":" not in name for name in suffixes))
        for frame, _, _ in groups:
            self.assertEqual(frame.index.tolist(), [0])
            self.assertEqual(len(frame), 1)
        self.assertEqual([frame.station_id.iloc[0] for frame, _, _ in groups], stations.station_id.tolist())

    def test_per_station_missing_selected_id(self):
        args = exporter.build_parser().parse_args([
            "--config", "dummy", "--per-station", "--station-trace", "selected",
            "--highlight-station", "missing",
        ])
        with self.assertRaisesRegex(ValueError, "not found.*missing"):
            list(exporter._station_map_groups(self.stations, args))

    def test_png_pdf_rendering_preserves_clipped_crossing_edges(self):
        from cartopy.io import Downloader
        from matplotlib.collections import LineCollection
        from matplotlib.figure import Figure

        trace = self.trace()
        original_save = Figure.savefig
        observed = []

        def save(fig, path, **kwargs):
            lines = [item for item in fig.axes[0].collections if isinstance(item, LineCollection)]
            observed.append(sum(len(item.get_segments()) for item in lines))
            self.assertEqual(len({tuple(item.get_color()[0]) for item in lines}), 3)
            self.assertTrue(all(item.get_clip_on() for item in lines))
            original_save(fig, path, **kwargs)

        with patch.object(Downloader, "_urlopen", side_effect=AssertionError("No downloads in tests")), \
             patch.object(Figure, "savefig", save):
            for suffix in ("png", "pdf"):
                output = self.root / f"trace.{suffix}"
                count = exporter.plot_station_map(
                    self.stations, output, title="Synthetic routing trace", extent=(-1, 1, -1, 1),
                    dpi=72, label_fontsize=6, river_detail="none", station_trace=trace,
                )
                self.assertEqual(count, 2)
                self.assertGreater(output.stat().st_size, 1000)
        self.assertEqual(observed, [7, 7])


if __name__ == "__main__":
    unittest.main()
