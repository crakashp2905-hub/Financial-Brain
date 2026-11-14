# PE-0155 — Portable daily evidence bundle and operator report

## Implemented

- Added a portable directory bundle containing the exact paper-account close archive and durable runner journal, plus any additional caller-selected evidence inputs. Each input path, byte count, and SHA-256 is listed in a canonical manifest.
- The operator report is generated only from the exported, hash-verified account archive and journal. It includes session outcome, NAV/cash, order quantities, inventory and marks, event denominators, and evidence hashes; it does not imply live execution or profitability.
- A different workstation can independently validate the EOD archive hash, runner journal hash chain, manifest and all input hashes, then regenerate and byte-compare the report. The bundle rejects undeclared files and symlinks.
- Optional Ed25519 signing is supported when a caller supplies an owner-controlled key. The verifier reports unsigned/hash-only, valid-but-unpinned, or trusted-key signature state; callers must pin the expected public-key hash to authenticate the signer.
- Bundle creation is idempotent for the same manifest and refuses altered content at an existing output path.

## Verification

`tests/test_daily_evidence_bundle.py` and `tests/test_eod_paper_archive.py`: 8 passed; Ruff passed. The cross-workstation fixture copies the bundle, pins the Ed25519 key, verifies all artifacts, and reproduces the human report. Tampering and untrusted-key cases are refused.

## Remaining evidence gap

The module is callable but is not yet run by a scheduled daily launcher. Production signing requires a managed owner key and out-of-band trusted-key pin. The package is a portable directory; operators must transfer it intact. This remains PARTIAL until daily orchestration and key custody are wired.
