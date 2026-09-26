"""A hundred published anomalies, with sources, and an honest note on which we can test.

## Why a catalogue rather than a pile of code

Harvey, Liu & Zhu (2016) counted 316 published factors and argued the significance hurdle
should be **t > 3.0**, not 2.0, precisely because the literature is one enormous
multiple-testing exercise. Hou, Xue & Zhang (2020) replicated 452 anomalies and found
**65% did not survive** once microcaps were handled properly. McLean & Pontiff (2016)
found published anomalies decay **58%** after publication.

So a hundred strategies is not a hundred chances to win. It is a hundred trials, and this
project already counts every one of them in a shared ledger. Adding a hundred moves our
Bonferroni bar from |t| > 3.35 to |t| > 3.57 - a real but modest cost, and *stricter than
the paper that made strictness famous*.

The catalogue exists so that each entry carries what a strategy needs to be argued with:
the claim, the paper it came from, the inputs it requires, and whether those inputs exist
here. An entry that cannot be computed is marked ``needs`` and left untested rather than
approximated - a proxy for book-to-market is not book-to-market, and pretending otherwise
is how a replication study becomes fiction.

## The finding that fell out of building it

Of the hundred below, roughly half need **fundamental data this project does not hold**:
``financial_results`` has 20 rows and ``filing_facts`` has 1. Book value, earnings, assets,
accruals and share counts are all absent.

That is not a minor gap. The best-replicated anomalies in the literature - value,
profitability, investment, the Fama-French five-factor and Hou-Xue-Zhang q-factor models -
are **all fundamental**. This project has tested roughly twenty price and technical factors
and rejected all of them, which is consistent with the replication literature: price-only
anomalies are the ones that replicate worst.

So the binding constraint has moved. It is no longer "more factors" - it is *fundamental
data*, and sourcing it is worth more than another twenty technical signals.

## Fields

    name      the factor as it will appear in the trial ledger
    claim     what it asserts, in one line
    source    author, year - the paper, not a blog
    family    for grouping; several families are one economic idea in many coats
    needs     the inputs required
    status    "ready" (computable now), "needs-fundamentals", "needs-external"
    expr      SQL over `features` where ready; None otherwise
    direction +1 if high is good, -1 if low is good
    prior     what we expect, written before testing
"""
from __future__ import annotations

READY = "ready"
NEEDS_FUNDAMENTALS = "needs-fundamentals"
NEEDS_EXTERNAL = "needs-external"


def _f(name, claim, source, family, needs, status, expr=None, direction=1, prior=""):
    return {"name": name, "claim": claim, "source": source, "family": family,
            "needs": needs, "status": status, "expr": expr, "direction": direction,
            "prior": prior}


# ---------------------------------------------------------------------------
# MOMENTUM AND REVERSAL - the family this project has tested most and rejected most
# ---------------------------------------------------------------------------
MOMENTUM = [
    _f("mom_12_1", "Winners over 12 months, skipping the last, keep winning.",
       "Jegadeesh & Titman (1993)", "momentum", ["prices"], READY,
       "mom_12_1", 1, "Already rejected here: deflated Sharpe 0.00 after 19 trials."),
    _f("mom_6_1", "The same effect at six months.",
       "Jegadeesh & Titman (1993)", "momentum", ["prices"], READY,
       "ret_120d_skip", 1, "Same idea, shorter window. Correlated with mom_12_1, so it "
       "is nearly a re-test rather than a new question."),
    _f("mom_intermediate", "Returns from t-12 to t-7 predict; recent months do not.",
       "Novy-Marx (2012)", "momentum", ["prices"], READY, "mom_12_7", 1,
       "The most interesting momentum variant: it claims the *old* part of the window "
       "carries the signal."),
    _f("ret_1m_reversal", "Last month's losers outperform next month.",
       "Jegadeesh (1990)", "reversal", ["prices"], READY, "ret_20d", -1,
       "Rejected here: -0.78%/period net at 81% turnover."),
    _f("ret_1w_reversal", "The same at one week, stronger and costlier.",
       "Lehmann (1990)", "reversal", ["prices"], READY, "ret_5d", -1,
       "Turnover will kill it before the signal is even measured."),
    _f("ret_60m_reversal", "Five-year losers beat five-year winners.",
       "De Bondt & Thaler (1985)", "reversal", ["prices"], READY, "ret_1250d", -1,
       "Needs five years per name; our panel starts 2015 so the early years are thin."),
    _f("mom_52w_high", "Nearness to the 52-week high predicts continuation.",
       "George & Hwang (2004)", "momentum", ["prices"], READY, "dist_52w_high", 1,
       "Distinct from momentum: an anchoring story, not a trend one."),
    _f("industry_momentum", "Industries trend even where individual names do not.",
       "Moskowitz & Grinblatt (1999)", "momentum", ["prices", "sector"], NEEDS_EXTERNAL,
       None, 1, "Needs a sector map we do not maintain."),
    _f("mom_residual", "Momentum in the part of returns a factor model cannot explain.",
       "Blitz, Huij & Martens (2011)", "momentum", ["prices", "factors"], READY,
       "resid_mom_12_1", 1, "Requires fitting a market model per name - doable from "
       "prices alone, unlike the fundamental versions."),
    _f("mom_seasonality", "A stock's return in the same calendar month tends to recur.",
       "Heston & Sadka (2008)", "seasonality", ["prices"], READY, "same_month_mean", 1,
       "A genuinely odd result that has partially replicated. Cheap to test here."),
]

