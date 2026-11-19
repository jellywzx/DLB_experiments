#!/usr/bin/env python3
from __future__ import annotations

import json
import fcntl
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


TOOLS=Path(__file__).resolve().parents[1]
SCRIPT=TOOLS/'dlb_09_submit_openbench.py'
GENERATOR=TOOLS/'dlb_08_generate_openbench.py'
PYTHON=Path(sys.executable)
TREATMENTS=('E1_D','E2_L','E3_B','E4_DL','E5_DB','E6_LB','E7_ALL')
SHORT={'E1_D':'D','E2_L':'L','E3_B':'B','E4_DL':'DL','E5_DB':'DB','E6_LB':'LB','E7_ALL':'DLB'}


def fixture(root:Path)->Path:
    configs=root/'config/openbench/basin';configs.mkdir(parents=True)
    outputs=root/'openbench_output';projects=[]
    for treatment in TREATMENTS:
        for kind in ('streamflow','inundation_fraction','inundation_area'):
            short=SHORT[treatment];name=f'study_basin_{kind}_{short}'
            config=configs/f'{kind}_{short}.yaml'
            config.write_text(f'project:\n  name: {name}\n  output_dir: {outputs}\n')
            projects.append({'project':name,'config':str(config.relative_to(root)),
                             'treatment':treatment,'status':'missing'})
    for treatment in ('spinup','ctl','comparisons'):
        for kind in ('streamflow','inundation_fraction','inundation_area'):
            name=f'study_basin_{kind}_{treatment}'
            config=configs/f'{kind}_{treatment}.yaml'
            config.write_text(f'project:\n  name: {name}\n  output_dir: {outputs}\n')
            projects.append({'project':name,'config':str(config.relative_to(root)),
                             'treatment':treatment,'status':'missing'})
    (root/'manifest.yaml').write_text(json.dumps({'evaluation':{'projects':projects}}))
    executable=root/'openbench'
    executable.write_text('#!/usr/bin/env bash\necho "$@" >> "$OPENBENCH_CALLS"\n')
    executable.chmod(0o755)
    return executable


def run(root:Path,executable:Path,*args:str,ok:bool=True,env:dict|None=None):
    result=subprocess.run([str(PYTHON),str(SCRIPT),str(root),*args,
                           '--openbench-exe',str(executable)],text=True,capture_output=True,env=env)
    if ok and result.returncode:raise AssertionError(result.stderr or result.stdout)
    return result


