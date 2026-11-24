# Five-desk 15-minute index module parity audit

Date: 2026-11-25

## Sources inspected

- Five-desk D2 source branch `feature/five-desk-d2-data-parity`, commit
  `aff421aa01834b9f2a3533402e4747b8ce3aef79`; original
  `src/financial_brain/forecasting/index_15m.py` blob
  `9a96b5b9f870de2d62ee88951eef3042ffe2bcb3`.
- Target branch before this integration: `codex/v3-trade-hurdles-restack-20261124`.
- Existing target market contract: `src/financial_brain/forecasting/index_market.py`
  (catalogue, event availability, OHLC validation, complete session bars and same-session labels).
- Original five-desk acceptance suite: `tests/test_index_15m_s24.py` on D2.

## Parity findings and integration decision

| Contract | D2 `index_15m.py` | Target `index_market.py` | Integrated result |
| --- | --- | --- | --- |
| 15-minute bar construction | Session-aligned bars from minute price tuples; marks missing buckets partial; allows a fully observed short final interval. | Validates full OHLC and aware source/availability times; emits only complete 15-minute NSE bars available by cutoff. | Keep the stricter target builder; `index_15m.bars_15m` delegates to it. The legacy price-tuple API is intentionally not preserved because it has no receipt/availability clock or OHLC evidence. |
| Point-in-time feature visibility | Complete bars ending by decision time. | Also retains `available_at`; close time alone cannot make delayed data visible. | `completed_before` requires both end and availability at/before decision time and isolates the latest session. |
| Return labels | Same-step complete bars; returns `None` for missing/partial targets. | Supports exact 15/30/60-minute same-session horizons and refuses missing/noncontiguous targets. | Reuse target `forward_return`; feature returns also require contiguous bars. |
| Feature set | 15-minute/session returns, last range, session return volatility, minutes since open, opening-range position. | No feature builder. | Reintroduced with the D2 feature names and only availability-qualified bars. |
| Leakage check | Perturbs bars after decision cutoff and checks feature invariance. | No feature-level perturbation check. | Reintroduced and also treats delayed-but-closed bars as unknown. |
| Folds and baselines | Expanding chronological purged folds, training-only noise threshold, class-frequency baseline, probability validation and Brier score. | Frozen threshold label helper only. | Reintroduced with parameter and finite-value checks. |
| Index universe and optionability | Not included in this module. | Separate effective-dated index and optionable-index catalogues with source hash. | Keep the target catalogue; no optionability inferred from forecast coverage. |

## Verification

The target's market tests and new feature parity tests pass together:
`python -m pytest tests/test_forecasting_index_market.py tests/test_forecasting_index_15m.py -q`
reported 14 passed. This verifies selected contract parity and the deliberate stricter
availability/complete-bar behavior; it is not a profitability test or a full five-desk
forecast-to-options integration test.

## Remaining work

The original D2 source was not present in the target branch, so this is a parity-informed
integration rather than a git-level reuse of the exact source file. The old tuple-price API
was rejected in favor of the target's clocked OHLC input. This module is still research
infrastructure: no authorized live index ingestion, forecast model/calibration, derivative
policy, end-to-end paper session, or prospective evidence is established. SL-19-01 therefore
remains PARTIAL.
