"""BO strategy implementing the existing automatic trial runner's interface."""
import math
from concurrent.futures import ThreadPoolExecutor
from ga_pid_tuner import PIDCandidate
from pid_controller import PIDGains
from .optimization import BotorchBayesianOptimizer, OptimizationCandidate, OptimizationObservation


class BOPIDAdapter:
    is_bo = True

    def __init__(self, bounds, budget=20, **_kwargs):
        self.budget = max(1, int(budget))
        self.optimizer = BotorchBayesianOptimizer(
            [(name, getattr(bounds, name)) for name in ('kp', 'ki', 'kd')],
            use_cuda=False,
        )
        self.population = []
        self.finished = False
        self.best_overall = None
        self.future = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='PID-BO')

    def initialize(self, seed_gains):
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
        if not candidate.valid:
            raise RuntimeError('Invalid BO trial; search stopped')
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
        self.population.append(candidate)
        return candidate

    def close(self):
        if self.future is not None:
            self.future.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)
