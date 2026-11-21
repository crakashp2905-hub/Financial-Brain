"""The hundred candidates (H001-H100): the registry, frozen before any of them is run.

Each row: id, name, family, prior class (ADR-0007), direction (+1 a book expected to beat its benchmark, -1 a
flagged set expected to lose: an avoid filter), engine, and the builder key with its parameters in
``scripts/research/hundred.py``. The family size used for the Sidak adjustment is ``PRIOR_FAMILY[family]`` (variants
of the idea already examined in this project) plus the number of rows of that family below.

Engines
    xs      monthly book of the top 20 by a score among the 500 most liquid (or the stated sub-universe), equal
            weight, month end to month end, net of per-name costs, against the equal-weight universe
    flag    monthly book of every flagged name in the 500 (the 40 most liquid if more; a month with fewer than 5
            holds the universe), same costs and benchmark
    xs5     as xs but rebalanced every 5 sessions
    xs20    as xs on the feature library's 20-session grid (walk-forward model)
    rot     monthly rotation among 13 NSE sector total-return indices, top or bottom 3, 20 bp round trip, against
            the equal-weight sector set
    timing  exposure in [0, 1] to an index (or an index pair), 6.5% cash, 4 bp per unit switched; the series is
            the return over the same strategy held at its own trailing average exposure (timing alpha)
"""
from __future__ import annotations

PRIOR_FAMILY = {"mom": 20, "rev": 6, "vol": 8, "volume": 6, "fund": 8, "own": 0, "event": 12, "fno": 4, "rot": 0,
                "timing": 12, "ml": 6, "leadlag": 2, "macro": 0, "tech": 26, "deals": 0, "listing": 0,
                "composite": 6}

