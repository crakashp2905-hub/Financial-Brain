"""India Implementability Gate (ARCHITECTURE.md §9) - before any backtest.

A research paper will happily propose a US long-short strategy that cannot be traded
here. The gate reads a hypothesis and answers, with reasons, whether it *could* be run
in Indian cash equities at the owner's size - before a single backtest is spent on it.

Checks:

``long_only``     shorting cash equity overnight is not available; a short leg needs
                  single-stock futures, and the F&O eligibility list is not ingested yet,
                  so a hypothesis that needs shorts FAILS (closed) rather than passing on
                  hope
``capacity``      at ``aum_inr`` with ``positions`` equal weights, each position must be
                  at most ``max_participation`` of the name's 20-session traded value -
                  for enough names in the universe to fill the book
``turnover``      the cost of the hypothesis's own rebalance frequency (round trip per
                  rebalance, Indian delivery costs incl. STT both legs and impact) must
                  leave the expected edge positive
``surveillance``  ASM / GSM / T2T lists are not ingested: reported UNCHECKED, never PASS

The gate does not judge whether the idea works - that is the firewall's job, afterwards.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..costs.india import CostModel

PASS, FAIL, UNCHECKED = "PASS", "FAIL", "UNCHECKED"


@dataclass
class Hypothesis:
    name: str
    signal: str                          # a feature from features/indicators.py
    rebalance_days: int                  # sessions between rebalances
    positions: int                       # names held
    aum_inr: float                       # capital deployed
    needs_short: bool = False
    expected_edge_per_rebalance: float | None = None   # e.g. 0.01 = 1% per period
    max_participation: float = 0.05      # of a name's ADV20 per trade
    bucket: str = "mid"                  # impact-cost liquidity bucket


def check(con, h: Hypothesis, as_of=None) -> dict:
    checks: dict[str, tuple[str, str]] = {}

    checks["long_only"] = (FAIL, "needs a short leg; F&O eligibility is not ingested, so "
                           "shorting is not assumed possible") if h.needs_short else \
        (PASS, "long-only")

    per_name = h.aum_inr / h.positions
    need_adv = per_name / h.max_participation
    date_sql = "(SELECT MAX(business_date) FROM features)" if as_of is None else "?"
    params = [need_adv] + ([] if as_of is None else [as_of])
    n_ok = con.execute(f"""SELECT COUNT(*) FROM features WHERE adv20 >= ?
                           AND business_date = {date_sql}""", params).fetchone()[0]
    checks["capacity"] = (PASS if n_ok >= 5 * h.positions else FAIL,
                          f"{n_ok} names trade >= Rs {need_adv / 1e7:.1f} cr/day (Rs "
                          f"{per_name / 1e7:.2f} cr per position at {h.max_participation:.0%}"
                          f" of ADV); need {5 * h.positions} to choose {h.positions}")

    rt = CostModel().round_trip(turnover=1_000_000, bucket=h.bucket)["bps"] / 10_000
    per_year = 250 / h.rebalance_days
    if h.expected_edge_per_rebalance is None:
        checks["turnover"] = (UNCHECKED, f"no expected edge stated; costs are {rt:.2%} per "
                              f"full rebalance, {rt * per_year:.1%} a year at full turnover")
    else:
        net = h.expected_edge_per_rebalance - rt
        checks["turnover"] = (PASS if net > 0 else FAIL,
                              f"edge {h.expected_edge_per_rebalance:.2%} - round trip "
                              f"{rt:.2%} = {net:+.2%} per rebalance ({per_year:.0f}/yr)")

    checks["surveillance"] = (UNCHECKED, "ASM/GSM/T2T lists not ingested")
    verdict = FAIL if any(v == FAIL for v, _ in checks.values()) else PASS
    return {"hypothesis": h.name, "verdict": verdict,
            "checks": {k: {"result": v, "why": w} for k, (v, w) in checks.items()}}
