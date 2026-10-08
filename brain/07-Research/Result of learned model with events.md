---
type: research
tags:
  - research
  - result
  - edges
  - learned-model
date: 2026-10-08
status: result
---

# Result of learned model with events

Tests [[PRE-REGISTERED learned model with events]]. Walk-forward by year 2019-2026, 30-day embargo, same panel and
parameters as the deep library; books every 4th sampled date, top 20, sized round trips. Event-feature coverage:
news 100%, results reaction 90% (within 60 sessions), futures open interest 20% (F&O names). Walk-forward rows in
`data/edges/learned_events_walk.parquet`, books in `data/edges/learned_events_books.parquet`.

## L1 v2 (with events) against v1

| Period | v1 net / month | t | v2 net / month | t | v2 - v1 | t |
|---|---:|---:|---:|---:|---:|---:|
| 2019-2021 (38) | +0.735% | +1.28 | +0.660% | +1.26 | -0.075% | -0.20 |
| 2022-2026 (58) | +0.688% | +2.14 | +0.783% | +2.46 | +0.095% | +0.31 |

**Fails.** v2's 2022-26 t is above 2 but it beats v1 by 0.095% a month, under the 0.2% bar, and was worse in
2019-21. The difference is noise. **M1 right**: sparse event features add nothing the price features do not already
carry. No `learned-v3` book: on paper it would trade almost the same names as learned-v1.

(The v1 control reproduces the deep library's result: +0.69% a month 2022-26, t 2.1, against +0.77%, t 2.3, in the
original run - the panel and folds are the same; the costs and the tie order differ slightly.)

## L2 v1 long-short in stock futures

| Period | Net / month | t |
|---|---:|---:|
| 2019-2021 (37) | +0.830% | +0.65 |
| 2022-2026 (58) | +1.128% | **+2.44** |

Gross +1.15% a month, cost about 0.03%. **Fails the bar** (2019-21 t 0.65 - a short, noisy first period), but
**M2 is wrong**: the short leg earns. In 2022-26 the market-neutral book returned more a month than the long-only
learned book's excess, with no market exposure. It is the strongest futures result today and goes into the zoo as
`edge_l2_learned_ls` on paper (verdict beside it); a pass on its forward record, or a longer history, is what would
promote it.

Information, not advice.
