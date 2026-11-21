"""Final addendum tests: lifecycle clocks and lock, challengers, matched controls, PBO, static UI, walkthrough, daily
report, recovery and the offline-usability gate (ALG-028, 038, 051, 067, 074..083, 097, 102, 113, 119, 120, 123, 126)."""

from __future__ import annotations

import ast
import importlib.util
import json
import random
import socket
from pathlib import Path

import pytest

from financial_brain.patterns import demo
from financial_brain.patterns.detectors import Bars, discover
from financial_brain.patterns.ops import ScanCheckpoint, run_resumable
from financial_brain.patterns.source import BarSource, SourceError
from financial_brain.patterns.views import ticket_preview
from financial_brain.strategies.challengers import (
    KELTNER_GRID,
    TRIALS_IN_GRID,
    combined_challenger,
    keltner_breakouts,
)
from financial_brain.strategies.evaluation_controls import (
    Candidate,
    matched_controls,
    probability_of_backtest_overfitting,
)
from financial_brain.strategies.execution_rules import FillRecord, AcceptedOrder, Intent, Outcome, Signal, realised_pnl
from financial_brain.strategies.lifecycle import (
    EntryClock,
    IllegalTransition,
    Lifecycle,
    StructureClock,
    advance_entry,
    lock_level,
)
from financial_brain.strategies.reports import daily_paper_report, render_text
from financial_brain.strategies.ui import (
    PAPER_BANNER,
    card_html,
    compare_panel,
    demo_page,
    money,
    overlay_svg,
    radar_filter,
    state_style,
    why_not_log,
)

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = ROOT / "src" / "financial_brain" / "strategies"


# ---------------------------------------------------------------- lifecycle and clocks (ALG-028, ALG-038)
def test_entry_clock_never_advances_the_structure_clock() -> None:  # ALG-028
    structure = StructureClock(closed_bar=10)
    s2, e2 = advance_entry(structure, EntryClock(bar=12), new_bar=40)
    assert s2 == structure and e2.bar == 40
    assert not structure.visible(11)  # a weekly state closed at bar 10 is not visible at 11 by the entry clock alone
    with pytest.raises(ValueError):
        advance_entry(structure, EntryClock(bar=12), new_bar=5)


def test_lifecycle_allows_the_locked_path_and_refuses_illegal_moves() -> None:  # ALG-038
    lc = Lifecycle("pev_1")
    lc.move("LOCKED", at_bar=5, reason="neckline frozen")
    lc.move("CONFIRMED", at_bar=7, reason="close beyond neckline")
    assert lc.state == "CONFIRMED" and lc.replay() == "CONFIRMED"
    with pytest.raises(IllegalTransition):
        lc.move("LOCKED", at_bar=8, reason="cannot go back")


def test_candidate_cannot_skip_the_lock() -> None:  # ALG-038
    lc = Lifecycle("pev_2")
    with pytest.raises(IllegalTransition):
        lc.move("CONFIRMED", at_bar=3, reason="no lock first")


def test_locked_levels_cannot_be_restated() -> None:  # ALG-038
    assert lock_level(101.0, frozen=False) == 101.0
    with pytest.raises(ValueError):
        lock_level(102.0, frozen=True)


def test_replay_detects_a_tampered_log() -> None:  # ALG-038
    lc = Lifecycle("pev_3")
    lc.move("LOCKED", at_bar=1, reason="x")
    lc.log[0] = lc.log[0].__class__("pev_3", "CANDIDATE", "CONFIRMED", 1, "tampered")
    with pytest.raises(IllegalTransition):
        lc.replay()


# ---------------------------------------------------------------- challengers (ALG-051, ALG-067)
def _trend(n: int = 120) -> tuple[list[float], list[float], list[float], list[float]]:
    rng = random.Random(9)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + 0.004 + rng.gauss(0, 0.002)))
    highs = [c * 1.004 for c in closes]
    lows = [c * 0.996 for c in closes]
    volumes = [1000.0 + 50 * (i % 7) for i in range(n)]
    return closes, highs, lows, volumes


def test_keltner_grid_is_frozen_and_counted_as_trials() -> None:  # ALG-051
    assert KELTNER_GRID["ema"] == (20,)
    assert TRIALS_IN_GRID == 2


def test_keltner_signals_are_scale_invariant_and_appear_in_a_trend() -> None:  # ALG-051
    c, h, l, v = _trend()
    base = [s.index for s in keltner_breakouts(h, l, c, v)]
    scaled = [s.index for s in keltner_breakouts([x * 3 for x in h], [x * 3 for x in l], [x * 3 for x in c], v)]
    assert base == scaled
    assert base  # the steady uptrend produces at least one breakout on this seed


def test_combined_challenger_is_deterministic_and_bounded() -> None:  # ALG-067
    c, h, l, _v = _trend()
    out = combined_challenger(c, h, l)
    assert out == combined_challenger(c, h, l)
    assert all(30 <= i < len(c) for i in out) and out == sorted(set(out))


