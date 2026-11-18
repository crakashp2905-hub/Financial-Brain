"""Skip rules, from registration to the verdict that earns them a place - judged as a counterfactual on the unchanged live-v1 trades.

A confirmed lesson is a request for a decision, not a rule. When the owner pre-registers a skip rule for it and adds it to
``docs/shadow/skip-rules.v2.json``, this module is what happens next. v2 is v1 minus the trades the rule would have skipped, so each
night the rule is asked of every closed live trade from ``active_from`` onward: *would you have skipped this one?* No second runner,
no second Kite poll, live-v1 untouched.

Guards, in code:

* A rule is **refused** unless the pre-registration note it names exists and the git audit says it was written before any result.
* It is judged only on trades from ``active_from``: never on the data it was found on.
* The verdict needs ``MIN_SHADOW_DAYS`` sessions and ``MIN_SKIPPED`` skipped trades; until then the status is COLLECTING.
* PASSING needs the skipped trades to lose (negative net, t <= ``T_PASS`` across days) *and* v2 to beat v1. It means "worth the
  owner's decision" - activation is a separate, human act (``governance``), and an expired or invalid approval silently returns a
  rule to shadow.

Stated approximation: skipping a trade can free exposure for one v1 could not take (the engine stops opening trades at ``MAX_GROSS``
times the account); that trade is not in the ledger, so v2 cannot credit it. The report counts the sessions on which the cap was within
10% of binding. The real v2 cohort (``activation``) has no such gap, and ``ab`` compares it with v1.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import features as F

RULES_FILE = Path("docs") / "shadow" / "skip-rules.v2.json"
SCHEMA_ID = "fb.shadow.skip_rules.v2"
MIN_SHADOW_DAYS = 60
MIN_SKIPPED = 30
T_PASS = -2.0
CAP_NEAR = 0.9
REGISTERED_OK = ("OK", "NO_RESULT_YET", "AMENDED_BEFORE_RESULT")
STRATEGIES = ("ORB", "GAP")
STATUSES = ("SHADOW", "ACTIVE", "RETIRED")


class ShadowError(ValueError):
    pass


@dataclass(frozen=True)
class SkipRule:
    id: str
    strategy: str
    dimension: str
    value: str
    prereg: str
    active_from: str
    lesson: str | None = None
    status: str = "SHADOW"
    approval: dict | None = None
    problem: str | None = None                          # why a rule declared ACTIVE is running as SHADOW

    @property
    def pattern(self) -> str:
        return f"{self.dimension} = {self.value}"

    @property
    def cell(self) -> str:
        return F.cell_id(self.strategy, self.dimension, self.value)

    @property
    def lesson_id(self) -> str:
        return self.lesson or self.id

    @property
    def effective_status(self) -> str:
        return "SHADOW" if self.problem else self.status


def scope_definition(x) -> dict:
    """What an approval binds to: the rule as written, not its status or approval."""
    g = (lambda k: getattr(x, k)) if not isinstance(x, dict) else (lambda k: x.get(k))
    return {"id": g("id"), "lesson": g("lesson"), "strategy": g("strategy"), "dimension": g("dimension"),
            "value": g("value"), "prereg": g("prereg"), "active_from": g("active_from")}


def load_rules(path: Path = RULES_FILE, *, repo: Path = Path("."), audit=None, now: datetime | None = None) -> list[SkipRule]:
    """Read and validate the rule file. Raises ``ShadowError`` for anything that is not a registered, well-formed rule. A rule
    marked ACTIVE whose approval is missing, expired or no longer matches its definition is returned as SHADOW with ``problem``."""
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
    from . import governance as G
    now = now or datetime.now(timezone.utc)
    rules, seen = [], set()
    for x in raw:
        try:
            r = SkipRule(id=x["id"], strategy=x["strategy"], dimension=x["dimension"], value=x["value"], prereg=x["prereg"],
                         active_from=x["active_from"], lesson=x.get("lesson"), status=x.get("status", "SHADOW"),
                         approval=x.get("approval"))
        except KeyError as e:
            raise ShadowError(f"rule {x.get('id', '?')}: missing {e}") from None
        if r.id in seen:
            raise ShadowError(f"duplicate rule id {r.id}")
        seen.add(r.id)
        if r.strategy not in STRATEGIES:
            raise ShadowError(f"{r.id}: strategy must be one of {', '.join(STRATEGIES)}")
        if r.dimension not in F.DIMENSIONS:
            raise ShadowError(f"{r.id}: unknown dimension {r.dimension!r} (one of {', '.join(F.DIMENSIONS)})")
        if r.value not in F.DIMENSIONS[r.dimension].values:
            raise ShadowError(f"{r.id}: {r.value!r} is not a value of {r.dimension}")
        if r.status not in STATUSES:
            raise ShadowError(f"{r.id}: status must be one of {', '.join(STATUSES)}")
        try:
            date.fromisoformat(r.active_from)
        except ValueError:
            raise ShadowError(f"{r.id}: active_from must be an ISO date") from None
        st = status.get(f"{r.prereg}.md") or status.get(r.prereg)
        if st not in REGISTERED_OK:
            raise ShadowError(f"{r.id}: pre-registration {r.prereg!r} is not registered before its result "
                              f"(audit: {st or 'not found'}). A rule enters shadow only after it is written down.")
        if r.status == "ACTIVE":
            problem = G.approval_problem(r, now)
            if problem:
                r = SkipRule(**{**r.__dict__, "problem": problem})
        rules.append(r)
    return rules


# ------------------------------------------------------------------------------------------------ evaluation

def would_skip(rule: SkipRule, df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=bool)
    return ((df["strategy"] == rule.strategy) & (df[rule.dimension] == rule.value)).fillna(False)


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
    """``live``: closed live trades labelled by ``features.label`` (so every dimension column exists)."""
    today = today or date.today().isoformat()
    base = {"rule_id": rule.id, "pattern": rule.pattern, "cell": rule.cell, "strategy": rule.strategy,
            "active_from": rule.active_from, "days": 0, "skipped_trades": 0, "skipped_net_inr": 0.0, "skipped_bp": None,
            "skipped_t": None, "kept_bp": None, "v1_net_inr": 0.0, "v2_net_inr": 0.0, "cap_days": 0}
    if rule.status == "RETIRED":
        return {**base, "status": "RETIRED"}
    if today < rule.active_from:
        return {**base, "status": "SCHEDULED"}
    df = live[live["day"] >= rule.active_from] if len(live) else live
    if df is None or df.empty:
        return {**base, "status": "COLLECTING"}
    df = df[df["strategy"] == rule.strategy]
    if df.empty:
        return {**base, "status": "COLLECTING"}
    skip = would_skip(rule, df)
    sk, kept = df[skip], df[~skip]
    per_day = sk.groupby("day")["net_bp"].mean() if len(sk) else pd.Series(dtype=float)
    t = None
    if len(per_day) >= 3 and per_day.std(ddof=1) > 0:
        t = float(per_day.mean() / (per_day.std(ddof=1) / np.sqrt(len(per_day))))
    skipped_bp = float(sk["net_bp"].mean()) if len(sk) else None
    v1, skipped_net = float(df["net_pnl"].sum()), float(sk["net_pnl"].sum())
    out = {**base, "days": int(df["day"].nunique()), "skipped_trades": int(len(sk)), "skipped_net_inr": skipped_net,
           "skipped_bp": skipped_bp, "skipped_t": t, "kept_bp": float(kept["net_bp"].mean()) if len(kept) else None,
           "v1_net_inr": v1, "v2_net_inr": v1 - skipped_net, "cap_days": peak_exposure_days(df, cap_inr) if cap_inr else 0}
    if out["days"] < MIN_SHADOW_DAYS or len(sk) < MIN_SKIPPED:
        out["status"] = "COLLECTING"
    elif skipped_bp is not None and skipped_bp >= 0:
        out["status"] = "FAILING"                              # it would have removed winners
    elif t is not None and t <= T_PASS and skipped_net < 0 and out["v2_net_inr"] > v1:
        out["status"] = "PASSING"                              # worth the owner's decision, never an automatic switch
    else:
        out["status"] = "INCONCLUSIVE"
    return out


def evaluate(live: pd.DataFrame, rules: list[SkipRule], *, today: str | None = None, cap_inr: float | None = None) -> list[dict]:
    return [evaluate_rule(r, live, cap_inr=cap_inr, today=today) for r in rules]


def record(store, rules: list[SkipRule], results: list[dict], session: str) -> list[str]:
    """Write the registration and each night's verdict to the ledger. Returns the rule ids whose verdict changed."""
    changed = []
    for r, res in zip(rules, results, strict=True):
        lid = r.lesson_id
        store.append(lid, "REGISTERED", session=r.id, actor="owner",
                     payload={"rule": r.id, "prereg": r.prereg, "active_from": r.active_from, "cell": r.cell})
        if res["status"] not in ("SCHEDULED",):
            store.append(lid, "SHADOW", session=r.id, payload={"rule": r.id, "from": r.active_from})
        prev = [e for e in store.events(lid) if e["kind"] == "SHADOW_RESULT" and e["payload"].get("rule") == r.id]
        if not prev or prev[-1]["payload"].get("status") != res["status"]:
            changed.append(r.id)
        store.append(lid, "SHADOW_RESULT", session=session + ":" + r.id, payload={"rule": r.id, **res})
    return changed


