# PE-0108 — Canonical scheduled-session verification

Promotion-grade verification now requires a frozen scheduled-date set and pinned trust roots. Each candidate day is checked against the canonical decision manifest digest, the signed receipt, and decision/entry timing. Missing roots or schedule dates fail closed to an empty independent numerator.

Arbitrary verifier callbacks were removed because a callback could grant eligibility without using the canonical custody contract. The existing optional anchor row stores the provider key ID and signature needed by the canonical verifier.

**Status: PARTIAL.** PE-0103 has not yet resolved frozen date ranges into authoritative exchange-session dates, and no owner-approved timestamp service or production trust root is configured. Synthetic signatures validate software behavior only.

Evidence and file hashes are in `pe-0108-canonical-schedule-verification-evidence.json`.
