"""How have this system's own calls actually done? (C25, the loop that makes it better)

A research system that never scores itself gets more articulate, not more right. Every
decision that reaches paper is marked to market at its horizon against the Nifty, net of
costs (``paper/ledger.py``); this turns those into a verdict, with the statistics stated
honestly:

* **hit rate with a confidence interval.** Eight wins from twelve is 67% and means
  almost nothing - the Wilson interval on that is roughly 39-86%, which includes "worse
  than a coin". The interval is reported next to the rate so a good run is not mistaken
  for an edge.
* **mean excess return**, net of the round-trip cost the trade was charged.
* **slices** - by action, by liquidity bucket, by the event type that prompted it -
  because an edge that exists only in illiquid names is a cost model's illusion.

Until there are enough closed trades, this says **so**, rather than printing a number
that reads like evidence. ``MIN_TRADES`` is the line; below it the verdict is "not
enough evidence yet", whatever the wins look like.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

MIN_TRADES = 20            # below this, a hit rate is noise with a decimal point
Z = 1.96                   # 95%


def wilson(wins: int, n: int, z: float = Z) -> tuple[float, float]:
    """Confidence interval for a hit rate that behaves at small n, unlike wins/n."""
    if n == 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1 + z ** 2 / n
    centre = (p + z ** 2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass
class Slice:
    name: str
    n: int
    wins: int
    mean_excess: float
    median_excess: float
    worst: float
    best: float

    @property
    def hit_rate(self) -> float:
        return self.wins / self.n if self.n else 0.0

    @property
    def interval(self) -> tuple[float, float]:
        return wilson(self.wins, self.n)

    def verdict(self) -> str:
        """What this slice is entitled to claim."""
        if self.n < MIN_TRADES:
            return f"not enough evidence yet ({self.n} of {MIN_TRADES} closed trades)"
        low, high = self.interval
        if low > 0.5:
            return f"better than a coin ({low:.0%}-{high:.0%} at 95%)"
        if high < 0.5:
            return f"worse than a coin ({low:.0%}-{high:.0%} at 95%)"
        return f"indistinguishable from chance ({low:.0%}-{high:.0%} at 95%)"


@dataclass
class Scorecard:
    overall: Slice | None
    by_action: list[Slice] = field(default_factory=list)
    by_bucket: list[Slice] = field(default_factory=list)
    open_trades: int = 0
    as_of: date | None = None

    def enough(self) -> bool:
        return bool(self.overall and self.overall.n >= MIN_TRADES)


def _slice(rows: list[tuple], name: str) -> Slice | None:
    excess = sorted(r[0] for r in rows if r[0] is not None)
    if not excess:
        return None
    n = len(excess)
    return Slice(name=name, n=n, wins=sum(1 for e in excess if e > 0),
                 mean_excess=sum(excess) / n,
                 median_excess=excess[n // 2] if n % 2 else
                 (excess[n // 2 - 1] + excess[n // 2]) / 2,
                 worst=excess[0], best=excess[-1])


def build(con, *, as_of: date | None = None, since: date | None = None) -> Scorecard:
    """Score every closed paper trade, overall and by slice."""
    where, args = "status = 'closed'", []
    if since:
        where += " AND exit_date >= ?"
        args.append(since)
    rows = con.execute(f"""SELECT excess, action, bucket, decision_id
                           FROM paper_trades WHERE {where}""", args).fetchall()
    open_n = con.execute("SELECT COUNT(*) FROM paper_trades WHERE status = 'open'"
                         ).fetchone()[0]
    card = Scorecard(overall=_slice(rows, "all closed trades"), open_trades=open_n,
                     as_of=as_of)
    for idx, attr in ((1, "by_action"), (2, "by_bucket")):
        groups: dict[str, list] = {}
        for r in rows:
            groups.setdefault(r[idx] or "unknown", []).append(r)
        sliced = [s for key, grp in sorted(groups.items()) if (s := _slice(grp, key))]
        setattr(card, attr, sorted(sliced, key=lambda s: -s.n))
    return card


def lines(card: Scorecard) -> list[str]:
    """The scorecard as text, saying plainly when it proves nothing."""
    if not card.overall:
        return [f"No closed paper trades yet ({card.open_trades} open). The system has "
                f"made no decision it can be scored on."]
    o = card.overall
    out = [f"{o.n} closed paper trades, {card.open_trades} open.",
           f"Hit rate {o.hit_rate:.0%} - {o.verdict()}.",
           f"Mean excess {o.mean_excess:+.2%} per trade, median {o.median_excess:+.2%}, "
           f"worst {o.worst:+.2%}, best {o.best:+.2%} (net of costs, against the Nifty)."]
    if not card.enough():
        out.append("Treat every number above as provisional: the sample is too small to "
                   "separate skill from luck.")
    for group, label in ((card.by_action, "by action"), (card.by_bucket, "by liquidity")):
        shown = [s for s in group if s.n >= 5]
        if shown:
            out.append(f"  {label}: " + "; ".join(
                f"{s.name} {s.n} trades, {s.hit_rate:.0%} hit, {s.mean_excess:+.1%}"
                for s in shown))
    return out
