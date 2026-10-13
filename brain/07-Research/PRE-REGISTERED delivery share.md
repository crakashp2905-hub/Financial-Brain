---
type: research
tags:
  - research
  - pre-registration
  - delivery
  - fno
date: 2026-10-13
status: pre-registered
---

# PRE-REGISTERED delivery share

Written on 2026-10-13 under [[ADR-0004 one evidence standard]], **before the data was loaded** (the prefetch was
running when this was committed) and before anything below is computed. Trial 8 of 2026Q4's budget (the last).

## Why

Every share traded on NSE is either squared off within the day or taken to delivery. NSE publishes the delivered
share per security per session (the MTO file, 2015 onward - new to this project, `ingest/delivery.py`). Delivery
is investment demand; intraday volume is speculation. A rising delivery share is the textbook Indian-market sign of
accumulation by holders who intend to keep the stock. The data is orthogonal to every price, volume and filing
signal tested here.

## The book, fixed

* **Names**: F&O stocks with a live near-month contract at the rebalance (`data/basis_contracts.parquet`), series EQ,
  joined to prices through the ticker each session carried (`adjusted_prices`).
* **Score**: mean delivery % over the last 20 sessions minus mean delivery % over the 120 sessions before those
  (at least 15 and 90 sessions present).
* **Rebalance**: every 20 sessions from the first session of 2016 with the history; held 20 sessions.
* **Book**: long the top fifth by score, short the bottom fifth, equal weight, equal gross on each side
  (market-neutral; no index hedge).
* **Costs**: 5 bp one way on every unit of weight changed, both legs (stock futures).
* **Measured**: net return per rebalance.

## The bar

G3: positive mean with t >= 2 in 2016-2021 and in 2022-2026. G4: above the 95th percentile of 200 random long-short
books with the same leg sizes from the same eligible names.

## Prediction

* **D1** positive in both halves; fails G3 (about 70%) - delivery share in large caps is dominated by index and
  arbitrage flows that carry no view.

Information, not advice.
