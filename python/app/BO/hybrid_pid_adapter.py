"""Bridge the reference hybrid policies to the Python automatic PID runner."""
import math
from pid_controller import PIDGains
from .bo_pid_adapter import BOPIDAdapter
from .trial_metrics import TrialMetrics
from .optimization.pid_gain_adapter import PidGainCandidate, PidTrialResult
from .optimization.rolling_hybrid_optimizer import create_hybrid_optimizer
from .optimization.hybrid_pid_optimizer import HybridState


class HybridPIDAdapter(BOPIDAdapter):
    is_hybrid = True

    def __init__(self, bounds, *, approach='legacy', **settings):
        super().__init__(bounds, budget=settings.get('budget', 48), seed=settings.get('seed', 1729))
        self.bounds = bounds
        self.approach, self.hybrid_settings = approach, settings
        self.policy = None
        self.interleave_reference = False  # The hybrid owns reference scheduling and budgets.

    def initialize(self, seed_gains):
        bounds = tuple(getattr(self.bounds, n) for n in ('kp', 'ki', 'kd'))
        if any(lo < 0 or hi <= lo for lo, hi in bounds):
            raise ValueError('Hybrid gain bounds must be nonnegative with positive widths')
        self.policy = create_hybrid_optimizer(bounds, PidGainCandidate(
            seed_gains.kp, seed_gains.ki, seed_gains.kd), approach=self.approach, **self.hybrid_settings)
        self.policy.controller_kind = "python_nla"
        self.optimizer = self.policy.optimizer
        self.reference_gains = seed_gains
        return self._append_hybrid(self.policy.propose_batch(1)[0])

    def _append_hybrid(self, proposal):
        source = self.policy.pending_source
        self.role = 'reference' if source == 'Reference' else 'candidate'
        decision = dict(source=source, state=str(self.policy.state),
            training_count=len(self.optimizer.safe_observations), statistics_available=False,
            unavailable_reason=self.policy.reason)
        if self.policy.prediction:
            decision.update(predicted_cost=self.policy.prediction[0], posterior_sd=self.policy.prediction[1])
        return self._append_proposal(self._vector(PIDGains(proposal.kp, proposal.ki, proposal.kd)), decision)

    def submit_fitness(self, fitness, *, valid=True):
        candidate = self.current_candidate()
        if candidate.evaluated:
            raise RuntimeError('Hybrid trial result already submitted')
        analysis = self.analyses.get(candidate.index, {})
        metrics_data = analysis.get('metrics')
        metrics = TrialMetrics(**metrics_data) if metrics_data else None
        valid = bool(valid and math.isfinite(fitness) and metrics is not None)
        candidate.fitness = float(fitness) if math.isfinite(fitness) else 1e12
        candidate.valid = valid
        # Record the exact proposed vector: physical measurements never use GA selection fitness.
        result = PidTrialResult(self.policy.pending, candidate.fitness,
            metrics.settling_time if metrics else 0., metrics.overshoot if metrics else 0.,
            metrics.steady_state_error if metrics else 0., 0., valid, controller_kind=self.policy.controller_kind, metrics=metrics,
            termination_reason=analysis.get('status', 'Incomplete response'))
        self.policy.record_results([result])
        self.decisions[-1]['hybrid_record'] = dict(self.policy.records[-1])
        self.completed_candidates += 1
        self.finished = self.completed_candidates >= self.budget
        if self.policy.state == HybridState.STOPPED:
            self.best_overall = None
            raise RuntimeError(self.policy.reason)
        best = self.policy.best_result
        self.best_overall = None
        if best:
            matches = [c for c in self.population if c.valid and (not self.require_tracking or self.acceptance.get(c.index, False))
                and (c.gains.kp,c.gains.ki,c.gains.kd) == (best.candidate.kp,best.candidate.ki,best.candidate.kd)]
            self.best_overall = min(matches, key=lambda c: abs(c.fitness-best.score), default=None)
        if not self.finished:
            self.future = self.executor.submit(self.policy.propose_batch, 1)
        return None

    def poll_candidate(self):
        if self.future is None or not self.future.done():
            return None
        future, self.future = self.future, None
        try:
            return self._append_hybrid(future.result()[0])
        except Exception as exc:
            raise RuntimeError(f'Hybrid proposal failed: {exc}') from exc
