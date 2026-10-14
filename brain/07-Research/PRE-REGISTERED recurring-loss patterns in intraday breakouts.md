---
type: research
tags:
  - research
  - pre-registration
  - intraday
  - loss-patterns
date: 2026-10-14
status: withdrawn
---

# PRE-REGISTERED recurring-loss patterns in intraday breakouts

> **WITHDRAWN 2026-10-14, never run.** Registered by a session that had not read ADR-0004: the 2026Q4 trial budget
> was already spent (9 of 9 with amendment 1), and ADR-0004 closes intraday direction strategies and says a losing
> context "becomes a hypothesis, never a fix". No data was computed against it. It may be re-registered in 2027Q1 as
> a trial if the owner chooses to spend a slot on it; until then the nightly `fb loss-review --propose` keeps turning
> recurring losing contexts into PROPOSED hypotheses.

Written on 2026-10-14 before anything below is computed. The owner asked the brain to find patterns, check for
edges, and constantly avoid recurring losses. The live record's biggest recurring loss is the opening-range breakout
(ORB): -15 to -21 bp a trade net over 251k simulated trades ([[Result of the opening-range breakout with stops]]),
and its daily P&L swings with the market. The danger in "avoid what lost" is fitting noise: almost any slice of a
losing strategy looks worse than average somewhere. So the search is split in time, and only what repeats counts.

## Data and trades

The 300 names with Kite minute bars, 2024-09-25 to 2026-10-01; ORB long and short exactly as `evaluation/orb.py`
(15-minute range, trigger 09:30-11:00, full stop, 2R target, square-off), intraday costs by bucket as in the trend
study. **Discovery: up to 2025-09-30. Confirmation: 2025-10-01 onward** (never looked at before the cells are fixed).

## Context, all known at the entry minute

1. gap: open / previous close - 1, signed by side (positive = gap in the trade's direction)
2. opening-range width / open
3. trigger minute
4. the equal-weighted market's move from 09:15 to the trigger minute, signed by side
5. Nifty 500 above its 20-session average at the previous close, signed by side (with / against)
6. previous session's return, signed by side
7. the name's 20-session daily volatility
8. opening-range turnover / its 20-session average opening-range turnover
9. day of the week
10. the name's 5-session return, signed by side

Continuous ones are cut into quintiles at **discovery** cut points; categorical ones kept as they are. A cell is
side x context x bin (about 2 x 10 x 5 = 100 cells).

## Rules (fixed here)

* **Loss cell**: discovery net mean < 0 with date-clustered t <= -3 **and** confirmation net mean < 0 with t <= -2.
* **Edge cell**: discovery net mean > 0 with t >= 3 **and** confirmation net mean > 0 with t >= 2.
* **Filtered ORB** = ORB minus every confirmed loss cell. Its confirmation-period net per trade, against unfiltered
  ORB's, is the honest measure of what avoiding the recurring losses is worth (the cells were chosen without that
  period).
* **Adoption**: confirmed loss cells become skip rules in a new intraday version **live-v2** run on paper beside
  live-v1 (unchanged). Confirmed edge cells, if any, become a separate pre-registered paper strategy. Nothing changes
  in live-v1.

## Recurring

The same miner (`evaluation/loss_patterns.py`) re-runs weekly as minute bars and live trades accumulate, always
with a discovery window and a later confirmation window it did not see; anything it finds is a PROPOSED hypothesis
until confirmed this way. The weekly job never edits a running version.

## Predictions

* **R1** A handful (2-6) of loss cells confirm; trades against the intraday market move and very narrow opening
  ranges are among them.
* **R2** Filtered ORB loses less than ORB in confirmation but is still negative net.
* **R3** No edge cell confirms.

Information, not advice.