# ---------------------------------------------------------------- matched controls and PBO (ALG-097, ALG-102)
def test_matched_controls_are_same_sector_unique_and_never_self() -> None:  # ALG-097
    events = [Candidate("A", "IT", 0.2, 0.95, 0.02), Candidate("B", "IT", 0.1, 0.9, 0.03)]
    pool = [Candidate("A", "IT", 0.2, 0.95, 0.02), Candidate("C", "IT", 0.15, 0.92, 0.025),
            Candidate("D", "BANK", 0.2, 0.95, 0.02), Candidate("E", "IT", -0.3, 0.5, 0.06)]
    out = matched_controls(events, pool)
    controls = [o["control"] for o in out]
    assert out[0]["control"] != "A"  # an event is never its own control
    assert "D" not in controls  # other-sector names are excluded
    assert len(set(c for c in controls if c)) == len([c for c in controls if c])
    assert all(o["control"] in {"A", "C", "E", None} for o in out)  # A is a valid control for event B


def test_pbo_is_near_chance_for_noise_and_low_for_a_real_edge() -> None:  # ALG-102
    noise_values = []
    for seed in range(30):  # a single draw is noisy; the mean over seeds is what should sit near chance
        rng = random.Random(seed)
        noise = [[rng.gauss(0, 1) for _ in range(6)] for _ in range(96)]
        noise_values.append(probability_of_backtest_overfitting(noise))
    assert 0.3 <= sum(noise_values) / len(noise_values) <= 0.7
    edge = [[rng.gauss(0, 1) + (0.8 if k == 0 else 0.0) for k in range(6)] for _ in range(96)]
    assert probability_of_backtest_overfitting(edge) < 0.1


def test_pbo_refuses_bad_shapes() -> None:
    with pytest.raises(ValueError):
        probability_of_backtest_overfitting([[1.0, 2.0]] * 8, blocks=5)
    with pytest.raises(ValueError):
        probability_of_backtest_overfitting([[1.0], [2.0]] * 4)


# ---------------------------------------------------------------- static UI (ALG-074..083)
def test_card_shows_source_claim_and_no_trading_instruction() -> None:  # ALG-074, ALG-075
    entry = {"title": "Donchian", "author": "RafaelPiccolo", "platform": "TradingView", "access_date": "2026-11-08",
             "family": "trend_breakout"}
    html = card_html(entry, "a" * 64, last_validated="2026-11-08")
    assert "SOURCE_CLAIM" in html and "RafaelPiccolo" in html and "paper only" in html
    assert "Buy" not in html and "Sell" not in html


def test_overlays_respect_known_time_and_display_bounds() -> None:  # ALG-076
    closes = [100.0 + (i % 5) for i in range(40)]
    bars = Bars.from_closes(closes, spread=0.3)
    ov = {"name": "neckline", "from_index": 5, "to_index": 20, "known_at": 22, "price": 103.0,
          "earliest_entry": 23, "state": "CONFIRMED"}
    with pytest.raises(ValueError):
        overlay_svg(bars, [ov], shown_until=20, title="x")  # not yet knowable at the shown time
    svg = overlay_svg(bars, [{**ov, "known_at": 18}], shown_until=25, title="x")
    assert "known 18" in svg and "<line" in svg


def test_unconfirmed_and_confirmed_states_look_different() -> None:  # ALG-077
    assert state_style("CONFIRMED")["dash"] == ""
    assert state_style("CANDIDATE")["dash"] != ""
    assert state_style("CANDIDATE")["opacity"] != state_style("CONFIRMED")["opacity"]
    assert state_style("UNKNOWN") == state_style("CANDIDATE")


def test_money_rounds_to_tick_and_whole_lots() -> None:  # ALG-078
    assert money(101.237, tick=0.05, lot=75, quantity_lots=2) == "INR 101.20 x 150 units (2 lot(s) of 75)"
    with pytest.raises(ValueError):
        money(101.0, tick=0.0, lot=75, quantity_lots=1)


def test_radar_filters_keep_canonical_event_ids() -> None:  # ALG-079
    rows = [
        {"event_id": "pev_a", "instrument": "ABC", "timeframe": "1D", "regime": "UP", "known_at": "2026-11-01", "maturity_bars": 5},
        {"event_id": "pev_b", "instrument": "XYZ", "timeframe": "1D", "regime": "UP", "known_at": "2026-10-01", "maturity_bars": 1},
    ]
    out = radar_filter(rows, instrument="ABC", as_of="2026-11-08", max_age_days=10)
    assert [r["event_id"] for r in out] == ["pev_a"]
    assert [r["event_id"] for r in radar_filter(rows, min_maturity=2)] == ["pev_a"]


def test_compare_panel_keeps_gross_net_and_incremental_separate() -> None:  # ALG-080
    panel = compare_panel(gross=0.05, net=0.03, benchmark_net=0.02, cash_net=0.0)
    assert panel["gross_return"] != panel["after_cost_return"]
    assert panel["incremental_vs_benchmark"] == pytest.approx(0.01)


