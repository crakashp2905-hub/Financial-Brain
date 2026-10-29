# Trader-platform checklist audit — 2026-10-29

This checks the user's immediate/next/advanced list against the local checkout, not
the older “previously observed” ratings. Local HEAD is `ffa99ae0f7ba9241bd46dd85069e46d810eea264`
on `main`; configured remote `main` resolves to `37acab0bb9a1214f70ce1f52a9851b0b5f271737`.
The local branch is one commit ahead; the GitHub app returns 404, but a direct Git
remote read succeeds. The worktree has existing uncommitted work, preserved here.

| Requirement | Verified source/test evidence | Status / acceptance gap |
|---|---|---|
| Repository access and audit | Git remote read; 1,574 tests collected; 73 critical tests and 10 integration tests passed | Local/remote access verified; full suite stopped after partial progress without a pass/fail verdict; remote CI unverified |
| Market-wide scanner | `opportunity/scanners.py`, `discovery.py`, `engine.py`, terminal snapshot | Built; daily PIT/freshness/coverage SLO not demonstrated across prospective sessions |
| Frozen benchmark | `evaluation/champion.py`, forward gate `evaluation/prospective.py` | Built; old picks are unattested, zero resolved attested cohorts |
| Net-alpha ranking | `decisions/net_alpha.py`, `opportunity_card.py`, `paper/trader_view.py` | Built; estimated alpha is modelled, not proven net edge |
| Portfolio allocation | `portfolio/optimizer.py`, `next_capital.py`, `decisions/exit_brain.py` | Built; prospective incremental-utility test against simple allocation pending |
| Trader dashboard | `paper/terminal.py`, saved HTML and verified snapshot; `fb trader-core` now includes holdings | Built; user task completion and latency measurements pending |
| TWAP/VWAP/POV | `execution/algos.py`, `tests/test_phase5_exec_algos.py` | Simulation built; observed-spread/depth calibration pending |
| Order-state recovery | `execution/oms/{engine,model,kite_broker,killswitch}.py`, `tests/test_oms.py` | Implemented; live broker recovery drill, reconciliation evidence and approval pending |
| Signal half-life | `evaluation/half_life.py`, `half_life_table.py`, relevant tests | Research built; execution-coupled alpha-decay decision policy unverified |
| Counterfactual learning | `opportunity/memory.py`, `evaluation/regret.py`, decision journal | Descriptive memory built; feasible policy-value uplift unverified |
| Strategy health | `risk/degradation.py`, `risk/health.py`, `risk/killswitch.py` | Detectors built; alert/recovery/pause operations need prospective drills |
| Prospective paper experiment | champion ledger, paper books, `fb champion-audit`, `fb paper-compare` | Open: zero resolved attested cohorts and zero matched live/replay trade pairs |
| Intraday/order-book data | Kite minute archive, quote archive contract | Partial; license/retention entitlement and historical depth coverage need verification |
| F&O/options | `fo_bhav` EOD archive, `kite_fno.py`, `options_quotes.py`, `fb options-audit` | 122,389,495 daily option rows through 2026-10-26; no local option quote archive, so no historical bid/ask backtest |
| Journal/behavior | `paper/decision_journal.py`, `portfolio/journal.py`, `behaviour_review.py` | Built; owner consent and prospective decision-value test pending |
| Rust acceleration | `docs/RUST-US-OPTIONS-PLAN.md` | Deferred until a measured bottleneck and output-parity benchmark justify it |
| Commercial identity/compliance | API/service layer exists | Not built or authorized as a multi-user regulated product; requires separate scope and professional review |

The saved `fb trader-core --limit 1` check currently reports a 2026-10-28 opportunity
snapshot (10 rows), saved holdings (2 positions as of 2026-10-28), unknown kill-switch
state, zero attested champion days and no paired live/replay trades. It reports
`RESEARCH_ONLY` and `execution_allowed=false`. A saved broker holdings snapshot is
not live position reconciliation.
The integrated readout now lists the missing kill-switch state, forward-attested
resolved cohort, and same-day paired paper trades as explicit evidence gaps.

Next empirical gate: freeze the momentum champion and a portfolio-aware challenger
on the same PIT universe, dates, costs and feasible execution rules; record decisions
before outcomes; wait for independent cohorts; report net utility, turnover,
drawdown, capacity and uncertainty. Historical and synthetic tests cannot replace
that elapsed forward evidence.
