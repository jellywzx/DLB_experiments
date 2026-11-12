#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

TOOLS=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(TOOLS))

import dlb_06_stage_status as status


def fixture_study():
    root=Path('/synthetic/cases')

    def run(label:str):
        return SimpleNamespace(label=label,case='case_'+label,case_dir=root/('case_'+label))

    return SimpleNamespace(nml_dir=Path('/synthetic/config/colm'),case_root=root,
        spinup=run('SPINUP'),dam_init=None,
        experiments=tuple(run(f'E{i}') for i in range(8)),
        eval_start_year=2001,eval_end_year=2005)


class StageStatusTests(unittest.TestCase):
    @patch.object(status,'marker',return_value=True)
    @patch.object(status,'month_files',return_value=(60,60,[]))
    @patch.object(status,'restart_ready',return_value=(True,[]))
    @patch.object(status,'load_study')
    def test_complete_uses_manifest_targets_and_accepts_compatibility_flag(
            self,load_study,restart_ready,month_files,marker):
        load_study.return_value=fixture_study()
        argv=['dlb_06_stage_status.py','--nml-dir','/synthetic/config/colm',
              '--allow-nonstandard-schedule','--require-complete','--json']
        with patch.object(sys,'argv',argv),contextlib.redirect_stdout(io.StringIO()) as output:
            code=status.main()
        self.assertEqual(code,0)
        self.assertTrue(json.loads(output.getvalue())['colm_complete'])
        load_study.assert_called_once_with(Path('/synthetic/config/colm'),
                                           allow_draft=False,validate_schedule=False)
        self.assertTrue(all(call.args[1]==2006 for call in restart_ready.call_args_list[-8:]))
        self.assertTrue(all(call.args[1:]==(2001,2005) for call in month_files.call_args_list))

    @patch.object(status,'marker',return_value=True)
    @patch.object(status,'month_files',return_value=(12,60,['missing.nc']))
    @patch.object(status,'load_study')
    def test_successful_partial_rerun_is_not_whole_experiment_complete(
            self,load_study,month_files,marker):
        load_study.return_value=fixture_study()

        def restart_ready(run,year,grid_only=False):
            return year!=2006,[]

        argv=['dlb_06_stage_status.py','--nml-dir','/synthetic/config/colm',
              '--require-complete','--json']
        with patch.object(status,'restart_ready',side_effect=restart_ready), \
                patch.object(sys,'argv',argv),contextlib.redirect_stdout(io.StringIO()) as output:
            code=status.main()
        result=json.loads(output.getvalue())
        self.assertEqual(code,1)
        self.assertFalse(result['colm_complete'])
        self.assertTrue(all(not row['complete'] for row in result['records'][-1]['experiments']))


if __name__=='__main__':
    unittest.main()
