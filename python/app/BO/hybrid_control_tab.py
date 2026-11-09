"""Hybrid GA + BO workspace using the shared Python PID execution path."""
from dataclasses import fields
from PyQt6.QtWidgets import QLabel, QComboBox, QGridLayout, QSpinBox, QDoubleSpinBox, QWidget
from .bo_control_tab import BOControlTab
from .hybrid_pid_adapter import HybridPIDAdapter
from .optimization.rolling_hybrid_optimizer import RollingHybridConfig
from .optimization.hybrid_pid_optimizer import HybridConfig


class HybridControlTab(BOControlTab):
    def __init__(self, pid_tab, parent=None):
        self.hybrid_editors = {}
        super().__init__(pid_tab, parent)
        self.layout().itemAt(0).widget().setText(
            'Hybrid GA + BO PID\nGA explores PID gains; BO learns from measured responses. '
            'Legacy uses measured paired handover; windowed modes refine GA parents with '
            'Lamarckian, Baldwinian or mixed inheritance. All trials run through the Python PID. '
            'Trials use the same checks, target restoration and apply workflow as GA autotune.')
        self.start.setText('Start hybrid')
        self.arm.setText('Arm hybrid trials using PID output')
        self.budget.setRange(6,500)
        self.budget.setValue(48)
        self.initial_trials.hide()
        panel = QWidget()
        grid = QGridLayout(panel)
        self.approach = QComboBox()
        for label, value in [('GA → BO handover','legacy'), ('Lamarckian','lamarckian'),
                             ('Baldwinian','baldwinian'), ('Mixed','mixed')]:
            self.approach.addItem(label,value)
        grid.addWidget(QLabel('BO learning'),0,0)
        grid.addWidget(self.approach,0,1)
        defaults = RollingHybridConfig()
        for i, field in enumerate(fields(defaults)):
            if field.name in ('learning_mode','budget','seed','reference_interval','drift_fraction'):
                continue
            value = getattr(defaults,field.name)
            editor = QSpinBox() if isinstance(value,int) else QDoubleSpinBox()
            if isinstance(value,int):
                editor.setRange(1,10000)
            else:
                editor.setDecimals(4)
                editor.setRange(0,10 if field.name == 'uncertainty_multiplier' else 1)
            editor.setValue(value)
            self.hybrid_editors[field.name] = editor
            row, col = 1+i//3, (i%3)*2
            grid.addWidget(QLabel(field.name.replace('_',' ').capitalize()),row,col)
            grid.addWidget(editor,row,col+1)
        self.layout().insertWidget(4,panel)
        self.approach.currentIndexChanged.connect(self._refresh)
        self._refresh()

    def _fingerprint(self):
        base = super()._fingerprint()
        if not hasattr(self,'approach'):
            return base
        return base + (self.approach.currentData(), self.budget.value(), self.seed.value(),
            tuple((name, editor.value()) for name,editor in self.hybrid_editors.items()))

    def _create_search_adapter(self, bounds):
        return HybridPIDAdapter(bounds, approach=self.approach.currentData(),
            budget=self.budget.value(), seed=self.seed.value(), reference_interval=self.budget.value()+1,
            **{name:editor.value() for name,editor in self.hybrid_editors.items()})

    def _launch(self):
        from .optimization.hybrid_pid_optimizer import HybridConfig
        from .optimization.rolling_hybrid_optimizer import RollingHybridConfig
        settings = {name:e.value() for name,e in self.hybrid_editors.items()}
        settings.update(budget=self.budget.value(),seed=self.seed.value())
        try:
            if self.approach.currentData() == 'legacy':
                HybridConfig(**{k:v for k,v in settings.items() if k in HybridConfig.__dataclass_fields__}).validate()
            else:
                RollingHybridConfig(**settings,learning_mode=self.approach.currentData()).validate()
            for name in ('kp','ki','kd'):
                lo,hi = (self.editors[name+suffix].value() for suffix in ('_min_spin','_max_spin'))
                gain = getattr(self.pid_tab,name+'_spin').value()
                if lo < 0 or hi <= lo or not lo <= gain <= hi:
                    raise ValueError('Use positive-width nonnegative bounds containing the current PID gains')
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        super()._launch()
        if self.session is not None and getattr(self.session,'is_hybrid',False):
            self.session.interleave_reference = False

    def _refresh(self):
        super()._refresh()
        if not hasattr(self,'approach'):
            return
        active = self.pid_tab._ga_auto_active
        self.approach.setEnabled(not active)
        rolling = self.approach.currentData() != 'legacy'
        window_names = set(RollingHybridConfig.__dataclass_fields__) - set(HybridConfig.__dataclass_fields__)
        for name,editor in self.hybrid_editors.items():
            relevant = rolling if name in window_names else not rolling if name in ('confirmation_pairs','plateau_trials') else True
            editor.setEnabled(not active and relevant)
        if self.session is not None and getattr(self.session,'is_hybrid',False) and self.session.policy:
            self.status.setText(self.status.text().split(' | ')[0].replace('BO search','Hybrid search').replace('BO finished','Hybrid finished')
                + ' | ' + str(self.session.policy.state) + ': ' + self.session.policy.reason)
