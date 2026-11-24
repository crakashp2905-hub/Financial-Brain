"""Build/verify the trader-core capability and module ownership inventory."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/edge-program/capability-ownership.v1.json"
sys.path.insert(0, str(ROOT / "src"))

CAPABILITIES = [
    {"id": "strategy_identity_lifecycle", "category": "strategy", "owner": "src/financial_brain/forward_edge/registry.py",
     "owner_symbol": "StrategySpec", "implementations": ["src/financial_brain/forward_edge/strategy_adapter.py", "src/financial_brain/forward_edge/parameter_freeze.py"],
     "boundary": "Canonical version, benchmark, status-transition and paper eligibility registry. AlgorithmSpec remains the algorithm behavior/clock contract."},
    {"id": "strategy_candidate_catalogue", "category": "strategy", "owner": "src/financial_brain/forward_edge/eligibility.py",
     "owner_symbol": "build_catalogue", "implementations": ["src/financial_brain/forward_edge/strategy_inventory.py", "docs/sources/algorithm-source-registry.json", "docs/trials/ledger.jsonl"],
     "boundary": "Enumerates current source candidates and protected historical trials with explicit status; candidate presence does not imply paper eligibility or validated edge."},
    {"id": "paired_paper_sleeve_evaluation", "category": "evaluation", "owner": "src/financial_brain/forward_edge/paired_paper.py",
     "owner_symbol": "compare_paper_sleeves", "implementations": ["src/financial_brain/forward_edge/paired_controls.py"],
     "boundary": "Compares automatic and human-reviewed paper outcomes over one frozen eligible signal set; requires cash outcomes for abstentions and never authenticates actors or promotes a strategy."},
    {"id": "mf_paper_lot_accounting", "category": "ledger", "owner": "src/financial_brain/paper/mf_lot_book.py",
     "owner_symbol": "subscribe/redeem/account_snapshot", "implementations": ["src/financial_brain/paper/mf_folio_book.py", "src/financial_brain/paper/mf_exit_loads.py", "src/financial_brain/paper/mf_redemption_constraints.py", "src/financial_brain/providers/mf_scheme_terms.py", "src/financial_brain/storage/schema.sql"],
     "boundary": "Durable folio-scoped paper MF cash and FIFO purchase-lot accounting with event idempotency, source-hashed exit-load schedules, effective-dated terms, evidence-backed legacy-folio reconciliation and fail-closed redemption restrictions. It consumes supplied NAVs and does not verify ownership evidence with a registrar or model tax, live settlement or production orders."},
    {"id": "algorithm_behavior_spec", "category": "strategy", "owner": "src/financial_brain/strategies/specs.py",
     "owner_symbol": "AlgorithmSpec", "implementations": ["src/financial_brain/strategies/families.py", "src/financial_brain/strategies/execution_rules.py"],
     "boundary": "Algorithm clock, cost, order, abstention and behavior specification; not promotion status or portfolio selection."},
    {"id": "trade_admissibility_gate", "category": "execution", "owner": "src/financial_brain/kernel/trade_admissibility.py",
     "owner_symbol": "evaluate_trade_admissibility", "implementations": ["src/financial_brain/kernel/net_ev.py", "src/financial_brain/kernel/execution_feasibility.py", "src/financial_brain/kernel/entry_constraints.py", "src/financial_brain/forward_edge/parameter_freeze.py"],
     "boundary": "Combines registered strategy freezes, paper budget/session/instrument/approval checks, entry-zone/TTL checks, execution evidence and costed net EV into reason-coded research/paper decisions. It is not wired into the forward session runner and never authorizes broker orders."},
    {"id": "prospective_strategy_adapter", "category": "strategy", "owner": "src/financial_brain/forward_edge/strategy_adapter.py",
     "owner_symbol": "StrategyAdapter", "implementations": ["src/financial_brain/forward_edge/registry.py"],
     "boundary": "Adapts approved strategy versions into prospective session decisions; legacy paper strategies remain separate runtime owners."},
    {"id": "kite_provider_adapter", "category": "feed", "owner": "src/financial_brain/providers/kite_data.py",
     "owner_symbol": "quote/candles", "implementations": ["src/financial_brain/providers/kite.py", "src/financial_brain/ingest/kite_history.py", "src/financial_brain/ingest/kite_live.py", "src/financial_brain/ingest/kite_minute.py"],
     "boundary": "Read-only Kite provider access and provider-specific history/live ingesters. No module infers use rights."},
    {"id": "nse_bse_amfi_provider_adapters", "category": "feed", "owner": "src/financial_brain/providers/",
     "owner_symbol": "source-specific provider modules", "implementations": ["src/financial_brain/ingest/"],
     "boundary": "Provider and ingestion modules listed under module_inventory own their source-specific fetch/parse behavior; entitlement remains separately owned."},
    {"id": "market_event_normalization", "category": "feed", "owner": "src/financial_brain/forward_edge/market_data.py",
     "owner_symbol": "normalize_kite_quote", "implementations": ["src/financial_brain/forward_edge/market_events.py"],
     "boundary": "Normalize source packets and preserve event/receive/availability clocks; does not establish provider permission."},
    {"id": "market_data_entitlement", "category": "feed", "owner": "src/financial_brain/forward_edge/data_permissions.py",
     "owner_symbol": "DataPermission", "implementations": ["src/financial_brain/forward_edge/data_gate.py", "src/financial_brain/forward_edge/session_runner.py", "src/financial_brain/paper/intraday_runner.py"],
     "boundary": "Source/use-scoped permission state and runtime enforcement; provider terms are supplied and reviewed externally."},
    {"id": "quote_archive", "category": "feed", "owner": "src/financial_brain/forward_edge/quote_archive.py",
     "owner_symbol": "QuoteArchive", "implementations": ["src/financial_brain/forward_edge/market_data.py", "src/financial_brain/paper/intraday_runner.py"],
     "boundary": "Normalized append-only quote-event archive; legacy raw DuckDB quote archive is a separate migration/source store."},
    {"id": "broker_oms", "category": "oms", "owner": "src/financial_brain/execution/oms/engine.py",
     "owner_symbol": "OrderManagementSystem", "implementations": ["src/financial_brain/execution/oms/kite_broker.py", "src/financial_brain/execution/oms/journal.py", "src/financial_brain/execution/oms/reconcile.py"],
     "boundary": "Broker order lifecycle and reconciliation. Forward paper simulation and kernel simulated OMS have separate scopes."},
    {"id": "forward_paper_oms", "category": "oms", "owner": "src/financial_brain/forward_edge/session_runner.py",
     "owner_symbol": "ForwardSessionRunner", "implementations": ["src/financial_brain/forward_edge/durable.py", "src/financial_brain/forward_edge/execution_scenario.py"],
     "boundary": "Normalized prospective-session paper intent, simulated fill and durable evidence lifecycle; no broker submission."},
    {"id": "legacy_intraday_paper_oms", "category": "oms", "owner": "src/financial_brain/paper/intraday_runner.py",
     "owner_symbol": "run_live/run_replay", "implementations": ["src/financial_brain/paper/intraday.py", "src/financial_brain/paper/ledger.py"],
     "boundary": "Existing intraday Kite quote loop and its local paper engine/ledger; not merged with ForwardSessionRunner evidence."},
    {"id": "kernel_simulated_oms", "category": "oms", "owner": "src/financial_brain/kernel/oms.py",
     "owner_symbol": "simulated event OMS", "implementations": ["src/financial_brain/kernel/paper_account.py", "src/financial_brain/kernel/fills.py"],
     "boundary": "Kernel-level deterministic simulated order/account events. Not broker OMS or forward session persistence."},
    {"id": "canonical_portfolio_ledger", "category": "ledger", "owner": "src/financial_brain/portfolio/canonical_ledger.py",
     "owner_symbol": "canonical event ledger", "implementations": ["src/financial_brain/portfolio/legacy_migration.py"],
     "boundary": "Consolidated event accounting and P&L promotion gate; migrated historical gaps remain unresolved rather than synthesized."},
    {"id": "forward_session_evidence_journal", "category": "ledger", "owner": "src/financial_brain/forward_edge/durable.py",
     "owner_symbol": "DurableSessionJournal", "implementations": ["src/financial_brain/forward_edge/session_runner.py", "src/financial_brain/forward_edge/session_state.py"],
     "boundary": "Append-only audit/event persistence for normalized forward sessions, not the user's legacy paper book."},
    {"id": "legacy_paper_book_ledger", "category": "ledger", "owner": "src/financial_brain/paper/ledger.py",
     "owner_symbol": "paper ledger API", "implementations": ["src/financial_brain/paper/intraday_runner.py", "src/financial_brain/paper/ledger.py"],
     "boundary": "Legacy intraday paper trade/book storage; canonical portfolio migration is the consolidated accounting target."},
    {"id": "signals_and_opportunities", "category": "signals", "owner": "src/financial_brain/opportunity/engine.py",
     "owner_symbol": "opportunity decision engine", "implementations": ["src/financial_brain/signals/watch.py", "src/financial_brain/forward_edge/signal_trace.py"],
     "boundary": "Candidate generation/ranking is separate from strategy registry, execution eligibility and trader presentation."},
    {"id": "paper_signal_renderer", "category": "renderer", "owner": "src/financial_brain/paper/signals.py",
     "owner_symbol": "render", "implementations": ["src/financial_brain/paper/board.py", "src/financial_brain/paper/live_view.py"],
     "boundary": "Legacy paper signal/report outputs. The live dashboard snapshot publisher is a separate live-view contract."},
    {"id": "trader_cockpit", "category": "renderer", "owner": "src/financial_brain/forward_edge/cockpit.py",
     "owner_symbol": "render_cockpit", "implementations": ["src/financial_brain/forward_edge/pnl_dashboard.py", "src/financial_brain/forward_edge/order_blotter.py", "src/financial_brain/forward_edge/session_timeline.py"],
     "boundary": "Forward-edge trader cockpit projections; no broker control surface."},
    {"id": "cli_output", "category": "output", "owner": "src/financial_brain/cli.py",
     "owner_symbol": "fb command router", "implementations": ["src/financial_brain/commands/"],
     "boundary": "Command output and operator actions; command modules remain subordinate implementations."},
    {"id": "api_and_mcp_outputs", "category": "output", "owner": "src/financial_brain/interfaces/gateway.py",
     "owner_symbol": "read-only gateway contracts", "implementations": ["src/financial_brain/interfaces/mcp_server.py", "src/financial_brain/api/"],
     "boundary": "Read-only API/MCP query and rendering surfaces; broker order write endpoints are out of scope."}
]


def module_inventory() -> list[dict[str, str]]:
    rows: dict[str, str] = {}

    def add(path: Path, role: str) -> None:
        relative = path.relative_to(ROOT).as_posix()
        if path.name != "__init__.py" and path.is_file():
            rows[relative] = role

    for path in (ROOT / "src/financial_brain/strategies").glob("*.py"):
        add(path, "strategy_source_or_contract")
    for folder, role in (("providers", "provider_adapter"), ("ingest", "feed_ingester"),
                         ("evaluation", "strategy_research_or_evaluation"),
                         ("research", "research_or_strategy_research"),
                         ("decisions", "decision_support_or_output"),
                         ("signals", "signal_generation"),
                         ("opportunity", "opportunity_generation_or_memory"),
                         ("patterns", "technical_pattern_strategy_or_report"),
                         ("regime", "regime_signal_or_risk_model"),
                         ("forward_edge", "forward_trading_contract_or_support"),
                         ("risk", "risk_decision_or_control"),
                         ("portfolio", "portfolio_allocation_or_accounting"),
                         ("execution", "execution_or_cost_model"),
                         ("costs", "transaction_cost_model"),
                         ("execution/oms", "broker_oms_component"), ("paper", "paper_engine_or_output"),
                         ("interfaces", "api_or_mcp_output"), ("api", "api_output"),
                         ("commands", "command_output")):
        for path in (ROOT / "src/financial_brain" / folder).glob("*.py"):
            add(path, role)
    explicit = [
        "src/financial_brain/forward_edge/registry.py", "src/financial_brain/forward_edge/strategy_adapter.py",
        "src/financial_brain/forward_edge/parameter_freeze.py", "src/financial_brain/forward_edge/market_data.py",
        "src/financial_brain/forward_edge/market_events.py", "src/financial_brain/forward_edge/quote_archive.py",
        "src/financial_brain/forward_edge/data_permissions.py", "src/financial_brain/forward_edge/data_gate.py",
        "src/financial_brain/forward_edge/session_runner.py", "src/financial_brain/forward_edge/durable.py",
        "src/financial_brain/forward_edge/execution_scenario.py", "src/financial_brain/forward_edge/cockpit.py",
        "src/financial_brain/forward_edge/pnl_dashboard.py", "src/financial_brain/forward_edge/order_blotter.py",
        "src/financial_brain/forward_edge/session_timeline.py", "src/financial_brain/forward_edge/signal_trace.py",
        "src/financial_brain/strategies/specs.py", "src/financial_brain/strategies/families.py",
        "src/financial_brain/strategies/execution_rules.py", "src/financial_brain/portfolio/canonical_ledger.py",
        "src/financial_brain/portfolio/legacy_migration.py", "src/financial_brain/kernel/oms.py",
        "src/financial_brain/kernel/paper_account.py", "src/financial_brain/kernel/fills.py",
        "src/financial_brain/kernel/batch20c_strategies.py", "src/financial_brain/kernel/batch20e_strategies.py",
        "src/financial_brain/kernel/strategy_primitives.py", "src/financial_brain/kernel/strategy_sdk.py",
        "src/financial_brain/kernel/strategy_state.py", "src/financial_brain/kernel/execution_model.py",
        "src/financial_brain/kernel/execution_tools.py", "src/financial_brain/kernel/ledger.py",
        "src/financial_brain/kernel/trade_admissibility.py",
        "src/financial_brain/kernel/entry_constraints.py",
        "src/financial_brain/kernel/ledger.py", "src/financial_brain/opportunity/engine.py",
        "src/financial_brain/signals/watch.py", "src/financial_brain/paper/board.py",
        "src/financial_brain/paper/live_view.py", "src/financial_brain/paper/signals.py",
        "src/financial_brain/cli.py",
    ]
    for relative in explicit:
        path = ROOT / relative
        add(path, "canonical_contract_or_signal_output")
    # Every file defining a renderer, publisher, emitter or notification sender is enumerated.
    for path in (ROOT / "src/financial_brain").rglob("*.py"):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(token in source for token in ("def render", "def publish", "def emit", "def send_")):
            add(path, "signal_report_or_trader_renderer")
    for path in (ROOT / "src/financial_brain").rglob("*.py"):
        name = path.name.lower()
        if "ledger" in name or "journal" in name:
            add(path, "ledger_or_journal")
    return [{"capability_id": f"module:{path}", "path": path,
             "module_role": role, "module_owner": path} for path, role in sorted(rows.items())]


def build() -> dict:
    from financial_brain.forward_edge.strategy_inventory import build_strategy_inventory

    strategy_inventory = build_strategy_inventory(ROOT)
    strategy_candidates = [
        {"strategy_id": row.strategy_id, "status": row.eligibility, "strategy_class": row.strategy_class,
         "source_ref": row.source_ref, "source_sha256": row.source_sha256,
         "canonical_owner": "src/financial_brain/forward_edge/eligibility.py::build_catalogue"}
        for row in strategy_inventory.rows
    ]
    return {
        "schema": "fb.capability_ownership.v1",
        "scope": "Trader-core capabilities and implementation modules; human review routing is recorded separately in CODEOWNERS.",
        "generated_by": "scripts/capability_ownership.py",
        "capabilities": CAPABILITIES,
        "strategy_candidates": strategy_candidates,
        "strategy_catalogue_sha256": strategy_inventory.catalogue_sha256,
        "module_inventory": module_inventory(),
        "known_unresolved_boundaries": [
            "AlgorithmSpec and StrategySpec identities, hashes, clocks and promotion states are not synchronized by one registry.",
            "Provider adapters in providers/ingest are individually scoped; license/effective-use status remains governed by purpose-specific permission objects.",
            "Broker OMS, kernel simulated OMS, forward prospective paper OMS and legacy intraday paper OMS remain separate persistence/execution domains.",
            "Legacy paper books are not yet fully reconciled into canonical portfolio accounting; source gaps are explicitly unresolved.",
            "The repository-wide Python source inventory is broader than this trader-core map; generated module inventory is intentionally limited to named trader capability areas."
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    current = json.dumps(build(), indent=2, ensure_ascii=False) + "\n"
    if args.write:
        OUTPUT.write_text(current, encoding="utf-8", newline="\n")
        print(f"wrote {OUTPUT}")
        return 0
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != current:
            print("capability ownership inventory is stale")
            return 1
        print(f"capability ownership inventory verified ({len(CAPABILITIES)} contracts, {len(module_inventory())} modules)")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
