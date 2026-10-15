"""The combination agent (``PRE-REGISTERED combination agent search``).

It works through combinations of the screener's chart, candle and indicator triggers with market and name contexts,
on both sides and three horizons, and keeps a complete record of everything it tried - the record is what makes the
search honest, because its size is the multiple-testing penalty the result has to pay.

    load(rcon)            one read of the research database (then closed): bars, membership, Nifty, delivery share
    prepare(data)         triggers (51) and contexts (10) per name per session, and forward excess returns
    prior(...)            the System One model's typed choice of side and horizon per (trigger, context), from each
                          primitive's definition only - never a test result (the results span the validation period)
    search(...)           every combination the prior allows (or the full grid) on the search sample (2016-2022)
    validate(...)         the locked phase: refuses unless an ADR-0004 trial is available

Results go to ``data/knowledge.duckdb`` (tables agent_runs, agent_prior, agent_combos), beside the evidence cards.
"""
from __future__ import annotations

import json
import time
import warnings
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

KB_PATH = Path("data") / "knowledge.duckdb"
SEARCH_END = pd.Timestamp("2022-12-31")
VALID_START, VALID_END = pd.Timestamp("2023-01-01"), pd.Timestamp("2026-10-08")
HORIZONS = (1, 5, 20)
COST = 0.0020
DEDUPE = 5
MIN_EVENTS, MIN_MONTHS, SEARCH_T, KEEP = 60, 24, 3.0, 10
CONTEXTS = {
    "none": "no extra condition",
    "above_ma200": "the stock closes above its 200-session average",
    "below_ma200": "the stock closes below its 200-session average",
    "nifty_up": "the Nifty 50 closes above its 200-session average",
    "nifty_down": "the Nifty 50 closes below its 200-session average",
    "high_vol": "the stock's 60-session volatility is in the top third of F&O stocks that day",
    "low_vol": "the stock's 60-session volatility is in the bottom third of F&O stocks that day",
    "volume_surge": "that day's volume is at least twice its 20-session average",
    "volume_dry": "that day's volume is at most half its 20-session average",
    "delivery_rising": "the stock's delivery share over the last 20 sessions is above the 120 before",
}
CHOICES = ["long for 1 session", "long for 5 sessions", "long for 20 sessions", "short for 1 session",
           "short for 5 sessions", "short for 20 sessions", "no mechanism - do not trade"]
CHOICE_MAP = {CHOICES[0]: (1, 1), CHOICES[1]: (1, 5), CHOICES[2]: (1, 20), CHOICES[3]: (-1, 1), CHOICES[4]: (-1, 5),
              CHOICES[5]: (-1, 20)}
SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (run_id VARCHAR PRIMARY KEY, started VARCHAR, mode VARCHAR, model VARCHAR,
    family INTEGER, selected INTEGER, note VARCHAR);
CREATE TABLE IF NOT EXISTS agent_prior (trigger VARCHAR, context VARCHAR, model VARCHAR, choice VARCHAR,
    confidence DOUBLE, PRIMARY KEY (trigger, context, model));
CREATE TABLE IF NOT EXISTS agent_combos (run_id VARCHAR, trigger VARCHAR, context VARCHAR, side INTEGER, horizon INTEGER,
    events INTEGER, months INTEGER, mean DOUBLE, t DOUBLE, selected BOOLEAN, valid_t DOUBLE, valid_events INTEGER);
