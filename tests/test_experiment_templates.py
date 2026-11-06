#!/usr/bin/env python3
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS=Path(__file__).resolve().parents[1]
PYTHON=Path(sys.executable)
FIXTURE=tempfile.TemporaryDirectory()
ROOT=Path(FIXTURE.name)
REPO=ROOT/'CoLM'
MASKS=ROOT/'masks'
PROFILE=ROOT/'server.json'


def setUpModule():
    sys.path.insert(0,str(TOOLS))
    from dlb_08_generate_openbench import BASINS,MASK_ROOT
    for rel in ('run/examples','run/forcing','share','include'):
        (REPO/rel).mkdir(parents=True)
    base=(TOOLS/'templates/dlb_base.nml').read_text()
    (REPO/'run/examples/Amazon_Grid_25km_IGBP_VG_RiverLake.nml').write_text(base)
    keys={n.value.split('%')[0] for n in ast.walk(ast.parse((TOOLS/'dlb_02_generate_namelists.py').read_text()))
          if isinstance(n,ast.Constant) and isinstance(n.value,str) and n.value.startswith('DEF_')
          and all(c.isalnum() or c in '_%' for c in n.value)}
    import re
    keys.update(re.findall(r'^\s*(DEF_\w+)',base,re.M))
    members=set(re.findall(r'%(\w+)',base))
    source='type fixture_type\n integer :: '+', '.join(sorted(members))+'\nend type fixture_type\n'
    source+='type (fixture_type) :: DEF_domain\ntype (fixture_type) :: DEF_simulation_time\n'
    source+='namelist /nl_colm/ &\n'+', &\n'.join(sorted(keys))+'\n'
    (REPO/'share/MOD_Namelist.F90').write_text(source)
    (REPO/'include/define.h').write_text('\n'.join(
        '#define '+k for k in ('GRIDBASED','GridRiverLakeFlow','USEMPI','LULC_IGBP_PFT'))+'\n'+
        '\n'.join('#undef '+k for k in ('CaMa_Flood','BGC','CROP','URBAN_MODEL','LULCC','MaterialTransport')))
    for name in ('mksrfdata','mkinidata','colm'):
        file=REPO/'run'/f'{name}.x';file.write_text('#!/bin/sh\nexit 99\n');file.chmod(0o755)
    for folder in ('rawdata','runtime/unitcatchment','forcing','cases'):
        (ROOT/folder).mkdir(parents=True)
    for res in ('06min','15min'):
        (ROOT/'runtime/unitcatchment'/f'grid_routing_data_{res}.nc').write_bytes(b'fixture')
    (ROOT/'soilstate.nc').write_bytes(b'fixture')
    (REPO/'run/forcing/JRA3Q.nml').write_text(
        "&nl_colm_forcing\n DEF_dir_forcing = '"+str(ROOT/'forcing')+"'\n"
        " DEF_forcing%dataset = 'JRA3Q'\n DEF_forcing%startyr = 1947\n"
        " DEF_forcing%startmo = 9\n DEF_forcing%endyr = 2025\n DEF_forcing%endmo = 6\n/\n")
    for specs in BASINS.values():
        for _,_,_,mask in specs:
            if mask:
                file=MASKS/mask.relative_to(MASK_ROOT);file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(b'mask')
    PROFILE.write_text(json.dumps(dict(repo_root=str(REPO),case_root=str(ROOT/'cases'),
        experiments_root=str(ROOT/'experiments'),rawdata=str(ROOT/'rawdata'),runtime=str(ROOT/'runtime'),
        soil_init=str(ROOT/'soilstate.nc'),mask_root=str(MASKS),forcing_nml='JRA3Q',
        template=str(TOOLS/'templates/dlb_base.nml'),scheduler='slurm',queue='second',cores=144,
        tasks_per_node=144,memory='100G',walltime='48:00:00',launcher='mpirun',exclusive=True,
        mpi_root=None,intel_env=None,no_lsf=True)))


def tearDownModule():
    FIXTURE.cleanup()


