# BO validation path

Status: requirements checklist retained from the supplied plan. Local software
implementation is recorded in [LOCAL_IMPLEMENTATION.md](LOCAL_IMPLEMENTATION.md).
The checklist below preserves the original requirements, rather than serving as
the current software completion tracker. Physical experiments have not been run.
The full specification and evidence are in the
[supplied controlled-testing plan](CONTROLLED_TESTING_PLAN.md).

## 1. Isolated pretrial fresh-beam wait

- [ ] Add `WAITING_FOR_BEAM`, stored candidate, monotonic start/deadline and reason;
  default timeout 10.0 seconds. Use the existing sequence timer without blocking.
- [ ] Keep the same candidate and original deadline across transient recovery.
  Start strictly before the deadline only, with a final feedback recheck.
- [ ] Require the target implementation's complete/fresh/valid average contract;
  reject stale finite, incomplete and nonfinite snapshots.
- [ ] On every waiting tick enforce Stop, arm, callback, power/enable and ownership.
  Authorization removal or fault aborts immediately with existing conditional recovery.
- [ ] Do not start PID/evaluator, log `TRIAL_START`, consume budget or submit fitness
  while waiting. Resume exactly once; stale timer callbacks cannot revive a candidate.
- [ ] Clear wait fields on success, stop, abort, finalization and new run. Display
  `WAITING FOR FRESH BEAM — x.x / 10.0 s` with usable Stop. Log wait/recovery/timeout.
- [ ] Preserve current immediate fault behavior for feedback loss during evaluation.
  Keep pre-outcome proposal diagnostics separate from actual-start initial context.

## 2. Diagnose regulation and reproduce scoring

- [ ] Replay/simulate a working controller on upward and downward target steps.
  Diagnose derivative-only zero output through filter/reset/deadband/rounding and
  command authorization/acceptance; do not presume an engine defect.
- [ ] Check reachable targets, calibration and command versus measured coil response.
- [ ] Reproduce saved scores from applied gains, normalized terms and weights.
  Compare zero gains, inactive control, known working control and excessive movement.

## 3. Separate data validity from performance acceptance

- [ ] Predefine error band, hold duration, settling deadline and overshoot limit.
  The supplied ±0.2 nA example is not a universally approved tolerance.
- [ ] A valid completed poor-tracking trial is a performance failure. Record it
  separately from hardware faults, missing data and incomplete runs.
- [ ] Require tracking acceptance before declaring a validated controller; rank
  acceptable controllers afterward. Explicitly decide how finite performance failures
  train the GP, and version changed penalties/constraints. Never insert nonfinite costs.
- [ ] Export nA error, band occupancy and weighted contributions. The approximately
  1 nA-offset inactive response described in the supplied evidence must fail acceptance.

## 4. Physical readiness stage

- [ ] Restore a defined starting command through the authorized path, then require
  measured beam/coil ranges, fresh feedback and a stable hold with bounded timeout.
- [ ] Use an achievable initial beam range, independently of the next test target.
  Reset integral/derivative/filter state consistently; define post-reset sample eligibility.
- [ ] Record setpoint, beam, command/readback, machine settings and readiness duration.
  Report readiness failures separately and exclude waiting from scoring time.

## 5. Fixed-gain responsiveness validation

Use a deterministic square-wave **beam-current setpoint**, not direct coil commands:

```text
r(t) = r0 + A * (2 * (floor(t / H) mod 2) - 1)
```

It starts at `r0-A`. Approve reachable levels, holds and transition count before
collection; the supplied 10.5/11.5 nA and 30-second examples are illustrations.
Hold gains fixed for the sequence and repeat the identical waveform for reference
and candidate. Keep this separate from constant-target search fitness unless a
new objective is deliberately defined and versioned.

- [ ] Add pure `evaluate_responsiveness(time_s, beam_nA, setpoint_nA,
  event_times_s, error_band_nA, hold_time_s, max_settling_time_s,
  max_overshoot_nA)`; optionally accept actuator samples.
- [ ] Segment at the next event. Report first entry, qualifying settling/hold,
  direction-aware overshoot, final signed error, time-weighted MAE/RMSE,
  band occupancy and optional actuator movement. Mark starting-in-band cases.
- [ ] Return `PASS`, `FAIL` or `INSUFFICIENT_DATA` with reasons. A never-settled
  response fails only with enough observation time; short windows or substantial
  logging gaps are insufficient. Never replace unknown settling with test duration.
- [ ] Reject invalid samples, nonfinite/nonmonotonic timestamps and unaccounted gain
  changes. Optional sine tests measure phase/amplitude, not step settling time.

## 6. Drift controls and independent validation

- [ ] Interleave labeled fixed-gain references and candidates. Define reference
  budget/training treatment explicitly; existing hybrid scheduling differs from
  the proposed alternating reference/candidate experiment.
- [ ] Preselect repeat count and machine-time budget, vary order, retain all trials,
  and investigate reference/initial-state drift before attributing gain effects.
- [ ] Validate selected gains over several minutes at approved low/nominal/high
  conditions. Report between-repeat uncertainty separately from sample variation
  and GP predictive intervals.

## 7. Model decision and live gate

Keep the gain-only GP for the first controlled comparison. Verify fit diagnostics
and prospective predictions. Adopt pretrial context only if repeated controlled
data support it; test prospective calibration and observation noise/drift before
changing model complexity.

Complete [offline and simulation checks](TESTS_AND_CHANGES.md), inspect exported
events/plots, set approved operating/performance/actuator limits and verify Stop.
Collect the baseline/selected-gain pilot before a full BO search. Export trial IDs,
events, provenance, gains, states, score terms, metrics, predictions and failures;
produce responsiveness, uncertainty/calibration and measured-performance figures.
Paper claims require independently repeated tracking and responsiveness evidence.
Until then the supplied 20-trial result supports objective reduction with
unsuccessful target tracking.
