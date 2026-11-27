"""The edge candidates of ``PRE-REGISTERED edges for every mode`` run on paper in the zoo, passed or failed.

Each returns target weights for the next session (lineages, "NIFTY50"), with the same definitions as the tests in
``evaluation/edges.py``. The verdict each earned is kept beside it (``VERDICT``), so a paper record is never read
as a new discovery. Monthly strategies rebalance on the first session of a month (the test used the last close of
the month before - one session's shift); state between evenings is kept in ``zoo_state``.

    edge_p1_residual_mom      P1 residual momentum, top 20 of 500, monthly
    edge_p2_volmanaged_mom    P2 momentum top 20 scaled by 12% / trailing 6-month vol (cap 1.5x), monthly
    edge_p3_frog_in_pan       P3 momentum top 20 among the continuous-information half, monthly
    edge_h003_riskadj_mom     H003 12-1 momentum / 250-day volatility, top 20, monthly (added 2026-11-18)
    edge_h005_industry_mom    H005 best 20 names in the top 3 industries by 6-month return, monthly (added 2026-11-18)
    edge_s1_no_news_reversal  S1 bottom decile of 5-session excess without material news, every 5 sessions
    edge_s2_reaction_drift    S2 top-fifth results reaction, bought the session after, held 20 sessions
    edge_f1_long_buildup      F1 price up and OI change top quintile, F&O names, every 5 sessions
    edge_f2_short_covering    F2 price up and OI change bottom quintile: short, long Nifty of equal notional
    edge_z1_turn_of_month     Z1 long Nifty from 2 sessions before month end to the 3rd session of the next
    edge_i1_index_intraday    I1 settled each evening from the day's Nifty minute bars (sign of the move to 09:45,
                              position 15:00-15:29), when a Kite token is present
"""
from __future__ import annotations

import json
import hashlib
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..costs.india import CostModel, Segment, Side

VERDICT = {"edge_p1_residual_mom": "failed (t -0.3 / +1.8)", "edge_p2_volmanaged_mom": "failed (t +2.3 / +1.2; halves drawdown)",
           "edge_p3_frog_in_pan": "failed (t +3.3 / +1.7)", "edge_s1_no_news_reversal": "failed (t -4.3 / -0.6)",
           "edge_s2_reaction_drift": "failed (t -0.8 / +0.8)", "edge_f1_long_buildup": "failed (t +0.2 / -2.0)",
           "edge_f2_short_covering": "failed (t +0.6 / -0.5)", "edge_z1_turn_of_month": "failed (t +1.3 / +0.9)",
           "edge_i1_index_intraday": "failed (gross 0.0 bp, t -7.5 / -8.1 net)",
           "results_reaction_short": "passed on unseen names (N2: t -2.6 / -4.7); forward test"}
KIND = {k: "edge" for k in VERDICT}


def _first_of_month(z, day: date) -> bool:
    prev = z.execute("SELECT MAX(day) FROM zoo_pnl WHERE day < ?", [day]).fetchone()[0]
    return prev is None or pd.Timestamp(prev).to_period("M") != pd.Timestamp(day).to_period("M")


def _panel(rcon, day: date):
    from ..evaluation import edges as E
    P = E.load(rcon, start=str(pd.Timestamp(day) - pd.DateOffset(years=4))[:10])
    P = {k: v.loc[: pd.Timestamp(day)] for k, v in P.items()}
    return P


MONTHLY_BOOKS = ("edge_p1_residual_mom", "edge_p2_volmanaged_mom", "edge_p3_frog_in_pan",
                 "edge_h003_riskadj_mom", "edge_h005_industry_mom")


def size_monthly_candidate(*, strategy: str, targets: dict[str, float],
                           lineage_to_ticker: dict[str, str], prices: dict[str, float],
                           capital_inr: float, min_order_inr: float = 5_000.0,
                           costs: CostModel | None = None) -> dict:
    """Convert one frozen monthly target set to whole-share, cost-aware paper quantities.

    This reuses the human-review ticket sizer. It does not submit or persist orders.
    Missing instrument mappings and prices are retained as explicit skipped rows; no
    unresolved lineage is silently dropped.
    """
    from ..execution.ticket import size_book

    if strategy not in MONTHLY_BOOKS:
        raise ValueError(f"strategy must be one of {MONTHLY_BOOKS}")
    normalized: dict[str, float] = {}
    for lineage, raw_weight in targets.items():
        if not isinstance(lineage, str) or not lineage.strip():
            raise ValueError("target lineages must be non-empty strings")
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float)) \
                or not np.isfinite(raw_weight) or not 0 <= raw_weight <= 1:
            raise ValueError(f"{lineage}: target weight must be finite and in [0, 1]")
        normalized[lineage] = float(raw_weight)
    if sum(normalized.values()) > 1.0 + 1e-9:
        raise ValueError("monthly target weights cannot exceed fully invested capital")

    book: list[dict] = []
    lineage_by_ticker: dict[str, str] = {}
    skipped: list[dict] = []
    for lineage, weight in sorted(normalized.items()):
        ticker = lineage_to_ticker.get(lineage)
        if not isinstance(ticker, str) or not ticker.strip():
            skipped.append({"lineage": lineage, "why": "no point-in-time ticker mapping"})
            continue
        if ticker in lineage_by_ticker:
            raise ValueError(f"multiple target lineages map to ticker {ticker}")
        lineage_by_ticker[ticker] = lineage
        book.append({"ticker": ticker, "weight": weight})

    sized = size_book(book, capital_inr=capital_inr, prices=prices,
                      min_order_inr=min_order_inr, costs=costs)
    positions = [{**row, "lineage": lineage_by_ticker[row["ticker"]]}
                 for row in sized["positions"]]
    skipped.extend({**row, "lineage": lineage_by_ticker[row["ticker"]]}
                   for row in sized["skipped"])
    return {
        "strategy": strategy,
        "target_hash": monthly_target_hashes({strategy: normalized})[strategy],
        "status": ("NO_CANDIDATES" if not normalized else
                   "SIZED" if positions else "NO_EXECUTABLE_POSITIONS"),
        "capital_inr": sized["capital"],
        "positions": positions,
        "skipped": skipped,
        "deployed_inr": sized["deployed"],
        "cash_committed_inr": sized["cash_committed"],
        "cash_inr": sized["cash"],
        "entry_fees_inr": sized["entry_fees_total"],
        "expected_round_trip_fees_inr": sized["fees_total"],
        "physical_orders_submitted": False,
    }


