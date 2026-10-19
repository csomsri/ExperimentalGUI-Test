# -*- coding: utf-8 -*-
"""Asynchronous SQLite logger for PID controller samples.

The PID GUI enqueues one lightweight dictionary for each fresh beam-feedback
sample. A dedicated daemon thread writes batches to SQLite using WAL mode, so
normal control-loop execution never waits for disk I/O.
"""

from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


PID_SAMPLE_COLUMNS = (
    "timestamp_utc",
    "timestamp_epoch_s",
    "session_id",
    "sample_index",
    "session_elapsed_s",
    "beam_sample_timestamp",
    "beam_age_s",
    "beam_status",
    "selected_tc_index",
    "selected_tc_number",
    "selected_tc_name",
    "setpoint_nA",
    "beam_nA",
    "signed_error_nA",
    "abs_error_nA",
    "deadband_nA",
    "trend_tolerance_nA",
    "direction_check_s",
    "initial_direction",
    "direction",
    "direction_changed",
    "error_trend",
    "in_deadband",
    "kp",
    "ki",
    "kd",
    "p_term_a",
    "i_term_a",
    "d_term_a",
    "pid_magnitude_a",
    "raw_pid_output_a",
    "signed_delta_tc_a",
    "loop_dt_s",
    "loop_period_ms",
    "derivative_filter_tau_s",
    "output_min_a",
    "output_max_a",
    "integral_min_as",
    "integral_max_as",
    "tc_min_a",
    "tc_max_a",
    "baseline_target_a",
    "tc_target_before_a",
    "tc_target_proposed_a",
    "tc_target_after_a",
    "tc_measured_a",
    "target_limited",
    "pid_saturated",
    "hardware_armed",
    "output_applied",
    "control_mode",
    "ga_auto_active",
    "pid_status",
)


_CREATE_PID_SAMPLES = """
CREATE TABLE IF NOT EXISTS pid_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc TEXT NOT NULL,
    timestamp_epoch_s REAL NOT NULL,
    session_id TEXT NOT NULL,
    sample_index INTEGER NOT NULL,
    session_elapsed_s REAL,
    beam_sample_timestamp REAL,
    beam_age_s REAL,
    beam_status TEXT,
    selected_tc_index INTEGER,
    selected_tc_number INTEGER,
    selected_tc_name TEXT,
    setpoint_nA REAL,
    beam_nA REAL,
    signed_error_nA REAL,
    abs_error_nA REAL,
    deadband_nA REAL,
    trend_tolerance_nA REAL,
    direction_check_s REAL,
    initial_direction INTEGER,
    direction INTEGER,
    direction_changed INTEGER,
    error_trend TEXT,
    in_deadband INTEGER,
    kp REAL,
    ki REAL,
    kd REAL,
    p_term_a REAL,
    i_term_a REAL,
    d_term_a REAL,
    pid_magnitude_a REAL,
    raw_pid_output_a REAL,
    signed_delta_tc_a REAL,
    loop_dt_s REAL,
    loop_period_ms INTEGER,
    derivative_filter_tau_s REAL,
    output_min_a REAL,
    output_max_a REAL,
    integral_min_as REAL,
    integral_max_as REAL,
    tc_min_a REAL,
    tc_max_a REAL,
    baseline_target_a REAL,
    tc_target_before_a REAL,
    tc_target_proposed_a REAL,
    tc_target_after_a REAL,
    tc_measured_a REAL,
    target_limited INTEGER,
    pid_saturated INTEGER,
    hardware_armed INTEGER,
    output_applied INTEGER,
    control_mode TEXT,
    ga_auto_active INTEGER,
    pid_status TEXT
)
"""

_CREATE_PID_EVENTS = """
CREATE TABLE IF NOT EXISTS pid_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc TEXT NOT NULL,
    timestamp_epoch_s REAL NOT NULL,
    session_id TEXT,
    event_type TEXT NOT NULL,
    details_json TEXT
)
"""


