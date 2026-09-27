"""The services layer: its contract, and the two paths it must never open.

The contract is narrow and worth testing as a contract rather than per-function: a service takes a
connection, returns plain data, raises ``ServiceError`` with a code, and never prints. Most of these
tests assert the *shape* of the layer, because the behaviour they wrap is tested where it lives.

The two that matter most are the refusals. An API route is one line away from any service, so a
service that can approve a decision or reach a broker is an API that can, and "the framework is
only on localhost" is not a control.
"""
from __future__ import annotations

import inspect
from datetime import date, timedelta

import duckdb
import pytest

from financial_brain import services
from financial_brain.services import companies, decisions, market, portfolio, research

AS_OF = date(2026, 6, 15)
SERVICE_MODULES = (market, companies, research, portfolio, decisions)


# ------------------------------------------------------------------------- the contract
def test_no_service_prints_or_exits():
    """A service that prints cannot be used by an API, and one that exits takes the process
    with it. Both are easy to add by accident when moving code out of a CLI."""
    for mod in SERVICE_MODULES:
        src = inspect.getsource(mod)
        assert "\n    print(" not in src, f"{mod.__name__} prints"
        assert "sys.exit" not in src, f"{mod.__name__} exits"
        assert "argparse" not in src, f"{mod.__name__} knows about argparse"


def test_every_service_takes_a_connection_first():
    """The caller owns the transaction and whether it is read-only. A service that opens its own
    connection cannot be given a read-only one."""
    for mod in SERVICE_MODULES:
        for name, fn in vars(mod).items():
            if name.startswith("_") or not inspect.isfunction(fn):
                continue
            if fn.__module__ != mod.__name__:
                continue
            params = list(inspect.signature(fn).parameters)
            assert params and params[0] == "con", f"{mod.__name__}.{name}{tuple(params)}"


def test_service_error_carries_a_code_a_caller_can_branch_on():
    e = services.ServiceError(services.NOT_ESTIMABLE, "too thin", positions=2)
    assert e.code == services.NOT_ESTIMABLE
    assert e.as_dict() == {"error": "not_estimable", "message": "too thin", "positions": 2}


def test_the_api_maps_every_service_code():
    """A new code with no status mapping falls through to 500. That is the right default and it
    should be a deliberate gap, not an unnoticed one."""
    from financial_brain.api import app as api

    codes = {v for k, v in vars(services).items()
             if k.isupper() and isinstance(v, str) and not k.startswith("_")}
    assert codes <= set(api.STATUS), f"unmapped: {codes - set(api.STATUS)}"


# ------------------------------------------------------------------------ the refusals
def test_a_service_cannot_approve_a_decision_or_reach_a_broker():
    """The broker-enabling parameter must not be reachable through any service signature.

    Checked on signatures rather than on source text: the module docstring discusses
    ``execution_enabled`` at length, and a substring search over prose would fail on the
    explanation of why the thing is forbidden.
    """
    assert decisions.NEVER_VIA_SERVICE == {"HUMAN_APPROVED", "PROPOSED_TO_BROKER"}
    for name, fn in vars(decisions).items():
        if name.startswith("_") or not inspect.isfunction(fn):
            continue
        if fn.__module__ != decisions.__name__:
            continue
        params = inspect.signature(fn).parameters
        assert "execution_enabled" not in params, f"{name} can enable the broker path"
        assert "constitution" not in params, f"{name} can substitute the constitution"


def test_advance_refuses_an_agent_actor():
    con = duckdb.connect(":memory:")
    with pytest.raises(services.ServiceError) as e:
        decisions.advance(con, decision_id="dc_x", actor="agent:analyst")
    assert e.value.code == services.FORBIDDEN


def test_advance_refuses_an_empty_actor():
    con = duckdb.connect(":memory:")
    with pytest.raises(services.ServiceError) as e:
        decisions.advance(con, decision_id="dc_x", actor="")
    assert e.value.code == services.FORBIDDEN


def test_the_api_exposes_no_write_route_by_default():
    """Every default route is a GET. The two POSTs appear only when writes are asked for in
    code, and neither moves a position or a state machine."""
    from financial_brain.api import app as api

    src = inspect.getsource(api)
    # The POST decorators must sit inside the allow_writes branch.
    head, _, tail = src.partition("if allow_writes:")
    assert "@app.post" not in head, "a POST route is registered unconditionally"
    assert "@app.post" in tail


def test_the_api_binds_localhost_by_default_and_warns_otherwise():
    from financial_brain.api import app as api

    assert inspect.signature(api.serve).parameters["host"].default == "127.0.0.1"
    assert inspect.signature(api.serve).parameters["allow_writes"].default is False
    assert "WARNING" in inspect.getsource(api.serve)


def test_fastapi_absence_gives_an_instruction_not_a_traceback():
    from financial_brain.api import app as api

    src = inspect.getsource(api._require_fastapi)
    assert 'pip install -e ".[api]"' in src


