---
type: research
tags:
  - research
  - pre-registration
  - promoter-graph
  - fundamentals
date: 2026-10-03
status: result
---

# Result of the promoter graph and pledging tests

Predictions committed at `fb355f2` before running.

Three outcomes: fundamentals could not be tested at all, the holder filings found nothing that clears
the bar, and the promoter graph produced the strongest in-sample *and* out-of-sample numbers this
project has seen — which it cannot claim, for a reason registered in advance.

## Fundamentals: no test was possible

    company_fundamentals   30 rows (a current snapshot, not a panel)
    financial_results      20 rows
    filing_facts            1 row
    pit_observations        0 rows

Unchanged from the registration. There is nothing to test. A value or quality factor is not a weak
result in this project, it is an **absent** one, and
[[Naive momentum beats it, and the horizon was a fit]] already lists it as the hole in the baseline
hierarchy. Building the point-in-time store is the prerequisite.

## Holder filings: nothing clears the bar

Matched event study, control from the same session and the same momentum *and* recent-return bucket,
against a Bonferroni bar of **3.703** over the whole ledger including these eight trials:

    flag               h   predicted    effect        t   sessions    cases
    h_pledge           5     NEGATIVE   -0.21%    -2.57      2,238   10,129
    h_pledge          20     NEGATIVE   -0.25%    -1.49      2,238   10,121
    h_substantial      5        ~ZERO   +0.16%    +2.24      2,457   17,249
    h_substantial     20        ~ZERO   +0.50%    +3.31      2,457   17,228
    h_exempt           5        ~ZERO   +0.11%    +0.74      1,256    2,211
    h_exempt          20        ~ZERO   -0.34%    -1.04      1,255    2,209
    h_open_offer       5     POSITIVE   +0.50%    +1.04         71       82
    h_open_offer      20     POSITIVE   +1.21%    +1.44         71       82
    h_insider        5/20        ~ZERO   unusable: 21 matched sessions, below the 30 minimum

    clearing the bar: none      at nominal 5%: 3 of 8 (chance predicts 0.4)

**Promoter pledging is in the right direction and too weak to claim.** -0.21% at t = -2.57 over 10,129
cases. That is the one genuinely India-specific prior I said I held most strongly, and against the
multiplicity this project has already spent it does not clear. Directionally right, statistically
unproven.

**`h_substantial` surprised me and I was wrong about it.** I predicted near zero because acquisition
and reduction disclosures are pooled with no direction field, so they should cancel. It came back
**+0.50% at t = +3.31** at h20 — the strongest thing in the table, just under the bar. If that is real
it means the filing mix is lopsided rather than that substantial holders predict anything, and the
table cannot distinguish those.

**`h_open_offer` is unmeasurable as predicted** — 82 cases across eleven years, t of 1.04 and 1.44 with
the right sign and no power. `h_insider` could not be measured at all: 10,547 filings, but they
concentrate on too few sessions among liquid names to form 30 matched pairs.

## The promoter graph: the strongest numbers here, and I cannot claim them

Siblings always excluding self, so a group signal is never the name's own feature.

    signal              large            next             mid           small
    group_momentum         --   +0.0548/ +2.59  +0.0518/+13.48  +0.0526/+13.55   replicates
    group_stress           --   +0.0863/ +3.68  +0.0532/+12.89  +0.0533/+14.52   replicates
    group_dispersion       --              --   +0.0248/ +5.22  -0.0134/ -2.95   no

The `large` tier is unmeasurable: 24% universe coverage leaves too few group members in the top 50.

**Both pass the held-out tiers**, direction fixed, ranks 501-1500:

    signal                deep IC/t       micro IC/t   verdict
    group_momentum   +0.0270/  +8.79  +0.0682/ +20.45   PASS
    group_stress     +0.0555/ +17.90  +0.0812/ +23.84   PASS
    group_dispersion -0.0229/  -5.60  +0.0135/  +2.86   FAIL

With the size gradient every surviving effect here has shown, continuing past rank 500.

**And they are distinct**, which is where my prediction was wrong. I expected `group_momentum` to be
momentum wearing a graph and said a rank correlation above ~0.4 would prove it:

    signal              mom_12_1   dist_52w_high    vol_60
    group_momentum        +0.294          +0.212    -0.037
    group_stress          +0.204          +0.316    -0.183

0.29 and 0.32. Below the threshold I set in advance. Sibling momentum is not this name's momentum.

### Why none of that counts yet

`promoter_groups` has **no dates**. It is current membership with no `valid_from`, and the signal
applies today's group structure to 2016.

**The holdout does not fix this, and cannot.** Ranks 501-1500 are a different cross-section carrying
*the same contamination*: both samples use the same static membership table. A cross-sectional holdout
tests whether an effect is a tier-specific artifact. It is silent about a defect in how the signal is
constructed, and this one is in the construction.

The specific worry is survivorship in the graph itself. Today's membership is the set of group
relationships that **survived** — companies sold out of a group, demerged away, or delisted are not in
the table. Sibling momentum computed from survivors is survivor momentum, and survivors went up. With
161 undated groups there is no way here to bound how much of +0.068 that is.

So: **the strongest and most distinct signal this project has found, and the one it can say least
about.** That is an uncomfortable combination and it is the honest one.

### What would settle it

Dated membership — a `valid_from` / `valid_to` on `promoter_groups`, built the way
`index_constituents` was meant to be and the way `security_lineage` already handles ISIN successions.
Then the same test, point-in-time, with no re-selection. Until that exists this is a hypothesis with
good numbers attached, which is exactly the thing this project has learned to distrust.

Related: [[PRE-REGISTERED promoter graph and fundamentals]] ·
[[The event archive is real and it is not tradeable]] ·
[[Result of the pre-registered out-of-sample test]] ·
[[Naive momentum beats it, and the horizon was a fit]]