class OpenBenchJobTests(unittest.TestCase):
    def test_usa_uses_standard_basin_masks(self):
        from dlb_08_generate_openbench import BASINS,MASK_ROOT
        masks=[mask.relative_to(MASK_ROOT).as_posix() for _,_,_,mask in BASINS['USA']]
        self.assertEqual(masks,[
            'basin_masks_06min/missouri/missouri_basin.nc',
            'basin_masks_06min/lower_mississippi/lower_mississippi_basin.nc'])

    def test_generator_uses_output_root_without_project_name(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);colm=root/'config/colm/run_manifest.json'
            colm.parent.mkdir(parents=True)
            experiments=[{'label':label,'case':f'study_{label}'}
                         for label in ('E0_CTL',*TREATMENTS)]
            colm.write_text(json.dumps({'paths':{'case_root':str(root/'cases')},'draft':False,
                'runs':{'spinup':{'Global':{'case':'study_spinup'}},
                        'experiments':{'Global':experiments}}}))
            (root/'manifest.yaml').write_text(json.dumps({'region':'Global','name':'study',
                'configuration':{'colm':str(colm.relative_to(root))},
                'evaluation':{'start_year':2001,'end_year':2005}}))
            result=subprocess.run([str(PYTHON),str(GENERATOR),str(root),'--no-refresh'],
                                  text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            config=root/'config/openbench/global/streamflow_D.yaml'
            self.assertEqual(yaml.safe_load(config.read_text())['project']['output_dir'],
                             str(root/'openbench_output'))

    def test_openbench_is_detected_from_active_path(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root);env=os.environ.copy()
            env['PATH']=str(root)+os.pathsep+env.get('PATH','')
            result=subprocess.run([str(PYTHON),str(SCRIPT),str(root),'-D'],
                                  text=True,capture_output=True,env=env)
            self.assertEqual(result.returncode,0,result.stderr)
            job=(root/'config/openbench_jobs/submit_openbench_D_array.lsf').read_text()
            self.assertIn(f'OPENBENCH={executable.resolve()}',job)
            self.assertIn('#BSUB -J "openbench_D[1-3]%20"',job)

    def test_each_selector_maps_to_one_exact_treatment(self):
        expected={('-D',):'E1_D',('-L',):'E2_L',('-B',):'E3_B',
                  ('-DL',):'E4_DL',('-DB',):'E5_DB',
                  ('-LB',):'E6_LB',('-DLB',):'E7_ALL'}
        for flags,treatment in expected.items():
            with self.subTest(flags=flags),tempfile.TemporaryDirectory() as td:
                root=Path(td);executable=fixture(root);run(root,executable,*flags)
                label=SHORT[treatment]
                mapping=(root/f'config/openbench_jobs/openbench_{label}_projects.tsv').read_text()
                self.assertEqual(mapping.count('\n'),4)
                self.assertIn(f'_{label}.yaml',mapping)
                for other in set(SHORT.values())-{label}:
                    self.assertNotIn(f'_{other}.yaml',mapping)

    def test_separate_flags_select_multiple_individual_experiments(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root)
            result=run(root,executable,'-L','-B')
            self.assertIn('Selection L_B: selected=6 pending=6 skipped=0',result.stdout)
            mapping=(root/'config/openbench_jobs/openbench_L_B_projects.tsv').read_text()
            self.assertEqual(mapping.count('\n'),7)
            self.assertIn('_L.yaml',mapping)
            self.assertIn('_B.yaml',mapping)
            self.assertNotIn('_LB.yaml',mapping)
            self.assertIn(str(root/'openbench_output/study_basin_streamflow_L'),mapping)

    def test_factor_combination_is_exact_and_existing_output_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root)
            existing=root/'openbench_output/study_basin_streamflow_DL'
            existing.mkdir(parents=True);(existing/'result.nc').write_bytes(b'result')
            result=run(root,executable,'-DL','--max-concurrent','3')
            self.assertIn('selected=3 pending=2 skipped=1',result.stdout)
            mapping=(root/'config/openbench_jobs/openbench_DL_projects.tsv').read_text()
            self.assertIn('inundation_fraction_DL.yaml',mapping)
            self.assertIn('inundation_area_DL.yaml',mapping)
            self.assertNotIn('streamflow_DL.yaml',mapping)
            job=root/'config/openbench_jobs/submit_openbench_DL_array.lsf'
            text=job.read_text()
            self.assertIn('#BSUB -J "openbench_DL[1-2]%3"',text)
            self.assertIn('#BSUB -R "span[hosts=1]"',text)
            self.assertIn('#BSUB -n 48',text)
            self.assertNotIn('#BSUB -x',text)
            subprocess.run(['bash','-n',str(job)],check=True)

    def test_runtime_recheck_does_not_overwrite_new_result(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root);run(root,executable,'-B')
            mapping=(root/'config/openbench_jobs/openbench_B_projects.tsv').read_text().splitlines()
            output=Path(mapping[1].split('\t')[3]);output.mkdir(parents=True);(output/'new.nc').write_bytes(b'x')
            calls=root/'calls.txt';env=os.environ.copy();env.update(LSB_JOBINDEX='1',OPENBENCH_CALLS=str(calls))
            job=root/'config/openbench_jobs/submit_openbench_B_array.lsf'
            result=subprocess.run(['bash',str(job)],text=True,capture_output=True,env=env)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('SKIP existing result',result.stdout)
            self.assertFalse(calls.exists())
            self.assertEqual((output/'new.nc').read_bytes(),b'x')

    def test_all_selection_and_missing_selection_error(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root)
            result=run(root,executable,ok=False)
            self.assertEqual(result.returncode,2)
            self.assertIn('select --all',result.stderr)
            result=run(root,executable,'--all')
            self.assertIn('selected=30 pending=30 skipped=0',result.stdout)

    def test_comparisons_selection_and_combined_selector(self):
        for flags,label,count in ((('--comparisons',),'comparisons',3),
                                  (('-D','--comparisons'),'D_comparisons',6)):
            with self.subTest(flags=flags),tempfile.TemporaryDirectory() as td:
                root=Path(td);executable=fixture(root)
                result=run(root,executable,*flags)
                self.assertIn(f'selected={count} pending={count} skipped=0',result.stdout)
                rows=(root/f'config/openbench_jobs/openbench_{label}_projects.tsv').read_text().splitlines()[1:]
                self.assertEqual(len(rows),count)
                self.assertEqual(sum(row.split('\t')[1].endswith('_comparisons') for row in rows),3)
                job=root/f'config/openbench_jobs/submit_openbench_{label}_array.lsf'
                subprocess.run(['bash','-n',str(job)],check=True)

    def test_all_rejects_comparisons_and_experiment_selectors(self):
        for flags in (('--all','--comparisons'),('--all','-D'),('--all','-D','--comparisons')):
            with self.subTest(flags=flags),tempfile.TemporaryDirectory() as td:
                root=Path(td);executable=fixture(root)
                result=run(root,executable,*flags,ok=False)
                self.assertEqual(result.returncode,2)
                self.assertIn('--all cannot be combined',result.stderr)
                self.assertFalse((root/'config/openbench_jobs').exists())

    def test_comparisons_nonempty_output_is_protected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root)
            output=root/'openbench_output/study_basin_streamflow_comparisons'
            output.mkdir(parents=True);(output/'result.nc').write_bytes(b'protected')
            result=run(root,executable,'--comparisons')
            self.assertIn('selected=3 pending=2 skipped=1',result.stdout)
            mapping=(root/'config/openbench_jobs/openbench_comparisons_projects.tsv').read_text()
            self.assertNotIn('streamflow_comparisons.yaml',mapping)
            self.assertEqual((output/'result.nc').read_bytes(),b'protected')

    def test_submit_uses_bsub_stdin(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root);bin_dir=root/'bin';bin_dir.mkdir()
            capture=root/'submitted.lsf';bsub=bin_dir/'bsub'
            bsub.write_text('#!/usr/bin/env bash\ncat > "$BSUB_CAPTURE"\necho "Job <123>"\n')
            bsub.chmod(0o755);env=os.environ.copy()
            env.update(PATH=str(bin_dir)+os.pathsep+env.get('PATH',''),BSUB_CAPTURE=str(capture))
            result=run(root,executable,'-D','--submit',env=env)
            self.assertIn('Job <123>',result.stdout)
            self.assertIn('openbench_D[1-3]%20',capture.read_text())

    def test_repeated_dry_run_reuses_snapshot_without_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root)
            run(root,executable,'--comparisons')
            folder=root/'config/openbench_jobs'
            before={p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in folder.iterdir()}
            result=run(root,executable,'--comparisons')
            self.assertIn('Reusing generated job snapshot',result.stdout)
            self.assertIn('NOT SUBMITTED: bsub',result.stdout)
            self.assertEqual(before,{p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in folder.iterdir()})

    def test_submit_after_dry_run_reuses_snapshot_without_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root);run(root,executable,'-L','-B')
            job=root/'config/openbench_jobs/submit_openbench_L_B_array.lsf'
            before=job.read_text();bin_dir=root/'bin';bin_dir.mkdir();capture=root/'submitted.lsf'
            bsub=bin_dir/'bsub';bsub.write_text(
                '#!/usr/bin/env bash\ncat > "$BSUB_CAPTURE"\necho "Job <456>"\n')
            bsub.chmod(0o755);env=os.environ.copy()
            env.update(PATH=str(bin_dir)+os.pathsep+env.get('PATH',''),BSUB_CAPTURE=str(capture))
            result=run(root,executable,'-L','-B','--cores','9','--submit',env=env)
            self.assertIn('Reusing generated job snapshot',result.stdout)
            self.assertEqual(job.read_text(),before)
            self.assertIn('--cores 4',capture.read_text())
            self.assertNotIn('--cores 9',capture.read_text())


