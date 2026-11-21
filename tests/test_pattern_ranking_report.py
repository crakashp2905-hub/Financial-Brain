"""Ranking, quality separation, trader mode, overlays and offline report (CP-0220..0233, CP-0250..0265)."""

from __future__ import annotations

import json

import pytest

from financial_brain.patterns.detectors import Formation
from financial_brain.patterns.ranking import (
    Opportunity,
    Scores,
    contrast,
    dedupe_by_symbol,
    filter_opportunities,
    net_cost_gate,
    rank,
    scenario_summary,
    similarity_clusters,
    trader_recommendations,
)
from financial_brain.patterns.report import (
    InvalidOverlay,
    Overlay,
    export_json,
    render_html,
    validate_export,
)


def _f(known: int, detector: str = "rectangle", pivots=(), conf=None) -> Formation:
    return Formation(detector, "1", known - 5, known, confirmed_index=conf,
                     confirmed_direction="bullish" if conf else None, geometry={"pivots": list(pivots)})


def _opp(symbol: str, known: int, fit: float, liq: float, dq: float = 1.0, evidence: str = "DESCRIPTIVE", conf=None) -> Opportunity:
    return Opportunity(symbol, _f(known, conf=conf), Scores(fit, dq, evidence), liq)


def test_scores_stay_separate_and_no_return_field() -> None:
    o = _opp("ABC", 50, 0.8, 0.9)
    assert o.scores.calibration == "not calibrated"
    assert not hasattr(o, "expected_return")
    assert not hasattr(o.scores, "expected_return")


def test_ranking_is_deterministic_and_penalises_missing_and_expired() -> None:
    a = _opp("A", 90, 0.5, 0.5)
    b = _opp("B", 95, 0.9, 0.9)
    stale = _opp("C", 10, 0.99, 0.99)
    missing = _opp("D", 96, 0.99, 0.99, dq=0.0)
    ranked = rank([stale, missing, a, b], now=100, max_age=30)
    order = [o.symbol for o, _ in ranked]
    assert order == ["B", "A", "D", "C"]  # clean rows first; penalised rows last
    codes = {o.symbol: c for o, c in ranked}
    assert "EXPIRED" in codes["C"] and "MISSING_DATA" in codes["D"]
    assert [o.symbol for o, _ in rank([b, a], now=100, max_age=30)] == [o.symbol for o, _ in rank([a, b], now=100, max_age=30)]


def test_dedupe_groups_evidence_per_symbol() -> None:
    ranked = rank([_opp("A", 90, 0.5, 0.5), _opp("A", 91, 0.6, 0.5, conf=93)], now=100, max_age=30)
    assert dedupe_by_symbol(ranked) == {"A": ["rectangle", "rectangle"]}


def test_correlated_shapes_cluster_together() -> None:
    fs = [_f(50, pivots=[10, 20]), _f(52, pivots=[20, 30]), _f(60, pivots=[70, 80])]
    assert similarity_clusters(fs) == [[0, 1], [2]]


def test_contrast_and_cost_gate_default_to_watch() -> None:
    assert contrast(0.02, 0.01)["difference"] == pytest.approx(0.01)
    assert net_cost_gate(None, 10) == "WATCH"
    assert net_cost_gate(5, 10) == "WATCH"
    assert net_cost_gate(20, 10) == "CANDIDATE_FOR_PAPER_REVIEW"


def test_scenarios_carry_no_probabilities() -> None:
    summary = scenario_summary("CANDIDATE")
    assert "not available" in summary["probabilities"]


def test_trader_mode_shows_all_but_recommends_only_above_descriptive() -> None:
    ranked = rank([_opp("A", 90, 0.5, 0.5), _opp("B", 91, 0.6, 0.6, evidence="HISTORICAL_REPLICATED")], now=100, max_age=30)
    assert len(filter_opportunities(ranked, mode="trader")) == 2
    recs = trader_recommendations(ranked)
    assert [o.symbol for o in recs] == ["B"]
    with pytest.raises(ValueError):
        filter_opportunities(ranked, mode="quant")


def test_overlay_validated_against_bars_and_known_from() -> None:
    Overlay("trendline", ((2, 10.0), (5, 11.0)), known_from=5, state="CONFIRMED").validate(10)
    with pytest.raises(InvalidOverlay):
        Overlay("trendline", ((2, 10.0), (9, 11.0)), known_from=5, state="CONFIRMED").validate(10)
    with pytest.raises(InvalidOverlay):
        Overlay("trendline", ((2, float("nan")),), known_from=5, state="TENTATIVE").validate(10)
    with pytest.raises(InvalidOverlay):
        Overlay("mystery", ((1, 1.0),), known_from=2, state="TENTATIVE").validate(10)


def test_html_escapes_hostile_symbols_and_always_shows_banners() -> None:
    rows = [{"symbol": "<script>alert(1)</script>", "detector": "rectangle", "state": "CANDIDATE",
             "research_evidence": "DESCRIPTIVE", "calibration": "not calibrated"}]
    page = render_html(rows, title="Radar <b>")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    assert "SIMULATION" in page and "RESEARCH ONLY" in page and "K7" in page
    assert "http://" not in page and "https://" not in page  # no external resources
    assert "@media" in page and "data-theme" in page


def test_export_round_trips_with_provenance() -> None:
    text = export_json([{"symbol": "A"}], provenance={"catalogue_sha256": "x", "code_commit": "y"})
    data = validate_export(text)
    assert data["rows"] == [{"symbol": "A"}]
    assert json.loads(text) == data
    with pytest.raises(ValueError):
        validate_export(json.dumps({"schema": "other", "provenance": {"a": "b"}}))
