---
type: research
tags: [research, result, intraday, chart-patterns, candles, indicators, family]
date: 2026-11-11
status: result; 35 of 35 candidates FAIL; trial 14 of 2026Q4 (budget 14, ADR-0004 amendment 5)
---

# Result of intraday chart candle and indicator family

Registered first in [[PRE-REGISTERED intraday chart candle and indicator family]] (commits `6345449`, `efb9fe4`; engine
`6a103b2`); the run used exactly those definitions. Ledger row: `V2 intraday chart candle and indicator family (40
candidates)`, verdict **FAIL**. Raw output: `data/edges/v2_result.json`, `data/edges/v2_events.parquet` (local, not committed).

## Verdict

**No rule passes G3**: none reaches |t| >= 2 with a positive sign in both 2016-2021 and 2022-2026 (net of a 10 bp round
trip). G4 (random-entry null) was therefore not run for any rule. There is no CANDIDATE and nothing to shortlist as an edge.

* Universe: 207 F&O names scanned, the 100 most liquid each quarter, 2,353,754 trades, 2016 to 2026-10-08.
* Family tests (31 rules with at least 100 judgeable months, 122 aligned months): Reality Check p
  0.498, SPA p 0.490; the best rule by t is L7 at t +0.68. Bonferroni |t| for 35 rules: 3.19.
* PBO 0.00 is **not** evidence of robustness: every rule loses roughly the cost, so the in-sample rank among them
  persists out of sample (in-sample to out-of-sample slope -0.90).
* Five candidates were untestable on data we hold and are recorded as such: L14 (open-interest build-up), L16 (ML skip
  filter), L17 (results-day reaction), L19 (option overlay), L20 (order-book imbalance).

## Per rule (2016-2021 / 2022-2026; bp per trade; t of the monthly net series)

| id | rule | trades | gross bp | net bp | t (net) | verdict |
|---|---|---|---|---|---|---|
| L0 | live-v1 ORB (control) | 135,311 | +2.1 / +4.5 | -7.9 / -5.5 | -6.83 / -5.10 | CONTROL |
| G0 | live-v1 GAP (control) | 2,603 | +22.9 / +4.2 | +12.9 / -5.8 | +2.19 / -1.00 | CONTROL |
| L1 | ORB, 09:30 side filter | 34,673 | +0.7 / +8.9 | -9.3 / -1.1 | -4.10 / -0.33 | FAIL |
| L2 | ORB in trend direction | 49,106 | -5.3 / +0.4 | -15.3 / -9.6 | -6.70 / -5.93 | FAIL |
| L3 | ORB range-width filter | 114,772 | +1.6 / +3.3 | -8.4 / -6.7 | -8.12 / -7.72 | FAIL |
| L4 | ORB cost gate | 133,624 | +2.1 / +4.5 | -7.9 / -5.5 | -6.78 / -5.17 | FAIL |
| L5 | ORB trailing exit | 135,311 | -0.8 / +2.0 | -10.8 / -8.0 | -13.76 / -9.27 | FAIL |
| L6 | large-gap fade | 844 | +70.2 / -2.3 | +60.2 / -12.3 | +2.76 / -0.54 | FAIL |
| L7 | gap-and-go | 8,370 | +14.4 / +11.4 | +4.4 / +1.4 | +0.99 / +0.26 | FAIL |
| L8 | opening-range spring/upthrust | 142,529 | +1.3 / +0.0 | -8.7 / -10.0 | -23.66 / -33.23 | FAIL |
| L9 | prior-day level retest | 73,448 | -1.3 / +0.4 | -11.3 / -9.6 | -29.54 / -33.35 | FAIL |
| L10 | gap-window continuation | 22,786 | +5.4 / +5.8 | -4.6 / -4.2 | -1.54 / -1.39 | FAIL |
| L11 | opening-drive exhaustion fade | 41,107 | +7.5 / +0.6 | -2.5 / -9.4 | -1.91 / -6.85 | FAIL |
| L12 | stock VWAP pullback | 143,000 | -0.5 / +0.3 | -10.5 / -9.7 | -38.98 / -40.15 | FAIL |
| L13 | relative strength vs Nifty | 48,412 | +3.6 / +7.5 | -6.4 / -2.5 | -4.33 / -1.56 | FAIL |
| L15 | regime switch | 87,807 | -3.0 / -0.2 | -13.0 / -10.2 | -10.17 / -7.47 | FAIL |
| L18 | ORB 09:30-10:15 only | 111,388 | +0.7 / +4.5 | -9.3 / -5.5 | -7.27 / -5.04 | FAIL |
| P1 | rectangle break | 148,061 | +8.5 / +4.3 | -1.5 / -5.7 | -1.93 / -9.91 | FAIL |
| P2 | triangle break | 23,158 | +1.6 / -1.8 | -8.4 / -11.8 | -6.59 / -13.09 | FAIL |
| P3 | flag / pennant | 82 | +12.9 / n/a | +2.9 / n/a | n/a / n/a | FAIL |
| P4 | double top / bottom | 3,136 | -0.5 / -1.1 | -10.5 / -11.1 | -5.47 / -7.76 | FAIL |
| P5 | head-and-shoulders | 0 | n/a / n/a | n/a / n/a | n/a / n/a | FAIL |
| P6 | cup with handle | 0 | n/a / n/a | n/a / n/a | n/a / n/a | FAIL |
| P7 | channel break | 15,812 | -1.9 / -1.9 | -11.9 / -11.9 | -8.48 / -8.40 | FAIL |
| P8 | failed gap | 22,976 | +10.0 / +7.0 | -0.0 / -3.0 | -0.01 / -1.09 | FAIL |
| P9 | throwback entry | 135,565 | +1.9 / +1.3 | -8.1 / -8.7 | -30.39 / -38.69 | FAIL |
| P10 | engulfing at opening-range edge | 85,869 | +3.5 / +1.5 | -6.5 / -8.5 | -12.07 / -16.38 | FAIL |
| P11 | hammer / star at a level | 52,097 | +1.1 / +0.3 | -8.9 / -9.7 | -24.96 / -31.01 | FAIL |
| P12 | morning / evening star | 4,354 | +8.9 / +0.7 | -1.1 / -9.3 | -0.38 / -4.83 | FAIL |
| P13 | three methods | 6,224 | +10.8 / +3.8 | +0.8 / -6.2 | +0.30 / -3.10 | FAIL |
| P14 | spring / upthrust candle | 18,655 | -1.7 / -0.0 | -11.7 / -10.0 | -15.81 / -14.64 | FAIL |
| P15 | Bollinger squeeze | 37,151 | +2.4 / +2.6 | -7.6 / -7.4 | -8.85 / -7.63 | FAIL |
| P16 | Donchian + ADX | 83,591 | -2.0 / +2.9 | -12.0 / -7.1 | -8.07 / -4.95 | FAIL |
| P17 | RSI divergence at a level | 42,882 | +3.3 / +3.8 | -6.7 / -6.2 | -4.93 / -4.87 | FAIL |
| P18 | EMA ribbon pullback | 82,351 | +0.1 / +1.3 | -9.9 / -8.7 | -14.53 / -12.32 | FAIL |
| P19 | volume-spike breakout | 138,666 | +6.4 / +4.7 | -3.6 / -5.3 | -4.97 / -6.14 | FAIL |
| P20 | confluence score | 168,033 | +6.2 / +4.2 | -3.8 / -5.8 | -2.89 / -7.74 | FAIL |

