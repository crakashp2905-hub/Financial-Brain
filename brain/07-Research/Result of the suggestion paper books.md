---
type: research
tags:
  - research
  - result
  - paper-trading
  - swing
date: 2026-10-05
status: result
---

# Result of the suggestion paper books (replays)

Pre-registrations: [[PRE-REGISTERED suggestion paper book v1]], [[PRE-REGISTERED swing paper book v1]]. Both
replays use an expected-return model measured on 2016-2026, so they are **in-sample**: the best these books could
look, not evidence. Benchmark: the liquid universe bought at each trade's entry close and held to its exit close.

## book-v1 (top 5 position names, 60 sessions), 2023-01 to 2026-10

147 sold. Win rate 50%, mean net +0.79% a trade, **mean excess -3.08% (t -2.54)**, beat the universe 36% of the
time, 14% stopped. Rs 91,195 net against Rs 3,62,715 for the universe on the same money.

* P1 (positive excess under 1%) **FAIL** - negative.
* P2 (beat-the-universe rate 45-60%) **FAIL** - 36%.
* P3 (stops under 20%) **PASS** - 14%.

Why: the decile model gives every name in a decile the same expectation, so the ranking broke ties by cost, which
picked the largest, most liquid names. Through the 2023-26 small-cap rally that is a size bet that lost.

## swing-v1 (top 50, 7 sessions), 2023-01 to 2026-10

5,607 sold over 690 entry days.

| per trade | |
|---|---|
| gross return | +0.61% |
| universe over the same days | +0.63% |
| **gross excess (before costs)** | **-0.02%** |
| round trip (sized, incl. DP charge) | 0.36% |
| net return | +0.25% |
| **net excess** | **-0.37% (t -3.98)** |

Win rate 48%, beat the universe 42%, 1% stopped (the swing stop at 2 sigma over 7 sessions is rarely reached).
Rs 2,72,043 net against Rs 6,83,463 for the universe on the same money. By entry year, net per trade: 2023 +0.85%,
2024 +0.43%, 2025 -0.05%, 2026 -0.42%; the excess is negative in every year.

* S1 (net excess negative) **PASS.**
* S2 (win rate under 55%) **PASS.**
* S3 (one week decides nothing) - the week of 2026-09-28 is shown as mechanics only.

## What it says

The swing ranking has **no edge before costs** (-0.02% a trade against the universe), so after costs it loses
almost exactly its round trip. The positive absolute return in 2023-24 was the market's, which the universe earned
more of. This is the same finding as the swing-setup study and the intraday ORB backtest: where gross is ~0, net is
minus the cost, every time. The books keep running live because the live record is the only out-of-sample check,
but nothing here suggests a swing or position book built on this ranking beats simply holding the universe.
