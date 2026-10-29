# 2026-10-29 local audit of the formidable roadmap

This is a code-and-artifact inventory, not a profitability verdict. The external roadmap
states that its GitHub review failed and used an older snapshot. Local checkout `main`
at `ffa99ae` was available on 2026-10-29, one commit ahead of `origin/main` at audit
time. The worktree also contained uncommitted agent/MCP/journal changes from the
preceding work; this audit preserves them. The remote GitHub and CI state were not
verified by this local inspection.

Verification in this pass: focused agent/reconciliation tests passed (4/4) and Ruff
passed on the changed Python files. The full repository suite was started but stopped
before completion; it has no valid pass/fail result for this worktree. Do not interpret
the partial progress as a green suite.

| Roadmap area | Current local evidence | Honest status / remaining gate |
|---|---|---|
| PIT universe and opportunity discovery | `opportunity/discovery.py`, `opportunity/scanners.py`, `opportunity/engine.py`, `decisions/opportunity_card.py`, `paper/board.py` | Implemented core; prove a canonical daily top-20 is fresh, complete, traceable and usable on every intended session. Do not equate positive modelled alpha with a deployable edge. |
| Validation factory | `evaluation/standard.py`, `evaluation/family_validation.py`, `research/preregistry.py`, trial ledger and registered baselines; `evaluation/prospective.py` now attests new champion pick batches and filters standings to them | Implemented methods; 2026Q4 trial budget is spent, with no confirmed deployable edge. Existing champion rows lack write-time attestation and remain unverified. Preserve the final holdout and run the next registered trial only under its declared budget. |
| Portfolio allocation and exits | `portfolio/optimizer.py`, `portfolio/next_capital.py`, `decisions/exit_brain.py`, `risk/allocator.py` | Implemented components, incomplete prospective *incremental value* test against a feasible equal-weight/risk-budget baseline. |
| Execution policies and accounting | `execution/algos.py` already implements TWAP, VWAP, POV and other schedules; `paper/execution.py` attributes signal/execution/charges; OMS has paper/order stages | Implemented simulations, partial calibration against observed market depth and fills. No assumption of historical depth where none was archived. |
| Live/replay intraday reconciliation | `paper/intraday_runner.py` records live/replay separately; new `paper/reconcile.py` compares only exact same-day/strategy/symbol/side closed paper trades | Diagnostic now implemented. Current ledger has no same-day mode overlap, so no paired execution conclusion is possible yet. A separately labelled replay of live dates is needed before this comparison yields matched trades. |
| Counterfactual and analogue memory | `opportunity/memory.py`, `opportunity/replay.py`, `features/analogues.py`, `evaluation/regret.py`, inverse research | Implemented descriptive infrastructure; complete feasible-as-of constraint capture and later independent policy-value tests remain open. Hindsight regret is not a causal benefit claim. |
| Agent reliability | `committee/run.py`, `llm/router.py`, `decisions/safety.py`, read-only `agents/brain.py`, CLI/MCP gateway, versioned synthetic `fb agent-eval` | Partial. Synthetic guardrails now have a regression suite; build a separate versioned Indian financial-fact evaluation set and test numerical, temporal, citation and prompt-injection failure rates end to end. A model stance is not empirical alpha. |
| Strategy health and operations | `risk/degradation.py`, `risk/killswitch.py`, `execution/oms`, data contracts and CI | Implemented guards; demonstrate recovery, alert delivery, and state transitions on complete prospective sessions. Full local test suite result is recorded separately when it finishes. |
| Intraday/options data | Kite minute archive, live quote/depth archive, F&O snapshot, option scenarios | Partial. Delisted-name survivorship, expired option history, depth retention rights, fill coverage and contract lifecycle limit claims. |
| Trader terminal | `paper/terminal.py`, `paper/trader_view.py`, `paper/decision_journal.py`, `interfaces/gateway.py` | Implemented read-only workflow; still needs measured user-task completion, end-to-end latency and verified source freshness on every day. |
| Earnings-call intelligence | `ingest/calls.py`, `docintel/calls.py`, local `call_documents` table | 4,030 local call/presentation documents as of this audit; 0 embeddings. Structure is read; longitudinal guidance/Q&A claims and validated predictive tests are not built. |
| Elliott Wave and similar chart taxonomies | `features/wavelet.py` is frequency analysis, not Elliott counting; technical library covers many named methods | Elliott is not a validated strategy here. Any addition needs a causal pivot/count definition, disagreement/ambiguity handling, frozen costs and baselines, and a fresh registered trial rather than retrospective chart annotation. |

## Next decisive empirical experiment

The paper ledger currently contains 47,101 champion picks across 2026-10-05 through
2026-10-28 and no resolved champion cohorts (read-only local check on 2026-10-29).
Those existing picks have no creation-time attestation. `fb champion-audit` reports
attested versus unattested days; future `fb champion --record` refuses backfills,
future-outcome archives, and overwrites. Standings shown by that CLI use only attested
days. This improves evidence integrity but does not turn past rows into forward data.

Compare a **frozen naive momentum policy** with a portfolio-aware Financial-Brain
challenger on the *same* PIT universe, sessions, prices, rebalance schedule, executable
instrument set, cost model and risk limits. Register ablations before seeing final
outcomes: (1) momentum baseline, (2) ranking/evidence filter, (3) reliability-based
abstention, (4) portfolio-aware allocation, (5) execution policy. Report gross and net
return, turnover, drawdown, capacity, concentration and uncertainty. A historical win
is not deployable until a separate prospective paper cohort supports it. Do not use the
existing protected holdout to choose variants.

## What code cannot complete immediately

- Independent forward evidence needs real elapsed market sessions, complete as-of
  decisions, and sufficient effective sample size. It cannot be backfilled as "live."
- Historical depth, delisted contracts and any commercial/redistribution rights need an
  authorized data source or the owner's written provider clarification.
- Broker approval, market-hours rehearsals and any real-capital deployment remain human
  decisions. Agent and MCP paths must stay read-only and cannot reset the kill switch.
- No 10/10 rating or profitable-edge claim follows from passing tests or adding features.
