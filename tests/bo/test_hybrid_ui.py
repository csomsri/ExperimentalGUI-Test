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
class HybridWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from controller_context import ControllerContext
        from pid_ga_control_tab import PIDGAControlTab
        self.context = ControllerContext([f'TC{i+1}' for i in range(12)], [0.0]*12)
        self.page = PIDGAControlTab(self.context, output_callback=lambda *args: True)
        self.addCleanup(self.page.close)

    def test_mode_controls(self):
        h=self.page.hybrid_tab
        self.assertFalse(h.hybrid_editors['window_size'].isEnabled())
        h.approach.setCurrentIndex(2)
        self.assertTrue(h.hybrid_editors['window_size'].isEnabled())
        self.assertFalse(h.hybrid_editors['confirmation_pairs'].isEnabled())

    def test_search_and_apply_with_ga_checks(self):
        import pid_ga_control_tab as module
        from BO.optimization import OptimizationCandidate
        p, bo = self.page, self.page.hybrid_tab
        self.context.power_states[:] = [True] * 12
        self.context.enable_states[:] = [True] * 12
        snapshot = dict(valid=True, fresh=True, ready=True, value_nA=1.0,
                        sample_count=10, range_index=0, age_s=0.0)
        p._beam_window_snapshot = lambda **kw: dict(snapshot)
        p.setpoint_spin.setValue(1.0)
        bo.budget.setValue(6)
        bo.hybrid_editors['baseline_repeats'].setValue(2)
        bo.hybrid_editors['population'].setValue(4)
        bo.editors['ga_evaluation_spin'].setValue(3)
        bo.editors['ga_warmup_spin'].setValue(0)
        original_ga = p.ga_evaluation_spin.value()
        with tempfile.TemporaryDirectory() as root, \
                patch.object(module, 'project_file', side_effect=lambda name: Path(root) / name), \
                patch('BO.bo_control_tab.importlib.util.find_spec', return_value=object()), \
                patch('BO.optimization.BotorchBayesianOptimizer.propose_batch',
                      return_value=[OptimizationCandidate(dict(kp=0.5, ki=0.1, kd=0.1))]):
            p.arm_output_check.setChecked(True)
            bo.arm.setChecked(True)
            bo._start()
            self.assertTrue(p._ga_auto_active, bo.status.text())
            bo._refresh()
            for _ in range(6):
                p._finish_ga_restore()
                self.assertTrue(p._pid_running, p.ga_status_label.text())
                t0 = p.ga_evaluator.start_timestamp_s
                result = None
                for elapsed in [i/10 for i in range(1,32)]:
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
            self.assertTrue(bo.apply.isEnabled())
            best = bo.best.gains
            bo._apply()
            self.assertFalse(p._pid_running)
            self.assertEqual(p.kp_spin.value(), round(best.kp,6))
            self.assertIn('loaded',bo.status.text())
            self.assertTrue((p._ga_run_dir / 'trials.csv').exists())
            if bo.figure_future is not None:
                bo.figure_future.result(timeout=30)
