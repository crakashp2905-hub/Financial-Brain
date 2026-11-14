# PE-0189 — Research ranking versus forward champions

## Acceptance

Keep provisional research rankings separate from verified executable forward champions. A ranking diagnostic must never receive a tradable-edge badge.

## Implementation and verification

`forward_edge.cockpit.research_vs_champions` renders separate provisional and champion sections. Every provisional row is labeled `PROVISIONAL — RESEARCH ONLY`; that section never uses the promotable style. A forward row receives a champion label only with VERIFIED status, explicit executable-forward evidence, positive verified sessions, equal receipt-session coverage, and a valid SHA-256 evidence digest. Incomplete or non-verified rows remain visible as non-champions.

Verification: `tests/test_research_vs_champions_view.py` — 2 passed; combined research/champion cockpit run — 22 passed. Ruff and targeted mypy passed.

## Limits

The function renders supplied registry and evidence claims; it does not independently validate the forward receipts or compute strategy profitability. Upstream evidence verification remains the authority for those fields. No current candidate is promoted by this view.
