# Tests and changes

## Existing checks to run for implementation changes

Run from the repository root with the configured project interpreter. Dependencies
and the native extension must match the selected tests; see the root build guide.
These are verification entry points, not newly obtained passing results.

| Area | Command / test file under `CrockerGUI/tests/` |
| --- | --- |
| BO model and gain adapter | `python CrockerGUI/tests/BotorchPidOptimizerTest.py -v` |
| Controller reference/parity | `OctoberPIDTest.py`, `NLAPIDParityTest.py`, `CppNLAPIDIntegrationTest.py` |
| Feedback mapping | `BeamFeedbackMappingTest.py`, `BeamPIDTest.py`, `ZMQFeedbackFieldsTest.py` |
| PID UI and Python scoring | `PidControlPageTest.py`, `PythonPIDPageTest.py`, `PythonBOMetricsTest.py` |
| Budgets, restart and validation | `PidAutoRunTest.py`, `BORestartTest.py`, `GainValidationTest.py` |
| GA and hybrid policy | `GAPIDTest.py`, `HybridPIDTest.py`, `RollingHybridTest.py` |
| Persistent trial history | `BOHistoryTest.py` |
| Full simulation/transport/BO path | `python CrockerGUI/tests/BOReadinessTest.py -v` |
| Direct coil BO and synthetic plant | `TrimCoilBOTest.py`, `TrimCoilBOPageTest.py`, `Smoke2BoTest.py`, `Smoke2MisoTest.py` |

For a listed filename, use `python CrockerGUI/tests/<filename> -v`. Diagnose
environment/native build failures separately from assertions. Dated results in
[BO_TEST_READINESS.md](../BO_TEST_READINESS.md) remain historical evidence.

## Required new checks for the validation path

These are pending requirements, not coverage claimed for existing tests.

| Scenario | Expected result |
| --- | --- |
| Invalid beam recovers at 3 s | Same candidate starts once; no waiting-time score/budget consumption |
| Invalid through 10 s, or valid exactly at/after deadline | One timeout/abort, no PID start, authorized restoration only |
| Transient recovery fails final start check | Original deadline retained; no repeated grace period |
| NaN/inf, stale finite or incomplete average | Rejected under documented readiness contract |
| Stop/disarm/power/enable/callback/ownership loss | Immediate cancellation/fault; old callbacks cannot restart |
| New run after aborted wait | Cleared state and a new candidate lifecycle |
| Active evaluation loses feedback | Existing immediate fault handling preserved |
| Reset command accepted but measurements unsettled | Readiness cannot advance |
| Ideal upward/downward steps and starting-in-band | Correct metrics and explicit recovery qualification |
| Slow/nonsettling, overshoot, late events, short windows or logging gaps | Correct FAIL versus INSUFFICIENT_DATA; no invented settling time |
| Invalid timestamps/samples or gain changes | Rejected analysis with reason |
| Inactive versus working/excessive-movement controller | Reproducible score components and tracking acceptance |

Rehearse reset → wait → physical readiness → reference → candidate → validation
→ abort/cleanup in simulation/replay. Inspect exported settings, exact event times,
samples, model snapshots, contributions and plots before live collection. Software
tests cannot establish physical reachability, drift causes or commissioned limits.

## Changes in this documentation consolidation — November 6, 2026

- Created `docs/BO_PID/` as the central implementation documentation folder.
- Preserved the complete supplied text as `CONTROLLED_TESTING_PLAN.md`.
- Added an ordered BO validation path with explicit pending implementation gates.
- Added the UI/controller/ML/scoring/recording source map and verification matrix.
- Linked the folder from the root README, readiness guide and remaining-work tracker.
- Recorded the external/local path mismatch, software-reset limitation, distinction
  between current validation completion and proposed tracking acceptance, and
  historical documentation differences.

Source files/imports remain at their established locations, as requested. This
change adds documentation only; it does not implement the proposed wait/readiness/
waveform features or alter hardware behavior, objective, model or test code.
Pre-existing working-tree changes are outside this documentation change record.
Previous implementation changes and dated test evidence remain linked from the
[central index](README.md); this file does not invent a complete historical audit.

