"""Pure step-response analysis. Seconds, beam nA; right-endpoint time integration.

Unknown settling is None. Each event is clipped at the next event. Missing
coverage takes precedence over performance failure. No objective is changed.
"""
from dataclasses import dataclass, asdict
import math
from bisect import bisect_left, bisect_right

VERSION = 'beam-responsiveness-v1'


@dataclass(frozen=True)
class TrackingCriteria:
    error_band_nA: float = 0.2
    hold_time_s: float = 2.0
    max_settling_time_s: float = 20.0
    max_overshoot_nA: float = 0.5
    max_gap_s: float = 1.0

    def __post_init__(self):
        for name, value in asdict(self).items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be finite and positive')


def square_wave_setpoint(time_s, center_nA, amplitude_nA, half_period_s):
    values = (time_s, center_nA, amplitude_nA, half_period_s)
    if not all(math.isfinite(v) for v in values) or time_s < 0 or amplitude_nA <= 0 or half_period_s <= 0:
        raise ValueError('Invalid square-wave settings')
    return center_nA + amplitude_nA * (2 * (int(time_s // half_period_s) % 2) - 1)


def _finite(values, name):
    rows = [float(v) for v in values]
    if not all(math.isfinite(v) for v in rows):
        raise ValueError(f'Nonfinite {name}')
    return rows


def evaluate_responsiveness(time_s, beam_nA, setpoint_nA, event_times_s,
                            error_band_nA, hold_time_s, max_settling_time_s,
                            max_overshoot_nA, *, actuator_samples=None,
                            max_gap_s=1.0, gain_samples=None):
    criteria = TrackingCriteria(error_band_nA, hold_time_s, max_settling_time_s,
                                max_overshoot_nA, max_gap_s)
    t, y, r, events = (_finite(v, name) for v, name in (
        (time_s, 'timestamps'), (beam_nA, 'beam'), (setpoint_nA, 'setpoints'),
        (event_times_s, 'events')))
    if len(t) != len(y) or len(t) != len(r):
        raise ValueError('Sample arrays must have equal lengths')
    if any(b <= a for a, b in zip(t, t[1:])) or any(b <= a for a, b in zip(events, events[1:])):
        raise ValueError('Timestamps and events must be strictly increasing')
    actuator = None if actuator_samples is None else _finite(actuator_samples, 'actuator')
    if actuator is not None and len(actuator) != len(t):
        raise ValueError('Actuator samples must align with beam samples')
    if gain_samples is not None:
        gains = [tuple(_finite(g, 'gains')) for g in gain_samples]
        if len(gains) != len(t) or any(g != gains[0] for g in gains):
            raise ValueError('Fixed gains required throughout validation')
    result = dict(version=VERSION, status='INSUFFICIENT_DATA', reasons=[],
                  criteria=asdict(criteria), events=[])
    if len(t) < 2 or not events:
        result['reasons'] = ['At least two samples and one event are required']
        return result
    # Every observed target change must have a matching event since the previous sample.
    for i in range(1, len(t)):
        if r[i] != r[i-1] and not any(t[i-1] < e <= t[i] for e in events):
            raise ValueError('Unaccounted setpoint change')
    for k, event in enumerate(events):
        boundary = events[k+1] if k+1 < len(events) else t[-1]
        end = min(boundary, t[-1])
        first = bisect_left(t, event)
        last = bisect_left(t, boundary) if k+1 < len(events) else len(t)
        indices = list(range(first, last))
        report = dict(event_time_s=event, target_nA=None, direction=None,
                      status='INSUFFICIENT_DATA', reasons=[], starting_in_band=False,
                      first_entry_s=None, settling_time_s=None, hold_completed_s=None,
                      overshoot_nA=None, final_signed_error_nA=None, mae_nA=None,
                      rmse_nA=None, band_occupancy=None, actuator_movement_a=None,
                      observed_duration_s=max(0, end-event), demonstrates_recovery=False)
        result['events'].append(report)
        if not indices or event < t[0] - max_gap_s or event > t[-1]:
            report['reasons'].append('Event lacks recorded coverage')
            continue
        target = r[first]
        if any(r[i] != target for i in indices):
            raise ValueError('Multiple targets within one event segment')
        previous = r[first-1] if first else None
        direction = (1 if target > previous else -1) if previous is not None and target != previous else (1 if target > y[first] else -1)
        report.update(target_nA=target, direction='UP' if direction > 0 else 'DOWN')
        errors = [target-y[i] for i in indices]
        inside = [abs(e) <= error_band_nA for e in errors]
        report['starting_in_band'] = inside[0]
        report['first_entry_s'] = next((t[i]-event for i, ok in zip(indices, inside) if ok), None)
        report['overshoot_nA'] = max(0.0, max(direction*(y[i]-target) for i in indices))
        report['final_signed_error_nA'] = errors[-1]
        # Integrate with the current sample over the interval ending at that sample.
        duration = absolute = squared = occupancy = 0.0
        cursor = event
        largest_gap = max(0.0, t[first]-event)
        run_start = None
        for i, error, ok in zip(indices, errors, inside):
            dt = max(0.0, t[i]-cursor)
            largest_gap = max(largest_gap, dt)
            duration += dt
            absolute += abs(error)*dt
            squared += error*error*dt
            occupancy += int(ok)*dt
            cursor = t[i]
            if ok:
                if run_start is None:
                    run_start = t[i]
                if report['settling_time_s'] is None and t[i]-run_start >= hold_time_s:
                    report['settling_time_s'] = run_start-event
                    report['hold_completed_s'] = t[i]-event
            else:
                run_start = None
                # Settling requires a qualifying final continuous in-band hold;
                # an early visit must not survive a later excursion.
                report['settling_time_s'] = None
                report['hold_completed_s'] = None
        largest_gap = max(largest_gap, max(0.0, end-t[indices[-1]]))
        if duration:
            report.update(mae_nA=absolute/duration, rmse_nA=math.sqrt(squared/duration),
                          band_occupancy=occupancy/duration)
        if actuator is not None:
            report['actuator_movement_a'] = sum(abs(actuator[b]-actuator[a]) for a,b in zip(indices, indices[1:]))
        sufficient = end-event >= max_settling_time_s + hold_time_s and len(indices) >= 2 and largest_gap <= max_gap_s
        if not sufficient:
            report['reasons'].append('Short observation window or logging gap exceeds the configured limit')
            continue
        report['status'] = 'PASS'
        if report['settling_time_s'] is None or report['settling_time_s'] > max_settling_time_s:
            report['status'] = 'FAIL'
            report['reasons'].append('Tracking band was not held within the settling deadline')
        if report['overshoot_nA'] > max_overshoot_nA:
            report['status'] = 'FAIL'
            report['reasons'].append('Overshoot limit exceeded')
        if abs(report['final_signed_error_nA']) > error_band_nA:
            report['status'] = 'FAIL'
            report['reasons'].append('Final response is outside the tracking band')
        report['demonstrates_recovery'] = report['status'] == 'PASS' and not report['starting_in_band']
    statuses = [e['status'] for e in result['events']]
    result['status'] = ('INSUFFICIENT_DATA' if 'INSUFFICIENT_DATA' in statuses else
                        'FAIL' if 'FAIL' in statuses else 'PASS')
    result['reasons'] = [f"Event {i+1}: {reason}" for i, row in enumerate(result['events']) for reason in row['reasons']]
    return result


def analyze_candidate(result, evaluator, criteria):
    """Reproduce original score; keep data validity distinct from tracking acceptance.

    Finite performance failures remain GP training observations under the original
    objective. Only passing, data-valid trials are eligible for best selection.
    """
    from .trial_metrics import evaluate_trial
    valid = result.phase.value == 'COMPLETE' and not result.safety_violation and math.isfinite(result.score)
    rows = [s for s in result.samples if s.scoring]
    report = dict(version=VERSION, data_valid=valid, status='INSUFFICIENT_DATA',
                  accepted=False, metrics=None, weighted_contributions={}, score_reproduced=None,
                  training_policy='finite-data-valid-including-performance-failures-v1')
    if len(rows) < 2:
        return report
    config, weights = evaluator.config, evaluator.weights
    # This is a constant-target search diagnostic, never waveform search fitness.
    metrics = evaluate_trial([(s.elapsed_s,s.beam_nA,s.error_nA,s.pid_delta_a,s.tc_target_a,int(s.saturated))
                              for s in result.samples], evaluator.setpoint_nA,
                             baseline_target=evaluator.baseline_target_a,
                             warmup_seconds=config.warmup_s,
                             evaluation_config=config, fitness_weights=weights)
    response = evaluate_responsiveness([s.elapsed_s for s in rows], [s.beam_nA for s in rows],
        [s.setpoint_nA for s in rows], [config.warmup_s], **asdict(criteria),
        actuator_samples=[s.tc_target_a for s in rows])
    contributions = {name: getattr(weights,name)*getattr(result.terms,name)
                     for name in ('tracking','steady_state','movement','saturation','oscillation')}
    contributions['safety'] = result.terms.safety
    report.update(status=response['status'], accepted=valid and response['status']=='PASS',
                  metrics=asdict(metrics), responsiveness=response,
                  weighted_contributions=contributions, score_reproduced=sum(contributions.values()),
                  score_matches=math.isclose(sum(contributions.values()), result.score, rel_tol=1e-9, abs_tol=1e-9))
    return report
