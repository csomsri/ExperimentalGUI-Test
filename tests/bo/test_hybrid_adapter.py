"""Contract tests for measured Python PID trials crossing into hybrid policies."""
import unittest
from dataclasses import asdict
from BO.hybrid_pid_adapter import HybridPIDAdapter
from BO.trial_metrics import evaluate_trial
from ga_pid_tuner import GainBounds
from pid_controller import PIDGains


class HybridAdapterTests(unittest.TestCase):
    def make(self, **settings):
        adapter = HybridPIDAdapter(GainBounds(), population=4, baseline_repeats=2,
            minimum_distinct=12, budget=6, **settings)
        self.addCleanup(adapter.close)
        adapter.initialize(PIDGains(.5,.1,.1))
        return adapter

    def submit(self, adapter, valid=True):
        candidate = adapter.current_candidate()
        metrics = evaluate_trial([(0,1,0,0,0,0),(1,1,0,0,0,0)],1)
        adapter.analyses[candidate.index] = dict(metrics=asdict(metrics),status='PASS')
        adapter.acceptance[candidate.index] = valid
        adapter.submit_fitness(1.,valid=valid)

    def test_all_modes_share_measured_budget_and_training_locations(self):
        for approach in ('legacy','lamarckian','baldwinian','mixed'):
            with self.subTest(approach=approach):
                a=self.make(approach=approach)
                for i in range(6):
                    measured=a.current_candidate().gains
                    self.submit(a)
                    observation=a.optimizer.observations[-1]
                    self.assertEqual(observation.candidate.values,dict(kp=measured.kp,ki=measured.ki,kd=measured.kd))
                    self.assertEqual(observation.metadata['controller_kind'],'python_nla')
                    if a.future:
                        a.future.result(timeout=3)
                        a.poll_candidate()
                self.assertTrue(a.finished)
                self.assertEqual(len(a.population),6)
                self.assertIsNotNone(a.best_candidate())

    def test_invalid_measurement_stops_without_training_or_applicable_best(self):
        a=self.make()
        with self.assertRaises(RuntimeError):
            self.submit(a,valid=False)
        self.assertIsNone(a.best_candidate())
        self.assertEqual(len(a.optimizer.safe_observations),0)
        self.assertEqual(len(a.policy.results),1)
        self.assertEqual(a.policy.state,'Stopped')

    def test_duplicate_and_missing_metrics_are_rejected(self):
        a=self.make()
        self.submit(a)
        with self.assertRaises(RuntimeError):
            a.submit_fitness(1.)
        a.future.result(timeout=3)
        a.poll_candidate()
        with self.assertRaises(RuntimeError):
            a.submit_fitness(1.)

    def test_seed_and_positive_width_bounds_are_required(self):
        with self.assertRaises(ValueError):
            HybridPIDAdapter(GainBounds(kp=(1,1)),budget=48)
        a=self.make()
        with self.assertRaises(ValueError):
            a.initialize(PIDGains(100,.1,.1))