"""


# ------------------------------------------------------------------ data

def load(rcon) -> dict:
    """Everything the search needs, in memory; the caller closes the connection straight after."""
    from ..evaluation import batch_three as B3
    from ..evaluation import delivery_share as DS
    from ..features import technical as TE
    basis = pd.read_parquet("data/basis_contracts.parquet")
    pan = B3.panels(rcon, basis)
    syms = list(pan["C"].columns)
    rcon.execute(TE.BARS_SQL)
    lins = [pan["t2l"][s] for s in syms]
    bars = rcon.execute("""SELECT lineage, d, o, h, l, c, v FROM _tbars WHERE lineage IN (SELECT UNNEST(?))
                           ORDER BY lineage, d""", [lins]).df()
    bars["d"] = pd.to_datetime(bars["d"])
    D = DS.delivery_panel(rcon, {s: pan["t2l"][s] for s in syms}, pan["C"].index)
    l2s = {v: k for k, v in pan["t2l"].items()}
    return {"bars": {l2s[lin]: g.reset_index(drop=True) for lin, g in bars.groupby("lineage")},
            "C": pan["C"], "M": pan["M"], "N": pan["N"], "D": D}


def prepare(data: dict) -> dict:
    """Per name: trigger flags (51) and contexts; and forward excess returns over the equal-weighted F&O names."""
    from ..features import library as LB
    from ..paper import signals as SG
    from ..evaluation import delivery_share as DS
    C, M, N = data["C"], data["M"], data["N"]
    idx = C.index
    vol60 = C.pct_change(fill_method=None).rolling(60, min_periods=50).std().where(M)
    vrank = vol60.rank(axis=1, pct=True)
    nifty_up = N > N.rolling(200, min_periods=200).mean()
    dscore = DS.score(data["D"])
    rows = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for sym, b in data["bars"].items():
            if len(b) < 260 or sym not in C.columns:
                continue
            f = LB.compute(b[["d", "o", "h", "l", "c", "v"]])
            sf = SG.signal_frame(f)
            sf.index = b["d"]
            trig = {}
            for k in sf.columns:
                side = SG.EVENTS[k][1] if k in SG.EVENTS else "both"
                if side in ("buy", "watch", "both"):
                    trig[f"{k}:up"] = sf[k].to_numpy() > 0
                if side in ("sell", "both"):
                    trig[f"{k}:down"] = sf[k].to_numpy() < 0
            c = b.set_index("d")["c"]
            v = b.set_index("d")["v"]
            sma200 = c.rolling(200, min_periods=200).mean()
            vr = v / v.rolling(20, min_periods=20).mean().shift(1)
            dd = b["d"]
            ctx = {"none": np.ones(len(b), bool), "above_ma200": (c > sma200).to_numpy(),
                   "below_ma200": (c < sma200).to_numpy(),
                   "nifty_up": nifty_up.reindex(dd).fillna(False).to_numpy(),
                   "nifty_down": (~nifty_up.reindex(dd).fillna(True)).to_numpy(),
                   "high_vol": (vrank[sym].reindex(dd) >= 2 / 3).fillna(False).to_numpy(),
                   "low_vol": (vrank[sym].reindex(dd) <= 1 / 3).fillna(False).to_numpy(),
                   "volume_surge": (vr >= 2).fillna(False).to_numpy(), "volume_dry": (vr <= 0.5).fillna(False).to_numpy(),
                   "delivery_rising": (dscore[sym].reindex(dd) > 0).fillna(False).to_numpy() if sym in dscore
                   else np.zeros(len(b), bool)}
            live = M[sym].reindex(dd).fillna(False).to_numpy()
            T = pd.DataFrame(trig, index=dd).loc[live]
            X = pd.DataFrame(ctx, index=dd).loc[live]
            hit = T.to_numpy().any(axis=1)
            if not hit.any():
                continue
            Th, Xh = T[hit], X[hit]
            for j, (d, trow) in enumerate(Th.iterrows()):
                for tname in trow.index[trow.to_numpy()]:
                    rows.append((sym, d, tname, Xh.iloc[j].to_numpy().copy()))
    ev = pd.DataFrame([(s, d, t) for s, d, t, _ in rows], columns=["sym", "d", "trigger"])
    ctxm = pd.DataFrame(np.vstack([x for *_, x in rows]) if rows else np.zeros((0, len(CONTEXTS))),
                        columns=list(CONTEXTS))
    ev = pd.concat([ev, ctxm.astype(bool)], axis=1)
    fwd = {}
    for h in HORIZONS:
        R = C.shift(-(1 + h)) / C.shift(-1) - 1
        EW = R.where(M).mean(axis=1)
        X = R.sub(EW, axis=0)
        pos_d = idx.get_indexer(ev["d"])
        pos_s = C.columns.get_indexer(ev["sym"])
        fwd[h] = X.to_numpy()[pos_d, pos_s]
    for h in HORIZONS:
        ev[f"x{h}"] = fwd[h]
    ev["pos"] = idx.get_indexer(ev["d"])
    return {"events": ev, "triggers": sorted(ev["trigger"].unique()), "index": idx}


# ------------------------------------------------------------------ the System One prior

def _describe(trigger: str) -> str:
    from ..paper import signals as SG
    name, direction = trigger.rsplit(":", 1)
    label = SG.label(name)
    if name in SG.EVENTS:
        claim = {"buy": "a chart trader reads it as bullish", "sell": "a chart trader reads it as bearish",
                 "watch": "a chart trader reads it as indecision or a coming move, no direction"}[SG.EVENTS[name][1]]
    else:
        claim = f"this is the indicator turning {'up' if direction == 'up' else 'down'}"
    return f"{label} ({claim})"


def prior(triggers: list[str], *, model: str = "qwen2.5:7b", kb: Path = KB_PATH, on_progress=None) -> pd.DataFrame:
    """The model's typed choice per (trigger, context). Cached in ``agent_prior``; the context is definitions only."""
    from ..llm import system1
    k = duckdb.connect(str(kb))
    k.execute(SCHEMA)
    have = {(t, c) for t, c in k.execute("SELECT trigger, context FROM agent_prior WHERE model = ?", [model]).fetchall()}
    instruction = ("You are designing a trading rule for Indian F&O stocks traded as stock futures, hedged against the "
                   "average F&O stock. Given a chart signal and a market condition, choose the side and holding period "
                   "that the signal's economic mechanism implies, or say there is no mechanism. Decide from what the "
                   "signal means, not from any remembered backtest.")
    n = 0
    for t in triggers:
        for cname, ctext in CONTEXTS.items():
            if (t, cname) in have:
                continue
            state = f"Signal: {_describe(t)}. Condition: {ctext}."
            dcs = system1.ollama_decide(model, instruction, state, CHOICES)
            k.execute("INSERT OR REPLACE INTO agent_prior VALUES (?,?,?,?,?)", [t, cname, model, dcs.label, dcs.confidence])
            n += 1
            if on_progress and n % 25 == 0:
                on_progress(n)
    df = k.execute("SELECT trigger, context, choice, confidence FROM agent_prior WHERE model = ?", [model]).df()
    k.close()
    return df


