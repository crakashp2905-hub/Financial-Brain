"""The OMS (ADR-0005): every path against PaperBroker and a fake Kite transport - nothing touches a network."""
import io
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from financial_brain.execution.oms import broker as B
from financial_brain.execution.oms import engine as EN
from financial_brain.execution.oms import journal as J
from financial_brain.execution.oms import killswitch as KS
from financial_brain.execution.oms import kite_broker as KB
from financial_brain.execution.oms import model as M
from financial_brain.execution.oms import reconcile as RC
from financial_brain.execution.oms import risk_gate as RG
from financial_brain.execution.oms import rollout as RO
from financial_brain.execution.oms import slicer as SL

IST = timezone(timedelta(hours=5, minutes=30))
IN_SESSION = datetime(2026, 10, 13, 10, 0, tzinfo=IST)          # a Tuesday
K = ("NSE", "ABC")


@pytest.fixture
def env(tmp_path):
    j = J.Journal(tmp_path / "oms.duckdb")
    b = B.PaperBroker()
    b.tick({K: 100.0})
    oms = EN.OMS(j, b, verdicts={"s": "CANDIDATE"}, stage_path=tmp_path / "stage.json", ks_path=tmp_path / "ks.json",
                 banned=frozenset(), clock=lambda: IN_SESSION)
    yield oms, j, b, tmp_path
    j.close()


def _submit(oms, qty=10, px=100.0, side=M.BUY, symbol="ABC"):
    return oms.submit(strategy="s", day="2026-10-13", symbol=symbol, side=side, qty=qty, limit_price=px)


# ------------------------------------------------------------------ the state machine

def test_illegal_transitions_and_falling_fills_are_refused():
    o = M.Order("c", "s", "d", "ABC", "NSE", M.BUY, 10, 100.0)
    with pytest.raises(M.OrderError):
        M.advance(o, M.FILLED)                                     # NEW cannot jump to FILLED
    o = M.advance(M.advance(M.advance(o, M.APPROVED), M.SUBMITTED, broker_id="1"), M.PARTIAL, filled_qty=4)
    with pytest.raises(M.OrderError):
        M.advance(o, M.PARTIAL, filled_qty=3)
    done = M.advance(o, M.FILLED, filled_qty=10)
    with pytest.raises(M.OrderError):
        M.advance(done, M.CANCELLED)                               # terminal
    with pytest.raises(M.OrderError):
        M.Order("c", "s", "d", "ABC", "NSE", M.BUY, 10, 0.0)       # limit orders only


def test_the_same_intent_is_the_same_order(env):
    oms, j, b, _ = env
    a = _submit(oms)
    again = _submit(oms)
    assert a.client_id == again.client_id and len(b.orders()) == 1


# ------------------------------------------------------------------ journal

def test_journal_is_exactly_once_and_tamper_evident(env):
    oms, j, b, _ = env
    o = _submit(oms)
    n = len(j.events())
    assert j.record(j.get(o.client_id), "SUBMITTED") is False      # same event again: nothing written
    assert len(j.events()) == n and j.verify()["ok"]
    j.con.execute("UPDATE oms_events SET payload = '{}' WHERE seq = 2")
    assert j.verify() == {"ok": False, "broken_at": 2, "why": "hash"}


# ------------------------------------------------------------------ fills

def test_full_fill_and_position(env):
    oms, j, b, _ = env
    o = _submit(oms)
    oms.poll()
    assert j.get(o.client_id).state == M.FILLED and j.positions() == {K: 10}


def test_partial_fills_then_cancel_keeps_what_filled(env):
    oms, j, b, _ = env
    b.max_fill = 3
    o = _submit(oms)
    oms.poll()
    assert j.get(o.client_id).state == M.PARTIAL and j.get(o.client_id).filled_qty == 3
    b.tick({K: 100.0})
    oms.poll()
    assert j.get(o.client_id).filled_qty == 6
    b.cancel(j.get(o.client_id).broker_id)
    oms.poll()
    got = j.get(o.client_id)
    assert got.state == M.CANCELLED and got.filled_qty == 6 and j.positions() == {K: 6}


def test_broker_reject_path(env):
    oms, j, b, _ = env
    b.reject_symbols.add("ABC")
    o = _submit(oms)
    oms.poll()
    assert j.get(o.client_id).state == M.REJECTED and j.positions() == {}


def test_timeout_becomes_unknown_then_found_by_tag(env):
    oms, j, b, _ = env
    b.timeout_next = 1
    o = _submit(oms)
    assert j.get(o.client_id).state == M.UNKNOWN
    oms.poll()
    assert j.get(o.client_id).state == M.FILLED                    # found by its tag, never re-sent
    assert len(b.orders()) == 1


