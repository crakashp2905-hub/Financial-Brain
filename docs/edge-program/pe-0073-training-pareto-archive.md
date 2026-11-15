# PE-0073 training-only Pareto trade-off archive

Added a five-objective candidate report for net performance, drawdown, turnover, capacity, and complexity. Every candidate must carry the frozen training-data digest and `TRAINING_ONLY` role; the evaluation protocol is content-addressed. The selection must lie on the Pareto frontier and cite a predeclared selection rule. Every non-selected candidate requires a reason. The report stores all candidates, dispositions, dominators, objectives, and its own digest in a local hash-chained archive that detects record edits and duplicate submissions.

The task remains `PARTIAL`: the mechanics are verified on synthetic records, but no approved training cohort or governance-approved selection rule has been run. The archive is local integrity evidence, not independent attestation, and no strategy recommendation or performance claim is made.
