"""The live-v2 shadow: skip rules that run beside the unchanged live-v1 rules and are judged only on sessions after
they were registered.

A confirmed losing situation (``loss_miner``) is a request for a decision, not a rule. The owner decides whether to
pre-register a skip rule for it; this module is what happens next. It holds the rules in a versioned file
(``docs/shadow/skip-rules.v2.json``, empty until the owner adds one) and, each night, asks of every closed live trade
"would the rule have skipped this one?". v2 is v1 minus the skipped trades, so the answer needs no second runner and
no second Kite poll, and live-v1 is not touched in any way. The report per rule is the forward, out-of-sample
verdict: did the trades the rule would have skipped lose, on sessions it never saw?

Guards, in code:

* A rule is **refused** unless the pre-registration note it names exists and the git audit says it was written before
  any result (``research/preregistry``). Rules come from a hypothesis that survived confirmation, but nothing enters
  shadow on a hunch.
* Only trades from ``active_from`` onward are evaluated: a rule is never judged on the data it was found on.
* The verdict needs ``MIN_SHADOW_DAYS`` sessions *and* a skipped group of at least ``MIN_SKIPPED`` trades; below that
  the status is SHADOW (collecting). Even PASSING only means "worth the owner's decision", never "apply it".

Stated approximation: skipping a trade can free exposure for one v1 could not take (the engine stops opening trades at
``MAX_GROSS`` x the account). Those trades are not in the ledger, so they are not credited to v2. The report counts the
sessions on which the cap was within 10% of binding, so the size of the omission is visible.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from . import loss_review as LR

RULES_FILE = Path("docs") / "shadow" / "skip-rules.v2.json"
SCHEMA_ID = "fb.shadow.skip_rules.v1"
MIN_SHADOW_DAYS = 60
MIN_SKIPPED = 30
T_PASS = -2.0
CAP_NEAR = 0.9
REGISTERED_OK = ("OK", "NO_RESULT_YET", "AMENDED_BEFORE_RESULT")

TABLES = """
CREATE TABLE IF NOT EXISTS shadow_results (
    run_on VARCHAR, rule_id VARCHAR, days INTEGER, skipped_trades INTEGER, skipped_net_inr DOUBLE, skipped_bp DOUBLE,
    skipped_t DOUBLE, kept_bp DOUBLE, v1_net_inr DOUBLE, v2_net_inr DOUBLE, cap_days INTEGER, status VARCHAR,
    PRIMARY KEY (run_on, rule_id));
CREATE TABLE IF NOT EXISTS shadow_decisions (
    rule_id VARCHAR, trade_id VARCHAR, mode VARCHAR, day VARCHAR, strategy VARCHAR, symbol VARCHAR, net_pnl DOUBLE,
    would_skip BOOLEAN, PRIMARY KEY (rule_id, trade_id, mode));
