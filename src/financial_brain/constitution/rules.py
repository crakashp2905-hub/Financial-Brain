"""Investment Constitution (C14): the owner's rules, enforced in code.

ARCHITECTURE.md §8: the constitution is built from the investor's own material. This
module is the machinery, not the content. The owner writes ``data/constitution.toml``
(private, never committed); until then ``docs/constitution.example.toml`` applies,
and every rule it holds is a conservative placeholder marked as such.

Only machine-checkable rules live here. Each is evaluated against a decision *as of its
world state* - using data known then, never later - and a violation names its rule and
the facts behind it. Principles that need judgement (valuation, chart reading) belong in
the prose of the constitution for agents to retrieve, not in this file.

Rules checked:

``position.max_weight``                  sizing["weight"] must not exceed it
``liquidity.min_adv20_inr``              20-session average traded value (features)
``exclusions.isins``                     never buy these
``governance.max_pledge_filings``        promoter pledge filings in the look-back
``governance.auditor_resignation_days``  no buy within N days of an auditor resigning
``governance.exclude_insolvency``        no buy in a company under CIRP
``regime.no_new_buys_in``                no BUY/ADD while the market is in these regimes
``process.min_horizon_days``             decisions shorter than this are trading, not
                                         investing
"""
from __future__ import annotations

import json
import tomllib
from datetime import date, datetime, timedelta
from pathlib import Path

EXAMPLE = Path(__file__).resolve().parents[3] / "docs" / "constitution.example.toml"
BUYS = {"BUY", "ADD"}


def load(data_root: Path | None = None) -> dict:
    """The owner's constitution if written, else the example (marked as such)."""
    own = data_root / "constitution.toml" if data_root else None
    path = own if own and own.exists() else EXAMPLE
    rules = tomllib.loads(path.read_text(encoding="utf-8"))
    rules["_source"] = str(path)
    rules["_is_example"] = path == EXAMPLE
    return rules


def _world(con, version: str) -> tuple[date, datetime, str | None]:
    row = con.execute("SELECT business_date, content FROM world_states WHERE version_id = ?",
                      [version]).fetchone()
    if not row:
        raise ValueError(f"world state {version} does not exist")
    c = json.loads(row[1])
    return (row[0], datetime.fromisoformat(str(c["as_of"])),
            (c.get("market") or {}).get("regime"))


def check(con, decision: dict, rules: dict) -> list[dict]:
    """Every violated rule, each with the facts that violate it. Empty means pass."""
    d, as_of, regime = _world(con, decision["world_state_version"])
    isin, action = decision["isin"], decision["action"]
    out: list[dict] = []

    def fail(rule, fact):
        out.append({"rule": rule, "fact": fact})

    pos, liq = rules.get("position", {}), rules.get("liquidity", {})
    gov, reg = rules.get("governance", {}), rules.get("regime", {})
    proc, exc = rules.get("process", {}), rules.get("exclusions", {})

    w = (decision.get("sizing") or {}).get("weight")
    if w is not None and "max_weight" in pos and w > pos["max_weight"]:
        fail("position.max_weight", f"weight {w:.1%} > {pos['max_weight']:.1%}")
    if "min_horizon_days" in proc and decision["horizon_days"] < proc["min_horizon_days"]:
        fail("process.min_horizon_days",
             f"horizon {decision['horizon_days']}d < {proc['min_horizon_days']}d")
    if action not in BUYS:
        return out                               # the rest guard new money only

    if isin in set(exc.get("isins", [])):
        fail("exclusions.isins", f"{isin} is excluded")
    if regime and regime in set(reg.get("no_new_buys_in", [])):
        fail("regime.no_new_buys_in", f"market regime on {d} is {regime}")
    if "min_adv20_inr" in liq:
        r = con.execute("""SELECT f.adv20 FROM features f JOIN security_lineage l
                           ON l.lineage = f.lineage WHERE l.isin = ? AND f.business_date <= ?
                           ORDER BY f.business_date DESC LIMIT 1""", [isin, d]).fetchone() \
            if _has(con, "features") and _has(con, "security_lineage") else None
        if r is None or r[0] is None:
            fail("liquidity.min_adv20_inr", "no 20-session traded value known - unproven "
                 "liquidity fails closed")
        elif r[0] < liq["min_adv20_inr"]:
            fail("liquidity.min_adv20_inr",
                 f"ADV20 Rs {r[0] / 1e7:.2f} cr < Rs {liq['min_adv20_inr'] / 1e7:.2f} cr")
    if "max_pledge_filings" in gov and _has(con, "holder_filings"):
        days = gov.get("pledge_lookback_days", 365)
        n = con.execute("""SELECT COUNT(*) FROM holder_filings WHERE isin = ?
                           AND relation = 'PLEDGE' AND business_date > ?
                           AND business_date <= ?""",
                        [isin, d - timedelta(days=days), d]).fetchone()[0]
        if n > gov["max_pledge_filings"]:
            fail("governance.max_pledge_filings",
                 f"{n} promoter pledge filings in {days}d > {gov['max_pledge_filings']}")
    if "auditor_resignation_days" in gov:
        r = con.execute("""SELECT MAX(business_date) FROM announcements WHERE isin = ?
                           AND event_type = 'AUDITOR_RESIGNATION' AND business_date > ?
                           AND published_at <= ?""",
                        [isin, d - timedelta(days=gov["auditor_resignation_days"]),
                         as_of]).fetchone()[0]
        if r:
            fail("governance.auditor_resignation_days", f"statutory auditor resigned {r}")
    if gov.get("exclude_insolvency"):
        r = con.execute("""SELECT MAX(business_date) FROM announcements WHERE isin = ?
                           AND event_type = 'INSOLVENCY' AND business_date > ?
                           AND published_at <= ?""",
                        [isin, d - timedelta(days=365), as_of]).fetchone()[0]
        if r:
            fail("governance.exclude_insolvency", f"insolvency filing {r}")
    return out


def _has(con, name: str) -> bool:
    return bool(con.execute("""SELECT 1 FROM information_schema.tables
                               WHERE table_name = ?""", [name]).fetchone())
