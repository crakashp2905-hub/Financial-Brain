"""What an account is left with after tax: the same return series, taxed two ways.

An **active** strategy turns its book over, so every gain is realised and every gain is short-term (20.8%).
Tax is paid each 31 March out of the account, so the money that paid it stops compounding. A **passive**
holder realises nothing until the end and pays long-term tax once on the whole gain above the exemption, so
the gain compounds untaxed for the whole period. The same pre-tax return series therefore ends in different
places, and the gap is what the strategy has to earn *in addition* to beat the index after tax.

Returns passed in are **net of trading costs** and **absolute** (not excess over a benchmark): tax is charged
on the rupees made, not on the rupees made over the market.
"""
from __future__ import annotations

from datetime import date

import numpy as np

from ..costs import tax as T


class AfterTaxError(ValueError):
    pass


def _check(dates, returns):
    if len(dates) != len(returns) or len(dates) < 4:
        raise AfterTaxError("need matching dates and returns, at least four periods")
    if any(b <= a for a, b in zip(dates, dates[1:])):
        raise AfterTaxError("dates must be strictly increasing")


def simulate_active(dates: list[date], returns, *, initial: float = 1_000_000.0,
                    rules: T.TaxRules = T.TaxRules(), kind: str = T.SHORT) -> dict:
    """Realise every period's gain; settle tax at each financial-year end and once more at the finish."""
    _check(dates, returns)
    led = T.Ledger(rules)
    w, path, taxes = initial, [], 0.0
    for i, (d, r) in enumerate(zip(dates, returns)):
        gain = w * r
        w += gain
        led.add(d, gain, kind)
        last = i == len(dates) - 1
        if last or T.fiscal_year(dates[i + 1]) != T.fiscal_year(d):
            t = led.close_year(T.fiscal_year(d))
            w -= t
            taxes += t
        path.append(w)
    return _summary(dates, initial, path, taxes)


def simulate_passive(dates: list[date], returns, *, initial: float = 1_000_000.0,
                     rules: T.TaxRules = T.TaxRules()) -> dict:
    """Buy and hold; sell everything on the last date and pay long-term tax once."""
    _check(dates, returns)
    w = initial
    path = []
    for r in returns:
        w *= 1 + r
        path.append(w)
    holding = (dates[-1] - dates[0]).days
    kind = T.classify(holding, rules=rules)
    tax = T.tax_on_gain(w - initial, kind, rules)
    path[-1] = w - tax
    return _summary(dates, initial, path, tax)


def _summary(dates, initial, path, taxes) -> dict:
    years = (dates[-1] - dates[0]).days / 365.25 + 20 / 252     # the last period's own length
    end = path[-1]
    return {"terminal": float(end), "cagr": float((end / initial) ** (1 / years) - 1) if end > 0 else float("nan"),
            "tax_paid": float(taxes), "years": float(years), "path": path}


def pre_tax_cagr(dates, returns, *, initial: float = 1_000_000.0) -> float:
    w = initial * float(np.prod(1 + np.asarray(returns, dtype=float)))
    years = (dates[-1] - dates[0]).days / 365.25 + 20 / 252
    return float((w / initial) ** (1 / years) - 1)
