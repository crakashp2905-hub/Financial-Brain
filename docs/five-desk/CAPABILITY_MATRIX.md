# Capability matrix (FB-1410)

`BUILT` = code and tests exist. `NOT WIRED` = not yet called by the running paper runners.
`EXTERNAL` = needs data, access or elapsed time that the repository cannot supply.

| Capability | SWING | INTRADAY | OPTIONS | ETF | MF | Module |
|---|---|---|---|---|---|---|
| Instrument identity and units | BUILT | BUILT | BUILT | BUILT | BUILT | `instruments/` |
| Point-in-time quote/NAV gating | BUILT | BUILT | BUILT | BUILT | BUILT | `instruments/pit.py` |
| One signal schema with SL/TP | BUILT | BUILT | BUILT | BUILT | NAV thresholds | `signals/contracts.py` |
| Admissibility (net EV) | BUILT | BUILT | BUILT | BUILT | n/a | `signals/admissibility.py` |
| Paper fills | quote-backed | quote-backed | leg-wise | quote-backed | NAV allotment | `execution/` |
| Exits (OCO, gaps, trailing) | BUILT | BUILT + flatten | premium/spread value | BUILT | redemption requests | `signals/exit_*`, `execution/option_book.py` |
| Double-entry ledger | BUILT | BUILT | BUILT | BUILT | BUILT | `portfolio/multi_asset_ledger.py` |
| Capital allocator | BUILT | BUILT | BUILT | BUILT | BUILT | `risk/capital_allocator.py` |
| Wired into runners | NOT WIRED | NOT WIRED | NOT WIRED | NOT WIRED | NOT WIRED | — |
| Live data | EXTERNAL | EXTERNAL | EXTERNAL | EXTERNAL | AMFI daily only | — |
| Forward evidence | EXTERNAL (time) | EXTERNAL | EXTERNAL | EXTERNAL | EXTERNAL | — |
