# Tesla-Style Modular Cyclotron Controller

## Installation

Place all files from this folder in the same directory as the existing CNL controller modules. Back up the old `MagneticFieldControllerWindow.py`, then replace it with the new one.

Required project dependency:

- `scaling.py` with `get_scaler` and `TRIM_KEYS`

Required Python GUI package:

- `PyQt6`

The controller no longer requires `pyqtgraph`; the trend displays are drawn directly with Qt.

## Files

- `MagneticFieldControllerWindow.py` — controller shell, CNL header, subsystem tabs, channel table, live-data input, scaling, and control-queue output.
- `controller_context.py` — shared state/signals used by all modes.
- `controller_theme.py` — Tesla colors, cards, tabs, logo loader, buttons, and trend widget.
- `manual_control_tab.py` — manual target adjustment only.
- `sequence_control_tab.py` — single-channel timed sequence only.
- `multi_tc_sequence_tab.py` — synchronized multi-channel sequence only.
- `pid_ga_control_tab.py` — PID/GA user interface and development hooks.
- `pid_controller.py` — independent deterministic PID algorithm.
- `ga_pid_tuner.py` — independent genetic-algorithm candidate manager and starter fitness function.
- `cnl_logo.png` — CNL symbol used in the header.

## Compatibility

The public constructor is unchanged:

```python
MagneticFieldControllerWindow(
    control_queue,
    target_values,
    plot_config,
    current_values_ref=None,
    data_queue=None,
)
```

The control payload remains:

```python
(raw_target_values, power_bits, enable_bits)
```

Engineering targets are still converted with `scaler.eng_to_raw(TRIM_KEYS[i], value)` before transmission.

## PID development state

The PID tab is safe by default:

- It calculates PID output in preview mode.
- It does not write PID output to hardware unless an output callback is connected and the operator checks `ARM HARDWARE OUTPUT`.
- The GA creates one candidate at a time and waits for a lower-is-better fitness score.
- The temporary `Development Fitness` field lets the candidate interface be tested manually.

A future integration/test runner can connect PID output with:

```python
window.set_pid_output_callback(my_pid_output_adapter)
```

The callback signature is:

```python
def my_pid_output_adapter(channel_index, pid_output, pid_result):
    # Apply ramp limits, safety checks, and the chosen output interpretation.
    # Return False to reject the command and stop PID.
    return True
```

A future automated GA test runner can submit a measured candidate score with:

```python
window.submit_pid_ga_fitness(score)
```

or connect directly to:

```python
window.pid_tab.candidate_requested
```

## Important integration note

Opening the PID tab does not activate PID. `START PID` is required. Manual, PID, Sequence, and Multi-TC Sequence update a shared control-mode state so an active timed mode stops when another mode takes command authority.

## Windows installation — important

All of the following files must be in the **same folder** as `main.py`,
`main_control_gui.py`, and the existing `scaling.py`:

- `MagneticFieldControllerWindow.py`
- `controller_context.py`
- `controller_theme.py`
- `manual_control_tab.py`
- `pid_ga_control_tab.py`
- `pid_controller.py`
- `ga_pid_tuner.py`
- `sequence_control_tab.py`
- `multi_tc_sequence_tab.py`
- `cnl_logo.png`

Do not copy only `MagneticFieldControllerWindow.py`. It imports the other modules.
After copying the files, run `python CHECK_INSTALLATION.py`, then `python main.py`.
