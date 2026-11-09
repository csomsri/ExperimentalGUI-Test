# ExperimentalGUI hybrid PID integration

Open **PID / GA AUTO-TUNE → HYBRID GA + BO**. The hybrid has its own bounds,
scoring settings, arm switch, trial budget and learning configuration. Configure
channel, beam setpoint, current limits and PID execution settings on PID CONTROL.
Run the current-gain/reference pilot before searching, then re-arm and start hybrid.
After search, run independent paired square-wave validation before Apply. Applying
loads gains and leaves PID stopped. Stopping requests captured target restoration.

## Reference comparison

The pure Python search policies were ported from the local
`Crocker-Digitlization-GUI/CrockerGUI/source/Python/Optimization` implementation:
`hybrid_pid_optimizer`, `rolling_hybrid_optimizer`, `genetic_optimizer`,
`october_ga`, and `pid_gain_adapter`. Imports now use this repository's Python
PIDGains, TrialMetrics and BO machinery. There is no runtime dependency on that
checkout, PySide6, CycloViz, a native PID service or a C++ controller.

Legacy keeps baseline repeats, GA exploration, diversity readiness, a measured
BO challenger, paired cost/MAE confirmation, BO fallback and periodic drift checks.
Lamarckian, Baldwinian and mixed keep windowed training, local measured refinement,
and distinct inherited genotype / measured phenotype records. All sources consume
the shared search budget. Reference batches are scheduled by the hybrid itself;
the standalone BO interleaved-reference option is hidden here.

Execution intentionally follows ExperimentalGUI's existing Python automatic trial
runner and BO acceptance workflow: measured readiness before trials, Python PID
feedback/output, actuator-aware five-term cost, restoration, tracking acceptance,
and separate multi-condition waveform validation. This differs from the reference
page's native trial execution and its software-target-only recovery. Predictions
remain proposals; independent validation is required to enable Apply.

## Local source map

- `python/app/BO/hybrid_control_tab.py`: BO-style workspace, mode settings and validation fingerprint.
- `python/app/BO/hybrid_pid_adapter.py`: asynchronous sequential policy/runner bridge.
- `python/app/BO/optimization/`: ported pure policies and shared GP implementation.
- `python/app/pid_ga_control_tab.py`: tab registration and existing Python execution/recovery.

Records remain under `runtime/bo_runs`, with `strategy: HYBRID`, approach and policy
settings in `run_settings.json`, ordinary response/decision exports, and
`hybrid_history.json` holding source/state, inheritance and transition audit data.
Baldwinian selection fitness never replaces a physical GP observation. Only
tracking-accepted measured gains can be offered for independent final validation.

## Verification

Run `.venv/Scripts/python.exe scripts/run_tests.py --ui`. The test runner includes
ported legacy and rolling policy regressions and a simulated hybrid search →
restoration → independent waveform validation → apply workflow. These checks do
not establish performance on the physical machine.
