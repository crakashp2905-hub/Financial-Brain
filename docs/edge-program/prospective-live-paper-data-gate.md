# Prospective live-paper data permission gate

**Disposition: BLOCKED pending account- and product-specific written authorization.** This is an engineering control based on public terms, not a legal opinion or a finding that a private owner-only use is prohibited.

## Evidence and scope

- Zerodha's [Kite Connect terms](https://www.kite.trade/terms/) allow a private interface for personal trading/investment experience, but also say APIs/live data may not be used for virtual/mock trading apps, public display is prohibited, and returned API content may not be permanently copied unless expressly permitted (sections 2(a), 4(b)). The terms do not clearly explain whether this owner's private, single-user paper-research workflow is covered by the virtual/mock restriction.
- NSE Data & Analytics' [Non-Display Usage Policy](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/Non_Display_Policy.pdf) defines algorithmic trading-decision support and internal derived analysis as non-display use and describes a separate agreement/undertaking and applicable fees. Whether that policy or an upstream agreement applies to this user's Kite entitlement requires provider confirmation.
- SEBI's [May 24, 2024 circular on sharing real-time price data to third parties](https://www.sebi.gov.in/legal/circulars/may-2024/norms-for-sharing-of-real-time-price-data-to-third-parties_83572.html) addresses third-party sharing; it does not itself resolve a private in-house workflow. The project must not expose live quotes to others while this remains unresolved.

## Runtime control

`LIVE_PAPER` sessions require a source-specific `PROSPECTIVE_PAPER_CAPTURE` permission in addition to general replay/simulation permission. Missing, expired, or denied rights block event ingestion and identify forward executable paper testing as blocked. Existing `PAPER_SIMULATION` permission does not imply live prospective capture rights. Synthetic fixtures remain explicitly synthetic; archived/replay evidence remains in its replay mode and cannot be upgraded to prospective evidence.

The repository permission registry records this new scope as `UNKNOWN_RIGHTS` for every source. No code or subscription charge can change that status. The owner must obtain and review written confirmation from Zerodha and, if applicable, NSE Data & Analytics, then update only the exact source/use records supported by that evidence.

## Questions required to unblock

1. Does the active personal Kite Connect agreement permit this exact single-user, private application to consume current quotes for simulated orders/fills and retain the resulting evidence? State the permitted retention period and deletion requirements.
2. Does Kite's virtual/mock trading prohibition apply to this private research workflow, even though it is not offered to other users and sends no orders?
3. Does the user's upstream data entitlement cover algorithmic/non-display use for internal decision support, or is an NSE Data & Analytics agreement/fee required?
4. May raw quote/depth snapshots, historical candles, derived signals, and audit hashes be stored locally and backed up? Specify each product separately.

Until the evidence is recorded, use delayed, historical, or synthetic replay only where its separate rights permit it. Do not claim prospective live-market execution evidence.
