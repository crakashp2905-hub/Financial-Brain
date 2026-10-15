---
type: data-source
tags:
  - data-source
  - nse
  - fundamentals
  - insider
updated: 2026-10-15
---

# NSE structured disclosures (integrated filing, insider XBRL)

Found 2026-10-15 by reading the public pages' own requests in the built-in browser; robots.txt allows `/api`. JSON on
www.nseindia.com through the session the page itself sets (`providers/nse_results.NSEResults._get`); XBRL files on the
archive host, no session.

## Integrated Filing - Financials (in use)

`/api/integrated-filing-results?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY&period_ended=all&type=Integrated%20Filing-%20Financials&page=N&size=100`

* From the **March-2025 quarter** (filed April 2025) - the store's 2019-2024 XBRL results end exactly there.
* Each row: symbol, company, quarter end, standalone/consolidated, audited, original/revision, broadcast time, XBRL URL.
* XBRL: SEBI `in-capmkt` taxonomy (namespace dated, e.g. `http://www.sebi.gov.in/xbrl/2026-01-31/in-capmkt`), same
  concepts and OneD/FourD contexts as the older results XBRL.
* Loaded 2026-10-15 for the 700 most liquid names each quarter: 13,103 filings stored, 13,076 statements promoted to
  `financial_results`, 99.5% pass their arithmetic check. `fb integrated-filing --prefetch`, then load.

## Insider trading, structured (not yet collected)

`/api/corporates-pit-gg?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY`

* **History starts July 2026** (empty for every earlier month probed: 2016, 2019, 2022-2026 H1) - the XBRL insider
  filings are a new requirement. About 620 disclosures a month.
* Each XBRL: category of person, transaction type (buy/sell), number and value of securities, holding before and after
  (shares and %), mode (market purchase, off-market, ...), dates, ISIN.
* **Use:** a forward record only - an insider/promoter net-buying signal can be tested out of sample after 6-12 months
  of nightly collection. Earlier history exists only as SAST/PIT PDFs ([[Pilot - insider and promoter direction from
  SAST filings]]).
