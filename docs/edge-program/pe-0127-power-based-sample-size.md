# PE-0127 — power-based sample-size requirement

`design_sample_size()` computes a one-sided normal-approximation session requirement from the detectable daily benefit, daily volatility, desired power, familywise alpha, number of planned looks, dependence design effect and expected usable-session rate. It returns both the independent-session calculation and dependence/missingness-adjusted requirement. The default 60-session value is only a floor: the final requirement is the larger of the floor and the calculated sample, and `power_at_floor` shows when the floor is underpowered. The content-addressed plan requires a freeze timestamp before first observation.

**Status: DONE for the sample-size calculation contract.** Thirteen focused tests and Ruff pass, including cases where the computed requirement exceeds 60, where the floor binds, and where multiple looks, dependence or missing sessions increase the requirement.

The calculation uses caller-supplied design assumptions and a normal approximation. This does not establish that a strategy is profitable, that the supplied volatility/dependence inputs are empirically calibrated, or that the plan is durably registered in the prospective trial registry.

Evidence and source hashes are recorded in `pe-0127-power-based-sample-size-evidence.json`.
