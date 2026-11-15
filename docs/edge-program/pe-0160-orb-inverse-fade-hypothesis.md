# PE-0160 — Exploratory ORB failed-break fade hypothesis

## Implemented

- Added a separately identified `EXPLORATORY_RESEARCH` hypothesis for fading an opening-range breakout only after a completed bar probes beyond the range and closes back inside. The mechanism, likely payer, eligible regimes, decision rule, exit rule and falsifier are frozen in its own content-addressed record.
- The observer requires timezone-aware opening-range, event, availability and decision timestamps in causal order. It emits only `EXPLORATORY_CANDIDATE` with a direction; it has no quantity or order intent and is not inserted in the strategy registry or evidence ledger.
- Added a dedicated round-trip cost budget with entry/exit spread, fee, tax and impact inputs, an effective-date fee-schedule identity/hash, and 1x/1.5x/2x cost stress. The preregistered exploratory hurdle requires gross edge greater than 2x total modeled costs in every scenario. Missing or malformed evidence fails closed.

## Verification

`tests/test_orb_fade_hypothesis.py`: 7 passed; Ruff passed. Tests check the isolated mechanism and unregistered state, causal timestamp boundaries, both rejection directions, missing cost evidence, and stress-budget outcomes.

## Research boundary

The 2x cost-coverage rule is a research protocol parameter, not an observed fee estimate or profitability claim. The module does not run a historical search, produce an alpha estimate, register the candidate, or route it to a paper book. Any later evaluation needs its own authorized timestamped data, point-in-time controls, a frozen training/validation split and a separate prospective protocol.
