"""A paper trading engine: signal, then decision, then order, then fill, then portfolio, then P&L.

The distinction this exists to enforce is the one between research and a trading system:

    research          signal today -> buy today's close -> compute the future return
    trading system    signal at t -> order at t+1 -> fill from the volume that traded
                      -> position -> cash -> mark to market -> P&L

The first is a perfectly good way to measure whether a signal orders names. It is not a way to find
out what a book would have done, because it never holds anything: there is no cash, no position that
persists, no order that fails to fill, and no day on which the strategy owns something it did not
choose today.

## The four separations the loop keeps

**Signal time from decision time.** The signal is computed from features dated at or before session
*t*. The order is placed on *t+1* and filled from *t+1*'s volume. A backtest that fills at the close
that generated the signal has bought a day of hindsight per trade, which is the single most common
way these things overstate themselves.

**Target from holdings.** The strategy names a target book; the engine diffs it against what is
actually held and trades only the difference. A name already held is not re-bought, which is where
the turnover number comes from rather than an assumption about it.

**Intent from fill.** An order that cannot complete leaves a smaller position, not the intended one,
and the next rebalance sees the smaller one. Orders are not silently completed.

**Cash from equity.** Cash is tracked, trades settle against it, and equity is
``cash + sum(shares x price)``. The invariant is checked every session, because an accounting error
in a paper engine looks exactly like alpha.

## Fills: measured where possible, and the coverage is reported

Minute bars exist for **100 names over 497 sessions**; a cross-sectional quintile here is ~300 names
over eleven years. So a fill is simulated through real volume (``execution/simulator.py``) when the
bars exist and taken at the next session's close otherwise, and the **share of notional filled each
way is a first-class output**. A run whose fills were 80% close-priced is not a run that measured
execution, and the number says so rather than the caveat living in someone's memory.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date

from ..costs import book as costbook
from ..costs.india import CostModel, Segment
from ..execution import simulator as sim

#: Sessions between rebalances.
REBALANCE = 20
#: Names held at most.
MAX_POSITIONS = 40
#: Share of a minute's volume an order may take, when fills are simulated.
PARTICIPATION = 0.10
#: Cash kept back. A book at 100% invested cannot pay costs, and a paper engine that lets cash go
#: negative is quietly levered.
CASH_BUFFER = 0.02
#: How far a position may drift from its target weight before the rebalance corrects it, as a
#: fraction of that target. Under proportional costs the optimal policy is not to hold the target
#: exactly but to tolerate a region around it (Constantinides 1986), because a trade smaller than
#: the spread it crosses is negative-value by construction. Without a band the trim leg fires on
#: the cost drag alone: a static book with a constant ranking and constant prices re-traded every
#: name at two consecutive rebalances, because paying costs lowers equity, which lowers the target
#: weight, which makes every position overweight by a rupee.
REBALANCE_BAND = 0.20
#: Equity-identity tolerance, as a fraction. Anything above this is an accounting bug, not rounding.
IDENTITY_TOLERANCE = 1e-6

CLOSE_FILL, SIMULATED_FILL = "close", "simulated"


class PaperError(ValueError):
    pass


@dataclass
class Position:
    #: The **lineage**, not an ISIN. An ISIN is a security code, not a company: 843 of this
    #: database's 17,060 ISINs are superseded ones, and a position keyed on the old code loses its
    #: own price the day the new one starts. See the module note on successions.
    lineage: str
    shares: int = 0
    cost_basis: float = 0.0        # total rupees paid, for realised-P&L accounting

    @property
    def avg_price(self) -> float:
        return self.cost_basis / self.shares if self.shares else 0.0


@dataclass
class Trade:
    session: date
    lineage: str
    side: str
    shares: int
    price: float
    notional: float
    cost_inr: float
    fill_kind: str
    shortfall_bps: float | None = None
    minutes: int | None = None
    intended_shares: int = 0

    @property
    def unfilled(self) -> int:
        return self.intended_shares - self.shares


@dataclass
class Run:
    experiment_id: str
    config: dict
    equity: list[dict] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    rebalances: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: What the traded universe did on its own, and what the market did. Computed by the engine
    #: rather than left to whoever remembers to ask: the first run here returned +41.75% on a
    #: universe that itself returned +22.65% against a market that fell 5.89%, and reporting the
    #: first number without the other two is how universe selection becomes "alpha".
    control: dict = field(default_factory=dict)

    def summary(self) -> dict:
        if not self.equity:
            return {"experiment_id": self.experiment_id, "sessions": 0}
        start, end = self.equity[0], self.equity[-1]
        rets = [self.equity[i]["equity"] / self.equity[i - 1]["equity"] - 1
                for i in range(1, len(self.equity))
                if self.equity[i - 1]["equity"] > 0]
        sim_notional = sum(t.notional for t in self.trades
                           if t.fill_kind == SIMULATED_FILL)
        total_notional = sum(t.notional for t in self.trades)
        costs = sum(t.cost_inr for t in self.trades)
        import math
        from statistics import mean, stdev
        vol = stdev(rets) * math.sqrt(250) if len(rets) > 2 else float("nan")
        return {
            "experiment_id": self.experiment_id,
            "sessions": len(self.equity),
            "first": start["session"], "last": end["session"],
            "opening_equity": start["equity"], "closing_equity": end["equity"],
            "total_return": end["equity"] / start["equity"] - 1,
            "mean_daily": mean(rets) if rets else float("nan"),
            "vol_annual": vol,
            "sharpe_annual": ((mean(rets) / stdev(rets) * math.sqrt(250))
                              if len(rets) > 2 and stdev(rets) > 0 else float("nan")),
            "max_drawdown": _max_drawdown([e["equity"] for e in self.equity]),
            "trades": len(self.trades),
            "rebalances": len(self.rebalances),
            "charge_costs": self.config.get("charge_costs", True),
            "costs_inr": costs,
            "cost_drag": costs / start["equity"] if start["equity"] else float("nan"),
            # The number that says whether execution was measured or assumed.
            "notional_traded": total_notional,
            "simulated_fill_share": (sim_notional / total_notional
                                     if total_notional else float("nan")),
            "unfilled_orders": sum(1 for t in self.trades if t.unfilled),
            "warnings": self.warnings,
            # The only figures that make the return interpretable.
            "universe_return": self.control.get("universe_return"),
            "benchmark_return": self.control.get("benchmark_return"),
            "benchmark": self.control.get("benchmark"),
            "excess_over_universe": (
                (end["equity"] / start["equity"] - 1) - self.control["universe_return"]
                if self.control.get("universe_return") is not None else None),
            "excess_over_benchmark": (
                (end["equity"] / start["equity"] - 1) - self.control["benchmark_return"]
                if self.control.get("benchmark_return") is not None else None),
        }


def _max_drawdown(series: list[float]) -> float:
    peak, worst = series[0] if series else 0.0, 0.0
    for v in series:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1)
    return worst


def experiment_id(config: dict) -> str:
    """A hash of the configuration, so a run is identified by what produced it.

    Two runs with the same id must be the same experiment. That is the precondition for
    ``fb paper --experiment EXP-...`` reconstructing a result rather than recomputing something
    similar.
    """
    body = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)
    return "EXP-" + hashlib.sha256(body.encode()).hexdigest()[:12]


def _calendar(con, start: date, end: date) -> list[date]:
    return [r[0] for r in con.execute("""
        SELECT DISTINCT business_date FROM adjusted_prices
        WHERE business_date BETWEEN ? AND ? ORDER BY business_date""",
        [start, end]).fetchall()]


def _target_book(con, feature: str, as_of: date, *, direction: int,
                 min_adv: float, max_positions: int,
                 eligible: set | None = None) -> list[str]:
    """The strategy's intended holdings, from information available at ``as_of`` and no later.

    ``eligible`` restricts the **ranking universe**, not the result. Taking the global top N and
    then intersecting is a different and much worse strategy: the first run here did that, and
    because the top 20 of ~1,700 names rarely includes one of the 100 with minute bars, it held
    two to four names instead of twenty - a concentrated bet whose 33.7% volatility and -46%
    drawdown said nothing about the signal. Ranking within the eligible set is what
    "this strategy on this universe" means.

    Returns lineages. The first version joined ``security_lineage`` to return ISINs, which also made
    a lineage holding three ISINs produce three rows from one feature row - so the top twenty could
    contain the same company three times. ``features`` is keyed on lineage already and the join was
    never needed.
    """
    rows = con.execute(f"""
        SELECT f.lineage
        FROM features f
        WHERE f.business_date = (SELECT MAX(business_date) FROM features
                                 WHERE business_date <= ?)
          AND f.{feature} IS NOT NULL AND f.adv20 >= ?
          AND (? IS FALSE OR f.lineage IN (SELECT UNNEST(?)))
        ORDER BY CAST(f.{feature} AS DOUBLE) * {direction} DESC, f.lineage
        LIMIT ?
    """, [as_of, min_adv, eligible is not None,
          sorted(eligible) if eligible else [], max_positions]).fetchall()
    return [r[0] for r in rows]


class _PriceBook:
    """One query per session instead of one per position per session.

    Marking a book of 300 names over 2,700 sessions the naive way is 810,000 single-row queries,
    which took hours and made the eleven-year run unanswerable in practice. A session's closes come
    back in one scan. Only the current session and the signal session are ever read, so a handful
    of sessions is enough to hold - the cache is bounded rather than an accumulating copy of the
    price table.
    """

    KEEP = 4

    def __init__(self, con):
        self._con = con
        self._by_session: dict[date, dict[str, float]] = {}
        self._order: list[date] = []

    def _load(self, session: date) -> dict[str, float]:
        got = self._by_session.get(session)
        if got is None:
            got = {r[0]: r[1] for r in self._con.execute(
                """SELECT lineage, close_adj FROM adjusted_prices
                   WHERE business_date = ? AND close_adj > 0""", [session]).fetchall()}
            self._by_session[session] = got
            self._order.append(session)
            while len(self._order) > self.KEEP:
                self._by_session.pop(self._order.pop(0), None)
        return got

    def close(self, lineage: str, session: date) -> float | None:
        return self._load(session).get(lineage)


def _close(con, lineage: str, session: date) -> float | None:
    """Kept for callers holding a bare connection; a run goes through _PriceBook."""
    if isinstance(con, _PriceBook):
        return con.close(lineage, session)
    r = con.execute("""SELECT close_adj FROM adjusted_prices
                       WHERE lineage = ? AND business_date = ? AND close_adj > 0""",
                    [lineage, session]).fetchone()
    return r[0] if r else None


def _symbol(con, lineage: str) -> str | None:
    """A tradingsymbol for the minute-bar simulator, resolved through the lineage's ISINs.

    ``minute_bars`` is keyed on ISIN because that is what the broker feed carries, so a lineage has
    to be expanded before it can be looked up. Taking the newest symbol on purpose: a superseded
    ISIN's symbol may no longer be quotable.
    """
    r = con.execute("""SELECT MAX(m.tradingsymbol) FROM minute_bars m
                       JOIN security_lineage l ON l.isin = m.isin
                       WHERE l.lineage = ?""", [lineage]).fetchone()
    return r[0] if r and r[0] else None


def run(con, *, feature: str, start: date, end: date, capital_inr: float = 10_000_000.0,
        direction: int = 1, rebalance: int = REBALANCE,
        max_positions: int = MAX_POSITIONS, min_adv: float = 1e7,
        participation: float = PARTICIPATION,
        segment: Segment = Segment.DELIVERY,
        simulate_fills: bool = True,
        restrict_to_minute_bars: bool = False,
        eligible_lineages: set | None = None,
        universe_label: str = "",
        charge_costs: bool = True, band: float = REBALANCE_BAND) -> Run:
    """Walk the sessions, holding a real book with real cash.

    ``restrict_to_minute_bars`` trades only names whose minute bars exist, which gives a run with
    100% measured fills on a smaller universe. Both are worth having: the restricted run is the
    complete chain, and the unrestricted one shows what fill coverage a real universe would have.
    """
    cal = _calendar(con, start, end)
    if len(cal) < rebalance * 2:
        raise PaperError(f"{len(cal)} sessions between {start} and {end} is too few")
    costbook.ensure_view(con)
    prices = _PriceBook(con)
    model = CostModel()
    rt = {b: model.round_trip(turnover=1_000_000, segment=segment, bucket=b)["bps"] / 1e4
          for b in costbook.BUCKETS}
    if not charge_costs:
        # Not a way to make a strategy look better - a way to compare it to a control that pays no
        # costs either. The control is a cost-free index, so a net strategy measured against it is
        # charged twice over for its turnover, and the whole-universe drag test cannot separate
        # engine drift from brokerage until both sides are gross. Never quote a gross number as a
        # result.
        rt = dict.fromkeys(costbook.BUCKETS, 0.0)

    # An explicit universe beats the minute-bar shortcut. The shortcut selects names by turnover
    # as measured when the Kite ingest ran, which is *after* the window - so it is partly a list of
    # what went up, and any excess measured against it inherits that. Passing the universe in is
    # how a run can be given one chosen from information available at the start.
    eligible = eligible_lineages
    if eligible is None and restrict_to_minute_bars:
        # minute_bars is keyed on ISIN, so the shortcut has to be mapped onto lineages before it can
        # restrict a universe that is now keyed on them.
        eligible = {r[0] for r in con.execute(
            """SELECT DISTINCT l.lineage FROM minute_bars m
               JOIN security_lineage l ON l.isin = m.isin""").fetchall()}

    config = {"feature": feature, "start": start, "end": end,
              "capital_inr": capital_inr, "direction": direction,
              "rebalance": rebalance, "max_positions": max_positions,
              "min_adv": min_adv, "participation": participation,
              "segment": segment.value, "simulate_fills": simulate_fills,
              "restrict_to_minute_bars": restrict_to_minute_bars,
              "charge_costs": charge_costs, "band": band,
              "universe_label": universe_label,
              # The universe is part of the experiment's identity: the same strategy on a
              # different universe is a different experiment, and hashing only the strategy
              # would give two runs the same id.
              "universe_size": len(eligible) if eligible else None,
              "universe_hash": (hashlib.sha256(
                  "|".join(sorted(eligible)).encode()).hexdigest()[:12]
                  if eligible else None)}
    out = Run(experiment_id=experiment_id(config), config=config)

    cash = capital_inr
    positions: dict[str, Position] = {}
    buckets = {}

    for i, session in enumerate(cal):
        # ---------------------------------------------------- rebalance, one session late
        # The signal is computed from `prev`; the orders execute on `session`. That one-session
        # gap is the difference between a trading system and a backtest with hindsight.
        if i > 0 and (i - 1) % rebalance == 0:
            prev = cal[i - 1]
            target = _target_book(con, feature, prev, direction=direction,
                                  min_adv=min_adv, max_positions=max_positions,
                                  eligible=eligible)
            if not target:
                out.warnings.append(f"{session}: no eligible names in the target book")
            else:
                # costbook.buckets is keyed on (date, LINEAGE) and always was. Passing ISINs
                # into it meant every superseded name missed its bucket and silently fell back to
                # "micro", the most expensive tier - so the succession names were charged small-cap
                # impact on top of losing their prices.
                bmap = costbook.buckets(con, [prev], set(target) | set(positions))
                for lineage in set(target) | set(positions):
                    buckets[lineage] = bmap.get(
                        (prev, lineage), buckets.get(lineage, "micro"))
                cash, trades = _rebalance(
                    con, session=session, signal_session=prev, target=target,
                    positions=positions, cash=cash,
                    capital_inr=capital_inr, rt=rt, buckets=buckets,
                    participation=participation, simulate_fills=simulate_fills,
                    prices=prices, band=band)
                out.trades += trades
                out.rebalances.append({
                    "session": session, "signal_from": prev,
                    "target": len(target), "held_after": len(positions),
                    "trades": len(trades),
                    "notional": sum(t.notional for t in trades)})

        # ------------------------------------------------------------- mark to market
        held_value, priced, stale = 0.0, 0, 0
        for lineage, p in positions.items():
            px = prices.close(lineage, session)
            if px is None:
                # A name that did not trade is held at its last known value rather than dropped,
                # which is the conservative treatment: a suspended holding is not free.
                px = p.avg_price
                stale += 1
            else:
                priced += 1
            held_value += p.shares * px
        equity = cash + held_value
        if equity > 0 and abs((cash + held_value) - equity) / equity > IDENTITY_TOLERANCE:
            raise PaperError("equity identity violated")
        out.equity.append({"session": session, "equity": equity, "cash": cash,
                           "holdings_value": held_value, "positions": len(positions),
                           "stale_marks": stale,
                           "invested": (held_value / equity) if equity else 0.0})
    out.control = _control(con, cal[0], cal[-1], eligible=eligible, min_adv=min_adv,
                           rebalance=rebalance)
    return out


def calibrate(con, *, start: date, end: date, eligible_lineages: set | None = None,
              min_adv: float = 1e7, rebalance: int = REBALANCE,
              capital_inr: float = 1e9) -> dict:
    """Measure the engine's own drift, so an excess can be read against it.

    A book holding every eligible name at equal weight **is** the control by construction, so its
    gross excess must be zero. Whatever it actually is, is the engine: cash left uninvested by
    rounding share counts down, weights drifting inside the no-trade band, names starved when the
    buy loop runs out of cash partway down the ranking. That floor applies to every excess the
    engine reports, and an excess smaller than it means nothing.

    Costs are off deliberately. The control is a cost-free index, so a net book is charged for
    turnover the control never pays: on eleven years the same test read -106.68% net and -52.52%
    gross, and the 54-point difference was brokerage rather than drift.

    Returns the floor as a return gap. Quote it next to any excess from the same window - not once,
    because it grows with the number of rebalance periods rather than staying fixed.
    """
    r = run(con, feature="dist_52w_high", start=start, end=end, capital_inr=capital_inr,
            rebalance=rebalance, max_positions=10_000, min_adv=min_adv,
            simulate_fills=False, charge_costs=False, eligible_lineages=eligible_lineages,
            universe_label="calibration: the book IS the universe")
    s = r.summary()
    invested = sum(e["invested"] for e in r.equity) / len(r.equity)
    return {
        "floor": s["excess_over_universe"],
        "book_return": s["total_return"],
        "universe_return": s["universe_return"],
        "mean_invested": invested,
        "uninvested": 1 - invested,
        "periods": r.control.get("universe_periods"),
        "names": r.control.get("universe_names"),
        "note": "gross excess of a book holding its entire eligible universe, which must be zero. "
                "Any excess smaller in magnitude than this floor is engine drift, not strategy.",
    }


def _control(con, start: date, end: date, *, eligible: set | None, min_adv: float,
             rebalance: int = REBALANCE, benchmark: str = "Nifty 500") -> dict:
    """What the traded universe and the market did, with no strategy in the way.

    ``evaluation/control.py`` made this project's most important early finding by asking exactly
    this of the committee: the universe returned +5.00% and the committee contributed -1.85%. The
    same question has to be asked of every paper run, and asking it *inside* the engine is the
    difference between a control and a good intention.

    The control rebalances at the strategy's own frequency, which took three attempts to get right
    because each wrong version was biased in a different direction:

    * **Priced at both ends of the window.** Keeps only the names that still had a price at the
      end, so every delisting is deleted from the control and it becomes an index of survivors. On
      eleven years it reported the universe at +307%.
    * **Chained daily cross-sectional mean.** Fixes survivorship and introduces a rebalancing bonus
      instead: compounding a daily arithmetic mean harvests the cross-sectional variance of 800
      names, which nobody can capture without free daily trading. It reported +356%.
    * **Chained at the strategy's rebalance frequency, over the point-in-time eligible universe.**
      What an equal-weight book actually does, with the same names available on the same days. This
      is the only version that answers "did the strategy beat holding its own universe".
    """
    cal = _calendar(con, start, end)
    bounds = cal[::rebalance]
    if bounds[-1] != cal[-1]:
        bounds.append(cal[-1])

    growth, periods, names = 1.0, 0, 0
    for d0, d1 in zip(bounds, bounds[1:]):
        row = con.execute("""
            WITH universe AS (
                SELECT DISTINCT f.lineage FROM features f
                WHERE f.business_date = (SELECT MAX(business_date) FROM features
                                         WHERE business_date <= ?)
                  AND f.adv20 >= ? AND (? IS FALSE OR f.lineage IN (SELECT UNNEST(?)))
            ), opened AS (
                SELECT p.lineage, p.close_adj AS px
                FROM adjusted_prices p JOIN universe USING (lineage)
                WHERE p.business_date = ? AND p.close_adj > 0
            ), closed AS (
                -- The last price inside the period, so a name that stops trading is carried at
                -- where it stopped rather than dropped from the control.
                SELECT p.lineage, LAST(p.close_adj ORDER BY p.business_date) AS px
                FROM adjusted_prices p JOIN universe USING (lineage)
                WHERE p.business_date > ? AND p.business_date <= ? AND p.close_adj > 0
                GROUP BY p.lineage
            )
            SELECT AVG(c.px / o.px - 1), COUNT(*)
            FROM opened o JOIN closed c USING (lineage)
        """, [d0, min_adv, eligible is not None,
              sorted(eligible) if eligible else [], d0, d0, d1]).fetchone()
        if row and row[0] is not None:
            growth *= 1 + row[0]
            periods += 1
            names = max(names, row[1])

    def level(d):
        r = con.execute("""SELECT close_level FROM index_levels
                           WHERE index_name = ? AND business_date <= ? AND close_level > 0
                           ORDER BY business_date DESC LIMIT 1""",
                        [benchmark, d]).fetchone()
        return r[0] if r else None

    lo, hi = level(cal[0]), level(cal[-1])
    return {
        "universe_return": (growth - 1) if periods else None,
        "universe_names": names,
        "universe_periods": periods,
        "universe_rebalance": rebalance,
        "benchmark": benchmark,
        "benchmark_return": (hi / lo - 1) if lo and hi else None,
        "note": f"equal-weight return of the point-in-time eligible universe, rebalanced every "
                f"{rebalance} sessions like the strategy itself, and the index. A return that "
                f"does not beat the first is selection, not skill.",
    }


def _rebalance(con, *, session: date, signal_session: date, target: list[str],
               positions: dict, cash: float, capital_inr: float, rt: dict,
               buckets: dict, participation: float, simulate_fills: bool,
               prices: _PriceBook, band: float = REBALANCE_BAND
               ) -> tuple[float, list[Trade]]:
    """Exit what left the book, trim what is overweight, then buy with the proceeds.

    Sells first, deliberately: buying before the sells have settled would spend cash the book does
    not have, which is leverage introduced by ordering rather than by intent.

    **The trim leg is not optional, and its absence was a bug that invalidated every paper run.**
    The first version sold only the names that had left the target and, for names that stayed,
    bought up to the equal weight but never sold down past it. So a winner was never trimmed: the
    book was equal-weight buy-and-hold-with-additions, drifting toward whatever had run up, and the
    drift compounded with every period. It was caught by asking the engine to hold its *entire*
    eligible universe, which makes the book identical to the equal-weight control by construction,
    so its excess must be zero - and it came back at **-140.27% over eleven years**. That gap was
    the missing trim, not a strategy, and it had been sitting underneath every excess this engine
    reported.
    """
    trades: list[Trade] = []
    want = set(target)

    for lineage in [k for k in list(positions) if k not in want]:
        p = positions[lineage]
        t = _fill(con, session=session, lineage=lineage, side=sim.SELL, shares=p.shares,
                  rt=rt, buckets=buckets, participation=participation,
                  simulate_fills=simulate_fills, prices=prices,
                  decision_price=prices.close(lineage, signal_session))
        if t is None:
            continue
        cash += t.notional - t.cost_inr
        p.shares -= t.shares
        p.cost_basis = p.avg_price * p.shares
        if p.shares <= 0:
            del positions[lineage]
        trades.append(t)

    # Equal weight over the target, sized on equity **after** the exits so the book cannot spend
    # money it is still waiting for.
    held_value = 0.0
    for lineage, p in positions.items():
        px = prices.close(lineage, session) or p.avg_price
        held_value += p.shares * px
    investable = (cash + held_value) * (1 - CASH_BUFFER)
    per_name = investable / len(target) if target else 0.0

    # Trim the overweights before buying, for two reasons: it is what equal weight means, and the
    # proceeds are what pays for the underweights. Without this leg the buys are funded only by
    # exits and the book cannot converge on its own target.
    for lineage in [k for k in list(positions) if k in want]:
        p = positions[lineage]
        px = prices.close(lineage, session)
        if not px:
            continue
        excess = p.shares * px - per_name
        if excess <= max(px, per_name * band):
            continue
        shares = min(p.shares, int(excess // px))
        if shares <= 0:
            continue
        t = _fill(con, session=session, lineage=lineage, side=sim.SELL, shares=shares,
                  rt=rt, buckets=buckets, participation=participation,
                  simulate_fills=simulate_fills, prices=prices,
                  decision_price=prices.close(lineage, signal_session))
        if t is None:
            continue
        cash += t.notional - t.cost_inr
        p.shares -= t.shares
        p.cost_basis = p.avg_price * p.shares
        if p.shares <= 0:
            del positions[lineage]
        trades.append(t)

    for lineage in target:
        px = prices.close(lineage, session)
        if not px:
            continue
        have = positions.get(lineage)
        current = (have.shares * px) if have else 0.0
        gap = per_name - current
        # The same band on the buy side, so the book has one no-trade region rather than a tight
        # threshold going up and a loose one coming down.
        if gap <= max(px, per_name * band) and current > 0:
            continue
        if gap <= px:
            continue
        shares = int(min(gap, cash) // px)
        if shares <= 0:
            continue
        t = _fill(con, session=session, lineage=lineage, side=sim.BUY, shares=shares,
                  rt=rt, buckets=buckets, participation=participation,
                  simulate_fills=simulate_fills, prices=prices,
                  decision_price=prices.close(lineage, signal_session))
        if t is None:
            continue
        cash -= t.notional + t.cost_inr
        p = positions.setdefault(lineage, Position(lineage=lineage))
        p.shares += t.shares
        p.cost_basis += t.notional
        trades.append(t)
    return cash, trades


def _fill(con, *, session: date, lineage: str, side: str, shares: int, rt: dict,
          buckets: dict, participation: float, simulate_fills: bool,
          decision_price: float | None = None,
          prices: _PriceBook | None = None) -> Trade | None:
    """Simulated through real volume where the bars exist; at the close otherwise, and labelled.

    ``decision_price`` is the price **visible when the signal was computed** - the previous
    session's close. The first version passed the fill session's own close, which measures
    the intraday drift between the open and that close rather than any slippage: it reported
    a mean shortfall of -31.67 bps, a large apparent *gain*, on 480 fills. Implementation
    shortfall is achieved-against-decision, and the decision was made yesterday.
    """
    if shares <= 0:
        return None
    close = (prices.close(lineage, session) if prices is not None
             else _close(con, lineage, session))
    decision = decision_price if decision_price is not None else close
    symbol = _symbol(con, lineage) if simulate_fills else None
    bucket = buckets.get(lineage, "micro")
    half = rt.get(bucket, rt["micro"]) / 2      # one leg

    if symbol:
        f = sim.execute(con, symbol=symbol, session=session, side=side,
                        target_shares=shares,
                        decision_price=decision or 0.0, start_minute=0,
                        participation=participation)
        if f.filled_shares > 0 and f.achieved:
            notional = f.filled_shares * f.achieved
            return Trade(session=session, lineage=lineage, side=side,
                         shares=f.filled_shares, price=f.achieved, notional=notional,
                         cost_inr=notional * half, fill_kind=SIMULATED_FILL,
                         shortfall_bps=f.shortfall_bps, minutes=f.minutes,
                         intended_shares=shares)
        if not close:
            return None
    if not close:
        return None
    notional = shares * close
    return Trade(session=session, lineage=lineage, side=side, shares=shares, price=close,
                 notional=notional, cost_inr=notional * half,
                 fill_kind=CLOSE_FILL, intended_shares=shares)
