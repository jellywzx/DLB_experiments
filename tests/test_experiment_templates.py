#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS=Path(__file__).resolve().parents[1]
REPO=Path('/share/home/dq134/wzx/CoLM/CoLM202X_1030')
PYTHON=Path('/share/home/dq134/.conda/envs/wzx/bin/python3')


def run(*args:str,ok:bool=True)->subprocess.CompletedProcess:
    result=subprocess.run([str(PYTHON),*args],text=True,capture_output=True)
    if ok and result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result


@unittest.skipUnless(REPO.is_dir(),'local CoLM checkout unavailable')
class ExperimentTemplateTests(unittest.TestCase):
    def test_all_regions_and_job_stages(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cases=root/'cases';cases.mkdir()
            expected={'USA':28,'SA':14,'Mekong':14,'Global':14}
            for index,(region,count) in enumerate(expected.items(),901):
                run(str(TOOLS/'dlb_01_create_experiment.py'),f'EXP{index}','smoke',region,
                    '--repo-root',str(REPO),'--case-root',str(cases),
                    '--experiments-root',str(root/'experiments'),'--draft')
                exp=root/'experiments'/f'EXP{index}_smoke_{region}'
                yaml_files=list((exp/'config/openbench').rglob('*.yaml'))
                self.assertEqual(len(yaml_files),count)
                try:
                    import yaml
                except ImportError:
                    yaml=None
                for config in yaml_files:
                    text=config.read_text()
                    self.assertNotIn('__',text)
                    self.assertIn('E0_CTL:',text)
                    self.assertIn('_hist_unitcat_',text)
                    if yaml:yaml.safe_load(text)
                self.assertEqual(list(cases.iterdir()),[],'create must not create cases')
                self.assertNotIn(str(exp.parent/f'.EXP{index}_smoke_{region}'),
                                 (exp/'config/colm/run_manifest.json').read_text())
                spin=(exp/'config/jobs/submit_SPINUP.lsf').read_text()
                dam=(exp/'config/jobs/submit_D_INIT.lsf').read_text()
                formal=(exp/'config/jobs/submit_E7_ALL.lsf').read_text()
                self.assertIn('STEPS mksrfdata mkinidata colm',spin)
                self.assertIn('STEPS mkinidata',dam)
                self.assertNotIn('mksrfdata.x',dam)
                self.assertIn('STEPS colm',formal)
                self.assertNotIn('mkinidata.x',formal)
            duplicate=run(str(TOOLS/'dlb_01_create_experiment.py'),'EXP901','smoke','USA',
                '--repo-root',str(REPO),'--case-root',str(cases),
                '--experiments-root',str(root/'experiments'),'--draft',ok=False)
            self.assertNotEqual(duplicate.returncode,0)

    def test_legacy_flat_command(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cases=root/'cases';cases.mkdir();output=root/'flat'
            run(str(TOOLS/'dlb_02_generate_namelists.py'),'--repo-root',str(REPO),
                '--out-dir',str(output),'--case-root',str(cases),'--forcing-nml','JRA3Q',
                '--region','USA','--draft')
            self.assertTrue((output/'USA_spinup_0p1.nml').is_file())
            self.assertTrue((output/'USA_ctl_0p1.lsf').is_file())
            self.assertFalse(any(output.glob('*D_INIT*')))

    def test_exact_output_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cases=root/'cases';cases.mkdir()
            target=root/'custom'/'my_experiment'
            run(str(TOOLS/'dlb_01_create_experiment.py'),'EXP960','custom','Mekong',
                '--repo-root',str(REPO),'--case-root',str(cases),
                '--output-dir',str(target),'--draft')
            self.assertTrue((target/'manifest.yaml').is_file())
            self.assertFalse((target.parent/'EXP960_custom_Mekong').exists())
            conflict=run(str(TOOLS/'dlb_01_create_experiment.py'),'EXP961','custom','Mekong',
                '--repo-root',str(REPO),'--case-root',str(cases),
                '--output-dir',str(target),'--draft',ok=False)
            self.assertNotEqual(conflict.returncode,0)

    def test_transactional_restart_donor_selection(self):
        try:
            from scipy.io import netcdf_file
        except ImportError:
            self.skipTest('SciPy unavailable')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cases=root/'cases';cases.mkdir();configs=root/'configs'
            run(str(TOOLS/'dlb_02_generate_namelists.py'),'--repo-root',str(REPO),
                '--out-dir',str(configs),'--case-root',str(cases),'--forcing-nml','JRA3Q',
                '--region','USA','--study-id','EXP950','--study-slug','handoff',
                '--include-dam-init','--no-lsf','--draft')
            manifest=json.loads((configs/'run_manifest.json').read_text())['runs']
            spin=manifest['spinup']['USA']['case'];dam=manifest['dam_init']['USA']['case']
            experiments=manifest['experiments']['USA']
            (cases/spin/'landdata').mkdir(parents=True)
            stamp='2000-001-00000'
            spin_time=cases/spin/'restart'/stamp;spin_const=cases/spin/'restart/const'
            dam_time=cases/dam/'restart'/stamp
            spin_time.mkdir(parents=True);spin_const.mkdir();dam_time.mkdir(parents=True)
            for suffix in ('const_lc','pft_const_lc'):
                (spin_const/f'{spin}_restart_{suffix}.nc').write_bytes(b'const')
            for suffix in (f'{stamp}_lc',f'pft_{stamp}_lc'):
                (spin_time/f'{spin}_restart_{suffix}.nc').write_bytes(b'spin')

            def grid(path:Path,reservoir:bool,donor:int)->None:
                with netcdf_file(str(path),'w') as ds:
                    ds.createDimension('n',1)
                    names=['gridriver_restart_schema','gridriver_restart_complete',
                        'gridriver_restart_feature_levee','gridriver_restart_feature_bifurcation',
                        'gridriver_ucatch_identity','wdsrf_ucat','veloc_riv','acctime_rnof',
                        'acc_rnof_uc','volwater_ucat','donor_code']
                    if reservoir:names+=['volresv','gridriver_reservoir_identity']
                    for name in names:
                        var=ds.createVariable(name,'i',('n',));var[:]=[2 if name=='gridriver_restart_schema' else
                            1 if name=='gridriver_restart_complete' else donor if name=='donor_code' else 0]
            spin_grid=spin_time/f'{spin}_restart_gridriver_{stamp}_lc.nc'
            dam_grid=dam_time/f'{dam}_restart_gridriver_{stamp}_lc.nc'
            grid(spin_grid,False,10);grid(dam_grid,True,20)
            dry=run(str(TOOLS/'dlb_05_restart_handoff.py'),'handoff','--nml-dir',str(configs),'--allow-draft')
            self.assertIn('DRY RUN',dry.stdout)
            self.assertFalse(any((cases/x['case']).exists() for x in experiments))
            run(str(TOOLS/'dlb_05_restart_handoff.py'),'handoff','--nml-dir',str(configs),
                '--allow-draft','--apply','--hash')
            spin_hash=hashlib.sha256(spin_grid.read_bytes()).hexdigest()
            dam_hash=hashlib.sha256(dam_grid.read_bytes()).hexdigest()
            for item in experiments:
                case=item['case'];target=cases/case/'restart'/stamp/f'{case}_restart_gridriver_{stamp}_lc.nc'
                actual=hashlib.sha256(target.read_bytes()).hexdigest()
                self.assertEqual(actual,dam_hash if item['switches']['reservoir_method'] else spin_hash)
                self.assertTrue((cases/case/'landdata').is_symlink())
                self.assertTrue((cases/case/'.aas_restart_handoff.json').is_file())


if __name__=='__main__':unittest.main()
