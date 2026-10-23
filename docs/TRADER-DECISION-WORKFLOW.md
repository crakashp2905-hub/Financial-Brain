# Trader decision workflow (2026-10-24)

The product is a research and risk co-pilot, not a proven signal service. The first screen
must answer **act, watch, or do nothing** without implying that a modelled probability or
positive in-sample alpha is a validated trading edge. At present, the answer for new risk is
**no validated trade signal**. Holding reviews are separate from new-buy research.

## Daily path

1. Before market: check data freshness, kill switch and holdings requiring review. A stale
   source or non-OK kill switch is a hold, even when a ranking looks attractive.
2. During market: receive only high-threshold **research watch** messages and stop-crossing
   **sell reviews**. Option scenarios are archived as research observations, not pushed as
   trade prompts. No alert sends an order.
3. For each watch: compare cash, current holdings and the candidate; inspect benchmark,
   fees, spread, impact, size, maximum planned loss, expiry/event exposure and evidence tier.
   The detailed terminal supplies these fields; modelled values are exploratory.
4. Log the human decision before the outcome is known. After market, evaluate the selected
   action *and the rejected alternatives* on a prospective, after-cost basis.

`python -m financial_brain.paper.decision_journal record --symbol CASH --choice NO_TRADE --asof 2026-10-22 --shown-at 2026-10-22T09:30:00+05:30 --evidence "Historical observation" --reason "No confirmed edge"`

`python -m financial_brain.paper.decision_journal report`

The journal is owner-entered and append-only. It measures choice counts and time to decide,
not P&L or causal benefit. Do not backfill missed decisions as if they were prospective.

## Evaluation protocol

Run a scripted session with at least 10 realistic cases and record the case version, shown
time, decision time, answer and reason. Include: stale quote, stale daily source, kill-switch
halt, stop-crossing holding, attractive but unconfirmed stock, wide-spread option, option
near expiry, sector-concentrated portfolio, a no-opportunity day and a contradictory event.
Have a trader use the old page and the decision-first page on different, counterbalanced
cases. Predefine a correct **risk-aware** answer for each case. Record:

| Measure | Desired interpretation |
|---|---|
| Time to a correct decision | Faster is useful only if correctness is maintained |
| Unsupported-trade rate | Should decrease; count an unconfirmed watch interpreted as a buy |
| Stale/kill-switch compliance | Must be 100% in scripted blocking cases |
| Alert precision and volume | Count alerts that led to a useful review, not clicks alone |
| Net excess return after costs | Prospective paper outcomes vs cash, simple benchmark and existing workflow |
| Tail loss, drawdown, missed opportunity | Report alongside average return, by regime and product |

Ten cases test usability, not profitability. Prospective trading evidence needs many more
market sessions, actual quote/depth records and fill calibration. Outcomes must be split by
stock swing, stock intraday and each option strategy. Do not pool options and stocks into one
success score. Do not promote a trial beyond the preregistered evidence gates in ADR-0004.

## Remaining blockers

- The registered 2026Q4 trial budget is spent (13 FAIL); no confirmed edge exists.
- Market-hours rehearsals, real fill/slippage calibration and long forward-paper samples
  cannot be created by editing the interface.
- Kite historical candles are useful for stocks, but expired option instrument tokens must
  be cached prospectively; `continuous` does not reconstruct minute-level options history.
- The option quote archive is permission-gated. Clarify the owner's permitted retention and
  use before enabling it; no dataset is silently acquired.
- Commercial distribution or autonomous execution would need a separate legal, broker and
  operational review. This workflow assumes a single owner and paper trading.
