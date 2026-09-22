"""Situational awareness as refusals: when not to act, decided before acting (C20/C19).

A system that always has an answer is dangerous. Most of the ways this one could lose
money are not wrong opinions but right opinions applied in the wrong conditions: on stale
data, in a crisis, in a name too thin to exit, in a group it already owns, or straight
after a run of losses that says something has changed.

Each check below answers one question with data that is already in the record, and every
refusal carries the number behind it. They are deliberately **deterministic** - Tier 0.
A model deciding when a model may act is a loop with no floor.

    stale_data        the last session, announcements and regime we hold must be recent
    crisis            no new long in a CRISIS regime
    liquidity         the name must trade enough to exit the position in a day
    concentration     one promoter group, one position; a cap on open positions
    drawdown          a run of losses pauses new trades until reviewed
    lesson            a rule the closed record actually supports (decisions/postmortem)

``assess`` returns every breach, not the first, because "thin *and* in a crisis" is a
different situation from either alone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from . import postmortem

MAX_STALE_SESSIONS = 3          # trading days of price data we may be behind
MAX_STALE_NEWS_DAYS = 4         # calendar days without any announcement is suspicious
MIN_ADV_INR = 1e7               # Rs 1 crore average daily traded value
MAX_POSITION_SHARE_OF_ADV = 0.10
MAX_OPEN_POSITIONS = 12
MAX_PER_GROUP = 1
DRAWDOWN_WINDOW = 5             # most recent closed trades
DRAWDOWN_LIMIT = -0.08          # mean excess over that window that pauses new trades


@dataclass
class Breach:
    check: str
    detail: str
    blocking: bool = True


@dataclass
class Assessment:
    as_of: date
    isin: str | None
    breaches: list[Breach] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        return not any(b.blocking for b in self.breaches)

    def describe(self) -> str:
        if self.safe and not self.breaches:
            return "no safety condition breached"
        return "; ".join(f"{b.check}: {b.detail}" for b in self.breaches)


def _latest(con, table: str, column: str = "business_date"):
    try:
        return con.execute(f"SELECT MAX({column}) FROM {table}").fetchone()[0]
    except Exception:            # noqa: BLE001 - a missing table is itself staleness
        return None


def check_freshness(con, as_of: date) -> list[Breach]:
    out = []
    last_price = _latest(con, "adjusted_prices")
    if last_price is None:
        return [Breach("stale_data", "no price history at all")]
    sessions_behind = con.execute(
        "SELECT COUNT(DISTINCT business_date) FROM adjusted_prices WHERE business_date > ?",
        [last_price]).fetchone()[0]
    gap = (as_of - last_price).days
    if gap > MAX_STALE_SESSIONS * 2 + 1:      # weekends and a holiday
        out.append(Breach("stale_data",
                          f"last price {last_price}, {gap} days before {as_of}"))
    last_news = _latest(con, "announcements")
    if last_news and (as_of - last_news).days > MAX_STALE_NEWS_DAYS:
        out.append(Breach("stale_data",
                          f"last announcement {last_news}, {(as_of - last_news).days} "
                          f"days old"))
    if sessions_behind:                        # future-dated rows: something is wrong
        out.append(Breach("stale_data", f"{sessions_behind} price sessions after {last_price}"))
    return out


def check_regime(con, as_of: date) -> list[Breach]:
    row = con.execute("""SELECT regime, business_date FROM market_regime
                         WHERE business_date <= ? ORDER BY business_date DESC LIMIT 1""",
                      [as_of]).fetchone()
    if not row:
        return [Breach("crisis", "no regime has been computed", blocking=False)]
    if row[0] == "CRISIS":
        return [Breach("crisis", f"market regime is CRISIS as of {row[1]}")]
    return []


def check_liquidity(con, isin: str, as_of: date, position_inr: float) -> list[Breach]:
    try:
        row = con.execute("""SELECT f.adv20 FROM features f
                             JOIN security_lineage l ON l.lineage = f.lineage
                             WHERE l.isin = ? AND f.business_date <= ?
                               AND f.adv20 IS NOT NULL
                             ORDER BY f.business_date DESC LIMIT 1""",
                          [isin, as_of]).fetchone()
    except Exception:            # noqa: BLE001 - no feature history at all
        row = None
    if not row:
        # Unverifiable is not the same as fine: a position we cannot size against traded
        # value is one we may not be able to leave.
        return [Breach("liquidity", "no traded-value history for this name")]
    adv = float(row[0])
    if adv < MIN_ADV_INR:
        return [Breach("liquidity", f"20-session traded value Rs {adv:,.0f} is below the "
                                    f"Rs {MIN_ADV_INR:,.0f} floor")]
    share = position_inr / adv if adv else 1.0
    if share > MAX_POSITION_SHARE_OF_ADV:
        return [Breach("liquidity", f"position is {share:.0%} of a day's traded value "
                                    f"(limit {MAX_POSITION_SHARE_OF_ADV:.0%})")]
    return []


def check_concentration(con, isin: str, as_of: date) -> list[Breach]:
    open_n = con.execute("SELECT COUNT(*) FROM paper_trades WHERE status = 'open'"
                         ).fetchone()[0]
    out = []
    if open_n >= MAX_OPEN_POSITIONS:
        out.append(Breach("concentration",
                          f"{open_n} positions already open (limit {MAX_OPEN_POSITIONS})"))
    if con.execute("""SELECT 1 FROM information_schema.tables
                      WHERE table_name = 'holder_filings'""").fetchone():
        from ..graph import build as graph
        try:
            groups = graph.groups(con, as_of=as_of)
        except Exception:        # noqa: BLE001 - the graph is optional context
            groups = []
        mine = next((g for g in groups if isin in g["isins"]), None)
        if mine:
            held = [r[0] for r in con.execute(
                "SELECT isin FROM paper_trades WHERE status = 'open'").fetchall()]
            same = [h for h in held if h in mine["isins"] and h != isin]
            if len(same) >= MAX_PER_GROUP:
                out.append(Breach("concentration",
                                  f"already holding {len(same)} name(s) in the "
                                  f"{mine['anchor'] or 'same'} promoter group"))
    return out


def check_drawdown(con) -> list[Breach]:
    rows = [r[0] for r in con.execute("""SELECT excess FROM paper_trades
                                         WHERE status = 'closed' AND excess IS NOT NULL
                                         ORDER BY exit_date DESC LIMIT ?""",
                                      [DRAWDOWN_WINDOW]).fetchall()]
    if len(rows) < DRAWDOWN_WINDOW:
        return []
    mean = sum(rows) / len(rows)
    if mean <= DRAWDOWN_LIMIT:
        return [Breach("drawdown", f"last {len(rows)} closed trades averaged "
                                   f"{mean:+.1%}; new trades paused pending review")]
    return []


def assess(con, *, isin: str | None = None, as_of: date | None = None,
           position_inr: float = 300_000.0, regime: str | None = None,
           prompted_by: str | None = None) -> Assessment:
    """Every safety condition, checked before a trade is opened."""
    d = as_of or date.today()
    a = Assessment(as_of=d, isin=isin)
    a.breaches += check_freshness(con, d)
    a.breaches += check_regime(con, d)
    a.breaches += check_drawdown(con)
    if isin:
        a.breaches += check_liquidity(con, isin, d, position_inr)
        a.breaches += check_concentration(con, isin, d)
    for lesson in postmortem.gate(con, regime=regime, prompted_by=prompted_by):
        a.breaches.append(Breach("lesson", lesson.describe()))
    return a


def window(con, as_of: date | None = None) -> dict:
    """Is now a sensible time to be deciding at all? The market-wide half of the answer,
    without reference to any one name."""
    d = as_of or date.today()
    a = Assessment(as_of=d, isin=None)
    a.breaches += check_freshness(con, d)
    a.breaches += check_regime(con, d)
    a.breaches += check_drawdown(con)
    return {"as_of": d, "clear": a.safe, "why": a.describe(),
            "breaches": [b.check for b in a.breaches if b.blocking]}


def next_review(con, as_of: date | None = None) -> date:
    """When the system should look again after refusing: the next session it holds data
    for, or tomorrow."""
    d = as_of or date.today()
    row = con.execute("""SELECT MIN(business_date) FROM adjusted_prices
                         WHERE business_date > ?""", [d]).fetchone()
    return row[0] if row and row[0] else d + timedelta(days=1)
