# PE-0206 capability evidence audit

The capability audit manifest binds each curated capability to current SHA-256 values for its source files, executable test modules, maturity and matrix. A per-capability artifact hash and overall manifest hash bind those rows. CI runs the staleness check after the full test suite, so changed source/test content or maturity requires an explicit manifest update before a green merge.

The curated evidence scope is expanded from five to eight capabilities. The added rows bind the forward paper account authority boundary, registered paper book isolation, and market-data availability lineage to their executable source and test files. The two account/OMS rows describe locally tested boundaries with explicit remaining governance and legacy-engine gaps; the market-data row remains partial pending an authorized observed Kite session and verified provider timestamp semantics. The manifest generator/checker and these focused suites are being validated on the current branch. CI checks the manifest after the complete test suite on both Ubuntu and Windows.

The matrix is still curated rather than exhaustive. File hashes do not establish prospective profitability, external custody, real-feed behavior, provider entitlements or completeness of platform-wide evidence.
