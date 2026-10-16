CNL PID / GA TWO-TAB UPDATE
============================

Purpose
-------
The existing full-width "PID / GA AUTO-TUNE" controller page now contains two
nested tabs:

1. PID CONTROL
   - Keeps the existing selected-channel control.
   - Keeps selected channel, setpoint, actual, and error readouts.
   - Keeps the PID setpoint-response graph.
   - Keeps all current PID gain, limit, timing, output, and safety controls.
   - Contains no GA controls.

2. GA AUTO-TUNE
   - Contains the existing GA population, generation, gain-bound, candidate,
     fitness, progress, and best-gain controls.
   - Adds a separate GA fitness-history graph.
   - Orange trace: submitted candidate fitness.
   - Green trace: best fitness found so far.
   - Fitness remains lower-is-better.

Files to replace
----------------
Copy these two files into the main project folder and overwrite the existing
versions:

- pid_ga_control_tab.py
- controller_theme.py

No change is required in MagneticFieldControllerWindow.py. The public class
name and integration callbacks remain compatible with the current project.

Validation performed
--------------------
- Python syntax compilation passed for the changed GUI files and related
  controller files.
- Existing PID/GA backend smoke tests passed.
- Static layout checks confirmed that GA controls are not built on the PID page
  and are built on the separate GA page.

Start the system as usual with:

    python main.py
