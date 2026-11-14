# PE-0195 CI evidence

Existing PR head `da72876` completed CI run 43580566788 on Ubuntu and Windows. Ubuntu reported 4026 passing and 17 skipped tests. Lint, mypy, migrations, inventories, full pytest, coverage report, pattern coverage and dependency audit passed.

New work adds accounting invariants, archive path guards, and a crash-after-fill-and-fee recovery test. Focused checks passed (78 tests, Ruff, mypy). A new CI run is required for this exact source revision. PE-0195 remains partial until the updated PR matrix passes and changes land on `main`; independent approval is required.
