# app_settings.py — persistent app-wide plotting/logging & RF/Vac display settings
from __future__ import annotations
from project_paths import project_file
from dataclasses import dataclass, asdict, field
import json, os

CFG_PATH = os.environ.get("CNL_APP_SETTINGS", str(project_file('app_settings.json')))

@dataclass
class AppSettings:
    # ---- plotting (applies to all monitors) ----
    plot_fps: float = 30.0          # UI refresh rate (Hz)
    plot_sps: float = 200.0         # displayed samples/second per curve (decimated)
    plot_window_s: float = 10.0     # visible time window (seconds)

    # ---- logging (read by logger if you wire it) ----
    log_cap_hz: float | None = None # None = unlimited
    log_batch: int = 500
    log_flush_ms: int = 200

    # ---- RF personalized display ----
    rf_gauge_max_kv: float = 100.0  # linear gauge full-scale
    rf_deadband_kv: float = 0.5     # ignore tiny jiggles
    rf_smooth_tau_s: float = 0.20   # low-pass (seconds)

    # ---- Vacuum personalized display ----
    vac_units: str = "mbar"         # "mbar" or "torr" (display only)
    vac_min_exp: int = -7           # left decade on gauge
    vac_max_exp: int = -2           # right decade on gauge
    vac_smooth_tau_s: float = 0.30  # low-pass (seconds)
    vac_tp_keys: list[str] = field(default_factory=lambda: ["vac1","vac2"])  # gauges shown as TP

def load() -> AppSettings:
    try:
        if os.path.exists(CFG_PATH):
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            s = AppSettings(**{k:v for k,v in d.items() if k in AppSettings().__dict__})
            return s
    except Exception:
        pass
    s = AppSettings()
    save(s)
    return s

def save(s: AppSettings):
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(asdict(s), f, indent=2)

# singleton
app_settings: AppSettings = load()
