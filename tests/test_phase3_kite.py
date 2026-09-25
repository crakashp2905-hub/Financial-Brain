"""Kite, read only: the structural guarantees, tested without touching the network.

The claim worth defending is not "we choose not to place orders" but "no code path here
can". These tests check that claim the way a reader would - by looking at what the module
can build - plus the two things a live feed makes easy to get wrong: leaking a credential
into an error, and reading a price from after the moment being reasoned about.
"""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from financial_brain.providers import kite_data as kd
from financial_brain.providers import kite_login as kl


# --- no order can be placed ---------------------------------------------------

def test_no_endpoint_is_transactional():
    """Every path this module will build, listed. None of them trades."""
    for path in kd.ENDPOINTS:
        assert path.startswith("/instruments") or path.startswith("/quote"), path
    forbidden = ("/orders", "/gtt", "/portfolio", "/margins", "/session")
    for bad in forbidden:
        assert not any(p.startswith(bad) for p in kd.ENDPOINTS), bad


def test_a_path_outside_the_allowlist_is_refused_before_any_credential_is_read(monkeypatch):
    """The refusal must come first, so a typo cannot even trigger a credential lookup."""
    monkeypatch.delenv("KITE_API_KEY", raising=False)
    with pytest.raises(kd.KiteDataError, match="not a read endpoint"):
        kd._get("/orders/regular")


def test_the_package_contains_no_order_call_anywhere():
    """A grep a reviewer could run, run as a test."""
    import pathlib
    root = pathlib.Path(kd.__file__).parent
    for path in root.glob("*.py"):
        body = path.read_text(encoding="utf-8")
        # the endpoint itself, and the verbs that would reach it
        assert '"/orders' not in body and "'/orders" not in body, path.name
        assert 'method="POST"' not in body, path.name
        assert 'method="DELETE"' not in body, path.name


# --- credentials --------------------------------------------------------------

def test_missing_credentials_name_the_variable_not_the_value(monkeypatch):
    monkeypatch.delenv("KITE_API_KEY", raising=False)
    with pytest.raises(kd.KiteNotConfigured, match="KITE_API_KEY"):
        kd.credentials()


def test_a_stale_access_token_is_not_used(monkeypatch, tmp_path):
    """Kite rotates tokens each morning; yesterday's must not be presented as today's."""
    path = tmp_path / "kite_access_token.json"
    path.write_text(json.dumps({"access_token": "stale", "login_day": "2000-01-01"}))
    monkeypatch.setattr(kl, "token_path", lambda: path)
    assert kl.stored_token() is None

    path.write_text(json.dumps({"access_token": "fresh",
                                "login_day": date.today().isoformat()}))
    assert kl.stored_token() == "fresh"


def test_the_login_url_carries_only_the_api_key(monkeypatch):
    """The owner authenticates with Zerodha directly. Nothing here sees a password."""
    monkeypatch.setenv("KITE_API_KEY", "k123")
    url = kl.login_url()
    assert url.startswith("https://kite.zerodha.com/connect/login")
    assert "api_key=k123" in url
    for leaked in ("password", "secret", "totp", "pin"):
        assert leaked not in url.lower()


