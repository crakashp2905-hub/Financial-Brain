---
type: research
tags:
  - research
  - technical
  - chart-patterns
  - candles
  - synthesis
date: 2026-11-07
status: synthesis and plan; nothing here has been computed on market data
---

# Chart patterns and candles: what is testable, and how

Sources: [[Source notes - Nison Japanese Candlestick Charting Techniques]] (about 55 candle lines and formations plus
Western-tool confluence) and [[Source notes - Bulkowski Encyclopedia of Chart Patterns]] (47 chart patterns, statistics on US
stocks 1991-96). Both are **Historical observation** under [[ADR-0004 one evidence standard]]. Nothing below spends the
2026Q4 trial budget (13 of 13 used); no returns were computed on any data to write it.

## 1. The two books are different kinds of source

* **Nison** is a definitions-and-folklore source: what each shape is called, what it is said to mean, and how to combine it
  with trendlines, retracements, averages, oscillators and volume. It gives no statistics at all. Its value to us is a
  complete, OHLC-only vocabulary and a list of conditions the author considers important (trend before, gap, penetration depth,
  volume, location at support/resistance, confirmation by the next bar).
* **Bulkowski** is a measurement source: per pattern, how often it was found, how often it "failed", how far price went and
  how often a projected target was reached. Its value to us is the *identification rules* (precise enough to code), the
  *trading rules the author used* and, with care, the *shape of the claims*.

## 2. Why Bulkowski's numbers are not expected returns

1. **Conditioned on the outcome.** A pattern only counts once price *closes beyond* its boundary. Of about 1,505 lookalike
   double bottoms only 542 (36%) ever closed above the confirmation point; of 1,280 double tops, 826 (65%) never closed below
   the valley and were "not double tops". The reported failure rate (3% and 17%) is among patterns already confirmed. A trader
   who buys the second low or shorts the second peak faces the *unconditional* odds, which the book itself puts at "one in
   three". The same structure holds for triple tops/bottoms, rectangles, H&S (93% "success" is among completed patterns).
2. **Maximum favourable excursion, not a return.** "Average rise/decline" runs from the breakout day to the *ultimate*
   high/low, defined as the extreme before a 20% reversal. It cannot be captured; the author's own "most likely" figure
   (typically 10-20% for bullish patterns, 5-15% for bearish) is the more honest scale, and he calls the mean a best case.
3. **A strong bull market.** 500 stocks, mid-1991 to mid-1996 (S&P 500 rising almost throughout); bullish patterns count more
   and look better. Rare patterns were topped up from 1996-99.
4. **No costs, no benchmark, no multiple-testing control, no random-entry comparison.** 47 patterns with several variants
   each were examined; some thresholds were chosen after looking ("verified out of sample" is asserted, not shown). Measure-rule
   hit rates reach 80% only after the target is shrunk (for example half the height) post hoc.
5. **Hindsight in identification.** Patterns were found by program and by eye on completed charts; gap types (breakaway,
   continuation, exhaustion) are labelled by what price did next.

## 3. Where the books agree with what this project has already measured

| claim in the books | what we have |
|---|---|
| Single candles (hammer, shooting star, engulfing, stars, harami) predict reversals (Nison) | Seven daily candle patterns all rejected, the directionless doji control fired at t -13.6; K7 (17 chart/candle setups in futures) failed G3 with bear/bull engulfing, morning/evening star significantly *negative* ([[Charts, candles and indicators]], [[PRE-REGISTERED chart and candle setups in futures]]). |
| Hanging man is a top signal (Nison) | Bulkowski tested it directly on 56,000 cases: a higher high next day 44-48%; only a third reversed. |
| Fibonacci, Gann, harmonics, Camarilla, round numbers | Deep feature library: golden pocket, extensions, Gann, Gartley null; Fibonacci nearness slightly *opposite*; BOS-up reversed ([[Result of the deep feature library]]). Bulkowski's measured-move retraces show no 38/62% clustering either. |
| Volume rules (high on left, low on right, breakout surge) | Bulkowski's own tests mostly find them weak or reversed. |
| Intraday candle shapes | Explained by the bid-ask bounce ([[Intraday candles and the bid-ask bounce]]). |

## 4. The one pattern in the patterns

