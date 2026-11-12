#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS=Path(__file__).resolve().parents[1]
SCRIPT=TOOLS/'dlb_09_submit_openbench.py'
PYTHON=Path(sys.executable)
TREATMENTS=('E1_D','E2_L','E3_B','E4_DL','E5_DB','E6_LB','E7_ALL')
SHORT={'E1_D':'D','E2_L':'L','E3_B':'B','E4_DL':'DL','E5_DB':'DB','E6_LB':'LB','E7_ALL':'DLB'}


def fixture(root:Path)->Path:
    configs=root/'config/openbench/basin';configs.mkdir(parents=True)
    outputs=root/'openbench_output';projects=[]
    for treatment in TREATMENTS:
        for kind in ('streamflow','inundation'):
            short=SHORT[treatment];name=f'study_basin_{kind}_{short}'
            config=configs/f'{kind}_{short}.yaml';output=outputs/name
            config.write_text(f'project:\n  name: {name}\n  output_dir: {output}\n')
            projects.append({'project':name,'config':str(config.relative_to(root)),
                             'treatment':treatment,'status':'missing'})
    for treatment in ('spinup','ctl'):
        name=f'study_basin_streamflow_{treatment}'
        config=configs/f'streamflow_{treatment}.yaml';output=outputs/name
        config.write_text(f'project:\n  name: {name}\n  output_dir: {output}\n')
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
    def test_openbench_is_detected_from_active_path(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root);env=os.environ.copy()
            env['PATH']=str(root)+os.pathsep+env.get('PATH','')
            result=subprocess.run([str(PYTHON),str(SCRIPT),str(root),'-D'],
                                  text=True,capture_output=True,env=env)
            self.assertEqual(result.returncode,0,result.stderr)
            job=(root/'config/openbench_jobs/submit_openbench_D_array.lsf').read_text()
            self.assertIn(f'OPENBENCH={executable.resolve()}',job)
            self.assertIn('#BSUB -J "openbench_D[1-2]%5"',job)

    def test_each_selector_maps_to_one_exact_treatment(self):
        expected={('-D',):'E1_D',('-L',):'E2_L',('-B',):'E3_B',
                  ('-DL',):'E4_DL',('-DB',):'E5_DB',
                  ('-LB',):'E6_LB',('-DLB',):'E7_ALL'}
        for flags,treatment in expected.items():
            with self.subTest(flags=flags),tempfile.TemporaryDirectory() as td:
                root=Path(td);executable=fixture(root);run(root,executable,*flags)
                label=SHORT[treatment]
                mapping=(root/f'config/openbench_jobs/openbench_{label}_projects.tsv').read_text()
                self.assertEqual(mapping.count('\n'),3)
                self.assertIn(f'_{label}.yaml',mapping)
                for other in set(SHORT.values())-{label}:
                    self.assertNotIn(f'_{other}.yaml',mapping)

    def test_separate_flags_select_multiple_individual_experiments(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root)
            result=run(root,executable,'-L','-B')
            self.assertIn('Selection L_B: selected=4 pending=4 skipped=0',result.stdout)
            mapping=(root/'config/openbench_jobs/openbench_L_B_projects.tsv').read_text()
            self.assertEqual(mapping.count('\n'),5)
            self.assertIn('_L.yaml',mapping)
            self.assertIn('_B.yaml',mapping)
            self.assertNotIn('_LB.yaml',mapping)

    def test_factor_combination_is_exact_and_existing_output_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root)
            existing=root/'openbench_output/study_basin_streamflow_DL'
            existing.mkdir(parents=True);(existing/'result.nc').write_bytes(b'result')
            result=run(root,executable,'-DL','--max-concurrent','3')
            self.assertIn('selected=2 pending=1 skipped=1',result.stdout)
            mapping=(root/'config/openbench_jobs/openbench_DL_projects.tsv').read_text()
            self.assertIn('inundation_DL.yaml',mapping)
            self.assertNotIn('streamflow_DL.yaml',mapping)
            job=root/'config/openbench_jobs/submit_openbench_DL_array.lsf'
            text=job.read_text()
            self.assertIn('#BSUB -J "openbench_DL[1-1]%3"',text)
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
            self.assertIn('selected=16 pending=16 skipped=0',result.stdout)

    def test_submit_uses_bsub_stdin(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);executable=fixture(root);bin_dir=root/'bin';bin_dir.mkdir()
            capture=root/'submitted.lsf';bsub=bin_dir/'bsub'
            bsub.write_text('#!/usr/bin/env bash\ncat > "$BSUB_CAPTURE"\necho "Job <123>"\n')
            bsub.chmod(0o755);env=os.environ.copy()
            env.update(PATH=str(bin_dir)+os.pathsep+env.get('PATH',''),BSUB_CAPTURE=str(capture))
            result=run(root,executable,'-D','--submit',env=env)
            self.assertIn('Job <123>',result.stdout)
            self.assertIn('openbench_D[1-2]%5',capture.read_text())

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
            self.assertIn('run "$CONFIG" --cores 2',capture.read_text())
            self.assertNotIn('run "$CONFIG" --cores 9',capture.read_text())


if __name__=='__main__':
    unittest.main()
