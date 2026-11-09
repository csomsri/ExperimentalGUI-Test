# BO and PID implementation documentation

Central index for the BO/PID implementation, from UI through experiment execution,
controller, ML, records, and tests. Source files stay in their existing packages.
Paths below are relative to this directory. Documentation consolidated November 6, 2026.

## Read in this order

1. [Implementation map](IMPLEMENTATION.md): current UI, runtime, ML, data and source ownership.
2. [BO validation path](VALIDATION_PATH.md): ordered implementation and experiment gates.
3. [Controlled testing plan](CONTROLLED_TESTING_PLAN.md): complete user-supplied BO/PID fixes and controlled-testing requirements, preserved verbatim.
4. [Tests and change record](TESTS_AND_CHANGES.md): verification commands, coverage gaps and changes in this consolidation.

## Current local software status

See [ExperimentalGUI implementation and operator workflow](LOCAL_IMPLEMENTATION.md)
for the implemented wait, measured readiness, trial metrics, tracking acceptance,
reference checks and independent square-wave validation. The controlled-testing
plan is preserved as supplied; its original pending status is historical.
The implementation map below includes external-project paths and must be read
alongside the local source mapping. Software tests do not establish physical
readiness or successful machine control.

## Existing implementation records

| Document | Scope |
| --- | --- |
| [TC10 evaluation guide](../../../TC10_PID_EVALUATION_GUIDE.md) | Beam/PID evaluation workflow |
| [Remaining work](../../../BO_PID_TUNER_REMAINING_WORK.md) | Historical commissioning backlog; not a current completion checklist |
| [BO readiness](../BO_TEST_READINESS.md) | Dated simulation evidence, restart fixes and limitations |
| [October PID integration](../../OCTOBER_PID_INTEGRATION.md) | Controller integration history |
| [Hybrid implementation](../../HYBRID_GA_BO_PID.md) | GA/BO selection, reference checks, windowed learning and records |
| [Direct trim-coil BO plan](../../MISO_TRIM_COIL_BO_PLAN.md) | Joint coil optimization; distinct from gain tuning |
| [Direct trim-coil BO usage](../../MISO_TRIM_COIL_BO_USAGE.md) | Operator workflow |
| [Beam optimization workspace](../BEAM_OPTIMIZATION_WORKSPACE.md) | Current workspace, prospective predictions and virtual exploration |
| [Smoke2 BO](../../SMOKE2_BO.md) | Synthetic plant workflow |
| [BO model explanation](../../source/Python/Optimization/BAYESIAN_OPTIMIZATION_EXPLAINED.md) | Model and acquisition background |

Historical test counts and operating examples in these documents retain their
original dates and scope. They are not new test results or approved live settings.
