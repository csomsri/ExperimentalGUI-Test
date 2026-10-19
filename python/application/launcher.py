"""Launch the original GUI or check its environment without starting workers."""
import argparse
import importlib
import runpy
import struct
import sys
from .paths import APP, ROOT, configure_environment

DEPENDENCIES = {
    'PyQt6.QtWidgets': 'PyQt6', 'PyQt6.QtMultimedia': 'PyQt6',
    'pyqtgraph': 'pyqtgraph', 'numpy': 'numpy', 'pandas': 'pandas',
    'matplotlib': 'matplotlib', 'openpyxl': 'openpyxl', 'zmq': 'pyzmq',
    'torch': 'torch', 'botorch': 'botorch', 'gpytorch': 'gpytorch',
}


def check_environment():
    errors = []
    if not (3, 11) <= sys.version_info[:2] < (3, 14) or struct.calcsize('P') != 8:
        errors.append('Use 64-bit Python 3.11, 3.12 or 3.13.')
    for module, package in DEPENDENCIES.items():
        try:
            importlib.import_module(module)
        except Exception as exc:
            errors.append(f'{package}: {exc}')
    for path in ('config/calibration.json', 'config/beam_cal.json',
                 'config/app_settings.json', 'config/alarm_config.json',
                 'python/app/main.py', 'python/app/BO/optimization/bayesian_optimizer.py'):
        if not (ROOT / path).is_file():
            errors.append('Missing file: ' + path)
    if not errors:
        # Imports only: no QApplication, ZMQ sockets or multiprocessing workers.
        for module in ('main_control_gui', 'zmq_rep_server', 'sqlite_logger', 'BO.bo_control_tab'):
            try:
                importlib.import_module(module)
            except Exception as exc:
                errors.append(f'{module}: {exc}')
    if errors:
        print('Environment is not ready:\n  ' + '\n  '.join(errors), file=sys.stderr)
        print('Run scripts/install.ps1 or bash scripts/install_ubuntu.sh.', file=sys.stderr)
        return 1
    print(f'Ready: Python {sys.version.split()[0]}, GUI, transport, logging and BO imports.')
    print(f'Runtime files: {ROOT / "runtime"}')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='ExperimentalGUI: existing CST GUI with PID / GA / BO')
    parser.add_argument('--check', action='store_true', help='check dependencies and imports without starting the GUI or workers')
    args = parser.parse_args(argv)
    configure_environment()
    if args.check:
        return check_environment()
    # Run the existing entry point unchanged, including its original processes,
    # queues, transport, database logger and GUI behavior.
    sys.argv = [str(APP / 'main.py')]
    runpy.run_path(str(APP / 'main.py'), run_name='__main__')
    return 0
