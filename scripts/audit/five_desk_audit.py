"""Audit of the Five-Desk Master Checklist v1.4 (405 tasks) against this repository.

FB-0012 asks for ``{id, status, source, test, artifact_hash, blocker, commit}`` per task with no unsupported
status claims. This script holds the assessed status of each task and *verifies the evidence mechanically*:
every evidence path must exist on a named git ref, and its blob hash is recorded. A path that does not
exist fails the run.

Depth: a module survey (file lists, public interfaces, keyword search) on 2026-11-17. No task was graded by
running its acceptance test, so nothing is marked DONE unless code and a test file both exist for it.

The task ids here are the *five-desk v1.4* ids. ``docs/audits/unified-platform-tracker.json`` uses the same
``FB-nnnn`` pattern for a different, older 711-task list; the two must not be joined on id.

Run:  python scripts/audit/five_desk_audit.py <path-to-checklist.md>
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "audits" / "five-desk-v1_4"
REFS = {
    "main": "main",
    "f00": "feature/forward-edge-f00-f01",
    "codex": "codex/forward-edge-falsification-audit-20261115",
    "dyn": "feature/dynamic-evidence-standard",
    "d1": "feature/five-desk-d1-instruments",
    "d2": "feature/five-desk-d2-data-parity",
}
S = "src/financial_brain/"
I = S + "instruments/"
T = "tests/test_instruments_d1.py"

D, P, F, N = "DONE", "PARTIAL", "FOOTHOLD", "NOT_STARTED"
MEANING = {
    D: "code and a test exist for the task as written",
    P: "part of the task is built and tested; the rest is named in the note",
    F: "a reusable building block exists, but the task's own subject is not built",
    N: "nothing in the repository addresses it",
}

#: tasks that cannot be finished in-house: (kind, what is needed)
EXTERNAL = {
    "FB-0008": ("EXTERNAL", "licence/retention review of NSE, BSE, AMFI and Kite terms"),
    "FB-0009": ("OWNER", "hosted CI cannot start: GitHub billing/spending limit"),
    "FB-0010": ("OWNER", "branch protection is a repository-owner setting"),
    "FB-0205": ("EXTERNAL", "Kite WebSocket entitlement and a running archive task"),
    "FB-0305": ("EXTERNAL", "AMFI history acquisition within its terms"),
    "FB-0495": ("TIME", "needs future paper sessions"),
    "FB-0814": ("OWNER", "market-hours rehearsals are run by the owner (fb live rehearse)"),
    "FB-1213": ("TIME", "needs future independent sessions"),
    "FB-1320": ("OWNER", "hosted CI blocked by billing; market-hours rehearsal is the owner's"),
    "FB-1403": ("EXTERNAL", "genuine ETF/option/AMFI sources"),
    "FB-1404": ("EXTERNAL", "live Kite session and entitlement check (owner logs in daily)"),
    "FB-1405": ("EXTERNAL", "only if live MF subscriptions are ever enabled"),
    "FB-1406": ("EXTERNAL", "broker, compliance and owner approval"),
    "FB-1407": ("TIME", "elapsed observed sessions"),
    "FB-1907": ("TIME", "prospective comparison needs future sessions"),
    "FB-2404": ("EXTERNAL", "option tick/OI entitlement; depth-archive task is unregistered (owner)"),
    "FB-2405": ("EXTERNAL", "provider entitlement and retention limits"),
    "FB-2430": ("OWNER", "2026Q4 trial budget is spent: governance amendment or 2027Q1"),
    "FB-2440": ("TIME", "real-session paper rehearsal"),
    "FB-2535": ("EXTERNAL", "licensed XAUUSD M1 data"),
    "FB-2536": ("OWNER", "trial-budget decision as FB-2430"),
    "FB-2544": ("TIME", "prospective paper sessions"),
}

# id: (status, [evidence paths], note).  Anything not listed is NOT_STARTED with no evidence.
A: dict[str, tuple[str, list[str], str]] = {
    # 0 baseline
    "FB-0001": (P, ["docs/forward-edge/baseline-2026-11-10.json", "docs/audits/protected-baseline.json"],
                "SHAs and test-file counts are in this audit; no hosted CI result exists (billing)"),
    "FB-0002": (P, ["docs/audits/capability-matrix.v2.json", S + "forward_edge/strategy_inventory.py"],
                "strategy inventory exists; no single canonical owner per capability across five desks"),
    "FB-0003": (P, [S + "forward_edge/evidence_labels.py", "tests/test_forward_edge_f00.py"],
                "labels exist on the forward-edge path only; zoo/intraday/options runners label themselves"),
    "FB-0004": (D, ["docs/trials/ledger.jsonl", S + "forward_edge/trials.py", "docs/forward-edge/protected-pins.json"],
                "ledger untouched; it now holds 14 failed 2026Q4 trials (the checklist says 13)"),
    "FB-0005": (F, [S + "forward_edge/charter.py"], "a forward-edge charter exists; no five-desk charter"),
    "FB-0006": (P, [S + "forward_edge/modes.py", "tests/test_forward_edge_f00.py"],
                "constant guards forward_edge only; execution/oms is gated separately by FB_LIVE_ORDERS and stages"),
    "FB-0007": (F, [S + "forward_edge/modes.py", S + "execution/oms/kite_broker.py"],
                "both paths exist and each has its own tests; no hostile test spans both"),
    "FB-0008": (N, ["docs/sources"], ""),
    "FB-0009": (P, [".github/workflows"], "workflows for Windows and Linux exist; no job has started since the billing block"),
    "FB-0010": (N, [], "evidence branch codex/branch-protection-evidence-20261113 is unmerged"),
    "FB-0011": (P, [S + "forward_edge/evidence_labels.py", S + "kernel/contracts.py"],
                "two label sets exist; neither is the five named tiers"),
    "FB-0012": (P, ["scripts/audit/five_desk_audit.py"], "this audit: survey depth, hashes verified, tests not run per task"),
    # 1 instruments and clocks
    "FB-0101": (D, [I + "master.py", T], "InstrumentId with effective span, ISIN, token and underlying; existing runners still key on symbol"),
    "FB-0102": (D, [I + "master.py", T], ""),
    "FB-0103": (D, [I + "units.py", T], ""),
    "FB-0104": (P, [I + "clocks.py", T], "clock is built and tested; no real exchange, scheme or bank holiday list is loaded"),
    "FB-0105": (P, [I + "clocks.py", T], "Stamps type exists; feeds do not carry it yet"),
    "FB-0106": (P, [I + "master.py", I + "adapters.py", T], "versioned terms exist; only listings and MF plan/option are imported, not tick, lot, expiry or ETF index"),
    "FB-0107": (D, [I + "master.py", T], "only LISTED and DELISTED are populated from observation"),
    "FB-0108": (P, [I + "master.py", I + "corporate_actions.py", I + "adapters.py", T], "ISIN changes import only with whole or half ratios; no scheme-merger source yet"),
    "FB-0109": (P, [I + "clocks.py", T], "effective-dated rules with scheme overrides; no real cut-off or publication rule is loaded"),
    "FB-0110": (D, [I + "master.py", I + "store.py", T], "unknown, overlapping and future-published records are refused"),
    # 2 ETF data
    "FB-0204": (F, [S + "providers/bhavcopy.py"], "exchange bhavcopy carries ETF rows; they are not classified or kept as an ETF universe"),
    "FB-0205": (F, [S + "ingest/kite_live.py", S + "ingest/depth_archive.py"], "equity tick/depth plumbing; no ETF subscription"),
    "FB-0206": (F, [S + "kernel/ticks.py", S + "features/bars.py"], ""),
    "FB-0208": (F, [S + "costs/measured.py", S + "execution/exec_study.py"], ""),
    "FB-0212": (F, [S + "corpactions/actions.py"], ""),
    # 3 MF data
    "FB-0304": (F, [S + "ingest/mfnav.py"], "current NAV file only; no revision history"),
    "FB-0318": (P, [S + "providers/amfi.py", S + "ingest/mfnav.py"], "NAVAll.txt is parsed; stale-scheme detection not verified"),
    # 4 signal contract
    "FB-0401": (P, [S + "forward_edge/pipeline.py", S + "forward_edge/signal_trace.py"], "equity forward-edge signals only; no cross-desk Signal type"),
    "FB-0402": (P, [S + "decisions/expected_value.py", S + "decisions/opportunity_card.py"], ""),
    "FB-0404": (F, [S + "decisions/plan.py", S + "execution/ticket.py"], ""),
    "FB-0405": (P, [S + "opportunity/lifecycle.py", S + "strategies/lifecycle.py"], "two lifecycles, neither the checklist's state list"),
    "FB-0406": (F, [S + "signals/watch.py"], ""),
    "FB-0407": (P, [S + "paper/daily.py", S + "paper/board.py"], "daily equity candidates; no ETF ranks"),
    "FB-0409": (P, [S + "llm/gate.py", S + "security/untrusted.py", S + "interfaces/mcp_server.py"], "LLM path is read-only"),
    "FB-0410": (P, [S + "forward_edge/approvals.py", S + "forward_edge/operator_decisions.py", S + "forward_edge/shadow.py"], ""),
    "FB-0411": (P, [S + "research/counterfactual_store.py", S + "evaluation/regret.py"], ""),
    "FB-0412": (F, [S + "forward_edge/completeness.py", S + "forward_edge/data_gate.py"], ""),
    # 4A entry/exit lifecycle
    "FB-0452": (F, [S + "decisions/plan.py", S + "execution/ticket.py"], ""),
    "FB-0457": (F, [S + "decisions/expected_value.py", S + "costs/calculator.py"], ""),
    "FB-0458": (F, [S + "decisions/net_alpha.py"], ""),
    "FB-0459": (F, [S + "kernel/market_rules.py"], "ticket validation; no stop/target inequality checks"),
    "FB-0460": (P, [S + "risk/sizing.py", S + "decisions/expected_value.py"], "stop-distance sizing exists; lots, depth and correlation not combined"),
    "FB-0461": (F, [S + "forward_edge/instrument_rules.py"], ""),
    "FB-0462": (P, [S + "forward_edge/abstention.py"], "abstention on the forward-edge path"),
    "FB-0467": (F, [S + "kernel/fills.py"], ""),
    "FB-0468": (P, [S + "kernel/fills.py", "tests/test_kernel_fills.py"], "resolve_stop_target exists in the kernel; not used by every runner"),
    "FB-0469": (F, [S + "forward_edge/feed_health.py", S + "forward_edge/market_data.py"], ""),
    "FB-0471": (F, [S + "forward_edge/order_journal.py", S + "paper/ledger.py"], "order journal, not the eight-event position ledger"),
    "FB-0473": (F, [S + "paper/daily.py", S + "evaluation/swing.py"], "candidates without SL/TP"),
    "FB-0474": (P, [S + "paper/intraday_runner.py", S + "paper/intraday.py"], "live-v1: long-only ORB with stop and EOD flatten; no shorts, no linked exit events"),
    "FB-0476": (F, [S + "paper/options_runner.py"], ""),
    "FB-0477": (F, [S + "paper/options_forward.py", S + "ingest/options_quotes.py"], ""),
    "FB-0478": (F, [S + "paper/options_spread.py", S + "forward_edge/options_spread.py"], ""),
    "FB-0479": (F, [S + "forward_edge/options_risk.py"], ""),
    "FB-0485": (F, [S + "forward_edge/operator_decisions.py"], ""),
    "FB-0486": (F, [S + "alerts.py", S + "forward_edge/alerts.py"], ""),
    "FB-0487": (F, [S + "paper/live_view.py"], ""),
    "FB-0491": (F, ["tests/test_kernel_fills.py"], "kernel fill tests only"),
    "FB-0494": (F, [S + "evaluation/exit_rules.py"], "historical exit-rule study; not by live exit reason"),
    # 5 ETF alpha
    "FB-0503": (F, ["scripts/research/hundred.py"], "index-TRI rotation candidates were tested on 2026-11-17 and failed; not ETF prices"),
    "FB-0510": (F, [S + "evaluation/champion.py"], ""),
    # 6 MF research
    "FB-0608": (F, [S + "kernel/investor_tools.py"], "dca_simulate only"),
    # 8 exchange paper OMS
    "FB-0801": (P, [S + "forward_edge/pipeline.py", S + "forward_edge/session_runner.py", S + "forward_edge/paper_book_adapter.py"],
                "one path exists for forward-edge equities; zoo, intraday and options runners keep separate ledgers"),
    "FB-0802": (P, [S + "kernel/units.py", S + "paper/options_runner.py"], ""),
    "FB-0803": (P, [S + "execution/simulator.py", S + "forward_edge/marketable_limit.py", S + "forward_edge/passive_queue.py"], ""),
    "FB-0804": (P, [S + "kernel/oms.py", S + "execution/oms/model.py"], ""),
    "FB-0805": (P, [S + "costs/india.py", S + "costs/calculator.py", S + "kernel/fills.py"], "depository charge per sale is not in the proportional model"),
    "FB-0806": (F, [S + "forward_edge/claim_tier.py", S + "forward_edge/evidence_labels.py"], "no per-fill fidelity tag"),
    "FB-0807": (F, [S + "ingest/kite_fno.py", S + "ingest/options_quotes.py"], ""),
    "FB-0808": (P, [S + "paper/options.py", S + "paper/options_spread.py", S + "forward_edge/options_risk.py"], ""),
    "FB-0809": (F, [S + "kernel/options_paper.py"], "codex branch only"),
    "FB-0810": (P, [S + "forward_edge/durable.py", S + "execution/oms/journal.py", S + "execution/oms/reconcile.py"], ""),
    "FB-0811": (P, [S + "forward_edge/outage_gate.py", S + "forward_edge/intent_timing.py"], ""),
    "FB-0812": (F, [S + "forward_edge/books.py"], ""),
    "FB-0813": (P, [S + "execution/oms/risk_gate.py", S + "kernel/oms.py"], ""),
    "FB-0814": (P, [S + "forward_edge/rehearsal.py"], "offline rehearsal exists; market-hours rehearsals not yet run"),
    # 9 accounting
    "FB-0901": (P, [S + "kernel/ledger.py", S + "kernel/paper_account.py", S + "forward_edge/paper_accounting.py"], "double entry for exchange cash and positions; no MF or option legs"),
    "FB-0902": (F, [S + "forward_edge/books.py"], ""),
    "FB-0903": (F, [S + "kernel/oms.py"], "reserved vs available only"),
    "FB-0904": (P, [S + "kernel/paper_account.py"], "open question: whether close_adj includes cash dividends"),
    "FB-0905": (F, [S + "kernel/options_paper.py"], "codex branch only"),
    "FB-0907": (F, [S + "evaluation/performance.py"], "no money-weighted return"),
    "FB-0908": (F, [S + "costs/tax.py"], "equity classes only"),
    "FB-0909": (F, [S + "costs/tax.py"], ""),
    "FB-0912": (P, [S + "attribution/pnl.py", S + "forward_edge/strategy_attribution.py", S + "forward_edge/cluster_attribution.py"], ""),
    "FB-0913": (P, [S + "risk/covariance.py", S + "risk/decompose.py", S + "risk/stress.py", S + "portfolio/stress.py"], ""),
    "FB-0914": (F, [S + "graph/exposure.py", S + "risk/exposure.py"], "no ETF/MF/option look-through"),
    "FB-0915": (P, [S + "risk/limits.py", S + "forward_edge/exposure_limits.py", S + "forward_edge/risk_throttle.py"], ""),
    "FB-0917": (P, [S + "paper/reconcile.py", S + "forward_edge/ledger_reconcile.py"], ""),
    "FB-0918": (F, [S + "forward_edge/pnl_dashboard.py"], ""),
    # 10 scheduling
    "FB-1001": (P, [S + "kernel/preflight.py", S + "forward_edge/session_ops.py"], "preflight task is not registered (owner)"),
    "FB-1002": (P, [S + "paper/intraday_runner.py"], "equities, 20-second polling; no ETFs"),
    "FB-1003": (F, [S + "ingest/options_quotes.py", S + "ingest/depth_archive.py"], "depth-archive task is not registered (owner)"),
    "FB-1004": (P, [S + "paper/intraday_runner.py"], "ORB stop and EOD flatten only"),
    "FB-1005": (P, [S + "paper/daily.py", S + "paper/zoo.py"], "no ETF ranks"),
    "FB-1006": (F, [S + "ingest/mfnav.py"], ""),
    "FB-1008": (P, [S + "paper/daily_results.py", S + "paper/reconcile.py", S + "forward_edge/session_close.py"], ""),
    "FB-1010": (P, [S + "risk/killswitch.py", S + "forward_edge/outage_gate.py", S + "forward_edge/data_gate.py"], ""),
    "FB-1011": (P, [S + "ingest/job.py", S + "paper/run_manifest.py"], ""),
    "FB-1012": (F, [S + "execution/oms/shortfall.py", S + "forward_edge/session_timeline.py"], ""),
    # 11 dashboard
    "FB-1101": (P, [S + "api/app.py", S + "paper/terminal.py", S + "forward_edge/pnl_dashboard.py"], ""),
    "FB-1102": (F, [S + "paper/workstation.py"], "no desk pages"),
    "FB-1103": (F, [S + "paper/trader_view.py"], ""),
    "FB-1104": (P, [S + "forward_edge/ui_actions.py", S + "forward_edge/approvals.py"], ""),
    "FB-1105": (F, [S + "forward_edge/shadow.py"], ""),
    "FB-1109": (F, [S + "forward_edge/signal_trace.py"], ""),
    "FB-1110": (F, [S + "alerts.py"], ""),
    "FB-1111": (P, [S + "evidence/ledger.py", S + "evidence/anchor.py", S + "forward_edge/evidence_bundle.py"], ""),
    "FB-1112": (P, [S + "brief/render.py", S + "paper/daily_results.py"], "no per-desk benchmark comparison"),
    # 12 evaluation
    "FB-1201": (P, [S + "research/preregistry.py", S + "evaluation/standard.py", S + "evaluation/dynamic_standard.py"], "equity research only; dynamic standard is on PR 34"),
    "FB-1202": (P, [S + "evaluation/firewall.py", S + "forward_edge/parameter_freeze.py", S + "forward_edge/horizon_freeze.py"], ""),
    "FB-1203": (P, [S + "evaluation/benchmark.py", S + "ingest/index_tri.py"], "stocks only"),
    "FB-1207": (P, [S + "evaluation/performance.py", S + "evaluation/scorecard.py", S + "forward_edge/risk_metrics.py"], ""),
    "FB-1208": (F, [S + "forward_edge/portfolio_alpha.py"], ""),
    "FB-1209": (P, [S + "forward_edge/cost_sensitivity.py", S + "evaluation/implementability.py"], ""),
    "FB-1210": (P, [S + "evaluation/dynamic_standard.py", S + "forward_edge/familywise.py", S + "forward_edge/cohort_blocks.py"], ""),
    "FB-1211": (P, [S + "forward_edge/completeness.py", S + "paper/execution_audit.py"], ""),
    "FB-1212": (F, [S + "forward_edge/operator_decisions.py"], ""),
    "FB-1213": (P, [S + "evaluation/oos_monitor.py", S + "forward_edge/evidence_stats.py"], "rule exists; the sessions do not yet"),
    "FB-1214": (P, ["docs/trials/ledger.jsonl", "docs/trials/dynamic/hundred/RESULTS.md"], "failures are published; no desk-level loss view"),
    "FB-1215": (P, [S + "commands/trials.py", S + "forward_edge/trials.py"], ""),
    "FB-1216": (P, [S + "risk/allocator.py", S + "forward_edge/capital_allocation.py"], "sleeve allocator can set zero; not desk-level"),
    # 13 tests
    "FB-1302": (F, [S + "paper/daily.py"], ""),
    "FB-1303": (F, [S + "paper/intraday_runner.py"], ""),
    "FB-1304": (F, [S + "paper/options_runner.py"], ""),
    "FB-1316": (F, [S + "forward_edge/durable.py"], "exchange path only"),
    "FB-1317": (P, [S + "forward_edge/durable.py", S + "execution/oms/journal.py"], ""),
    "FB-1318": (P, ["tests/test_forward_edge_f00.py", S + "execution/oms/kite_broker.py"], "each path tested alone"),
    "FB-1319": (F, [S + "forward_edge/disaster_stress.py"], ""),
    "FB-1320": (P, [".github/workflows", "docs/audits/mutation-report.json"], "mutation report covers the patterns module only"),
    # 14 deployment
    "FB-1401": (P, [S + "forward_edge/durable.py"], "single process with durable state"),
    "FB-1402": (P, [S + "forward_edge/fixtures.py"], "equity fixtures only"),
    "FB-1406": (P, [S + "execution/oms/engine.py", S + "execution/oms/killswitch.py", S + "execution/oms/rollout.py"], "OMS built and locked; arming is the owner's"),
    "FB-1407": (P, [S + "paper/intraday_runner.py", S + "paper/zoo.py"], "paper record accrues daily since 2026-10"),
    "FB-1408": (P, [S + "forward_edge/backup_restore.py"], ""),
    "FB-1409": (F, ["docs/sources", S + "providers/robots.py"], ""),
    "FB-1410": (F, ["docs/audits/capability-matrix.v2.json", S + "paper/capabilities.py"], "not per desk"),
    # 16 expected value
    "FB-1601": (F, [S + "decisions/expected_value.py", S + "decisions/net_alpha.py", S + "decisions/forecast_ev.py"], "pieces exist; no single net_ev with fidelity tier"),
    "FB-1602": (F, [S + "decisions/forecast_ev.py"], ""),
    "FB-1603": (P, [S + "decisions/net_alpha.py", S + "evaluation/money_gate.py"], ""),
    "FB-1604": (P, [S + "forward_edge/abstention.py", S + "decisions/safety.py", S + "decisions/risk_filters.py"], ""),
    "FB-1605": (F, [S + "forward_edge/intent_timing.py", S + "forward_edge/marketable_limit.py"], ""),
    "FB-1606": (F, [S + "costs/calculator.py", S + "decisions/opportunity_card.py"], ""),
    "FB-1607": (P, [S + "forecasting/calibration.py"], ""),
    "FB-1608": (F, [S + "decisions/net_alpha.py"], ""),
    # 17 capital allocation
    "FB-1701": (F, [S + "forward_edge/capital_allocation.py", S + "forward_edge/risk_allocator.py"], ""),
    "FB-1702": (F, [S + "risk/allocator.py", S + "portfolio/next_capital.py", S + "portfolio/optimizer.py", S + "portfolio/gate.py"], "four modules, no single interface"),
    "FB-1703": (F, [S + "graph/exposure.py", S + "risk/exposure.py"], ""),
    "FB-1705": (P, [S + "risk/limits.py", S + "forward_edge/exposure_limits.py"], ""),
    "FB-1706": (P, [S + "risk/allocator.py", S + "forward_edge/capacity.py"], ""),
    "FB-1707": (P, [S + "risk/killswitch.py", S + "forward_edge/risk_throttle.py", S + "forward_edge/persistent_killswitch.py"], ""),
    "FB-1708": (F, [S + "kernel/oms.py"], ""),
    # 18 latency and fidelity
    "FB-1801": (F, [S + "forward_edge/session_timeline.py", S + "forward_edge/intent_timing.py"], ""),
    "FB-1802": (F, [S + "ingest/kite_live.py", S + "paper/intraday_runner.py"], "runner polls every 20 s"),
    "FB-1803": (P, [S + "forward_edge/feed_health.py", S + "forward_edge/outage_gate.py"], ""),
    "FB-1804": (P, [S + "execution/simulator.py", S + "forward_edge/passive_queue.py", S + "execution/oms/slicer.py"], ""),
    "FB-1805": (P, [S + "execution/oms/shortfall.py", S + "execution/exec_study.py", S + "costs/measured.py"], ""),
    "FB-1806": (P, [S + "kernel/fills.py"], "kernel rule exists; not enforced in every runner"),
    "FB-1808": (F, [S + "forward_edge/capture_replay.py", S + "opportunity/replay.py"], "capture_replay is on the codex branch"),
    # 19 exits
    "FB-1901": (F, [S + "evaluation/exit_rules.py", S + "strategies/execution_rules.py"], ""),
    "FB-1903": (F, [S + "risk/killswitch.py"], ""),
    "FB-1905": (F, [S + "forward_edge/disaster_stress.py", S + "forward_edge/market_events.py"], ""),
    # 20 options/ETF/MF
    "FB-2001": (F, [S + "kernel/options.py", S + "evaluation/options_real.py"], ""),
    "FB-2002": (F, [S + "kernel/options.py"], "implied vol per contract; no surface"),
    "FB-2003": (F, [S + "evaluation/options_vertical.py", S + "forward_edge/options_risk.py"], "defined-risk premium selling lost on 2016-21 training (2026-11-16)"),
    # 21 attribution
    "FB-2101": (P, [S + "paper/daily_results.py", S + "forward_edge/pnl_dashboard.py", S + "forward_edge/strategy_scorecard.py"], ""),
    "FB-2102": (P, [S + "attribution/pnl.py", S + "attribution/move.py", S + "forward_edge/strategy_attribution.py"], ""),
    "FB-2103": (F, [S + "evaluation/performance.py"], ""),
    "FB-2104": (P, [S + "evaluation/performance.py", S + "forward_edge/strategy_scorecard.py"], ""),
    "FB-2105": (P, [S + "attribution/move.py", S + "paper/loss_review.py"], ""),
    "FB-2106": (F, [S + "forward_edge/shadow.py"], ""),
    "FB-2107": (P, [S + "research/counterfactual_store.py", S + "evaluation/regret.py"], ""),
    "FB-2108": (F, [S + "forward_edge/ledger_reconcile.py"], ""),
    # 22 discovery and promotion
    "FB-2201": (P, [S + "evaluation/champion.py", S + "research/preregistry.py", S + "forward_edge/champion_governance.py"], ""),
    "FB-2202": (P, [S + "evaluation/registry.py", S + "forward_edge/registry.py"], "two registries"),
    "FB-2203": (P, [S + "evaluation/benchmark.py", S + "forward_edge/paired_controls.py", S + "forward_edge/outperformance.py"], ""),
    "FB-2204": (P, [S + "evaluation/dynamic_standard.py", S + "forward_edge/familywise.py", S + "forward_edge/bootstrap.py"], ""),
    "FB-2205": (F, [S + "forecasting/calibration.py", S + "regime/brain.py"], ""),
    "FB-2206": (F, [S + "forward_edge/parameter_freeze.py"], ""),
    "FB-2207": (P, [S + "risk/degradation.py", S + "evaluation/oos_monitor.py", S + "forward_edge/harm_stop.py"], ""),
    "FB-2208": (P, [S + "forward_edge/evidence_bundle.py", S + "evidence/anchor.py"], ""),
    # 23 control room
    "FB-2301": (F, [S + "paper/board.py", S + "forward_edge/cockpit.py"], ""),
    "FB-2302": (F, [S + "alerts.py"], ""),
    "FB-2303": (F, [S + "forward_edge/approvals.py"], ""),
    "FB-2304": (P, [S + "paper/daily_results.py", S + "paper/loss_review.py"], ""),
    "FB-2305": (P, [S + "risk/health.py", S + "forward_edge/feed_health.py"], ""),
    "FB-2306": (P, [S + "forward_edge/durable.py"], "exchange path only"),
    "FB-2307": (P, ["docs/RUNBOOK-LIVE.md", "docs/forward-edge/RUNBOOKS.md", "docs/forward-edge/OPERATOR.md"], ""),
    "FB-2308": (P, [S + "forward_edge/session_close.py"], ""),
    # 24 15-minute index programme
    "FB-2401": (F, [S + "indices/lineage.py", S + "ingest/index_tri.py"], "daily index series; no effective-dated index master"),
    "FB-2403": (F, [S + "ingest/kite_minute.py", S + "evaluation/index_rules.py"], "minute index data is used by the failed VWAP family; no 15-minute bar builder"),
    "FB-2404": (F, [S + "ingest/options_quotes.py", S + "ingest/depth_archive.py"], ""),
    "FB-2405": (F, [S + "forward_edge/venue_calendar.py"], ""),
    "FB-2408": (F, [S + "features/intraday.py"], "equity session features"),
    "FB-2414": (F, [S + "forecasting/calibration.py"], ""),
    "FB-2415": (F, [S + "forecasting/ensemble.py", S + "decisions/forecast_ev.py"], ""),
    "FB-2417": (F, [S + "kernel/options.py", S + "decisions/forecast_ev.py"], ""),
    "FB-2422": (F, [S + "paper/options_runner.py"], ""),
    "FB-2426": (F, [S + "forecasting/walkforward.py"], ""),
    "FB-2428": (F, [S + "forecasting/null.py", S + "forward_edge/intraday_nulls.py"], ""),
    "FB-2429": (F, [S + "forward_edge/cohort_blocks.py"], ""),
    "FB-2430": (P, ["docs/trials/ledger.jsonl"], "failed index and minute-bar verdicts are preserved; no new registration made"),
    # 25 four-node
    "FB-2512": (F, [S + "patterns/pivots.py"], "confirmed-pivot detector to reuse"),
    "FB-2514": (F, [S + "patterns/streaming.py"], ""),
    "FB-2533": (F, [S + "patterns/pivots.py", S + "patterns/streaming.py", S + "kernel/indicators.py", S + "ingest/kite_minute.py"], "modules exist; no interface audit written"),
}


# Updates from the D2+ branch (feature/five-desk-d2-data-parity). Modules are tested in isolation;
# none is wired into the existing runners yet.
_PIT, _TPIT = I + "pit.py", "tests/test_pit_d2.py"
_SIG, _TSIG = S + "signals/contracts.py", "tests/test_signals_d3.py"
_EX, _TEX = S + "signals/exit_triggers.py", "tests/test_exit_triggers_4a.py"
_MF, _TMF = S + "execution/mf_orders.py", "tests/test_mf_orders_s7.py"
_LG, _TLG = S + "portfolio/multi_asset_ledger.py", "tests/test_multi_asset_ledger_s9.py"
_OMS, _TOMS = S + "execution/exchange_paper.py", "tests/test_exchange_paper_s8.py"
_EV, _AL = S + "signals/admissibility.py", S + "risk/capital_allocator.py"
_TEV = "tests/test_admissibility_allocator_s16_17.py"
_SC, _TSC = S + "evaluation/scorecards.py", "tests/test_scorecards_s21_22.py"
A.update({
    "FB-0207": (D, [_PIT, _TPIT], "iNAV premium refused when stale or asynchronous"),
    "FB-0210": (P, [_PIT, _TPIT], "stale NAV and large-move flags; international closure calendar not loaded"),
    "FB-0214": (D, [_PIT, _TPIT], "missing quote is UNPRICED; close/NAV never substituted"),
    "FB-0308": (D, [_PIT, _TPIT], "NAV usable only after its publication instant"),
    "FB-0318": (P, [_PIT, _TPIT, S + "providers/amfi.py"], "row-date flags; not yet run inside mfnav ingest"),
    "FB-0401": (D, [_SIG, _TSIG], "immutable cross-desk Signal; runners not yet emitting it"),
    "FB-0403": (D, [_SIG, _TSIG], "Intent enum with desk/intent compatibility"),
    "FB-0404": (P, [_SIG, _TSIG], "entry, stop, target, tick; holding window not a field"),
    "FB-0405": (D, [_SIG, _TSIG], "lifecycle state machine refuses illegal steps"),
    "FB-0412": (P, [_SIG, _PIT], "missing data explicit (UNPRICED, NO_TRADE reason)"),
    "FB-0451": (P, [_SIG, _EX], "Signal plus position-linked exit records; no separate ExitSignal type"),
    "FB-0452": (D, [_SIG, _TSIG], "exchange trades refused without side, entry, size, SL, TP, policy"),
    "FB-0454": (D, [_SIG, _TSIG], "ordered ExitTrigger set in ExitPolicy"),
    "FB-0457": (D, [_SIG, _TSIG], "planned loss, gross and net reward:risk"),
    "FB-0458": (D, [_SIG, _TSIG], "per-strategy net reward:risk gate"),
    "FB-0459": (D, [_SIG, _TSIG], "long/short ordering and tick checks; versioned exception for no stop"),
    "FB-0461": (P, [_SIG, _TSIG], "tick check and no overnight shorts; no automatic re-rounding"),
    "FB-0462": (D, [_SIG, _EV, _TSIG], "NO_TRADE with reason; WATCH/CASH decisions"),
    "FB-0463": (D, [_EX, _TEX], "planned exit separate from emitted trigger and fill events"),
    "FB-0464": (P, [_EX, _TEX], "no duplicate exits, idempotent replay; partial-fill quantity adjust not built"),
    "FB-0465": (D, [_EX, _TEX], "first of stop/target closes and cancels the sibling"),
    "FB-0467": (D, [_EX, _TEX], "gap fills at open, extra loss kept"),
    "FB-0468": (D, [_EX, _TEX], "both touched in one bar: stop first"),
    "FB-0470": (D, [_EX, _TEX], "unresolved exit intent during halt; no fictional fill"),
    "FB-0472": (P, [_SIG, _EX], "entry refused without policy; closed only on recorded fill"),
    "FB-0481": (P, [_SIG, _MF], "MF signals cannot carry exchange stops; NAV threshold trigger not built"),
    "FB-0491": (P, [_TEX], "SL-only, TP-only, both-in-bar, gaps; trailing and partial paths missing"),
    "FB-0701": (P, [_MF, _TMF], "Subscription order with immutable request time; folio not modelled"),
    "FB-0705": (D, [_MF, _TMF], "eligible NAV date from acceptance, funds and cut-off; other days refused"),
    "FB-0706": (D, [_MF, _TMF], "NAV_PENDING until published; truncated fractional allotment"),
    "FB-0707": (P, [_MF, _TMF], "FIFO redemption; deferred settlement via cash states"),
    "FB-0708": (P, [_MF, _TMF], "per-lot exit load by holding age; stamp duty not modelled"),
    "FB-0709": (D, [_MF, _TMF], "redemption cash states gate redeployment"),
    "FB-0802": (D, [_OMS, _TOMS], "whole units and whole lots enforced"),
    "FB-0803": (D, [_OMS, _TOMS], "fills at observed bid/ask with participation cap"),
    "FB-0804": (P, [_OMS, _TOMS], "order statuses; TIMED_OUT/UNKNOWN paths untested"),
    "FB-0806": (D, [_OMS, _TOMS], "fidelity tag on every fill"),
    "FB-0808": (D, [_OMS, _TOMS], "option structure allow-list blocks naked writing"),
    "FB-0811": (D, [_OMS, _TOMS], "expired signal never fills; stale feed halts the desk"),
    "FB-0812": (D, [_OMS, _TOMS], "one API, separate autonomous and approved accounts"),
    "FB-0901": (P, [_LG, _TLG], "double-entry multi-desk journal; kernel/paper_account not migrated"),
    "FB-0903": (D, [_LG, _TLG], "AVAILABLE/RESERVED/UNSETTLED/MF_PENDING cash"),
    "FB-0911": (P, [_LG, _TLG], "unsettled proceeds not spendable; settlement calendar rules not loaded"),
    "FB-0914": (D, [_LG, _TLG], "exposure summed by underlying across wrappers"),
    "FB-0916": (D, [_LG, _TLG], "stale/missing marks reported UNPRICED, NAV withheld"),
    "FB-0917": (P, [_LG, _TLG], "journal balance check; daily scheduled reconciliation not wired"),
    "FB-0918": (D, [_LG, _TLG], "desk NAVs sum to consolidated NAV"),
    "FB-1601": (P, [_EV, _TEV], "net_ev with validation; no confidence interval"),
    "FB-1603": (D, [_EV, _TEV], "hurdle, budget and session gates"),
    "FB-1604": (D, [_EV, _TEV], "reason-coded TRADE/WATCH/REJECT/CASH"),
    "FB-1606": (P, [_EV, _TEV], "receipt with EV, risk at stop, break-even, cost version"),
    "FB-1608": (D, [_TEV], "gross edge eaten by costs skipped; missing costs/quotes never trade"),
    "FB-1701": (P, [_AL, _TEV], "central reservations; desks not yet calling it"),
    "FB-1705": (P, [_AL, _TEV], "desk, underlying, capital and daily-loss limits"),
    "FB-1707": (D, [_AL, _TEV], "daily-loss kill switch with human reset; quarantine"),
    "FB-1708": (D, [_AL, _TEV], "atomic idempotent reservations; concurrent burst test"),
    "FB-1806": (D, [_EX, _TEX], "adverse-first intrabar ordering"),
    "FB-1902": (D, [_EX, _TEX], "OCO paper semantics, no double exit"),
    "FB-2103": (D, [_SC, _TSC], "TWR excludes flows; XIRR separate"),
    "FB-2104": (P, [_SC, _TSC], "trade stats; drawdown and benchmark excess not computed here"),
    "FB-2108": (D, [_SC, _TSC], "desk P&L must equal consolidated"),
    "FB-2204": (P, [_SC, _TSC], "session-block bootstrap; multiple-testing adjustment elsewhere"),
    "FB-2207": (P, [_SC, _TSC], "frozen-rule FAILING on drawdown or non-positive lower bound"),
})
_DC, _TDC = S + "paper/day_cycle.py", "tests/test_day_cycle_s10.py"
_ADV = "tests/test_adversarial_s13.py"
_IX, _TIX = S + "forecasting/index_15m.py", "tests/test_forecasting_index_15m.py"
_IO, _TIO = S + "signals/index_options.py", "tests/test_index_options_s24d.py"
A.update({
    "FB-1008": (P, [_DC, _TDC], "daily reconcile phase slot; handler not wired"),
    "FB-1010": (D, [_DC, _TDC], "fail-closed halts trading phases for the day"),
    "FB-1011": (D, [_DC, _TDC], "run statuses, once per (day, phase), retry after failure"),
    "FB-1301": (P, [_ADV], "1200 randomised ledger events; timezones/calendars covered elsewhere"),
    "FB-1302": (D, [_ADV], "no TRADE when net does not cover costs"),
    "FB-1303": (P, [_ADV], "delayed quote never fills; polling-vs-stream comparison not built"),
    "FB-1305": (D, [_ADV], "stale iNAV gives no premium; wide spread blocks fill"),
    "FB-1306": (P, [_ADV], "same-underlying ETF and fund summed; currency/commodity not covered"),
    "FB-1309": (P, [_ADV], "cut-off, late funds, weekend, holiday; liquid/overnight rules not loaded"),
    "FB-1310": (D, [_ADV], "NAV published after decision cannot allot"),
    "FB-1311": (D, [_ADV], "insufficient or pending cash rejected"),
    "FB-1312": (P, [_ADV], "partial redemption, lot loads, fractional units; minimums/lock-in missing"),
    "FB-1316": (P, [_ADV], "exit replay after restart idempotent; other crash points not covered"),
    "FB-1317": (D, [_ADV], "duplicate keys post once"),
    "FB-1319": (P, [_ADV], "25% gap stress; option and MF gating stresses not covered"),
    "FB-2403": (P, [_IX, _TIX], "15m bars from 1m without volume; live tick ingest not built"),
    "FB-2405": (P, [_IX, _TIX], "partial bars never presented as full; holiday/auction calendars external"),
    "FB-2406": (D, [_IX, _TIX], "forward return from completed bar, None across gaps"),
    "FB-2407": (D, [_IX, _TIX], "UP/DOWN/FLAT from training-only noise threshold"),
    "FB-2408": (P, [_IX, _TIX], "return, range, volatility, time and opening-range features"),
    "FB-2410": (D, [_IX, _TIX], "mutation leak check on bars after cutoff"),
    "FB-2411": (P, [_IX, _TIX], "class-frequency baseline; persistence/vol baselines missing"),
    "FB-2413": (P, [_IX, _TIX], "probability validation and Brier; no prediction interval"),
    "FB-2416": (D, [_IO, _TIO], "listed, unexpired, fresh, two-sided, deep contracts only"),
    "FB-2417": (P, [_IO, _TIO], "delta-gamma-theta-vega expectation; no full path simulation"),
    "FB-2418": (P, [_IO, _TIO], "ranked by expected net per lot; no tail/portfolio impact"),
    "FB-2425": (P, [_IO, _TIO], "state enum; RESEARCH_ONLY/NO_TRADE/UNPRICED/PAPER_ELIGIBLE produced"),
    "FB-2426": (D, [_IX, _TIX], "chronological purged and embargoed folds"),
    "FB-2436": (P, [_TIX], "bar close, as-of, labels, partial bars; holidays external"),
    "FB-2437": (P, [_TIX, _TIO], "probabilities sum to 1; unlisted/expired/zero-depth refused"),
    "FB-2438": (D, [_TIO], "bullish forecast with costly option gives NO_TRADE"),
})
_ER, _MM, _TEM = S + "strategies/etf_rank.py", S + "research/mf_metrics.py", "tests/test_etf_mf_s5_s6.py"
_CR, _TCR = S + "paper/control_room.py", "tests/test_control_room_s11_23.py"
_XA, _TXA = S + "research/cross_asset.py", "tests/test_cross_asset_s20.py"
_RB = "docs/runbooks/five-desk-operator.md"
A.update({
    "FB-0501": (P, [_ER, _TEM], "group-only momentum ranking; no exposure controls or live study"),
    "FB-0507": (P, [_ER, _TEM], "liquidity tier at intended size; creation-unit effects not modelled"),
    "FB-0508": (D, [_ER, _TEM], "unlisted products cannot be ranked"),
    "FB-0510": (P, [_ER, _TEM], "ranking needs confidence gate; champion/challenger store not linked"),
    "FB-0512": (D, [_ER, _TEM], "ETF_SHADOW_ONLY unless cost and confidence pass"),
    "FB-0601": (P, [_MM, _TEM], "rolling CAGR, vol, drawdown, downside; peer category not loaded"),
    "FB-0602": (P, [_MM, _TEM], "excess vs TRI; benchmark data external"),
    "FB-0603": (D, [_MM, _TEM], "direct vs regular on own NAVs; no TER double count"),
    "FB-0610": (D, [_MM, _TEM], "MF action vocabulary with category horizon"),
    "FB-0612": (D, [_MM, _TEM], "category minimum holding periods; no short-horizon MF trades"),
    "FB-1103": (P, [_CR, _TCR], "alerts carry time, rule version, evidence tier; no UI page"),
    "FB-1110": (P, [_CR, _TCR], "alert kinds include exits and MF pending; delivery channel not built"),
    "FB-1112": (P, [_CR, _TCR], "deterministic desk report; benchmark comparison not included"),
    "FB-2003": (D, [_XA, _TXA], "vertical scenario grid with spread shock and unfilled-leg contingency"),
    "FB-2005": (P, [_XA, _TXA], "best implementable net wrapper; tax/account differences not modelled"),
    "FB-2301": (P, [_CR, _TCR], "searchable prioritised Today list; no screen"),
    "FB-2302": (D, [_CR, _TCR], "two alert levels with deadline, version and tier"),
    "FB-2303": (P, [_CR, _TCR], "approval expires with TTL; adjust within guardrails not built"),
    "FB-2304": (P, [_CR, _TCR], "post-market report text from ledger inputs"),
    "FB-2306": (P, [_CR, _TCR, _TEX, _TLG], "idempotent alerts, exits and ledger posts"),
    "FB-2307": (D, [_RB], "operator runbook"),
    "FB-2308": (D, [_CR, _TCR], "SESSION_COMPLETE gate"),
})
_MP, _TMP = S + "execution/mf_plans.py", "tests/test_mf_plans.py"
_MS, _TMS = I + "mf_scheme.py", "tests/test_mf_scheme.py"
_XP, _TXP = S + "signals/exit_plans.py", "tests/test_exit_plans.py"
_ET, _TET = I + "etf_reference.py", "tests/test_etf_reference.py"
A.update({
    "FB-0201": (P, [_ET, _TET], "reference loader from owner-provided disclosures; no fetcher"),
    "FB-0202": (D, [_ET, _TET], "exposure classes incl. debt, gold, silver, international, factor"),
    "FB-0203": (P, [_ET, _TET], "benchmark (TRI flag), fund house, listing; iNAV feed external"),
    "FB-0209": (P, [_ET, _TET], "dated TER, tracking difference and error fields; ingest external"),
    "FB-0211": (D, [_ET, _TET], "creation/redemption is context only; no retail redemption path"),
    "FB-0213": (D, [_ET, _TET], "price, NAV and benchmark TR series are distinct types"),
    "FB-0505": (D, [_ET, _TET], "premium anomaly needs fresh synchronous iNAV inside window; observation only"),
    "FB-2004": (D, [_ET, _TET], "executable side price vs fresh iNAV"),
    "FB-0301": (D, [_MS, _TMS], "scheme master keyed by code, plan, option, ISIN with dated versions"),
    "FB-0302": (D, [_MS, _TMS], "plan, option, structure, asset type, passive flags"),
    "FB-0303": (P, [_MS, _TMS], "fund house, riskometer, benchmark TRI; manager/objective not stored"),
    "FB-0307": (D, [_MS, _TMS], "merged/wound-up schemes stay in historical universe"),
    "FB-0309": (P, [_MS, _TMS], "dated TER, AUM and exit-load schedules; minimums in mf_plans"),
    "FB-0314": (D, [_MS, _TMS], "exit load per purchase lot from schedule in force at purchase"),
    "FB-0315": (P, [_MP, _TMP], "ELSS lock-in and gating; segregated portfolios not modelled"),
    "FB-0316": (P, [_MP, _TMP], "IDCW payout/reinvest preserve value; merger handled in D1 successions"),
    "FB-0317": (D, [_MM, _TEM], "NAV return without TER deduction"),
    "FB-0480": (P, [_MP, _TMP], "NAV policy target/drawdown actions"),
    "FB-0482": (D, [_MP, _TMP], "NAV threshold produces dated NAV_PENDING request signal"),
    "FB-0483": (P, [_MP, _MF, _TMP], "lots, loads, SIP/STP/SWP, lock-ins, fractional units; tax-aware not built"),
    "FB-0702": (D, [_MP, _TMP], "submitted/acknowledged/accepted/rejected states"),
    "FB-0703": (D, [_MP, _TMP], "payment initiated/credited/available kept separate"),
    "FB-0710": (D, [_MP, _TMP], "switch-in waits for switch-out settlement"),
    "FB-0711": (D, [_MP, _TMP], "SIP installments independent; missed stays missed"),
    "FB-0712": (D, [_MP, _TMP], "SWP and STP legs"),
    "FB-0713": (P, [_MP, _TMP], "minimums, lock-in, gating; folio restrictions not modelled"),
    "FB-0714": (D, [_MP, _TMP], "mark at last published NAV with stale flag"),
    "FB-0715": (P, [_MP, _TMP], "IDCW payout/reinvest amounts; not yet posted to journal"),
    "FB-0717": (D, [_MP, _TMP], "ASSUMED vs OBSERVED payment evidence"),
    "FB-0718": (D, [_MP, _TMP], "MF paper realism score"),
    "FB-0906": (P, [_MP, _TMP], "units x last published NAV; distribution income not in journal"),
    "FB-0453": (D, [_XP, _TXP], "derived closing sides; spread closes every leg"),
    "FB-0455": (D, [_XP, _TXP], "price basis on every level; cross-basis comparison refused"),
    "FB-0456": (P, [_XP, _TXP], "typed levels; TTL and slippage fields live on Signal/OMS"),
    "FB-0466": (D, [_XP, _TXP], "versioned ladder, breakeven move, trailing stop"),
    "FB-0488": (D, [_XP, _TXP], "paired lifecycle report with revisions and R-multiple"),
    "FB-1904": (D, [_XP, _TXP], "option exits only on exact active tokens"),
    "FB-1906": (P, [_XP, _TXP], "partial exits never oversell; cancellation races not simulated"),
    "FB-1908": (D, [_XP, _TXP], "independent linked ExitSignal with trigger, fill, fees, P&L"),
    "FB-1308": (D, [_TMS], "direct and regular are distinct keys with own versions"),
    "FB-1313": (P, [_TMP], "IDCW payout/reinvest; winding-up via universe; delayed redemption NAV via mf_orders"),
    "FB-1314": (D, [_TEM], "NAV return not reduced by TER"),
})
_FN = S + "strategies/four_node/"
_FS, _FI, _FNO, _FR = _FN + "spec.py", _FN + "indicators.py", _FN + "nodes.py", _FN + "rules.py"
_TFN, _FDOC = "tests/test_four_node_s25.py", "docs/strategies/four_node/README.md"
_FSRC = "docs/strategies/four_node/source/SHA256SUMS"
_IOP, _TIOP = S + "forecasting/index_ops.py", "tests/test_index_ops.py"
A.update({
    "FB-2501": (D, [_FSRC, _FS, _TFN], "verbatim source and Pine reference archived with SHA-256"),
    "FB-2502": (D, [_FS, _TFN], "XAU reference (never executable) and India variant, independent params"),
    "FB-2503": (D, [_FS, _TFN], "flagged; variant BLOCKED until owner choice"),
    "FB-2504": (D, [_FS, _TFN], "both hypotheses named; never pooled"),
    "FB-2505": (P, [_FS, _FR, _TFN], "comparator alternatives; inclusive-boundary fixtures for N2 not added"),
    "FB-2506": (D, [_FS, _TFN], "N4 alternatives; unresolved blocks"),
    "FB-2507": (D, [_FR, _TFN], "both candle definitions with fixtures; unresolved returns None"),
    "FB-2508": (D, [_FI, _TFN], "prior-day-only Fib with named anchor"),
    "FB-2509": (D, [_FI, _TFN], "Wilder SMMA 20 on low/close/high, warm-up and gap reset"),
    "FB-2510": (P, [_FI, _TFN], "SMMA on close for any period; parity only against hand reference"),
    "FB-2511": (D, [_FI, _TFN], "full 27-ratio set"),
    "FB-2512": (D, [_FNO, _TFN], "left/right windows, strict ties, alternation, no repaint"),
    "FB-2513": (D, [_FNO, _TFN], "pivot_at separate from confirmed_at"),
    "FB-2514": (P, [_FNO, _TFN], "zigzag with replace events; invalidation/expiry rules not defined"),
    "FB-2515": (D, [_FR, _TFN], "three observable nodes required; unobservable rejected"),
    "FB-2516": (D, [_FR, _TFN], "named predicates with passed/failed reason codes"),
    "FB-2517": (D, [_FR, _TFN], "entry actionable next bar, not at the breakout close"),
    "FB-2518": (D, [_FR, _TFN], "D1 inclusive, D2 exclusive boundary tested"),
    "FB-2519": (P, [_FR, _TFN], "barrier clearance; ahead/behind geometry not distinguished"),
    "FB-2520": (P, [_FR, _TFN], "spread-aware stop beyond N3 and max risk; tick rounding not applied"),
    "FB-2521": (D, [_FS, _FR], "XAU pips kept for reference; India uses ATR multiples"),
    "FB-2522": (D, [_FR, _TFN], "trail start, step ratchet, never loosens, both sides"),
    "FB-2523": (P, [_FR], "trail takes an executable price; bid/ask choice left to caller"),
    "FB-2524": (D, [_FR, _TFN], "unknown candle/N4 definition returns BLOCKED"),
    "FB-2525": (P, [_FR, _TFN], "instrument config for tick, multiplier, ATR, spread, slippage"),
    "FB-2526": (D, [_FR, _TFN], "rupee risk sizing in whole lots; forex X rule not used"),
    "FB-2528": (P, [_FDOC, S + "signals/index_options.py"], "bridge documented through index_options; not wired"),
    "FB-2529": (P, [S + "signals/index_options.py"], "option checks reused; stale quotes give UNPRICED/NO_TRADE"),
    "FB-2533": (D, [_FDOC], "reuse map"),
    "FB-2537": (P, [_FDOC, S + "forecasting/index_15m.py"], "purged splits available; no run"),
    "FB-2541": (D, [_FR, _TFN], "funnel stages deduped and reconciled by event id"),
    "FB-2542": (D, [_FR, _TFN], "per-trade trace record"),
    "FB-2543": (P, [_TFN], "repaint and blocked-variant tests; reconnect/crash not simulated"),
    "FB-2402": (D, [_IOP, _TIOP], "dated optionable-index snapshots"),
    "FB-2412": (P, [_IOP, _TIOP], "versioned logistic model; tree and multi-index comparison not run"),
    "FB-2419": (D, [_IOP, _TIOP], "linked BUY CE/PE card with exits, TTL and max planned loss"),
    "FB-2421": (D, [_IOP, _TIOP], "append-only hash-chained forecast events"),
    "FB-2431": (D, [_IOP, _TIOP], "one decision per completed 15-minute interval"),
    "FB-2432": (P, [_IOP, _TIOP], "UNAVAILABLE on stale/missing inputs; reconnect/backfill not built"),
    "FB-2434": (P, [_IOP, _TIOP], "option card data; no UI"),
    "FB-2435": (D, [_IOP, _TIOP], "EOD audit by index/state with unresolved list"),
})
_LT = "tests/test_lifecycle_tables_4a.py"
_MPF, _TMPF = S + "research/mf_portfolio.py", "tests/test_mf_portfolio.py"
_RT, _TRT = S + "paper/router.py", "tests/test_router.py"
_ST, _TST = S + "evaluation/study_tools.py", "tests/test_study_tools.py"
_CLI, _TCLI = S + "paper/five_desk_cli.py", "tests/test_five_desk_cli.py"
A.update({
    "FB-0490": (D, [_LT], "table-driven inequalities, ticks, R-multiple, fees, expiry"),
    "FB-0492": (D, [_LT], "stale, disconnect, slippage, no liquidity, repeats, out-of-order, recovery, duplicate exits"),
    "FB-0493": (D, [_LT], "no unprotected position, no double sell, quantities reconcile, no early fills"),
    "FB-1307": (P, [_LT], "split preserves cost; halt keeps risk open; delisting via D1 states"),
    "FB-1315": (D, [_LT], "ETF fill and MF allotment never share time or price"),
    "FB-2008": (D, [_LT], "expiry, wide spread, stale iNAV, MF overlap adversarial fixtures"),
    "FB-2439": (P, [_LT], "shared capital, duplicate batches, overspend; reconnect/halt via other tests"),
    "FB-0704": (D, [_MPF, _TMPF], "effective-dated cut-offs by class with scheme override and suspension"),
    "FB-0716": (D, [_MPF, _TMPF], "MF money reconciliation"),
    "FB-0312": (D, [_MPF, _TMPF], "debt metrics with ABSENT labels"),
    "FB-0313": (D, [_MPF, _TMPF], "fund-of-funds lookthrough with coverage and cycle guard"),
    "FB-0604": (P, [_MPF, _TMPF], "overlap and concentration; style drift and manager effects need disclosures"),
    "FB-0606": (D, [_MPF, _TMPF], "cash-management options net of payout lag and load"),
    "FB-0607": (P, [_MPF, _TMPF], "risk-budgeted allocation with cash; no covariance optimiser"),
    "FB-0408": (D, [_MPF, _TMPF], "allocation recommendation record with NAV_PENDING"),
    "FB-0613": (D, [_MPF], "owner-set SIP parameters record"),
    "FB-0614": (D, [_MPF], "versioned charter with cash/no-action control"),
    "FB-1106": (D, [_MPF, _TMPF], "ETF card fields with UNAVAILABLE premium"),
    "FB-1107": (D, [_MPF, _TMPF], "MF card fields incl. pending allotment, cut-off, load, tax"),
    "FB-1108": (D, [_MPF, _TMPF], "MF decisions, no buy-at-price action"),
    "FB-1206": (D, [_MPF, _TMPF], "return with flows, pending redemption and idle cash"),
    "FB-2007": (P, [_MPF, _TMPF], "tax-lot/load-aware HOLD; SIP schedule in mf_plans"),
    "FB-0910": (D, [_MM, _TEM], "NAV-based returns not reduced by TER"),
    "FB-0475": (D, [_RT, _TRT], "ETF long only unless style validated"),
    "FB-0484": (D, [_RT, _TRT], "autonomous always, approved twin separately"),
    "FB-1704": (P, [_RT, _TRT], "delta-equivalent with uncovered lookthrough listed; option scenarios in cross_asset"),
    "FB-2420": (P, [_RT, _TRT], "expiry and leg-sync guards; IV collapse in cross_asset scenarios"),
    "FB-2423": (D, [_RT, _TRT], "risk checks every tick, signals on their own clock"),
    "FB-2424": (P, [_RT, _AL], "delta exposure by underlying plus allocator limits"),
    "FB-2527": (D, [_RT, _TRT], "unsupported instruments research-only"),
    "FB-2530": (D, [_RT, _TRT], "underlying levels to premium SL/TP via delta"),
    "FB-2531": (P, [_RT, _TRT], "futures and ETF sizing with margin/cash; fee model elsewhere"),
    "FB-2532": (D, [_RT, _TRT], "independent risk clock from signal clock"),
    "FB-0502": (P, [_ST, _TST], "MA trend, mean reversion, vol sizing; not preregistered or run"),
    "FB-0504": (D, [_ST, _TST], "cost-plausibility gate for short-horizon ETF studies"),
    "FB-0506": (D, [_ST, _TST], "friction vs tracking decomposition"),
    "FB-0509": (D, [_ST, _TST], "stress scenarios"),
    "FB-0511": (P, [_ST, _TST], "excess over risk-matched benchmark; not run on data"),
    "FB-0605": (P, [_ST, _TST], "persistence measure; survivorship via scheme master"),
    "FB-0609": (D, [_ST, _TST], "studies need ledger registration to be decisive"),
    "FB-0611": (P, [_ST], "peer percentile tool; no measured result"),
    "FB-0310": (P, [_ST, _MS], "peer percentile and dated benchmark field"),
    "FB-1204": (D, [_ST, _TST], "price return vs NAV vs benchmark decomposition"),
    "FB-1205": (P, [_ST, _MM], "TRI and peer comparison tools"),
    "FB-1807": (D, [_ST, _TST], "p99 latency, expiry, missed, duplicate and coverage rates"),
    "FB-2006": (P, [_XA, _ST], "wrapper routing and peer tools"),
    "FB-2409": (P, [_ST, _TST], "divergence and relative strength; OI/IV features need data"),
    "FB-2427": (D, [_ST, _TST], "direction and P&L scored separately"),
    "FB-2534": (P, [_ST, _TST], "stream/batch parity check on pivots"),
    "FB-2538": (D, [_ST, _TST], "no-trade, random matched, opening-range baselines"),
    "FB-2539": (D, [_ST, _TST], "cost model with spread, commission, tax, latency"),
    "FB-2540": (D, [_ST, _TST], "Holm, BH and neighbourhood stability"),
    "FB-0489": (D, [_CLI, _TCLI], "read-only CLI views"),
    "FB-1009": (P, [_CLI, _TCLI], "weekly MF review schedule"),
    "FB-2433": (D, [_CLI, _TCLI], "index heatmap view with tradability"),
    "FB-0311": (P, [_CLI, _TCLI], "disclosure CSV parser; fetching is external"),
})
_OB, _TOB = S + "execution/option_book.py", "tests/test_option_book.py"
_PL, _TX, _TPT = S + "portfolio/position_ledger.py", S + "portfolio/tax_lots.py", "tests/test_position_tax.py"
_DOC5 = "docs/five-desk/"
_ISO = "tests/test_broker_isolation_five_desk.py"
_MQ, _TMQ = I + "market_quality.py", "tests/test_market_quality.py"
_DL, _TDL = S + "signals/desk_logic.py", "tests/test_desk_logic.py"
_CT, _OPS, _TCO = S + "forecasting/calibration_tools.py", S + "paper/ops.py", "tests/test_calibration_ops.py"
A.update({
    "FB-0476": (D, [_OB, _IOP, _TOB], "exact token, CE/PE, strike, expiry, lots; premium band via option card"),
    "FB-0477": (D, [_OB, _TOB], "spread, depth, decay, expiry monitoring flags"),
    "FB-0478": (D, [_OB, _TOB], "combined spread value triggers; leg risk tracked"),
    "FB-0479": (D, [_OB, _TOB], "uncovered writing blocked"),
    "FB-0807": (P, [_OB, _TOB], "contract identity with version date and chain routing; live chain external"),
    "FB-0809": (P, [_OB, _TOB], "leg state, partial fills, hedge risk; exercise/settlement not modelled"),
    "FB-0905": (D, [_OB, _TOB], "contract-level realized and unrealized premium P&L with fees"),
    "FB-1304": (P, [_TOB, _LT], "zero bids, depth, expired, partial legs; wrong lot via contract validation"),
    "FB-2001": (D, [_OB, _TOB], "chain selection on bid/ask, depth, theta with OI caveat"),
    "FB-2002": (P, [_OB, _TOB], "monotonicity, convexity, calendar checks; no fitted surface"),
    "FB-0471": (D, [_PL, _TPT], "per-position ordered events with idempotency keys and snapshots"),
    "FB-0485": (D, [_PL, _TPT], "human decisions separate; original plan immutable"),
    "FB-0486": (P, [_PL, _CR], "alert kinds defined; no streaming UI"),
    "FB-0487": (D, [_PL, _TPT], "MTM row withholds P&L on stale quotes"),
    "FB-0494": (D, [_PL, _TPT], "net by exit reason and ablation vs plain SL/TP"),
    "FB-0902": (P, [_PL], "position id with funding account/sleeve"),
    "FB-1105": (P, [_PL, _TPT], "sleeve comparison; same-feed enforcement via one OMS"),
    "FB-1109": (D, [_PL, _TPT], "recorded attribution chain"),
    "FB-1212": (D, [_PL, _TPT], "paired eligible signals; selection vs execution effect"),
    "FB-2106": (D, [_PL, _TPT], "same decision-time set; human effects separated"),
    "FB-0907": (D, [_TX, _TPT], "external flows kept apart from returns"),
    "FB-0908": (D, [_TX, _TPT], "effective-dated tax classes and rules as data; missing raises"),
    "FB-0909": (D, [_TX, _TPT], "FIFO gain lots, term, estimate, financial year"),
    "FB-1208": (P, [_TX, _SC], "desk contribution; TWR/XIRR in scorecards"),
    "FB-0005": (D, [_DOC5 + "CHARTER.md"], "five-desk charter and execution scope"),
    "FB-1409": (P, [_DOC5 + "DATA_SOURCES.md"], "sources, limits, retention, failover; legal review is owner's"),
    "FB-1410": (D, [_DOC5 + "CAPABILITY_MATRIX.md"], "capability matrix"),
    "FB-0007": (D, [_ISO], "hostile isolation: paper modules import no broker; locks closed"),
    "FB-1318": (D, [_ISO], "env flip alone cannot open broker; unlisted endpoints refused"),
    "FB-0204": (P, [_MQ, _TMQ], "EOD rows with hashes and kept corrections; import run external"),
    "FB-0206": (D, [_MQ, _TMQ], "session-gated tick-to-bar aggregation"),
    "FB-0208": (D, [_MQ, _TMQ], "spread, depth, traded value, stale rate, gaps"),
    "FB-0212": (P, [_MQ, S + "instruments/corporate_actions.py", _LT], "split/distribution via D1; lot/tick revisions via D1 terms"),
    "FB-0304": (D, [_MQ, _TMQ], "NAV revision history; corrections not backdated"),
    "FB-0406": (D, [_MQ, _TMQ], "alert dedupe keeps new versions and material moves"),
    "FB-0469": (D, [_MQ, _TMQ], "timestamps, age, spread, depth, circuit, halt; LTP-only refused"),
    "FB-1803": (P, [_MQ, _TMQ], "sequence gaps detected; downgrade wiring not built"),
    "FB-1905": (P, [_MQ, _TMQ], "gap and circuit-locked stop fills; option spread widening in cross_asset"),
    "FB-1901": (D, [_DL, _TDL], "frozen versioned exit policy API"),
    "FB-1903": (D, [_DL, _TDL], "exit guard reduces largest risks to limit"),
    "FB-1602": (D, [_DL, _TDL], "SL/TP/time probabilities with gap term from pre-decision vol"),
    "FB-1605": (D, [_DL, _TDL], "entry zone, spread, slippage, TTL constraints"),
    "FB-0473": (P, [_DL, _TDL], "up to five positive-EV swing candidates with ATR SL/TP; not wired"),
    "FB-0503": (P, [_DL, _TDL], "equal-vol rotation with cash; study not run"),
    "FB-0608": (P, [_DL, _TDL], "SIP simulation with lag; rebalance rules minimal"),
    "FB-1703": (D, [_DL, _TDL], "exposure by sector/index/factor tags"),
    "FB-2414": (D, [_CT, _TCO], "isotonic calibration on earlier window"),
    "FB-2205": (D, [_CT, _TCO], "strata calibration on prior data; thin strata UNCALIBRATED"),
    "FB-2206": (D, [_CT, _TCO], "material change detector"),
    "FB-2401": (D, [_CT, _TCO], "dated index master incl. renamed and discontinued"),
    "FB-2415": (P, [_CT, _TCO], "baseline comparison helper; existing ensemble not wired"),
    "FB-2428": (D, [_CT, _TCO], "always-up, frequency, momentum, no-trade nulls"),
    "FB-2429": (P, [_CT, _SC], "stratified metrics and session bootstrap"),
    "FB-1801": (D, [_OPS, _TCO], "per-signal ordered stage timestamps"),
    "FB-1802": (P, [_OPS, _TCO], "feed mode labels; async adapter is external integration"),
    "FB-1808": (P, [_OPS, _TCO], "replay through the same decision function"),
    "FB-1702": (D, [_OPS, _TCO], "gateway: one spending authority over proposing allocators"),
    "FB-2422": (P, [_OPS, _TCO], "eligible forecast to canonical OMS order"),
    "FB-1003": (D, [_OPS, _TCO], "options signals only with fresh chain"),
    "FB-1006": (D, [_OPS, _TCO], "after-NAV steps only after publication"),
    "FB-1007": (D, [_OPS, _TCO], "MF requests only before cut-off"),
    "FB-1012": (D, [_OPS, _TCO], "per-desk latency, quote-to-fill, drops, downtime"),
    "FB-1102": (P, [_OPS, _TCO], "five desk page records with clocks; no UI"),
})
_EN, _TEN = S + "paper/five_desk_engine.py", "tests/test_five_desk_engine.py"
_EVD, _RPT, _TER = S + "signals/evidence.py", S + "evaluation/reports.py", "tests/test_evidence_reports.py"
_TPT2, _TMJ = "tests/test_partial_and_timeouts.py", "tests/test_mf_journal_folio.py"
_TFE, _IXX, _TIXX = "tests/test_four_node_edges.py", S + "forecasting/index_extras.py", "tests/test_index_extras.py"
A.update({
    "FB-0801": (P, [_EN, _TEN], "one versioned path signal->risk->OMS->fill->ledger; legacy runners not migrated"),
    "FB-0810": (D, [_EN, _TEN], "keyed outbox; replay after crash posts nothing twice"),
    "FB-0813": (D, [_EN, _TEN], "instrument, expiry, reservation and cash verified before commit"),
    "FB-0472": (D, [_EN, _TEN, _SIG], "no entry without policy; closed only on recorded exit fill"),
    "FB-1701": (D, [_EN, _AL, _TEN], "engine reserves before ordering and releases on exit"),
    "FB-0003": (D, [_EVD, _TER], "replayed/historical results can never be promoted to forward tiers"),
    "FB-0011": (D, [_EVD, _TER], "five named tiers with required evidence per tier"),
    "FB-0402": (D, [_EVD, _TER], "decision record: features, distribution interval, benchmark, costs, risk, contraindications"),
    "FB-0404": (D, [_EVD, _SIG, _TER], "holding window on the decision record; entry/stop/target/tick on Signal"),
    "FB-0409": (D, [_EVD, _TER], "LLM output never orders"),
    "FB-0410": (D, [_EVD, _PL, _TPT], "separate manual-approved sleeve; autonomous outcomes preserved"),
    "FB-0411": (D, [_EVD, _TER], "counterfactuals tracked, never in portfolio P&L"),
    "FB-0412": (D, [_EVD, _PIT, _TER], "missing inputs explicit on the record; UNPRICED quotes"),
    "FB-0912": (D, [_RPT, _TER], "attribution by any key with shared-position weights"),
    "FB-0913": (P, [_RPT, _TER], "VaR/ES with liquidity add-on, beta, drawdown; correlation matrix not built"),
    "FB-0915": (D, [_RPT, _TER], "limit breaches: drawdown, instrument, underlying, premium"),
    "FB-1101": (P, [_RPT, _TER], "consolidated report data; no dashboard UI"),
    "FB-1104": (D, [_RPT, _TER], "manual actions are paper-only"),
    "FB-1111": (P, [_RPT, _TER], "hash-stamped JSON bundle; CSV/Parquet export not built"),
    "FB-1207": (D, [_RPT, _TER], "expectancy CI, hit rate, payoff, Sharpe, turnover, shortfall"),
    "FB-1209": (P, [_RPT, _TER], "cost 1x/1.5x/2x; regime/latency robustness not run"),
    "FB-1210": (D, [_RPT, _TER], "effective sample size for overlap and autocorrelation"),
    "FB-1211": (D, [_RPT, _TER], "intent denominator; no-fill and unknown as coverage"),
    "FB-1214": (D, [_RPT, _TER], "worst desks listed first"),
    "FB-1216": (D, [_RPT, _TER], "committee gives zero to non-positive marginal desks"),
    "FB-1601": (D, [_EV, _RPT, _TER], "net EV plus bootstrap interval"),
    "FB-1706": (D, [_RPT, _TER], "no weight increase on non-positive long-run edge"),
    "FB-1804": (D, [_RPT, _TER], "depth walk with participation cap and non-fills"),
    "FB-1805": (P, [_RPT, _TER], "shortfall measure; market comparison needs live data"),
    "FB-2101": (D, [_RPT, _TER], "daily desk report with flows separated"),
    "FB-2102": (D, [_RPT, _TER], "attribution with explicit residual"),
    "FB-2105": (D, [_RPT, _TER], "top drivers from recorded inputs only"),
    "FB-2107": (D, [_RPT, _TER], "selection regret, not credited"),
    "FB-2201": (D, [_RPT, _TER], "registration needs complete preregistration"),
    "FB-2202": (D, [_RPT, _TER], "registry with immutable code/config hash per version"),
    "FB-2203": (D, [_RPT, _TER], "vs cash, benchmark, naive, champion; momentum removed"),
    "FB-2208": (D, [_RPT, _TER], "hash-stamped evidence bundle; attestation never inferred"),
    "FB-2305": (P, [_RPT, _TER], "health data with status; no screen"),
    "FB-0464": (D, [_EX, _TPT2], "partial exits keep remainder armed; no overselling; restart-safe"),
    "FB-0491": (D, [_TEX, _TPT2, _TXP], "SL, TP, both-in-bar, gaps, trailing, partial entry/exit paths"),
    "FB-0804": (D, [_OMS, _TPT2], "TIMED_OUT and UNKNOWN states tested"),
    "FB-1906": (P, [_TPT2, _TXP], "partial and scale-out invariants; cancel races not simulated"),
    "FB-0715": (D, [_LG, _TMJ], "IDCW payout and reinvestment posted to the journal"),
    "FB-0906": (D, [_LG, _MP, _TMJ], "units at published NAV plus distribution income in journal"),
    "FB-0708": (D, [_MS, _MP, _LG, _TMJ], "per-lot exit load and stamp duty posted"),
    "FB-0701": (D, [_MF, _MP, _TMJ], "order with folio, scheme, amount, immutable request time"),
    "FB-0713": (D, [_MP, _TMJ, _TMP], "minimums, lock-in, gating, folio restrictions"),
    "FB-0481": (D, [_SIG, _MP, _TMP], "NAV thresholds produce redemption requests with NAV timing"),
    "FB-0451": (D, [_SIG, _XP, _TXP], "EntrySignal plus linked ExitSignal with ids and lineage"),
    "FB-2505": (D, [_FR, _TFE], "inclusive boundary fixtures for N2 comparators"),
    "FB-2514": (D, [_FNO, _FR, _TFE], "zigzag with replace events and node expiry"),
    "FB-2519": (D, [_FR, _TFE], "barriers split ahead/behind"),
    "FB-2520": (D, [_FR, _TFE], "stop rounded away from entry on tick"),
    "FB-2523": (D, [_FR, _TFE], "trail on executable side (bid long, ask short)"),
    "FB-2411": (D, [_IX, _IXX, _TIXX], "frequency, persistence and time-of-day vol baselines"),
    "FB-2413": (D, [_IX, _IXX, _TIXX], "probabilities validated plus empirical prediction interval"),
    "FB-2417": (D, [_IXX, _TIXX], "path simulation to premium distribution with IV shock and exit spread"),
    "FB-2418": (D, [_IXX, _TIXX], "ranking with tail and marginal exposure penalties"),
})
_POL, _TPOL = S + "paper/policy.py", "tests/test_policy_and_more_adversarial.py"
A.update({
    "FB-0006": (D, [_POL, _TPOL, _ISO], "central paper-only policy asserted with forward_edge constant"),
    "FB-0460": (D, [_POL, _TPOL], "size from risk, cash, notional, depth and correlated capacity in lots"),
    "FB-0461": (D, [_POL, _TPOL, _SIG], "conservative tick rounding then R:R revalidation; no overnight shorts"),
    "FB-0917": (D, [_POL, _LG, _TPOL], "reconcile handler: journal balance then session gate"),
    "FB-1008": (D, [_POL, _DC, _TPOL], "daily reconcile handler wired into the phase"),
    "FB-1301": (D, [_ADV, _TPOL], "1200 ledger events and 1000 randomised timezone PIT checks"),
    "FB-1303": (D, [_TPOL, _ADV], "polling labelled degraded; stale quotes never fill better"),
    "FB-1306": (D, [_TPOL, _ADV], "NIFTY, gold and USD exposures summed across wrappers"),
    "FB-1309": (D, [_TPOL, _ADV], "normal, liquid, weekend, holiday, late funds, suspension"),
    "FB-1312": (D, [_ADV, _TMP], "partial redemption, lot loads, fractional units, minimums, lock-in"),
    "FB-1313": (D, [_TPOL, _TMP], "IDCW, winding-up history, delayed redemption NAV"),
    "FB-1316": (D, [_TPOL, _TEN], "crash at each lifecycle point replays identically"),
    "FB-1319": (D, [_TPOL, _ADV], "25% gap, 50% premium loss, gated MF, correlated exposure"),
    "FB-1304": (D, [_TOB, _LT], "zero bids, depth, expired, partial legs, invalid contracts"),
    "FB-1307": (D, [_LT], "split cost preserved, halt keeps risk open, delisting state via D1"),
    "FB-2439": (D, [_LT, _TPT2], "shared capital, duplicate batches, partial exits, overspend refused"),
})
_DR, _TDR = S + "paper/desk_runs.py", "tests/test_desk_runs.py"
A.update({
    "FB-0474": (D, [_DR, _TDR], "intraday BUY and SELL_SHORT with SL/TP and mandatory flatten; missed exit escalates"),
    "FB-1004": (P, [_DR, _EN, _TDR], "intraday exits and flatten built; live loop external"),
    "FB-0407": (D, [_DR, _TDR], "up to five swing candidates plus independent ETF ranks; no quota"),
    "FB-1005": (D, [_DR, _TDR], "locked next-session publication"),
})
_DH, _TDH = S + "paper/dashboard_html.py", "tests/test_dashboard_html.py"
A.update({
    "FB-0305": ("PARTIAL", ["src/financial_brain/providers/amfi_history.py", "tests/test_amfi_history.py"],
                "bounded windows, resume, retries, rate limit; real AMFI fetch is external"),
    "FB-0306": (D, ["src/financial_brain/providers/amfi_history.py", "tests/test_amfi_history.py"],
                "windows capped at 90 days; wider requests refused"),
})

A.update({
    "FB-1101": (D, [_DH, _RPT, _TDH], "consolidated NAV, capital and P&L page"),
    "FB-1102": (D, [_DH, _OPS, _TDH], "five desk pages with their own clocks"),
    "FB-1103": (D, [_DH, _CR, _TDH], "signals with side, closing action, SL/TP, trigger, costs, evidence"),
    "FB-2301": (D, [_DH, _CR, _TDH], "prioritised Today screen"),
    "FB-2305": (D, [_DH, _RPT, _TDH], "health screen"),
    "FB-2434": (D, [_DH, _IOP, _TDH], "option cards page"),
})


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True).stdout.strip()


def locate(path: str) -> dict | None:
    """First ref on which the path exists, with its object hash."""
    for label, ref in REFS.items():
        sha = git("rev-parse", "--verify", "--quiet", f"{ref}:{path}")
        if sha:
            return {"path": path, "ref": label, "object": sha[:12]}
    if (ROOT / path).exists():
        body = (ROOT / path).read_bytes() if (ROOT / path).is_file() else b""
        return {"path": path, "ref": "worktree", "object": hashlib.sha1(body).hexdigest()[:12]}
    return None


def tasks_of(text: str) -> list[dict]:
    out, section = [], ""
    for line in text.splitlines():
        if line.startswith("#"):
            section = line.lstrip("# ").strip()
        m = re.match(r"- \[ \] \*\*(FB-\d{4})\*\* (.*)", line)
        if m:
            title = re.split(r"(?<=[.;:])\s", m.group(2), maxsplit=1)[0]
            out.append({"id": m.group(1), "section": section, "title": title[:150]})
    return out


def main(checklist: Path) -> int:
    text = checklist.read_text(encoding="utf-8")
    tasks = tasks_of(text)
    ids = {t["id"] for t in tasks}
    unknown = sorted(set(A) | set(EXTERNAL) - ids) and sorted((set(A) | set(EXTERNAL)) - ids)
    if unknown:
        print("ids not in the checklist:", unknown)
        return 1
    missing, rows = [], []
    for t in tasks:
        status, paths, note = A.get(t["id"], (N, [], ""))
        ev = []
        for p in paths:
            hit = locate(p)
            if hit is None:
                missing.append((t["id"], p))
            else:
                ev.append(hit)
        kind, need = EXTERNAL.get(t["id"], ("IN_HOUSE", ""))
        rows.append({**t, "status": status, "dependency": kind, "blocker": need, "note": note,
                     "source": [e for e in ev if not e["path"].startswith("tests/")],
                     "test": [e for e in ev if e["path"].startswith("tests/")],
                     "unmerged": sorted({e["ref"] for e in ev} - {"main"})})
    if missing:
        print("evidence paths that do not exist on any ref:")
        for m in missing:
            print("  ", *m)
        return 1

    refs = {k: {"ref": v, "commit": git("rev-parse", "--short=10", v),
                "ahead_of_main": int(git("rev-list", "--count", f"main..{v}") or 0),
                "behind_main": int(git("rev-list", "--count", f"{v}..main") or 0),
                "test_files": len(git("ls-tree", "-r", "--name-only", v, "--", "tests").splitlines())}
            for k, v in REFS.items()}
    by_status = Counter(r["status"] for r in rows)
    by_dep = Counter(r["dependency"] for r in rows)
    by_section: dict[str, Counter] = {}
    for r in rows:
        by_section.setdefault(r["section"], Counter())[r["status"]] += 1
    doc = {"schema": "five-desk-audit/1", "audited": "2026-11-17", "depth": "module survey; acceptance tests not run",
           "checklist": {"name": checklist.name, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "tasks": len(tasks)},
           "refs": refs, "status_meaning": MEANING, "by_status": dict(by_status), "by_dependency": dict(by_dep), "tasks": rows}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "audit.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")

    L = ["# Five-desk checklist v1.4: repository audit", "",
         f"Audited 2026-11-17 against {len(tasks)} tasks. Checklist sha256 `{doc['checklist']['sha256'][:16]}`.",
         "Depth: module survey. Evidence paths and hashes are verified by `scripts/audit/five_desk_audit.py`;",
         "no acceptance test was run per task. Machine-readable copy: `audit.json`.", "",
         "| status | tasks | meaning |", "|---|---|---|"]
    L += [f"| {s} | {by_status.get(s, 0)} | {MEANING[s]} |" for s in (D, P, F, N)]
    L += ["", "Not finishable in-house: " + ", ".join(f"{k} {v}" for k, v in sorted(by_dep.items()) if k != "IN_HOUSE") + ".", "",
          "## Branches the evidence lives on", "", "| label | ref | commit | ahead of main | behind | test files |", "|---|---|---|---|---|---|"]
    L += [f"| {k} | `{v['ref']}` | `{v['commit']}` | {v['ahead_of_main']} | {v['behind_main']} | {v['test_files']} |" for k, v in refs.items()]
    n_un = sum(1 for r in rows if r["unmerged"])
    L += ["", f"{n_un} tasks cite evidence that is not on `main`.", "", "## By section", "",
          "| section | done | partial | foothold | not started |", "|---|---|---|---|---|"]
    L += [f"| {s} | {c.get(D, 0)} | {c.get(P, 0)} | {c.get(F, 0)} | {c.get(N, 0)} |" for s, c in by_section.items()]
    L += ["", "## Every task", "", "| id | status | needs | evidence | note |", "|---|---|---|---|---|"]
    for r in rows:
        ev = "<br>".join(f"`{e['path'].replace(S, '')}` ({e['ref']} {e['object'][:7]})" for e in r["source"] + r["test"])
        need = "" if r["dependency"] == "IN_HOUSE" else f"{r['dependency']}: {r['blocker']}"
        L.append(f"| {r['id']} | {r['status']} | {need} | {ev} | {r['title']}{' — ' + r['note'] if r['note'] else ''} |")
    (OUT / "AUDIT.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(json.dumps({"tasks": len(tasks), "by_status": by_status, "by_dependency": by_dep, "unmerged_evidence": n_un}, indent=1))
    for s, c in by_section.items():
        print(f"{c.get(D,0):3d} {c.get(P,0):3d} {c.get(F,0):3d} {c.get(N,0):3d}  {s[:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
