# zmq_rep_server.py — REQ/REP server (fast path, LabVIEW-compatible)
# v2025-10-13  robust epoch+unit detection, no TCP_NODELAY, gentle queue fallback

from __future__ import annotations
import os, time, struct, json
from typing import List, Dict, Any, Optional, Tuple
import zmq
from queue import Full, Empty

# ---------- Protocol sizes (unchanged) ----------
N_TRIM    = 14
N_SRCEX12 = 12
N_SRCEX18 = 18
N_TRANS   = 10
N_VACBM7  = 7   # vac1..vac5, rf_kV, beam_raw
REPLY_DOUBLES = 14 + 1
REPLY_FMT     = "<" + "d"*REPLY_DOUBLES

# LabVIEW epoch (1904) → Unix epoch (1970)
EPOCH_OFFSET = 2082844800.0

# ---------- Debug toggles ----------
DBG_VERBOSE  = True
DBG_RAW      = os.environ.get("ZMQ_RAW", "0") == "1"
DUMP_JSON_FP = os.environ.get("ZMQ_DUMP_JSON", "").strip() or None
LOG_EVERY_S  = float(os.environ.get("ZMQ_LOG_PERIOD", "1.0"))  # periodic status print

def _bind_with_fallback(sock, preferred_ep: str) -> str:
    try:
        sock.bind(preferred_ep)
        print(f"✅ REP server on {preferred_ep}")
        return preferred_ep
    except zmq.ZMQError as e:
        port = sock.bind_to_random_port("tcp://127.0.0.1")
        ep = f"tcp://127.0.0.1:{port}"
        print(f"⚠️ {preferred_ep} busy; using {ep}  [{e}]")
        return ep

def _decode_beam_idx_from_bitmask(bm: int) -> Optional[int]:
    bits = (bm >> 28) & 0x3FF
    for i in range(9, -1, -1):
        if (bits >> i) & 1:
            return i
    return None

def _slice_best_effort(doubles: Tuple[float, ...]) -> Dict[str, Any]:
    nd = len(doubles)
    out: Dict[str, Any] = {}
    if nd < 1 + N_TRIM + 1:
        return out
    i = 0
    ts = doubles[i]; i += 1
    trim = doubles[i:i+N_TRIM]; i += N_TRIM
    mid = doubles[i:-1] if nd - i >= 1 else ()
    bm_dbl = doubles[-1] if nd - i >= 1 else 0.0
    bm = int(round(bm_dbl))

    out["timestamp"] = ts
    out["channels"]  = list(trim)
    out["bitmask"]   = bm

    remain = len(mid)
    if remain <= 0:
        return out

    # try SRCE/EX 18 then 12
    off = 0
    if remain - off >= N_SRCEX18:
        se18 = mid[off:off+N_SRCEX18]; off += N_SRCEX18
        out["extraction"] = list(se18[:6])
        out["extraction_angles"] = list(se18[6:12])
        out["source"] = list(se18[12:18])
    elif remain - off >= N_SRCEX12:
        se12 = mid[off:off+N_SRCEX12]; off += N_SRCEX12
        out["extraction"] = list(se12[:6])
        out["source"]     = list(se12[6:12])

    # attempt TRANS+VAC or VAC+TRANS exact fit
    def try_layout(off0: int, order: str):
        off = off0
        trans = None; vacbm = None; vac_idx = None
        left = remain - off
        if order == "TRANS_FIRST":
            if left >= N_TRANS:
                trans = mid[off:off+N_TRANS]; off += N_TRANS
                left = remain - off
                if left == N_VACBM7:
                    vacbm = mid[off:off+N_VACBM7]; off += N_VACBM7
                elif left == N_VACBM7 + 1:
                    vacbm = mid[off:off+N_VACBM7]; off += N_VACBM7
                    vac_idx = int(round(mid[off])); off += 1
        else:
            if left >= N_VACBM7:
                if left >= N_VACBM7 + N_TRANS + 1:
                    vacbm = mid[off:off+N_VACBM7]; off += N_VACBM7
                    vac_idx = int(round(mid[off])); off += 1
                else:
                    vacbm = mid[off:off+N_VACBM7]; off += N_VACBM7
                left = remain - off
                if left == N_TRANS:
                    trans = mid[off:off+N_TRANS]; off += N_TRANS
        return trans, vacbm, vac_idx, off

    trans = None; vacbm = None; beam_range_idx = None
    t1,v1,idx1,off1 = try_layout(off, "TRANS_FIRST")
    if t1 is not None and v1 is not None and off1 == remain:
        trans, vacbm, beam_range_idx = t1, v1, idx1
    else:
        t2,v2,idx2,off2 = try_layout(off, "VAC_FIRST")
        if t2 is not None and v2 is not None and off2 == remain:
            trans, vacbm, beam_range_idx = t2, v2, idx2
        else:
            off2 = off
            if remain - off2 >= N_TRANS:
                trans = mid[off2:off2+N_TRANS]; off2 += N_TRANS
            if remain - off2 >= N_VACBM7:
                vacbm = mid[off2:off2+N_VACBM7]; off2 += N_VACBM7

    if trans is not None: out["transport"] = list(trans[:N_TRANS])
    if vacbm is not None:
        out["vacuum"] = list(vacbm[:5])
        if len(vacbm) >= 6: out["rf_power_kv"]  = float(vacbm[5])
        if len(vacbm) >= 7: out["beam_current"] = float(vacbm[6])

    if beam_range_idx is not None:
        out["beam_range_idx"] = int(beam_range_idx)
    else:
        idx = _decode_beam_idx_from_bitmask(bm)
        if idx is not None: out["beam_range_idx"] = idx

    if DBG_RAW:
        print(f"RAW mid len={len(mid)}  TRANS={'Y' if trans else 'n'}  VAC={'Y' if vacbm else 'n'}")
    return out

