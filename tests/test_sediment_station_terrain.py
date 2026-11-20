"""Small, offline terrain tests; use the same environment as station trace tests."""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
import xarray as xr
from netCDF4 import Dataset

import test_sediment_station_trace as trace_tests

exporter = trace_tests.exporter


class StationTerrainTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="station-terrain-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.lon = np.arange(-4., 5.)
        self.lat = np.arange(-3., 4.)
        self.z = (self.lat[:, None] + 3) * 100 + self.lon[None, :] + 4
        self.dem = xr.Dataset({"elevation": (("lat", "lon"), self.z, {"units": "m"})},
                              coords={"lon": self.lon, "lat": self.lat})
        self.dem_path = self.root / "dem.nc"
        self.dem.to_netcdf(self.dem_path)
        self.extent = (-4, 4, -3, 3)

    def args(self, *options):
        return exporter.build_parser().parse_args(["--config", "dummy.yaml", *options])

    def test_disabled_never_opens_dem_and_custom_options_require_switch(self):
        with patch.object(xr, "open_dataset", side_effect=AssertionError("Must not open DEM")):
            with exporter._terrain_source(self.args()) as source:
                self.assertIsNone(source)
            for options in (("--dem-file", "absent.nc"), ("--terrain-mask", "absent.nc"),
                            ("--terrain-stride", "6")):
                with self.subTest(options=options), self.assertRaisesRegex(ValueError, "require --terrain"):
                    with exporter._terrain_source(self.args(*options)):
                        pass

    def test_orientation_dimension_order_and_cf_missing_values(self):
        baseline = exporter.TerrainSource(self.dem, None, 1).view(self.extent)
        reversed_dem = self.dem.isel(lon=slice(None, None, -1), lat=slice(None, None, -1)).transpose("lon", "lat")
        reversed_dem = reversed_dem.rename_dims({"lon": "east", "lat": "north"})
        actual = exporter.TerrainSource(reversed_dem, None, 1).view(self.extent)
        np.testing.assert_allclose(actual.rgba, baseline.rgba)
        self.assertEqual(actual.extent, (-4.5, 4.5, -3.5, 3.5))
        ds = self.dem.copy(deep=True)
        ds["elevation"].values[3, 4] = np.nan
        ds.to_netcdf(self.dem_path, mode="w", encoding={"elevation": {"_FillValue": -9999.}})
        with exporter._terrain_source(self.args("--terrain", "--dem-file", str(self.dem_path),
                                                "--terrain-stride", "1")) as source:
            view = source.view(self.extent)
            self.assertEqual(view.rgba[3, 4, 3], 0)
            self.assertEqual(view.rgba[-1, 0, 3], 1)  # Zero metres is valid.
            self.assertTrue(np.isfinite(view.rgba).all())

    def test_different_mask_resolution_preserves_valid_zero_and_missing(self):
        ds = self.dem.copy(deep=True)
        ds["elevation"].values[3, 4] = np.nan
        mask = xr.Dataset({"basin_mask": (("lat", "lon"), np.array([[0, 0, 1, 1, 1]] * 3))},
                           coords={"lon": [-4., -2., 0., 2., 4.], "lat": [3., 0., -3.]})
        view = exporter.TerrainSource(ds, mask, 1).view(self.extent)
        expected = np.broadcast_to(self.lon >= -1, self.z.shape).copy()
        expected[3, 4] = False
        np.testing.assert_array_equal(view.rgba[..., 3], expected[::-1])
        self.assertIsNotNone(view.outline)
        # Normalize axes so even nearest-cell boundary ties are consistent.
        descending = mask.isel(lat=slice(None, None, -1), lon=slice(None, None, -1))
        other = exporter.TerrainSource(ds, descending, 1).view(self.extent)
        np.testing.assert_array_equal(other.rgba, view.rgba)

    def test_outside_mask_or_dem_keeps_an_empty_background(self):
        mask = xr.Dataset({"basin_mask": (("lat", "lon"), np.zeros_like(self.z))},
                           coords={"lon": self.lon, "lat": self.lat})
        source = exporter.TerrainSource(self.dem, mask, 1)
        with self.assertLogs(exporter.LOG, level="WARNING"):
            self.assertIsNone(source.view(self.extent))
            self.assertIsNone(source.view((20, 22, 20, 22)))
        with patch.object(source, "_read_view", side_effect=AssertionError("Cached empty view")):
            self.assertIsNone(source.view(self.extent))

    def test_window_pixel_budget_and_lru_cache(self):
        source = exporter.TerrainSource(self.dem, None, 1)
        small = source.view((-0.1, 0.1, -0.1, 0.1))
        self.assertLess(small.rgba.shape[0], len(self.lat))
        self.assertLess(small.rgba.shape[1], len(self.lon))
        with patch.object(exporter, "TERRAIN_MAX_PIXELS", 20):
            view = source.view(self.extent)
        self.assertLessEqual(view.rgba.shape[0] * view.rgba.shape[1], 20)
        with patch.object(source, "_read_view", wraps=source._read_view) as read:
            self.assertIs(source.view(self.extent), view)
            self.assertEqual(read.call_count, 0)
            source.view((-2, 2, -2, 2))
            self.assertEqual(len(source.cache), 2)
            source.view((-0.1, 0.1, -0.1, 0.1))
            self.assertEqual(read.call_count, 2)  # The oldest view was evicted.

    def test_invalid_inputs_fail_before_export_and_close_files(self):
        for options in (("--terrain-stride", "0"), ("--terrain-stride", "-1")):
            with self.assertRaisesRegex(ValueError, "positive"):
                with exporter._terrain_source(self.args("--terrain", *options)):
                    pass
        with self.assertRaises(FileNotFoundError):
            exporter.export(self.args("--terrain", "--dem-file", str(self.root / "absent.nc")))
        invalid = self.dem.copy(deep=True)
        invalid["elevation"].attrs["units"] = "degrees"
        invalid.to_netcdf(self.dem_path, mode="w")
        with patch.object(exporter, "_export", side_effect=AssertionError("Must validate first")):
            with self.assertRaisesRegex(ValueError, "units"):
                exporter.export(self.args("--terrain", "--dem-file", str(self.dem_path)))
        self.assertFalse(list(self.root.glob("*.csv")))
        with patch.object(xr.Dataset, "close", autospec=True) as close:
            with self.assertRaisesRegex(ValueError, "units"):
                with exporter._terrain_source(self.args("--terrain", "--dem-file", str(self.dem_path))):
                    pass
            self.assertEqual(close.call_count, 1)
        for ds in (self.dem.drop_vars("elevation"), self.dem.assign_coords(lon=[0, 1, 2, 3, 4, 5, 6, 7, 9])):
            with self.assertRaises(ValueError):
                exporter.TerrainSource(ds, None, 1)

    def test_export_per_station_trace_formats_cache_and_names(self):
        routing = self.root / "routing.nc"
        with Dataset(routing, "w") as ds:
            ds.createDimension("node", 3)
            for name, data in {"seq": [1, 2, 3], "seq_next": [2, 3, -1],
                               "seq_lon": [-1, 0, 1], "seq_lat": [0, 0, 0],
                               "topo_uparea": [1e9] * 3}.items():
                var = ds.createVariable(name, "f8", ("node",))
                var[:] = data
                if name == "topo_uparea":
                    var.units = "m2"
        stations = trace_tests.StationTraceTests.table(["A", "B"], [-1, 1], [0, 0])
        evaluated = pd.DataFrame({"ID": ["A", "B"], "ref_lon": [-1, 1], "ref_lat": [0, 0]})
        ref = NS(status="ok", var_name=exporter.SEDIMENT_VARIABLES[0], source_name="ref", resolved_name="ref")
        cfg = NS(project=NS(lon_range=[-2, 2], lat_range=[-1, 1], grid_res=0.1), simulation={"sim": NS(grid_res=0.1)})
        ref_ds = NS(station_matching=NS(dataset_file="ref.nc", station_id_var="station"))
        modules = {
            "openbench.cli.run": NS(_expand_config_paths=lambda cfg: None),
            "openbench.config": NS(load_config=lambda path: cfg),
            "openbench.config.resolver": NS(resolve_all_references=lambda *a, **kw: [ref]),
            "openbench.data.registry.manager": NS(get_registry=lambda: None),
            "openbench.data.station_matcher": NS(resolve_station_dataset=lambda *a: routing),
        }
        for per_station, mode in ((False, "none"), (True, "none"), (False, "all"), (True, "selected")):
            options = ["--terrain", "--dem-file", str(self.dem_path), "--terrain-stride", "1",
                       "--routing-file", str(routing), "--river-detail", "none", "--station-trace", mode,
                       "--output-dir", str(self.root / f"{mode}_{per_station}"), "--format", "both"]
            if per_station:
                options.append("--per-station")
            if mode == "selected":
                options += ["--highlight-station", "A"]
            original = exporter.TerrainSource._read_view
            reads = []

            def read(source, extent):
                reads.append(extent)
                return original(source, extent)

            with ExitStack() as stack:
                stack.enter_context(patch.dict("sys.modules", modules))
                for name, value in (("_case_dir", self.root), ("_effective_reference", (ref_ds, self.root)),
                                    ("find_evaluated_station_file", routing), ("read_station_csv", evaluated),
                                    ("read_reference_station_metadata", stations)):
                    stack.enter_context(patch.object(exporter, name, return_value=value))
                stack.enter_context(patch.object(exporter.TerrainSource, "_read_view", read))
                plot = stack.enter_context(patch.object(exporter, "plot_station_map", return_value=2))
                outputs = exporter.export(self.args(*options))
            csv, figures = outputs[0]
            self.assertEqual(pd.read_csv(csv).station_id.tolist(), ["A", "B"])
            self.assertNotIn("__terrain", csv.name)
            self.assertTrue(all(p.stem.endswith("__terrain") for p in figures))
            groups = 2 if per_station and mode == "none" else 1
            self.assertEqual(len(figures), groups * (2 if mode == "none" else 4))
            self.assertEqual(len(reads), len(set(reads)))
            for i in range(0, len(plot.call_args_list), 2):
                first, second = plot.call_args_list[i:i+2]
                self.assertIs(first.kwargs["terrain"], second.kwargs["terrain"])
                self.assertTrue(first.kwargs["terrain_enabled"])
                if per_station:
                    self.assertEqual(len(first.args[0]), 1)
                if mode != "none":
                    self.assertIsNotNone(first.kwargs["station_trace"])

    def test_offline_png_pdf_layers_keep_stations_outside_mask(self):
        from cartopy.io import Downloader
        from matplotlib.figure import Figure
        from matplotlib.collections import LineCollection

        mask = xr.Dataset({"basin_mask": (("lat", "lon"), np.broadcast_to(self.lon >= 0, self.z.shape))},
                           coords={"lon": self.lon, "lat": self.lat})
        view = exporter.TerrainSource(self.dem, mask, 1).view(self.extent)
        stations = trace_tests.StationTraceTests.table(["outside", "inside"], [-2, 2], [0, 0])
        # This line crosses the mask boundary and must remain intact.
        rivers = np.array([[[-2, 0], [2, 0]]])
        original_save = Figure.savefig
        observed = []

        def save(fig, path, **kwargs):
            ax = fig.axes[0]
            points = [c for c in ax.collections if hasattr(c, "get_offsets") and len(c.get_offsets()) == 2]
            self.assertTrue(points)
            lines = [c for c in ax.collections if isinstance(c, LineCollection)]
            self.assertEqual(sum(len(c.get_segments()) for c in lines), 1)
            self.assertGreater(lines[0].get_zorder(), ax.images[0].get_zorder())
            self.assertEqual(len(fig.axes), 2)
            annotations = [t for t in ax.texts if t.get_text() in ("inside", "outside")]
            self.assertEqual(len(annotations), 2)
            self.assertTrue(all(t.get_path_effects() for t in annotations))
            observed.append(True)
            original_save(fig, path, **kwargs)

        with patch.object(Downloader, "_urlopen", side_effect=AssertionError("No downloads in tests")), \
             patch.object(Figure, "savefig", save):
            for ext in ("png", "pdf"):
                path = self.root / f"terrain.{ext}"
                self.assertEqual(exporter.plot_station_map(stations, path, title="Synthetic terrain stations",
                                  extent=self.extent, dpi=72, label_fontsize=7, river_segments=rivers,
                                  terrain=view, terrain_enabled=True), 2)
                self.assertGreater(path.stat().st_size, 1000)
        self.assertEqual(len(observed), 2)


if __name__ == "__main__":
    unittest.main()
