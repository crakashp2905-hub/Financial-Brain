"""Indian tax on trading profits, as a configurable rule set - information, not tax advice.

Three kinds of profit are taxed differently, and a trade's *holding period and segment* decide which:

    intraday (squared off the same day)   speculative business income, taxed at the trader's slab rate;
                                          its losses offset only other speculative profit (carry 4 years)
    short-term (equity held <= 12 months) 20% (+4% cess), no exemption; a short-term LOSS offsets short-term
                                          then long-term gains and carries forward 8 years
    long-term (equity held > 12 months)   12.5% (+4% cess) on the gains above Rs 1.25 lakh in a financial year

The rules below are the ones in force as the project understood them on 2026-10-04 (post-July-2024 rates,
unchanged in the 2026 budget per public summaries). They are **parameters, not facts of nature**: the rates
move in budgets, and the slab rate is personal. ``TaxRules`` takes every one of them, and an account holder
should check the current schedule and their own position before relying on any number here.

What this deliberately does not model: surcharge on high incomes, the section 87A rebate (it does not apply
to special-rate capital gains anyway), advance-tax interest, the new-regime/old-regime choice, grandfathering
of pre-2018 holdings, and tax on dividends. It also taxes the *account* once a year at 31 March; real tax is
settled through advance tax and the return.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

INTRADAY, SHORT, LONG = "intraday", "short", "long"
CAPITAL_CARRY_YEARS = 8
SPECULATIVE_CARRY_YEARS = 4


class TaxError(ValueError):
    pass


@dataclass(frozen=True)
class TaxRules:
    stcg: float = 0.20
    ltcg: float = 0.125
    ltcg_exempt: float = 125_000.0
    cess: float = 0.04
    long_term_days: int = 365
    slab: float = 0.30                  #: the trader's marginal slab rate, used for intraday profit

    @property
    def short_rate(self) -> float:
        return self.stcg * (1 + self.cess)

    @property
    def long_rate(self) -> float:
        return self.ltcg * (1 + self.cess)

    @property
    def speculative_rate(self) -> float:
        return self.slab * (1 + self.cess)


def classify(holding_days: float, intraday: bool = False, rules: TaxRules = TaxRules()) -> str:
    if holding_days < 0:
        raise TaxError("holding period cannot be negative")
    if intraday:
        return INTRADAY
    return LONG if holding_days > rules.long_term_days else SHORT


def fiscal_year(d: date) -> int:
    """The year in which the financial year ends: 2025-04-01 .. 2026-03-31 is 2026."""
    return d.year + 1 if d.month >= 4 else d.year


def tax_on_gain(gain: float, kind: str, rules: TaxRules = TaxRules()) -> float:
    """Tax on a single realised gain taken in isolation (a loss earns no refund here)."""
    if gain <= 0:
        return 0.0
    if kind == INTRADAY:
        return gain * rules.speculative_rate
    if kind == SHORT:
        return gain * rules.short_rate
    if kind == LONG:
        return max(gain - rules.ltcg_exempt, 0.0) * rules.long_rate
    raise TaxError(f"unknown kind {kind!r}")


class Ledger:
    """Realised profit and loss by financial year, with loss set-off and carry-forward.

    Order of set-off within a year, following the Act's structure: short-term losses are applied against
    short-term gains and then long-term gains; long-term losses only against long-term gains; speculative
    losses only against speculative gains. Whatever is left of a loss carries forward, aging out after the
    statutory number of years.
    """

    def __init__(self, rules: TaxRules = TaxRules()):
        self.rules = rules
        self.year: dict[int, dict[str, float]] = {}
        self.carry_cap: list[list] = []       # [fiscal_year_of_loss, amount]  (capital: short and long pooled)
        self.carry_spec: list[list] = []
        self.paid: dict[int, float] = {}

    def add(self, d: date, gain: float, kind: str) -> None:
        if kind not in (INTRADAY, SHORT, LONG):
            raise TaxError(f"unknown kind {kind!r}")
        y = self.year.setdefault(fiscal_year(d), {INTRADAY: 0.0, SHORT: 0.0, LONG: 0.0})
        y[kind] += gain

    @staticmethod
    def _use_carry(carry: list[list], fy: int, horizon: int, gain: float) -> float:
        """Apply carried-forward losses (oldest first, unexpired only) against ``gain``; return the rest."""
        carry[:] = [c for c in carry if fy - c[0] <= horizon and c[1] > 0]
        for c in sorted(carry, key=lambda c: c[0]):
            if gain <= 0:
                break
            use = min(c[1], gain)
            c[1] -= use
            gain -= use
        return gain

    def close_year(self, fy: int) -> float:
        r = self.rules
        y = self.year.get(fy, {INTRADAY: 0.0, SHORT: 0.0, LONG: 0.0})
        short, long_, spec = y[SHORT], y[LONG], y[INTRADAY]
        # capital gains: net the buckets
        if short < 0 and long_ > 0:
            offset = min(-short, long_)
            long_ -= offset
            short += offset
        if long_ < 0 and short > 0:                                  # a long-term loss cannot offset short-term
            pass
        short_taxable = max(short, 0.0)
        long_taxable = max(long_, 0.0)
        new_cap_loss = (-short if short < 0 else 0.0) + (-long_ if long_ < 0 else 0.0)
        # brought-forward capital losses against whatever is still taxable, short-term first
        short_taxable = self._use_carry(self.carry_cap, fy, CAPITAL_CARRY_YEARS, short_taxable)
        if short_taxable <= 0:
            long_taxable = self._use_carry(self.carry_cap, fy, CAPITAL_CARRY_YEARS, long_taxable)
        if new_cap_loss > 0:
            self.carry_cap.append([fy, new_cap_loss])
        # speculative
        spec_taxable = max(spec, 0.0)
        spec_taxable = self._use_carry(self.carry_spec, fy, SPECULATIVE_CARRY_YEARS, spec_taxable)
        if spec < 0:
            self.carry_spec.append([fy, -spec])
        tax = (short_taxable * r.short_rate + max(long_taxable - r.ltcg_exempt, 0.0) * r.long_rate
               + spec_taxable * r.speculative_rate)
        self.paid[fy] = tax
        return tax