"""


class ShadowError(ValueError):
    pass


@dataclass(frozen=True)
class SkipRule:
    id: str
    dimension: str
    value: str
    strategy: str | None
    prereg: str
    active_from: str
    note: str = ""

    @property
    def pattern(self) -> str:
        return f"{self.dimension} = {self.value}"


def load_rules(path: Path = RULES_FILE, *, repo: Path = Path("."), audit=None) -> list[SkipRule]:
    """Read and validate the rule file. Raises ``ShadowError`` for anything that is not a registered, well-formed rule."""
    if not path.exists():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != SCHEMA_ID:
        raise ShadowError(f"{path}: schema must be {SCHEMA_ID}")
    raw = doc.get("rules") or []
    if not raw:
        return []
    if audit is None:
        from ..research import preregistry as PR
        audit = PR.audit(repo)
    status = {r["prereg"]: r["status"] for r in audit}
    rules, seen = [], set()
    for x in raw:
        try:
            r = SkipRule(id=x["id"], dimension=x["dimension"], value=x["value"], strategy=x.get("strategy"),
                         prereg=x["prereg"], active_from=x["active_from"], note=x.get("note", ""))
        except KeyError as e:
            raise ShadowError(f"rule {x.get('id', '?')}: missing {e}") from None
        if r.id in seen:
            raise ShadowError(f"duplicate rule id {r.id}")
        seen.add(r.id)
        if r.dimension not in LR.DIMENSIONS:
            raise ShadowError(f"{r.id}: unknown dimension {r.dimension!r} (one of {', '.join(LR.DIMENSIONS)})")
        try:
            date.fromisoformat(r.active_from)
        except ValueError:
            raise ShadowError(f"{r.id}: active_from must be an ISO date") from None
        st = status.get(f"{r.prereg}.md") or status.get(r.prereg)
        if st not in REGISTERED_OK:
            raise ShadowError(f"{r.id}: pre-registration {r.prereg!r} is not registered before its result "
                              f"(audit: {st or 'not found'}). A rule enters shadow only after it is written down.")
        rules.append(r)
    return rules


def _closed_live(con) -> pd.DataFrame:
    df = con.execute("""SELECT trade_id, mode, day, strategy, symbol, side, qty, entry_ts, exit_ts,
                               entry_px * qty AS notional, net_pnl, exit_reason, mkt_ret, daily_up, gap, or_width,
                               entry_minute FROM trades WHERE exit_ts IS NOT NULL AND mode = 'live'""").df()
    return LR.label(df) if not df.empty else df


def would_skip(rule: SkipRule, df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=bool)
    m = df[rule.dimension].astype(str) == rule.value
    if rule.strategy:
        m &= df["strategy"] == rule.strategy
    return m.fillna(False)


def peak_exposure_days(df: pd.DataFrame, cap_inr: float) -> int:
    """Sessions on which open intraday exposure got within ``CAP_NEAR`` of the engine's cap."""
    n = 0
    for _day, g in df.groupby("day"):
        ev = []
        for r in g.itertuples():
            try:
                ev += [(pd.Timestamp(r.entry_ts), r.notional), (pd.Timestamp(r.exit_ts), -r.notional)]
            except (ValueError, TypeError):
                continue
        ev.sort(key=lambda e: (e[0], e[1] > 0))              # exits before entries at the same instant
        cur = peak = 0.0
        for _t, v in ev:
            cur += v
            peak = max(peak, cur)
        n += peak >= CAP_NEAR * cap_inr
    return n


def evaluate_rule(rule: SkipRule, live: pd.DataFrame, *, cap_inr: float | None = None, today: str | None = None) -> dict:
    today = today or date.today().isoformat()
    base = {"rule_id": rule.id, "pattern": rule.pattern, "active_from": rule.active_from}
    if today < rule.active_from:
        return {**base, "status": "SCHEDULED", "days": 0, "skipped_trades": 0, "skipped_net_inr": 0.0, "skipped_bp": None,
                "skipped_t": None, "kept_bp": None, "v1_net_inr": 0.0, "v2_net_inr": 0.0, "cap_days": 0}
    df = live[live["day"] >= rule.active_from] if len(live) else live
    if df is None or df.empty:
        return {**base, "status": "SHADOW", "days": 0, "skipped_trades": 0, "skipped_net_inr": 0.0, "skipped_bp": None,
                "skipped_t": None, "kept_bp": None, "v1_net_inr": 0.0, "v2_net_inr": 0.0, "cap_days": 0}
    if rule.strategy:
        df = df[df["strategy"] == rule.strategy]
    skip = would_skip(rule, df)
    sk, kept = df[skip], df[~skip]
    days = int(df["day"].nunique())
    per_day = sk.groupby("day")["net_bp"].mean() if len(sk) else pd.Series(dtype=float)
    t = None
    if len(per_day) >= 3 and per_day.std(ddof=1) > 0:
        t = float(per_day.mean() / (per_day.std(ddof=1) / np.sqrt(len(per_day))))
    skipped_bp = float(sk["net_bp"].mean()) if len(sk) else None
    v1, skipped_net = float(df["net_pnl"].sum()), float(sk["net_pnl"].sum())
    out = {**base, "days": days, "skipped_trades": int(len(sk)), "skipped_net_inr": skipped_net, "skipped_bp": skipped_bp,
           "skipped_t": t, "kept_bp": float(kept["net_bp"].mean()) if len(kept) else None, "v1_net_inr": v1,
           "v2_net_inr": v1 - skipped_net,
           "cap_days": peak_exposure_days(df, cap_inr) if cap_inr else 0}
    if days < MIN_SHADOW_DAYS or len(sk) < MIN_SKIPPED:
        out["status"] = "SHADOW"
    elif skipped_bp is not None and skipped_bp >= 0:
        out["status"] = "FAILING"                              # it would have removed winners
    elif t is not None and t <= T_PASS and skipped_net < 0:
        out["status"] = "PASSING"                              # worth the owner's decision, never an automatic switch
    else:
        out["status"] = "INCONCLUSIVE"
    return out


