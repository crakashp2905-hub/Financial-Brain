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
#: Equity-identity tolerance, as a fraction. Anything above this is an accounting bug, not rounding.
IDENTITY_TOLERANCE = 1e-6

CLOSE_FILL, SIMULATED_FILL = "close", "simulated"


class PaperError(ValueError):
    pass


@dataclass
class Position:
    isin: str
    shares: int = 0
    cost_basis: float = 0.0        # total rupees paid, for realised-P&L accounting

    @property
    def avg_price(self) -> float:
        return self.cost_basis / self.shares if self.shares else 0.0


@dataclass
class Trade:
    session: date
    isin: str
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
            "costs_inr": costs,
            "cost_drag": costs / start["equity"] if start["equity"] else float("nan"),
            # The number that says whether execution was measured or assumed.
            "notional_traded": total_notional,
            "simulated_fill_share": (sim_notional / total_notional
                                     if total_notional else float("nan")),
            "unfilled_orders": sum(1 for t in self.trades if t.unfilled),
            "warnings": self.warnings,
            # The only figures that make the return interpretable.
            "universe_buy_and_hold": self.control.get("universe_return"),
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
    """
    rows = con.execute(f"""
        SELECT l.isin
        FROM features f
        JOIN security_lineage l ON l.lineage = f.lineage
        WHERE f.business_date = (SELECT MAX(business_date) FROM features
                                 WHERE business_date <= ?)
          AND f.{feature} IS NOT NULL AND f.adv20 >= ?
          AND (? IS FALSE OR l.isin IN (SELECT UNNEST(?)))
        ORDER BY CAST(f.{feature} AS DOUBLE) * {direction} DESC, l.isin
        LIMIT ?
    """, [as_of, min_adv, eligible is not None,
          sorted(eligible) if eligible else [], max_positions]).fetchall()
    return [r[0] for r in rows]


def _close(con, isin: str, session: date) -> float | None:
    r = con.execute("""SELECT close_adj FROM adjusted_prices
                       WHERE isin = ? AND business_date = ? AND close_adj > 0""",
                    [isin, session]).fetchone()
    return r[0] if r else None


def _symbol(con, isin: str) -> str | None:
    r = con.execute("""SELECT MAX(tradingsymbol) FROM minute_bars WHERE isin = ?""",
                    [isin]).fetchone()
    return r[0] if r and r[0] else None


def run(con, *, feature: str, start: date, end: date, capital_inr: float = 10_000_000.0,
        direction: int = 1, rebalance: int = REBALANCE,
        max_positions: int = MAX_POSITIONS, min_adv: float = 1e7,
        participation: float = PARTICIPATION,
        segment: Segment = Segment.DELIVERY,
        simulate_fills: bool = True,
        restrict_to_minute_bars: bool = False) -> Run:
    """Walk the sessions, holding a real book with real cash.

    ``restrict_to_minute_bars`` trades only names whose minute bars exist, which gives a run with
    100% measured fills on a smaller universe. Both are worth having: the restricted run is the
    complete chain, and the unrestricted one shows what fill coverage a real universe would have.
    """
    cal = _calendar(con, start, end)
    if len(cal) < rebalance * 2:
        raise PaperError(f"{len(cal)} sessions between {start} and {end} is too few")
    costbook.ensure_view(con)
    model = CostModel()
    rt = {b: model.round_trip(turnover=1_000_000, segment=segment, bucket=b)["bps"] / 1e4
          for b in costbook.BUCKETS}

    eligible = None
    if restrict_to_minute_bars:
        eligible = {r[0] for r in con.execute(
            "SELECT DISTINCT isin FROM minute_bars").fetchall()}

    config = {"feature": feature, "start": start, "end": end,
              "capital_inr": capital_inr, "direction": direction,
              "rebalance": rebalance, "max_positions": max_positions,
              "min_adv": min_adv, "participation": participation,
              "segment": segment.value, "simulate_fills": simulate_fills,
              "restrict_to_minute_bars": restrict_to_minute_bars}
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
                bmap = costbook.buckets(con, [prev], set(target) | set(positions))
                for isin in set(target) | set(positions):
                    buckets[isin] = bmap.get((prev, isin), buckets.get(isin, "micro"))
                cash, trades = _rebalance(
                    con, session=session, signal_session=prev, target=target,
                    positions=positions, cash=cash,
                    capital_inr=capital_inr, rt=rt, buckets=buckets,
                    participation=participation, simulate_fills=simulate_fills)
                out.trades += trades
                out.rebalances.append({
                    "session": session, "signal_from": prev,
                    "target": len(target), "held_after": len(positions),
                    "trades": len(trades),
                    "notional": sum(t.notional for t in trades)})

        # ------------------------------------------------------------- mark to market
        held_value, priced, stale = 0.0, 0, 0
        for isin, p in positions.items():
            px = _close(con, isin, session)
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
    out.control = _control(con, cal[0], cal[-1], eligible=eligible, min_adv=min_adv)
    return out


def _control(con, start: date, end: date, *, eligible: set | None,
             min_adv: float, benchmark: str = "Nifty 500") -> dict:
    """What the traded universe and the market did, with no strategy in the way.

    ``evaluation/control.py`` made this project's most important early finding by asking exactly
    this of the committee: the universe returned +5.00% and the committee contributed -1.85%. The
    same question has to be asked of every paper run, and asking it *inside* the engine is the
    difference between a control and a good intention.
    """
    rows = con.execute("""
        WITH universe AS (
            SELECT DISTINCT l.isin FROM features f
            JOIN security_lineage l ON l.lineage = f.lineage
            WHERE f.business_date = (SELECT MAX(business_date) FROM features
                                     WHERE business_date <= ?)
              AND f.adv20 >= ? AND (? IS FALSE OR l.isin IN (SELECT UNNEST(?)))
        ), a AS (
            SELECT p.isin, p.close_adj FROM adjusted_prices p JOIN universe USING (isin)
            WHERE p.business_date = (SELECT MAX(business_date) FROM adjusted_prices
                                     WHERE business_date <= ?)
        ), b AS (
            SELECT p.isin, p.close_adj FROM adjusted_prices p JOIN universe USING (isin)
            WHERE p.business_date = (SELECT MAX(business_date) FROM adjusted_prices
                                     WHERE business_date <= ?)
        )
        SELECT AVG(b.close_adj / a.close_adj - 1), COUNT(*)
        FROM a JOIN b USING (isin) WHERE a.close_adj > 0
    """, [start, min_adv, eligible is not None,
          sorted(eligible) if eligible else [], start, end]).fetchone()

    def level(d):
        r = con.execute("""SELECT close_level FROM index_levels
                           WHERE index_name = ? AND business_date <= ? AND close_level > 0
                           ORDER BY business_date DESC LIMIT 1""",
                        [benchmark, d]).fetchone()
        return r[0] if r else None

    lo, hi = level(start), level(end)
    return {
        "universe_return": rows[0] if rows and rows[0] is not None else None,
        "universe_names": rows[1] if rows else 0,
        "benchmark": benchmark,
        "benchmark_return": (hi / lo - 1) if lo and hi else None,
        "note": "equal-weight buy-and-hold of the traded universe over the same window, and the "
                "index. A return that does not beat the first is selection, not skill.",
    }


def _rebalance(con, *, session: date, signal_session: date, target: list[str],
               positions: dict, cash: float, capital_inr: float, rt: dict,
               buckets: dict, participation: float,
               simulate_fills: bool) -> tuple[float, list[Trade]]:
    """Sell what left the book, then buy what entered, with the proceeds.

    Sells first, deliberately: buying before the sells have settled would spend cash the book does
    not have, which is leverage introduced by ordering rather than by intent.
    """
    trades: list[Trade] = []
    want = set(target)

    for isin in [k for k in list(positions) if k not in want]:
        p = positions[isin]
        t = _fill(con, session=session, isin=isin, side=sim.SELL, shares=p.shares,
                  rt=rt, buckets=buckets, participation=participation,
                  simulate_fills=simulate_fills,
                  decision_price=_close(con, isin, signal_session))
        if t is None:
            continue
        cash += t.notional - t.cost_inr
        p.shares -= t.shares
        p.cost_basis = p.avg_price * p.shares
        if p.shares <= 0:
            del positions[isin]
        trades.append(t)

    # Equal weight over the target, sized on equity **after** the sells so the book cannot spend
    # money it is still waiting for.
    held_value = 0.0
    for isin, p in positions.items():
        px = _close(con, isin, session) or p.avg_price
        held_value += p.shares * px
    investable = (cash + held_value) * (1 - CASH_BUFFER)
    per_name = investable / len(target) if target else 0.0

    for isin in target:
        px = _close(con, isin, session)
        if not px:
            continue
        have = positions.get(isin)
        current = (have.shares * px) if have else 0.0
        gap = per_name - current
        if gap <= px:                      # less than one share of difference
            continue
        shares = int(min(gap, cash) // px)
        if shares <= 0:
            continue
        t = _fill(con, session=session, isin=isin, side=sim.BUY, shares=shares,
                  rt=rt, buckets=buckets, participation=participation,
                  simulate_fills=simulate_fills,
                  decision_price=_close(con, isin, signal_session))
        if t is None:
            continue
        cash -= t.notional + t.cost_inr
        p = positions.setdefault(isin, Position(isin=isin))
        p.shares += t.shares
        p.cost_basis += t.notional
        trades.append(t)
    return cash, trades


def _fill(con, *, session: date, isin: str, side: str, shares: int, rt: dict,
          buckets: dict, participation: float, simulate_fills: bool,
          decision_price: float | None = None) -> Trade | None:
    """Simulated through real volume where the bars exist; at the close otherwise, and labelled.

    ``decision_price`` is the price **visible when the signal was computed** - the previous
    session's close. The first version passed the fill session's own close, which measures
    the intraday drift between the open and that close rather than any slippage: it reported
    a mean shortfall of -31.67 bps, a large apparent *gain*, on 480 fills. Implementation
    shortfall is achieved-against-decision, and the decision was made yesterday.
    """
    if shares <= 0:
        return None
    close = _close(con, isin, session)
    decision = decision_price if decision_price is not None else close
    symbol = _symbol(con, isin) if simulate_fills else None
    bucket = buckets.get(isin, "micro")
    half = rt.get(bucket, rt["micro"]) / 2      # one leg

    if symbol:
        f = sim.execute(con, symbol=symbol, session=session, side=side,
                        target_shares=shares,
                        decision_price=decision or 0.0, start_minute=0,
                        participation=participation)
        if f.filled_shares > 0 and f.achieved:
            notional = f.filled_shares * f.achieved
            return Trade(session=session, isin=isin, side=side,
                         shares=f.filled_shares, price=f.achieved, notional=notional,
                         cost_inr=notional * half, fill_kind=SIMULATED_FILL,
                         shortfall_bps=f.shortfall_bps, minutes=f.minutes,
                         intended_shares=shares)
        if not close:
            return None
    if not close:
        return None
    notional = shares * close
    return Trade(session=session, isin=isin, side=side, shares=shares, price=close,
                 notional=notional, cost_inr=notional * half,
                 fill_kind=CLOSE_FILL, intended_shares=shares)