# --- Timestamp normalization: handles s / ms / µs / ns + LV/Unix epochs ---
def _normalize_timestamp(ts_raw: float) -> Tuple[float, str]:
    """
    Returns (unix_seconds, mode_string)
    mode_string in {'labview_s','unix_s','ms','us','ns','small/legacy'}
    """
    ts = float(ts_raw)
    # 1) normalize units to seconds
    if ts > 1e14:
        ts = ts / 1e9; mode = "ns"
    elif ts > 1e12:
        ts = ts / 1e6; mode = "us"
    elif ts > 1e10:
        ts = ts / 1e3; mode = "ms"
    else:
        mode = "s"

    # 2) decide epoch: LabVIEW seconds are ~3.7–3.9e9 today; Unix ~1.6–1.9e9
    if ts > 3.0e9:
        # looks like LabVIEW epoch seconds
        return ts - EPOCH_OFFSET, f"labview_{mode}"
    elif ts > 1.0e9:
        return ts, f"unix_{mode}"
    else:
        return ts, "small/legacy"

# ---------- Main server ----------
def zmq_server_main(
    data_queue,
    target_values,
    control_queue,
    log_queue,
    endpoint: Optional[str] = None,
):
    ctx = zmq.Context(io_threads=1)
    sock = ctx.socket(zmq.REP)

    # Safe timeouts + big buffers (no TCP_NODELAY in pyzmq)
    sock.setsockopt(zmq.LINGER,             0)
    sock.setsockopt(zmq.RCVTIMEO,        1500)
    sock.setsockopt(zmq.SNDTIMEO,        1500)
    sock.setsockopt(zmq.TCP_KEEPALIVE,      1)
    sock.setsockopt(zmq.TCP_KEEPALIVE_IDLE, 30)
    sock.setsockopt(zmq.TCP_KEEPALIVE_CNT,   5)
    sock.setsockopt(zmq.TCP_KEEPALIVE_INTVL,30)
    sock.setsockopt(zmq.RCVHWM,          50000)
    sock.setsockopt(zmq.SNDHWM,          50000)
    sock.setsockopt(zmq.RCVBUF,    4*1024*1024)
    sock.setsockopt(zmq.SNDBUF,    4*1024*1024)

    preferred = endpoint or os.environ.get("ZMQ_ENDPOINT", "tcp://0.0.0.0:5555")
    _bind_with_fallback(sock, preferred)

    latest_targets = (
        list(target_values[:N_TRIM])
        if isinstance(target_values, (list, tuple)) and len(target_values) >= N_TRIM
        else [0.0]*N_TRIM
    )
    latest_bitmask = 0
    pending_range_idx: Optional[int] = None

    last_log = 0.0
    pkt_count = 0
    drop_gui = drop_log = 0

    try:
        while True:
            try:
                frame = sock.recv(copy=False)
            except zmq.Again:
                continue

            pid_rx_mono = time.monotonic()
            buf = memoryview(frame.buffer)
            mlen = len(buf)

            # Always reply to keep REQ/REP lockstep
            if mlen % 8 != 0 or mlen < (1 + N_TRIM + 1)*8:
                reply_bm = latest_bitmask
                if pending_range_idx is not None:
                    reply_bm &= ~(0xF << 28)
                    reply_bm |= ((pending_range_idx & 0xF) << 28)
                rep = struct.pack(REPLY_FMT, *(latest_targets + [float(reply_bm)]))
                sock.send(rep, copy=False)
                continue

            nd = mlen // 8
            doubles = struct.unpack_from("<" + "d"*nd, buf, 0)
            pkt = _slice_best_effort(doubles)


