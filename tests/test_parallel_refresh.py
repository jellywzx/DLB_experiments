"""Bounded concurrency and end-to-end refresh checks using small temporary files."""
from __future__ import annotations

import contextlib
import csv
import io
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

TOOLS=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(TOOLS))
import dlb_00_workflow_common as common
import dlb_06_stage_status as status
import dlb_07_refresh_experiment as refresh
import dlb_08_generate_openbench as generator
from dlb_parallel import ordered_checks
from test_openbench_templates import fixture


def slow_check(value):
    if value<0:raise ValueError('injected worker failure')
    started=time.monotonic()
    time.sleep(.1)
    return value,os.getpid(),started,time.monotonic()


class ParallelChecksTests(unittest.TestCase):
    def test_order_bound_overlap_and_spawn_isolation(self):
        for processes in (False,True):
            with self.subTest(processes=processes),contextlib.redirect_stderr(io.StringIO()):
                rows=ordered_checks(slow_check,range(12),4,'synthetic slow checks',processes=processes)
                self.assertEqual([row[0] for row in rows],list(range(12)))
                events=sorted([(row[2],1) for row in rows]+[(row[3],-1) for row in rows])
                active=maximum=0
                for _,delta in events:
                    active+=delta;maximum=max(maximum,active)
                self.assertGreater(maximum,1)
                self.assertLessEqual(maximum,4)
                if processes:
                    self.assertNotIn(os.getpid(),{row[1] for row in rows})
                    self.assertLessEqual(len({row[1] for row in rows}),4)

    def test_empty_single_serial_and_worker_failure(self):
        for processes in (False,True):
            with self.subTest(processes=processes),contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(ordered_checks(slow_check,[],4,'empty',processes=processes),[])
                # Only one task needs no executor even with a large requested count.
                with patch('dlb_parallel.ProcessPoolExecutor',side_effect=AssertionError('unexpected pool')):
                    self.assertEqual(ordered_checks(slow_check,[0],20,'one',processes=processes)[0][1],os.getpid())
                with self.assertRaisesRegex(ValueError,'injected worker failure'):
                    ordered_checks(slow_check,[-1,0],2,'failure',processes=processes)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(all(row[1]==os.getpid() for row in ordered_checks(slow_check,[0,1],1,'serial',processes=True)))

    def test_progress_is_visible_before_process_finishes(self):
        code="from dlb_parallel import timed_phase\nwith timed_phase('live'):\n input()\n"
        process=subprocess.Popen([sys.executable,'-c',code],cwd=TOOLS,stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            readable,_,_=select.select([process.stderr],[],[],10)
            self.assertTrue(readable,'start progress was buffered')
            self.assertIn('live: start',process.stderr.readline())
            self.assertIsNone(process.poll())
        finally:
            stdout,stderr=process.communicate('\n',timeout=10)
        self.assertEqual(process.returncode,0,stderr)
        self.assertEqual(stdout,'')
        self.assertIn('live: done',stderr)

    def test_cli_rejects_invalid_workers_and_documents_default(self):
        for script in ('dlb_06_stage_status.py','dlb_07_refresh_experiment.py','dlb_08_generate_openbench.py'):
            base=[sys.executable,str(TOOLS/script)]
            help_result=subprocess.run([*base,'--help'],capture_output=True,text=True)
            self.assertEqual(help_result.returncode,0,help_result.stderr)
            self.assertIn('--workers',help_result.stdout)
            self.assertIn('default: 4',help_result.stdout)
            args=['--nml-dir','/unused'] if script.startswith('dlb_06') else ['/unused']
            for workers in ('0','-2','abc'):
                result=subprocess.run([*base,*args,'--workers',workers],capture_output=True,text=True)
                self.assertEqual(result.returncode,2)
                self.assertIn('workers must',result.stderr)

    def test_checksum_pruning_preserves_inventory(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for name in ('keep','logs/a/b/skip','openbench_output/a/b/skip',
                         'provenance/backups/old/keep','nested/logs/skip','artifacts.yaml'):
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'x')
            expected=[p for p in sorted(root.rglob('*')) if p.is_file() and
                      p.name not in ('checksums.sha256','validation_report.txt','artifacts.yaml') and
                      'openbench_output' not in p.parts and 'logs' not in p.parts]
            walk=refresh.os.walk;visited=[]
            def tracked(*args,**kwargs):
                for entry in walk(*args,**kwargs):
                    visited.append(Path(entry[0]));yield entry
            with patch.object(refresh.os,'walk',side_effect=tracked):
                self.assertEqual(refresh.checksum_paths(root),expected)
            self.assertFalse(any('logs' in p.parts or 'openbench_output' in p.parts for p in visited))


class RefreshIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);fixture(self.root)
        self.configs=self.root/'config/colm'
        runs=[]
        for label in ('SPINUP','D_INIT',*common.EXPERIMENTS):
            dam,levee,bif=common.SWITCHES.get(label,(int(label=='D_INIT'),False,False))
            case='study_'+label;filename=case+'.nml'
            (self.configs/filename).write_text(
                f"DEF_CASE_NAME = '{case}'\nDEF_dir_output = '{self.root/'cases'}'\n"
                f"DEF_Reservoir_Method = {dam}\nDEF_USE_LEVEE = {'.true.' if levee else '.false.'}\n"
                f"DEF_USE_BIFURCATION = {'.true.' if bif else '.false.'}\n")
            runs.append({'label':label,'case':case,'nml_file':filename})
        with (self.configs/'experiments.csv').open('w') as stream:
            writer=csv.DictWriter(stream,fieldnames=['region','experiment','nml_file','case_name']);writer.writeheader()
            writer.writerows({'region':'Global','experiment':run['label'],'nml_file':run['nml_file'],
                             'case_name':run['case']} for run in runs[2:])
        colm={'schema_version':2,'draft':True,'paths':{'case_root':str(self.root/'cases')},
              'evaluation':{'start_year':2001,'end_year':2001},
              'runs':{'spinup':{'Global':runs[0]},'dam_init':{'Global':runs[1]},'experiments':{'Global':runs[2:]}}}
        (self.configs/'run_manifest.json').write_text(json.dumps(colm))
        self.study=common.load_study(self.configs,allow_draft=True,validate_schedule=False)
        with patch.object(sys,'argv',['generate',str(self.root),'--templates','streamflow','--no-refresh']), \
                contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            generator.main()

    def cli(self,script,*args):
        return subprocess.run([sys.executable,str(TOOLS/script),*map(str,args)],text=True,capture_output=True,timeout=40)

    def stage(self,workers):
        return self.cli('dlb_06_stage_status.py','--nml-dir',self.configs,'--allow-draft',
                        '--require-complete','--json','--workers',workers)

    def test_missing_restarts_history_and_no_dam_init_match(self):
        for with_dam in (True,False):
            if not with_dam:
                p=self.configs/'run_manifest.json';m=json.loads(p.read_text());m['runs'].pop('dam_init')
                p.write_text(json.dumps(m));(self.configs/'study_D_INIT.nml').unlink()
            serial=self.stage(1);parallel=self.stage(4)
            self.assertEqual(serial.returncode,1,serial.stderr)
            self.assertEqual(parallel.returncode,1,parallel.stderr)
            self.assertEqual(json.loads(serial.stdout),json.loads(parallel.stdout))
            self.assertIn('CoLM status',parallel.stderr)

    def populate_restarts(self):
        from netCDF4 import Dataset
        for run in (self.study.spinup,self.study.dam_init,*self.study.experiments):
            for year in ((2000,) if run.label in ('SPINUP','D_INIT') else (2000,2006)):
                directory=run.restart/common.stamp(year);directory.mkdir(parents=True)
                for prefix in ('','pft_'):
                    (directory/f'{run.case}_restart_{prefix}{common.stamp(year)}_lc.nc').write_bytes(b'fixture')
                with Dataset(directory/f'{run.case}_restart_gridriver_{common.stamp(year)}_lc.nc','w') as ds:
                    values={'gridriver_restart_schema':2,'gridriver_restart_complete':1,
                            'gridriver_restart_feature_levee':int(run.levee),
                            'gridriver_restart_feature_bifurcation':int(run.bif)}
                    for key in ('wdsrf_ucat','veloc_riv','acctime_rnof','acc_rnof_uc','volwater_ucat',
                                'volresv','gridriver_reservoir_identity','gridriver_ucatch_identity'):
                        values[key]=1
                    for key,value in values.items():ds.createVariable(key,'i4').assignValue(value)
            const=run.restart/'const';const.mkdir()
            for prefix in ('','pft_'):(const/f'{run.case}_restart_{prefix}const_lc.nc').write_bytes(b'fixture')
            (run.case_dir/'.aas_restart_handoff.json').write_text('{}')
            run.history.mkdir()
            for month in range(1,13):(run.history/f'{run.case}_hist_unitcat_2001-{month:02d}.nc').write_bytes(b'fixture')
            markers=self.root/'config/jobs/markers';markers.mkdir(parents=True,exist_ok=True)
            (markers/f'{run.label}.success').touch()

    def test_complete_and_corrupt_netcdf_have_identical_serial_parallel_status(self):
        self.populate_restarts()
        serial=self.stage(1);parallel=self.stage(4)
        self.assertEqual(serial.returncode,0,serial.stderr)
        self.assertEqual(parallel.returncode,0,parallel.stderr)
        self.assertEqual(json.loads(serial.stdout),json.loads(parallel.stdout))
        run=self.study.experiments[-1]
        (run.restart/common.stamp(2006)/f'{run.case}_restart_gridriver_{common.stamp(2006)}_lc.nc').write_bytes(b'broken')
        serial=self.stage(1);parallel=self.stage(4)
        self.assertEqual(serial.returncode,1,serial.stderr)
        self.assertEqual(parallel.returncode,1,parallel.stderr)
        self.assertEqual(json.loads(serial.stdout),json.loads(parallel.stdout))
        self.assertTrue(json.loads(parallel.stdout)['records'][-1]['experiments'][-1]['details'])

    def test_refresh_outputs_match_including_checksums_and_parameter_forwarding(self):
        class FixedDatetime:
            @staticmethod
            def now(tz):
                from datetime import datetime
                return datetime(2026,1,1,tzinfo=tz)
        snapshots=[]
        # Real dlb_06 subprocess; a fixed timestamp makes generated hashes comparable.
        for workers in (1,4):
            with patch.object(sys,'argv',['refresh',str(self.root),'--workers',str(workers)]), \
                    patch.object(refresh,'datetime',FixedDatetime), \
                    contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(refresh.main(),0)
            snapshots.append({name:(self.root/name).read_bytes() for name in
                              ('manifest.yaml','artifacts.yaml','checksums.sha256','validation_report.txt')})
        self.assertEqual(*snapshots)
        # Actual dlb_08 -> dlb_07 -> spawned dlb_06; check each layer's progress.
        result=self.cli('dlb_08_generate_openbench.py',self.root,'--templates','streamflow','--overwrite','--workers','2')
        self.assertEqual(result.returncode,0,result.stderr)
        for phase in ('Configuration backup','Experiment refresh','Checksum hashes','CoLM status [10 tasks, 2 workers]'):
            self.assertIn(phase,result.stderr)
        self.assertTrue(list((self.root/'provenance/backups').iterdir()))


if __name__=='__main__':unittest.main()
