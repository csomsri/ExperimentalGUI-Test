"""Operator controls for BO using the shared PID and automatic trial runner."""
import importlib.util
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox,
    QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView, QDoubleSpinBox, QComboBox,
)
from .bo_pid_adapter import BOPIDAdapter


class BOControlTab(QWidget):
    def __init__(self, pid_tab, parent=None):
        super().__init__(parent)
        self.pid_tab = pid_tab
        self.session = None
        self.validation = False
        self.validated = False
        self.best = None
        self.settings = None
        self.was_running = False
        layout = QVBoxLayout(self)
        intro = QLabel(
            'BO + PID\nBayesian optimization tunes Kp, Ki and Kd through the existing PID. '
            'Configure the TC, beam setpoint, output limits and timing on the PID page. '
            'Gain bounds, warm-up, trial duration and scoring weights come from the GA settings. '
            'The first trial measures your current gains; later trials use Sobol initialization '
            'and Gaussian-process expected improvement. Lower measured scores are better.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        row = QHBoxLayout()
        row.addWidget(QLabel('Trial budget'))
        self.budget = QSpinBox()
        self.budget.setRange(2, 500)
        self.budget.setValue(20)
        row.addWidget(self.budget)
        self.arm = QCheckBox('Arm BO trials using PID output')
        row.addWidget(self.arm)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.start = QPushButton('Start BO')
        self.stop = QPushButton('Stop / Restore')
        self.validate = QPushButton('Validate best gains (60 s minimum)')
        self.apply = QPushButton('Apply validated gains to PID')
        for button, callback in ((self.start, self._start), (self.stop, self._stop),
                                 (self.validate, self._validate), (self.apply, self._apply)):
            row.addWidget(button)
            button.clicked.connect(callback)
        layout.addLayout(row)
        self.status = QLabel('Ready. Arm output on the PID page, then arm BO here.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.history = QTableWidget(0, 5)
        self.history.setHorizontalHeaderLabels(['Trial', 'Kp', 'Ki', 'Kd', 'Measured score'])
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.history.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.history)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(200)
        self._refresh()

    def _fingerprint(self):
        p = self.pid_tab
        # Any controller/evaluation setting change requires fresh validation.
        values = tuple((w.objectName(), w.value()) for w in p.findChildren(
            QDoubleSpinBox) if w not in (p.kp_spin, p.ki_spin, p.kd_spin))
        ints = tuple(w.value() for w in p.findChildren(QSpinBox))
        combos = tuple(w.currentText() for w in p.findChildren(QComboBox))
        return (p._actuator_index, p._pending_actuator_index, values, ints, combos,
                p.auto_baseline_check.isChecked())

    def _launch(self, validation=False):
        p = self.pid_tab
        if p._ga_auto_active or p._pid_running:
            self.status.setText('Stop the active PID or tuning run before starting BO.')
            return
        if not self.arm.isChecked() or not p.arm_output_check.isChecked():
            self.status.setText('Arm PID output and BO trials before starting.')
            return
        missing = [name for name in ('torch', 'botorch', 'gpytorch')
                   if importlib.util.find_spec(name) is None]
        if missing:
            self.status.setText('BO dependencies missing: ' + ', '.join(missing)
                                + '. Install BO/requirements.txt in the application Python environment.')
            return
        best = self.best
        def factory(bounds, **kwargs):
            tuner = BOPIDAdapter(bounds, budget=1 if validation else self.budget.value())
            tuner.validation = validation
            if validation:
                initialize = tuner.initialize
                tuner.initialize = lambda seed: initialize(best.gains)
            return tuner
        p._automatic_tuner_factory = factory
        p.auto_ga_arm_check.setChecked(True)
        self.validated = False
        self.validation = validation
        old_duration = p.ga_evaluation_spin.value()
        if validation:
            p.ga_evaluation_spin.setValue(max(60.0, old_duration))
        try:
            p._start_automatic_ga()
        finally:
            del p._automatic_tuner_factory
        if p._ga_auto_active:
            self.session = p.ga_tuner
            self.was_running = True
            self.settings = self._fingerprint()
            self.original_duration = old_duration
            if not validation:
                self.best = None
            self.status.setText('BO validation running.' if validation else 'BO search running.')
        else:
            if validation:
                p.ga_evaluation_spin.setValue(old_duration)
            if getattr(p.ga_tuner, 'is_bo', False):
                p.ga_tuner.close()
            self.status.setText(p.ga_status_label.text())
            p.auto_ga_arm_check.setChecked(False)

    def _start(self):
        self._launch()

    def _stop(self):
        if (self.session is self.pid_tab.ga_tuner and self.pid_tab._ga_auto_active
                and not self.pid_tab._ga_abort_after_restore):
            self.pid_tab.abort_ga()

    def _validate(self):
        if self.best is not None:
            if self.settings != self._fingerprint():
                self.status.setText('Settings changed since search. Restore the search settings or run BO again.')
                return
            self._launch(validation=True)

    def _apply(self):
        if self.validated and self.settings == self._fingerprint() and self.best is not None:
            p = self.pid_tab
            if p._pid_running or p._ga_auto_active:
                return
            for spin, value in zip((p.kp_spin, p.ki_spin, p.kd_spin),
                                   (self.best.gains.kp, self.best.gains.ki, self.best.gains.kd)):
                spin.setValue(value)
            self.status.setText('Validated gains loaded into PID. PID remains stopped.')

    def _refresh(self):
        p = self.pid_tab
        active = p._ga_auto_active
        own_active = active and p.ga_tuner is self.session
        if own_active and not self.arm.isChecked():
            self._stop()
        if self.session is not None:
            records = self.session.population
            self.history.setRowCount(len(records))
            for row, candidate in enumerate(records):
                values = (row + 1, candidate.gains.kp, candidate.gains.ki,
                          candidate.gains.kd, candidate.fitness)
                for col, value in enumerate(values):
                    self.history.setItem(row, col, QTableWidgetItem(
                        'Pending' if value is None else f'{value:.6g}'))
        if own_active:
            self.status.setText(('BO validation — ' if self.validation else 'BO search — ')
                                + p.ga_status_label.text())
        elif self.was_running:
            self.was_running = False
            complete = p._ga_sequence_state == 'COMPLETE' and p.ga_tuner is self.session
            if self.validation:
                result = self.session.best_candidate()
                self.validated = bool(complete and result and self.settings == self._fingerprint()
                                      and result.fitness <= max(self.best.fitness * 1.2, 1e-9))
                p.ga_evaluation_spin.setValue(self.original_duration)
                self.settings = self._fingerprint()
                self.status.setText('Validation passed; gains may be applied.' if self.validated
                                    else 'Validation failed or interrupted; gains were not approved.')
            else:
                self.best = self.session.best_candidate() if complete else None
                self.status.setText('BO complete. Re-arm PID output and BO to validate best gains.'
                                    if self.best else 'BO stopped: ' + p.ga_status_label.text())
            self.arm.setChecked(False)
        if self.validated and (self.settings != self._fingerprint() or active or p._pid_running):
            self.validated = False
            self.status.setText('Settings changed; validate best gains again before applying.')
        self.start.setEnabled(not active and not p._pid_running)
        self.stop.setEnabled(own_active)
        self.budget.setEnabled(not active)
        self.validate.setEnabled(not active and not p._pid_running and self.best is not None)
        self.apply.setEnabled(not active and not p._pid_running and self.validated)
