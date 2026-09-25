"""Kite Connect's daily login, without a web server and without handling a password.

Kite access tokens expire every morning around 6am IST, so this is a daily ritual rather
than a one-off setup, and it is worth making it one command.

## The flow, and where the boundary sits

1. The owner opens Kite's login URL in their own browser.
2. They authenticate **with Zerodha directly** - password, TOTP, the lot. None of it
   passes through this code, and nothing here ever prompts for it. That boundary is the
   same one drawn at MFCentral: Claude does not authenticate as the owner.
3. Kite redirects to the app's registered redirect URL with a ``request_token`` in the
   query string. The redirect points at ``127.0.0.1``, so the token lands on the owner's
   own machine and never leaves it.
4. This module catches it on a short-lived local socket, exchanges it for an access token
   using the API secret, and writes the access token to a file under ``data/`` - which is
   gitignored, along with ``.env``.

The exchange is a SHA-256 of ``api_key + request_token + api_secret``, which is why the
secret is needed once per day and why it is read from the environment rather than passed
around.

## What is deliberately absent

There is no order placement anywhere in this package, and that is a design gate rather
than a convention - see [[Kite readiness]]. ``providers/kite.py`` implements only GETs
against instrument, historical and quote endpoints. A reader checking whether this system
can place an order should be able to answer it by grepping for the endpoint and finding
nothing.

## Secrets

``KITE_API_KEY`` and ``KITE_API_SECRET`` come from the environment. Nothing in this module
prints, logs or returns the secret, and the access token is written with the narrowest
permissions the platform allows. If a call fails, the error names the variable that is
missing, never its value.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

API = "https://api.kite.trade"
LOGIN = "https://kite.zerodha.com/connect/login"
DEFAULT_PORT = 8765
CALLBACK_PATH = "/kite/callback"
TIMEOUT_S = 300


class KiteLoginError(RuntimeError):
    """The login flow could not complete. The message says which step."""


def token_path() -> Path:
    """Where the daily access token lives: under data/, which git ignores."""
    from ..config import load
    return load().data_root / "kite_access_token.json"


def login_url(api_key: str | None = None) -> str:
    """The URL the owner opens. They authenticate with Zerodha, not with this code."""
    key = api_key or os.environ.get("KITE_API_KEY")
    if not key:
        raise KiteLoginError("set KITE_API_KEY in the environment")
    return f"{LOGIN}?api_key={urllib.parse.quote(key)}&v=3"


class _Catcher(BaseHTTPRequestHandler):
    """Answers exactly one redirect and holds the request_token it carried."""

    token: str | None = None
    error: str | None = None

    def do_GET(self):                                    # noqa: N802 - stdlib name
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        got = (params.get("request_token") or [None])[0]
        status = (params.get("status") or [None])[0]
        if got:
            _Catcher.token = got
            body = ("<h2>Kite login captured.</h2>"
                    "<p>You can close this tab and return to the terminal.</p>")
        else:
            _Catcher.error = f"no request_token in the redirect (status={status})"
            body = ("<h2>No request token in the redirect.</h2>"
                    "<p>Check that the app's redirect URL matches this address exactly."
                    "</p>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):                        # noqa: A003 - stdlib name
        """Silence the default access log: the path carries the request token."""


def catch_request_token(port: int = DEFAULT_PORT, timeout: int = TIMEOUT_S) -> str:
    """Serve one request on 127.0.0.1 and return the request_token it carried.

    Bound to the loopback interface only, so nothing outside this machine can reach it,
    and it stops after a single request.
    """
    _Catcher.token = _Catcher.error = None
    server = HTTPServer(("127.0.0.1", port), _Catcher)
    server.timeout = timeout
    try:
        server.handle_request()
    finally:
        server.server_close()
    if _Catcher.token:
        return _Catcher.token
    raise KiteLoginError(_Catcher.error or
                         f"no redirect arrived on 127.0.0.1:{port} within {timeout}s")


def exchange(request_token: str, *, api_key: str | None = None,
             api_secret: str | None = None) -> dict:
    """Swap a request token for the day's access token.

    The checksum is SHA-256 of api_key + request_token + api_secret. The secret is used
    here and nowhere else, and is never returned or logged.
    """
    key = api_key or os.environ.get("KITE_API_KEY")
    secret = api_secret or os.environ.get("KITE_API_SECRET")
    if not key or not secret:
        raise KiteLoginError("set KITE_API_KEY and KITE_API_SECRET in the environment")

    checksum = hashlib.sha256((key + request_token + secret).encode()).hexdigest()
    data = urllib.parse.urlencode({"api_key": key, "request_token": request_token,
                                   "checksum": checksum}).encode()
    req = urllib.request.Request(f"{API}/session/token", data=data,
                                 headers={"X-Kite-Version": "3"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        # The body carries Kite's own message; the secret is not in it, but the request
        # body would be, so only the response is surfaced.
        raise KiteLoginError(f"token exchange refused: {e.code} {e.read()[:200]!r}") from e
    except OSError as e:
        raise KiteLoginError(f"token exchange failed: {e}") from e

    body = payload.get("data") or {}
    if not body.get("access_token"):
        raise KiteLoginError(f"no access_token in the response: {payload!r}")
    return body


def save(session: dict) -> Path:
    """Write the day's token where the providers look for it."""
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"access_token": session["access_token"],
               "user_id": session.get("user_id"),
               "login_day": date.today().isoformat()}
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass                                   # best effort; Windows ACLs differ
    return path


def stored_token() -> str | None:
    """The saved access token, if it was issued today. Kite expires them each morning."""
    path = token_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if payload.get("login_day") != date.today().isoformat():
        return None                            # stale: Kite rotates daily around 6am IST
    return payload.get("access_token")
