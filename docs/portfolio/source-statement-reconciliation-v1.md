# Canonical portfolio source-statement reconciliation v1

`fb reconcile-source-events` compares normalized external statement events with the source
event rows recorded in a canonical paper ledger. It is a read-only comparison apart from
persisting the report receipt in the same SQLite database.

## Input contract

The JSON document has exactly these top-level fields:

```json
{
  "schema": "fb.portfolio-source-statement.v1",
  "account_id": "paper-main",
  "source_system": "kite-paper-import",
  "period_start": "2026-11-20T09:00:00+05:30",
  "period_end": "2026-11-20T15:30:00+05:30",
  "events": []
}
```

Each `events` entry uses the canonical event fields: `source_event_id`, `source_sha256`,
`event_kind`, `occurred_at`, and `sleeve_id`, plus the applicable `currency`, `asset_class`,
`instrument_id`, `side`, `position_action`, `quantity`, `price`, `multiplier`, `cash_delta`,
`evidence_class`, and `metadata`. Decimal values must be JSON strings to preserve exact values.
`account_id` and `source_system` may be supplied per event or inherited from the document.
Event times must be timezone-aware and lie in `(period_start, period_end]`.

## Run

```text
fb reconcile-source-events --ledger path/to/paper-ledger.sqlite --statement statement.json --output reconciliation.json
```

The report includes the raw statement file SHA-256, normalized statement and ledger row-set
hashes, row counts, matched event count, statement-only IDs, ledger-only IDs, changed IDs,
reconciliation status, and a stable report hash. The SQLite receipt becomes invalid if a
canonical source event is added or changed inside the report cutoff. Events after the cutoff
do not invalidate the historical receipt.

Exit status is `0` only when rows match and the persisted report remains current; `2` means
invalid input, a row mismatch, or stale report state.

The file hash binds the report to supplied bytes; it does not establish who issued those bytes.
This tool does not authenticate the broker or prove that the statement covers the complete
account period. A matched row set is not a broker-confirmed account reconciliation or a
profitability claim.
