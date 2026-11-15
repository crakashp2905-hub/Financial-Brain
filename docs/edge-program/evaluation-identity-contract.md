# Protected evaluation identity

Protected evaluations use `EvaluationIdentity` plus the `ExperimentCharter` and immutable `CohortSchedule`.

The identity contains SHA-256 digests for strategy code, model code, the source data snapshot, the declared data-rights record, market calendar, point-in-time universe, execution-cost schedule, risk/stop policy, and each frozen benchmark definition. The respective data-rights, calendar, and cost schedule identifiers travel with their digests. The charter adds strategy/version, parameters, capital, dates, universe, benchmark names, and holding horizon. Canonical serialization hashes all of these fields into the charter ID.

`CharterRegistry` appends registrations and cohort closures to the existing fsynced hash-chain journal. A protected registration requires a complete identity and a cohort schedule bound to that exact charter hash. Reusing a version with changed code, rights, calendar, costs, benchmark, or endpoints is rejected. A change requires the next version, the previous charter hash, and a later prospective start date. A closed cohort outcome is immutable. Reopening the registry replays and validates the append-only history.

The protected-trial gate now requires the charter, its cohort schedule, and the durable registry after approval validation. Hashing a data-rights record documents its declared basis; it does not prove the owner's contractual rights or completeness of the upstream data.