def plan_monthly_rebalance(*, strategy: str, targets: dict[str, float],
                           current_shares: dict[str, int], lineage_to_ticker: dict[str, str],
                           prices: dict[str, float], liquidity_bucket_by_ticker: dict[str, str],
                           capital_inr: float, free_cash_inr: float,
                           decision_date: date, next_session_date: date,
                           turnover_budget_inr: float, cash_reserve_fraction: float = 0.05,
                           min_order_inr: float = 5_000.0,
                           costs: CostModel | None = None) -> dict:
    """Plan next-session monthly paper rebalance with explicit cash and turnover gates.

    Sale proceeds are treated as unsettled and cannot fund buys in this plan. Outputs
    are reviewable paper intents only; no fill or broker action is simulated here.
    """
    if next_session_date <= decision_date:
        raise ValueError("next executable session must follow the signal date")
    if not np.isfinite(capital_inr) or capital_inr <= 0:
        raise ValueError("capital_inr must be finite and positive")
    if not np.isfinite(free_cash_inr) or not 0 <= free_cash_inr <= capital_inr:
        raise ValueError("free_cash_inr must be finite and within account capital")
    if not np.isfinite(turnover_budget_inr) or turnover_budget_inr < 0:
        raise ValueError("turnover_budget_inr must be finite and non-negative")
    if not np.isfinite(cash_reserve_fraction) or not 0 <= cash_reserve_fraction < 1:
        raise ValueError("cash_reserve_fraction must be finite and in [0, 1)")
    if strategy not in MONTHLY_BOOKS:
        raise ValueError(f"strategy must be one of {MONTHLY_BOOKS}")
    for lineage, shares in current_shares.items():
        if not isinstance(lineage, str) or not lineage.strip():
            raise ValueError("current position lineages must be non-empty strings")
        if isinstance(shares, bool) or not isinstance(shares, int) or shares < 0:
            raise ValueError(f"{lineage}: current shares must be a non-negative whole number")

    normalized_targets: dict[str, float] = {}
    for lineage, weight in targets.items():
        if not isinstance(lineage, str) or not lineage.strip():
            raise ValueError("target lineages must be non-empty strings")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) \
                or not np.isfinite(weight) or not 0 <= weight <= 1:
            raise ValueError(f"{lineage}: target weight must be finite and in [0, 1]")
        normalized_targets[lineage] = float(weight)
    if sum(normalized_targets.values()) > 1.0 + 1e-9:
        raise ValueError("monthly target weights cannot exceed fully invested capital")

    if not normalized_targets:
        return {"strategy": strategy, "status": "NO_CANDIDATE_TARGETS",
                "decision_date": decision_date.isoformat(),
                "next_session_date": next_session_date.isoformat(), "orders": [],
                "rejected": [{"why": "no candidate targets; existing positions are retained"}],
                "physical_orders_submitted": False}
    lineages = set(normalized_targets) | {key for key, shares in current_shares.items() if shares}
    lineage_tickers: dict[str, str] = {}
    ticker_lineages: dict[str, str] = {}
    missing: list[dict[str, str]] = []
    model = costs or CostModel()
    for lineage in sorted(lineages):
        ticker = lineage_to_ticker.get(lineage)
        if not isinstance(ticker, str) or not ticker.strip():
            missing.append({"lineage": lineage, "why": "no point-in-time ticker mapping"})
            continue
        if ticker in ticker_lineages and ticker_lineages[ticker] != lineage:
            raise ValueError(f"multiple position lineages map to ticker {ticker}")
        ticker_lineages[ticker] = lineage
        lineage_tickers[lineage] = ticker
        price = prices.get(ticker)
        if isinstance(price, bool) or not isinstance(price, (int, float)) \
                or not np.isfinite(price) or price <= 0:
            missing.append({"lineage": lineage, "why": "no finite positive next-session reference price"})
        bucket = liquidity_bucket_by_ticker.get(ticker)
        if not isinstance(bucket, str) or bucket not in model.impact:
            missing.append({"lineage": lineage, "why": "missing or unknown liquidity cost bucket"})
    if missing:
        return {"strategy": strategy, "status": "INCOMPLETE_INPUTS",
                "decision_date": decision_date.isoformat(),
                "next_session_date": next_session_date.isoformat(), "orders": [],
                "rejected": missing, "physical_orders_submitted": False}

    current_market_value = sum(current_shares.get(lineage, 0) * float(prices[lineage_tickers[lineage]])
                               for lineage in lineages)
    if not np.isclose(free_cash_inr + current_market_value, capital_inr, rtol=0.0, atol=0.01):
        raise ValueError("free cash plus current marked positions must reconcile to capital_inr")

    reserve = capital_inr * cash_reserve_fraction
    desired = size_monthly_candidate(
        strategy=strategy, targets=normalized_targets, lineage_to_ticker=lineage_tickers,
        prices={ticker: float(prices[ticker]) for ticker in ticker_lineages},
        capital_inr=capital_inr - reserve, min_order_inr=min_order_inr, costs=model,
    )
    desired_shares = {row["lineage"]: row["shares"] for row in desired["positions"]}
    candidate_order_rows: list[dict] = []
    for lineage in sorted(lineages):
        delta = desired_shares.get(lineage, 0) - current_shares.get(lineage, 0)
        if delta:
            candidate_order_rows.append({"lineage": lineage, "ticker": lineage_tickers[lineage],
                                         "side": "BUY" if delta > 0 else "SELL",
                                         "requested_shares": abs(delta),
                                         "reference_price": float(prices[lineage_tickers[lineage]]),
                                         "target_weight": normalized_targets.get(lineage, 0.0)})
    requested_turnover = sum(row["requested_shares"] * row["reference_price"]
                             for row in candidate_order_rows)
    if requested_turnover > turnover_budget_inr + 1e-9:
        return {"strategy": strategy, "status": "REJECTED_TURNOVER_BUDGET",
                "decision_date": decision_date.isoformat(),
                "next_session_date": next_session_date.isoformat(),
                "requested_turnover_inr": requested_turnover,
                "turnover_budget_inr": turnover_budget_inr, "orders": [],
                "rejected": [{**row, "why": "aggregate turnover exceeds frozen budget"}
                             for row in candidate_order_rows],
                "physical_orders_submitted": False}

    reserve_available_cash = max(0.0, free_cash_inr - reserve)
    cash_for_buys = reserve_available_cash
    orders: list[dict] = []
    rejected = list(desired["skipped"])
    estimated_costs = 0.0
    executed_turnover = 0.0
    for row in sorted(candidate_order_rows,
                      key=lambda item: (item["side"] != "SELL", -item["target_weight"], item["lineage"])):
        side = Side.BUY if row["side"] == "BUY" else Side.SELL
        price = row["reference_price"]
        bucket = liquidity_bucket_by_ticker[row["ticker"]]
        quantity = row["requested_shares"]
        if side is Side.BUY:
            low, high = 0, quantity
            while low < high:
                candidate = (low + high + 1) // 2
                notional = candidate * price
                fee = model.one_way(turnover=notional, side=side, segment=Segment.DELIVERY,
                                    bucket=bucket, include_impact=True)["total"]
                if notional + fee <= cash_for_buys + 1e-9:
                    low = candidate
                else:
                    high = candidate - 1
            quantity = low
            if quantity == 0:
                rejected.append({**row, "why": "cash reserve leaves insufficient funds for one share and costs"})
                continue
            if quantity < row["requested_shares"]:
                rejected.append({**row, "why": "cash reserve reduced requested quantity to whole shares",
                                 "accepted_shares": quantity})
        notional = quantity * price
        fee = model.one_way(turnover=notional, side=side, segment=Segment.DELIVERY,
                            bucket=bucket, include_impact=True)
        orders.append({**row, "shares": quantity, "notional_inr": notional,
                       "estimated_cost_inr": fee["total"], "cost_components": fee,
                       "status": "PAPER_REVIEW_REQUIRED"})
        estimated_costs += fee["total"]
        executed_turnover += notional
        if side is Side.BUY:
            cash_for_buys -= notional + fee["total"]

    return {
        "strategy": strategy,
        "status": "READY_FOR_PAPER_REVIEW" if orders else "NO_ACTION",
        "decision_date": decision_date.isoformat(),
        "next_session_date": next_session_date.isoformat(),
        "target_hash": desired["target_hash"],
        "capital_inr": capital_inr,
        "cash_reserve_inr": reserve,
        "free_cash_inr": free_cash_inr,
        "cash_available_for_buys_inr": reserve_available_cash,
        "cash_after_buy_intents_inr": free_cash_inr - sum(
            row["notional_inr"] + row["estimated_cost_inr"]
            for row in orders if row["side"] == "BUY"),
        "unsettled_sell_proceeds_inr": sum(
            row["notional_inr"] - row["estimated_cost_inr"]
            for row in orders if row["side"] == "SELL"),
        "requested_turnover_inr": requested_turnover,
        "planned_turnover_inr": executed_turnover,
        "turnover_budget_inr": turnover_budget_inr,
        "estimated_costs_inr": estimated_costs,
        "orders": orders,
        "rejected": rejected,
        "physical_orders_submitted": False,
    }


def _capture_target(name: str, compute, targets: dict[str, dict], errors: dict[str, str]) -> None:
    """Isolate one paper book's data/selection failure and preserve its prior targets upstream."""
    try:
        targets[name] = compute()
    except Exception as exc:  # noqa: BLE001 - one desk's missing data must not skip other books
        errors[name] = f"{type(exc).__name__}: {str(exc)}"[:200]


