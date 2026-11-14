"""Integration guard: paper-side Kite access remains read-only at the HTTP boundary."""
from datetime import datetime, timezone
from email.message import Message
from io import BytesIO
import json
import urllib.parse
import urllib.error

import pytest

from financial_brain.forward_edge.modes import Mode, resolve_mode
from financial_brain.providers import kite_data


class Response:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


def test_paper_data_reads_use_only_get_routes_and_order_paths_never_reach_network(monkeypatch):
    paper_mode = resolve_mode(Mode.LIVE_PAPER, now=datetime(2026, 11, 16, 3, 0, tzinfo=timezone.utc))
    assert paper_mode.may_send_physical_order is False
    requests = []

    def fake_urlopen(request, timeout):
        route = urllib.parse.urlsplit(request.full_url).path
        method = request.get_method()
        requests.append((method, route))
        assert method == "GET"
        if route.startswith("/instruments/historical/"):
            body = {"status": "success", "data": {"candles": [
                ["2026-11-16T09:15:00+05:30", 100, 102, 99, 101, 1000]
            ]}}
            return Response(json.dumps(body).encode())
        if route.startswith("/instruments/"):
            return Response(b"instrument_token,tradingsymbol,segment\n123,INFY,NSE\n")
        if route == "/quote":
            return Response(json.dumps({"status": "success", "data": {"NSE:INFY": {"last_price": 101}}}).encode())
        raise AssertionError(f"unexpected network route: {route}")

    monkeypatch.setattr(kite_data, "credentials", lambda: ("test-key", "test-token"))
    monkeypatch.setattr(kite_data, "_throttle", lambda: None)
    monkeypatch.setattr(kite_data.urllib.request, "urlopen", fake_urlopen)

    kite_data.quote(["NSE:INFY"], kind="full")
    kite_data.candles(123, datetime(2026, 11, 16, 9, 15), datetime(2026, 11, 16, 9, 15))
    kite_data.instruments("NSE")
    assert requests == [
        ("GET", "/quote"),
        ("GET", "/instruments/historical/123/minute"),
        ("GET", "/instruments/NSE"),
    ]

    calls_before_blocked_routes = len(requests)
    blocked = (
        "/orders", "/orders/regular", "/orders/regular/123", "/gtt/triggers",
        "/instruments/../orders", "/instruments/historical/1/../../orders",
        "/quote/../orders", "/quote/anything", "/quote?next=/orders",
        "https://api.kite.trade/orders",
    )
    for route in blocked:
        with pytest.raises(kite_data.KiteDataError, match="not a read endpoint"):
            kite_data._get(route)
    with pytest.raises(kite_data.KiteDataError, match="venue code"):
        kite_data.instruments("../orders")
    assert len(requests) == calls_before_blocked_routes


def test_provider_error_diagnostics_redact_api_key_and_access_token(monkeypatch):
    api_key, access_token = "private-key-123", "daily-token-456"
    headers = Message()
    headers["Content-Type"] = "text/plain"
    error_body = f"reflected {api_key} {access_token}".encode()
    response = urllib.error.HTTPError("https://api.kite.trade/quote", 500, "failure", headers,
                                      BytesIO(error_body))
    monkeypatch.setattr(kite_data, "credentials", lambda: (api_key, access_token))
    monkeypatch.setattr(kite_data, "_throttle", lambda: None)
    monkeypatch.setattr(kite_data.urllib.request, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(response))

    with pytest.raises(kite_data.KiteDataError) as caught:
        kite_data.quote(["NSE:INFY"], kind="full")

    message = str(caught.value)
    assert api_key not in message and access_token not in message
    assert "[REDACTED]" in message


def test_success_error_payload_redacts_credentials(monkeypatch):
    api_key, access_token = "private-key-abc", "daily-token-def"
    body = {"status": "error", "message": f"bad {api_key} {access_token}"}

    class JsonResponse(Response):
        def read(self):
            return json.dumps(body).encode()

    monkeypatch.setattr(kite_data, "credentials", lambda: (api_key, access_token))
    monkeypatch.setattr(kite_data, "_throttle", lambda: None)
    monkeypatch.setattr(kite_data.urllib.request, "urlopen", lambda *_args, **_kwargs: JsonResponse(b""))

    with pytest.raises(kite_data.KiteDataError) as caught:
        kite_data.quote(["NSE:INFY"], kind="full")

    assert api_key not in str(caught.value) and access_token not in str(caught.value)
