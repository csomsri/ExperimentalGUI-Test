# Windowed BO learning inside GA

Implementation: [rolling_hybrid_optimizer.py](../../source/Python/Optimization/rolling_hybrid_optimizer.py).
These modes retain GA ownership throughout; they do not perform legacy challenger
handover or paired confirmation.

## What the window contains

`WindowedBayesianOptimizer.safe_observations` exposes only the most recent W valid
physical observations. `recent_results` exposes the matching recent PID results.
Baseline, reference, exploration, GA-parent and refinement responses all count.
Repeated vectors remain separate observations. Invalid responses remain in the
full archive but do not enter the training window and stop search.

W limits GP training size, not total audit storage. Readiness is recalculated
from the window and can disappear when diverse points age out. This is a
forgetting model without an explicit time coordinate or temporal kernel.

## One parent's lifecycle

1. Measure the current GA individual's actual gains (`GA parent`).
2. Choose inheritance mode for that parent. In mixed mode, a seeded probability
   draw chooses Lamarckian or Baldwinian behavior.
3. Finish selection immediately if readiness fails, the shared budget is spent,
   or the refinement-probability draw declines learning.
4. Otherwise refit the GP on the current window and optimize acquisition inside
   the local box around the best accepted measured gains for this parent.
5. Require a different gain vector and a predicted cost plus uncertainty margin
   better than the current measured cost by the configured threshold.
6. If prediction fails, finalize selection without spending a physical trial.
   The same proposal call can then schedule a reference, exploration, or parent.
7. If prediction passes, measure a `BO refinement`. Accept it as the learned
   response only if its measured cost improves beyond the relative/noise margin
   and it is eligible. A worse valid response still trains the GP.
8. Stop learning on non-improvement, exhausted refinement allowance, exhausted
   total budget, or lost readiness. Otherwise refit and refine around the accepted
   gains. Every measured refinement spends one shared-budget trial.

Pending learning takes priority over periodic reference/exploration checks. Once
the parent is finalized, periodic checks resume. The inherited drift guard still
compares seed-gain reference batches with the original baseline.

## Genotype, phenotype, selection fitness

The genotype is the gain vector passed to reproduction; the phenotype is the
gain vector whose measured response supplied the parent's best learned cost.

| Mode | Inherited gains | GA selection fitness | BO training location |
| --- | --- | --- | --- |
| Lamarckian | Accepted learned vector | Best measured cost during this parent's learning | Each vector that actually ran |
| Baldwinian | Original parent vector | Best measured cost during this parent's learning | Each vector that actually ran |
| Mixed | Determined once per parent by probability | Same measured-fitness rule | Each vector that actually ran |

For example, if parent A costs 10 and refinement B costs 6 and clears the margin,
Lamarckian selection records B with fitness 6. Baldwinian selection records A with
fitness 6. In both cases the physical archive records A at 10 and B at 6. The
optimizer never fabricates a measurement of A at cost 6.

`_finish_learning()` records the decision, optionally replaces the current
population gene, and calls `ga.submit_fitness()` exactly once for the parent.
Surviving genes, including elites, are measured again in each generation.

## Selecting gains for validation

Windowed `best_result` takes the latest measured response for each gain vector in
the active window, removes rejected validation vectors and responses without
metrics, then selects the lowest measured cost. It does not use Baldwinian
selection fitness or average all historical repeats. Drift disables this result.

The generic windowed BO object's `best_observation` instead selects the smallest
single observation cost in the window. This internal statistic can differ from
the PID policy's latest-per-vector validation candidate.

Windowed refinement acceptance uses cost improvement. It does not use the
legacy paired MAE gate. MAE is still reported, and reference drift still checks
both cost and MAE. Independent final validation remains a page responsibility.
