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

    def test_actual_telemetry_timestamp_is_not_refreshed_by_cached_values(self):
        from unittest.mock import patch
        with patch('controller_context.time.monotonic',return_value=10):
            self.context.update_actual_values([2]*12)
        with patch('controller_context.time.monotonic',return_value=20):
            self.context.update_actual_values([2]*12,fresh=False)
        self.assertEqual(self.context.actual_timestamps[0],10)

    def test_readiness_uses_measured_hold_before_evaluator_start(self):
        from BO.bo_pid_adapter import BOPIDAdapter
        from ga_pid_tuner import GainBounds
        from types import SimpleNamespace
        from pid_controller import PIDGains
        from controller_context import ControlMode
        import time
        p=self.page
        tuner=BOPIDAdapter(GainBounds(),budget=1)
        self.addCleanup(tuner.close)
        candidate=tuner.initialize(PIDGains(1,0,0))
        tuner.recovery=dict(enabled=True,timeout_s=10,stable_s=2,
            beam_min_na=.8,beam_max_na=1.2,tc_tolerance_a=.1,max_gap_s=1)
        p.ga_tuner=tuner
        p._ga_auto_active=True
        p._ga_sequence_state='PREPARING'
        p._ga_recovery_reference=SimpleNamespace(actual_map={p._actuator_index:0})
        p._ga_baseline_target=0
        p._bo_readiness_key=None
        self.context.set_mode(ControlMode.GA_TUNING)
        self.context.power_states[:]=[True]*12
        self.context.enable_states[:]=[True]*12
        p.auto_ga_arm_check.setChecked(True)
        p.arm_output_check.setChecked(True)
        snapshot=dict(valid=True,fresh=True,ready=True,value_nA=1,timestamp=10,receive_first_s=10)
        p._beam_window_snapshot=lambda **kw:dict(snapshot)
        with patch.object(time,'monotonic',return_value=10):
            self.context.update_actual_values([0]*12)
            p._start_automatic_candidate(candidate)
        self.assertEqual(p._ga_sequence_state,'READINESS')
        self.assertFalse(p.ga_evaluator.active)
        self.assertFalse(p._pid_running)
        for now in (11,12):
            snapshot.update(timestamp=now,receive_first_s=now)
            with patch.object(time,'monotonic',return_value=now):
                self.context.update_actual_values([0]*12)
                p._ga_sequence_tick()
        self.assertTrue(p._pid_running)
        self.assertTrue(p.ga_evaluator.active)
        p._ga_auto_active=False
        p.stop_pid(restore_manual=False)

    def test_logging_failure_blocks_start_before_pid_or_evaluator(self):
        p,bo=self.page,self.page.bo_tab
        self.context.power_states[:]=[True]*12
        self.context.enable_states[:]=[True]*12
        p._beam_window_snapshot=lambda **kw:dict(valid=True,fresh=True,ready=True,
            value_nA=1,sample_count=10,range_index=0,age_s=0)
        bo.budget.setValue(2)
        p.arm_output_check.setChecked(True)
        bo.arm.setChecked(True)
        bo.limits_approved.setChecked(True)
        with patch.object(p,'_create_ga_run_directory'), patch('BO.bo_control_tab.importlib.util.find_spec',return_value=object()):
            bo._start()
        self.assertFalse(p._ga_auto_active)
        self.assertFalse(p._pid_running)
        self.assertFalse(p.ga_evaluator.active)
        self.assertIn('LOGGING',bo.status.text())

    def test_full_search_requires_a_matching_pilot(self):
        bo=self.page.bo_tab
        self.page.arm_output_check.setChecked(True)
        bo.arm.setChecked(True)
        bo.limits_approved.setChecked(True)
        with patch('BO.bo_control_tab.importlib.util.find_spec',return_value=object()):
            bo._start()
        self.assertFalse(self.page._ga_auto_active)
        self.assertIn('pilot',bo.status.text())

    def test_current_gain_pilot_is_available_without_prior_search(self):
        bo=self.page.bo_tab
        self.page.arm_output_check.setChecked(True)
        bo.arm.setChecked(True)
        bo.testing['reference_kp'].setValue(.2)
        with patch.object(bo,'_launch') as launch:
            bo._pilot()
        launch.assert_called_once_with(validation=True)
        self.assertEqual(bo.best.gains.kp,self.page.kp_spin.value())
        self.assertEqual(bo.reference_gains.kp,.2)

    def test_apply_requires_validation(self):
        from types import SimpleNamespace
        from pid_controller import PIDGains
        bo = self.page.bo_tab
        bo.best = SimpleNamespace(gains=PIDGains(0.5, 0.1, 0.01))
        bo.validated = False
        with patch.object(self.page, '_bo_event', return_value=False):
            bo._refresh()
            self.assertFalse(bo.apply.isEnabled())
            bo._apply()
        self.assertNotEqual(self.page.kp_spin.value(), 0.5)
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
            bo._start()
            self.assertTrue(p._ga_auto_active, bo.status.text())
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


if __name__ == '__main__':
    unittest.main()
