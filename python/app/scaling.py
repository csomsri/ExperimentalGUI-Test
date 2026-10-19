from project_paths import project_file
# scaling.py — persistent per-signal calibration (linear or curve),
# with TP gauge PV→mbar conversion and a shared Scaler instance.

import json
import os
import time
import bisect
from typing import Dict, List, Tuple

CALIB_PATH = os.environ.get("CNL_CALIB", str(project_file('calibration.json')))

# -------- Canonical keys --------
# Trim (14)
TRIM_KEYS: List[str] = [
    "ch1","ch2","ch3","ch4","ch5","ch6","ch7","ch8","ch9","ch10","ch11","ch12",
    "main_magnet","centering_beam",
]

# Extraction base (6)
EXTR_BASE_KEYS: List[str] = ["coax_a","inside_iron_a","outside_iron_a","esd_v","esd_a","fringe_a"]

# Extraction angles (6)
EXTR_ANGLE_KEYS: List[str] = ["esd_in_angle","esd_out_angle","coax_in_angle","coax_out_angle","iron_in_angle","iron_out_angle"]

# Source (6)
SOURCE_KEYS: List[str] = ["filament_a","filament_v","arc_v","arc_a","gas_flow","gas_turns"]

# Vacuum (5), Beam (1), RF (1)
VAC_KEYS: List[str]  = ["vac1","vac2","vac3","vac4","vac5"]
BEAM_KEYS: List[str] = ["beam_current"]
RF_KEYS:   List[str] = ["rf_power_kv"]

# Transport (10)
TRANSPORT_KEYS: List[str] = [
    "q1a_a","q1b_a","q2a_a","q2b_a",
    "steer40_a","steer50_a",
    "coll_s1_l_a","coll_s1_r_a","coll_s2_l_a","coll_s2_r_a"
]

# All scalable keys (order used in the editor)
ALL_KEYS: List[str] = (
    TRIM_KEYS
    + EXTR_BASE_KEYS
    + EXTR_ANGLE_KEYS
    + SOURCE_KEYS
    + VAC_KEYS
    + BEAM_KEYS
    + RF_KEYS
    + TRANSPORT_KEYS
)

# -------- Human labels --------
LABELS: Dict[str, str] = {
    # Trim
    "ch1":"TC1","ch2":"TC2","ch3":"TC3","ch4":"TC4","ch5":"TC5","ch6":"TC6",
    "ch7":"TC7","ch8":"TC8","ch9":"TC9","ch10":"TC10","ch11":"TC11","ch12":"TC12",
    "main_magnet":"Main Magnet","centering_beam":"Centering Beam",

    # Extraction base
    "coax_a":"Coax [A]","inside_iron_a":"Inside Iron [A]","outside_iron_a":"Outside Iron [A]",
    "esd_v":"ESD [V]","esd_a":"ESD [A]","fringe_a":"Fringe [A]",

    # Extraction angles
    "esd_in_angle":"ESD Entrance [°]","esd_out_angle":"ESD Exit [°]",
    "coax_in_angle":"Coax Entrance [°]","coax_out_angle":"Coax Exit [°]",
    "iron_in_angle":"Iron Entrance [°]","iron_out_angle":"Iron Exit [°]",

    # Source
    "filament_a":"Filament [A]","filament_v":"Filament [V]",
    "arc_v":"Arc [V]","arc_a":"Arc [A]","gas_flow":"Gas Flow","gas_turns":"Gas Turns",

    # Vacuum / Beam / RF
    "vac1":"Vac 1","vac2":"Vac 2","vac3":"Vac 3","vac4":"Vac 4","vac5":"Vac 5",
    "beam_current":"Beam Current","rf_power_kv":"RF Power [kV]",

    # Transport
    "q1a_a":"1_A Quadrupoles [A]","q1b_a":"1_B Quadrupoles [A]",
    "q2a_a":"2_A Quadrupoles [A]","q2b_a":"2_B Quadrupoles [A]",
    "steer40_a":"Steering 40\" [A]","steer50_a":"Steering 50\" [A]",
    "coll_s1_l_a":"Coll S1 Left [A]","coll_s1_r_a":"Coll S1 Right [A]",
    "coll_s2_l_a":"Coll S2 Left [A]","coll_s2_r_a":"Coll S2 Right [A]",
}