def alerts(results: list[dict], changed: list[str]) -> list[dict]:
    out = []
    for res in results:
        if res["rule_id"] in changed and res["status"] in ("PASSING", "FAILING"):
            what = (f"would have saved Rs {-res['skipped_net_inr']:,.0f} over {res['days']} sessions: ready for your decision "
                    f"(fb learn approve {res['rule_id']})" if res["status"] == "PASSING"
                    else "would have removed winners: consider retiring it")
            out.append({"key": f"shadow:{res['rule_id']}:{res['status']}", "title": f"Skip rule {res['rule_id']} {res['status']}",
                        "body": f"{res['pattern']} ({res['strategy']}) {what}. Nothing was changed."})
    return out


def render(results: list[dict]) -> str:
    if not results:
        return ("Shadow: no skip rules registered (docs/shadow/skip-rules.v2.json is empty). A confirmed lesson becomes one only "
                "after the owner pre-registers it (fb learn draft <lesson>).")
    lines = [f"{'rule':8s} {'cell':44s} {'status':12s} {'days':>5s} {'skipped':>8s} {'skip bp':>8s} {'t':>6s} {'v1 Rs':>11s} {'v2 Rs':>11s}"]
    for r in results:
        sb = f"{r['skipped_bp']:+8.1f}" if r["skipped_bp"] is not None else "       -"
        st = f"{r['skipped_t']:+6.2f}" if r["skipped_t"] is not None else "     -"
        lines.append(f"{r['rule_id']:8s} {r['cell']:44s} {r['status']:12s} {r['days']:5d} {r['skipped_trades']:8d} {sb} {st} "
                     f"{r['v1_net_inr']:11,.0f} {r['v2_net_inr']:11,.0f}")
        if r.get("cap_days"):
            lines.append(f"         exposure cap within 10% on {r['cap_days']} session(s): v2 cannot credit trades freed by a skip")
    lines.append("v2 = v1 minus the skipped trades; live-v1 is unchanged. PASSING is a request for a decision, not a switch.")
    return "\n".join(lines)