def test_unknown_order_absent_from_the_book_is_not_found_after_grace(env):
    oms, j, b, _ = env
    b.timeout_next = 1
    o = _submit(oms)
    b._orders.clear()                                              # the request never reached the broker
    for _ in range(EN.UNKNOWN_GRACE):
        oms.poll()
    assert j.get(o.client_id).state == M.NOT_FOUND


# ------------------------------------------------------------------ gate

def test_risk_gate_refusals(env):
    oms, j, b, tmp = env
    lim = RO.SMALL
    o = M.Order("c", "s", "d", "ABC", "NSE", M.BUY, 10, 100.0)
    ok = RG.Context(now=IN_SESSION, ltp=100.0)
    assert RG.check(o, lim, ok) == []
    assert RG.check(o, lim, RG.Context(now=IN_SESSION.replace(hour=16), ltp=100.0))
    assert RG.check(o, lim, RG.Context(now=IN_SESSION, ltp=95.0))                 # 5% from the last price
    assert RG.check(o, lim, RG.Context(now=IN_SESSION, ltp=None))
    assert RG.check(M.Order("c", "s", "d", "ABC", "NSE", M.BUY, 200, 100.0), lim, ok)   # 20,000 > 10,000
    assert RG.check(o, lim, RG.Context(now=IN_SESSION, ltp=100.0, banned=frozenset({"ABC"})))
    assert RG.check(o, lim, RG.Context(now=IN_SESSION, ltp=100.0, live_same_side=True))
    assert RG.check(M.Order("c", "s", "d", "ABC", "NSE", M.SELL, 10, 100.0), lim, ok)   # short sale in delivery
    assert RG.check(M.Order("c", "s", "d", "ABC", "NSE", M.BUY, 10, 100.03), lim, ok)   # off the tick grid
    r = _submit(oms, px=103.0)
    assert j.get(r.client_id).state == M.REJECTED_RISK and "from the last price" in r.reason


def test_unreadable_ban_list_refuses_everything(env):
    oms, j, b, _ = env
    oms.banned = None
    assert _submit(oms).state == M.REJECTED_RISK


def test_ban_list_parser():
    text = "Securities in Ban For Trade Date 13-OCT-2026:\n1,ABC\n2,M&M\nfooter,x y\n"
    assert RG.parse_ban_list(text) == frozenset({"ABC", "M&M"})


# ------------------------------------------------------------------ stages, approvals, kill switch

def test_ticket_stage_needs_a_human(env, tmp_path):
    oms, j, b, tmp = env
    RO._write({"stage": RO.TICKET, "since": "2026-10-13", "history": []}, tmp / "stage.json")
    o = _submit(oms)
    assert o.state == M.AWAITING_APPROVAL and b.orders() == []
    with pytest.raises(PermissionError):
        oms.approve(o.client_id, "agent:claude")
    oms.approve(o.client_id, "Akash")
    oms.poll()
    assert j.get(o.client_id).state == M.FILLED


def test_kill_switch_cancels_at_the_broker_and_blocks(env):
    oms, j, b, tmp = env
    b.tick({K: 101.0})                                             # a buy at 100 now rests
    o = _submit(oms, px=101.0 - 0.95)
    assert j.get(o.client_id).state == M.SUBMITTED
    KS.trip("test", b, path=tmp / "ks.json", journal=j)
    oms.poll()
    assert j.get(o.client_id).state == M.CANCELLED
    assert _submit(oms, qty=5, px=100.95).state == M.REJECTED_RISK
    with pytest.raises(KS.KillSwitchError):
        KS.reset("agent:claude", "no", path=tmp / "ks.json")
    KS.reset("Akash", "checked", path=tmp / "ks.json")


def test_reconciliation_mismatch_demotes_and_trips(env):
    oms, j, b, tmp = env
    RO._write({"stage": RO.SMALL_AUTO, "since": "2026-10-13", "history": []}, tmp / "stage.json")
    _submit(oms)
    oms.poll()
    b._pos[K] += 5                                                 # shares the journal never recorded
    res = RC.run(j, b, stage_path=tmp / "stage.json", ks_path=tmp / "ks.json")
    assert not res["ok"] and res["diffs"][0]["diff"] == 5
    assert RO.load(tmp / "stage.json")["stage"] == RO.TICKET
    assert KS.load(tmp / "ks.json")["tripped"]