# -------- Group tags (optional, used in editor) --------
GROUPS: Dict[str, str] = {}
for k in TRIM_KEYS: GROUPS[k] = "Trim"
for k in EXTR_BASE_KEYS: GROUPS[k] = "Extraction"
for k in EXTR_ANGLE_KEYS: GROUPS[k] = "Extraction Angles"
for k in SOURCE_KEYS: GROUPS[k] = "Source"
for k in VAC_KEYS + BEAM_KEYS + RF_KEYS: GROUPS[k] = "Vac/Beam/RF"
for k in TRANSPORT_KEYS: GROUPS[k] = "Transport"

# -------- Default calibration entry --------
_DEFAULT_CH = {
    "enabled": True,
    # raw_to_eng / eng_to_raw can be:
    # { "type": "linear", "gain": g, "offset": o }
    # OR { "type": "curve", "points": [[x1,y1],[x2,y2],...] }
    "raw_to_eng": {"type": "linear", "gain": 1.0, "offset": 0.0},
    "eng_to_raw": {"type": "linear", "gain": 1.0, "offset": 0.0},
}

# ======================================================================
# TP gauge piecewise fit: PV (V) or mV -> mbar
# ======================================================================

# Coefficients (highest .. lowest power) per region  (unchanged)
_P  = (-0.004926898023775,     0.002452493892591,   -0.000447301571160,    0.000036515258694,   -0.000001015960008)
_P1 = ( 0.200472363921348e-3, -0.317525576096514e-3, 0.180746368755412e-3, -0.041219280068612e-3, 0.003477460657735e-3)
_P2 = ( 0.009583071495255e-5, -0.045223700085835e-5, 0.076005437677271e-5,  0.117519057072123e-5, 0.023852530234535e-5)
_P3 = (-0.000885919178607e-3,  0.015391110493744e-3,-0.090475088181461e-3,  0.226773550144986e-3,-0.203840700798600e-3)
_P4 = (-0.000004085108599,      0.000081161207961,  -0.000598244799205,     0.001966633776276,  -0.002432645109185)
_P5 = ( 0.000021401429312,     -0.000580557056172,   0.005931626883700,    -0.026975018732576,   0.046051227837852)
_P6 = ( 0.002868493050724,     -0.102929391814433,   1.385639986012646,    -8.292975251802320,  18.616233998715796)
_P7 = ( 0.000313493577268e5,   -0.012184175001957e5, 0.177578952119012e5,  -1.150272910015201e5, 2.794075105512838e5)

def _poly5(c, x: float) -> float:
    a4,a3,a2,a1,a0 = c
    return (((a4*x + a3)*x + a2)*x + a1)*x + a0

def _tp_mbar_from_pv(pv) -> float:
    """
    Convert TP gauge output PV to mbar.
    Accepts Volts (0..10V) or millivolts (if >20, treat as mV and /1000).
    Returns float('nan') on bad input.
    """
    try:
        pv = float(pv)
    except Exception:
        return float('nan')

    x = pv/1000.0 if pv > 20 else pv  # mV -> V

    if x < 0.17:
        return _poly5(_P,  x)
    elif x < 0.549:
        return _poly5(_P1, x)
    elif x < 2.641:
        return _poly5(_P2, x)
    elif x < 4.24:
        return _poly5(_P3, x)
    elif x < 5.75:
        return _poly5(_P4, x)
    elif x < 8.751:
        return _poly5(_P5, x)
    elif x < 9.579:
        return _poly5(_P6, x)
    else:
        return _poly5(_P7, x)

# Vacuum channels to treat as TP gauges (PV→mbar)
TP_VAC_KEYS: List[str] = ["vac1", "vac2", "vac3"]

# Optional: rename those to show mbar explicitly in UIs
for i, k in enumerate(TP_VAC_KEYS, start=1):
    LABELS[k] = f"TP{i} [mbar]"

# ======================================================================
# Curve helpers
# ======================================================================

def _sorted_xy(points: List[List[float]]) -> Tuple[List[float], List[float]]:
    """Deduplicate by X and return sorted xs,ys for interpolation."""
    pts = [(float(x), float(y)) for x,y in points if x is not None and y is not None]
    if not pts:
        return [], []
    byx = {}
    for x,y in pts:
        byx[x] = y
    xs = sorted(byx.keys())
    ys = [byx[x] for x in xs]
    return xs, ys

