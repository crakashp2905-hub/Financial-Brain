"""The prospective protocol: nominate on what has been seen, confirm once on what has not.

The first version of the loss miner re-split all the sessions every night: yesterday's confirmation sessions quietly became
tomorrow's discovery sessions, a pattern could be "failed" one night and "confirmed" the next on nearly the same data, and every
night was another look at the same numbers. That is not an error rate anyone can state. This replaces it with a protocol whose
error rate can be stated, and is measured (``calibrate``).

**What a lesson claims.** "ORB trades taken against the market are worse than the other ORB trades the same day, and lose."
Both halves matter. *Worse than the rest of the same day* is what makes it a cause and not a bystander: when one real effect
exists, every cell that overlaps it inherits a loss, and without this a single cause produces dozens of redundant "lessons"
(the first calibration run confirmed 29 from one planted effect). *And loses* (net below zero, by at least ``min_effect_bp``)
is what makes skipping it worth anything.

1. **Nominate.** At each look (every ``look_every`` sessions from the ``min_days``-th, so the number of times the same data is examined is
   bounded and counted, not one per night) every cell (strategy x dimension x value) with enough trades is examined on all sessions so far:
   per-day contrast against the strategy's other trades that day, one-sided Student t over days, Benjamini-Hochberg across the
   cells of its family (``q_nominate``), p <= ``p_nominate``, own net <= ``-min_effect_bp``. Candidates that overlap a stronger
   nominee by more than ``max_overlap`` of their trades are dropped as redundant. A nomination freezes the cell's definition and
   its discovery window (every session up to ``D0``).
2. **Confirm, once, on fresh sessions.** Only sessions after ``D0`` count. When ``confirm_sessions`` have passed and the cell has
   ``confirm_trades`` trades in them, it is decided exactly once: CONFIRMED if the new contrast is negative with
   p <= ``alpha_confirm / k`` (k = nominations at the same look) and the cell's own net is negative; otherwise REFUTED. The sample
   size that triggers the decision depends on how many trades arrived, not on how they came out, so the p-value means what it
   says. A cell that never collects the trades is EXPIRED after ``expire_sessions``.
3. **Terminal.** Confirmed, refuted and expired are final. A refuted cell may be nominated again only from data after its
   decision, so the same numbers are never tested twice.
4. **Decay.** A standing lesson keeps being watched: if the last ``decay_window`` sessions no longer show the loss it is marked
   DECAYING. Nothing is retired by machine.

Everything is one observation per day (trades on a day share one market), equal-weighted across days. A cell whose value
is constant for the whole session (weekday, regime, VIX) cannot be compared with the rest of its own day, so it is compared with the
*other days* instead: Welch's one-sided t on the day means of its days against the strategy's other days. The cell-by-day matrix
holds cumulative sums, so one night's work is a few array operations whatever the history length. Nothing here touches a
database: ``step`` takes the matrix and the folded lessons and returns events.
"""
from __future__ import annotations

import bisect
import hashlib
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from scipy import stats

from . import features as F

END_OF_TIME = "9999-12-31"


@dataclass(frozen=True)
class Params:
    min_days: int = 12
    min_trades: int = 30
    p_nominate: float = 0.01
    q_nominate: float = 0.10
    min_effect_bp: float = 10.0
    max_overlap: float = 0.5
    look_every: int = 5                  # nominate only at fixed looks (session 12, 17, 22, ...), not every night
    confirm_sessions: int = 20
    confirm_trades: int = 30
    alpha_confirm: float = 0.05
    expire_sessions: int = 60
    decay_window: int = 30
    decay_min_days: int = 10
    decay_p: float = 0.80


DEFAULT = Params()


def _p_less(mean: np.ndarray, sd: np.ndarray, n: np.ndarray) -> np.ndarray:
    """One-sided p that the mean is below zero. No spread with a negative mean is p = 0; too few days is p = 1."""
    with np.errstate(divide="ignore", invalid="ignore"):
        t = mean / (sd / np.sqrt(n))
    p = stats.t.cdf(t, np.maximum(n - 1, 1))
    flat = (sd == 0) | np.isnan(sd)
    p = np.where(flat, np.where(mean < 0, 0.0, 1.0), p)
    return np.where(n < 3, 1.0, p)


