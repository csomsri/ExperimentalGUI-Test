SINGLE-LOOP BEAM PID UPDATE — v1.1

NEW EDITABLE GUI FIELD
Response wait (ms): 0–10000 ms; default 0 ms preserves v1.0 behavior.
After a nonzero TC correction, the controller holds the target during this wait.
Only beam samples with server receive times AFTER the deadline enter the NEXT
complete beam average. Averaging happens after the wait, not during it.

Example (illustrative, NOT an optimized setting):
  Response wait 200 ms + Beam avg. 300 ms requires at least about 500 ms before
  the next correction, plus polling/transport timing. A 300 ms Loop is a check
  opportunity, not a guarantee of a correction at 300 ms.

The sequence is:
  TC correction -> response wait -> fresh samples -> average beam -> error ->
  keep/reverse direction -> next TC correction.

Loop and Beam avg. remain editable. No separate Dir. check timer is reintroduced.
D filter remains separate and only smooths the derivative.

Changing Response wait, Loop or Beam avg. while PID runs holds the target,
restarts the wait/average, and clears the comparison reference while retaining
its current direction. This avoids comparing incompatible averaging windows.
The first average after starting PID needs no response wait because this PID
session has not yet commanded a correction. A zero correction (e.g. deadband)
also needs no extra response wait. Preview follows the same proposed-move timing
but does not send hardware output. Live acquisition/logging continues during
waits. No sleep/blocking delay or additional GUI timer is used.

DEFAULT FIELDS
Setpoint 2.7314 nA; Kp 0.8; Ki 0.1; Kd 0.1.
Deadband 0.2 nA; Trend tol. 0.1 nA.
Loop 300 ms; Beam avg. 300 ms; Response wait 0 ms.
D filter tau 0.2 s; Beam stale 1.5 s.
These are the previously supplied trial values plus disabled optional response
wait. They are NOT optimized settings. Values remain adjustable; this update
does not add saving of timing edits across application restarts.

INSTALL / UPGRADE
1. Close the ENTIRE control application, including its Python ZeroMQ process.
2. Extract PID_Single_Loop_Update_v1_1 INSIDE the CURRENT project folder,
   alongside main.py. Keep the payload subfolder inside the update folder.
3. Run Install_PID_Update.bat with the controller's Python environment.
4. Restart main.py. Begin testing with ARM HARDWARE OUTPUT UNCHECKED.

This package can install directly on the supported original 9_21_RCDS source
or upgrade a checksum-verified PID Single-Loop v1.0 installation. It is not
necessary to run the old installer first. Previously installed files that have
subsequently been edited are not overwritten automatically. A failed preflight
stops without modifying program files. Backups are made automatically.

Example:
  Your controller folder/
    main.py
    pid_ga_control_tab.py
    ...
    PID_Single_Loop_Update_v1_1/
      Install_PID_Update.bat
      install_pid_update.py
      payload/

Check_Compatibility.bat does a read-only preflight.
Restore_Previous_PID.bat restores the verified prior version. If this was an
upgrade from v1.0, it restores v1.0 and its previous backup marker; a second
restore can return to the pre-v1.0 files. Later manual edits are not overwritten.
Decision CSVs and databases are never removed by restore.

IMPORTANT LIMITS
The wait starts AFTER the Python output callback returns, NOT after a LabVIEW
hardware acknowledgment. It is an adjustable receive-side timing margin; it
cannot prove the target was applied or TC/beam settled. Delayed acquisition
packets can still arrive after the deadline. Validate physical response timing
in a supervised test before relying on automatic direction decisions.

The stale check still monitors the newest high-rate sample during the wait.
Stale/invalid data stops PID through the existing path. A wait longer than the
stale timeout is permitted only while genuinely fresh feedback keeps arriving.
Current bounds, arming and callback checks remain. LabVIEW supplies the physical
ramp; this package does not change its loops or ramp settings.

INCLUDED v1.0 BEHAVIOR
Original high-rate packets feed PID before GUI downsampling. Beam averaging is
performed in raw voltage within one meter range, then converted using existing
calibration. The scalar display-averaging early-return bug is fixed.
Complete non-reused averages are required; incomplete data holds the target.
Absolute beam-error magnitude is compared at each decision: hold inside the
deadband; reverse if its increase exceeds Trend tol.; otherwise keep direction.
No extra worsening-window confirmations. Acquisition gaps and meter-range
changes reset comparisons. Existing integral/derivative behavior is preserved.
GA candidates share this PID path; GA search/recovery was not redesigned and
neither PID nor GA was tested on hardware here.

FILES AND LOGS
Patched source blocks: main_control_gui.py, zmq_rep_server.py,
pid_controller.py, pid_ga_control_tab.py. Helper modules: pid_beam_buffer.py,
pid_decision_log.py. For a v1.0 upgrade, only the GUI and decision-log helper
content changes. No replacement of MagneticFieldControllerWindow.py,
static_target_latch.py, calibration, RCDS, SQLite modules, databases or snapshots.
The LabVIEW wire reply format remains unchanged.

Each PID session writes a CSV in pid_decision_logs. Added columns:
  response_wait_ms
  next_average_after_receive_mono_s (receive-time eligibility deadline for NEXT cycle)
HOLD events identify RESPONSE WAIT or incomplete averaging. output_accepted
means Python accepted a callback, not that LabVIEW applied a target.

VALIDATION
82 software tests and 10 installer/restore checks passed. See TEST_REPORT.txt
and validation_logs. Run_Software_Tests.bat reruns the 82 software tests after
installation. Python 3.10+ and the normal controller dependencies are required.
Native Qt rendering, LabVIEW, power supplies and real-beam response were not
tested. These checks are not hardware validation or a safety certification.
