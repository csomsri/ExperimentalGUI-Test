# ExperimentalGUI BO/PID implementation

Updated November 6, 2026. This is the local implementation record; the supplied
controlled-testing plan remains unchanged as the requirements/evidence source.
Crocker-Digitlization-GUI was inspected read-only. Its `trial_metrics.py`
implementation was ported into this application and adapted to its existing
`ga_test_runner` imports/configuration, without importing that project's UI or
native-service architecture.

## Implemented software

| Functionality | Local source / behavior |
| --- | --- |
| Constant-target trial analysis | `python/app/BO/trial_metrics.py`: `evaluate_trial`, `trial_cost`, `TuningQuality`, `TrialMetrics`. Settling/entry, steady-state error/RMS, overshoot, oscillation, effort, time-weighted tracking, command movement and saturation. Six-column actuator recordings reproduce the existing five-term score using the actual evaluation configuration and weights. Four-column recordings remain descriptive and cannot be scored. The port retains the original `settled` flag and end-time sentinel; consult `settled` before interpreting its settling field. |
| Bounded fresh-beam wait | Existing sequence timer, retained candidate, monotonic ten-second deadline, strict deadline and final feedback/authorization checks. No evaluator/PID start, scored time, budget use or GP observation during waiting. |
| Measured readiness | `BO/bo_trial_runner.py`: bounded beam/TC range hold after command restoration. Requires post-reset full-average support, fresh beam and actual coil telemetry spanning the configured hold; software acceptance and cached values cannot establish readiness. |
| Tracking acceptance | `BO/responsiveness.py`: configurable error band, hold, settling deadline, overshoot and maximum logging gap. Data validity is recorded separately. Only tracking-accepted candidates can become the selected search result. |
| GP treatment | Original constant-target five-term objective retained and versioned. Finite data-valid performance failures train the gain-only GP; hardware/data-invalid trials do not. Reference and independent waveform-validation trials are excluded from GP training and search candidate budgets. |
| Reference drift checks | Configurable fixed reference gains interleaved between candidates by default. Failed reference tracking or reference MAE drift beyond the configured threshold stops search with normal authorized restoration. |
| Responsiveness analysis | `evaluate_responsiveness(...)`: event segmentation, upward/downward overshoot, first entry, qualifying settling/hold, final signed error, time-weighted MAE/RMSE, band occupancy and optional movement. Returns PASS/FAIL/INSUFFICIENT_DATA with reasons. Unknown settling is null. Rejects nonfinite/nonmonotonic data, unaccounted target changes and supplied gain changes. |
| Independent validation | Deterministic square-wave beam-current setpoint; fixed gains within each run. Identical waveforms for reference and candidate, paired repetitions in seeded varied order, at approved low/nominal/high centers. Every actual transition is logged; validation requires observed out-of-band recovery in both directions, separately from starting-in-band cases. A delayed/missing transition cannot produce a passing complete validation. |
| Pilot and live gate | Current-gain/reference pilot can run before search. Searches above two candidates require a passing pilot under the same settings. A two-candidate pilot search can also precede waveform validation. The operator confirms reachable targets, initial ranges, performance and actuator limits before output runs. Settings changes invalidate validation. Applying gains requires successful repeated validation and does not start PID. |
| Records / reporting | Schema 3 retains old experiments. Exports criteria, schedules, roles, conditions, actual rounded gains, initial beam/command/readback, readiness time, average provenance, original terms, weighted contributions, reproduced score, tracking outcomes and prospective GP diagnostics. `validation_summary.json` reports reference drift by condition and between-repeat SD/standard error separately from within-trial and GP variation. |
| Figures | Background export of `responsiveness.png`, `measured_performance.png`, and `uncertainty_calibration.png`; validation-only runs explicitly report that they contain no new GP predictions. Search runs retain prospective calibration plots. |

The public pure analysis functions are also exported by `BO.__init__`.

## Operator workflow

1. Configure the PID channel, beam target, coil limits, gain bounds and feedback timing.
2. In BO, set known working fixed reference gains. Set physically achievable initial
   beam bounds and measured coil tolerance; these need not equal the next target.
3. Define tracking and logging limits. Configure waveform amplitude, hold,
   transitions, low/high center offset and repetition count. Review target extremes
   and the displayed total scored-time budget. Default values are examples.
4. Confirm those settings, arm PID output and BO, and run a current-gain/reference
   pilot (or a two-candidate search followed by validation). Inspect the exports.
5. Re-arm for full search. Its scored fitness stays constant-target; waveform
   diagnostics do not silently become BO training costs.
6. Re-arm and validate selected gains independently. All scheduled reference and
   candidate repetitions must pass, and reference drift must stay within limits.
7. Apply validated gains to the stopped PID, then explicitly start PID if desired.
   Stop/Restore and both existing emergency arming controls remain usable.

Changing a target or limit requires confirming it again. Validation duration must
fit the runner's supported 3600-second per-trial bound, and each level must cover
settling deadline + required hold + maximum sample gap. Constant-target scoring
must cover settling deadline + required hold.

Fresh TC telemetry timestamps are tracked in `controller_context.py`; the queue
consumer only refreshes them for genuinely new packets. The legacy magnetic-only
shared-list demo has no producer timestamps and cannot establish physical BO
readiness. This is distinct from changing the existing PID control algorithm.

## Verification and remaining physical work

Run from the repository root with `PYTHONPATH=python/app`:

```text
python -m unittest discover -s tests -v
```

Checks include upward/downward synthetic control, legitimate derivative-only zero
output under constant error, inactive-offset rejection, excessive movement,
score reproduction, ideal/slow/overshooting/short/gapped responses, stale readback,
post-reset measured readiness, reference budget/training exclusion, fixed-gain
validation schedules, and the offscreen search → paired validation → apply flow.

The archival `archive/legacy_scripts/test_pid_modules.py` is incompatible with
the pre-existing controller API (`trend_tolerance` no longer exists); it is not
part of the active test suite and was left untouched.

Physical reachability, instrument calibration, command-versus-readback behavior,
commissioned operating limits and independently repeated machine performance must
still be established on the actual equipment. No live experiment was run. The
supplied November 2 data were not available here to reproduce numerically; new
trial records now contain the configuration and contributions needed for replay.
Sine-wave validation and contextual GP adoption remain optional future work,
as specified in the supplied plan. The gain-only GP has been retained.
