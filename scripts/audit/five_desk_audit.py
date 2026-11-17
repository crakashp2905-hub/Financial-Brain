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
    "FB-0306": ("EXTERNAL", "AMFI history acquisition within its terms"),
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
    "FB-2501": ("OWNER", "the original 4-Node rules document is not in the repository; the owner must supply it"),
    "FB-2503": ("OWNER", "owner must choose the BUY-exception interpretation"),
    "FB-2504": ("OWNER", "owner must confirm N3 midpoint symmetry"),
    "FB-2505": ("OWNER", "owner must confirm the N2 comparator"),
    "FB-2506": ("OWNER", "owner must define N4"),
    "FB-2507": ("OWNER", "owner must define a significant candle"),
    "FB-2508": ("OWNER", "owner must give the Fibonacci anchors"),
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
_IX, _TIX = S + "forecasting/index_15m.py", "tests/test_index_15m_s24.py"
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
