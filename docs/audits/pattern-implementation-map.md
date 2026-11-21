# Chart-pattern implementation map

Version 1, produced with the first wave of `docs/audits/chart-patterns-master-checklist.md`.
Each row names real files; `tests/test_pattern_matrix.py` checks that they exist.
The authoritative per-task status is `docs/audits/chart-patterns-completion.json`.

| Capability | Source | Tests | Checklist tasks |
|---|---|---|---|
| Offline guard (`PATTERN_DEV_MODE`, fail-closed) | `src/financial_brain/patterns/offline.py` | `tests/test_pattern_offline.py` | CP-0011 |
| Import boundary (no network, broker, execution or paper imports) | `src/financial_brain/patterns/` | `tests/test_pattern_offline.py` | CP-0149 |
| Catalogue validation, typed book stats, families, direction class | `src/financial_brain/patterns/catalogue.py` | `tests/test_pattern_catalogue.py` | CP-0014, CP-0016, CP-0018, CP-0019, CP-0020, CP-0025 |
| Canonical pattern spec, event model, lifecycle, evidence tier, event IDs | `src/financial_brain/patterns/spec.py` | `tests/test_pattern_spec.py` | CP-0027, CP-0028, CP-0030, CP-0033, CP-0034, CP-0038, CP-0040 |
| Causal swing pivots and tie rule | `src/financial_brain/patterns/pivots.py` | `tests/test_pattern_pivots.py` | CP-0038, CP-0056, CP-0057, CP-0136, CP-0138 |
| Baseline fingerprint and completion matrix | `scripts/chart_patterns_matrix.py` | `tests/test_pattern_matrix.py` | CP-0001, CP-0010 |

Existing modules reused rather than duplicated (CP-0007, not yet a formal design note):
`src/financial_brain/features/library.py` (candle features `c_*`),
`src/financial_brain/evaluation/chart_setups.py` (K7 chart experiments and null result),
`src/financial_brain/evaluation/family_validation.py`, `src/financial_brain/research/preregistry.py`,
`src/financial_brain/evaluation/standard.py`, `src/financial_brain/evaluation/champion.py`,
`src/financial_brain/evaluation/prospective.py`. No detector in this wave consumes them yet.

Not yet built: geometric detectors (stage 05 onward), detector-level causal tests, event store,
paper adapter, UI, learning layer, agent tools, Rust kernels, JSON Schema file.
