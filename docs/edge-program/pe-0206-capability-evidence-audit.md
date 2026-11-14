# PE-0206 capability evidence audit

The capability audit manifest binds each curated capability to current SHA-256 values for its source files, executable test modules, maturity and matrix. A per-capability artifact hash and overall manifest hash bind those rows. CI runs the staleness check after the full test suite, so changed source/test content or maturity requires an explicit manifest update before a green merge.

The curated evidence scope is expanded from five to eight capabilities. The added rows bind the forward paper account authority boundary, registered paper book isolation, and market-data availability lineage to their executable source and test files. This matrix and its post-suite evidence check passed the full Ubuntu and Windows CI run [#609](https://github.com/crakashp2905-hub/Financial-Brain/actions/runs/43605705134) at commit `bf19b2fb56a8917a685cedf0a5c91582461af5f0`. The follow-up registered-book bridge changes are newer than that run and require their own full CI validation.

The matrix is still curated rather than exhaustive. File hashes do not establish prospective profitability, external custody, real-feed behavior, provider entitlements or completeness of platform-wide evidence.
