# -*- coding: utf-8 -*-
"""Pure recovery-profile state machine for automatic GA PID tuning.

The automatic GA can intentionally test PID gains that temporarily reduce the
beam current.  This module keeps the recovery logic separate from Qt, the PID
controller, and the LabVIEW transport.  It stores a known-good trim-coil target
profile and reports one bounded recovery command at a time until both the trim
coil readbacks and the beam current have returned to the reference condition.

No hardware command is issued here.  The GUI integration consumes
:meth:`GARecoveryManager.next_command` and routes that command through the same
explicitly armed PID output callback used by normal closed-loop control.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class GARecoveryReference:
    """Known-good actuator targets and corresponding beam-current reference."""

    targets_a: tuple[tuple[int, float], ...]
    actuals_a: tuple[tuple[int, float], ...]
    beam_nA: float
    captured_at_s: float

    def validated(self) -> "GARecoveryReference":
        if not math.isfinite(float(self.beam_nA)):
            raise ValueError("Recovery-reference beam current must be finite")
        if not math.isfinite(float(self.captured_at_s)):
            raise ValueError("Recovery-reference timestamp must be finite")
        targets: dict[int, float] = {}
        actuals: dict[int, float] = {}
        for index, target in self.targets_a:
            idx = int(index)
            value = float(target)
            if idx < 0:
                raise ValueError("Recovery-reference channel indices must be nonnegative")
            if not math.isfinite(value):
                raise ValueError("Recovery-reference targets must be finite")
            targets[idx] = value
        for index, actual in self.actuals_a:
            idx = int(index)
            value = float(actual)
            if idx < 0:
                raise ValueError("Recovery-reference channel indices must be nonnegative")
            if not math.isfinite(value):
                raise ValueError("Recovery-reference readbacks must be finite")
            actuals[idx] = value
        if not targets:
            raise ValueError("Recovery reference must contain at least one trim coil")
        if set(targets) != set(actuals):
            raise ValueError("Recovery target and readback references must use the same channels")
        return GARecoveryReference(
            targets_a=tuple(sorted(targets.items())),
            actuals_a=tuple(sorted(actuals.items())),
            beam_nA=float(self.beam_nA),
            captured_at_s=float(self.captured_at_s),
        )

    @property
    def target_map(self) -> dict[int, float]:
        return dict(self.targets_a)

    @property
    def actual_map(self) -> dict[int, float]:
        return dict(self.actuals_a)


@dataclass(frozen=True)
class GARecoveryConfig:
    """Limits and acceptance criteria used during a baseline recovery."""

    command_step_a: float = 0.02
    target_tolerance_a: float = 0.005
    actual_tolerance_a: float = 0.05
    minimum_beam_nA: float = 0.05
    minimum_beam_fraction: float = 0.50
    beam_reference_tolerance_nA: float = 0.20
    stable_hold_s: float = 2.0
    timeout_s: float = 24.0

    def validated(self) -> "GARecoveryConfig":
        step = max(1.0e-6, float(self.command_step_a))
        target_tolerance = max(0.0, float(self.target_tolerance_a))
        actual_tolerance = max(0.0, float(self.actual_tolerance_a))
        minimum_beam = max(0.0, float(self.minimum_beam_nA))
        fraction = min(1.0, max(0.0, float(self.minimum_beam_fraction)))
        beam_tolerance = max(0.0, float(self.beam_reference_tolerance_nA))
        stable_hold = max(0.0, float(self.stable_hold_s))
        timeout = max(0.1, float(self.timeout_s))
        values = (
            step,
            target_tolerance,
            actual_tolerance,
            minimum_beam,
            fraction,
            beam_tolerance,
            stable_hold,
            timeout,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("GA recovery settings must be finite")
        return GARecoveryConfig(
            command_step_a=step,
            target_tolerance_a=target_tolerance,
            actual_tolerance_a=actual_tolerance,
            minimum_beam_nA=minimum_beam,
            minimum_beam_fraction=fraction,
            beam_reference_tolerance_nA=beam_tolerance,
            stable_hold_s=stable_hold,
            timeout_s=timeout,
        )


@dataclass(frozen=True)
class GARecoveryCommand:
    """One bounded actuator command requested by the recovery state machine."""

    channel_index: int
    delta_a: float
    reference_target_a: float
    current_target_a: float


@dataclass(frozen=True)
class GARecoveryUpdate:
    """Live recovery status returned after each observation."""

    active: bool
    complete: bool
    timed_out: bool
    elapsed_s: float
    stable_s: float
    targets_ok: bool
    actuals_ok: bool
    beam_present: bool
    beam_near_reference: bool
    beam_nA: float
    beam_reference_nA: float
    minimum_recovery_beam_nA: float
    maximum_target_error_a: float
    maximum_actual_error_a: float
    status: str


class GARecoveryManager:
    """Coordinate recovery to a captured multi-trim-coil reference profile."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.reference: GARecoveryReference | None = None
        self.config = GARecoveryConfig()
        self.active = False
        self._started_at_s = 0.0
        self._stable_since_s: float | None = None
        self._last_update: GARecoveryUpdate | None = None

    @staticmethod
    def _value(container: Sequence[float] | Mapping[int, float], index: int) -> float:
        try:
            if isinstance(container, Mapping):
                value = container[index]
            else:
                value = container[index]
            return float(value)
        except (IndexError, KeyError, TypeError, ValueError):
            return float("nan")

    @staticmethod
    def capture(
        *,
        targets_a: Sequence[float] | Mapping[int, float],
        actual_values_a: Sequence[float] | Mapping[int, float],
        beam_nA: float,
        channel_indices: Sequence[int],
        timestamp_s: float,
    ) -> GARecoveryReference:
        target_references: list[tuple[int, float]] = []
        actual_references: list[tuple[int, float]] = []
        for raw_index in channel_indices:
            index = int(raw_index)
            target = GARecoveryManager._value(targets_a, index)
            actual = GARecoveryManager._value(actual_values_a, index)
            if not math.isfinite(target):
                raise ValueError(f"No finite target is available for recovery channel {index}")
            if not math.isfinite(actual):
                raise ValueError(f"No finite readback is available for recovery channel {index}")
            target_references.append((index, target))
            actual_references.append((index, actual))
        return GARecoveryReference(
            targets_a=tuple(target_references),
            actuals_a=tuple(actual_references),
            beam_nA=float(beam_nA),
            captured_at_s=float(timestamp_s),
        ).validated()

    def start(
        self,
        *,
        reference: GARecoveryReference,
        config: GARecoveryConfig,
        timestamp_s: float,
    ) -> None:
        timestamp = float(timestamp_s)
        if not math.isfinite(timestamp):
            raise ValueError("Recovery start timestamp must be finite")
        self.reference = reference.validated()
        self.config = config.validated()
        self.active = True
        self._started_at_s = timestamp
        self._stable_since_s = None
        self._last_update = None

    @property
    def last_update(self) -> GARecoveryUpdate | None:
        return self._last_update

    @property
    def minimum_recovery_beam_nA(self) -> float:
        if self.reference is None:
            return self.config.minimum_beam_nA
        return max(
            self.config.minimum_beam_nA,
            abs(self.reference.beam_nA) * self.config.minimum_beam_fraction,
        )

    def next_command(
        self,
        current_targets_a: Sequence[float] | Mapping[int, float],
    ) -> GARecoveryCommand | None:
        """Return at most one bounded command, prioritizing the largest error.

        Sending one trim-coil command per recovery tick avoids flooding the
        existing control queue when a multi-coil reference profile is used.
        """
        if not self.active or self.reference is None:
            return None

        largest: tuple[float, int, float, float] | None = None
        for index, reference_target in self.reference.targets_a:
            current = self._value(current_targets_a, index)
            if not math.isfinite(current):
                continue
            error = reference_target - current
            magnitude = abs(error)
            if magnitude <= self.config.target_tolerance_a:
                continue
            candidate = (magnitude, index, reference_target, current)
            if largest is None or candidate[0] > largest[0]:
                largest = candidate

        if largest is None:
            return None
        _, index, reference_target, current = largest
        remaining = reference_target - current
        delta = max(
            -self.config.command_step_a,
            min(self.config.command_step_a, remaining),
        )
        return GARecoveryCommand(
            channel_index=index,
            delta_a=float(delta),
            reference_target_a=float(reference_target),
            current_target_a=float(current),
        )

    def observe(
        self,
        *,
        timestamp_s: float,
        current_targets_a: Sequence[float] | Mapping[int, float],
        actual_values_a: Sequence[float] | Mapping[int, float],
        beam_nA: float,
    ) -> GARecoveryUpdate:
        timestamp = float(timestamp_s)
        beam = float(beam_nA)
        if self.reference is None or not self.active:
            update = GARecoveryUpdate(
                active=False,
                complete=False,
                timed_out=False,
                elapsed_s=0.0,
                stable_s=0.0,
                targets_ok=False,
                actuals_ok=False,
                beam_present=False,
                beam_near_reference=False,
                beam_nA=beam,
                beam_reference_nA=(
                    float("nan") if self.reference is None else self.reference.beam_nA
                ),
                minimum_recovery_beam_nA=self.minimum_recovery_beam_nA,
                maximum_target_error_a=float("inf"),
                maximum_actual_error_a=float("inf"),
                status="RECOVERY IDLE",
            )
            self._last_update = update
            return update

        if not math.isfinite(timestamp):
            timestamp = self._started_at_s
        elapsed = max(0.0, timestamp - self._started_at_s)

        target_errors: list[float] = []
        actual_errors: list[float] = []
        actuals_finite = True
        actual_reference_map = self.reference.actual_map
        for index, reference_target in self.reference.targets_a:
            reference_actual = actual_reference_map[index]
            target = self._value(current_targets_a, index)
            actual = self._value(actual_values_a, index)
            if math.isfinite(target):
                target_errors.append(abs(target - reference_target))
            else:
                target_errors.append(float("inf"))
            if math.isfinite(actual):
                actual_errors.append(abs(actual - reference_actual))
            else:
                actual_errors.append(float("inf"))
                actuals_finite = False

        max_target_error = max(target_errors, default=float("inf"))
        max_actual_error = max(actual_errors, default=float("inf"))
        targets_ok = max_target_error <= self.config.target_tolerance_a
        actuals_ok = (
            actuals_finite
            and max_actual_error <= self.config.actual_tolerance_a
        )

        beam_present = (
            math.isfinite(beam)
            and abs(beam) >= self.minimum_recovery_beam_nA
        )
        beam_near_reference = (
            beam_present
            and abs(beam - self.reference.beam_nA)
            <= self.config.beam_reference_tolerance_nA
        )

        stable_condition = targets_ok and actuals_ok and beam_near_reference
        if stable_condition:
            if self._stable_since_s is None:
                self._stable_since_s = timestamp
            stable_s = max(0.0, timestamp - self._stable_since_s)
        else:
            self._stable_since_s = None
            stable_s = 0.0

        complete = stable_condition and stable_s >= self.config.stable_hold_s
        timed_out = elapsed >= self.config.timeout_s and not complete
        if complete or timed_out:
            self.active = False

        if complete:
            status = (
                f"REFERENCE RECOVERED — STABLE {stable_s:.1f}/"
                f"{self.config.stable_hold_s:.1f} s"
            )
        elif timed_out:
            status = (
                f"RECOVERY TIMEOUT — TARGET Δ {max_target_error:.3f} A | "
                f"ACTUAL Δ {max_actual_error:.3f} A | BEAM {beam:.3f} nA"
            )
        elif not targets_ok:
            status = f"RESTORING TARGETS — MAX Δ {max_target_error:.3f} A"
        elif not actuals_ok:
            actual_text = (
                f"{max_actual_error:.3f} A"
                if math.isfinite(max_actual_error)
                else "NO READBACK"
            )
            status = f"WAITING FOR TC READBACK — MAX Δ {actual_text}"
        elif not beam_present:
            beam_text = f"{beam:.3f}" if math.isfinite(beam) else "NO DATA"
            status = (
                f"WAITING FOR BEAM RECOVERY — {beam_text} nA; NEED ≥ "
                f"{self.minimum_recovery_beam_nA:.3f} nA"
            )
        elif not beam_near_reference:
            status = (
                f"BEAM FOUND — RETURNING TO REFERENCE: {beam:.3f} nA vs "
                f"{self.reference.beam_nA:.3f} ± "
                f"{self.config.beam_reference_tolerance_nA:.3f} nA"
            )
        else:
            status = (
                f"REFERENCE STABLE {stable_s:.1f}/"
                f"{self.config.stable_hold_s:.1f} s"
            )

        update = GARecoveryUpdate(
            active=self.active,
            complete=complete,
            timed_out=timed_out,
            elapsed_s=elapsed,
            stable_s=stable_s,
            targets_ok=targets_ok,
            actuals_ok=actuals_ok,
            beam_present=beam_present,
            beam_near_reference=beam_near_reference,
            beam_nA=beam,
            beam_reference_nA=self.reference.beam_nA,
            minimum_recovery_beam_nA=self.minimum_recovery_beam_nA,
            maximum_target_error_a=max_target_error,
            maximum_actual_error_a=max_actual_error,
            status=status,
        )
        self._last_update = update
        return update


__all__ = [
    "GARecoveryCommand",
    "GARecoveryConfig",
    "GARecoveryManager",
    "GARecoveryReference",
    "GARecoveryUpdate",
]