def bh_qvalues(p: np.ndarray) -> np.ndarray:
    m = len(p)
    if m == 0:
        return p
    order = np.argsort(p)
    q = np.empty(m)
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, p[i] * m / rank)
        q[i] = prev
    return q


def lesson_id(cell: str, d0: str) -> str:
    return "L-" + hashlib.sha1(f"{cell}@{d0}".encode()).hexdigest()[:8]


# ------------------------------------------------------------------------------------------------ the matrix

def _cum(a: np.ndarray) -> np.ndarray:
    return np.concatenate([np.zeros((a.shape[0], 1)), np.cumsum(a, axis=1)], axis=1)


class CellMatrix:
    """Per (cell, session): own mean net bp, contrast against the strategy's other trades that day, trades and Rs - with
    cumulative sums, so statistics over any run of sessions are differences of two columns."""

    def __init__(self, trades: pd.DataFrame, sessions: list[str]):
        self.sessions = list(sessions)
        self.pos = {s: i for i, s in enumerate(self.sessions)}
        day_tot = trades.groupby(["strategy", "day"]).agg(S=("net_bp", "sum"), N=("net_bp", "size")).reset_index()
        parts = []
        for dim in F.DIMENSIONS:
            if dim not in trades:
                continue
            g = trades.dropna(subset=[dim]).groupby(["strategy", dim, "day"]).agg(
                n=("net_bp", "size"), s=("net_bp", "sum"), pnl=("net_pnl", "sum")).reset_index().rename(columns={dim: "value"})
            g["dimension"] = dim
            parts.append(g)
        long = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
            columns=["strategy", "value", "day", "n", "s", "pnl", "dimension"])
        long = long.merge(day_tot, on=["strategy", "day"], how="left")
        long["mean"] = long["s"] / long["n"]
        rest_n = long["N"] - long["n"]
        long["contrast"] = np.where(rest_n > 0, long["mean"] - (long["S"] - long["s"]) / rest_n.where(rest_n > 0, 1), np.nan)
        long["cell"] = long["strategy"] + "|" + long["dimension"] + "|" + long["value"].astype(str)
        self.cells = sorted(long["cell"].unique())
        self.row = {c: i for i, c in enumerate(self.cells)}
        meta = long.drop_duplicates("cell").set_index("cell").loc[self.cells] if len(long) else pd.DataFrame()
        self.strategy = meta["strategy"].to_numpy() if len(meta) else np.array([])
        self.dimension = meta["dimension"].to_numpy() if len(meta) else np.array([])
        self.value = meta["value"].to_numpy() if len(meta) else np.array([])
        self.family = np.array([F.DIMENSIONS[d].family for d in self.dimension])
        self.is_day = np.array([F.DIMENSIONS[d].level == "day" for d in self.dimension], dtype=bool)
        strategies = sorted(set(self.strategy)) if len(self.strategy) else []
        self.strat_row = np.array([strategies.index(x) for x in self.strategy], dtype=int) if len(self.strategy) else np.array([], dtype=int)
        shape = (len(self.cells), len(self.sessions))
        M, K, N, PNL = (np.full(shape, np.nan), np.full(shape, np.nan), np.zeros(shape), np.zeros(shape))
        if len(long):
            r = long["cell"].map(self.row).to_numpy()
            c = long["day"].map(self.pos).to_numpy()
            ok = ~pd.isna(c)
            r, c = r[ok], c[ok].astype(int)
            M[r, c], K[r, c] = long["mean"].to_numpy()[ok], long["contrast"].to_numpy()[ok]
            N[r, c], PNL[r, c] = long["n"].to_numpy()[ok], long["pnl"].to_numpy()[ok]
        # each strategy's own day means over all its trades: the "other days" for a day-level cell are these minus the cell's days
        A = np.full((len(strategies), len(self.sessions)), np.nan)
        for r in day_tot.itertuples():
            if r.strategy in strategies and r.day in self.pos:
                A[strategies.index(r.strategy), self.pos[r.day]] = r.S / r.N
        self.c_ad, self.c_as, self.c_ass = _cum(~np.isnan(A)), _cum(np.nan_to_num(A)), _cum(np.nan_to_num(A) ** 2)
        self.c_md, self.c_ms, self.c_mss = _cum(~np.isnan(M)), _cum(np.nan_to_num(M)), _cum(np.nan_to_num(M) ** 2)
        self.c_kd, self.c_ks, self.c_kss = _cum(~np.isnan(K)), _cum(np.nan_to_num(K)), _cum(np.nan_to_num(K) ** 2)
        self.c_n, self.c_pnl = _cum(N), _cum(PNL)

    def lo_after(self, session: str | None) -> int:
        """Column index such that sessions strictly after ``session`` are the columns above it (-1: from the start)."""
        return -1 if session is None else bisect.bisect_right(self.sessions, session) - 1

    def stats_arrays(self, hi: int, lo: int = -1, rows: np.ndarray | None = None) -> dict[str, np.ndarray]:
        """Per cell over sessions ``lo+1 .. hi``, as arrays: days, trades, own mean, contrast mean/sd/days, p, Rs."""
        sel = slice(None) if rows is None else rows

        def d(c):
            return c[sel, hi + 1] - c[sel, lo + 1]

        md, ms, kd, ks, kss = d(self.c_md), d(self.c_ms), d(self.c_kd), d(self.c_ks), d(self.c_kss)
        with np.errstate(divide="ignore", invalid="ignore"):
            mean = np.where(md > 0, ms / md, np.nan)
            kmean = np.where(kd > 0, ks / kd, np.nan)
            var = np.where(kd > 1, (kss - ks * ks / kd) / (kd - 1), np.nan)
        ksd = np.sqrt(np.maximum(var, 0))
        p = _p_less(kmean, ksd, kd.astype(float))
        day = self.is_day[sel]
        if day.any():                                    # a day-level cell: its days against the strategy's other days (Welch)
            sr = self.strat_row[sel]
            ad = (self.c_ad[:, hi + 1] - self.c_ad[:, lo + 1])[sr]
            a_s = (self.c_as[:, hi + 1] - self.c_as[:, lo + 1])[sr]
            ass = (self.c_ass[:, hi + 1] - self.c_ass[:, lo + 1])[sr]
            mss = d(self.c_mss)
            n_o, n_r = md, ad - md
            with np.errstate(divide="ignore", invalid="ignore"):
                m_o, m_r = ms / n_o, (a_s - ms) / n_r
                v_o = np.where(n_o > 1, (mss - ms * ms / n_o) / (n_o - 1), np.nan)
                v_r = np.where(n_r > 1, ((ass - mss) - (a_s - ms) ** 2 / n_r) / (n_r - 1), np.nan)
                se2 = v_o / n_o + v_r / n_r
                tstat = (m_o - m_r) / np.sqrt(se2)
                dfw = se2 ** 2 / ((v_o / n_o) ** 2 / (n_o - 1) + (v_r / n_r) ** 2 / (n_r - 1))
            pw = np.where((n_o >= 3) & (n_r >= 3) & np.isfinite(tstat), stats.t.cdf(tstat, np.maximum(np.nan_to_num(dfw, nan=1.0), 1.0)), 1.0)
            flat = ((v_o == 0) & (v_r == 0)) & (n_o >= 3) & (n_r >= 3)
            pw = np.where(flat, np.where(m_o < m_r, 0.0, 1.0), pw)
            kmean = np.where(day, m_o - m_r, kmean)
            ksd = np.where(day, np.sqrt(np.maximum(np.nan_to_num(v_o), 0)), ksd)
            kd = np.where(day, np.where(n_r >= 3, n_o, 0), kd)
            p = np.where(day, pw, p)
        return {"days": md.astype(int), "trades": d(self.c_n).astype(int), "mean": mean, "contrast": kmean, "csd": ksd,
                "cdays": np.asarray(kd).astype(int), "pnl": d(self.c_pnl), "p": p}

    def stats(self, hi: int, lo: int = -1, rows: np.ndarray | None = None) -> pd.DataFrame:
        """``stats_arrays`` as a frame with the cell's identity (used for the few cells decided or watched each night)."""
        a = self.stats_arrays(hi, lo, rows)
        sel = slice(None) if rows is None else rows
        return pd.DataFrame({"cell": np.array(self.cells)[sel], "strategy": self.strategy[sel],
                             "dimension": self.dimension[sel], "value": self.value[sel], "days": a["days"],
                             "trades": a["trades"], "mean_bp": a["mean"], "contrast": a["contrast"],
                             "contrast_sd": a["csd"], "contrast_days": a["cdays"], "pnl": a["pnl"], "p": a["p"]})


