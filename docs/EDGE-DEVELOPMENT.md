# Edge development: swing, intraday and options

## MQL5 / Pine-inspired mechanisms (2026-10-24, research only)

`evaluation/script_mechanisms.py` implements three **independently specified** ideas; no
third-party source code was copied. The point is to test a mechanism under Indian data and
costs, not to import an author's equity curve:

1. A 55-session stock breakout conditioned on **yesterday's** Nifty 50 regime (Nifty close
   above its 200-session average). This is compared with the same breakout without the
   regime condition; signal at the completed close, earliest entry next session open.
2. A 20-session breakout after a prior 10-session true-range average below 65% of the
   prior 60-session average, with current volume above the previous 20-session median.
   It is a frozen compression mechanism, not another optimised Supertrend variant.
3. A stock intraday **no-trade** gate using a fresh two-sided depth book, requested size,
   spread and visible book-walk. The default 15 bp spread and 25 bp walk caps are
   explicit engineering assumptions, not tuned or validated thresholds. It never adds a trade. `liquidity_counterfactual` compares
   a frozen baseline's kept trades with *all* skipped trades, counting missed winners.

Run a single-symbol training diagnostic in PowerShell:
`$env:PYTHONPATH='src'; python -m financial_brain.evaluation.script_mechanisms --symbol RELIANCE`.
The runner reads Kite minute bars and the research DB
read-only, and refuses observations after 2021. It uses a provisional 20 bp stock round
trip; this is not measured slippage. The two swing rules currently make no live alert,
paper order or trial-ledger entry. The liquidity gate needs archived decision-time quote
books paired with frozen baseline paper trades before it can be scored historically.
The existing quote/paper archives start in the later forward period; they are not a
sealed training set for tuning this filter or rewriting frozen ORB/GAP rules.

Initial four-name convenience check (RELIANCE, TCS, INFY, HDFCBANK), **not a sampled
cross-sectional universe**: each has 1,477 training sessions. The regime-conditioned
breakout had lower mean net than the same unfiltered breakout on RELIANCE (0.551% vs
0.734%), INFY (0.325% vs 0.698%) and HDFCBANK (0.004% vs 0.095%); TCS was less
negative (-0.582% vs -0.662%). The strict compression rule selected only 0, 2, 7 and
1 observations respectively—far too few for inference; their outcomes vary in sign.
Returns overlap, use raw minute prices without a complete corporate-action/survivorship
audit, and assume next-open entry without a measured fill. The regime condition did **not**
show consistent incremental benefit, and there is no reason to call any of the three "best"
or tradeable. Do not tune these thresholds to RELIANCE or query the 2022–2026 holdout to
rescue a result. First gather data-quality and execution evidence; a distinct registered
trial can be considered under ADR-0004 in the next permitted period.

