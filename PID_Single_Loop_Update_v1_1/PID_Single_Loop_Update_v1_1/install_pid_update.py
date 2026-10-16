# -*- coding: utf-8 -*-
"""Apply a small, preflight-checked update to an existing CNL PID/GA project.

Standard library only. Does not run the controller, connect to LabVIEW, open a
SQLite database, change calibration, or replace whole project files blindly.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

VERSION = 'single-loop-pid-1.1'
PREVIOUS_VERSION = 'single-loop-pid-1.0'
MARKER = '.pid_single_loop_update.json'
REQUIRED = ('main_control_gui.py', 'zmq_rep_server.py', 'pid_controller.py', 'pid_ga_control_tab.py')
NEW_MODULES = ('pid_beam_buffer.py', 'pid_decision_log.py')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_name(name: str) -> None:
    if Path(name).name != name or name not in REQUIRED + NEW_MODULES:
        raise ValueError(f'Invalid patch filename: {name}')


def locate(folder: str | None) -> Path:
    if folder:
        p = Path(folder).expanduser().resolve()
        if not all((p / n).is_file() for n in REQUIRED):
            raise ValueError(f'The selected folder does not contain all four controller files: {p}')
        return p
    here = Path(__file__).resolve().parent
    for candidate in (here, here.parent, here.parent.parent, Path.cwd()):
        if all((candidate / n).is_file() for n in REQUIRED):
            return candidate.resolve()
    raise ValueError(
        'Put the PID_Single_Loop_Update_v1_1 folder INSIDE your current controller project '\
        '(beside main.py), then run Install_PID_Update.bat. Alternatively use --folder PATH.'
    )


def same_installed_files(root: Path, record: dict) -> bool:
    for name, value in record['after_sha256'].items():
        validate_name(name)
        path = root / name
        if not path.is_file() or digest(path.read_bytes()) != value:
            return False
    return True


def prepare(root: Path, payload: Path, upgrade: bool = False) -> tuple[dict[str, bytes | None], dict[str, bytes]]:
    recipe = 'upgrade_from_1_0.json' if upgrade else 'patch_manifest.json'
    edits = json.loads((payload / recipe).read_text(encoding='utf-8'))
    before, after = {}, {}
    for name, changes in edits.items():
        validate_name(name)
        path = root / name
        raw = path.read_bytes()
        before[name] = raw
        text = raw.decode('utf-8-sig').replace('\r\n', '\n')
        for i, change in enumerate(changes, 1):
            count = text.count(change['old'])
            if count != change['count']:
                raise ValueError(
                    f'Version check stopped at {name}, edit {i}: expected '
                    f'{change["count"]} matching block(s), found {count}. '\
                    'NO program files were changed. This file differs from the '
                    'newer PID/GA/RCDS source used to build this update; do not '
                    'replace it with an older whole-file copy.'
                )
            text = text.replace(change['old'], change['new'])
        compile(text, str(path), 'exec')
        newline = '\r\n' if b'\r\n' in raw else '\n'
        final = text.replace('\n', newline).encode('utf-8')
        if raw.startswith(b'\xef\xbb\xbf'):
            final = b'\xef\xbb\xbf' + final
        after[name] = final
    for name in NEW_MODULES:
        validate_name(name)
        path = root / name
        raw = (payload / name).read_bytes()
        compile(raw.decode('utf-8-sig'), str(path), 'exec')
        if path.exists() and path.read_bytes() != raw and not upgrade:
            raise ValueError(f'{name} already exists with different contents. No files changed.')
        before[name] = path.read_bytes() if path.exists() else None
        after[name] = raw
    return before, after


def replace_bytes(path: Path, raw: bytes) -> None:
    temporary = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        with temporary.open('wb') as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def apply(root: Path, payload: Path, check_only: bool = False) -> dict | None:
    marker = root / MARKER
    previous_marker = marker.read_bytes() if marker.exists() else None
    upgrade = False
    if previous_marker is not None:
        record = json.loads(previous_marker.decode('utf-8'))
        verified = same_installed_files(root, record)
        if record.get('version') == VERSION and verified:
            print('This exact update is already installed. Nothing changed.')
            return record
        if record.get('version') == PREVIOUS_VERSION and verified:
            upgrade = True
        else:
            raise ValueError('An update marker exists, but its version or installed files differ. '
                             'No files changed. Preserve your edits before reinstalling or restoring.')
    before, after = prepare(root, payload, upgrade=upgrade)
    print('Project:', root)
    print('Preflight passed for all four existing modules and two new modules.')
    if check_only:
        print('CHECK ONLY: no files were changed.')
        return None
    backup = root / 'pid_update_backups' / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid4().hex[:6])
    backup.mkdir(parents=True, exist_ok=False)
    for name, raw in before.items():
        if raw is not None:
            (backup / name).write_bytes(raw)
    record = {
        'version': VERSION,
        'previous_marker': previous_marker.decode('utf-8') if previous_marker is not None else None,
        'backup': str(backup.relative_to(root)),
        'previously_present': [n for n, raw in before.items() if raw is not None],
        'before_sha256': {n: digest(raw) if raw is not None else None for n, raw in before.items()},
        'after_sha256': {n: digest(raw) for n, raw in after.items()},
    }
    (backup / 'restore_manifest.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    applied = []
    try:
        for name, raw in after.items():
            replace_bytes(root / name, raw)
            applied.append(name)
        replace_bytes(marker, json.dumps(record, indent=2).encode('utf-8'))
    except Exception:
        for name in reversed(applied):
            if before[name] is None:
                (root / name).unlink(missing_ok=True)
            else:
                replace_bytes(root / name, before[name])
        if previous_marker is not None:
            replace_bytes(marker, previous_marker)
        else:
            marker.unlink(missing_ok=True)
        raise
    print('Installed:', VERSION)
    print('Backup:', backup)
    print('Unchanged: magnetic output/scaling, calibration, RCDS modules and SQLite modules.')
    print('Restart the ENTIRE controller application (main.py), including its ZeroMQ process.')
    print('Test with ARM HARDWARE OUTPUT unchecked first.')
    return record


def restore(root: Path) -> None:
    marker = root / MARKER
    if not marker.exists():
        raise ValueError('No installed update marker found; nothing restored.')
    record = json.loads(marker.read_text(encoding='utf-8'))
    if not same_installed_files(root, record):
        raise ValueError('Some files changed after installation. Automatic restore will not overwrite those edits.')
    backup = (root / record['backup']).resolve()
    if root not in backup.parents:
        raise ValueError('Invalid backup path.')
    restored = {}
    for name in record['after_sha256']:
        validate_name(name)
        if name in record['previously_present']:
            data = (backup / name).read_bytes()
            if digest(data) != record['before_sha256'][name]:
                raise ValueError(f'Backup verification failed: {name}')
            restored[name] = data
        else:
            restored[name] = None
    current = {name: (root / name).read_bytes() for name in restored}
    applied = []
    try:
        for name, data in restored.items():
            if data is None:
                (root / name).unlink()
            else:
                replace_bytes(root / name, data)
            applied.append(name)
    except Exception:
        for name in applied:
            replace_bytes(root / name, current[name])
        raise
    if record.get('previous_marker') is not None:
        replace_bytes(marker, record['previous_marker'].encode('utf-8'))
    else:
        marker.unlink()
    print('Previous program files restored. Decision CSVs and all databases were left untouched.')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', help='Existing controller project directory')
    parser.add_argument('--check', action='store_true', help='Validate without writing files')
    parser.add_argument('--restore', action='store_true', help='Restore the verified automatic backup')
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        print('Python 3.10 or later is required by this controller project.', file=sys.stderr)
        return 2
    try:
        root = locate(args.folder)
        if args.restore:
            restore(root)
        else:
            apply(root, Path(__file__).resolve().parent / 'payload', args.check)
    except (OSError, ValueError, KeyError, SyntaxError) as exc:
        print('UPDATE STOPPED:', exc, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
