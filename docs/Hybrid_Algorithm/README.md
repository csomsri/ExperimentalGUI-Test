# Hybrid Algorithm documentation

Implementation reference for the GA + Bayesian optimization PID tuner, inspected
on 2026-11-09. This folder explains the code currently in this repository;
defaults are optimizer defaults unless explicitly identified otherwise.

For this Python GUI, start with [Local implementation and workflow](LOCAL_IMPLEMENTATION.md).
The reference links below describe the compared CrockerGUI project.

## Reading order

1. [Architecture and shared search machinery](01_Architecture.md)
2. [Original GA-to-BO handover algorithm](02_Legacy_Handover.md)
3. [Windowed BO and GA learning variants](03_Windowed_Learning.md)
4. [Measurements, fitness, and decision equations](04_Metrics_and_Equations.md)
5. [Configuration reference](05_Configuration.md)
6. [Operation, integration, records, and verification](06_Operation_and_Development.md)

## What is being optimized?

Each physical trial evaluates one bounded gain vector `(Kp, Ki, Kd)` using beam
current feedback in nA and a selected trim-coil command in A. The scalar fitness
is a cost: smaller is better. Python coordinates experiments and proposes gains;
the native PID/control service executes trials.

| Approach value | Search owner | BO training history | How learning affects inheritance |
| --- | --- | --- | --- |
| `legacy` (factory default) | GA, then BO after measured confirmation | All valid observations | BO eventually takes over proposal selection |
| `lamarckian` | GA | Latest W valid observations | Accepted refined gains replace the parent genotype |
| `baldwinian` | GA | Latest W valid observations | Parent genotype remains; learned measured cost drives selection |
| `mixed` | GA | Latest W valid observations | Choose inheritance mode separately per parent |

## Important implementation boundaries

The proposal uncertainty gate authorizes a measurement; it does not establish
plant safety or stability. A result's `safe` flag represents trial validity as
reported by execution. The optimizer's current `eligible()` checks only `safe`,
the presence of metrics, and finite score. It does **not** independently require
settling, absence of sustained oscillation, or a steady-error threshold.

Recovery currently restores captured **software targets** to two decimal places.
It does not wait for physical current or beam settling. Some statements in the
[older combined guide](../../HYBRID_GA_BO_PID.md) describe stronger recovery and
response checks; use the source-backed distinctions in this folder when assessing
current behavior. No controller behavior was changed to create these documents.
