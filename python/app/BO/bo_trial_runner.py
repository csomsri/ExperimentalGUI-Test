"""Additional measured recovery gate around the shared automatic trial runner."""
import math
import time


def recovery_ready(page):
    """Require three seconds of fresh beam and measured TC near the baseline."""
    settings = getattr(getattr(page, 'ga_tuner', None), 'recovery', {})
    if not settings.get('enabled', True):
        return True
    now = time.monotonic()
    reference = page._ga_recovery_reference
    key = page._ga_restore_started
    if getattr(page, '_bo_recovery_key', None) != key:
        page._bo_recovery_key = key
        page._bo_stable_since = None
    snapshot = page._beam_window_snapshot()
    beam = float(snapshot.get('value_nA', float('nan')))
    index = page._actuator_index
    actual = page.context.actual(index)
    stable = (bool(snapshot.get('valid')) and math.isfinite(beam)
              and math.isfinite(actual)
              and abs(actual - reference.actual_map[index]) <= settings.get('tc_tolerance_a', 0.1)
              and abs(beam - reference.beam_nA) <= max(
                  abs(reference.beam_nA) * settings.get('beam_relative', 0.05), settings.get('beam_floor_na', 0.1)))
    if not stable:
        page._bo_stable_since = None
    elif page._bo_stable_since is None:
        page._bo_stable_since = now
    if page._bo_stable_since is not None and now - page._bo_stable_since >= settings.get('stable_s', 3.0):
        return True
    if now - key > settings.get('timeout_s', 60.0):
        raise RuntimeError('BO baseline recovery timed out (beam / measured TC not stable)')
    return False

class PhysicalReadiness:
    """A bounded measured-state hold, distinct from pretrial data collection."""
    def __init__(self, settings, reference, now):
        self.settings = dict(settings)
        self.reference = reference
        self.started = now
        self.stable_since = None
        self.actual_stable_since = None
        self.last_timestamp = None
        self.last_beam = None

    def poll(self, now, snapshot, actual_a):
        if now - self.started >= self.settings['timeout_s']:
            raise RuntimeError('PHYSICAL READINESS TIMEOUT: measured beam / TC not stable')
        beam = float(snapshot.get('value_nA', float('nan')))
        stamp = snapshot.get('timestamp')
        actual_stamp = float(snapshot.get('actual_timestamp_s', 0.0))
        first_stamp = snapshot.get('receive_first_s', stamp)
        ready = (math.isfinite(actual_stamp) and actual_stamp >= self.started
                 and 0 <= now-actual_stamp <= self.settings['max_gap_s']
                 and all(snapshot.get(k, False) for k in ('valid','fresh','ready'))
                 and math.isfinite(beam) and math.isfinite(actual_a)
                 and self.settings['beam_min_na'] <= beam <= self.settings['beam_max_na']
                 and abs(actual_a - self.reference) <= self.settings['tc_tolerance_a'])
        if not ready:
            self.stable_since = None
            self.actual_stable_since = None
            return False
        # A cached average must never establish a continuous measured hold.
        if stamp is None or not math.isfinite(float(stamp)):
            self.stable_since = None
            self.actual_stable_since = None
            return False
        if float(stamp) < self.started or first_stamp is None or float(first_stamp) < self.started:
            self.stable_since = None
            self.actual_stable_since = None
            return False
        if self.last_timestamp is not None and stamp <= self.last_timestamp:
            return False
        if self.last_timestamp is not None and stamp-self.last_timestamp > self.settings['max_gap_s']:
            self.stable_since = None
            self.actual_stable_since = None
        self.last_timestamp = stamp
        self.last_beam = beam
        if self.stable_since is None:
            self.stable_since = stamp
        if self.actual_stable_since is None:
            self.actual_stable_since = actual_stamp
        return (stamp-self.stable_since >= self.settings['stable_s']
                and actual_stamp-self.actual_stable_since >= self.settings['stable_s'])


def candidate_readiness(page, candidate, snapshot):
    """Return only when fresh post-reset measurements satisfy configured limits."""
    tuner = page.ga_tuner
    settings = tuner.recovery
    if not settings.get('enabled', False):
        return True
    gate = getattr(page, '_bo_readiness', None)
    key = (id(tuner), candidate.index)
    if getattr(page, '_bo_readiness_key', None) != key:
        page._bo_readiness_key = key
        page._bo_readiness = gate = PhysicalReadiness(settings,
            page._ga_recovery_reference.actual_map[page._actuator_index], time.monotonic())
        page._bo_event('READINESS_START', candidate=candidate.index+1, settings=settings)
    now = time.monotonic()
    snapshot = dict(snapshot, actual_timestamp_s=page.context.actual_timestamps[page._actuator_index])
    if gate.poll(now, snapshot, page.context.actual(page._actuator_index)):
        page._bo_event('READINESS_COMPLETE', duration_s=now-gate.started,
                       beam_nA=gate.last_beam, measured_tc_a=page.context.actual(page._actuator_index))
        return True
    page._bo_ready_candidate = candidate
    page._ga_sequence_state = 'READINESS'
    page.ga_status_label.setText(f'PHYSICAL READINESS — {now-gate.started:.1f} / {settings["timeout_s"]:.1f} s')
    page._ga_sequence_timer.start()
    return False
