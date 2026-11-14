"""The loop audits itself: the null really has no effect, a planted effect is the size it says, and the audit reports sane numbers.
Also the lessons ledger: append-only, idempotent, folded the same way everywhere."""
from __future__ import annotations

import numpy as np
import pytest

from financial_brain.learning import calibrate as C
from financial_brain.learning import features as F
from financial_brain.learning.store import Store, StoreError, fold


def test_the_null_has_no_effect_in_any_cell_and_keeps_the_ledgers_structure():
    led = C.synthetic_ledger(60, seed=2, plant=("vs_market_today", "against the market", 80.0))
    nul = C.null_outcomes(led, np.random.default_rng(1))
    assert nul["net_bp"].mean() == pytest.approx(0.0, abs=2.0)
    assert (nul[["day", "strategy", "vs_market_today"]].to_numpy() == led[["day", "strategy", "vs_market_today"]].to_numpy()).all()
    a = nul[nul["vs_market_today"] == "against the market"]["net_bp"].mean()
    b = nul[nul["vs_market_today"] == "with the market"]["net_bp"].mean()
    assert abs(a - b) < 6.0                                            # the planted 80 bp is gone
    for _day, g in nul.groupby("day"):                                  # within a day outcomes are only re-ordered and sign-flipped
        orig = led.loc[g.index, "net_bp"] - led["net_bp"].mean()
        assert np.allclose(sorted(np.abs(g["net_bp"])), sorted(np.abs(orig)))


def test_a_planted_effect_is_the_size_it_says_and_only_in_its_cell():
    led = C.synthetic_ledger(40, seed=3)
    out = C.plant_effect(led, "vs_market_today", "against the market", 50.0, "ORB")
    m = (led["vs_market_today"] == "against the market") & (led["strategy"] == "ORB")
    assert ((out.loc[m, "net_bp"] - led.loc[m, "net_bp"]).round(6) == -50.0).all()
    assert (out.loc[~m, "net_bp"] == led.loc[~m, "net_bp"]).all()


def test_resampling_builds_the_requested_number_of_dated_sessions_from_real_days():
    led = C.synthetic_ledger(20, seed=4)
    h = C.resample_history(led, 55, np.random.default_rng(2))
    assert h["day"].nunique() == 55 and h["day"].min() == "2027-01-04" and h["trade_id"].is_unique
    assert set(h.groupby("day").size()) <= set(led.groupby("day").size())


def test_the_audit_reports_rates_in_range_and_finds_a_big_planted_effect(monkeypatch):
    led = C.synthetic_ledger(40, seed=5)
    r = C.calibrate(led, draws=4, n_sessions=45, effects=(60.0,), seed=3)
    assert r["draws"] == 4 and 0 <= r["null"]["false_confirmation_rate"] <= 1
    p = r["power"][60.0]
    assert p["nominated"] >= 0.75 and p["confirmed"] >= 0.5 and p["median_session_confirmed"] >= 12 + 20
    text = C.render(r)
    assert "NO real effect" in text and "-60 bp" in text


# ------------------------------------------------------------------------------------------------ the ledger

def test_events_are_append_only_unique_per_lesson_kind_and_session():
    with Store(":memory:") as st:
        assert st.append("L-1", "NOMINATED", session="s1", payload={"cell": "c"}) is True
        assert st.append("L-1", "NOMINATED", session="s1", payload={"cell": "different"}) is False       # ignored, not replaced
        assert st.append("L-1", "NOMINATED", session="s2", payload={"cell": "c"}) is True
        assert [e["payload"]["cell"] for e in st.events("L-1")] == ["c", "c"]
        with pytest.raises(StoreError):
            st.append("L-1", "MADE_UP")


def test_a_lessons_stage_is_folded_from_its_events_and_notes_do_not_move_it():
    ev = [{"lesson_id": "L-1", "kind": k, "session": s, "payload": p} for k, s, p in (
        ("NOMINATED", "d0", {"cell": "ORB|x|y", "d0": "d0"}), ("CONFIRMED", "d1", {"p": 0.01}), ("DRAFTED", "x", {}),
        ("REGISTERED", "SK-1", {"cell": "ORB|x|y"}), ("SHADOW", "SK-1", {}), ("SHADOW_RESULT", "d2:SK-1", {"rule": "SK-1", "status": "COLLECTING"}),
        ("DECAYING", "d3", {"p": 0.9}))]
    x = fold(ev)["L-1"]
    assert x["stage"] == "SHADOW" and x["cell"] == "ORB|x|y" and x["decision"]["kind"] == "CONFIRMED"
    assert x["shadow"]["status"] == "COLLECTING" and x["decaying"]["p"] == 0.9
    assert fold(ev[:3])["L-1"]["stage"] == "CONFIRMED"                  # DRAFTED is an annotation, not a stage


def test_a_read_only_store_cannot_write_and_a_file_store_persists(tmp_path):
    p = tmp_path / "l.duckdb"
    with Store(p) as st:
        st.append("L-1", "NOMINATED", session="s", payload={"cell": "c"})
    with Store(p, read_only=True) as st:
        assert len(st.events()) == 1
        with pytest.raises(Exception):                                   # noqa: B017 - DuckDB's read-only error type varies by version
            st.append("L-1", "CONFIRMED", session="s2")


def test_feature_families_cover_every_dimension_once():
    fams = {}
    for name, d in F.DIMENSIONS.items():
        fams.setdefault(d.family, []).append(name)
    assert set(fams) == set(F.FAMILIES) and sum(len(v) for v in fams.values()) == len(F.DIMENSIONS)