def _equal_weight_pick(pick, name: str) -> dict[str, float]:
    if len(pick) == 0:
        raise ValueError(f"{name} has no eligible holdings at this rebalance")
    return {str(lineage): 1.0 / len(pick) for lineage in pick}


def record_monthly_paper_snapshot(z, *, strategy: str, session: date,
                                  target_weights: dict[str, float],
                                  fills: list[dict], marks: dict[str, float],
                                  opening_cash_inr: float) -> dict:
    """Persist one reconciled monthly-book close from actual paper fills.

    ``fills`` are simulated fill/order receipts, not planned quantities. A zero-fill row
    must carry a reason so rejected or missed opportunities remain visible. Replaying the
    same canonical input is idempotent; a different payload for the same book/date fails.
    """
    import math

    if strategy not in MONTHLY_BOOKS:
        raise ValueError(f"strategy must be one of {MONTHLY_BOOKS}")
    if not isinstance(session, date):
        raise ValueError("session must be a date")
    if isinstance(opening_cash_inr, bool) or not isinstance(opening_cash_inr, (int, float)) \
            or not np.isfinite(opening_cash_inr) or opening_cash_inr < 0:
        raise ValueError("opening_cash_inr must be finite and non-negative")
    normalized_targets: dict[str, float] = {}
    for lineage, weight in target_weights.items():
        if not isinstance(lineage, str) or not lineage.strip():
            raise ValueError("target lineages must be non-empty strings")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) \
                or not np.isfinite(weight) or not 0 <= weight <= 1:
            raise ValueError(f"{lineage}: target weight must be finite and in [0, 1]")
        normalized_targets[lineage] = float(weight)
    if sum(normalized_targets.values()) > 1.0 + 1e-9:
        raise ValueError("monthly target weights cannot exceed fully invested capital")

    from typing import Any

    normalized_fills: list[dict[str, Any]] = []
    for index, raw in enumerate(fills):
        fill_lineage, fill_side = raw.get("lineage"), raw.get("side")
        if not isinstance(fill_lineage, str) or not fill_lineage.strip() \
                or fill_side not in ("BUY", "SELL"):
            raise ValueError(f"fill {index}: valid lineage and BUY/SELL side required")
        requested, filled = raw.get("requested_shares"), raw.get("filled_shares")
        if (isinstance(requested, bool) or not isinstance(requested, int) or requested < 0
                or isinstance(filled, bool) or not isinstance(filled, int) or filled < 0):
            raise ValueError(f"fill {index}: requested and filled shares must be non-negative integers")
        if filled > requested:
            raise ValueError(f"fill {index}: filled shares cannot exceed requested shares")
        price, fee = raw.get("fill_price"), raw.get("cost_inr")
        normalized_price: float | None = None
        if filled:
            if isinstance(price, bool) or not isinstance(price, (int, float)) \
                    or not np.isfinite(price) or price <= 0:
                raise ValueError(f"fill {index}: positive finite fill_price required")
            normalized_price = float(price)
        elif not raw.get("reason"):
            raise ValueError(f"fill {index}: zero fills require a rejection/no-fill reason")
        if isinstance(fee, bool) or not isinstance(fee, (int, float)) \
                or not np.isfinite(fee) or fee < 0:
            raise ValueError(f"fill {index}: cost_inr must be finite and non-negative")
        normalized_fills.append({
            "lineage": fill_lineage, "side": fill_side, "requested_shares": requested,
            "filled_shares": filled, "fill_price": normalized_price,
            "cost_inr": float(fee), "reason": str(raw.get("reason", "")),
            "order_id": str(raw.get("order_id", f"{index}")),
        })

    z.execute("""CREATE TABLE IF NOT EXISTS zoo_monthly_book_snapshots (
        strategy VARCHAR, session DATE, target_hash VARCHAR, opening_cash DOUBLE,
        closing_cash DOUBLE, holdings_value DOUBLE, equity DOUBLE, payload_hash VARCHAR,
        PRIMARY KEY (strategy, session))""")
    z.execute("""CREATE TABLE IF NOT EXISTS zoo_monthly_book_positions (
        strategy VARCHAR, session DATE, lineage VARCHAR, target_weight DOUBLE,
        target_shares BIGINT, actual_shares BIGINT, mark_price DOUBLE, market_value DOUBLE,
        actual_weight DOUBLE, drift DOUBLE, entry_shares BIGINT, exit_shares BIGINT,
        reason VARCHAR, PRIMARY KEY (strategy, session, lineage))""")
    z.execute("""CREATE TABLE IF NOT EXISTS zoo_monthly_book_orders (
        strategy VARCHAR, session DATE, order_id VARCHAR, lineage VARCHAR, side VARCHAR,
        requested_shares BIGINT, filled_shares BIGINT, fill_price DOUBLE, cost_inr DOUBLE,
        reason VARCHAR, PRIMARY KEY (strategy, session, order_id))""")

    prior = z.execute("""SELECT session, closing_cash FROM zoo_monthly_book_snapshots
                         WHERE strategy = ? AND session < ? ORDER BY session DESC LIMIT 1""",
                      [strategy, session]).fetchone()
    if prior and not math.isclose(float(prior[1]), float(opening_cash_inr), rel_tol=0, abs_tol=0.01):
        raise ValueError("opening cash does not reconcile to previous monthly snapshot")
    previous_rows = (z.execute("""SELECT lineage, actual_shares FROM zoo_monthly_book_positions
                                  WHERE strategy = ? AND session = ?""", [strategy, prior[0]]).fetchall()
                     if prior else [])
    shares = {lineage: int(quantity) for lineage, quantity in previous_rows}
    opening_shares = dict(shares)
    cash = float(opening_cash_inr)
    for row in normalized_fills:
        qty = row["filled_shares"]
        notional = qty * (row["fill_price"] or 0.0)
        if row["side"] == "BUY":
            cash -= notional + row["cost_inr"]
            shares[row["lineage"]] = shares.get(row["lineage"], 0) + qty
        else:
            if qty > shares.get(row["lineage"], 0):
                raise ValueError(f"{row['lineage']}: sell fill exceeds paper holdings")
            cash += notional - row["cost_inr"]
            shares[row["lineage"]] = shares.get(row["lineage"], 0) - qty
    if cash < -0.01:
        raise ValueError("fills would create negative paper cash")
    shares = {lineage: quantity for lineage, quantity in shares.items() if quantity}
    for lineage, price in marks.items():
        if isinstance(price, bool) or not isinstance(price, (int, float)) \
                or not np.isfinite(price) or price <= 0:
            raise ValueError(f"{lineage}: marks must be finite and positive")
    missing_marks = sorted(set(shares) - set(marks))
    if missing_marks:
        raise ValueError(f"missing marks for held lineages: {missing_marks}")

    target_hash = monthly_target_hashes({strategy: normalized_targets})[strategy]
    holdings_value = sum(quantity * float(marks[lineage]) for lineage, quantity in shares.items())
    equity = cash + holdings_value
    union = sorted(set(shares) | set(opening_shares) | set(normalized_targets))
    positions = []
    for lineage in union:
        quantity = shares.get(lineage, 0)
        mark = float(marks[lineage]) if lineage in marks else 0.0
        value = quantity * mark
        actual_weight = value / equity if equity else 0.0
        target_weight = normalized_targets.get(lineage, 0.0)
        target_shares = int(target_weight * equity / mark) if mark else 0
        bought = sum(x["filled_shares"] for x in normalized_fills
                     if x["lineage"] == lineage and x["side"] == "BUY")
        sold = sum(x["filled_shares"] for x in normalized_fills
                   if x["lineage"] == lineage and x["side"] == "SELL")
        if quantity and not opening_shares.get(lineage):
            reason = "ENTRY_FILLED"
        elif opening_shares.get(lineage) and not quantity:
            reason = "EXIT_FILLED"
        elif bought or sold:
            reason = "POSITION_ADJUSTED"
        elif target_weight and not quantity:
            reason = "NO_FILL_OR_REJECTED"
        elif quantity and abs(actual_weight - target_weight) > 1e-9:
            reason = "WEIGHT_DRIFT"
        else:
            reason = "NO_TRADE"
        positions.append({"lineage": lineage, "target_weight": target_weight,
                          "target_shares": target_shares, "actual_shares": quantity,
                          "mark_price": mark, "market_value": value,
                          "actual_weight": actual_weight,
                          "drift": actual_weight - target_weight,
                          "entry_shares": bought, "exit_shares": sold, "reason": reason})

    payload = {"strategy": strategy, "session": session.isoformat(), "target_hash": target_hash,
               "opening_cash_inr": float(opening_cash_inr), "closing_cash_inr": cash,
               "holdings_value_inr": holdings_value, "equity_inr": equity,
               "positions": positions, "fills": normalized_fills}
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                                allow_nan=False).encode()).hexdigest()
    existing = z.execute("SELECT payload_hash FROM zoo_monthly_book_snapshots WHERE strategy=? AND session=?",
                         [strategy, session]).fetchone()
    if existing:
        if existing[0] == payload_hash:
            return {**payload, "payload_hash": payload_hash, "idempotent_replay": True}
        raise ValueError("conflicting monthly paper snapshot already exists")
    z.execute("BEGIN TRANSACTION")
    try:
        z.execute("INSERT INTO zoo_monthly_book_snapshots VALUES (?,?,?,?,?,?,?,?)",
                  [strategy, session, target_hash, float(opening_cash_inr), cash,
                   holdings_value, equity, payload_hash])
        z.executemany("INSERT INTO zoo_monthly_book_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      [(strategy, session, row["lineage"], row["target_weight"],
                        row["target_shares"], row["actual_shares"], row["mark_price"],
                        row["market_value"], row["actual_weight"], row["drift"],
                        row["entry_shares"], row["exit_shares"], row["reason"])
                       for row in positions])
        z.executemany("INSERT INTO zoo_monthly_book_orders VALUES (?,?,?,?,?,?,?,?,?,?)",
                      [(strategy, session, row["order_id"], row["lineage"], row["side"],
                        row["requested_shares"], row["filled_shares"], row["fill_price"],
                        row["cost_inr"], row["reason"]) for row in normalized_fills])
        z.execute("COMMIT")
    except Exception:
        z.execute("ROLLBACK")
        raise
    return {**payload, "payload_hash": payload_hash, "idempotent_replay": False}


