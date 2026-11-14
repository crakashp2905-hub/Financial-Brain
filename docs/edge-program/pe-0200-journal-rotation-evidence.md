# PE-0200 journal rotation evidence

Rollover now refuses the active journal as its own archive destination and refuses to overwrite an existing archived segment. Tests check both failures preserve journal/archive bytes. Existing tests verify compacted snapshot state hash and backup restore equivalence. Recovery checks per-record hash links and rejects terminated corrupted records. Verification: 78 combined focused accounting, archive and crash tests passed; Ruff and mypy passed. A cross-segment append-only archive catalog/manifest chain remains outstanding.
