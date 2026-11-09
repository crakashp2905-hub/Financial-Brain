# Chart patterns: developer guide

## Authoring a detector

1. Write a function `name(bars: Bars, *, ...) -> list[Formation]` in `detectors.py`.
2. Read only bars at or before the candidate bar `e`. Emit a candidate only on the bar where it becomes knowable
   (`candidate_known_index == e`). Pivot-based shapes must use `known_at_index`.
3. Search for confirmation or failure only after `e`, through `_resolve`.
4. Give every formation a `geometry` dict of plain numbers and lists.
5. Add it to `DETECTORS`, add positive and negative fixtures, and extend the prefix-invariance test in
   `tests/test_pattern_detectors.py`.
6. Add its catalogue ID to `inventory.STATUS` and a matrix row with real test paths.
7. Run `python -m pytest -q tests/test_pattern_*.py`, `python -m ruff check`, `python -m mypy src/financial_brain/patterns`.

## Rules the tests enforce

- No module under `patterns/` imports network, broker, OMS, execution or paper packages.
- Detector output is DESCRIPTIVE.
- Pending definitions are not inference inputs.
- Protected records must not change (`docs/audits/protected-baseline.json`).