The best-scoring Bulkowski patterns are **continuation breakouts in uptrends near highs**: rectangle tops with up-breaks,
ascending triangles, cup-with-handle, flags and high-tight flags, rounding tops, falling wedges inside uptrends. That is
12-month momentum and distance-from-52-week-high in another costume, and those two are the only features that replicated here
across four size tiers ([[Four edges that replicate, and the chart patterns that do work]]). So the sharp question is not "does a
triangle work?" but **"does a confirmed pattern breakout add anything to momentum and 52-week-high proximity?"** Bearish
reversal patterns (double top, H&S top) have the opposite problem: rare, mostly fail to confirm, and the average decline after
confirmation is only 15-23% with a modal 10-15%.

## 5. What exists in the repo and what does not

* **Built (features/library, evaluation):** the candle features (hammer, shooting star, engulfing, harami, piercing, dark
  cloud, stars, soldiers/crows, marubozu, doji), sweeps, break of structure, fair-value gaps, 55-day highs on volume,
  unfilled gaps, Fibonacci/pivot/Camarilla/Gartley features, Donchian, Ichimoku, plus the technical zoo rules.
* **Not built at all:** double and triple tops/bottoms, head-and-shoulders (both), triangles, wedges, flags and pennants,
  rectangles, cup with handle, rounding tops/bottoms, diamonds, bump-and-run, horns/pipes, island reversals, outside/inside
  days as signals, weekly reversals, measured moves, scallops; from Nison: windows (range gaps) as a rule, tasuki, gapping plays,
  rising/falling three methods, belt hold, counterattack, separating lines, tweezers, springs/upthrusts, change-of-polarity
  levels; Elliott; Market Profile; open-interest/volume confluence rules.
* **Data we have that the books lacked or used less:** 10 years of Indian daily bars with corporate-action adjustment, F&O
  history with open interest and a participant split, delivery share, minute bars for a 610-name survivor universe.

## 6. How to test it properly (design rules for a future registration)

1. **Causal detection.** Pivots are only known after the confirming bars (a 5-bar fractal is known two bars later). Every
   detector must use data up to the decision bar only; unit tests on synthetic series, including a look-ahead test that
   truncating the future cannot change any earlier signal.
2. **Two clocks.** Report forward returns from the *detection* date (what a trader can act on) and from the *confirmation
   close* (Bulkowski's clock), side by side, because the gap between them is the selection bias above.
3. **Fixed horizons, no ultimate high.** 5, 20 and 60 sessions, net of the intraday/delivery/futures cost schedule as
   appropriate; next-open entry; no peak-picking exits.
4. **Controls.** Universe-matched and momentum-matched (12-1 and 52-week-high decile) and volatility-matched random dates in the
   same stocks, plus placebo "patterns" with the direction shuffled. The doji control lesson: always include one.
5. **Specification fixed from the book, once.** Use the books' identification tables as the spec (they are the pre-registered
   definition); no tuning of tolerances; one family, family-wise error control (SPA/Reality Check, PBO) as for A10/K7/B4.
6. **India specifics.** Overnight gap behaviour in cash stocks differs from 1990 futures (Nison's own caveat); circuit limits;
   short exposure only through F&O names; the 2016-21 and 2022-26 halves must each clear |t| >= 2.
7. **Detection rates first.** Before any return is computed, check that the detectors find patterns at roughly the book's
   frequencies (for example confirmed double bottoms about a third of twin-low lookalikes). That is a unit-test-level check,
   not a trial.

## 7. Candidate hypotheses, ranked by prior (none registered)

| id | hypothesis | prior |
|---|---|---|
| P1 | Confirmed break above flat resistance (rectangle top, ascending triangle, cup-with-handle) in an uptrend near 52-week highs earns more than momentum-matched controls | low-moderate (momentum overlap) |
| P2 | Double bottom, confirmed, bought after the throwback | low |
| P3 | Head-and-shoulders bottom after confirmation | low |
| P4 | Bump-and-run top, short via F&O | low |
| P5 | Springs and upthrusts (failed range breaks) revert to the other side of the range | low-moderate (stop and target are explicit) |
| P6 | Windows (range gaps) as support/resistance and continuation | low |
| P7 | Weekly reversals with a wide range | low |
| P8 | Nison "confluence": candle at a prior support/resistance level | very low, forking-paths risk, register last |

The honest expectation after 13 straight failures is another null; the value of the exercise is closing the question with the
author's own definitions rather than leaving "chart patterns" as an untested belief. The earliest it can run is 2027Q1
(the 2026Q4 budget is spent), as one family, after the detectors and their tests exist. Building detectors and tests does
not touch the budget.

## 8. Safeguards

No book rule goes to the terminal, the paper engine or the OMS on the strength of this note. Terminal text may *describe* a
pattern as a Historical observation only. The catalogue file records each pattern's book definition, the book's reported
numbers (as claims) and the data needed to test it.