# ------------------------------------------------------------------------------------------------ nominate

def _overlap_pruned(hit: pd.DataFrame, trades: pd.DataFrame, session: str, max_overlap: float) -> pd.DataFrame:
    """Keep the strongest candidates; drop one whose trades mostly belong to an already kept, stronger one."""
    kept, kept_masks = [], []
    t = trades[trades["day"] <= session]
    for r in hit.sort_values("p").itertuples():
        m = ((t["strategy"] == r.strategy) & (t[r.dimension] == r.value)).to_numpy()
        small = m.sum()
        if any(small and (m & k).sum() / min(small, k.sum()) > max_overlap for k in kept_masks):
            continue
        kept.append(r.Index)
        kept_masks.append(m)
    return hit.loc[kept]


def screen(cm: CellMatrix, trades: pd.DataFrame, session: str, *, blocked: dict[str, str], params: Params = DEFAULT) -> list[dict]:
    """Nominations at ``session``. ``blocked``: cell -> the session after which its data may be used again; ``END_OF_TIME``
    keeps a cell with a lesson in force or in flight from being nominated twice."""
    if not cm.cells:
        return []
    hi = cm.pos[session]
    A = cm.stats_arrays(hi)
    off = np.zeros(len(cm.cells), dtype=bool)
    for cell, after in blocked.items():
        i = cm.row.get(cell)
        if i is None:
            continue
        if after >= session:
            off[i] = True
        else:                                            # a decided cell is re-examined on data after its decision only
            fresh = cm.stats_arrays(hi, cm.lo_after(after), np.array([i]))
            for key in A:
                A[key][i] = fresh[key][0]
    eligible = ~off & (A["cdays"] >= params.min_days) & (A["trades"] >= params.min_trades)
    out: list[dict] = []
    for fam in F.FAMILIES:
        idx = np.flatnonzero(eligible & (cm.family == fam))
        if len(idx) == 0:
            continue
        q = bh_qvalues(A["p"][idx])
        ok = (A["mean"][idx] <= -params.min_effect_bp) & (A["p"][idx] <= params.p_nominate) & (q <= params.q_nominate)
        if not ok.any():
            continue
        rows = idx[ok]
        hit = pd.DataFrame({"row": rows, "strategy": cm.strategy[rows], "dimension": cm.dimension[rows],
                            "value": cm.value[rows], "p": A["p"][rows], "q": q[ok]}, index=rows)
        for r in _overlap_pruned(hit, trades, session, params.max_overlap).itertuples():
            i = r.row
            out.append({"cell": cm.cells[i], "strategy": r.strategy, "dimension": r.dimension, "value": r.value,
                        "family": fam, "family_size": int(len(idx)), "d0": session, "days": int(A["days"][i]),
                        "trades": int(A["trades"][i]), "mean_bp": float(A["mean"][i]),
                        "contrast_bp": float(A["contrast"][i]), "contrast_sd": float(A["csd"][i]), "p": float(r.p),
                        "q": float(r.q), "net_inr": float(A["pnl"][i])})
    k = len(out)
    for n in out:
        n["batch_size"] = k
        n["id"] = lesson_id(n["cell"], n["d0"])
        n["params"] = asdict(params)
    return out


