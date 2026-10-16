# BO + PID

This package ports the reusable optimizer from RecurseIntall/Crocker-Digitlization-GUI/CrockerGUI/source/Python/Optimization. Imports were relocated to BO.optimization; the GP and qLogExpectedImprovement implementation is retained. It uses the destination application's existing AdaptiveDirectionPIDController, calibrated beam input, output callback, evaluator, and automatic trial/recovery scheduler. No second PID loop is created.

Install optional dependencies into the same Python environment that launches the application:

    python -m pip install -r BO/requirements.txt

The BO tab and existing application can load without these dependencies. Starting BO reports missing dependencies. Model fitting and proposals run on a single background worker using CPU. Stopping prevents subsequent candidates from running; a model fit already in progress may continue computing until it returns.

## Operator workflow

1. On PID / GA AUTO-TUNE, choose and confirm the TC, configure PID gains, beam setpoint, timing and TC limits. Configure gain bounds, warm-up, evaluation duration and score weights in its GA settings. Each gain bound must have a strictly increasing minimum and maximum.
2. Enable/power the selected TC and arm PID output. On BO + PID, choose a trial budget, arm BO and select Start BO.
3. The current gains are measured first. BO then requests Sobol initial candidates until six valid observations exist, followed by GP/qLogEI proposals. Trials execute sequentially. An invalid trial stops search and does not train the model.
4. Existing baseline target commands are restored between trials. BO additionally requires fresh beam within max(5% of baseline, 0.1 nA) and selected measured TC within 0.1 A of its saved readback for three seconds. Recovery times out after 60 seconds. These fixed tolerances should be reviewed against the beam and readback noise before operating. The other existing GA recovery behavior is unchanged.
5. When search and final recovery complete, original working gains are restored and output is disarmed. Review measured scores. Re-arm PID output and BO, then Validate best gains. This runs the measured best gains with at least 60 seconds of scoring, followed by baseline recovery.
6. Validation passes when the completed finite score is at most 120% of the search score (absolute floor 1e-9), settings are unchanged, and recovery completes. This is a repeatability heuristic, not a closed-loop stability guarantee. Apply validated gains loads gain fields only; it never starts PID. A subsequent PID/tuning run or settings change invalidates approval.

Search and validation each save settings, per-trial response CSVs and summary.csv under bo_runs/. Shared PID / GA progress widgets also show the active BO trials; their GA captions are retained. The BO tab supplies strategy-specific status and history. Gains from Recurse's different PID backend and historical experiments are not imported as observations.

## Verification

    python -m unittest BO.test_bo_integration

These tests use deterministic proposal substitutes and fake readbacks; they require neither Qt, BO dependencies nor hardware. Full application UI checks require PyQt6 and the application's normal dependencies. Exercising actual GP fitting additionally requires BO/requirements.txt.
