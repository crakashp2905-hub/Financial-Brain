from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from financial_brain.paper.options import Quote
from financial_brain.paper.options_plan import OptionContract
from financial_brain.strategies.four_node.options import (
    OptionCandidateTerms,
    UnderlyingThesis,
    route_underlying_thesis,
)


NOW = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)
HASH = "a" * 64


def _candidate(option_type: str, token: int, *, underlying: str = "NIFTY",
               symbol: str | None = None, observed_at: datetime = NOW) -> OptionCandidateTerms:
    contract_symbol = symbol or f"NIFTY26DEC20000{option_type}"
    quote = Quote(token, contract_symbol, date(2026, 12, 31), 50, observed_at,
                  99.0, 100.0, 500, 500)
    return OptionCandidateTerms(OptionContract(underlying, option_type, 20000.0, quote),
                                stop_premium=80.0, target_premium=120.0)


def test_underlying_buy_routes_only_to_separately_audited_ce_candidates():
    thesis = UnderlyingThesis("sig-1", "NIFTY", "BUY", NOW, 20010.0, 19950.0, HASH)
    result = route_underlying_thesis(
        thesis, (_candidate("CE", 10), _candidate("PE", 11)), lots=1,
        max_quote_age=timedelta(seconds=2),
    )
    assert result["underlying_thesis"]["record_type"] == "UNDERLYING_THESIS"
    decision = result["option_decision"]
    assert decision["record_type"] == "OPTION_CANDIDATE_DECISION"
    assert decision["parent_signal_id"] == "sig-1"
    assert decision["intended_option_type"] == "CE"
    assert decision["status"] == "CANDIDATES_AVAILABLE"
    assert [plan["option_type"] for plan in decision["plans"]] == ["CE"]
    assert decision["candidate_selection"] == "NOT_RANKED_OR_SELECTED"
    assert decision["underlying_return_is_option_pnl"] is False
    assert decision["broker_order_created"] is False
    assert decision["rejected_candidates"][0]["reason"] == "DIRECTION_CONTRACT_MISMATCH"


def test_underlying_sell_routes_to_pe_and_missing_stale_or_wrong_underlying_is_no_trade():
    thesis = UnderlyingThesis("sig-2", "NIFTY", "SELL", NOW, 20000.0, 20050.0, HASH)
    stale = _candidate("PE", 20, observed_at=NOW - timedelta(minutes=1))
    wrong_underlying = _candidate("PE", 21, underlying="BANKNIFTY")
    result = route_underlying_thesis(
        thesis, (stale, wrong_underlying), lots=1, max_quote_age=timedelta(seconds=2),
    )
    decision = result["option_decision"]
    assert decision["intended_option_type"] == "PE"
    assert decision["status"] == "NO_TRADE"
    assert decision["plans"] == []
    assert {row["reason"] for row in decision["rejected_candidates"]} == {
        "OPTION_PLAN_REJECTED",
        "UNDERLYING_ID_MISMATCH",
    }
    stale_rejection = next(row for row in decision["rejected_candidates"]
                           if row["reason"] == "OPTION_PLAN_REJECTED")
    assert stale_rejection["detail"] == "contract quote is future-dated or stale at decision time"