## What it says

1. **Cost decides it.** Most rules earn -2 to +10 bp gross against a 10 bp round trip, so net sits near -10 bp every month;
   the very large negative t-values (L12 -39, P9 -39) are consistency of a cost drag on roughly zero gross, not a wrong-signed
   signal. The inverse trade pays the same cost on the same near-zero gross, so inversion is not a hypothesis.
2. **ORB cannot be filtered into profit.** The control L0 loses 6 to 8 bp net over 135k trades, as the earlier backtest found.
   All seven filter variants built from the obvious lessons (L1-L5, L15, L18) stayed negative net in both halves.
3. **Closest to anything: L7 gap-and-go**, +4.4 / +1.4 bp net, t +0.99 / +0.26, fading in the later half. P8, P1, P19 and P20 are
   gross-positive in both halves (+4 to +10 bp) and still lose net. L6 and the GAP control look good in 2016-21 and negative
   after, the instability G3 exists to catch.
4. **Candles and indicators:** nothing survives. Even where gross is +3 to +4 bp (P10, P17) the net is -6 to -8 bp. This agrees
   with the earlier minute-candle (bid-ask bounce) and daily candle findings.
5. **Book chart patterns were not really tested.** P3, P5 and P6 produced 82, 0 and 0 trades. The registered default detector
   settings were built for daily bars (a flag needs an 8% pole in 8 bars; a cup 12% depth; head-and-shoulders produced no
   formation of any state on 123 RELIANCE sessions). They are unevaluated, not failed. A proper intraday test needs an
   intraday-scaled parameter set registered separately (earliest 2027Q1).

## Limits and disclosures

* Survivor-only universe (currently listed names); 5- and 15-minute aggregation hides intrabar order; stop-before-target on
  a bar that reaches both is conservative; shorts assume stock futures are available.
* Before the full run the engine was run on one stock (SBIN, 2021-01 to 2022-06) as a pipeline check and printed per-rule
  averages. No rule, parameter or threshold changed afterwards; the engine commit precedes the full run.
* Cost is the registered 10 bp (live schedule about 8 bp); a 20 bp sensitivity is stored in the events file and not judged.

## What follows

* Nothing is promoted and nothing enters the paper engine on this result.
* The 2026Q4 budget is now 14 of 14. The next trial can be registered for 2027Q1 at the earliest.
* Candidates for a *shadow* paper run, if the owner wants one, are leads only (L7, P8, P1, P19, P20) and would be a new
  pre-registered `live-v2`, not part of this trial.

Information, not advice.