Source-method cautions: [TradingView's repainting guidance](https://www.tradingview.com/pine-script-docs/concepts/repainting/)
and [broker-emulator documentation](https://www.tradingview.com/pine-script-docs/concepts/strategies/)
explain why a Pine chart backtest cannot substitute for next-bar execution and observed
quotes. [MQL5's strategy-testing documentation](https://www.mql5.com/en/docs/runtime/testing)
is a research workflow, not validation on NSE. [Kite's historical-data documentation](https://kite.trade/docs/connect/v3/historical/)
also limits what can be reconstructed for expired options. Respect each script's licence
before copying code; this module uses only general mechanisms.

## Research-programme diagnostics (2026-10-24)

`python -m financial_brain.evaluation.research_program` reads the existing ledgers without
writing a trial, changing M1, or promoting an OMS verdict. On 2026-10-24 it found only eight
valid M1 forward daily observations (2026-10-12 to 2026-10-22), with mean daily net excess
-0.6295 percentage points over that short sample. This is **not** an independent-rebalance
estimate or a verdict. The intraday paper-execution report belongs to ORB/GAP, **not M1**,
and its simulated fills are not broker fills. The option-book archive was absent.

The module also supplies three fail-closed research primitives: `filter_counterfactual`
charges foregone winners as well as avoided losers on a complete frozen training book;
`event_peer_diagnostic` requires publication-before-entry timestamps, matched net peer
returns, and an exit inside the sealed 2016-2021 training period; `vertical_roundtrip`
requires fresh simultaneous two-sided entry/exit books for the same defined-risk spread,
visible size and explicit total charges. They produce descriptions, not BUY/SELL calls or
new trials. The timestamp-coverage report counts field presence, not proof that every
stored timestamp is the original exchange dissemination time. In particular, do not use
`evaluation.news_model.load` as a new PIT event-test universe without changing its
2025-liquidity universe selection: that selection looks ahead for earlier events.

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
An **optional, default-off** market watcher archive can append returned ATM CE/PE and approximately 1%-OTM wing
books to `data/options_quotes.duckdb`, including source/receipt times, contract identity, bid/ask size, five-level
depth and a quality flag for missing or crossed books. Kite Connect's published terms restrict permanent copies
of its market data. Do **not** set `FB_OPTIONS_ARCHIVE_LICENSE_APPROVED=1` without express written permission for
this retention and intended research use. The code collects no option-book archive by default. Even when licensed,
it starts prospectively and does not cover the full chain. A separately licensed data vendor is another route.

## Training observations, not recommendations

The 2016-2021 permitted training run produced 2,950 long-option signal rows. Four simple five-session
trend/reversal by CE/PE variants were examined. Three had negative mean price-proxy returns. Reversal CE had
a +1.21% mean among 562 priced exits, but a -6.47% median and -5.96% 5%-trimmed mean; assigning a total loss to
its 39 unavailable exits changes the mean to -5.36%. It is a fragile, outlier-driven *hypothesis*, not an edge.
The eight directional CE/PE debit/credit vertical variants also had negative mean proxies. Re-running with
different costs is a sensitivity study, not a licence to pick the most flattering result. Their overlapping
five-session positions were not treated as independent samples and no significance claim is made.

`python -m financial_brain.evaluation.oi_positioning` adds a distinct, training-only positioning question using
the near-money Nifty put/call OI ratio in [NSE's after-close F&O bhavcopy](https://nsearchives.nseindia.com/content/circulars/MSD74764.pdf). Its prior 252-session percentile bands
are shifted one session, and outcomes begin at the **next** index open. From 1,423 eligible 2016-2021 days, the
high-ratio condition's five-session mean was +0.122% versus +0.095% unconditionally: only **+2.7 basis points**
of descriptive separation *before costs*. The next-session separation was about +3.3 bp. The low-ratio
condition was not stronger. These are index-point proxies, not futures fills, and five-session outcomes overlap.
No OI strategy has been promoted or assigned a trial verdict.

## Next candidates and falsification requirements

| Domain | Mechanism worth testing next | Required data and failure test |
|---|---|---|
| Swing long/short | Delayed post-disclosure repricing conditional on dissemination time and crowding, not candle shape | Point-in-time filing timestamps, survivorship-complete security lineage, next-session executable entry; beat equal-turnover filing peers and index after costs. Prior earnings tests failed, so a new mechanism must be genuinely distinct. |
| Swing risk overlay | Avoid high adverse-selection or unexitable names, including failed delistings and repeated gap days | Compare *kept plus missed winners* against the unfiltered book; do not report only avoided losers. |
| Intraday | Liquidity/impact-aware execution or a no-trade filter around auction, circuit and wide-spread states | Timestamped two-sided quotes, next-bar fills, exchange sessions and broker execution logs; beat a schedule-matched random/null and a simple passive execution baseline. Existing direction rules failed. |
| Intraday / swing positioning | Option OI crowding linked to subsequent index movement | A separated source and execution benchmark. The initial near-money put/call OI bands showed only 2–3 bp training separation, so this version is not ready for a registered trade test. |
| Long CE/PE | Difference between option-implied and subsequently realised movement, with event/time-of-day conditioning | Historical executable bid/ask/depth, strike/expiry identity, PIT lot size and all fees; show both early/late training stability and an untouched prospective period. Daily closes alone cannot do this. |
| Call/put debit/credit spreads | Defined-risk volatility or skew dislocation, never naked short options | Both legs quoted and fillable at the same timestamp, margin/physical-settlement constraints, tail stress and actual broker cost receipts; compare against no-trade and simple vertical baselines. |

The practical priority is to obtain data rights for a full live option-chain archive and broker fills with timestamps, including
failed or rejected orders, then register one distinct mechanism for the next permitted trial period. A useful
research system must be able to output **no trade**. The current stock/options watcher remains a scenario and
review tool, not an automatic call/put buy or sell recommender.

Exchange definitions and settlement must be versioned with each study: see the
[NSE equity-derivatives contract specifications](https://www.nseindia.com/static/products-services/equity-derivatives-contract-specifications)
and [NSE settlement mechanism](https://www.nseindia.com/static/products-services/equity-derivatives-settlement-mechanism).
The [SEBI individual F&O profitability study](https://www.sebi.gov.in/reports-and-statistics/research/aug-2026/study-profitability-of-individual-traders-in-the-equity-derivatives-segment-fy25-fy26-_103835.html?outputType=chromeless)
is a useful risk baseline, not evidence for or against any particular system strategy.
The [Kite Connect terms](https://kite.trade/terms/) govern retention and reuse of Kite market data.
