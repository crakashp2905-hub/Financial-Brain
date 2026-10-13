---
type: research
tags:
  - research
  - pre-registration
  - volatility
  - fno
date: 2026-10-13
status: pre-registered
---

# PRE-REGISTERED betting against beta in futures

Written on 2026-10-13 under [[ADR-0004 one evidence standard]], before anything below is computed. Trial 6 of
2026Q4's budget (8).

## Why

Low volatility orders Indian stocks better than anything else measured here (rank IC t +12.3, replicated in four
size tiers) and still failed: long-only, its beta to the market was 0.91-0.97, and the alpha at that beta was
negative ([[Four edges that replicate, and the chart patterns that do work]]). Frazzini & Pedersen (2014) state the
mechanism precisely - leverage-constrained investors overpay for high-beta stocks, so the low-beta premium is earned
by a **beta-neutral** book: long low-beta levered up, short high-beta levered down. Stock futures make both legs
cheap (5 bp one way) and the short leg possible. The volatility family has not been tested this way here.

## The book, fixed

* **Names**: F&O stocks with a live near-month contract at the rebalance (`data/basis_contracts.parquet`), with 252
  sessions of returns.
* **Rebalance**: the first session of each month from 2016-02, held to the next.
* **Beta**: shrunk time-series beta to the Nifty 50 price index on daily returns over the previous 252 sessions,
  `0.6 x beta_ts + 0.4 x 1.0` (Vasicek-style shrinkage toward one, as Frazzini-Pedersen do).
* **Legs**: the bottom third by beta long, the top third short, equal weight within each leg; the long leg scaled to
  1 / beta_L and the short leg to 1 / beta_H (each leg's average shrunk beta), so the book's ex-ante beta is zero.
* **Return**: each leg's spot return minus the risk-free rate (6.5% a year, by days) - a future's return is close to
  spot minus financing - so the book earns `(r_L - rf) / beta_L - (r_H - rf) / beta_H`.
* **Costs**: 5 bp one way on every unit of notional changed, both legs, levered notional counted.
* **Measured**: net return per month.

## The bar

* G3: mean positive with t >= 2 in 2016-2021 and in 2022-2026.
* G4: above the 95th percentile of 200 books with the same leg sizes, legs drawn at random from the eligible names
  (with the same beta scaling applied to the random legs' own betas).

## Predictions

* **BABa** gross positive in both halves (the ordering is known to exist).
* **BABb** fails G3 - realised betas of low-beta Indian stocks drift up in sell-offs, which the shrinkage only partly
  fixes. Chance of passing about 30%.

Information, not advice.