# ------------------------------------------------------------------------------------------------ confirm and watch

def decide(cm: CellMatrix, nomination: dict, session: str, params: Params = DEFAULT) -> dict | None:
    """The one decision on an open hypothesis, or None if it is not ready yet."""
    d0 = nomination["d0"]
    hi = cm.pos[session]
    new = hi - cm.pos.get(d0, -1)                        # sessions after the nomination
    if new <= 0 or nomination["cell"] not in cm.row:
        return None
    r = cm.stats(hi, cm.lo_after(d0), np.array([cm.row[nomination["cell"]]])).iloc[0]
    if not (new >= params.confirm_sessions and r["trades"] >= params.confirm_trades):
        if new >= params.expire_sessions:
            return {"kind": "EXPIRED", "session": session, "sessions_after": int(new), "days": int(r["days"]),
                    "trades": int(r["trades"]),
                    "why": f"{int(r['trades'])} trades in {new} sessions after nomination; {params.confirm_trades} needed"}
        return None
    alpha = params.alpha_confirm / max(1, int(nomination.get("batch_size", 1)))
    ok = bool(r["mean_bp"] < 0 and r["contrast"] < 0 and r["p"] <= alpha)
    return {"kind": "CONFIRMED" if ok else "REFUTED", "session": session, "sessions_after": int(new), "days": int(r["days"]),
            "trades": int(r["trades"]), "mean_bp": float(r["mean_bp"]), "contrast_bp": float(r["contrast"]),
            "contrast_sd": float(r["contrast_sd"]), "p": float(r["p"]), "alpha": alpha, "net_inr": float(r["pnl"])}


