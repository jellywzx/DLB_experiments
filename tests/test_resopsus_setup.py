"""Reference setup checks using only temporary CSVs and synthetic file names."""
from __future__ import annotations

import contextlib
import csv
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dlb_prepare_resopsus as setup


class ResOpsUSSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'source'
        (self.source/'list').mkdir(parents=True)
        (self.source/'dataset').mkdir()
        for station in ('41','42'):
            (self.source/'dataset'/f'ResOpsUS_{station}.nc').write_bytes(b'not opened')
        for name in setup.LIST_NAMES:
            (self.source/'list'/name).write_text(
                'ID,SYEAR,EYEAR,LON,LAT,Flag,DIR\n'
                '41,1970,2020,-121.05,48.75,True,./old/ResOpsUS_41.nc\n'
                '42,1980,2021,-121.15,48.65,False,./old/ResOpsUS_42.nc\n')
        self.user = self.root/'user'

    def run_setup(self, *args):
        with patch.object(sys, 'argv', ['prepare', '--source-dir', str(self.source),
                                       '--user-dir', str(self.user), *args]), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(setup.main(), 0)
        return output.getvalue()

    def test_preview_writes_nothing(self):
        self.assertIn('DRY-RUN', self.run_setup())
        self.assertFalse(self.user.exists())

    def test_apply_preserves_sources_and_prepares_both_resolutions(self):
        before = {str(p):p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        self.run_setup('--apply')
        overlay = self.user/'references/dlb_resopsus.yaml'
        ref = yaml.safe_load(overlay.read_text())['ResOpsUS']
        self.assertEqual(ref['years'], [1970,2020])
        self.assertEqual(ref['variables'], setup.VARIABLES)
        for name in setup.LIST_NAMES:
            with (Path(ref['root_dir'])/'list'/name).open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]['DIR'], str(self.source/'dataset/ResOpsUS_41.nc'))
            self.assertEqual([row['Flag'] for row in rows], ['1','0'])
        self.assertEqual(before, {str(p):p.read_bytes() for p in self.source.rglob('*') if p.is_file()})
        previous = overlay.read_bytes()
        with self.assertRaisesRegex(ValueError, 'overlay exists'):
            self.run_setup('--apply')
        self.run_setup('--apply', '--overwrite')
        backups = list((self.user/'dlb_resopsus').glob('*/previous_dlb_resopsus.yaml'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), previous)

    def test_missing_file_or_bad_list_fails_before_writing(self):
        (self.source/'dataset/ResOpsUS_41.nc').unlink()
        with self.assertRaisesRegex(ValueError, 'missing observation'):
            self.run_setup('--apply')
        self.assertFalse(self.user.exists())
        (self.source/'list'/setup.LIST_NAMES[0]).write_text('ID,DIR\n41,old\n')
        with self.assertRaisesRegex(ValueError, 'missing CSV columns'):
            self.run_setup('--apply')

    def test_duplicate_ids_require_explicit_exclusion_of_all_rows(self):
        path = self.source/'list'/setup.LIST_NAMES[0]
        with path.open('a') as stream:
            stream.write('42,1980,2021,-80,20,True,./other/ResOpsUS_42.nc\n')
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'ambiguous duplicate IDs'):
            self.run_setup('--apply')
        self.assertFalse(self.user.exists())
        self.assertIn('excluding ALL rows', self.run_setup('--apply', '--exclude-duplicate-ids'))
        ref = yaml.safe_load((self.user/'references/dlb_resopsus.yaml').read_text())['ResOpsUS']
        with (Path(ref['root_dir'])/'list'/setup.LIST_NAMES[0]).open() as stream:
            self.assertEqual([r['ID'] for r in csv.DictReader(stream)], ['41'])
        self.assertEqual(path.read_bytes(), before)

    def test_atomic_overlay_failure_preserves_previous_registration(self):
        self.run_setup('--apply')
        overlay = self.user/'references/dlb_resopsus.yaml'
        previous = overlay.read_bytes()
        snapshots = set((self.user/'dlb_resopsus').iterdir())
        with patch.object(setup.os, 'replace', side_effect=OSError('injected failure')):
            with self.assertRaisesRegex(OSError, 'injected failure'):
                self.run_setup('--apply', '--overwrite')
        self.assertEqual(overlay.read_bytes(), previous)
        self.assertEqual(set((self.user/'dlb_resopsus').iterdir()), snapshots)

    def test_openbench_registry_and_filter_use_prepared_lists(self):
        try:
            from openbench.data.registry.manager import RegistryManager
            from openbench.data.custom.ResOpsUS_filter import _resolve_station_list, filter_ResOpsUS
        except ImportError:
            self.skipTest('OpenBench not installed')
        from types import SimpleNamespace
        self.run_setup('--apply')
        registry = RegistryManager(user_dir=self.user)
        ref = registry.get_reference('ResOpsUS')
        self.assertEqual(ref.years, [1970,2020])
        for grid, name in zip((0.1,0.25), setup.LIST_NAMES):
            info = SimpleNamespace(ref_dir=ref.root_dir, sim_grid_res=grid,
                                   sim_syear=2001, sim_eyear=2005, syear=2001, eyear=2005,
                                   min_year=1, min_lat=40, max_lat=50, min_lon=-130, max_lon=-110)
            self.assertEqual(_resolve_station_list(info).name, name)
            filter_ResOpsUS(info)
            self.assertEqual(len(info.stn_list), 1)
            self.assertEqual(int(info.stn_list.iloc[0]['ID']), 41)
            info.min_lat, info.max_lat = -22,11
            info.min_lon, info.max_lon = -82,-35
            with self.assertRaisesRegex(ValueError, 'No ResOpsUS stations'):
                filter_ResOpsUS(info)


if __name__ == '__main__':
    unittest.main()
