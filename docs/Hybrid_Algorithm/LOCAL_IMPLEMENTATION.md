# ExperimentalGUI hybrid PID integration

Open **PID / GA AUTO-TUNE → HYBRID GA + BO**. The hybrid has its own bounds,
scoring settings, arm switch, trial budget and learning configuration. Configure
channel, beam setpoint, current limits and PID execution settings on PID CONTROL.
Arm PID output and hybrid trials, then start the search. Apply loads the best
valid observed gains and leaves PID stopped, matching GA autotune. Stop requests
captured target restoration. There is no extra pilot, limits approval, physical
readiness panel, tracking-acceptance gate or mandatory waveform validation.


## Reference comparison

The pure Python search policies were ported from the local
`Crocker-Digitlization-GUI/CrockerGUI/source/Python/Optimization` implementation:
`hybrid_pid_optimizer`, `rolling_hybrid_optimizer`, `genetic_optimizer`,
`october_ga`, and `pid_gain_adapter`. Imports now use this repository's Python
PIDGains, TrialMetrics and BO machinery. There is no runtime dependency on that
checkout, PySide6, CycloViz, a native PID service or a C++ controller.

Legacy keeps baseline repeats, GA exploration, diversity readiness, a measured
BO challenger, paired cost/MAE confirmation and BO fallback.
Lamarckian, Baldwinian and mixed keep windowed training, local measured refinement,
and distinct inherited genotype / measured phenotype records. All sources consume
the shared search budget. Reference batches are scheduled by the hybrid itself;
periodic reference/drift checks are disabled in this GUI's search configuration.
The pure reference policy still supports them for standalone use.

Execution uses ExperimentalGUI's shared Python automatic trial runner: output and
autotune arming, channel power/enable, fresh feedback, configured actuator limits,
actuator-aware five-term fitness and target restoration. These are the existing GA
controls. BO and hybrid add no separate configurable safety thresholds. Hybrid's
coverage, predicted-improvement and paired handover settings control proposal
selection; they are optimizer learning parameters.


## Local source map

- `python/app/BO/hybrid_control_tab.py`: BO-style workspace, mode settings.
- `python/app/BO/hybrid_pid_adapter.py`: asynchronous sequential policy/runner bridge.
- `python/app/BO/optimization/`: ported pure policies and shared GP implementation.
- `python/app/pid_ga_control_tab.py`: tab registration and existing Python execution/recovery.

Records remain under `runtime/bo_runs`, with `strategy: HYBRID`, approach and policy
settings in `run_settings.json`, ordinary response/decision exports, and
`hybrid_history.json` holding source/state, inheritance and transition audit data.
Baldwinian selection fitness never replaces a physical GP observation. Best valid measured gains can be applied directly, as in GA autotune.

## Verification

Run `.venv/Scripts/python.exe scripts/run_tests.py --ui`. The test runner includes
ported legacy and rolling policy regressions and a simulated hybrid search →
restoration → independent waveform validation → apply workflow. These checks do
not establish performance on the physical machine.
