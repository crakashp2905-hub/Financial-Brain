"""Subcommands as thin formatters over ``services``, registered from a list.

``cli.py`` is 2,025 lines and 94KB, and 59 subcommands live in it with their argparse wiring
interleaved. Most of those are **operational** - ingest, prefetch, daily, dq, gaps, migrate - and
they have no service behind them because there is no domain question to ask: they move bytes and
report what moved. Rewriting them to route through a service would be churn with no reader served,
so they stay where they are.

What this package holds is the other kind: commands whose whole body is *ask a domain question,
print the answer*. Each module here registers its own parser and formats one service's output, and
``cli.py`` registers them in a loop.

## The payoff, and it is concrete

Every command below takes ``--json`` and it costs nothing to provide, because a service already
returns plain data. That is the test of whether the layer is real: if formatting and computation
were still tangled, a JSON flag would mean writing a second serialiser per command.

## The contract each module keeps

``register(sub)``   add one parser, set ``fn``.
``run(args)``       open a connection, call services, print, return an exit code.

Nothing here computes anything. A command that starts doing arithmetic is a service that has not
been written yet.
"""
from __future__ import annotations

import functools
import json as _json
from datetime import date, datetime

from ..config import load
from ..services import ServiceError
from ..storage.db import Database

#: Exit codes. 0 success, 1 a refusal the caller could act on, 2 a request that was malformed.
OK, REFUSED, BAD = 0, 1, 2

EXIT = {
    "not_found": REFUSED,
    "bad_request": BAD,
    "not_available": REFUSED,
    "not_estimable": REFUSED,
    "forbidden": REFUSED,
}


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%d").date()


def connect(*, write: bool = False):
    """A read-only connection unless a command genuinely writes.

    Read-only by default for the same reason the API is: most of these only read, and a bug in a
    formatter should not be able to touch an eleven-year archive.
    """
    return Database(load(), read_only=not write).connect()


def _default(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, set):
        return sorted(o)
    return str(o)


def emit(payload, args, printer) -> int:
    """``--json`` dumps the service's own data; otherwise ``printer`` formats it.

    The JSON branch prints the service's return value **unmodified**. A CLI that reshapes data
    before serialising it becomes a second, undocumented API, and the two drift.
    """
    if getattr(args, "json", False):
        print(_json.dumps(payload, indent=2, default=_default))
    else:
        printer(payload)
    return OK


def guard(fn):
    """Turn a ``ServiceError`` into a message and an exit code, not a traceback.

    ``functools.wraps`` rather than copying ``__name__`` and ``__doc__`` by hand: it also sets
    ``__wrapped__``, which is what lets ``inspect.getsource`` see the decorated function instead
    of this wrapper. A test that reads a command's source to check it does not open a writable
    connection needs that, and without it the test silently inspects the decorator instead.
    """
    @functools.wraps(fn)
    def wrapper(args) -> int:
        try:
            return fn(args)
        except ServiceError as exc:
            if getattr(args, "json", False):
                print(_json.dumps(exc.as_dict(), indent=2, default=_default))
            else:
                print(f"{exc.code}: {exc.message}")
                for k, v in exc.detail.items():
                    print(f"  {k}: {v}")
            return EXIT.get(exc.code, REFUSED)
    return wrapper


def add_json(parser):
    parser.add_argument("--json", action="store_true",
                        help="print the service's data unchanged")
    return parser


def _print_rows(rows: list[dict], limit: int = 40) -> None:
    """Aligned columns. Lives here rather than in ``cli`` so a command module never imports the
    file it is decomposing."""
    if not rows:
        print("  (no rows)")
        return
    cols = list(rows[0].keys())
    w = {k: max(len(k), *(len(str(r.get(k, ""))) for r in rows[:limit])) for k in cols}
    print("  " + "  ".join(k.ljust(w[k]) for k in cols))
    print("  " + "  ".join("-" * w[k] for k in cols))
    for r in rows[:limit]:
        print("  " + "  ".join(str(r.get(k, "")).ljust(w[k]) for k in cols))
    if len(rows) > limit:
        print(f"  ... {len(rows) - limit} more")


def modules():
    """The command modules, imported lazily so a broken one cannot take down `fb --help`."""
    from . import company, decide, opportunities, portfolio, trials, worldstate
    return (worldstate, company, trials, portfolio, opportunities, decide)


def register_all(sub) -> list[str]:
    names = []
    for mod in modules():
        names.append(mod.register(sub))
    return names
