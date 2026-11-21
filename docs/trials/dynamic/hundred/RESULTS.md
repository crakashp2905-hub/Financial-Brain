# The hundred candidates: results, 2026-11-17

Registration: `brain/07-Research/PRE-REGISTERED the hundred candidates under the dynamic standard.md`, committed
(6bd74970) before any return was computed. Code: `scripts/research/hundred.py`. Graded under ADR-0007.

## Tally

**96 FAIL, 2 CANDIDATE_SMALL, 1 INCUBATE, 1 UNTESTABLE.** The owner's target of twenty-five distinct edges was not
met, and no honest reading of these results reaches it.

| | what | net excess / month | t (HAC) | halves | null | P(real) | tier |
|---|---|---|---|---|---|---|---|
| H005 | industry momentum: top 3 industries by 6-month return, their 20 best 12-1 momentum names | +1.42% | 3.52 | 2.95 / 2.11 | passed (q95 0.46) | 0.88 | CANDIDATE_SMALL |
| H003 | risk-adjusted momentum: 12-1 return over 250-day volatility, top 20 | +1.28% | 3.18 | 2.76 / 1.64 | passed (q95 0.72) | 0.72 | CANDIDATE_SMALL |
| P3 | frog-in-the-pan momentum, re-graded at family 29 as its registration required | +1.44% | 3.15 | 3.33 / 1.35 | passed | 0.71 | CANDIDATE_SMALL |
| H026 | bulk-deal net selling (avoid) | -1.18% | 3.03 | 2.64 / 1.58 | **failed** (q95 3.30) | 0.80 | INCUBATE |

H005 is the first registered series in this project with t above 2 in both halves, which is ADR-0004's own G3.

## What the three books are

One idea in three constructions: momentum. Correlations of monthly excess: H003-P3 0.73, H005-H003 0.41, H005-P3
0.42. The registered combination (equal weight of every book at INCUBATE or better, here H003 and H005) has
t 4.16; graded as registered (class C, family 100, because it was selected on these results) it is INCUBATE,
posterior 0.61.

They share momentum's weaknesses: 2024 was -12% (H003) and +5% (H005) against the universe, excess drawdowns of
20% and 15%, 41-46% monthly turnover, last-36-month t of 1.15 and 0.85.

## The predictions

* **E1** fewer than ten at CANDIDATE_SMALL: **right** (two), but they are books, not avoid filters: half wrong.
* **E2** no timing or technical rule reaches INCUBATE: **right**.
* **E3** frog-in-the-pan replicates in ranks 101-500 and 501-1000, weaker in the top 100: **partly**. Positive in
  both halves in all three tiers (t 2.35, 1.16, 1.61) but the second half is weak everywhere (0.70, 0.23, 0.37).
* **E4** an ownership or results candidate reaches INCUBATE: **wrong**. Shareholding has no usable first half
  (2020-21), so every ownership candidate fails "both halves" by construction.
* **E5** twenty-five edges not met: **right**.

## Near misses: forward paper only, no capital

Positive in both halves and short of their family-adjusted bar. Not edges.

* H100 turn of the month in the Midcap 150: t 2.44, halves 1.94 / 2.50 (the Nifty version, Z1, was 1.58).
* H036 turnaround, H031 margin expansion, H033 operating leverage: t 2.2-2.6 on 7.5 years of fundamentals.
* H044 mutual-fund accumulation: t 2.28 on 2022-26 only.
* H010 industry lead-lag: t 2.11. H008 frog-in-the-pan in ranks 101-500: t 2.35.
* Avoid shapes: exchange clarification (H059, t 3.21), legal filing (H061, 2.92), pledge filing (H055, 2.16),
  auditor resignation (H054, 2.06). Class C or family 33 keeps each below 0.15.

## Closed by this run

