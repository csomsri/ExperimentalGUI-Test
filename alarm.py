# -*- coding: utf-8 -*-
"""
Created on Tue Sep  9 08:10:18 2025

@author: clasa
"""

# alarm.py — simple alarm manager (GUI-process local)
from __future__ import annotations
from dataclasses import dataclass, asdict
from collections import deque
import json, os, time
from typing import Dict, Deque, Tuple, Optional, List

CFG_PATH = os.environ.get("CNL_ALARM_CFG", "alarm_config.json")

@dataclass
class AlarmConfig:
    # RF rule: trigger if abs change >= rf_dkv within rf_window_s
    rf_dkv: float = 5.0
    rf_window_s: float = 1.0
    # Vacuum rule: trigger if max/min >= factor within vac_window_s (rise by N×)
    vac_factor: float = 10.0
    vac_window_s: float = 2.0
    vac_channels: List[str] = None  # which vac keys to watch
    # overlays (use empty for none)
    rf_bg: str = "rf_alarm_bg.png"
    vac_bg: str = "vac_alarm_bg.png"

    def __post_init__(self):
        if self.vac_channels is None:
            self.vac_channels = ["vac1", "vac2"]  # TP1/TP2 by default

class AlarmManager:
    def __init__(self):
        self.active: bool = False
        self.trigger_rf: bool = False
        self.trigger_vac: bool = False
        self.cfg: AlarmConfig = self._load()
        # rolling buffers: key -> deque[(t,val)]
        self.buf: Dict[str, Deque[Tuple[float, float]]] = {}

    # ----- config -----
    def _load(self) -> AlarmConfig:
        try:
            if os.path.exists(CFG_PATH):
                with open(CFG_PATH, "r", encoding="utf-8") as f:
                    d = json.load(f)
                return AlarmConfig(**d)
        except Exception:
            pass
        return AlarmConfig()

    def save_config(self):
        try:
            with open(CFG_PATH, "w", encoding="utf-8") as f:
                json.dump(asdict(self.cfg), f, indent=2)
        except Exception:
            pass

    def set_active(self, on: bool):
        self.active = bool(on)
        if not on:
            self.trigger_rf = self.trigger_vac = False

    def apply_config(self, cfg: AlarmConfig):
        self.cfg = cfg
        self.save_config()

    # ----- data feed -----
    def update(self, key: str, t: float, value: float):
        """
        Feed a (key, t, value). Supply:
          key 'rf_power_kv' for RF,
          keys in cfg.vac_channels for mbar vacuum values.
        """
        if key not in self.buf:
            self.buf[key] = deque()
        dq = self.buf[key]
        dq.append((t, float(value)))
        # prune > max window we may use
        horizon = max(self.cfg.rf_window_s, self.cfg.vac_window_s) + 0.5
        tmin = t - horizon
        while dq and dq[0][0] < tmin:
            dq.popleft()

        if not self.active:
            return

        # RF rule
        if key == "rf_power_kv":
            self._eval_rf(t)

        # Vacuum rule
        if key in self.cfg.vac_channels:
            self._eval_vac(t)

    # ----- rules -----
    def _eval_rf(self, tnow: float):
        dq = self.buf.get("rf_power_kv")
        if not dq:
            return
        # keep last rf_window_s
        w = self.cfg.rf_window_s
        tmin = tnow - w
        vals = [v for (t, v) in dq if t >= tmin]
        if len(vals) >= 2:
            if (max(vals) - min(vals)) >= self.cfg.rf_dkv:
                self.trigger_rf = True

    def _eval_vac(self, tnow: float):
        w = self.cfg.vac_window_s
        changed = False
        for key in self.cfg.vac_channels:
            dq = self.buf.get(key)
            if not dq:
                continue
            tmin = tnow - w
            vals = [v for (t, v) in dq if t >= tmin and v is not None and v > 0]
            if len(vals) >= 2:
                r = (max(vals) / min(vals)) if min(vals) > 0 else 0.0
                if r >= self.cfg.vac_factor:
                    changed = True
                    break
        if changed:
            self.trigger_vac = True

    # ----- status -----
    def any_alarm(self) -> bool:
        return self.trigger_rf or self.trigger_vac

    def reset_latches(self):
        self.trigger_rf = self.trigger_vac = False

# Singleton
alarm_manager = AlarmManager()
