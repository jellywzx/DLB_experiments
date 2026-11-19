"""Overwrite checks use synthetic inputs, never real models or schedulers."""
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from dlb_overwrite import backup_files
from test_openbench_jobs import fixture, run
import test_experiment_templates as fixtures


def snapshot(root):
    return {str(p.relative_to(root)):(p.is_dir(),None if p.is_dir() else p.read_bytes(),p.stat().st_mtime_ns)
            for p in root.rglob('*')}


class BackupTests(unittest.TestCase):
    def test_backups_preserve_contents_and_refuse_links(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);a=root/'a';a.write_bytes(b'old')
            backup=backup_files([a,root/'missing'],root)
            a.write_bytes(b'new')
            self.assertEqual((backup/'a').read_bytes(),b'old')
            link=root/'link';link.symlink_to(a)
            with self.assertRaisesRegex(ValueError,'regular generated file'):
                backup_files([a,link],root)

    def test_failed_backup_prevents_replacement(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);a=root/'a';a.write_bytes(b'old')
            with patch('dlb_overwrite.shutil.copy2',side_effect=OSError('no space')):
                with self.assertRaises(OSError):backup_files([a],root)
            self.assertEqual(a.read_bytes(),b'old')


class OpenBenchPreviewTests(unittest.TestCase):
    def test_preview_writes_nothing_on_fresh_and_existing_projects(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);exe=fixture(root)
            for existing in (False,True):
                if existing:
                    run(root,exe,'-D','--overwrite')
                    output=root/'openbench_output/study_basin_streamflow_D'
                    output.mkdir(parents=True);(output/'old.nc').write_bytes(b'old')
                before=snapshot(root)
                for flags in (('-D',),('-D','--overwrite'),('-D','--retry-failed')):
                    result=run(root,exe,*flags,'--dry-run',ok=False)
                    # A changed pending list may refuse an old snapshot, but may never write.
                    self.assertIn(result.returncode,(0,2))
                    self.assertEqual(snapshot(root),before)

    def test_job_and_entry_overwrite_have_backups(self):
        from dlb_09_submit_openbench import generate_submit_entries
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);exe=fixture(root)
            run(root,exe,'-D','--overwrite')
            folder=root/'config/openbench_jobs'
            job=folder/'submit_openbench_D_overwrite_array.lsf';old=job.read_bytes()
            run(root,exe,'-D','--overwrite','--cores','2')
            self.assertEqual(next((folder/'.dlb_backups').glob('*/'+job.name)).read_bytes(),old)
            entry=folder/'submit_openbench_D.sh';entry.write_text('custom entry')
            before=snapshot(root)
            generate_submit_entries(root,overwrite=True,dry_run=True)
            self.assertEqual(snapshot(root),before)
            generate_submit_entries(root,overwrite=True)
            self.assertTrue(any(p.read_text()=='custom entry'
                                for p in (folder/'.dlb_backups').glob('*/'+entry.name)))


class GeneratorRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.setUpModule()

    @classmethod
    def tearDownClass(cls):fixtures.tearDownModule()

    def test_flat_generators_preview_and_backup(self):
        tools=fixtures.TOOLS
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);flat=root/'flat';jobs=root/'jobs'
            namelist=[str(tools/'dlb_02_generate_namelists.py'),'--site-config',str(fixtures.PROFILE),
                      '--out-dir',str(flat),'--region','Mekong','--include-dam-init']
            job=[str(tools/'dlb_03_generate_jobs.py'),'--site-config',str(fixtures.PROFILE),
                 '--nml-dir',str(flat),'--out-dir',str(jobs)]
            fixtures.run(*namelist,'--dry-run');self.assertFalse(flat.exists())
            fixtures.run(*namelist)
            fixtures.run(*job,'--dry-run');self.assertFalse(jobs.exists())
            fixtures.run(*job)
            for command,folder in ((namelist,flat),(job,jobs)):
                sentinel=folder/'unrelated';sentinel.write_text('keep')
                before=snapshot(root)
                fixtures.run(*command,'--overwrite','--dry-run')
                self.assertEqual(snapshot(root),before)
                original={p.name:p.read_bytes() for p in folder.iterdir() if p.is_file() and p!=sentinel}
                fixtures.run(*command,'--overwrite')
                backup=next((folder/'.dlb_backups').iterdir())
                self.assertEqual({p.name:p.read_bytes() for p in backup.iterdir()},original)
                self.assertEqual(sentinel.read_text(),'keep')

    def test_experiment_regeneration_preview_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);target=root/'experiment'
            fixtures.run(str(fixtures.TOOLS/'dlb_01_create_experiment.py'),'EXP969','preview','Mekong',
                         '--site-config',str(fixtures.PROFILE),'--output-dir',str(target),'--draft')
            before=snapshot(root)
            fixtures.run(str(fixtures.TOOLS/'dlb_02_generate_namelists.py'),
                         '--experiment-dir',str(target),'--overwrite','--dry-run')
            self.assertEqual(snapshot(root),before)


class OverlayPreviewTests(unittest.TestCase):
    def test_explicit_preview_preserves_overlay_and_snapshots(self):
        from test_resopsus_setup import ResOpsUSSetupTests
        case=ResOpsUSSetupTests('test_preview_writes_nothing')
        case.setUp()
        try:
            case.run_setup('--apply')
            before=snapshot(case.root)
            case.run_setup('--overwrite','--dry-run')
            self.assertEqual(snapshot(case.root),before)
        finally:case.doCleanups()