def test_promotion_rules(tmp_path):
    p = tmp_path / "stage.json"
    good = {"strategy_verdict_at_least": "CANDIDATE", "paper_oms_sessions": 10, "journal_chain_ok": True,
            "runbook_read_by_owner": True}
    with pytest.raises(RO.RolloutError):
        RO.promote(RO.TICKET, "agent:claude", good, path=p)
    with pytest.raises(RO.RolloutError):
        RO.promote(RO.SMALL_AUTO, "Akash", good, path=p)            # a jump
    with pytest.raises(RO.RolloutError, match="paper_oms_sessions"):
        RO.promote(RO.TICKET, "Akash", {**good, "paper_oms_sessions": 3}, path=p)
    with pytest.raises(RO.RolloutError, match="strategy_verdict"):
        RO.promote(RO.TICKET, "Akash", {**good, "strategy_verdict_at_least": "FAIL"}, path=p)
    assert RO.promote(RO.TICKET, "Akash", good, path=p)["stage"] == RO.TICKET
    assert not RO.eligible(RO.SMALL_AUTO, "CANDIDATE") and RO.eligible(RO.SMALL_AUTO, "CONFIRMED")


# ------------------------------------------------------------------ the Kite broker's locks

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_kite_broker_is_locked_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("FB_LIVE_ORDERS", raising=False)
    sent = []
    kb = KB.KiteBroker("CONFIRMED", opener=lambda req, timeout: sent.append(req), stage_path=tmp_path / "s.json",
                       ks_path=tmp_path / "k.json")
    o = M.Order(M.client_id("s", "d", "ABC", M.BUY, 1), "s", "d", "ABC", "NSE", M.BUY, 1, 100.0)
    with pytest.raises(KB.LiveLocked) as e:
        kb.place(o)
    assert "FB_LIVE_ORDERS" in str(e.value) and "stage is OFF" in str(e.value) and sent == []


def test_kite_broker_sends_a_tagged_limit_only_when_every_lock_is_open(tmp_path, monkeypatch):
    monkeypatch.setenv("FB_LIVE_ORDERS", "1")
    monkeypatch.setenv("KITE_API_KEY", "test-key")
    monkeypatch.setenv("KITE_ACCESS_TOKEN", "test-token")
    RO._write({"stage": RO.SMALL_AUTO, "history": []}, tmp_path / "s.json")
    sent = []

    def opener(req, timeout):
        sent.append(req)
        return _Resp(json.dumps({"status": "success", "data": {"order_id": "2610130001"}}).encode())

    o = M.Order(M.client_id("s", "d", "ABC", M.BUY, 1), "s", "d", "ABC", "NSE", M.BUY, 1, 100.0)
    assert KB.locks("CANDIDATE", stage_path=tmp_path / "s.json", ks_path=tmp_path / "k.json")   # stage 2 needs CONFIRMED
    kb = KB.KiteBroker("CONFIRMED", opener=opener, stage_path=tmp_path / "s.json", ks_path=tmp_path / "k.json")
    assert kb.place(o) == "2610130001"
    body = dict(x.split("=") for x in sent[0].data.decode().split("&"))
    assert sent[0].get_method() == "POST" and sent[0].full_url.endswith("/orders/regular")
    assert body["order_type"] == "LIMIT" and body["tag"] == o.client_id and body["validity"] == "DAY"
    KS.trip("test", path=tmp_path / "k.json")
    with pytest.raises(KB.LiveLocked, match="kill switch"):
        kb.place(o)
    with pytest.raises(B.BrokerError, match="refusing"):
        kb._request("POST", "/user/margins")


# ------------------------------------------------------------------ slicing

def test_vwap_respects_the_participation_cap_and_reports_the_rest():
    curve = np.array([0.2, 0.1, 0.1, 0.1, 0.1, 0.4])
    s = SL.schedule(1000, kind="VWAP", intervals=6, curve=curve, expected_volume=5000, participation=0.10)
    caps = 0.10 * 5000 * curve
    assert all(c.qty <= cap + 1e-9 for c, cap in zip(s["children"], caps))
    assert sum(c.qty for c in s["children"]) + s["unfilled"] == 1000 and s["unfilled"] > 0
    t = SL.schedule(120, kind="TWAP", intervals=12)
    assert [c.qty for c in t["children"]] == [10] * 12 and t["unfilled"] == 0
    assert SL.child_limit(100.0, M.BUY) == 100.05 and SL.child_limit(100.0, M.SELL) == 99.95


def test_the_order_path_lives_in_one_file_only():
    """ADR-0005: providers/ stays read-only; the only module that can build an order request is kite_broker.py."""
    import pathlib

    import financial_brain
    root = pathlib.Path(financial_brain.__file__).parent
    allowed = root / "execution" / "oms" / "kite_broker.py"
    for path in root.rglob("*.py"):
        if path == allowed:
            continue
        body = path.read_text(encoding="utf-8")
        assert '"/orders/regular' not in body and "'/orders/regular" not in body, path
        if "api.kite.trade" in body:                      # a file that talks to Kite may only read
            assert 'method="POST"' not in body and 'method="DELETE"' not in body, path
