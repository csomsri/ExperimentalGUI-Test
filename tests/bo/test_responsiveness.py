"""Hardware-free acceptance, readiness, waveform and score reproducibility checks."""
import math
import unittest
from dataclasses import asdict
from unittest.mock import patch
from BO.responsiveness import evaluate_responsiveness, square_wave_setpoint, TrackingCriteria, analyze_candidate
from BO.bo_trial_runner import PhysicalReadiness
from BO.bo_pid_adapter import BOPIDAdapter, FixedGainValidationAdapter
from BO.optimization import OptimizationCandidate
from BO.trial_metrics import evaluate_trial, trial_cost
from ga_pid_tuner import GainBounds
from ga_test_runner import GACandidateEvaluator, GAEvaluationConfig, GAFitnessWeights
from pid_controller import PIDGains, AdaptiveDirectionPIDController, AdaptiveDirectionSettings


class ResponseTests(unittest.TestCase):
    def analyze(self, t, y, targets=None, events=None, **kw):
        settings = dict(error_band_nA=.2, hold_time_s=1, max_settling_time_s=3,
                        max_overshoot_nA=.3, max_gap_s=.6)
        settings.update(kw)
        return evaluate_responsiveness(t, y, targets or [1]*len(t), events or [0], **settings)

    def test_ideal_up_down_and_time_weighting(self):
        t=[i/2 for i in range(21)]
        r=[1 if x<5 else 0 for x in t]
        y=[(0 if x<1 else 1) if x<5 else (1 if x<6 else 0) for x in t]
        response=self.analyze(t,y,r,[0,5])
        self.assertEqual(response['status'],'PASS')
        self.assertEqual([e['direction'] for e in response['events']],['UP','DOWN'])
        self.assertEqual([e['settling_time_s'] for e in response['events']],[1,1])
        self.assertTrue(all(e['demonstrates_recovery'] for e in response['events']))
        self.assertAlmostEqual(response['events'][1]['mae_nA'],.1)

    def test_inactive_offset_fails_even_with_no_movement(self):
        t=[i/2 for i in range(13)]
        report=self.analyze(t,[0]*len(t))
        self.assertEqual(report['status'],'FAIL')
        self.assertIsNone(report['events'][0]['settling_time_s'])
        self.assertEqual(report['events'][0]['band_occupancy'],0)
        short=self.analyze(t[:5],[0]*5)
        self.assertEqual(short['status'],'INSUFFICIENT_DATA')

    def test_overshoot_late_event_and_logging_gap(self):
        t=[i/2 for i in range(13)]
        y=[0,1.5]+[1]*11
        self.assertEqual(self.analyze(t,y)['status'],'FAIL')
        self.assertEqual(self.analyze(t,[1]*len(t),events=[5])['status'],'INSUFFICIENT_DATA')
        self.assertEqual(self.analyze([0,.5,4,4.5,5],[1]*5)['status'],'INSUFFICIENT_DATA')

    def test_early_hold_does_not_hide_later_loss_of_tracking(self):
        t=[i/2 for i in range(13)]
        y=[1]*10+[0,0,1]
        report=self.analyze(t,y)
        self.assertEqual(report['status'],'FAIL')
        self.assertIsNone(report['events'][0]['settling_time_s'])
        self.assertEqual(report['events'][0]['first_entry_s'],0)

    def test_starting_in_band_is_explicit(self):
        result=self.analyze([i/2 for i in range(13)],[1]*13)
        self.assertEqual(result['status'],'PASS')
        self.assertTrue(result['events'][0]['starting_in_band'])
        self.assertFalse(result['events'][0]['demonstrates_recovery'])

    def test_invalid_timestamps_samples_gains_and_events_rejected(self):
        for t,y in [([0,0,1],[1]*3),([0,2,1],[1]*3),([0,1,math.nan],[1]*3),([0,1,2],[1,math.inf,1])]:
            with self.assertRaises(ValueError): self.analyze(t,y)
        with self.assertRaises(ValueError):
            self.analyze([0,1,2],[1]*3,targets=[1,2,2])
        with self.assertRaises(ValueError):
            self.analyze([0,1,2],[1]*3,gain_samples=[(1,0,0),(2,0,0),(2,0,0)])

    def test_square_wave_phase_and_invalid_settings(self):
        self.assertEqual([square_wave_setpoint(t,11,.5,30) for t in (0,29,30,60)], [10.5,10.5,11.5,10.5])
        with self.assertRaises(ValueError): square_wave_setpoint(0,11,.5,0)


