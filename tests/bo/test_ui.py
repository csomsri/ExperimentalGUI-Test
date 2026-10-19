"""Offscreen Qt checks; requires the application's PyQt6 environment."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, 'PyQt6 is not installed')
class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from controller_context import ControllerContext
        from pid_ga_control_tab import PIDGAControlTab
        self.context = ControllerContext([f'TC{i+1}' for i in range(12)], [0.0] * 12)
        self.page = PIDGAControlTab(self.context, output_callback=lambda *args: True)
        self.addCleanup(self.page.close)

    def test_inner_tabs_independent_settings_and_arming(self):
        p, bo = self.page, self.page.bo_tab
        self.assertEqual([p.control_tabs.tabText(i) for i in range(3)],
                         ['PID CONTROL', 'GA AUTO-TUNE', 'BO AUTO-TUNE'])
        original = p.ga_evaluation_spin.value()
        bo.editors['ga_evaluation_spin'].setValue(12)
        self.assertEqual(p.ga_evaluation_spin.value(), original)
        self.assertFalse(bo.apply.isEnabled())
        bo._start()
        self.assertIn('Arm', bo.status.text())

    def test_apply_best_without_validation_or_logging(self):
        from types import SimpleNamespace
        from pid_controller import PIDGains
        bo = self.page.bo_tab
        bo.best = SimpleNamespace(gains=PIDGains(0.5, 0.1, 0.01))
        bo.validated = False
        with patch.object(self.page, '_bo_event', return_value=False):
            bo._refresh()
            self.assertTrue(bo.apply.isEnabled())
            bo._apply()
        self.assertEqual(self.page.kp_spin.value(), 0.5)
        self.assertFalse(self.page._pid_running)

    def test_search_validation_and_apply_with_fake_feedback_and_proposals(self):
        import pid_ga_control_tab as module
        from BO.optimization import OptimizationCandidate
        p, bo = self.page, self.page.bo_tab
        self.context.power_states[:] = [True] * 12
        self.context.enable_states[:] = [True] * 12
        snapshot = dict(valid=True, fresh=True, ready=True, value_nA=1.0,
                        sample_count=10, range_index=0, age_s=0.0)
        p._beam_window_snapshot = lambda **kw: dict(snapshot)
        p.setpoint_spin.setValue(1.0)
        bo.budget.setValue(2)
        bo.editors['ga_evaluation_spin'].setValue(1)
        original_ga = p.ga_evaluation_spin.value()
        with tempfile.TemporaryDirectory() as root, \
                patch.object(module, 'project_file', side_effect=lambda name: Path(root) / name), \
                patch('BO.bo_control_tab.importlib.util.find_spec', return_value=object()), \
                patch('BO.bo_trial_runner.recovery_ready', return_value=True), \
                patch('BO.optimization.BotorchBayesianOptimizer.propose_batch',
                      return_value=[OptimizationCandidate(dict(kp=0.5, ki=0.1, kd=0.1))]):
            p.arm_output_check.setChecked(True)
            bo.arm.setChecked(True)
            bo._start()
            self.assertTrue(p._ga_auto_active, bo.status.text())
            for _ in range(2):
                p._finish_ga_restore()
                self.assertTrue(p._pid_running, p.ga_status_label.text())
                t0 = p.ga_evaluator.start_timestamp_s
                result = None
                for elapsed in (0.1, 0.5, 1.1):
                    update = p.ga_evaluator.observe(timestamp_s=t0 + elapsed, beam_nA=1.0,
                        tc_target_a=0, tc_actual_a=0, pid_delta_a=0, saturated=False)
                    result = update.result or result
                self.assertIsNotNone(result)
                p._complete_automatic_candidate(result)
                if p.ga_tuner.future:
                    p.ga_tuner.future.result(timeout=3)
            p._finish_ga_restore()
            bo._refresh()
            self.assertIsNotNone(bo.best, bo.status.text())
            self.assertEqual(p.ga_evaluation_spin.value(), original_ga)
            search_id = bo.search_run_id
            self.assertTrue((p._ga_run_dir / 'trials.csv').exists())
            self.assertTrue(list(p._ga_run_dir.glob('trial_*_decisions/*.csv')))
            p.arm_output_check.setChecked(True)
            bo.arm.setChecked(True)
            bo._validate()
            self.assertTrue(p._ga_auto_active, bo.status.text())
            p._finish_ga_restore()
            t0 = p.ga_evaluator.start_timestamp_s
            result = None
            for elapsed in (0.1, 30, 60.1):
                update = p.ga_evaluator.observe(timestamp_s=t0 + elapsed, beam_nA=1.0,
                    tc_target_a=0, tc_actual_a=0, pid_delta_a=0, saturated=False)
                result = update.result or result
            p._complete_automatic_candidate(result)
            p._finish_ga_restore()
            bo._refresh()
            self.assertTrue(bo.validated, bo.status.text())
            self.assertEqual(p.ga_tuner.parent_search_run_id, search_id)
            bo._apply()
            self.assertFalse(p._pid_running)
            self.assertIn('loaded', bo.status.text())


if __name__ == '__main__':
    unittest.main()
