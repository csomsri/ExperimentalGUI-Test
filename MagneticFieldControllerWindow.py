# -*- coding: utf-8 -*-
"""Modular Tesla-style cyclotron controller window.

Compatibility:
    The public class name and constructor remain compatible with the previous
    ``MagneticFieldControllerWindow`` used by the CNL control system.

Architecture:
    * This file owns the common header, upper navigation, channel table,
      live-data adapter, scaling, and control-queue output.
    * Manual, Sequence, and Multi-TC Sequence remain inside the Magnetic Field
      page because they share the compact channel-control workspace.
    * BEAM METER is a separate upper page, never the VACUUM / BEAM page.
      An existing Magnetic / Beam Meter wrapper keeps its upper Beam Meter
      tab; direct launchers use this controller's upper BEAM METER tab.
    * The live Beam page, input buttons and conversion are embedded in this
      file. CNL Ammeter uses the installed calibration. Other Devices uses the
      existing beam_current number directly as nA. No new helper is required.
    * The original PID/GA engines stay in their existing files; the small
      subclass below selects the same conversion for high-rate PID feedback.
    * This is a controller-only update. Separate monitoring windows and the
      launcher are not modified. Run the project with main.py as usual.
    * PID / GA Auto-Tune is a full-width upper tab because it needs more space.
      Its GUI remains in ``pid_ga_control_tab.py`` and imports the independent
      ``pid_controller.py`` and ``ga_pid_tuner.py`` engines.
"""

from __future__ import annotations

import math
from collections import deque
import os
import queue as _queue
import sys
import time
from typing import Callable, List

from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import QFont, QIcon, QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from scaling import TRIM_KEYS, get_scaler
from beam_cal import beam_cal
from beam_range import RANGE_LABELS, RangeCodec
from pid_beam_buffer import BEAM_BUFFER

from controller_context import ControlMode, ControllerContext
from controller_theme import (
    CONTROLLER_QSS,
    Card,
    ClickLabel,
    PlotToggle,
    StatusDot,
    T,
    TeslaTabBar,
    bulk_button_css,
    flat_button_css,
    load_cnl_logo_pixmap,
    secondary_button_css,
    state_button_css,
    target_label_css,
)
from manual_control_tab import ManualControlTab
from multi_tc_sequence_tab import MultiTCSequenceTab
from pid_ga_control_tab import PIDGAControlTab, PIDOutputCallback
from BO.bo_control_tab import BOControlTab
from sequence_control_tab import SequenceControlTab


CHANNEL_NAMES = [
    "TC1",
    "TC2",
    "TC3",
    "TC4",
    "TC5",
    "TC6",
    "TC7",
    "TC8",
    "TC9",
    "TC10",
    "TC11",
    "TC12",
    "MAIN MAGNET",
    "CENTERING BEAM",
]
NUM_CHANNELS = len(CHANNEL_NAMES)


# ---------------------------------------------------------------------------
# Beam controls are embedded here. No beam_input.py or beam_meter_control_tab.py
# is required by this controller. The other application modules are unchanged.
# ---------------------------------------------------------------------------
CNL_AMMETER = "ammeter"
OTHER_DEVICES = "other_devices"


def _beam_number(value: float) -> str:
    value = float(value)
    if not math.isfinite(value):
        return "—"
    if abs(value) >= 1e7:
        return f"{value:.5g}"
    return f"{value:,.1f}" if abs(value) >= 10000 else f"{value:,.3f}"


def _beam_raw_value(packet: dict) -> float:
    """Read the existing beam_current field, not a new acquisition channel."""
    for key in ("beam_current", "beam_v_raw", "beam"):
        value = packet.get(key)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            if len(value) != 1:
                raise ValueError("expected one beam-current value per packet")
            value = value[0]
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("non-finite beam current")
        return value
    raise ValueError("beam_current is missing")


def _beam_range_index(packet: dict) -> int:
    """An explicit hardware readback takes precedence over the saved selection."""
    index = packet.get("beam_range_idx", packet.get("range_idx"))
    if index is None:
        index = beam_cal.select_index_for_pkt(packet)
    numeric = float(index)
    if not math.isfinite(numeric) or numeric != int(numeric):
        raise ValueError("invalid beam_range_idx")
    index = int(numeric)
    if not 0 <= index < len(beam_cal.cfg.ranges):
        raise ValueError("beam_range_idx is outside the installed calibration")
    return index


class _ControllerBeamInput:
    """Controller-local interpretation; does not modify the shared calibration.

    The high-rate buffer still stores the original raw samples. Both the Beam
    page and this controller's PID/GA tab use this conversion. No other window's
    calibration function is monkey-patched and no settings file is overwritten.
    """
    def __init__(self, mode: str = CNL_AMMETER):
        self.mode = self.normalize(mode)
        self.changed_at = 0.0
        self.cutover_mark = None

    @staticmethod
    def normalize(mode: str) -> str:
        aliases = {
            "ammeter": CNL_AMMETER, "cnl_ammeter": CNL_AMMETER,
            "other_devices": OTHER_DEVICES, "other": OTHER_DEVICES,
            "direct": OTHER_DEVICES, "labview_direct": OTHER_DEVICES,
        }
        try:
            return aliases[str(mode).strip().lower()]
        except KeyError as exc:
            raise ValueError("Select CNL Ammeter or Other Devices") from exc

    @property
    def is_direct(self) -> bool:
        return self.mode == OTHER_DEVICES

    @property
    def label(self) -> str:
        return "Other Devices" if self.is_direct else "CNL Ammeter"

    def convert(self, raw: float, range_index=None, *, strict_curve=False) -> float:
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("non-finite beam current")
        if self.is_direct:
            # Numerical identity. Preserve zero and the sign; do not use a
            # range, absolute value, gain, offset, LUT, or volts-to-amps factor.
            return value
        numeric = float(range_index)
        if not math.isfinite(numeric) or numeric != int(numeric):
            raise ValueError("invalid beam range")
        index = int(numeric)
        if not 0 <= index < len(beam_cal.cfg.ranges):
            raise ValueError("beam range is outside the installed calibration")
        selected = beam_cal.cfg.ranges[index]
        # Keep the existing PID's out-of-curve rejection. The ordinary meter
        # readout continues to use the installed beam_cal interpolation policy.
        if strict_curve and selected.mode == "curve" and selected.curve:
            xs = [float(point[0]) for point in selected.curve]
            if value < min(xs) or value > max(xs):
                raise ValueError("averaged voltage is outside the calibrated curve")
        current = float(beam_cal.volts_to_nA(value, index))
        if not math.isfinite(current):
            raise ValueError("non-finite calibrated beam current")
        return current

    def calibrate_mean(self, raw: float, range_index: int) -> float:
        return self.convert(raw, range_index, strict_curve=True)

    def change(self, mode: str) -> bool:
        mode = self.normalize(mode)
        if mode == self.mode:
            return False
        self.changed_at = time.monotonic()
        self.cutover_mark = BEAM_BUFFER.mark(now=self.changed_at)
        self.mode = mode
        return True


