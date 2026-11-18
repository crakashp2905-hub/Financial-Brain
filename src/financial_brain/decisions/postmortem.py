"""What was knowable when a trade went wrong, and what rule follows (C25).

The ask is "never repeat this mistake". The tempting answer is reinforcement learning;
with a few dozen trades that would fit noise and then trade on it, which is the failure
this system exists to prevent. What works at this sample size is duller and checkable:

1. **Postmortem** every closed trade - not only losses, because a rule learned from
   losses alone will also forbid the wins that share their features.
2. **Describe the entry in features that were knowable then**: market regime, what kind
   of filing prompted it, whether that filing was unusual for the company, how liquid the
   name was, how confident the committee's own stances were.
3. **Count.** A feature is a candidate lesson only when enough trades share it and the
   difference is material. Below that it is a story about two trades.
4. **Control the number of looks.** Five features and a handful of values each means a dozen groups examined;
   one or two will look bad by chance. A lesson must also survive Benjamini-Hochberg false-discovery control
   across every group examined (one-sided Welch test of the group against the rest, q = ``FDR_Q``).
5. **Gate.** A confirmed lesson becomes a pre-trade check with its support attached, so a
   blocked decision says "4 of 5 BUYs in RISK_OFF lost, mean -6%" rather than "blocked".

Every lesson carries the trades it came from, so it can be argued with, and is re-derived
from the record rather than accumulated in a model nobody can inspect.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

MIN_SUPPORT = 5            # trades sharing a feature before it may be called a lesson
MATERIAL = 0.02            # 2% mean excess difference; below this it is noise
FDR_Q = 0.10               # false-discovery rate across every group examined


@dataclass
class Lesson:
    feature: str           # e.g. "regime=RISK_OFF"
    n: int
    losses: int
    mean_excess: float
    rest_mean: float       # mean excess of trades without this feature
    decisions: list[str] = field(default_factory=list)
    p: float = 1.0         # one-sided Welch p that the group did worse than the rest
    q: float | None = None  # the same after Benjamini-Hochberg across every group examined (None: not computed)

    @property
    def gap(self) -> float:
        return self.mean_excess - self.rest_mean

    def confirmed(self) -> bool:
        """Enough cases, a difference worth acting on, and one that survives the number of groups looked at."""
        return (self.n >= MIN_SUPPORT and self.gap <= -MATERIAL
                and (self.q is None or self.q <= FDR_Q))

    def describe(self) -> str:
        tail = f" (q {self.q:.2f})" if self.q is not None else ""
        return (f"{self.feature}: {self.losses} of {self.n} lost, mean excess "
                f"{self.mean_excess:+.1%} against {self.rest_mean:+.1%} elsewhere{tail}")


def welch_p_worse(have: list[float], rest: list[float]) -> float:
    """One-sided Welch p that ``have`` has a lower mean than ``rest``. 1.0 when either side has fewer than two
    observations; when both sides have no spread, 0.0 if ``have`` is lower and 1.0 otherwise."""
    import math

    from scipy import stats
    n1, n2 = len(have), len(rest)
    if n1 < 2 or n2 < 2:
        return 1.0
    m1, m2 = sum(have) / n1, sum(rest) / n2
    v1 = sum((x - m1) ** 2 for x in have) / (n1 - 1)
    v2 = sum((x - m2) ** 2 for x in rest) / (n2 - 1)
    se2 = v1 / n1 + v2 / n2
    if se2 <= 0:
        return 0.0 if m1 < m2 else 1.0
    df = se2 ** 2 / ((v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1))
    return float(stats.t.cdf((m1 - m2) / math.sqrt(se2), df))


def bh_adjust(p: list[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values (q-values), in the input order."""
    m = len(p)
    order = sorted(range(m), key=lambda i: p[i])
    q = [1.0] * m
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, p[i] * m / rank)
        q[i] = prev
    return q