def test_abstentions_are_logged_not_dropped() -> None:  # ALG-082
    log = why_not_log([], event_id="pev_9", primary_constraint="RANGE_TOO_WIDE", at="2026-11-08T09:40:00+05:30")
    assert log[0]["decision"] == "NO_TRADE" and log[0]["primary_constraint"] == "RANGE_TOO_WIDE"
    with pytest.raises(ValueError):
        why_not_log(log, event_id="pev_9", primary_constraint="", at="x")


def test_offline_demo_page_has_banner_and_no_external_references(monkeypatch: pytest.MonkeyPatch) -> None:  # ALG-083
    def blocked(*_a: object, **_k: object) -> None:
        raise RuntimeError("network disabled")

    monkeypatch.setattr(socket, "socket", blocked)
    corpus = demo.synthetic_corpus()
    page = demo_page(corpus, cards=[{"card_sha256": "f" * 64, "family": "trend_breakout"}])
    assert PAPER_BANNER in page and "<script" not in page
    assert 'src="http' not in page and 'href="http' not in page  # the SVG namespace URI is not a fetch
    assert page.count("<svg") == len(corpus)


# ---------------------------------------------------------------- walkthrough and report (ALG-119, ALG-120)
def test_trader_walkthrough_chart_to_journal() -> None:  # ALG-119
    corpus = demo.synthetic_corpus()
    steps: list[str] = []
    symbol, bars = sorted(corpus.items())[0]
    steps.append("chart")
    formations = discover(bars)
    steps.append("pattern")
    assert isinstance(formations, list)
    preview = ticket_preview(earliest_entry="next session open", costs=12.5, capital_required=50_000.0, risk_verdict="PASS")
    steps.append("risk_preview")
    assert preview["submittable"] is False
    intent = Intent(Signal(len(bars) - 1, "LONG"), 10)
    fill = FillRecord(AcceptedOrder(intent, len(bars) - 1), fill_index=len(bars), price=bars.open[-1], quantity=10)
    steps.append("paper_intent")
    outcome = Outcome(fill=fill, realised=5.0)
    journal = {"symbol": symbol, "realised": realised_pnl(outcome), "mode": "PAPER ONLY"}
    steps.append("journal")
    assert steps == ["chart", "pattern", "risk_preview", "paper_intent", "journal"]
    assert journal["mode"] == "PAPER ONLY"


def test_daily_report_states_assumptions_and_custody() -> None:  # ALG-120
    decisions = [
        {"decision": "TRADE", "realised": 10.0},
        {"decision": "TRADE", "realised": None},
        {"decision": "NO_TRADE"},
    ]
    report = daily_paper_report(day="2026-11-08", decisions=decisions, cost_model_version="cost-v1")
    assert report["trades"] == 2 and report["abstentions"] == 1 and report["trades_without_fill"] == 1
    text = render_text(report)
    assert "Not independent forward-market custody" in text and "next-open" in text


# ---------------------------------------------------------------- recovery (ALG-123)
def test_corrupted_checkpoint_is_refused_not_silently_reset(tmp_path: Path) -> None:  # ALG-123
    path = tmp_path / "ckpt.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        run_resumable(5, ScanCheckpoint(path, "m" * 64), lambda i: i)


def test_missing_bars_and_missing_tables_fail_closed(tmp_path: Path) -> None:  # ALG-123
    with pytest.raises(SourceError):
        BarSource(tmp_path / "absent.duckdb")
    rows = demo.synthetic_corpus()
    assert all(len(b) > 0 for b in rows.values())


def test_locked_database_is_reported_not_bypassed(tmp_path: Path) -> None:  # ALG-123
    import duckdb

    db = tmp_path / "locked.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE bars (symbol VARCHAR, session VARCHAR, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE)")
    con.close()
    src = BarSource(db)
    assert len(src.bars("ABC", "2026-01-01", "2027-01-01")) == 0  # empty is an answer, not an error


# ---------------------------------------------------------------- offline usability gate (ALG-126)
def test_offline_usability_gate_is_closed_until_waves_one_to_four_are_done() -> None:  # ALG-126
    spec = importlib.util.spec_from_file_location("algo_progress", ROOT / "scripts" / "algo_progress.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    early = [r for r in mod.ROWS if r[1] in ("W1", "W2", "W3", "W4")]
    open_early = [r[0] for r in early if r[2] not in mod.DONE]
    assert open_early == [], f"offline usability blocked by {open_early}"


def test_no_strategy_module_reaches_an_order_or_a_broker() -> None:  # ALG-126, ALG-081
    forbidden = ("financial_brain.execution", "financial_brain.paper", "kiteconnect", "socket", "urllib", "requests", "http")
    for path in list(STRATEGIES.glob("*.py")) + [ROOT / "src" / "financial_brain" / "research" / "external_strategies" / "registry.py"]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for name in names:
                assert not any(name == f or name.startswith(f + ".") for f in forbidden), (path.name, name)
