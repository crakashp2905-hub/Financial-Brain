# Offline archived paper-session CLI

This CLI path consumes a local normalized JSON archive. It does not import a market-data client or broker adapter,
and its default strategy is an explicit `NO_TRADE` because a versioned strategy adapter is not yet registered in this
entry point. The result is labeled `RESEARCH_REPLAY` / `HISTORICAL_REPLAY`; it is not observed forward paper evidence.

## Archive format

The UTF-8 JSON file must contain `schema_version: 1`, the exact `exchange_date`, and a nonempty `events` array.
Each event carries normalized event, received and available timestamps with UTC offsets; the payload includes source
and exchange provenance. Events are checked against the requested date in `Asia/Kolkata`.

```json
{
  "schema_version": 1,
  "exchange_date": "2026-11-12",
  "events": [
    {
      "kind": "QUOTE",
      "instrument": "NSE:INFY",
      "event_utc": "2026-11-12T04:00:00+00:00",
      "received_utc": "2026-11-12T04:00:00.010000+00:00",
      "available_utc": "2026-11-12T04:00:00.020000+00:00",
      "payload": {
        "last_price": "1500.00",
        "source": "kite_connect_archive",
        "exchange": "NSE"
      }
    }
  ]
}
```

## Commands

```text
fb forward real-paper --date 2026-11-12 --cohort local-check --archive session.json \
  --journal session.jsonl --confirm-paper-only
fb forward session --resume 2026-11-12:local-check:attempt-1 --journal session.jsonl
```

The archive SHA, event lineage, frozen run config, preflight, session events and result are written to the durable
hash-chained journal. The resume command reconstructs the frozen config and events from that journal. If the process
crashed before persisting any events, pass the original `--archive`; when an archive hash was already frozen, a
different file is rejected. Unknown/missing execution receipts remain subject to the runner's reconciliation gate.

This CLI path covers offline orchestration and no-trade recovery. It does not yet connect the Kite runner, executable
strategy adapters, the canonical paper-account OMS/ledger, or a full fill/position reconciliation path.

Paper-capable strategies must enter the registry through the guarded `SHADOW_ONLY` to `PAPER_ELIGIBLE`
transition, with a frozen matching benchmark/charter and scoped human approval. `Registry.register()` rejects a
strategy constructed directly as `PAPER_ELIGIBLE`, `FROZEN_CHALLENGER`, or `VERIFIED`; direct status construction
must not be used to bypass enrollment evidence. Unapproved strategies remain observation-only.

The separate `ForwardPaperSessionService` now requires the exact registered paper-capable `StrategySpec` and
rechecks its status, code digest and frozen benchmark before any preflight or journal write. It then delegates to
the durable forward runner and reconciles completed receipts through `PaperAccount`. This service-level guard does
not turn archived replay into observed paper trading, and the offline CLI does not invoke the service yet.