def _interp(x: float, xs: List[float], ys: List[float]) -> float:
    """Simple piecewise-linear interpolation (clamped at endpoints)."""
    if not xs:
        return float('nan')
    x = float(x)
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_left(xs, x)
    x0, x1 = xs[i-1], xs[i]
    y0, y1 = ys[i-1], ys[i]
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)

# ======================================================================
# Scaler class
# ======================================================================

class Scaler:
    def __init__(self, path: str = CALIB_PATH):
        self.path = path
        self._mtime = 0.0
        self.data: Dict[str, Dict] = {}
        self.load()

    def _ensure_defaults(self) -> bool:
        changed = False
        for k in ALL_KEYS:
            if k not in self.data:
                self.data[k] = dict(_DEFAULT_CH)
                changed = True
        return changed

    def load(self):
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
        else:
            self.data = {}
        if self._ensure_defaults():
            self.save()
        self._mtime = os.path.getmtime(self.path) if os.path.exists(self.path) else time.time()

    def reload_if_changed(self):
        try:
            m = os.path.getmtime(self.path)
            if m > self._mtime:
                self.load()
        except FileNotFoundError:
            pass

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        if os.path.exists(self.path):
            self._mtime = os.path.getmtime(self.path)

    @staticmethod
    def _apply_linear(gain: float, offset: float, x: float) -> float:
        return gain * float(x) + offset

    def _apply_map(self, m: Dict, x: float, *, allow_curve: bool) -> float:
        m = m or {}
        t = m.get("type", "linear")
        if allow_curve and t == "curve":
            xs, ys = _sorted_xy(m.get("points", []))
            return _interp(x, xs, ys)
        # default linear
        return self._apply_linear(m.get("gain", 1.0), m.get("offset", 0.0), x)

    def raw_to_eng(self, key: str, x: float) -> float:
        """
        Convert raw reading to engineering units for a given key.

        Priority:
          1) If raw_to_eng.type == "curve": use the curve (LUT) for that key.
          2) Else, if key is in TP_VAC_KEYS: use the piecewise PV→mbar fit.
          3) Else: apply configured linear raw→eng.
        """
        self.reload_if_changed()

        entry = self.data.get(key, _DEFAULT_CH)
        if not entry.get("enabled", True):
            return float(x)

        r2e = entry.get("raw_to_eng", {}) or {}

        # 1) explicit curve for this key
        if r2e.get("type") == "curve":
            return self._apply_map(r2e, x, allow_curve=True)

        # 2) TP conversion (if no custom curve)
        if key in TP_VAC_KEYS:
            return _tp_mbar_from_pv(x)

        # 3) linear mapping (default)
        return self._apply_map(r2e, x, allow_curve=False)

    def eng_to_raw(self, key: str, y: float) -> float:
        """
        Convert engineering setpoint to raw output for a given key.

        Priority:
          1) If eng_to_raw.type == "curve": use that curve directly (eng→raw).
          2) Else, if raw_to_eng.type == "curve": invert that LUT on the fly.
          3) Else: apply configured linear eng→raw.
        """
        self.reload_if_changed()

        entry = self.data.get(key, _DEFAULT_CH)
        if not entry.get("enabled", True):
            return float(y)

        e2r = entry.get("eng_to_raw", {}) or {}
        r2e = entry.get("raw_to_eng", {}) or {}

        # 1) explicit eng→raw curve
        if e2r.get("type") == "curve":
            return self._apply_map(e2r, y, allow_curve=True)

        # 2) invert raw→eng curve if present
        if r2e.get("type") == "curve":
            pts = r2e.get("points", []) or []
            # swap axes: (raw,eng) → (eng,raw)
            swapped = [[float(eng), float(raw)]
                       for raw,eng in pts
                       if raw is not None and eng is not None]
            xs, ys = _sorted_xy(swapped)
            return _interp(y, xs, ys)

        # 3) linear mapping (default)
        return self._apply_linear(e2r.get("gain", 1.0), e2r.get("offset", 0.0), y)

# -------- convenience for UIs --------
def all_keys() -> List[str]:
    return list(ALL_KEYS)

def label_for(key: str) -> str:
    return LABELS.get(key, key)

def group_for(key: str) -> str:
    return GROUPS.get(key, "")

# -------- shared Scaler instance for the whole app --------
# Use this everywhere to guarantee all windows see the same calibration.
_shared_scaler = Scaler()

def get_scaler() -> Scaler:
    """Return a shared Scaler instance (single source of truth)."""
    return _shared_scaler
