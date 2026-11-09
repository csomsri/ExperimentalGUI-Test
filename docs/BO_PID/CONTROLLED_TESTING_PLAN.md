# BO/PID fixes and preparation for controlled testing

Status: implementation plan only. No controller or hardware behavior has been changed.

## Goal

Make automatic candidate startup tolerate a short gap in beam averages without freezing the UI, then evaluate whether BO-selected gains actually regulate beam current and respond to repeatable changes. A low objective value alone must not establish successful control.

The controller regulates measured beam current through TC10. Test waveforms change the beam-current setpoint; they are not direct square-wave coil commands.

## Current implementation and evidence

- Implementation target: `C:/Users/matth/OneDrive/Desktop/ExperimentalGUI/python/app/pid_ga_control_tab.py`.
- `_start_automatic_candidate()` immediately aborts when the beam snapshot is invalid or nonfinite. There is no existing timeout to increase at that check.
- `_ga_sequence_tick()` currently handles `EVALUATING` and `RESTORING`. Use this existing timer for a new nonblocking waiting state.
- `_finish_ga_restore()` clears `_ga_pending_candidate` before calling `_start_automatic_candidate()`. The waiting implementation must retain that candidate explicitly instead of losing it.
- Current target reset checks software command acceptance, not measured beam/coil settling. Restoring the command alone does not establish identical initial conditions.
- In BO run `20261102_130727_891651`, 20 trials used 40-second scoring, zero warmup, and a final five-second steady-state window. Selected trial 9 used Kp=0, Ki=0, Kd=0.1 and issued no coil adjustments. Its mean absolute beam error was about 1.02 nA at a 12 nA target. No trial entered the configured ±0.2 nA deadband.
- Cost fell from 1.153 to 0.245, but about 77% of that reduction came from removing the actuator-movement contribution. A zero-gain candidate also scored better than the starting controller.
- Repeated identical gains produced changing costs and starting beam errors. The saved GP uses gains as inputs and explicitly excludes drift/calibration error from its intervals. This suggests changing experimental conditions; it does not establish their physical cause or a GP software defect.

## 1. Add a bounded, nonblocking beam-data wait

Implement this first as an isolated change; do not combine it with relaxed in-trial feedback checks.

1. Introduce `WAITING_FOR_BEAM` and explicit stored candidate, wait-start/deadline, and reason fields. Use `time.monotonic()` and a default timeout of 10.0 seconds.
2. On invalid/nonfinite beam at candidate startup, retain the same candidate, enter the waiting state, set the deadline once, and start/keep the sequence timer running. Do not call `ga_evaluator.start()`, `start_pid()`, or log `TRIAL_START` yet. Do not consume a trial or submit a fitness value.
3. Each sequence tick checks for cancellation, arm state, output callback, actuator power/enable, and control ownership. Removal of authorization or a hardware fault aborts immediately; it must not wait for the beam timeout.
4. Poll `_beam_window_snapshot()` without blocking. Reuse the established validity/freshness/complete-average contract. Inspect its implementation before deciding whether `valid` already implies fresh and ready; do not accept a stale cached finite value.
5. If valid feedback is available strictly before the deadline, clear the wait state and resume the same candidate once through the normal startup checks. Recheck feedback at the final start boundary. If it becomes invalid again, retain the original deadline rather than granting another ten seconds.
6. At or beyond the deadline, abort with a specific message such as `FRESH BEAM DATA UNAVAILABLE FOR 10 S BEFORE CANDIDATE START`. Preserve the existing restoration policy; restoration remains conditional on output authorization and hardware state.
7. Clear candidate/deadline fields on success, stop, abort, finalization, and a new automatic run. Make stale timer callbacks harmless. Centralize state transitions enough to prevent duplicate candidate starts and trial IDs.
8. Display `WAITING FOR FRESH BEAM — x.x / 10.0 s`; retain an immediately usable Stop control. Log wait start, recovery, elapsed wait, and timeout without recording nonfinite beam as a scored observation.

