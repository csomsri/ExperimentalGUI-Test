# -*- coding: utf-8 -*-
"""
Created on Tue Oct  7 07:28:27 2025

@author: clasa
"""
from project_paths import project_file

# -*- coding: utf-8 -*-
# beam_trace.py — drop-in tracer for the beam pipeline (ASCII-safe)
#
# Usage (add 1-liners):
#   in main_control_gui.py, near the top:
#       import beam_trace; beam_trace.install()
#   in zmq_rep_server.py, right after you set pkt["beam_current"]:
#       import beam_trace; beam_trace.trace_rep(pkt)
#   [optional] in VacuumBeamMonitoringWindow.py, inside update_gui() after you compute disp:
#       import beam_trace; beam_trace.trace_window(last, idx, i_nA, disp, self.g_bm._unit, self.ts_bm._ymax)
#
# Then run your app; logs go to beam_trace.log next to this file.

import os, sys, time, functools, threading

_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), str(project_file('beam_trace.log')))
_LOCK = threading.Lock()
_installed = False

def _ts():
    return time.strftime("%Y-%m-%d %H:%M:%S")

def _writeline(line: str):
    try:
        with _LOCK:
            with open(_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"{_ts()} {line}\n")
    except Exception:
        # last resort: print to console
        print(line)

# ---------------- public trace helpers ----------------

def trace_rep(pkt: dict):
    """Call from zmq_rep_server right after parsing the vac+beam tail."""
    try:
        bc = pkt.get("beam_current")
        if isinstance(bc, (list, tuple)):
            bc = bc[0] if bc else None
        bri = pkt.get("beam_range_idx")
        _writeline(f"[REP] beam_current={bc!r}  beam_range_idx={bri!r}")
    except Exception as e:
        _writeline(f"[REP] error: {e!r}")

def trace_window(pkt: dict, idx: int, i_nA: float, disp: float, unit: str, y_max: float):
    """Call from VacuumBeamMonitoringWindow after computing disp (what you actually draw)."""
    try:
        raw = pkt.get("beam_current")
        if isinstance(raw, (list, tuple)):
            raw = raw[0] if raw else None
        _writeline(f"[WIN] idx={idx}  raw={raw!r}  i_nA={i_nA:.6f}  disp={disp:.6f} {unit}  y_max={y_max:.6f} {unit}")
    except Exception as e:
        _writeline(f"[WIN] error: {e!r}")

# ------------- monkey-patch beam_cal safely -------------

def _wrap_beam_cal():
    """Wrap beam_cal.volts_to_nA and select_index_for_pkt to log I/O."""
    try:
        import beam_cal as BC
    except Exception as e:
        _writeline(f"[TRACE] beam_cal import failed: {e!r}")
        return

    # guard: only wrap once
    if getattr(BC, "_beam_trace_wrapped", False):
        return

    # wrap volts_to_nA
    try:
        orig_v2n = BC.volts_to_nA
        @functools.wraps(orig_v2n)
        def v2n_traced(volts, range_idx=None):
            t0 = time.time()
            out = orig_v2n(volts, range_idx)
            dt = (time.time()-t0)*1e3
            try:
                flip = getattr(BC.cfg, "flip_sign", None)
                _writeline(f"[BC.cvt] volts={volts:+.6f} -> {out:.6f} nA  flip_sign={flip}  dt={dt:.2f}ms")
            except Exception:
                _writeline(f"[BC.cvt] volts={volts!r} -> {out!r} nA")
            return out
        BC.volts_to_nA = v2n_traced
    except Exception as e:
        _writeline(f"[TRACE] wrap volts_to_nA failed: {e!r}")

    # wrap select_index_for_pkt
    try:
        orig_sel = BC.select_index_for_pkt
        @functools.wraps(orig_sel)
        def sel_traced(pkt):
            idx = orig_sel(pkt)
            try:
                raw = pkt.get("beam_current")
                if isinstance(raw, (list, tuple)):
                    raw = raw[0] if raw else None
                est = BC.volts_to_nA(raw) if raw is not None else None
                _writeline(f"[BC.sel] idx={idx}  raw={raw!r}  est={est if est is None else round(est,6)} nA")
            except Exception:
                _writeline(f"[BC.sel] idx={idx}")
            return idx
        BC.select_index_for_pkt = sel_traced
    except Exception as e:
        _writeline(f"[TRACE] wrap select_index_for_pkt failed: {e!r}")

    setattr(BC, "_beam_trace_wrapped", True)
    _writeline("[TRACE] beam_cal hooks installed")

# ---------------- install entrypoint ----------------

def install():
    """Call once early (e.g., in main_control_gui.py) to enable tracing."""
    global _installed
    if _installed:
        return
    _installed = True
    _writeline("========== Beam Trace START ==========")
    _wrap_beam_cal()

# ---------------- convenience: tail on demand ----------------

def tail(n=100):
    """Quick helper: print last n lines of the log."""
    try:
        with open(_LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()[-int(n):]
        for ln in lines:
            sys.stdout.write(ln)
    except Exception as e:
        print(f"[TRACE] tail failed: {e!r}")

if __name__ == "__main__":
    # allow: python beam_trace.py --tail 200
    if len(sys.argv) >= 2 and sys.argv[1] == "--tail":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 100
        tail(n)
    else:
        print("Beam tracer is a helper module. Use --tail or import and call install().")
