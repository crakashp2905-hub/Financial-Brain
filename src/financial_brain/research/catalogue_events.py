"""The remaining families: earnings, corporate events, and technical.

Split from ``catalogue.py`` only to keep each file readable. The three families here are
where this project's data is unusually *strong* rather than weak - 3.08M classified
announcements is a better event archive than most retail setups have - and where the
analogue work has already produced measured base rates for several entries.
"""
from __future__ import annotations

from .catalogue import NEEDS_EXTERNAL, NEEDS_FUNDAMENTALS, READY, _f

# ---------------------------------------------------------------------------
# EARNINGS AND ANALYST - mostly blocked, with three exceptions worth noting
# ---------------------------------------------------------------------------
EARNINGS = [
    _f("pead", "Prices drift for weeks after an earnings surprise.",
       "Bernard & Thomas (1989)", "earnings", ["earnings", "consensus"],
       NEEDS_FUNDAMENTALS),
    _f("sue", "Standardised unexpected earnings.",
       "Foster, Olsen & Shevlin (1984)", "earnings", ["earnings history"],
       NEEDS_FUNDAMENTALS),
    _f("earnings_announcement_return", "The three-day return around results recurs.",
       "Frazzini & Lamont (2007)", "earnings", ["results dates"], READY,
       "ear_3d", 1,
       "Partly computable: we hold 280,009 RESULTS announcement dates even without the "
       "numbers, so the return *around* them is measurable when the surprise is not."),
    _f("revisions", "Analyst forecast revisions predict.",
       "Chan, Jegadeesh & Lakonishok (1996)", "analyst", ["estimates"], NEEDS_EXTERNAL),
    _f("dispersion", "High forecast dispersion predicts low returns.",
       "Diether, Malloy & Scherbina (2002)", "analyst", ["estimates"], NEEDS_EXTERNAL),
    _f("recommendation_changes", "Broker upgrades predict short-horizon returns.",
       "Womack (1996)", "analyst", ["recommendations"], NEEDS_EXTERNAL),
    _f("guidance", "Management guidance moves prices predictably.",
       "Hutton, Miller & Skinner (2003)", "earnings", ["guidance"], NEEDS_EXTERNAL),
    _f("earnings_quality", "Cash earnings beat accrual earnings.",
       "Richardson, Sloan, Soliman & Tuna (2005)", "accruals", ["financials"],
       NEEDS_FUNDAMENTALS),
    _f("late_filing", "Firms that file results late underperform.",
       "Bartov & Konchitchki (2017)", "earnings", ["filing dates"], READY,
       "days_late_results", -1,
       "Computable from our announcement timestamps - a rare case where the event "
       "archive substitutes for the fundamentals it usually needs."),
    _f("results_correction", "A revised or corrected filing signals trouble.",
       "Hribar & Jenkins (2004)", "earnings", ["filings"], READY,
       "clarification_90d", -1,
       "CLARIFICATION already measured -2.54% over 6,795 cases in the analogue "
       "catalogue, which makes this the best-supported untested entry in the list."),
]

# ---------------------------------------------------------------------------
# EVENTS AND CORPORATE ACTIONS - the strongest data this project holds
# ---------------------------------------------------------------------------
EVENTS = [
    _f("insider_buying", "Insider purchases predict positively.",
       "Lakonishok & Lee (2001)", "insider", ["dealing direction"], NEEDS_EXTERNAL,
       None, 1,
       "VOID here rather than rejected: the feature contained no dealing data at all. "
       "Direction sits unparsed in 95,751 SAST attachments."),
    _f("buyback_announcement", "Repurchase announcements are followed by drift.",
       "Ikenberry, Lakonishok & Vermaelen (1995)", "corporate-action", ["announcements"],
       READY, "buyback_90d", 1, "5,803 BUYBACK filings held."),
    _f("seo_underperformance", "Equity issuers underperform for years.",
       "Loughran & Ritter (1995)", "corporate-action", ["announcements"], READY,
       "fund_raising_90d", -1,
       "FUND_RAISING measured **+0.62%** in the analogue work - the opposite sign to "
       "the paper, which makes it worth a proper test rather than an assumption."),
    _f("spinoff", "Spun-off units outperform.",
       "Cusatis, Miles & Woolridge (1993)", "corporate-action", ["announcements"],
       READY, "scheme_90d", 1, "SCHEME measured -1.85%, also opposite."),
    _f("index_addition", "Stocks added to an index rise on inclusion.",
       "Shleifer (1986)", "corporate-action", ["index membership"], NEEDS_EXTERNAL),
    _f("dividend_initiation", "Initiating a dividend predicts positively.",
       "Michaely, Thaler & Womack (1995)", "corporate-action", ["announcements"],
       READY, "first_dividend", 1, "95,476 DIVIDEND filings; the *first* one per name "
       "is the event the paper is about."),
    _f("merger_target", "Targets rise and acquirers fall on announcement.",
       "Asquith (1983)", "corporate-action", ["announcements"], READY,
       "acquisition_90d", 1, "ACQUISITION measured +0.41% over 6,370 cases."),
    _f("credit_downgrade", "Rating downgrades are followed by drift.",
       "Dichev & Piotroski (2001)", "credit", ["ratings"], READY, "rating_90d", -1,
       "8,935 filings, and the own-record scorecard independently flagged CREDIT_RATING "
       "prompts as losers - 4 of 5, -3.2% against +4.7%."),
    _f("auditor_resignation", "Auditor departure signals trouble.",
       "Wells & Loudder (1997)", "governance", ["announcements"], READY,
       "auditor_resign_90d", -1,
       "Measured **-3.58%** - the most negative event type in the analogue catalogue."),
    _f("insolvency_filing", "Distress filings predict continued decline.",
       "Campbell, Hilscher & Szilagyi (2008)", "governance", ["announcements"], READY,
       "insolvency_90d", -1, "Measured -3.29% over 144 cases."),
    _f("promoter_pledge", "Promoter share pledging signals leverage stress.",
       "India-specific; analogous to Anderson & Puleo (2015)", "governance",
       ["announcements"], READY, "pledge_90d", -1,
       "27,688 filings, a clear mechanism, and no US equivalent - one of the few places "
       "an Indian archive has an edge over a US one."),
    _f("order_win", "Contract awards are followed by drift.",
       "Analogous to Chaney, Devinney & Winer (1991)", "corporate-action",
       ["announcements"], READY, "order_win_90d", 1,
       "Measured +1.49% mean on a **-2.29% median** and a 45% win rate: right-skewed, "
       "so the mean is a handful of large winners rather than a general drift."),
    _f("management_change", "CEO and CFO departures predict.",
       "Denis & Denis (1995)", "governance", ["announcements"], READY,
       "mgmt_change_90d", -1, "9,995 filings; measured -0.21%."),
    _f("legal_regulatory", "Enforcement and tax actions predict negatively.",
       "Karpoff, Lee & Martin (2008)", "governance", ["announcements"], READY,
       "legal_90d", -1, "8,872 filings; measured +0.04%, essentially nothing."),
    _f("joint_venture", "JV announcements create value.",
       "McConnell & Nantell (1985)", "corporate-action", ["announcements"], READY,
       "jv_90d", 1, "Measured **+1.67%**, the most positive event type held, though on "
       "only 277 cases."),
]

