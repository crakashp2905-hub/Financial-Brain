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

## Integration status

The function and fixture tests establish the accounting and coverage contract. PE-0110 remains **PARTIAL** until durable `ForwardSessionRunner` / `PaperAccount` events are exported into these inputs and real prospective cohorts complete. Synthetic tests do not establish market profitability or production data provenance.