# ----------------------------------------------------------------- errors, not silence
def _empty_db():
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE adjusted_prices (business_date DATE, isin VARCHAR,
                   lineage VARCHAR, close_adj DOUBLE, turnover DOUBLE)""")
    con.execute("""CREATE TABLE market_regime (business_date DATE, version VARCHAR,
                   regime VARCHAR)""")
    con.execute("""CREATE TABLE index_levels (business_date DATE, index_name VARCHAR,
                   close_level DOUBLE)""")
    con.execute("""CREATE TABLE universe_snapshots (business_date DATE, isin VARCHAR,
                   exchange VARCHAR, instrument_type VARCHAR, ticker VARCHAR,
                   turnover DOUBLE)""")
    con.execute("""CREATE TABLE announcements (business_date DATE, isin VARCHAR,
                   event_type VARCHAR, materiality VARCHAR, headline VARCHAR)""")
    con.execute("""CREATE TABLE security_listings (isin VARCHAR, ticker VARCHAR,
                   first_seen DATE, last_seen DATE)""")
    con.execute("""CREATE TABLE security_reference (isin VARCHAR, company_name VARCHAR,
                   listing_date DATE)""")
    con.execute("""CREATE TABLE promoter_groups (group_id VARCHAR, anchor VARCHAR,
                   member VARCHAR)""")
    con.execute("""CREATE TABLE paper_trades (decision_id VARCHAR, isin VARCHAR,
                   status VARCHAR, weight DOUBLE, entry_date DATE, entry_price DOUBLE,
                   bucket VARCHAR, excess DOUBLE)""")
    con.execute("""CREATE TABLE features (business_date DATE, lineage VARCHAR,
                   adv20 DOUBLE, mom_12_1 DOUBLE, dist_52w_high DOUBLE, vol_60 DOUBLE,
                   ret_20d DOUBLE, rsi_14 DOUBLE, above_ma200 BOOLEAN)""")
    con.execute("""CREATE TABLE security_lineage (isin VARCHAR, lineage VARCHAR)""")
    con.execute("""CREATE TABLE evaluation_runs (run_at TIMESTAMP, feature VARCHAR,
                   horizon INTEGER, dates INTEGER, mean_ic DOUBLE, ic_t DOUBLE,
                   sharpe DOUBLE, deflated_sharpe DOUBLE, verdict VARCHAR,
                   params VARCHAR)""")
    return con


def test_an_empty_archive_raises_not_available_rather_than_returning_zeros():
    """Zeros would be indistinguishable from a quiet market."""
    con = _empty_db()
    with pytest.raises(services.ServiceError) as e:
        market.world_state(con)
    assert e.value.code == services.NOT_AVAILABLE


def test_a_missing_index_names_what_was_missing():
    con = _empty_db()
    con.execute("INSERT INTO adjusted_prices VALUES (?,?,?,?,?)",
                [AS_OF, "INE1", "L1", 100.0, 1e8])
    with pytest.raises(services.ServiceError) as e:
        market.indices(con, names=["Nifty 50"], as_of=AS_OF)
    assert e.value.code == services.NOT_FOUND
    assert "Nifty 50" in e.value.message


def test_a_two_character_query_is_refused_before_it_scans():
    con = _empty_db()
    with pytest.raises(services.ServiceError) as e:
        companies.resolve(con, "R")
    assert e.value.code == services.BAD_REQUEST


def test_a_book_too_small_for_a_covariance_says_so():
    con = _empty_db()
    con.execute("INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?)",
                ["d1", "INE1", "open", 0.05, AS_OF - timedelta(days=5), 100.0,
                 "large", None])
    with pytest.raises(services.ServiceError) as e:
        portfolio.risk(con, as_of=AS_OF, window_days=400)
    assert e.value.code == services.NOT_ESTIMABLE
    assert "not meaningful" in e.value.message


def test_a_weight_outside_zero_to_one_is_refused_before_any_estimation():
    con = _empty_db()
    for w in (0.0, -0.1, 1.5):
        with pytest.raises(services.ServiceError) as e:
            portfolio.gate(con, isin="INE1", weight=w, as_of=AS_OF, capital_inr=1e7)
        assert e.value.code == "bad_request"


def test_the_bar_is_recomputed_not_stored():
    """A bar quoted from when a test was run is out of date the moment anything else is
    tested, which is how a stale PROMOTE came to sit in the real ledger."""
    con = _empty_db()
    first = research.bar(con)["bar"]
    con.executemany(
        "INSERT INTO evaluation_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(None, "f", 20, 10, 0.01, 2.0, 0.1, 0.5, "REJECT", "{}") for _ in range(40)])
    assert research.bar(con)["bar"] > first


def test_what_has_been_tried_flags_a_promote_from_before_the_cost_model_epoch():
    from financial_brain.opportunity import discovery

    con = _empty_db()
    before = discovery.COST_MODEL_EPOCH - timedelta(days=5)
    con.execute("INSERT INTO evaluation_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                [before, "mom_12_1", 20, 100, 0.02, 3.7, 0.2, 0.9, "PROMOTE", "{}"])
    r = research.what_has_been_tried(con)
    row = next(x for x in r["features"] if x["feature"] == "mom_12_1")
    assert row["ever_promoted"] is True
    assert row["promote_is_stale"] is True
    assert row["current_verdict"] == "REJECT"
    assert r["totals"]["currently_promoted"] == 0
    assert r["totals"]["stale_promotes"] == 1
