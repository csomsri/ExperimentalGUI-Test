# Implementation map: UI to ML and control

This map describes source locations inspected on November 6, 2026. It includes
working-tree changes already present when documentation work began; it does not
certify those changes or supersede path-specific tests.

## UI and operator workflow

Registration lives in [AutomationPage.py](../../python/app/Automation/AutomationPage.py).
It lists Trim-coil BO, PID Control, PythonPID, GA + C++ PID, GA + Python PID,
and Hybrid GA + BO PID. Labels in older guides may describe earlier layouts.

| Entry point | Responsibility |
| --- | --- |
| [PidControlPage.py](../../python/app/Automation/PidControlPage.py) | Beam-feedback native PID workspace, BO session/budget, gain bounds, duration, history, validation and apply workflow |
| [PythonPIDPage.py](../../python/app/Automation/PythonPIDPage.py) | Python NLAPID workspace; direct coil-current feedback in amperes rather than interchangeable beam feedback |
| [GAPIDPage.py](../../python/app/Automation/GAPIDPage.py) and [PIDGAControlTab.py](../../python/app/Automation/GA/PIDGAControlTab.py) | GA workspace integration and automatic candidate lifecycle |
| [HybridPIDPage.py](../../python/app/Automation/HybridPIDPage.py) | Shared GA/BO experiments, reference recovery, handover, validation and export |
| [TrimCoilBOPage.py](../../python/app/Automation/TrimCoilBOPage.py) | Direct joint trim-coil optimization of beam output; does not search PID gains |
| [ControlOwnership.py](../../python/app/Automation/ControlOwnership.py) and [ControllerContext.py](../../python/app/Automation/GA/ControllerContext.py) | Ownership and shared controller context |
| [SurrogatePlotWidget.py](../../python/app/Automation/SurrogatePlotWidget.py), [GainSurfaceWidget.py](../../python/app/Automation/GainSurfaceWidget.py), [TrimCoilBOPlots.py](../../python/app/Automation/TrimCoilBOPlots.py), [TrimCoilModelPlots.py](../../python/app/Automation/TrimCoilModelPlots.py) | GP/gain-space/beam visualizations; predictions are not measured control outcomes |

PID BO proposes gains, executes an authorized trial, scores feedback and records
an observation. Separate gain validation precedes application in the current
main PID page. Applying settings must remain separate from starting PID/output.
Successful completion under the current objective is not the proposed independent
step-tracking acceptance rule. Older Control/README prose describing approval
without a separate validation run is historical relative to the current page.

## Controller and feedback

| Source | Role |
| --- | --- |
| [NLAPID.cpp](../../source/Controls/ControlSystem/NLAPID.cpp) and [header](../../include/Controls/ControlSystem/NLAPID.hpp) | Native adaptive-direction controller |
| [ControlService.cpp](../../source/Controls/Service/ControlService.cpp) | Native service and execution boundary |
| [NLAPID.py](../../source/Python/Control/NLAPID.py) | Python reference controller and settings |
| [CppPIDAdapter.py](../../source/Python/Control/CppPIDAdapter.py) | Python/native adaptation |
| [PythonNLATrial.py](../../source/Python/Control/PythonNLATrial.py) | Python trial worker |
| [FeedbackAverage.py](../../source/Python/Control/FeedbackAverage.py) | Non-overlapping fresh-sample averages; rejects nonfinite/backward timestamps, skips duplicates |
| [ga_backend_adapter.py](../../source/Python/Automation/ga_backend_adapter.py) | GA execution integration |

The Python adaptive engine returns a signed actuator increment per update; its
caller adds it once to the target. Its filtered derivative is damping-only;
zero derivative-only output under constant error can be legitimate. Distinguish
beam nA, actuator A, command acceptance and measured readback. A complete finite
average alone does not establish wall-clock freshness at the consumer.

The local GA tab currently checks `context.beam_snapshot(max_age_s=...)` at
candidate startup and aborts immediately for invalid/nonfinite beam. Its
`_ga_sequence_tick()` and `_finish_ga_restore()` are integration points for the
proposed wait. The supplied external implementation instead names
`_beam_window_snapshot()`; inspect the actual target's contract before porting.
[ga_recovery.py](../../source/Python/Automation/ga_recovery.py) explicitly restores
software targets, with no measured settling acceptance. Hybrid recovery is a
different execution path and must be reviewed separately.

## ML and search policy

Reusable [Optimization package](../../source/Python/Optimization/README.md):

| Module | Responsibility |
| --- | --- |
| [parameter_space.py](../../source/Python/Optimization/parameter_space.py) | Named bounded parameters and candidate validation |
| [observations.py](../../source/Python/Optimization/observations.py) | Observation/audit storage and safe training subset |
| [training.py](../../source/Python/Optimization/training.py) | Gain vectors to tensors; negates cost because acquisition maximizes |
| [surrogate_model.py](../../source/Python/Optimization/surrogate_model.py) | SingleTaskGP with bounded input normalization and standardized scalar outcome |
| [acquisition.py](../../source/Python/Optimization/acquisition.py) | qLogExpectedImprovement and bounded acquisition optimization |
| [bayesian_optimizer.py](../../source/Python/Optimization/bayesian_optimizer.py) | Initialization, fitting and candidate proposals |
| [pid_gain_adapter.py](../../source/Python/Optimization/pid_gain_adapter.py) | PID-specific gain/result interface |
| [genetic_optimizer.py](../../source/Python/Optimization/genetic_optimizer.py) | GA candidates and measured fitness consumption |
| [hybrid_pid_optimizer.py](../../source/Python/Optimization/hybrid_pid_optimizer.py) | GA/BO readiness and measured handover policy |
| [rolling_hybrid_optimizer.py](../../source/Python/Optimization/rolling_hybrid_optimizer.py) | Windowed observations and local learning policies |
| [trim_coil_optimizer.py](../../source/Python/Optimization/trim_coil_optimizer.py) | Direct coil BO, beam uncertainty and prospective model diagnostics |

Gain-only GP inputs do not explain changing machine conditions. Its uncertainty
does not establish experimental repeatability or closed-loop stability. Keep
prospective predictions tied to the exact pre-outcome training snapshot, candidate
and applied rounded gains. Do not mix direct beam maximization and gain-cost
observations. Windowed hybrid behavior and training eligibility are policy-specific;
see the hybrid guide for reference/confirmation budgets and inheritance rules.

## Scoring, records and change boundaries

[trial_metrics.py](../../source/Python/Optimization/trial_metrics.py) supplies shared
response analysis/cost; [ga_evaluation.py](../../source/Python/Automation/ga_evaluation.py)
and [october_ga.py](../../source/Python/Optimization/october_ga.py) supply GA/October
evaluation behavior. Record actual profile, weights, normalization, duration,
warmup and component contributions. Do not infer successful tracking from cost
alone or silently replace the existing objective with waveform scoring.

[PIDRecording.py](../../python/app/Automation/PIDRecording.py) integrates PID
records. Consult [BO readiness](../BO_TEST_READINESS.md) for SQLite locations and
the [hybrid guide](../../HYBRID_GA_BO_PID.md) for session JSON and per-trial CSV.
Keep raw/averaged provenance, timestamps, initial state, failures, gain application,
model snapshots and validation outcomes linked by session/trial ID.

Implementation of the proposed validation path will touch the UI/state machine,
feedback/readiness, pure analysis, acceptance policy, export schema and tests.
Version any objective or data-schema change; preserve previous experimental data.