class ReadinessTests(unittest.TestCase):
    def gate(self):
        return PhysicalReadiness(dict(timeout_s=10,stable_s=2,beam_min_na=9,beam_max_na=11,
            tc_tolerance_a=.1,max_gap_s=1), reference=2, now=0)
    def snap(self,t,beam=10):
        return dict(valid=True,fresh=True,ready=True,timestamp=t,value_nA=beam,actual_timestamp_s=t)
    def test_requires_measured_readback_and_postreset_hold(self):
        gate=self.gate()
        self.assertFalse(gate.poll(0,self.snap(-1),2))
        self.assertFalse(gate.poll(0,self.snap(0),3))
        for t in (1,2): self.assertFalse(gate.poll(t,self.snap(t),2))
        self.assertTrue(gate.poll(3,self.snap(3),2))
    def test_cached_average_does_not_establish_hold_and_timeout_is_bounded(self):
        gate=self.gate()
        self.assertFalse(gate.poll(1,self.snap(1),2))
        self.assertFalse(gate.poll(3,self.snap(1),2))
        with self.assertRaisesRegex(RuntimeError,'READINESS TIMEOUT'):
            gate.poll(10,self.snap(10),2)
    def test_stale_coil_readback_and_pre_reset_average_cannot_advance(self):
        gate=self.gate()
        snapshot=self.snap(3)
        snapshot['actual_timestamp_s']=1
        self.assertFalse(gate.poll(3,snapshot,2))
        snapshot=self.snap(4)
        snapshot['receive_first_s']=-1
        self.assertFalse(gate.poll(4,snapshot,2))

    def test_out_of_range_or_gap_restarts_hold(self):
        gate=self.gate()
        gate.poll(0,self.snap(0),2)
        gate.poll(1,self.snap(1,20),2)
        self.assertFalse(gate.poll(2,self.snap(2),2))
        self.assertFalse(gate.poll(4,self.snap(4),2))


