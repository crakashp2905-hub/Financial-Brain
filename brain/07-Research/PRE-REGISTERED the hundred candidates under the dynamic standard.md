---
type: research
tags:
  - research
  - pre-registration
  - dynamic-standard
date: 2026-11-17
status: pre-registered
---

# PRE-REGISTERED the hundred candidates under the dynamic standard

Written on 2026-11-17 at the owner's instruction: list the hundred most achievable ways to gain an edge with the
data held, backtest every one, and combine what survives. Registered under ADR-0007
(`docs/adr/0007-dynamic-evidence-standard.md`). This note, `scripts/research/hundred_spec.py` (the registry) and
`scripts/research/hundred.py` (the rules, as code) were committed together **before any candidate's return was
computed**; only a mechanics check (how many names each rule picks) had been run. No ADR-0004 trial is spent and
`docs/trials/ledger.jsonl` is not touched.

## What is fixed here

* **The list**: the hundred rows below, each with its family, prior class and direction.
* **The family size** used for the Sidak adjustment: variants of the idea already examined in this project plus
  this batch's members (in brackets). Momentum goes from 20 to 29, which also re-grades the registered
  frog-in-the-pan book ([[PRE-REGISTERED frog-in-the-pan momentum under the dynamic standard]]) as that
  registration said it would.
* **The engines**: top 20 of the 500 most liquid, monthly, equal weight, net of per-name costs, against the
  equal-weight universe (`xs`); every flagged name, capped at the 40 most liquid, a month with fewer than five
  holding the universe (`flag`); sector rotation among 13 NSE sector total-return indices at 20 bp a round trip
  (`rot`); index timing as the return over the same strategy at its own trailing average exposure (`timing`).
* **Timing of information**: decisions use data available at the rebalance close; a filing published at or after
  15:00 counts from the next session; fundamentals from the first report of each quarter at its filing time;
  shareholding from its dissemination time; macro series from the observation date plus the release lag.
* **Grading**: `dynamic_standard.judge` on the net series, split 2022-01-01. A book or filter with posterior at or
  above 0.5 gets a random-book null of the same size and turnover (100 draws). Fewer than 24 active periods is
  UNTESTABLE, not a failure. An avoid filter can never hold capital.
* **Combination step, after the hundred**: one equal-weight sleeve of every *book* graded INCUBATE or better, and
  one of every book graded CANDIDATE_SMALL or better. Both are selected on the results, so both are graded as
  class C with family 100.

## Predictions, written before the run

* **E1** Fewer than ten of the hundred reach CANDIDATE_SMALL, and most of those are avoid filters.
* **E2** No index-timing rule and no technical rule reaches INCUBATE.
* **E3** Frog-in-the-pan momentum replicates in ranks 101-500 and 501-1000 with a positive mean in both halves, and
  is weaker in the 100 most liquid.
* **E4** At least one ownership or results-based candidate reaches INCUBATE.
* **E5** The owner's target of twenty-five distinct edges is not met by books; the count is reported as it falls.

## Known limits, stated before the run

Industry labels are today's (NSE's current classification applied to history). Ticker-keyed sources (delivery,
bulk deals, the F&O ban list, futures) are mapped through each lineage's latest ticker. Entries are at the close
that produced the signal, the project's existing convention. Fundamentals start in 2019 and shareholding in 2020,
so their first halves are short. The proportional cost model omits the depository charge per sale.

## The hundred