# ---------------------------------------------------------------------------
# VOLATILITY, BETA AND THE LOTTERY FAMILY
# ---------------------------------------------------------------------------
RISK = [
    _f("vol_low", "Low-volatility stocks earn at least as much as high.",
       "Blitz & van Vliet (2007)", "low-risk", ["prices"], READY, "vol_60", -1,
       "Rejected here on costs. Has now appeared a third time via atr_14_pct."),
    _f("beta_low", "Low-beta stocks outperform on a risk-adjusted basis.",
       "Frazzini & Pedersen (2014)", "low-risk", ["prices", "index"], READY, "beta_250", -1,
       "Betting-against-beta. Computable: we hold NIFTY levels."),
    _f("ivol", "High idiosyncratic volatility predicts LOW returns.",
       "Ang, Hodrick, Xing & Zhang (2006)", "low-risk", ["prices", "index"], READY,
       "ivol_60", -1, "One of the better-replicated price anomalies."),
    _f("max_return", "Stocks with an extreme recent daily gain underperform.",
       "Bali, Cakici & Whitelaw (2011)", "lottery", ["prices"], READY, "max_ret_20d", -1,
       "The lottery-preference story, and it overlaps ivol heavily."),
    _f("skew_idio", "Positively skewed stocks are overpriced.",
       "Boyer, Mitton & Vorkink (2010)", "lottery", ["prices"], READY, "skew_60", -1,
       "Same family as max_return; expect them to say the same thing."),
    _f("vol_of_vol", "Volatility of volatility predicts returns negatively.",
       "Baltussen, van Bekkum & van der Grient (2018)", "low-risk", ["prices"], READY,
       "vol_of_vol_60", -1, ""),
    _f("downside_beta", "Beta measured only in down markets is the priced one.",
       "Ang, Chen & Xing (2006)", "low-risk", ["prices", "index"], READY,
       "downside_beta_250", -1, ""),
    _f("coskewness", "Stocks that co-skew with the market earn less.",
       "Harvey & Siddique (2000)", "low-risk", ["prices", "index"], READY,
       "coskew_250", -1, ""),
]

# ---------------------------------------------------------------------------
# LIQUIDITY AND MICROSTRUCTURE - computable, because it is all price and volume
# ---------------------------------------------------------------------------
LIQUIDITY = [
    _f("amihud", "Illiquid stocks earn a premium.",
       "Amihud (2002)", "liquidity", ["prices", "volume"], READY, "amihud_60", 1,
       "We already compute this for the cost model. Note the tension: if it works as a "
       "signal, it works because of the very costs that would prevent harvesting it."),
    _f("turnover_low", "Low-turnover stocks outperform.",
       "Datar, Naik & Radcliffe (1998)", "liquidity", ["volume"], READY, "turnover_60", -1, ""),
    _f("volume_trend", "A rising volume trend predicts negative returns.",
       "Chordia, Subrahmanyam & Anshuman (2001)", "liquidity", ["volume"], READY,
       "vol_trend_60", -1, ""),
    _f("zero_return_days", "Days with no price change proxy for illiquidity.",
       "Lesmond, Ogden & Trzcinka (1999)", "liquidity", ["prices"], READY,
       "zero_days_60", 1, ""),
    _f("bid_ask_spread", "The spread itself is priced.",
       "Amihud & Mendelson (1986)", "liquidity", ["quotes"], NEEDS_EXTERNAL, None, 1,
       "Corwin-Schultz failed on this data - see the cost work. Needs real quotes."),
    _f("illiq_shock", "A sudden change in liquidity predicts returns.",
       "Acharya & Pedersen (2005)", "liquidity", ["prices", "volume"], READY,
       "amihud_shock", -1, ""),
    _f("dollar_volume", "Trading value level, as a size proxy.",
       "Brennan, Chordia & Subrahmanyam (1998)", "liquidity", ["volume"], READY,
       "adv20", -1, "Overlaps size almost entirely."),
]

