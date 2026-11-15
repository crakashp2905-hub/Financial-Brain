# PE-0200 journal rotation evidence

Journal rollover now creates immutable segment files and appends a fsynced `MANIFEST.jsonl` entry linking each segment SHA-256 and folded state SHA-256 through an append-only sequence/previous-hash chain. Existing segment paths cannot be overwritten, active-journal aliases and segments outside the manifest root are refused, and recovery rejects tampered/missing segments or a corrupted middle manifest record.

Verification: durability, archive and crash-recovery suite passed 72 tests; Ruff and mypy passed. Hash-chain integrity is tamper-evident relative to a trusted manifest head; external custody of that head is outside this local archive feature.
