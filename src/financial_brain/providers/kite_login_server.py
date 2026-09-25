"""The loopback receiver, bound before the browser is ever opened.

Splitting this out of ``kite_login`` exists for one reason, and it is a security one.

The first version opened the browser and *then* bound the port. That leaves a window: if
anything else already held the port, Kite would redirect to it and hand that process the
``request_token``. The window is small and needs a hostile local process to exploit, but
it is a real hole and it is free to close - so the port is claimed first, and the browser
is only opened once we own it.

``Receiver`` is a context manager for exactly that ordering::

    with Receiver(port) as rx:      # the socket is ours from here
        webbrowser.open(url)       # only now is Kite told where to send the token
        token = rx.wait()

If the bind fails, nothing is opened and the error says which port is busy - which is the
right outcome, because the alternative is sending a credential to an unknown listener.
"""
from __future__ import annotations

import urllib.parse
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer

DEFAULT_PORT = 8765
TIMEOUT_S = 300

_PAGE_OK = ("<h2>Kite login captured.</h2>"
            "<p>You can close this tab and return to the terminal.</p>")
_PAGE_NO = ("<h2>No request token in the redirect.</h2>"
            "<p>Check that the app's redirect URL matches this address exactly.</p>")


class _ExclusiveHTTPServer(HTTPServer):
    """An HTTPServer that will not share its port.

    HTTPServer sets allow_reuse_address = 1, which on Windows lets a *second*
    process bind a port a first process already holds - so "we own the port" would not
    have been true, and the token could still have gone to a squatter. Turning it off,
    and asking Windows for exclusive use explicitly, makes the claim real.
    """

    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):        # Windows
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class PortUnavailable(RuntimeError):
    """The loopback port could not be claimed, so no login was started."""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):                                    # noqa: N802 - stdlib name
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        token = (params.get("request_token") or [None])[0]
        if token:
            self.server.captured = token                 # type: ignore[attr-defined]
            body = _PAGE_OK
        else:
            status = (params.get("status") or [None])[0]
            self.server.failure = (                      # type: ignore[attr-defined]
                f"no request_token in the redirect (status={status})")
            body = _PAGE_NO
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):                        # noqa: A003 - stdlib name
        """Silenced deliberately: the default access log prints the path, and the path
        carries the request token."""


class Receiver:
    """Owns the loopback port for the duration of one login."""

    def __init__(self, port: int = DEFAULT_PORT, timeout: int = TIMEOUT_S):
        self.port, self.timeout, self._server = port, timeout, None

    def __enter__(self) -> "Receiver":
        try:
            # Loopback only, never the wildcard address: the redirect carries a
            # credential, and nothing outside this machine has any business reaching it.
            self._server = _ExclusiveHTTPServer(("127.0.0.1", self.port), _Handler)
        except OSError as e:
            raise PortUnavailable(
                f"127.0.0.1:{self.port} is already in use, so the login was not started - "
                f"a redirect now would hand the request token to whatever holds it. "
                f"Free the port or pass a different one.") from e
        self._server.timeout = self.timeout
        self._server.captured = None                     # type: ignore[attr-defined]
        self._server.failure = None                      # type: ignore[attr-defined]
        return self

    def __exit__(self, *exc) -> None:
        if self._server is not None:
            self._server.server_close()
            self._server = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/kite/callback"

    def wait(self) -> str:
        """Serve one redirect and return the request token it carried."""
        if self._server is None:
            raise PortUnavailable("the receiver is not running; use it as a context manager")
        self._server.handle_request()
        token = getattr(self._server, "captured", None)
        if token:
            return token
        raise PortUnavailable(
            getattr(self._server, "failure", None)
            or f"no redirect arrived on 127.0.0.1:{self.port} within {self.timeout}s")
