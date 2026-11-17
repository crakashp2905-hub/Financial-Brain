# Data sources, rights and failover (FB-1409)

This records what the code assumes. Legal review of rights is the owner's task (FB-0008); nothing
here is a legal opinion.

| Source | Used for | Access | Quota / limits (as documented by the provider) | Retention in this repo | Failover |
|---|---|---|---|---|---|
| AMFI `NAVAll.txt` | daily MF NAVs | public file, no login | one file per day | raw bytes in the lake with SHA-256 | none; a missing day stays missing, never backfilled from another source |
| AMFI NAV history | MF history | public form | 90 days per request (FB-0306) | lake | none |
| NSE bhavcopy | EOD equity/ETF prices | public archives | site rate limits | lake | BSE bhavcopy for the same ISIN, labelled as a different source |
| Kite Connect | live quotes, minute bars, option chains | owner's paid account; daily owner login | provider rate limits; owner must confirm | quotes archived only where the terms allow | stale feed halts the affected desk; no substitute prices |
| AMC disclosures | ETF/MF reference, TER, portfolios | public PDFs/CSVs downloaded by the owner | — | owner-provided files only | absent fields labelled ABSENT |

**Rules in code.** Sources are recorded on every dated version; refused rows are reported, not
forced in; future-published data is rejected; nothing is redistributed.
