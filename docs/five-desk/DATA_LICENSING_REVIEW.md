# Data licensing and retention review (FB-0008), first pass, 2026-11-18

This is a desk review of public terms. It is not legal advice. Each open question needs a written
answer from the provider before it counts as settled, so FB-0008 stays PARTIAL until those answers
are filed under `docs/five-desk/licensing/`.

## Current use

| Source | What we pull | Where it is stored | Who sees it |
|---|---|---|---|
| Kite Connect (Zerodha) | quotes, WebSocket ticks and depth, minute/day history, instruments | `data/minute_history.duckdb`, `data/_explore_5m`, depth archive, paper ledgers | owner only, local machine |
| NSE (public site and archives) | bhavcopy EOD, corporate actions, F&O snapshots | `data/curated`, `financial_brain.duckdb` | owner only |
| BSE | XBRL filings, insider disclosures | XBRL store | owner only |
| AMFI | daily NAV file, NAV history (90-day windows) | MF tables | owner only |

Nothing is published, redistributed or shown to anyone else. The control-room HTML is a local file.

## Findings

1. **Kite Connect.** [Kite terms](https://kite.trade/terms) limit use to a private interface for the
   user's own trading and investing. They forbid showing live data to the public and forbid
   redistribution, and redistribution can end API access. **They also forbid using the APIs or live
   data for "virtual/mock trading apps".** Our paper runners use Kite live quotes to simulate fills,
   which is mock trading for one person, not an app offered to others. Whether that clause covers a
   private paper book is the most important open question here.
   Forum threads asking whether local storage for personal backtesting is allowed have no clear
   written answer from staff
   ([example 1](https://kite.trade/forum/discussion/16384/is-private-local-storage-of-kite-data-for-personal-backtesting-and-paper-tracking-allowed),
   [example 2](https://kite.trade/forum/discussion/16295/clarification-on-historical-nse-equity-data-retention-and-personal-quantitative-research)).
   Forum users cite a clause against permanent copies or databases, but I could not find its
   wording on the official terms page.
2. **NSE.** NSE's [data policy](https://www.nseindia.com/market-data/nse-data-policy) and
   [data sharing and usage policy](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/NSE_Data_Sharing%26Usage_Policy.pdf)
   govern use, display and redistribution. The
   [non-display policy](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/Non_Display_Policy.pdf)
   requires a separate agreement for non-display uses such as algorithmic use, and for any
   derived data distributed outside. Personal non-display use of data that comes through a
   broker's licence is the grey area.
3. **AMFI.** The [AMFI terms of use](https://www.amfiindia.com/terms-of-use) claim copyright on
   site content and disclaim accuracy. NAV history downloads are limited to 90 days at a time, and
   `providers/amfi_history.py` enforces that limit. I found no clause that specifically allows or
   forbids keeping NAVs for personal analysis.
4. **BSE.** Not reviewed in this pass. The XBRL and insider data comes from public disclosures.

## Questions for the owner to send

- **Zerodha (Kite Connect support, in writing):**
  - (a) Does the "virtual/mock trading" clause cover a private paper-trading book that only the
    account holder sees?
  - (b) May minute history and tick or depth data be kept locally for personal backtesting? If
    so, for how long?
  - (c) Is there a request-rate or entitlement limit on options-chain quotes for a personal app?
- **NSE Data & Analytics:** does personal, non-display algorithmic research on broker-sourced data
  need a non-display licence?
- **AMFI:** may NAV history be stored for personal, non-published analysis?

## Retention rule until answers arrive

- Keep everything local. Publish or share nothing.
- Do not build features that show market data to anyone else.
- If Zerodha says paper trading on live data is not allowed, pause the live paper runners and use
  delayed or EOD data instead.
