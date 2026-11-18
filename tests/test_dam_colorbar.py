"""Small basin-local colorbar checks; no model runs or map downloads."""
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
import xarray as xr
from cartopy.mpl.geoaxes import GeoAxes

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dam_analysis'))
import plot_openbench_dams_rivers as plot


def metadata(product='difference', measure='NSE', kind='metric', data_type='grid'):
    return SimpleNamespace(product_type=product, measure=measure, measure_type=kind,
                           data_type=data_type, simulations=('D', 'CTL'), item='Streamflow', unit='')


class ColorbarTests(unittest.TestCase):
    def style(self, values, product='difference', measure='NSE', kind='metric', limit=None,
              data_type='grid'):
        return plot.resolve_openbench_plot_style(
            np.asarray(values), metadata(product, measure, kind, data_type),
            SimpleNamespace(color_limit=limit))

    def test_small_signed_one_sided_and_extreme_values(self):
        for values in ([-.03, .07], [.01, .07], [-.07, -.01]):
            with self.subTest(values=values):
                ticks = plot.difference_color_ticks(np.array(values), None, {})
                np.testing.assert_allclose(ticks, np.arange(-4, 5)*.02)
        for amplitude in (1e-12, 1e12, 123., 1.):
            ticks = plot.difference_color_ticks(np.array([-amplitude/2, amplitude]), None, {})
            self.assertGreaterEqual(ticks[-1], amplitude)
            self.assertEqual(ticks[0], -ticks[-1])

    def test_zero_nonfinite_and_empty(self):
        ticks = plot.difference_color_ticks(np.array([0., np.nan, 0.]), None, {})
        np.testing.assert_allclose(ticks, np.linspace(-1, 1, 9))
        for values in ([], [np.nan, np.inf, -np.inf]):
            with self.assertRaisesRegex(ValueError, 'no finite values'):
                plot.difference_color_ticks(np.array(values), None, {})
        ticks = plot.difference_color_ticks(np.array([np.nan, -.03, .07, np.inf]), None, {})
        self.assertAlmostEqual(ticks[-1], .08)

    def test_difference_and_anomaly_do_not_use_raw_metric_bounds(self):
        for product in ('difference', 'anomaly'):
            for measure, kind in (('NSE', 'metric'), ('RMSE', 'metric'),
                                  ('percent_bias', 'metric'), ('Overall_Score', 'score')):
                with self.subTest(product=product, measure=measure):
                    with patch.object(plot, 'get_index', side_effect=AssertionError('legacy path')):
                        style = self.style([-.03, .07], product, measure, kind)
                    self.assertAlmostEqual(style.norm.vmin, -.08)
                    self.assertAlmostEqual(style.norm.vmax, .08)
                    self.assertEqual(style.norm(0), .5)
                    self.assertEqual(style.extend, 'neither')
                    np.testing.assert_array_equal(style.ticks, style.levels)

    def test_extrema_are_not_percentile_clipped(self):
        style = self.style([0.]*100 + [-3., .07], measure='RMSE')
        self.assertLessEqual(style.norm.vmin, -3.)
        self.assertEqual(style.extend, 'neither')

    def test_manual_limits_are_exact_and_extensions_are_actual(self):
        for values, extend in (([-.03, .07], 'neither'), ([-.5, .5], 'both'),
                               ([-.5, .01], 'min'), ([0., .5], 'max')):
            style = self.style(values, limit=.073)
            self.assertEqual(style.norm.vmin, -.073)
            self.assertEqual(style.norm.vmax, .073)
            self.assertEqual(style.extend, extend)
        for limit in (0, -1, np.nan, np.inf):
            with self.assertRaises(ValueError):
                plot.difference_color_ticks(np.array([0.]), limit, {})

    def test_cli_rejects_invalid_color_limits(self):
        for value in ('0', '-1', 'nan', 'inf'):
            with self.subTest(value=value), patch.object(
                    sys, 'argv', ['plot', '--color-limit', value]), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    plot.parse_args()
                self.assertEqual(error.exception.code, 2)

    def test_template_override_and_cli_precedence(self):
        option = dict(vmin_max_on=True, vmin=-.11, vmax=.23)
        ticks = plot.difference_color_ticks(np.array([-.03, .07]), None, option)
        np.testing.assert_allclose(ticks[[0, -1]], [-.11, .23])
        ticks = plot.difference_color_ticks(np.array([-.03, .07]), .07, option)
        np.testing.assert_allclose(ticks[[0, -1]], [-.07, .07])
        with self.assertRaises(ValueError):
            plot.difference_color_ticks(np.array([0.]), None, dict(option, vmax=-.2))

    def test_raw_and_ensemble_keep_native_path(self):
        for product in ('raw', 'ensemble_mean'):
            for measure, kind, bounds in (('NSE', 'metric', (-1., 1.)),
                                          ('Overall_Score', 'score', (0., 1.))):
                with patch.object(plot, 'get_index', wraps=plot.get_index) as native:
                    style = self.style([.1, .3], product, measure, kind)
                native.assert_called_once()
                self.assertEqual((style.norm.vmin, style.norm.vmax), bounds)

    def test_filtered_csv_netcdf_and_all_renderers_share_limits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            coords = {'lat': [30., 31., 32.], 'lon': [-96., -95., -94.]}
            mask = np.zeros((3, 3), dtype='i1')
            mask[1, 1:] = 1
            xr.Dataset({'basin_mask': (('lat', 'lon'), mask)}, coords=coords).to_netcdf(root/'mask.nc')
            basin = plot.load_basin_mask(root/'mask.nc')
            values = np.full((3, 3), 1000.)
            values[1, 1:] = [-.03, .07]
            xr.Dataset({'NSE': (('lat', 'lon'), values)}, coords=coords).to_netcdf(root/'grid.nc')
            grid = plot.apply_basin_mask(plot.read_grid(root/'grid.nc', 'NSE'), basin)
            pd.DataFrame({'lon': [-96, -95, -94], 'lat': [30, 31, 31],
                          'NSE': [1000., -.03, .07]}).to_csv(root/'stn.csv', index=False)
            stations = plot.filter_points_to_basin(pd.read_csv(root/'stn.csv'), basin)
            styles = [self.style(grid.values), self.style(stations.NSE.values, data_type='station')]
            for style in styles:
                self.assertAlmostEqual(style.norm.vmax, .08)
                style.option['font'] = 'DejaVu Sans'
            # Natural Earth features are unrelated to this check; avoid downloads.
            with patch.object(GeoAxes, 'add_feature'), patch.object(
                    plot, '_add_openbench_colorbar', wraps=plot._add_openbench_colorbar) as colorbar:
                plot.plot_combined_map(stations, plot.empty_dams(), 'NSE', styles[1],
                                       root/'station.png', basin.extent, 50, 'none', basin_mask=basin)
                for method in ('mesh', 'interpolate'):
                    styles[0].option['show_method'] = method
                    plot.plot_grid_map(grid, plot.empty_dams(), root/'grid.nc', root/f'{method}.png',
                                       basin.extent, styles[0], 50, None, None, basin, None)
                self.assertEqual(colorbar.call_count, 3)
                for call in colorbar.call_args_list:
                    self.assertAlmostEqual(call.args[3].norm.vmax, .08)
            self.assertEqual(len(list(root.glob('*.png'))), 3)


if __name__ == '__main__':
    unittest.main()
