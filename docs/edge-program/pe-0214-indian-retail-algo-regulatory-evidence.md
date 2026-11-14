# PE-0214 — India retail algorithmic trading regulatory evidence

**Reviewed:** 2026-11-14

**Scope:** In-house market research and paper trading using read-only Kite market/account data. No physical order route is enabled by this project.

## Why this is recorded

The checklist calls for applicable India-specific regulatory/operational review before any broker-connected live order path. This note records primary-source material for engineering gate design. It is not a legal opinion and does not determine whether a particular use is regulated. A future order-submission capability requires confirmation from the broker and appropriately qualified Indian counsel/compliance personnel before being enabled.

## Primary sources reviewed

1. [SEBI circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013, 4 February 2025](https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html) — framework for safer retail participation in algorithmic trading through brokers.
2. [SEBI circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/132, 30 September 2025](https://www.sebi.gov.in/legal/circulars/sep-2025/extension-of-timeline-for-implementation-of-sebi-circular-dated-february-04-2025-on-safer-participation-of-retail-investors-in-algorithmic-trading-_96979.html) and its [official PDF](https://www.sebi.gov.in/sebi_data/attachdocs/sep-2025/1759232056254.pdf) — states that, from 1 April 2026, the February 2025 framework together with exchange implementation standards and detailed operational modalities applies to all stock brokers.
3. [NSE circular NSE/INVG/67858, 5 May 2025](https://nsearchives.nseindia.com/content/circulars/INVG67858.pdf) — implementation standards, including static IP mapping for client API access and broker-side controls.
4. [NSE retail algo FAQ, 3 November 2025](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/FAQ_Retail%20Algo_03112025_NSE.pdf) — says API orders are treated as algo orders and describes tagging and other operational questions.
5. [NSE Decision Support Tools / Algorithm Trading page](https://www.nseindia.com/static/trade/platform-services-non-neat-decision-support-tools-algorithm-trading), updated 27 May 2026 — points members to NSE circular NSE/INVG/73992 dated 30 April 2026 for current application and registration requirements.

## Engineering interpretation for this repository

- Read-only market-data retrieval and simulated/paper fills are the configured use case. The Kite adapter has no order-submission or cancellation route; the rehearsal capability is deliberately read-only.
- Local signal generation is not a permission to send orders. Do not infer API, broker, exchange, vendor, or client approval from Kite subscription, account access, this source review, or paper results.
- The compliance and promotion gates must remain fail-closed when broker/venue permission evidence, required registration/tagging evidence, or human authorization is absent.
- Before considering any live order path, obtain current written confirmation from the broker about the account/API setup and the applicable framework, identify who is responsible for exchange registration and algo tagging, verify current exchange rules and static-IP/hosting requirements, and record qualified compliance/legal review. Re-check source circulars because operational requirements can change.
- This note does not authorize live orders. `LIVE_CASH` and physical-order permission remain disabled.

## Acceptance evidence and blocker

The code-level gate rejects missing/false permission facts. The project has not supplied broker/venue permission records, registration/tagging artifacts, or a qualified applicability review. PE-0214 therefore remains **PARTIAL**. The source research improves the evidence base but does not satisfy the missing external approvals.
