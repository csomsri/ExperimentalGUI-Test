# BO + PID

This package ports the reusable optimizer from RecurseIntall/Crocker-Digitlization-GUI/CrockerGUI/source/Python/Optimization. Imports were relocated to BO.optimization; the GP and qLogExpectedImprovement implementation is retained. It uses the destination application's existing AdaptiveDirectionPIDController, calibrated beam input, output callback, evaluator, and automatic trial/recovery scheduler. No second PID loop is created.

The root installer includes BO dependencies. To install them separately into the environment that launches the application:

    python -m pip install -r requirements-optimization.txt

The BO tab and existing application can load without these dependencies. Starting BO reports missing dependencies. Model fitting and proposals run on a single background worker using CPU. Stopping prevents subsequent candidates from running; a model fit already in progress may continue computing until it returns.

## Operator workflow

1. Open PID / GA AUTO-TUNE and use PID CONTROL to confirm the TC, gains, beam setpoint, timing and TC limits. Then select BO AUTO-TUNE beside GA AUTO-TUNE. Configure BO gain bounds, trial timing, score weights, initialization observations, random seed and recovery settings there. GA settings are saved and restored around BO runs. Each gain bound must have a strictly increasing minimum and maximum.
2. Enable/power the selected TC and arm PID output. On BO AUTO-TUNE, choose a trial budget, arm BO and select Start BO.
3. The current gains, clamped to the configured bounds as in GA, are measured first. BO requests Sobol initial candidates until six valid observations exist, followed by GP/qLogEI proposals. Invalid trials are excluded from training and search continues, as with GA.
4. Starting target commands are restored between trials through the same runner as GA. BO adds no beam/TC baseline tolerance, stable dwell, or recovery timeout gate.
5. At completion or after stopping, the best available valid gains can be loaded with **Apply best gains to PID**. PID remains stopped. Original working gains and target restoration follow the shared GA runner.
6. **Validate best gains (optional)** repeats the best candidate using the configured scoring duration. Its 120% cost comparison is informational and does not restrict applying gains. There is no 60-second minimum or requirement to preserve search settings.

BO retains the shared PID/GA arming, power/enable, feedback, current limits, evaluator checks and target restoration. Logging failures remain visible but do not prevent trials or applying gains, matching GA's best-effort logging behavior.

Search and validation each save an independently identified folder under runtime/bo_runs/<timestamp>/<run-id>/. The displayed logging path points to the exact folder. Files include run_settings.json (configuration, dependency versions and source hashes), run_settings.txt, trials.csv (proposed/applied gains, cost terms, validity and reason), summary.csv (GA-compatible comparison fields), per-trial response CSVs, trial-specific decision CSV folders, events.jsonl and result.json. Validation records link to the parent search run and record the acceptance threshold and measured validation result in events.jsonl. Gain application is recorded when logging is available.

Decision CSVs include run_id and trial_id, source/receive timestamps, raw/averaged feedback, range, freshness, PID decisions and output acceptance. Response CSVs distinguish warm-up from scoring; utc_estimate is an approximate wall-clock alignment from trial start plus elapsed time. Decision source timestamps remain the measurement timing authority. One-second telemetry events include beam feedback, TC target/readback and runner phase, including recovery. PID SQLite remains disabled; BO audit records do not depend on it.

Logging is best effort. Failed, invalid, and interrupted trials remain excluded from GP training. BO proposal/model errors still stop the run because a next candidate cannot be generated.

The BO tab has its own beam/error/TC response plots and best-so-far cost graph, matching the existing GA plot widgets and card theme. Shared runner messages may retain GA terminology internally. Gains from Recurse's different PID backend and historical experiments are not imported as observations.

## Verification

### GP decision statistics

New runs record pre-trial statistics without changing the acquisition function,
gain selection, training noise model, or run restrictions. In the BO trial table,
select a row to see its predicted cost, posterior SD, credible and predictive
intervals, learned observation-noise variance, probability of improvement,
analytic marginal expected improvement, and Monte Carlo qLogEI acquisition value.
The acquisition value is a ranking score, not a probability. PI and EI use the
best observed valid cost as a fixed reference, matching the current qLogEI
baseline; uncertainty in that reference is not integrated out.

The first plot compares predictions made before measurement with later valid
measurements. Bars are 95% predictive intervals. The second shows a seeded sample
of 64 alternative gain vectors and the chosen vector, with GP mean and 95%
credible intervals projected against Kp. Ki and Kd vary: this is not a fixed-gain
slice or an exhaustive picture of the acquisition search.

Each BO run folder includes:

- `gp_decisions.csv`: linked pre-trial statistics and later measured cost,
  prediction error, standardized error and interval coverage. Baseline/Sobol
  rows have unavailable GP statistics, not zero uncertainty.
- `gp_models/model_###.json`: the exact proposing model state, training gains,
  costs and trial IDs, seed, fitting warnings, timings and interval definitions.
  Raw model state uses internal transformed coordinates; exported cost
  predictions, SDs and noise variance use original cost units.
- `gp_candidates/decision_###.csv`: the chosen proposal and sampled alternatives.
- `gp_calibration.csv`: valid held-out prediction errors, running RMSE and
  empirical 95% predictive-interval coverage. Coverage is descriptive and can
  be unstable with few adaptively selected trials.
- `trials.csv` and `events.jsonl`: linked decision/result statistics as nested
  JSON, in addition to the existing measured score components and response data.

Statistics use the exact GP fitted for the proposal and are saved before PID
starts that trial. Predictions refer to proposed gains; the PID fields can round
executed gains to six decimal places, and both vectors are recorded. Diagnostics
are best effort: failures are reported without stopping BO or replacing gains.
Plots and predictions condition on fitted hyperparameters and do not include
calibration error or hardware drift. Predictive intervals add the GP's inferred
shared observation noise; per-trial measured cost variance is not yet supplied.

No statistics are retroactively inferred for older runs.

    python scripts/run_tests.py

These tests use deterministic proposal substitutes and fake readbacks; they require neither Qt, BO dependencies nor hardware. Full application UI checks require PyQt6 and the application's normal dependencies. Exercising actual GP fitting additionally requires requirements-optimization.txt.

The legacy test_pid_modules.py currently passes trend_tolerance to AdaptiveDirectionSettings, which the existing PID backend no longer accepts. This predates the BO tab changes.

Optional offscreen UI and full simulated search/validation/apply checks:

    python scripts/run_tests.py --ui

The implementation was checked with 16 hardware-free integration/audit tests and two offscreen UI/workflow tests. For this machine's UI checks, the installed Qt6/PySide6 runtime was mapped to the PyQt6 imports in the test harness only. Production imports remain PyQt6. Native PyQt6 execution, live hardware and real GP fitting have not been verified. images/bo_ui_preview.png shows the rendered BO workspace.
