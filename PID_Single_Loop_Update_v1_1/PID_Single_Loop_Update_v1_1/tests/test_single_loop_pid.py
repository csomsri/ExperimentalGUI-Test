"""Software-only tests: no LabVIEW connection, no hardware output.
Run with the same Python used for the controller. Qt is not needed by these tests.
"""
from __future__ import annotations
import ast
import csv
import importlib.util
import math
import os
from pathlib import Path
import random
import sys
import tempfile
import textwrap
import types
import unittest
from collections import deque
from dataclasses import asdict
from functools import lru_cache

HERE = Path(__file__).resolve()
candidates = [Path(os.environ['CNL_PID_TEST_ROOT'])] if os.environ.get('CNL_PID_TEST_ROOT') else []
candidates += [HERE.parents[1] / 'patched', HERE.parents[1], HERE.parents[2], Path.cwd()]
ROOT = next((p for p in candidates if (p / 'pid_beam_buffer.py').is_file()), None)
if ROOT is None:
    raise SystemExit('Install the update first, or set CNL_PID_TEST_ROOT to the updated project folder.')
sys.path.insert(0, str(ROOT))
from pid_beam_buffer import BeamSampleBuffer, calibrate_beam_mean
from pid_controller import AdaptiveDirectionPIDController, AdaptiveDirectionSettings, PIDGains, PIDLimits
from pid_decision_log import PIDDecisionLog


def simple_pid(kp=1.0, ki=0.0, kd=0.0, deadband=.2, tolerance=.1, **kw):
    opts=dict(deadband=deadband, trend_tolerance=tolerance, direction_each_update=True,
              max_control_dt=.3, reset_integral_on_direction_change=True)
    opts.update(kw)
    return AdaptiveDirectionPIDController(PIDGains(kp,ki,kd),
        PIDLimits(output_min=0, output_max=100, integral_min=0, integral_max=100,
                  derivative_filter_tau=.2), AdaptiveDirectionSettings(**opts))


def fill(buffer, start=10., n=30, dt=.01, value=2., index=2, source_base=1000.):
    for k in range(1,n+1):
        v = value(k) if callable(value) else value
        buffer.add(v, index, source_base+start+k*dt, start+k*dt)