# ---------------------------------------------------------------------------
# SEASONALITY AND CALENDAR - cheap to test, weak priors, high publication bias
# ---------------------------------------------------------------------------
SEASONALITY = [
    _f("turn_of_month", "Returns concentrate around the turn of the month.",
       "Ariel (1987)", "seasonality", ["prices"], READY, "is_turn_of_month", 1,
       "Plausible mechanism (salary flows, SIP inflows in India) and heavily mined."),
    _f("january_effect", "Small caps rally in January.",
       "Rozeff & Kinney (1976)", "seasonality", ["prices"], READY, "is_january", 1,
       "India's tax year ends in March, so the US mechanism does not transfer. Test the "
       "March/April version too."),
    _f("day_of_week", "Monday returns are lower.",
       "French (1980)", "seasonality", ["prices"], READY, "is_monday", -1,
       "Among the most data-mined claims in finance."),
    _f("halloween", "May-October returns are lower than November-April.",
       "Bouman & Jacobsen (2002)", "seasonality", ["prices"], READY, "is_winter_half", 1,
       ""),
    _f("holiday_effect", "Returns rise before market holidays.",
       "Ariel (1990)", "seasonality", ["prices", "calendar"], READY, "pre_holiday", 1, ""),
    _f("fy_end_india", "The March fiscal-year end drives Indian tax-loss selling.",
       "Indian analogue of Rozeff & Kinney", "seasonality", ["prices"], READY,
       "is_march", -1, "The India-specific version, which is the one worth testing here."),
]


# ---------------------------------------------------------------------------
# VALUE - the oldest and best-replicated family, and we can test none of it
# ---------------------------------------------------------------------------
VALUE = [
    _f("book_to_market", "High book-to-market stocks earn more.",
       "Fama & French (1992)", "value", ["book equity"], NEEDS_FUNDAMENTALS),
    _f("earnings_yield", "High E/P earns more.",
       "Basu (1977)", "value", ["earnings"], NEEDS_FUNDAMENTALS),
    _f("cash_flow_yield", "High CF/P earns more.",
       "Lakonishok, Shleifer & Vishny (1994)", "value", ["cash flow"], NEEDS_FUNDAMENTALS),
    _f("sales_to_price", "High S/P earns more; robust where earnings are noisy.",
       "Barbee, Mukherji & Raines (1996)", "value", ["revenue"], NEEDS_FUNDAMENTALS),
    _f("ebitda_to_ev", "Enterprise multiples beat equity multiples.",
       "Loughran & Wellman (2011)", "value", ["ebitda", "debt"], NEEDS_FUNDAMENTALS),
    _f("dividend_yield", "High yield predicts return.",
       "Litzenberger & Ramaswamy (1979)", "value", ["dividends"], NEEDS_FUNDAMENTALS),
    _f("intangible_value", "Value adjusted for intangibles works where raw B/M fails.",
       "Arnott, Harvey, Kalesnik & Linnainmaa (2021)", "value", ["book", "R&D"],
       NEEDS_FUNDAMENTALS),
    _f("value_momentum_combo", "Value and momentum combine because they are negatively "
       "correlated.", "Asness, Moskowitz & Pedersen (2013)", "value",
       ["book equity", "prices"], NEEDS_FUNDAMENTALS),
    _f("net_payout_yield", "Buybacks plus dividends beat dividends alone.",
       "Boudoukh, Michaely, Richardson & Roberts (2007)", "value",
       ["payout"], NEEDS_FUNDAMENTALS),
    _f("enterprise_multiple", "EV/EBITDA as the single best value metric.",
       "Gray & Vogel (2012)", "value", ["ebitda", "debt"], NEEDS_FUNDAMENTALS),
]

