# PE master checklist progress

This tracker belongs to the 218 unique `PE-####` tasks in
[`MASTER_CHECKLIST_2026-11-12.md`](MASTER_CHECKLIST_2026-11-12.md). The separate FE-0001..FE-0152 tracker is not a
substitute for this checklist. Task wording is retained from the attachment; the repo copy adds checked boxes and
completion evidence only after acceptance criteria pass. The original Downloads file is untouched.

Verified checklist count: **4 / 218 complete**. Partial tasks are reported separately and are not included in this count.

## Current package status

| Task | Status | Evidence | Remaining acceptance gap |
|---|---|---|---|
| PE-0015 | Complete in software scope | `tests/test_forward_edge_session_state.py` (durable lifecycle transitions, terminal/invalid transition rejection, retry idempotency, monotonic clocks, session ID binding) | Full runner integration and restart recovery of session side effects remain separate tasks. |
| PE-0016 | Complete in software scope | `tests/test_forward_edge_session_state.py` (canonical sorted JSON and decimal normalization) | The stored config is bound to a session ID; authenticated operator selection and live-ledger registration remain separate tasks. |
| PE-0017 | Complete in normalized event intake scope | `tests/test_forward_edge_session_runner.py::test_fixture_and_kite_adapters_enter_the_same_normalized_runner` | Runner consumes fixtures and dated records from the same Kite archive used by the existing live paper loop. Strategy orchestration, risk, OMS, fills and accounting are later tasks. |
| PE-0018 | Not yet applicable to this checkout | Source audit (`rg --files src/financial_brain/forward_edge`; `rg -n "def run_session" src tests`) found no `forward_edge.pipeline` module or `pipeline.run_session`; current replay is `paper.intraday_runner.run_replay` and is explicitly historical-only in `forward_edge/cli.py` | Reconcile checklist target with current source/history, then establish a golden fixture contract before adding or changing a replay API. No behavior was fabricated to satisfy the absent target. |
| PE-0021 | Partial | `ForwardSessionStore` and `ForwardSessionRunner` persist lifecycle, source events, decisions and report hashes in SQLite WAL | Shared durable journal and decision-manifest integration, transaction boundaries and fault-injection proof remain open. |
| PE-0022 | Partial | Durable kernel outbox now wraps deterministic paper execution receipts; tests cover filled/partial/missed/rejected, crash-after-receipt UNKNOWN, explicit reconciliation and no duplicate receipt | Canonical OMS order acknowledgements and double-entry account postings are not integrated. |
| PE-0023 | Partial | `tests/test_forward_edge_session_runner.py` verifies persisted preflight, market events, causal decisions, paper intents and execution receipts | Strategy-driven data rejection and risk verdicts, OMS state and canonical fill/cost accounting still need the integrated strategy/OMS path. |
| PE-0024 | Complete in session identity scope | `tests/test_forward_edge_session_state.py::test_strategy_attempt_keys_are_durable_and_never_overwrite_prior_attempt` verifies stable strategy/date/cohort/mode/attempt keys, immutable config binding and separate persisted attempts | Callers must use `session_attempt_key()` and unique operator or scheduler `attempt_id` values for intentional reruns; automatic scheduling is a separate integration task. |
| PE-0025 | Partial | `forward_edge/clock.py` provides injectable UTC, fixed and market-clock readings; `market_data.normalize_kite_quote()`, `paper.intraday_runner.run_live()` and `ForwardSessionRunner` accept the shared provider for receipt, availability, decision and simulated execution times. `tests/test_forward_edge_clock.py` plus session-runner tests cover injected clocks, availability ordering and execution latency. | The production supervisor does not yet instantiate and pass one shared provider through the live poller, session state, decision journal and execution lifecycle. |
| PE-0028 | Partial | `ForwardSessionStore.config()` rehydrates the immutable run contract; `ForwardSessionRunner.resume()` and `fb forward session --resume SESSION_ID` restore persisted event/decision/receipt evidence, preserve UNKNOWN outbox items for manual reconciliation, and recover a DRAINING close only when evidence is complete. Tests cover restart, unknown effects and CLI recovery. | Acceptance also requires risk, positions and cash to reconstruct identically. The forward runner still lacks integrated OMS/accounting, so PE-0028 is not checked complete. |
| PE-0032 | Partial | `QuoteArchive` stores the raw Kite payload SHA (`MarketEvent.evidence_id`), normalized event JSON and exchange/receive/available clocks, plus an injected immutable `ingested_at`; legacy archives migrate with `NULL` ingest clocks rather than fabricated values. `tests/test_forward_edge_quote_archive.py` covers clock immutability and legacy migration. | The forward runner's durable session manifest does not yet persist archive ingest time alongside each event and execution receipt, so an arbitrary fill cannot yet be reconstructed from the session package alone. |
| PE-0019 | Partial | `tests/test_forward_edge_kite_market_data.py`, `tests/test_phase5_intraday_runner.py`; commit `53ca3e3` and receipt identity fix `22e4c24` | A genuinely observed Kite session and account entitlement/retention record are not present in the repository. |
| PE-0030 | Partial | `normalize_kite_quote()` retains available best bid/ask, visible quantities, depth, volume, session OHLC and circuit limits | Legacy `ingest/kite_live.forward_events` is absent and the normalized fields are not yet consumed by an execution gate. |
| PE-0031 | Partial | `test_ltp_only_observation_is_archived_as_non_executable_tick` and `test_executable_quote_requires_two_sided_positive_displayed_liquidity` | `executable_quote()` now rejects ticks, missing depth and zero displayed liquidity; all strategy and promotion adapters still need to call it at execution entry points. |
| PE-0110 | Partial | `tests/test_executable_outcomes.py`; commit `abfa1db` | Resolver currently consumes supplied outcomes; it is not populated from actual forward orders and marks or wired into promotion cohorts. |

## Current external gates

- Independent pre-entry timestamp custody still lacks a selected provider and pinned trust root.
- Real-market session evidence must accumulate prospectively; fixtures and historical replay cannot satisfy it.
- GitHub integration denies branch-protection reads and PR creation with HTTP 403. Local `gh` credentials are invalid, so this workspace cannot create/update the PR or change repository protection settings.
- Protected Q4 2026 trial budget is exhausted at 13/13; new confirmatory research requires the permitted governance pathway.

This file records implementation evidence and blockers. It does not claim positive net alpha, profitable strategies, live authorization, or completion of the PE checklist.
