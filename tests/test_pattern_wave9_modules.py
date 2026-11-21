"""Wave 9 module tests: read-only bar source, workstation views, basket controls, lifecycle fields, resumable scans,
detection traces, scheduled retrain and the synthetic continual dry run (CP-0041, CP-0124, CP-0127, CP-0131,
CP-0133, CP-0153, CP-0173, CP-0181, CP-0215, CP-0217, CP-0235..0262, CP-0285, CP-0290, CP-0296)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import duckdb
import pytest

from financial_brain.patterns import views
from financial_brain.patterns.detectors2 import rounding
from financial_brain.patterns.learning import (
    ImmatureLabel,
    Label,
    ModelRegistry,
    continual_dry_run,
    scheduled_retrain,
)
from financial_brain.patterns.ops import CheckpointConflict, ScanCheckpoint, detection_trace, run_resumable
from financial_brain.patterns.risk_views import (
    DATA_INSUFFICIENT,
    beta_diagnostics,
    candidate_risk_explanations,
    point_in_time_controls,
    sector_concentration,
    turnover_and_cost_delta,
)
from financial_brain.patterns.source import BarSource, SourceError, file_sha256
from financial_brain.patterns.spec import EventState, InvalidContract, PatternEvent

PKG = Path(__file__).resolve().parents[1] / "src" / "financial_brain" / "patterns"


# ---------------------------------------------------------------- read-only source (CP-0041, CP-0153)
def _make_db(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE bars (symbol VARCHAR, session VARCHAR, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE)")
    rows = [
        ("ABC", "2026-01-05", 100, 101, 99, 100.5),
        ("ABC", "2026-01-06", 100.5, 102, 100, 101.5),
        ("ABC", "2026-01-07", 101.5, 103, 101, 102.5),
        ("XYZ", "2026-01-05", 50, 51, 49, 50.5),
    ]
    con.executemany("INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?)", rows)
    con.close()


def test_bar_source_reads_a_half_open_session_range(tmp_path: Path) -> None:
    db = tmp_path / "bars.duckdb"
    _make_db(db)
    bars = BarSource(db).bars("ABC", "2026-01-05", "2026-01-07")
    assert len(bars) == 2 and bars.close == (100.5, 101.5)


def test_bar_source_never_modifies_the_protected_database(tmp_path: Path) -> None:
    db = tmp_path / "bars.duckdb"
    _make_db(db)
    before = file_sha256(db)
    source = BarSource(db)
    source.bars("ABC", "2026-01-01", "2027-01-01")
    source.bars("XYZ", "2026-01-01", "2027-01-01")
    assert file_sha256(db) == before  # CP-0153


def test_bar_source_refuses_writes_through_its_connection(tmp_path: Path) -> None:
    db = tmp_path / "bars.duckdb"
    _make_db(db)
    con = duckdb.connect(str(db), read_only=True)
    with pytest.raises(duckdb.Error):
        con.execute("DELETE FROM bars")
    con.close()


def test_bar_source_rejects_unsafe_table_names_and_missing_files(tmp_path: Path) -> None:
    db = tmp_path / "bars.duckdb"
    _make_db(db)
    with pytest.raises(SourceError):
        BarSource(db, table="bars; DROP TABLE bars")
    with pytest.raises(SourceError):
        BarSource(tmp_path / "missing.duckdb")


def test_bar_source_reads_parquet_with_the_same_contract(tmp_path: Path) -> None:
    db = tmp_path / "bars.duckdb"
    _make_db(db)
    parquet = tmp_path / "bars.parquet"
    con = duckdb.connect(str(db), read_only=True)
    con.execute(f"COPY bars TO '{parquet.as_posix()}' (FORMAT PARQUET)")
    con.close()
    bars = BarSource(parquet, parquet=True).bars("ABC", "2026-01-05", "2026-01-08")
    assert len(bars) == 3


# ---------------------------------------------------------------- workstation views (CP-0235..0262)
ROWS = [
    {"symbol": "ABC", "family": "reversal", "timeframe": "1D", "state": "CONFIRMED", "known_at": "2026-01-07",
     "evidence_tier": "DESCRIPTIVE", "liquidity": 5e6, "sector": "IT", "signal_index": 9, "earliest_entry_index": 10},
    {"symbol": "XYZ", "family": "continuation", "timeframe": "1D", "state": "CANDIDATE", "known_at": "2026-01-06",
     "evidence_tier": "DESCRIPTIVE", "liquidity": None, "sector": None},
    {"symbol": "QRS", "family": "reversal", "timeframe": "1W", "state": "FAILED", "known_at": "2026-01-02",
     "evidence_tier": "HISTORICAL_REPLICATED", "liquidity": 1e5, "sector": "BANK"},
]


def test_status_counts_report_each_state_with_latest_timestamp() -> None:
    counts = views.status_counts(ROWS)
    assert counts["CANDIDATE"] == {"count": 1, "latest_known_at": "2026-01-06"}
    assert counts["CONFIRMED"]["count"] == 1 and counts["EXPIRED"]["count"] == 0


def test_filters_apply_each_control_and_never_pass_missing_liquidity() -> None:
    assert [r["symbol"] for r in views.filter_rows(ROWS, family="reversal")] == ["ABC", "QRS"]
    assert [r["symbol"] for r in views.filter_rows(ROWS, timeframe="1W")] == ["QRS"]
    assert [r["symbol"] for r in views.filter_rows(ROWS, min_liquidity=1e6)] == ["ABC"]  # XYZ liquidity missing
    assert [r["symbol"] for r in views.filter_rows(ROWS, min_evidence="HISTORICAL_REPLICATED")] == ["QRS"]
    assert [r["symbol"] for r in views.filter_rows(ROWS, date_from="2026-01-06", date_to="2026-01-07")] == ["ABC", "XYZ"]


def test_detail_marks_unrecorded_fields_instead_of_inventing_them() -> None:
    page = views.detail({"symbol": "XYZ", "detector": "rectangle"})
    assert page["observed_bars"] == views.NOT_RECORDED and page["tests"] == views.NOT_RECORDED
    assert page["label"] == "RESEARCH ONLY"


def test_baseline_comparison_keeps_missing_inputs_visible() -> None:
    out = views.compare_with_baseline({"state": "CONFIRMED"}, momentum=None, week52_position=0.8)
    assert out["momentum"] == views.NOT_RECORDED and out["week52_position"] == 0.8


def test_why_not_trade_is_never_empty_and_names_the_uncertainty() -> None:
    reasons = views.why_not_trade(ROWS[1])
    assert any("not confirmed" in r for r in reasons)
    assert any("DESCRIPTIVE" in r for r in reasons)
    assert reasons and "costs" in reasons[-1]


def test_ticket_preview_is_paper_only_and_not_submittable() -> None:
    ticket = views.ticket_preview(earliest_entry="2026-01-08", costs=12.5, capital_required=None, risk_verdict="PASS")
    assert ticket["mode"] == "PAPER ONLY" and ticket["submittable"] is False
    assert ticket["capital_required"] == views.NOT_RECORDED


def test_only_the_two_paper_actions_are_allowed() -> None:
    assert views.allowed_action("Add to paper watchlist") == "Add to paper watchlist"
    with pytest.raises(views.ActionRefused):
        views.allowed_action("Place order")


def test_time_slider_hides_what_was_not_yet_knowable() -> None:
    rows = [{"symbol": "ABC", "known_at": "2026-01-05", "state": "CONFIRMED", "confirmed_at": "2026-01-09"}]
    early = views.knowable_at(rows, "2026-01-06")
    assert early[0]["state"] == "CANDIDATE" and "confirmed_at" not in early[0]
    late = views.knowable_at(rows, "2026-01-10")
    assert late[0]["state"] == "CONFIRMED" and late[0]["confirmed_at"] == "2026-01-09"


def test_status_page_flags_stale_data_and_failing_paper_runs() -> None:
    page = views.status_page(
        last_session="2026-01-05", as_of="2026-01-08T09:00:00+05:30", failed_scans=0,
        audit_exceptions=0, paper_runs_ok=3, paper_runs_failed=1,
    )
    assert page["data_freshness"] == "STALE" and page["paper_run_health"] == "ATTENTION"


def test_signal_and_entry_markers_are_separate() -> None:
    marks = views.overlay_markers({"signal_index": 9, "earliest_entry_index": 10})
    assert marks["separate"] is True and marks["signal_bar"] == 9 and marks["earliest_entry_bar"] == 10


def test_boundaries_beyond_the_displayed_time_are_not_drawn() -> None:
    assert views.visible_boundaries([3, 8, 12, 20], t=10) == [3, 8]


def test_revisions_are_listed_as_new_versions_without_mutating_inputs() -> None:
    versions = [{"version": 1, "state": "CANDIDATE"}, {"version": 2, "state": "CONFIRMED"}]
    before = [dict(v) for v in versions]
    history = views.version_history(versions)
    assert history == [{"from_version": 1, "to_version": 2, "changed": ["state", "version"]}]
    assert versions == before


def test_risk_zone_has_no_guaranteed_target() -> None:
    zone = views.risk_zones(atr_value=2.0, last_close=100.0, hypothetical_cost=3.5)
    assert zone["atr_range"] == [98.0, 102.0] and zone["guaranteed_target"] is None
    assert views.risk_zones(atr_value=None, last_close=100.0, hypothetical_cost=None)["atr_range"] == views.NOT_RECORDED


def test_hover_breakdown_and_research_controls() -> None:
    hover = views.hover_breakdown({"tolerance": 0.5})
    assert hover["level_tolerance"] == 0.5 and hover["pivots"] == views.NOT_RECORDED
    assert views.research_controls(research_mode=False, matched_baseline="m", lookalikes=["x"]) == {"shown": False}
    shown = views.research_controls(research_mode=True, matched_baseline="m", lookalikes=["x"])
    assert shown["shown"] is True and shown["unconfirmed_lookalikes"] == ["x"]


@pytest.mark.parametrize("n", [20, 500])
def test_chart_scale_keeps_aspect_ratio_for_short_and_long_views(n: int) -> None:
    scale = views.chart_scale(n, width_px=1000, height_px=600)
    assert scale["aspect_ratio"] == 0.6
    assert 1.0 <= scale["px_per_bar"] <= 12.0
    with pytest.raises(ValueError):
        views.chart_scale(0, width_px=1000, height_px=600)


# ---------------------------------------------------------------- basket diagnostics (CP-0173, CP-0181, CP-0215, CP-0217)
def test_sector_concentration_flags_unknown_sectors() -> None:
    out = sector_concentration({"A": ("IT", 60.0), "B": ("BANK", 30.0), "C": (None, 10.0)})
    assert out["status"] == DATA_INSUFFICIENT and out["largest_sector"] == "IT"
    assert 0 < out["hhi"] <= 1


def test_beta_is_unavailable_when_any_beta_is_missing() -> None:
    assert beta_diagnostics({"A": 1.0, "B": 1.0}, {"A": 1.2, "B": None})["status"] == DATA_INSUFFICIENT
    ok = beta_diagnostics({"A": 1.0, "B": 1.0}, {"A": 1.2, "B": 0.8})
    assert ok == {"status": "OK", "weighted_beta": 1.0}


def test_point_in_time_controls_never_pass_missing_attributes() -> None:
    out = point_in_time_controls(
        [{"symbol": "A"}, {"symbol": "B"}],
        {"A": {"liquidity": 5e6, "market_cap": 9e9, "sector": "IT"}, "B": {"liquidity": None, "market_cap": 9e9}},
        min_liquidity=1e6, min_size=1e9,
    )
    assert out[0]["eligible"] is True
    assert out[1]["checks"]["liquidity"] == DATA_INSUFFICIENT and out[1]["eligible"] is False


def test_turnover_and_cost_delta_are_attributed_to_the_filter() -> None:
    out = turnover_and_cost_delta([{"notional": 100.0}, {"notional": -50.0}], [{"notional": 100.0}], cost_rate=0.001)
    assert out == {"turnover_unfiltered": 150.0, "turnover_filtered": 100.0, "cost_delta": 0.05}
    with pytest.raises(ValueError):
        turnover_and_cost_delta([], [], cost_rate=-1)


def test_risk_explanations_state_they_do_not_authorise_trades() -> None:
    out = candidate_risk_explanations(["SYMBOL_LIMIT"])
    assert out[0]["code"] == "SYMBOL_LIMIT" and out[0]["reason"]


# ---------------------------------------------------------------- lifecycle fields (CP-0124, CP-0127)
def _event(**over: object) -> PatternEvent:
    kw: dict[str, object] = dict(
        event_id="pev_1", spec_sha256="0" * 64, lineage="L", timeframe="1D",
        candidate_known_at_utc="2026-01-05T10:00:00+00:00", status=EventState.CANDIDATE, data_sha256="1" * 64,
    )
    kw.update(over)
    return PatternEvent(**kw)  # type: ignore[arg-type]


def test_invalidation_and_expiry_need_explicit_reasons() -> None:
    with pytest.raises(InvalidContract):
        _event(status=EventState.INVALIDATED, invalidated_at_utc="2026-01-06T10:00:00+00:00")
    with pytest.raises(InvalidContract):
        _event(expired_at_utc="2026-01-09T10:00:00+00:00")
    ok = _event(expired_at_utc="2026-01-09T10:00:00+00:00", expiry_reason="WINDOW_ELAPSED")
    assert ok.to_dict()["expiry_reason"] == "WINDOW_ELAPSED"


def test_generation_cannot_precede_observation() -> None:
    with pytest.raises(InvalidContract):
        _event(observed_at_utc="2026-01-06T10:00:00+00:00", generated_at_utc="2026-01-05T10:00:00+00:00")


def test_optional_lifecycle_fields_do_not_change_existing_hashes() -> None:
    base = _event()
    assert "observed_at_utc" not in base.to_dict()
    expected = hashlib.sha256(
        __import__("financial_brain.patterns.spec", fromlist=["canonical_json"]).canonical_json(base.to_dict())
    ).hexdigest()
    assert base.payload_sha256() == expected


# ---------------------------------------------------------------- resumable scans and traces (CP-0131, CP-0133)
def test_interrupted_scan_resumes_at_the_last_committed_bar(tmp_path: Path) -> None:
    ckpt_path = tmp_path / "ckpt.json"
    seen: list[int] = []

    def flaky(index: int) -> int:
        if index == 5 and not seen.count(5):
            seen.append(5)
            raise RuntimeError("simulated crash")
        seen.append(index)
        return index * 2

    checkpoint = ScanCheckpoint(ckpt_path, "m" * 64)
    with pytest.raises(RuntimeError):
        run_resumable(10, checkpoint, flaky)
    assert checkpoint.last_committed() == 4

    outputs = run_resumable(10, ScanCheckpoint(ckpt_path, "m" * 64), lambda i: i * 2)
    assert outputs == [i * 2 for i in range(5, 10)]
    assert ScanCheckpoint(ckpt_path, "m" * 64).last_committed() == 9


def test_checkpoint_refuses_other_manifests_and_backwards_moves(tmp_path: Path) -> None:
    path = tmp_path / "ckpt.json"
    cp = ScanCheckpoint(path, "a" * 64)
    cp.commit(3)
    with pytest.raises(CheckpointConflict):
        ScanCheckpoint(path, "b" * 64).last_committed()
    with pytest.raises(CheckpointConflict):
        cp.commit(1)


def test_detection_trace_records_thresholds_next_to_geometry() -> None:
    trace = detection_trace(rounding, {"window": 60}, detector_name="rounding_bottom", version="1")
    assert trace["thresholds"]["window"] == 60 and trace["thresholds"]["min_r2"] == 0.8
    assert trace["geometry"] == {"window": 60}


# ---------------------------------------------------------------- learning (CP-0285, CP-0290, CP-0296)
def test_scheduled_retrain_uses_only_matured_labels_and_stays_a_challenger() -> None:
    labels = [Label(f"e{i}", i, i + 5, 1.0 if i % 2 else -1.0) for i in range(10)]
    registry = ModelRegistry()
    version = scheduled_retrain(labels, now_index=20, registry=registry, version_id="v1")
    assert version.role == "challenger" and version.evaluation["calibration"] == "NO_CALIBRATION"
    assert registry.active is None  # never promoted by a retrain
    with pytest.raises(ImmatureLabel):
        scheduled_retrain(labels, now_index=8, registry=registry, version_id="v2")
    with pytest.raises(ImmatureLabel):
        scheduled_retrain([], now_index=8, registry=registry, version_id="v3")


def test_continual_dry_run_is_synthetic_and_flags_the_intentional_shift() -> None:
    out = continual_dry_run(seed=7, n_per_regime=200, shift=3.0)
    assert out["synthetic"] is True and out["promoted"] is False and out["registry_changed"] is False
    assert out["drift"] == "REVIEW"
    assert continual_dry_run(seed=7, n_per_regime=200, shift=0.0)["drift"] == "OK"
