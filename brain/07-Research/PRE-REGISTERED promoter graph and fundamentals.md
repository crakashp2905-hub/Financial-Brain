---
type: research
tags:
  - research
  - pre-registration
  - promoter-graph
  - fundamentals
date: 2026-10-03
status: registered, not yet run
---

# PRE-REGISTERED: the promoter graph, pledging, and why fundamentals cannot be tested

Committed before running. Result in a separate note.

## Fundamentals cannot be tested, and that is a data fact rather than a methodology choice

    company_fundamentals        30 rows   (symbol, fetched_on, ratios) - a CURRENT snapshot
    financial_results           20 rows
    filing_facts                 1 row
    pit_observations             0 rows   - the point-in-time store is empty

There is no fundamentals panel. Thirty rows of ratios **fetched on one date** cannot produce a
cross-sectional signal across 2,537 sessions, and applying today's price-to-book to 2016 would be the
same class of error as the `ICHIMOKU_CHIKOU` lookahead in
[[Result of the ninety-indicator sweep]] - except deliberate, and across the whole panel.

So no fundamental test is registered here. What is registered is the **absence**: a value, quality or
earnings-revision signal is not weak evidence in this project, it is **no** evidence, and
[[Naive momentum beats it, and the horizon was a fit]] already names it as the gap in the baseline
hierarchy. Building the PIT fundamentals store is the prerequisite, not the test.

## What the promoter graph actually is

    161 groups, 447 members, 445 companies
    147 groups have a member reaching the features panel
    group sizes: 105 of size 2, 33 of 3, 8 of 4, 7 of 5, 4 of 6, and a handful up to 17

Roughly 400 lineages out of a ~1,700-name liquid universe - about **24% coverage**. The tiers in
[[Four edges that replicate, and the chart patterns that do work]] will therefore be thin, and the
`large` and `next` tiers may not reach the session minimum at all.

**The graph has no dates.** `promoter_groups` is current membership with no `valid_from`. Applying
today's structure to 2016 is a **structure lookahead**: demergers, stake sales and group exits are
exactly the events that would make a group signal look predictive. Group membership is far more stable
than a financial ratio, so this is a weaker contamination than the fundamentals case - but it is real,
it biases toward finding an effect, and no result here can be clean of it.

### Signals and predictions

Computed per lineage per session, siblings always **excluding self**:

    group_momentum     mean mom_12_1 of siblings        predicted POSITIVE
    group_stress       mean drawdown-from-52w-high of siblings   predicted POSITIVE
                       (sibling distress -> this name falls, so a HIGHER sibling
                        dist_52w_high, i.e. nearer the high, predicts better returns)
    group_dispersion   cross-sibling spread of ret_20d  predicted no sign

**I predict `group_momentum` has a positive IC and fails the distinctness test.** Siblings in a
promoter group are correlated by construction - common ownership, often common sector - so their mean
momentum will partly be this name's own momentum. The test that matters is its rank correlation with
`mom_12_1`; if that exceeds ~0.4 it is momentum wearing a graph.

**I predict `group_stress` is the one with a chance**, because contagion is a claim about *other*
companies' trouble predicting this one's, which own-momentum cannot express.

## Holder filings: the part that is genuinely point-in-time

    143,058 rows, 2015-01 to 2026-09, 4,906 ISINs, every row carrying business_date

    SUBSTANTIAL  95,737      PLEDGE  27,491      INSIDER  10,547
    EXEMPT        8,974   OPEN_OFFER     309

These are sparse per-name events, so the instrument is the **matched event study** from
[[The event archive is real and it is not tradeable]] - control drawn from the same session and the
same momentum *and* recent-return bucket - not a cross-sectional IC. The candle patterns taught that
lesson: a flag firing on under 2% of rows has nothing to correlate within a tier.

### A limitation that caps what any of this can show

`holder_filings` records **that a filing of a kind occurred**, with no direction and no quantity. There
is no buy/sell flag and no share count. So:

    PLEDGE       predicted NEGATIVE at h5 and h20. Promoter pledging is the canonical
                 Indian distress signal and the one genuinely India-specific prior here.
                 This is the prediction I hold most strongly.
    INSIDER      predicted NEAR ZERO. Insider filings include purchases and disposals and
                 this table cannot tell them apart, so the two should cancel. A large
                 effect either way would mean the mix is lopsided, not that insiders predict.
    SUBSTANTIAL  predicted NEAR ZERO, same reason - acquisition and reduction disclosures
                 are pooled.
    OPEN_OFFER   predicted POSITIVE (takeover premium) but at 309 rows across eleven years
                 I expect it to be UNMEASURABLE and reported as such.

**Pass** = the predicted sign against the Bonferroni bar over the whole ledger including these trials,
not against 1.96.

## What a pass would and would not mean

Even a clean PLEDGE result is a **short** or **avoid** signal in a long-only book, which
[[The event archive is real and it is not tradeable]] already showed costs more in forgone momentum
than it saves - and that exclusion filter failed its walk-forward. So a significant pledge effect
would be a real finding about Indian markets and still not a strategy.

Registered 2026-10-03, before running. Result: [[Result of the promoter graph and pledging tests]].

Related: [[The event archive is real and it is not tradeable]] ·
[[Four edges that replicate, and the chart patterns that do work]] ·
[[Result of the volume family holdout]]
