"""Small raster/render checks; no OpenBench bootstrap, downloads or model runs."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
import pickle
import unittest

import cartopy.crs as ccrs
from cartopy.mpl.path import shapely_to_path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
import numpy as np
from shapely.geometry import box, Point
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
import xarray as xr

# Isolate the real spatial helpers from CLI-dependent OpenBench source discovery.
SOURCE = Path(__file__).resolve().parents[1] / "dam_analysis/plot_openbench_dams_rivers.py"
NAMES = {"BasinMask", "coordinate_edges", "clip_map_to_basin", "draw_basin_mask_boundary"}
tree = ast.parse(SOURCE.read_text())
helpers = ast.Module(body=[node for node in tree.body
                          if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                          and node.name in NAMES], type_ignores=[])
exec(compile(helpers, str(SOURCE), "exec"), globals())


class BasinClippingTests(unittest.TestCase):
    def setUp(self):
        self.values = np.zeros((7, 9), dtype=bool)
        self.values[1:6, 1:6] = True
        self.values[3, 3] = False  # Interior hole.
        self.values[1, 7] = True  # Disconnected cell.
        self.basin = BasinMask(
            xr.DataArray(self.values, dims=("lat", "lon"),
                         coords={"lat": np.arange(7), "lon": np.arange(9)}),
            (0.5, 7.5, 0.5, 5.5), Path("synthetic.nc"),
        )
        self.addCleanup(plt.close, "all")

    def test_cell_edges_holes_islands_and_worker_pickle(self):
        geometry = self.basin.geometry
        self.assertEqual(geometry.area, 25)
        self.assertEqual(geometry.bounds, (0.5, 0.5, 7.5, 5.5))
        self.assertFalse(geometry.covers(Point(3, 3)))
        self.assertTrue(geometry.covers(Point(7, 1)))
        self.assertTrue(geometry.covers(Point(0.51, 1)))
        self.assertFalse(geometry.covers(Point(0.49, 1)))
        restored = pickle.loads(pickle.dumps(self.basin))
        self.assertTrue(restored.geometry.equals(geometry))

    def test_all_render_types_clip_holes_and_outside_on_repeated_draws(self):
        for kind in ("feature", "mesh", "contour", "scatter", "lines", "image"):
            with self.subTest(kind=kind):
                crs = ccrs.PlateCarree()
                fig, ax = plt.subplots(figsize=(6, 4), dpi=100,
                                       subplot_kw={"projection": crs})
                ax.set_extent((-0.5, 8.5, -0.5, 6.5), crs=crs)
                if kind == "feature":
                    ax.add_geometries([box(-1, -1, 10, 8)], crs,
                                      facecolor="red", edgecolor="none")
                elif kind == "mesh":
                    ax.pcolormesh(np.arange(10)-0.5, np.arange(8)-0.5,
                                  np.ones((7, 9)), cmap="Reds", vmin=0, vmax=1)
                elif kind == "contour":
                    ax.contourf(np.arange(9), np.arange(7), np.ones((7, 9)),
                                levels=[0, 2], colors=["red"])
                elif kind == "scatter":
                    ax.scatter([0, 2, 3, 7], [3, 3, 3, 1], s=1600, color="red")
                elif kind == "lines":
                    ax.add_collection(LineCollection([[(-1, 3), (10, 3)]],
                                                     colors="red", linewidths=15))
                else:
                    ax.imshow(np.zeros((7, 9)), extent=(-0.5, 8.5, -0.5, 6.5),
                              cmap="Reds_r", vmin=0, vmax=1)
                gridlines = ax.gridlines(draw_labels=True, color="blue")
                gridlines.top_labels = gridlines.right_labels = False
                draw_basin_mask_boundary(ax, self.basin, crs)
                clip_map_to_basin(ax, self.basin, crs, gridlines)
                for _ in range(2):
                    fig.canvas.draw()
                    pixels = np.asarray(fig.canvas.buffer_rgba())
                    for point in ((0, 3), (3, 3), (7, 3)):
                        x, y = np.rint(ax.transData.transform(point)).astype(int)
                        np.testing.assert_array_equal(
                            pixels[pixels.shape[0]-1-y, x, :3], [255, 255, 255])
                    x, y = np.rint(ax.transData.transform((2, 3))).astype(int)
                    self.assertFalse(np.all(pixels[pixels.shape[0]-1-y, x, :3] == 255))
                self.assertTrue(gridlines.label_artists)
                self.assertEqual(len(ax.spines["geo"].get_path().vertices), 6)
                plt.close(fig)

    def test_no_basin_keeps_rectangular_map(self):
        fig, ax = plt.subplots(subplot_kw={"projection": ccrs.PlateCarree()})
        line, = ax.plot([0, 1], [0, 1])
        original = line.get_clip_path()
        gridlines = ax.gridlines()
        clip_map_to_basin(ax, None, ccrs.PlateCarree(), gridlines)
        self.assertIs(line.get_clip_path(), original)


if __name__ == "__main__":
    unittest.main()
