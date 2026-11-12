# Executable champion cohort input contract

`evaluation.champion.resolve_executable` consumes a frozen schedule plus normalized order, fill, session, and mark evidence. It writes to `champion_executable_cohorts`, preserving the existing `champion_cohorts` close-to-close diagnostic table. Promotion standings prefer executable records for a matching `(day, strategy)`.

## Inputs

- `scheduled`: `cohort_id`, `day`, `strategy`, and positive `capital`. A cohort covers its entry session and the following `horizon` exchange sessions. Starts for a strategy cannot overlap.
- `exchange_sessions`: the frozen, ordered exchange calendar used to select every cohort's endpoint. Calendar dates must be unique and ordered.
- `session_evidence`: one row per `cohort_id` and exchange session, including `complete` and `corporate_actions_checked`. This explicitly records sessions without orders, so idle cash remains measurable.
- `orders`: every attempted order, with stable `order_id`, cohort and strategy identity, submitted UTC timestamp, session, symbol, side, requested quantity, and final state. Accepted final states are `FILLED`, `NO_FILL`, `REJECTED`, `CANCELED`/`CANCELLED`, or `EXPIRED`. A still-open `PARTIAL` state is unresolved; a partially executed order must reach a terminal canceled/expired state before the cohort can resolve.
- `fills`: observed `fill_id`, `order_id`, fill session and UTC timestamp, positive quantity/price, and nonnegative fees. Partial fills use the quantity actually executed; unfilled remainder stays in cash.
- `marks`: one price per symbol/session with source and UTC observation timestamp. Held inventory must have a mark on every session from entry through endpoint.

The resolver rejects malformed identities and duplicate facts. Cohorts with missing session or corporate-action evidence, nonterminal orders, inconsistent fill quantities, insufficient cash, short inventory, or missing marks are recorded incomplete with a null return. No fill or mark is inferred. For complete cohorts, return is closing cash plus marked inventory over opening capital; gross excludes recorded fill fees and net includes them. Cash days, no-fill/canceled orders, rejected orders, partial fills, and attempted orders are counted separately.

## Research universe capture

When `universe_snapshots`, `security_lineage`, `features`, and `adjusted_prices` are available, champion recording captures each NSE EQ listing for the decision session in `champion_universe_attempts`. The row records whether it was tradable, eligible, selected by the strategy, or rejected for a specific data/liquidity/rule reason. If the snapshot schema or session rows are unavailable, `champion_universe_capture` records that absence instead of claiming a complete capture. Non-tradable names are excluded before books are constructed.

When a close-to-close diagnostic cohort resolves, each captured listing receives an observed/missing path status and the IDs of recorded corporate actions between entry and endpoint. Cohort reports include attempted and rejected counts, path coverage, action counts, the mean observed attempted-universe return, and a conservative sensitivity assigning -100% to each missing path. This sensitivity is diagnostic and does not replace the selected-book return. The action count only describes records present in the local corporate-action table; it cannot establish that the feed contains every market action.

## Integration status

The functions and fixture tests establish the accounting and coverage contracts. PE-0110 remains **PARTIAL** until durable `ForwardSessionRunner` / `PaperAccount` events are exported into these inputs and real prospective cohorts complete. PE-0111 remains **PARTIAL** until the historical universe and corporate-action feeds have demonstrated complete source coverage. Synthetic tests do not establish market profitability or production data provenance.
