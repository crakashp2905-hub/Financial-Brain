# Success plan: research copilot and trading evidence

Updated 2026-10-21. The first user is a self-directed Indian-equity investor (the product charter). Two outcomes
must be measured separately: better research and portfolio decisions; and incremental trading return after costs.
The first can succeed while the second is still unproven.

## Changes landed in this review

* Forward confirmation now requires a positive mean **and** a cumulative result inside the registered band. The
  broker-facing verdict requires an exact historical `CANDIDATE` trial before a forward `CONFIRMED` result counts.
  A failed forward record refuses promotion. Existing M1/F3/C1 trial rows remain FAIL and were not rewritten.
* A backtest refuses a missing future return for a selected holding. The historical outcome must be resolved as a
  delisting, suspension, cash settlement or other explicit policy before that book can be judged.
* Trial fingerprints hash whole records so swapping values between dates changes the hash. External artifacts
  such as minute history and basis files are inventoried. Their file metadata is **not** a verified immutable snapshot;
  earlier trial manifests cannot be retroactively made complete.
* `dependence_audit` reports an autocorrelation-aware t-statistic alongside the old statistic. It is diagnostic:
  changing the registered G3 test would be a new prospective decision, not a silent edit to spent trials.
* The market-hours watcher supplies fresh stock and option **scenarios**, holding stop reviews, and desktop or opt-in
  Telegram alerts. It records the first scenario for each instrument each day before the outcome. It never sends an
  order. Until an options strategy passes its own historical and forward gates, options remain WATCH only.

## Required evidence before stronger messages

| Claim shown to the owner | Required evidence |
|---|---|
| Baseline probability / option payoff scenario | Fresh input, named model, horizon, quote and explicit model limits |
| WATCH BUY / SELL REVIEW | Historical opportunity or a held position's recorded stop, with no implied proven alpha |
| Candidate strategy | Exact preregistration and ledger row passing G1-G4, net of costs |
| Confirmed strategy | Candidate plus G5's minimum rebalances, positive net excess and in-band forward result |
| Real-money order | The separate OMS stage, broker and human locks in ADR-0005/0006 |

No stock or options message should state a calibrated win probability until it has a separately held-out calibration
record. A bootstrap P(up) is a baseline probability, not evidence of conditional prediction skill. Option payoff
scenarios exclude brokerage, taxes, exercise/settlement complications and early-sale liquidity. They must not be
ranked as options alpha.

## Remaining work and acceptance conditions

1. **Research correction:** identify which old trials used `standard.book_series` with missing future returns;
   resolve their outcomes from source data, rerun unchanged rules, and preserve the old and corrected results side by
   side. Require a frozen input snapshot per future trial, including files outside the research database.
2. **Inference:** register the dependence lag and empirical joint null before future tests. Use shared-date resampling
   across liquidity tiers and purge overlapping outcome windows. Keep the prior bar visible in historical reports;
   a new bar cannot create an old edge.
3. **Data:** measure coverage per series, instrument and session, not only a table's last date. The macro table was
   empty at inspection on 2026-10-21. FRED observation dates and latest revisions are not historical release vintages;
   use vintage data for research or label these feeds as context only. Complete delisting, index membership and option
   quote history where the data rights and source permit.
4. **Operations:** install the declared environment on the running machine; make every scheduled job use that
   interpreter; add explicit job dependencies, retries, a final failure status and a refresh watchdog. Store a backup
   outside the single Windows machine and prove restoration of the lake and decision ledgers. An owner-selected
   destination and a quiet database window are needed before the first real restore drill.
5. **Copilot value:** compare the terminal against a simple watchlist and filing feed on timed research tasks,
   missed material changes, stale or unsupported claims, thesis changes caught, and decisions later regretted.
   Predefine tasks and samples before scoring them.
6. **Trading value:** maintain frozen forward paper books, measure the net result against a liquid-universe baseline,
   cash and naive momentum, including lost gains from avoid filters. Calibrate quoted spread, impact and actual fills
   by liquidity bucket when the owner has enough broker sessions. No strategy currently clears the full evidence bar.
7. **Options:** accumulate timestamped bid/ask/depth, full contract identities and expiries, corporate actions,
   actual fees and settlement outcomes. Register each options strategy before any decisive test. A synthetic option
   backtest and a one-day scenario do not qualify it for a BUY/SELL message.

The 2026Q4 trial budget is already spent. Further *new* hypotheses wait for the next permitted research period;
repairing infrastructure, measuring forward predictions and auditing spent trials do not consume an additional
strategy trial. M1's 40-session hold requires at least 480 settled sessions for 12 rebalances, so its complete G5
record is farther away than a calendar-year estimate suggests.
