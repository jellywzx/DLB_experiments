#!/usr/bin/env python3
"""Template generation checks using only temporary synthetic experiments."""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

TOOLS=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(TOOLS))
import dlb_08_generate_openbench as generator
import dlb_09_submit_openbench as submitter

TREATMENTS={'D':'E1_D','L':'E2_L','B':'E3_B','DL':'E4_DL',
            'DB':'E5_DB','LB':'E6_LB','DLB':'E7_ALL'}


def fixture(root:Path,region:str='Global')->None:
    colm=root/'config/colm/run_manifest.json';colm.parent.mkdir(parents=True)
    masks=root/'masks'
    for _,_,_,mask in generator.BASINS[region]:
        if mask:
            target=masks/mask.relative_to(generator.MASK_ROOT)
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'mask fixture')
    colm.write_text(json.dumps({'paths':{'case_root':str(root/'cases')},'draft':False,
        'runs':{'spinup':{region:{'case':'study_spinup'}},'experiments':{region:[
            {'label':label,'case':f'study_{label}'} for label in ('E0_CTL',*TREATMENTS.values())]}}}))
    (root/'manifest.yaml').write_text(json.dumps({'region':region,'name':'study',
        'configuration':{'colm':'config/colm/run_manifest.json'},
        'evaluation':{'start_year':2002,'end_year':2004,'mask_root':str(masks),'projects':[]}}))


def generate(root:Path,*args:str)->tuple[str,str]:
    stdout=io.StringIO();stderr=io.StringIO()
    with patch.object(sys,'argv',['generate',str(root),'--no-refresh',*args]), \
         contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
        assert generator.main()==0
    return stdout.getvalue(),stderr.getvalue()


def files(root:Path)->dict[str,bytes]:
    return {str(path.relative_to(root)):path.read_bytes() for path in root.rglob('*') if path.is_file()}


def write_template(path:Path,value:dict)->None:
    path.write_text(yaml.safe_dump(value,sort_keys=False))


