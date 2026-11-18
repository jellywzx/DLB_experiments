"""Selective generation regression checks; synthetic temporary experiments only."""
from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from test_openbench_templates import fixture, files
import dlb_08_generate_openbench as generator


class TemplateSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);fixture(self.root)
        self.manifest_path=self.root/'manifest.yaml'
        self.destination=self.root/'config/openbench'

    def run_generator(self,*args,refresh=False):
        argv=['generate',str(self.root),*args]
        if not refresh:argv.append('--no-refresh')
        with patch.object(sys,'argv',argv),contextlib.redirect_stdout(io.StringIO()) as out, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(generator.main(),0)
        return out.getvalue()

    def manifest(self):return json.loads(self.manifest_path.read_text())

    def save_manifest(self,manifest):self.manifest_path.write_text(json.dumps(manifest))

    def test_load_selected_filenames_and_custom_directory_only(self):
        directory=self.root/'templates';shutil.copytree(generator.TEMPLATE_ROOT,directory)
        (directory/'unselected.yaml').write_text('broken: [')
        result=generator.load_templates(directory,['dam_inflow.yaml','dam_storage','dam_storage.yaml'])
        self.assertEqual(set(result),{'dam_inflow','dam_storage'})
        for selector in ('unknown','../streamflow','/streamflow'):
            with self.assertRaisesRegex(ValueError,'unknown template|unsafe template'):
                generator.load_templates(directory,[selector])
        shutil.copy2(directory/'dam_inflow.yaml',directory/'dam_inflow.yml')
        with self.assertRaisesRegex(ValueError,'ambiguous template'):
            generator.load_templates(directory,['dam_inflow'])
        self.assertEqual(set(generator.load_templates(directory,['dam_inflow.yml'])),{'dam_inflow'})
        self.run_generator('--template-dir',str(directory),'--templates','dam_inflow.yml','dam_storage')
        self.assertEqual(len(self.manifest()['evaluation']['projects']),20)

    def test_add_multiple_templates_preserves_old_files_records_and_results(self):
        self.run_generator('--templates','streamflow')
        manifest=self.manifest();old_records=copy.deepcopy(manifest['evaluation']['projects'])
        old_records[0]['status']='success';old_records[0]['custom']='keep'
        manifest['evaluation']['projects']=old_records;self.save_manifest(manifest)
        previous=files(self.destination)
        result=self.root/'openbench_output/keep/result.nc'
        result.parent.mkdir(parents=True);result.write_bytes(b'unchanged result')
        stdout=self.run_generator('--templates','dam_inflow','dam_storage.yaml')
        self.assertIn('add=20 overwrite=0 skip=0',stdout)
        self.assertEqual(len(self.manifest()['evaluation']['projects']),30)
        self.assertEqual(self.manifest()['evaluation']['projects'][:10],old_records)
        for relative,content in previous.items():self.assertEqual((self.destination/relative).read_bytes(),content)
        self.assertEqual(result.read_bytes(),b'unchanged result')
        backup=next((self.root/'provenance/backups').iterdir())
        self.assertEqual(files(backup/'openbench'),previous)

    def test_repair_missing_selected_file_skips_station_converted_yaml(self):
        self.run_generator('--templates','dam_storage')
        station_path=self.destination/'global/dam_storage_D.yaml'
        config=yaml.safe_load(station_path.read_text())
        config['simulation']={'E1_D':{'model':'CoLM2024','data_type':'stn','root_dir':'/prepared/stations'}}
        station_path.write_text(yaml.safe_dump(config))
        before=station_path.read_bytes()
        missing=self.destination/'global/dam_storage_L.yaml';missing.unlink()
        stdout=self.run_generator('--templates','dam_storage')
        self.assertIn('add=1 overwrite=0 skip=9',stdout)
        self.assertTrue(missing.is_file())
        self.assertEqual(station_path.read_bytes(),before)
        self.assertEqual(len(self.manifest()['evaluation']['projects']),10)

    def test_all_skipped_is_exact_noop_and_does_not_refresh(self):
        self.run_generator('--templates','streamflow')
        before=files(self.root);mtime=self.manifest_path.stat().st_mtime_ns
        with patch.object(generator.subprocess,'run',side_effect=AssertionError('noop refreshed')):
            self.assertIn('No changes',self.run_generator('--templates','streamflow',refresh=True))
        self.assertEqual(files(self.root),before)
        self.assertEqual(self.manifest_path.stat().st_mtime_ns,mtime)
        self.assertFalse((self.root/'provenance').exists())

    def test_selected_overwrite_changes_only_selected_and_resets_state(self):
        self.run_generator()
        manifest=self.manifest()
        for item in manifest['evaluation']['projects']:
            item.update(status='success',task_state='completed_unchanged',manual_status='success')
        self.save_manifest(manifest)
        previous=files(self.destination)
        directory=self.root/'templates';directory.mkdir()
        template=generator.load_templates(generator.TEMPLATE_ROOT,['dam_storage'])['dam_storage']
        template['metrics']=['RMSE'];(directory/'dam_storage.yaml').write_text(yaml.safe_dump(template))
        stdout=self.run_generator('--template-dir',str(directory),'--templates','dam_storage','--overwrite')
        self.assertIn('add=0 overwrite=10 skip=0',stdout)
        for item in self.manifest()['evaluation']['projects']:
            if item['type']=='dam_storage':
                self.assertEqual(item['status'],'missing')
                self.assertNotIn('task_state',item);self.assertNotIn('manual_status',item)
                self.assertEqual(yaml.safe_load((self.root/item['config']).read_text())['metrics'],['RMSE'])
            else:
                self.assertEqual(item,next(old for old in manifest['evaluation']['projects'] if old['project']==item['project']))
                relative=str(Path(item['config']).relative_to('config/openbench'))
                self.assertEqual((self.root/item['config']).read_bytes(),previous[relative])

    def test_identical_overwrite_preserves_record_status(self):
        self.run_generator('--templates','streamflow')
        manifest=self.manifest();manifest['evaluation']['projects'][0].update(status='success',custom='keep')
        self.save_manifest(manifest)
        self.run_generator('--templates','streamflow','--overwrite')
        self.assertEqual(self.manifest()['evaluation']['projects'],manifest['evaluation']['projects'])

    def test_explicit_years_output_and_mask_do_not_change_existing_defaults(self):
        self.run_generator('--templates','streamflow')
        before=self.manifest()['evaluation']
        old=files(self.destination)
        output=self.root/'different_output'
        self.run_generator('--templates','dam_inflow','--eval-start-year','2003',
                           '--output-root',str(output),'--mask-root',str(self.root/'different_masks'))
        evaluation=self.manifest()['evaluation']
        for field in ('start_year','end_year','mask_root','validation_issues'):
            self.assertEqual(evaluation[field],before[field])
        config=yaml.safe_load((self.destination/'global/dam_inflow_D.yaml').read_text())
        self.assertEqual(config['project']['years'],[2003,2004])
        self.assertEqual(config['project']['output_dir'],str(output))
        for relative,content in old.items():self.assertEqual((self.destination/relative).read_bytes(),content)

    def test_dry_run_new_selected_overwrite_and_full_write_nothing(self):
        before=files(self.root)
        with patch.object(generator.subprocess,'run',side_effect=AssertionError('dry-run refreshed')):
            self.assertIn('DRY-RUN',self.run_generator('--templates','dam_inflow','--dry-run',refresh=True))
            self.assertEqual(files(self.root),before)
            self.assertFalse(self.destination.exists())
            self.run_generator('--dry-run',refresh=True)
        self.assertEqual(files(self.root),before)
        self.run_generator('--templates','streamflow')
        before=files(self.root)
        self.run_generator('--templates','streamflow','--overwrite','--dry-run')
        self.run_generator('--overwrite','--dry-run')
        self.assertEqual(files(self.root),before)
        self.assertFalse((self.root/'provenance').exists())

    def test_orphan_yaml_and_unknown_template_rejected_without_writes(self):
        self.run_generator('--templates','streamflow')
        before=files(self.root)
        with self.assertRaisesRegex(ValueError,'unknown template'):
            self.run_generator('--templates','typo','--overwrite')
        self.assertEqual(files(self.root),before)
        (self.destination/'global/orphan.yaml').write_text('project: {}')
        before=files(self.root)
        with self.assertRaisesRegex(ValueError,'no project registration'):
            self.run_generator('--templates','dam_inflow')
        self.assertEqual(files(self.root),before)

    def test_manifest_conflicts_and_missing_unselected_files_fail(self):
        self.run_generator('--templates','streamflow')
        manifest=self.manifest()
        variants=[]
        duplicate=copy.deepcopy(manifest)
        duplicate['evaluation']['projects'].append(duplicate['evaluation']['projects'][0].copy())
        variants.append((duplicate,'conflicting project registration'))
        mismatch=copy.deepcopy(manifest);mismatch['evaluation']['projects'][0]['project']='different_name'
        variants.append((mismatch,'path conflict|name/config mismatch'))
        wrong_output=copy.deepcopy(manifest);wrong_output['evaluation']['projects'][0]['output_dir']='/wrong'
        variants.append((wrong_output,'output_dir/config mismatch'))
        for value,error in variants:
            with self.subTest(error=error):
                self.save_manifest(value);before=files(self.root)
                with self.assertRaisesRegex(ValueError,error):self.run_generator('--templates','streamflow','--overwrite')
                self.assertEqual(files(self.root),before)
        self.save_manifest(manifest)
        (self.destination/'global/streamflow_D.yaml').unlink()
        before=files(self.root)
        with self.assertRaisesRegex(ValueError,'unselected registered config is missing'):
            self.run_generator('--templates','dam_inflow')
        self.assertEqual(files(self.root),before)

    def test_manifest_write_failure_restores_tree_and_manifest(self):
        self.run_generator('--templates','streamflow')
        before=files(self.destination);manifest=self.manifest_path.read_bytes()
        with patch.object(generator,'write_json_atomic',side_effect=OSError('injected manifest failure')):
            with self.assertRaisesRegex(OSError,'injected manifest failure'):
                self.run_generator('--templates','dam_inflow')
        self.assertEqual(files(self.destination),before)
        self.assertEqual(self.manifest_path.read_bytes(),manifest)
        self.assertEqual(list((self.root/'config').glob('.openbench*')),[])

    def test_atomic_replace_failure_cleans_manifest_temporary_file(self):
        self.run_generator('--templates','streamflow')
        before=files(self.destination);manifest=self.manifest_path.read_bytes()
        with patch.object(generator.os,'replace',side_effect=OSError('injected atomic replace failure')):
            with self.assertRaisesRegex(OSError,'injected atomic replace failure'):
                self.run_generator('--templates','dam_inflow')
        self.assertEqual(files(self.destination),before)
        self.assertEqual(self.manifest_path.read_bytes(),manifest)
        self.assertEqual(list(self.root.glob('.manifest.yaml.tmp.*')),[])

    def test_regular_refresh_receives_merged_manifest_after_commit(self):
        self.run_generator('--templates','streamflow')
        def refresh(command,**kwargs):
            self.assertTrue(command[1].endswith('dlb_07_refresh_experiment.py'))
            self.assertEqual(len(self.manifest()['evaluation']['projects']),20)
            self.assertTrue((self.destination/'global/dam_inflow_D.yaml').is_file())
            return subprocess.CompletedProcess(command,0,'refreshed','')
        with patch.object(generator.subprocess,'run',side_effect=refresh) as mocked:
            self.assertIn('refreshed',self.run_generator('--templates','dam_inflow',refresh=True))
            mocked.assert_called_once()

    def test_symlink_target_is_not_written_during_staging(self):
        self.run_generator('--templates','streamflow')
        path=self.destination/'global/streamflow_D.yaml'
        target=path.with_suffix('.saved');path.rename(target);path.symlink_to(target)
        before=target.read_bytes()
        with self.assertRaisesRegex(ValueError,'symlink'):
            self.run_generator('--templates','streamflow','--overwrite')
        self.assertEqual(target.read_bytes(),before)

    def test_legacy_full_generation_still_requires_overwrite_and_replaces_all(self):
        self.run_generator('--templates','streamflow')
        extra=self.destination/'keep.txt';extra.write_text('old full-replacement behavior')
        with self.assertRaisesRegex(ValueError,'use --overwrite'):self.run_generator()
        self.run_generator('--overwrite')
        self.assertEqual(len(self.manifest()['evaluation']['projects']),60)
        self.assertFalse(extra.exists())


if __name__=='__main__':unittest.main()
