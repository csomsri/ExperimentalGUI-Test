# -*- coding: utf-8 -*-
"""Synchronized multi-channel sequence tab."""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from controller_context import ControlMode, ControllerContext
from controller_theme import T, info_label_css, secondary_button_css


class MultiTCSequenceTab(QWidget):
    def __init__(self, context: ControllerContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.context = context
        self._job: dict | None = None
        self._paused = False
        self._pause_started = 0.0
        self._paused_total = 0.0

        self._build_ui()
        self.context.mode_changed.connect(self._on_mode_changed)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(25)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(12)

        title = QLabel("MULTI-CHANNEL SEQUENCE")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 20px; font-weight: 500;")
        layout.addWidget(title)

        note = QLabel(
            "Enter an elapsed time and one or more channel targets. Empty cells preserve their current target. "
            "All values in one row are transmitted together."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {T.muted};")
        layout.addWidget(note)

        self.table = QTableWidget(0, 1 + len(self.context.channel_names))
        self.table.setHorizontalHeaderLabels(["TIME (s)"] + self.context.channel_names)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 90)
        for column in range(1, self.table.columnCount()):
            self.table.setColumnWidth(column, 105)
        layout.addWidget(self.table, 1)

        controls = QHBoxLayout()
        for text, handler in (
            ("ADD ROW", self.add_row),
            ("LOAD CURRENT", self.load_current),
            ("REMOVE", self.remove_selected_row),
            ("CLEAR", self.clear_rows),
        ):
            button = QPushButton(text)
            button.setStyleSheet(secondary_button_css())
            button.clicked.connect(handler)
            controls.addWidget(button)
        controls.addStretch()

        self.start_button = QPushButton("START")
        self.start_button.setStyleSheet(secondary_button_css(T.green))
        self.start_button.clicked.connect(self.start)
        controls.addWidget(self.start_button)

        self.pause_button = QPushButton("PAUSE")
        self.pause_button.setCheckable(True)
        self.pause_button.setStyleSheet(secondary_button_css(T.amber))
        self.pause_button.clicked.connect(self.toggle_pause)
        controls.addWidget(self.pause_button)

        self.stop_button = QPushButton("STOP")
        self.stop_button.setStyleSheet(secondary_button_css(T.red))
        self.stop_button.clicked.connect(self.stop)
        controls.addWidget(self.stop_button)
        layout.addLayout(controls)

        self.status_label = QLabel("IDLE")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet(info_label_css())
        layout.addWidget(self.status_label)

    def add_row(self) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        previous_time = 0.0
        if row > 0:
            item = self.table.item(row - 1, 0)
            try:
                previous_time = float(item.text()) if item else float(row - 1)
            except (TypeError, ValueError):
                previous_time = float(row - 1)
        self.table.setItem(row, 0, QTableWidgetItem(f"{previous_time + (0.0 if row == 0 else 1.0):.2f}"))
        for column in range(1, self.table.columnCount()):
            self.table.setItem(row, column, QTableWidgetItem(""))

    def load_current(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            self.add_row()
            row = self.table.rowCount() - 1
        for index, value in enumerate(self.context.targets_snapshot()):
            self.table.setItem(row, index + 1, QTableWidgetItem(f"{value:.2f}"))

    def remove_selected_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def clear_rows(self) -> None:
        if self._job is not None:
            self.stop()
        self.table.setRowCount(0)
        self.status_label.setText("IDLE")

    def _read_rows(self) -> list[tuple[float, dict[int, float]]]:
        rows: list[tuple[float, dict[int, float]]] = []
        for row in range(self.table.rowCount()):
            time_item = self.table.item(row, 0)
            if time_item is None or not time_item.text().strip():
                continue
            try:
                at_time = float(time_item.text())
            except (TypeError, ValueError):
                raise ValueError(f"Invalid time in row {row + 1}")
            if at_time < 0.0:
                raise ValueError(f"Time cannot be negative in row {row + 1}")

            updates: dict[int, float] = {}
            for index in range(len(self.context.channel_names)):
                item = self.table.item(row, index + 1)
                if item is None or not item.text().strip():
                    continue
                try:
                    updates[index] = float(item.text())
                except (TypeError, ValueError):
                    raise ValueError(
                        f"Invalid target for {self.context.channel_name(index)} in row {row + 1}"
                    )
            if updates:
                rows.append((at_time, updates))
        rows.sort(key=lambda item: item[0])
        return rows

    def start(self) -> None:
        try:
            rows = self._read_rows()
        except ValueError as exc:
            self.status_label.setText(str(exc).upper())
            return
        if not rows:
            self.status_label.setText("SEQUENCE EMPTY")
            return

        self._job = {
            "rows": rows,
            "start": time.monotonic(),
            "next": 0,
        }
        self._paused = False
        self._paused_total = 0.0
        self.pause_button.blockSignals(True)
        self.pause_button.setChecked(False)
        self.pause_button.blockSignals(False)
        self.pause_button.setText("PAUSE")
        self.context.set_mode(ControlMode.MULTI_SEQUENCE)
        self.status_label.setText(f"RUNNING — 0 / {len(rows)}")

    def toggle_pause(self, checked: bool) -> None:
        if self._job is None:
            self.pause_button.blockSignals(True)
            self.pause_button.setChecked(False)
            self.pause_button.blockSignals(False)
            return
        self._paused = bool(checked)
        if self._paused:
            self._pause_started = time.monotonic()
            self.pause_button.setText("RESUME")
            self.status_label.setText("PAUSED")
        else:
            self._paused_total += time.monotonic() - self._pause_started
            self.pause_button.setText("PAUSE")
            self.status_label.setText("RUNNING")

    def stop(self, *, restore_manual_mode: bool = True) -> None:
        self._job = None
        self._paused = False
        self.pause_button.blockSignals(True)
        self.pause_button.setChecked(False)
        self.pause_button.blockSignals(False)
        self.pause_button.setText("PAUSE")
        self.status_label.setText("STOPPED")
        if restore_manual_mode and self.context.can_write(ControlMode.MULTI_SEQUENCE):
            self.context.set_mode(ControlMode.MANUAL)

    def _on_mode_changed(self, mode: str) -> None:
        if self._job is not None and mode != ControlMode.MULTI_SEQUENCE.value:
            self.stop(restore_manual_mode=False)
            self.status_label.setText(f"STOPPED — MODE CHANGED TO {mode}")

    def _advance(self) -> None:
        if self._job is None or self._paused:
            return
        if not self.context.can_write(ControlMode.MULTI_SEQUENCE):
            self.stop(restore_manual_mode=False)
            return

        elapsed = time.monotonic() - self._job["start"] - self._paused_total
        rows = self._job["rows"]
        while self._job["next"] < len(rows) and elapsed >= rows[self._job["next"]][0]:
            _at_time, updates = rows[self._job["next"]]
            self.context.set_targets(
                updates,
                send=True,
                source_mode=ControlMode.MULTI_SEQUENCE,
            )
            self._job["next"] += 1

        self.status_label.setText(
            f"RUNNING — {self._job['next']} / {len(rows)} — t = {elapsed:.2f} s"
        )
        if self._job["next"] >= len(rows):
            self.status_label.setText("COMPLETED")
            self._job = None
            self.context.set_mode(ControlMode.MANUAL)


__all__ = ["MultiTCSequenceTab"]