class PIDSQLiteLogger:
    """Non-blocking PID logger backed by one SQLite worker thread."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        queue_size: int = 20_000,
        batch_size: int = 200,
        flush_interval_s: float = 0.25,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.queue_size = max(100, int(queue_size))
        self.batch_size = max(1, int(batch_size))
        self.flush_interval_s = max(0.02, float(flush_interval_s))

        self._queue: queue.Queue[tuple[str, Any] | None] = queue.Queue(
            maxsize=self.queue_size
        )
        self._thread: threading.Thread | None = None
        self._started = False
        self._closing = False
        self._lock = threading.Lock()
        self._dropped_records = 0
        self._written_samples = 0
        self._written_events = 0
        self._last_error = ""

    @property
    def started(self) -> bool:
        return bool(self._started and self._thread is not None and self._thread.is_alive())

    @property
    def dropped_records(self) -> int:
        with self._lock:
            return int(self._dropped_records)

    @property
    def written_samples(self) -> int:
        with self._lock:
            return int(self._written_samples)

    @property
    def written_events(self) -> int:
        with self._lock:
            return int(self._written_events)

    @property
    def last_error(self) -> str:
        with self._lock:
            return str(self._last_error)

    def status(self) -> dict[str, Any]:
        return {
            "started": self.started,
            "db_path": str(self.db_path),
            "queued": int(self._queue.qsize()),
            "dropped": self.dropped_records,
            "written_samples": self.written_samples,
            "written_events": self.written_events,
            "last_error": self.last_error,
        }

    def start(self) -> None:
        if self.started:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._closing = False
        self._thread = threading.Thread(
            target=self._worker,
            name="PIDSQLiteLogger",
            daemon=True,
        )
        self._thread.start()
        self._started = True

    @staticmethod
    def utc_now_text() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    @staticmethod
    def new_session_id(prefix: str = "PID") -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        return f"{prefix}_{stamp}"

    def log_sample(self, record: Mapping[str, Any]) -> bool:
        """Queue one sample without blocking the caller.

        Missing fields are stored as NULL. Booleans are converted to 0/1 by
        SQLite. If the queue is full, the new sample is dropped and the control
        loop continues uninterrupted.
        """
        if not self.started:
            self.start()
        values = tuple(record.get(column) for column in PID_SAMPLE_COLUMNS)
        return self._enqueue(("sample", values))

    def log_event(
        self,
        event_type: str,
        *,
        session_id: str | None = None,
        details: Mapping[str, Any] | str | None = None,
    ) -> bool:
        if not self.started:
            self.start()
        now = time.time()
        if details is None:
            details_json = None
        elif isinstance(details, str):
            details_json = json.dumps({"message": details}, ensure_ascii=False)
        else:
            details_json = json.dumps(dict(details), ensure_ascii=False, default=str)
        row = (
            self.utc_now_text(),
            now,
            session_id,
            str(event_type),
            details_json,
        )
        return self._enqueue(("event", row))

    def _enqueue(self, item: tuple[str, Any]) -> bool:
        if self._closing:
            return False
        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            with self._lock:
                self._dropped_records += 1
            return False

    def close(self, timeout_s: float = 2.0) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            # Make room for the sentinel without blocking the GUI thread.
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout_s)))

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        declaration: str,
    ) -> None:
        existing = {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if column not in existing:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.db_path),
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute(_CREATE_PID_SAMPLES)
        connection.execute(_CREATE_PID_EVENTS)
        # Forward-compatible migration for databases created by an earlier
        # development build of this PID-only logger.
        self._ensure_column(connection, "pid_samples", "session_elapsed_s", "REAL")
        self._ensure_column(connection, "pid_samples", "selected_tc_number", "INTEGER")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_pid_samples_session_time "
            "ON pid_samples(session_id, timestamp_epoch_s)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_pid_samples_tc_time "
            "ON pid_samples(selected_tc_name, timestamp_epoch_s)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_pid_events_session_time "
            "ON pid_events(session_id, timestamp_epoch_s)"
        )
        return connection

    def _worker(self) -> None:
        connection: sqlite3.Connection | None = None
        sample_sql = (
            f"INSERT INTO pid_samples ({', '.join(PID_SAMPLE_COLUMNS)}) "
            f"VALUES ({', '.join(['?'] * len(PID_SAMPLE_COLUMNS))})"
        )
        event_sql = (
            "INSERT INTO pid_events "
            "(timestamp_utc, timestamp_epoch_s, session_id, event_type, details_json) "
            "VALUES (?, ?, ?, ?, ?)"
        )

        try:
            connection = self._connect()
            samples: list[tuple[Any, ...]] = []
            events: list[tuple[Any, ...]] = []
            deadline = time.monotonic() + self.flush_interval_s
            stop_requested = False

            while True:
                timeout = max(0.0, deadline - time.monotonic())
                try:
                    item = self._queue.get(timeout=timeout)
                except queue.Empty:
                    item = "flush"

                if item is None:
                    stop_requested = True
                elif item == "flush":
                    pass
                else:
                    kind, row = item
                    if kind == "sample":
                        samples.append(tuple(row))
                    elif kind == "event":
                        events.append(tuple(row))

                should_flush = (
                    stop_requested
                    or len(samples) + len(events) >= self.batch_size
                    or time.monotonic() >= deadline
                )
                if should_flush and (samples or events):
                    connection.execute("BEGIN")
                    try:
                        if samples:
                            connection.executemany(sample_sql, samples)
                        if events:
                            connection.executemany(event_sql, events)
                        connection.execute("COMMIT")
                    except Exception:
                        connection.execute("ROLLBACK")
                        raise
                    with self._lock:
                        self._written_samples += len(samples)
                        self._written_events += len(events)
                    samples.clear()
                    events.clear()
                    deadline = time.monotonic() + self.flush_interval_s

                if stop_requested:
                    # Drain any records queued before the sentinel, then exit.
                    while True:
                        try:
                            pending = self._queue.get_nowait()
                        except queue.Empty:
                            break
                        if pending is None:
                            continue
                        kind, row = pending
                        if kind == "sample":
                            samples.append(tuple(row))
                        elif kind == "event":
                            events.append(tuple(row))
                    if samples or events:
                        connection.execute("BEGIN")
                        try:
                            if samples:
                                connection.executemany(sample_sql, samples)
                            if events:
                                connection.executemany(event_sql, events)
                            connection.execute("COMMIT")
                        except Exception:
                            connection.execute("ROLLBACK")
                            raise
                        with self._lock:
                            self._written_samples += len(samples)
                            self._written_events += len(events)
                    break

        except Exception as exc:  # pragma: no cover - disk/SQLite boundary
            with self._lock:
                self._last_error = f"{type(exc).__name__}: {exc}"
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
            self._started = False


__all__ = ["PIDSQLiteLogger", "PID_SAMPLE_COLUMNS"]