Documentation verification: compare the preserved plan with the attachment and
check local Markdown link targets. Runtime suites are deferred until code changes;
documentation checks do not establish BO/PID performance.

## Local implementation update — November 6, 2026

The shared `python/app/pid_ga_control_tab.py` automatic BO/GA runner now has
an isolated `WAITING_FOR_BEAM` pretrial stage, with a retained candidate and
monotonic ten-second deadline. It requires valid, fresh, ready, finite feedback,
checks output/automatic arming, callback, channel power/enable and GA ownership,
and rechecks feedback after presenting gains. Waiting does not start the evaluator
or PID or submit fitness. Timeout uses the existing conditional abort/recovery path.
New-run initialization, abort and finalization clear wait fields. BO events record
wait start/recovery/timeout, and trial start records initial beam and TC context.
Active evaluation feedback handling and the cost/GP policy are unchanged.

New hardware-free checks: `python -m unittest tests.bo.test_beam_wait -v`.
These cover recovery, exact deadline, unchanged deadline across invalid polls,
stale/incomplete/nonfinite feedback, authorization loss and inactive timer ticks.
Physical readiness, tracking acceptance, waveform analysis and independent live
validation remain pending. This change does not certify live operating limits.

## Full local BO/PID workflow update — November 6, 2026

This supersedes the earlier isolated-wait implementation record above. See
[LOCAL_IMPLEMENTATION.md](LOCAL_IMPLEMENTATION.md) for source mapping, parameter
semantics, the operator workflow, and commissioning limitations.

- Ported the existing Crocker constant-target `evaluate_trial`, `trial_cost`,
  `TuningQuality` and `TrialMetrics` functionality with local evaluator imports
  and explicit actual score configuration/weights. Added pure event-based
  `evaluate_responsiveness` and deterministic square-wave setpoint generation.
- Integrated measured readiness after target restoration, with post-reset full
  beam averages and fresh measured TC telemetry spanning the configured hold.
  Cached packets cannot refresh TC receive timestamps or establish readiness.
- Kept the original objective and gain-only GP. Recorded data validity separately
  from performance acceptance; finite performance failures train, but cannot win
  tracking-constrained selection. Reference and validation trials do not train.
- Added configurable fixed references and interleaving, reference tracking/drift
  checks, current-gain/reference pilots and a matching-pilot gate for full search.
- Added repeated, predeclared reference/candidate square-wave validation at low,
  nominal and high target centers in seeded varied order. All repetitions must
  pass; missing transitions, inadequate coverage or unobserved recovery in either
  direction block successful validation/application. Gains remain fixed per run.
- Added schema 3 settings/events/results, average provenance, initial context,
  normalized terms/weighted contributions, reproducible score diagnostics,
  per-condition between-repeat uncertainty and reference drift summaries.
- Added background exports for responsiveness, measured performance and
  prospective GP calibration figures. Experiment logging failure blocks BO start.
- Changed Apply to require successful recorded independent validation. Applying
  settings leaves PID stopped. Changed operating settings invalidate validation.
- Updated the migration preservation test to exclude `controller_context.py`,
  which is now intentionally maintained for actual-telemetry freshness; its new
  behavior has dedicated tests. Historical migration hashes were preserved.

The archival PID smoke script still expects the removed `trend_tolerance`
constructor argument. It was not changed; the current simulation/controller tests
exercise the active API. No physical reachability/calibration or live machine
validation is claimed, and the supplied historical run scores were not available
for numerical replay. Optional sine tests and contextual GP adoption remain future
experiments rather than prerequisites silently enabled by this change.

Final verification: `PYTHONPATH=python/app python -m unittest discover -s tests -q`
passed all **53 active tests**. Compilation checks and the changed-file whitespace
check also passed. Tests ran with offscreen Qt and temporary records; they did not
command physical hardware. The existing Torch deprecation warning did not affect
results.
