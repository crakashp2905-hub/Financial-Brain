---
type: research
tags:
  - research
  - pre-registration
  - edges
date: 2026-10-13
status: pre-registered
---

# PRE-REGISTERED edges batch three

Written on 2026-10-13 under [[ADR-0004 one evidence standard]], before anything below is computed. Trials 3, 4 and 5
of 2026Q4's budget (8). The owner asked for every route to more than one tradeable edge; these are the three with
the best prior odds that are not price-pattern searches. Each names its mechanism, uses a cost model fixed here, and
is judged by G3 (|t| >= 2, right sign, in both 2016-2021 and 2022-2026) and G4 (above the 95th percentile of the
same statistic over 200 random draws of the same size from the same pool and dates).

Shared definitions. **F&O names**: a stock with a near-month future live on that date (a contract in
`data/basis_contracts.parquet` whose start <= date <= expiry). **Hedge**: the Nifty 50 price index over the same
sessions. **Costs**: stock-future round trip 10 bp, Nifty-future round trip 4 bp. **Period series**: events
averaged by the calendar month of entry; t over months.

## C1 Rich-basis cash-and-carry

*Mechanism*: when longs crowd into a stock future, its basis rises above financing cost; the arbitrageur who buys
spot and sells the future to expiry is paid for supplying the other side ([[Result of cash-and-carry and basis trading]]).

* Every contract in `basis_contracts` with annualised basis at the start above **10%** (the threshold fixed in that
  study's run script before it ran; the 8%, 12% and 15% rows are not candidates here).
* Outcome per contract: `net - rf` (net of the 0.265% round trip, against 6.5% a year over its days).
* Monthly mean by contract start month; months with no rich contract are absent, not zero.
* **Seen before**: that study reported the 10% row at t +2.9 overall and +5.2 in 2022-26, so the 2022-26 half of G3
  is not new evidence. The 2016-2021 half has not been computed. G5 (live) is what confirms it.

## N2F Rejected results, as a hedged short in stock futures

*Mechanism*: under-reaction to bad news ([[Result of edges for every mode]], N2 replicated on ranks 501-1000).

* Event: the live N2 definition (`decisions/news_filter`, as implemented in `evaluation/momentum_book.n2_events`):
  a RESULTS reaction in the bottom fifth of the previous 30 days' reactions, at least 5 peers.
* Only F&O names on the reaction window's closing date. Entered at that close: short the stock future, long the
  Nifty for equal notional, held 20 sessions.
* Outcome per event: `-(stock return - Nifty return) - 0.0014`.
* **Seen before**: N2 was discovered on ranks 1-500 (which contain the F&O names) and replicated on 501-1000. Its
  F&O-only, futures-cost version has not been computed.

## EAP Earnings-announcement premium, long in stock futures

*Mechanism*: stocks earn a premium in the days before a scheduled earnings announcement - investors are paid to bear
announcement risk, and attention rises into the date (Frazzini & Lamont 2007; Barber, De George, Lehavy & Trueman
2013, across 32 countries). Not tested here before; the data is the exchange's board-meeting intimations, which
announce the date in advance (176,615 since 2015).

* Event: a BOARD_MEETING announcement whose headline names a scheduled date ("scheduled on DD/MM/YYYY") and mentions
  financial results, for an F&O name; the meeting date 3 to 15 sessions after the intimation's session.
* Entered at the close of the intimation's session (or the next session if it came on a non-session day): long the
  stock future, short the Nifty, held to the close of the first session after the meeting date.
* Outcome per event: `(stock return - Nifty return) - 0.0014`. One event per name per meeting date.

## Predictions

* **C1a** positive in both halves; **C1b** passes G3 (about 55%: the 2022-26 half is known strong, the earlier one
  is not).
* **N2Fa** positive in both halves; **N2Fb** passes G3 (about 40%: futures costs are a quarter of delivery's).
* **EAPa** positive in both halves; **EAPb** fails G3 (about 70% fail: published premia are ~0.5-1% a month and the
  holding period is short).

## If any passes

It becomes a CANDIDATE (ADR-0004), joins the paper zoo the next session, and enters `oos_monitor.REGISTERED`. A
CANDIDATE is the one verdict that lets the owner arm stage TICKET (ADR-0005), where each real order still needs the
owner's approval. Nothing here changes after a result: a variation is a new registration.

Information, not advice.