# ---------------------------------------------------------------------------
# TECHNICAL - tested hardest here, and rejected wholesale
# ---------------------------------------------------------------------------
TECHNICAL = [
    _f("ma_200", "Price above its 200-session average.",
       "Brock, Lakonishok & LeBaron (1992)", "trend", ["prices"], READY,
       "above_ma200", 1,
       "**t = +3.69** - the only signal here ever to clear Bonferroni - and it died on "
       "70% turnover."),
    _f("golden_cross", "The 50-session average above the 200.",
       "Brock, Lakonishok & LeBaron (1992)", "trend", ["prices"], READY,
       "ma_double_cross_50_200", 1, "t = +2.74, net positive, failed significance."),
    _f("trend_atr_band", "The same filter with a volatility-scaled band.",
       "Kaufman (2013), adaptive bands", "trend", ["prices"], READY,
       "above_ma200_atr_band", 1,
       "Passed the cost gate - the first trend rule here to do so - at t = +0.43."),
    _f("rsi_oversold", "RSI below 30 predicts a bounce.",
       "Wilder (1978)", "oscillator", ["prices"], READY, "rsi_14", -1,
       "Rejected at t = -0.24 on 81% turnover."),
    _f("macd_signal", "MACD histogram crossing its signal line.",
       "Appel (1979)", "trend", ["prices"], READY, "macd_hist", 1, "Rejected, t = -1.64."),
    _f("bollinger_reversion", "Two standard deviations below the mean.",
       "Bollinger (2001)", "oscillator", ["prices"], READY, "bb_pct_20", -1,
       "Rejected as a time-series system at t = -6.75."),
    _f("donchian_breakout", "The twenty-day high, the Turtle rule.",
       "Dennis and Eckhardt, documented in Faith (2007)", "breakout", ["prices"],
       READY, "turtle_20_10", 1, "Rejected, t = -5.20."),
    _f("adx_strength", "Trend strength as a conditioner rather than a signal.",
       "Wilder (1978)", "trend", ["prices"], READY, "adx_14", 1,
       "Rejected standalone at t = +1.57, which is the correct result: it measures "
       "strength without direction, so alone it should say nothing."),
    _f("obv_divergence", "Volume confirming or contradicting price.",
       "Granville (1963)", "volume", ["prices", "volume"], READY, "obv_slope_20", 1,
       "Rejected, t = -2.31."),
    _f("candlestick_reversals", "Reversal shapes in daily bars.",
       "Nison (1991); tested by Marshall, Young & Rose (2006)", "pattern", ["OHLC"],
       READY, "k_hammer", 1,
       "All seven rejected. The doji control fired at t = -13.60, and investigation "
       "showed the patterns select on volatility rather than direction."),
    _f("opening_range_breakout", "Intraday break of the first fifteen minutes.",
       "Crabel (1990)", "breakout", ["minute bars"], READY, "opening_range", 1,
       "Now testable: 18.45M minute bars held across 100 names."),
    _f("dual_thrust", "Range-based intraday breakout from N sessions.",
       "Wang, documented in Chan (2009)", "breakout", ["minute bars"], READY,
       "dual_thrust", 1, "Developed on futures, where a round trip is a fraction of a "
       "basis point. Indian cash equity pays 20 bps of STT alone."),
    _f("r_breaker", "Pivot levels from the previous session.",
       "Mark Johnson, documented in Chan (2009)", "breakout", ["minute bars"], READY,
       "r_breaker", 1, "Same futures caveat."),
    _f("dynamic_breakout_ii", "Breakout with a volatility-adaptive lookback.",
       "Stridsman (2000)", "breakout", ["minute bars"], READY,
       "dynamic_breakout_ii", 1, "Same futures caveat."),
    _f("ghost_trader", "Trade only after the rule would have lost.",
       "Stridsman (2003)", "breakout", ["minute bars"], READY, "ghost_trader", 1,
       "A claim about the strategy's own autocorrelation rather than the market's, "
       "which makes it unusually easy to fool oneself with."),
]
