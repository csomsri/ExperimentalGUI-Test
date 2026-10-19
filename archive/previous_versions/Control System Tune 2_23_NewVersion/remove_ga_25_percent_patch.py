# -*- coding: utf-8 -*-
"""Remove the automatic ±25% PID-gain-bounds shortcut from the GA GUI.

Run this script from the project directory that contains pid_ga_control_tab.py,
or pass the file explicitly:

    python remove_ga_25_percent_patch.py
    python remove_ga_25_percent_patch.py path/to/pid_ga_control_tab.py

The script creates a timestamped backup before writing the modified file.
Only pid_ga_control_tab.py is changed.  The PID engine, GA engine, main
controller, communication files, and shared theme are not touched.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import tokenize
from datetime import datetime
from pathlib import Path


TARGET_NAME = "pid_ga_control_tab.py"


def _read_source(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    return raw.decode(encoding), encoding


def _remove_button_block(lines: list[str]) -> tuple[list[str], bool]:
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if "self.ga_bounds_button = QPushButton" in line
        ),
        None,
    )
    if start is None:
        return lines, False

    end = next(
        (
            index
            for index in range(start, min(len(lines), start + 20))
            if "config_grid.addWidget(self.ga_bounds_button" in lines[index]
        ),
        None,
    )
    if end is None:
        raise RuntimeError(
            "Found ga_bounds_button creation, but could not identify the complete "
            "button block safely. No file was changed."
        )

    return lines[:start] + lines[end + 1 :], True


def _remove_method(lines: list[str]) -> tuple[list[str], bool]:
    pattern = re.compile(r"^    def _set_ga_bounds_around_current\b")
    start = next(
        (index for index, line in enumerate(lines) if pattern.match(line)),
        None,
    )
    if start is None:
        return lines, False

    end = None
    for index in range(start + 1, len(lines)):
        if re.match(r"^    (?:def |@)", lines[index]):
            end = index
            break
    if end is None:
        raise RuntimeError(
            "Found _set_ga_bounds_around_current(), but could not identify the "
            "end of the method safely. No file was changed."
        )

    retained = lines[:start]
    while retained and not retained[-1].strip():
        retained.pop()
    retained.append("\n")
    return retained + lines[end:], True


def patch_source(source: str) -> tuple[str, list[str]]:
    newline = "\r\n" if "\r\n" in source else "\n"
    normalized = source.replace("\r\n", "\n")

    feature_markers = (
        "ga_bounds_button",
        "_set_ga_bounds_around_current",
        "SET BOUNDS ±25% OF CURRENT GAINS",
        "75% TO 125% OF THE CURRENT PID GAINS",
        "USE THE ±25% BUTTON",
    )
    if not any(marker in normalized for marker in feature_markers):
        if "Kp Min" in normalized and "Kp Max" in normalized:
            return source, [
                "the ±25% shortcut is already absent; no changes were needed"
            ]
        raise RuntimeError(
            "This does not look like the expected PID/GA interface file. "
            "No file was changed."
        )

    lines = normalized.splitlines(keepends=True)
    changes: list[str] = []

    lines, removed = _remove_button_block(lines)
    if removed:
        changes.append("removed the SET BOUNDS ±25% OF CURRENT GAINS button")

    before = len(lines)
    lines = [line for line in lines if "self.ga_bounds_button," not in line]
    if len(lines) != before:
        changes.append("removed the deleted button from the GA lock/unlock list")

    before = len(lines)
    lines = [
        line
        for line in lines
        if "self._set_ga_bounds_around_current(show_status=False)" not in line
    ]
    if len(lines) != before:
        changes.append("removed automatic percentage-based bound initialization")

    lines, removed = _remove_method(lines)
    if removed:
        changes.append("removed the 75%-to-125% bound-calculation method")

    patched = "".join(lines)

    error_pattern = re.compile(
        r'(?P<indent>^[ \t]*)f"\{limits\[0\]:\.6g\} TO '
        r'\{limits\[1\]:\.6g\}; USE THE ±25% BUTTON "\n'
        r'(?P=indent)f"OR EDIT THE BOUNDS"',
        flags=re.MULTILINE,
    )

    def _manual_bounds_message(match: re.Match[str]) -> str:
        indent = match.group("indent")
        return (
            f'{indent}f"{{limits[0]:.6g}} TO {{limits[1]:.6g}}; '
            'EDIT THE GA SEARCH BOUNDS"'
        )

    patched, replacement_count = error_pattern.subn(
        _manual_bounds_message,
        patched,
        count=1,
    )
    if replacement_count:
        changes.append("updated the out-of-range message to reference manual bounds")
    elif "USE THE ±25% BUTTON" in patched:
        raise RuntimeError(
            "The ±25% validation message has an unexpected format. "
            "No file was changed."
        )

    old_idle = 'self.ga_status_label = QLabel("GA IDLE")'
    new_idle = (
        'self.ga_status_label = QLabel('
        '"GA IDLE — SET Kp/Ki/Kd MIN/MAX BOUNDS"'
        ')'
    )
    if old_idle in patched:
        patched = patched.replace(old_idle, new_idle, 1)
        changes.append("made the initial GA status request explicit manual bounds")

    forbidden = {
        "ga_bounds_button": "deleted GA bounds button reference",
        "_set_ga_bounds_around_current": "deleted percentage-bound method reference",
        "SET BOUNDS ±25%": "deleted ±25% button text",
        "75% TO 125% OF THE CURRENT PID GAINS": "deleted percentage-bound status text",
        "USE THE ±25% BUTTON": "deleted percentage-bound validation text",
    }
    remaining = [description for marker, description in forbidden.items() if marker in patched]
    if remaining:
        raise RuntimeError(
            "Patch verification failed; the source still contains: "
            + ", ".join(remaining)
            + ". No file was changed."
        )

    # Syntax validation does not import PyQt6 or execute application code.
    compile(patched, TARGET_NAME, "exec")

    if newline == "\r\n":
        patched = patched.replace("\n", "\r\n")
    return patched, changes



def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove the automatic ±25% current-gain bounds shortcut from the GA page."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=TARGET_NAME,
        help=f"PID/GA interface file; default: {TARGET_NAME}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and report the patch without writing files",
    )
    args = parser.parse_args()

    target = Path(args.target).expanduser().resolve()
    if not target.is_file():
        print(f"ERROR: file not found: {target}", file=sys.stderr)
        return 2

    try:
        source, encoding = _read_source(target)
        patched, changes = patch_source(source)
    except (OSError, UnicodeError, SyntaxError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    changed = patched != source
    if args.dry_run:
        print(f"DRY RUN OK: {target}")
        for change in changes:
            print(f"  - {change}")
        return 0

    if not changed:
        print(f"NO CHANGE: {target}")
        for change in changes:
            print(f"  - {change}")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = target.with_name(f"{target.name}.before_remove_ga_25_percent_{stamp}.bak")
    temp = target.with_name(f".{target.name}.ga25.tmp")

    try:
        backup.write_bytes(target.read_bytes())
        temp.write_bytes(patched.encode(encoding))
        temp.replace(target)
    except OSError as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"ERROR while writing files: {exc}", file=sys.stderr)
        print(f"The original backup is at: {backup}", file=sys.stderr)
        return 1

    print("PATCH APPLIED SUCCESSFULLY")
    print(f"Updated: {target}")
    print(f"Backup:  {backup}")
    for change in changes:
        print(f"  - {change}")
    print("\nThe GA now uses only the manually entered Kp/Ki/Kd Min/Max limits.")
    print("Enter those six limits before starting GA; the current PID gains must lie inside them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
