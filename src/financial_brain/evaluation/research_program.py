"""Read-only edge research diagnostics; never a trial verdict or trading signal.

The quarterly trial budget is spent. These checks expose missing evidence and price a
defined-risk option round trip from *observed two-sided books*, without consulting the
sealed options holdout or altering the registered M1 strategy.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb

M1 = "edge_m1_momentum_book"
TRAIN_END = date(2021, 12, 31)
MAX_BOOK_SKEW = timedelta(seconds=2)
MAX_BOOK_AGE = timedelta(seconds=120)
IST = timezone(timedelta(hours=5, minutes=30))


def m1_forward_snapshot(con) -> dict:
    """Describe the frozen M1 paper series; never re-score or promote it."""
    rows = con.execute("""SELECT day, gross, cost, net, bench FROM zoo_pnl
                          WHERE strategy = ? ORDER BY day""", [M1]).fetchall()
    if not rows:
        return {"strategy": M1, "sessions": 0, "status": "INSUFFICIENT_FORWARD_EVIDENCE"}
    valid = [r for r in rows if all(v is not None and math.isfinite(float(v)) for v in r[1:])]
    return {"strategy": M1, "sessions": len(rows), "valid_sessions": len(valid),
            "first_day": str(rows[0][0]), "last_day": str(rows[-1][0]),
            "mean_net_excess": sum(float(r[3]) - float(r[4]) for r in valid) / len(valid) if valid else None,
            "mean_modelled_cost": sum(float(r[2]) for r in valid) / len(valid) if valid else None,
            "status": "DESCRIPTIVE_ONLY_NO_NEW_VERDICT"}


def execution_snapshot(con) -> dict:
    """Measured paper execution, kept apart from live broker fills."""
    from ..paper import execution as EX
    return {"attribution": EX.attribution(con, mode="live"),
            "shortfall": EX.shortfall(con, mode="live"),
            "evidence": "intraday ORB/GAP paper fills, not M1 or broker executions"}


def filter_counterfactual(trades: list[dict]) -> dict:
    """Whole-book training diagnostic: a skipped winner is charged as forgone P&L.

    Rows must be the frozen baseline's selected trades, *before* the proposed filter.
    Cash earns zero in this narrow diagnostic; portfolio-level risk and opportunity
    effects still require a new registered experiment.
    """
    if not trades:
        return {"trades": 0, "status": "NO_OBSERVATIONS"}
    seen, base, kept = set(), 0.0, 0.0
    avoided_losers = missed_winners = 0
    for row in trades:
        key = row["trade_id"]
        if key in seen:
            raise ValueError("duplicate baseline trade_id")
        seen.add(key)
        if date.fromisoformat(str(row["day"])) > TRAIN_END:
            raise ValueError("counterfactual is restricted to sealed training")
        pnl = float(row["baseline_net_inr"])
        if not math.isfinite(pnl) or not isinstance(row["keep"], bool):
            raise ValueError("invalid net P&L or keep decision")
        base += pnl
        if row["keep"]:
            kept += pnl
        else:
            avoided_losers += pnl < 0
            missed_winners += pnl > 0
    return {"trades": len(trades), "baseline_net_inr": base, "filtered_net_inr": kept,
            "difference_inr": kept - base, "avoided_losers": avoided_losers,
            "missed_winners": missed_winners, "status": "TRAINING_DESCRIPTION_NOT_A_TRIAL"}


def filing_timestamp_coverage(con, *, start: date = date(2019, 1, 1),
                              end: date = TRAIN_END) -> list[dict]:
    """Count usable event timestamps in training only; never infer alpha from coverage."""
    if start > end or end > TRAIN_END:
        raise ValueError("filing diagnostic is restricted to the 2019-2021 training period")
    rows = con.execute("""SELECT event_type, COUNT(*), COUNT(published_at), COUNT(submitted_at),
                                 COUNT(*) FILTER (WHERE published_at >= submitted_at), COUNT(DISTINCT isin)
                          FROM announcements WHERE business_date BETWEEN ? AND ?
                          GROUP BY 1 ORDER BY COUNT(*) DESC""", [start, end]).fetchall()
    return [{"event_type": r[0], "events": r[1], "with_published_at": r[2],
             "with_submitted_at": r[3], "ordered_timestamps": r[4], "distinct_isins": r[5]}
            for r in rows]


def event_peer_diagnostic(rows: list[dict]) -> dict:
    """Training-only paired net returns from an independently prepared, timestamped event book.

    The peer must be formed on the same date/universe and held for the same horizon.
    This function cannot choose events, fabricate entry prices, or register a verdict.
    """
    if not rows:
        return {"events": 0, "status": "NO_OBSERVATIONS"}
    seen = set()
    excess = []
    for row in rows:
        identity = row["event_id"]
        if identity in seen:
            raise ValueError("duplicate event_id")
        seen.add(identity)
        event_at, entry_at, exit_at = (datetime.fromisoformat(str(row[k])) for k in
                                      ("event_at", "entry_at", "exit_at"))
        if any(t.tzinfo is None for t in (event_at, entry_at, exit_at)):
            raise ValueError("event and trade timestamps must have timezone offsets")
        if not event_at < entry_at < exit_at or exit_at.date() > TRAIN_END:
            raise ValueError("trade must follow publication and exit within sealed training")
        net, peer = float(row["net_return"]), float(row["peer_net_return"])
        if not math.isfinite(net) or not math.isfinite(peer):
            raise ValueError("non-finite event or peer return")
        excess.append(net - peer)
    return {"events": len(excess), "mean_net_excess": sum(excess) / len(excess),
            "positive_fraction": sum(x > 0 for x in excess) / len(excess),
            "status": "TRAINING_DESCRIPTION_NOT_A_TRIAL"}


@dataclass(frozen=True)
class Book:
    at: datetime
    expiry: date
    strike: float
    kind: str
    bid: float
    ask: float
    bid_qty: int
    ask_qty: int


def _valid_book(book: Book, decision_at: datetime, qty: int) -> None:
    if book.at.tzinfo is None or decision_at.tzinfo is None:
        raise ValueError("book and decision timestamps require timezone offsets")
    if not timedelta(0) <= decision_at - book.at <= MAX_BOOK_AGE:
        raise ValueError("missing, future or stale option book")
    if (book.kind not in ("CE", "PE") or not all(math.isfinite(x) for x in
                                                 (book.strike, book.bid, book.ask))
            or book.strike <= 0 or book.bid <= 0 or book.ask < book.bid
            or min(book.bid_qty, book.ask_qty) < qty):
        raise ValueError("option book is crossed, one-sided or too small")


def vertical_roundtrip(*, atm_entry: Book, wing_entry: Book, atm_exit: Book, wing_exit: Book,
                       entry_at: datetime, exit_at: datetime, lots: int, lot_size: int,
                       structure: str, fees_inr: float) -> dict:
    """Price an observed CE/PE vertical at entry asks/bids and exit bids/asks.

    `fees_inr` must include the caller's complete round-trip charges. This is a
    retrospective measurement, not an order instruction or a calibrated edge.
    """
    if (structure not in ("debit", "credit") or lots < 1 or lot_size < 1
            or not math.isfinite(fees_inr) or fees_inr < 0):
        raise ValueError("invalid structure, quantity or charges")
    qty = lots * lot_size
    for book, when in ((atm_entry, entry_at), (wing_entry, entry_at),
                       (atm_exit, exit_at), (wing_exit, exit_at)):
        _valid_book(book, when, qty)
    if not entry_at < exit_at or exit_at.astimezone(IST).date() > atm_entry.expiry:
        raise ValueError("exit must follow entry and precede expiry")
    if (max(atm_entry.at, wing_entry.at) - min(atm_entry.at, wing_entry.at) > MAX_BOOK_SKEW
            or max(atm_exit.at, wing_exit.at) - min(atm_exit.at, wing_exit.at) > MAX_BOOK_SKEW):
        raise ValueError("spread legs were not quoted simultaneously")
    if (atm_entry.expiry != wing_entry.expiry or atm_entry.expiry != atm_exit.expiry
            or atm_entry.expiry != wing_exit.expiry or atm_entry.kind != wing_entry.kind
            or atm_entry.kind != atm_exit.kind or atm_entry.kind != wing_exit.kind
            or atm_entry.strike != atm_exit.strike or wing_entry.strike != wing_exit.strike):
        raise ValueError("entry and exit are not the same spread")
    if not (wing_entry.strike > atm_entry.strike if atm_entry.kind == "CE"
            else wing_entry.strike < atm_entry.strike):
        raise ValueError("wing must be out of the money")
    width = abs(wing_entry.strike - atm_entry.strike)
    if structure == "debit":
        entry_points = atm_entry.ask - wing_entry.bid
        exit_points = atm_exit.bid - wing_exit.ask
        expiry_risk = entry_points * qty + fees_inr
        net = (exit_points - entry_points) * qty - fees_inr
    else:
        entry_points = atm_entry.bid - wing_entry.ask
        exit_points = atm_exit.ask - wing_exit.bid
        expiry_risk = (width - entry_points) * qty + fees_inr
        net = (entry_points - exit_points) * qty - fees_inr
    if not 0 < entry_points < width:
        raise ValueError("entry spread is not economically valid")
    return {"structure": structure, "kind": atm_entry.kind, "expiry": str(atm_entry.expiry),
            "atm_strike": atm_entry.strike, "wing_strike": wing_entry.strike,
            "lots": lots, "entry_points": entry_points, "exit_points": exit_points,
            "net_pnl_inr": net, "expiry_risk_before_exit_costs_inr": expiry_risk,
            "evidence": "observed book diagnostic; no validated options edge"}


def report(*, zoo: Path = Path("data/paper_zoo.duckdb"),
           paper: Path = Path("data/paper_live.duckdb"),
           research: Path = Path("data/financial_brain.duckdb"),
           options: Path = Path("data/options_quotes.duckdb")) -> dict:
    out = {"options_quote_archive": "present" if options.exists() else "absent",
           "verdict": "NO_NEW_EDGE_VERDICT"}
    for label, path, fn in (("m1_forward", zoo, m1_forward_snapshot),
                            ("paper_execution", paper, execution_snapshot),
                            ("filing_timestamps_training", research, filing_timestamp_coverage)):
        if not path.exists():
            out[label] = {"status": "MISSING_DATABASE"}
            continue
        con = duckdb.connect(str(path), read_only=True)
        try:
            out[label] = fn(con)
        except duckdb.Error as exc:
            out[label] = {"status": "UNAVAILABLE", "reason": str(exc)[:120]}
        finally:
            con.close()
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only, non-promoting edge research diagnostics")
    parser.parse_args(argv)
    print(json.dumps(report(), default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