def features_at_entry(con, decision_id: str) -> dict:
    """What the record says was knowable when this trade was opened."""
    row = con.execute("""SELECT p.isin, p.entry_date, p.bucket, p.excess, d.content
                         FROM paper_trades p JOIN decisions d USING (decision_id)
                         WHERE p.decision_id = ?""", [decision_id]).fetchone()
    if not row:
        return {}
    isin, entry, bucket, excess, content = row
    c = json.loads(content)
    out = {"decision_id": decision_id, "isin": isin, "entry_date": entry,
           "excess": excess, "action": c.get("action"), "bucket": bucket or "unknown"}

    # ORDER BY version too: the table holds every version at once and v1/v2 disagree on
    # 425 sessions, so ordering by date alone picks one at random.
    regime = con.execute("""SELECT regime FROM market_regime WHERE business_date <= ?
                            ORDER BY business_date DESC, version DESC
                            LIMIT 1""", [entry]).fetchone()
    out["regime"] = regime[0] if regime else "unknown"

    event = con.execute("""SELECT event_type FROM announcements
                           WHERE isin = ? AND business_date <= ? AND materiality = 'high'
                           ORDER BY business_date DESC LIMIT 1""",
                        [isin, entry]).fetchone()
    out["prompted_by"] = event[0] if event else "unknown"

    fired = con.execute("""SELECT COUNT(*) FROM decision_alerts
                           WHERE decision_id = ?""", [decision_id]).fetchone()[0]
    out["invalidation_fired"] = fired > 0
    out["typed_checks"] = len(c.get("invalidation_checks") or [])
    return out


def record_all(con) -> dict:
    """Postmortem every closed trade that does not have one yet."""
    todo = [r[0] for r in con.execute("""
        SELECT p.decision_id FROM paper_trades p
        LEFT JOIN decision_postmortems m ON m.decision_id = p.decision_id
        WHERE p.status = 'closed' AND m.decision_id IS NULL""").fetchall()]
    now = datetime.now(timezone.utc)
    for did in todo:
        f = features_at_entry(con, did)
        if not f:
            continue
        con.execute("""INSERT INTO decision_postmortems (decision_id, excess, regime,
                       prompted_by, bucket, action, invalidation_fired, features,
                       created_at) VALUES (?,?,?,?,?,?,?,?,?)
                       ON CONFLICT (decision_id) DO NOTHING""",
                    [did, f["excess"], f["regime"], f["prompted_by"], f["bucket"],
                     f["action"], f["invalidation_fired"], json.dumps(f, default=str),
                     now])
    return {"written": len(todo)}


def lessons(con) -> list[Lesson]:
    """Candidate rules, with the trades behind each. Unconfirmed ones are returned too -
    seeing what nearly qualified matters as much as the rules that did."""
    rows = con.execute("""SELECT decision_id, excess, regime, prompted_by, bucket, action,
                          invalidation_fired FROM decision_postmortems
                          WHERE excess IS NOT NULL""").fetchall()
    if not rows:
        return []
    total = [r[1] for r in rows]
    out: list[Lesson] = []
    for idx, name in ((2, "regime"), (3, "prompted_by"), (4, "liquidity"),
                      (5, "action"), (6, "invalidation_fired")):
        for value in sorted({r[idx] for r in rows}):
            have = [r for r in rows if r[idx] == value]
            rest = [r[1] for r in rows if r[idx] != value]
            if not have:
                continue
            mean = sum(r[1] for r in have) / len(have)
            rest_mean = (sum(rest) / len(rest)) if rest else (sum(total) / len(total))
            out.append(Lesson(feature=f"{name}={value}", n=len(have),
                              losses=sum(1 for r in have if r[1] <= 0), mean_excess=mean,
                              rest_mean=rest_mean,
                              decisions=[r[0] for r in have],
                              p=welch_p_worse([r[1] for r in have], rest)))
    for lesson, q in zip(out, bh_adjust([x.p for x in out]), strict=True):
        lesson.q = q                                     # across every group examined, not one at a time
    return sorted(out, key=lambda x: x.gap)


def gate(con, *, regime: str | None = None, prompted_by: str | None = None,
         bucket: str | None = None) -> list[Lesson]:
    """Confirmed lessons that apply to a proposed trade. Empty means nothing learned yet
    forbids it - which is the usual and honest answer."""
    want = {f"regime={regime}", f"prompted_by={prompted_by}", f"liquidity={bucket}"}
    return [x for x in lessons(con) if x.confirmed() and x.feature in want]