# ---------------------------------------------------------------------------
# PROFITABILITY AND QUALITY
# ---------------------------------------------------------------------------
QUALITY = [
    _f("gross_profitability", "Gross profits to assets predicts as well as value.",
       "Novy-Marx (2013)", "quality", ["gross profit", "assets"], NEEDS_FUNDAMENTALS),
    _f("roe", "Return on equity predicts.",
       "Haugen & Baker (1996)", "quality", ["earnings", "equity"], NEEDS_FUNDAMENTALS),
    _f("roa", "Return on assets predicts.",
       "Balakrishnan, Bartov & Faurel (2010)", "quality", ["earnings", "assets"],
       NEEDS_FUNDAMENTALS),
    _f("operating_profitability", "The Fama-French five-factor profitability leg.",
       "Fama & French (2015)", "quality", ["operating income"], NEEDS_FUNDAMENTALS),
    _f("quality_minus_junk", "A composite of profitability, growth, safety and payout.",
       "Asness, Frazzini & Pedersen (2019)", "quality", ["many"], NEEDS_FUNDAMENTALS),
    _f("piotroski_f", "A nine-point accounting score separates good value from bad.",
       "Piotroski (2000)", "quality", ["financials"], NEEDS_FUNDAMENTALS),
    _f("ohlson_o", "Bankruptcy probability is priced.",
       "Ohlson (1980)", "quality", ["financials"], NEEDS_FUNDAMENTALS),
    _f("altman_z", "Distress score predicts returns.",
       "Altman (1968)", "quality", ["financials"], NEEDS_FUNDAMENTALS),
    _f("earnings_stability", "Stable earnings earn a premium.",
       "Graham & Dodd, formalised in Asness et al.", "quality", ["earnings history"],
       NEEDS_FUNDAMENTALS),
    _f("gross_margin_growth", "Improving margins predict.",
       "Novy-Marx (2013)", "quality", ["margins"], NEEDS_FUNDAMENTALS),
    _f("cash_productivity", "Cash flow to assets.",
       "Chandrashekar & Rao (2009)", "quality", ["cash flow", "assets"],
       NEEDS_FUNDAMENTALS),
    _f("leverage_low", "Low leverage predicts positively once distress is controlled.",
       "Penman, Richardson & Tuna (2007)", "quality", ["debt", "equity"],
       NEEDS_FUNDAMENTALS),
]

# ---------------------------------------------------------------------------
# INVESTMENT AND ISSUANCE
# ---------------------------------------------------------------------------
INVESTMENT = [
    _f("asset_growth", "Firms that grow assets fast underperform.",
       "Cooper, Gulen & Schill (2008)", "investment", ["assets"], NEEDS_FUNDAMENTALS),
    _f("investment_to_assets", "The q-factor investment leg.",
       "Hou, Xue & Zhang (2015)", "investment", ["capex", "assets"], NEEDS_FUNDAMENTALS),
    _f("net_share_issuance", "Firms issuing shares underperform.",
       "Pontiff & Woodgate (2008)", "investment", ["share count"], NEEDS_FUNDAMENTALS),
    _f("composite_issuance", "Issuance measured through market value.",
       "Daniel & Titman (2006)", "investment", ["share count"], NEEDS_FUNDAMENTALS),
    _f("accruals", "High accruals predict low returns.",
       "Sloan (1996)", "accruals", ["financials"], NEEDS_FUNDAMENTALS),
    _f("net_operating_assets", "Balance-sheet bloat predicts negatively.",
       "Hirshleifer, Hou, Teoh & Zhang (2004)", "accruals", ["balance sheet"],
       NEEDS_FUNDAMENTALS),
    _f("percent_accruals", "Accruals scaled by earnings rather than assets.",
       "Hafzalla, Lundholm & Van Winkle (2011)", "accruals", ["financials"],
       NEEDS_FUNDAMENTALS),
    _f("inventory_growth", "Inventory build predicts negatively.",
       "Thomas & Zhang (2002)", "investment", ["inventory"], NEEDS_FUNDAMENTALS),
    _f("capex_growth", "Abnormal capital expenditure predicts negatively.",
       "Titman, Wei & Xie (2004)", "investment", ["capex"], NEEDS_FUNDAMENTALS),
    _f("external_finance", "Total external financing predicts negatively.",
       "Bradshaw, Richardson & Sloan (2006)", "investment", ["financing"],
       NEEDS_FUNDAMENTALS),
]