class _BeamAwarePIDGAControlTab(PIDGAControlTab):
    """Use the selected input path without changing the PID/GA engine files.

    The original buffer, complete averaging windows, source/receive timestamps,
    decision marks, response wait, output arming and GA algorithms are retained.
    Only the raw-to-nA adapter and the raw comparison trace are selected here.
    """
    def __init__(self, context, *, beam_input_state, output_callback=None):
        self._controller_beam_input = beam_input_state
        super().__init__(context, output_callback=output_callback)

    def _beam_window_snapshot(self, *, for_decision: bool = False) -> dict:
        state = self._controller_beam_input
        now = time.monotonic()
        decision_mark = self._beam_cycle_mark if for_decision else None
        mark = decision_mark
        cutoff = state.cutover_mark
        if cutoff is not None and (mark is None or cutoff.receive_time > mark.receive_time):
            mark = cutoff
        snapshot = BEAM_BUFFER.snapshot(
            self.beam_average_spin.value() / 1000.0,
            self._beam_stale_limit(), state.calibrate_mean,
            after=mark, now=now,
        )
        # A buffered pre-switch sample must not appear in the raw comparison
        # trace or count as healthy feedback after the interpretation changes.
        if cutoff is not None and float(snapshot.get("timestamp", 0.0)) <= cutoff.receive_time:
            snapshot.update(valid=False, ready=False, fresh=False,
                            value_nA=float("nan"), raw_latest=float("nan"),
                            status="INPUT CHANGED — WAITING FOR FRESH BEAM DATA")
        if (for_decision and decision_mark is not None
                and now < decision_mark.receive_time and snapshot.get("fresh")):
            remaining_ms = 1000.0 * (decision_mark.receive_time - now)
            snapshot.update(ready=False, valid=False,
                            response_wait_remaining_ms=remaining_ms,
                            status=f"SETTLING WAIT — {remaining_ms:.0f} ms remaining")
        return snapshot

    def _raw_beam_nA_from_snapshot(self, snapshot: dict) -> float:
        try:
            return self._controller_beam_input.convert(
                snapshot.get("raw_latest", float("nan")),
                snapshot.get("range_index"), strict_curve=True,
            )
        except (TypeError, ValueError, OverflowError, AttributeError):
            return float("nan")

    def clear_input_history(self) -> None:
        """Only called while PID/GA is stopped; do not change any gains/targets."""
        self._last_pid_average = None
        self._last_average_generation = None
        self._last_processed_beam_timestamp = 0.0
        self._beam_cycle_mark = None
        self._reset_plot()
        self.refresh()