# ------------------------------------------------------------------ search and (locked) validation

def _dedupe(e: pd.DataFrame) -> pd.DataFrame:
    keep, last = [], {}
    for i, s, p in zip(e.index, e["sym"], e["pos"]):
        if p - last.get(s, -10**9) >= DEDUPE:
            keep.append(i)
            last[s] = p
    return e.loc[keep]


def _stats(e: pd.DataFrame, side: int, h: int) -> dict:
    x = side * e[f"x{h}"] - COST
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return {"events": 0, "months": 0, "mean": np.nan, "t": np.nan}
    m = x.groupby(e.loc[x.index, "d"].dt.to_period("M")).mean()
    t = float(m.mean() / (m.std(ddof=1) / np.sqrt(len(m)))) if len(m) > 2 and m.std(ddof=1) > 0 else np.nan
    return {"events": int(len(x)), "months": int(len(m)), "mean": float(m.mean()), "t": t}


def combos_from_prior(pr: pd.DataFrame | None, triggers: list[str]) -> list[tuple]:
    if pr is None:
        return [(t, c, s, h) for t in triggers for c in CONTEXTS for s in (1, -1) for h in HORIZONS]
    out = []
    for r in pr.itertuples():
        if r.choice in CHOICE_MAP and r.trigger in triggers:
            s, h = CHOICE_MAP[r.choice]
            out.append((r.trigger, r.context, s, h))
    return out


