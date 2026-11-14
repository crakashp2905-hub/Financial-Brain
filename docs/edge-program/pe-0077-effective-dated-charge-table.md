# PE-0077 effective-dated trading charges

The execution cost schedule selects rules by segment and effective date, hashes the complete table, and computes side-specific brokerage, exchange, SEBI/IPFT/clearing fees, STT, stamp duty, and GST with paise rounding applied once to the total. Spread crossing is represented by executable ask/bid fills, not hidden inside the fee table; its quote evidence and fill price remain separate from statutory charges.

The task is `DONE` for configurable cost-table mechanics. Fixture rates are explicitly test data, not a statement of the current Indian tariff. An owner-supplied and independently checked effective-dated rate table is needed before describing paper costs as broker-accurate.
