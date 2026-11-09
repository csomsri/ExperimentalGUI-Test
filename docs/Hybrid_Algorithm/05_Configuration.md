# Configuration reference

Source: `HybridConfig` and `RollingHybridConfig`. GUI fields can impose additional
ranges. These are Python optimizer defaults, not a saved session's values.

## Shared settings

| Field | Default | Effect |
| --- | --- | --- |
| `population` | 6 | Measured individuals per GA generation |
| `baseline_repeats` | 3 | Initial seed repeats and reference-batch size |
| `minimum_distinct` | 12 | Minimum distinct valid vectors for BO readiness |
| `coverage` | 0.35 | Minimum normalized observed span on every axis |
| `improvement_fraction` | 0.05 | Relative improvement margin |
| `confirmation_pairs` | 3 | Legacy incumbent/challenger repeat pairs |
| `plateau_trials` | 5 | Legacy consecutive BO cost stalls before fallback |
| `mutation_probability` | 0.25 | GA per-gain mutation probability |
| `mutation_scale` | 0.1 | GA mutation scale relative to gain span |
| `seed` | 1729 | Base random seed |
| `budget` | 48 | Total measured search trials, all sources combined |
| `reference_interval` | 8 | Qualifying non-baseline/non-reference trials between reference batches |
| `exploration_interval` | 5 | Qualifying trials before extra space-filling exploration |
| `drift_fraction` | 0.2 | Relative original-baseline drift threshold |

Shared validation requires positive intervals; finite drift fraction in `(0,1]`;
population and distinct minimum at least four; at least two baseline repeats;
at least three confirmation pairs; positive plateau count; budget covering
baseline repeats plus one population; and finite coverage/improvement in `(0,1]`.
The GA additionally validates mutation probability and scale in `[0,1]`, finite
ordered nonnegative bounds, and an in-bounds finite seed. The hybrid span equations
need strictly positive bound widths. Shared validation does not explicitly enforce
integer types on every count field, so direct callers should supply integers.

## Windowed settings

| Field | Default | Effect |
| --- | --- | --- |
| `learning_mode` | `lamarckian` | Direct rolling-class default; factory uses `approach` |
| `window_size` | 32 | W recent valid measurements used by GP |
| `local_radius` | 0.15 | Half-width of local box as fraction of full axis span |
| `refinement_probability` | 0.5 | Chance of offering learning to a ready measured parent |
| `lamarckian_probability` | 0.5 | Lamarckian chance per parent in mixed mode |
| `refinement_trials` | 1 | Maximum physical refinements for a selected parent |
| `uncertainty_multiplier` | 1.0 | Standard deviations added in prediction screen |
| `mc_samples` | 256 | Acquisition Monte Carlo samples |
| `num_restarts` | 10 | Acquisition optimizer restarts |
| `raw_samples` | 256 | Acquisition initialization samples |

Window size, refinement trials and acquisition counts must be positive integers
(booleans rejected); W must cover `minimum_distinct`. Probabilities must be finite
in `[0,1]`, radius finite in `(0,1]`, and uncertainty multiplier finite/nonnegative.
Legacy handover/plateau fields are inherited and validated but do not drive
windowed learning decisions.

## Factory and GUI

```python
from source.Python.Optimization.pid_gain_adapter import PidGainCandidate
from source.Python.Optimization.rolling_hybrid_optimizer import create_hybrid_optimizer

optimizer = create_hybrid_optimizer(
    ((0.0, 1.0), (0.0, 0.5), (0.0, 0.2)),
    PidGainCandidate(0.1, 0.05, 0.01),
    approach="lamarckian", window_size=32, budget=48,
)
```

Omitting `approach` creates the original `HybridPIDOptimizer`. The factory filters
settings by the selected config's dataclass fields: window-only fields are ignored
in legacy mode. Unknown approach values fail rolling-mode validation.

In the page, **AI tuning → BO / GA settings → BO learning** selects the approach.
Session inputs lock while running, and the configuration fingerprint detects
programmatic changes. Target, channel, gain bounds, trial duration, budget and
controller configuration are page inputs rather than all being config fields.
Window controls are disabled for legacy; handover controls are disabled for
windowed modes. Inspect exported session settings for an actual run's parameters.
