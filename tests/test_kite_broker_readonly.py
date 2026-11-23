import io
import json
import urllib.error

import pytest

from financial_brain.execution import order_policy
from financial_brain.execution.oms import kite_broker as broker
from financial_brain.execution.oms import model


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _response(data):
    return Response(json.dumps({"status": "success", "data": data}).encode())


def _configured(monkeypatch):
    from financial_brain.providers import kite_data

    monkeypatch.setattr(kite_data, "credentials", lambda: ("test-key", "test-token"))
    monkeypatch.setattr(order_policy, "PHYSICAL_ORDERS_ENABLED", True)


def test_lock_diagnostics_name_environment_stage_verdict_and_kill_switch(tmp_path, monkeypatch):
    from financial_brain.execution.oms import killswitch, rollout

    monkeypatch.delenv("FB_LIVE_ORDERS", raising=False)
    monkeypatch.setattr(rollout, "load", lambda _path=None: {"stage": rollout.OFF})
    monkeypatch.setattr(killswitch, "load", lambda _path=None: {"tripped": False})
    locked = broker.locks("PENDING", stage_path=tmp_path / "stage.json", ks_path=tmp_path / "ks.json")
    assert len(locked) == 2
    assert "FB_LIVE_ORDERS" in locked[0] and "stage is OFF" in locked[1]

    monkeypatch.setenv("FB_LIVE_ORDERS", "1")
    monkeypatch.setattr(rollout, "load", lambda _path=None: {"stage": rollout.SMALL_AUTO})
    monkeypatch.setattr(killswitch, "load", lambda _path=None: {"tripped": True, "reason": "test trip"})
    locked = broker.locks("CANDIDATE", stage_path=tmp_path / "stage.json", ks_path=tmp_path / "ks.json")
    assert any("needs a CONFIRMED" in reason for reason in locked)
    assert any("kill switch tripped: test trip" in reason for reason in locked)


def test_read_only_request_validates_endpoint_and_maps_kite_headers(monkeypatch):
    _configured(monkeypatch)
    requests = []

    def opener(req, timeout):
        requests.append((req, timeout))
        return _response([])

    client = broker.KiteBroker("PENDING", opener=opener)
    with pytest.raises(broker.BrokerError, match="not an endpoint"):
        client._request("GET", "/arbitrary")
    assert client._request("GET", "/orders") == []
    req, timeout = requests[0]
    assert req.get_method() == "GET"
    assert req.get_header("X-kite-version") == "3"
    assert req.get_header("Authorization") == "token test-key:test-token"
    assert timeout == 10


def test_orders_maps_partial_cancelled_and_unknown_statuses(monkeypatch):
    _configured(monkeypatch)
    rows = [
        {"order_id": "1", "tag": "a", "tradingsymbol": "AAA", "exchange": "NSE",
         "transaction_type": "BUY", "quantity": 5, "filled_quantity": 2, "status": "OPEN"},
        {"order_id": "2", "tag": "b", "tradingsymbol": "BBB", "exchange": "NSE",
         "transaction_type": "SELL", "quantity": 5, "filled_quantity": 5, "status": "CANCELLED"},
        {"order_id": "3", "tag": "c", "tradingsymbol": "CCC", "exchange": "BSE",
         "transaction_type": "BUY", "quantity": 1, "filled_quantity": 1, "status": "COMPLETE",
         "average_price": 101.5, "exchange_update_timestamp": "2026-11-23T10:00:00+05:30"},
        {"order_id": "4", "tag": "d", "tradingsymbol": "DDD", "exchange": "NSE",
         "transaction_type": "BUY", "quantity": 2, "filled_quantity": 0, "status": "REJECTED"},
    ]
    client = broker.KiteBroker("PENDING", opener=lambda *_args, **_kwargs: _response(rows))
    got = client.orders()
    assert [row.state for row in got] == [model.PARTIAL, model.FILLED, model.FILLED, model.REJECTED]
    assert got[0].filled_qty == 2
    assert got[2].avg_price == 101.5
    assert got[2].updated == "2026-11-23T10:00:00+05:30"


def test_positions_combine_holdings_and_day_net_and_drop_zero_rows(monkeypatch):
    _configured(monkeypatch)
    payloads = [
        {"status": "success", "data": [
            {"tradingsymbol": "AAA", "exchange": "NSE", "quantity": 4, "t1_quantity": 1},
            {"tradingsymbol": "ZERO", "quantity": 0, "t1_quantity": 0},
        ]},
        {"status": "success", "data": {"day": [
            {"tradingsymbol": "AAA", "exchange": "NSE", "quantity": -2},
            {"tradingsymbol": "BBB", "quantity": 3},
        ]}},
    ]

    def opener(*_args, **_kwargs):
        return Response(json.dumps(payloads.pop(0)).encode())

    got = broker.KiteBroker("PENDING", opener=opener).positions()
    assert got == {("NSE", "AAA"): 3, ("NSE", "BBB"): 3}


def test_ltp_uses_the_read_only_quote_provider(monkeypatch):
    from financial_brain.providers import kite_data

    seen = []
    monkeypatch.setattr(kite_data, "quote", lambda keys, kind: seen.append((keys, kind)) or {
        "NSE:AAA": {"last_price": "10.5"}, "BSE:BBB": {"last_price": 20}})
    got = broker.KiteBroker("PENDING").ltp([("NSE", "AAA"), ("BSE", "BBB")])
    assert seen == [(["NSE:AAA", "BSE:BBB"], "ltp")]
    assert got == {("NSE", "AAA"): 10.5, ("BSE", "BBB"): 20.0}


def test_read_failures_are_broker_errors_and_writes_are_denied_before_transport(monkeypatch):
    _configured(monkeypatch)
    client = broker.KiteBroker("CONFIRMED", opener=lambda *_args, **_kwargs: Response(b"bad json"))
    with pytest.raises(broker.BrokerError, match="refusing"):
        client._request("GET", "/not-allowed")
    with pytest.raises(broker.BrokerError, match="invalid JSON"):
        client._request("GET", "/orders")
    malformed_shape = broker.KiteBroker("PENDING", opener=lambda *_args, **_kwargs: Response(b"[]"))
    with pytest.raises(broker.BrokerError, match="invalid response shape"):
        malformed_shape._request("GET", "/orders")

    for exc in (TimeoutError("timeout"), OSError("network")):
        broken = broker.KiteBroker("PENDING", opener=lambda *_args, error=exc, **_kwargs: (_ for _ in ()).throw(error))
        with pytest.raises(broker.BrokerError):
            broken._request("GET", "/orders")

    http_error = urllib.error.HTTPError("url", 503, "unavailable", {}, io.BytesIO(b"maintenance"))
    broken = broker.KiteBroker("PENDING", opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(http_error))
    with pytest.raises(broker.BrokerError, match="503 from GET /orders"):
        broken._request("GET", "/orders")

    sent = []
    write_client = broker.KiteBroker("CONFIRMED", opener=lambda *args, **kwargs: sent.append(args))
    with pytest.raises(order_policy.PhysicalOrderDenied):
        write_client._request("POST", "/orders/regular", {"quantity": 1})
    assert sent == []


def test_kite_error_status_becomes_broker_error(monkeypatch):
    _configured(monkeypatch)
    body = Response(json.dumps({"status": "error", "message": "invalid session"}).encode())
    with pytest.raises(broker.BrokerError, match="Kite error.*invalid session"):
        broker.KiteBroker("PENDING", opener=lambda *_args, **_kwargs: body)._request("GET", "/orders")