class ScoringAndSelectionTests(unittest.TestCase):
    def result(self,beam,command=lambda t:0):
        evaluator=GACandidateEvaluator(GAEvaluationConfig(warmup_s=0,evaluation_s=6,steady_state_window_s=1,
            error_normalization_nA=.5,movement_normalization_a=2),
            GAFitnessWeights(tracking=2,steady_state=3,movement=.7))
        evaluator.start(candidate_number=1,generation_number=1,gains=PIDGains(1,0,0),
            setpoint_nA=1,baseline_target_a=0,timestamp_s=10)
        for i in range(1,62):
            t=i/10
            update=evaluator.observe(timestamp_s=10+t,beam_nA=beam(t),tc_target_a=command(t),
                tc_actual_a=command(t),pid_delta_a=.01,saturated=False)
        return evaluator,evaluator.result
    def test_score_terms_reproduce_and_tracking_acceptance_separates_inactive(self):
        criteria=TrackingCriteria(.2,1,3,.5,.5)
        evaluator,result=self.result(lambda t:0)
        inactive=analyze_candidate(result,evaluator,criteria)
        self.assertTrue(inactive['data_valid'])
        self.assertFalse(inactive['accepted'])
        self.assertTrue(inactive['score_matches'])
        evaluator,result=self.result(lambda t:1)
        working=analyze_candidate(result,evaluator,criteria)
        self.assertTrue(working['accepted'])
        self.assertAlmostEqual(working['metrics']['october_score'],result.score)
        evaluator,moving=self.result(lambda t:1,lambda t:10 if int(t*10)%2 else 0)
        self.assertGreater(moving.score,result.score)
        self.assertGreater(trial_cost(evaluate_trial([(0,0,1,0,0,0),(1,0,1,0,0,0)],1)),0)
    def test_performance_failure_trains_but_cannot_be_best(self):
        tuner=BOPIDAdapter(GainBounds(),budget=1)
        self.addCleanup(tuner.close)
        tuner.initialize(PIDGains())
        tuner.acceptance[0]=False
        tuner.submit_fitness(.1,valid=True)
        self.assertEqual(len(tuner.optimizer.safe_observations),1)
        self.assertIsNone(tuner.best_candidate())
    def test_reference_does_not_consume_budget_or_train(self):
        tuner=BOPIDAdapter(GainBounds(),budget=2)
        self.addCleanup(tuner.close)
        tuner.initialize(PIDGains(1,0,0))
        tuner.acceptance[0]=True
        with patch.object(tuner.optimizer,'propose_batch',return_value=[OptimizationCandidate(dict(kp=2,ki=0,kd=0))]):
            tuner.submit_fitness(2)
            tuner.future.result(timeout=3)
            ref=tuner.poll_candidate()
            self.assertEqual(tuner.role,'reference')
            self.assertEqual(ref.gains.kp,1)
            tuner.submit_fitness(.01)
            self.assertFalse(tuner.finished)
            candidate=tuner.poll_candidate()
            self.assertEqual(candidate.gains.kp,2)
            tuner.acceptance[candidate.index]=True
            tuner.submit_fitness(1)
        self.assertTrue(tuner.finished)
        self.assertEqual(len(tuner.optimizer.safe_observations),2)
        self.assertEqual(tuner.best_candidate().fitness,1)
    def test_reference_drift_blocks_otherwise_passing_validation(self):
        from types import SimpleNamespace
        from BO.validation_report import validation_summary
        analyses={}
        for condition in ('low','nominal','high'):
            for repeat in (1,2):
                for role in ('reference','validation'):
                    mae=.5 if condition=='low' and repeat==2 and role=='reference' else .1
                    analyses[len(analyses)]=dict(role=role,repeat=repeat,condition=condition,
                        accepted=True,responsiveness=dict(events=[dict(mae_nA=mae)]))
        tuner=SimpleNamespace(analyses=analyses,schedule=[None]*12,reference_drift_limit_nA=.2)
        summary=validation_summary(tuner)
        self.assertFalse(summary['passed'])
        self.assertAlmostEqual(summary['reference_drift_by_condition']['low']['mae_range_nA'],.4)
        self.assertEqual(summary['by_condition']['nominal']['reference']['n'],2)

    def test_validation_preschedules_identical_pairs_and_excludes_gp(self):
        tuner=FixedGainValidationAdapter(GainBounds(),PIDGains(1,0,0),PIDGains(2,0,0),repeats=2)
        self.addCleanup(tuner.close)
        tuner.initialize(PIDGains())
        self.assertEqual(len(tuner.schedule),12)
        for index in range(12):
            self.assertEqual(tuner.current_candidate().index,index)
            tuner.acceptance[index]=True
            tuner.submit_fitness(1)
        self.assertTrue(tuner.finished)
        self.assertEqual(len(tuner.optimizer.safe_observations),0)
        self.assertEqual(set(c['name'] for c in tuner.schedule_conditions),{'low','nominal','high'})


class ControllerReplayTests(unittest.TestCase):
    def test_adaptive_controller_tracks_upward_and_downward_in_simple_plant(self):
        for target in (1,-1):
            controller=AdaptiveDirectionPIDController(PIDGains(1,0,0),
                settings=AdaptiveDirectionSettings(deadband=.02,direction_each_update=True,max_control_dt=.1))
            command=beam=0
            for _ in range(600):
                output=controller.update(target,beam,.1)
                command+=output.output
                beam+=.2*(command-beam)
            self.assertLess(abs(beam-target),.1)
    def test_derivative_only_constant_error_is_legitimately_zero(self):
        controller=AdaptiveDirectionPIDController(PIDGains(0,0,.1),
            settings=AdaptiveDirectionSettings(direction_each_update=True))
        outputs=[controller.update(12,11,.1).output for _ in range(20)]
        self.assertTrue(all(output==0 for output in outputs))


if __name__=='__main__': unittest.main()
