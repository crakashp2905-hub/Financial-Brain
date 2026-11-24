# Cross-desk exposure overlap primitive

Date: 2026-11-25

`portfolio.cross_desk_overlap` compares two exposure books at one identical UTC
as-of timestamp. Exposure vectors use namespaced canonical keys and signed INR notionals;
options must arrive as delta-equivalent exposure. The result includes shared gross
notional, signed common notional, a conservative overlap ratio whose denominator includes
unresolved gross, a known-only ratio, directional overlap, source references and each
book's look-through coverage.

The comparator refuses time-misaligned books and invalid identity keys. Unknown ETF/MF
constituent exposures are represented as `unresolved_gross_inr`, which lowers known
coverage and the conservative ratio instead of disappearing from the denominator. It does
not infer constituent exposures from an ETF/MF unit or expand NIFTY option exposure across
index constituents. A caller needs sourced, point-in-time holdings and Greeks/constituent
weights to do that correctly.

Focused verification: `tests/test_cross_desk_overlap.py` reports 3 passed; Ruff and targeted
mypy pass.

**Status: partial.** No adapter currently builds aligned `ExposureBook` values for H005,
H003, P3, NIFTY options, ETFs, or MF folios from their canonical books. The primitive is
not yet a live portfolio overlap report, and its caller-provided source references do not
validate redistribution rights.
