"""Experiment audit and UI configuration isolation checks without hardware."""
import ast
import csv
import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

from BO.experiment_log import ExperimentLog
from ga_test_runner import GACandidateEvaluator, GAEvaluationConfig
from pid_controller import PIDGains


class LoggingTests(unittest.TestCase):
    @staticmethod
    def runner_method(name):
        source = Path(__file__).resolve().parents[2] / 'python/app/pid_ga_control_tab.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PIDGAControlTab')
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
        namespace = {'math': math}
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<runner>', 'exec',
                     flags=__import__('__future__').annotations.compiler_flag), namespace)
        return namespace[name]

    def test_event_failure_is_visible(self):
        log = SimpleNamespace(event=Mock(side_effect=OSError('disk full')), error='')
        page = SimpleNamespace(_bo_log=log)
        self.assertFalse(self.runner_method('_bo_event')(page, 'TRIAL_START'))
        self.assertEqual(log.error, 'disk full')

    def test_linked_trial_metadata_and_nonfinite_abort(self):
        with tempfile.TemporaryDirectory() as root:
            log = ExperimentLog(root, {'strategy': 'BO', 'parent_search_run_id': 'search-1'})
            log.trial_id = log.run_id + ':001'
            evaluator = GACandidateEvaluator(GAEvaluationConfig(warmup_s=0))
            evaluator.start(candidate_number=1, generation_number=1, gains=PIDGains(),
                setpoint_nA=1, baseline_target_a=0, baseline_actual_a=0, timestamp_s=1)
            result = evaluator.abort('feedback lost', timestamp_s=2)
            tuner = SimpleNamespace(proposal_source='baseline', proposed_gains={'kp': 0, 'ki': 0, 'kd': 0})
            log.result(result, tuner)
            log.event('NONFINITE', value=math.nan)
            settings = json.loads((log.path / 'run_settings.json').read_text())
            self.assertEqual(settings['parent_search_run_id'], 'search-1')
            self.assertIn('pid_controller.py', settings['source_sha256'])
            with (log.path / 'trials.csv').open(newline='') as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(row['trial_id'], log.trial_id)
            self.assertEqual(row['valid'], 'False')
            events = [json.loads(line) for line in (log.path / 'events.jsonl').read_text().splitlines()]
            self.assertIsNone(events[-1]['value'])
            self.assertEqual(events[-2]['reason'], 'feedback lost')

    def test_unique_runs_and_atomic_result_replacement(self):
        with tempfile.TemporaryDirectory() as root:
            first, second = ExperimentLog(root, {}), ExperimentLog(root, {})
            self.assertNotEqual(first.run_id, second.run_id)
            first.json('result.json', {'success': False})
            first.json('result.json', {'success': True})
            self.assertTrue(json.loads((first.path / 'result.json').read_text())['success'])
            self.assertFalse((first.path / 'result.json.tmp').exists())

    def test_restores_ga_settings_and_recovery_scope(self):
        tree = ast.parse((Path(__file__).resolve().parents[2] / 'python/app/BO/bo_control_tab.py').read_text(encoding='utf-8'))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_restore_ga_settings')
        namespace = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<restore>', 'exec'), namespace)
        editor, scope = Mock(), Mock()
        page = SimpleNamespace(saved_ga_settings={'ga_evaluation_spin': 30}, saved_scope=1,
                               pid_tab=SimpleNamespace(ga_evaluation_spin=editor, ga_recovery_scope_combo=scope))
        namespace['_restore_ga_settings'](page)
        editor.setValue.assert_called_once_with(30)
        scope.setCurrentIndex.assert_called_once_with(1)
        self.assertIsNone(page.saved_ga_settings)


if __name__ == '__main__':
    unittest.main()