Do not use `time.sleep()`, delete the feedback check, synthesize beam samples, or apply this grace period to an active evaluation. A missing average before a trial and feedback loss during active control are separate conditions.

Distinguish recorded proposal diagnostics from actual trial start. If diagnostic capture timing changes, ensure the saved prediction is still produced before its outcome is measured and uses the correct model/training data. Record initial beam context when the candidate actually starts.

## 2. Diagnose controller behavior before changing the BO model

- Reproduce why the selected derivative-only gains produced zero commands. Check derivative signal/filter state, resets, rounding, deadband behavior, output authorization, and command acceptance. Do not assume zero output is a bug: derivative-only control can legitimately have no response to persistent constant error.
- Use a simulator/replay first to verify that a known working controller reduces error for both upward and downward target changes.
- Inspect target versus measured coil readback and existing calibration before interpreting their difference as actuator lag.
- Verify the configured beam-current targets are reachable under the selected operating conditions.
- Check that fitness terms, weights, normalization, applied rounded gains, and reported total reproduce the saved scores.

## 3. Make successful tracking necessary for selection

- Define and record an error band, required hold duration, maximum allowed settling time, and overshoot limit before testing. ±0.2 nA is the existing example, not a newly validated universal threshold.
- Separate safety/data validity from control-performance acceptance. A completed trial with valid data but poor tracking is a measured performance failure, not missing data or a hardware fault.
- Require the agreed tracking criterion before a candidate can be declared a successful validated controller. Rank acceptable controllers by error, movement, and oscillation afterward.
- Review/rebalance the search objective so a persistent offset with zero movement cannot dominate useful regulation. Consider constraint-aware selection or a documented failure penalty. Choose the treatment of finite failed-performance observations explicitly for GP training; do not indiscriminately discard them or insert nonfinite costs.
- Log error in nA, deadband occupancy, and weighted score contributions alongside total cost. Recheck the score with zero gains, an inactive controller, a known working controller, and a controller with excessive movement.

Acceptance: the agreed acceptance rule rejects the observed inactive, approximately 1 nA-offset behavior even if its movement penalty is zero.

## 4. Establish comparable starting conditions

Create a separate readiness/reset stage; the ten-second fresh-data wait is not a substitute for physical settling.

- Restore the defined starting coil command through the existing authorized path.
- Require fresh beam data and measured coil/beam conditions inside configured starting ranges for a configured hold duration, with a bounded readiness timeout.
- Use an initial beam range that is physically achievable at the starting command; do not require the new test target before testing recovery toward it.
- Reset PID integral/derivative/filter state consistently and verify reset behavior. Define whether the next average must contain only samples collected after reset/start.
- Record initial setpoint, beam, coil command/readback, relevant machine settings, and readiness duration for every trial.
- Report readiness failures separately; do not silently start unmatched trials or count readiness waiting as scored time.

## 5. Implement beam-current responsiveness validation

Start with a deterministic square-wave beam-current reference. One phase convention is:

`r(t) = r0 + A * (2 * (floor(t / H) mod 2) - 1)`

Here `r0` is the center beam-current target, `A` is the amplitude, and `H` is the hold time at each level. This starts at `r0-A` and alternates with `r0+A`. Log exact setpoint-change timestamps. The previously drawn 10.5/11.5 nA levels and 30-second holds were illustrations, not approved operating settings.

- Configure approved reachable targets, transition count, and holds long enough to observe the selected settling criterion.
- Keep PID gains fixed throughout each validation sequence; PID computes the coil adjustments normally.
- Run the identical waveform with reference gains and candidate gains. Keep this validation separate from the original constant-target 40-second fitness unless deliberately defining and versioning a new waveform-based search objective.
- After square-wave validation, optionally use a sine reference to measure phase lag and amplitude loss. Do not use a sine response to report step settling time.

Implement a pure analysis function such as:

`evaluate_responsiveness(time_s, beam_nA, setpoint_nA, event_times_s, error_band_nA, hold_time_s, max_settling_time_s, max_overshoot_nA)`

For each event report first band entry, qualifying settling/hold time, direction-aware overshoot, final signed error, time-weighted MAE/RMSE, deadband occupancy, and actuator movement if supplied. Treat upward and downward transitions consistently. Mark starting-in-band cases so they are not mistaken for demonstrated recovery.

Return `PASS`, `FAIL`, or `INSUFFICIENT_DATA` with reasons. A never-settled response is a failure only when the observation window is long enough to evaluate the criterion; a shortened window or substantial logging gap is insufficient data. Do not substitute the test duration as an observed settling time. Segment at the next setpoint change and reject nonmonotonic/nonfinite timestamps, invalid samples, or unaccounted gain changes.

## 6. Detect drift and independently validate selected gains

- Interleave the same fixed reference gains between search candidates: reference, candidate, reference, candidate. Label reference checks separately so they do not silently consume candidate budgets or enter GP training under the wrong role.
- Compare reference performance and initial beam state over time. If the reference changes, investigate drift/readiness before attributing changes to candidate gains.
- Repeat each validation condition, vary baseline/candidate order, and retain all trials. Choose repeat count and machine-time budget before collection; do not stop after the best-looking response.
- Validate selected gains in longer runs of several minutes, using the same reference waveform and operating conditions. Check low, nominal, and high approved operating targets rather than one favorable condition.
- Report uncertainty across independent repetitions separately from within-trial beam variation and GP predictive intervals.

## 7. Decide whether the GP needs more context

Keep the existing gain-only GP for the first controlled comparison. Verify model fit diagnostics and prospective prediction logging. More model complexity will not repair the scoring objective or missing experimental controls.

If repeated controlled tests show systematic dependence on starting conditions, evaluate a contextual model using measured pretrial variables such as initial beam current, coil readback, and setpoint. Use only information available before the trial outcome. Gather enough repeated/context-diverse observations and test calibration/prediction accuracy prospectively before adopting the model. Reassess observation noise and drift rather than merely choosing a larger kernel.

## Verification and test readiness

### Offline automated checks

- Invalid average becomes valid at 3 s: same candidate starts exactly once; no scored time or trial budget is consumed while waiting.
- Feedback stays invalid through 10 s: one timeout/abort, no PID start, correct conditional restoration.
- Valid feedback at/after the deadline: deterministic timeout; transient recovery/re-entry cannot renew the deadline.
- NaN/infinite beam, stale finite data, and incomplete windows follow the documented readiness contract.
- Stop, disarm, power/enable loss, output callback loss, and new-run initialization work during the wait; old callbacks cannot revive a candidate.
- Existing evaluation-time feedback loss still follows its current immediate fault handling.
- Reset/readiness uses physical measurements and cannot advance on software command acceptance alone.
- Synthetic response analysis covers ideal steps, slow/nonsettling responses, overshoot, upward/downward changes, late events, logging gaps, and too-short windows. Scoring tests exercise inactive versus working control.

### Simulation/replay and live test gate

1. Complete offline checks and inspect exported events/settings/plots.
2. Rehearse the full sequence in simulation/replay: reset, wait, readiness, baseline, candidate, validation, abort, cleanup.
3. Before live collection, set approved targets/amplitudes, holds, starting ranges, readiness timeout, performance limits, actuator limits, and repeat budget. Verify Stop and output authorization behavior.
4. Collect the baseline/selected-gain pilot first; inspect tracking and repeatability before spending a full BO search budget.
5. Export trial IDs, exact event times, raw/averaged feedback provenance, actual gains, state transitions, score terms, validation metrics, prospective GP predictions, and all failures. Produce the three figure groups: responsiveness, uncertainty/calibration, and measured performance.

Ready for a paper claim only after independently repeated validation supports both target tracking and responsiveness, with limitations reported. Until then, describe the existing 20-trial result as objective-cost reduction with unsuccessful target tracking.