def monthly_target_hashes(targets: dict[str, dict]) -> dict[str, str]:
    """Stable per-book hashes for intended-versus-persisted monthly target audits."""
    hashes = {}
    for book in MONTHLY_BOOKS:
        target = targets.get(book, {})
        payload = json.dumps({str(key): float(value) for key, value in sorted(target.items())},
                             sort_keys=True, separators=(",", ":"), allow_nan=False)
        hashes[book] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return hashes


def monthly_rebalance_audit(day: date, scheduled_signal_date: date, state: dict,
                            intended_targets: dict[str, dict]) -> dict | None:
    """Describe a missed first-session rebalance without rewriting its signal or target history."""
    if day <= scheduled_signal_date or state.get("set") == str(scheduled_signal_date):
        return None
    intended = monthly_target_hashes(intended_targets)
    actual = monthly_target_hashes(state.get("targets", {}))
    unresolved = sorted(intended_targets.get("_errors", {}).keys())
    return {
        "status": "MISSED_OR_STALE",
        "signal_date": str(scheduled_signal_date),
        "detected_on": str(day),
        "last_actual_signal_date": state.get("set"),
        "intended_hashes": intended,
        "actual_hashes": actual,
        "mismatched_books": [book for book in MONTHLY_BOOKS if intended[book] != actual[book]],
        "unresolved_intended_books": unresolved,
    }


def monthly_targets(rcon, day: date) -> dict[str, dict]:
    """P1, P2, P3 targets on ``day``'s close (equal weight; P2 scaled)."""
    from ..evaluation import edges as E
    out: dict[str, dict] = {}
    errors: dict[str, str] = {}
    try:
        P = _panel(rcon, day)
    except Exception as exc:  # noqa: BLE001 - record shared source failure for each book
        message = f"{type(exc).__name__}: {str(exc)}"[:200]
        return {"_errors": {name: message for name in MONTHLY_BOOKS}}

    last = P["C"].index[-1]
    uni = P["U"].loc[last][P["U"].loc[last]].index

    def p1_target():
        residual = E.residual_scores(P)
        return _equal_weight_pick(residual.loc[last, uni].dropna().nlargest(20).index,
                                  "P1 residual momentum")

    def p3_target():
        momentum = E.momentum_scores(P)
        discreteness = E.info_discreteness(P)
        scores = momentum.loc[last, uni].dropna()
        low_discreteness = discreteness.loc[last, scores.index] <= discreteness.loc[last, uni].median()
        return _equal_weight_pick(scores[low_discreteness.fillna(False)].nlargest(20).index,
                                  "P3 frog-in-pan")

    _capture_target("edge_p1_residual_mom", p1_target, out, errors)
    _capture_target("edge_p3_frog_in_pan", p3_target, out, errors)
    h = h_targets(rcon, P)
    h_errors = h.pop("_errors", {})
    out.update(h)
    errors.update(h_errors)

    # Compute P2 weights separately so a missing return in its training history cannot
    # suppress P1/P3/H003/H005. The stored scale remains the existing volatility policy.
    def p2_scaled_target():
        mom = E.momentum_scores(P)
        picks = E.top_picks(P, mom)
        m = mom.loc[last, uni].dropna()
        picks[last] = list(m.nlargest(20).index)
        hist = E.monthly_selection(P, picks)["net"]
        vol = hist.tail(6).std(ddof=1) * np.sqrt(12) if len(hist) >= 6 else np.nan
        weight = float(min(1.5, 0.12 / vol)) if vol == vol and vol > 0 else 1.0
        top = m.nlargest(20).index
        return {str(lineage): weight / len(top) for lineage in top}

    _capture_target("edge_p2_volmanaged_mom", p2_scaled_target, out, errors)
    return out | ({"_errors": errors} if errors else {})


VERDICT.update({
    "edge_h003_riskadj_mom": "CANDIDATE_SMALL under ADR-0007 (hundred, 2026-11-17); forward shadow from 2026-11-18",
    "edge_h005_industry_mom": "CANDIDATE_SMALL under ADR-0007 (hundred, 2026-11-17); forward shadow from 2026-11-18"})


def _industries(rcon, lineages, as_of: date) -> pd.Series:
    """Resolve NIFTY 500 industry rows effective and observed by the decision-date close.

    The daily rebalance job runs after the NSE close. A membership row ingested later
    than 23:59:59 IST on the signal date cannot affect that date's candidate ranking.
    """
    available_by = datetime.combine(as_of, time.max, tzinfo=ZoneInfo("Asia/Kolkata")).astimezone(
        timezone.utc
    )
    df = rcon.execute("""WITH security_asof AS (
                            SELECT lineage, isin,
                                   ROW_NUMBER() OVER (PARTITION BY lineage ORDER BY business_date DESC) AS rn
                            FROM adjusted_prices
                            WHERE business_date <= ? AND lineage IN (SELECT UNNEST(?)) AND isin IS NOT NULL
                        ), membership_asof AS (
                            SELECT s.lineage, m.industry,
                                   ROW_NUMBER() OVER (PARTITION BY s.lineage
                                                      ORDER BY m.as_of DESC, m.observed_at DESC) AS rn
                            FROM security_asof s JOIN index_membership m USING (isin)
                            WHERE s.rn = 1 AND m.index_name = 'NIFTY 500' AND m.as_of <= ?
                              AND m.observed_at <= ?
                        )
                        SELECT lineage, industry FROM membership_asof WHERE rn = 1""",
                       [as_of, list(lineages), as_of, available_by]).df()
    return df.drop_duplicates("lineage").set_index("lineage")["industry"].reindex(list(lineages))


