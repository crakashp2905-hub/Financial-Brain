"""Final batch: formation-start clock, help terms, rejection text, guarded models, daily summary, evidence bundle,
fill coverage, intent restore, known-arithmetic scenarios and the offline policy (CP-0009, CP-0029, CP-0204,
CP-0246, CP-0250, CP-0302, CP-0310, CP-0343, CP-0386, CP-0390)."""

from __future__ import annotations

from pathlib import Path

import pytest

from financial_brain.patterns.agent import explain_rejection
from financial_brain.patterns.analytics import daily_summary
from financial_brain.patterns.detectors import Formation
from financial_brain.patterns.journal import (
    ClosedTrade,
    IntentLedger,
    dump_intents,
    load_intents,
    pnl_split,
)
from financial_brain.patterns.ops import model_output_or_none
from financial_brain.patterns.paper import (
    FillOutcome,
    PatternIntent,
    evidence_bundle,
    fill_coverage,
    simulate_fill,
)
from financial_brain.patterns.report import HELP
from financial_brain.patterns.spec import EventState, InvalidContract, PatternEvent

ROOT = Path(__file__).resolve().parents[1]


def _event(**over) -> PatternEvent:
    kw = dict(event_id="pev_f", spec_sha256="0" * 64, lineage="L", timeframe="1D",
              candidate_known_at_utc="2026-01-05T10:00:00+00:00", status=EventState.CANDIDATE, data_sha256="1" * 64)
    kw.update(over)
    return PatternEvent(**kw)


def test_formation_start_must_not_follow_candidate_knowledge() -> None:
    ok = _event(formation_start_utc="2026-01-02T10:00:00+00:00")
    assert ok.formation_start_utc.startswith("2026-01-02")
    with pytest.raises(InvalidContract, match="formation_start"):
        _event(formation_start_utc="2026-01-09T10:00:00+00:00")


def test_help_terms_match_the_glossary() -> None:
    glossary = (ROOT / "docs" / "PATTERNS-GLOSSARY.md").read_text(encoding="utf-8").lower()
    for term in ("throwback", "pivot confirmation", "ultimate-high bias", "candidate", "not calibrated"):
        assert term in HELP
        assert term in glossary


def test_rejection_codes_carry_text_and_never_authorise_a_trade() -> None:
    out = explain_rejection(["SYMBOL_LIMIT", "NOT_A_CODE"])
    assert out[0]["reason"].startswith("the symbol weight")
    assert out[1]["reason"] == "unrecognised rejection code"
    assert all(row["authorises_trade"] == "false" for row in out)


def test_unavailable_model_is_contained_and_reports_its_error() -> None:
    def broken() -> int:
        raise TimeoutError("model down")

    assert model_output_or_none(broken) == (None, "TimeoutError")
    assert model_output_or_none(lambda: 7) == (7, None)


def test_daily_summary_counts_only_that_bar() -> None:
    fs = [
        Formation("rectangle", "1", 0, 20, confirmed_index=22, confirmed_direction="bullish"),
        Formation("flag_bullish", "1", 0, 15, failed_index=20),
    ]
    summary = daily_summary(fs, day=20, no_trade_count=3)
    assert summary["new_shapes"] == 1 and summary["failures"] == 1 and summary["confirmations"] == 0
    assert summary["no_trade_events"] == 3
    assert "RESEARCH ONLY" in summary["labels"]


def _intent() -> PatternIntent:
    return PatternIntent(event_id="pev_1", spec_sha256="a" * 64, data_sha256="b" * 64, manifest_sha256="c" * 64,
                         symbol="ABC", side="long", quantity=10, decision_at_utc="2026-01-06T10:00:00+00:00",
                         earliest_entry_at_utc="2026-01-07T10:00:00+00:00", segment="cash_equity")


def test_evidence_bundle_links_spec_event_fill_and_ledger() -> None:
    costs = {"total": 0.5}
    bundle = evidence_bundle(_intent(), simulate_fill(FillOutcome.FILLED, 10), costs, ledger_sha256="d" * 64)
    assert bundle["spec_sha256"] == "a" * 64 and bundle["ledger_sha256"] == "d" * 64
    assert len(str(bundle["bundle_sha256"])) == 64


def test_fill_coverage_counts_assumed_labels_per_strategy_and_day() -> None:
    records = [
        {"strategy": "rect", "day": "d1", "fill_label": "ASSUMED"},
        {"strategy": "rect", "day": "d1", "fill_label": "ASSUMED"},
        {"strategy": "flag", "day": "d2", "fill_label": "OBSERVED"},
    ]
    cov = fill_coverage(records)
    assert cov["rect|d1"] == {"total": 2, "assumed": 2}
    assert cov["flag|d2"] == {"total": 1, "assumed": 0}


def test_intent_backup_restores_states_and_refuses_tampering() -> None:
    ledger = IntentLedger()
    ledger.set_state("i1", "PENDING")
    ledger.set_state("i2", "FULLY_FILLED")
    text, checksum = dump_intents(ledger)
    restored = load_intents(text, checksum)
    assert restored.states == ledger.states
    with pytest.raises(ValueError, match="checksum"):
        load_intents(text.replace("PENDING", "FULLY_FILLED"), checksum)


def test_known_arithmetic_profit_loss_and_flat_scenarios() -> None:
    closed = [
        ClosedTrade("P", entry=100.0, exit=110.0, quantity=10, costs=0.0),  # +100
        ClosedTrade("L", entry=100.0, exit=95.0, quantity=10, costs=0.0),   # -50
        ClosedTrade("F", entry=100.0, exit=100.0, quantity=10, costs=0.0),  # 0
    ]
    split = pnl_split(closed, {})
    assert split["realized"] == pytest.approx(50.0)
    assert split["unrealized"] == pytest.approx(0.0)
    assert [t.realized for t in closed] == pytest.approx([100.0, -50.0, 0.0])


def test_offline_policy_document_names_the_external_gate_categories() -> None:
    policy = ROOT / "docs" / "audits" / "pattern-dev-policy.md"
    text = policy.read_text(encoding="utf-8")
    for category in ("OWNER", "DATA_TIME", "LICENSE"):
        assert category in text
    assert "fixtures" in text.lower()
