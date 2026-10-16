"""Read-only, viewer-local raw -> engineering-unit conversion.

Calibration data come from the existing calibration.json and beam_cal.json.
Linear, clamped-curve, and TP-vacuum arithmetic match the supplied project's
scaling.py / beam_cal.py. These modules are deliberately not imported: their
module-level singletons can create or rewrite configuration files on import.
No calibration defaults are written and no SQLite data are modified here.

Historical beam conversion uses each row's recorded range, never today's
manual range. Already-engineering columns are not calibrated a second time.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd

# Canonical channel names and unit labels from the supplied project.
TRIM_KEYS = {f"ch{i}" for i in range(1, 13)} | {"main_magnet", "centering_beam"}
EXTRACTION_KEYS = {"coax_a", "inside_iron_a", "outside_iron_a", "esd_v", "esd_a", "fringe_a"}
ANGLE_KEYS = {f"{device}_{end}_angle" for device in ("esd", "coax", "iron") for end in ("in", "out")}
SOURCE_KEYS = {"filament_a", "filament_v", "arc_v", "arc_a", "gas_flow", "gas_turns"}
VAC_KEYS = {f"vac{i}" for i in range(1, 6)}
TRANSPORT_KEYS = {"q1a_a", "q1b_a", "q2a_a", "q2b_a", "steer40_a", "steer50_a",
                  "coll_s1_l_a", "coll_s1_r_a", "coll_s2_l_a", "coll_s2_r_a"}
SIGNAL_KEYS = TRIM_KEYS | EXTRACTION_KEYS | ANGLE_KEYS | SOURCE_KEYS | VAC_KEYS | TRANSPORT_KEYS | {"beam_current", "rf_power_kv"}
METADATA_KEYS = {"timestamp", "bitmask", "latency", "ts_mode", "beam_range_idx", "id", "rowid"}
ALIASES = {"inside_iron": "inside_iron_a", "outside_iron": "outside_iron_a",
           "beam": "beam_current", "rf": "rf_power_kv", "rf_kv": "rf_power_kv"}
TP_KEYS = {"vac1", "vac2", "vac3"}

# Exact polynomial coefficients and boundaries from the supplied scaling.py.
_TP_BOUNDARIES = (0.17, 0.549, 2.641, 4.24, 5.75, 8.751, 9.579)
_TP_POLYS = (
    (-0.004926898023775, 0.002452493892591, -0.000447301571160, 0.000036515258694, -0.000001015960008),
    (0.200472363921348e-3, -0.317525576096514e-3, 0.180746368755412e-3, -0.041219280068612e-3, 0.003477460657735e-3),
    (0.009583071495255e-5, -0.045223700085835e-5, 0.076005437677271e-5, 0.117519057072123e-5, 0.023852530234535e-5),
    (-0.000885919178607e-3, 0.015391110493744e-3, -0.090475088181461e-3, 0.226773550144986e-3, -0.203840700798600e-3),
    (-0.000004085108599, 0.000081161207961, -0.000598244799205, 0.001966633776276, -0.002432645109185),
    (0.000021401429312, -0.000580557056172, 0.005931626883700, -0.026975018732576, 0.046051227837852),
    (0.002868493050724, -0.102929391814433, 1.385639986012646, -8.292975251802320, 18.616233998715796),
    (0.000313493577268e5, -0.012184175001957e5, 0.177578952119012e5, -1.150272910015201e5, 2.794075105512838e5),
)


def identify_column(name: str) -> tuple[str, bool, str]:
    """Return canonical key, explicit engineering-unit flag, explicit unit."""
    base, engineering, unit = name, False, ""
    for suffix, suffix_unit in (("_mbar", "mbar"), ("_eu", ""), ("_raw", "")):
        if name.endswith(suffix):
            base = name[:-len(suffix)]
            engineering = suffix != "_raw"
            unit = suffix_unit
            break
    for prefix in ("beam_", "beam_current_"):
        if name.startswith(prefix) and name[len(prefix):] in ("ua", "na", "pa"):
            return "beam_current", True, {"ua": "µA", "na": "nA", "pa": "pA"}[name[len(prefix):]]
    return ALIASES.get(base, base), engineering, unit


def related_columns(name: str) -> set[str]:
    """Extra SELECT candidates, including recorded range metadata for beam."""
    key, _, _ = identify_column(name)
    bases = {key, name} | {alias for alias, canonical in ALIASES.items() if canonical == key}
    result = {name}
    for base in bases:
        result.update((base, base + "_raw", base + "_eu", base + "_mbar"))
    if key == "beam_current":
        result.update({"beam_raw", "beam_ua", "beam_na", "beam_pa", "beam_current_ua",
                       "beam_current_na", "beam_current_pa", "beam_range_idx", "bitmask"})
    return result


def _numeric(values) -> np.ndarray:
    return pd.to_numeric(values, errors="coerce").to_numpy(dtype=float, na_value=np.nan).copy()


def _curve(values: np.ndarray, points) -> np.ndarray:
    # Same duplicate-X policy (last point wins) and endpoint clamping as Scaler.
    xy = {}
    for x, y in points:
        if x is None or y is None:
            continue
        x, y = float(x), float(y)
        if not np.isfinite(x) or not np.isfinite(y):
            raise ValueError("curve has a non-finite calibration point")
        xy[x] = y
    if not xy:
        raise ValueError("calibration curve has no points")
    xs = sorted(xy)
    return np.interp(values, xs, [xy[x] for x in xs])


def _map(values: np.ndarray, config: dict) -> np.ndarray:
    if not isinstance(config, dict) or not config:
        raise ValueError("raw_to_eng mapping is missing")
    kind = config.get("type", "linear")
    if kind == "curve":
        return _curve(values, config.get("points", []))
    if kind != "linear":
        raise ValueError(f"unsupported calibration type: {kind}")
    gain, offset = float(config.get("gain", 1.0)), float(config.get("offset", 0.0))
    if not np.isfinite(gain) or not np.isfinite(offset):
        raise ValueError("gain/offset must be finite")
    return gain * values + offset


def _tp(values: np.ndarray) -> np.ndarray:
    pv = np.where(values > 20.0, values / 1000.0, values)
    regions = np.searchsorted(_TP_BOUNDARIES, pv, side="right")
    result = np.full_like(pv, np.nan)
    for region, coefficients in enumerate(_TP_POLYS):
        mask = regions == region
        result[mask] = np.polyval(coefficients, pv[mask])
    return result


def engineering_unit(key: str, entry=None) -> str:
    if isinstance(entry, dict) and entry.get("unit"):
        return str(entry["unit"])
    if key in TRIM_KEYS or (key in SIGNAL_KEYS and key.endswith("_a")):
        return "A"
    if key in ANGLE_KEYS:
        return "°"
    if key in TP_KEYS:
        return "mbar"
    if key == "rf_power_kv":
        return "kV"
    if key in SIGNAL_KEYS and key.endswith("_v"):
        return "V"
    # The source does not specify engineering units for gas_flow/gas_turns/vac4/vac5.
    return ""


@dataclass
class DisplaySeries:
    values: np.ndarray
    label: str
    unit: str = ""
    kind: str = "recorded"
    note: str = ""
    warning: str = ""


class ViewerCalibration:
    """Read snapshots of the existing configuration, without importing live controls."""
    def __init__(self, script_dir: str):
        self.script_dir = Path(script_dir)
        self.overrides: dict[str, str] = {}
        self.paths: dict[str, str] = {}
        self.errors: dict[str, str] = {}
        self.channels: dict = {}
        self.beam: dict = {}

    def _path(self, kind: str, db_path: str) -> Path | None:
        filename, env_name = (("calibration.json", "CNL_CALIB") if kind == "channels"
                              else ("beam_cal.json", "CNL_BEAM_CAL"))
        explicit = self.overrides.get(kind) or os.environ.get(env_name)
        if explicit:
            return Path(explicit).expanduser().resolve()
        # An already-loaded application's path is authoritative, with no imports
        # or mutation of its live Scaler/beam-calibration singleton.
        module = sys.modules.get("scaling" if kind == "channels" else "beam_cal")
        live_path = (getattr(getattr(module, "_shared_scaler", None), "path", None)
                     if kind == "channels" else getattr(module, "CFG_PATH", None))
        if live_path:
            return Path(live_path).expanduser().resolve()
        directories = [self.script_dir, Path.cwd()]
        if db_path:
            directories.append(Path(db_path).expanduser().resolve().parent)
        for directory in directories:
            candidate = directory / filename
            if candidate.is_file():
                return candidate.resolve()
        return None

    def refresh(self, db_path: str = "") -> None:
        self.paths, self.errors = {}, {}
        loaded = {}
        for kind in ("channels", "beam"):
            path = self._path(kind, db_path)
            self.paths[kind] = str(path) if path is not None else "not found"
            try:
                if path is None:
                    raise FileNotFoundError("calibration file was not found")
                with path.open("r", encoding="utf-8-sig") as handle:
                    data = json.load(handle)
                if not isinstance(data, dict):
                    raise ValueError("JSON root must be an object")
                loaded[kind] = data
            except (OSError, ValueError) as exc:
                self.errors[kind] = str(exc)
                loaded[kind] = {}
        self.channels, self.beam = loaded["channels"], loaded["beam"]

    def _beam_values(self, raw: np.ndarray, df: pd.DataFrame) -> tuple[np.ndarray, str]:
        ranges = self.beam.get("ranges")
        if not isinstance(ranges, list) or not ranges:
            raise ValueError("beam_cal.json is missing or contains no ranges")
        indices = np.full(len(raw), np.nan)
        if "beam_range_idx" in df:
            indices = _numeric(df["beam_range_idx"])
        # A missing range may be recovered from this project's documented one-hot
        # bits 28..37. Multiple asserted bits are ambiguous, not silently guessed.
        if "bitmask" in df:
            masks = _numeric(df["bitmask"])
            for row in np.flatnonzero(np.isnan(indices) & np.isfinite(masks)):
                value = masks[row]
                if value < 0 or value != np.floor(value):
                    continue
                bits = (int(value) >> 28) & 0x3FF
                if bits and not (bits & (bits - 1)):
                    indices[row] = bits.bit_length() - 1
        valid = (np.isfinite(indices) & (indices == np.floor(indices)) &
                 (indices >= 0) & (indices < len(ranges)) & np.isfinite(raw))
        result = np.full_like(raw, np.nan)
        failures = []
        for index in np.unique(indices[valid]).astype(int):
            mask = valid & (indices == index)
            r = ranges[index]
            try:
                if not isinstance(r, dict):
                    raise ValueError("range entry must be an object")
                mode = r.get("mode", "linear")
                if mode == "curve" and r.get("curve"):
                    result[mask] = _curve(raw[mask], r["curve"])
                elif mode in ("linear", "curve"):
                    # beam_cal.py uses the linear fallback for an empty curve.
                    result[mask] = _map(raw[mask], {"type": "linear",
                        "gain": r.get("gain_na_per_v", 1.0), "offset": r.get("offset_na", 0.0)})
                else:
                    raise ValueError(f"unsupported range mode: {mode}")
            except (ValueError, TypeError, OverflowError) as exc:
                failures.append(f"range {index}: {exc}")
        missing = int(np.count_nonzero(np.isfinite(raw) & ~valid))
        if missing:
            failures.append(f"{missing} samples have no valid recorded beam range")
        return result, "; ".join(failures)

    def display(self, requested: str, source: str, df: pd.DataFrame, scaled: bool) -> DisplaySeries:
        values = _numeric(df[source])
        key, already_eng, explicit_unit = identify_column(source)
        name = requested if requested == source else f"{requested} → {source}"
        entry = self.channels.get(key)
        unit = explicit_unit or engineering_unit(key, entry)
        if not scaled:
            return DisplaySeries(values, requested, explicit_unit if already_eng else "", note=f"Stored column: {source}")
        if already_eng:
            # Never apply gain, curve or range conversion again to stored EU data.
            return DisplaySeries(values, f"{name} [{unit or 'stored EU'}]", unit,
                                 "engineering", f"Stored engineering column: {source}; not rescaled")
        if key in METADATA_KEYS or (key not in SIGNAL_KEYS and key not in self.channels):
            return DisplaySeries(values, f"{name} [recorded]", kind="recorded",
                                 note="Metadata/unmapped column: unchanged")
        try:
            # Beam monitor has its own per-range calibration, not Scaler's
            # usually-identity beam_current entry. Keep one stable unit (nA).
            warning = ""
            if key == "beam_current":
                converted, warning = self._beam_values(values, df)
                unit, note = "nA", f"{source}: beam_cal.json + each sample's recorded range"
            else:
                if not isinstance(entry, dict):
                    raise ValueError(f"no calibration entry for {key}")
                if not entry.get("enabled", True):
                    return DisplaySeries(values, f"{name} [scaling disabled]", kind="recorded",
                                         note=f"{key}: calibration is disabled; stored values retained")
                finite = np.isfinite(values)
                converted = np.full_like(values, np.nan)
                mapping = entry.get("raw_to_eng", {}) or {}
                if key in TP_KEYS and mapping.get("type") != "curve":
                    converted[finite] = _tp(values[finite])
                else:
                    converted[finite] = _map(values[finite], mapping)
                note = f"{source}: {key}.raw_to_eng from calibration.json"
            overflow = np.isfinite(values) & ~np.isfinite(converted)
            if overflow.any() and not warning:
                warning = f"{int(overflow.sum())} samples could not be converted"
            converted[~np.isfinite(converted)] = np.nan
            return DisplaySeries(converted, f"{name} [{unit or 'scaled'}]", unit, "scaled", note, warning)
        except (ValueError, TypeError, OverflowError) as exc:
            # Do not present raw volts as engineering units. Omitted data are
            # explicitly identified in the plot and the scaling-details menu.
            return DisplaySeries(np.full_like(values, np.nan), f"{name} [unavailable]",
                                 kind="unavailable", warning=f"{key}: {exc}")
