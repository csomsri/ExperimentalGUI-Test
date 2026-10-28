"""Durable, linked BO experiment records independent of optional SQLite logging."""
import csv
import hashlib
import importlib.metadata
import json
import math
import platform
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


class ExperimentLog:
    def __init__(self, root, settings):
        self.run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ_') + uuid4().hex[:8]
        self.path = Path(root) / self.run_id
        self.path.mkdir(parents=True, exist_ok=False)
        self.trial_id = None
        self.error = ''
        self._decision_rows = {}
        self._calibration_rows = []
        versions = {'python': platform.python_version()}
        for name in ('torch', 'botorch', 'gpytorch', 'PyQt6'):
            try:
                versions[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                versions[name] = None
        source = Path(__file__).resolve().parents[1]
        hashes = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in [source / 'pid_controller.py', source / 'pid_ga_control_tab.py',
                            source / 'pid_beam_buffer.py', source / 'ga_test_runner.py',
                            source / 'pid_decision_log.py', source / 'beam_cal.py',
                            Path(__file__), *Path(__file__).parent.glob('*.py'),
                            *Path(__file__).parent.joinpath('optimization').glob('*.py')]}
        self.json('run_settings.json', dict(settings, run_id=self.run_id,
                  schema_version=2, versions=versions, source_sha256=hashes))
        self.event('START')

    def json(self, filename, record):
        target = self.path / filename
        temporary = target.with_suffix(target.suffix + '.tmp')
        temporary.write_text(json.dumps(clean(record), indent=2, allow_nan=False), encoding='utf-8')
        temporary.replace(target)

    def event(self, event, **data):
        if event == 'TRIAL_START':
            self.trial_started_epoch = time.time()
        with (self.path / 'events.jsonl').open('a', encoding='utf-8') as handle:
            record = dict(event=event, run_id=self.run_id, trial_id=self.trial_id,
                          utc=datetime.now(timezone.utc).isoformat(), monotonic_s=time.monotonic())
            record.update(data)
            handle.write(json.dumps(clean(record), allow_nan=False) + '\n')

    def result(self, result, tuner):
        record = asdict(result)
        record.pop('samples')
        record['phase'] = result.phase.value
        record.update(run_id=self.run_id, trial_id=self.trial_id,
                      proposal_source=tuner.proposal_source,
                      proposed_gains=tuner.proposed_gains,
                      valid=result.phase.value == 'COMPLETE' and math.isfinite(result.score))
        record['valid'] = record['valid'] and not result.safety_violation
        record['gp_statistics'] = {}
        decision = self._decision_rows.get(self.trial_id)
        if decision is not None:
            from .optimization.decision_statistics import comparison
            statistics = comparison(decision, result.score, record['valid'])
            decision.update(statistics, measured_cost=result.score, valid=record['valid'])
            record['gp_statistics'] = dict(decision)
            self._write_decisions()
            if statistics:
                self._calibration_rows.append(dict(run_id=self.run_id, trial_id=self.trial_id,
                    **statistics))
                count = len(self._calibration_rows)
                errors = [r['prediction_error'] for r in self._calibration_rows]
                self._write_csv('gp_calibration.csv', [dict(r,
                    running_count=i + 1,
                    running_coverage_95=sum(x['inside_predictive_95'] for x in self._calibration_rows[:i+1]) / (i+1),
                    running_rmse=math.sqrt(sum(x['prediction_error']**2 for x in self._calibration_rows[:i+1]) / (i+1)))
                    for i, r in enumerate(self._calibration_rows)])
                self.event('GP_CALIBRATION', evaluated_predictions=count,
                    coverage_95=sum(r['inside_predictive_95'] for r in self._calibration_rows)/count,
                    rmse=math.sqrt(sum(e*e for e in errors)/count))
        response = self.path / f'trial_{result.candidate_number:03d}_response.csv'
        fields = ['run_id', 'trial_id', 'utc_estimate', 'phase', *result.samples[0].__dataclass_fields__] if result.samples else []
        if fields:
            with response.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for sample in result.samples:
                    writer.writerow(dict(asdict(sample), run_id=self.run_id, trial_id=self.trial_id,
                        utc_estimate=datetime.fromtimestamp(
                            getattr(self, 'trial_started_epoch', time.time()) + sample.elapsed_s,
                            timezone.utc).isoformat(), phase='SCORING' if sample.scoring else 'WARMUP'))
        # JSON keeps nested score terms and proposed gains losslessly.
        flat = {k: json.dumps(clean(v)) if isinstance(v, dict) else v for k, v in record.items()}
        target = self.path / 'trials.csv'
        exists = target.exists()
        with target.open('a', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(flat))
            if not exists:
                writer.writeheader()
            writer.writerow(clean(flat))
        self.event('TRIAL_COMPLETE', **record)

    def _write_csv(self, filename, rows):
        if not rows:
            return
        target = self.path / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix('.csv.tmp')
        fields = list(dict.fromkeys(key for row in rows for key in row))
        with temporary.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: json.dumps(clean(v)) if isinstance(v, (dict, list)) else clean(v)
                                 for k, v in row.items()})
        temporary.replace(target)

    def _write_decisions(self):
        self._write_csv('gp_decisions.csv', list(self._decision_rows.values()))

    def decision(self, tuner, candidate):
        """Persist the pre-trial prediction before any measured result exists."""
        number = candidate.index + 1
        diagnostics = tuner.decisions[candidate.index]
        prediction = dict((diagnostics.get('selected') or [{}])[0])
        row = dict(run_id=self.run_id, trial_id=self.trial_id, trial=number,
                   source=diagnostics['source'], training_count=diagnostics.get('training_count', 0),
                   statistics_available=diagnostics.get('statistics_available', False),
                   unavailable_reason=diagnostics.get('unavailable_reason'),
                   diagnostics_error=diagnostics.get('diagnostics_error'),
                   proposed_gains=tuner.proposed_gains, applied_gains=asdict(candidate.gains),
                   prediction_basis='proposed_gains; PID fields may round gains to six decimal places',
                   **prediction)
        self._decision_rows[self.trial_id] = row
        self._write_decisions()
        if diagnostics.get('statistics_available'):
            models = self.path / 'gp_models'
            models.mkdir(exist_ok=True)
            self.json(f'gp_models/model_{number:03d}.json', dict(diagnostics,
                run_id=self.run_id, trial_id=self.trial_id,
                training_trial_ids=[f'{self.run_id}:{d["trial_index"]:03d}'
                    for d in diagnostics.get('training_data', []) if d.get('trial_index') is not None]))
            self._write_csv(f'gp_candidates/decision_{number:03d}.csv',
                [dict(r, run_id=self.run_id, trial_id=self.trial_id)
                 for r in diagnostics.get('candidates', [])])
        self.event('GP_DECISION', **row)
