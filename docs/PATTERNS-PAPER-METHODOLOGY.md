# Chart patterns: paper methodology

- Entries are the next executable session after confirmation. Same-bar fills are not permitted.
- Costs are modelled and listed separately: spread, fee, impact, financing. Net = gross minus all of them.
- Fills are FILLED, PARTIAL, NO_FILL, REJECTED, TIMEOUT or STALE_QUOTE. Nothing fills silently. Labels say ASSUMED.
- Cash cannot go negative; reservations are checked against available buying power.
- Pending intents are never holdings. Realised, unrealised and total P&L are reported separately.
- Ledger identity: opening + flows + realised + unrealised = ending, within tolerance.
- Risk refusals are machine-readable codes (SYMBOL_LIMIT, SECTOR_LIMIT, DRAWDOWN_LIMIT, LEVERAGE_LIMIT,
  INELIGIBLE_SESSION, KILL_SWITCH_ACTIVE). The kill switch blocks new entries only.
- Shorts are restricted to explicitly supported futures fixtures. Cash equities cannot be shorted.
- Limit: the existing OMS risk gate is not called from this module (architecture boundary). See the final gates document.