| id | candidate | family (size) | prior | kind | engine |
|---|---|---|---|---|---|
| H001 | 6-1 momentum | mom (29) | A | book | xs |
| H002 | nearness to the 52-week high, long only | mom (29) | A | book | xs |
| H003 | risk-adjusted momentum (12-1 return over 250-day volatility) | mom (29) | A | book | xs |
| H004 | 12-1 momentum among names above their 200-day average | mom (29) | A | book | xs |
| H005 | industry momentum (top 3 industries by 6-month return, their best names) | mom (29) | A | book | xs |
| H006 | market-state momentum (book held only when the Nifty 500 is up over 12 months) | mom (29) | A | book | xs |
| H007 | frog-in-the-pan momentum, 100 most liquid only (replication tier) | mom (29) | A | book | xs |
| H008 | frog-in-the-pan momentum, ranks 101-500 (replication tier) | mom (29) | A | book | xs |
| H009 | frog-in-the-pan momentum, ranks 501-1000 (unseen universe) | mom (29) | A | book | xs |
| H010 | industry lead-lag: small names in industries whose large caps led last month | leadlag (4) | B | book | xs |
| H011 | industry earnings spillover: names in industries with the best recent results reactions | leadlag (4) | B | book | xs |
| H012 | one-month losers on low volume (liquidity provision) | rev (10) | B | book | xs |
| H013 | industry-relative weekly reversal, 5-session hold | rev (10) | B | book | xs5 |
| H014 | one-month reversal in the 100 most liquid | rev (10) | B | book | xs |
| H015 | high-volume one-month winners continue | rev (10) | B | book | xs |
| H016 | low volatility in the 100 most liquid | vol (12) | A | book | xs |
| H017 | nearness to the 52-week low (avoid) | vol (12) | B | avoid | flag |
| H018 | nominal price under Rs 50 (lottery names, avoid) | vol (12) | C | avoid | flag |
| H019 | low volatility of turnover | vol (12) | C | book | xs |
| H020 | listed under 250 sessions (new-issue underperformance, avoid) | listing (1) | A | avoid | flag |
| H021 | high delivery share (20-session average) | volume (10) | C | book | xs |
| H022 | rising delivery share with a rising price (accumulation) | volume (10) | C | book | xs |
| H023 | delivery spike on a down day (absorption), 20 sessions | volume (10) | C | book | flag |
| H024 | volume climax after a run-up (blow-off, avoid) | volume (10) | C | avoid | flag |
| H025 | bulk-deal net buying over 20 sessions | deals (3) | B | book | xs |
| H026 | bulk-deal net selling over 20 sessions (avoid) | deals (3) | B | avoid | xs |
| H027 | block-deal buy in the last 40 sessions | deals (3) | B | book | flag |
| H028 | profit growth year on year, latest quarter | fund (20) | A | book | xs |
| H029 | standardised unexpected earnings | fund (20) | A | book | xs |
| H030 | revenue growth acceleration | fund (20) | B | book | xs |
| H031 | margin expansion over four quarters | fund (20) | B | book | xs |
| H032 | net margin level | fund (20) | B | book | xs |
| H033 | operating leverage (revenue growth minus expense growth) | fund (20) | B | book | xs |
| H034 | four straight quarters of profit growth, ranked by momentum | fund (20) | B | book | xs |
| H035 | results beat: profit up over 25% and revenue over 15%, filed in the last 40 sessions | fund (20) | A | book | flag |
| H036 | turnaround: profit positive against a loss a year earlier, 60 sessions | fund (20) | B | book | flag |
| H037 | loss over the trailing four quarters (avoid) | fund (20) | B | avoid | flag |
| H038 | late filers: results more than 44 days after the quarter (avoid) | fund (20) | B | avoid | flag |
| H039 | early filers: results within 20 days of the quarter | fund (20) | B | book | flag |
| H040 | promoter holding up 0.5 points or more in the quarter | own (7) | B | book | flag |
| H041 | promoter holding down 1 point or more (avoid) | own (7) | B | avoid | flag |
| H042 | foreign institutional holding, largest increases | own (7) | B | book | xs |
| H043 | foreign institutional holding, largest decreases (avoid) | own (7) | B | avoid | xs |
| H044 | mutual fund holding, largest increases | own (7) | B | book | xs |
| H045 | retail shareholder count, fastest growth (avoid) | own (7) | B | avoid | xs |
| H046 | institutional accumulation (foreign + mutual fund + insurance) | own (7) | B | book | xs |
| H047 | order win, 20 sessions | event (33) | B | book | flag |
| H048 | buyback, 40 sessions | event (33) | A | book | flag |
| H049 | credit rating upgrade, 60 sessions | event (33) | B | book | flag |
| H050 | credit rating downgrade, 60 sessions (avoid) | event (33) | B | avoid | flag |
| H051 | fund raising, 60 sessions (avoid) | event (33) | A | avoid | flag |
| H052 | acquisition announced, 40 sessions (avoid) | event (33) | B | avoid | flag |
| H053 | management change, 40 sessions (avoid) | event (33) | C | avoid | flag |
| H054 | auditor resignation, 120 sessions (avoid) | event (33) | B | avoid | flag |
| H055 | promoter pledge filing, 60 sessions (avoid) | event (33) | B | avoid | flag |
| H056 | dividend announced, 20 sessions | event (33) | C | book | flag |
| H057 | three or more investor meets in 20 sessions | event (33) | C | book | flag |
| H058 | bonus or split announced, 40 sessions | event (33) | B | book | flag |
| H059 | exchange sought a clarification, 20 sessions (avoid) | event (33) | C | avoid | flag |
| H060 | three or more press releases in 20 sessions | event (33) | C | book | flag |
| H061 | legal or regulatory filing, 40 sessions (avoid) | event (33) | C | avoid | flag |
| H062 | insolvency filing, 120 sessions (avoid) | event (33) | B | avoid | flag |
| H063 | two or more substantial-acquisition filings in 40 sessions | event (33) | C | book | flag |
| H064 | earnings call or investor presentation filed, 40 sessions | event (33) | C | book | flag |
| H065 | allotment or ESOP dilution, 40 sessions (avoid) | event (33) | C | avoid | flag |
| H066 | entered the F&O ban list in the last 10 sessions (avoid) | event (33) | C | avoid | flag |
| H067 | new entrant to the 100 most liquid | event (33) | C | book | flag |
| H068 | futures at the deepest discount to spot | fno (7) | B | book | xs |
| H069 | highest put-call open-interest ratio (contrarian) | fno (7) | C | book | xs |
| H070 | futures open interest and price both up over 20 sessions | fno (7) | C | book | xs |
| H071 | volatility contraction within 10% of the 52-week high | tech (28) | C | book | xs |
| H072 | reclaimed the 200-day average in the last 20 sessions | tech (28) | C | book | flag |
| H073 | oil-beta rotation: negative oil beta when Brent is falling, positive when rising | macro (2) | C | book | xs |
| H074 | rupee-beta rotation: names that gain from the dollar's 3-month trend | macro (2) | C | book | xs |
| H075 | earnings plus price momentum (rank sum of SUE and 12-1 momentum) | composite (9) | B | book | xs |
| H076 | institutional accumulation plus momentum (rank sum) | composite (9) | B | book | xs |
| H077 | class-A composite: momentum, 52-week high, continuous information, low volatility | composite (9) | B | book | xs |
| H078 | gradient-boosted rank model on the 160-feature library, refit yearly, walk-forward | ml (8) | C | book | xs20 |
| H079 | ridge rank model on the 160-feature library, refit yearly, walk-forward | ml (8) | C | book | xs20 |
| H080 | sector 6-1 momentum, top 3 | rot (7) | B | book | rot |
| H081 | sector 12-1 momentum, top 3 | rot (7) | B | book | rot |
| H082 | sector one-month reversal, bottom 3 | rot (7) | C | book | rot |
| H083 | sector nearness to the 52-week high, top 3 | rot (7) | B | book | rot |
| H084 | sector low volatility, lowest 3 | rot (7) | B | book | rot |
| H085 | sector 6-1 momentum, only sectors above their 10-month average, else cash | rot (7) | B | book | rot |
| H086 | size rotation: Smallcap 250 or Nifty 50, whichever led over 6 months | rot (7) | B | book | timing |
| H087 | Nifty 50 above its 10-month average | timing (26) | A | book | timing |
| H088 | breadth: more than half of stocks above their 200-day average | timing (26) | B | book | timing |
| H089 | volatility-managed Nifty 500 (12% target on 20-day realised) | timing (26) | A | book | timing |
| H090 | India VIX under 25 | timing (26) | C | book | timing |
| H091 | variance risk premium high (VIX squared minus realised variance) | timing (26) | B | book | timing |
| H092 | foreign investors' index-futures net long rising over 20 sessions | timing (26) | C | book | timing |
| H093 | retail index-futures net long falling over 20 sessions (contrarian) | timing (26) | C | book | timing |
| H094 | Nifty put-call open-interest ratio above its 1-year median (contrarian) | timing (26) | C | book | timing |
| H095 | Nifty futures basis below its 1-year median (contrarian) | timing (26) | C | book | timing |
| H096 | breadth thrust: advancers over 65% of issues on a 10-day average | timing (26) | B | book | timing |
| H097 | rupee weakening over 3 months: Nifty IT over Nifty 50 | timing (26) | B | book | timing |
| H098 | copper rising over 3 months: Nifty Metal over Nifty 50 | timing (26) | B | book | timing |
| H099 | 10-year yield falling over 3 months: Nifty Bank over Nifty 50 | timing (26) | B | book | timing |
| H100 | turn of the month in the Midcap 150 (last 2 and first 3 sessions) | timing (26) | B | book | timing |

Information, not advice.
