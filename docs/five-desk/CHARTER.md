# Five-desk product charter (FB-0005)

**Mandate.** One paper-only quant platform with five operating desks. Success is risk-adjusted net
profit measured on future observations. `NO_TRADE` and `CASH` are valid outcomes.

## Execution scope
| Desk | Instruments | Clock | Directions | Paper fill mechanics |
|---|---|---|---|---|
| SWING | NSE equities | daily candidates, multi-session holds | long only (no overnight shorts) | observed bid/ask, next session |
| INTRADAY | NSE equities | event-driven bars/ticks | long; short only intraday with mandatory session flatten | observed bid/ask, participation cap |
| OPTIONS | long calls/puts, defined-risk debit spreads on listed contracts | event-driven + 15m index forecasts | long premium or debit spreads only | contract bid/ask per leg |
| ETF | listed Indian ETFs | intraday/swing/position | long only until another style is validated | exchange bid/ask, never NAV/iNAV |
| MUTUAL_FUND | open-ended conventional schemes (direct/regular, growth/IDCW) | daily/weekly NAV allocations | SUBSCRIBE/REDEEM/SWITCH/SIP/HOLD | NAV allotment after cut-off, funds and publication |

## Out of scope (blocked in code)
Naked option writing, physically settled exits without a settlement model, overnight equity shorts,
inverse/leveraged products absent from the Indian listed universe, and any physical broker order.
`PHYSICAL_ORDERS_ENABLED = False`; only the owner sets `FB_LIVE_ORDERS`.

## Evidence labels
FIXTURE → REPLAY → LOCAL_PAPER → ATTESTED_FORWARD → BROKER_APPROVED. A label is earned by its own
evidence; it is never inferred from completing tasks.
