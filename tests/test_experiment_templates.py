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


def tree_hash(root:Path)->str:
    digest=hashlib.sha256()
    for path in sorted(x for x in root.rglob('*') if x.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@unittest.skipUnless(REPO.is_dir(),'local CoLM checkout unavailable')
class ExperimentTemplateTests(unittest.TestCase):
    def test_all_regions_and_job_stages(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cases=root/'cases';cases.mkdir()
            expected={'USA':36,'SA':18,'Mekong':18,'Global':18}
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
                    if config.stem not in ('streamflow_spinup','inundation_spinup'):
                        self.assertIn('E0_CTL:',text)
                    self.assertIn('_hist_unitcat_',text)
                    if yaml:yaml.safe_load(text)
                for basin_dir in (exp/'config/openbench').iterdir():
                    self.assertTrue((basin_dir/'streamflow_DLB.yaml').is_file())
                    self.assertFalse((basin_dir/'streamflow_ALL.yaml').exists())
                    for name in ('streamflow_spinup.yaml','inundation_spinup.yaml',
                                 'streamflow_ctl.yaml','inundation_ctl.yaml'):
                        self.assertTrue((basin_dir/name).is_file())
                    self.assertIn('years: [1996, 1999]',
                                  (basin_dir/'streamflow_spinup.yaml').read_text())
                    self.assertIn('enabled: false',
                                  (basin_dir/'streamflow_ctl.yaml').read_text())
                self.assertEqual(list(cases.iterdir()),[],'create must not create cases')
                self.assertNotIn(str(exp.parent/f'.EXP{index}_smoke_{region}'),
                                 (exp/'config/colm/run_manifest.json').read_text())
                spin=(exp/'config/jobs/submit_SPINUP.lsf').read_text()
                dam=(exp/'config/jobs/submit_D_INIT.lsf').read_text()
                formal=(exp/'config/jobs/submit_E7_ALL.lsf').read_text()
                self.assertIn('STEPS mksrfdata mkinidata colm',spin)
                self.assertIn('STEPS mkinidata',dam)
                self.assertNotIn('mksrfdata.x',dam)
                self.assertIn(f'#BSUB -o {exp}/logs/D_INIT.%J.out',dam)
                self.assertIn(f'#BSUB -e {exp}/logs/D_INIT.%J.err',dam)
                self.assertIn('#BSUB -env "all,LSB_STDOUT_DIRECT=Y"',dam)
                self.assertNotIn('#BSUB -o ../../logs',dam)
                resolution='0p5' if region=='Global' else '0p1'
                self.assertIn(f'NML={exp}/config/colm/{exp.name}_D_INIT_{resolution}.nml',dam)
                self.assertIn(f'MARKER={exp}/config/jobs/markers/D_INIT.success',dam)
                self.assertNotIn('BASH_SOURCE',dam)
                self.assertIn('STEPS colm',formal)
                self.assertNotIn('mkinidata.x',formal)
                helper=(exp/'config/jobs/submit_batch.sh').read_text()
                self.assertIn('JOB_DIR="$SCRIPT_DIR"',helper)
                self.assertNotIn(f'JOB_DIR={exp}',helper)
                self.assertIn('ORDER=(E0_CTL E2_L E1_D E4_DL E3_B E5_DB E6_LB E7_ALL)',helper)
                moved=root/f'moved_{region}'
                exp.rename(moved)
                preview=subprocess.run(
                    ['bash',str(moved/'config/jobs/submit_batch.sh'),'--dry-run',
                     '--stage','dam-init'],text=True,capture_output=True)
                self.assertEqual(preview.returncode,0,preview.stderr or preview.stdout)
                self.assertIn('submit_D_INIT.lsf',preview.stdout)
                moved.rename(exp)
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

    def test_default_repo_and_case_roots(self):
        with tempfile.TemporaryDirectory() as td:
            target=Path(td)/'minimal_command'
            run(str(TOOLS/'dlb_01_create_experiment.py'),'EXP962','defaults','Mekong',
                '--output-dir',str(target),'--draft')
            manifest=json.loads((target/'config/colm/run_manifest.json').read_text())
            self.assertEqual(manifest['paths']['repo_root'],str(REPO.resolve()))
            self.assertEqual(manifest['paths']['case_root'],
                '/share/home/dq134/wzx/CoLM/cases')

    def test_default_experiments_root_is_documented(self):
        result=run(str(TOOLS/'dlb_01_create_experiment.py'),'--help')
        self.assertIn('/share/home/dq134/wzx/CoLM/DLB_nml',result.stdout)

    def test_component_regeneration_and_backups(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cases=root/'cases';cases.mkdir();target=root/'experiment'
            run(str(TOOLS/'dlb_01_create_experiment.py'),'EXP963','components','Mekong',
                '--repo-root',str(REPO),'--case-root',str(cases),
                '--output-dir',str(target),'--draft')
            colm_before=tree_hash(target/'config/colm')
            jobs_before=tree_hash(target/'config/jobs')
            refused=run(str(TOOLS/'dlb_08_generate_openbench.py'),str(target),ok=False)
            self.assertNotEqual(refused.returncode,0)
            run(str(TOOLS/'dlb_08_generate_openbench.py'),str(target),
                '--eval-start-year','2002','--eval-end-year','2004','--overwrite')
            self.assertEqual(tree_hash(target/'config/colm'),colm_before)
            self.assertEqual(tree_hash(target/'config/jobs'),jobs_before)
            self.assertIn('years: [2002, 2004]',
                          (target/'config/openbench/mekong/streamflow_D.yaml').read_text())
            backups=target/'provenance/backups'
            self.assertTrue(any((path/'openbench').is_dir() for path in backups.iterdir()))
            run(str(TOOLS/'dlb_02_generate_namelists.py'),
                '--experiment-dir',str(target),'--overwrite')
            self.assertTrue((target/'config/colm/run_manifest.json').is_file())
            self.assertTrue((target/'config/jobs/submit_batch.sh').is_file())
            self.assertTrue(any((path/'colm').is_dir() and (path/'jobs').is_dir()
                                for path in backups.iterdir()))
            regenerated=json.loads((target/'config/colm/run_manifest.json').read_text())
            spin_case=regenerated['runs']['spinup']['Mekong']['case']
            nested_restart=cases/spin_case/'restart/2000-001-00000'/f'{spin_case}_restart_test.nc'
            nested_restart.parent.mkdir(parents=True);nested_restart.write_bytes(b'restart')
            blocked=run(str(TOOLS/'dlb_02_generate_namelists.py'),
                '--experiment-dir',str(target),'--overwrite',ok=False)
            self.assertNotEqual(blocked.returncode,0)
            self.assertIn('run products exist',blocked.stderr)
            self.assertIn(str(nested_restart),blocked.stderr)
            nested_restart.unlink();nested_restart.parent.rmdir()
            nested_restart.parent.parent.rmdir();nested_restart.parent.parent.parent.rmdir()
            marker=target/'config/jobs/markers/SPINUP.success'
            marker.parent.mkdir();marker.touch()
            blocked=run(str(TOOLS/'dlb_02_generate_namelists.py'),
                '--experiment-dir',str(target),'--overwrite',ok=False)
            self.assertNotEqual(blocked.returncode,0)
            self.assertIn('run products exist',blocked.stderr)

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
            river_source=cases/spin/'riverlake/unitcatchment_regional.nc'
            river_source.parent.mkdir();river_source.write_bytes(b'regional-routing')
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
                regional=cases/case/'riverlake/unitcatchment_regional.nc'
                self.assertTrue(regional.is_symlink())
                self.assertEqual(regional.resolve(),river_source.resolve())
                self.assertTrue((cases/case/'.aas_restart_handoff.json').is_file())


if __name__=='__main__':unittest.main()
