# Trader Core v1 audit — 2026-10-29

The pasted “world-class platform” proposal is a product direction, not evidence that
its checklist was absent from this checkout. This is a local implementation audit;
it does not claim profitable trading or regulatory readiness.

| Proposed requirement | Local implementation | Remaining acceptance gate |
|---|---|---|
| Audit modules and tests | `docs/ROADMAP-AUDIT-2026-10-29.md`, tests, this matrix | Complete suite and CI baseline; remote state not verified |
| Daily full-market scanner | `opportunity/scanners.py`, `opportunity/discovery.py`, `opportunity/engine.py` | Daily completeness, PIT and freshness SLO on every intended session |
| Net-alpha and uncertainty ranking | `decisions/net_alpha.py`, `decisions/opportunity_card.py`, `paper/trader_view.py` | Prospective calibration and net value against frozen momentum |
| Portfolio allocation/replacement | `portfolio/optimizer.py`, `portfolio/next_capital.py`, `decisions/exit_brain.py` | Same-universe incremental utility trial after costs |
| Market/opportunity/position cockpit | `paper/terminal.py`, `paper/trader_view.py` | Measured trader task success, latency and drill-down accuracy |
| Reproducible paper trading | `paper/book.py`, `paper/intraday_runner.py`, `paper/reconcile.py` | Same-day paired live/replay cases and realistic fill coverage |
| Frozen baseline comparison | `evaluation/champion.py`, `evaluation/prospective.py` | Existing picks are unattested; new cohorts need time to mature |
| Chosen/rejected/missed memory | `opportunity/memory.py`, `evaluation/regret.py`, `paper/decision_journal.py` | Feasible-as-of alternative capture and prospective policy comparison |
| Strategy reliability/deployment gates | `risk/degradation.py`, `risk/killswitch.py`, `agents/brain.py`, `interfaces/trader_core.py` | Incident/recovery drills; no live authorization from this workflow |

`fb trader-core` now combines the verified opportunity snapshot, deterministic agent
verdicts, kill-switch state, champion attestation audit and 30-day paper reconciliation
in one read-only JSON response. A missing or tampered snapshot yields `ABSTAIN`; a
missing kill-switch state causes individual decisions to abstain. The command does
not fetch data, send alerts, approve a trade or place orders. `fb terminal` still
publishes the underlying snapshot; run it separately after data checks.

The next decisive experiment remains a registered, same-universe comparison of a
frozen naive-momentum policy with a portfolio-aware challenger. Do not tune against
the protected holdout or relabel historical picks as forward evidence.