def h_targets(rcon, P: dict) -> dict[str, dict]:
    """H003 and H005 on the last session of ``P``, as in the registered study (hundred.py, 2026-11-17):
    daily 12-1 momentum = close(t-21) / close(t-252) - 1 over the top-500 universe."""
    C, U = P["C"], P["U"]
    last = C.index[-1]
    u = U.loc[last].fillna(False).astype(bool) & C.loc[last].notna()
    mom = C.shift(21) / C.shift(252) - 1
    vol = C.pct_change(fill_method=None).rolling(250, min_periods=200).std()
    out: dict[str, dict] = {}
    errors: dict[str, str] = {}

    def h003_target() -> dict[str, float]:
        scores = (mom.loc[last] / vol.loc[last]).where(u).replace([np.inf, -np.inf], np.nan).dropna()
        return _equal_weight_pick(scores.nlargest(20).index, "H003 risk-adjusted momentum")

    def h005_target() -> dict[str, float]:
        ind = _industries(rcon, C.columns, last.date())
        r6 = (C.loc[last] / C.shift(126).loc[last] - 1).where(u)
        industry_return = r6.groupby(ind).mean().dropna()
        industry_count = u.groupby(ind).sum()
        industry_return = industry_return[industry_count.reindex(industry_return.index) >= 8]
        best = set(industry_return.nlargest(3).index)
        scores = mom.loc[last].where(u & ind.isin(best)).dropna()
        return _equal_weight_pick(scores.nlargest(20).index, "H005 industry momentum")

    _capture_target("edge_h003_riskadj_mom", h003_target, out, errors)
    _capture_target("edge_h005_industry_mom", h005_target, out, errors)
    return out | ({"_errors": errors} if errors else {})


def s1_targets(rcon, day: date, uni: list[str], bars_close: pd.DataFrame) -> dict:
    """S1 on ``day``: no-news losers among ``uni``. ``bars_close``: sessions x lineages, up to ``day``."""
    from ..evaluation import edges as E
    if len(bars_close) < 6:
        return {}
    r5 = (bars_close.iloc[-1] / bars_close.iloc[-6] - 1).reindex(uni).dropna()
    r5 = r5 - r5.mean()
    losers = r5[r5 <= r5.quantile(0.10)].index
    since = bars_close.index[-5]
    news = {r[0] for r in rcon.execute(f"""SELECT DISTINCT p.lineage FROM announcements a
              JOIN (SELECT DISTINCT isin, lineage FROM adjusted_prices WHERE isin IS NOT NULL) p USING (isin)
              WHERE a.event_type IN ({','.join("'" + x + "'" for x in E.NEWS_TYPES)}) AND a.business_date > ?::DATE - INTERVAL 3 DAY
              AND a.business_date <= ? AND p.lineage IN (SELECT UNNEST(?))""", [since, day, list(losers)]).fetchall()}
    pick = [x for x in losers if x not in news]
    return {k: 1.0 / len(pick) for k in pick} if pick else {}


def s2_targets(rcon, day: date, st: dict, sessions: list) -> tuple[dict, dict]:
    """S2: names whose results reaction closed yesterday in the top fifth of the trailing 30 days' reactions are
    bought today, each held 20 sessions."""
    from ..decisions import news_filter as NF
    held = {k: v for k, v in st.get("held", {}).items()}
    re = NF.reactions(rcon, day)
    if len(re) and len(sessions) >= 2:
        yday = pd.Timestamp(sessions[-2])
        for r in re[re["after"] == yday].itertuples():
            peers = re[(re["after"] <= r.after) & (re["after"] > r.after - pd.Timedelta(days=NF.WINDOW_DAYS))]["react"]
            pct = float((peers < r.react).mean() + 0.5 * (peers == r.react).mean())
            if len(peers) >= 5 and pct >= 0.8:
                held[r.lineage] = str(day)
    sess = [str(pd.Timestamp(s).date()) for s in sessions]
    keep = {k: v for k, v in held.items() if v in sess and len(sess) - 1 - sess.index(v) < 20}
    w = {k: 1.0 / len(keep) for k in keep} if keep else {}
    return w, {"held": keep}


def oi_targets(fut, lins_by_symbol: dict, closes: pd.DataFrame, day: date) -> tuple[dict, dict]:
    """F1 / F2 on ``day`` from the futures OI history and spot closes (sessions x lineages)."""
    oi = fut.execute("""SELECT symbol, d, oi FROM futures_daily WHERE d <= ? AND d > ?::DATE - INTERVAL 20 DAY AND oi > 0""",
                     [day, day]).df()
    if oi.empty:
        return {}, {}
    oi["d"] = pd.to_datetime(oi["d"])
    O = oi.pivot(index="d", columns="symbol", values="oi").sort_index()
    if len(O) < 6 or O.index[-1] != pd.Timestamp(day):
        return {}, {}
    d5 = (O.iloc[-1] / O.iloc[-6] - 1)
    rows = []
    for sym, ch in d5.dropna().items():
        lin = lins_by_symbol.get(sym)
        if lin in closes.columns and len(closes) >= 6:
            c = closes[lin]
            if np.isfinite(c.iloc[-1]) and np.isfinite(c.iloc[-6]):
                rows.append((lin, c.iloc[-1] / c.iloc[-6] - 1, ch))
    if len(rows) < 50:
        return {}, {}
    df = pd.DataFrame(rows, columns=["lin", "r", "oi"])
    hi, lo = df["oi"].quantile(0.8), df["oi"].quantile(0.2)
    lb = df[(df["r"] > 0) & (df["oi"] >= hi)]["lin"]
    sc = df[(df["r"] > 0) & (df["oi"] <= lo)]["lin"]
    f1 = {k: 1.0 / len(lb) for k in lb} if len(lb) else {}
    f2 = {k: -1.0 / len(sc) for k in sc} if len(sc) else {}
    if f2:
        f2["NIFTY50"] = 1.0
    return f1, f2


def z1_exposure(day: date) -> float:
    """1 if ``day``'s close starts or continues the turn-of-month window, by weekdays (holidays near a month end
    shift it by a session - stated)."""
    d = pd.Timestamp(day)
    month_days = pd.bdate_range(d.replace(day=1), d + pd.offsets.MonthEnd(0))
    if d in month_days[-3:-1] or d == month_days[-1]:
        return 1.0                                       # from 2 sessions before month end
    first = pd.bdate_range(d.replace(day=1), periods=3)
    return 1.0 if d in first[:2] else 0.0              # held to the 3rd session's close


def i1_return(day: date) -> float | None:
    """I1's net return for ``day`` from Kite's Nifty 50 minute bars (None without a token or bars)."""
    from datetime import datetime, time
    from ..evaluation import edges as E
    try:
        from ..providers import kite_data as kd
        bars = kd.candles(256265, datetime.combine(day, time(9, 15)), datetime.combine(day, time(15, 30)), interval="minute")
        prev = kd.candles(256265, pd.Timestamp(day) - pd.Timedelta(days=7), pd.Timestamp(day) - pd.Timedelta(days=1), interval="day")
    except Exception:                                   # noqa: BLE001 - no token: no I1 tonight
        return None
    if not bars or not prev:
        return None
    b = pd.DataFrame(bars)
    out = E.index_intraday_momentum(b[["ts", "close"]], {pd.Timestamp(day): float(prev[-1]["close"])})
    return float(out["net"].iloc[0]) if len(out) else None


def state(z, s: str) -> dict:
    r = z.execute("SELECT state FROM zoo_state WHERE strategy = ?", [s]).fetchone()
    return json.loads(r[0]) if r else {}


def save(z, s: str, st: dict) -> None:
    z.execute("INSERT OR REPLACE INTO zoo_state VALUES (?, ?)", [s, json.dumps(st, default=str)])


# ------------------------------------------------------------------------------------------------- batch two

