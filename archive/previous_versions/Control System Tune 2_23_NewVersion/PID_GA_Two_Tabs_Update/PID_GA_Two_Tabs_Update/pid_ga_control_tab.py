# -*- coding: utf-8 -*-
"""Tesla-style PID and genetic-algorithm controller workspace.

The GUI imports the PID and GA engines from separate, non-Qt modules.  That
allows PID/GA development without modifying Manual or Sequence code.

Safety note:
    The tab starts in preview mode.  It calculates PID output but does not write
    that output to hardware unless an explicit ``output_callback`` is supplied
    by the integration layer and ARM HARDWARE OUTPUT is checked.
"""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Callable

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from controller_context import ControlMode, ControllerContext
from controller_theme import (
    Card,
    T,
    TeslaTabBar,
    TrendPlot,
    info_label_css,
    secondary_button_css,
)
from ga_pid_tuner import GAPIDTuner, GATuningConfig, GainBounds, PIDCandidate
from pid_controller import PIDController, PIDGains, PIDLimits, PIDResult


PIDOutputCallback = Callable[[int, float, PIDResult], bool | None]


class PIDGAControlTab(QWidget):
    """Nested PID and GA pages with independent graphs and shared controller context."""

    candidate_requested = pyqtSignal(object)
    pid_output_calculated = pyqtSignal(int, float, object)

    def __init__(
        self,
        context: ControllerContext,
        output_callback: PIDOutputCallback | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.context = context
        self.output_callback = output_callback

        self.pid = PIDController()
        self.ga_tuner: GAPIDTuner | None = None
        self._pid_running = False
        self._pid_channel = 0
        self._last_pid_time = 0.0

        self._plot_t0 = time.monotonic()
        self._plot_t = deque(maxlen=1000)
        self._plot_setpoint = deque(maxlen=1000)
        self._plot_actual = deque(maxlen=1000)

        # GA history is intentionally independent from the PID response plot.
        # Each submitted candidate contributes one point: candidate fitness and
        # best-so-far fitness versus evaluation number.
        self._ga_evaluation_count = 0
        self._ga_evaluations = deque(maxlen=1000)
        self._ga_fitness_history = deque(maxlen=1000)
        self._ga_best_history = deque(maxlen=1000)

        self._build_ui()
        self._connect_context()
        self.refresh()

        self._pid_timer = QTimer(self)
        self._pid_timer.timeout.connect(self._pid_step)

        self._display_timer = QTimer(self)
        self._display_timer.timeout.connect(self._refresh_live_readouts)
        self._display_timer.start(100)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        """Build separate PID and GA workspaces inside the controller page.

        The public widget remains ``PIDGAControlTab`` so the integration in
        ``MagneticFieldControllerWindow.py`` does not change.  Internally, the
        page now contains two Tesla-style tabs:

        * PID CONTROL keeps the existing PID channel selector, live readouts,
          response graph, parameters, and start/stop controls.
        * GA AUTO-TUNE contains only the genetic-algorithm controls and its own
          candidate-fitness graph.
        """
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.control_tabs = QTabWidget()
        self.control_tabs.setTabBar(TeslaTabBar())
        self.control_tabs.tabBar().setExpanding(True)
        self.control_tabs.setDocumentMode(True)
        self.control_tabs.setStyleSheet(
            "QTabWidget::pane { border: none; background: transparent; }"
        )
        self.control_tabs.addTab(self._build_pid_page(), "PID CONTROL")
        self.control_tabs.addTab(self._build_ga_page(), "GA AUTO-TUNE")
        outer.addWidget(self.control_tabs)

        # Keep the GA seed-gain readout synchronized with the gain fields that
        # remain on the PID page.
        for spin in (self.kp_spin, self.ki_spin, self.kd_spin):
            spin.valueChanged.connect(lambda _value: self._refresh_ga_context())
        self._refresh_ga_context()

    @staticmethod
    def _new_scroll_page() -> tuple[QWidget, QVBoxLayout]:
        """Create one independently scrollable nested-tab page."""
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page_layout.addWidget(scroll)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(18, 14, 18, 18)
        layout.setSpacing(14)
        scroll.setWidget(body)
        return page, layout

    def _build_pid_page(self) -> QWidget:
        page, layout = self._new_scroll_page()

        title = QLabel("PID CONTROL")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            "font-size: 20px; font-weight: 500; letter-spacing: 1px;"
        )
        layout.addWidget(title)

        notice = QLabel(
            "DEVELOPMENT MODE — PID output is calculated locally. Hardware output remains blocked until an "
            "integration callback is connected and ARM HARDWARE OUTPUT is selected."
        )
        notice.setWordWrap(True)
        notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        notice.setStyleSheet(info_label_css(T.amber))
        layout.addWidget(notice)

        # This is the same PID content that previously occupied the left side of
        # the combined PID/GA page, now allowed to use the full page width.
        layout.addWidget(self._build_channel_selector_card())
        layout.addWidget(self._build_readout_card())
        layout.addWidget(self._build_plot_card())
        layout.addWidget(self._build_pid_card())
        layout.addStretch()
        return page

    def _build_ga_page(self) -> QWidget:
        page, layout = self._new_scroll_page()

        title = QLabel("GENETIC ALGORITHM — PID AUTO-TUNE")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            "font-size: 20px; font-weight: 500; letter-spacing: 1px;"
        )
        layout.addWidget(title)

        notice = QLabel(
            "GA WORKSPACE — Candidate generation, fitness submission, progress, and fitness history are isolated "
            "from the PID controls. Lower fitness is better. The starting gains are read from the PID CONTROL tab."
        )
        notice.setWordWrap(True)
        notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        notice.setStyleSheet(info_label_css(T.cyan))
        layout.addWidget(notice)

        layout.addWidget(self._build_ga_context_card())
        layout.addWidget(self._build_ga_plot_card())
        layout.addWidget(self._build_ga_card())
        layout.addStretch()
        return page

    def _build_ga_context_card(self) -> QWidget:
        card = Card(radius=9)
        grid = QGridLayout(card)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)

        channel_heading = QLabel("SELECTED CONTROL CHANNEL")
        channel_heading.setStyleSheet(
            f"color: {T.muted}; font-size: 11px; font-weight: 600;"
        )
        seed_heading = QLabel("GAINS LOADED IN PID TAB (Kp, Ki, Kd)")
        seed_heading.setStyleSheet(
            f"color: {T.muted}; font-size: 11px; font-weight: 600;"
        )
        grid.addWidget(channel_heading, 0, 0)
        grid.addWidget(seed_heading, 0, 1)

        self.ga_channel_display = QLabel("—")
        self.ga_channel_display.setStyleSheet(
            f"color: {T.cyan}; font-size: 18px; font-weight: 600;"
        )
        self.ga_seed_display = QLabel("—")
        self.ga_seed_display.setStyleSheet(
            f"color: {T.text}; font-size: 16px; font-weight: 600;"
        )
        grid.addWidget(self.ga_channel_display, 1, 0)
        grid.addWidget(self.ga_seed_display, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 3)
        return card

    def _build_ga_plot_card(self) -> QWidget:
        card = Card(radius=9)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 10, 8)
        layout.setSpacing(8)

        title = QLabel("GA FITNESS HISTORY")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {T.muted}; font-size: 13px;")
        layout.addWidget(title)

        readouts = QHBoxLayout()
        readouts.setSpacing(10)
        last_label = QLabel("LAST CANDIDATE")
        last_label.setStyleSheet(f"color: {T.orange}; font-size: 11px;")
        self.ga_last_fitness_display = self._component_display(T.orange)
        best_label = QLabel("BEST SO FAR")
        best_label.setStyleSheet(f"color: {T.green}; font-size: 11px;")
        self.ga_best_fitness_plot_display = self._component_display(T.green)
        readouts.addStretch()
        readouts.addWidget(last_label)
        readouts.addWidget(self.ga_last_fitness_display)
        readouts.addSpacing(16)
        readouts.addWidget(best_label)
        readouts.addWidget(self.ga_best_fitness_plot_display)
        readouts.addStretch()
        layout.addLayout(readouts)

        self.ga_plot = TrendPlot(
            y_axis_label="FITNESS SCORE",
            x_axis_label="EVALUATION",
            target_color=T.green,
            actual_color=T.orange,
        )
        self.ga_plot.setMinimumHeight(240)
        layout.addWidget(self.ga_plot)

        explanation = QLabel(
            "Orange = submitted candidate fitness. Green = best fitness found so far. Fitness is lower-is-better."
        )
        explanation.setAlignment(Qt.AlignmentFlag.AlignCenter)
        explanation.setStyleSheet(f"color: {T.muted}; font-size: 11px;")
        layout.addWidget(explanation)
        return card

    def _build_channel_selector_card(self) -> QWidget:
        card = Card(radius=9)
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(12)

        label = QLabel("CONTROL CHANNEL")
        label.setStyleSheet(f"color: {T.muted}; font-size: 12px; font-weight: 600;")
        row.addWidget(label)

        self.channel_selector = QComboBox()
        self.channel_selector.addItems(self.context.channel_names)
        self.channel_selector.setMinimumWidth(230)
        self.channel_selector.setMaximumWidth(360)
        self.channel_selector.setToolTip(
            "Select the trim coil or magnet controlled by PID. Changing the channel stops a running PID loop."
        )
        self.channel_selector.setStyleSheet(
            f"QComboBox {{ color: {T.cyan}; font-size: 16px; font-weight: 600; padding: 7px 10px; }}"
        )
        self.channel_selector.currentIndexChanged.connect(self._on_channel_selector_changed)
        row.addWidget(self.channel_selector)

        explanation = QLabel(
            "The selection is shared with the Magnetic Field page. PID, Manual, Sequence, and the separate GA tab use the same controller context."
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet(f"color: {T.muted}; font-size: 12px;")
        row.addWidget(explanation, 1)
        return card

    def _build_readout_card(self) -> QWidget:
        card = Card(radius=9)
        grid = QGridLayout(card)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setHorizontalSpacing(0)

        headings = [
            ("SELECTED CHANNEL", T.muted),
            ("SETPOINT", T.cyan),
            ("ACTUAL", T.orange),
            ("ERROR", T.amber),
        ]
        displays = []
        for logical_col, (text, color) in enumerate(headings):
            col = logical_col * 2
            heading = QLabel(text)
            heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
            heading.setStyleSheet(f"color: {color}; font-size: 12px;")
            grid.addWidget(heading, 0, col)

            display = QLabel("—")
            display.setAlignment(Qt.AlignmentFlag.AlignCenter)
            display.setStyleSheet(f"color: {color}; font-size: 25px; font-weight: 600;")
            grid.addWidget(display, 1, col)
            displays.append(display)
            grid.setColumnStretch(col, 1)

            if logical_col < len(headings) - 1:
                divider = QFrame()
                divider.setFrameShape(QFrame.Shape.VLine)
                divider.setStyleSheet(f"color: {T.border};")
                grid.addWidget(divider, 0, col + 1, 2, 1)

        (
            self.channel_display,
            self.setpoint_display,
            self.actual_display,
            self.error_display,
        ) = displays
        return card

    def _build_plot_card(self) -> QWidget:
        card = Card(radius=9)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 10, 8)
        title = QLabel("PID SETPOINT RESPONSE")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {T.muted}; font-size: 13px;")
        layout.addWidget(title)
        self.plot = TrendPlot(y_axis_label="PROCESS VALUE")
        self.plot.setMinimumHeight(220)
        layout.addWidget(self.plot)
        return card

    def _build_pid_card(self) -> QWidget:
        card = Card(radius=9)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        title = QLabel("PID PARAMETERS")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        self.setpoint_spin = self._double_spin(-9999.99, 9999.99, 2, 1.0)
        self.kp_spin = self._double_spin(0.0, 1_000_000.0, 6, 0.01)
        self.ki_spin = self._double_spin(0.0, 1_000_000.0, 6, 0.01)
        self.kd_spin = self._double_spin(0.0, 1_000_000.0, 6, 0.001)
        self.output_min_spin = self._double_spin(-9999.99, 9999.99, 3, 0.1)
        self.output_max_spin = self._double_spin(-9999.99, 9999.99, 3, 0.1)
        self.output_min_spin.setValue(-100.0)
        self.output_max_spin.setValue(100.0)
        self.integral_min_spin = self._double_spin(-9999.99, 9999.99, 3, 0.1)
        self.integral_max_spin = self._double_spin(-9999.99, 9999.99, 3, 0.1)
        self.integral_min_spin.setValue(-100.0)
        self.integral_max_spin.setValue(100.0)
        self.loop_period_spin = QSpinBox()
        self.loop_period_spin.setRange(10, 5000)
        self.loop_period_spin.setValue(100)
        self.loop_period_spin.setSuffix(" ms")
        self.derivative_tau_spin = self._double_spin(0.0, 10.0, 4, 0.01)
        self.derivative_tau_spin.setValue(0.05)

        fields = [
            ("Setpoint", self.setpoint_spin, 0, 0),
            ("Kp", self.kp_spin, 0, 2),
            ("Ki", self.ki_spin, 0, 4),
            ("Kd", self.kd_spin, 0, 6),
            ("Output Min", self.output_min_spin, 1, 0),
            ("Output Max", self.output_max_spin, 1, 2),
            ("Integral Min", self.integral_min_spin, 1, 4),
            ("Integral Max", self.integral_max_spin, 1, 6),
            ("Loop Period", self.loop_period_spin, 2, 0),
            ("Derivative Filter τ", self.derivative_tau_spin, 2, 2),
        ]
        for label_text, widget, row, col in fields:
            label = QLabel(label_text)
            label.setStyleSheet(f"color: {T.muted};")
            grid.addWidget(label, row, col)
            grid.addWidget(widget, row, col + 1)
        layout.addLayout(grid)

        component_grid = QGridLayout()
        self.output_display = self._component_display(T.cyan)
        self.p_display = self._component_display(T.text)
        self.i_display = self._component_display(T.text)
        self.d_display = self._component_display(T.text)
        for col, (name, display) in enumerate(
            (("OUTPUT", self.output_display), ("P", self.p_display), ("I", self.i_display), ("D", self.d_display))
        ):
            label = QLabel(name)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet(f"color: {T.muted}; font-size: 11px;")
            component_grid.addWidget(label, 0, col)
            component_grid.addWidget(display, 1, col)
        layout.addLayout(component_grid)

        controls = QHBoxLayout()
        self.load_target_button = QPushButton("LOAD TARGET AS SETPOINT")
        self.load_target_button.setStyleSheet(secondary_button_css())
        self.load_target_button.clicked.connect(self._load_target_as_setpoint)
        controls.addWidget(self.load_target_button)

        self.reset_button = QPushButton("RESET INTEGRATOR")
        self.reset_button.setStyleSheet(secondary_button_css())
        self.reset_button.clicked.connect(self.reset_pid)
        controls.addWidget(self.reset_button)
        controls.addStretch()

        self.arm_output_check = QCheckBox("ARM HARDWARE OUTPUT")
        self.arm_output_check.setToolTip(
            "Output is still blocked when no integration callback is connected."
        )
        controls.addWidget(self.arm_output_check)

        self.start_pid_button = QPushButton("START PID")
        self.start_pid_button.setStyleSheet(secondary_button_css(T.green))
        self.start_pid_button.clicked.connect(self.start_pid)
        controls.addWidget(self.start_pid_button)

        self.stop_pid_button = QPushButton("STOP PID")
        self.stop_pid_button.setStyleSheet(secondary_button_css(T.red))
        self.stop_pid_button.clicked.connect(self.stop_pid)
        controls.addWidget(self.stop_pid_button)
        layout.addLayout(controls)

        self.pid_status_label = QLabel("PID IDLE")
        self.pid_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pid_status_label.setStyleSheet(info_label_css())
        layout.addWidget(self.pid_status_label)
        return card

    def _build_ga_card(self) -> QWidget:
        card = Card(radius=9)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        title = QLabel("GENETIC ALGORITHM — PID GAIN SEARCH")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(title)

        note = QLabel(
            "The GA engine is connected, but plant evaluation is intentionally external. START GA creates a candidate; "
            "a future test runner should apply it, collect the response, and submit a lower-is-better fitness score. "
            "The development score input below lets you exercise that interface manually now."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {T.muted};")
        layout.addWidget(note)

        config_grid = QGridLayout()
        config_grid.setHorizontalSpacing(10)
        config_grid.setVerticalSpacing(7)

        self.population_spin = QSpinBox()
        self.population_spin.setRange(4, 500)
        self.population_spin.setValue(18)
        self.generations_spin = QSpinBox()
        self.generations_spin.setRange(1, 500)
        self.generations_spin.setValue(25)

        self.kp_min_spin = self._double_spin(0.0, 1_000_000.0, 6, 0.01)
        self.kp_max_spin = self._double_spin(0.0, 1_000_000.0, 6, 0.01)
        self.ki_min_spin = self._double_spin(0.0, 1_000_000.0, 6, 0.01)
        self.ki_max_spin = self._double_spin(0.0, 1_000_000.0, 6, 0.01)
        self.kd_min_spin = self._double_spin(0.0, 1_000_000.0, 6, 0.001)
        self.kd_max_spin = self._double_spin(0.0, 1_000_000.0, 6, 0.001)
        self.kp_max_spin.setValue(10.0)
        self.ki_max_spin.setValue(5.0)
        self.kd_max_spin.setValue(2.0)

        ga_fields = [
            ("Population", self.population_spin, 0, 0),
            ("Generations", self.generations_spin, 0, 2),
            ("Kp Min", self.kp_min_spin, 1, 0),
            ("Kp Max", self.kp_max_spin, 1, 2),
            ("Ki Min", self.ki_min_spin, 2, 0),
            ("Ki Max", self.ki_max_spin, 2, 2),
            ("Kd Min", self.kd_min_spin, 3, 0),
            ("Kd Max", self.kd_max_spin, 3, 2),
        ]
        for text, widget, row, col in ga_fields:
            label = QLabel(text)
            label.setStyleSheet(f"color: {T.muted};")
            config_grid.addWidget(label, row, col)
            config_grid.addWidget(widget, row, col + 1)
        layout.addLayout(config_grid)

        progress_grid = QGridLayout()
        self.ga_generation_display = self._component_display(T.cyan)
        self.ga_candidate_display = self._component_display(T.cyan)
        self.ga_current_display = self._component_display(T.text)
        self.ga_best_display = self._component_display(T.green)
        progress_items = [
            ("GENERATION", self.ga_generation_display),
            ("CANDIDATE", self.ga_candidate_display),
            ("CURRENT GAINS", self.ga_current_display),
            ("BEST", self.ga_best_display),
        ]
        for col, (text, display) in enumerate(progress_items):
            label = QLabel(text)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet(f"color: {T.muted}; font-size: 11px;")
            progress_grid.addWidget(label, 0, col)
            progress_grid.addWidget(display, 1, col)
        layout.addLayout(progress_grid)

        controls = QHBoxLayout()
        self.start_ga_button = QPushButton("START GA")
        self.start_ga_button.setStyleSheet(secondary_button_css(T.green))
        self.start_ga_button.clicked.connect(self.start_ga)
        controls.addWidget(self.start_ga_button)

        self.abort_ga_button = QPushButton("ABORT GA")
        self.abort_ga_button.setStyleSheet(secondary_button_css(T.red))
        self.abort_ga_button.clicked.connect(self.abort_ga)
        controls.addWidget(self.abort_ga_button)

        controls.addStretch()
        score_label = QLabel("Development Fitness")
        score_label.setStyleSheet(f"color: {T.muted};")
        controls.addWidget(score_label)
        self.fitness_spin = self._double_spin(0.0, 1.0e12, 6, 1.0)
        self.fitness_spin.setValue(100.0)
        controls.addWidget(self.fitness_spin)

        self.submit_fitness_button = QPushButton("SUBMIT SCORE")
        self.submit_fitness_button.setStyleSheet(secondary_button_css(T.amber))
        self.submit_fitness_button.clicked.connect(
            lambda: self.submit_candidate_fitness(self.fitness_spin.value())
        )
        controls.addWidget(self.submit_fitness_button)

        self.apply_best_button = QPushButton("APPLY BEST GAINS")
        self.apply_best_button.setStyleSheet(secondary_button_css(T.cyan))
        self.apply_best_button.clicked.connect(self.apply_best_gains)
        controls.addWidget(self.apply_best_button)
        layout.addLayout(controls)

        self.ga_status_label = QLabel("GA IDLE")
        self.ga_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ga_status_label.setStyleSheet(info_label_css())
        layout.addWidget(self.ga_status_label)
        return card

    @staticmethod
    def _double_spin(
        minimum: float,
        maximum: float,
        decimals: int,
        step: float,
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(float(minimum), float(maximum))
        spin.setDecimals(int(decimals))
        spin.setSingleStep(float(step))
        spin.setKeyboardTracking(False)
        return spin

    @staticmethod
    def _component_display(color: str) -> QLabel:
        label = QLabel("—")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumHeight(34)
        label.setStyleSheet(
            f"color: {color}; background: #0E151A; border: 1px solid #27323A; "
            "border-radius: 6px; padding: 5px; font-size: 13px; font-weight: 600;"
        )
        return label

    # ------------------------------------------------------------ Context
    def _connect_context(self) -> None:
        self.context.selected_channel_changed.connect(self._on_channel_changed)
        self.context.target_changed.connect(self._on_target_changed)
        self.context.actual_values_changed.connect(lambda _values: self._refresh_live_readouts())
        self.context.mode_changed.connect(self._on_mode_changed)

    def _on_channel_selector_changed(self, index: int) -> None:
        if index < 0 or index == self.context.selected_index:
            return
        self.context.set_selected_index(index)

    def _on_channel_changed(self, _index: int) -> None:
        if self._pid_running:
            self.stop_pid(reason="PID STOPPED — CHANNEL CHANGED")
        self._load_target_as_setpoint()
        self._reset_plot()
        self.refresh()

    def _on_target_changed(self, index: int, _value: float) -> None:
        if index == self.context.selected_index and not self._pid_running:
            self._refresh_live_readouts()

    def _on_mode_changed(self, mode: str) -> None:
        if self._pid_running and mode != ControlMode.PID.value:
            self.stop_pid(reason=f"PID STOPPED — MODE CHANGED TO {mode}", restore_manual=False)

    def refresh(self) -> None:
        if hasattr(self, "channel_selector"):
            self.channel_selector.blockSignals(True)
            self.channel_selector.setCurrentIndex(self.context.selected_index)
            self.channel_selector.blockSignals(False)
        self.channel_display.setText(self.context.channel_name())
        if not self._pid_running:
            self.setpoint_spin.setValue(self.context.target())
        self._refresh_live_readouts()
        self._refresh_ga_progress()

    def _load_target_as_setpoint(self) -> None:
        self.setpoint_spin.setValue(self.context.target())
        self._refresh_live_readouts()

    def _refresh_live_readouts(self) -> None:
        self.channel_display.setText(self.context.channel_name())
        self._refresh_ga_context()
        setpoint = self.setpoint_spin.value()
        actual = self.context.actual()
        self.setpoint_display.setText(f"{setpoint:.2f}")
        if math.isfinite(actual):
            self.actual_display.setText(f"{actual:.2f}")
            self.error_display.setText(f"{setpoint - actual:.2f}")
        else:
            self.actual_display.setText("—")
            self.error_display.setText("—")

    # --------------------------------------------------------------- PID
    def _pid_settings(self) -> tuple[PIDGains, PIDLimits]:
        gains = PIDGains(
            kp=self.kp_spin.value(),
            ki=self.ki_spin.value(),
            kd=self.kd_spin.value(),
        )
        limits = PIDLimits(
            output_min=self.output_min_spin.value(),
            output_max=self.output_max_spin.value(),
            integral_min=self.integral_min_spin.value(),
            integral_max=self.integral_max_spin.value(),
            derivative_filter_tau=self.derivative_tau_spin.value(),
        )
        return gains, limits

    def start_pid(self) -> None:
        actual = self.context.actual()
        if not math.isfinite(actual):
            self.pid_status_label.setText("PID NOT STARTED — ACTUAL VALUE IS NOT AVAILABLE")
            return
        try:
            gains, limits = self._pid_settings()
            self.pid.set_gains(gains)
            self.pid.set_limits(limits)
        except ValueError as exc:
            self.pid_status_label.setText(f"PID NOT STARTED — {exc}")
            return

        self._pid_channel = self.context.selected_index
        self.pid.reset(measurement=actual)
        self._last_pid_time = time.monotonic()
        self._pid_timer.setInterval(self.loop_period_spin.value())
        self._pid_running = True
        self.context.set_mode(ControlMode.PID)
        self._reset_plot()
        self._pid_timer.start()

        if self.arm_output_check.isChecked() and self.output_callback is None:
            self.pid_status_label.setText("PID PREVIEW ACTIVE — NO HARDWARE CALLBACK CONNECTED")
        elif self.arm_output_check.isChecked():
            self.pid_status_label.setText("PID ACTIVE — HARDWARE OUTPUT ARMED")
        else:
            self.pid_status_label.setText("PID PREVIEW ACTIVE — HARDWARE OUTPUT BLOCKED")

    def stop_pid(self, *, reason: str = "PID STOPPED", restore_manual: bool = True) -> None:
        self._pid_timer.stop()
        self._pid_running = False
        self.pid_status_label.setText(reason)
        if restore_manual and self.context.can_write(ControlMode.PID):
            self.context.set_mode(ControlMode.MANUAL)

    def reset_pid(self) -> None:
        actual = self.context.actual()
        self.pid.reset(measurement=actual if math.isfinite(actual) else None)
        self.output_display.setText("0.0000")
        self.p_display.setText("0.0000")
        self.i_display.setText("0.0000")
        self.d_display.setText("0.0000")
        self.pid_status_label.setText("PID INTEGRATOR RESET")

    def _pid_step(self) -> None:
        if not self._pid_running:
            return
        if not self.context.can_write(ControlMode.PID):
            self.stop_pid(reason="PID STOPPED — CONTROL MODE LOST", restore_manual=False)
            return
        if self.context.selected_index != self._pid_channel:
            self.stop_pid(reason="PID STOPPED — CHANNEL CHANGED")
            return

        now = time.monotonic()
        dt = now - self._last_pid_time
        self._last_pid_time = now
        actual = self.context.actual(self._pid_channel)
        if not math.isfinite(actual):
            self.stop_pid(reason="PID STOPPED — ACTUAL DATA LOST")
            return

        try:
            result = self.pid.update(self.setpoint_spin.value(), actual, dt)
        except ValueError as exc:
            self.stop_pid(reason=f"PID STOPPED — {exc}")
            return

        self.output_display.setText(f"{result.output:.4f}")
        self.p_display.setText(f"{result.proportional:.4f}")
        self.i_display.setText(f"{result.integral:.4f}")
        self.d_display.setText(f"{result.derivative:.4f}")
        self.error_display.setText(f"{result.error:.3f}")

        elapsed = now - self._plot_t0
        self._plot_t.append(elapsed)
        self._plot_setpoint.append(self.setpoint_spin.value())
        self._plot_actual.append(actual)
        self.plot.set_data(self._plot_t, self._plot_setpoint, self._plot_actual)

        self.pid_output_calculated.emit(self._pid_channel, result.output, result)

        if self.arm_output_check.isChecked() and self.output_callback is not None:
            try:
                accepted = self.output_callback(self._pid_channel, result.output, result)
            except Exception as exc:  # pragma: no cover - integration safety boundary
                self.stop_pid(reason=f"PID STOPPED — OUTPUT CALLBACK FAILED: {exc}")
                return
            if accepted is False:
                self.stop_pid(reason="PID STOPPED — OUTPUT CALLBACK REJECTED COMMAND")
                return
            self.pid_status_label.setText(
                "PID ACTIVE — OUTPUT ARMED" + (" — SATURATED" if result.saturated else "")
            )
        elif self.arm_output_check.isChecked():
            self.pid_status_label.setText("PID PREVIEW ACTIVE — NO HARDWARE CALLBACK CONNECTED")
        else:
            self.pid_status_label.setText(
                "PID PREVIEW ACTIVE" + (" — OUTPUT SATURATED" if result.saturated else "")
            )

    def _reset_plot(self) -> None:
        self._plot_t0 = time.monotonic()
        self._plot_t.clear()
        self._plot_setpoint.clear()
        self._plot_actual.clear()
        self.plot.clear()

    def _refresh_ga_context(self) -> None:
        if hasattr(self, "ga_channel_display"):
            self.ga_channel_display.setText(self.context.channel_name())
        if hasattr(self, "ga_seed_display") and all(
            hasattr(self, name) for name in ("kp_spin", "ki_spin", "kd_spin")
        ):
            self.ga_seed_display.setText(
                f"{self.kp_spin.value():.6g}, {self.ki_spin.value():.6g}, {self.kd_spin.value():.6g}"
            )

    # ---------------------------------------------------------------- GA
    def _reset_ga_plot(self) -> None:
        self._ga_evaluation_count = 0
        self._ga_evaluations.clear()
        self._ga_fitness_history.clear()
        self._ga_best_history.clear()
        if hasattr(self, "ga_plot"):
            self.ga_plot.clear()
        if hasattr(self, "ga_last_fitness_display"):
            self.ga_last_fitness_display.setText("—")
        if hasattr(self, "ga_best_fitness_plot_display"):
            self.ga_best_fitness_plot_display.setText("—")

    def _record_ga_fitness(self, fitness: float) -> None:
        score = float(fitness)
        if not math.isfinite(score):
            return

        self._ga_evaluation_count += 1
        self._ga_evaluations.append(float(self._ga_evaluation_count))
        self._ga_fitness_history.append(score)

        best_score = score
        if self.ga_tuner is not None:
            best = self.ga_tuner.best_candidate()
            if best is not None and best.fitness is not None:
                candidate_best = float(best.fitness)
                if math.isfinite(candidate_best):
                    best_score = candidate_best
        self._ga_best_history.append(best_score)

        self.ga_last_fitness_display.setText(f"{score:.6g}")
        self.ga_best_fitness_plot_display.setText(f"{best_score:.6g}")
        self.ga_plot.set_data(
            self._ga_evaluations,
            self._ga_best_history,
            self._ga_fitness_history,
        )

    def _ga_configuration(self) -> tuple[GainBounds, GATuningConfig]:
        bounds = GainBounds(
            kp=(self.kp_min_spin.value(), self.kp_max_spin.value()),
            ki=(self.ki_min_spin.value(), self.ki_max_spin.value()),
            kd=(self.kd_min_spin.value(), self.kd_max_spin.value()),
        ).validated()
        config = GATuningConfig(
            population_size=self.population_spin.value(),
            generations=self.generations_spin.value(),
        ).validated()
        return bounds, config

    def start_ga(self) -> None:
        if self._pid_running:
            self.stop_pid(reason="PID STOPPED — GA TUNING STARTED", restore_manual=False)
        try:
            bounds, config = self._ga_configuration()
            self.ga_tuner = GAPIDTuner(bounds=bounds, config=config)
            candidate = self.ga_tuner.initialize(
                PIDGains(self.kp_spin.value(), self.ki_spin.value(), self.kd_spin.value())
            )
        except (ValueError, RuntimeError) as exc:
            self.ga_status_label.setText(f"GA NOT STARTED — {exc}")
            return

        self._reset_ga_plot()
        self.context.set_mode(ControlMode.GA_TUNING)
        self._present_candidate(candidate)
        self.ga_status_label.setText(
            "GA READY — APPLY/TEST CURRENT CANDIDATE, THEN SUBMIT ITS FITNESS"
        )

    def abort_ga(self) -> None:
        self.ga_tuner = None
        self.ga_status_label.setText("GA ABORTED")
        self._refresh_ga_progress()
        if self.context.can_write(ControlMode.GA_TUNING):
            self.context.set_mode(ControlMode.MANUAL)

    def submit_candidate_fitness(self, fitness: float) -> None:
        if self.ga_tuner is None:
            self.ga_status_label.setText("START GA BEFORE SUBMITTING FITNESS")
            return
        try:
            next_candidate = self.ga_tuner.submit_fitness(float(fitness))
        except RuntimeError as exc:
            self.ga_status_label.setText(str(exc).upper())
            return

        self._record_ga_fitness(float(fitness))

        if self.ga_tuner.finished:
            best = self.ga_tuner.best_candidate()
            if best is None:
                self.ga_status_label.setText("GA FINISHED — NO VALID CANDIDATE")
            else:
                self.ga_status_label.setText(
                    f"GA COMPLETE — BEST FITNESS {float(best.fitness):.6g}"
                )
            self.context.set_mode(ControlMode.MANUAL)
            self._refresh_ga_progress()
            return

        if next_candidate is not None:
            self._present_candidate(next_candidate)
            self.ga_status_label.setText("NEXT CANDIDATE READY — AWAITING FITNESS")

    def _present_candidate(self, candidate: PIDCandidate) -> None:
        self.kp_spin.setValue(candidate.gains.kp)
        self.ki_spin.setValue(candidate.gains.ki)
        self.kd_spin.setValue(candidate.gains.kd)
        self._refresh_ga_progress()
        self.candidate_requested.emit(candidate)

    def apply_best_gains(self) -> None:
        if self.ga_tuner is None or self.ga_tuner.best_candidate() is None:
            self.ga_status_label.setText("NO EVALUATED GA CANDIDATE IS AVAILABLE")
            return
        best = self.ga_tuner.best_candidate()
        assert best is not None
        self.kp_spin.setValue(best.gains.kp)
        self.ki_spin.setValue(best.gains.ki)
        self.kd_spin.setValue(best.gains.kd)
        self.ga_status_label.setText(
            f"BEST GAINS LOADED — Kp={best.gains.kp:.6g}, Ki={best.gains.ki:.6g}, Kd={best.gains.kd:.6g}"
        )

    def _refresh_ga_progress(self) -> None:
        if self.ga_tuner is None:
            self.ga_generation_display.setText("—")
            self.ga_candidate_display.setText("—")
            self.ga_current_display.setText("—")
            self.ga_best_display.setText("—")
            return

        generation, generations, candidate, population = self.ga_tuner.progress()
        self.ga_generation_display.setText(f"{generation} / {generations}")
        self.ga_candidate_display.setText(f"{candidate} / {population}")
        try:
            current = self.ga_tuner.current_candidate()
            self.ga_current_display.setText(
                f"{current.gains.kp:.3g}, {current.gains.ki:.3g}, {current.gains.kd:.3g}"
            )
        except RuntimeError:
            self.ga_current_display.setText("COMPLETE")

        best = self.ga_tuner.best_candidate()
        if best is None:
            self.ga_best_display.setText("—")
        else:
            self.ga_best_display.setText(
                f"{float(best.fitness):.4g} | {best.gains.kp:.3g}, {best.gains.ki:.3g}, {best.gains.kd:.3g}"
            )


__all__ = ["PIDGAControlTab", "PIDOutputCallback"]
