"""Read-only diagnostics from the exact fitted model used for a proposal.

All costs are returned in original cost units. Intervals condition on fitted
hyperparameters; predictive intervals add the GP's learned observation noise.
"""
import math


def comparison(prediction, cost, valid=True):
    """Compare a held-out observation with its pre-trial prediction."""
    if not valid or not math.isfinite(cost) or prediction.get('predicted_cost') is None:
        return {}
    error = cost - prediction['predicted_cost']
    sd = prediction['predictive_sd']
    return dict(prediction_error=error,
                standardized_error=error / sd if sd > 0 else None,
                inside_predictive_95=prediction['predictive_low'] <= cost <= prediction['predictive_high'],
                observed_improvement=prediction['reference_cost'] - cost)


def summarize_model(model, candidates, alternatives, acquisition, train_y, torch):
    model.eval()
    # Local generator avoids changing optimizer/global random state.
    points = torch.cat((candidates, alternatives), dim=0)
    with torch.no_grad():
        latent = model.posterior(points)
        predictive = model.posterior(points, observation_noise=True)
        means = -latent.mean.reshape(-1)
        variances = latent.variance.reshape(-1).clamp_min(0)
        predictive_variances = predictive.variance.reshape(-1).clamp_min(0)
        acquisition_values = acquisition(points.unsqueeze(-2)).reshape(-1)
    reference = float(-train_y.max())
    rows = []
    for i in range(len(points)):
        mean = float(means[i])
        sd = math.sqrt(float(variances[i]))
        predictive_sd = math.sqrt(float(predictive_variances[i]))
        improvement = reference - mean
        z = improvement / sd if sd > 0 else 0
        probability = 0.5 * (1 + math.erf(z / math.sqrt(2))) if sd > 0 else float(improvement > 0)
        ei = improvement * probability + sd * math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
        rows.append(dict(predicted_cost=mean, posterior_sd=sd,
                         credible_low=mean - 1.95996398454 * sd,
                         credible_high=mean + 1.95996398454 * sd,
                         predictive_sd=predictive_sd,
                         predictive_low=mean - 1.95996398454 * predictive_sd,
                         predictive_high=mean + 1.95996398454 * predictive_sd,
                         reference_cost=reference,
                         reference_kind='best_observed_valid_cost',
                         probability_improvement=probability, expected_improvement=max(0.0, ei),
                         acquisition_log_ei=float(acquisition_values[i]),
                         learned_noise_variance=max(0.0, float(predictive_variances[i] - variances[i])),
                         selected=i < len(candidates), values=points[i].tolist()))
    # Named parameters include likelihood noise and kernel length scales in
    # internal coordinates; prediction rows above are in original cost units.
    state = {name: value.detach().cpu().tolist() for name, value in model.state_dict().items()}
    return dict(selected=rows[:len(candidates)], candidates=rows, model_state=state,
                model_state_units='Internal normalized input / standardized objective coordinates; raw parameters may use constraints.',
                improvement_note='PI and analytic marginal EI compare latent cost with the best observed valid cost, treated as fixed. qLogEI uses Monte Carlo smoothing and need not equal log(analytic EI).',
                interval_note='Conditional on fitted GP hyperparameters; excludes drift and calibration error.',
                alternatives_note='Seeded comparison sample, not the full acquisition optimization search.')