VERDICT.update({"edge_w1_reversal_ls": "failed (t -1.2 / -1.4; negative gross)", "edge_w2_52wh_ls": "failed (t -0.0 / -1.3)",
                "edge_w3_momentum_ls": "failed (t -0.0 / +0.5)", "edge_e1_mom_ensemble": "failed (t +0.7 / -0.1)",
                "edge_x1_overnight": "failed (t +5.9 / +1.1: faded since 2022)", "edge_x2_opening_fade": "failed (t -0.5 / -0.1)"})


def ls_targets(closes: pd.DataFrame, fno_lins: list[str], kind: str, frac: float = 0.10) -> dict:
    """W1 (5-session reversal), W2 (nearness to the 52-week high), W3 (12-1 momentum): market-neutral deciles of
    the F&O names, +1 long and -1 short."""
    C = closes[[c for c in fno_lins if c in closes.columns]]
    if len(C) < 253 and kind != "W1":
        return {}
    if kind == "W1":
        sc = -(C.iloc[-1] / C.iloc[-6] - 1)
    elif kind == "W2":
        sc = C.iloc[-1] / C.iloc[-252:].max()
    else:
        sc = C.iloc[-22] / C.iloc[-253] - 1
    sc = sc.dropna()
    if len(sc) < 50:
        return {}
    L = sc[sc >= sc.quantile(1 - frac)].index
    S = sc[sc <= sc.quantile(frac)].index
    w = {k: 1.0 / len(L) for k in L}
    for k in S:
        w[k] = w.get(k, 0.0) - 1.0 / len(S)
    return w


def ensemble_target(rcon, day: date) -> dict:
    from ..evaluation import edges as E
    P = _panel(rcon, day)
    ens = E.ensemble_scores(P)
    last = P["C"].index[-1]
    if last not in ens.index:
        ens.loc[last] = E.ensemble_scores(P).iloc[-1]
    pick = ens.loc[last].dropna().nlargest(20).index
    return {k: 1.0 / len(pick) for k in pick}


def index_day(day: date) -> dict:
    """X1 (yesterday's close to today's open) and X2 (today's opening fade) from Kite's Nifty 50 bars."""
    from datetime import datetime, time
    from ..evaluation import edges as E
    try:
        from ..providers import kite_data as kd
        bars = kd.candles(256265, datetime.combine(day, time(9, 15)), datetime.combine(day, time(15, 30)), interval="minute")
        daily = kd.candles(256265, pd.Timestamp(day) - pd.Timedelta(days=130), pd.Timestamp(day), interval="day")
    except Exception:                                   # noqa: BLE001 - no token: nothing tonight
        return {}
    if not bars or len(daily) < 2:
        return {}
    b = pd.DataFrame(bars)
    out: dict[str, float] = {}
    dd = pd.DataFrame(daily)
    dd["d"] = pd.to_datetime(dd["ts"]).dt.tz_localize(None).dt.normalize()
    dd = dd.set_index("d")
    today = pd.Timestamp(day)
    if today in dd.index:
        prev_close = float(dd["close"][dd.index < today].iloc[-1])
        out["edge_x1_overnight"] = float(b["open"].iloc[0]) / prev_close - 1 - 2 * E.INDEX_ONE_WAY
    # X2 needs the 09:15-09:30 moves of the previous 60 sessions: from the stored minute history plus today
    try:
        path = "data/edges/nifty50_minute.parquet"
        h = pd.read_parquet(path)
        b["ts"] = pd.to_datetime(b["ts"])
        h = pd.concat([h, b[h.columns.intersection(b.columns)]]).drop_duplicates("ts").sort_values("ts")
        h.to_parquet(path)                              # the stored history grows by a day each evening
        dly = E.index_daily_from_minutes(h)
        ev = E.opening_fade(dly)
        if today in ev.index:
            out["edge_x2_opening_fade"] = float(ev.loc[today, "net"])
        else:
            out["edge_x2_opening_fade"] = 0.0           # no signal today: flat
    except Exception:                                   # noqa: BLE001
        pass
    return out


VERDICT["edge_i2_seasonality_ls"] = "failed on cost (gross +3.6 bp a slot, t +9; net -3% a day)"
HH_PATH = "data/edges/halfhour.parquet"


def i2_day(rcon, day: date, *, lookback: int = 20) -> float | None:
    """I2 for ``day``: every half-hour slot, long the top and short the bottom decile of the names' mean same-slot
    return over the previous ``lookback`` sessions; the day's sum over slots, net of both legs' intraday costs.
    The slot history (``HH_PATH``) starts from the minute-bar table and grows by a day each evening from Kite."""
    from datetime import datetime, time
    from pathlib import Path
    from ..evaluation import edges as E
    if Path(HH_PATH).exists():
        hh = pd.read_parquet(HH_PATH)
    else:
        hh = E.half_hour_returns(rcon)
        hh["r"] = hh["end_px"] / hh["start_px"] - 1
        hh = hh[hh["n"] >= 10][["s", "d", "slot", "r"]]
        hh["d"] = pd.to_datetime(hh["d"])
    today = pd.Timestamp(day)
    if not (hh["d"] == today).any():
        try:
            from ..providers import kite_data as kd
            inst = kd.instruments("NSE")
        except Exception:                               # noqa: BLE001 - no token
            return None
        rows = []
        for sym in sorted(hh["s"].unique()):
            tok = kd.token_for(sym, inst)
            if tok is None:
                continue
            try:
                bars = kd.candles(tok, datetime.combine(day, time(9, 15)), datetime.combine(day, time(15, 30)), interval="minute")
            except Exception:                           # noqa: BLE001 - one name failing leaves the rest
                continue
            if len(bars) < 100:
                continue
            b = pd.DataFrame(bars)
            b["ts"] = pd.to_datetime(b["ts"])
            mins = b["ts"].dt.hour * 60 + b["ts"].dt.minute - 555
            b["slot"] = mins // 30
            b = b[(b["slot"] >= 0) & (b["slot"] <= 12)]
            g = b.groupby("slot").agg(c0=("close", "first"), c1=("close", "last"), n=("close", "size"))
            g["start"] = g["c1"].shift(1).fillna(g["c0"])
            for slot, r in g.iterrows():
                if r["n"] >= 10:
                    rows.append({"s": sym, "d": today, "slot": int(slot), "r": float(r["c1"] / r["start"] - 1)})
        if not rows:
            return None
        hh = pd.concat([hh, pd.DataFrame(rows)], ignore_index=True)
        hh.to_parquet(HH_PATH)
    total, slots = 0.0, 0
    for _slot, g in hh[hh["d"] <= today].groupby("slot"):
        R = g.pivot_table(index="d", columns="s", values="r")
        if today not in R.index:
            continue
        sig = R[R.index < today].tail(lookback)
        if len(sig) < 15:
            continue
        sig = sig.mean()
        r = R.loc[today]
        sig = sig[r.reindex(sig.index).notna()]
        if len(sig) < 100:
            continue
        spread = r[sig[sig >= sig.quantile(0.9)].index].mean() - r[sig[sig <= sig.quantile(0.1)].index].mean()
        total += spread - 2 * E.INTRADAY_RT
        slots += 1
    return total if slots else None


VERDICT["edge_l2_learned_ls"] = "failed (t +0.65 / +2.44; strongest futures result)"


def l2_targets(rcon, day: date, fno_lins: list[str], frac: float = 0.10) -> dict:
    """L2: the learned model's scores for the F&O names, long the top decile and short the bottom, equal gross."""
    from ..evaluation import learned_model as LM
    sc = LM.score_day(rcon, day)
    if sc is None or sc.empty:
        return {}
    s = sc[sc["lineage"].isin(fno_lins)].set_index("lineage")["pred"]
    if len(s) < 50:
        return {}
    L = s[s >= s.quantile(1 - frac)].index
    S = s[s <= s.quantile(frac)].index
    w = {k: 1.0 / len(L) for k in L}
    for k in S:
        w[k] = w.get(k, 0.0) - 1.0 / len(S)
    return w


# ------------------------------------------------------------------------------------------------- ADR-0004 trials

VERDICT.update({"edge_m1_momentum_book": "failed G3 (t +1.5 / +1.2), beat the random-book null; forward test (G5)",
                "edge_f3_short_buildup": "failed (t -1.2 / +0.9, wrong sign 2019-21)"})
