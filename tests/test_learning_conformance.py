"""Conformance: the live-v2 cohort is exactly live-v1 minus the entries a skip rule declines.

Three things must agree, and nothing but this test ties them together:

1. the **vector** labelling of closed trades (what the loss review and the shadow counterfactual use),
2. the **scalar** decision-time values (what the engine's entry filter uses), and
3. the engine itself, with and without a filter.

If a rule's dimension were computed differently at decision time than when the trades were labelled, v2 would decline different
entries than the shadow claimed, and every shadow verdict would be fiction. So: run the same simulated sessions through v1 and through
v2, and require that v2's trades are v1's trades minus those whose recorded labels match the rule - identical in every field.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, time, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from financial_brain.learning import activation as AC
from financial_brain.learning import features as F
from financial_brain.learning import shadow as SH
from financial_brain.paper import intraday as I

IST = timezone(timedelta(hours=5, minutes=30))
DAY_CONTEXT = {"regime_prev": "RISK_OFF", "vix": 17.5}
_CACHE: dict = {}


def simulate(seed: int, *, rules=None, day_context=None, account=None, day="2026-11-02", names: int = 50):
    rng = np.random.default_rng(seed)
    kw = {"rules": rules} if rules else {}
    if account:
        kw["account"] = account
    eng = I.DayEngine(day, **kw)
    eng.daily_up = bool(rng.integers(0, 2))
    eng.day_context = dict(day_context or {})
    syms = [f"S{i}" for i in range(names)]
    px = {}
    for s in syms:
        pc = float(rng.uniform(100, 3000))
        gap = rng.choice([0, 0, 0.02, -0.02, 0.005, -0.004]) + rng.normal(0, .002)
        op = pc * (1 + gap)
        width = abs(rng.normal(0.01, 0.005)) * op + 0.1
        eng.add_symbol(s, pc, bucket=str(rng.choice(["mega", "large", "mid"])))
        eng.set_opening(s, open_px=op, or_hi=op + width * 0.6, or_lo=op - width * 0.4)
        px[s] = op
        eng.set_market(s, vwap=op * (1 + rng.normal(0, .003)))
    t = datetime.fromisoformat(f"{day}T09:30:00").replace(tzinfo=IST)
    drift = {s: rng.normal(0, 0.00006) for s in syms}
    quoted = {s: bool(rng.random() < 0.6) for s in syms}
    spread_k = {s: int(rng.integers(1, 4)) for s in syms}
    while t.time() <= time(15, 20):
        for s in syms:
            px[s] *= 1 + drift[s] + rng.normal(0, 0.0008)
            if quoted[s]:
                eng.on_price(s, t, px[s], bid=px[s] * (1 - 0.0003), ask=px[s] * (1 + 0.0004 * spread_k[s]))
            else:
                eng.on_price(s, t, px[s])
        t += timedelta(seconds=20)
    eng.end_of_day(t, px)
    return eng


def v1(seed, **kw):
    k = (seed, tuple(sorted(kw.items())))
    if k not in _CACHE:
        _CACHE[k] = simulate(seed, day_context=DAY_CONTEXT, **kw)
    return _CACHE[k]


def frame(eng, day_context=None) -> pd.DataFrame:
    df = pd.DataFrame([dataclasses.asdict(x) for x in eng.trades])
    if df.empty:
        return df
    df["mode"] = "live"
    df["daily_up"] = df["daily_up"].astype("boolean")
    for k, v in (day_context or {}).items():
        df[k] = v
    return F.label(df)


def rule(dimension, value, strategy="ORB", rid="SK-0001"):
    return SH.SkipRule(id=rid, strategy=strategy, dimension=dimension, value=value, prereg="PRE-REGISTERED x",
                       active_from="2026-01-01", status="ACTIVE")


def v2(seed, rules, **kw):
    return simulate(seed, rules=AC.build_engine_rules(rules), day_context=DAY_CONTEXT, **kw)


def key(t):
    return (t.strategy, t.symbol)


SEEDS = (1, 2)


def _cells(strategy="ORB"):
    f = pd.concat([frame(v1(s), DAY_CONTEXT) for s in SEEDS])
    out = []
    for name in F.DIMENSIONS:
        for v, n in f[f["strategy"] == strategy][name].value_counts().items():
            if n >= 8:
                out.append((name, v))
    return out


def test_the_simulated_sessions_have_enough_trades_in_every_dimension_to_make_the_comparison_meaningful():
    f = pd.concat([frame(v1(s), DAY_CONTEXT) for s in SEEDS])
    for name in F.DIMENSIONS:
        assert f[f["strategy"] == "ORB"][name].notna().sum() > 30, name
    assert len(_cells("ORB")) >= 20


@pytest.mark.parametrize("dimension,value", _cells("ORB"))
def test_v2_is_v1_minus_exactly_the_entries_the_rule_declines(dimension, value):
    r = rule(dimension, value)
    checked = 0
    for seed in SEEDS:
        a = v1(seed)
        if any("exposure limit" in s["why"] for s in a.skipped):
            continue                                              # the cap is exercised by its own test below
        b = v2(seed, [r])
        fa = frame(a, DAY_CONTEXT)
        declined = set()
        if len(fa):
            m = (fa["strategy"] == "ORB") & (fa[dimension] == value)
            declined = {(x.strategy, x.symbol) for x in fa[m].itertuples()}
        want = {key(t): t for t in a.trades if key(t) not in declined}
        got = {key(t): t for t in b.trades}
        assert set(got) == set(want), (dimension, value, seed)
        for k in got:
            assert dataclasses.asdict(got[k]) == dataclasses.asdict(want[k]), (k, dimension, value)
        checked += 1
    assert checked >= 1, "every seed hit the exposure cap: the comparison did not run"


def test_a_filter_that_never_matches_changes_nothing_at_all():
    r = rule("regime_prev", "regime CRISIS")
    for seed in SEEDS:
        a, b = v1(seed), v2(seed, [r])
        assert [dataclasses.asdict(t) for t in a.trades] == [dataclasses.asdict(t) for t in b.trades]


def test_gap_rules_filter_gap_entries_and_leave_orb_alone():
    cells = _cells("GAP")
    assert cells, "the simulated sessions must contain GAP trades"
    dimension, value = next(c for c in cells if c[0] in ("direction", "vs_market_today", "gap_size", "liquidity"))
    r = rule(dimension, value, strategy="GAP")
    for seed in SEEDS:
        a, b = v1(seed), v2(seed, [r])
        if not any("exposure limit" in s["why"] for s in a.skipped):
            assert [dataclasses.asdict(t) for t in a.trades if t.strategy == "ORB"] == \
                   [dataclasses.asdict(t) for t in b.trades if t.strategy == "ORB"]
        fb = frame(b, DAY_CONTEXT)
        assert fb.empty or not ((fb["strategy"] == "GAP") & (fb[dimension] == value)).any()


def test_a_declined_entry_does_not_come_back_later_in_the_day():
    r = rule("vs_market_today", "against the market")
    b = v2(2, [r])
    declined = {(s["strategy"], s["symbol"]) for s in b.skipped if s.get("filtered")}
    assert declined and not ({key(t) for t in b.trades} & declined)
    assert all("SK-0001" in s["why"] for s in b.skipped if s.get("filtered"))


def test_with_two_rules_the_first_match_names_the_reason():
    r1, r2 = rule("direction", "long", rid="SK-0001"), rule("direction", "short", rid="SK-0002")
    b = v2(1, [r1, r2])
    assert not [t for t in b.trades if t.strategy == "ORB"]            # every ORB entry is long or short: all declined
    assert {s["why"].split(":")[0] for s in b.skipped if s.get("filtered")} == {"skip rule SK-0001", "skip rule SK-0002"}


def test_when_the_exposure_cap_binds_v2_may_take_entries_v1_could_not_but_never_one_the_rule_declines():
    """Every trade is sized to about a tenth of the account and the engine stops at five times the account, so it takes a crowded
    session (many names, most of them breaking out) for the cap to bind."""
    r = rule("vs_market_today", "against the market")
    kw = {"names": 160, "account": 1_000_000.0}
    bound = [s for s in (1, 2, 3) if any("exposure limit" in x["why"] for x in v1(s, **kw).skipped)]
    assert bound, "need a simulated session where v1's exposure cap binds"
    for seed in bound:
        a, b = v1(seed, **kw), v2(seed, [r], **kw)
        fb = frame(b, DAY_CONTEXT)
        assert fb.empty or not ((fb["strategy"] == "ORB") & (fb["vs_market_today"] == "against the market")).any()
        assert b.trades and a.trades


def test_the_engine_rules_are_versioned_by_the_rules_in_force():
    a = AC.build_engine_rules([rule("direction", "long")])
    b = AC.build_engine_rules([rule("direction", "long"), rule("weekday", "Mon", rid="SK-0002")])
    c = AC.build_engine_rules([rule("direction", "long", strategy="GAP")])
    assert set(a) == {"ORB"} and set(c) == {"GAP"} and a["ORB"].version != b["ORB"].version
    assert a["ORB"].base == "ORB" and a["ORB"].version.startswith("ORB-skip-")
    assert AC.build_engine_rules([]) == {}
