---
type: research
tags:
  - research
  - tax
  - costs
date: 2026-10-04
status: result
---

# Result of tax-aware returns

Predictions committed at `7cf8084`. 129 twenty-session periods, 2016-04 to 2026-08, Rs 10 lakh start. Sleeve
returns are **absolute and net of trading costs** (not excess). Active sleeves realise every gain each period,
all short-term (STCG 20% + 4% cess = 20.8%), tax settled each 31 March with loss carry-forward. Passive holders
realise once at the end (LTCG 12.5% + cess = 13% above Rs 1.25 lakh). Tax rules are parameters checked against
public sources on 2026-10-04; this is information, not tax advice.

## The registered comparison: against the Nifty 500 price index

    strategy                        pre-tax CAGR   after-tax CAGR   terminal Rs   tax paid Rs   tax drag
    Nifty 500, buy and hold            13.20%         12.18%         3,311,529       326,723      1.02%
    momentum sleeve                    21.87%         17.69%         5,458,755     1,170,986      4.17%
    near-52w-high sleeve               21.25%         17.02%         5,143,675     1,088,238      4.23%
    composite top 20%                  18.49%         14.84%         4,227,496       847,625      3.65%
    composite, all but worst 20%       17.51%         14.15%         3,969,695       779,920      3.36%

    T1 every active sleeve loses >= 15% of its pre-tax CAGR to tax        PASS   19-20%
    T2 Nifty 500 buy-and-hold beats every active sleeve after tax         FAIL   all four beat it, by 20% to 65% in terminal wealth
    T3 momentum after-tax CAGR within 1.5 points of the index             FAIL   +5.5 points
    T4 realising every period costs >= 1 point of CAGR vs deferring        PASS   2.2 to 2.9 points (momentum 20.5% deferred vs 17.7% realised)
    T5 no swing or ORB variant turns positive after tax                   PASS   by arithmetic: tax only reduces a positive result

**T2 and T3 failed, but the comparison was not like for like.** The Nifty 500 is a cap-weighted large-cap price
index; the sleeves are equal-weight baskets drawn from the whole liquid universe (turnover >= Rs 1 crore a day),
which tilts hard to small caps. A like-for-like benchmark was added **after** seeing the result, so it is
disclosed as post hoc.

## The like-for-like comparison: against the same universe, equal-weight

    liquid universe, equal-weight, held (tax deferred, costless)   15.40% -> 14.27% after tax    Rs 4,013,710
    liquid universe, equal-weight, rebalanced yearly (costless)    15.40% -> 12.46% after tax    Rs 3,397,816

    sleeve                          after-tax CAGR vs the universe HELD    terminal    vs the universe rebalanced like it
    momentum                              +3.42 points                       +36%            +5.24 points
    near-52w-high                         +2.75 points                       +28%            +4.57 points
    composite top 20%                     +0.57 points                        +5%            +2.38 points
    composite, all but worst 20%          -0.12 points                        -1%            +1.69 points

About two of the 22 points of momentum's CAGR is simply that the universe itself returned 15.4% against the
index's 13.2% (equal weight and a small-cap tilt, and any survivorship in the archive). Strip that out and
**momentum and the 52-week-high sleeves still beat the same universe by about 3 points a year after cost and
tax; the composite top-20% by half a point; and "own everything except the worst 20%" does not beat the
universe held passively.** Against the universe turned over every year like the sleeve (the fairer tax
comparison for an active alternative) all four are ahead by 1.7 to 5.2 points.

## What tax does, in plain numbers

* Tax takes **19-20% of an active sleeve's pre-tax CAGR**: not a rounding error, a fifth of the return.
* **Deferral is worth 2.2 to 2.9 points of CAGR a year.** The same return series, if its gains could be left
  untaxed until the end, compounds a quarter faster. This is the structural advantage of buy-and-hold and the
  hidden cost of every strategy that sells winners.
* Tax charges **gains, not skill**: the sleeves' profits include market return, so tax falls on all of it.
* Tax cannot rescue a negative result (T5): the swing setups lose to a random name before tax, and an
  intraday trade's 15-21 bp loss earns no tax credit unless there are speculative gains to set it against.

## What this does not say

* **In-sample.** The sleeves are built from signals found on this same period; only the live paper record
  can confirm them, and it has two sessions.
* **Not implementable at Rs 10 lakh.** Each sleeve holds about 300 names at Rs 5 lakh each (a Rs 15 crore
  book). A Rs 10 lakh account would hold perhaps 20 names, with a flat Rs 15.34 depository fee on every sale and
  far more concentration risk. The numbers describe the strategy at scale, not an account of that size.
* **Momentum has crash risk** that a CAGR hides; the sleeves have not been stress-tested here.
* Every gain is taken as short-term: some names are in fact held over a year and would be taxed at 13%, so the
  slow sleeves are slightly over-taxed. Surcharge, advance-tax interest and the old/new regime choice are not
  modelled.

Related: [[PRE-REGISTERED tax-aware returns]] · [[Why costs are bigger than the edge]] ·
[[Trader layers built, and what they showed]]