class BufferTests(unittest.TestCase):
    def test_average_all_thirty_original_samples(self):
        b=BeamSampleBuffer(); b.add(0.,2,1010.,10.); mark=b.mark(10.)
        fill(b,value=lambda k:float(k))
        s=b.snapshot(.3,1.5,lambda v,i:v,after=mark,now=10.30)
        self.assertTrue(s['ready']); self.assertEqual(s['sample_count'],30)
        self.assertAlmostEqual(s['value_nA'],15.5)

    def test_periodic_ripple_is_averaged_before_error(self):
        b=BeamSampleBuffer();fill(b,n=60,value=lambda k:2.7314+.55*math.sin(2*math.pi*40*k/100))
        s=b.snapshot(.3,1.5,lambda v,i:v,now=10.6)
        self.assertAlmostEqual(s['value_nA'],2.7314,places=9)
        self.assertEqual(simple_pid().update(2.7314,s['value_nA'],.3).output,0.)

    def test_incomplete_window_holds(self):
        b=BeamSampleBuffer();fill(b,n=10)
        s=b.snapshot(.3,1.5,lambda v,i:v,now=10.1)
        self.assertTrue(s['fresh']);self.assertFalse(s['ready'])

    def test_mark_prevents_reusing_any_old_sample(self):
        b=BeamSampleBuffer();fill(b,n=30)
        mark=b.mark(10.3)
        s=b.snapshot(.3,1.5,lambda v,i:v,after=mark,now=10.31)
        self.assertFalse(s['ready']);self.assertEqual(s['sample_count'],0)

    def test_old_queued_packets_are_not_treated_as_postcommand(self):
        b=BeamSampleBuffer();fill(b,n=30,value=100.)
        mark=b.mark(10.6)  # command issued after these packets were received
        fill(b,start=10.3,n=30,value=100.)  # delivery now, but original receive times <= 10.6
        fill(b,start=10.6,n=30,value=2.)
        s=b.snapshot(.3,1.5,lambda v,i:v,after=mark,now=10.9)
        self.assertTrue(s['ready']);self.assertAlmostEqual(s['value_nA'],2.)
        self.assertGreater(s['receive_first_s'],10.6)

    def test_duplicate_does_not_refresh_stale_timestamp(self):
        b=BeamSampleBuffer();fill(b,n=30)
        self.assertFalse(b.add(99.,2,1010.30,20.))
        s=b.snapshot(.3,1.5,lambda v,i:v,now=20.)
        self.assertFalse(s['fresh']);self.assertIn('STALE',s['status'])

    def test_backwards_source_is_rejected(self):
        b=BeamSampleBuffer();fill(b,n=30)
        self.assertFalse(b.add(99,2,1000,10.5))
        self.assertEqual(b.snapshot(.3,1.5,lambda v,i:v,now=10.5)['sample_count'],30)

    def test_constant_values_with_new_timestamps_are_valid(self):
        b=BeamSampleBuffer();fill(b,n=50,value=2.0)
        self.assertTrue(b.snapshot(.3,1.5,lambda v,i:v,now=10.5)['valid'])

    def test_missing_receive_stamp_is_not_synthesized(self):
        b=BeamSampleBuffer()
        self.assertFalse(b.add_packet(dict(timestamp=1010.,beam_current=2.,beam_range_idx=2)))
        self.assertIn('RESTART',b.snapshot(.3,1.5,lambda v,i:v,now=10.)['status'])

    def test_valid_stamped_packet(self):
        b=BeamSampleBuffer()
        self.assertTrue(b.add_packet(dict(_pid_source_s=1010.,_pid_rx_mono=10.,
                                         beam_current=2.,beam_range_idx=2)))

    def test_range_change_cannot_mix_ranges(self):
        b=BeamSampleBuffer();fill(b,n=30,value=100.,index=1)
        fill(b,start=10.3,n=10,value=2.,index=2)
        self.assertFalse(b.snapshot(.3,1.5,lambda v,i:v,now=10.4)['ready'])
        fill(b,start=10.4,n=20,value=2.,index=2)
        s=b.snapshot(.3,1.5,lambda v,i:v,now=10.6)
        self.assertTrue(s['ready']);self.assertEqual(s['range_index'],2)
        self.assertAlmostEqual(s['value_nA'],2.)

    def test_acquisition_gap_refills_the_window(self):
        b=BeamSampleBuffer();fill(b,n=30)
        fill(b,start=11.,n=5,value=5.)
        self.assertFalse(b.snapshot(.3,1.5,lambda v,i:v,now=11.05)['ready'])
        fill(b,start=11.05,n=25,value=5.)
        self.assertAlmostEqual(b.snapshot(.3,1.5,lambda v,i:v,now=11.3)['value_nA'],5.)

    def test_nonfinite_input_clears_average(self):
        b=BeamSampleBuffer();fill(b,n=30)
        self.assertFalse(b.add(float('nan'),2,1010.31,10.31))
        self.assertFalse(b.snapshot(.3,1.5,lambda v,i:v,now=10.31)['valid'])

    def test_invalid_calibration_cannot_produce_command_value(self):
        b=BeamSampleBuffer();fill(b,n=30)
        s=b.snapshot(.3,1.5,lambda v,i:float('nan'),now=10.3)
        self.assertFalse(s['valid']);self.assertIn('CALIBRATION',s['status'])

    def test_only_raw_mean_is_calibrated(self):
        b=BeamSampleBuffer();fill(b,n=30,value=lambda k:float(k))
        calls=[]
        def convert(v,i): calls.append((v,i));return v*2
        s=b.snapshot(.3,1.5,convert,now=10.3)
        self.assertEqual(len(calls),1);self.assertAlmostEqual(s['value_nA'],31.)

    def test_slow_input_reports_real_sample_count(self):
        b=BeamSampleBuffer();b.add(2.,2,1010.,10.);mark=b.mark(10.)
        fill(b,n=1,dt=.3)
        self.assertFalse(b.snapshot(.3,1.5,lambda v,i:v,after=mark,now=10.3)['ready'])
        fill(b,start=10.3,n=1,dt=.3)
        s=b.snapshot(.3,1.5,lambda v,i:v,after=mark,now=10.6)
        self.assertTrue(s['ready']);self.assertEqual(s['sample_count'],2)

    def test_real_epoch_floating_point_precision(self):
        b=BeamSampleBuffer();fill(b,n=30,source_base=1770000000.)
        s=b.snapshot(.3,1.5,lambda v,i:v,now=10.3)
        self.assertTrue(s['ready']);self.assertEqual(s['sample_count'],30)

    def test_future_receive_clock_is_not_fresh(self):
        b=BeamSampleBuffer();fill(b,n=30)
        self.assertFalse(b.snapshot(.3,1.5,lambda v,i:v,now=1.)['valid'])

    def test_bad_window_parameters_raise(self):
        b=BeamSampleBuffer()
        for w in (0,-1,float('nan'),float('inf')):
            with self.subTest(w=w),self.assertRaises(ValueError):
                b.snapshot(w,1.5,lambda v,i:v,now=10.)

    def test_bounded_buffer_does_not_fabricate_coverage(self):
        b=BeamSampleBuffer(capacity=10);fill(b,n=100)
        self.assertFalse(b.snapshot(.3,1.5,lambda v,i:v,now=11.)['ready'])


