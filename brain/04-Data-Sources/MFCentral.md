---
type: resource
tier: 1
verdict: OWNER-ONLY
tags:
  - resource
  - data-source
  - mutual-funds
---

# MFCentral

**Owner-only.** The consolidated view of a holder's own mutual-fund holdings requires
**the owner's PAN and an OTP**. Claude does not authenticate as the owner, so this is not
automatable here.

The clean paths instead:
- the owner exports the CAS themselves and the file is ingested;
- [[AMFI NAV feed]] for scheme-level prices and ISINs.

Related: [[AMFI NAV feed]] · [[Kite Connect]]