KIND.update({"edge_m1_momentum_book": "edge", "edge_f3_short_buildup": "edge"})


def m1_targets(rcon, day: date, st: dict) -> tuple[dict, dict]:
    """M1, rebalanced every 40 sessions (``PRE-REGISTERED cost-aware momentum book``); between rebalances the
    weights drift and are carried as they were set."""
    from ..evaluation import momentum_book as MB
    k = int(st.get("k", 0))
    if k % MB.HOLD == 0 or "w" not in st:
        w, held = MB.targets(rcon, day, st.get("held", []))
        st = {"w": w, "held": held, "set": str(day)}
    st["k"] = k + 1
    return st["w"], st


def f3_targets(fut, lins_by_symbol: dict, closes: pd.DataFrame, day: date) -> dict:
    """F3 on ``day``: short the F&O names in short build-up, long the Nifty for the same notional (as F2 and N2 are
    traded here). Empty when the futures history does not reach ``day``."""
    from ..evaluation import short_buildup as SB
    oi = fut.execute("""SELECT symbol, d, SUM(oi) AS oi FROM futures_daily WHERE d <= ? AND d > ?::DATE - INTERVAL 20 DAY
                        AND oi > 0 GROUP BY 1, 2""", [day, day]).df()
    if oi.empty:
        return {}
    oi["d"] = pd.to_datetime(oi["d"])
    O = oi.pivot(index="d", columns="symbol", values="oi").sort_index()
    if len(O) < SB.K + 1 or O.index[-1] != pd.Timestamp(day):
        return {}
    syms = [s for s in O.columns if lins_by_symbol.get(s) in closes.columns]
    Sp = pd.DataFrame({s: closes[lins_by_symbol[s]] for s in syms})
    Sp.index = pd.to_datetime(Sp.index)
    Sp = Sp.reindex(O.index)
    M = Sp.notna() & O[syms].notna()
    flagged, _ = SB.flags(Sp, O[syms], M, O.index[-1])
    if not flagged:
        return {}
    w = {lins_by_symbol[s]: -1.0 / len(flagged) for s in flagged}
    w["NIFTY50"] = 1.0
    return w


VERDICT["edge_eap_announcement_premium"] = "registered 2026-10-13 (EAP); verdict in Result of edges batch three"
KIND["edge_eap_announcement_premium"] = "edge"


