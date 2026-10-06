"""Point-in-time fundamental observations and the ratios a trader asks for, as known on a date.

``fundamental_observation`` is a long table derived from the exchange XBRL facts (which are append-only): one row
per (filing, metric) with the period it covers, the moment the exchange disseminated it (``filed_at``), the basis
(consolidated / standalone), the source document id and a **revision** number - the second filing for the same
period and basis is revision 2, never an overwrite. ``as_known_on`` returns, for a symbol and a date, the latest
value of each metric among filings public on or before that date - so nothing filed after the date can leak in.

Metrics, from the quarterly results (``Q``), the half-yearly balance sheet (``I``) and year-to-date cash flow
(``YTD``):

    revenue, other_income, pbt, pat, eps, finance_costs, depreciation
    ebitda = pbt + finance_costs + depreciation - other_income      (operating, so other income is excluded)
    ebit   = pbt + finance_costs
    equity, debt (current + non-current borrowings), cash, assets   (half-yearly)
    cfo, capex                                                       (year to date, as filed)

Ratios at a price, from trailing-four-quarter flows and the latest balance sheet: P/E, P/B, EV/EBITDA, ROE, ROCE,
debt/equity, interest cover. What is *not* here: earnings revisions (no analyst consensus exists in the archive),
institutional ownership (not ingested); promoter holding and pledges live in the promoter graph.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

Q_METRICS = {"RevenueFromOperations": "revenue", "OtherIncome": "other_income", "ProfitBeforeTax": "pbt",
             "ProfitLossForPeriod": "pat", "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "eps",
             "FinanceCosts": "finance_costs", "DepreciationDepletionAndAmortisationExpense": "depreciation",
             "PaidUpValueOfEquityShareCapital": "paid_up", "FaceValueOfEquityShareCapital": "face_value"}
I_METRICS = {"Equity": "equity", "BorrowingsCurrent": "debt_current", "BorrowingsNoncurrent": "debt_noncurrent",
             "CashAndCashEquivalents": "cash", "Assets": "assets"}
YTD_METRICS = {"CashFlowsFromUsedInOperatingActivities": "cfo",
               "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": "capex"}
FLOWS = ("revenue", "other_income", "pbt", "pat", "eps", "finance_costs", "depreciation")


def build(con) -> int:
    """(Re)derive ``fundamental_observation`` from the append-only facts. Returns its row count."""
    maps = [(c, m, "Q") for c, m in Q_METRICS.items()] + [(c, m, "I") for c, m in I_METRICS.items()] + \
           [(c, m, "YTD") for c, m in YTD_METRICS.items()]
    con.register("metric_map", pd.DataFrame(maps, columns=["concept", "metric", "ctx"]))
    con.execute("""
        CREATE OR REPLACE TABLE fundamental_observation AS
        WITH f AS (
            SELECT fl.seq_number, fl.symbol, fl.isin, fl.period_end, fl.filed_at, fl.basis, m.metric, m.ctx, x.value
            FROM xbrl_facts x
            JOIN metric_map m ON m.concept = x.concept AND m.ctx = x.ctx
            JOIN xbrl_filings fl ON fl.seq_number = x.seq_number
            WHERE fl.status = 'stored' AND x.value IS NOT NULL)
        SELECT *, 'NSE XBRL ' || seq_number AS source,
               DENSE_RANK() OVER (PARTITION BY symbol, period_end, basis ORDER BY filed_at, seq_number) AS revision
        FROM f""")
    con.unregister("metric_map")
    return con.execute("SELECT COUNT(*) FROM fundamental_observation").fetchone()[0]


def as_known_on(con, symbol: str, day: date, *, basis: str | None = None) -> pd.DataFrame:
    """Every observation of ``symbol`` filed on or before the end of ``day``; consolidated preferred."""
    df = con.execute("""SELECT period_end, filed_at, basis, metric, ctx, value, revision, source FROM fundamental_observation
                        WHERE symbol = ? AND filed_at < (?::DATE + INTERVAL 1 DAY)""", [symbol, day]).df()
    if df.empty:
        return df
    if basis is None:
        basis = "consolidated" if (df["basis"].str.lower() == "consolidated").any() else df["basis"].iloc[0]
    df = df[df["basis"].str.lower() == basis.lower()]
    # the latest revision known on the day, per period and metric
    return df.sort_values("filed_at").groupby(["period_end", "metric"], as_index=False).last()


def snapshot(obs: pd.DataFrame) -> dict:
    """Trailing-four-quarter flows and the latest balance sheet from ``as_known_on`` output."""
    if obs.empty:
        return {}
    out = {}
    q = obs[obs["ctx"] == "Q"].pivot_table(index="period_end", columns="metric", values="value").sort_index()
    if len(q):
        out["latest_period"] = str(pd.Timestamp(q.index[-1]).date())
    last4 = q.tail(4)
    if len(last4) == 4:
        for m in FLOWS:
            if m in last4:
                out[f"ttm_{m}"] = float(last4[m].sum())
    if len(q):
        for m in ("paid_up", "face_value"):
            if m in q:
                out[m] = float(q[m].dropna().iloc[-1]) if q[m].notna().any() else None
    bs = obs[obs["ctx"] == "I"].pivot_table(index="period_end", columns="metric", values="value").sort_index()
    if len(bs):
        last = bs.iloc[-1]
        for m in ("equity", "cash", "assets"):
            if m in last and pd.notna(last[m]):
                out[m] = float(last[m])
        out["debt"] = float(sum(last.get(k, 0.0) or 0.0 for k in ("debt_current", "debt_noncurrent") if pd.notna(last.get(k))))
        out["balance_sheet_date"] = str(bs.index[-1])
    if "ttm_pbt" in out:
        out["ttm_ebit"] = out["ttm_pbt"] + out.get("ttm_finance_costs", 0.0)
        out["ttm_ebitda"] = out["ttm_ebit"] + out.get("ttm_depreciation", 0.0) - out.get("ttm_other_income", 0.0)
    return out


def ratios(s: dict, price: float) -> dict:
    """Ratios at ``price``. Shares = paid-up capital / face value. Missing inputs give None, never a guess."""
    sh = s["paid_up"] / s["face_value"] if s.get("paid_up") and s.get("face_value") else None
    mcap = price * sh if sh else None
    div = lambda a, b: (a / b) if a is not None and b not in (None, 0) else None     # noqa: E731
    net_debt = (s.get("debt", 0.0) - s.get("cash", 0.0)) if "debt" in s else None
    return {"market_cap": mcap, "pe": div(price, s.get("ttm_eps")) if s.get("ttm_eps", 0) > 0 else None,
            "pb": div(mcap, s.get("equity")), "ev_ebitda": div((mcap or 0) + (net_debt or 0), s.get("ttm_ebitda")) if mcap else None,
            "roe": div(s.get("ttm_pat"), s.get("equity")), "roce": div(s.get("ttm_ebit"), (s.get("equity") or 0) + (s.get("debt") or 0)),
            "debt_equity": div(s.get("debt"), s.get("equity")), "interest_cover": div(s.get("ttm_ebit"), s.get("ttm_finance_costs")),
            "net_debt": net_debt}
