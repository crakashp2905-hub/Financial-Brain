# Edge development: swing, intraday and options

Updated 2026-10-21. **No new tradeable edge has been established.** An edge means incremental return after
actual costs, an appropriate baseline and the prospective gates in ADR-0004. A strategy idea, an in-sample
average or a model probability is not that result. The 2026Q4 registered trial budget is exhausted; this work
is infrastructure and *training-only exploration*, not a new registered verdict. The 2022-2026 options holdout
was not queried by the new evaluator.

## What was built

`python -m financial_brain.evaluation.options_real --verticals` explores NIFTY call/put contracts in the NSE
F&O daily archive. The source has observed contract closes, volume and OI from 2015 onward, so this is less
synthetic than `options_synth.py`. It still has **no historical bid/ask/depth** and is not a fill record. The engine
selects a near-expiry ATM contract at the signal close using the dated Nifty 50 PRICE index as a reference, enters
at the *next* session close and exits after five sessions in the **same expiry and strike**. It rejects an
expired contract and tracks missing/untraded exits. The optional vertical engine pairs the short ATM leg with a
longer-dated-in-strike OTM wing of the same type and expiry; it never researches naked writing. All displayed
returns use a provisional 2% premium haircut *per leg per side*. This is not a measured spread or complete
Indian tax/fee schedule. The normalized risk of a spread is its payoff risk **before exit costs**; losses after
haircuts can exceed that denominator.

The earlier swing edge evaluator now refuses to silently remove a selected holding whose future return is
missing. Old published trial results remain unchanged and require a separate data-outcome audit before reuse.
The market watcher also now appends every returned ATM CE/PE and approximately 1%-OTM wing book it requests to
`data/options_quotes.duckdb`,
including source/receipt times, contract identity, bid/ask size, five-level depth and a quality flag for missing
or crossed books. This starts prospectively when the owner runs the watcher with a valid Kite session. It does
not retroactively produce history and does not cover the *full* option chain yet.

## Training observations, not recommendations

The 2016-2021 permitted training run produced 2,950 long-option signal rows. Four simple five-session
trend/reversal by CE/PE variants were examined. Three had negative mean price-proxy returns. Reversal CE had
a +1.21% mean among 562 priced exits, but a -6.47% median and -5.96% 5%-trimmed mean; assigning a total loss to
its 39 unavailable exits changes the mean to -5.36%. It is a fragile, outlier-driven *hypothesis*, not an edge.
The eight directional CE/PE debit/credit vertical variants also had negative mean proxies. Re-running with
different costs is a sensitivity study, not a licence to pick the most flattering result. Their overlapping
five-session positions were not treated as independent samples and no significance claim is made.

## Next candidates and falsification requirements

| Domain | Mechanism worth testing next | Required data and failure test |
|---|---|---|
| Swing long/short | Delayed post-disclosure repricing conditional on dissemination time and crowding, not candle shape | Point-in-time filing timestamps, survivorship-complete security lineage, next-session executable entry; beat equal-turnover filing peers and index after costs. Prior earnings tests failed, so a new mechanism must be genuinely distinct. |
| Swing risk overlay | Avoid high adverse-selection or unexitable names, including failed delistings and repeated gap days | Compare *kept plus missed winners* against the unfiltered book; do not report only avoided losers. |
| Intraday | Liquidity/impact-aware execution or a no-trade filter around auction, circuit and wide-spread states | Timestamped two-sided quotes, next-bar fills, exchange sessions and broker execution logs; beat a schedule-matched random/null and a simple passive execution baseline. Existing direction rules failed. |
| Long CE/PE | Difference between option-implied and subsequently realised movement, with event/time-of-day conditioning | Historical executable bid/ask/depth, strike/expiry identity, PIT lot size and all fees; show both early/late training stability and an untouched prospective period. Daily closes alone cannot do this. |
| Call/put debit/credit spreads | Defined-risk volatility or skew dislocation, never naked short options | Both legs quoted and fillable at the same timestamp, margin/physical-settlement constraints, tail stress and actual broker cost receipts; compare against no-trade and simple vertical baselines. |

The practical priority is to expand this narrow quote archive to the full live option chain and broker fills with timestamps, including
failed or rejected orders, then register one distinct mechanism for the next permitted trial period. A useful
research system must be able to output **no trade**. The current stock/options watcher remains a scenario and
review tool, not an automatic call/put buy or sell recommender.

Exchange definitions and settlement must be versioned with each study: see the
[NSE equity-derivatives contract specifications](https://www.nseindia.com/static/products-services/equity-derivatives-contract-specifications)
and [NSE settlement mechanism](https://www.nseindia.com/static/products-services/equity-derivatives-settlement-mechanism).
The [SEBI individual F&O profitability study](https://www.sebi.gov.in/reports-and-statistics/research/aug-2026/study-profitability-of-individual-traders-in-the-equity-derivatives-segment-fy25-fy26-_103835.html?outputType=chromeless)
is a useful risk baseline, not evidence for or against any particular system strategy.
