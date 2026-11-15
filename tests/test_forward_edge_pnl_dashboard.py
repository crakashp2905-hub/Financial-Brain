from datetime import date

import pytest

from financial_brain.forward_edge.pnl_dashboard import (
    PnlDashboardError,
    build_pnl_dashboard,
    render_pnl_dashboard,
)
from financial_brain.forward_edge.strategy_attribution import (
    StrategyPnlComponents,
    attribute_strategy_pnl,
)


def _attribution(*, ledger_override=None):
    net = 100.0
    row = StrategyPnlComponents("swing-v1", date(2026, 11, 16), 150, 0, 0,
                                10, 15, 20, 5, 0,
                                net if ledger_override is None else ledger_override)
    return attribute_strategy_pnl(session=row.session, components=(row,),
                                  strategy_weights={"swing-v1": 1.0},
                                  covariance_matrix=((0.04,),))


def test_pnl_view_reconciles_components_to_ledger_and_shows_benchmark(tmp_path):
    report = build_pnl_dashboard(_attribution(), benchmark_net_pnl=70,
                                 benchmark_source_sha256="b" * 64)
    page = render_pnl_dashboard(report, tmp_path / "pnl.html").read_text(encoding="utf-8")
    assert "Calculated net" in page
    assert "Ledger net" in page
    assert "Risk contribution" in page
    assert "100.00" in page
    assert "70.00" in page
    assert "b" * 64 in page


def test_nonreconciled_or_unprovenanced_pnl_is_rejected():
    with pytest.raises(PnlDashboardError, match="does not reconcile"):
        build_pnl_dashboard(_attribution(ledger_override=101), benchmark_net_pnl=70,
                            benchmark_source_sha256="b" * 64)
    with pytest.raises(PnlDashboardError, match="SHA-256"):
        build_pnl_dashboard(_attribution(), benchmark_net_pnl=70,
                            benchmark_source_sha256="fixture")


def test_strategy_labels_are_escaped_in_pnl_page(tmp_path):
    row = StrategyPnlComponents("<script>", date(2026, 11, 16), 2, 0, 0,
                                0, 0, 0, 0, 0, 2)
    attribution = attribute_strategy_pnl(session=row.session, components=(row,),
                                         strategy_weights={"<script>": 1.0},
                                         covariance_matrix=((0.01,),))
    report = build_pnl_dashboard(attribution, benchmark_net_pnl=0,
                                 benchmark_source_sha256="c" * 64)
    page = render_pnl_dashboard(report, tmp_path / "pnl.html").read_text(encoding="utf-8")
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
