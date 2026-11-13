"""Submission evidence tests. Every executable and result is a temporary fixture."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import dlb_openbench_state as state
import dlb_09_submit_openbench as submit
import dlb_07_refresh_experiment as refresh


class TaskStateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.config=self.root/'config/openbench/basin/streamflow_D.yaml'
        self.config.parent.mkdir(parents=True)
        self.config.write_text(f'project:\n  name: study\n  output_dir: {self.root}/openbench_output\nevaluation: {{variables: [Streamflow]}}\nreference: {{Streamflow: ref}}\nmetrics: [RMSE]\nscores: [Overall_Score]\nsimulation: {{}}\ncomparison: {{enabled: false}}\n')
        self.output=self.root/'openbench_output/study'
        self.exe=self.root/'fake-openbench'
        self.exe.write_text('#!'+sys.executable+'''\nimport os,sys,time
from pathlib import Path
mode=os.environ.get('DLB_TEST_MODE','')
if mode=='fail_'+sys.argv[1]:sys.exit(7)
if sys.argv[1]=='run':
    if mode=='edit':
        p=Path(sys.argv[2]);p.write_text(p.read_text()+'# changed during execution\\n')
    if mode=='wait':
        Path(os.environ['DLB_TEST_READY']).touch()
        while not Path(os.environ['DLB_TEST_RELEASE']).exists():time.sleep(.02)
''');self.exe.chmod(0o755)
        (self.root/'manifest.yaml').write_text(json.dumps({'name':'test','evaluation':{'projects':[
            {'project':'study','config':str(self.config.relative_to(self.root)),'treatment':'E1_D','status':'success'}]}}))
        colm=self.root/'config/colm';colm.mkdir();(colm/'run_manifest.json').write_text('{}')

    def status(self):return state.task_state('study',self.config,self.output)

    def run_job(self,**kwargs):
        return state.execute('study',self.config,self.output,state.sha256(self.config),self.exe,1,**kwargs)

    def generate(self,*args):
        with patch.object(sys,'argv',['submit',str(self.root),'-D','--openbench-exe',str(self.exe),*args]),contextlib.redirect_stdout(io.StringIO()):
            return submit.main()

    def refresh(self,*args,complete=False):
        stages={x:complete for x in ('spinup_complete','dam_init_complete','handoff_complete','colm_complete')}
        result=subprocess.CompletedProcess([],0,json.dumps(stages),'')
        with patch.object(sys,'argv',['refresh',str(self.root),*args]),patch.object(refresh.subprocess,'run',return_value=result),contextlib.redirect_stdout(io.StringIO()):
            refresh.main()
        return json.loads((self.root/'manifest.yaml').read_text())['evaluation']

    def test_first_success_and_changed_metrics_reference(self):
        self.assertEqual(self.status(),'not_run');self.generate()
        self.run_job();self.assertEqual(self.status(),'completed_unchanged')
        self.assertEqual(self.refresh()['projects'][0]['status'],'success')
        original=self.config.read_text()
        for text in (original.replace('[RMSE]','[RMSE, KGE]'),original.replace('Streamflow: ref','Streamflow: new_ref')):
            self.config.write_text(text);self.assertEqual(self.status(),'config_changed')
            self.generate('--overwrite')
            evaluation=self.refresh();self.assertEqual(evaluation['projects'][0]['task_state'],'config_changed')
            self.assertEqual(evaluation['summary']['success'],0)
            with self.assertRaisesRegex(ValueError,'cannot mark success'):
                self.refresh('--openbench-result','study=success')
        self.assertEqual(self.run_job(),0);self.assertEqual(self.status(),'completed_unchanged')

    def test_failed_check_or_run_retries_without_deleting_results(self):
        for mode in ('fail_check','fail_run'):
            with self.subTest(mode=mode):
                with patch.dict(os.environ,DLB_TEST_MODE=mode):
                    with self.assertRaises(ValueError):self.run_job()
                self.assertEqual(self.status(),'failed')
                (self.output/'partial.nc').write_bytes(b'keep')
                self.generate('--overwrite');self.assertEqual(self.run_job(),0)
                self.assertEqual((self.output/'partial.nc').read_bytes(),b'keep')
                # A changed configuration starts the next attempt.
                self.config.write_text(self.config.read_text()+'# next attempt\n')

    def test_legacy_results_are_unverified_and_require_explicit_rerun(self):
        self.output.mkdir(parents=True)
        for file in ('success.marker','.openbench_success','partial.nc'):(self.output/file).write_bytes(b'keep')
        self.assertEqual(self.status(),'unverified')
        self.assertEqual(self.refresh()['projects'][0]['status'],'unverified')
        self.assertEqual(self.run_job(),2)
        self.generate('--rerun-unverified')
        self.assertEqual(self.run_job(rerun_unverified=True),0)
        for file in ('success.marker','.openbench_success','partial.nc'):
            self.assertEqual((self.output/file).read_bytes(),b'keep')

    def test_config_change_during_execution_never_records_success(self):
        with patch.dict(os.environ,DLB_TEST_MODE='edit'):
            with self.assertRaisesRegex(ValueError,'changed during execution'):self.run_job()
        self.assertEqual(self.status(),'failed')
        self.assertNotIn('success_at',json.loads((self.output/state.RECORD).read_text()))

    def test_queued_config_and_mapping_changes_reject_old_job(self):
        self.generate()
        job=self.root/'config/openbench_jobs/submit_openbench_D_array.lsf'
        text=self.config.read_text();self.config.write_text(text+'# queued edit\n')
        env={**os.environ,'LSB_JOBINDEX':'1'}
        result=subprocess.run(['bash',str(job)],env=env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0);self.assertIn('stale config snapshot',result.stderr)
        self.assertFalse(self.output.exists())
        with self.assertRaisesRegex(ValueError,'stale job snapshot'):self.generate('--submit')
        self.generate('--overwrite');old=self.root/'old.lsf';old.write_text(job.read_text())
        self.config.write_text(self.config.read_text()+'# another edit\n');self.generate('--overwrite')
        result=subprocess.run(['bash',str(old)],env=env,capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0);self.assertIn('stale mapping snapshot',result.stderr)

    def test_concurrent_jobs_and_stale_running_record(self):
        self.generate();job=self.root/'config/openbench_jobs/submit_openbench_D_array.lsf'
        ready=self.root/'ready';release=self.root/'release'
        env={**os.environ,'LSB_JOBINDEX':'1','DLB_TEST_MODE':'wait','DLB_TEST_READY':str(ready),'DLB_TEST_RELEASE':str(release)}
        proc=subprocess.Popen(['bash',str(job)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+10
            while not ready.exists() and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(ready.exists());self.assertEqual(self.status(),'running')
            duplicate=subprocess.run(['bash',str(job)],env=env,capture_output=True,text=True,timeout=5)
            self.assertEqual(duplicate.returncode,0);self.assertIn('SKIP running',duplicate.stdout)
        finally:
            release.touch();stdout,stderr=proc.communicate(timeout=10)
        self.assertEqual(proc.returncode,0,stderr)
        self.assertEqual(self.status(),'completed_unchanged')
        record=json.loads((self.output/state.RECORD).read_text());record['status']='running'
        state.atomic_json(self.output/state.RECORD,record)
        self.assertEqual(self.status(),'failed')

    def test_refresh_completion_requires_evidence_and_manual_cannot_upgrade(self):
        self.run_job();self.refresh(complete=True)
        self.assertEqual(json.loads((self.root/'manifest.yaml').read_text())['status'],'openbench_complete')
        self.refresh('--openbench-result','study=failed',complete=True)
        self.assertEqual(json.loads((self.root/'manifest.yaml').read_text())['status'],'colm_complete')
        (self.output/state.RECORD).unlink();(self.output/'success.marker').touch()
        self.refresh(complete=True)
        self.assertEqual(json.loads((self.root/'manifest.yaml').read_text())['status'],'colm_complete')
        with self.assertRaisesRegex(ValueError,'cannot mark success'):
            self.refresh('--openbench-result','study=success',complete=True)

    def test_pending_changes_and_tampered_jobs_invalidate_snapshot(self):
        self.generate();job=self.root/'config/openbench_jobs/submit_openbench_D_array.lsf'
        job.write_text(job.read_text()+'# tampered\n')
        with self.assertRaisesRegex(ValueError,'stale job snapshot'):self.generate('--submit')
        self.generate('--overwrite')
        # Old-format job files without a sidecar are never trusted.
        (self.root/'config/openbench_jobs/openbench_D_snapshot.json').unlink()
        with self.assertRaisesRegex(ValueError,'missing/invalid job snapshot'):self.generate('--submit')

    def test_variable_template_output_can_run_and_track_changes(self):
        from test_openbench_templates import fixture,generate
        root=self.root/'template-experiment';root.mkdir();fixture(root);generate(root)
        config=root/'config/openbench/global/inundation_area_comparisons.yaml'
        project='study_global_inundation_area_comparisons';output=root/'openbench_output'/project
        self.assertEqual(state.task_state(project,config,output),'not_run')
        self.assertEqual(state.execute(project,config,output,state.sha256(config),self.exe,1),0)
        self.assertEqual(state.task_state(project,config,output),'completed_unchanged')
        config.write_text(config.read_text().replace('- RMSE','- RMSE\n- MAE'))
        self.assertEqual(state.task_state(project,config,output),'config_changed')

    def test_corrupt_record_is_not_trusted(self):
        self.output.mkdir(parents=True);(self.output/state.RECORD).write_text('{broken')
        self.assertEqual(self.status(),'unverified')


if __name__=='__main__':unittest.main()
