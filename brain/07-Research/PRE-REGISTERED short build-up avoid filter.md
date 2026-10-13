---
type: research
tags:
  - research
  - pre-registration
  - fno
date: 2026-10-13
status: pre-registered
---

# PRE-REGISTERED short build-up avoid filter

Written on 2026-10-13 under [[ADR-0004 one evidence standard]], before anything below is computed. Trial F3 of
2026Q4's budget.

## Mechanism

In Indian cash equities a short position cannot be carried overnight; stock futures are the main short channel, so
a rise in futures open interest while the price falls is new short positions being opened. Short sellers are, on
average, informed: stocks with high or rising short interest earn lower subsequent returns (Asquith, Pathak & Ritter
2005; Boehmer, Jones & Zhang 2008). This is the one F&O configuration not yet tested here: F1 (price up, OI up) and
F2 (price up, OI down) both failed ([[Result of edges for every mode]]); price down with OI up has not been looked at.
It is orthogonal to every price-only signal tested, because it needs the positioning data.

## The test, fixed

* **Names**: stocks with futures open interest that session (`evaluation/edges.fno_panel`), index futures excluded.
* **Dates**: every 20 sessions from the first session of 2019 with five sessions of OI history (non-overlapping).
* **Flag**: 5-session spot return below zero **and** 5-session change in futures OI in the top fifth of all F&O
  names that session.
* **Outcome**: the flagged names' mean spot return over the next 20 sessions minus the equal-weighted mean of all
  F&O names - gross, because the use is to **avoid** names (as N2 was judged). Also reported, not gating: the same
  as a short in stock futures net of a 10 bp round trip.
* **Halves**: 2019-2021 and 2022-2026 (OI history starts 2019-01-22).

## The bar (ADR-0004)

* G3 with direction -1: mean negative with t <= -2 in both halves.
* G4: the replication statistic beats the 95th percentile over 200 random sets of F&O names of the same size each
  date (and the same carry-over between dates).

## Predictions

* **F3a** the mean is negative in both halves.
* **F3b** it fails G3 - single-stock OI is noisy and F1/F2 found nothing in the same data. Chance of passing about 25%.

If it passes it becomes an exclusion in the momentum book's next registered version (never retrofitted into the
current one) and an avoid flag in the brief. Either way it is paper-traded in the zoo with its verdict.

Information, not advice.