def decay(cm: CellMatrix, cell: str, session: str, params: Params = DEFAULT) -> dict | None:
    """DECAYING when the last ``decay_window`` sessions no longer show the loss: not credibly worse than the rest any more."""
    hi = cm.pos[session]
    lo = hi - params.decay_window
    if lo < -1 or cell not in cm.row:
        return None
    r = cm.stats(hi, lo, np.array([cm.row[cell]])).iloc[0]
    if r["contrast_days"] < params.decay_min_days:
        return None
    if r["mean_bp"] >= 0 or r["contrast"] >= 0 or r["p"] >= params.decay_p:
        return {"session": session, "days": int(r["days"]), "trades": int(r["trades"]), "mean_bp": float(r["mean_bp"]),
                "contrast_bp": float(r["contrast"]), "p": float(r["p"]), "window": [cm.sessions[lo + 1], session]}
    return None


def watchlist(cm: CellMatrix, session: str, n: int = 8, *, min_days: int = 8, min_trades: int = 20) -> pd.DataFrame:
    """The cells closest to being nominated: the strongest evidence of being worse than the rest and losing, whether or not it clears
    the bar. The loop shows these so that "nothing nominated" can be seen to mean "nothing yet", not "nothing looked at"."""
    if not cm.cells:
        return pd.DataFrame()
    a = cm.stats_arrays(cm.pos[session])
    ok = (a["cdays"] >= min_days) & (a["trades"] >= min_trades) & (a["mean"] < 0) & (a["contrast"] < 0)
    idx = np.flatnonzero(ok)
    if len(idx) == 0:
        return pd.DataFrame()
    order = idx[np.argsort(a["p"][idx])][:n]
    return pd.DataFrame({"cell": np.array(cm.cells)[order], "days": a["days"][order], "trades": a["trades"][order],
                         "mean_bp": a["mean"][order], "contrast_bp": a["contrast"][order], "p": a["p"][order],
                         "net_inr": a["pnl"][order]})


IN_FORCE = ("NOMINATED", "CONFIRMED", "REGISTERED", "SHADOW", "APPROVED", "ACTIVE")
STANDING = ("CONFIRMED", "REGISTERED", "SHADOW", "APPROVED", "ACTIVE")


def step(cm: CellMatrix, trades: pd.DataFrame, session: str, lessons: dict[str, dict], params: Params = DEFAULT) -> list[dict]:
    """One night at ``session``: events to append, each ``{lesson_id, kind, session, payload}``. Re-running a night is harmless:
    the store keys events by (lesson, kind, session)."""
    events: list[dict] = []
    blocked: dict[str, str] = {}
    for x in lessons.values():
        cell = x.get("cell")
        if not cell:
            continue
        if x["stage"] in IN_FORCE:
            blocked[cell] = END_OF_TIME                  # a lesson is in force or in flight: never nominate it again
        elif x["stage"] in ("REFUTED", "EXPIRED", "RETIRED") and blocked.get(cell) != END_OF_TIME:
            last = x["decision"]["session"] if x.get("decision") else session
            blocked[cell] = max(blocked.get(cell, ""), last)   # re-examined only on data after the decision
    for lid, x in lessons.items():
        if x["stage"] == "NOMINATED":
            d = decide(cm, x["nomination"], session, params)
            if d:
                events.append({"lesson_id": lid, "kind": d["kind"], "session": d["session"], "payload": d})
        elif x["stage"] in STANDING and x.get("cell"):
            d = decay(cm, x["cell"], session, params)
            if d:
                events.append({"lesson_id": lid, "kind": "DECAYING", "session": d["session"], "payload": d})
    if (cm.pos[session] + 1 - params.min_days) % max(1, params.look_every) == 0:     # a look: only now may a cell be nominated
        for n in screen(cm, trades, session, blocked=blocked, params=params):
            events.append({"lesson_id": n["id"], "kind": "NOMINATED", "session": session, "payload": n})
    return events
