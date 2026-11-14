# PE-0168 — FX paper simulation with jurisdiction and venue gates

## Acceptance

The FX paper-intent simulator produces `PAPER_SIMULATED` only when a supported Indian exchange-traded currency product has effective-dated contract specifications, current source-hashed authorization for a recognized exchange, eligible RBI rule terms, a fresh two-sided quote, and—when INR is involved—a matching unhedged contracted exposure sufficient for the proposed quantity. Unknown venue or jurisdiction becomes a reason-coded `NO_TRADE`.

## Implementation and verification

`financial_brain.kernel.fx_paper.simulate_fx_paper_intent` recognizes the seven RBI-listed currency pairs and the futures/European-option product set, applies the 12-month tenor and INR-settlement constraints, and requires current dated venue authorization evidence. The venue allowlist is NSE, BSE and MSEI, with separately supplied authorization evidence that must be no more than 30 days old and valid on the session date. INR-pair simulated orders require contracted, unhedged, same-pair exposure covering the proposed notional. Buys are simulated at ask and sells at bid, with half-spread cost displayed in INR. No external order call exists.

The effective-dated rule snapshot is in [`fx-paper-rulebook.v1.json`](fx-paper-rulebook.v1.json). The RBI consolidated Directions took effect on 3 May 2024 after an RBI announcement delayed their initial effective date. The rulebook digest is asserted against the runtime policy in tests.

The focused suite has 27 tests covering valid quote simulation, unknown jurisdictions and venues, stale/invalid authorization, contract constraints, missing or insufficient exposure, and missing/stale/future quotes. Ruff, targeted mypy and source-inventory checks pass (616 Python files).

## Limits

Tests use synthetic venue, contract, exposure and quote evidence. The tool does not fetch or independently authenticate source documents. It performs per-intent execution simulation only; it is not a complete position/funding account, broker integration or profitability validation. Only the reviewed Indian exchange-traded instruments are enabled; other jurisdictions and venues fail closed.

## Official references

- [RBI Master Direction: Risk Management and Inter-Bank Dealings](https://rbi.org.in/scripts/FS_Notification.aspx?Id=12594&Mode=0&fn=6) — exchange-traded FX products, currency pairs, term and settlement rules.
- [RBI press release on exchange-traded currency derivatives](https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=57628) — INR hedging requirement and effective date of 3 May 2024.
- [SEBI Annual Report 2025-26](https://www.sebi.gov.in/reports-and-statistics/publications/aug-2026/SEBI%20ANNUAL%20REPORT%202025_26.pdf) — identifies NSE, BSE and MSEI as exchanges authorized to offer currency derivatives at the report date.
