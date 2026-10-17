"""Data contracts: for every source a signal can rest on, where it comes from, which time it carries, how revisions
are kept, what it covers, what it does not, and how fresh it must be. ``check`` measures each one live, so a
contract that is not being met is a visible failure, not a footnote.

``max_lag`` is in calendar days from the expected session (weekends and holidays make some lag normal; the bound is
the point past which a signal built on the table must not be trusted).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Contract:
    table: str
    date_col: str
    source: str
    times: str
    revisions: str
    limits: str
    max_lag: int


CONTRACTS = [
    Contract("eod_prices", "business_date", "NSE/BSE bhavcopy (raw lake, hashed)",
             "trade date", "a republished file is kept beside the original", "listed names only from the archive start (2015)", 4),
    Contract("adjusted_prices", "business_date", "derived: eod_prices x corporate actions, by lineage",
             "trade date", "rebuilt; factors kept in adjustment_factors", "demergers partly reconciled (fb gaps)", 4),
    Contract("features", "business_date", "derived from adjusted_prices", "trade date (close)", "rebuilt nightly",
             "price/volume only", 4),
    Contract("market_regime", "business_date", "derived: Nifty 50, India VIX, breadth", "trade date", "versioned (v2)",
             "thresholds fixed in regime/brain.py, not fitted", 4),
    Contract("announcements", "business_date", "NSE corporate announcements", "published_at (exchange dissemination)",
             "new rows, never edited", "PDF text quality varies; materiality is rule-based", 4),
    Contract("financial_results", "filed_at", "NSE results XBRL (2019-24) + Integrated Filing (2025-)",
             "filed_at = dissemination time; period_end", "a revision is a new filing; first-reported kept",
             "2019 onward only (~7 years); ~99.5% pass arithmetic checks", 100),
    Contract("delivery", "business_date", "NSE MTO security-wise delivery", "trade date", "one file per day",
             "EQ series", 5),
    Contract("insider_disclosures", "disseminated_at", "NSE PIT XBRL (corporates-pit-gg)",
             "disseminated_at; trade_from/to; intimated_on", "a revision is its own filing (app_id)",
             "history starts 2026-07 only - a forward record", 5),
    Contract("index_levels", "business_date", "NSE index archives", "trade date", "kept as published",
             "historical constituents only partly known", 5),
]


def check(rcon, today: date | None = None) -> list[dict]:
    today = today or date.today()
    out = []
    for c in CONTRACTS:
        try:
            n, first, last = rcon.execute(f"SELECT COUNT(*), MIN({c.date_col}), MAX({c.date_col}) FROM {c.table}").fetchone()
        except Exception as e:                          # noqa: BLE001 - a missing table breaks its contract
            out.append({"table": c.table, "ok": False, "why": f"unreadable: {str(e)[:60]}"})
            continue
        last_d = last.date() if isinstance(last, datetime) else last
        lag = (today - last_d).days if last_d else None
        ok = bool(n) and lag is not None and lag <= c.max_lag
        out.append({"table": c.table, "ok": ok, "rows": int(n), "first": str(first), "last": str(last), "lag_days": lag,
                    "max_lag": c.max_lag, "source": c.source, "times": c.times, "revisions": c.revisions,
                    "limits": c.limits, "why": "" if ok else ("empty" if not n else f"{lag} days behind (max {c.max_lag})")})
    return out
