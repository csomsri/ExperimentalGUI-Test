# sqlite_logger.py — fast, non-blocking, batch SQLite logger for Cyclotron Control System
# Prevents backlog accumulation and UI stalls by:
#  - avoiding Queue.empty()
#  - batching inserts (executemany)
#  - committing periodically
#  - optional backlog drop guard

from __future__ import annotations

import os
import time
import sqlite3
from typing import Dict, Any, List, Optional, Tuple
from queue import Empty

# ---------------- CONFIG ----------------
DB_PATH = os.environ.get("CNL_DB_PATH", "cyclotron_data.db")
TABLE_NAME = os.environ.get("CNL_DB_TABLE", "channel_data")

# Insert batching
BATCH_MAX_ROWS   = int(os.environ.get("CNL_LOG_BATCH", "500"))     # rows per executemany
COMMIT_EVERY_S   = float(os.environ.get("CNL_LOG_COMMIT_S", "0.5"))# commit period
GET_TIMEOUT_S    = float(os.environ.get("CNL_LOG_GET_TO", "0.05")) # queue.get timeout

# Backlog safety:
# If logger falls behind and the queue grows, this prevents memory bloat.
# Set to 0 to disable dropping.
DROP_IF_BACKLOG_OVER = int(os.environ.get("CNL_LOG_DROP_OVER", "5000"))

# If your scaling is disabled and you don't want *_eu columns, set this False
WRITE_EU_COLUMNS = os.environ.get("CNL_LOG_WRITE_EU", "1") == "1"

# ---------------- SIGNAL MAP ----------------
# These are the keys your pkt dict may contain (from zmq_rep_server):
#   timestamp, channels(trim14), source(6), extraction(6), extraction_angles(6),
#   transport(10), vacuum(5), rf_power_kv, beam_current, bitmask, latency, ts_mode, beam_range_idx
#
# We'll store them in columns that match your GUI expectations.
TRIM_COLS = [f"ch{i}" for i in range(1, 13)] + ["main_magnet", "centering_beam"]

EXTRACTION_COLS = ["coax_a", "inside_iron_a", "outside_iron_a", "esd_v", "esd_a", "fringe_a"]
EXTR_ANGLE_COLS = ["esd_in_angle", "esd_out_angle", "coax_in_angle", "coax_out_angle", "iron_in_angle", "iron_out_angle"]
SOURCE_COLS     = ["filament_a", "filament_v", "arc_v", "arc_a", "gas_flow", "gas_turns"]
VAC_COLS        = ["vac1", "vac2", "vac3", "vac4", "vac5"]
TRANS_COLS      = ["q1a_a","q1b_a","q2a_a","q2b_a","steer40_a","steer50_a","coll_s1_l_a","coll_s1_r_a","coll_s2_l_a","coll_s2_r_a"]

META_COLS = [
    "timestamp", "bitmask", "latency", "ts_mode", "beam_range_idx",
    "rf_power_kv", "beam_current"
]

ALL_BASE_COLS = META_COLS + TRIM_COLS + EXTRACTION_COLS + EXTR_ANGLE_COLS + SOURCE_COLS + VAC_COLS + TRANS_COLS


def _ensure_table(conn: sqlite3.Connection, cols: List[str]) -> None:
    cur = conn.cursor()

    # Create table if not exists (timestamp stored as REAL)
    # Use ROWID as implicit primary key.
    col_defs = ["timestamp REAL"]
    for c in cols:
        if c == "timestamp":
            continue
        # store everything numeric as REAL except bitmask (INTEGER) and ts_mode (TEXT)
        if c == "bitmask" or c == "beam_range_idx":
            col_defs.append(f"{c} INTEGER")
        elif c == "ts_mode":
            col_defs.append(f"{c} TEXT")
        else:
            col_defs.append(f"{c} REAL")

    cur.execute(f"CREATE TABLE IF NOT EXISTS {TABLE_NAME} ({', '.join(col_defs)})")

    # Ensure missing columns exist (safe incremental upgrade)
    existing = {r[1] for r in cur.execute(f"PRAGMA table_info({TABLE_NAME})").fetchall()}
    for c in cols:
        if c in existing:
            continue
        if c == "bitmask" or c == "beam_range_idx":
            cur.execute(f"ALTER TABLE {TABLE_NAME} ADD COLUMN {c} INTEGER")
        elif c == "ts_mode":
            cur.execute(f"ALTER TABLE {TABLE_NAME} ADD COLUMN {c} TEXT")
        elif c == "timestamp":
            # already present by definition
            pass
        else:
            cur.execute(f"ALTER TABLE {TABLE_NAME} ADD COLUMN {c} REAL")

    # Helpful indexes (fast “latest row” queries)
    cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_ts ON {TABLE_NAME}(timestamp)")
    conn.commit()