class _ControllerBeamPlot(QWidget):
    """Actual received data only; no generated or demonstration signal."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.samples = deque(maxlen=1800)
        self.setMinimumHeight(126)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def clear(self) -> None:
        self.samples.clear()
        self.update()

    def append(self, value: float, stamp: float) -> None:
        if math.isfinite(value) and math.isfinite(stamp):
            self.samples.append((stamp, value))
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(54, 12, max(1, self.width() - 66), max(1, self.height() - 38))
        now = time.monotonic()
        points = [(stamp, value) for stamp, value in self.samples if now - 30 <= stamp <= now]
        values = [value for _, value in points]
        if values:
            lo, hi = min(values), max(values)
            pad = max(0.05, abs(hi) * 0.06, (hi - lo) * 0.3)
            lo, hi = lo - pad, hi + pad
        else:
            lo, hi = 0.0, 1.0
        painter.fillRect(area, QColor("#0E1419"))
        font = QFont("Segoe UI")
        font.setPixelSize(10)
        painter.setFont(font)
        for i in range(4):
            y = area.top() + area.height() * i / 3.0
            painter.setPen(QPen(QColor(T.border_soft), 1))
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            value = hi - (hi - lo) * i / 3.0
            text = f"{value:.2f}" if abs(value) < 100 else f"{value:.3g}"
            painter.setPen(QColor(T.dim))
            painter.drawText(QRectF(0, y - 8, 48, 16), Qt.AlignmentFlag.AlignRight, text)
        for i, caption in enumerate(("−30 s", "−20 s", "−10 s", "now")):
            x = area.left() + area.width() * i / 3.0
            painter.setPen(QPen(QColor(T.border_soft), 1))
            painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
            painter.setPen(QColor(T.dim))
            painter.drawText(QRectF(x - 20, area.bottom() + 6, 40, 18),
                             Qt.AlignmentFlag.AlignCenter, caption)
        if not points:
            painter.setPen(QColor(T.dim))
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "Waiting for beam data")
            return
        painter.save()
        painter.setClipRect(area.adjusted(-1, -1, 1, 1))
        path = QPainterPath()
        previous_stamp = None
        last_point = None
        for stamp, value in points:
            point = QPointF(area.right() - (now - stamp) * area.width() / 30.0,
                            area.bottom() - (value - lo) * area.height() / (hi - lo))
            # Break the trace over long acquisition gaps; do not draw fake data.
            if previous_stamp is None or stamp - previous_stamp > 1.0:
                path.moveTo(point)
            else:
                path.lineTo(point)
            previous_stamp, last_point = stamp, point
        painter.setPen(QPen(QColor(T.cyan), 1.7))
        painter.drawPath(path)
        if last_point is not None:
            painter.setBrush(QColor(T.cyan))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(last_point, 2.5, 2.5)
        painter.restore()


class _ControllerBeamPage(QWidget):
    """Compact Beam page embedded directly in MagneticFieldControllerWindow."""
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self._plotted_stamp = None
        self._plotted_range = None
        self.setObjectName("cnlBeamPage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFont(QFont("Segoe UI"))
        self.setStyleSheet(f"""
            QWidget#cnlBeamPage {{ background:{T.window}; }}
            QWidget#cnlBeamPage QWidget {{ font-family:"Segoe UI"; }}
            QWidget#cnlBeamPage QFrame#cnlBeamCard,
            QWidget#cnlBeamPage QFrame#cnlBeamInput {{
                background:{T.panel}; border:1px solid {T.border}; border-radius:10px;
            }}
            QWidget#cnlBeamPage QPushButton {{
                background:#101A22; color:{T.muted}; border:1px solid {T.border};
                border-radius:7px; padding:6px 12px; font-size:14px;
            }}
            QWidget#cnlBeamPage QPushButton:hover:enabled {{
                background:#192A36; border-color:#577789;
            }}
            QWidget#cnlBeamPage QPushButton:disabled {{
                background:#0E151B; color:{T.dim}; border-color:#27323A;
            }}
            QWidget#cnlBeamPage QPushButton:checked,
            QWidget#cnlBeamPage QPushButton:checked:disabled {{
                background:#193448; color:{T.text}; border:1px solid {T.cyan};
                font-weight:600;
            }}
            QWidget#cnlBeamPage QPushButton[beamPending="true"] {{
                border:1px dashed {T.amber};
            }}
            QWidget#cnlBeamPage QPushButton#beamInputButton {{
                font-size:15px; font-weight:600;
            }}
            QWidget#cnlBeamPage QPushButton#beamRangeButton {{
                text-align:left; padding-left:18px;
            }}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 6, 4, 4)
        root.setSpacing(8)
        heading = QHBoxLayout()
        heading.addWidget(self._label("Beam meter", 22, T.text, True))
        heading.addStretch(1)
        self.live_label = self._label("WAITING FOR DATA", 10, T.dim, True)
        heading.addWidget(self.live_label)
        root.addLayout(heading)

        # This box intentionally contains exactly one heading + two buttons.
        self.input_panel = QFrame()
        self.input_panel.setObjectName("cnlBeamInput")
        row = QHBoxLayout(self.input_panel)
        row.setContentsMargins(14, 8, 12, 8)
        row.setSpacing(10)
        row.addWidget(self._label("BEAM INPUT", 11, T.muted, True), 1)
        self.ammeter_button = QPushButton("CNL Ammeter")
        self.direct_button = QPushButton("Other Devices")
        self._input_group = QButtonGroup(self)
        self._input_group.setExclusive(True)
        for button in (self.ammeter_button, self.direct_button):
            button.setObjectName("beamInputButton")
            button.setCheckable(True)
            button.setMinimumWidth(160)
            button.setFixedHeight(40)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._input_group.addButton(button)
            row.addWidget(button, 3)
        self.ammeter_button.clicked.connect(lambda: controller.set_beam_input_mode(CNL_AMMETER))
        self.direct_button.clicked.connect(lambda: controller.set_beam_input_mode(OTHER_DEVICES))
        root.addWidget(self.input_panel)

        panels = QHBoxLayout()
        panels.setSpacing(12)
        panels.addWidget(self._build_ranges(), 6)
        panels.addWidget(self._build_signal(), 4)
        root.addLayout(panels, 1)
        self.status_label = self._label("Waiting for the beam_current stream", 11, T.muted)
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)
        controller.context.beam_measurement_changed.connect(self.refresh)
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    @staticmethod
    def _label(text: str, size=12, color=None, bold=False) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"color:{color or T.text};font-size:{size}px;"
                           f"font-weight:{600 if bold else 400};background:transparent;border:none;")
        return label

    def _card(self):
        card = QFrame()
        card.setObjectName("cnlBeamCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(7)
        return card, layout

    def _build_ranges(self):
        card, layout = self._card()
        row = QHBoxLayout()
        row.addWidget(self._label("CNL Ammeter ranges", 18, T.text, True))
        row.addStretch(1)
        self.range_status = self._label("WAITING", 11, T.cyan, True)
        row.addWidget(self.range_status)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.range_mode_buttons = {}
        self._range_mode_group = QButtonGroup(self)
        for mode in ("analog", "digital", "auto"):
            button = QPushButton(mode.title())
            button.setCheckable(True)
            button.setFixedHeight(34)
            self._range_mode_group.addButton(button)
            button.clicked.connect(lambda checked=False, m=mode: self.controller.set_beam_range_mode(m))
            row.addWidget(button)
            self.range_mode_buttons[mode] = button
        self.range_mode_buttons["analog"].setToolTip("Follow the received hardware range.")
        self.range_mode_buttons["digital"].setToolTip("Request a range through the existing control queue.")
        self.range_mode_buttons["auto"].setToolTip(
            "Existing software-request mode. No automatic range-search algorithm is added.")
        layout.addLayout(row)
        grid = QGridLayout()
        grid.setSpacing(6)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.range_buttons = []
        for index, text in enumerate(RANGE_LABELS):
            button = QPushButton(f"{index + 1:02d}    {text.replace(' - ', ' – ')}")
            button.setObjectName("beamRangeButton")
            button.setProperty("beamPending", False)
            button.setAccessibleName(f"Ammeter range {index + 1}: {text}")
            button.setCheckable(True)
            button.setMinimumWidth(190)
            button.setFixedHeight(44)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, i=index: self.controller.request_set_range(i))
            self.range_buttons.append(button)
            grid.addWidget(button, index // 2, index % 2)
        layout.addLayout(grid)
        layout.addStretch(1)
        self.range_footer = self._label("Waiting for hardware range readback", 11, T.muted)
        self.range_footer.setWordWrap(True)
        self.range_footer.setMinimumHeight(30)
        layout.addWidget(self.range_footer)
        return card

    def _build_signal(self):
        card, layout = self._card()
        row = QHBoxLayout()
        row.addWidget(self._label("BEAM CURRENT", 10, T.muted, True))
        row.addStretch(1)
        self.source_label = self._label("CNL AMMETER", 10, T.cyan, True)
        row.addWidget(self.source_label)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.beam_value = self._label("—", 44, T.text, True)
        self.beam_value.setMinimumHeight(53)
        self.beam_value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.beam_value, 1)
        row.addWidget(self._label("nA", 19, T.muted))
        layout.addLayout(row)
        self.mapping_label = self._label("Waiting for beam_current", 11, T.cyan)
        self.mapping_label.setWordWrap(True)
        self.mapping_label.setMinimumHeight(24)
        layout.addWidget(self.mapping_label)
        self.plot = _ControllerBeamPlot()
        layout.addWidget(self.plot, 1)
        self.age_label = self._label("Sample age: —", 11, T.muted)
        self.calibration_label = self._label("Installed calibration · beam_cal.json", 11, T.muted)
        self.calibration_label.setWordWrap(True)
        layout.addWidget(self.age_label)
        layout.addWidget(self.calibration_label)
        return card

    def set_base_bitmask(self, bitmask_u64: int) -> None:
        """Readback adapter for the existing upper Magnetic / Beam Meter wrapper."""
        mask = int(bitmask_u64)
        if mask < 0 or mask > 0xFFFFFFFFFFFFFFFF or mask != bitmask_u64:
            raise ValueError("invalid hardware bitmask")
        self.controller._beam_base_bitmask = mask
        self.controller._beam_mask_stamp = time.monotonic()

    def follow_from_u64(self, bitmask_u64: int, explicit_idx=None) -> None:
        """Follow hardware in EVERY UI mode; never turn readback into a command."""
        index = explicit_idx if explicit_idx is not None else RangeCodec.parse(bitmask_u64)
        self.controller.request_beam_range_change(index, bitmask_u64)

    def clear_history(self):
        self.plot.clear()
        self._plotted_stamp = None
        self._plotted_range = None
        self.refresh()

    def refresh(self, snapshot=None):
        ctrl = self.controller
        state = ctrl._beam_input_state
        reason = ctrl._beam_input_change_block_reason()
        for button, selected in ((self.ammeter_button, not state.is_direct),
                                 (self.direct_button, state.is_direct)):
            button.setChecked(selected)
            button.setEnabled(not bool(reason))
            button.setToolTip(reason)
        self.source_label.setText(state.label.upper())
        for mode, button in self.range_mode_buttons.items():
            button.setChecked(mode == ctrl._beam_range_mode)
            button.setEnabled(not state.is_direct and not reason)
        range_reason = ctrl._beam_range_block_reason()
        confirmed = ctrl._beam_confirmed_range
        pending = ctrl._beam_pending_range
        for index, button in enumerate(self.range_buttons):
            # SAME readback in Analog, Digital and Auto. The enabled state only
            # controls whether clicking can request a new range, not its display.
            button.setChecked(not state.is_direct and index == confirmed)
            requested = (not state.is_direct and pending == index and pending != confirmed)
            if button.property("beamPending") != requested:
                button.setProperty("beamPending", requested)
                button.style().unpolish(button)
                button.style().polish(button)
                button.update()
            button.setEnabled(not bool(range_reason) and index < len(beam_cal.cfg.ranges))
            details = f"Range {index + 1:02d} · {RANGE_LABELS[index]}"
            if not state.is_direct and index == confirmed:
                details += " · received hardware range"
            if requested:
                details += " · requested, not yet confirmed"
            if range_reason:
                details += "\n" + range_reason
            else:
                details += "\nClick to request; the solid selection follows hardware readback."
            button.setToolTip(details)
            button.setAccessibleDescription(details)

        if state.is_direct:
            self.range_status.setText("BYPASSED")
            self.range_footer.setText("Ammeter settings retained · no range calibration")
        elif confirmed is not None:
            # A request must not replace the ACTUAL range with a generic PENDING
            # label. Keep range 03 visible when the received index is 2.
            last_only = time.monotonic() - ctrl._beam_range_readback_stamp > ctrl.HOLD_SECONDS
            self.range_status.setText(f"RANGE {confirmed + 1:02d}" + (" · LAST" if last_only else ""))
            suffix = "Last hardware readback" if last_only else "Hardware readback"
            self.range_footer.setText(f"{suffix} · {RANGE_LABELS[confirmed]}")
            if pending is not None and pending != confirmed:
                self.range_footer.setText(self.range_footer.text() +
                    f" · requested {pending + 1:02d}, awaiting confirmation")
        else:
            self.range_status.setText("WAITING FOR RANGE")
            self.range_footer.setText("Waiting for hardware range readback")
            if pending is not None:
                self.range_footer.setText(f"Requested range {pending + 1:02d} · waiting for hardware range readback")
        self.calibration_label.setText("Received number = current in nA · no calibration"
                                       if state.is_direct else "Installed calibration · beam_cal.json")
        # Recompute age on every refresh. Receiving no packets must not leave a
        # frozen number marked LIVE. The input itself is not smoothed here.
        limit = ctrl._beam_display_stale_limit()
        snapshot = ctrl.context.beam_snapshot(max_age_s=limit)
        age = float(snapshot.get("age_s", float("inf")))
        self.age_label.setText(f"Sample age: {age:.2f} s" if math.isfinite(age) else "Sample age: —")
        status = str(snapshot.get("status", "Waiting for data"))
        if snapshot.get("valid") and math.isfinite(ctrl._beam_last_raw):
            value = float(snapshot["value_nA"])
            stamp = float(snapshot["timestamp"])
            text = _beam_number(value)
            size = 44 if len(text) <= 9 else 30 if len(text) <= 14 else 23
            self.beam_value.setText(text)
            self.beam_value.setStyleSheet(f"color:{T.text};font-size:{size}px;font-weight:600;background:transparent;")
            self.live_label.setText("●  LIVE BEAM")
            unit = "received" if state.is_direct else "V"
            self.mapping_label.setText(f"{ctrl._beam_last_raw:.6g} {unit}  →  {text} nA")
            range_key = None if state.is_direct else ctrl._beam_last_value_range
            if range_key != self._plotted_range:
                self.plot.clear()
                self._plotted_range = range_key
            if stamp != self._plotted_stamp:
                self.plot.append(value, stamp)
                self._plotted_stamp = stamp
        else:
            self.beam_value.setText("—")
            self.live_label.setText("WAITING / STALE")
            self.mapping_label.setText(status)
        self.plot.update()
        self.status_label.setText(reason or ctrl._beam_action_status or f"{state.label} · {status}")


