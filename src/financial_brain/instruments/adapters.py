"""Import adapters: fill the instrument master from tables this project already keeps.

* equities and ETFs from the observed security master (``securities``, ``security_listings``)
* ISIN successions from ``isin_successions``
* mutual-fund schemes from ``mf_nav`` (AMFI gives each plan and option its own scheme code)
* an exchange calendar from the days that actually had prices

Every adapter returns what it imported **and what it refused**, with the reason. A row that would break an
invariant of the master (an overlapping identity, an unconfirmed ratio) is reported, never forced in.
The adapters only read; they write nothing to the database.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from fractions import Fraction

from financial_brain.instruments.clocks import Calendar, CalendarKind
from financial_brain.instruments.master import (
    AssetClass,
    InstrumentError,
    InstrumentId,
    InstrumentMaster,
    InstrumentState,
    Provenance,
    StateChange,
    Succession,
    Terms,
)

#: A succession ratio is inferred from prices, which also carry a day's market move. Fractions with larger
#: denominators are so dense that any price ratio lands near one, so only whole and half ratios within
#: this tolerance are accepted. Anything else needs a ratio confirmed from the corporate-action notice.
RATIO_TOLERANCE = 0.03
MAX_DENOMINATOR = 2


def equity_uid(isin: str) -> str:
    return f"IN:{isin}"


def scheme_uid(scheme_code: str) -> str:
    return f"IN:MF:{scheme_code}"


def _public_from(day: date) -> datetime:
    """A listing seen in a day's bhavcopy was public by the end of that day (UTC midnight after)."""
    return datetime.combine(day, time(23, 59, 59), tzinfo=timezone.utc)


def _report() -> dict:
    return {"imported": 0, "refused": []}


def import_listings(con, master: InstrumentMaster, *, now: datetime) -> dict:
    """Equities and ETFs. One identity version per (ISIN, exchange, ticker) span actually observed.

    A span still running on the exchange's latest observed day is left open; an earlier end is closed and
    the instrument is marked DELISTED from the next day only if it never reappears on that exchange.
    Asset class is ETF only where the source said so; everything else is EQUITY.
    """
    out = _report()
    latest = dict(con.execute("SELECT exchange, MAX(last_seen) FROM security_listings GROUP BY 1").fetchall())
    rows = con.execute("""
        SELECT l.isin, l.exchange, l.ticker, MIN(l.first_seen), MAX(l.last_seen),
               ANY_VALUE(l.instrument_id), ANY_VALUE(s.instrument_type)
        FROM security_listings l LEFT JOIN securities s USING (isin)
        GROUP BY l.isin, l.exchange, l.ticker ORDER BY MIN(l.first_seen), l.isin, l.exchange
    """).fetchall()
    last_by_uid: dict[str, date] = {}
    first_by_uid: dict[str, date] = {}
    for isin, exchange, ticker, first, last, token, kind in rows:
        uid = equity_uid(isin)
        open_ended = last >= latest[exchange]
        try:
            master.add_identity(InstrumentId(
                uid=uid, asset_class=AssetClass.ETF if (kind or "").upper() == "ETF" else AssetClass.EQUITY,
                market="IN", exchange=exchange, symbol=ticker, isin=isin, provider_token=token,
                effective_from=first, effective_to=None if open_ended else last,
                provenance=Provenance("security_listings", "", _public_from(first), now)), now=now)
        except InstrumentError as exc:
            out["refused"].append({"isin": isin, "exchange": exchange, "ticker": ticker, "why": str(exc)})
            continue
        out["imported"] += 1
        first_by_uid[uid] = min(first, first_by_uid.get(uid, first))
        last_by_uid[uid] = date.max if open_ended else max(last, last_by_uid.get(uid, last))
    for uid, first in first_by_uid.items():
        master.add_state(StateChange(uid, first, InstrumentState.LISTED,
                                     Provenance("security_listings", "", _public_from(first), now)), now=now)
        last = last_by_uid[uid]
        if last != date.max:
            gone = date.fromordinal(last.toordinal() + 1)
            master.add_state(StateChange(uid, gone, InstrumentState.DELISTED, Provenance(
                "security_listings: no longer observed", "", _public_from(gone), now)), now=now)
    return out


