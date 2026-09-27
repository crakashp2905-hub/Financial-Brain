"""The command layer: thin formatters, and a decomposition that stays decomposed.

The tests worth having here are structural. Whether a table aligns is not worth a test; whether a
command module has started computing something is, because that is the failure mode a
decomposition suffers - logic creeps back into the formatter, and the services layer becomes
decoration.

The ``--json`` test is the one that proves the layer is real: it costs nothing to provide only
because a service already returns plain data. If formatting and computation were still tangled,
every command would need a second serialiser.
"""
from __future__ import annotations

import argparse
import inspect
import json

import pytest

from financial_brain import commands


def _parser():
    p = argparse.ArgumentParser(prog="fb")
    sub = p.add_subparsers(dest="cmd", required=True)
    names = commands.register_all(sub)
    return p, sub, names


# --------------------------------------------------------------------- the decomposition
def test_every_command_module_registers_and_runs():
    for mod in commands.modules():
        assert hasattr(mod, "register"), f"{mod.__name__} has no register()"
        assert hasattr(mod, "run"), f"{mod.__name__} has no run()"
        assert inspect.signature(mod.register).parameters.keys() == {"sub"}


def test_all_commands_register_without_colliding():
    _, _, names = _parser()
    assert len(names) == len(set(names)), f"duplicate command names: {names}"
    # `research` is taken by the published-anomaly catalogue in cli.py; the trial ledger is
    # `trials`. The collision was real and is why the name differs.
    assert "research" not in names
    assert "trials" in names


def test_no_command_module_computes_anything():
    """A command that does arithmetic is a service that has not been written yet.

    Checked on imports rather than on tokens: a formatter has no business importing an
    evaluation engine, a covariance estimator or duckdb directly.
    """
    forbidden = ("evaluation", "risk.covariance", "risk.decompose", "risk.stress",
                 "portfolio.gate", "attribution.move", "opportunity.discovery",
                 "duckdb", "statistics")
    for mod in commands.modules():
        src = inspect.getsource(mod)
        for f in forbidden:
            assert f"import {f}" not in src and f"from ..{f}" not in src, \
                f"{mod.__name__} imports {f}; that belongs behind a service"


def test_command_modules_do_not_import_the_file_they_decompose():
    for mod in commands.modules():
        src = inspect.getsource(mod)
        assert "from ..cli" not in src and "import cli" not in src, \
            f"{mod.__name__} imports cli, which is circular and defeats the point"


def test_the_operational_commands_were_left_alone():
    """Ingest, prefetch, daily, dq and gaps move bytes and report what moved. Routing them
    through a service would be churn with no reader served, so they must still be in cli.py."""
    from financial_brain import cli

    src = inspect.getsource(cli)
    for name in ("cmd_ingest", "cmd_prefetch", "cmd_daily", "cmd_dq", "cmd_gaps",
                 "cmd_migrate", "cmd_status"):
        assert f"def {name}(" in src, f"{name} should not have been moved"


# ------------------------------------------------------------------------------- --json
def test_every_command_accepts_json():
    p, _, names = _parser()
    for name in names:
        # Each command needs the minimum required arguments to parse; --json must survive.
        argv = {"world": [name, "state"],
                "company": [name, "find", "RELIANCE"],
                "trials": [name, "bar"],
                "portfolio": [name, "book", "--as-of", "2026-09-18"],
                "opportunities": [name, "--as-of", "2026-09-18"],
                "decide": [name, "list"]}[name]
        args = p.parse_args([*argv, "--json"])
        assert args.json is True, f"{name} does not honour --json"


def test_emit_passes_the_service_payload_through_unchanged(capsys):
    """A CLI that reshapes data before serialising becomes a second, undocumented API and the
    two drift."""
    payload = {"a": 1, "nested": {"b": [1, 2]}, "when": None}
    args = argparse.Namespace(json=True)
    commands.emit(payload, args, lambda _: pytest.fail("printer must not run"))
    assert json.loads(capsys.readouterr().out) == payload


def test_emit_uses_the_printer_when_json_is_off(capsys):
    args = argparse.Namespace(json=False)
    commands.emit({"x": 1}, args, lambda p: print(f"formatted {p['x']}"))
    assert capsys.readouterr().out.strip() == "formatted 1"


def test_dates_serialise_rather_than_raising():
    from datetime import date

    assert commands._default(date(2026, 9, 18)) == "2026-09-18"
    assert commands._default({1, 3, 2}) == [1, 2, 3]


# ------------------------------------------------------------------- errors and exit codes
def test_a_service_error_becomes_a_message_and_an_exit_code(capsys):
    from financial_brain.services import BAD_REQUEST, ServiceError

    @commands.guard
    def boom(args):
        raise ServiceError(BAD_REQUEST, "give at least two characters", got="R")

    code = boom(argparse.Namespace(json=False))
    out = capsys.readouterr().out
    assert code == commands.BAD
    assert "bad_request" in out and "two characters" in out
    assert "got: R" in out


def test_a_service_error_serialises_under_json(capsys):
    from financial_brain.services import NOT_ESTIMABLE, ServiceError

    @commands.guard
    def boom(args):
        raise ServiceError(NOT_ESTIMABLE, "too thin", positions=2)

    code = boom(argparse.Namespace(json=True))
    assert code == commands.REFUSED
    assert json.loads(capsys.readouterr().out) == {
        "error": "not_estimable", "message": "too thin", "positions": 2}


def test_exit_codes_separate_a_bad_request_from_a_refusal():
    """2 means the caller asked wrongly and can fix it; 1 means the answer is no or unknown.
    Collapsing them makes a script unable to tell a typo from a refusal."""
    assert commands.EXIT["bad_request"] == commands.BAD == 2
    assert commands.EXIT["not_found"] == commands.REFUSED == 1
    assert commands.EXIT["not_estimable"] == commands.REFUSED
    assert commands.EXIT["forbidden"] == commands.REFUSED
    assert set(commands.EXIT) >= {"not_found", "bad_request", "not_available",
                                  "not_estimable", "forbidden"}


def test_connections_are_read_only_unless_a_command_writes():
    src = inspect.getsource(commands.connect)
    assert "read_only=not write" in src
    # `decide challenge` records an attempt and is the only writer among the new commands.
    from financial_brain.commands import decide

    assert 'write=args.action == "challenge"' in inspect.getsource(decide.run)
    for mod in commands.modules():
        if mod is decide:
            continue
        assert "connect(write=True)" not in inspect.getsource(mod), \
            f"{mod.__name__} opens a writable connection"
