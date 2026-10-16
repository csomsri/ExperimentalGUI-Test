"""Hardware-free checks: run with python -m unittest BO.test_bo_integration."""
import unittest
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
from ga_pid_tuner import GainBounds
from pid_controller import PIDGains
from BO.bo_pid_adapter import BOPIDAdapter
from BO.optimization import OptimizationCandidate
from BO.bo_trial_runner import recovery_ready


class AdapterTests(unittest.TestCase):
    def make_tuner(self, budget=2):
        tuner = BOPIDAdapter(GainBounds(), budget)
        self.addCleanup(tuner.close)
        tuner.initialize(PIDGains(1, 0.1, 0.01))
        return tuner

    def test_seed_score_worker_proposal_and_budget(self):
        tuner = self.make_tuner()
        with patch.object(tuner.optimizer, 'propose_batch', return_value=[
            OptimizationCandidate({'kp': 2, 'ki': 0.2, 'kd': 0.02})
        ]):
            self.assertIsNone(tuner.submit_fitness(10))
            self.assertTrue(tuner.proposal_pending)
            tuner.future.result(timeout=2)
            candidate = tuner.poll_candidate()
        self.assertEqual(candidate.index, 1)
        self.assertEqual(candidate.gains.kp, 2)
        tuner.submit_fitness(5)
        self.assertTrue(tuner.finished)
        self.assertEqual(tuner.best_candidate().fitness, 5)
        self.assertEqual(len(tuner.optimizer.safe_observations), 2)

    def test_invalid_trial_excluded_and_stops(self):
        tuner = self.make_tuner()
        with self.assertRaises(RuntimeError):
            tuner.submit_fitness(100, valid=False)
        self.assertIsNone(tuner.best_candidate())
        self.assertFalse(tuner.proposal_pending)
        self.assertEqual(tuner.optimizer.safe_observations, [])

    def test_proposal_failure_propagates(self):
        tuner = self.make_tuner()
        with patch.object(tuner.optimizer, 'propose_batch', side_effect=ValueError('model failure')):
            tuner.submit_fitness(10)
            with self.assertRaises(ValueError):
                tuner.future.result(timeout=2)
            with self.assertRaisesRegex(RuntimeError, 'model failure'):
                tuner.poll_candidate()

    def test_duplicate_result_rejected(self):
        tuner = self.make_tuner(1)
        tuner.submit_fitness(10)
        with self.assertRaises(RuntimeError):
            tuner.submit_fitness(9)

    def test_bounds_checked_before_trial(self):
        tuner = self.make_tuner()
        with self.assertRaises(ValueError):
            tuner.initialize(PIDGains(100, 0, 0))


class RecoveryTests(unittest.TestCase):
    def page(self):
        return SimpleNamespace(
            _ga_restore_started=10, _actuator_index=0,
            _ga_recovery_reference=SimpleNamespace(actual_map={0: 2}, beam_nA=100),
            context=SimpleNamespace(actual=lambda i: 2),
            _beam_window_snapshot=lambda: {'valid': True, 'value_nA': 100},
        )

    def test_stable_interval_required(self):
        page = self.page()
        with patch('BO.bo_trial_runner.time.monotonic', return_value=11):
            self.assertFalse(recovery_ready(page))
        with patch('BO.bo_trial_runner.time.monotonic', return_value=14):
            self.assertTrue(recovery_ready(page))

    def test_stale_feedback_resets_interval_and_times_out(self):
        page = self.page()
        page._beam_window_snapshot = lambda: {'valid': False, 'value_nA': 100}
        with patch('BO.bo_trial_runner.time.monotonic', return_value=71):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                recovery_ready(page)


class SchedulerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Exercise the actual shared scheduler method without importing Qt/hardware.
        source = Path(__file__).resolve().parents[1] / 'pid_ga_control_tab.py'
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        controller = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                          and n.name == 'PIDGAControlTab')
        method = next(n for n in controller.body if isinstance(n, ast.FunctionDef)
                      and n.name == '_finish_ga_restore')
        namespace = {'ControlMode': SimpleNamespace(GA_TUNING='GA_TUNING')}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
        cls.finish = staticmethod(namespace['_finish_ga_restore'])

    def page(self):
        return SimpleNamespace(
            _ga_abort_after_restore=False, _ga_run_complete=False,
            auto_ga_arm_check=SimpleNamespace(isChecked=lambda: True),
            ga_tuner=SimpleNamespace(is_bo=True, proposal_pending=True, poll_candidate=Mock(return_value=None)),
            ga_status_label=Mock(), _ga_pending_candidate=None,
            _finalize_automatic_ga=Mock(), _start_automatic_candidate=Mock(), context=Mock(),
        )

    def test_pending_model_never_starts_pid(self):
        page = self.page()
        with patch('BO.bo_trial_runner.recovery_ready', return_value=True):
            self.finish(page)
        page._start_automatic_candidate.assert_not_called()
        page._finalize_automatic_ga.assert_not_called()

    def test_ready_model_runs_only_after_recovery(self):
        page = self.page()
        candidate = object()
        page.ga_tuner.poll_candidate.return_value = candidate
        with patch('BO.bo_trial_runner.recovery_ready', return_value=False):
            self.finish(page)
        page.ga_tuner.poll_candidate.assert_not_called()
        with patch('BO.bo_trial_runner.recovery_ready', return_value=True):
            self.finish(page)
        page._start_automatic_candidate.assert_called_once_with(candidate)

    def test_budget_completion_waits_for_recovery(self):
        page = self.page()
        page._ga_run_complete = True
        with patch('BO.bo_trial_runner.recovery_ready', return_value=False):
            self.finish(page)
        page._finalize_automatic_ga.assert_not_called()
        with patch('BO.bo_trial_runner.recovery_ready', return_value=True):
            self.finish(page)
        self.assertTrue(page._finalize_automatic_ga.call_args.args[0])

    def test_worker_failure_ends_session_without_starting_pid(self):
        page = self.page()
        page.ga_tuner.poll_candidate.side_effect = RuntimeError('fit failed')
        with patch('BO.bo_trial_runner.recovery_ready', return_value=True):
            self.finish(page)
        page._finalize_automatic_ga.assert_called_once_with(False, 'fit failed')
        page._start_automatic_candidate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
