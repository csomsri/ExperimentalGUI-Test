# Operation, integration, records, and verification

## Session workflow

The Automation hybrid page captures controller/session settings and a common
TC target/beam reference, creates the selected optimizer, and sequences one trial
at a time. Native PID execution and telemetry collection surround the pure search
policy. The operator must configure the selected TC, target, bounds, command
limits and arm state through the application's existing controls.

After each response, `_complete_tuning_trial()` checks completion, evaluates
metrics and fitness, creates a `PidTrialResult`, records it with the optimizer,
saves response/history information, and starts captured-target restoration.
Completion includes at least two post-warmup samples and a final sample reaching
the requested duration within 0.5 seconds. Metric-evaluation failure marks the
trial invalid. A trial fault or incomplete response stops search rather than
silently switching optimizers.

Search-budget completion, operator stop, optimizer faults and reference drift are
distinct causes. The page can stop execution without assigning the optimizer's
`Stopped` enum. Applying gains does not automatically start PID.

## Recovery means target restoration

`GARecoveryManager.capture()` saves channel targets, readbacks, beam value and
timestamp. `next_command()` checks the current software targets and requests one
channel's full difference from its captured target. Equality uses two-decimal
precision. The page routes the delta through the ordinary authorized adapter.
The existing lower-level ramp handles output behavior.

There is no physical-current acceptance, beam hold, dwell or recovery timeout in
this manager. Readbacks and beam values in the captured reference are diagnostic;
they are not used as settling gates. Recovery-related GUI labels/settings should
not be interpreted as proof those checks are implemented. Target restoration is
necessary for consistent commands but does not establish identical plant state.

## Validation and application

The page selects the policy's best measured candidate and runs a separate final
validation through the inherited PID-page workflow. Validation is outside the
search budget. `_validate_best_gains()` checks controller ownership and telemetry,
rejects dry run, locks inputs, and restores captured targets before executing.
After a valid response, target restoration precedes the inherited final approval
handler. Failed validation blocks application; rejected candidates are excluded
from later best-result selection. Reference drift makes the policy's best result
unavailable and disables approval/application.

The inherited [PidControlPage.py](../../python/app/Automation/PidControlPage.py)
defines `_validation_seconds()`: it returns the tuning duration when no cruise
workspace exists, otherwise the validation-duration field. There is no fixed
60-second minimum in this method. Its final approval handler requires a safe
response, metrics, at least two samples, and elapsed time reaching the selected
duration within 0.5 seconds. When cruise is active, it additionally requires a
usable measured baseline and validation cost no worse than that baseline. The
handler does not independently reject an unsettled or oscillating response.
Search `eligible()` is a separate condition (`safe`, metrics present, finite score).

## Result and audit model

`PidGainCandidate` contains `kp`, `ki`, `kd`. `PidTrialResult` includes the
candidate, cost, response summaries, `safe`, controller kind, optional full
metrics, termination reason, trial ID and start/end times. Controller kinds must
match the optimizer; finite values are required even for invalid audit results.

The optimizer retains:

- `results`: measured PID trials, including invalid audit entries.
- `optimizer.observations`: generic measured observations with metadata.
- `records`: source/state/gains/cost/beam metrics/prediction/reason history.
- `events`: state/reason transitions and the result count at transition time.
- `feasibility_observations`: validity/reason audit data, not a trained feasibility GP.

History sources distinguish `Baseline`, `GA`, `BO challenger`, `Confirm incumbent`,
`Confirm BO`, `BO`, `Reference`, `Exploration`, `GA parent`, and `BO refinement`.
Windowed records also expose active-window count, parent gains, learning mode,
acceptance and selection details. Selection stores genotype, measured phenotype,
parent, fitness, generation and population index. Selection metadata can be added
to the most recent record when a prediction is rejected without a new measurement.

The page's `_save_session()` persists settings, history and validation information;
history review supports CSV export, and response samples support later inspection.
Do not substitute selection scores for physical measurements in exports or GP
training, especially in Baldwinian mode.

## Developer integration

```text
while search is active and budget remains:
    candidate = optimizer.propose_batch(1)[0]
    source = optimizer.pending_source
    measured_result = caller_runs_and_scores_trial(candidate)
    optimizer.record_results([measured_result])
    caller_restores_targets_and_handles_stop_conditions()
caller_runs_separate_final_validation_before_applying_gains()
```

The caller owns hardware, telemetry freshness, interlocks, arming, trial timing,
abort behavior and application of gains. An injected `proposer` enables policy
tests without real GP fitting: legacy takes no arguments and rolling receives a
center candidate; both return `(candidate, predicted_cost, cost_std)`.

## Existing verification

From the `CrockerGUI` directory, using the project's configured Python environment:

```powershell
py -3.13 tests/HybridPIDTest.py
py -3.13 tests/RollingHybridTest.py
```

[HybridPIDTest.py](../../tests/HybridPIDTest.py) covers legacy policy and execution
integration. [RollingHybridTest.py](../../tests/RollingHybridTest.py) covers measured
inheritance, correct training locations, window filtering, loss of readiness,
reference drift, reproducible choices, failure audit, budget handling, multistep
learning, rejected predictions, survivor remeasurement, factory behavior and model
plumbing. Some tests depend on BoTorch, PySide6 or the native extension and may skip
when those dependencies are absent. GUI tests exercise simulation, not plant
performance or a control-theoretic stability guarantee.

When changing policy, verify proposal/result matching, budget accounting, measured
data provenance and invalid-result handling. Changes to hardware execution require
the corresponding execution checks; pure policy tests cannot establish those
properties. This documentation addition itself makes no runtime changes.
