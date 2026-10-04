---
type: research
tags:
  - research
  - roadmap
  - strategy-portfolio
  - regime
date: 2026-10-04
status: result
---

# Trader layers built, and what they showed

The 24-item roadmap, as built. Items that need an order path are **not** built, by the project's own
design gate, and the note says what stands in for them.

## Status by item

    Phase 1 - make it a trader
     1 full-market universe            built    1,521 names >= Rs 1 cr/day scanned each session
     2 Kite live data                  built    ingest/kite_live.py (snapshot + divergence), ingest/kite_minute.py (300 names, 55M bars)
     3 1m/5m/15m data                  built    features/bars.py resamples to complete 5/15/30-minute buckets
     4 opportunity scanner             built    opportunity/engine.py; `fb paper-daily`
     5 technical/volume/momentum       built    existing features + finta/Pine audit; the four validated signals drive the engine
     6 market/sector regime            built    regime/breadth.py: breadth + empirical sectors (below)
     7 expected net alpha              built    engine: IC x sigma x z minus sized round-trip cost
    Phase 2 - a real edge
     8 event -> price database         built    606k filings, 8 horizons; analogues report edge over the baseline
     9 historical analogue engine      built    evaluation/event_response.analogues (point-in-time)
    10 signal half-life                built    result below: information keeps accruing to 120 sessions
    11 F&O intelligence                built    ingest/kite_fno.py: OI, PCR, max pain, implied vol, skew from Kite (read-only)
    12 second-order market graph       built+   spillover through promoter siblings tested: a null (see the half-life note)
    13 evidence-centric retrieval      built    research/retrieval.py: BM25, point-in-time, every record cited, text flagged untrusted
    Phase 3 - allocate capital
    14 portfolio optimizer             built    portfolio/optimizer.py: Ledoit-Wolf covariance, turnover-aware, long-only
    15 strategy portfolio              built    risk/allocator.py on five sleeves (below)
    16 signal/forecast/sizing/execution attribution   built   attribution/pipeline.py, layers sum exactly
    17 counterfactual opportunity memory              built   opportunity/memory.py, 128k decisions audited
    18 strategy degradation engine     built    risk/degradation.py: CUSUM + rolling t on the IC
    Phase 4 - make it trade
    19 realistic execution simulator   built    execution/simulator.py + calibration on 34,866 fills
    20 live paper trading              built    paper/daily.py; two live sessions recorded
    21 Kite order management           NOT BUILT   Kite is market-data only; no order endpoint may exist
    22 human approval                  exists   decisions/record.py: HUMAN_APPROVED refuses any agent actor
    23 small-capital trading           NOT BUILT as trading   execution/ticket.py sizes a small account into whole
                                                shares and prints a ticket "NOT SENT ANYWHERE" for the owner to
                                                place by hand, or ignore
    24 automatic risk reduction / kill switch   built   risk/killswitch.py, wired into paper-daily; humans reset

## What the layers showed

**Sleeves (item 15).** Equal-weight top-quintile sleeves from each signal, 20-session rebalance, net of
sized costs with turnover measured between rebalances, against the liquid-universe mean, 129 months:

    sleeve                         net/month      t
    momentum                        +0.47%      +2.51
    near_52w_high                   +0.33%      +1.48
    composite: own all but worst 20% +0.12%     +1.87
    composite top 20%               +0.11%      +0.48
    low_vol                         -0.28%      -1.04
    reversal                        -0.84%      -4.97

Reversal has the fastest information decay (half-life 16 sessions) *and* loses 0.84% a month net: a
signal that must be traded often is the one costs destroy. Momentum, which keeps accruing for 120
sessions, is the only sleeve with a clear positive net result. The allocator puts reversal, low-vol and
the top-20% composite on STOP, momentum and 52-week-high on REDUCE, and, because **no sleeve has passed
the money gate**, gives every sleeve zero capital whatever its statistics. (As an illustration only: had
the gate passed, it would weight momentum 38%, 52w-high 24%, avoid-the-bottom 38%.)

**Degradation (item 18).** On the composite's IC history the monitor reads OK today (recent IC +0.119
against a baseline of +0.057) and flagged DEGRADED when run through session 60, a weak stretch in the
first half of the sample that has since recovered: the monitor is sensitive to exactly what it should be.

**Regime (item 6).** On 2026-10-01 breadth reads NARROW: 43% of 1,713 liquid names above their 200-day
average, 29% up over 20 days, 4% near a 52-week high. For reference it reads narrow on 2020-03-23 (4%
above the 200-day) and broad on 2021-10-18 (84%). Empirical sectors, found by clustering residual returns
with the market removed, come out as recognisable groups: metals (JSW Steel, Hindalco, Tata Steel, SAIL,
Vedanta), PSU banks, pharma, IT, capital goods (Siemens, ABB, CG Power), FMCG with private banks, and
gold/silver ETFs. A first version without removing the market put 334 of 400 names in one cluster; the
residual version is the one used.

**F&O (item 11).** Live from Kite on the last session: Reliance ATM implied vol 25.7%, Sun Pharma 19.6%,
Tata Steel 31.6%, Adani Enterprises 32.9%; put/call open-interest ratios of 0.62-0.79; downside skew
largest in Adani (+1.7 pts) and Tata Steel (+1.5). These are positioning facts, not forecasts, and have
not been tested for any predictive value.

## What is not claimed

None of this has a track record. The sleeves are in-sample for the signal selection; the only evidence
recorded before its outcome is the live paper loop, which has two sessions in it.

Related: [[Result of half-life and second-order spillover]] · [[Result of the opportunity engine replay]]
