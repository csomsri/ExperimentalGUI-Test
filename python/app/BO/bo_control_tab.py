"""Operator controls for BO using the shared PID and automatic trial runner."""
import importlib.util
import time
import math
from dataclasses import asdict
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox,
    QGridLayout, QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView, QDoubleSpinBox, QComboBox,
)
from .bo_pid_adapter import BOPIDAdapter, FixedGainValidationAdapter
from .responsiveness import TrackingCriteria
from controller_theme import Card, T, secondary_button_css


class BOControlTab(QWidget):
    def __init__(self, pid_tab, parent=None):
        super().__init__(parent)
        self.pid_tab = pid_tab
        self.session = None
        self.validation = False
        self.validated = False
        self.best = None
        self.pilot_passed = False
        self.pilot_settings = None
        self.settings = None
        self.was_running = False
        layout = QVBoxLayout(self)
        self.saved_ga_settings = None
        self.search_run_id = None
        self.last_telemetry = 0.0
        self.figure_future = None
        self.figure_log = None
        self._build_workspace(layout)
        intro = QLabel(
            'BO + PID\nBayesian optimization tunes Kp, Ki and Kd through the existing PID. '
            'Configure the TC, beam setpoint, output limits and timing on the PID page. '
            'Gain bounds, timing, scoring and measured readiness are configured below. '
            'The first trial measures your current gains; later trials use Sobol initialization '
            'and Gaussian-process expected improvement. Tracking acceptance precedes cost ranking. '
            'Approve reachable targets and measured readiness limits before running. '
        )
        intro.setWordWrap(True)
        intro.setStyleSheet('font-size: 15px;')
        layout.insertWidget(0, intro)
        row = QHBoxLayout()
        row.addWidget(QLabel('Trial budget'))
        self.budget = QSpinBox()
        self.budget.setRange(2, 500)
        self.budget.setValue(20)
        row.addWidget(self.budget)
        self.arm = QCheckBox('Arm BO trials using PID output')
        row.addWidget(self.arm)
        layout.insertLayout(1,row)
        row = QGridLayout()
        self.start = QPushButton('Start BO')
        self.pilot = QPushButton('Pilot current gains / reference')
        self.stop = QPushButton('Stop / Restore')
        self.validate = QPushButton('Validate best: square-wave setpoint')
        self.apply = QPushButton('Apply validated gains to PID')
        for index, (button, callback) in enumerate(((self.start, self._start), (self.pilot, self._pilot), (self.stop, self._stop),
                                 (self.validate, self._validate), (self.apply, self._apply))):
            row.addWidget(button,index//3,index%3)
            button.clicked.connect(callback)
            button.setStyleSheet(secondary_button_css())
        layout.insertLayout(2,row)
        self.status = QLabel('Ready. Arm output on the PID page, then arm BO here.')
        self.status.setWordWrap(True)
        layout.insertWidget(3,self.status)
        self.history = QTableWidget(0, 15)
        self.history.setHorizontalHeaderLabels(['Trial', 'Source', 'Kp', 'Ki', 'Kd', 'Cost', 'Valid', 'Reason',
            'GP predicted cost', 'GP SD', 'Predictive 95%', 'P(improve)', 'Tracking', 'MAE (nA)', 'Band occupancy'])
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.history.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.history)
        self.history.itemSelectionChanged.connect(self._refresh_statistics)
        self.statistics_detail = QLabel('Select a trial to inspect its GP decision statistics.')
        self.statistics_detail.setWordWrap(True)
        layout.addWidget(self.statistics_detail)
        from .gp_statistics_widget import GPStatisticsPlot
        self.prediction_plot = GPStatisticsPlot()
        self.candidate_plot = GPStatisticsPlot()
        layout.addWidget(self.prediction_plot)
        layout.addWidget(self.candidate_plot)
        for editor in (pid_tab.setpoint_spin, pid_tab.tc_min_spin, pid_tab.tc_max_spin):
            editor.valueChanged.connect(lambda _: self.limits_approved.setChecked(False)
                if not self.pid_tab._ga_auto_active else None)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(200)
        self._refresh()

    def _fingerprint(self):
        p = self.pid_tab
        from beam_cal import beam_cal
        # Track whether independent validation still matches the current settings.
        shared = tuple((name, getattr(p, name).value()) for name in (
            'setpoint_spin', 'tc_min_spin', 'tc_max_spin', 'loop_period_spin',
            'beam_average_spin', 'response_wait_spin', 'deadband_spin', 'derivative_tau_spin',
            'beam_stale_spin', 'auto_baseline_hold_spin'))
        return (tuple((name, editor.value()) for name, editor in self.testing.items()),
                self.interleave.isChecked(), p._actuator_index, p._pending_actuator_index, shared,
                tuple(e.value() for e in self.editors.values()), self.recovery_scope.currentData(),
                p.initial_direction_combo.currentData(), p.auto_baseline_check.isChecked(),
                getattr(getattr(p, '_controller_beam_input', None), 'mode', None),
                repr(beam_cal.cfg),
                p._beam_window_snapshot().get('range_index'))

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
                                + '. Install requirements-optimization.txt in the application Python environment.')
            return
        if not self.limits_approved.isChecked():
            self.status.setText('Confirm the operating, readiness and performance limits before running.')
            return
        values = {name: editor.value() for name, editor in self.testing.items()}
        if values['beam_min_na'] >= values['beam_max_na']:
            self.status.setText('Readiness beam minimum must be below its maximum.')
            return
        if values['timeout_s'] <= values['stable_s']:
            self.status.setText('Readiness timeout must exceed its measured hold duration.')
            return
        criteria = TrackingCriteria(**{name: values[name] for name in asdict(TrackingCriteria())})
        if validation and values['half_period_s']*(int(values['transitions'])+1) > p.ga_evaluation_spin.maximum():
            self.status.setText('Waveform duration exceeds the supported trial duration; reduce holds or transitions.')
            return
        if validation and values['half_period_s'] < criteria.max_settling_time_s + criteria.hold_time_s + criteria.max_gap_s:
            self.status.setText('Each waveform hold must exceed settling deadline + hold duration + maximum sample gap.')
            return
        if not validation and self.editors['ga_evaluation_spin'].value() < criteria.max_settling_time_s + criteria.hold_time_s:
            self.status.setText('Scoring time must cover the tracking settling deadline and hold duration.')
            return
        if (not validation and self.budget.value()>2 and
                (not self.pilot_passed or self.pilot_settings != self._fingerprint())):
            self.status.setText('Run the current-gain/reference pilot first, or use a two-trial pilot search and validate its result before a full BO search.')
            return
        best = self.best
        if not validation:
            from pid_controller import PIDGains
            self.reference_gains = PIDGains(*(values['reference_'+name] for name in ('kp','ki','kd')))
        def factory(bounds, **kwargs):
            if validation:
                tuner = FixedGainValidationAdapter(bounds, self.reference_gains, best.gains,
                    repeats=int(values['repeats']), seed=self.seed.value(),
                    condition_offset_nA=values['condition_offset_nA'])
            else:
                tuner = self._create_search_adapter(bounds)
            tuner.reference_override = self.reference_gains
            tuner.parent_search_run_id = self.search_run_id if validation else None
            tuner.criteria = criteria
            tuner.interleave_reference = self.interleave.isChecked()
            tuner.recovery = {name: values[name] for name in
                ('beam_min_na','beam_max_na','tc_tolerance_a','stable_s','timeout_s','max_gap_s')}
            tuner.recovery['enabled'] = True
            tuner.validation = validation
            tuner.reference_drift_limit_nA = values['reference_drift_limit_nA']
            tuner.waveform = dict(center_nA=p.setpoint_spin.value(), amplitude_nA=values['amplitude_nA'],
                half_period_s=values['half_period_s'], transitions=int(values['transitions']))
            return tuner
        self.saved_ga_settings = {name: getattr(p, name).value() for name in self.editors}
        self.saved_scope = p.ga_recovery_scope_combo.currentIndex()
        for name, editor in self.editors.items():
            getattr(p, name).setValue(editor.value())
        p.ga_recovery_scope_combo.setCurrentIndex(self.recovery_scope.currentIndex())
        p._automatic_tuner_factory = factory
        p.auto_ga_arm_check.setChecked(True)
        self.validated = False
        self.validation = validation
        old_duration = p.ga_evaluation_spin.value()
        self.old_warmup = p.ga_warmup_spin.value()
        if validation:
            p.ga_warmup_spin.setValue(0)
            p.ga_evaluation_spin.setValue(values['half_period_s']*(int(values['transitions'])+1))
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
            self._restore_ga_settings()

    def _create_search_adapter(self, bounds):
        return BOPIDAdapter(bounds, budget=self.budget.value(),
            initial_safe_trials=self.initial_trials.value(), seed=self.seed.value())

    def _pilot(self):
        from pid_controller import PIDGains
        from ga_pid_tuner import PIDCandidate
        p = self.pid_tab
        if p._ga_auto_active or p._pid_running:
            return
        if not self.arm.isChecked() or not p.arm_output_check.isChecked():
            self.status.setText('Arm PID output and BO trials before starting a pilot.')
            return
        self.best = PIDCandidate(PIDGains(p.kp_spin.value(), p.ki_spin.value(), p.kd_spin.value()), fitness=0)
        self.reference_gains = PIDGains(*(self.testing['reference_'+name].value() for name in ('kp','ki','kd')))
        self._launch(validation=True)

    def _start(self):
        self._launch()

    def _stop(self):
        if (self.session is self.pid_tab.ga_tuner and self.pid_tab._ga_auto_active
                and not self.pid_tab._ga_abort_after_restore):
            self.pid_tab.abort_ga()

    def _validate(self):
        if self.best is not None:
            self._launch(validation=True)

    def _apply(self):
        if self.best is not None and self.validated and self.settings == self._fingerprint():
            p = self.pid_tab
            if p._pid_running or p._ga_auto_active:
                return
            p._bo_event('GAINS_APPLIED', gains=dict(kp=self.best.gains.kp, ki=self.best.gains.ki, kd=self.best.gains.kd))
            for spin, value in zip((p.kp_spin, p.ki_spin, p.kd_spin),
                                   (self.best.gains.kp, self.best.gains.ki, self.best.gains.kd)):
                spin.setValue(value)
            self.status.setText('Best gains loaded into PID. PID remains stopped.')

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
                record = self.session.results[row] if row < len(self.session.results) else {}
                decision = self.session.decisions[row] if row < len(self.session.decisions) else {}
                stats = (decision.get('selected') or [{}])[0]
                interval = (f"[{stats['predictive_low']:.4g}, {stats['predictive_high']:.4g}]"
                            if stats else '—')
                values = (row + 1, decision.get('source', 'baseline'),
                          candidate.gains.kp, candidate.gains.ki, candidate.gains.kd,
                          record.get('cost', candidate.fitness), record.get('valid', 'Pending'), record.get('reason', ''),
                          stats.get('predicted_cost', '—'), stats.get('posterior_sd', '—'), interval,
                          f"{stats['probability_improvement']:.1%}" if stats else '—')
                for col, value in enumerate(values):
                    self.history.setItem(row, col, QTableWidgetItem(
                        'Pending' if value is None else (f'{value:.6g}' if isinstance(value, (float, int)) else str(value))))
            self._refresh_statistics()
        if own_active:
            self.status.setText(('BO validation — ' if self.validation else 'BO search — ')
                                + p.ga_status_label.text().replace('AUTOMATIC GA', 'BO')
                                  .replace('AUTO GA', 'BO').replace('GA CANDIDATE', 'BO TRIAL'))
        elif self.was_running:
            self.was_running = False
            complete = p._ga_sequence_state == 'COMPLETE' and p.ga_tuner is self.session
            if self.validation:
                analyses = self.session.analyses
                expected = len(self.session.schedule)
                self.validated = bool(complete and len(analyses) == expected
                    and all(a.get('accepted', False) for a in analyses.values())
                    and self.settings == self._fingerprint())
                p.ga_evaluation_spin.setValue(self.original_duration)
                p.ga_warmup_spin.setValue(self.old_warmup)
                self.settings = self._fingerprint()
                self.status.setText('Paired waveform validation passed; gains may be applied.' if self.validated
                                    else 'Waveform validation failed, incomplete or interrupted; inspect exported metrics.')
            else:
                self.best = self.session.best_candidate()
                self.status.setText('BO finished. Re-arm and run paired waveform validation before applying gains.'
                                    if self.best else 'No candidate met tracking acceptance. ' + p.ga_status_label.text())
            self._restore_ga_settings()
            self.settings = self._fingerprint()
            if not self.validation and complete:
                self.search_run_id = getattr(getattr(p, '_bo_log', None), 'run_id', None)
            if self.validation:
                from .validation_report import validation_summary
                summary = validation_summary(self.session)
                self.validated = self.validated and summary['passed']
                log = getattr(p, '_bo_log', None)
                try:
                    if log is None:
                        raise OSError('No validation log')
                    log.json('validation_summary.json', summary)
                    recorded = p._bo_event('VALIDATION_RESULT', passed=self.validated,
                        parent_search_run_id=self.search_run_id, summary=summary)
                except (OSError, ValueError) as exc:
                    recorded = False
                    self.status.setText('Validation export failed: ' + str(exc))
                self.validated = self.validated and recorded and summary['passed']
                self.pilot_passed = self.validated
                self.pilot_settings = self._fingerprint() if self.pilot_passed else None
                if not summary['passed']:
                    self.status.setText('Validation blocked: reference drift or failed / insufficient repetitions. Inspect validation_summary.json.')
            log = getattr(p, '_bo_log', None)
            if log is not None and (log.path / 'trials.csv').exists():
                from concurrent.futures import ThreadPoolExecutor
                from .validation_report import export_figures
                executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='BO-figures')
                self.figure_log = log
                self.figure_future = executor.submit(export_figures, log)
                executor.shutdown(wait=False)
            self.arm.setChecked(False)
        if self.figure_future is not None and self.figure_future.done():
            error = self.figure_future.exception()
            self.figure_future = None
            if error is not None and self.figure_log is not None:
                self.figure_log.error = 'Figure export: ' + str(error)
        if self.validated and (not self.limits_approved.isChecked() or self.settings != self._fingerprint() or active or p._pid_running):
            self.validated = False
            self.status.setText('Settings changed since validation. Best gains remain available.')
        total = 6*int(self.testing['repeats'].value())
        seconds = total*self.testing['half_period_s'].value()*(int(self.testing['transitions'].value())+1)
        center = p.setpoint_spin.value()
        amplitude = self.testing['amplitude_nA'].value()
        offset = self.testing['condition_offset_nA'].value()
        self.validation_budget_label.setText(f'Validation: {total} fixed-gain trials, {seconds/60:.1f} scored minutes plus readiness. '
            f'Target extremes: {center-offset-amplitude:.6g} to {center+offset+amplitude:.6g} nA.')
        self.start.setEnabled(not active and not p._pid_running)
        self.pilot.setEnabled(not active and not p._pid_running)
        self.stop.setEnabled(own_active)
        self.budget.setEnabled(not active)
        for editor in (*self.editors.values(), *self.recovery.values(), self.initial_trials, self.seed, self.recovery_scope, *self.testing.values(), self.interleave, self.limits_approved):
            editor.setEnabled(not active)
        self.context_label.setText(f'{p.context.channel_name(p._actuator_index)} | Setpoint {p.setpoint_spin.value():.6g} nA | PID gains {p.kp_spin.value():.6g}, {p.ki_spin.value():.6g}, {p.kd_spin.value():.6g}')
        self._refresh_plots(own_active)
        if own_active and time.monotonic() - self.last_telemetry >= 1.0:
            self.last_telemetry = time.monotonic()
            p._bo_event('TELEMETRY', phase=p._ga_sequence_state,
                    evaluation_phase=p.ga_evaluator.phase.value,
                    beam=p._beam_window_snapshot(), tc_target_A=p.context.target(p._actuator_index),
                    tc_readback_A=p.context.actual(p._actuator_index))
        log = getattr(p, '_bo_log', None)
        self.logging.setText('Logging: ' + (str(log.path) + ('  -  ERROR: ' + log.error if log.error else '  -  OK') if log else 'ready; required for validation'))
        self.validate.setEnabled(not active and not p._pid_running and self.best is not None)
        self.apply.setEnabled(not active and not p._pid_running and self.best is not None and self.validated)

    def _restore_ga_settings(self):
        if self.saved_ga_settings is not None:
            for name, value in self.saved_ga_settings.items():
                getattr(self.pid_tab, name).setValue(value)
            self.pid_tab.ga_recovery_scope_combo.setCurrentIndex(self.saved_scope)
            self.saved_ga_settings = None

    def _refresh_statistics(self):
        if self.session is None or not hasattr(self, 'statistics_detail'):
            return
        from .optimization.decision_statistics import comparison
        rows = self.session.decisions
        index = self.history.currentRow()
        index = index if 0 <= index < len(rows) else len(rows) - 1
        if index < 0:
            return
        selected = rows[index]
        stats = (selected.get('selected') or [{}])[0]
        points = []
        for i, decision in enumerate(rows):
            prediction = (decision.get('selected') or [{}])[0]
            if not prediction:
                continue
            candidate = self.session.population[i]
            observed = candidate.fitness if candidate.valid and candidate.evaluated else None
            points.append((i+1, prediction['predicted_cost'], prediction['predictive_low'],
                           prediction['predictive_high'], observed, True))
        self.prediction_plot.set_points('Pre-trial prediction (cyan), measured cost (orange ×), 95% predictive intervals',
                                       'Trial number · cost in original units', points)
        samples = selected.get('candidates', [])
        self.candidate_plot.set_points('Candidate comparison: GP mean and 95% credible intervals; chosen point cyan',
            'Kp projection; Ki and Kd vary across samples (not a fixed-gain slice)',
            [(r['values'][0], r['predicted_cost'], r['credible_low'], r['credible_high'], None, r['selected']) for r in samples])
        if not stats:
            self.statistics_detail.setText(f"Trial {index+1}: " + selected.get('diagnostics_error',
                selected.get('unavailable_reason', 'GP statistics unavailable.')))
            return
        candidate = self.session.population[index]
        result = comparison(stats, candidate.fitness, candidate.valid) if candidate.evaluated else {}
        suffix = (f" Prediction error {result['prediction_error']:.4g}; inside predictive interval: "
                  f"{result['inside_predictive_95']}." if result else '')
        self.statistics_detail.setText(
            f"Trial {index+1} · {selected.get('training_count')} valid training trials · "
            f"predicted cost {stats['predicted_cost']:.5g}, GP SD {stats['posterior_sd']:.4g}; "
            f"95% credible interval [{stats['credible_low']:.4g}, {stats['credible_high']:.4g}]; "
            f"predictive SD {stats['predictive_sd']:.4g}; learned noise variance {stats['learned_noise_variance']:.4g}. "
            f"P(improve) {stats['probability_improvement']:.1%}, expected improvement {stats['expected_improvement']:.4g}; "
            f"qLogEI {stats['acquisition_log_ei']:.4g} (not a probability), reference cost {stats['reference_cost']:.4g}. "
            'Predictions use proposed gains; PID rounds to six decimals. Intervals condition on the fitted GP; '
            'drift and calibration errors are not included.' + suffix)

    def _build_workspace(self, layout):
        from pid_ga_control_tab import TrendPlot
        self.context_label = QLabel()
        layout.addWidget(self.context_label)
        card = Card(radius=9)
        plots = QGridLayout(card)
        self.plots = []
        for i, (title, axis) in enumerate((('Beam / setpoint', 'BEAM (nA)'),
                ('Absolute error / deadband', 'ERROR (nA)'), ('TC target / readback', 'TC (A)'),
                ('Best tracking-accepted / measured cost', 'COST'))):
            plot = TrendPlot(y_axis_label=axis, x_axis_label='TRIAL' if i == 3 else 'TIME (s)', allow_missing_target=i == 3)
            plot.setMinimumHeight(180)
            plots.addWidget(QLabel(title), (i // 2) * 2, i % 2)
            plots.addWidget(plot, (i // 2) * 2 + 1, i % 2)
            self.plots.append(plot)
        layout.addWidget(card)
        card = Card(radius=9)
        grid = QGridLayout(card)
        self.editors = {}
        names = [('Kp min', 'kp_min_spin'), ('Kp max', 'kp_max_spin'), ('Ki min', 'ki_min_spin'),
                 ('Ki max', 'ki_max_spin'), ('Kd min', 'kd_min_spin'), ('Kd max', 'kd_max_spin'),
                 ('Warm-up (s)', 'ga_warmup_spin'), ('Scoring (s)', 'ga_evaluation_spin'),
                 ('Steady window (s)', 'ga_steady_window_spin'), ('Tracking weight', 'ga_w_track_spin'),
                 ('Steady weight', 'ga_w_ss_spin'), ('Movement weight', 'ga_w_move_spin'),
                 ('Saturation weight', 'ga_w_sat_spin'), ('Oscillation weight', 'ga_w_osc_spin')]
        for i, (label, name) in enumerate(names):
            original = getattr(self.pid_tab, name)
            editor = QDoubleSpinBox()
            editor.setDecimals(original.decimals())
            editor.setRange(original.minimum(), original.maximum())
            editor.setSingleStep(original.singleStep())
            editor.setValue(original.value())
            if name in ('kp_max_spin', 'ki_max_spin', 'kd_max_spin') and editor.value() == 0:
                editor.setValue(max(0.000001, 2 * getattr(self.pid_tab, name[:2] + '_spin').value()))
            self.pid_tab._style_compact_pid_editor(editor)
            self.editors[name] = editor
            grid.addWidget(QLabel(label), i // 2, (i % 2) * 2)
            grid.addWidget(editor, i // 2, (i % 2) * 2 + 1)
        self.recovery_scope = QComboBox()
        original = self.pid_tab.ga_recovery_scope_combo
        for i in range(original.count()):
            self.recovery_scope.addItem(original.itemText(i), original.itemData(i))
        self.recovery_scope.setCurrentIndex(original.currentIndex())
        self.initial_trials = QSpinBox()
        self.initial_trials.setRange(1, 100)
        self.initial_trials.setValue(6)
        self.seed = QSpinBox()
        self.seed.setRange(0, 2147483647)
        self.seed.setValue(1729)
        for row, label, editor in ((7, 'Recovery scope', self.recovery_scope),
                (8, 'Initial observations (includes baseline)', self.initial_trials), (9, 'Random seed', self.seed)):
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(editor, row, 1)
        self.recovery = {}
        layout.addWidget(card)
        testing_card = Card(radius=9)
        testing_grid = QGridLayout(testing_card)
        self.testing = {}
        options = [
            ('reference_kp','Fixed reference Kp',self.pid_tab.kp_spin.value(),0,10000),
            ('reference_ki','Fixed reference Ki',self.pid_tab.ki_spin.value(),0,10000),
            ('reference_kd','Fixed reference Kd',self.pid_tab.kd_spin.value(),0,10000),
            ('error_band_nA','Tracking error band (nA)',0.2,0.000001,10000),
            ('hold_time_s','Required tracking hold (s)',2,0.01,3600),
            ('max_settling_time_s','Settling deadline (s)',20,0.01,3600),
            ('max_overshoot_nA','Overshoot limit (nA)',0.5,0.000001,10000),
            ('max_gap_s','Maximum logging gap (s)',1,0.01,60),
            ('reference_drift_limit_nA','Reference MAE drift limit (nA)',0.2,0.000001,1000),
            ('beam_min_na','Starting beam minimum (nA)',0,-10000,10000),
            ('beam_max_na','Starting beam maximum (nA)',20,-10000,10000),
            ('tc_tolerance_a','Starting measured TC tolerance (A)',0.1,0.000001,100),
            ('stable_s','Measured readiness hold (s)',3,0.01,600),
            ('timeout_s','Readiness timeout (s)',60,0.1,3600),
            ('amplitude_nA','Validation target amplitude (nA)',0.5,0.000001,10000),
            ('half_period_s','Validation hold per level (s)',30,0.1,3600),
            ('transitions','Validation transitions',6,2,100),
            ('repeats','Paired validation repetitions per condition',3,2,30),
            ('condition_offset_nA','Low / high validation center offset (nA)',0.5,0.000001,10000)]
        for row, (name,label,value,minimum,maximum) in enumerate(options):
            editor = QDoubleSpinBox()
            editor.setDecimals(0 if name in ('transitions','repeats') else 6)
            editor.setRange(minimum,maximum)
            editor.setValue(value)
            self.testing[name] = editor
            testing_grid.addWidget(QLabel(label),row//2,(row%2)*2)
            testing_grid.addWidget(editor,row//2,(row%2)*2+1)
        self.interleave = QCheckBox('Interleave fixed reference trials (excluded from candidate budget and GP)')
        self.interleave.setChecked(True)
        testing_grid.addWidget(self.interleave,10,0,1,4)
        self.limits_approved = QCheckBox('I have checked reachable waveform levels, starting ranges, performance and actuator limits')
        testing_grid.addWidget(self.limits_approved,11,0,1,4)
        note = QLabel('Values above are editable examples. Validation changes the beam-current setpoint with fixed gains; PID computes coil commands. Each pair repeats the same waveform in seeded, varied order. Reference drift or any failed/insufficient response blocks application. Low, nominal and high centers are tested; the displayed budget is fixed before collection.')
        note.setWordWrap(True)
        testing_grid.addWidget(note,12,0,1,4)
        for editor in self.testing.values():
            editor.valueChanged.connect(lambda _: self.limits_approved.setChecked(False))
        self.validation_budget_label = QLabel()
        testing_grid.addWidget(self.validation_budget_label,13,0,1,4)
        layout.addWidget(testing_card)
        self.logging = QLabel()
        self.logging.setWordWrap(True)
        layout.addWidget(self.logging)

    def _refresh_plots(self, active):
        p = self.pid_tab
        if active and p._ga_live_t:
            for plot, target, actual in zip(self.plots[:3],
                    (p._ga_live_setpoint, p._ga_live_deadband, p._ga_live_tc_target),
                    (p._ga_live_beam, p._ga_live_abs_error, p._ga_live_tc_actual)):
                plot.set_data(p._ga_live_t, target, actual)
        if self.session:
            records = [c for c in self.session.population if c.fitness is not None]
            costs, bests, best = [], [], None
            for candidate in records:
                costs.append(candidate.fitness)
                if (candidate.valid and self.session.acceptance.get(candidate.index, False)
                        and self.session.decisions[candidate.index].get('source') != 'reference'):
                    best = candidate.fitness if best is None else min(best, candidate.fitness)
                bests.append(best)
            if costs:
                self.plots[3].set_data(list(range(1, len(costs) + 1)), bests, costs)
