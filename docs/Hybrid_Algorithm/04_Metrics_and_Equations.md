# Measurements, fitness, and decision equations

## Response samples and diagnostics

[evaluate_trial()](../../source/Python/Optimization/trial_metrics.py) accepts rows
`(seconds, measurement, error, effort[, command, saturated])`. Values must be
finite, rows must be time ordered and consistently shaped, and saturation must
be 0 or 1. Duplicate timestamps replace the earlier row. At least two distinct
timestamps are needed. Four-column samples permit diagnostics but cannot produce
the current actuator-aware cost.

Right-endpoint integration produces total tracking error and effort. Full-response
MAE is `sum(abs(error_i) * dt_i) / duration`, in nA. Steady-error and RMS diagnostics
use the final 20% of response duration. Settling requires the final uninterrupted
in-tolerance segment to last the configured hold duration. The tolerance is the
maximum of the quality threshold (or 0.1), 1% of `max(abs(target), 1)`, and deadband.

Hysteretic turning points detect completed error swings larger than twice the
oscillation threshold. Diagnostics include cycles, amplitude, a sustained flag,
and a diagnostic penalty. With quality settings, sustained oscillation also
requires the configured duration and cycle minimum. These report diagnostics
are distinct from the October oscillation term used in fitness.

## Scalar fitness actually used

`trial_cost(metrics, profile)` currently returns `metrics.october_score`. The
profile argument does not switch the objective. The diagnostic oscillation
penalty is not directly added by this function.

The adapter invokes `GACandidateEvaluator` with its default configuration and
combines terms using the default weights:

```text
J = 1.0 * tracking + 2.0 * steady_state + 0.25 * movement
    + 5.0 * saturation + 1.0 * oscillation
```

Tracking is time-weighted mean absolute error divided by the evaluator's error
normalization (by default `max(abs(setpoint_nA), 1e-6)`). Steady-state fitness uses the evaluator's final configured time
window (default five seconds) and the same normalization, which differs from the report's final-20%
diagnostic. Movement is total variation of command targets, including the first
move from the captured baseline, divided by the movement normalization (default
10 A). Saturation
is the fraction of samples flagged at a limit. Oscillation is excess normalized
error variation plus nonzero-sign crossings, divided by the number of sample
intervals. Exact normalization defaults live in
[GAEvaluationConfig](../../source/Python/Automation/ga_evaluation.py).

Warmup excludes early samples from tracking/steady fitness; movement, saturation,
and oscillation use the full response. Full-response MAE diagnostics still use
the response supplied to `evaluate_trial()`. A failed or incomplete trial gets a
finite sentinel score in the page and is excluded from cost-model training.

## Notation and noise

Let `f` be `improvement_fraction`, `J` cost, `E` beam MAE, `mu` predicted cost,
and `sigma` posterior cost standard deviation. `sJ` is the larger of the original
baseline cost sample standard deviation and the last passing reference-batch
cost standard deviation. `sE` is defined independently for MAE in nA. Missing
baseline MAE makes its noise margin infinite, preventing the MAE gate.

All improvement comparisons below are strict: equality fails.

## Readiness

For each gain axis with bounds `[lo, hi]`:

```text
normalized_gain = (gain - lo) / (hi - lo)
span_axis = max(normalized_gain) - min(normalized_gain)
ready = distinct_valid_vectors >= minimum_distinct
        and min(span_kp, span_ki, span_kd) >= coverage
```

For bounds 0 to 10 and tested values 2 to 6, that axis spans 40%. All three axes
must pass. This is a diversity heuristic, not a calibrated model-accuracy test.
The policy's normalization requires positive spans; use nonzero-width bounds.

## Legacy gates

```text
cost_margin = max(f * abs(incumbent_mean_J), 2*sJ, 1e-9)
promising_prediction = mu + sigma < incumbent_mean_J - cost_margin
measured_screen = incumbent_mean_J - challenger_J > cost_margin
MAE_screen = incumbent_mean_E - challenger_E
             > max(f*incumbent_mean_E, 2*sE, 1e-9)
```

For confirmation, pair differences are `dJ_i = incumbent_J_i - challenger_J_i`
and `dE_i = incumbent_E_i - challenger_E_i`. Each mean improvement must exceed:

```text
cost_required = max(f*abs(mean(incumbent_J)), 2*sJ,
                    t*stdev(dJ)/sqrt(n), 1e-9)
MAE_required = max(f*mean(incumbent_E), 2*sE,
                   t*stdev(dE)/sqrt(n), 1e-9)
```

The code's two-sided 95% Student-t lookup uses `t=4.303` for three pairs and
entries through ten pairs. Counts beyond ten use the fixed fallback 2.262,
rather than computing a new critical value. Correlation, sequential selection
and repeated testing mean these thresholds are operational rules, not a formal
global confidence guarantee.

A BO prediction miss is:

```text
abs(measured_J - mu) > max(3*sigma, 2*sJ, f*abs(mu), 1e-9)
```

## Windowed local learning

For center `c`, local radius `r`, and full axis span `hi-lo`:

```text
local_lo = max(lo, c - r*(hi-lo))
local_hi = min(hi, c + r*(hi-lo))
margin = max(f*abs(current_learned_J), 2*sJ, 1e-9)
run_refinement = new_vector != center
                 and mu + uncertainty_multiplier*sigma < current_learned_J-margin
accept_measured_refinement = eligible(result)
                             and current_learned_J-new_J > margin
```

The GP fits data across the active window using full-bound input normalization;
only acquisition optimization is restricted to the local box.

## Reference drift

For each metric independently, compare original baseline batch B and current
reference batch R at the same seed gains:

```text
shift = abs(mean(R) - mean(B))
threshold = max(drift_fraction*abs(mean(B)),
                3*stdev(B)*sqrt(1/len(B) + 1/len(R)), 1e-9)
drift = shift > threshold
```

Missing/nonfinite cost or MAE counts as drift. Either metric stopping the session
sets `reference_drift_detected`, prevents further proposals, and removes the
best-gain validation path. A passing batch refreshes the noise estimates. Windowed
forgetting does not disable this comparison with the original baseline.
