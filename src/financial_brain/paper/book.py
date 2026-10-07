"""Suggestion paper books: the terminal's names bought on paper and sold by the terminal's own exits.

Two books, each pre-registered before it was run (brain/07-Research):

    book-v1   position: the top 5 by expected net each evening, at most 10 open, sold at the stop or after the
              opportunity's horizon (60 sessions for the names that top the board)
    swing-v1  swing: the top 50 by the swing ranking each evening, at most 50 open at 2% each, sold at the swing
              stop or after 7 sessions

After each session's close the signals fill free slots; each is bought at the next session's close (the archive
has closing prices only), sold at the first close at or below its stop or at the close ``horizon`` sessions after
entry, and charged the sized delivery round trip. Every trade is kept - the losers are the reason the record is
worth anything - and each is set against the equal-weighted liquid universe bought and held over the same days.

``replay`` runs the same rules over history to give a record today; the ranking's expected-return model was
measured on 2016-2026, so a replay is in-sample and is labelled ``replay`` everywhere, apart from ``live``.

No order code exists here: the books are a table in ``data/paper_book.duckdb``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from ..costs.india import CostModel

CAPITAL = 1_000_000.0
MIN_ADV = 1e7
PATH = Path("data") / "paper_book.duckdb"
VERDICT_TRADES, VERDICT_DAYS = 100, 20


@dataclass(frozen=True)
class Spec:
    version: str
    top_n: int
    max_open: int
    style: str                   #: the plan style that sets the stop and the size
    horizon: int | None          #: sessions held; None = the opportunity's own horizon
    max_weight: float
    label: str
    ranker: str = "v1"           #: "v1" (the terminal's list), "swing", "learned" or "momentum"


POSITION = Spec("book-v1", 5, 10, "position", None, 0.10, "Position book: top 5 a day, up to 60 sessions")
SWING = Spec("swing-v1", 50, 50, "swing", 7, 0.02, "Swing book: top 50 a day, 7 sessions")
LEARNED = Spec("learned-v1", 20, 20, "position", 20, 0.05, "Learned-model book: top 20 of the 500 most liquid, 20 sessions",
               ranker="learned")
MOMENTUM = Spec("momentum-v1", 20, 20, "position", 20, 0.05, "Momentum book (the champion): top 20, 20 sessions",
                ranker="momentum")
BOOKS = {s.version: s for s in (POSITION, SWING, LEARNED, MOMENTUM)}
# the first book's names, kept so its tests and callers read as before
VERSION, TOP_N, MAX_OPEN = POSITION.version, POSITION.top_n, POSITION.max_open

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    version VARCHAR, mode VARCHAR, signal_day DATE, rank INTEGER, ticker VARCHAR, lineage VARCHAR, type VARCHAR,
    horizon INTEGER, shares INTEGER, stop DOUBLE, ref_price DOUBLE, expected_net DOUBLE, cost DOUBLE,
    entry_day DATE, entry_px DOUBLE, exit_day DATE, exit_px DOUBLE, exit_reason VARCHAR,
    last_day DATE, last_px DOUBLE, held INTEGER, bench DOUBLE,
    PRIMARY KEY (version, mode, signal_day, ticker));
CREATE TABLE IF NOT EXISTS skips (
    version VARCHAR, mode VARCHAR, signal_day DATE, ticker VARCHAR, rank INTEGER, expected_net DOUBLE, reason VARCHAR,
    PRIMARY KEY (version, mode, signal_day, ticker));
"""


class BookError(ValueError):
    pass


