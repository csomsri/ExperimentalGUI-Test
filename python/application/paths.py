"""Resolve project resources independently of the caller's working directory."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'python' / 'app'
RUNTIME = ROOT / 'runtime'
RESOURCE_INDEX = json.loads((ROOT / 'config' / 'resource_paths.json').read_text(encoding='utf-8'))


def project_file(name):
    """Return an absolute resource/data path; accept already-resolved paths."""
    path = Path(name)
    if path.is_absolute():
        return path
    if str(name) not in RESOURCE_INDEX:
        raise KeyError(f'Unknown project resource: {name}')
    return ROOT / RESOURCE_INDEX[str(name)]


def configure_environment():
    for folder in ('data', 'logs', 'snapshots', 'ga_runs', 'bo_runs', 'pid_logs', 'pid_decision_logs'):
        (RUNTIME / folder).mkdir(parents=True, exist_ok=True)
    # Resolve user overrides before setting a stable working directory.
    defaults = {
        'CNL_CALIB': ROOT / 'config/calibration.json',
        'CNL_BEAM_CAL': ROOT / 'config/beam_cal.json',
        'CNL_APP_SETTINGS': ROOT / 'config/app_settings.json',
        'CNL_ALARM_CFG': ROOT / 'config/alarm_config.json',
    }
    defaults['CNL_DB_PATH'] = RUNTIME / 'data/cyclotron_data.db'
    if 'CNL_SQLITE' in os.environ:
        defaults['CNL_SQLITE'] = os.environ['CNL_SQLITE']
    for name, default in defaults.items():
        os.environ[name] = str(Path(os.environ.get(name, default)).expanduser().resolve())
    for path in (ROOT, APP):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    os.chdir(RUNTIME)
