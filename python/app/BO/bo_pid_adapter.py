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
        self.population = []
        self.finished = False
        self.best_overall = None
        self.future = None
        self.proposal_source = 'baseline'
        self.proposed_gains = None
        self.results = []
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='PID-BO')

    def initialize(self, seed_gains):
        seed_gains = seed_gains.validated()
        seed_gains = PIDGains(**{
            parameter.name: min(max(getattr(seed_gains, parameter.name), parameter.bounds[0]), parameter.bounds[1])
            for parameter in self.optimizer.parameters
        })
        self.proposed_gains = dict(kp=seed_gains.kp, ki=seed_gains.ki, kd=seed_gains.kd)
        self.optimizer.parameter_space.validate_candidate(self._vector(seed_gains))
        self.population = [PIDCandidate(seed_gains)]
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
        return 1, 1, len(self.population), self.budget

    def submit_fitness(self, fitness, *, valid=True):
        candidate = self.current_candidate()
        if candidate.evaluated:
            raise RuntimeError('BO trial result already submitted')
        candidate.fitness = float(fitness)
        candidate.valid = bool(valid and math.isfinite(fitness))
        if candidate.valid:
            self.optimizer.record_observations([
                OptimizationObservation(self._vector(candidate.gains), float(fitness))
            ])
            if self.best_overall is None or fitness < self.best_overall.fitness:
                self.best_overall = candidate
        self.finished = len(self.population) >= self.budget
        if not self.finished:
            self.future = self.executor.submit(self.optimizer.propose_batch, 1)
        return None

    @property
    def proposal_pending(self):
        return self.future is not None

    def poll_candidate(self):
        if self.future is None or not self.future.done():
            return None
        future, self.future = self.future, None
        try:
            proposal = future.result()[0]
            self.optimizer.parameter_space.validate_candidate(proposal)
        except Exception as exc:
            raise RuntimeError(f'BO proposal failed: {exc}') from exc
        candidate = PIDCandidate(PIDGains(**proposal.values), index=len(self.population))
        self.proposal_source = ('sobol' if len(self.optimizer.safe_observations)
                                < self.optimizer.initial_safe_trials else 'gp_qlogei')
        self.proposed_gains = dict(proposal.values)
        self.population.append(candidate)
        return candidate

    def close(self):
        if self.future is not None:
            self.future.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)
