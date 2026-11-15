# PE-0101 — Full-NAV performance series

PaperAccount persists an immutable daily NAV for cash, marked positions and external flows. The resulting return series includes no-trade cash days and open-position gains or losses. It survives restart and is independently recomputed from the durable journal.

PE-0101's cash-equity acceptance criterion passes. Multi-asset accounting remains covered by the separate derivative and multi-currency tasks.

Evidence and source hashes are in `pe-0101-full-nav-performance-evidence.json`.