class SubmissionEntryTests(unittest.TestCase):
    def entry(self,root,label,*args,env=None):
        return subprocess.run(['bash',str(root/f'config/openbench_jobs/submit_openbench_{label}.sh'),
                               *map(str,args)],text=True,capture_output=True,env=env,cwd='/tmp')

    def test_all_entries_select_exact_groups_and_forward_resources(self):
        from dlb_09_submit_openbench import generate_submit_entries
        with tempfile.TemporaryDirectory(prefix='openbench entries ') as td:
            root=Path(td);executable=fixture(root)
            # Entry generation does not need OpenBench in PATH.
            entries=generate_submit_entries(root)
            self.assertEqual(len(entries),11)
            for entry in entries:
                subprocess.run(['bash','-n',str(entry)],check=True)
            for label in (*SHORT.values(),'ctl','spinup','comparisons','all'):
                with self.subTest(label=label):
                    result=self.entry(root,label,'--openbench-exe',executable,
                                      '--cores=2','--max-concurrent','3')
                    self.assertEqual(result.returncode,0,result.stderr)
                    count=30 if label=='all' else 3
                    self.assertIn(f'selected={count} pending={count}',result.stdout)
                    folder=root/'config/openbench_jobs'
                    rows=(folder/f'openbench_{label}_projects.tsv').read_text().splitlines()[1:]
                    self.assertEqual(len(rows),count)
                    if label!='all':
                        self.assertTrue(all(row.split('\t')[1].endswith('_'+label) for row in rows))
                    job=(folder/f'submit_openbench_{label}_array.lsf').read_text()
                    self.assertIn(f'[1-{count}]%3',job)
                    self.assertIn('--cores 2',job)

    def test_default_preview_and_all_submit_use_one_array(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root)
            run(root,executable,'-D')
            capture=root/'submitted';bin_dir=root/'bin';bin_dir.mkdir()
            bsub=bin_dir/'bsub'
            bsub.write_text('#!/usr/bin/env bash\ncat >> "$BSUB_CAPTURE"\necho "Job <123>"\n')
            bsub.chmod(0o755);env=os.environ.copy()
            env.update(PATH=str(bin_dir)+os.pathsep+env.get('PATH',''),BSUB_CAPTURE=str(capture))
            result=self.entry(root,'all','--openbench-exe',executable,env=env)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('NOT SUBMITTED',result.stdout)
            self.assertFalse(capture.exists())
            result=self.entry(root,'all','--openbench-exe',executable,'--submit',env=env)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('Reusing generated job snapshot',result.stdout)
            self.assertEqual(capture.read_text().count('#BSUB -J'),1)
            self.assertIn('openbench_all[1-30]',capture.read_text())
            self.assertFalse((root/'openbench_output').exists())

    def test_entry_selection_and_submit_mode_cannot_be_overridden(self):
        from dlb_09_submit_openbench import generate_submit_entries
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root);generate_submit_entries(root)
            for args in (('-L',),('--all',),('--comparisons',),('--spinup',),
                         ('--ctl',),('--al',),('--queue',),('--submit','--dry-run')):
                with self.subTest(args=args):
                    result=self.entry(root,'D','--openbench-exe',executable,*args)
                    self.assertEqual(result.returncode,2,result.stderr)
            self.assertFalse(list((root/'config/openbench_jobs').glob('*.lsf')))

    def test_existing_entries_are_preserved_and_conflicts_require_overwrite(self):
        from dlb_09_submit_openbench import generate_submit_entries
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture(root);entries=generate_submit_entries(root)
            before={p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in entries}
            generate_submit_entries(root)
            self.assertEqual(before,{p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in entries})
            entry=entries[-1];entry.write_text('# custom entry\n')
            entries[0].unlink()
            with self.assertRaisesRegex(ValueError,'use --overwrite'):generate_submit_entries(root)
            self.assertFalse(entries[0].exists(),'conflict check must precede writes')
            self.assertEqual(entry.read_text(),'# custom entry\n')
            generate_submit_entries(root,overwrite=True)
            self.assertEqual(entry.read_bytes(),before[entry.name][0])
            entry.unlink();target=root/'external';target.write_text('keep')
            entry.symlink_to(target)
            with self.assertRaisesRegex(ValueError,'unsafe'):generate_submit_entries(root,overwrite=True)
            self.assertEqual(target.read_text(),'keep')

    def test_no_pending_projects_still_generate_entries_without_openbench(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture(root)
            result=run(root,root/'missing-openbench','--all','--retry-failed')
            self.assertIn('No jobs to generate or submit',result.stdout)
            self.assertEqual(len(list((root/'config/openbench_jobs').glob('*.sh'))),11)
            self.assertFalse(list((root/'config/openbench_jobs').glob('*.lsf')))

    def test_all_existing_results_still_generate_entries_without_array(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture(root)
            projects=json.loads((root/'manifest.yaml').read_text())['evaluation']['projects']
            for project in projects:
                output=root/'openbench_output'/project['project']
                output.mkdir(parents=True);(output/'result.nc').write_bytes(b'keep')
            result=run(root,root/'missing-openbench','--all')
            self.assertIn('selected=30 pending=0 skipped=30',result.stdout)
            self.assertEqual(len(list((root/'config/openbench_jobs').glob('*.sh'))),11)
            self.assertFalse(list((root/'config/openbench_jobs').glob('*.lsf')))
            for project in projects:
                self.assertEqual((root/'openbench_output'/project['project']/'result.nc').read_bytes(),b'keep')

    def test_custom_job_directory_does_not_change_entry_location(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root)
            run(root,executable,'-D','--out-dir',str(root/'custom_jobs'))
            self.assertTrue((root/'custom_jobs/submit_openbench_D_array.lsf').is_file())
            self.assertTrue((root/'config/openbench_jobs/submit_openbench_all.sh').is_file())

    def test_retry_entry_preserves_existing_results_during_preview(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root)
            output=root/'openbench_output/study_basin_streamflow_D'
            output.mkdir(parents=True);(output/'.openbench_failed').touch()
            (output/'old.nc').write_bytes(b'original')
            run(root,executable,'-D')
            result=self.entry(root,'D','--openbench-exe',executable,'--retry-failed')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('pending=1 skipped=2',result.stdout)
            self.assertTrue((root/'config/openbench_jobs/submit_openbench_D_retry_failed_array.lsf').is_file())
            self.assertEqual((output/'old.nc').read_bytes(),b'original')
            self.assertFalse((output.parent/'.dlb_openbench_backups').exists())


class OverwriteTests(unittest.TestCase):
    def execute(self,root,output,index=1):
        env=os.environ.copy()
        env.update(LSB_JOBINDEX=str(index),OPENBENCH_CALLS=str(root/'calls'),TEST_OUTPUT=str(output))
        job=root/'config/openbench_jobs/submit_openbench_D_overwrite_array.lsf'
        subprocess.run(['bash','-n',str(job)],check=True)
        return subprocess.run(['bash',str(job)],env=env,text=True,capture_output=True)

    def test_default_explains_how_to_rerun_and_preview_preserves_results(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root)
            output=root/'openbench_output/study_basin_streamflow_D'
            output.mkdir(parents=True);(output/'old.nc').write_bytes(b'old')
            result=run(root,executable,'-D')
            self.assertIn('non-empty output; use --overwrite to rerun',result.stdout)
            self.assertIn('pending=2 skipped=1',result.stdout)
            result=run(root,executable,'-D','--overwrite','--dry-run')
            self.assertIn('OVERWRITE non-empty output directory',result.stdout)
            self.assertIn('pending=3 skipped=0',result.stdout)
            self.assertEqual((output/'old.nc').read_bytes(),b'old')
            self.assertFalse((output.parent/'.dlb_openbench_backups').exists())
            self.assertFalse((root/'calls').exists())

    def test_runtime_archives_success_failed_and_unknown_and_does_not_repeat(self):
        for marker in ('.openbench_success','.openbench_failed',None):
            with self.subTest(marker=marker),tempfile.TemporaryDirectory() as td:
                root=Path(td);executable=fixture(root)
                output=root/'openbench_output/study_basin_streamflow_D'
                output.mkdir(parents=True);(output/'old.nc').write_bytes(b'old')
                if marker:(output/marker).touch()
                run(root,executable,'-D','--overwrite')
                result=self.execute(root,output)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertTrue((output/'.openbench_success').is_file())
                self.assertFalse((output/'old.nc').exists())
                backups=list((output.parent/'.dlb_openbench_backups').iterdir())
                self.assertEqual(len(backups),1)
                self.assertEqual((backups[0]/'old.nc').read_bytes(),b'old')
                calls=(root/'calls').read_text()
                self.assertIn('check ',calls);self.assertIn('run ',calls)
                self.assertEqual(self.execute(root,output).returncode,0)
                self.assertEqual((root/'calls').read_text(),calls)

    def test_missing_and_empty_outputs_can_run(self):
        for exists in (False,True):
            with self.subTest(exists=exists),tempfile.TemporaryDirectory() as td:
                root=Path(td);executable=fixture(root)
                output=root/'openbench_output/study_basin_streamflow_D'
                if exists:output.mkdir(parents=True)
                run(root,executable,'-D','--overwrite')
                result=self.execute(root,output)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertTrue((output/'.openbench_success').is_file())

    def test_running_is_skipped_at_selection(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root)
            output=root/'openbench_output/study_basin_streamflow_D'
            output.mkdir(parents=True);(output/'.dlb_openbench_running').touch()
            result=run(root,executable,'-D','--overwrite')
            self.assertIn('SKIP running result',result.stdout)
            self.assertIn('pending=2 skipped=1',result.stdout)
            mapping=next((root/'config/openbench_jobs').glob('*overwrite*_projects.tsv'))
            self.assertNotIn(str(output),mapping.read_text())

    def test_runtime_rechecks_running_changed_output_and_lock(self):
        for change in ('running','changed','lock','created'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as td:
                root=Path(td);executable=fixture(root)
                output=root/'openbench_output/study_basin_streamflow_D'
                if change!='created':
                    output.mkdir(parents=True);(output/'old.nc').write_bytes(b'old')
                run(root,executable,'-D','--overwrite')
                output.parent.mkdir(parents=True,exist_ok=True)
                with (output.parent/f'.{output.name}.retry.lock').open('a') as lock:
                    if change=='lock':fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    elif change=='created':output.mkdir();(output/'new.nc').touch()
                    elif change=='running':(output/'.dlb_openbench_running').touch()
                    else:(output/'new.nc').touch()
                    result=self.execute(root,output)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertFalse((root/'calls').exists())
                self.assertFalse((output.parent/'.dlb_openbench_backups').exists())

    def test_overwrite_mapping_is_immutable_and_normal_job_stays_separate(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root)
            run(root,executable,'-D')
            folder=root/'config/openbench_jobs'
            normal=folder/'submit_openbench_D_array.lsf';before=normal.read_bytes()
            run(root,executable,'-D','--overwrite')
            mapping=next(folder.glob('*overwrite*_projects.tsv'));before_mapping=mapping.read_bytes()
            run(root,executable,'-D','--overwrite')
            self.assertEqual(mapping.read_bytes(),before_mapping)
            self.assertEqual(len(list(folder.glob('*overwrite*_projects.tsv'))),2)
            self.assertEqual(normal.read_bytes(),before)

    def test_overwrite_submit_forwards_rerun_job_without_moving_results(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root)
            output=root/'openbench_output/study_basin_streamflow_D'
            output.mkdir(parents=True);(output/'old.nc').write_bytes(b'old')
            bin_dir=root/'bin';bin_dir.mkdir();capture=root/'submitted'
            bsub=bin_dir/'bsub'
            bsub.write_text('#!/usr/bin/env bash\ncat > "$BSUB_CAPTURE"\necho "Job <789>"\n')
            bsub.chmod(0o755);env=os.environ.copy()
            env.update(PATH=str(bin_dir)+os.pathsep+env.get('PATH',''),BSUB_CAPTURE=str(capture))
            result=run(root,executable,'-D','--overwrite','--submit',env=env)
            self.assertIn('Job <789>',result.stdout)
            self.assertIn('--_overwrite-task',capture.read_text())
            self.assertIn('openbench_D_overwrite[1-3]',capture.read_text())
            self.assertEqual((output/'old.nc').read_bytes(),b'old')
            self.assertFalse((output.parent/'.dlb_openbench_backups').exists())

    def test_unsafe_output_is_rejected(self):
        for symlink in (False,True):
            with self.subTest(symlink=symlink),tempfile.TemporaryDirectory() as td:
                root=Path(td);executable=fixture(root)
                output=root/'openbench_output/study_basin_streamflow_D'
                output.parent.mkdir()
                if symlink:output.symlink_to(root/'missing',target_is_directory=True)
                else:output.write_bytes(b'keep')
                result=run(root,executable,'-D','--overwrite',ok=False)
                self.assertEqual(result.returncode,2)
                self.assertIn('unsafe overwrite output',result.stderr)
                self.assertFalse((root/'calls').exists())


class RetryFailedTests(unittest.TestCase):
    def prepare(self,root):
        executable=fixture(root)
        output=root/'openbench_output/study_basin_streamflow_D'
        output.mkdir(parents=True)
        (output/'.openbench_failed').touch()
        (output/'old.nc').write_bytes(b'original data')
        return executable,output

    def execute(self,root,output,**updates):
        env=os.environ.copy()
        env.update(LSB_JOBINDEX='1',OPENBENCH_CALLS=str(root/'calls'),TEST_OUTPUT=str(output))
        env.update(updates)
        job=root/'config/openbench_jobs/submit_openbench_D_retry_failed_array.lsf'
        subprocess.run(['bash','-n',str(job)],check=True)
        return subprocess.run(['bash',str(job)],env=env,text=True,capture_output=True)

    def test_classification(self):
        from dlb_09_submit_openbench import result_state
        for marker in ('.openbench_failed','failed.marker'):
            with self.subTest(marker=marker),tempfile.TemporaryDirectory() as td:
                path=Path(td);(path/marker).touch()
                self.assertEqual(result_state(path),'failed')
                (path/'.openbench_success').touch()
                self.assertEqual(result_state(path),'conflicting')
                (path/'run.log').write_text('All phases complete: 2 evaluated, 1 errors\n')
                self.assertEqual(result_state(path),'failed')
                (path/'.dlb_openbench_running').touch()
                self.assertEqual(result_state(path),'running')

    def test_preview_filters_and_preserves_results(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable,output=self.prepare(root)
            other=root/'openbench_output/study_basin_inundation_fraction_D';other.mkdir()
            (other/'.openbench_success').touch()
            result=run(root,executable,'-D','--retry-failed')
            self.assertIn('pending=1 skipped=2',result.stdout)
            self.assertIn('NOT SUBMITTED',result.stdout)
            self.assertEqual((output/'old.nc').read_bytes(),b'original data')
            self.assertFalse((output.parent/'.dlb_openbench_backups').exists())
            mapping=next((root/'config/openbench_jobs').glob('*retry_failed*_projects.tsv'))
            self.assertNotIn('inundation_fraction_D.yaml',mapping.read_text())
            self.assertNotIn('inundation_area_D.yaml',mapping.read_text())

    def test_manifest_alone_and_running_are_not_retried(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable,output=self.prepare(root)
            (output/'.dlb_openbench_running').touch()
            manifest=json.loads((root/'manifest.yaml').read_text())
            for project in manifest['evaluation']['projects']:project['status']='failed'
            (root/'manifest.yaml').write_text(json.dumps(manifest))
            result=run(root,executable,'--all','--retry-failed')
            self.assertIn('pending=0',result.stdout)
            self.assertEqual(len(list((root/'config/openbench_jobs').glob('*.sh'))),11)
            self.assertFalse(list((root/'config/openbench_jobs').glob('*.lsf')))

    def test_runtime_backup_success_and_duplicate_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable,output=self.prepare(root)
            run(root,executable,'-D','--retry-failed')
            result=self.execute(root,output)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue((output/'.openbench_success').is_file())
            backups=list((output.parent/'.dlb_openbench_backups').iterdir())
            self.assertEqual(len(backups),1)
            self.assertEqual((backups[0]/'old.nc').read_bytes(),b'original data')
            self.assertFalse((output/'old.nc').exists())
            calls=(root/'calls').read_text()
            self.assertEqual(self.execute(root,output).returncode,0)
            self.assertEqual((root/'calls').read_text(),calls)

    def test_runtime_detects_zero_exit_with_errors_and_does_not_repeat(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable,output=self.prepare(root)
            executable.write_text('#!/usr/bin/env bash\n'
                'echo "$@" >> "$OPENBENCH_CALLS"\n'
                'if [[ "$1" == run ]]; then\n'
                '  echo "All phases complete: 2 evaluated, 1 errors" > "$TEST_OUTPUT/run.log"\n'
                'fi\n')
            run(root,executable,'-D','--retry-failed')
            self.assertNotEqual(self.execute(root,output).returncode,0)
            self.assertTrue((output/'.openbench_failed').is_file())
            calls=(root/'calls').read_text()
            self.assertEqual(self.execute(root,output).returncode,0)
            self.assertEqual((root/'calls').read_text(),calls)

    def test_retry_nonzero_check_and_run_are_marked_failed(self):
        for command in ('check','run'):
            with self.subTest(command=command),tempfile.TemporaryDirectory() as td:
                root=Path(td);executable,output=self.prepare(root)
                executable.write_text('#!/usr/bin/env bash\n'
                    'echo "$@" >> "$OPENBENCH_CALLS"\n'
                    f'if [[ "$1" == {command} ]]; then exit 7; fi\n')
                run(root,executable,'-D','--retry-failed')
                result=self.execute(root,output)
                self.assertEqual(result.returncode,7,result.stderr)
                self.assertTrue((output/'.openbench_failed').is_file())
                self.assertFalse((output/'.dlb_openbench_running').exists())
                if command=='check':self.assertNotIn('run ',(root/'calls').read_text())

    def test_recheck_success_running_and_lock(self):
        for state in ('success','running','lock'):
            with self.subTest(state=state),tempfile.TemporaryDirectory() as td:
                root=Path(td);executable,output=self.prepare(root)
                run(root,executable,'-D','--retry-failed')
                with (output.parent/f'.{output.name}.retry.lock').open('a') as lock:
                    if state=='lock':fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    elif state=='running':(output/'.dlb_openbench_running').touch()
                    else:
                        (output/'.openbench_failed').unlink()
                        (output/'.openbench_success').touch()
                    result=self.execute(root,output)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertFalse((root/'calls').exists())
                self.assertTrue((output/'old.nc').exists())

    def test_retry_snapshot_isolated_reused_and_mapping_immutable(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable,output=self.prepare(root)
            run(root,executable,'-D')
            normal=root/'config/openbench_jobs/submit_openbench_D_array.lsf'
            before=normal.read_bytes()
            run(root,executable,'-D','--retry-failed')
            mapping=next(normal.parent.glob('*retry_failed*_projects.tsv'))
            snapshot=mapping.read_bytes()
            bin_dir=root/'bin';bin_dir.mkdir()
            bsub=bin_dir/'bsub'
            bsub.write_text('#!/usr/bin/env bash\ncat > "$BSUB_CAPTURE"\necho "Job <456>"\n')
            bsub.chmod(0o755);env=os.environ.copy()
            env.update(PATH=str(bin_dir)+os.pathsep+env.get('PATH',''),BSUB_CAPTURE=str(root/'submitted'))
            result=run(root,executable,'-D','--retry-failed','--submit',env=env)
            self.assertIn('Reusing generated job snapshot',result.stdout)
            self.assertIn(str(mapping),(root/'submitted').read_text())
            run(root,executable,'-D','--retry-failed','--overwrite')
            self.assertEqual(mapping.read_bytes(),snapshot)
            self.assertEqual(len(list(normal.parent.glob('*retry_failed*_projects.tsv'))),2)
            self.assertEqual(normal.read_bytes(),before)

    def test_normal_job_does_not_mark_internal_failure_successful(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root)
            output=root/'openbench_output/study_basin_streamflow_D'
            executable.write_text('#!/usr/bin/env bash\n'
                'if [[ "$1" == run ]]; then\n'
                '  echo "Evaluation phase: 2 succeeded, 1 failed" > "$TEST_OUTPUT/run.log"\n'
                'fi\n')
            run(root,executable,'-D')
            env=os.environ.copy();env.update(LSB_JOBINDEX='1',TEST_OUTPUT=str(output))
            job=root/'config/openbench_jobs/submit_openbench_D_array.lsf'
            result=subprocess.run(['bash',str(job)],env=env,capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertTrue((output/'.openbench_failed').is_file())
            self.assertFalse((output/'.openbench_success').exists())


if __name__=='__main__':
    unittest.main()