class MagneticFieldControllerWindow(QWidget):
    """Tesla-style controller shell with modular operating-mode tabs."""

    def __init__(
        self,
        control_queue,
        target_values,
        plot_config,
        current_values_ref=None,
        data_queue=None,
    ):
        super().__init__()

        if len(target_values) < NUM_CHANNELS:
            raise ValueError(
                f"target_values must contain at least {NUM_CHANNELS} entries; "
                f"received {len(target_values)}"
            )

        self.control_queue = control_queue
        self.target_values = target_values
        self.plot_config = plot_config if plot_config is not None else {}
        self.current_values_ref = current_values_ref
        self.data_queue = data_queue
        self.scaler = get_scaler()

        # Keep beam selection and UI inside this controller, not a new module.
        try:
            self._beam_input_state = _ControllerBeamInput(
                self.plot_config.get("controller_beam_input_mode", CNL_AMMETER))
        except ValueError:
            self._beam_input_state = _ControllerBeamInput()
        self._beam_range_mode = "analog"
        self._beam_confirmed_range = None
        self._beam_range_readback_stamp = 0.0
        self._beam_outer_tabs = None
        self._beam_pending_range = None
        self._beam_base_bitmask = None
        self._beam_mask_stamp = 0.0
        self._beam_last_raw = float("nan")
        self._beam_last_value_range = None
        self._beam_last_rx = 0.0
        self._beam_last_source = None
        self._beam_action_status = ""

        self.actual_values = [0.0] * NUM_CHANNELS
        self._last_sample = None
        self.last_data_time = 0.0
        self.HOLD_SECONDS = 5.0
        self.controls_locked = False

        self.setWindowTitle("Cyclotron Controller")
        self._fit_controller_window()
        self.setStyleSheet(CONTROLLER_QSS)

        here = os.path.dirname(os.path.abspath(__file__))
        self._cnl_logo_pixmap = load_cnl_logo_pixmap(here)
        if not self._cnl_logo_pixmap.isNull():
            self.setWindowIcon(QIcon(self._cnl_logo_pixmap))

        # One model is shared by every operating-mode tab.  The callback is set
        # after construction so send_update can use the fully initialized UI.
        self.context = ControllerContext(
            channel_names=CHANNEL_NAMES,
            target_values=self.target_values,
            plot_config=self.plot_config,
            send_callback=None,
            parent=self,
        )

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 14, 18, 14)
        root_layout.setSpacing(8)
        root_layout.addWidget(self._build_header())
        root_layout.addWidget(self._build_subsystem_tabs(), 1)

        self.context.set_send_callback(self.send_update)
        self._connect_context()
        self._sync_all_channel_controls()
        self.update_display()

        self._rx_timer = QTimer(self)
        self._rx_timer.timeout.connect(self._poll_actual_data)
        self._rx_timer.start(50)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()
        # The legacy wrapper attaches its children after this constructor.
        # Defer placement until that existing upper tab row is available.
        QTimer.singleShot(0, self._place_beam_meter_in_outer_tab)

    # ------------------------------------ Embedded live Beam input / ranges
    @staticmethod
    def _controller_scroll(page: QWidget) -> QScrollArea:
        """Keep a large PID/magnetic page from forcing every other tab larger."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumSize(0, 0)
        scroll.setWidget(page)
        return scroll

    def _fit_controller_window(self) -> None:
        """Keep the original controller proportions; compact only the Beam page."""
        width, height = 1680, 900
        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(width, max(640, available.width() - 32))
            height = min(height, max(480, available.height() - 60))
        self.setMinimumSize(min(1280, width), min(720, height))
        self.resize(width, height)

    def _beam_display_stale_limit(self) -> float:
        tab = getattr(self, "pid_tab", None)
        return tab._beam_stale_limit() if tab is not None else 1.0

    def _beam_input_change_block_reason(self) -> str:
        tab = getattr(self, "pid_tab", None)
        if tab is not None and (getattr(tab, "_pid_running", False)
                                or getattr(tab, "_ga_auto_active", False)):
            return "Stop PID / GA before changing the beam input or range."
        context = getattr(self, "context", None)
        if context is not None and context.active_mode in (ControlMode.PID, ControlMode.GA_TUNING):
            return "Stop PID / GA before changing the beam input or range."
        return ""

    def set_beam_input_mode(self, mode: str) -> bool:
        """Select the meaning of beam_current. This never sends an output."""
        try:
            mode = self._beam_input_state.normalize(mode)
            reason = self._beam_input_change_block_reason()
            if reason and mode != self._beam_input_state.mode:
                raise RuntimeError(reason)
            if not self._beam_input_state.change(mode):
                self.beam_tab.refresh()
                return False
        except (ValueError, RuntimeError) as exc:
            self._beam_action_status = str(exc)
            self.beam_tab.refresh()
            return False

        # Remember the selection while this project's shared plot_config lives.
        # The default in a new session is CNL Ammeter. No calibration/settings
        # JSON file is changed by this single-file controller update.
        self.plot_config["controller_beam_input_mode"] = mode
        self._beam_action_status = ""
        self._beam_last_raw = float("nan")
        self._beam_last_value_range = None
        self._beam_last_rx = 0.0
        self._beam_last_source = None
        self._beam_pending_range = None
        self._last_sample = None
        self.last_data_time = 0.0
        # This is only the controller's dedicated display queue, not the raw
        # acquisition queue. Do not keep a pre-switch, unstamped GUI packet.
        if self.data_queue is not None:
            for _ in range(1000):
                try:
                    self.data_queue.get_nowait()
                except _queue.Empty:
                    break
                except (AttributeError, OSError, ValueError):
                    break
        self.context.invalidate_beam_measurement("INPUT CHANGED — WAITING FOR FRESH BEAM DATA")
        self.pid_tab.clear_input_history()
        self.beam_tab.clear_history()
        return True

    def set_beam_range_mode(self, mode: str) -> None:
        if mode not in ("analog", "digital", "auto"):
            return
        if self._beam_input_state.is_direct or self._beam_input_change_block_reason():
            self.beam_tab.refresh()
            return
        self._beam_range_mode = mode
        self._beam_action_status = ""
        self.beam_tab.refresh()

    def _beam_range_block_reason(self) -> str:
        if self._beam_input_state.is_direct:
            return "Other Devices bypasses the CNL Ammeter range controls."
        locked = self._beam_input_change_block_reason()
        if locked:
            return locked
        if self._beam_range_mode == "analog":
            return "Analog mode follows hardware. Select Digital to request a range."
        if self.control_queue is None:
            return "No control queue is connected."
        if (self._beam_base_bitmask is None or self._beam_mask_stamp <= 0
                or time.monotonic() - self._beam_mask_stamp > self.HOLD_SECONDS):
            return "Waiting for a fresh bitmask readback before sending a range command."
        return ""

    def request_set_range(self, index: int) -> bool:
        """Request a hardware range; do not relabel old voltage with a new LUT."""
        reason = self._beam_range_block_reason()
        if reason:
            self._beam_action_status = reason
            self.beam_tab.refresh()
            return False
        try:
            numeric = float(index)
            if not math.isfinite(numeric) or numeric != int(numeric):
                raise ValueError("invalid requested range")
            index = int(numeric)
            if not 0 <= index < min(len(RANGE_LABELS), len(beam_cal.cfg.ranges)):
                raise ValueError("requested range is not in the installed calibration")
            if index == self._beam_confirmed_range and self._beam_pending_range in (None, index):
                # Clicking the current range is a no-op, not a pending change.
                self._beam_pending_range = None
                self._beam_action_status = ""
                self.beam_tab.refresh()
                return True
            mask = RangeCodec.compose(self._beam_base_bitmask, index, mode="4bit", mirror=True)
            # Same command dictionary and codec as the existing BeamRangePad.
            self.control_queue.put({"cmd": "set_beam_range", "beam_range_idx": index,
                                    "bitmask_u64": int(mask)}, block=False)
        except (TypeError, ValueError, OverflowError, _queue.Full, OSError, AttributeError) as exc:
            self._beam_action_status = f"Range command was not sent: {exc}"
            self.beam_tab.refresh()
            return False
        self._beam_pending_range = index
        self._beam_action_status = ""
        self.beam_tab.refresh()
        return True

    def request_beam_range_change(self, idx: int, bitmask_u64: int) -> None:
        """Compatibility readback hook used by the existing main GUI launcher."""
        try:
            mask = int(bitmask_u64)
            if mask < 0 or mask > 0xFFFFFFFFFFFFFFFF or mask != bitmask_u64:
                raise ValueError("invalid hardware bitmask")
            self._beam_base_bitmask = mask
            self._beam_mask_stamp = time.monotonic()
            self._confirm_beam_range(idx)
        except (TypeError, ValueError, OverflowError):
            return
        if hasattr(self, "beam_tab"):
            self.beam_tab.refresh()

    def _confirm_beam_range(self, index) -> None:
        numeric = float(index)
        if not math.isfinite(numeric) or numeric != int(numeric):
            raise ValueError("invalid hardware range readback")
        index = int(numeric)
        if not 0 <= index < min(len(RANGE_LABELS), len(beam_cal.cfg.ranges)):
            raise ValueError("hardware range readback is outside installed ranges")
        self._beam_confirmed_range = index
        self._beam_range_readback_stamp = time.monotonic()
        if self._beam_pending_range == index:
            self._beam_pending_range = None

    def _follow_beam_range_from_packet(self, packet: dict) -> None:
        """Read range/bitmask metadata without altering any current conversion."""
        for key in ("bitmask_u64", "bitmask_double", "bitmask"):
            value = packet.get(key)
            if value is None:
                continue
            try:
                mask = int(value)
                if mask < 0 or mask > 0xFFFFFFFFFFFFFFFF or mask != value:
                    raise ValueError("invalid hardware bitmask")
                stamp = float(packet.get("_pid_rx_mono", time.monotonic()))
                now = time.monotonic()
                if not math.isfinite(stamp) or stamp <= 0 or stamp > now + 0.05:
                    raise ValueError("invalid receive time")
                self._beam_base_bitmask = mask
                self._beam_mask_stamp = stamp
            except (TypeError, ValueError, OverflowError):
                self._beam_base_bitmask = None
                self._beam_mask_stamp = 0.0
            break
        index = packet.get("beam_range_idx", packet.get("range_idx"))
        if index is not None:
            try:
                self._confirm_beam_range(index)
            except (TypeError, ValueError, OverflowError):
                self._beam_confirmed_range = None

    # ---------------------------------------------------------------- Header
    def _build_header(self) -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(16)

        brand = QLabel()
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setFixedSize(66, 56)
        brand.setStyleSheet("background: transparent; border: none;")
        if not self._cnl_logo_pixmap.isNull():
            brand.setPixmap(
                self._cnl_logo_pixmap.scaled(
                    62,
                    54,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            brand.setText("CNL")
            brand.setStyleSheet(
                f"background: transparent; border: 1px solid {T.muted}; "
                "border-radius: 8px; color: white; font-size: 15px;"
            )
        layout.addWidget(brand)

        layout.addWidget(self._vertical_divider())

        title = QLabel("CYCLOTRON CONTROLLER")
        title.setStyleSheet("font-size: 19px; font-weight: 500; letter-spacing: 2px;")
        layout.addWidget(title)

        self.controller_subtitle = QLabel("MAGNETIC FIELD CONTROLLER")
        self.controller_subtitle.setStyleSheet(
            f"color: {T.muted}; font-size: 12px; letter-spacing: 1px;"
        )
        layout.addWidget(self.controller_subtitle)
        layout.addStretch()

        live = QWidget()
        live_layout = QHBoxLayout(live)
        live_layout.setContentsMargins(0, 0, 0, 0)
        live_layout.setSpacing(8)
        self.live_dot = StatusDot(T.green)
        live_layout.addWidget(self.live_dot)
        self.live_label = QLabel("LIVE")
        self.live_label.setStyleSheet(f"color: {T.green}; font-size: 14px;")
        live_layout.addWidget(self.live_label)
        layout.addWidget(live)

        layout.addWidget(self._vertical_divider())
        self.mode_label = QLabel("MODE: MANUAL")
        self.mode_label.setStyleSheet(f"color: {T.cyan}; font-size: 13px;")
        layout.addWidget(self.mode_label)

        layout.addWidget(self._vertical_divider())
        self.clock_label = QLabel("--:--")
        self.clock_label.setStyleSheet("font-size: 14px;")
        layout.addWidget(self.clock_label)

        layout.addWidget(self._vertical_divider())
        menu = QPushButton("☰")
        menu.setFixedSize(36, 36)
        menu.setCursor(Qt.CursorShape.PointingHandCursor)
        menu.setStyleSheet(flat_button_css())
        layout.addWidget(menu)
        return header

    @staticmethod
    def _vertical_divider() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setStyleSheet(f"color: {T.border};")
        line.setFixedHeight(32)
        return line

    # ---------------------------------------------------------- Subsystems
    def _build_subsystem_tabs(self) -> QTabWidget:
        """Original Tesla navigation, with a dedicated upper BEAM METER page.

        The compact stylesheet belongs only to the Beam page. The header,
        Magnetic workspace, PID workspace and TeslaTabBar keep their original
        fonts, spacing and widgets. If the legacy two-tab wrapper is present,
        _place_beam_meter_in_outer_tab moves this one page to its Beam Meter tab.
        """
        tabs = QTabWidget()
        tabs.setTabBar(TeslaTabBar())
        tabs.setDocumentMode(True)
        tabs.setStyleSheet("QTabWidget::pane { border: none; background: transparent; }")
        self._subsystem_headers = {}

        magnetic_page = self._build_magnetic_page()
        tabs.addTab(magnetic_page, "MAGNETIC FIELD")
        self._subsystem_headers[magnetic_page] = "MAGNETIC FIELD CONTROLLER"

        self.pid_tab = _BeamAwarePIDGAControlTab(
            self.context, beam_input_state=self._beam_input_state,
            output_callback=self._apply_pid_delta_output,
        )
        self.beam_tab = _ControllerBeamPage(self)
        self._beam_tab_container = self._controller_scroll(self.beam_tab)
        tabs.addTab(self._beam_tab_container, "BEAM METER")
        self._subsystem_headers[self._beam_tab_container] = "BEAM METER CONTROLLER"

        tabs.addTab(self.pid_tab, "PID / GA AUTO-TUNE")
        self._subsystem_headers[self.pid_tab] = "PID / GA AUTO-TUNE CONTROLLER"
        self.bo_tab = BOControlTab(self.pid_tab)
        tabs.addTab(self.bo_tab, "BO + PID")
        self._subsystem_headers[self.bo_tab] = "BO + PID AUTO-TUNE CONTROLLER"

        for caption, title, message in (
            ("SOURCE / EXTRACTION", "SOURCE / EXTRACTION CONTROLLER",
             "A future SourceExtractionControllerPage can be imported here without changing the magnetic tabs."),
            ("VACUUM / BEAM", "VACUUM / BEAM CONTROLLER",
             "A future VacuumBeamControllerPage can be imported here."),
            ("RF POWER", "RF POWER CONTROLLER",
             "A future RFControllerPage can be imported here."),
        ):
            page = self._build_controller_placeholder(title, message)
            tabs.addTab(page, caption)
            self._subsystem_headers[page] = title

        self.subsystem_tabs = tabs
        tabs.currentChanged.connect(self._on_subsystem_changed)
        return tabs

    def _build_controller_placeholder(self, title_text: str, message: str) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        title = QLabel(title_text)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 28px; font-weight: 500;")
        text = QLabel(message)
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text.setStyleSheet(f"color: {T.muted}; font-size: 17px;")
        layout.addStretch()
        layout.addWidget(title)
        layout.addWidget(text)
        layout.addStretch()
        return card

    def _on_subsystem_changed(self, index: int) -> None:
        # Resolve by the page, not a hard-coded index: Beam Meter may move to an
        # existing outer tab row, while PID/GA and the other pages stay intact.
        page = self.subsystem_tabs.widget(index)
        title = self._subsystem_headers.get(page)
        if title is not None:
            self.controller_subtitle.setText(title)
        if page is self.pid_tab:
            self.pid_tab.refresh()
        elif page is self._beam_tab_container:
            self.beam_tab.refresh()

    def _place_beam_meter_in_outer_tab(self) -> None:
        """Reuse the existing upper Beam Meter tab of the legacy wrapper.

        This is an instance-local UI integration, not a launcher/module patch.
        The Beam page is moved, never duplicated; it continues observing this
        controller's context and this controller remains the sole queue reader.
        A direct launcher has no such ancestor, so its own upper tab stays put.
        """
        if self._beam_outer_tabs is not None:
            return
        outer = self.parentWidget()
        while outer is not None:
            if isinstance(outer, QTabWidget) and outer is not self.subsystem_tabs:
                captions = [outer.tabText(i).replace("&", "").strip().lower()
                            for i in range(outer.count())]
                if "beam meter" in captions and any(
                        name in captions for name in ("magnetic", "magnetic field")):
                    break
            outer = outer.parentWidget()
        if outer is None:
            return

        beam_index = captions.index("beam meter")
        old_page = outer.widget(beam_index)
        active_page = outer.currentWidget()
        caption, icon = outer.tabText(beam_index), outer.tabIcon(beam_index)
        tooltip = outer.tabToolTip(beam_index)

        # The original wrapper forwards fresh hardware metadata to beam_pad.
        # Preserve that interface by pointing it to this live page before the
        # obsolete pad is deleted. No guessed/default range is copied from it.
        host = outer.parentWidget()
        while host is not None:
            if hasattr(host, "beam_pad"):
                host.beam_pad = self.beam_tab
                break
            host = host.parentWidget()

        inner_index = self.subsystem_tabs.indexOf(self._beam_tab_container)
        if inner_index >= 0:
            self.subsystem_tabs.removeTab(inner_index)
        outer.removeTab(beam_index)
        outer.insertTab(beam_index, self._beam_tab_container, icon, caption)
        outer.setTabToolTip(beam_index, tooltip)
        self._beam_outer_tabs = outer
        if active_page is old_page:
            outer.setCurrentWidget(self._beam_tab_container)
        elif active_page is not None:
            outer.setCurrentWidget(active_page)
        if old_page is not None and old_page is not self._beam_tab_container:
            old_page.setParent(None)
            old_page.deleteLater()
        self.beam_tab.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Also handle a controller attached to its wrapper after the event loop
        # has already started. The method is idempotent for either launcher.
        QTimer.singleShot(0, self._place_beam_meter_in_outer_tab)

    # ------------------------------------------------------ Magnetic page
    def _build_magnetic_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.addWidget(self._build_channel_panel(), 53)
        layout.addWidget(self._build_mode_panel(), 47)
        return page

    def _build_channel_panel(self) -> QWidget:
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(0)

        header = QGridLayout()
        header.setContentsMargins(0, 0, 0, 6)
        header.setHorizontalSpacing(12)
        widths = [80, 200, 160, 140, 140]
        for column, width in enumerate(widths):
            header.setColumnMinimumWidth(column, width)

        for column, text in enumerate(("PLOT", "TARGET (A)", "CHANNEL")):
            label = QLabel(text)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet(
                f"color: {T.muted}; font-size: 13px; letter-spacing: 0.5px;"
            )
            header.addWidget(label, 0, column)

        self.power_all_btn = self._build_bulk_header(
            header,
            column=3,
            title="POWER",
            initial_text="ALL ON",
            tooltip="Turn all magnetic-field power channels ON or OFF",
            handler=self._toggle_power_all,
        )
        self.enable_all_btn = self._build_bulk_header(
            header,
            column=4,
            title="ENABLE",
            initial_text="ALL ENABLE",
            tooltip="Enable or un-enable every magnetic-field channel",
            handler=self._toggle_enable_all,
        )
        header.setRowMinimumHeight(0, 58)
        layout.addLayout(header)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet(f"color: {T.border_soft};")
        layout.addWidget(separator)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(0)
        for column, width in enumerate(widths):
            grid.setColumnMinimumWidth(column, width)

        self.plot_buttons: List[PlotToggle] = []
        self.labels: List[ClickLabel] = []
        self.channel_labels: List[ClickLabel] = []
        self.power_buttons: List[QPushButton] = []
        self.enable_buttons: List[QPushButton] = []
        # Backward-compatible attribute names used by older code.
        self.on_buttons = self.power_buttons
        self.en_buttons = self.enable_buttons

        for index, channel in enumerate(CHANNEL_NAMES):
            plot_button = PlotToggle(index)
            plot_button.setToolTip(f"Include {channel} in monitoring plots")
            plot_button.selected.connect(self._on_plot_flag_changed)
            self.plot_buttons.append(plot_button)
            grid.addWidget(plot_button, index, 0, alignment=Qt.AlignmentFlag.AlignCenter)

            target = ClickLabel(index, "0.00")
            target.clicked.connect(self._select_channel)
            target.setAlignment(Qt.AlignmentFlag.AlignCenter)
            target.setMinimumHeight(42)
            self.labels.append(target)
            grid.addWidget(target, index, 1)

            name = ClickLabel(index, channel)
            name.clicked.connect(self._select_channel)
            name.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            name.setStyleSheet(f"color: #C7D0D6; font-size: 14px;")
            self.channel_labels.append(name)
            grid.addWidget(name, index, 2)

            power = QPushButton("ON")
            power.setCheckable(True)
            power.setMinimumHeight(40)
            power.setCursor(Qt.CursorShape.PointingHandCursor)
            power.clicked.connect(
                lambda checked, idx=index: self.context.set_power_state(idx, checked, send=True)
            )
            self.power_buttons.append(power)
            grid.addWidget(power, index, 3)

            enable = QPushButton("EN")
            enable.setCheckable(True)
            enable.setMinimumHeight(40)
            enable.setCursor(Qt.CursorShape.PointingHandCursor)
            enable.clicked.connect(
                lambda checked, idx=index: self.context.set_enable_state(idx, checked, send=True)
            )
            self.enable_buttons.append(enable)
            grid.addWidget(enable, index, 4)

            if index < NUM_CHANNELS - 1:
                rule = QFrame()
                rule.setFrameShape(QFrame.Shape.HLine)
                rule.setStyleSheet(f"color: {T.border_soft};")
                grid.addWidget(rule, index, 0, 1, 5, alignment=Qt.AlignmentFlag.AlignBottom)
            grid.setRowMinimumHeight(index, 48)

        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 8, 0, 0)
        self.plot_all_btn = QPushButton("PLOT ALL")
        self.plot_all_btn.setCheckable(True)
        self.plot_all_btn.setStyleSheet(secondary_button_css())
        self.plot_all_btn.clicked.connect(self._toggle_plot_all)
        footer.addWidget(self.plot_all_btn)
        footer.addStretch()

        self.lock_btn = QPushButton("🔒  UNLOCK")
        self.lock_btn.setCheckable(True)
        self.lock_btn.setStyleSheet(secondary_button_css())
        self.lock_btn.clicked.connect(self._toggle_lock)
        footer.addWidget(self.lock_btn)
        layout.addLayout(footer)
        return card

    def _build_bulk_header(
        self,
        header: QGridLayout,
        *,
        column: int,
        title: str,
        initial_text: str,
        tooltip: str,
        handler,
    ) -> QPushButton:
        wrapper = QWidget()
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(4)
        label = QLabel(title)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(
            f"color: {T.muted}; font-size: 13px; letter-spacing: 0.5px;"
        )
        button = QPushButton(initial_text)
        button.setToolTip(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedHeight(28)
        button.clicked.connect(handler)
        wrapper_layout.addWidget(label)
        wrapper_layout.addWidget(button)
        header.addWidget(wrapper, 0, column)
        return button

    def _build_mode_panel(self) -> QWidget:
        """Compact magnetic-field operating modes.

        PID / GA is intentionally not created here; it lives in the full-width
        upper tab built by :meth:`_build_subsystem_tabs`.
        """
        card = Card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        tabs = QTabWidget()
        tabs.setTabBar(TeslaTabBar())
        tabs.setDocumentMode(True)
        tabs.setStyleSheet("QTabWidget::pane { border: none; background: transparent; }")

        self.manual_tab = ManualControlTab(self.context)
        self.sequence_tab = SequenceControlTab(self.context)
        self.multi_sequence_tab = MultiTCSequenceTab(self.context)

        tabs.addTab(self.manual_tab, "MANUAL")
        tabs.addTab(self.sequence_tab, "SEQUENCE")
        tabs.addTab(self.multi_sequence_tab, "MULTI-TC SEQUENCE")
        layout.addWidget(tabs)
        self.mode_tabs = tabs
        return card

    # ------------------------------------------------------------- Context
    @property
    def selected_index(self) -> int:
        return self.context.selected_index

    @selected_index.setter
    def selected_index(self, value: int) -> None:
        self.context.set_selected_index(int(value))

    def _connect_context(self) -> None:
        self.context.selected_channel_changed.connect(lambda _idx: self.update_display())
        self.context.target_changed.connect(lambda _idx, _value: self.update_display())
        self.context.power_states_changed.connect(lambda _states: self._sync_power_controls())
        self.context.enable_states_changed.connect(lambda _states: self._sync_enable_controls())
        self.context.plot_flags_changed.connect(lambda _states: self._sync_plot_controls())
        self.context.mode_changed.connect(self._on_mode_changed)
        self.context.status_changed.connect(self._show_status_message)

    def _select_channel(self, index: int) -> None:
        self.context.set_selected_index(index)
        self.update_display()

    def _on_plot_flag_changed(self, index: int, checked: bool) -> None:
        self.context.set_plot_flag(index, checked, send=True)

    def _on_mode_changed(self, mode: str) -> None:
        self.mode_label.setText(f"MODE: {mode}")
        if mode == ControlMode.MANUAL.value:
            self.mode_label.setStyleSheet(f"color: {T.cyan}; font-size: 13px;")
        elif mode in (ControlMode.PID.value, ControlMode.GA_TUNING.value):
            self.mode_label.setStyleSheet(f"color: {T.orange}; font-size: 13px;")
        else:
            self.mode_label.setStyleSheet(f"color: {T.amber}; font-size: 13px;")

    def _show_status_message(self, text: str) -> None:
        print(f"[Controller] {text}")

    # ---------------------------------------------------------- Row states
    def _apply_power_button_visual(self, button: QPushButton, bit_state: bool) -> None:
        # Preserve the original controller's action-label convention:
        # bit False -> button offers ON; bit True -> button offers OFF.
        button.setText("OFF" if bit_state else "ON")
        button.setToolTip(
            "Power bit is ON — click to turn OFF"
            if bit_state
            else "Power bit is OFF — click to turn ON"
        )
        button.setStyleSheet(
            state_button_css(T.green if bit_state else T.red, bit_state)
        )

    def _apply_enable_button_visual(self, button: QPushButton, bit_state: bool) -> None:
        # Same action-label convention as the previous EN/UN controller.
        button.setText("UN" if bit_state else "EN")
        button.setToolTip(
            "Channel is enabled — click to un-enable"
            if bit_state
            else "Channel is not enabled — click to enable"
        )
        button.setStyleSheet(
            state_button_css(T.cyan if bit_state else T.dim, bit_state)
        )

    def _sync_power_controls(self) -> None:
        for button, state in zip(self.power_buttons, self.context.power_states):
            button.blockSignals(True)
            button.setChecked(bool(state))
            button.blockSignals(False)
            self._apply_power_button_visual(button, bool(state))

        all_on = bool(self.context.power_states) and all(self.context.power_states)
        self.power_all_btn.setText("ALL OFF" if all_on else "ALL ON")
        self.power_all_btn.setStyleSheet(
            bulk_button_css(T.red if all_on else T.green)
        )

    def _sync_enable_controls(self) -> None:
        for button, state in zip(self.enable_buttons, self.context.enable_states):
            button.blockSignals(True)
            button.setChecked(bool(state))
            button.blockSignals(False)
            self._apply_enable_button_visual(button, bool(state))

        all_enabled = bool(self.context.enable_states) and all(self.context.enable_states)
        self.enable_all_btn.setText("ALL DISABLE" if all_enabled else "ALL ENABLE")
        self.enable_all_btn.setStyleSheet(
            bulk_button_css(T.orange if all_enabled else T.cyan)
        )

    def _sync_plot_controls(self) -> None:
        for button, state in zip(self.plot_buttons, self.context.plot_flags):
            button.blockSignals(True)
            button.setChecked(bool(state))
            button.blockSignals(False)
        all_plotted = bool(self.context.plot_flags) and all(self.context.plot_flags)
        self.plot_all_btn.blockSignals(True)
        self.plot_all_btn.setChecked(all_plotted)
        self.plot_all_btn.setText("CLEAR ALL" if all_plotted else "PLOT ALL")
        self.plot_all_btn.blockSignals(False)

    def _sync_all_channel_controls(self) -> None:
        self._sync_power_controls()
        self._sync_enable_controls()
        self._sync_plot_controls()

    def _toggle_power_all(self) -> None:
        turn_on = not all(self.context.power_states)
        self.context.set_all_power_states(turn_on, send=True)

    def _toggle_enable_all(self) -> None:
        enable_all = not all(self.context.enable_states)
        self.context.set_all_enable_states(enable_all, send=True)

    def _toggle_plot_all(self, checked: bool) -> None:
        self.context.set_all_plot_flags(bool(checked), send=True)

    def _toggle_lock(self, checked: bool) -> None:
        self.controls_locked = bool(checked)
        self.lock_btn.setText("🔒  LOCKED" if checked else "🔒  UNLOCK")
        enabled = not checked
        for button in self.power_buttons + self.enable_buttons:
            button.setEnabled(enabled)
        self.power_all_btn.setEnabled(enabled)
        self.enable_all_btn.setEnabled(enabled)

    # ----------------------------------------------------------- Live data
    def _poll_actual_data(self) -> None:
        """Update magnetic actuals and calibrated beam-current feedback.

        The existing packet contains magnetic channels and beam_current.
        Magnetic scaling is unchanged. Beam input uses the selected CNL
        calibration or numerical nA identity, both embedded in this file.
        """
        # Preferred magnetic-only route used by some stand-alone launchers.
        # It has no beam packet, so any previously received beam sample simply
        # ages and is rejected by the PID stale-data check.
        if self.current_values_ref is not None:
            values: list[float] = []
            for index in range(NUM_CHANNELS):
                try:
                    values.append(float(self.current_values_ref[index]))
                except (IndexError, TypeError, ValueError):
                    values.append(self.actual_values[index])
            self.actual_values[:] = values
            self.context.update_actual_values(values)
            self._set_live_state(True)
            return

        if self.data_queue is None:
            self._set_live_state(False)
            self.context.invalidate_beam_measurement("NO CONTROLLER DATA QUEUE")
            return

        now = time.time()
        last = None
        received_new_packet = False
        while True:
            try:
                last = self.data_queue.get_nowait()
                received_new_packet = True
            except Exception:
                break

        if last is not None:
            self._last_sample = last
            self.last_data_time = now
        elif self._last_sample is not None and (now - self.last_data_time) <= self.HOLD_SECONDS:
            last = self._last_sample
        else:
            self._set_live_state(False)
            self.context.invalidate_beam_measurement("CONTROLLER DATA STALE")
            return

        if not isinstance(last, dict):
            self._set_live_state(False)
            if received_new_packet:
                self.context.invalidate_beam_measurement("INVALID CONTROLLER PACKET")
            return

        # Update beam timestamp only for a genuinely new packet.  Reusing the
        # held magnetic packet must not make stale beam data appear fresh.
        if received_new_packet:
            self._update_beam_feedback(last)

        raw = list(last.get("channels", []))
        if not raw:
            self._set_live_state(False)
            return

        count = min(len(TRIM_KEYS), len(raw), NUM_CHANNELS)
        try:
            for index in range(count):
                self.actual_values[index] = float(
                    self.scaler.raw_to_eng(TRIM_KEYS[index], raw[index])
                )
        except Exception as exc:
            print("[Controller] scaling error:", exc)
            self._set_live_state(False)
            return

        self.context.update_actual_values(self.actual_values)
        self._set_live_state(True)

    def _update_beam_feedback(self, packet: dict) -> None:
        """Read LabVIEW's beam_current using this controller's selected path."""
        self._follow_beam_range_from_packet(packet)
        state = self._beam_input_state
        try:
            raw = _beam_raw_value(packet)
            stamp = packet.get("_pid_rx_mono")
            stamp = time.monotonic() if stamp is None else float(stamp)
            now = time.monotonic()
            if not math.isfinite(stamp) or stamp <= 0 or stamp > now + 0.05:
                raise ValueError("invalid beam receive timestamp")
            if state.changed_at > 0 and stamp <= state.changed_at:
                self.context.invalidate_beam_measurement("INPUT CHANGED — WAITING FOR FRESH BEAM DATA")
                return
            if stamp <= self._beam_last_rx:
                return  # A held/out-of-order packet must not refresh the beam age.
            source = packet.get("_pid_source_s")
            if source is not None:
                source = float(source)
                if not math.isfinite(source) or source <= 0:
                    raise ValueError("invalid beam source timestamp")
                if self._beam_last_source is not None and source <= self._beam_last_source:
                    return
            # Direct mode never requires or uses beam_range_idx for scaling.
            index = None if state.is_direct else _beam_range_index(packet)
            current = state.convert(raw, index)
        except (TypeError, ValueError, OverflowError, AttributeError) as exc:
            self.context.invalidate_beam_measurement(f"BEAM INPUT ERROR: {exc}")
            return
        self._beam_last_raw = raw
        self._beam_last_value_range = index
        self._beam_last_rx = stamp
        self._beam_last_source = source
        status = ("LIVE — OTHER DEVICES / nA" if state.is_direct
                  else f"LIVE — CNL AMMETER / RANGE {index + 1}")
        self.context.update_beam_measurement(current, timestamp=stamp, status=status)

    def _set_live_state(self, live: bool) -> None:
        color = T.green if live else T.red
        self.live_dot.set_color(color)
        self.live_label.setText("LIVE" if live else "NO DATA")
        self.live_label.setStyleSheet(f"color: {color}; font-size: 14px;")

    # ------------------------------------------------------------- Display
    def update_display(self) -> None:
        for index, label in enumerate(self.labels):
            label.setText(f"{self.context.target(index):.2f}")
            label.setStyleSheet(target_label_css(index == self.context.selected_index))
        self.manual_tab.refresh()
        self.pid_tab.refresh()

    def _update_clock(self) -> None:
        self.clock_label.setText(time.strftime("%H:%M"))

    # ------------------------------------------------------- Output adapter
    def send_update(self) -> None:
        """Preserve the existing ENG->RAW scaling and control-queue payload."""
        self.plot_config["channels"] = list(self.context.plot_flags)

        engineering_values = self.context.targets_snapshot()
        raw_values = list(engineering_values)
        count = min(len(TRIM_KEYS), len(raw_values))
        for index in range(count):
            raw_values[index] = self.scaler.eng_to_raw(
                TRIM_KEYS[index], engineering_values[index]
            )

        if self.control_queue is None:
            return
        try:
            self.control_queue.put(
                (
                    raw_values[: len(TRIM_KEYS)],
                    list(self.context.power_states),
                    list(self.context.enable_states),
                )
            )
        except Exception as exc:
            print("⚠️ control_queue.put failed:", exc)

    def _apply_pid_delta_output(self, channel_index: int, delta_target: float, result) -> bool:
        """Apply the paper controller's signed per-sample TC target increment.

        This callback is reached only when the operator has selected
        ``ARM HARDWARE OUTPUT``.  Preview mode never calls it.  The existing
        engineering-target -> scaling -> control_queue path is preserved.
        """
        try:
            index = int(channel_index)
            delta = float(delta_target)
        except (TypeError, ValueError):
            return False

        if not math.isfinite(delta) or not 0 <= index < 12:
            return False
        if not self.context.can_write(ControlMode.PID):
            return False

        # Require the commanded trim coil to be both powered and enabled before
        # accepting an armed PID command.  This does not affect preview mode.
        if not self.context.power_states[index]:
            self.context.status_changed.emit(
                f"PID output rejected: {self.context.channel_name(index)} power is OFF"
            )
            return False
        if not self.context.enable_states[index]:
            self.context.status_changed.emit(
                f"PID output rejected: {self.context.channel_name(index)} is not enabled"
            )
            return False

        if abs(delta) <= 1e-12:
            return True

        current_target = self.context.target(index)
        requested_target = current_target + delta
        limited_target = self.context.clamp_target(index, requested_target)
        self.context.set_target(
            index,
            limited_target,
            send=True,
            source_mode=ControlMode.PID,
        )
        if not math.isclose(
            requested_target, limited_target, rel_tol=0.0, abs_tol=1e-9
        ):
            self.context.status_changed.emit(
                f"PID target clamped at {limited_target:.2f} A for "
                f"{self.context.channel_name(index)}"
            )
        return True

    def set_pid_output_callback(self, callback: PIDOutputCallback | None) -> None:
        """Connect the PID output to an integration/test adapter.

        The PID tab remains preview-only until this method is called and the
        operator checks ARM HARDWARE OUTPUT in the PID tab.
        """
        self.pid_tab.output_callback = callback

    def submit_pid_ga_fitness(self, fitness: float) -> None:
        """Hook for a future automated PID candidate test runner."""
        self.pid_tab.submit_candidate_fitness(float(fitness))


# ---------------------------------------------------------------- Demo only
if __name__ == "__main__":
    # This launcher exercises the modular GUI while keeping the production
    # constructor unchanged.  It still requires the project's scaling.py.
    import math as _math

    demo_targets = [341.0, 348.0, 0.0, 75.0, 63.0, 150.0, 209.0,
                    147.0, 296.0, 543.0, 0.0, 0.0, 2600.0, 7.0]
    demo_actuals = list(demo_targets)
    demo_control_queue = _queue.Queue()
    demo_plot_config = {"channels": [False] * NUM_CHANNELS}

    app = QApplication(sys.argv)
    window = MagneticFieldControllerWindow(
        demo_control_queue,
        demo_targets,
        demo_plot_config,
        current_values_ref=demo_actuals,
    )

    phase = 0.0

    def update_demo_actuals():
        nonlocal_phase = update_demo_actuals.phase
        for i, target in enumerate(demo_targets):
            demo_actuals[i] += 0.08 * (target - demo_actuals[i])
            demo_actuals[i] += 0.015 * _math.sin(nonlocal_phase + i * 0.6)
        update_demo_actuals.phase += 0.08

    update_demo_actuals.phase = phase
    demo_timer = QTimer()
    demo_timer.timeout.connect(update_demo_actuals)
    demo_timer.start(50)

    window.show()
    sys.exit(app.exec())
