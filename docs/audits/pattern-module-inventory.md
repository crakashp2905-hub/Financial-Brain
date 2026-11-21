# Chart patterns: existing modules reviewed before building (CP-0003)

Checked against `src/financial_brain/` in this checkout. The pattern package reuses these and does not duplicate them.

| Existing module | Used by the pattern package | Note |
|---|---|---|
| `features/library` (`compute`) | `patterns/candles.py` wraps its 13 candle columns | Candle definitions are reused, not reimplemented. |
| `pit` | Not imported yet | Point-in-time data for universe and eligibility (CP-0156). Reserved. |
| `universe` | Not imported yet | Universe definitions (CP-0156). Reserved. |
| `securities`, `security` | Not imported yet | Identity and lineage. Patterns use their own `IdentityMap`. |
| `corpactions` | Not imported yet | Corporate actions. Patterns use `adjust_for_split` and `adjust_for_dividend`. |
| `risk` | Not imported | Deterministic risk gate. Patterns keep their own reason codes. |
| `paper`, `execution` | Forbidden to pattern modules | The offline boundary test (`tests/test_pattern_offline.py`) blocks these imports. Risk-gate and workstation integration (CP-0205, CP-0234) therefore cannot be done inside `patterns/`. |
| `evaluation/family_validation` | Wrapped by `patterns/study.py` as a reality-check adapter | Reused. |
| `lake`, `storage` | Not imported | The read-only `BarSource` (`patterns/source.py`) talks to DuckDB and Parquet directly. |

The offline guard (`PATTERN_DEV_MODE`) and the import boundary are the constraints that set these limits.