def _row_from_pkt(pkt: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert pkt dict into flat DB row dict.
    Missing fields become None.
    """
    row: Dict[str, Any] = {}

    # Meta
    row["timestamp"] = pkt.get("timestamp", None)
    row["bitmask"] = pkt.get("bitmask", None)
    row["latency"] = pkt.get("latency", None)
    row["ts_mode"] = pkt.get("ts_mode", None)
    row["beam_range_idx"] = pkt.get("beam_range_idx", None)
    row["rf_power_kv"] = pkt.get("rf_power_kv", None)
    row["beam_current"] = pkt.get("beam_current", None)

    # Trim channels (14)
    ch = pkt.get("channels", None)
    if isinstance(ch, list) and len(ch) >= 14:
        for i, name in enumerate(TRIM_COLS):
            row[name] = ch[i]

    # Extraction (6)
    ex = pkt.get("extraction", None)
    if isinstance(ex, list) and len(ex) >= 6:
        for i, name in enumerate(EXTRACTION_COLS):
            row[name] = ex[i]

    # Extraction angles (6)
    ang = pkt.get("extraction_angles", None)
    if isinstance(ang, list) and len(ang) >= 6:
        for i, name in enumerate(EXTR_ANGLE_COLS):
            row[name] = ang[i]

    # Source (6)
    src = pkt.get("source", None)
    if isinstance(src, list) and len(src) >= 6:
        for i, name in enumerate(SOURCE_COLS):
            row[name] = src[i]

    # Vacuum (5)
    vac = pkt.get("vacuum", None)
    if isinstance(vac, list) and len(vac) >= 5:
        for i, name in enumerate(VAC_COLS):
            row[name] = vac[i]

    # Transport (10)
    tr = pkt.get("transport", None)
    if isinstance(tr, list) and len(tr) >= 10:
        for i, name in enumerate(TRANS_COLS):
            row[name] = tr[i]

    return row


def sqlite_logger(log_queue) -> None:
    """
    Process target: reads dict packets from multiprocessing.Queue and writes to SQLite.
    """

    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
    cur = conn.cursor()

    # SQLite performance pragmas (safe, fast)
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA synchronous=NORMAL;")
    cur.execute("PRAGMA temp_store=MEMORY;")
    cur.execute("PRAGMA cache_size=-200000;")   # ~200MB cache in KB units (negative = KB)
    cur.execute("PRAGMA busy_timeout=30000;")

    _ensure_table(conn, ALL_BASE_COLS)

    # Prepare insert statement
    ordered_cols = list(ALL_BASE_COLS)
    placeholders = ",".join(["?"] * len(ordered_cols))
    insert_sql = f"INSERT INTO {TABLE_NAME} ({', '.join(ordered_cols)}) VALUES ({placeholders})"

    buf_rows: List[Tuple[Any, ...]] = []
    last_commit = time.time()

    # Backlog drop bookkeeping
    dropped_total = 0
    last_stat = time.time()

    while True:
        # 1) Pull at least one item (blocking w/ timeout) so we don't busy-spin
        try:
            pkt = log_queue.get(timeout=GET_TIMEOUT_S)
        except Exception:
            pkt = None

        if isinstance(pkt, dict):
            row = _row_from_pkt(pkt)
            buf_rows.append(tuple(row.get(c, None) for c in ordered_cols))

        # 2) Drain more immediately available items (fast burst handling)
        # Fill up to BATCH_MAX_ROWS
        while len(buf_rows) < BATCH_MAX_ROWS:
            try:
                pkt2 = log_queue.get_nowait()
            except Exception:
                break
            if isinstance(pkt2, dict):
                row2 = _row_from_pkt(pkt2)
                buf_rows.append(tuple(row2.get(c, None) for c in ordered_cols))

        # 3) Backlog guard: if queue is exploding, optionally drop old packets
        # This keeps the whole system responsive.
        if DROP_IF_BACKLOG_OVER > 0:
            try:
                qsz = log_queue.qsize()
            except Exception:
                qsz = 0
            if qsz and qsz > DROP_IF_BACKLOG_OVER:
                # Drop a chunk of oldest packets quickly
                drop_n = min(qsz - DROP_IF_BACKLOG_OVER, 2000)
                for _ in range(drop_n):
                    try:
                        _ = log_queue.get_nowait()
                        dropped_total += 1
                    except Exception:
                        break

        # 4) Write batch if we have anything
        if buf_rows:
            try:
                cur.executemany(insert_sql, buf_rows)
            except sqlite3.OperationalError as e:
                # In case of transient DB lock, retry once after a short delay
                time.sleep(0.05)
                cur.executemany(insert_sql, buf_rows)

            buf_rows.clear()

        # 5) Commit periodically
        now = time.time()
        if (now - last_commit) >= COMMIT_EVERY_S:
            conn.commit()
            last_commit = now

        # 6) Optional periodic status
        if now - last_stat >= 5.0:
            if dropped_total:
                print(f"[sqlite_logger] Dropped {dropped_total} packets due to backlog protection.")
                dropped_total = 0
            last_stat = now