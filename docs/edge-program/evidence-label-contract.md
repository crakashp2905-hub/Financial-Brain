# Evidence labels at report boundaries

Every supported report mode maps to one of four explicit classes: exploratory shadow, historical replay, local paper, or independently attested forward. Fixture and replay modes remain historical even if a caller attaches fields that normally describe market observation or custody. Unknown modes fail closed.

Observed-market local paper stays `LOCAL_PAPER` by default. A forward-paper claim needs observed market data, a sealed pre-entry decision and a complete session. It can receive `INDEPENDENTLY_ATTESTED_FORWARD` only when the report path is supplied the custody verifier's `VERIFIED` result as well. This classification helper labels existing evidence; it does not create evidence or verify a signature.

The fixture CLI, daily paper report, intraday markdown export, option replay and prospective option paper results, execution audit, statistical row exporter, and paper-evidence ledger now include an explicit class. Source-aware claim tiers return only the replicated tier when a source is missing or replay; their compatibility wrapper never uses caller booleans alone to assert forward status. Reports reject a caller-supplied class that conflicts with the mode. Replay and fixture results cannot be promoted by relabelling at a UI/export boundary.
