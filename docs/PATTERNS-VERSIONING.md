# Chart patterns: versioning and migration

- Detector versions are the `VERSION` constant in `detectors.py`. A changed rule means a new version and a new event identity.
- `PatternSpec` identity is its SHA-256 over canonical JSON. Any parameter change changes the hash.
- Catalogue IDs never change meaning. Migration from v1 must preserve old records; v1 is the only version.
- Event versions are keyed by (event_id, status). Rewriting a stored version is refused (`ConflictingVersion`).
- Backups carry a SHA-256 checksum; restore refuses a mismatch.
- Backward-compatible migration tests for new schemas are not yet built; see the final gates.
