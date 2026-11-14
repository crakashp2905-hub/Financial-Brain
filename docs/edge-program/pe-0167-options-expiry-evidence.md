# PE-0167 — Indian option expiry and delivery-solvency gate

## Implemented

`OptionsPaperAccount` now requires a fresh, hash-bound India expiry preflight before recording a new expiry event. The preflight checks the contract venue, underlying class, exercise style, and settlement type against an effective-dated rule; verifies a source hash for settlement prices; calculates expiry cash flows; requires source-identified delivery-margin evidence for in-the-money physical contracts; and checks deliverable share inventory and post-expiry cash. The readiness hash binds these checks to the account state, rulebook, prices, and settlement source. Reusing a readiness result after the account changes is rejected. Journal replay verifies the stored readiness against the replayed account state.

Legacy expiry rows written before this gate are replayed to preserve historical account state and are exposed through `legacy_expiry_events`. They are not silently represented as having passed the new preflight.

The reviewed rules are in [`india-options-expiry-rules.v1.json`](india-options-expiry-rules.v1.json), mirrored by `RULES` in the kernel module. The index rule begins on 2000-12-11, when SEBI's initial index-option circular specified European-style contracts settled in cash. The generic stock rule starts 2019-10-01 for the remaining stock derivatives moved to mandatory physical settlement from October 2019 expiry. SEBI's rollout was phased, so pre-October 2019 stock expiries intentionally block absent contract-specific phase evidence. Unknown venues and missing rules also fail closed.

The calendar-spread overlay records the expiry-day no-offset rule effective 2026-05-05. The preflight does not manufacture an exchange margin rate: it requires positive, source-hashed per-contract margin evidence and callers must provide evidence reflecting the applicable no-offset treatment. Obtaining current official contract-master, settlement-price, and margin files remains a live provider operation and is outside synthetic unit-test evidence.

## Acceptance evidence

- `tests/test_india_options_expiry.py` covers settlement mismatch, missing or insufficient delivery margin/cash, insufficient delivery inventory, unsupported historical stock expiry, and stale readiness.
- `tests/test_kernel_options_paper.py` exercises the durable options account and expiry lifecycle with explicit preflight evidence.
- Focused run: `python -m pytest tests/test_india_options_expiry.py tests/test_kernel_options_paper.py -q` — 8 passed.
- Ruff and targeted mypy checks are run before packaging; the acceptance tests use synthetic contract, price, and margin evidence and do not claim live exchange validation.

## Official references

- [SEBI index option risk-containment circular (11 December 2000)](https://www.sebi.gov.in/sebi_data/attachdocs/1300707082606.pdf)
- [SEBI physical settlement circular (31 December 2018)](https://www.sebi.gov.in/legal/circulars/dec-2018/physical-settlement-of-stock-derivatives_41482.html)
- [NSE circular on physical settlement implementation](https://nsearchives.nseindia.com/content/circulars/INSP47293.pdf)
- [SEBI European-style stock option circular (27 October 2010)](https://www.sebi.gov.in/legal/circulars/oct-2010/european-style-stock-options_6846.html)
- [SEBI February 2026 calendar-spread margin circular](https://www.sebi.gov.in/sebi_data/attachdocs/feb-2026/1770288869049.pdf)

These references establish general rule history; contract-specific expiry, venue, lot size, underlying marks, settlement prices, and current margin remain sourced inputs and must be attested at run time.
