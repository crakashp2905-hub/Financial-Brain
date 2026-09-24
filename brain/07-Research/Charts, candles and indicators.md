---
type: research
tags:
  - research
  - technical-analysis
  - firewall
date: 2026-09-24
---

# Charts, candles and indicators

## The audit that prompted it

Asked how well the brain understood charts, the honest answer was: barely, and it had
never been measured. It held returns over five horizons, 12-1 momentum, two realised
volatilities and distance from the 52-week high. **No oscillator, no band, no true range,
no directional index, no volume indicator, and no candlestick logic of any kind.** Chart
patterns - head and shoulders, triangles, flags, double tops - are still absent; they are
multi-bar geometric fits rather than per-bar arithmetic and were not attempted.

"Candlesticks do not work" was an *inherited belief* here, not a measurement, and an
inherited belief cannot be cited in a decision.

## Ten indicators, all rejected

Built to published definitions (Wilder, Appel, Lane, Williams, Bollinger, Lambert,
Granville) and tested cross-sectionally. Pre-registered as [[h9]].

| | t | died on |
|---|---:|---|
| rsi_14 | -0.24 | nothing there, 81% turnover |
| stoch_k_14 | +0.16 | nothing there, 79% turnover |
| williams_r_14 | +0.15 | nothing there, 79% turnover |
| cci_20 | -0.63 | nothing there, 84% turnover |
| adx_14 | +1.57 | nothing - **as predicted** |
| macd_hist | -1.64 | costs |
| obv_slope_20 | -2.31 | costs |
| mfi_14 | -2.85 | costs |
| atr_14_pct | -3.82 | costs |

The four bounded oscillators came in at |t| < 0.7 on ~80% turnover, exactly as the
pre-registration said they would: *the same economic claim as short-term reversal wearing
different arithmetic*. `adx_14` measures trend strength without direction and showed
nothing, which is the correct answer and now a measured baseline rather than an assumption.
`atr_14_pct` is the low-volatility effect arriving for a third time.

**One prior was wrong.** `macd_hist` was expected to lean slightly positive, because the
golden cross reached t = +2.74. It came in at -1.64. The likely reconciliation is horizon:
12/26 is fast trend, which sits in short-term-reversal territory, while 50/200 is slow.
That is a hypothesis, not a conclusion, and it is recorded as a wrong prior rather than
quietly dropped.

## Seven candlestick patterns, all rejected - and the control fired

Pre-registered as [[h10]] with a deliberately weak, negative prior (Marshall, Young & Rose
2006; Horton 2009). Each pattern held 20 sessions after it fires.

    hammer            t  -5.79      morning_star      t  -7.09
    shooting_star     t  -9.01      marubozu_bull     t  -5.94
    bullish_engulfing t  -6.01      doji              t -13.60
    bearish_engulfing t  -9.86

Every one negative, including the two that are supposed to point in opposite directions.

**doji was pre-registered as a control, not a candidate** - it signals indecision, carries
no direction, and the pre-registration said that if it showed a signal, *the harness is
wrong rather than the pattern right*. It showed the largest t in the set.

So the harness was investigated before the result was believed.

## What the investigation found

**A volatility confound, for six of the seven.** The directional patterns select names
with 1.09x to 1.34x the universe's ATR, and high ATR was independently measured at
t = -3.82 in the same session. Their negative result is substantially the low-volatility
effect wearing candle shapes.

**Not for doji.** It selects names at 0.98x universe ATR - dead average - yet has the
largest t.

**A structural explanation, proposed and then refuted.** The equal-weighted arithmetic
mean is carried by a right tail of large winners. A doji is by definition a small-body
session, so a doji basket might systematically under-sample that tail and lose to the mean
without predicting anything. Testable, and tested: a **random basket of the same size**
(44% of the universe, held the same way) returned **-0.00056%/session, t = -0.28**.
Essentially zero. The harness does not penalise subsets, and the hypothesis is wrong.

## Where that leaves it

The doji deficit is real against a random basket of equal size. Two explanations survive
and this work does not separate them: either indecision genuinely carries a small negative
signal, or selecting on *quietness* correlates with missing the skewed tail in a way that
random selection does not. Distinguishing them needs a control matched on characteristics
rather than on size alone, which is the next step and is not done.

The magnitude is small either way - about -2% a year - and the t of -13.60 is large mostly
because the basket holds 886 names, so tracking error is tiny.

**What can now be said, which could not be said yesterday:** candlestick patterns in this
universe select on volatility characteristics, and volatility is priced here. That is a
measurement. "Candlesticks do not work" remains roughly the right conclusion, but it is
now held for a reason that can be checked, and the reason is not the one usually given.

Related: [[The golden cross and the bar it did not clear]] ·
[[Trend is the only thing that has cleared significance]] ·
[[Alpha Validation Firewall]] · [[MOC Strategies]]
