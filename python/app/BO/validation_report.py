"""Independent-repeat summaries and scientific figures from retained trial records."""
import csv
import json
import math
from statistics import mean, stdev
from pathlib import Path


def validation_summary(tuner):
    groups = {'reference': [], 'validation': []}
    for index, analysis in sorted(tuner.analyses.items()):
        events = analysis.get('responsiveness', {}).get('events', [])
        maes = [e['mae_nA'] for e in events if e.get('mae_nA') is not None]
        groups.setdefault(analysis.get('role', 'unknown'), []).append(dict(
            trial=index+1, repeat=analysis.get('repeat'), accepted=analysis.get('accepted', False), condition=analysis.get('condition'),
            mae_nA=mean(maes) if maes else None, initial=analysis.get('initial')))
    statistics = {}
    for role, rows in groups.items():
        values = [r['mae_nA'] for r in rows if r['mae_nA'] is not None]
        statistics[role] = dict(repetitions=len(rows), measured_repetitions=len(values),
            mean_mae_nA=mean(values) if values else None,
            pooled_condition_and_repeat_sd_nA=stdev(values) if len(values)>1 else None,
            pooled_standard_error_nA=stdev(values)/math.sqrt(len(values)) if len(values)>1 else None,
            rows=rows)
    drift_checks = {}
    limit = getattr(tuner, 'reference_drift_limit_nA', 0.2)
    for condition in ('low','nominal','high'):
        refs = [mean([e['mae_nA'] for e in a.get('responsiveness',{}).get('events',[]) if e.get('mae_nA') is not None])
                for a in tuner.analyses.values() if a.get('role')=='reference' and a.get('condition')==condition
                and any(e.get('mae_nA') is not None for e in a.get('responsiveness',{}).get('events',[]))]
        drift_checks[condition] = dict(repeats=len(refs), mae_range_nA=max(refs)-min(refs) if len(refs)>1 else None)
    expected = len(tuner.schedule)
    passed = (len(tuner.analyses)==expected
              and all(row['repeats']>=2 and row['mae_range_nA']<=limit for row in drift_checks.values())
              and all(a.get('accepted', False) for a in tuner.analyses.values()))
    by_condition = {}
    for condition in ('low','nominal','high'):
        by_condition[condition] = {}
        for role, rows in groups.items():
            values = [r['mae_nA'] for r in rows if r.get('condition') == condition and r['mae_nA'] is not None]
            by_condition[condition][role] = dict(n=len(values),mean_mae_nA=mean(values) if values else None,
                between_repeat_sd_nA=stdev(values) if len(values)>1 else None,
                between_repeat_standard_error_nA=stdev(values)/math.sqrt(len(values)) if len(values)>1 else None)
    return dict(version='paired-validation-v1', by_condition=by_condition, passed=passed,
        expected_trials=expected, groups=statistics, reference_drift_by_condition=drift_checks,
        reference_drift_limit_nA=limit,
        reasons=[] if passed else ['Missing/failed/insufficient repetitions or reference drift exceeds the configured limit'],
        uncertainty_note='Between-repeat sample SD and standard error; separate from within-trial sample variation and GP predictive intervals.')


def export_figures(log):
    """Render three figure groups from durable CSVs without importing GUI backends."""
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    root = Path(log.path)
    paths = sorted(root.glob('trial_*_response.csv'))
    responses = []
    for path in paths:
        with path.open(newline='', encoding='utf-8') as handle:
            rows = list(csv.DictReader(handle))
        if rows:
            responses.append((path.stem, rows))
    figure = Figure(figsize=(10, 6), layout='constrained')
    FigureCanvasAgg(figure)
    axes = figure.subplots(2, 1, sharex=True)
    for name, rows in responses:
        t = [float(r['elapsed_s']) for r in rows]
        axes[0].plot(t,[float(r['beam_nA']) for r in rows],label=name)
        axes[0].step(t,[float(r['setpoint_nA']) for r in rows],where='post',alpha=.4)
        axes[1].plot(t,[float(r['tc_target_a']) for r in rows],label=name)
    axes[0].set(ylabel='Beam / setpoint (nA)',title='Measured responsiveness (setpoint traces included)')
    axes[1].set(xlabel='Trial time (s)',ylabel='TC command (A)')
    if responses:
        axes[0].legend(fontsize=6,ncol=2)
    figure.savefig(root/'responsiveness.png',dpi=160)
    with (root/'trials.csv').open(newline='',encoding='utf-8') as handle:
        trials = list(csv.DictReader(handle))
    figure = Figure(figsize=(10,4),layout='constrained'); FigureCanvasAgg(figure)
    ax = figure.subplots()
    costs = [float(r['score']) for r in trials]
    ax.plot(range(1,len(costs)+1),costs,'o-')
    for i,row in enumerate(trials):
        analysis = json.loads(row.get('analysis') or '{}')
        ax.annotate(f"{row.get('role','candidate')}: {analysis.get('status','unknown')}",
                    (i+1,costs[i]),fontsize=7,rotation=25)
    ax.set(xlabel='Trial number',ylabel='Original five-term cost',title='Measured cost; tracking acceptance is separate')
    figure.savefig(root/'measured_performance.png',dpi=160)
    figure = Figure(figsize=(10,4),layout='constrained'); FigureCanvasAgg(figure)
    ax = figure.subplots()
    predictions = root/'gp_decisions.csv'
    count=0
    if predictions.exists():
        with predictions.open(newline='',encoding='utf-8') as handle:
            for row in csv.DictReader(handle):
                if row.get('predicted_cost') and row.get('measured_cost') and row.get('predictive_low'):
                    predicted=float(row['predicted_cost']); measured=float(row['measured_cost'])
                    ax.errorbar(measured,predicted,yerr=[[max(0,predicted-float(row['predictive_low']))],
                        [max(0,float(row['predictive_high'])-predicted)]],fmt='o')
                    count+=1
    if not count:
        ax.text(.5,.5,'No prospective GP predictions in this fixed-gain validation run',ha='center',transform=ax.transAxes)
    ax.set(xlabel='Measured cost',ylabel='Pretrial predicted cost / predictive 95%',title='Prospective calibration; excludes drift uncertainty')
    figure.savefig(root/'uncertainty_calibration.png',dpi=160)