# id, name, family, prior, direction, engine, builder, params
C = [
    # ---- momentum family (class A: published across markets, replicated in four tiers here)
    ("H001", "6-1 momentum", "mom", "A", 1, "xs", "mom", {"skip": 21, "look": 126}),
    ("H002", "nearness to the 52-week high, long only", "mom", "A", 1, "xs", "hi52", {}),
    ("H003", "risk-adjusted momentum (12-1 return over 250-day volatility)", "mom", "A", 1, "xs", "mom_riskadj", {}),
    ("H004", "12-1 momentum among names above their 200-day average", "mom", "A", 1, "xs", "mom_ma200", {}),
    ("H005", "industry momentum (top 3 industries by 6-month return, their best names)", "mom", "A", 1, "xs", "ind_mom", {}),
    ("H006", "market-state momentum (book held only when the Nifty 500 is up over 12 months)", "mom", "A", 1, "xs", "mom_state", {}),
    ("H007", "frog-in-the-pan momentum, 100 most liquid only (replication tier)", "mom", "A", 1, "xs", "fip", {"lo": 1, "hi": 100}),
    ("H008", "frog-in-the-pan momentum, ranks 101-500 (replication tier)", "mom", "A", 1, "xs", "fip", {"lo": 101, "hi": 500}),
    ("H009", "frog-in-the-pan momentum, ranks 501-1000 (unseen universe)", "mom", "A", 1, "xs", "fip", {"lo": 501, "hi": 1000}),
    # ---- lead-lag
    ("H010", "industry lead-lag: small names in industries whose large caps led last month", "leadlag", "B", 1, "xs", "ind_leadlag", {}),
    ("H011", "industry earnings spillover: names in industries with the best recent results reactions", "leadlag", "B", 1, "xs", "ind_results_spill", {}),
    # ---- reversal and short horizon
    ("H012", "one-month losers on low volume (liquidity provision)", "rev", "B", 1, "xs", "rev_lowvol", {}),
    ("H013", "industry-relative weekly reversal, 5-session hold", "rev", "B", 1, "xs5", "ind_rev5", {}),
    ("H014", "one-month reversal in the 100 most liquid", "rev", "B", 1, "xs", "rev1m", {"lo": 1, "hi": 100}),
    ("H015", "high-volume one-month winners continue", "rev", "B", 1, "xs", "hv_winners", {}),
    # ---- low risk, lottery, listing age
    ("H016", "low volatility in the 100 most liquid", "vol", "A", 1, "xs", "lowvol", {"lo": 1, "hi": 100}),
    ("H017", "nearness to the 52-week low (avoid)", "vol", "B", -1, "flag", "near_low", {}),
    ("H018", "nominal price under Rs 50 (lottery names, avoid)", "vol", "C", -1, "flag", "low_price", {}),
    ("H019", "low volatility of turnover", "vol", "C", 1, "xs", "vol_of_turnover", {}),
    ("H020", "listed under 250 sessions (new-issue underperformance, avoid)", "listing", "A", -1, "flag", "young", {}),
    # ---- volume, delivery, deals
    ("H021", "high delivery share (20-session average)", "volume", "C", 1, "xs", "deliv_level", {}),
    ("H022", "rising delivery share with a rising price (accumulation)", "volume", "C", 1, "xs", "deliv_accum", {}),
    ("H023", "delivery spike on a down day (absorption), 20 sessions", "volume", "C", 1, "flag", "deliv_spike_down", {}),
    ("H024", "volume climax after a run-up (blow-off, avoid)", "volume", "C", -1, "flag", "climax", {}),
    ("H025", "bulk-deal net buying over 20 sessions", "deals", "B", 1, "xs", "bulk_net", {"sign": 1}),
    ("H026", "bulk-deal net selling over 20 sessions (avoid)", "deals", "B", -1, "xs", "bulk_net", {"sign": -1}),
    ("H027", "block-deal buy in the last 40 sessions", "deals", "B", 1, "flag", "block_buy", {}),
    # ---- fundamentals (2019 onward)
    ("H028", "profit growth year on year, latest quarter", "fund", "A", 1, "xs", "fund", {"m": "pat_yoy"}),
    ("H029", "standardised unexpected earnings", "fund", "A", 1, "xs", "fund", {"m": "sue"}),
    ("H030", "revenue growth acceleration", "fund", "B", 1, "xs", "fund", {"m": "rev_accel"}),
    ("H031", "margin expansion over four quarters", "fund", "B", 1, "xs", "fund", {"m": "margin_delta"}),
    ("H032", "net margin level", "fund", "B", 1, "xs", "fund", {"m": "margin"}),
    ("H033", "operating leverage (revenue growth minus expense growth)", "fund", "B", 1, "xs", "fund", {"m": "oplev"}),
    ("H034", "four straight quarters of profit growth, ranked by momentum", "fund", "B", 1, "xs", "fund_consistent", {}),
    ("H035", "results beat: profit up over 25% and revenue over 15%, filed in the last 40 sessions", "fund", "A", 1, "flag", "fund_flag", {"m": "beat", "L": 40}),
    ("H036", "turnaround: profit positive against a loss a year earlier, 60 sessions", "fund", "B", 1, "flag", "fund_flag", {"m": "turnaround", "L": 60}),
    ("H037", "loss over the trailing four quarters (avoid)", "fund", "B", -1, "flag", "fund_flag", {"m": "ttm_loss", "L": 90}),
    ("H038", "late filers: results more than 44 days after the quarter (avoid)", "fund", "B", -1, "flag", "fund_flag", {"m": "late", "L": 60}),
    ("H039", "early filers: results within 20 days of the quarter", "fund", "B", 1, "flag", "fund_flag", {"m": "early", "L": 60}),
    # ---- ownership (shareholding patterns, 2020 onward)
    ("H040", "promoter holding up 0.5 points or more in the quarter", "own", "B", 1, "flag", "own_flag", {"m": "d_promoter", "thr": 0.5, "sign": 1}),
    ("H041", "promoter holding down 1 point or more (avoid)", "own", "B", -1, "flag", "own_flag", {"m": "d_promoter", "thr": 1.0, "sign": -1}),
    ("H042", "foreign institutional holding, largest increases", "own", "B", 1, "xs", "own", {"m": "d_fii", "sign": 1}),
    ("H043", "foreign institutional holding, largest decreases (avoid)", "own", "B", -1, "xs", "own", {"m": "d_fii", "sign": -1}),
    ("H044", "mutual fund holding, largest increases", "own", "B", 1, "xs", "own", {"m": "d_mf", "sign": 1}),
    ("H045", "retail shareholder count, fastest growth (avoid)", "own", "B", -1, "xs", "own", {"m": "g_retail", "sign": 1}),
    ("H046", "institutional accumulation (foreign + mutual fund + insurance)", "own", "B", 1, "xs", "own", {"m": "d_inst", "sign": 1}),
    # ---- events from filings (flag for L sessions after the filing)
    ("H047", "order win, 20 sessions", "event", "B", 1, "flag", "event", {"types": ["ORDER_WIN"], "L": 20}),
    ("H048", "buyback, 40 sessions", "event", "A", 1, "flag", "event", {"types": ["BUYBACK"], "L": 40}),
    ("H049", "credit rating upgrade, 60 sessions", "event", "B", 1, "flag", "rating", {"sign": 1, "L": 60}),
    ("H050", "credit rating downgrade, 60 sessions (avoid)", "event", "B", -1, "flag", "rating", {"sign": -1, "L": 60}),
    ("H051", "fund raising, 60 sessions (avoid)", "event", "A", -1, "flag", "event", {"types": ["FUND_RAISING"], "L": 60}),
    ("H052", "acquisition announced, 40 sessions (avoid)", "event", "B", -1, "flag", "event", {"types": ["ACQUISITION"], "L": 40}),
    ("H053", "management change, 40 sessions (avoid)", "event", "C", -1, "flag", "event", {"types": ["MANAGEMENT_CHANGE"], "L": 40}),
    ("H054", "auditor resignation, 120 sessions (avoid)", "event", "B", -1, "flag", "event", {"types": ["AUDITOR_RESIGNATION"], "L": 120}),
    ("H055", "promoter pledge filing, 60 sessions (avoid)", "event", "B", -1, "flag", "event", {"types": ["PROMOTER_PLEDGE"], "L": 60}),
    ("H056", "dividend announced, 20 sessions", "event", "C", 1, "flag", "event", {"types": ["DIVIDEND"], "L": 20}),
    ("H057", "three or more investor meets in 20 sessions", "event", "C", 1, "flag", "event", {"types": ["INVESTOR_MEET"], "L": 20, "min": 3}),
    ("H058", "bonus or split announced, 40 sessions", "event", "B", 1, "flag", "event", {"types": ["CORPORATE_ACTION"], "L": 40}),
    ("H059", "exchange sought a clarification, 20 sessions (avoid)", "event", "C", -1, "flag", "event", {"types": ["CLARIFICATION"], "L": 20}),
    ("H060", "three or more press releases in 20 sessions", "event", "C", 1, "flag", "event", {"types": ["PRESS_RELEASE"], "L": 20, "min": 3}),
    ("H061", "legal or regulatory filing, 40 sessions (avoid)", "event", "C", -1, "flag", "event", {"types": ["LEGAL_REGULATORY"], "L": 40}),
    ("H062", "insolvency filing, 120 sessions (avoid)", "event", "B", -1, "flag", "event", {"types": ["INSOLVENCY"], "L": 120}),
    ("H063", "two or more substantial-acquisition filings in 40 sessions", "event", "C", 1, "flag", "event", {"types": ["SUBSTANTIAL_ACQUISITION"], "L": 40, "min": 2}),
    ("H064", "earnings call or investor presentation filed, 40 sessions", "event", "C", 1, "flag", "event", {"types": ["EARNINGS_CALL", "INVESTOR_PRESENTATION"], "L": 40}),
    ("H065", "allotment or ESOP dilution, 40 sessions (avoid)", "event", "C", -1, "flag", "event", {"types": ["ALLOTMENT", "ESOP"], "L": 40}),
    ("H066", "entered the F&O ban list in the last 10 sessions (avoid)", "event", "C", -1, "flag", "fo_ban", {"L": 10}),
    ("H067", "new entrant to the 100 most liquid", "event", "C", 1, "flag", "new_top100", {}),
    # ---- F&O cross-section (names with stock futures)
    ("H068", "futures at the deepest discount to spot", "fno", "B", 1, "xs", "fno", {"m": "basis", "sign": -1, "n": 10}),
    ("H069", "highest put-call open-interest ratio (contrarian)", "fno", "C", 1, "xs", "fno", {"m": "pcr", "sign": 1, "n": 10}),
    ("H070", "futures open interest and price both up over 20 sessions", "fno", "C", 1, "xs", "fno", {"m": "buildup", "sign": 1, "n": 10}),
    # ---- technical, daily
    ("H071", "volatility contraction within 10% of the 52-week high", "tech", "C", 1, "xs", "vcp", {}),
    ("H072", "reclaimed the 200-day average in the last 20 sessions", "tech", "C", 1, "flag", "reclaim200", {}),
    # ---- macro sensitivity
    ("H073", "oil-beta rotation: negative oil beta when Brent is falling, positive when rising", "macro", "C", 1, "xs", "macro_beta", {"series": "DCOILBRENTEU"}),
    ("H074", "rupee-beta rotation: names that gain from the dollar's 3-month trend", "macro", "C", 1, "xs", "macro_beta", {"series": "DEXINUS"}),
    # ---- composites and models
    ("H075", "earnings plus price momentum (rank sum of SUE and 12-1 momentum)", "composite", "B", 1, "xs", "comp_sue_mom", {}),
    ("H076", "institutional accumulation plus momentum (rank sum)", "composite", "B", 1, "xs", "comp_own_mom", {}),
    ("H077", "class-A composite: momentum, 52-week high, continuous information, low volatility", "composite", "B", 1, "xs", "comp_a", {}),
    ("H078", "gradient-boosted rank model on the 160-feature library, refit yearly, walk-forward", "ml", "C", 1, "xs20", "lgbm", {}),
    ("H079", "ridge rank model on the 160-feature library, refit yearly, walk-forward", "ml", "C", 1, "xs20", "ridge", {}),
    # ---- sector rotation (ETF desk)
    ("H080", "sector 6-1 momentum, top 3", "rot", "B", 1, "rot", "rot_mom", {"skip": 1, "look": 6, "top": True}),
    ("H081", "sector 12-1 momentum, top 3", "rot", "B", 1, "rot", "rot_mom", {"skip": 1, "look": 12, "top": True}),
    ("H082", "sector one-month reversal, bottom 3", "rot", "C", 1, "rot", "rot_mom", {"skip": 0, "look": 1, "top": False}),
    ("H083", "sector nearness to the 52-week high, top 3", "rot", "B", 1, "rot", "rot_hi52", {}),
    ("H084", "sector low volatility, lowest 3", "rot", "B", 1, "rot", "rot_lowvol", {}),
    ("H085", "sector 6-1 momentum, only sectors above their 10-month average, else cash", "rot", "B", 1, "rot", "rot_mom_trend", {}),
    ("H086", "size rotation: Smallcap 250 or Nifty 50, whichever led over 6 months", "rot", "B", 1, "timing", "pair_mom", {"a": "NIFTY SMALLCAP 250", "b": "NIFTY 50"}),
    # ---- index timing
    ("H087", "Nifty 50 above its 10-month average", "timing", "A", 1, "timing", "t_ma10", {}),
    ("H088", "breadth: more than half of stocks above their 200-day average", "timing", "B", 1, "timing", "t_breadth", {}),
    ("H089", "volatility-managed Nifty 500 (12% target on 20-day realised)", "timing", "A", 1, "timing", "t_volman", {}),
    ("H090", "India VIX under 25", "timing", "C", 1, "timing", "t_vix", {}),
    ("H091", "variance risk premium high (VIX squared minus realised variance)", "timing", "B", 1, "timing", "t_vrp", {}),
    ("H092", "foreign investors' index-futures net long rising over 20 sessions", "timing", "C", 1, "timing", "t_part", {"who": "FII", "contra": False}),
    ("H093", "retail index-futures net long falling over 20 sessions (contrarian)", "timing", "C", 1, "timing", "t_part", {"who": "Client", "contra": True}),
    ("H094", "Nifty put-call open-interest ratio above its 1-year median (contrarian)", "timing", "C", 1, "timing", "t_pcr", {}),
    ("H095", "Nifty futures basis below its 1-year median (contrarian)", "timing", "C", 1, "timing", "t_basis", {}),
    ("H096", "breadth thrust: advancers over 65% of issues on a 10-day average", "timing", "B", 1, "timing", "t_thrust", {}),
    ("H097", "rupee weakening over 3 months: Nifty IT over Nifty 50", "timing", "B", 1, "timing", "pair_macro", {"a": "NIFTY IT", "b": "NIFTY 50", "series": "DEXINUS", "up": True}),
    ("H098", "copper rising over 3 months: Nifty Metal over Nifty 50", "timing", "B", 1, "timing", "pair_macro", {"a": "NIFTY METAL", "b": "NIFTY 50", "series": "PCOPPUSDM", "up": True}),
    ("H099", "10-year yield falling over 3 months: Nifty Bank over Nifty 50", "timing", "B", 1, "timing", "pair_macro", {"a": "NIFTY BANK", "b": "NIFTY 50", "series": "INDIRLTLT01STM", "up": False}),
    ("H100", "turn of the month in the Midcap 150 (last 2 and first 3 sessions)", "timing", "B", 1, "timing", "t_tom", {}),
]

assert len(C) == 100 and len({r[0] for r in C}) == 100


def family_size(family: str) -> int:
    return PRIOR_FAMILY[family] + sum(1 for r in C if r[2] == family)
