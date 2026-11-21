# Five-desk launch and evidence contract

`launch-gates.v1.json` is the machine-checked launch contract for SWING,
INTRADAY, OPTIONS, ETF, and MUTUAL_FUND. The product charter remains the source of
desk scope. This contract defines the evidence needed before a desk can progress
between stages; it does not claim a desk is implemented or profitable.

## Evidence labels

| Label | Meaning | Upgrade requirements |
|---|---|---|
| `FIXTURE` | Deterministic synthetic software test | Frozen fixture and seed; only claims behavior under those inputs |
| `REPLAY` | Historical, assumption-dependent simulation | Immutable input window, point-in-time/no-lookahead controls, explicit costs and fills |
| `LOCAL_PAPER` | Simulated orders against observed market data | Permitted source/use, timestamps/provenance, reconciled paper book, conservative costs and fills |
| `ATTESTED_FORWARD` | Prospective paper session with independently verifiable custody | Permitted observed source, sealed pre-entry decision, complete session and verified independent receipt |
| `BROKER_APPROVED` | Operational launch gate, **not** a performance-evidence class | Separate owner, broker/venue, compliance, data-rights and risk approval plus OMS rehearsal, reconciliation, human approval and rollback |

`BROKER_APPROVED` never upgrades a strategy's performance evidence. Approval and
performance evidence answer different questions. Caller-supplied labels do not
grant a stage. Any failed, missing, stale, or unknown gate blocks promotion.

## Desk-specific preflight

- **Swing:** effective-dated listings/corporate actions, overnight gap and
  concentration stress, holding-period exits, liquidity and cost limits.
- **Intraday:** exchange session, quote age/spread/depth and participation cap,
  forced-close policy, margin, turnover and fees.
- **Options:** effective-dated contract/lot/tick/expiry, executable bid/ask and
  multi-leg fill stress, Greeks/volatility/margin, expiry and settlement risk,
  and defined maximum loss. Naked writing remains excluded.
- **ETF:** whole-unit quantity, executable exchange price, spread/liquidity,
  tracking difference, premium/discount, and separate market price, NAV and index.
- **Mutual funds:** effective-dated scheme/plan/option, cutoff and holiday rules,
  NAV publication, allotment/settlement, fees and purchase/redemption restrictions.
  Exchange ticks and assumed intraday fills are prohibited.

All desks also require source/use rights, valid point-in-time instrument metadata,
fresh data and clocks, passing portfolio risk/cost/capital limits, and reconciled
orders, fills, cash, positions and P&L.

## Current state

All five charter entries remain `SCOPE_ONLY`. `BROKER_APPROVED` is blocked and
`PHYSICAL_ORDERS_ENABLED` / live cash remain false. Kite quote paper-simulation and
archive permissions remain `UNKNOWN_RIGHTS` pending provider-specific evidence;
independent timestamp custody is not configured. The contract does not override
either external blocker.

CI validates that the five desks and labels are all present, each desk has its own
preflight, source rights are required for local paper, forward evidence requires
custody, and the broker gate cannot be self-asserted or enabled by this specification.