def run(*args:str,ok:bool=True,env:dict|None=None)->subprocess.CompletedProcess:
    if args and Path(args[0]).name=='dlb_01_create_experiment.py' and '--mask-root' not in args:
        args=(*args,'--mask-root',str(MASKS))
    result=subprocess.run([str(PYTHON),*args],text=True,capture_output=True,
                          env={k:v for k,v in os.environ.items() if k!='DLB_SITE_CONFIG'} if env is None else env)
    if ok and result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result


def tree_hash(root:Path)->str:
    digest=hashlib.sha256()
    for path in sorted(x for x in root.rglob('*') if x.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class PreprocessorMacroTests(unittest.TestCase):
    def test_inactive_undef_does_not_override_active_definition(self):
        from dlb_00_workflow_common import preprocessor_state
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'define.h'
            path.write_text('''#define LULC_IGBP_PFT
#undef LULC_IGBP_PC
#define USEMPI
#if (defined URBAN_MODEL && defined SinglePoint)
#undef LULC_IGBP_PFT
#endif
#if (defined SinglePoint)
#undef USEMPI
#endif
''')
            state=preprocessor_state(path)
            self.assertTrue(state['LULC_IGBP_PFT'])
            self.assertFalse(state['LULC_IGBP_PC'])
            self.assertTrue(state['USEMPI'])

    def test_nested_elif_and_active_undef(self):
        from dlb_00_workflow_common import preprocessor_state
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'define.h'
            path.write_text('''#define BASE
#if defined(BASE)
#if defined(MISSING)
#define WRONG_BRANCH
#elif !defined(OTHER)
#undef BASE
#define SELECTED_BRANCH
#else
#define WRONG_ELSE
#endif
#endif
#ifndef SELECTED_BRANCH
#define WRONG_IFNDEF
#endif
''')
            state=preprocessor_state(path)
            self.assertFalse(state['BASE'])
            self.assertTrue(state['SELECTED_BRANCH'])
            self.assertNotIn('WRONG_BRANCH',state)
            self.assertNotIn('WRONG_ELSE',state)
            self.assertNotIn('WRONG_IFNDEF',state)

    def test_invalid_preprocessor_conditions_fail_clearly(self):
        from dlb_00_workflow_common import WorkflowError,preprocessor_state
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'define.h'
            for text in ('#if FEATURE == 1\n#endif\n','#if defined(FEATURE)\n'):
                with self.subTest(text=text):
                    path.write_text(text)
                    with self.assertRaises(WorkflowError):preprocessor_state(path)


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
                self.assertIn('#BSUB -W 168:00',formal)
                self.assertIn(f'#BSUB -o {exp}/logs/DLB.%J.out',formal)
                self.assertIn(f'#BSUB -e {exp}/logs/DLB.%J.err',formal)
                self.assertNotIn('/logs/E7_ALL.',formal)
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
                '--output-dir',str(target),'--site-config',str(PROFILE),'--draft')
            manifest=json.loads((target/'config/colm/run_manifest.json').read_text())
            self.assertEqual(manifest['paths']['repo_root'],str(REPO.resolve()))
            self.assertEqual(manifest['paths']['case_root'],
                str(ROOT/'cases'))

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
            dam_nml=configs/manifest['dam_init']['USA']['nml_file']
            original_dam_nml=dam_nml.read_text()
            dam_nml.write_text(original_dam_nml.replace(
                'DEF_simulation_time%start_year = 2000',
                'DEF_simulation_time%start_year = 1999'))
            selected=run(str(TOOLS/'dlb_05_restart_handoff.py'),'handoff','--nml-dir',str(configs),
                '--allow-draft','--only','E0_CTL,E2_L,E3_B')
            self.assertIn('DRY RUN',selected.stdout)
            strict=run(str(TOOLS/'dlb_05_restart_handoff.py'),'handoff','--nml-dir',str(configs),
                '--allow-draft','--only','E0_CTL,E2_L,E3_B','--validate-scope','all',ok=False)
            self.assertIn('D_INIT 年份应为 2000–2000',strict.stderr)
            dependent=run(str(TOOLS/'dlb_05_restart_handoff.py'),'handoff','--nml-dir',str(configs),
                '--allow-draft','--only','E1_D',ok=False)
            self.assertIn('D_INIT 年份应为 2000–2000',dependent.stderr)
            dam_nml.write_text(original_dam_nml)
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


    def test_site_slurm_and_explicit_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            exp=Path(td)/'study'
            run(str(TOOLS/'dlb_01_create_experiment.py'),'EXP970','site','SA',
                '--site-config',str(PROFILE),'--output-dir',str(exp),
                '--queue','custom','--cores','72','--tasks-per-node','72','--no-exclusive')
            manifest=json.loads((exp/'config/colm/run_manifest.json').read_text())
            self.assertFalse(manifest['draft'])
            self.assertEqual(manifest['validation_issues'],[])
            self.assertEqual(manifest['resources']['cores'],72)
            self.assertEqual(manifest['site_config']['path'],str(PROFILE))
            self.assertEqual(manifest['site_config']['sha256'],hashlib.sha256(PROFILE.read_bytes()).hexdigest())
            self.assertEqual(manifest['regions']['SA']['land_resolution_degrees'],0.1)
            self.assertTrue(manifest['regions']['SA']['routing_grid'].endswith('06min.nc'))
            for file in exp.rglob('*'):
                if file.is_file():self.assertNotIn('/share/home/',file.read_text())
            jobs=exp/'config/jobs'
            self.assertEqual(len(list(jobs.glob('*.slurm'))),10)
            for file in [*jobs.glob('*.slurm'),jobs/'submit_batch.sh']:
                subprocess.run(['bash','-n',str(file)],check=True)
            spin=(jobs/'submit_SPINUP.slurm').read_text()
            self.assertIn('--partition=custom',spin)
            self.assertIn('--nodes=1',spin)
            self.assertIn('--ntasks=72',spin)
            self.assertIn('--mem=100G',spin)
            self.assertIn('--time=48:00:00',spin)
            self.assertIn('mpirun -np 72',spin)
            self.assertNotIn('--exclusive',spin)
            self.assertNotIn('--nodelist',spin)
            self.assertNotIn('source ',spin)
            self.assertIn('STEPS mksrfdata mkinidata colm',spin)
            self.assertIn('STEPS mkinidata',(jobs/'submit_D_INIT.slurm').read_text())
            self.assertIn('STEPS colm',(jobs/'submit_E7_ALL.slurm').read_text())
            self.assertEqual(list((ROOT/'cases').iterdir()),[])
            run(str(TOOLS/'dlb_04_preflight.py'),'--nml-dir',str(exp/'config/colm'),'--phase','inputs')

    def test_standalone_generators_and_profile_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);out=root/'colm';jobs=root/'jobs'
            run(str(TOOLS/'dlb_02_generate_namelists.py'),'--site-config',str(PROFILE),
                '--out-dir',str(out),'--region','Global','--include-dam-init')
            self.assertFalse(list(out.glob('*.lsf')))
            manifest=json.loads((out/'run_manifest.json').read_text())
            self.assertEqual(manifest['regions']['Global']['land_resolution_degrees'],0.5)
            self.assertEqual(manifest['regions']['Global']['history_resolution_degrees'],0.25)
            self.assertTrue(manifest['regions']['Global']['routing_grid'].endswith('15min.nc'))
            self.assertIn('DEF_TRACER_NUM = 0',(out/'Global_ctl_0p5.nml').read_text())
            run(str(TOOLS/'dlb_03_generate_jobs.py'),'--site-config',str(PROFILE),
                '--nml-dir',str(out),'--out-dir',str(jobs),'--node','tms13')
            spin=(jobs/'submit_SPINUP.slurm').read_text()
            for flag in ('--exclusive','--nodelist=tms13','--ntasks=144','--nodes=1','mpirun -np 144'):
                self.assertIn(flag,spin)
            run(str(TOOLS/'dlb_04_preflight.py'),'--nml-dir',str(out),'--phase','inputs')

    def test_missing_mask_and_input_draft_diagnostics(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);profile=root/'server.json'
            data=json.loads(PROFILE.read_text());data['rawdata']=str(root/'missing_raw')
            profile.write_text(json.dumps(data))
            common=(str(TOOLS/'dlb_01_create_experiment.py'),'EXP971','missing','Mekong',
                    '--site-config',str(profile),'--mask-root',str(root/'missing_masks'))
            result=run(*common,'--output-dir',str(root/'strict'),ok=False)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('missing_raw',result.stderr)
            self.assertIn('mekong_basin.nc',result.stderr)
            self.assertFalse((root/'strict').exists())
            run(*common,'--output-dir',str(root/'draft'),'--draft')
            issues=(root/'draft/provenance/input_validation.txt').read_text()
            self.assertIn('missing_raw',issues)
            self.assertIn('mekong_basin.nc',issues)
            result=run(str(TOOLS/'dlb_04_preflight.py'),'--nml-dir',str(root/'draft/config/colm'),
                       '--phase','inputs',ok=False)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('draft',result.stderr)
            checked=subprocess.run(['bash',str(root/'draft/config/jobs/submit_batch.sh'),
                                    '--submit','--stage','spinup'],capture_output=True,text=True)
            self.assertNotEqual(checked.returncode,0)
            self.assertNotIn('SUBMIT:',checked.stdout)

    def test_unsupported_source_fields_are_not_silently_removed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);repo=root/'repo'
            import shutil
            shutil.copytree(REPO,repo)
            source=repo/'share/MOD_Namelist.F90'
            source.write_text(source.read_text().replace('DEF_GridRiverLake_FloodFeedback, &\n',''))
            # A declaration elsewhere does not make a field part of NAMELIST /nl_colm/.
            source.write_text(source.read_text()+'\nlogical :: DEF_GridRiverLake_FloodFeedback\n')
            common=(str(TOOLS/'dlb_02_generate_namelists.py'),'--site-config',str(PROFILE),
                    '--repo-root',str(repo),'--region','Mekong')
            result=run(*common,'--out-dir',str(root/'strict'),ok=False)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Unsupported namelist field DEF_GridRiverLake_FloodFeedback',result.stderr)
            self.assertFalse((root/'strict').exists())
            run(*common,'--out-dir',str(root/'draft'),'--draft')
            manifest=json.loads((root/'draft/run_manifest.json').read_text())
            self.assertTrue(any('DEF_GridRiverLake_FloodFeedback' in x for x in manifest['validation_issues']))
            self.assertIn('DEF_GridRiverLake_FloodFeedback',(root/'draft/Mekong_ctl_0p1.nml').read_text())

    def test_profile_validation_and_relative_template(self):
        from dlb_site_config import load_profile
        with tempfile.TemporaryDirectory() as td:
            profile=Path(td)/'server.json'
            profile.write_text(json.dumps({'template':'base.nml'}))
            self.assertEqual(load_profile(profile)['template'],Path(td)/'base.nml')
            for data in ({'typo':'x'},{'cores':True},{'exclusive':'true'},{'scheduler':'bad'},{'rawdata':None}):
                profile.write_text(json.dumps(data))
                with self.assertRaises(ValueError):load_profile(profile)
        result=run(str(TOOLS/'dlb_01_create_experiment.py'),'--help')
        self.assertIn('/share/home/dq134/wzx/CoLM/CoLM202X_1030',result.stdout)

    def test_monthly_forcing_supports_local_filename_convention(self):
        from dlb_site_config import forcing_month_issues
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);nml=root/'forcing.nml'
            lines=['&nl_colm_forcing',f"DEF_dir_forcing = '{root}'","DEF_forcing%groupby = 'month'"]
            for i in range(1,9):
                folder=root/f'v{i}';folder.mkdir()
                lines.append(f"DEF_forcing%fprefix({i}) = 'v{i}/v{i}'")
                sep=('_','-','')[i%3]
                for year in range(1996,2006):
                    for month in range(1,13):(folder/f'v{i}_{year}{sep}{month:02d}.nc').touch()
            nml.write_text('\n'.join([*lines,'/']))
            self.assertEqual(forcing_month_issues(nml),[])
            (root/'v1/v1_1996-01.nc').unlink()
            self.assertIn('199601',forcing_month_issues(nml)[0])

    def test_source_member_must_belong_to_its_type(self):
        from dlb_site_config import unsupported_fields
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'share').mkdir()
            (root/'share/MOD_Namelist.F90').write_text(
                'type domain_type\n real :: edges\nend type domain_type\n'
                'type (domain_type) :: DEF_domain\nreal :: unrelated_member\n'
                'namelist /nl_colm/ DEF_domain\n')
            text='&nl_colm\nDEF_domain%edges = 0\nDEF_domain%unrelated_member = 0\n/\n'
            errors=unsupported_fields(root,[text])
            self.assertEqual(len(errors),1)
            self.assertIn('unrelated_member',errors[0])


    def test_site_regeneration_uses_recorded_profile_and_upstream_layout(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);profile=root/'server.json';profile.write_text(PROFILE.read_text())
            target=root/'experiment'
            run(str(TOOLS/'dlb_01_create_experiment.py'),'EXP975','regenerate','Mekong',
                '--site-config',str(profile),'--output-dir',str(target),
                '--node','tms13','--cores','72','--tasks-per-node','72','--draft')
            colm_path=target/'config/colm/run_manifest.json'
            before=json.loads(colm_path.read_text())
            self.assertEqual(len(list((target/'config/openbench').rglob('*.yaml'))),18)
            self.assertIn('OpenBench_Streamflow_Daily',
                          (target/'config/openbench/mekong/streamflow_DLB.yaml').read_text())
            # Later changes or removal of the external profile must not alter an existing study.
            profile.write_text('{"invalid_later_edit": true}')
            openbench_before=tree_hash(target/'config/openbench')
            run(str(TOOLS/'dlb_02_generate_namelists.py'),'--experiment-dir',str(target),'--overwrite')
            after=json.loads(colm_path.read_text())
            self.assertEqual(after['site_config'],before['site_config'])
            self.assertEqual(after['resources'],before['resources'])
            self.assertEqual(after['base_template'],before['base_template'])
            self.assertEqual(tree_hash(target/'config/openbench'),openbench_before)
            spin=(target/'config/jobs/submit_SPINUP.slurm').read_text()
            for value in ('--ntasks=72','--exclusive','--nodelist=tms13','mpirun -np 72','NML="$SCRIPT_DIR"/'):
                self.assertIn(value,spin)
            job_meta=json.loads((target/'config/jobs/site_config.json').read_text())
            self.assertEqual(job_meta['path'],str(profile))
            self.assertEqual(job_meta['sha256'],before['site_config']['sha256'])
            for folder in ('colm','jobs'):
                for file in (target/'config'/folder).iterdir():
                    if file.is_file():self.assertNotIn('.colm-regenerate.',file.read_text())
            colm_hash=tree_hash(target/'config/colm');jobs_hash=tree_hash(target/'config/jobs')
            run(str(TOOLS/'dlb_08_generate_openbench.py'),str(target),'--overwrite')
            self.assertEqual(tree_hash(target/'config/colm'),colm_hash)
            self.assertEqual(tree_hash(target/'config/jobs'),jobs_hash)
            metadata=json.loads((target/'manifest.yaml').read_text())
            self.assertEqual(metadata['evaluation']['mask_root'],str(MASKS))
            moved=root/'moved';target.rename(moved)
            helper=moved/'config/jobs/submit_batch.sh'
            preview=subprocess.run(['bash',str(helper),'--dry-run','--stage','spinup'],
                                   text=True,capture_output=True)
            self.assertEqual(preview.returncode,0,preview.stderr)
            self.assertIn('submit_SPINUP.slurm',preview.stdout)
            for job in (moved/'config/jobs').glob('*.slurm'):
                subprocess.run(['bash','-n',str(job)],check=True)


    def test_global_profile_selection_and_switch_off(self):
        import argparse
        from unittest.mock import patch
        from dlb_site_config import parse_site_args
        def parse(argv,env_path):
            parser=argparse.ArgumentParser()
            parser.add_argument('--cores',type=int,default=240)
            parser.add_argument('--repo-root',type=Path)
            parser.add_argument('--exclusive',action=argparse.BooleanOptionalAction,default=False)
            with patch.dict(os.environ,{'DLB_SITE_CONFIG':env_path}),patch('sys.argv',['test',*argv]):
                return parse_site_args(parser)
        args=parse([],str(PROFILE))
        self.assertEqual(args.cores,144)
        self.assertTrue(args.exclusive)
        self.assertEqual(args.site_config,PROFILE)
        self.assertEqual(args.site_metadata['selection'],'environment')
        self.assertEqual(args.site_metadata['sha256'],hashlib.sha256(PROFILE.read_bytes()).hexdigest())
        args=parse(['--cores','72','--no-exclusive'],str(PROFILE))
        self.assertEqual(args.cores,72)
        self.assertFalse(args.exclusive)
        args=parse(['--site-config',str(PROFILE)],str(ROOT/'missing.json'))
        self.assertEqual(args.site_metadata['selection'],'cli')
        args=parse(['--no-site-config'],str(ROOT/'missing.json'))
        self.assertEqual(args.cores,240)
        self.assertIsNone(args.site_config)
        self.assertEqual(args.site_metadata['selection'],'disabled')
        args=parse([],'')
        self.assertEqual(args.cores,240)
        self.assertEqual(args.site_metadata['selection'],'default')
        with self.assertRaises(FileNotFoundError):parse([],str(ROOT/'missing.json'))

    def test_global_profile_command_entries_and_recorded_operations(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);exp=root/'experiment'
            env={**os.environ,'DLB_SITE_CONFIG':str(PROFILE)}
            run(str(TOOLS/'dlb_01_create_experiment.py'),'EXP976','globalconfig','Mekong',
                '--output-dir',str(exp),env=env)
            before=json.loads((exp/'config/colm/run_manifest.json').read_text())
            self.assertEqual(before['site_config']['selection'],'environment')
            self.assertEqual(before['resources']['scheduler'],'slurm')
            audit=run(str(TOOLS/'dlb_04_preflight.py'),'--region','Mekong',env=env)
            self.assertIn(str(PROFILE),audit.stdout)
            flat=root/'flat'
            run(str(TOOLS/'dlb_02_generate_namelists.py'),'--out-dir',str(flat),
                '--region','Mekong','--include-dam-init',env=env)
            self.assertTrue((flat/'run_manifest.json').is_file())
            jobs=root/'jobs'
            run(str(TOOLS/'dlb_03_generate_jobs.py'),'--nml-dir',str(flat),'--out-dir',str(jobs),env=env)
            self.assertIn('mpirun -np 144',(jobs/'submit_SPINUP.slurm').read_text())
            # A bad global path must not affect operations on a recorded study.
            bad_env={**os.environ,'DLB_SITE_CONFIG':str(root/'missing.json')}
            run(str(TOOLS/'dlb_02_generate_namelists.py'),'--experiment-dir='+str(exp),'--overwrite',env=bad_env)
            after=json.loads((exp/'config/colm/run_manifest.json').read_text())
            self.assertEqual(after['site_config'],before['site_config'])
            self.assertEqual(after['resources'],before['resources'])
            run(str(TOOLS/'dlb_04_preflight.py'),'--nml-dir='+str(exp/'config/colm'),
                '--phase','inputs',env=bad_env)
            run(str(TOOLS/'dlb_08_generate_openbench.py'),str(exp),'--overwrite',env=bad_env)
            explicit=root/'explicit'
            run(str(TOOLS/'dlb_01_create_experiment.py'),'EXP977','explicit','Mekong',
                '--site-config',str(PROFILE),'--output-dir',str(explicit),env=bad_env)
            # --help remains usable even when a global profile path is invalid.
            result=run(str(TOOLS/'dlb_01_create_experiment.py'),'--help',env=bad_env)
            self.assertIn('--no-site-config',result.stdout)
            self.assertEqual(list((ROOT/'cases').iterdir()),[])


if __name__=='__main__':unittest.main()
