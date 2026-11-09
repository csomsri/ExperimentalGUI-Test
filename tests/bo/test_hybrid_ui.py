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

    def test_mode_controls_and_validation_fingerprint(self):
        h = self.page.hybrid_tab
        self.assertEqual(self.page.control_tabs.tabText(3), 'HYBRID GA + BO')
        self.assertFalse(h.hybrid_editors['window_size'].isEnabled())
        initial = h._fingerprint()
        h.approach.setCurrentIndex(2)
        self.assertTrue(h.hybrid_editors['window_size'].isEnabled())
        self.assertFalse(h.hybrid_editors['confirmation_pairs'].isEnabled())
        self.assertNotEqual(initial,h._fingerprint())
        self.assertFalse(h.arm.isChecked())
        self.assertFalse(h.apply.isEnabled())

    def test_search_validation_and_apply_with_fake_feedback_and_proposals(self):
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
        bo.testing['max_settling_time_s'].setValue(.5)
        bo.testing['hold_time_s'].setValue(.2)
        bo.testing['max_gap_s'].setValue(.5)
        bo.testing['half_period_s'].setValue(2)
        bo.testing['transitions'].setValue(2)
        bo.testing['repeats'].setValue(2)
        bo.interleave.setChecked(False)
        bo.limits_approved.setChecked(True)
        original_ga = p.ga_evaluation_spin.value()
        with tempfile.TemporaryDirectory() as root, \
                patch.object(module, 'project_file', side_effect=lambda name: Path(root) / name), \
                patch('BO.bo_control_tab.importlib.util.find_spec', return_value=object()), \
                patch('BO.bo_trial_runner.candidate_readiness', return_value=True), \
                patch('BO.optimization.BotorchBayesianOptimizer.propose_batch',
                      return_value=[OptimizationCandidate(dict(kp=0.5, ki=0.1, kd=0.1))]):
            p.arm_output_check.setChecked(True)
            bo.arm.setChecked(True)
            bo.pilot_passed = True
            bo.pilot_settings = bo._fingerprint()
            bo._start()
            self.assertTrue(p._ga_auto_active, bo.status.text())
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
            search_id = bo.search_run_id
            self.assertTrue((p._ga_run_dir / 'trials.csv').exists())
            if bo.figure_future is not None:
                bo.figure_future.result(timeout=30)
            self.assertTrue((p._ga_run_dir / 'responsiveness.png').exists())
            self.assertTrue((p._ga_run_dir / 'measured_performance.png').exists())
            self.assertTrue((p._ga_run_dir / 'uncertainty_calibration.png').exists())
            self.assertTrue(list(p._ga_run_dir.glob('trial_*_decisions/*.csv')))
            p.arm_output_check.setChecked(True)
            bo.arm.setChecked(True)
            bo._validate()
            self.assertTrue(p._ga_auto_active, bo.status.text())
            for _ in range(len(p.ga_tuner.schedule)):
                p._finish_ga_restore()
                t0 = p.ga_evaluator.start_timestamp_s
                result = None
                previous_target = p.setpoint_spin.value()
                for elapsed in [i/10 for i in range(1,62)]:
                    p._advance_bo_waveform(t0+elapsed)
                    update = p.ga_evaluator.observe(timestamp_s=t0+elapsed,
                        beam_nA=previous_target,tc_target_a=0,tc_actual_a=0,
                        pid_delta_a=0,saturated=False)
                    result = update.result or result
                    previous_target = p.setpoint_spin.value()
                p._complete_automatic_candidate(result)
            p._finish_ga_restore()
            bo._refresh()
            self.assertTrue(bo.validated, bo.status.text())
            self.assertEqual(p.ga_tuner.parent_search_run_id, search_id)
            bo._apply()
            self.assertFalse(p._pid_running)
            self.assertIn('loaded', bo.status.text())
            if bo.figure_future is not None:
                bo.figure_future.result(timeout=30)
            self.assertTrue((p._ga_run_dir / 'validation_summary.json').exists())
