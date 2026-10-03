---
type: research
tags:
  - research
  - pre-registration
  - replication
  - volume
date: 2026-10-03
status: result
---

# Result of the volume family holdout

Predictions committed at `900d71d` before running.

**All three fail the holdout.** The confound test then showed why, and inverted my prediction about
which of them was clean.

## Test 1: the holdout kills all three

    candidate          pred       deep IC/t       micro IC/t   verdict
    CHAIKIN              +1  -0.0034/ -1.77   -0.0250/-13.32   FAIL
    ADL                  +1  -0.0019/ -0.99   +0.0037/ +2.34   FAIL
    WOBV                 -1  +0.0056/ +2.76   -0.0069/ -2.47   FAIL

`CHAIKIN` does not merely fail, it **reverses**: predicted positive, and -0.0250 at t = -13.32 in the
micro tier. `ADL` and `WOBV` each flip sign between the two held-out tiers - the same failure mode that
killed 47 of the 113 candidates in the sweep, now appearing in three that had survived it.

So the "third family" from [[Result of the ninety-indicator sweep]] does not exist in the form it was
found. Four tiers of agreement in ranks 1-500 did not carry to ranks 501-1500.

## Test 2: the confound was real, and it was in the levels

**Listing age is a signal in its own right**, as registered:

    listing_age    mid +0.0084/+3.75    small +0.0269/+17.64

An IC of +0.027 in the small tier is half of momentum's. Older Indian listings skew larger and more
established, and the size gradient running through every result in this project makes an age effect
inevitable. Cumulative volume is weaker (-0.0078 at t = -2.66 in small).

**And the candidate levels are substantially those confounds:**

    rank correlation      listing_age   cum_volume
    CHAIKIN                    -0.049       -0.248
    ADL                        -0.285       -0.528
    WOBV                       +0.485       +0.629

`WOBV`'s level is +0.63 correlated with cumulative volume and +0.49 with listing age. `ADL`'s is -0.53
with cumulative volume. These are running totals from the first bar, so their cross-sectional *level*
was ranking how long a name has been listed and how much it has ever traded.

## Where I was wrong, and it was backwards

I predicted **CHAIKIN would pass** the holdout because it is already a difference of two EMAs and
carries no level, and that **ADL and WOBV would be marginal** because they are raw levels. I also
predicted differencing would weaken ADL and WOBV and leave CHAIKIN unchanged.

The opposite happened on both counts:

    candidate    level ICs (large/next/mid/small)      differenced ICs
    CHAIKIN      +0.0138 +0.0272 +0.0069 +0.0118       -0.0096 +0.0040 -0.0089 -0.0036
    ADL          +0.0195 +0.0371 +0.0128 +0.0056       +0.0131 +0.0251 +0.0126 +0.0133
    WOBV         -0.0080 -0.0114 -0.0242 -0.0103       -0.0307 -0.0124 -0.0206 -0.0254

`CHAIKIN` **loses its replication when differenced** - the signs scatter. `ADL` and `WOBV` **keep
theirs, and WOBV strengthens**. And differencing does remove the confound: every differenced version
correlates near zero with listing age (+0.005, -0.042, +0.100).

So the level versions carried a real contaminant that the holdout detected, and the differenced
versions are decontaminated and still replicate in sample. That is not a rescue of the original claim -
the original claim failed - it is a different and narrower candidate.

## What is registered next, stated before running it

`ADL_D20` and `WOBV_D20` have been measured only on ranks 1-500. Their holdout has **not** been run,
and seeing their in-sample result puts me in exactly the position I was in with the three that just
failed. So, before looking:

    ADL_D20    predicted POSITIVE in both held-out tiers, |t| >= 2.0 in each
    WOBV_D20   predicted NEGATIVE in both held-out tiers, |t| >= 2.0 in each

**I expect them to fail.** Three candidates from the same family and the same price series just failed
this exact test, differencing changes the construction but not the underlying data, and the ICs
(0.012 to 0.031) are in the range where the holdout has already proved unforgiving. If they pass, that
is a genuine third family and I will have been wrong twice in a row about the same indicators, which
is itself worth knowing.

Either way the ICs do not clear an 80 bp round trip at any horizon, so this remains a question about
what is real rather than what is tradeable.

Related: [[PRE-REGISTERED holdout for the volume family]] ·
[[Result of the ninety-indicator sweep]] ·
[[Result of the pre-registered out-of-sample test]]
