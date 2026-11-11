# Chart patterns: data conventions

- Adjusted versus unadjusted: detectors run on the series they are given. Split and bonus adjustments are applied
  before detection (`marketdata.adjust_for_split`), so an ex-date does not look like a crash. Adjusted prices
  are never written back into source data.
- Corporate actions: gaps coinciding with a corporate action are classified `EXCLUDED_CORPORATE_ACTION` and never
  become breakouts (`candles.classify_gap_with_exclusions`).
- Timestamps: naive timestamps are refused. Aware timestamps are shown in Asia/Kolkata (+05:30) and persisted in UTC.
- Sessions: a session exists only where the versioned calendar table says so. Weekday arithmetic is not used.
- Missing bars: listed, never forward-filled.
- Volume: missing, negative or long zero-volume runs make volume rules ineligible (`DATA_INSUFFICIENT`).
- Identity: an alias that maps to two identities is withheld rather than guessed.
- Snapshots: each input is fingerprinted by row count, session range and canonical SHA-256.