def eap_targets(rcon, day: date, fno_lins: list[str]) -> dict:
    """EAP on ``day``'s close: long every F&O name whose results board meeting was intimated 3-15 sessions ahead and
    whose holding window (the intimation's close to the close of the session after the meeting) covers tonight;
    short the Nifty for the same notional. Equal weight across the names held."""
    from ..evaluation import batch_three as B3
    sess = [pd.Timestamp(r[0]) for r in rcon.execute(
        "SELECT DISTINCT business_date FROM features WHERE business_date <= ? ORDER BY 1 DESC LIMIT 40", [day]).fetchall()][::-1]
    if len(sess) < 20:
        return {}
    idx = pd.DatetimeIndex(sess)
    rows = rcon.execute("""SELECT DISTINCT a.business_date AS ann, a.headline, p.lineage FROM announcements a
                           JOIN (SELECT DISTINCT isin, lineage FROM adjusted_prices WHERE isin IS NOT NULL
                                 AND lineage IN (SELECT UNNEST(?))) p USING (isin)
                           WHERE a.event_type = 'BOARD_MEETING' AND lower(a.headline) LIKE '%result%'
                             AND a.business_date > ? AND a.business_date <= ?""",
                        [fno_lins, sess[0].date(), day]).fetchall()
    today = pd.Timestamp(day)
    held = set()
    for ann, headline, lin in rows:
        m = B3.SCHEDULED.search(headline or "")
        if not m:
            continue
        try:
            meet = pd.Timestamp(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            continue
        p0 = idx.searchsorted(pd.Timestamp(ann))
        if p0 >= len(idx):
            continue
        # sessions to the meeting, counted on the exchange calendar where known and on weekdays beyond it
        k = len(pd.bdate_range(idx[p0] + pd.Timedelta(days=1), meet)) if meet > idx[-1] else idx.searchsorted(meet) - p0
        if not (B3.EAP_MIN <= k <= B3.EAP_MAX):
            continue
        meet_pos = idx.searchsorted(meet)
        exit_session_passed = meet_pos + 1 < len(idx) and idx[meet_pos + 1] <= today
        if idx[p0] <= today and not exit_session_passed:
            held.add(lin)
    if not held:
        return {}
    w = {lin: 1.0 / len(held) for lin in held}
    w["NIFTY50"] = -1.0
    return w


VERDICT["edge_eapr_announcement_short"] = "out-of-sample only (sign-flip of failed EAP, chosen after the result)"
KIND["edge_eapr_announcement_short"] = "edge"


def eapr_targets(rcon, day: date, fno_lins: list[str]) -> dict:
    """EAPR: EAP's book with every sign flipped (short the names, long the Nifty)."""
    return {k: -v for k, v in eap_targets(rcon, day, fno_lins).items()}


VERDICT["edge_bab_futures"] = "failed (t -0.5 / -0.2; volatility family closed)"
KIND["edge_bab_futures"] = "edge"


def bab_targets(closes: pd.DataFrame, nifty: pd.Series, fno_lins: list[str]) -> dict:
    """BAB on the last session of ``closes`` (sessions x lineages) - the registered construction: shrunk 252-session
    betas to the Nifty, bottom third long scaled 1/beta_L, top third short scaled 1/beta_H."""
    from ..evaluation import bab as BB
    C = closes[[c for c in fno_lins if c in closes.columns]].copy()
    C.index = pd.to_datetime(C.index)
    N = pd.Series(nifty.to_numpy(), index=pd.to_datetime(nifty.index)).reindex(C.index).ffill()
    if len(C) <= BB.WINDOW:
        return {}
    b = BB.betas(C, N, C.index[-1]).dropna()
    b = b[b > 0]
    if len(b) < 30:
        return {}
    srt = b.sort_values()
    n = len(srt) // 3
    longs, shorts = list(srt.index[:n]), list(srt.index[-n:])
    bl, bh = float(b[longs].mean()), float(b[shorts].mean())
    w = {k: 1.0 / n / bl for k in longs}
    for k in shorts:
        w[k] = w.get(k, 0.0) - 1.0 / n / bh
    return w


# ------------------------------------------------------------------ trials 7 and 8 (both failed; on paper with verdicts)

def _k7_names() -> list[str]:
    from ..evaluation import chart_setups as K
    return [f"edge_k7_{s}" for s in K.SETUPS]


for _n in _k7_names():
    VERDICT[_n] = "failed (trial 7: no chart or candle setup had a 5-session edge in futures)"
    KIND[_n] = "edge"
VERDICT["edge_d1_delivery_ls"] = "failed (trial 8: t +1.0 / +0.1; G4 narrowly)"
KIND["edge_d1_delivery_ls"] = "edge"


def k7_targets(rcon, day: date, fno_lins: list[str]) -> dict[str, dict]:
    """Every setup of trial 7 on ``day``'s close: a setup that fired at session s is held from the close of s+1 to
    the close of s+6, so tonight's book holds the setups that fired in the last five sessions before today. Each
    setup's book: side / n on its names, hedged with -side spread equally over every F&O name (the registered
    equal-weighted F&O hedge)."""
    import warnings

    from ..evaluation import chart_setups as K
    from ..features import library as LB
    from ..features import technical as TE
    rcon.execute(TE.BARS_SQL)
    bars = rcon.execute("""SELECT lineage, d, o, h, l, c, v FROM _tbars WHERE lineage IN (SELECT UNNEST(?)) AND d <= ?
                           AND d > ?::DATE - INTERVAL 420 DAY ORDER BY lineage, d""", [fno_lins, day, day]).df()
    bars["d"] = pd.to_datetime(bars["d"])
    today = pd.Timestamp(day)
    held: dict[str, set] = {s: set() for s in K.SETUPS}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for lin, b in bars.groupby("lineage"):
            b = b.reset_index(drop=True)
            if len(b) < 120 or b["d"].iloc[-1] != today:
                continue
            f = LB.compute(b[["d", "o", "h", "l", "c", "v"]])
            n = len(b)
            for setup, (_, fire) in K.SETUPS.items():
                fired = np.flatnonzero(fire(f, b).fillna(False).to_numpy())
                fired = fired[fired >= n - 1 - 5 - K.HOLD]          # recent enough to matter, with dedupe context
                last, keep = -10**9, []
                for i in fired:
                    if i - last >= K.HOLD:
                        keep.append(i)
                        last = i
                if any(n - 6 <= i <= n - 2 for i in keep):        # fired at s in [today-5, today-1]
                    held[setup].add(lin)
    out: dict[str, dict[str, float]] = {}
    for setup, (side, _) in K.SETUPS.items():
        names = held[setup]
        if not names:
            out[f"edge_k7_{setup}"] = {}
            continue
        w = {k: side / len(names) for k in names}
        for k in fno_lins:
            w[k] = w.get(k, 0.0) - side / len(fno_lins)
        out[f"edge_k7_{setup}"] = {k: v for k, v in w.items() if abs(v) > 1e-12}
    return out


def d1_targets(rcon, day: date, fno_lins: list[str]) -> dict:
    """Trial 8's book on ``day``'s close: long the top fifth and short the bottom fifth of the F&O names by delivery
    share (last 20 sessions minus the 120 before), equal weight."""
    from ..evaluation import delivery_share as DS
    d = rcon.execute("""SELECT p.lineage, d.business_date, d.deliv_pct FROM delivery d
                        JOIN adjusted_prices p ON p.ticker = d.symbol AND p.business_date = d.business_date
                        WHERE d.series = 'EQ' AND p.lineage IN (SELECT UNNEST(?)) AND d.business_date <= ?
                          AND d.business_date > ?::DATE - INTERVAL 260 DAY""", [fno_lins, day, day]).df()
    if d.empty:
        return {}
    D = d.pivot_table(index="business_date", columns="lineage", values="deliv_pct").sort_index()
    sc = DS.score(D).iloc[-1].dropna()
    if len(sc) < 50 or pd.Timestamp(D.index[-1]) != pd.Timestamp(day):
        return {}
    hi, lo = sc.quantile(1 - DS.FRAC), sc.quantile(DS.FRAC)
    L, S_ = sc[sc >= hi].index, sc[sc <= lo].index
    w = {k: 1.0 / len(L) for k in L}
    for k in S_:
        w[k] = w.get(k, 0.0) - 1.0 / len(S_)
    return w


# ------------------------------------------------------------------ batch four (registered 2026-10-14; test pending budget)

Q_RULES = ("q1_extreme_down_long", "q2_extreme_up_short", "q3_rsi_long", "q4_rsi_short", "q5_above_ma10",
           "q6_dip_ma10", "q7_cross_ma10_3d", "q8_ten_day_extremes")
for _q in Q_RULES:
    VERDICT[f"edge_{_q}"] = "failed (trial 9: no Nifty mean-reversion or MA10 rule reached t 2 in either half)"
    KIND[f"edge_{_q}"] = "edge"
VERDICT["edge_v1_vwap_bounce"] = "failed (trial 9: 1,900 trades, -0.4 bp each net)"
KIND["edge_v1_vwap_bounce"] = "edge"


def q_targets(nifty: pd.Series) -> dict[str, float]:
    """Each rule's position for the next session, from the Nifty's closes up to tonight (the paper record uses the
    close; the registered test uses the 15:15 price - a stated difference)."""
    from ..evaluation import index_rules as IR
    p = pd.Series(nifty.to_numpy(dtype=float), index=pd.to_datetime(nifty.index)).dropna()
    if len(p) < 260:
        return {}
    pos = IR.positions(p)
    return {f"edge_{k}": float(v.iloc[-1]) for k, v in pos.items()}


def v1_return(day: date) -> float | None:
    """V1's net return for ``day`` from the stored Nifty minute history (grown each evening by ``index_day``)."""
    from ..evaluation import index_rules as IR
    try:
        h = pd.read_parquet("data/edges/nifty50_minute.parquet")
    except Exception:                                   # noqa: BLE001
        return None
    h["ts"] = pd.to_datetime(h["ts"])
    today = h[h["ts"].dt.tz_localize(None).dt.normalize() == pd.Timestamp(day)]
    if today.empty:
        return None
    s = IR.vwap_bounce(today)
    return float(s.iloc[0]) if len(s) else 0.0           # no setup today: flat


# ------------------------------------------------------------------ trial 10 (failed) and the high-volume premium (OOS)

A_NAMES = ("A1_intermediate_mom", "A2_long_term_reversal", "A3_return_seasonality", "A4_ivol", "A5_max", "A6_idio_skew",
           "A7_vol_of_vol", "A8_downside_beta", "A9_coskewness", "A10_amihud", "A11_volume_trend", "A12_zero_days",
           "A13_illiq_shock", "A14_day_of_week", "A15_halloween", "A16_india_year_end")
for _a in A_NAMES:
    VERDICT[f"edge_{_a.lower()}"] = "failed (trial 10: 0 of 16 published anomalies; SPA p 0.68)"
    KIND[f"edge_{_a.lower()}"] = "edge"
for _h in ("edge_hv1_rising_volume", "edge_hv2_improving_liquidity"):
    VERDICT[_h] = "out-of-sample only (found as a sign flip in trial 10); held-out test in 2027Q1"
    KIND[_h] = "edge"


def anomaly_targets(rcon, day: date) -> dict[str, dict]:
    """Monthly books for A1-A13 and HV1-HV2 (``evaluation.anomalies.targets``), keyed by zoo name."""
    from ..evaluation import anomalies as AN
    return {f"edge_{k.lower()}": w for k, w in AN.targets(rcon, day).items()}


def calendar_exposures(day: date, nifty_closes: pd.Series) -> dict[str, float]:
    """A14 (flat over the session ending Monday), A15 (long November-April). The next session is taken as the next
    weekday - a holiday shifts it by a day, stated."""
    nxt = pd.Timestamp(day) + pd.offsets.BDay(1)
    return {"edge_a14_day_of_week": 0.0 if nxt.weekday() == 0 else 1.0,
            "edge_a15_halloween": 1.0 if nxt.month in (11, 12, 1, 2, 3, 4) else 0.0}


def a16_window(day: date) -> bool:
    """A16's holding window: from the fifth-last weekday of March to the last weekday of April (weekdays stand in
    for sessions beyond the data)."""
    d = pd.Timestamp(day)
    mar = pd.bdate_range(f"{d.year}-03-01", f"{d.year}-03-31")
    apr = pd.bdate_range(f"{d.year}-04-01", f"{d.year}-04-30")
    return mar[-5] <= d < apr[-1]


def a16_targets(rcon, day: date, uni: list[str]) -> dict:
    """Bottom fifth of ``uni`` by the April-February return, equal weight."""
    y = pd.Timestamp(day).year
    px = rcon.execute("""SELECT lineage, business_date, close_adj FROM adjusted_prices WHERE lineage IN (SELECT UNNEST(?))
                         AND business_date BETWEEN ? AND ?""", [uni, f"{y - 1}-03-01", f"{y}-02-28"]).df()
    if px.empty:
        return {}
    px["business_date"] = pd.to_datetime(px["business_date"])
    C = px.pivot_table(index="business_date", columns="lineage", values="close_adj").sort_index()
    start = C[C.index.month == 3].iloc[-1] if (C.index.year == y - 1).any() else None
    end = C[(C.index.year == y) & (C.index.month == 2)]
    if start is None or end.empty:
        return {}
    r = (end.iloc[-1] / start - 1).dropna()
    losers = r[r <= r.quantile(0.2)].index
    return {k: 1.0 / len(losers) for k in losers} if len(losers) else {}
