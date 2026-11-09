"""Hybrid state-machine and real C++ beam-PID/recovery tests; no hardware."""
import os
import sys
import time
import unittest
import math
from dataclasses import replace
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from BO.optimization.hybrid_pid_optimizer import HybridPIDOptimizer, HybridConfig
from BO.optimization.pid_gain_adapter import PidGainCandidate, PidTrialResult
from BO.trial_metrics import evaluate_trial, trial_cost


def result(candidate,cost=10,safe=True,settled=True,beam_mae=None):
    error = cost*.001 if beam_mae is None else beam_mae
    metrics = evaluate_trial([(0,1-error,error,0,50,0),(1,1-error,error,0,50,0)],1)
    metrics = replace(metrics,settled=settled)
    return PidTrialResult(candidate,cost,0,0,0,0,safe,metrics=metrics)


class PolicyTest(unittest.TestCase):
    def test_reference_repeats_preserve_ga_slot_and_update_noise(self):
        o=self.make(reference_interval=2)
        for _ in range(4):
            self.feed(o)
        index=o.ga.index
        self.assertEqual(self.feed(o,9),'Reference')
        self.assertEqual(self.feed(o,11),'Reference')
        self.assertEqual(o.ga.index,index)
        self.assertEqual(o.state,'GA')
        self.assertAlmostEqual(o.noise,math.sqrt(2))
        self.assertAlmostEqual(o.error_noise,math.sqrt(2)*.001)
        self.assertFalse(o.records[-1]['reference_check']['cost']['drift'])
        self.assertEqual(o.results[-1].candidate,o.seed_gains)
        self.assertEqual(len(o.optimizer.safe_observations),6)

    def test_reference_cost_or_error_drift_stops_and_invalidates_best(self):
        for cost,error in ((20,.01),(10,.02)):
            o=self.make(reference_interval=2)
            for _ in range(4):
                self.feed(o)
            self.feed(o,cost,beam_mae=error)
            self.feed(o,cost,beam_mae=error)
            self.assertTrue(o.reference_drift_detected)
            self.assertEqual(o.state,'Stopped')
            self.assertIsNone(o.best_result)
            with self.assertRaises(RuntimeError):
                o.propose_batch(1)

    def test_exploration_trains_bo_even_when_poor_without_consuming_ga(self):
        candidates=[]
        for _ in range(2):
            o=self.make(exploration_interval=2)
            for _ in range(4):
                self.feed(o)
            index=o.ga.index
            self.assertEqual(self.feed(o,100),'Exploration')
            self.assertEqual(o.ga.index,index)
            self.assertEqual(len(o.optimizer.safe_observations),5)
            candidate=o.results[-1].candidate
            candidates.append(candidate)
            self.assertTrue(all(0<=v<=2 for v in (candidate.kp,candidate.ki,candidate.kd)))
            self.assertNotIn(candidate,[r.candidate for r in o.results[:-1]])
        self.assertEqual(*candidates)

    def test_confirmation_order_balanced_reproducible_and_not_interrupted(self):
        orders=[]
        for _ in range(2):
            o=self.make()
            self.warm(o)
            self.feed(o,5)
            o.config=replace(o.config,reference_interval=1,exploration_interval=1)
            order=o.records[-1]['confirmation_order']
            orders.append(order)
            pairs=[order[i:i+2] for i in range(0,6,2)]
            self.assertTrue(all(set(p)=={'Confirm incumbent','Confirm BO'} for p in pairs))
            self.assertIn(sum(p[0]=='Confirm BO' for p in pairs),(1,2))
            for source in order:
                self.assertEqual(self.feed(o,5 if source=='Confirm BO' else 10),source)
            self.assertEqual(o.state,'BO')
            self.assertEqual(self.feed(o),'Reference')
        self.assertEqual(*orders)

    def test_reference_batch_not_started_if_budget_cannot_cover_it(self):
        o=self.make(reference_interval=100,budget=7)
        self.warm(o)
        o.config=replace(o.config,reference_interval=1)
        self.assertNotEqual(self.feed(o),'Reference')
        with self.assertRaises(RuntimeError):
            o.propose_batch(1)

    def test_invalid_response_labels_are_json_serializable(self):
        import json
        o=self.make()
        self.feed(o,safe=False)
        self.assertFalse(o.feasibility_observations[0]['valid'])
        json.dumps(o.feasibility_observations,allow_nan=False)

    def test_mae_is_time_weighted_and_not_integrated_error(self):
        m = evaluate_trial([(0,0,0,0,50,0),(1,0,4,0,50,0),(3,0,1,0,50,0)],1)
        self.assertEqual(m.tracking_error, 6)
        self.assertEqual(m.mean_absolute_error, 2)

    def test_lower_cost_with_equal_or_worse_beam_error_cannot_confirm(self):
        for error in (.01, .02, .0099):
            with self.subTest(error=error):
                o=self.make()
                self.warm(o)
                self.feed(o,1,beam_mae=error)
                self.assertEqual(o.state,'GA')
                self.assertFalse(o.records[-1]['beam_error_gate']['passed'])

    def test_beam_noise_can_reject_challenger_despite_clear_cost_improvement(self):
        o=self.make()
        self.feed(o,10,beam_mae=.005)
        self.feed(o,10,beam_mae=.015)
        for _ in range(4):
            self.feed(o,10,beam_mae=.01)
        self.feed(o,1,beam_mae=.001)
        self.assertEqual(o.state,'GA')
        self.assertGreater(o.records[-1]['beam_error_gate']['required_nA'], .009)

    def test_confirmation_rejects_error_regression_and_inconsistent_improvement(self):
        for errors in ((.02,.02,.02),(.009,.009,0)):
            with self.subTest(errors=errors):
                o=self.make()
                self.warm(o)
                self.feed(o,5,beam_mae=.005)
                self.assertEqual(o.state,'Confirmation')
                index=0
                for _ in range(6):
                    c=o.propose_batch(1)[0]
                    is_bo=o.pending_source=='Confirm BO'
                    error=errors[index] if is_bo else .01
                    index += int(is_bo)
                    o.record_results([result(c,5 if is_bo else 10,beam_mae=error)])
                self.assertEqual(o.state,'GA')
                self.assertFalse(o.records[-1]['beam_error_gate']['passed'])

    def test_missing_error_metric_cannot_confirm(self):
        o=self.make()
        self.warm(o)
        c=o.propose_batch(1)[0]
        r=result(c,1)
        o.record_results([replace(r,metrics=replace(r.metrics,mean_absolute_error=None))])
        self.assertEqual(o.state,'GA')
        self.assertIsNone(o.records[-1]['beam_error_gate']['improvement_nA'])

    def make(self, **overrides):
        settings=dict(population=4,baseline_repeats=2,minimum_distinct=4,coverage=.05,
                      plateau_trials=2,budget=40,reference_interval=100,exploration_interval=100)
        settings.update(overrides)
        config=HybridConfig(**settings)
        return HybridPIDOptimizer([(0,2)]*3,PidGainCandidate(.5,.5,.5),config,
            proposer=lambda:(PidGainCandidate(.2,.2,.2),5,.1))

    def feed(self,o,cost=10,**kwargs):
        c=o.propose_batch(1)[0]
        source=o.pending_source
        o.record_results([result(c,cost,**kwargs)])
        return source

    def warm(self,o):
        for _ in range(6):
            self.feed(o)
        self.assertEqual(o.state,'GA')
        self.assertTrue(o.readiness()[0])

    def promote(self,o):
        self.warm(o)
        self.assertEqual(self.feed(o,5),'BO challenger')
        self.assertEqual(o.state,'Confirmation')
        for _ in range(6):
            c=o.propose_batch(1)[0]
            o.record_results([result(c,5 if o.pending_source == 'Confirm BO' else 10)])
        self.assertEqual(o.state,'BO')

    def test_ga_seeds_bo_without_any_sobol_proposals(self):
        o=self.make()
        o.optimizer._propose_sobol_batch=lambda _:self.fail('Hybrid must not ask BO for seed points')
        self.warm(o)
        self.assertEqual(len(o.optimizer.safe_observations),6)
        c=o.propose_batch(1)[0]
        self.assertEqual(o.pending_source,'BO challenger')
        self.assertEqual(o.state,'BO challenger')
        self.assertNotEqual(o.state,'BO')
        o.record_results([result(c,12)])
        self.assertEqual(o.state,'GA')

    def test_measured_paired_handover_and_plateau_fallback(self):
        o=self.make()
        self.promote(o)
        self.feed(o,8)
        self.feed(o,8)
        self.assertEqual(o.state,'GA')
        self.assertEqual(self.feed(o),'GA')

    def test_unsettled_challenger_is_ranked_by_fitness(self):
        o=self.make()
        self.warm(o)
        self.feed(o,1,settled=False)
        self.assertEqual(o.state,'Confirmation')

    def test_noise_and_uncertainty_prevent_challenger_trial(self):
        o=self.make()
        self.feed(o,1)
        self.feed(o,19)
        for _ in range(4):
            self.feed(o,10)
        self.assertEqual(self.feed(o,10),'GA')
        o=self.make()
        self.warm(o)
        o._proposer=lambda:(PidGainCandidate(.2,.2,.2),1,20)
        self.assertEqual(self.feed(o),'GA')

    def test_fault_stops_and_is_excluded_from_training(self):
        o=self.make()
        self.feed(o,safe=False)
        self.assertEqual(o.state,'Stopped')
        self.assertEqual(len(o.safe_results),0)
        self.assertIsNone(o.records[0]['cost'])
        with self.assertRaises(RuntimeError):
            o.propose_batch(1)

    def test_confirmation_budget_cannot_promote_on_partial_evidence(self):
        o=self.make()
        o.config=replace(o.config,budget=8)
        self.warm(o)
        self.feed(o,5)
        self.feed(o,10)
        with self.assertRaises(RuntimeError):
            o.propose_batch(1)
        self.assertEqual(o.state,'Confirmation')

    def test_unstable_confirmation_and_mismatched_result(self):
        o=self.make()
        self.warm(o)
        self.feed(o,5)
        for i in range(6):
            c=o.propose_batch(1)[0]
            is_bo=o.pending_source=='Confirm BO'
            o.record_results([result(c,5 if is_bo else 10,settled=not is_bo)])
        self.assertEqual(o.state,'BO')
        o.propose_batch(1)
        with self.assertRaises(ValueError):
            o.record_results([result(PidGainCandidate(99,99,99))])

    def test_real_botorch_proposal_uses_ga_observations_and_cost_units(self):
        o=self.make()
        for _ in range(6):
            c=o.propose_batch(1)[0]
            cost=1+sum((v-.25)**2 for v in (c.kp,c.ki,c.kd))
            o.record_results([result(c,cost)])
        o.optimizer.mc_samples=16
        o.optimizer.num_restarts=2
        o.optimizer.raw_samples=32
        c,mu,sigma=o._model_proposal()
        self.assertTrue(all(0<=v<=2 for v in (c.kp,c.ki,c.kd)))
        self.assertTrue(math.isfinite(mu) and math.isfinite(sigma) and sigma>=0)
        self.assertTrue(o.optimizer._botorch_ready)