def search(prep: dict, combos: list[tuple], *, run_id: str, mode: str, model: str | None, kb: Path = KB_PATH) -> dict:
    ev = prep["events"]
    S = ev[ev["d"] <= SEARCH_END]
    rows = []
    groups = {}
    for t, c, s, h in combos:
        key = (t, c)
        if key not in groups:
            g = S[(S["trigger"] == t) & S[c]].sort_values(["sym", "d"])
            groups[key] = _dedupe(g)
        st = _stats(groups[key], s, h)
        rows.append({"run_id": run_id, "trigger": t, "context": c, "side": s, "horizon": h, **st})
    R = pd.DataFrame(rows)
    ok = (R["events"] >= MIN_EVENTS) & (R["months"] >= MIN_MONTHS) & (R["t"] >= SEARCH_T)
    sel = R[ok].sort_values("t", ascending=False).drop_duplicates(["trigger", "context"]).head(KEEP)
    R["selected"] = R.index.isin(sel.index)
    R["valid_t"], R["valid_events"] = np.nan, np.nan
    k = duckdb.connect(str(kb))
    k.execute(SCHEMA)
    k.register("_r", R)
    k.execute("""INSERT INTO agent_combos SELECT run_id, trigger, context, side, horizon, events, months, mean, t,
                 selected, valid_t, valid_events FROM _r""")
    k.execute("INSERT OR REPLACE INTO agent_runs VALUES (?,?,?,?,?,?,?)",
              [run_id, datetime.now(timezone.utc).isoformat(), mode, model or "", len(R), int(R["selected"].sum()),
               "search phase only; validation locked"])
    k.close()
    return {"run_id": run_id, "family": len(R), "selected": sel[["trigger", "context", "side", "horizon", "events",
                                                                 "months", "mean", "t"]].to_dict("records"),
            "noise_expectation": f"max t of {len(R)} independent null tests ~ {np.sqrt(2 * np.log(max(len(R), 2))):.1f}"}


def validate(*args, **kwargs):
    """The locked phase. It needs an ADR-0004 trial: the 2023-2026 data the selected combinations have not been
    fitted on is spent once, under a registration, or it is not evidence."""
    from ..evaluation import standard as S
    b = S.check_budget(date.today())
    if not b["ok"]:
        raise PermissionError(f"validation is locked: no ADR-0004 trial available in {b['quarter']} "
                              f"({b['used']} of {b['budget']} used). It runs when 2027Q1 opens or the owner amends.")
    raise NotImplementedError("validation runs as a registered trial; record it in the ledger first")


def run(rcon_factory, *, model: str = "qwen2.5:7b", use_prior: bool = True, on_progress=print) -> dict:
    t0 = time.time()
    rcon = rcon_factory()
    try:
        data = load(rcon)
    finally:
        rcon.close()
    on_progress(f"data loaded ({len(data['bars'])} names) in {time.time() - t0:.0f}s; research database released")
    prep = prepare(data)
    on_progress(f"{len(prep['events'])} trigger events, {len(prep['triggers'])} triggers [{time.time() - t0:.0f}s]")
    pr, mode = None, "full grid"
    if use_prior:
        from ..events.tone import ollama_busy
        busy = ollama_busy({model})
        if busy:
            on_progress(f"System One skipped: {busy} - searching the full grid instead")
        else:
            pr = prior(prep["triggers"], model=model, on_progress=lambda n: on_progress(f"prior: {n} decisions"))
            mode = f"System One prior ({model})"
    combos = combos_from_prior(pr, prep["triggers"])
    run_id = "agent_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    res = search(prep, combos, run_id=run_id, mode=mode, model=model if pr is not None else None)
    res["mode"] = mode
    res["seconds"] = round(time.time() - t0)
    Path("data/edges").mkdir(parents=True, exist_ok=True)
    Path(f"data/edges/{run_id}.json").write_text(json.dumps(res, default=str, indent=1), encoding="utf-8")
    return res
