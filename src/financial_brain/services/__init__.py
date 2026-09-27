"""Domain operations, callable from anything, formatting nothing.

``cli.py`` is 76KB and has become the application rather than an interface to it. The operations
live inside argparse handlers, which means the only way to invoke them is to build a command line,
the only way to test them is to capture stdout, and a second interface - an API, a scheduler, a
notebook - has to either duplicate the logic or shell out.

This package is the layer that fixes that, and the contract is narrow enough to be worth stating:

**A service takes a connection and typed arguments, and returns plain data.** No printing, no
formatting, no argparse, no ``sys.exit``. A caller decides how to render.

**A service does not open the database.** The connection is passed in, so the caller controls
the transaction and whether it is read-only - which matters because most of these are read-only
and should be opened that way.

**A service raises.** It does not print an error and return None. ``ServiceError`` carries a
machine-readable ``code`` so an API can map it to a status and a CLI can map it to an exit code
without either one parsing a message.

    CLI      ->  services  ->  domain engines  ->  storage
    API      ->  services  ->        "              "

## What is deliberately not here

No business logic. Every service is a thin assembly of the engines that already exist -
``evaluation``, ``risk``, ``portfolio``, ``decisions``, ``opportunity``, ``attribution``. If a
service starts computing something, that computation belongs in an engine where it can be tested
against planted answers.
"""
from __future__ import annotations


class ServiceError(Exception):
    """A request a service will not fulfil, with a code a caller can branch on."""

    def __init__(self, code: str, message: str, **detail):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def as_dict(self) -> dict:
        return {"error": self.code, "message": self.message, **self.detail}


#: Codes services raise. Kept in one place so an API's status mapping is exhaustive rather than
#: a chain of string comparisons.
NOT_FOUND = "not_found"
BAD_REQUEST = "bad_request"
NOT_AVAILABLE = "not_available"        # the data does not exist in this archive
NOT_ESTIMABLE = "not_estimable"       # the data exists but is too thin to answer from
FORBIDDEN = "forbidden"               # refused by policy, not by data