Sector rotation in every form (7 rules, |t| under 1.6), index timing on trend, breadth, volatility, VIX, variance
premium, participant positioning, put-call ratio, basis and macro pairs (13 rules), the two walk-forward models on
160 features (t 0.66 and -0.95), reversal (4), delivery (3), ratings, order wins, buybacks, F&O cross-section.

## Two results that are probably artifacts, not signals

H056 (dividend announced, -0.71% a month, t -3.64) and H068 (futures at a discount, -1.52%, t -4.87) both select
names about to go ex-dividend. If `close_adj` does not adjust for cash dividends, the ex-date drop is counted as a
loss. That is unverified here. If true, every book in this project is slightly biased against dividend payers and
the adjustment should be fixed before either result is read as a short.

H026's null failure says the same about turnover: random books turned over 89% a month lose about as much, so the
"avoid" reading of bulk deals is mostly cost.

## On paper

`edge_h003_riskadj_mom` and `edge_h005_industry_mom` are added to the paper zoo's monthly targets and to `fb oos`
(cutoff 2026-11-17). They start at the first session of the month after this branch reaches the checkout that
runs the nightly refresh. Adding them exposed a latent crash: `zoo_edges.monthly_targets` raised on a past holding
with no later price (the missing-return guard added in October), which would have stopped every monthly edge
target at the next month start. Fixed here by scoring such a holding at its last traded price.

## All hundred

