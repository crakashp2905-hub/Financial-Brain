---
type: journal
tags:
  - journal
  - paper-trading
date: 2026-10-07
---

# 2026-10-07: why the paper trades lose

The owner asked why the paper trades lose money and what the brain is lacking. This note records the replay of
last week (28 Sep - 1 Oct 2026, replayed with today's code, labelled `replay-lastweek`) and the diagnosis. Full
trade lists: `data/paper_reports/replay-lastweek-2026-09-28.md`, ledgers `data/paper_replay_lastweek.duckdb`,
`data/paper_book.duckdb` (mode `replay-lastweek`), `data/paper_zoo_replay_lastweek.duckdb`.

## Where the money went

| Part | Trades | Net |
|---|---:|---:|
| Intraday (ORB + GAP, live-v1), 4 days | 237 | **-Rs 87,143** |
| Ranking v2 book, top 20 (to the 7 Oct close) | 20 | +Rs 39,067 |
| Momentum book, top 20 | 20 | +Rs 23,884 |
| Learned-model book, top 20 | 20 | +Rs 7,098 |
| Swing book, top 50 (7 sessions) | 50 | -Rs 11,542 |
| Original top-5 book (book-v1) | 10 | -Rs 9,695 |
| Live intraday, 7 Oct | 69 | -Rs 12,653 |

The losses are almost all intraday. The 20-session books built on tested signals were up together (+Rs 70,049 on
about Rs 24.6 lakh); the short-horizon swing and the original top-5 books were down, as their replays said they
would be.

## Why intraday loses: there is no edge to pay the fee

* **Gross ~ 0.** Over 251k historical opening-range breakouts the trade earned -0.2 to +6 bp before costs (Result of
  the opening-range breakout with stops). Last week: -32 bp gross a trade; 7 Oct live: -4 bp.
* **The fee is certain.** About 8.5 bp a round trip in the paper engine (14-24 bp with impact in the research).
  A zero-edge trade pays it every time: 60 trades a day x 8.5 bp is a steady leak.
* **Payoff shape.** Win rate 35% last week, 43% live; the average win (Rs 1,157) is about the average loss (Rs 1,285),
  so the stop does not create a payoff - it moves it around (mean R -0.23). 98 of 237 trades stopped out at -161 bp
  each.
* **The signals are slow.** Intraday half-life study: nothing predicts 5-60 minute returns well enough to beat
  5-25 bp of cost; the factors that do predict (52-week high, low volatility, momentum) work over weeks, not minutes.
* The market-trend filter added nothing out of sample (Result of trading with the market trend); last week the
  trades with the market lost more than those against it.

## Why the swing and top-5 books lose

* Too short and too costly: a 7-session hold pays a ~0.3% round trip for signals whose information plays out over
  20-60 sessions (half-life table). Replay: -0.63% a trade vs the universe (t -5.6).
* The top-5 list was tie-broken toward large, cheap-to-trade names (fixed later), and five names is mostly noise.

## What works, as tested

Slow, cross-sectional, low-turnover: the learned model (+0.77%/month net 2022-26, t 2.3), momentum / 52-week high,
rich-basis carry (small but steady), and splitting capital by trailing Sharpe (+11.4% a year 2022-26 vs +7.7% equal
weight). None of these clears the deflated-Sharpe bar after 330 trials; they are the best evidence there is, not proof.

## What the brain lacks

1. **An intraday edge.** It would need information other traders lack at that speed: order-book and order-flow
   history (the quote archive started 2026-10-06 - months away from a test), intraday F&O open-interest build-up,
   timed news. Without it, intraday is a measured cost.
2. **Different information for the slow strategies.** 2025+ fundamentals (blocked on NSE Integrated Filing), earnings
   dates and surprises, delivery percentage, FII/DII flows, bulk and block deals, insider (PIT/SAST) disclosures,
   promoter pledges. These are where documented Indian-equity effects live, and none is in the lake yet.
3. **Turnover discipline.** Capital goes where trades are many, not where evidence is.
4. **Time.** A real +0.77%/month edge needs about two years of monthly results to show at t = 2. One week, or one
   day of live, says nothing either way; the live record measures the rules, it does not tune them.

## What follows

No live-v1 rule changes because of P&L. Any shift (e.g. moving paper capital from intraday to the 20-session books
and the Sharpe allocator, retiring the swing book, adding a data source) is a new pre-registered version, made when
the owner asks for it.

Information, not advice.
