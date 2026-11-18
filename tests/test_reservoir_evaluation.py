"""Small synthetic monthly histories; no model, scheduler, or evaluation runs."""
from __future__ import annotations

import contextlib
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
import xarray as xr
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dlb_prepare_reservoir_evaluation as adapter


class ReservoirEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.history = self.root/'history'
        self.history.mkdir()
        self.reference = self.root/'reference'
        (self.reference/'list').mkdir(parents=True)
        self.station_list = self.reference/'list'/adapter.LIST_NAMES[0.1]
        with self.station_list.open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['ID','SYEAR','EYEAR','LON','LAT','Flag','DIR'])
            writer.writeheader()
            for station in (101,202,303):
                path = self.reference/f'{station}.nc'
                path.write_bytes(b'not read')
                writer.writerow(dict(ID=station,SYEAR=1990,EYEAR=2020,LON=-100,LAT=40,Flag=1,DIR=path))
        for year in (2001,2002):
            for month in range(1,13):
                ids = [101,202] if month % 2 else [202,101]
                data = np.array([[station,station] for station in ids], dtype=float).T
                if month == 1:
                    data[1,ids.index(101)] = -1e36
                ds = xr.Dataset({'resv_GRAND_ID':('reservoir',ids)},
                                coords={'time':pd.date_range(f'{year}-{month:02d}-01', periods=2)})
                for variable in ('qresv_in','qresv_out','volresv'):
                    ds[variable] = (('time','reservoir'),data)
                    ds[variable].attrs['units'] = 'm^3' if variable == 'volresv' else 'm^3/s'
                ds.to_netcdf(self.history/f'case_hist_unitcat_{year}-{month:02d}.nc')
        self.config_path = self.root/'config/openbench/basin/dam_inflow_D.yaml'
        self.config_path.parent.mkdir(parents=True)
        self.out = self.root/'config/reservoir_stations'
        self.config = dict(project=dict(name='test_dam_D',output_dir=str(self.root/'output'),
                                       years=[2001,2002],tim_res='Day',grid_res=0.1,
                                       lat_range=[30,50],lon_range=[-110,-90]),
                           evaluation=dict(variables=['Dam_Inflow']),
                           reference=dict(Dam_Inflow='ResOpsUS'),
                           metrics=['KGE','correlation','rSD','percent_bias'],scores=[],
                           simulation={'E1_D':dict(model='CoLM2024',root_dir=str(self.history),
                                       data_type='grid',tim_res='Day',grid_res=0.1,data_groupby='Month',
                                       variables={'Dam_Inflow':dict(prefix='case_hist_unitcat_',suffix=None)})},
                           comparison=dict(enabled=False,items=[]),statistics=dict(enabled=False,items=[]))
        self.write_config()

    def write_config(self):
        self.config_path.write_text(yaml.safe_dump(self.config,sort_keys=False))

    def run_adapter(self,*args):
        argv = ['prepare','--config',str(self.config_path),'--station-list',str(self.station_list),
                '--out-dir',str(self.out),*args]
        with patch.object(sys,'argv',argv), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(adapter.main(),0)
        return output.getvalue()

    def edit_month(self, transform):
        path = self.history/'case_hist_unitcat_2001-01.nc'
        with xr.open_dataset(path) as ds:
            updated = transform(ds.load())
        updated.to_netcdf(path, mode='w')

    def test_preview_never_extracts_or_writes(self):
        before = self.config_path.read_bytes()
        with patch.object(adapter,'extract',side_effect=AssertionError('preview read time series')):
            self.assertIn('matched 2 common stations',self.run_adapter())
        self.assertEqual(self.config_path.read_bytes(),before)
        self.assertFalse(self.out.exists())

    def test_apply_matches_ids_despite_reordered_columns_and_masks_missing(self):
        before = self.config_path.read_bytes()
        self.run_adapter('--apply')
        config = yaml.safe_load(self.config_path.read_text())
        sim = config['simulation']['E1_D']
        self.assertEqual(sim['data_type'],'stn')
        self.assertEqual(sim['variables'],{'Dam_Inflow':{'varname':'qresv_in','varunit':'m3 s-1'}})
        with Path(sim['fulllist']).open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([int(row['ID']) for row in rows],[101,202])
        with xr.open_dataset(rows[0]['DIR']) as ds:
            self.assertEqual(ds.qresv_in.dims,('time',))
            self.assertEqual(ds.sizes['time'],48)
            self.assertEqual(float(ds.qresv_in[0]),101)
            self.assertTrue(np.isnan(ds.qresv_in[1]))
            self.assertEqual(float(ds.qresv_in[2]),101)
            self.assertEqual(ds.qresv_in.attrs['units'],'m3 s-1')
        snapshot = next(self.out.iterdir())
        self.assertEqual((snapshot/'original.yaml').read_bytes(),before)
        self.assertEqual(json.loads((snapshot/'matching.json').read_text())['station_ids'],[101,202])
        self.assertEqual(config['project'],self.config['project'])
        self.assertEqual(config['reference']['overrides']['ResOpsUS']['root_dir'],str(self.reference))
        try:
            from openbench.config import load_config
            from openbench.data._processing_config import ProcessingConfigMixin
        except ImportError:
            return
        self.assertEqual(load_config(self.config_path).simulation['E1_D'].data_type,'stn')
        processor = ProcessingConfigMixin()
        merged = processor._merge_station_fulllists(sim['fulllist'],str(self.station_list))
        self.assertEqual(set(merged['ID'].astype(int)),{101,202})
        self.assertTrue({'sim_dir','ref_dir'} <= set(merged))

    def test_all_three_variable_bindings(self):
        for variable,(name,unit) in adapter.VARIABLES.items():
            with self.subTest(variable=variable):
                self.config['evaluation']['variables'] = [variable]
                self.config['reference'] = {variable:'ResOpsUS'}
                self.config['simulation']['E1_D']['variables'] = {variable:dict(prefix='case_hist_unitcat_')}
                self.write_config()
                self.run_adapter('--apply')
                config = yaml.safe_load(self.config_path.read_text())
                sim = config['simulation']['E1_D']
                with xr.open_dataset(Path(sim['root_dir'])/'202.nc') as ds:
                    np.testing.assert_array_equal(ds[name].values,np.full(48,202.))
                    self.assertEqual(ds[name].attrs['units'],unit)

    def test_no_nearest_matching_and_invalid_identity_rejected(self):
        for ids in ([900,901],[101,101],[-9999,202]):
            with self.subTest(ids=ids):
                self.edit_month(lambda ds: ds.assign(resv_GRAND_ID=('reservoir',ids)))
                with self.assertRaisesRegex(ValueError,'no common|duplicate resv|invalid/non-positive'):
                    self.run_adapter('--apply')
                self.assertFalse(self.out.exists())
        self.edit_month(lambda ds: ds.drop_vars('resv_GRAND_ID'))
        with self.assertRaisesRegex(ValueError,'missing resv_GRAND_ID'):
            self.run_adapter('--apply')

    def test_no_observations_in_amazon_fails_without_writes(self):
        self.config['project'].update(lat_range=[-22,11],lon_range=[-82,-35])
        self.write_config()
        with self.assertRaisesRegex(ValueError,'no ResOpsUS observations'):
            self.run_adapter('--apply')
        self.assertFalse(self.out.exists())

    def test_missing_month_and_units_are_not_silently_accepted(self):
        path = self.history/'case_hist_unitcat_2001-01.nc'
        original = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(ValueError,'missing monthly history'):
            self.run_adapter('--apply')
        path.write_bytes(original)
        def bad_unit(ds):
            ds.qresv_in.attrs['units'] = 'mm/day'
            return ds
        self.edit_month(bad_unit)
        with self.assertRaisesRegex(ValueError,'unsupported source units'):
            self.run_adapter('--apply')

    def test_ctl_omission_requires_explicit_simulation_selection(self):
        self.config['simulation']['E0_CTL'] = dict(model='CoLM2024',root_dir=str(self.root/'missing'),
                                                   data_type='grid',data_groupby='Month',
                                                   variables={'Dam_Inflow':dict(prefix='ctl_')})
        self.config['comparison'] = dict(enabled=True,items=['HeatMap'])
        self.write_config()
        with self.assertRaisesRegex(ValueError,'missing monthly history'):
            self.run_adapter('--apply')
        self.run_adapter('--simulation','E1_D','--apply')
        updated = yaml.safe_load(self.config_path.read_text())
        self.assertEqual(list(updated['simulation']),['E1_D'])
        self.assertEqual(updated['comparison'],{'enabled':False,'items':[]})

    def test_atomic_replace_failure_and_bad_time_leave_original(self):
        before = self.config_path.read_bytes()
        with patch.object(adapter.os,'replace',side_effect=OSError('injected failure')):
            with self.assertRaisesRegex(OSError,'injected failure'):
                self.run_adapter('--apply')
        self.assertEqual(self.config_path.read_bytes(),before)
        self.assertEqual(list(self.out.iterdir()),[])
        self.edit_month(lambda ds: ds.assign_coords(time=[ds.time.values[0],ds.time.values[0]]))
        with self.assertRaisesRegex(ValueError,'increasing and unique'):
            self.run_adapter('--apply')
        self.assertEqual(self.config_path.read_bytes(),before)
        self.assertEqual(list(self.out.iterdir()),[])

    def test_multiple_simulations_keep_comparison_and_common_station_ids(self):
        self.config['simulation']['E4_DL'] = dict(self.config['simulation']['E1_D'])
        self.config['comparison'] = dict(enabled=True,items=['HeatMap'])
        self.write_config()
        self.run_adapter('--apply')
        config = yaml.safe_load(self.config_path.read_text())
        self.assertEqual(list(config['simulation']),['E1_D','E4_DL'])
        self.assertEqual(config['comparison'],{'enabled':True,'items':['HeatMap']})
        for sim in config['simulation'].values():
            with Path(sim['fulllist']).open() as stream:
                self.assertEqual([row['ID'] for row in csv.DictReader(stream)],['101','202'])

    def test_changing_history_does_not_publish_partial_results(self):
        import os
        original_extract = adapter.extract
        before = self.config_path.read_bytes()
        def changing(paths, *args):
            result = original_extract(paths, *args)
            stat = paths[0].stat()
            os.utime(paths[0],ns=(stat.st_atime_ns,stat.st_mtime_ns+1_000_000_000))
            return result
        with patch.object(adapter,'extract',side_effect=changing):
            with self.assertRaisesRegex(ValueError,'history changed'):
                self.run_adapter('--apply')
        self.assertEqual(self.config_path.read_bytes(),before)
        self.assertEqual(list(self.out.iterdir()),[])


if __name__ == '__main__':
    unittest.main()
