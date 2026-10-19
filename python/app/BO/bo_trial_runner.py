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
