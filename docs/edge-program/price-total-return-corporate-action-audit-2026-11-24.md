# Price-return, total-return, and corporate-action audit — 2026-11-24

Checklist item: `PA-02-10` — distinguish price-return from total-return fields and assess whether dividends, splits, rights, bonus issues, and other actions can create false momentum or reversal results.

## Findings

- Equity feature prices in `features.prices` are split/action-adjusted from raw EOD closes using `adjustment_factors`; downstream technical and momentum features consume `close_adj`. The adjustment is retrospective using later-known actions. This is suitable for a price-continuity series, but it is not a total-return series: cash distributions are not reinvested into a shareholder wealth index.
- `corpactions.actions` derives price factors for splits, bonus, rights, and dividends. A dividend factor requires both a prior close and a positive dividend smaller than that close. Missing/invalid factors are skipped; suspicious large unexplained overnight moves have a separate detection path.
- ETF analytics explicitly labels `ETF_PRICE_RETURN`, `ETF_NAV_RETURN`, and `UNDERLYING_TOTAL_RETURN_INDEX` separately. Price and NAV returns do not reinvest distributions; the benchmark must be an explicitly supplied TRI. Missing sessions remain null and are not bridged.
- Existing regression coverage includes split/bonus classification, unclassified adjustment factors, factor compounding, derived action handling, ETF split-adjusted prices, and the distinct ETF return-series labels. Targeted verification: `python -m pytest tests/test_phase0_reference.py tests/test_etf_prices.py -k 'ActionClassification or DerivedActions or factors_compound or etf_daily_prices or separate_etf' -q` — 12 passed, 51 deselected.

## Limits and status

Status remains `PARTIAL`. The tests establish factor and field-separation behavior on fixtures, not production history correctness. There is no source-backed, distribution-reinvested equity total-return index here; rights entitlement valuation and cash-in-lieu treatment are not reconciled; the equity adjusted-price path’s later-action knowledge needs strict separation from point-in-time decision features; and no before/after study measures changes to momentum or reversal labels, trial verdicts, or strategy returns after action corrections. PA-02-11 separately requires the named H056/H068 ex-dividend artifact probe. Do not interpret this audit as evidence that an alpha survives corporate-action correction.