def snap_ratio(price_ratio: float | None) -> Decimal | None:
    """Units ratio implied by a price ratio, if it is within tolerance of a simple fraction."""
    if not price_ratio or price_ratio <= 0:
        return None
    implied = 1.0 / price_ratio
    frac = Fraction(implied).limit_denominator(MAX_DENOMINATOR)
    if abs(float(frac) - implied) / implied > RATIO_TOLERANCE:
        return None
    return Decimal(frac.numerator) / Decimal(frac.denominator)


def import_successions(con, master: InstrumentMaster, *, now: datetime) -> dict:
    """ISIN changes. Only corroborated ones whose unit ratio snaps cleanly are imported."""
    out = _report()
    rows = con.execute("""SELECT old_isin, new_isin, effective_date, price_ratio, confidence, observed_at
                          FROM isin_successions ORDER BY effective_date""").fetchall()
    for old, new, eff, price_ratio, confidence, observed in rows:
        why = None
        ratio = snap_ratio(price_ratio)
        if confidence != "corroborated":
            why = f"confidence is {confidence}"
        elif ratio is None:
            why = f"price ratio {price_ratio} does not imply a simple unit ratio"
        if why is None and ratio is not None:
            try:
                master.add_succession(Succession(
                    equity_uid(old), equity_uid(new), eff, "ISIN_CHANGE",
                    Provenance(f"isin_successions (ratio snapped from price ratio {price_ratio})", "",
                               min(observed, now), now), ratio=ratio), now=now)
                out["imported"] += 1
                continue
            except InstrumentError as exc:
                why = str(exc)
        out["refused"].append({"old_isin": old, "new_isin": new, "why": why})
    return out


_PLAN = {"direct": "DIRECT", "regular": "REGULAR"}


def _option(text: str | None) -> str | None:
    t = (text or "").lower()
    if "growth" in t:
        return "GROWTH"
    if "reinvest" in t:
        return "IDCW_REINVEST"
    if "idcw" in t or "dividend" in t or "payout" in t:
        return "IDCW_PAYOUT"
    return None


def import_schemes(con, master: InstrumentMaster, *, now: datetime) -> dict:
    """Mutual-fund schemes seen in ``mf_nav``. The scheme code is the symbol on exchange ``AMFI``.

    The identity starts on the first NAV date held and is public from when that row was first observed
    here, which for a newly started archive is later than the scheme's real launch. No state is recorded:
    whether a scheme is open for purchase is not in the NAV file, so it stays unknown and blocks trading
    until scheme metadata supplies it.
    """
    out = _report()
    rows = con.execute("""
        SELECT scheme_code, MIN(nav_date), MIN(observed_at), ANY_VALUE(isin_growth),
               ANY_VALUE(isin_reinvest), ANY_VALUE(plan), ANY_VALUE(option), ANY_VALUE(lake_key)
        FROM mf_nav GROUP BY scheme_code ORDER BY scheme_code
    """).fetchall()
    for code, first, observed, isin_g, isin_r, plan, option, lake_key in rows:
        uid = scheme_uid(code)
        prov = Provenance("mf_nav (AMFI NAVAll.txt)", lake_key or "", min(observed, now), now)
        try:
            master.add_identity(InstrumentId(
                uid=uid, asset_class=AssetClass.MUTUAL_FUND, market="IN", exchange="AMFI", symbol=code,
                isin=(isin_g or isin_r or None), effective_from=first, provenance=prov), now=now)
        except InstrumentError as exc:
            out["refused"].append({"scheme_code": code, "why": str(exc)})
            continue
        master.add_terms(Terms(uid, first, prov, mf_plan=_PLAN.get((plan or "").lower()),
                               mf_option=_option(option)), now=now)
        out["imported"] += 1
    return out


def exchange_calendar(sessions: list[date], *, name: str = "NSE", source: str) -> Calendar:
    """A historical exchange calendar from observed session dates. It does not cover the future."""
    return Calendar.from_observed(name, CalendarKind.EXCHANGE, sessions, source)