def connect(path: Path = PATH, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    if read_only:
        return duckdb.connect(str(path), read_only=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    old = con.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'book_positions'").fetchone()[0]
    con.execute("ALTER TABLE positions ADD COLUMN IF NOT EXISTS why VARCHAR")      # the combination behind a buy
    if old:                                     # the first layout keyed rows without the book's version
        con.execute("INSERT OR IGNORE INTO positions SELECT * FROM book_positions")
        con.execute("DROP TABLE book_positions")
    return con


# ---------------------------------------------------------------- the rules, as pure functions

def walk(path: pd.Series, *, stop: float, horizon: int) -> dict:
    """``path``: closes on the sessions after the signal day, oldest first. Entry at the first; exit at the first
    later close at or below ``stop``, else at the close ``horizon`` sessions after entry. Open if neither yet."""
    path = path.dropna()
    if path.empty:
        return {"entry_day": None, "entry_px": None, "exit_day": None, "exit_px": None, "exit_reason": None,
                "last_day": None, "last_px": None, "held": 0}
    days, px = list(path.index), path.to_numpy(dtype=float)
    out = {"entry_day": days[0], "entry_px": float(px[0]), "exit_day": None, "exit_px": None, "exit_reason": None}
    for i in range(1, len(px)):
        if px[i] <= stop:
            out.update(exit_day=days[i], exit_px=float(px[i]), exit_reason="stop")
            break
        if i >= horizon:
            out.update(exit_day=days[i], exit_px=float(px[i]), exit_reason="time")
            break
    last = i if out["exit_day"] is not None else len(px) - 1
    out.update(last_day=days[last], last_px=float(px[last]), held=last)
    return out


def choose(ranked: pd.DataFrame, held: set[str], open_count: int, *, top_n: int = TOP_N,
           max_open: int = MAX_OPEN) -> pd.DataFrame:
    """The top ``top_n`` of the ranking, minus names already held, into the free slots in rank order."""
    top = ranked.head(top_n).copy()
    top["rank"] = np.arange(1, len(top) + 1)
    top = top[~top["ticker"].isin(held)]
    return top.head(max(0, max_open - open_count))


# ---------------------------------------------------------------- against the archive

def ranking(rcon, day: date, capital: float = CAPITAL) -> pd.DataFrame:
    """book-v1: the terminal's ranking, one row per name by expected net."""
    from ..opportunity import scanners as S
    cs = S.cross_section(rcon, day)
    if cs.empty:
        return pd.DataFrame()
    best = S.best_per_name(S.scan(cs, day, capital=capital))
    if best.empty:
        return best
    best = best.merge(cs[["ticker", "lineage", "adv20", "vol_60"]].drop_duplicates("ticker"), on="ticker", how="left")
    return best.sort_values("expected_net", ascending=False).reset_index(drop=True)


def factor_ranking(rcon, day: date, spec: Spec, capital: float = CAPITAL) -> pd.DataFrame:
    """learned-v1 / momentum-v1: the 500 most liquid names, ordered by the learned model's score or by 12-1
    momentum, each with the plan's position stop. The learned book also carries each name's reasons."""
    from ..decisions import plan as P
    from ..opportunity import scanners as S
    cs = S.cross_section(rcon, day)
    if cs.empty:
        return pd.DataFrame()
    cs = cs.sort_values("adv20", ascending=False).head(500)
    if spec.ranker == "learned":
        from ..evaluation import learned_model as LM
        sc = LM.score_day(rcon, day)
        cs = cs.merge(sc[["lineage", "pred", "why"]], on="lineage", how="inner").sort_values("pred", ascending=False)
    else:
        cs = cs.dropna(subset=["mom_12_1"]).sort_values("mom_12_1", ascending=False)
        cs["why"] = [f"12-month momentum {m * 100:+.0f}%" for m in cs["mom_12_1"]]
    rows = []
    for r in cs.head(spec.top_n * 2).itertuples():
        try:
            pl = P.plan(ticker=r.ticker, price=float(r.price), vol_annual=float(r.vol_60), adv_inr=float(r.adv20),
                        capital_inr=capital, style="position", max_weight=spec.max_weight)
        except P.PlanError:
            continue
        rows.append({"ticker": r.ticker, "lineage": r.lineage, "type": spec.ranker, "horizon": spec.horizon,
                     "price": float(r.price), "invalidation": float(pl["stop_price"]), "adv20": float(r.adv20),
                     "vol_60": float(r.vol_60), "expected_net": 0.0, "why": r.why})
    return pd.DataFrame(rows)


def swing_ranking(rcon, day: date, spec: Spec = SWING, capital: float = CAPITAL,
                  costs: CostModel | None = None) -> pd.DataFrame:
    """swing-v1: names by the decile model's 7-session expectation, ties (a whole decile shares one number)
    broken by the composite score itself - not by cost, which turned book-v1 into a large-cap bet."""
    from ..decisions import net_alpha as NA
    from ..decisions import plan as P
    from ..opportunity import engine as E
    from ..opportunity import scanners as S
    costs = costs or CostModel()
    cs = S.cross_section(rcon, day)
    if cs.empty:
        return pd.DataFrame()
    cs = cs.assign(z=E.composite(cs).to_numpy(),
                   expected_return=S.decile_expectation(cs).to_numpy() * S.HORIZON_SCALE[spec.horizon])
    cs = cs.sort_values(["expected_return", "z"], ascending=False).head(spec.top_n * 3).reset_index(drop=True)
    rows = []
    for _, r in cs.iterrows():
        try:
            pl = P.plan(ticker=str(r["ticker"]), price=float(r["price"]), vol_annual=float(r["vol_60"]),
                        adv_inr=float(r["adv20"]), capital_inr=capital, style=spec.style, max_weight=spec.max_weight,
                        atr_pct=float(r["atr_14_pct"]) if pd.notna(r["atr_14_pct"]) else None)
        except P.PlanError:
            continue
        if pl["shares"] <= 0:
            continue
        notional = pl["shares"] * float(r["price"])
        na = NA.compute(excess=float(r["expected_return"]), horizon=spec.horizon, order_value=notional,
                        adv_inr=float(r["adv20"]), vol_annual=float(r["vol_60"]), weight=notional / capital, costs=costs)
        c = na.costs
        rows.append({"ticker": r["ticker"], "lineage": r["lineage"], "type": "swing", "horizon": spec.horizon,
                     "price": float(r["price"]), "invalidation": float(pl["stop_price"]), "shares": int(pl["shares"]),
                     "adv20": float(r["adv20"]), "vol_60": float(r["vol_60"]), "z": float(r["z"]),
                     "expected_return": float(r["expected_return"]), "cost": float(c),
                     "expected_net": float(na.net), "alpha": na.as_dict()})
    return pd.DataFrame(rows)


_BENCH: dict = {}


def universe_hold(rcon, signal_day: date, entry_day: date, end: date) -> float:
    """The liquid universe of the signal day, bought at the trade's entry close and held to its exit close,
    equal-weighted. Buy-and-hold, like the trade: compounding the daily cross-sectional mean instead would be a
    daily-rebalanced book, which earns a rebalancing and bid-ask bonus a held position cannot (1.35 points over
    60 sessions on the first window checked)."""
    key = (signal_day, entry_day, end)
    if key not in _BENCH:
        v = rcon.execute("""SELECT AVG(e.close_adj / s.close_adj - 1) FROM features f
                            JOIN adjusted_prices s ON s.lineage = f.lineage AND s.business_date = ?
                            JOIN adjusted_prices e ON e.lineage = f.lineage AND e.business_date = ?
                            WHERE f.business_date = ? AND f.adv20 >= ? AND s.close_adj > 0""",
                         [entry_day, end, signal_day, MIN_ADV]).fetchone()[0]
        _BENCH[key] = float(v) if v is not None else None
    return _BENCH[key]


def _closes(rcon, lineage: str, after: date, upto: date) -> pd.Series:
    rows = rcon.execute("""SELECT business_date, close_adj FROM adjusted_prices
                           WHERE lineage = ? AND business_date > ? AND business_date <= ? AND close_adj > 0
                           ORDER BY business_date""", [lineage, after, upto]).fetchall()
    return pd.Series({pd.Timestamp(d).date(): float(c) for d, c in rows}, dtype=float)


def _plan_shares(r, capital: float, spec: Spec = POSITION) -> int:
    from ..decisions import plan as P
    try:
        return int(P.plan(ticker=str(r["ticker"]), price=float(r["price"]), vol_annual=float(r["vol_60"]),
                          adv_inr=float(r["adv20"]), capital_inr=capital, style=spec.style,
                          max_weight=spec.max_weight)["shares"])
    except P.PlanError:
        return 0


def _resolve(rcon, book, mode: str, upto: date, spec: Spec = POSITION) -> int:
    """Bring every position not yet sold up to ``upto``."""
    rows = book.execute("""SELECT signal_day, ticker, lineage, horizon, stop FROM positions
                           WHERE mode = ? AND version = ? AND exit_day IS NULL""", [mode, spec.version]).fetchall()
    for sd, tk, lin, h, stop in rows:
        w = walk(_closes(rcon, lin, sd, upto), stop=stop, horizon=h)
        end = w["exit_day"] or w["last_day"]
        b = universe_hold(rcon, sd, w["entry_day"], end) if w["entry_day"] else None
        book.execute("""UPDATE positions SET entry_day = ?, entry_px = ?, exit_day = ?, exit_px = ?,
                        exit_reason = ?, last_day = ?, last_px = ?, held = ?, bench = ?
                        WHERE version = ? AND mode = ? AND signal_day = ? AND ticker = ?""",
                     [w["entry_day"], w["entry_px"], w["exit_day"], w["exit_px"], w["exit_reason"], w["last_day"],
                      w["last_px"], w["held"], b, spec.version, mode, sd, tk])
    return len(rows)


def step(rcon, book, day: date, *, mode: str, spec: Spec = POSITION, capital: float = CAPITAL,
         costs: CostModel | None = None) -> int:
    """Signals of ``day``: fill free slots. A slot is taken by every position not sold by ``day``'s close."""
    costs = costs or CostModel()
    occ = book.execute("""SELECT ticker FROM positions WHERE mode = ? AND version = ? AND signal_day < ?
                          AND (exit_day IS NULL OR exit_day > ?)""", [mode, spec.version, day, day]).fetchall()
    held = {r[0] for r in occ}
    if len(held) >= spec.max_open:
        return 0
    if spec.ranker in ("learned", "momentum"):
        ranked = factor_ranking(rcon, day, spec, capital)
    else:
        ranked = swing_ranking(rcon, day, spec, capital, costs) if spec.style == "swing" else ranking(rcon, day, capital)
    if ranked.empty:
        return 0
    added = 0
    chosen = choose(ranked, held, len(held), top_n=spec.top_n, max_open=spec.max_open)
    for k, r in enumerate(ranked.head(spec.top_n).itertuples(), 1):     # the rejections, so nothing silently vanishes
        if r.ticker in set(chosen["ticker"]):
            continue
        why = "already held" if r.ticker in held else "no free slot"
        book.execute("INSERT OR REPLACE INTO skips VALUES (?,?,?,?,?,?,?)",
                     [spec.version, mode, day, r.ticker, k, float(r.expected_net), why])
    for _, r in chosen.iterrows():
        if spec.style == "swing":
            shares, cost = int(r["shares"]), float(r["cost"])
        else:
            shares = _plan_shares(r, capital, spec)
            if shares <= 0:
                continue
            cost = costs.round_trip_sized(order_value=shares * float(r["price"]), adv_value=float(r["adv20"]),
                                          daily_vol=float(r["vol_60"]) / np.sqrt(252.0))["total"]
        book.execute("""INSERT OR REPLACE INTO positions (version, mode, signal_day, rank, ticker, lineage, type,
                        horizon, shares, stop, ref_price, expected_net, cost, why) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     [spec.version, mode, day, int(r["rank"]), r["ticker"], r["lineage"], r["type"],
                      int(spec.horizon or r["horizon"]), shares, float(r["invalidation"]), float(r["price"]),
                      float(r["expected_net"]), float(cost), r.get("why")])
        added += 1
    return added


def sessions(rcon, start: date, end: date) -> list[date]:
    return [pd.Timestamp(d).date() for (d,) in rcon.execute(
        "SELECT DISTINCT business_date FROM features WHERE business_date >= ? AND business_date <= ? ORDER BY 1",
        [start, end]).fetchall()]


def replay(rcon, book, start: date, end: date | None = None, *, spec: Spec = POSITION, capital: float = CAPITAL,
           mode: str = "replay") -> int:
    """The same rules walked through history, session by session. Labelled ``replay`` (in-sample model); a
    different ``mode`` label (e.g. ``replay-week``) keeps a shorter run beside the long one."""
    if mode == "live":
        raise BookError("a replay is never labelled live")
    end = end or rcon.execute("SELECT MAX(business_date) FROM features").fetchone()[0]
    book.execute("DELETE FROM positions WHERE mode = ? AND version = ?", [mode, spec.version])
    book.execute("DELETE FROM skips WHERE mode = ? AND version = ?", [mode, spec.version])
    added = 0
    for d in sessions(rcon, start, end):
        _resolve(rcon, book, mode, d, spec)            # only what is known by d's close decides the free slots
        added += step(rcon, book, d, mode=mode, spec=spec, capital=capital)
    _resolve(rcon, book, mode, end, spec)
    return added


def live(rcon, book, *, spec: Spec = POSITION, capital: float = CAPITAL) -> dict:
    """Evening run: bring live positions up to the latest session, then take that session's signals."""
    day = rcon.execute("SELECT MAX(business_date) FROM features").fetchone()[0]
    _resolve(rcon, book, "live", day, spec)
    added = step(rcon, book, day, mode="live", spec=spec, capital=capital)
    return {"day": day, "added": added}


# ---------------------------------------------------------------- the record

def frame(book, mode: str, spec: Spec = POSITION) -> pd.DataFrame:
    df = book.execute("SELECT * FROM positions WHERE mode = ? AND version = ? ORDER BY signal_day, rank",
                      [mode, spec.version]).df()
    if df.empty:
        return df
    df["notional"] = df["shares"] * df["entry_px"]
    px = df["exit_px"].fillna(df["last_px"])
    df["gross"] = px / df["entry_px"] - 1
    df["net"] = df["gross"] - df["cost"]
    df["net_rs"] = df["notional"] * df["net"]
    df["excess"] = df["net"] - df["bench"]
    df["status"] = np.where(df["entry_day"].isna(), "BUY AT NEXT CLOSE",
                            np.where(df["exit_day"].isna(), "OPEN", "SOLD"))
    return df


def summary(book, mode: str, spec: Spec = POSITION) -> dict:
    df = frame(book, mode, spec)
    closed = df[df["status"] == "SOLD"] if len(df) else df
    out = {"mode": mode, "version": spec.version, "closed": int(len(closed)),
           "open": int((df["status"] != "SOLD").sum()) if len(df) else 0}
    held = df[df["status"] == "OPEN"] if len(df) else df
    if len(held):
        out.update(open_net_rs=float(held["net_rs"].sum()), open_bench_rs=float((held["notional"] * held["bench"]).sum()))
    if closed.empty:
        out["verdict"] = "COLLECTING"
        return out
    per_day = closed.groupby("entry_day")["excess"].mean()
    se = per_day.std(ddof=1) / np.sqrt(len(per_day)) if len(per_day) > 1 else float("nan")
    t = float(per_day.mean() / se) if se and se == se and se > 0 else float("nan")
    out.update(entry_days=int(len(per_day)), win_rate=float((closed["net"] > 0).mean()),
               beat_universe=float((closed["excess"] > 0).mean()), mean_net=float(closed["net"].mean()),
               mean_excess=float(closed["excess"].mean()), t_excess=t, net_rs=float(closed["net_rs"].sum()),
               bench_rs=float((closed["notional"] * closed["bench"]).sum()),
               stopped=float((closed["exit_reason"] == "stop").mean()), mean_held=float(closed["held"].mean()),
               first=str(closed["entry_day"].min()), last=str(closed["exit_day"].max()))
    if mode != "live":
        out["verdict"] = "REPLAY (in-sample model; a demonstration, not evidence)"
    elif len(closed) < VERDICT_TRADES or len(per_day) < VERDICT_DAYS:
        out["verdict"] = f"COLLECTING ({len(closed)}/{VERDICT_TRADES} trades, {len(per_day)}/{VERDICT_DAYS} entry days)"
    else:
        out["verdict"] = "WORKING" if t >= 2 and out["mean_excess"] > 0 else "NOT DEMONSTRATED"
    return out


def modes(book, spec: Spec = POSITION) -> list[str]:
    got = {r[0] for r in book.execute("SELECT DISTINCT mode FROM positions WHERE version = ?", [spec.version]).fetchall()}
    return ["live"] + sorted(got - {"live"}, key=lambda m: (m != "replay-week", m))


def render(book, spec: Spec = POSITION) -> str:
    lines = [f"{spec.label} ({spec.version}): at most {spec.max_open} open, Rs {CAPITAL / 1e5:.0f} lakh"]
    for mode in modes(book, spec):
        s = summary(book, mode, spec)
        lines.append(f"\n{mode.upper()}: {s['verdict']}; {s['closed']} sold, {s['open']} open")
        if "open_net_rs" in s:
            lines.append(f"  open positions, net of the round trip: Rs {s['open_net_rs']:,.0f} "
                         f"(universe on the same money Rs {s['open_bench_rs']:,.0f})")
        if s["closed"]:
            lines.append(f"  win rate {s['win_rate']:.0%} | beat the universe {s['beat_universe']:.0%} | mean net "
                         f"{s['mean_net']:+.2%} | mean excess {s['mean_excess']:+.2%} (t {s['t_excess']:+.2f}, "
                         f"{s['entry_days']} entry days) | stopped {s['stopped']:.0%} | held {s['mean_held']:.0f} sessions")
            lines.append(f"  net P&L Rs {s['net_rs']:,.0f} against Rs {s['bench_rs']:,.0f} for the universe on the same money")
    return "\n".join(lines)


def attribution(rcon, book, mode: str, spec: Spec = POSITION, *, limit: int = 400) -> dict:
    """Where the sold positions' returns came from: market, sector, stock selection, costs (fills are at closes, so
    entry and exit timing are zero by construction). Means per trade, from ``portfolio/journal.attribute``."""
    from ..portfolio import journal as J
    df = frame(book, mode, spec)
    sold = df[df["status"] == "SOLD"].tail(limit) if len(df) else df
    if sold.empty:
        return {}
    trips = pd.DataFrame({"symbol": sold["ticker"], "product": "CNC", "side": "long", "qty": sold["shares"],
                          "entry_day": sold["entry_day"], "exit_day": sold["exit_day"], "entry_px": sold["entry_px"],
                          "exit_px": sold["exit_px"], "gross": sold["shares"] * (sold["exit_px"] - sold["entry_px"]),
                          "ret": sold["gross"]})
    a = J.attribute(rcon, trips)
    a = a[a["attributed"]] if "attributed" in a else a
    if a.empty:
        return {}
    out = {k: float(a[k].mean()) for k in ("market", "sector", "selection", "costs", "net")}
    out["trades"] = int(len(a))
    return out
