# Chart patterns: detector authoring guide

A detector turns completed bars into candidate shapes. Read `PATTERNS-ARCHITECTURE.md` first.

## The causal rule

A candidate at bar `e` may use only bars `<= e`. It is emitted only on the bar where its last input becomes knowable, so `candidate_known_index == e`. Confirmation and failure are searched **after** `e` and stored as later facts.

Every detector must satisfy the prefix-invariance identity:

    detect(bars.head(t + 1)) == [f.as_of(t) for f in detect(bars) if f.candidate_known_index <= t]

## Proof requirements for a new detector

Before a detector is merged, the author supplies:

1. The pivot rule it uses (`causal_pivots` or `StreamingPivots`), with its `left` and `right` windows.
2. The known index of every input, shown as `max(p.known_at_index)`.
3. The emission condition `known == e`, and why no earlier bar could emit.
4. The confirmation and failure rules, each searched strictly after `e`.
5. A prefix-invariance test on at least three seeded random walks, plus one positive example.
6. A complexity count: the number of geometry points, which must stay within `MAX_PIVOTS` (currently 6).

## Fixture template

Use `scripts/build_pattern_fixtures.py` as the template. Each family needs:

- one **positive** example that the detector must fire on;
- two **negative** examples: the same shape with one rule broken (for example a range too wide, or a retrace too deep).

Examples are synthetic OHLC rows, stored in `tests/fixtures/patterns/family_examples.json`. Regenerate with `python scripts/build_pattern_fixtures.py`. The test suite checks every expectation.

## Labelling

- Every candidate keeps `direction == "neutral"` until a breakout is confirmed.
- No detector returns a profit target. Explanatory levels come from `levels()` and always have `profit_target: None`.
- Evidence tier is `DESCRIPTIVE` for every detector output.

## Adding a detector to the scan

Wave-9 detectors live in `patterns/detectors2.py` and run through `discover_extended`. They are not yet in the scan pipeline `discover`, because changing that pipeline would change pinned demo outputs. Wiring them in is a separate step with its own hash update.
