# -*- coding: utf-8 -*-
"""Manual magnetic-channel control tab.

This file can remain unchanged while PID/GA development happens in the PID
modules.  It communicates only through ``ControllerContext``.
"""

from __future__ import annotations

import math
import time
from collections import deque
from typing import List

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QGridLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from controller_context import ControlMode, ControllerContext
from controller_theme import Card, T, TrendPlot, digit_button_css


DIGIT_FACTORS = [1000.0, 100.0, 10.0, 1.0, 0.1, 0.01]


class ManualControlTab(QWidget):
    def __init__(self, context: ControllerContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.context = context

        self._t0 = time.monotonic()
        self._plot_t = deque(maxlen=800)
        self._plot_target = deque(maxlen=800)
        self._plot_actual = deque(maxlen=800)

        self._build_ui()
        self._connect_context()
        self.refresh()

        self._plot_timer = QTimer(self)
        self._plot_timer.timeout.connect(self._update_plot)
        self._plot_timer.start(50)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(14)

        title = QLabel("ADJUST SELECTED CHANNEL")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: 500; letter-spacing: 1px;")
        layout.addWidget(title)

        readout = Card(radius=9)
        grid = QGridLayout(readout)
        grid.setContentsMargins(18, 12, 18, 12)
        grid.setHorizontalSpacing(0)

        labels = [
            ("SELECTED CHANNEL", T.muted),
            ("TARGET", T.muted),
            ("ACTUAL", T.orange),
        ]
        for col, (text, color) in zip((0, 2, 4), labels):
            label = QLabel(text)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet(f"color: {color}; font-size: 13px;")
            grid.addWidget(label, 0, col)

        self.selected_channel_display = QLabel("TC1")
        self.selected_channel_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.selected_channel_display.setStyleSheet(
            f"color: {T.text}; font-size: 29px; font-weight: 600; letter-spacing: 1px;"
        )
        self.target_display = QLabel("0.00 A")
        self.target_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.target_display.setStyleSheet(
            f"color: {T.cyan}; font-size: 33px; font-weight: 600;"
        )
        self.actual_display = QLabel("—")
        self.actual_display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.actual_display.setStyleSheet(
            f"color: {T.orange}; font-size: 33px; font-weight: 600;"
        )

        left_divider = QFrame()
        left_divider.setFrameShape(QFrame.Shape.VLine)
        left_divider.setStyleSheet(f"color: {T.border};")
        right_divider = QFrame()
        right_divider.setFrameShape(QFrame.Shape.VLine)
        right_divider.setStyleSheet(f"color: {T.border};")

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(2, 1)
        grid.setColumnStretch(4, 1)
        grid.addWidget(self.selected_channel_display, 1, 0)
        grid.addWidget(left_divider, 0, 1, 2, 1)
        grid.addWidget(self.target_display, 1, 2)
        grid.addWidget(right_divider, 0, 3, 2, 1)
        grid.addWidget(self.actual_display, 1, 4)
        layout.addWidget(readout)

        plot_card = Card(radius=9)
        plot_layout = QVBoxLayout(plot_card)
        plot_layout.setContentsMargins(12, 8, 10, 8)
        plot_title = QLabel("SELECTED CHANNEL TREND")
        plot_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        plot_title.setStyleSheet(f"color: {T.muted}; font-size: 13px;")
        plot_layout.addWidget(plot_title)
        self.plot = TrendPlot(y_axis_label="CURRENT (A)")
        plot_layout.addWidget(self.plot)
        layout.addWidget(plot_card, 1)

        digit_card = Card(radius=9)
        digit_grid = QGridLayout(digit_card)
        digit_grid.setContentsMargins(8, 10, 8, 10)
        digit_grid.setHorizontalSpacing(14)
        digit_grid.setVerticalSpacing(8)
        self.digit_labels: List[QLabel] = []
        self.up_buttons: List[QPushButton] = []
        self.down_buttons: List[QPushButton] = []

        for i in range(6):
            up = QPushButton("⌃")
            up.setFixedHeight(40)
            up.setStyleSheet(digit_button_css())
            up.clicked.connect(lambda _checked=False, idx=i: self._adjust_digit(idx, +1))
            digit_grid.addWidget(up, 0, i)
            self.up_buttons.append(up)

            digit = QLabel("0")
            digit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            digit.setMinimumSize(70, 48)
            digit.setStyleSheet(
                f"color: {T.cyan}; font-size: 34px; font-weight: 500; background: transparent;"
            )
            self.digit_labels.append(digit)
            digit_grid.addWidget(digit, 1, i)

            down = QPushButton("⌄")
            down.setFixedHeight(40)
            down.setStyleSheet(digit_button_css())
            down.clicked.connect(lambda _checked=False, idx=i: self._adjust_digit(idx, -1))
            digit_grid.addWidget(down, 2, i)
            self.down_buttons.append(down)

        layout.addWidget(digit_card)

    def _connect_context(self) -> None:
        self.context.selected_channel_changed.connect(self._on_channel_changed)
        self.context.target_changed.connect(self._on_target_changed)
        self.context.actual_values_changed.connect(lambda _values: self._refresh_actual())

    def _on_channel_changed(self, _index: int) -> None:
        self._reset_plot()
        self.refresh()

    def _on_target_changed(self, index: int, _value: float) -> None:
        if index == self.context.selected_index:
            self.refresh()

    def _adjust_digit(self, digit_index: int, direction: int) -> None:
        self.context.adjust_target(
            self.context.selected_index,
            DIGIT_FACTORS[digit_index] * float(direction),
            source_mode=ControlMode.MANUAL,
            send=True,
        )

    def _reset_plot(self) -> None:
        self._t0 = time.monotonic()
        self._plot_t.clear()
        self._plot_target.clear()
        self._plot_actual.clear()
        self.plot.clear()

    def _refresh_actual(self) -> None:
        actual = self.context.actual()
        self.actual_display.setText(f"{actual:.2f} A" if math.isfinite(actual) else "—")

    def _update_plot(self) -> None:
        elapsed = time.monotonic() - self._t0
        target = self.context.target()
        actual = self.context.actual()
        if not math.isfinite(actual):
            actual = self._plot_actual[-1] if self._plot_actual else 0.0
        self._plot_t.append(elapsed)
        self._plot_target.append(target)
        self._plot_actual.append(actual)
        self.plot.set_data(self._plot_t, self._plot_target, self._plot_actual)
        self._refresh_actual()

    def refresh(self) -> None:
        value = self.context.target()
        self.selected_channel_display.setText(self.context.channel_name())
        self.target_display.setText(f"{value:.2f} A")
        self._refresh_actual()
        digits = self._split_digits(value)
        for i, digit in enumerate(digits):
            self.digit_labels[i].setText(f".{digit}" if i == 4 else str(digit))

    @staticmethod
    def _split_digits(value: float) -> list[int]:
        value = max(0.0, min(9999.99, float(value)))
        hundredths = int(round(value * 100))
        return [
            (hundredths // 100000) % 10,
            (hundredths // 10000) % 10,
            (hundredths // 1000) % 10,
            (hundredths // 100) % 10,
            (hundredths // 10) % 10,
            hundredths % 10,
        ]


__all__ = ["ManualControlTab"]
