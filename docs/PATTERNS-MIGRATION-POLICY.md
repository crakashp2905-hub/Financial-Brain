# Chart patterns: versioning and migration policy

Covers detector, spec, catalogue and event schemas. See `PATTERNS-VERSIONING.md` for the version fields themselves.

## Rules

1. **Stable IDs never change meaning.** A catalogue ID keeps its meaning. A changed definition gets a new ID or a new version of the row.
2. **Migrations are explicit functions.** `catalogue.migrate_document` is the only catalogue migration. It refuses any change that would drop, rename or duplicate an ID (`MigrationRefused`).
3. **Input is never mutated.** Migrations return new objects. Protected files (see `docs/audits/protected-baseline.json`) are never rewritten by a migration.
4. **Optional fields are added without changing old hashes.** `PatternEvent.to_dict` adds the lifecycle keys (`observed_at_utc`, `generated_at_utc`, `expired_at_utc`, `invalidation_reason`, `expiry_reason`) only when set. Events written before they existed keep their payload hash. A test pins this.
5. **Detector changes bump the version.** `VERSION` in a detector module is recorded on every formation. A change in output for the same bars requires a new version string.
6. **Schemas are checked in.** The catalogue and event schemas live in `docs/schemas/`. A change to either schema needs a matching test update in the same commit.
7. **Unknown versions fail closed.** A catalogue with an unrecognised `schema_version` raises rather than guessing.

## Procedure for a breaking change

1. Add the new version alongside the old one; do not edit the old one in place.
2. Write the migration as a pure function with a test that the ID list is unchanged.
3. Record the change in `docs/CHANGELOG-PATTERNS.md`.
4. Update the protected-baseline hashes only when a protected file is itself deliberately changed, and say why in the commit message.
