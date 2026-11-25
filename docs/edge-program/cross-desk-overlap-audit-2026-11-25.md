# Cross-desk exposure overlap primitive

Date: 2026-11-25

`portfolio.cross_desk_overlap` compares two exposure books at one identical UTC
as-of timestamp. Exposure vectors use namespaced canonical keys and signed INR notionals;
options must arrive as delta-equivalent exposure. Gross exposure is retained by canonical
key before netting, so opposing positions within one book cannot disappear from the gross
overlap denominator. The result includes shared gross notional, signed common notional, a
conservative overlap ratio whose denominator includes unresolved gross, a known-only ratio,
directional overlap, source references and each book's look-through coverage.

`exposure_book_from_canonical_snapshot` joins a canonical ledger snapshot to caller-supplied
per-instrument look-through records. It enforces INR marks, exact snapshot as-of alignment,
component availability, source references, sleeve scope and explicit unresolved gross for
legacy rows. It does not fetch or authenticate the look-through inputs. A caller needs
sourced, point-in-time holdings and Greeks/constituent weights to expand ETFs, MF units and
NIFTY options correctly.

Unresolved ETF/MF constituents lower known coverage and the conservative overlap ratio
instead of disappearing from the denominator. Missing marks, future-available components,
stale/out-of-scope mappings and unpriced legacy positions are refused or require explicitly
sourced unresolved gross.

Focused verification: `tests/test_cross_desk_overlap.py` reports 6 passed and the combined cross-desk/canonical-ledger suites report 15 passed; Ruff and targeted
mypy pass.

**Status: partial.** The snapshot adapter is not yet wired to H005, H003, P3, NIFTY options,
ETF or MF book/provider resolvers. There is no live portfolio overlap report, and
caller-provided source references do not verify data rights or independently attest source
artifacts.
