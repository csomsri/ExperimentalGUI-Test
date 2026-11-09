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
        self.context = ControllerContext([f'TC{i+1}' for i in range(12)], [0.0]*12)
        self.page = PIDGAControlTab(self.context, output_callback=lambda *args: True)
        self.addCleanup(self.page.close)

    def test_extra_safety_controls_removed_from_both_workspaces(self):
        for tab in (self.page.bo_tab, self.page.hybrid_tab):
            for name in ('testing','limits_approved','pilot','validate','interleave'):
                self.assertFalse(hasattr(tab,name),name)
            self.assertFalse(tab.arm.isChecked())
            self.assertFalse(tab.apply.isEnabled())
        self.assertNotIn('drift_fraction',self.page.hybrid_tab.hybrid_editors)
        self.assertNotIn('reference_interval',self.page.hybrid_tab.hybrid_editors)

    def test_ga_arming_power_enable_and_feedback_checks_are_retained(self):
        p = self.page
        for tab in (p.bo_tab,p.hybrid_tab):
            tab._start()
            self.assertIn('Arm',tab.status.text())
            tab.arm.setChecked(True)
            p.arm_output_check.setChecked(True)
            with patch('BO.bo_control_tab.importlib.util.find_spec',return_value=object()):
                tab._start()
            self.assertFalse(p._ga_auto_active)
            self.assertIn('POWER IS OFF',tab.status.text())
            self.context.power_states[p._actuator_index]=True
            with patch('BO.bo_control_tab.importlib.util.find_spec',return_value=object()):
                tab._start()
            self.assertIn('NOT ENABLED',tab.status.text())
            self.context.enable_states[p._actuator_index]=True
            with patch('BO.bo_control_tab.importlib.util.find_spec',return_value=object()):
                tab._start()
            self.assertIn('FEEDBACK',tab.status.text())
            self.context.power_states[p._actuator_index]=False
            self.context.enable_states[p._actuator_index]=False

    def test_logging_failure_is_not_an_extra_start_gate(self):
        p=self.page
        self.context.power_states[:]=[True]*12
        self.context.enable_states[:]=[True]*12
        p._beam_window_snapshot=lambda **kw:dict(valid=True,fresh=True,ready=True,
            value_nA=1,sample_count=10,range_index=0,age_s=0)
        for tab in (p.bo_tab,p.hybrid_tab):
            p.arm_output_check.setChecked(True)
            tab.arm.setChecked(True)
            with patch.object(p,'_create_ga_run_directory'), patch(
                    'BO.bo_control_tab.importlib.util.find_spec',return_value=object()):
                tab._start()
            self.assertTrue(p._ga_auto_active,tab.status.text())
            self.assertFalse(p.ga_tuner.recovery['enabled'])
            self.assertFalse(p.ga_tuner.interleave_reference)
            if getattr(p.ga_tuner,'is_hybrid',False):
                self.assertGreater(p.ga_tuner.policy.config.reference_interval,p.ga_tuner.budget)
            p._finalize_automatic_ga(False,'Test completed')
            tab._refresh()

    def test_best_valid_result_does_not_require_tracking_approval(self):
        from BO.bo_pid_adapter import BOPIDAdapter
        from ga_pid_tuner import GainBounds
        from pid_controller import PIDGains
        a=BOPIDAdapter(GainBounds(),budget=1)
        self.addCleanup(a.close)
        a.initialize(PIDGains(.5,.1,.1))
        a.acceptance[0]=False
        a.submit_fitness(2.,valid=True)
        self.assertIsNotNone(a.best_candidate())
        bo=self.page.bo_tab
        bo.best=a.best_candidate()
        bo._refresh()
        self.assertTrue(bo.apply.isEnabled())
        bo._apply()
        self.assertEqual(self.page.kp_spin.value(),.5)
        self.assertFalse(self.page._pid_running)

    def test_search_and_apply_with_ga_checks(self):
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
            for _ in range(2):
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