class EngineTests(unittest.TestCase):
    def test_first_average_establishes_direction_reference(self):
        p=simple_pid();r=p.update(3.,2.,.3)
        self.assertEqual(r.direction,1);self.assertFalse(r.direction_changed);self.assertGreater(r.output,0)

    def test_worsening_reverses_on_same_update(self):
        p=simple_pid();p.update(3.,2.,.3);r=p.update(3.,1.8,.3)
        self.assertTrue(r.direction_changed);self.assertEqual(r.direction,-1);self.assertLess(r.output,0)

    def test_improving_keeps_direction(self):
        p=simple_pid();p.update(3.,2.,.3);r=p.update(3.,2.2,.3)
        self.assertEqual(r.direction,1);self.assertFalse(r.direction_changed)

    def test_small_change_does_not_reverse(self):
        p=simple_pid();p.update(3.,2.,.3);r=p.update(3.,1.95,.3)
        self.assertFalse(r.direction_changed)

    def test_exact_trend_threshold_is_not_more_than_threshold(self):
        p=simple_pid();p.update(3.,2.,.3);r=p.update(3.,1.9,.3)
        self.assertFalse(r.direction_changed)

    def test_direction_tolerance_is_not_silently_raised_to_deadband(self):
        p=simple_pid(deadband=.2,tolerance=.1);p.update(3.,2.6,.3);r=p.update(3.,2.45,.3)
        self.assertTrue(r.direction_changed)

    def test_deadband_holds_target(self):
        p=simple_pid(ki=.1);p.update(2.7314,2.,.3)
        r=p.update(2.7314,2.7,.3)
        self.assertTrue(r.in_deadband);self.assertEqual(r.output,0.)

    def test_exact_deadband_boundary_holds(self):
        r=simple_pid().update(2.7314,2.5314,.3)
        self.assertTrue(r.in_deadband)

    def test_signed_error_crossing_does_not_force_reversal(self):
        p=simple_pid(deadband=0,tolerance=0)
        p.update(10.,0.,.3);r=p.update(10.,12.,.3)
        self.assertEqual(r.direction,1);self.assertFalse(r.direction_changed)
        r=p.update(10.,14.,.3);self.assertEqual(r.direction,-1)

    def test_legacy_direction_timer_is_ignored_in_simple_mode(self):
        p=simple_pid(direction_check_interval=60.,direction_confirmations=20,minimum_direction_samples=100)
        p.update(3.,2.,.3);self.assertTrue(p.update(3.,1.8,.3).direction_changed)

    def test_full_300ms_not_hidden_250ms(self):
        r=simple_pid().update(3.,2.,.3)
        self.assertAlmostEqual(r.output,.3)

    def test_delayed_packet_cannot_create_catchup_step(self):
        p=simple_pid();p.update(3.,2.,.3)
        self.assertAlmostEqual(p.update(3.,2.,1.2).output,.3)

    def test_integral_uses_elapsed_time(self):
        p=simple_pid(kp=0,ki=1);r=p.update(3.,2.,.3)
        self.assertAlmostEqual(r.integral,.09)
        r=p.update(3.,2.,.6)
        self.assertAlmostEqual(r.integral,.27)

    def test_reverse_clears_old_integral_when_selected(self):
        p=simple_pid(kp=0,ki=1)
        p.update(3.,2.,.3);r=p.update(3.,1.8,.3)
        self.assertTrue(r.direction_changed);self.assertAlmostEqual(r.integral,.108)

    def test_filter_does_not_replace_beam_average(self):
        p=simple_pid(kp=1,kd=0,deadband=0,tolerance=0)
        r=p.update(2.7314,3.1314,.3)
        self.assertGreater(r.pid_magnitude,0.)
        p.reset();r=p.update(2.7314,(2.3314+3.1314)/2.,.3)
        self.assertAlmostEqual(r.output,0.,places=10)

    def test_setpoint_change_resets_comparison(self):
        p=simple_pid();p.update(3.,2.,.3);r=p.update(6.,2.,.3)
        self.assertFalse(r.direction_changed)

    def test_reset_does_not_compare_to_old_error(self):
        p=simple_pid();p.update(3.,2.,.3);p.reset(direction=-1)
        r=p.update(3.,0.,.3);self.assertFalse(r.direction_changed);self.assertEqual(r.direction,-1)

    def test_invalid_inputs_raise(self):
        for dt in (0,-1,float('nan'),float('inf')):
            with self.subTest(dt=dt),self.assertRaises(ValueError):simple_pid().update(3,2,dt)

    def test_increment_limit_is_kept(self):
        p=simple_pid(kp=10000);self.assertLessEqual(abs(p.update(3.,0.,.3).output),100.)

    def test_no_motion_for_zero_gains(self):
        p=simple_pid(kp=0);self.assertEqual(p.update(3,2,.3).output,0)

    def test_negative_initial_direction_is_retained_on_improvement(self):
        p=simple_pid(initial_direction=-1);p.update(3,2,.3);r=p.update(3,2.3,.3)
        self.assertEqual(r.direction,-1);self.assertLess(r.output,0)

    def test_simple_synthetic_plant_both_local_response_signs(self):
        for gain in (-1.,1.):
            p=simple_pid(kp=.8,ki=0,deadband=.02,tolerance=.0001)
            target=0.
            for _ in range(200):
                r=p.update(1.,gain*target,.3);target+=r.output
                if r.in_deadband:break
            self.assertLessEqual(abs(1.-gain*target),.02)


