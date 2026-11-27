# PA-18-09 — Indian retail algorithmic trading and market-data rules

**Reviewed:** 2026-11-27  
**Scope:** Identify current primary-source rules relevant to this repository's paper-trading system. This is an engineering source review, not legal advice or authorization to submit broker orders.

## Findings

| Source | Finding relevant to the system |
|---|---|
| [SEBI circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 (4 Feb 2025)](https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html) | Establishes the safer-participation framework for retail algorithmic trading through brokers and exchanges. The broker remains responsible for controls and exchange-facing compliance. The circular and exchange implementation standards must be read together. |
| [SEBI circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/132 (30 Sep 2025)](https://www.sebi.gov.in/sebi_data/attachdocs/sep-2025/1759232056254.pdf) | Extended implementation milestones and states that the framework and exchange operational modalities apply to all stock brokers from 1 Apr 2026. |
| [NSE operational modalities NSE/INVG/69255 (22 Jul 2025)](https://nsearchives.nseindia.com/content/circulars/INVG69255.pdf) | Exchange implementation detail referenced by later NSE guidance. Exchange-specific order tagging, controls, and broker-hosted operation need to be confirmed with the broker for the relevant segment and account. |
| [NSE circular NSE/INSP/77559 (20 Nov 2026)](https://archives.nseindia.com/content/circulars/INSP77559.pdf) | States the retail algo framework has applied since 1 Apr 2026. For NSE's “tech-savvy retail investor” pathway, the investor must develop their own algorithm using programming knowledge; brokers/providers must not give static-IP access under this category to clients who do not meet that definition; the broker must obtain the stated undertaking. |
| [NSE Retail Algo FAQ (3 Nov 2025)](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/FAQ_Retail%20Algo_03112025_NSE.pdf) | Summarizes NSE implementation, including API orders being treated as algo orders in the covered context. It expressly functions as an FAQ; binding circulars and broker implementation govern. |
| [Zerodha: Kite Connect data use on other platforms](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/can-i-use-historical-and-live-data-taken-from-kite-connect-api-on-other-platforms) | Zerodha states Kite Connect live/historical market data may not be displayed on other platforms and points to exchange-authorized vendors for data distribution. Therefore, do not pipe Kite market data into TradingView or another external display unless the relevant rights are separately confirmed. |
| [Zerodha: Kite Connect API FAQ](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/kite-connect-api-faqs) | Repeats the restriction on display or redistribution of Kite Connect data on external platforms. Account/app entitlements and API permissions remain account-specific. |

## Engineering implications

1. Keep the current paper-only physical-order lock in force. This review does not authorize or enable broker order submission.
2. Treat broker API order routing as a separately gated integration. Before considering it, obtain current written broker confirmation for the account, API app, exchange, segment, strategy classification, order-rate/ tagging requirements, static-IP/network model, and any required registration or undertaking.
3. Keep Kite data within the permitted internal use boundary. Any external chart or client-facing data display needs its own exchange/vendor rights review; a charting library license does not grant market-data rights.
4. This review is limited to the cited SEBI and NSE materials and Zerodha's published Kite Connect terms. It does not establish coverage for BSE, MCX, every product segment, later amendments, or the user's specific broker account.

## Remaining evidence / status

The primary-source baseline is identified, so the checklist item is **PARTIAL**. Account-specific written broker confirmation, exact exchange/segment applicability, and any data-display rights remain unverified and are not inferred from the user's paid Kite API subscription. Recheck official circulars and broker terms before any future change to the execution or data-distribution boundary.
