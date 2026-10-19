"""Execute the actual PID tab's decision methods with test doubles for Qt widgets.
No real GUI, LabVIEW connection, or hardware calls are created.
"""
from __future__ import annotations
import ast
from collections import deque
from enum import Enum
import math
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
from test_single_loop_pid import ROOT, extract_method, fill
from pid_beam_buffer import BeamSampleBuffer
from pid_controller import AdaptiveDirectionPIDController, AdaptiveDirectionSettings, PIDGains, PIDLimits
from pid_decision_log import PIDDecisionLog

class Mode(str,Enum):
    MANUAL='MANUAL';PID='PID'

class Widget:
    def __init__(self,v=0):self.v=v;self.text='';self.enabled=True
    def value(self):return self.v
    def currentData(self):return self.v
    def setText(self,t):self.text=str(t)
    def setEnabled(self,b):self.enabled=b
    def setValue(self,v):self.v=v
    def isChecked(self):return bool(self.v)
    def setChecked(self,b):self.v=b

class Timer:
    def __init__(self):self.active=False;self.interval=0
    def setInterval(self,n):self.interval=n
    def start(self):self.active=True
    def stop(self):self.active=False

class Context:
    def __init__(self):
        self.channel_names=[f'TC{i}' for i in range(1,13)]
        self.target_limits=[(0.,800.)]*12;self.targets=[620.]*12;self.mode=Mode.MANUAL
    def can_write(self,m):return self.mode==m
    def set_mode(self,m):self.mode=m
    def channel_name(self,i):return self.channel_names[i]
    def target(self,i):return self.targets[i]
    def actual(self,i):return 619.9
    def set_target_limits(self,i,a,b):self.target_limits[i]=(a,b)
    def clamp_target(self,i,x):a,b=self.target_limits[i];return max(a,min(b,x))

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.clock=types.SimpleNamespace(t=10.)
        self.clock.monotonic=lambda:self.clock.t
        self.time_patch=patch("pid_beam_buffer.time",self.clock)
        self.time_patch.start()
        self.buffer=BeamSampleBuffer()
        methods=['_beam_stale_limit','_beam_value','_beam_window_snapshot','_beam_display_snapshot',
                 '_on_pid_timing_changed','_decision_settings','_record_simple_decision',
                 '_pid_settings','start_pid','stop_pid','reset_pid','_pid_step']
        source='from __future__ import annotations\nclass RuntimeTab:\n'+''.join(
            extract_method('pid_ga_control_tab.py','PIDGAControlTab',m)+'\n\n' for m in methods)
        ns=dict(math=math,time=self.clock,BEAM_BUFFER=self.buffer,
                calibrate_beam_mean=lambda x,i:x,ControlMode=Mode,Path=Path,
                PIDGains=PIDGains,PIDLimits=PIDLimits,AdaptiveDirectionSettings=AdaptiveDirectionSettings,
                __file__=str(Path(self.temp.name)/'pid_ga_control_tab.py'))
        exec(compile(source,'actual_pid_tab_methods','exec'),ns)
        t=self.tab=ns['RuntimeTab']();t.context=Context();t.pid=AdaptiveDirectionPIDController()
        t._beam_cycle_mark=None;t._last_pid_average=None;t._last_average_generation=None
        t._last_processed_beam_timestamp=0.;t._last_pid_time=0.;t._plot_t0=10.
        t._pid_running=False;t._ga_auto_active=False;t._ga_starting_pid=False
        t._actuator_index=t._pending_actuator_index=t._pid_channel=9
        t._decision_log=PIDDecisionLog();t._pid_timer=Timer()
        settings={'setpoint_spin':3.,'kp_spin':1.,'ki_spin':0.,'kd_spin':0.,'loop_period_spin':300,
                  'beam_average_spin':300,'response_wait_spin':0,'derivative_tau_spin':.2,'deadband_spin':.2,
                  'trend_tolerance_spin':.1,'beam_stale_spin':1.5,'tc_min_spin':0.,'tc_max_spin':800.,
                  'initial_direction_combo':1,'reset_i_deadband_check':False,'reset_i_reverse_check':True,
                  'arm_output_check':False}
        for name,v in settings.items():setattr(t,name,Widget(v))
        for name in ('pid_status_label','actuator_selector','apply_actuator_button',
                     'output_display','magnitude_display','p_display','i_display','d_display',
                     'error_display','abs_error_display','direction_display','current_target_display',
                     'proposed_target_display'):
            setattr(t,name,Widget())
        for name in ('_plot_t','_plot_setpoint','_plot_actual','_plot_abs_error','_plot_deadband',
                     '_plot_tc_target','_plot_tc_actual'):setattr(t,name,deque())
        self.calls=[];self.calculated=[]
        t.pid_output_calculated=types.SimpleNamespace(emit=lambda *a:self.calculated.append(a))
        t._reset_plot=lambda:None;t._start_pid_log_session=lambda *a:None
        t._stop_pid_log_session=lambda *a:None;t._refresh_live_readouts=lambda:None
        t._update_actuator_selection_controls=lambda:None;t._set_trend_display=lambda *a:None
        t._direction_text=lambda v:str(v);t._log_pid_sample=lambda **k:None
        def callback(i,d,r):
            self.calls.append((i,d,r));t.context.targets[i]+=d;return True
        t.output_callback=callback
        fill(self.buffer,start=9.7,n=30,value=2.)

    def tearDown(self):
        self.tab._decision_log.close();self.time_patch.stop();self.temp.cleanup()
    def feed(self,value=2.,n=30,dt=.01):
        start=self.clock.t;fill(self.buffer,start=start,n=n,dt=dt,value=value)
        self.clock.t=round(start+n*dt,9)

    def test_preview_never_calls_hardware_callback(self):
        t=self.tab;t.start_pid();self.feed();t._pid_step()
        self.assertTrue(t._pid_running);self.assertEqual(len(self.calls),0)
        self.assertEqual(t.context.target(9),620.);self.assertEqual(len(self.calculated),1)
        self.assertEqual(t._last_pid_average['sample_count'],30)

    def test_no_complete_mean_no_math_no_command(self):
        t=self.tab;t.arm_output_check.v=True;t.start_pid();self.feed(n=10);t._pid_step()
        self.assertTrue(t._pid_running);self.assertEqual(self.calls,[]);self.assertEqual(self.calculated,[])
        self.assertIn('HOLD',t.pid_status_label.text)

    def test_immediate_reversal_through_actual_gui_method(self):
        t=self.tab;t.arm_output_check.v=True;t.start_pid();self.feed(2.);t._pid_step()
        self.feed(1.8);t._pid_step()
        self.assertEqual(len(self.calls),2)
        self.assertGreater(self.calls[0][1],0);self.assertLess(self.calls[1][1],0)
        self.assertTrue(self.calls[1][2].direction_changed)
        self.assertAlmostEqual(t.context.target(9),619.94)

    def test_no_sample_reuse_on_repeated_tick(self):
        t=self.tab;t.arm_output_check.v=True;t.start_pid();self.feed();t._pid_step()
        self.clock.t+=.3;t._pid_step();self.assertEqual(len(self.calls),1)

    def test_deadband_returns_zero_increment(self):
        t=self.tab;t.arm_output_check.v=True;t.start_pid();self.feed(3.0);t._pid_step()
        self.assertTrue(t.pid.last_result.in_deadband);self.assertEqual(self.calls[-1][1],0)
        self.assertEqual(t.context.target(9),620.)

    def test_stale_feedback_stops_without_new_write(self):
        t=self.tab;t.arm_output_check.v=True;t.start_pid();self.clock.t+=2;t._pid_step()
        self.assertFalse(t._pid_running);self.assertEqual(self.calls,[])

    def test_target_clamping_is_retained(self):
        t=self.tab;t.arm_output_check.v=True;t.tc_max_spin.v=620.1;t.start_pid()
        self.feed();t._pid_step();self.assertAlmostEqual(t.context.target(9),620.1)
        self.assertAlmostEqual(self.calls[-1][1],.1)

    def test_callback_rejection_stops(self):
        t=self.tab;t.arm_output_check.v=True;t.output_callback=lambda *a:False;t.start_pid()
        self.feed();t._pid_step();self.assertFalse(t._pid_running)
        self.assertIn('REJECTED',t.pid_status_label.text)

    def test_mode_loss_stops_without_write(self):
        t=self.tab;t.arm_output_check.v=True;t.start_pid();t.context.mode=Mode.MANUAL
        self.feed();t._pid_step();self.assertFalse(t._pid_running);self.assertEqual(self.calls,[])

    def test_actuator_change_stops_without_write(self):
        t=self.tab;t.arm_output_check.v=True;t.start_pid();t._actuator_index=8
        self.feed();t._pid_step();self.assertFalse(t._pid_running);self.assertEqual(self.calls,[])

    def test_editing_timing_refills_average_and_restarts_timer(self):
        t=self.tab;t.start_pid();self.feed();t._pid_step()
        t.loop_period_spin.v=500;t._on_pid_timing_changed()
        self.assertEqual(t._pid_timer.interval,500);self.assertIsNone(t._last_pid_average)
        self.assertEqual(t._last_processed_beam_timestamp,0)
        t._pid_step();self.assertEqual(len(self.calculated),1)

    def test_range_change_resets_error_reference(self):
        t=self.tab;t.arm_output_check.v=True;t.start_pid();self.feed(2.);t._pid_step()
        fill(self.buffer,start=self.clock.t,n=30,value=1.,index=3)
        self.clock.t+=.3;t._pid_step()
        self.assertFalse(t.pid.last_result.direction_changed)
        self.assertEqual(t._last_pid_average['range_index'],3)

    def test_actual_display_uses_same_mean_as_decision(self):
        t=self.tab;t.start_pid();self.feed(2.2);t._pid_step()
        self.feed(8.,n=5)
        self.assertAlmostEqual(t._beam_display_snapshot()['value_nA'],2.2)

    def test_no_data_refuses_start(self):
        self.buffer.clear();self.tab.start_pid();self.assertFalse(self.tab._pid_running)

    def test_malformed_new_data_stops(self):
        t=self.tab;t.start_pid();self.buffer.add(float('nan'),2,1010.01,10.01)
        self.clock.t=10.3;t._pid_step();self.assertFalse(t._pid_running)

    def test_stop_resets_freshness_gate(self):
        t=self.tab;t.start_pid();t.stop_pid(reason='test')
        self.assertFalse(t._pid_timer.active);self.assertIsNone(t._beam_cycle_mark)
        self.assertEqual(t.context.mode,Mode.MANUAL)


    def test_response_wait_holds_without_math_or_command(self):
        t=self.tab;t.response_wait_spin.v=500;t.arm_output_check.v=True
        t.start_pid();self.feed();t._pid_step()
        previous=t.pid.last_result
        self.feed(99.,n=30);t._pid_step()
        self.assertEqual(len(self.calls),1)
        self.assertIs(t.pid.last_result,previous)
        self.assertIn('RESPONSE WAIT',t.pid_status_label.text)
        self.assertTrue(t._pid_running)

    def test_response_wait_samples_excluded_from_next_average(self):
        t=self.tab;t.response_wait_spin.v=200;t.arm_output_check.v=True
        t.start_pid();self.feed();t._pid_step()
        deadline=t._beam_cycle_mark.receive_time
        self.feed(99.,n=20);t._pid_step()
        self.assertEqual(len(self.calls),1)
        self.feed(2.1,n=30);t._pid_step()
        self.assertEqual(len(self.calls),2)
        self.assertEqual(t._last_pid_average['sample_count'],30)
        self.assertAlmostEqual(t._last_pid_average['value_nA'],2.1)
        self.assertGreater(t._last_pid_average['receive_first_s'],deadline)
        self.assertAlmostEqual(self.calls[-1][2].error,.9)

    def test_no_response_wait_before_first_baseline(self):
        t=self.tab;t.response_wait_spin.v=1000;t.arm_output_check.v=True
        t.start_pid();self.feed();t._pid_step()
        self.assertEqual(len(self.calls),1)
        self.assertAlmostEqual(t._beam_cycle_mark.receive_time,11.3)

    def test_deadband_does_not_add_an_unnecessary_response_wait(self):
        t=self.tab;t.response_wait_spin.v=1000;t.arm_output_check.v=True
        t.start_pid();self.feed(3.0);t._pid_step()
        self.assertEqual(self.calls[-1][1],0.)
        self.assertAlmostEqual(t._beam_cycle_mark.receive_time,self.clock.t)
        self.feed(3.0);t._pid_step()
        self.assertEqual(len(self.calls),2)

    def test_response_wait_preview_has_identical_timing_no_hardware(self):
        t=self.tab;t.response_wait_spin.v=1000;t.start_pid()
        self.feed();t._pid_step();self.feed();t._pid_step()
        self.assertEqual(len(self.calculated),1)
        self.assertEqual(self.calls,[])
        self.assertIn('RESPONSE WAIT',t.pid_status_label.text)

    def test_stale_feedback_still_stops_during_response_wait(self):
        t=self.tab;t.response_wait_spin.v=10000;t.arm_output_check.v=True
        t.start_pid();self.feed();t._pid_step()
        self.clock.t+=1.6;t._pid_step()
        self.assertFalse(t._pid_running);self.assertEqual(len(self.calls),1)
        self.assertIn('STALE',t.pid_status_label.text)

    def test_invalid_feedback_still_stops_during_response_wait(self):
        t=self.tab;t.response_wait_spin.v=10000;t.arm_output_check.v=True
        t.start_pid();self.feed();t._pid_step()
        self.buffer.add(float('nan'),2,1010.31,10.31)
        self.clock.t=10.6;t._pid_step()
        self.assertFalse(t._pid_running);self.assertEqual(len(self.calls),1)

    def test_wait_longer_than_stale_limit_ok_with_continuous_fresh_samples(self):
        t=self.tab;t.response_wait_spin.v=2000;t.arm_output_check.v=True
        t.start_pid();self.feed();t._pid_step()
        for _ in range(6):
            self.feed(10.);t._pid_step()
        self.assertTrue(t._pid_running)
        self.assertEqual(len(self.calls),1)
        self.feed(2.,n=20);t._pid_step()
        self.assertEqual(len(self.calls),1)
        self.feed(2.1,n=30);t._pid_step()
        self.assertTrue(t._pid_running);self.assertEqual(len(self.calls),2)
        self.assertAlmostEqual(t._last_pid_average['value_nA'],2.1)

    def test_response_wait_edit_restarts_wait_and_error_reference(self):
        t=self.tab;t.arm_output_check.v=True;t.start_pid();self.feed();t._pid_step()
        t.response_wait_spin.v=400;t._on_pid_timing_changed()
        self.assertEqual(t.pid.direction,1)
        self.assertIsNone(t._last_pid_average)
        self.assertAlmostEqual(t._beam_cycle_mark.receive_time,self.clock.t+.4)
        self.feed(99.,n=40);t._pid_step()
        self.assertEqual(len(self.calls),1)
        self.feed(1.,n=30);t._pid_step()
        self.assertEqual(len(self.calls),2)
        self.assertFalse(t.pid.last_result.direction_changed)
        self.assertGreater(self.calls[-1][1],0)

    def test_response_wait_csv_records_setting_and_next_gate(self):
        import csv
        t=self.tab;t.response_wait_spin.v=250;t.start_pid();self.feed();t._pid_step()
        t.stop_pid(reason='test')
        with t._decision_log.path.open(newline='',encoding='utf-8') as f:
            rows=list(csv.DictReader(f))
        decisions=[r for r in rows if r['event']=='DECISION']
        self.assertEqual(decisions[0]['response_wait_ms'],'250')
        self.assertAlmostEqual(float(decisions[0]['next_average_after_receive_mono_s']),10.55)

    def test_stop_during_wait_does_not_issue_a_correction(self):
        t=self.tab;t.response_wait_spin.v=2000;t.arm_output_check.v=True
        t.start_pid();self.feed();t._pid_step();t.stop_pid(reason='operator stop')
        self.feed();t._pid_step()
        self.assertFalse(t._pid_running);self.assertEqual(len(self.calls),1)
        self.assertIsNone(t._beam_cycle_mark)

    def test_response_wait_followed_by_incomplete_average_still_holds(self):
        t=self.tab;t.response_wait_spin.v=300;t.arm_output_check.v=True
        t.start_pid();self.feed();t._pid_step();self.feed(99.,n=30)
        self.feed(2.1,n=10);t._pid_step()
        self.assertEqual(len(self.calls),1);self.assertIn('HOLD',t.pid_status_label.text)
        self.feed(2.1,n=20);t._pid_step();self.assertEqual(len(self.calls),2)

    def test_wait_gate_begins_after_callback_returns(self):
        t=self.tab;t.response_wait_spin.v=200;t.arm_output_check.v=True
        original=t.output_callback
        def slow_callback(*args):
            self.clock.t+=.4
            return original(*args)
        t.output_callback=slow_callback
        t.start_pid();self.feed();t._pid_step()
        self.assertAlmostEqual(self.clock.t,10.7)
        self.assertAlmostEqual(t._beam_cycle_mark.receive_time,10.9)

    def test_wait_never_uses_sleep_or_blocks_gui(self):
        source=extract_method('pid_ga_control_tab.py','PIDGAControlTab','_beam_window_snapshot')
        source+=extract_method('pid_ga_control_tab.py','PIDGAControlTab','_pid_step')
        self.assertNotIn('sleep(',source)
        self.assertNotIn('processEvents(',source)

    def test_response_wait_editor_and_live_signal_present(self):
        txt=(ROOT/'pid_ga_control_tab.py').read_text(encoding='utf-8-sig')
        self.assertIn('self.response_wait_spin.setRange(0, 10000)',txt)
        self.assertIn('self.response_wait_spin.setValue(0)',txt)
        self.assertIn('pair("Response wait", self.response_wait_spin',txt)
        self.assertIn('self.response_wait_spin.valueChanged.connect(self._on_pid_timing_changed)',txt)
        self.assertNotIn('direction_check_spin',txt)

if __name__=='__main__':unittest.main(verbosity=2)