# ==========================================================
# TEMPORARY FAKE BITMASK FOR GUI TESTING
# ==========================================================

            FAKE_BITMASK = True

            if FAKE_BITMASK:
                # Bits 0..13   = ON status for TC1..TC14
                # Bits 14..27  = REMOTE/ENABLE status for TC1..TC14

                fake_on_flags = (1 << 14) - 1
                fake_remote_flags = ((1 << 14) - 1) << 14

                fake_bitmask = fake_on_flags | fake_remote_flags

                pkt["bitmask"] = fake_bitmask






            # Robust timestamp → latency
            ts_unix, mode = _normalize_timestamp(pkt.get("timestamp", 0.0))
            now = time.time()
            latency_ms = (now - ts_unix) * 1000.0
            pkt["latency"] = round(latency_ms, 3)
            pkt["ts_mode"] = mode

            # Optional JSON dump
            if DUMP_JSON_FP:
                try:
                    with open(DUMP_JSON_FP, "w", encoding="utf-8") as f:
                        json.dump(pkt, f, indent=2, ensure_ascii=False)
                except Exception:
                    pass

            # Additive PID-only metadata; existing SQLite fields/wire reply unchanged.
            # All Python processes on this host share the monotonic clock.
            pkt["_pid_rx_mono"] = pid_rx_mono
            pkt["_pid_source_s"] = float(ts_unix)

            # Fan-out: non-blocking first; if full, try a 1 ms nudge
            try:
                data_queue.put_nowait(pkt)
            except Full:
                try:
                    data_queue.put(pkt, timeout=0.001)
                except Full:
                    drop_gui += 1
            try:
                log_queue.put_nowait(dict(pkt))
            except Full:
                try:
                    log_queue.put(dict(pkt), timeout=0.001)
                except Full:
                    drop_log += 1

            # Drain control queue quickly
            while True:
                try:
                    item = control_queue.get_nowait()
                except Exception:
                    break
                if isinstance(item, tuple) and len(item) >= 3:
                    new_targets, on_flags, en_flags = item[:3]
                    latest_targets = list(new_targets)[:N_TRIM]
                    bm = 0
                    for i in range(N_TRIM):
                        if i < len(on_flags) and on_flags[i]: bm |= (1 << i)
                        if i < len(en_flags) and en_flags[i]: bm |= (1 << (14 + i))
                    latest_bitmask = bm
                elif isinstance(item, dict) and "beam_range_idx" in item:
                    pending_range_idx = max(0, min(9, int(item["beam_range_idx"])))

            # Build reply
            reply_bm = latest_bitmask
            if pending_range_idx is not None:
                reply_bm &= ~(0xF << 28)
                reply_bm |= ((pending_range_idx & 0xF) << 28)
            rep = struct.pack(REPLY_FMT, *(latest_targets + [float(reply_bm)]))
            sock.send(rep, copy=False)

            # Periodic status (not per-packet)
            pkt_count += 1
            t = now
            if t - last_log >= LOG_EVERY_S:
                bm_req = int(pkt.get("bitmask", 0))
                print(
                    f"📥 pkts:{pkt_count} | lat:{latency_ms:.2f} ms | "
                    f"ts:{mode} | req bm:0x{bm_req:016X} | rep bm:0x{reply_bm:016X} | "
                    f"drops gui/log:{drop_gui}/{drop_log}"
                )
                pkt_count = 0; drop_gui = 0; drop_log = 0; last_log = t

            if DBG_VERBOSE:
                v = pkt.get("vacuum")
                print("—"*64)
                print(f"[PKT] mode={mode} lat={latency_ms:.2f} ms bm=0x{int(pkt.get('bitmask',0)):016X}")
                print(" trim[:5]   =", [f"{x:.3f}" for x in pkt.get('channels', [])[:5]])
                if "source" in pkt:            print(" source(6)  =", [f"{x:.3f}" for x in pkt['source']])
                if "extraction" in pkt:        print(" extract(6) =", [f"{x:.3f}" for x in pkt['extraction']])
                if "extraction_angles" in pkt: print(" angles(6)  =", [f"{x:.3f}" for x in pkt['extraction_angles']])
                if "transport" in pkt:         print(" trans(10)  =", [f"{x:.3f}" for x in pkt['transport']])
                if v:                           print(" vacuum(5)  =", [f"{x:.3e}" for x in v])
                if "rf_power_kv" in pkt:       print(" rf_kV      =", f"{pkt['rf_power_kv']:.3f}")
                print(" beam_current=", pkt.get("beam_current"))
                print(" beam_range_idx=", pkt.get("beam_range_idx"))

    except KeyboardInterrupt:
        print("👋 REP server stopping...")
    finally:
        try:
            sock.setsockopt(zmq.LINGER, 0); sock.close()
        except Exception:
            pass
        ctx.term()
