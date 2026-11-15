# PE-0123 — SPA and White Reality Check reporting

Family-level White Reality Check and Hansen-style SPA now report the zero-excess null, candidate and period counts, dropped incomplete periods, seed, resample count, block length and alternate half/double block-length p-value sensitivity. Candidate series with zero variance are refused.

**Status: PARTIAL.** The statistical tests pass, but the candidate matrix is supplied by the caller and is not yet checked against the complete authoritative development-search registry. Omitting tried candidates would still understate selection bias.

Evidence and source hashes are in `pe-0123-spa-reality-check-evidence.json`.