class LogTests(unittest.TestCase):
    def test_trace_records_measured_average_and_command(self):
        with tempfile.TemporaryDirectory() as temp:
            log=PIDDecisionLog();self.assertTrue(log.start(Path(temp),'TC10',{'loop_ms':300}))
            log.write('DECISION',{'sample_count':30,'averaged_beam_nA':2.7,'target_before_A':620,
                                  'target_after_A':620.1,'armed':0,'status':'PREVIEW'})
            log.write('STOP',{'status':'operator stop'});log.close()
            with log.path.open(newline='',encoding='utf-8') as f:rows=list(csv.DictReader(f))
            self.assertEqual([r['event'] for r in rows],['START','DECISION','STOP'])
            self.assertEqual(rows[1]['sample_count'],'30');self.assertEqual(rows[1]['armed'],'0')

    def test_readonly_or_invalid_log_path_does_not_raise(self):
        with tempfile.TemporaryDirectory() as temp:
            f=Path(temp)/'file';f.write_text('not a directory')
            log=PIDDecisionLog();self.assertFalse(log.start(f,'TC10',{}));self.assertTrue(log.error)

    def test_software_does_not_assume_log_exists_before_start(self):
        self.assertFalse(PIDDecisionLog().write('DECISION',{}))


@lru_cache(maxsize=64)
def extract_method(filename, clsname, method_name):
    txt=(ROOT/filename).read_text(encoding='utf-8-sig'); tree=ast.parse(txt)
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==clsname)
    node=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==method_name)
    return ''.join(txt.splitlines(keepends=True)[node.lineno-1:node.end_lineno])


class SourceIntegrationTests(unittest.TestCase):
    def test_scalar_average_helper_executes_all_values(self):
        source=textwrap.dedent(extract_method('main_control_gui.py','MainControlWindow','_mean_scalar'))
        ns={'math':math};exec(source,ns)
        self.assertEqual(ns['_mean_scalar'](None,[1,2,3]),2)
        self.assertEqual(ns['_mean_scalar'](None,[None,float('nan'),1,3]),2)

    def test_no_direction_check_control_remains(self):
        txt=(ROOT/'pid_ga_control_tab.py').read_text(encoding='utf-8-sig')
        self.assertNotIn('direction_check_spin',txt)
        self.assertIn('beam_average_spin',txt)

    def test_pid_input_feed_precedes_gui_decimation(self):
        txt=extract_method('main_control_gui.py','MainControlWindow','_pump_data')
        self.assertLess(txt.index('feed_pid_packet(pkt)'),txt.index('self._gui_accum.append(pkt)'))

    def test_server_stamp_precedes_queue_delivery(self):
        txt=(ROOT/'zmq_rep_server.py').read_text(encoding='utf-8-sig')
        self.assertLess(txt.index('pid_rx_mono = time.monotonic()'),txt.index('buf = memoryview(frame.buffer)'))
        self.assertLess(txt.index('pkt["_pid_rx_mono"]'),txt.index('data_queue.put_nowait(pkt)'))

    def test_unmodified_target_output_adapter_is_not_bundled_as_replacement(self):
        # The patch deliberately touches no magnetic send/scaling function.
        txt=(ROOT/'pid_ga_control_tab.py').read_text(encoding='utf-8-sig')
        self.assertIn('self.output_callback(',txt)
        self.assertIn('self.context.clamp_target(',txt)

    def test_all_changed_modules_compile(self):
        for filename in ('pid_controller.py','pid_ga_control_tab.py','main_control_gui.py',
                         'zmq_rep_server.py','pid_beam_buffer.py','pid_decision_log.py'):
            compile((ROOT/filename).read_text(encoding='utf-8-sig'),filename,'exec')


if __name__=='__main__':unittest.main(verbosity=2)
