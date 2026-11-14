# PE-0201 offline backup and restore evidence

The backup helper writes a versioned immutable dependency-set bundle with safe relative paths, file lengths and SHA-256 hashes. Restore verification rejects missing/invalid manifests, traversal, duplicate names, missing or changed files, and empty bundles. Tests preserve two same-named files in separate directories, copy the bundle to an isolated restore path, delete the source tree and verify the recovered bytes.

The daily paper evidence package now always includes the close archive, paper-account journal, and forward-runner journal. Its independent verifier replays the account journal, binds the journal head and sequence to the archive, checks OMS/account state and total-return reconciliation, and reproduces the report from copied inputs. The account journal cannot be omitted.

The `fb forward daily-finalize` CLI command reconstructs the account from its journal and runs close plus bundle export after validating the durable session terminal record. Owner-supplied close marks and capture time are explicit inputs.

Verification: `tests/test_forward_edge_f06.py`: 14 passed; `tests/test_daily_evidence_bundle.py` plus `tests/test_eod_paper_archive.py`: 14 passed. Ruff passed for the changed modules; mypy passed for the daily bundle and CLI modules. These checks validate declared inputs and the account/runner evidence pair. Automatic discovery of quote archives, strategy/configuration inputs, registry/authority state, and one-command reconstruction of a complete observed session remain outstanding.
