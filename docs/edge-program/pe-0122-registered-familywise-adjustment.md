# PE-0122 — Registered-family multiplicity control

`adjust_registered_family` applies Holm family-wise correction over a frozen set of registered trial IDs. It hashes that family, rejects duplicate or unknown trials, and retains unrun/failed candidates as p=1 instead of dropping them. A 100-trial fixture verifies that the selected p-value is adjusted against the whole declared family.

**Status: PARTIAL.** The family is still supplied by the caller and is not automatically bound to all preregistered strategy/horizon/filter searches or a governance-approved registry snapshot.

Evidence and source hashes are in `pe-0122-registered-familywise-adjustment-evidence.json`.
