"""BO strategy implementing the existing automatic trial runner's interface."""
import math
from concurrent.futures import ThreadPoolExecutor
from ga_pid_tuner import PIDCandidate
from pid_controller import PIDGains
from .optimization import BotorchBayesianOptimizer, OptimizationCandidate, OptimizationObservation


class BOPIDAdapter:
    is_bo = True

    def __init__(self, bounds, budget=20, initial_safe_trials=6, seed=1729, **_kwargs):
        self.budget = max(1, int(budget))
        self.optimizer = BotorchBayesianOptimizer(
            [(name, getattr(bounds, name)) for name in ('kp', 'ki', 'kd')],
            use_cuda=False,
            initial_safe_trials=initial_safe_trials, seed=seed,
        )
        self.acceptance = {}
        self.analyses = {}
        from .responsiveness import TrackingCriteria
        self.criteria = TrackingCriteria()
        self.recovery = {"enabled": False}
        self.validation = False
        self.require_tracking = True
        self.interleave_reference = True
        self.reference_gains = None
        self.queued_candidate = None
        self.role = 'candidate'
        self.completed_candidates = 0
        self.population = []
        self.finished = False
        self.best_overall = None
        self.future = None
        self.proposal_source = 'baseline'
        self.proposed_gains = None
        self.results = []
        self.decisions = []
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='PID-BO')

    def initialize(self, seed_gains):
        seed_gains = seed_gains.validated()
        seed_gains = PIDGains(**{
            parameter.name: min(max(getattr(seed_gains, parameter.name), parameter.bounds[0]), parameter.bounds[1])
            for parameter in self.optimizer.parameters
        })
        self.reference_gains = getattr(self, 'reference_override', None) or seed_gains
        self.proposed_gains = dict(kp=seed_gains.kp, ki=seed_gains.ki, kd=seed_gains.kd)
        self.optimizer.parameter_space.validate_candidate(self._vector(seed_gains))
        self.population = [PIDCandidate(seed_gains)]
        self.decisions = [dict(source=self.proposal_source, statistics_available=False,
            training_count=0, unavailable_reason='Starting gains are measured before GP fitting.')]
        return self.current_candidate()

    @staticmethod
    def _vector(gains):
        return OptimizationCandidate({name: getattr(gains, name) for name in ('kp', 'ki', 'kd')})

    def current_candidate(self):
        if self.finished or not self.population:
            raise RuntimeError('BO search is complete')
        return self.population[-1]

    def best_candidate(self):
        return self.best_overall

    def progress(self):
        return 1, 1, self.completed_candidates + 1, self.budget

    def submit_fitness(self, fitness, *, valid=True):
        candidate = self.current_candidate()
        if candidate.evaluated:
            raise RuntimeError('BO trial result already submitted')
        candidate.fitness = float(fitness)
        candidate.valid = bool(valid and math.isfinite(fitness))
        is_reference = self.role == 'reference'
        if candidate.valid and not is_reference:
            self.optimizer.record_observations([
                OptimizationObservation(self._vector(candidate.gains), float(fitness),
                                        metadata={'trial_index': candidate.index + 1,
                                            'tracking_accepted': self.acceptance.get(candidate.index, False),
                                            'objective_version': 'october-five-term-v1',
                                            'training_policy': 'finite-data-valid-including-performance-failures-v1'})
            ])
            if (not self.require_tracking or self.acceptance.get(candidate.index, False)) and (self.best_overall is None or fitness < self.best_overall.fitness):
                self.best_overall = candidate
        if not is_reference:
            self.completed_candidates += 1
        self.finished = self.completed_candidates >= self.budget
        if not self.finished and not is_reference:
            self.future = self.executor.submit(self.optimizer.propose_batch, 1)
        return None

    @property
    def proposal_pending(self):
        return self.future is not None or self.queued_candidate is not None

    def poll_candidate(self):
        if self.queued_candidate is not None:
            proposal, decision = self.queued_candidate
            self.queued_candidate = None
            self.role = 'candidate'
            return self._append_proposal(proposal, decision)
        if self.future is None or not self.future.done():
            return None
        future, self.future = self.future, None
        try:
            proposal = future.result()[0]
            self.optimizer.parameter_space.validate_candidate(proposal)
        except Exception as exc:
            raise RuntimeError(f'BO proposal failed: {exc}') from exc
        decision = self.optimizer.last_decision or dict(source='sobol', statistics_available=False)
        if self.interleave_reference:
            self.queued_candidate = (proposal, decision)
            self.role = 'reference'
            return self._append_proposal(self._vector(self.reference_gains),
                dict(source='reference', statistics_available=False,
                     unavailable_reason='Fixed reference: excluded from GP training and candidate budget.'))
        self.role = 'candidate'
        return self._append_proposal(proposal, decision)

    def _append_proposal(self, proposal, decision):
        candidate = PIDCandidate(PIDGains(**proposal.values), index=len(self.population))
        self.proposal_source = decision['source']
        self.decisions.append(decision)
        self.proposed_gains = dict(proposal.values)
        self.population.append(candidate)
        return candidate

    def close(self):
        if self.future is not None:
            self.future.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)


class FixedGainValidationAdapter(BOPIDAdapter):
    """Predeclared paired reference/candidate waveform repetitions; no GP training."""
    def __init__(self, bounds, reference, candidate, repeats=3, seed=1729, condition_offset_nA=0.5, **kwargs):
        super().__init__(bounds, budget=6*repeats, seed=seed)
        import random
        rng = random.Random(seed)
        self.schedule = []
        self.schedule_conditions = []
        for condition, offset in [('low',-condition_offset_nA),('nominal',0),('high',condition_offset_nA)]:
            for repeat in range(repeats):
                pair = [('reference', reference), ('validation', candidate)]
                if rng.randrange(2):
                    pair.reverse()
                self.schedule.extend((role, gains, repeat+1) for role, gains in pair)
                self.schedule_conditions.extend([dict(name=condition,offset_nA=offset)]*2)
        self.validation = True
        self.repeat = 1

    def initialize(self, seed_gains):
        role, gains, self.repeat = self.schedule[0]
        candidate = super().initialize(gains)
        # Fixed reference validation must not silently clamp the supplied gains
        # to search bounds; the actuator limits still apply to every command.
        candidate.gains = gains.validated()
        self.proposed_gains = dict(kp=gains.kp,ki=gains.ki,kd=gains.kd)
        self.role = role
        self.proposal_source = role
        self.decisions[0]['source'] = role
        return candidate

    def submit_fitness(self, fitness, *, valid=True):
        candidate = self.current_candidate()
        if candidate.evaluated:
            raise RuntimeError('Validation result already submitted')
        candidate.fitness = float(fitness)
        candidate.valid = bool(valid and math.isfinite(fitness))
        if self.role == 'validation' and self.acceptance.get(candidate.index, False):
            if self.best_overall is None or fitness < self.best_overall.fitness:
                self.best_overall = candidate
        self.finished = len(self.population) >= len(self.schedule)
        if not self.finished:
            role, gains, repeat = self.schedule[len(self.population)]
            self.role, self.repeat = role, repeat
            return self._append_proposal(self._vector(gains), dict(source=role,
                statistics_available=False, repeat=repeat,
                unavailable_reason='Independent fixed-gain waveform validation; no GP training.'))
        return None