| id | candidate | kind | prior / family | net excess / period | t | halves | posterior and tier |
|---|---|---|---|---|---|---|---|
| H005 | industry momentum (top 3 industries by 6-month return, their best names) | book | A/29 | +1.42% | +3.52 | +2.95 / +2.11 | 0.88 CANDIDATE_SMALL |
| H003 | risk-adjusted momentum (12-1 return over 250-day volatility) | book | A/29 | +1.28% | +3.18 | +2.76 / +1.64 | 0.72 CANDIDATE_SMALL |
| H026 | bulk-deal net selling over 20 sessions (avoid) | avoid | B/3 | -1.18% | +3.03 | +2.64 / +1.58 | 0.80 INCUBATE (lost to its random-book null) |
| H008 | frog-in-the-pan momentum, ranks 101-500 (replication tier) | book | A/29 | +0.99% | +2.35 | +2.82 / +0.70 | 0.30 FAIL |
| H020 | listed under 250 sessions (new-issue underperformance, avoid) | avoid | A/1 | -0.15% | +0.53 | -0.45 / +1.68 | 0.28 FAIL (not positive in both halves) |
| H004 | 12-1 momentum among names above their 200-day average | book | A/29 | +0.99% | +2.23 | +2.02 / +1.26 | 0.27 FAIL |
| H028 | profit growth year on year, latest quarter | book | A/20 | +0.76% | +2.03 | +1.68 / +1.28 | 0.26 FAIL |
| H010 | industry lead-lag: small names in industries whose large caps led last month | book | B/4 | +0.55% | +2.11 | +1.59 / +1.37 | 0.25 FAIL |
| H006 | market-state momentum (book held only when the Nifty 500 is up over 12 months) | book | A/29 | +0.92% | +2.02 | +1.65 / +1.59 | 0.25 FAIL |
| H035 | results beat: profit up over 25% and revenue over 15%, filed in the last 40 sessions | book | A/20 | +0.63% | +1.66 | +1.04 / +1.58 | 0.24 FAIL |
| H044 | mutual fund holding, largest increases | book | B/7 | +0.50% | +2.28 | +nan / +2.44 | 0.24 FAIL (not positive in both halves) |
| H048 | buyback, 40 sessions | book | A/33 | -0.10% | -0.37 | +0.38 / -1.51 | 0.24 FAIL (not positive in both halves) |
| H087 | Nifty 50 above its 10-month average | book | A/26 | -0.08% | -0.46 | -0.29 / -0.13 | 0.23 FAIL (not positive in both halves) |
| H031 | margin expansion over four quarters | book | B/20 | +0.83% | +2.63 | +2.52 / +1.56 | 0.23 FAIL |
| H001 | 6-1 momentum | book | A/29 | +0.72% | +1.67 | +1.72 / +0.61 | 0.21 FAIL |
| H016 | low volatility in the 100 most liquid | book | A/12 | -0.24% | -0.77 | -0.00 / -1.31 | 0.20 FAIL (not positive in both halves) |
| H089 | volatility-managed Nifty 500 (12% target on 20-day realised) | book | A/26 | -0.07% | -0.84 | -0.33 / -0.64 | 0.19 FAIL (not positive in both halves) |
| H007 | frog-in-the-pan momentum, 100 most liquid only (replication tier) | book | A/29 | +0.40% | +1.61 | +1.86 / +0.37 | 0.19 FAIL |
| H011 | industry earnings spillover: names in industries with the best recent results reactions | book | B/4 | +0.56% | +1.79 | +1.35 / +1.16 | 0.17 FAIL |
| H036 | turnaround: profit positive against a loss a year earlier, 60 sessions | book | B/20 | +1.11% | +2.43 | +2.69 / +1.52 | 0.17 FAIL |
| H100 | turn of the month in the Midcap 150 (last 2 and first 3 sessions) | book | B/26 | +0.55% | +2.44 | +1.94 / +2.50 | 0.15 FAIL |
| H076 | institutional accumulation plus momentum (rank sum) | book | B/9 | +0.67% | +2.02 | +nan / +2.16 | 0.14 FAIL (not positive in both halves) |
| H059 | exchange sought a clarification, 20 sessions (avoid) | avoid | C/33 | -0.84% | +3.21 | +3.08 / +1.52 | 0.14 FAIL |
| H033 | operating leverage (revenue growth minus expense growth) | book | B/20 | +0.80% | +2.15 | +2.62 / +0.61 | 0.12 FAIL |
| H055 | promoter pledge filing, 60 sessions (avoid) | avoid | B/33 | -0.47% | +2.16 | +1.20 / +2.49 | 0.10 FAIL |
| H017 | nearness to the 52-week low (avoid) | avoid | B/12 | -0.46% | +1.63 | +0.99 / +1.41 | 0.10 FAIL |
| H030 | revenue growth acceleration | book | B/20 | +0.70% | +1.84 | +1.61 / +1.12 | 0.10 FAIL |
| H043 | foreign institutional holding, largest decreases (avoid) | avoid | B/7 | +0.01% | -0.07 | +nan / -0.07 | 0.10 FAIL (not positive in both halves) |
| H054 | auditor resignation, 120 sessions (avoid) | avoid | B/33 | -0.80% | +2.06 | +2.15 / +1.05 | 0.10 FAIL |
| H075 | earnings plus price momentum (rank sum of SUE and 12-1 momentum) | book | B/9 | +0.48% | +1.41 | +1.21 / +0.87 | 0.10 FAIL |
| H097 | rupee weakening over 3 months: Nifty IT over Nifty 50 | book | B/26 | -0.02% | -0.09 | -0.23 / +0.10 | 0.10 FAIL (not positive in both halves) |
| H049 | credit rating upgrade, 60 sessions | book | B/33 | -0.03% | -0.11 | +0.07 / -0.33 | 0.10 FAIL (not positive in both halves) |
| H058 | bonus or split announced, 40 sessions | book | B/33 | -0.08% | -0.19 | -0.78 / +0.63 | 0.10 FAIL (not positive in both halves) |
| H046 | institutional accumulation (foreign + mutual fund + insurance) | book | B/7 | +0.34% | +1.15 | +nan / +1.17 | 0.10 FAIL (not positive in both halves) |
| H091 | variance risk premium high (VIX squared minus realised variance) | book | B/26 | -0.06% | -0.25 | -0.68 / +0.47 | 0.10 FAIL (not positive in both halves) |
| H032 | net margin level | book | B/20 | -0.07% | -0.29 | -0.17 / -0.19 | 0.10 FAIL (not positive in both halves) |
| H027 | block-deal buy in the last 40 sessions | book | B/3 | -0.07% | -0.36 | -0.71 / +0.55 | 0.09 FAIL (not positive in both halves) |
| H083 | sector nearness to the 52-week high, top 3 | book | B/7 | -0.09% | -0.39 | -0.67 / +0.33 | 0.09 FAIL (not positive in both halves) |
| H014 | one-month reversal in the 100 most liquid | book | B/10 | -0.12% | -0.44 | +0.45 / -1.33 | 0.09 FAIL (not positive in both halves) |
| H039 | early filers: results within 20 days of the quarter | book | B/20 | -0.17% | -0.57 | +1.16 / -2.15 | 0.09 FAIL (not positive in both halves) |
| H002 | nearness to the 52-week high, long only | book | A/29 | -0.45% | -1.65 | -0.91 / -0.99 | 0.08 FAIL (not positive in both halves) |
| H045 | retail shareholder count, fastest growth (avoid) | avoid | B/7 | +0.25% | -0.74 | +nan / -0.68 | 0.08 FAIL (not positive in both halves) |
| H086 | size rotation: Smallcap 250 or Nifty 50, whichever led over 6 months | book | B/7 | +0.17% | +0.83 | +1.14 / -0.02 | 0.07 FAIL (not positive in both halves) |
| H061 | legal or regulatory filing, 40 sessions (avoid) | avoid | C/33 | -0.59% | +2.92 | +3.03 / +0.70 | 0.07 FAIL |
| H037 | loss over the trailing four quarters (avoid) | avoid | B/20 | +0.54% | -1.03 | -1.56 / +0.50 | 0.06 FAIL (not positive in both halves) |
| H040 | promoter holding up 0.5 points or more in the quarter | book | B/7 | +0.24% | +0.62 | +nan / +0.61 | 0.05 FAIL (not positive in both halves) |
| H015 | high-volume one-month winners continue | book | B/10 | -0.51% | -1.28 | -0.63 / -1.26 | 0.05 FAIL (not positive in both halves) |
| H041 | promoter holding down 1 point or more (avoid) | avoid | B/7 | -0.22% | +0.58 | +nan / +0.64 | 0.05 FAIL (not positive in both halves) |
| H009 | frog-in-the-pan momentum, ranks 501-1000 (unseen universe) | book | A/29 | +0.57% | +1.16 | +1.67 / +0.23 | 0.04 FAIL |
| H080 | sector 6-1 momentum, top 3 | book | B/7 | -0.33% | -1.34 | -0.55 / -1.47 | 0.04 FAIL (not positive in both halves) |
| H099 | 10-year yield falling over 3 months: Nifty Bank over Nifty 50 | book | B/26 | -0.19% | -1.35 | -1.16 / -0.50 | 0.04 FAIL (not positive in both halves) |
| H029 | standardised unexpected earnings | book | A/20 | +0.27% | +0.94 | +0.16 / +1.15 | 0.04 FAIL |
| H085 | sector 6-1 momentum, only sectors above their 10-month average, else cash | book | B/7 | -0.41% | -1.56 | -0.74 / -1.22 | 0.03 FAIL (not positive in both halves) |
| H077 | class-A composite: momentum, 52-week high, continuous information, low volatility | book | B/9 | +0.17% | +0.50 | +0.91 / -0.31 | 0.02 FAIL (not positive in both halves) |
| H018 | nominal price under Rs 50 (lottery names, avoid) | avoid | C/12 | -0.87% | +1.61 | +1.79 / +0.63 | 0.02 FAIL |
| H067 | new entrant to the 100 most liquid | book | C/33 | -0.02% | -0.06 | -0.49 / +0.47 | 0.02 FAIL (not positive in both halves) |
| H024 | volume climax after a run-up (blow-off, avoid) | avoid | C/10 | +0.09% | -0.25 | +0.21 / -0.44 | 0.02 FAIL (not positive in both halves) |
| H042 | foreign institutional holding, largest increases | book | B/7 | +0.08% | +0.27 | +nan / +0.29 | 0.02 FAIL (not positive in both halves) |
| H064 | earnings call or investor presentation filed, 40 sessions | book | C/33 | -0.06% | -0.37 | +0.69 / -1.34 | 0.02 FAIL (not positive in both halves) |
| H065 | allotment or ESOP dilution, 40 sessions (avoid) | avoid | C/33 | -0.41% | +1.84 | +0.73 / +1.99 | 0.02 FAIL |
| H063 | two or more substantial-acquisition filings in 40 sessions | book | C/33 | -0.13% | -0.53 | -0.51 / -0.36 | 0.02 FAIL (not positive in both halves) |
| H060 | three or more press releases in 20 sessions | book | C/33 | -0.19% | -0.79 | -0.18 / -0.97 | 0.01 FAIL (not positive in both halves) |
| H079 | ridge rank model on the 160-feature library, refit yearly, walk-forward | book | C/8 | -0.41% | -0.95 | -0.80 / -0.57 | 0.01 FAIL (not positive in both halves) |
| H074 | rupee-beta rotation: names that gain from the dollar's 3-month trend | book | C/2 | -0.38% | -1.01 | -0.76 / -0.84 | 0.01 FAIL (not positive in both halves) |
| H021 | high delivery share (20-session average) | book | C/10 | +0.28% | +0.87 | +1.07 / +0.08 | 0.01 FAIL |
| H084 | sector low volatility, lowest 3 | book | B/7 | +0.03% | +0.12 | +0.24 / -0.13 | 0.01 FAIL (not positive in both halves) |
| H078 | gradient-boosted rank model on the 160-feature library, refit yearly, walk-forward | book | C/8 | +0.24% | +0.66 | +1.11 / -0.37 | 0.01 FAIL (not positive in both halves) |
| H092 | foreign investors' index-futures net long rising over 20 sessions | book | C/26 | +0.31% | +1.38 | +1.21 / +0.80 | 0.01 FAIL |
| H070 | futures open interest and price both up over 20 sessions | book | C/7 | -0.48% | -1.44 | -1.53 / -0.63 | 0.01 FAIL (not positive in both halves) |
| H019 | low volatility of turnover | book | C/12 | -0.41% | -1.53 | -0.43 / -1.95 | 0.01 FAIL (not positive in both halves) |
| H069 | highest put-call open-interest ratio (contrarian) | book | C/7 | -0.37% | -1.52 | -2.06 / +0.55 | 0.01 FAIL (not positive in both halves) |
| H081 | sector 12-1 momentum, top 3 | book | B/7 | +0.00% | +0.01 | +0.21 / -0.29 | 0.01 FAIL (not positive in both halves) |
| H013 | industry-relative weekly reversal, 5-session hold | book | B/10 | +0.02% | +0.25 | +0.03 / +0.33 | 0.01 FAIL |
| H090 | India VIX under 25 | book | C/26 | -0.29% | -1.74 | -1.58 / -1.23 | 0.00 FAIL (not positive in both halves) |
| H012 | one-month losers on low volume (liquidity provision) | book | B/10 | -0.85% | -2.69 | -1.52 / -1.79 | 0.00 FAIL (not positive in both halves) |
| H022 | rising delivery share with a rising price (accumulation) | book | C/10 | -0.48% | -2.02 | -1.74 / -0.79 | 0.00 FAIL (not positive in both halves) |
| H082 | sector one-month reversal, bottom 3 | book | C/7 | +0.07% | +0.23 | +0.86 / -1.07 | 0.00 FAIL (not positive in both halves) |
| H023 | delivery spike on a down day (absorption), 20 sessions | book | C/10 | -0.71% | -2.48 | -2.95 / -0.33 | 0.00 FAIL (not positive in both halves) |
| H050 | credit rating downgrade, 60 sessions (avoid) | avoid | B/33 | -0.32% | +0.83 | +1.50 / -0.73 | 0.00 FAIL (not positive in both halves) |
| H053 | management change, 40 sessions (avoid) | avoid | C/33 | -0.19% | +1.05 | +1.47 / +0.02 | 0.00 FAIL |
| H073 | oil-beta rotation: negative oil beta when Brent is falling, positive when rising | book | C/2 | -0.85% | -2.68 | -2.25 / -1.51 | 0.00 FAIL (not positive in both halves) |
| H025 | bulk-deal net buying over 20 sessions | book | B/3 | -1.23% | -3.37 | -2.45 / -1.70 | 0.00 FAIL (not positive in both halves) |
| H034 | four straight quarters of profit growth, ranked by momentum | book | B/20 | +0.07% | +0.23 | +0.03 / +0.30 | 0.00 FAIL |
| H038 | late filers: results more than 44 days after the quarter (avoid) | avoid | B/20 | -0.02% | +0.06 | +0.39 / -0.35 | 0.00 FAIL (not positive in both halves) |
| H047 | order win, 20 sessions | book | B/33 | +0.30% | +0.76 | -0.54 / +1.93 | 0.00 FAIL (not positive in both halves) |
| H051 | fund raising, 60 sessions (avoid) | avoid | A/33 | -0.11% | +0.49 | +1.36 / -1.06 | 0.00 FAIL (not positive in both halves) |
| H052 | acquisition announced, 40 sessions (avoid) | avoid | B/33 | -0.10% | +0.47 | -0.23 / +1.27 | 0.00 FAIL (not positive in both halves) |
| H056 | dividend announced, 20 sessions | book | C/33 | -0.71% | -3.64 | -1.92 / -2.81 | 0.00 FAIL (not positive in both halves) |
| H057 | three or more investor meets in 20 sessions | book | C/33 | +0.00% | +0.02 | +0.33 / -0.34 | 0.00 FAIL (not positive in both halves) |
| H066 | entered the F&O ban list in the last 10 sessions (avoid) | avoid | C/33 | -0.29% | +0.65 | +0.94 / -0.79 | 0.00 FAIL (not positive in both halves) |
| H068 | futures at the deepest discount to spot | book | B/7 | -1.52% | -4.87 | -3.51 / -4.06 | 0.00 FAIL (not positive in both halves) |
| H071 | volatility contraction within 10% of the 52-week high | book | C/28 | +0.21% | +0.69 | +0.82 / +0.02 | 0.00 FAIL |
| H072 | reclaimed the 200-day average in the last 20 sessions | book | C/28 | -1.02% | -3.39 | -4.05 / -0.96 | 0.00 FAIL (not positive in both halves) |
| H088 | breadth: more than half of stocks above their 200-day average | book | B/26 | +0.11% | +0.48 | +1.08 / -0.63 | 0.00 FAIL (not positive in both halves) |
| H093 | retail index-futures net long falling over 20 sessions (contrarian) | book | C/26 | +0.11% | +0.51 | +0.24 / +0.55 | 0.00 FAIL |
| H094 | Nifty put-call open-interest ratio above its 1-year median (contrarian) | book | C/26 | +0.06% | +0.29 | +0.08 / +0.40 | 0.00 FAIL |
| H095 | Nifty futures basis below its 1-year median (contrarian) | book | C/26 | +0.16% | +0.83 | +0.50 / +0.70 | 0.00 FAIL |
| H096 | breadth thrust: advancers over 65% of issues on a 10-day average | book | B/26 | +0.05% | +0.59 | +0.59 / +0.22 | 0.00 FAIL |
| H098 | copper rising over 3 months: Nifty Metal over Nifty 50 | book | B/26 | +0.19% | +0.66 | +0.54 / +0.47 | 0.00 FAIL |
| H062 | insolvency filing, 120 sessions (avoid) | | | | | | UNTESTABLE (only 0 periods with 5 or more names) |