def test_the_exchange_needs_the_secret_and_says_so_without_printing_it(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", "k123")
    monkeypatch.delenv("KITE_API_SECRET", raising=False)
    with pytest.raises(kl.KiteLoginError) as e:
        kl.exchange("req_token_abc")
    assert "KITE_API_SECRET" in str(e.value)


def test_the_callback_listens_only_on_loopback():
    """The redirect carries the request token, so the socket must not be reachable from
    anywhere but this machine."""
    import inspect
    src = inspect.getsource(kl.catch_request_token)
    assert '"127.0.0.1"' in src
    assert "0.0.0.0" not in src


def test_the_callback_does_not_log_the_request_token():
    """The default HTTP server access log prints the path, and the path is the token."""
    import inspect
    assert "def log_message" in inspect.getsource(kl._Catcher)


# --- point in time on a live feed ---------------------------------------------

def test_candles_require_an_explicit_as_of():
    """Not defaulted to now: on a stream, "everything available" and "everything up to
    the moment I am reasoning about" differ, and that difference is the look-ahead."""
    import inspect
    sig = inspect.signature(kd.candles)
    assert sig.parameters["as_of"].default is inspect.Parameter.empty
    assert sig.parameters["start"].default is inspect.Parameter.empty


def test_an_unknown_interval_is_refused():
    with pytest.raises(kd.KiteDataError, match="interval must be"):
        kd.candles(1, date(2026, 1, 1), date(2026, 1, 2), interval="7minute")


def test_minute_bars_are_available_as_an_interval():
    """The whole reason for connecting: four intraday strategies need these."""
    assert "minute" in kd.INTERVALS


# --- request shape ------------------------------------------------------------

def test_quote_repeats_the_symbol_parameter():
    """Kite takes one `i=` per symbol, not a comma-joined list."""
    url = kd.quote_url(["NSE:RELIANCE", "NSE:INFY"], "ohlc")
    assert url.count("i=") == 2
    assert "%3A" in url                     # the colon is encoded, not sent raw


def test_too_many_symbols_is_refused_rather_than_truncated(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", "k")
    monkeypatch.setenv("KITE_ACCESS_TOKEN", "t")
    with pytest.raises(kd.KiteDataError, match="at most"):
        kd.quote([f"NSE:S{i}" for i in range(kd.MAX_QUOTE_SYMBOLS + 1)])


def test_an_unknown_quote_kind_is_refused():
    with pytest.raises(kd.KiteDataError, match="kind must be"):
        kd.quote_url(["NSE:RELIANCE"], "depth") if False else kd.quote(["NSE:X"], "depth")


def test_calls_are_throttled_to_the_published_rate():
    """Exceeding Kite's limits risks the owner's account, which is not this project's
    cost to impose."""
    import time
    kd._last_call = 0.0
    t0 = time.monotonic()
    kd._throttle()
    kd._throttle()
    assert time.monotonic() - t0 >= kd.MIN_INTERVAL_S * 0.9


def test_datetime_bounds_carry_the_time_of_day():
    """Minute bars need a timestamp, not just a date - a date-only bound would silently
    fetch the whole session."""
    import inspect
    src = inspect.getsource(kd.candles)
    assert "%Y-%m-%d %H:%M:%S" in src
    assert isinstance(datetime(2026, 1, 1, 9, 15), datetime)


# --- the loopback receiver ----------------------------------------------------

def test_the_port_is_claimed_before_the_browser_is_opened():
    """The ordering IS the protection. Opening the browser first leaves a window where
    another process holding the port receives the request token instead."""
    import inspect

    from financial_brain import cli
    src = inspect.getsource(cli.cmd_kite)
    assert src.index("Receiver(port=") < src.index("webbrowser.open"), \
        "the receiver must be bound before Kite is told where to redirect"


def test_a_busy_port_aborts_the_login_rather_than_redirecting_anyway():
    """If the port cannot be claimed, no login starts - because a redirect would hand a
    credential to whatever does hold it."""
    from financial_brain.providers.kite_login_server import PortUnavailable, Receiver
    with Receiver(port=0) as held:
        port = held._server.server_address[1]
        with pytest.raises(PortUnavailable, match="already in use"):
            with Receiver(port=port):
                pass


def test_the_receiver_binds_loopback_only():
    import inspect

    from financial_brain.providers import kite_login_server as ks
    src = inspect.getsource(ks.Receiver.__enter__)
    assert '"127.0.0.1"' in src
    assert "0.0.0.0" not in src


def test_the_receiver_does_not_log_the_token_bearing_path():
    import inspect

    from financial_brain.providers import kite_login_server as ks
    assert "def log_message" in inspect.getsource(ks._Handler)


def test_the_advertised_callback_url_matches_what_is_registered_with_kite():
    """The URL printed for the user must be the one the socket actually serves, or the
    redirect silently goes nowhere."""
    from financial_brain.providers.kite_login_server import Receiver
    assert Receiver(port=8765).url == "http://127.0.0.1:8765/kite/callback"
