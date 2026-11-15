# PE-0099 — Persistent risk latch

PaperAccount now persists risk-latch trip/reset events in its hash-chained journal. A restart restores the latch and outstanding orders/reservations; new intents remain blocked until a matching, timestamped, human hash-backed repair signoff is journaled. Existing orders remain visible and can be managed.

The checklist acceptance criterion passes for the durable cash-equity PaperAccount. Cross-asset margin reservation remains tracked under its own workstream.

Evidence and source hashes are in `pe-0099-persistent-risk-latch-evidence.json`.
