# -*- coding: utf-8 -*-
"""Quick installation check for the modular Cyclotron Controller package."""
from __future__ import annotations

from pathlib import Path
import sys

REQUIRED = [
    "MagneticFieldControllerWindow.py",
    "controller_context.py",
    "controller_theme.py",
    "manual_control_tab.py",
    "pid_ga_control_tab.py",
    "pid_controller.py",
    "ga_pid_tuner.py",
    "sequence_control_tab.py",
    "multi_tc_sequence_tab.py",
    "cnl_logo.png",
    "scaling.py",  # supplied by the existing CNL control-system project
]

here = Path(__file__).resolve().parent
missing = [name for name in REQUIRED if not (here / name).exists()]

print(f"Controller directory: {here}")
if missing:
    print("\nMISSING FILES:")
    for name in missing:
        print(f"  - {name}")
    print("\nCopy all package files into the same directory as main.py and scaling.py.")
    raise SystemExit(1)

print("All required files are present.")
print("Import test...")
try:
    from MagneticFieldControllerWindow import MagneticFieldControllerWindow  # noqa: F401
except Exception as exc:
    print(f"Import failed: {type(exc).__name__}: {exc}")
    raise

print("Import successful. You can run: python main.py")
