"""The full Opportunity: one object carrying everything the brain knows about a trade, from discovery to lifecycle.

The scanners' ``Opportunity`` is the light version made for every flagged name; this is the complete record made for
the names a trader actually looks at, assembled from the modules that measure each part:

    identity        instrument, ticker, timestamp, type, direction, horizon
    context         market regime (breadth), sector regime (home sector vs the market), catalyst (recent filings)
    states          technical, fundamental (stale-labelled), event, derivative (F&O positioning)
    distribution    p10 / p50 / p90, probability of profit, expected downside, MFE / MAE from analogues
    economics       the alpha chain: expected net alpha -> risk-adjusted -> portfolio-adjusted (crowding penalty)
    implementation  liquidity, capacity, portfolio impact, position size, execution plan, invalidation
    provenance      evidence ids, experiment id, lifecycle

``alpha_chain`` is the one formula the review asked for:

    expected net alpha       = expected return - benchmark - fees - spread - impact
    risk-adjusted utility    = net alpha - own-risk penalty
    portfolio utility        = risk-adjusted utility - crowding penalty, where crowding = RISK_AVERSION x 2 x Cov(name, book) over the
                               horizon: the variance the name adds *through what is already held*
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

import numpy as np

from .net_alpha import RISK_AVERSION


def alpha_chain(net: float, sigma_h: float, cov_with_book_h: float | None,
                *, risk_penalty: float = 0.0) -> dict:
    crowd = RISK_AVERSION * 2 * max(cov_with_book_h or 0.0, 0.0)
    utility = net - risk_penalty
    return {"expected_net_alpha": net, "risk_adjusted_utility": utility,
            "alpha_per_unit_risk": net / sigma_h if sigma_h > 0 else float("nan"),
            "own_risk_penalty": risk_penalty, "crowding_penalty": crowd,
            "portfolio_utility": utility - crowd,
            "portfolio_adjusted_alpha": net - crowd}


def capacity_inr(adv_inr: float, *, participation: float = 0.10, days: int = 1) -> float:
    """Rupees that can be bought in ``days`` sessions at ``participation`` of the average daily turnover."""
    return float(adv_inr * participation * days)


@dataclass
class FullOpportunity:
    instrument: str
    ticker: str
    timestamp: str
    opportunity_type: str
    direction: str
    horizon: int
    market_regime: str | None = None
    sector_regime: dict = field(default_factory=dict)
    catalyst: list = field(default_factory=list)
    technical_state: dict = field(default_factory=dict)
    fundamental_state: dict = field(default_factory=dict)
    event_state: dict = field(default_factory=dict)
    derivative_state: dict = field(default_factory=dict)
    forecast_distribution: dict = field(default_factory=dict)
    expected_return: float | None = None
    expected_downside: float | None = None
    expected_cost: float | None = None
    alpha: dict = field(default_factory=dict)           #: the itemised net alpha
    chain: dict = field(default_factory=dict)           #: net -> risk-adjusted -> portfolio-adjusted
    probability_of_profit: float | None = None
    mfe_expectation: float | None = None
    mae_expectation: float | None = None
    invalidation: dict = field(default_factory=dict)
    liquidity: dict = field(default_factory=dict)
    capacity_inr: float | None = None
    portfolio_impact: dict = field(default_factory=dict)
    position_size: dict = field(default_factory=dict)
    execution_plan: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    lifecycle: list = field(default_factory=list)

    def to_json(self) -> str:
        def clean(v):
            if isinstance(v, float) and not np.isfinite(v):
                return None
            if isinstance(v, dict):
                return {k: clean(x) for k, x in v.items()}
            if isinstance(v, (list, tuple)):
                return [clean(x) for x in v]
            if isinstance(v, (np.floating, np.integer)):
                return clean(v.item())
            return v
        return json.dumps(clean(asdict(self)), default=str)


def sector_regime(rcon, sector_index: str | None, asof) -> dict:
    """The home sector's last 20 sessions against the Nifty 500."""
    if not sector_index:
        return {}
    rows = rcon.execute("""SELECT index_name, business_date, close_level FROM index_levels_canonical WHERE variant = 'PRICE'
                           AND index_name IN (?, 'Nifty 500') AND business_date <= ? ORDER BY business_date DESC LIMIT 60""",
                        [sector_index, asof]).fetchall()
    by = {}
    for name, _, lv in rows:
        by.setdefault(name, []).append(lv)
    if len(by.get(sector_index, [])) < 21 or len(by.get("Nifty 500", [])) < 21:
        return {"sector": sector_index}
    s = by[sector_index][0] / by[sector_index][20] - 1
    m = by["Nifty 500"][0] / by["Nifty 500"][20] - 1
    return {"sector": sector_index, "sector_20d": s, "market_20d": m, "relative_20d": s - m,
            "state": "leading" if s - m > 0.02 else "lagging" if s - m < -0.02 else "in line"}


def catalysts(rcon, isin: str | None, asof, *, days: int = 5) -> list[dict]:
    """Material filings in the last few days - headlines are company text, kept as data."""
    if not isin:
        return []
    try:
        rows = rcon.execute("""SELECT business_date, event_type, materiality, LEFT(headline, 140) FROM announcements
                               WHERE isin = ? AND business_date BETWEEN ?::DATE - INTERVAL (?) DAY AND ?
                               AND materiality IN ('HIGH', 'CRITICAL') ORDER BY business_date DESC LIMIT 5""",
                            [isin, asof, days, asof]).fetchall()
    except Exception:                                   # noqa: BLE001 - no filings table
        return []
    return [{"date": str(d), "event": e, "materiality": m, "headline": h} for d, e, m, h in rows]
