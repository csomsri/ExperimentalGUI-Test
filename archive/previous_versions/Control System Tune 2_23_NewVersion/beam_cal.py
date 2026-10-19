# beam_cal.py — Proton beam calibration & range selection (10 ranges: 100 pA → 100 µA)
from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import List, Tuple, Optional
import json, os

CFG_PATH = os.environ.get("CNL_BEAM_CAL", "beam_cal.json")


@dataclass
class BeamRange:
    name: str = "Range"
    fs_nA: float = 100.0               # full-scale in nA (drives display units & gauge FS)
    mode: str = "linear"               # "linear" or "curve"
    gain_na_per_v: float = 1.0         # linear slope (nA per Volt)
    offset_na: float = 0.0             # linear offset (nA)
    curve: List[Tuple[float, float]] = field(default_factory=list)  # [(V, I_nA)] for LUT mode


@dataclass
class BeamCalConfig:
    # *** TEN RANGES: 100 pA … 100 µA (fs in nA) ***
    ranges: List[BeamRange] = field(default_factory=lambda: [
        BeamRange(name="100 pA",  fs_nA=0.1),
        BeamRange(name="300 pA",  fs_nA=0.3),
        BeamRange(name="1 nA",    fs_nA=1.0),
        BeamRange(name="3 nA",    fs_nA=3.0),
        BeamRange(name="10 nA",   fs_nA=10.0),
        BeamRange(name="30 nA",   fs_nA=30.0),
        BeamRange(name="100 nA",  fs_nA=100.0),
        BeamRange(name="300 nA",  fs_nA=300.0),
        BeamRange(name="1 µA",    fs_nA=1000.0),
        BeamRange(name="100 µA",  fs_nA=100_000.0),
    ])

    # Selection
    select_mode: str = "manual"        # "manual" | "digital"
    manual_index: int = 0
    # Digital selection source:
    #   "beam_range_idx" : use pkt["beam_range_idx"]
    #   "bitmask_low4"   : low 4 bits of pkt["bitmask"]
    #   "bitmask_high4"  : bits 14..17 of pkt["bitmask"] (adjust to match your wiring)
    digital_source: str = "beam_range_idx"

    # Filtering / display
    smooth_tau_s: float = 0.25         # low-pass (seconds)
    deadband_display: float = 0.02     # ignore tiny changes (in chosen display unit)

    # Gauge FS: use per-range FS, or a fixed override (µA) if False
    gauge_uses_range_fs: bool = True
    gauge_fullscale_override_uA: float = 100.0


def _load_cfg() -> BeamCalConfig:
    """
    Load config from disk. If missing/invalid, create defaults and write them
    WITHOUT referencing the beam_cal singleton (avoid circular init).
    """
    try:
        if os.path.exists(CFG_PATH):
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            rngs = [BeamRange(**r) for r in d.get("ranges", [])]
            return BeamCalConfig(
                ranges=rngs or BeamCalConfig().ranges,
                select_mode=d.get("select_mode", "manual"),
                manual_index=int(d.get("manual_index", 0)),
                digital_source=d.get("digital_source", "beam_range_idx"),
                smooth_tau_s=float(d.get("smooth_tau_s", 0.25)),
                deadband_display=float(d.get("deadband_display", 0.02)),
                gauge_uses_range_fs=bool(d.get("gauge_uses_range_fs", True)),
                gauge_fullscale_override_uA=float(d.get("gauge_fullscale_override_uA", 100.0)),
            )
    except Exception:
        pass

    # Defaults
    c = BeamCalConfig()
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(asdict(c), f, indent=2)
    except Exception:
        pass
    return c


def save_cfg(c: Optional[BeamCalConfig] = None):
    """
    Write config to disk. Works during first import (when the singleton isn't created yet).
    If 'c' is provided, write it; else try beam_cal.cfg if available.
    """
    cfg_to_write: Optional[BeamCalConfig] = c
    if cfg_to_write is None:
        try:
            cfg_to_write = beam_cal.cfg  # may NameError during first import
        except NameError:
            return
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(asdict(cfg_to_write), f, indent=2)
    except Exception:
        pass


class BeamCalManager:
    def select_index_for_pkt(self, pkt: dict) -> int: ...
    def volts_to_nA(self, v: float, idx: int) -> float: ...

    # ---- selection ----
    def select_index_for_pkt(self, pkt: dict) -> int:
        c = self.cfg
        if c.select_mode == "manual":
            return max(0, min(len(c.ranges) - 1, int(c.manual_index)))
        try:
            if c.digital_source == "beam_range_idx" and "beam_range_idx" in pkt:
                return max(0, min(len(c.ranges) - 1, int(pkt["beam_range_idx"])))
            bm = int(pkt.get("bitmask", 0))
            if c.digital_source == "bitmask_low4":
                return (bm & 0xF) % len(c.ranges)
            if c.digital_source == "bitmask_high4":
                idx = (bm >> 14) & 0xF  # adjust nibble to your wiring
                return idx % len(c.ranges)
        except Exception:
            pass
        return max(0, min(len(c.ranges) - 1, int(c.manual_index)))

    # ---- conversion ----
    @staticmethod
    def _interp_curve(curve: List[Tuple[float, float]], v: float) -> float:
        if not curve:
            return float('nan')
        curve = sorted(curve, key=lambda x: x[0])
        xs, ys = zip(*curve)
        if v <= xs[0]:
            return ys[0]
        if v >= xs[-1]:
            return ys[-1]
        lo, hi = 0, len(xs) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if v < xs[mid]:
                hi = mid
            else:
                lo = mid
        x0, x1 = xs[lo], xs[hi]
        y0, y1 = ys[lo], ys[hi]
        t = (v - x0) / (x1 - x0) if x1 != x0 else 0.0
        return y0 + t * (y1 - y0)

    def volts_to_nA(self, v: float, idx: int) -> float:
        r = self.cfg.ranges[max(0, min(idx, len(self.cfg.ranges) - 1))]
        if r.mode == "curve" and r.curve:
            return float(self._interp_curve(r.curve, float(v)))
        return float(r.gain_na_per_v * float(v) + r.offset_na)

    # ---- display helpers (no mA path) ----
    @staticmethod
    def unit_for_fs(fs_na: float):
        """
        Decide display unit & scale factor (nA -> display unit value) from full-scale in nA.
        No mA range: only pA, nA, µA.
        """
        if fs_na >= 1000.0:     # µA
            return "µA", 1/1000.0
        if fs_na < 1.0:         # pA
            return "pA", 1000.0
        return "nA", 1.0


# Singleton
beam_cal = BeamCalManager()
beam_cal.cfg = _load_cfg() 