def evaluate(con, rules: list[SkipRule], *, today: str | None = None, cap_inr: float | None = None) -> list[dict]:
    live = _closed_live(con)
    return [evaluate_rule(r, live, cap_inr=cap_inr, today=today) for r in rules]


def persist(con, rules: list[SkipRule], results: list[dict], *, today: str | None = None) -> list[str]:
    """Store the results and the per-trade decisions. Returns rule ids whose status changed since the last run."""
    today = today or date.today().isoformat()
    con.execute(TABLES)
    live = _closed_live(con)
    changed = []
    for r, res in zip(rules, results, strict=True):
        prev = con.execute("SELECT status FROM shadow_results WHERE rule_id = ? ORDER BY run_on DESC LIMIT 1",
                           [r.id]).fetchone()
        con.execute("INSERT OR REPLACE INTO shadow_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    [today, r.id, res["days"], res["skipped_trades"], res["skipped_net_inr"], res["skipped_bp"],
                     res["skipped_t"], res["kept_bp"], res["v1_net_inr"], res["v2_net_inr"], res["cap_days"], res["status"]])
        if not prev or prev[0] != res["status"]:
            changed.append(r.id)
        if len(live):
            df = live[live["day"] >= r.active_from]
            if r.strategy:
                df = df[df["strategy"] == r.strategy]
            skip = would_skip(r, df)
            rows = [(r.id, x.trade_id, x.mode, x.day, x.strategy, x.symbol, x.net_pnl, bool(s))
                    for x, s in zip(df.itertuples(), skip, strict=True)]
            if rows:
                con.executemany("INSERT OR REPLACE INTO shadow_decisions VALUES (?,?,?,?,?,?,?,?)", rows)
    return changed


def alerts(results: list[dict], changed: list[str], day) -> list[dict]:
    out = []
    for res in results:
        if res["rule_id"] in changed and res["status"] in ("PASSING", "FAILING"):
            what = ("would have saved Rs {:,.0f} over {} sessions: ready for your decision".format(
                -res["skipped_net_inr"], res["days"]) if res["status"] == "PASSING"
                    else "would have removed winners: it should be retired")
            out.append({"key": f"shadow:{res['rule_id']}:{res['status']}", "title": f"Shadow rule {res['rule_id']} {res['status']}",
                        "body": f"{res['pattern']} {what}. Nothing was changed."})
    return out


def render(results: list[dict]) -> str:
    if not results:
        return ("Shadow: no skip rules registered (docs/shadow/skip-rules.v2.json is empty). A confirmed hypothesis from "
                "`fb loss-mine` becomes one only after the owner pre-registers it.")
    lines = [f"{'rule':9s} {'situation':40s} {'status':13s} {'days':>5s} {'skipped':>8s} {'skip bp':>8s} {'t':>6s} {'v1 Rs':>11s} {'v2 Rs':>11s}"]
    for r in results:
        sb = f"{r['skipped_bp']:+8.1f}" if r["skipped_bp"] is not None else "       -"
        st = f"{r['skipped_t']:+6.2f}" if r["skipped_t"] is not None else "     -"
        lines.append(f"{r['rule_id']:9s} {r['pattern']:40s} {r['status']:13s} {r['days']:5d} {r['skipped_trades']:8d} {sb} {st} "
                     f"{r['v1_net_inr']:11,.0f} {r['v2_net_inr']:11,.0f}")
        if r.get("cap_days"):
            lines.append(f"          exposure cap within 10% on {r['cap_days']} session(s): v2 cannot credit trades freed by a skip")
    lines.append("v2 = v1 minus the skipped trades; live-v1 is unchanged. PASSING is a request for a decision, not a switch.")
    return "\n".join(lines)
