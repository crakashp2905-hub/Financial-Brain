# PE-0055 bounded-search outcome journal

The existing daily bounded random-search entry point now freezes its variant catalogue and mechanism source digest before loading outcomes. Each variant receives exactly one durable result with an explicit outcome and canonical details digest; the underlying journal is append-only and hash-chained. A run cannot close while any declared variant lacks a result. If evaluation errors, remaining variants are recorded as `ABORTED` and the original exception is re-raised.

The journal defaults to `data/bounded_search_journal.jsonl` and can be overridden through the `audit_path` argument. The CLI's JSON output retains the complete per-variant result and includes the audit run ID/path. The journal is separate from the optional summary JSON and is not a trial ledger, profitability claim, order path, alert path, or promotion mechanism. Any use must preserve the exact local audit file with the result artifact.

Acceptance coverage exercises interrupted-run recovery, duplicate and missing result rejection, bounded variant counts, non-finite float canonicalization, and end-to-end bounded-search recording for all frozen variants. This remains partial: full historical data/variant runs, independent replays, larger mechanism families, and prospective paper results have not been produced.
