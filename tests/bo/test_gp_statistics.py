"""Pre-trial statistics, held-out comparison and linked artifact regression tests."""
import csv
import importlib.util
import json
import math
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from BO.experiment_log import ExperimentLog
from BO.optimization.decision_statistics import comparison
from ga_test_runner import GACandidateEvaluator, GAEvaluationConfig
from pid_controller import PIDGains


class StatisticsTests(unittest.TestCase):
    def test_held_out_comparison(self):
        prediction = dict(predicted_cost=2., predictive_sd=.5,
                          predictive_low=1., predictive_high=3., reference_cost=4.)
        actual = comparison(prediction, 2.5)
        self.assertEqual(actual['prediction_error'], .5)
        self.assertEqual(actual['standardized_error'], 1.)
        self.assertEqual(actual['observed_improvement'], 1.5)
        self.assertTrue(actual['inside_predictive_95'])
        self.assertFalse(comparison(prediction, 3.5)['inside_predictive_95'])
        self.assertEqual(comparison(prediction, 2.5, False), {})
        self.assertEqual(comparison(prediction, math.nan), {})
        self.assertEqual(comparison({}, 2.5), {})

    def test_decision_is_saved_before_result_and_linked_afterward(self):
        with tempfile.TemporaryDirectory() as root:
            log = ExperimentLog(root, {})
            log.trial_id = log.run_id + ':007'
            stats = dict(predicted_cost=1., predictive_sd=.5, posterior_sd=.2,
                predictive_low=0., predictive_high=2., reference_cost=2.,
                credible_low=.6, credible_high=1.4, probability_improvement=.99,
                expected_improvement=1., acquisition_log_ei=0., values=[.5,.1,.01], selected=True)
            decision = dict(source='gp_qlogei', statistics_available=True, training_count=6,
                selected=[stats], candidates=[stats], model_state={'example': [1.]},
                training_data=[dict(trial_index=1, gains={}, cost=2.)])
            candidate = SimpleNamespace(index=6, gains=PIDGains(.5,.1,.01))
            tuner = SimpleNamespace(decisions=[{}]*6+[decision], proposed_gains={}, proposal_source='gp_qlogei')
            log.decision(tuner, candidate)
            with (log.path/'gp_decisions.csv').open(newline='') as handle:
                before = list(csv.DictReader(handle))[0]
            self.assertNotIn('measured_cost', before)
            model = json.loads((log.path/'gp_models/model_007.json').read_text())
            self.assertEqual(model['training_trial_ids'], [log.run_id+':001'])
            self.assertTrue((log.path/'gp_candidates/decision_007.csv').exists())
            evaluator = GACandidateEvaluator(GAEvaluationConfig(warmup_s=0, evaluation_s=1))
            evaluator.start(candidate_number=7, generation_number=1, gains=candidate.gains,
                setpoint_nA=1, baseline_target_a=0, baseline_actual_a=0, timestamp_s=1)
            result = None
            for timestamp in (1.1, 2.1):
                result = evaluator.observe(timestamp_s=timestamp, beam_nA=1., tc_target_a=0.,
                    tc_actual_a=0., pid_delta_a=0., saturated=False).result or result
            self.assertIsNotNone(result)
            log.result(result, tuner)
            with (log.path/'gp_decisions.csv').open(newline='') as handle:
                after = list(csv.DictReader(handle))[0]
            self.assertEqual(after['predicted_cost'], before['predicted_cost'])
            self.assertEqual(float(after['measured_cost']), result.score)
            self.assertTrue((log.path/'gp_calibration.csv').exists())

    @unittest.skipUnless(importlib.util.find_spec('botorch'), 'optional BoTorch dependency')
    def test_real_gp_statistics_and_selection_are_consistent(self):
        import torch
        from botorch.models import SingleTaskGP
        from BO.optimization.acquisition import propose_expected_improvement_batch
        x = torch.tensor([[0.], [.5], [1.]], dtype=torch.double)
        y = torch.tensor([[-1.], [-.1], [-1.]], dtype=torch.double)
        model = SingleTaskGP(x, y)
        bounds = torch.tensor([[0.], [1.]], dtype=torch.double)
        proposed = torch.tensor([[.4]], dtype=torch.double)
        kwargs = dict(model=model, train_y=y, bounds=bounds, batch_size=1, torch=torch,
                      seed=1729, mc_samples=32, num_restarts=2, raw_samples=16)
        diagnostics = {}
        # The optimizer's candidate is unchanged by diagnostics. Posteriors and
        # acquisition evaluations below are real BoTorch calculations.
        with patch('botorch.optim.optimize_acqf', return_value=(proposed, torch.tensor(0.))):
            plain = propose_expected_improvement_batch(**kwargs)
            state = torch.random.get_rng_state().clone()
            recorded = propose_expected_improvement_batch(**kwargs, diagnostics=diagnostics)
            self.assertTrue(torch.equal(state, torch.random.get_rng_state()))
        self.assertTrue(torch.equal(plain, recorded))
        self.assertNotIn('diagnostics_error', diagnostics)
        row = diagnostics['selected'][0]
        with torch.no_grad():
            expected = model.posterior(proposed)
            predictive = model.posterior(proposed, observation_noise=True)
        self.assertAlmostEqual(row['predicted_cost'], -expected.mean.item())
        self.assertAlmostEqual(row['posterior_sd']**2, expected.variance.item())
        self.assertAlmostEqual(row['predictive_sd']**2, predictive.variance.item())
        self.assertGreaterEqual(row['predictive_sd'], row['posterior_sd'])
        self.assertTrue(0 <= row['probability_improvement'] <= 1)
        self.assertGreaterEqual(row['expected_improvement'], 0)
        self.assertEqual(len(diagnostics['candidates']), 65)
        failed = {}
        with patch('botorch.optim.optimize_acqf', return_value=(proposed, torch.tensor(0.))), \
             patch('BO.optimization.decision_statistics.summarize_model', side_effect=ValueError('diagnostic failed')):
            candidate = propose_expected_improvement_batch(**kwargs, diagnostics=failed)
        self.assertTrue(torch.equal(candidate, plain))
        self.assertEqual(failed['diagnostics_error'], 'diagnostic failed')


if __name__ == '__main__':
    unittest.main()