class OpenBenchTemplateTests(unittest.TestCase):
    def test_three_template_rules(self):
        templates=generator.load_templates(generator.TEMPLATE_ROOT)
        self.assertEqual(set(templates),{'streamflow','inundation_fraction','inundation_area'})
        for kind,template in templates.items():
            flood=kind!='streamflow'
            variable={'streamflow':'Streamflow','inundation_fraction':'Inundation_Fraction',
                      'inundation_area':'Inundation_Area'}[kind]
            self.assertEqual(template['evaluation']['variables'],[variable])
            self.assertEqual(template['reference'],{variable:'GIEMS_v2_LowRes' if flood else
                                                   'OpenBench_Streamflow_Daily'})
            self.assertEqual(template['project']['tim_res'],'Month' if flood else 'Day')
            self.assertEqual(template['metrics'],(['bias'] if flood else [])+
                             ['RMSE','correlation','percent_bias','NSE','KGE','KGESS','MFM'])
            self.assertEqual(template['scores'],['Overall_Score']+(['nBiasScore','nRMSEScore',
                'nPhaseScore','nIavScore','nSpatialScore','nSeasonalityScore'] if flood else []))
            self.assertEqual(template['comparison']['items'],['Taylor_Diagram','HeatMap','Diff_Plot'])
            self.assertEqual(template['statistics'],{'enabled':False,'items':[]})

    def test_all_regions_manifest_and_simulation_rules(self):
        templates=generator.load_templates(generator.TEMPLATE_ROOT)
        for region,count in {'USA':60,'SA':30,'Mekong':30,'Global':30}.items():
            with self.subTest(region=region),tempfile.TemporaryDirectory() as td:
                root=Path(td);fixture(root,region);generate(root)
                projects=json.loads((root/'manifest.yaml').read_text())['evaluation']['projects']
                actual={str(path.relative_to(root)) for path in (root/'config/openbench').rglob('*.yaml')}
                self.assertEqual(len(actual),count)
                self.assertEqual({p['config'] for p in projects},actual)
                self.assertEqual(len({p['project'] for p in projects}),count)
                self.assertEqual(len({p['output_dir'] for p in projects}),count)
                basins={basin[0]:basin for basin in generator.BASINS[region]}
                for project in projects:
                    config=yaml.safe_load((root/project['config']).read_text())
                    template=templates[project['type']];variables=template['evaluation']['variables']
                    self.assertEqual(config['evaluation']['variables'],variables)
                    for field in ('reference','metrics','scores','statistics'):
                        self.assertEqual(config[field],template[field])
                    self.assertEqual(config['project']['tim_res'],template['project']['tim_res'])
                    self.assertEqual(config['project']['name'],project['project'])
                    self.assertEqual(Path(config['project']['output_dir'])/project['project'],
                                     Path(project['output_dir']))
                    basin=basins[project['basin']]
                    self.assertEqual(config['project']['lat_range'],basin[1])
                    self.assertEqual(config['project']['lon_range'],basin[2])
                    self.assertEqual(config['project']['grid_res'],0.25 if region=='Global' else 0.1)
                    if basin[3]:
                        self.assertEqual(config['project']['basin_mask'],
                            str(root/'masks'/basin[3].relative_to(generator.MASK_ROOT)))
                    else:self.assertNotIn('basin_mask',config['project'])
                    label=project['treatment'];paired=label in TREATMENTS.values()
                    comparison=paired or label=='comparisons'
                    self.assertEqual(config['comparison']['enabled'],comparison)
                    self.assertEqual(config['comparison']['items'],template['comparison']['items'] if comparison else [])
                    self.assertEqual(config['project']['years'],[1996,1999] if label=='spinup' else [2002,2004])
                    expected=['E0_CTL',label] if paired else ['spinup' if label=='spinup' else 'E0_CTL']
                    if label=='comparisons':expected=['E0_CTL',*TREATMENTS.values()]
                    self.assertEqual(list(config['simulation']),expected)
                    for run,simulation in config['simulation'].items():
                        case=f'study_{run}'
                        self.assertEqual(simulation['root_dir'],str(root/'cases'/case/'history'))
                        self.assertEqual(simulation['model'],'CoLM2024')
                        self.assertEqual(simulation['tim_res'],'Day')
                        self.assertEqual(simulation['data_groupby'],'Month')
                        self.assertEqual(simulation['grid_res'],config['project']['grid_res'])
                        self.assertEqual(simulation['variables'],{
                            variables[0]:{'prefix':case+'_hist_unitcat_','suffix':None}})
                    short=next((k for k,v in TREATMENTS.items() if v==label),label)
                    self.assertEqual(Path(project['config']).name,f'{project["type"]}_{short}.yaml')

    @unittest.skipUnless(importlib.util.find_spec('openbench'),'OpenBench not installed')
    def test_generated_yaml_loads_with_openbench(self):
        from openbench.config import load_config
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture(root,'USA');generate(root)
            for config in (root/'config/openbench').rglob('*.yaml'):
                with self.subTest(config=config.name):
                    parsed=load_config(config)
                    self.assertEqual(len(parsed.evaluation.variables),1)

    def test_fourth_variable_and_template_edits_need_no_python_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture(root);templates=root/'templates'
            shutil.copytree(generator.TEMPLATE_ROOT,templates)
            template=yaml.safe_load((templates/'inundation_fraction.yaml').read_text())
            template['evaluation']['variables']=['Evapotranspiration']
            template['reference']={'Evapotranspiration':'GLEAM_v4.2a_LowRes'}
            template['metrics']=['RMSE','correlation'];template['scores']=['Overall_Score']
            write_template(templates/'evapotranspiration.yaml',template)
            generate(root,'--template-dir',str(templates))
            self.assertEqual(len(list((root/'config/openbench').rglob('*.yaml'))),40)
            config=root/'config/openbench/global/evapotranspiration_DLB.yaml'
            data=yaml.safe_load(config.read_text())
            self.assertEqual(data['evaluation'],template['evaluation'])
            for field in ('reference','metrics','scores'):
                self.assertEqual(data[field],template[field])
            # Configurations must remain usable after all source templates are removed.
            shutil.rmtree(templates)
            self.assertEqual(yaml.safe_load(config.read_text()),data)
            if importlib.util.find_spec('openbench'):
                from openbench.config import load_config
                self.assertEqual(load_config(config).evaluation.variables,['Evapotranspiration'])
                comparison=load_config(root/'config/openbench/global/evapotranspiration_comparisons.yaml')
                self.assertEqual(comparison.evaluation.variables,['Evapotranspiration'])
                self.assertEqual(list(comparison.simulation),['E0_CTL',*TREATMENTS.values()])

    def test_template_errors_are_explicit_and_do_not_replace_existing_configs(self):
        base=generator.load_templates(generator.TEMPLATE_ROOT)['streamflow']
        bad=[]
        for field in ('project','evaluation','reference','metrics','scores','comparison','statistics'):
            template=copy.deepcopy(base);del template[field];bad.append((field,template))
        for section,field in (('project','tim_res'),('evaluation','variables'),('comparison','items'),
                              ('statistics','enabled'),('statistics','items')):
            template=copy.deepcopy(base);del template[section][field];bad.append((f'{section}.{field}',template))
        template=copy.deepcopy(base);template['evaluation']['variables']*=2
        bad.append(('exactly one variable',template))
        for message,template in bad:
            with self.subTest(message=message),tempfile.TemporaryDirectory() as td:
                root=Path(td);write_template(root/'test.yaml',template)
                with self.assertRaisesRegex(ValueError,message):generator.load_templates(root)
        for content,message in [('project: [','invalid OpenBench template'),
                                ('project: {}\nproject: {}','duplicate YAML field')]:
            with tempfile.TemporaryDirectory() as td:
                root=Path(td);(root/'test.yaml').write_text(content)
                with self.assertRaisesRegex(ValueError,message):generator.load_templates(root)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture(root);generate(root);before=files(root)
            templates=root/'templates';templates.mkdir()
            with self.assertRaisesRegex(ValueError,'no variable YAML'):generate(root,'--overwrite','--template-dir',str(templates))
            write_template(templates/'first.yaml',base);write_template(templates/'second.yaml',base)
            with self.assertRaisesRegex(ValueError,'duplicate variable'):generate(root,'--overwrite','--template-dir',str(templates))
            (templates/'second.yaml').unlink()
            second=copy.deepcopy(base);second['evaluation']['variables']=['Evapotranspiration']
            second['reference']={'Evapotranspiration':'GLEAM_v4.2a_LowRes'}
            write_template(templates/'first.yml',second)
            with self.assertRaisesRegex(ValueError,'output naming conflict'):generate(root,'--overwrite','--template-dir',str(templates))
            shutil.rmtree(templates)
            self.assertEqual(files(root),before)

    def test_overwrite_backup_protects_colm_and_existing_results(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture(root)
            for rel in ('config/colm/test.nml','config/jobs/job.lsf','cases/study_E0_CTL/restart/state.nc',
                        'cases/study_E0_CTL/history/data.nc','openbench_output/study_global_streamflow_D/result.nc',
                        'openbench_output/study_global_streamflow_D/.openbench_success',
                        'openbench_output/study_global_inundation_D/result.nc'):
                path=root/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'unchanged')
            generate(root)
            before=files(root);old_config=files(root/'config/openbench')
            with self.assertRaisesRegex(ValueError,'use --overwrite'):generate(root)
            self.assertEqual(files(root),before)
            _,stderr=generate(root,'--overwrite','--eval-start-year','2003')
            self.assertIn('does not re-evaluate',stderr)
            backup=next((root/'provenance/backups').iterdir())
            self.assertEqual(files(backup/'openbench'),old_config)
            self.assertEqual((backup/'manifest.yaml').read_bytes(),before['manifest.yaml'])
            for rel,content in before.items():
                if not rel.startswith('config/openbench/') and rel!='manifest.yaml':
                    self.assertEqual((root/rel).read_bytes(),content)
            data=yaml.safe_load((root/'config/openbench/global/streamflow_D.yaml').read_text())
            self.assertEqual(data['project']['years'],[2003,2004])

    def test_atomic_manifest_failure_restores_old_configs(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture(root);generate(root);before=files(root/'config/openbench')
            manifest=(root/'manifest.yaml').read_bytes()
            with patch.object(generator,'write_json_atomic',side_effect=OSError('injected write failure')):
                with self.assertRaisesRegex(OSError,'injected write failure'):generate(root,'--overwrite')
            self.assertEqual(files(root/'config/openbench'),before)
            self.assertEqual((root/'manifest.yaml').read_bytes(),manifest)
            self.assertEqual(list((root/'config').glob('.openbench.*')),[])

    def test_project_name_collision_is_rejected_before_replacement(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture(root);generate(root);before=files(root/'config/openbench')
            spec=generator.BASINS['Global'][0]
            with patch.object(generator,'basin_specs',return_value=[spec,spec]):
                with self.assertRaisesRegex(ValueError,'output naming conflict'):generate(root,'--overwrite')
            self.assertEqual(files(root/'config/openbench'),before)
            self.assertFalse((root/'provenance/backups').exists())

    def test_submitter_selects_new_projects_and_skips_existing_results(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);fixture(root,'USA');generate(root)
            executable=root/'fake-openbench';executable.write_text('#!/bin/sh\nexit 99\n');executable.chmod(0o755)
            for flag,treatment in [*TREATMENTS.items(),('comparisons','comparisons')]:
                with patch.object(sys,'argv',['submit',str(root),('--comparisons' if flag=='comparisons' else '-'+flag),
                                               '--openbench-exe',str(executable)]), \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(submitter.main(),0)
                rows=(root/f'config/openbench_jobs/openbench_{flag}_projects.tsv').read_text().splitlines()[1:]
                self.assertEqual(len(rows),6)
                for row in rows:
                    _,project,config,output=row.split('\t')
                    self.assertTrue(project.endswith('_'+flag))
                    expected={'E0_CTL',*TREATMENTS.values()} if flag=='comparisons' else {'E0_CTL',treatment}
                    self.assertEqual(set(yaml.safe_load(Path(config).read_text())['simulation']),expected)
                    self.assertEqual(Path(output),root/'openbench_output'/project)
            selected=next((root/'config/openbench').rglob('streamflow_D.yaml'))
            project=yaml.safe_load(selected.read_text())['project']['name']
            output=root/'openbench_output'/project;output.mkdir(parents=True)
            (output/'result.nc').write_bytes(b'protected')
            stdout=io.StringIO()
            with patch.object(sys,'argv',['submit',str(root),'--all','--openbench-exe',str(executable)]), \
                 contextlib.redirect_stdout(stdout):
                self.assertEqual(submitter.main(),0)
            self.assertIn('selected=60 pending=59 skipped=1',stdout.getvalue())
            rows=(root/'config/openbench_jobs/openbench_all_projects.tsv').read_text().splitlines()[1:]
            self.assertEqual(len(rows),59)
            self.assertNotIn(project,{row.split('\t')[1] for row in rows})
            self.assertEqual((output/'result.nc').read_bytes(),b'protected')


if __name__=='__main__':
    unittest.main()
