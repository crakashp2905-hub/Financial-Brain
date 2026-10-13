---
type: research
tags:
  - research
  - pre-registration
  - fundamentals
date: 2026-10-13
status: pre-registered (out of sample only)
---

# PRE-REGISTERED earnings surprise drift out of sample

Written on 2026-10-13 under [[ADR-0004 one evidence standard]]. **Out-of-sample only**: standardised unexpected
earnings were already scored in-sample on the PIT-2019 XBRL store (strongest in the small tier, t 3.4, below the
bar), so an in-sample test now would re-use data already seen. No trial is spent until the out-of-sample test runs.

## Mechanism

Post-earnings-announcement drift: prices under-react to earnings news (Ball & Brown 1968; Bernard & Thomas 1989),
the most replicated accounting anomaly. The data is orthogonal to every price signal tested here.

## The test, fixed

* **Data**: quarterly profit after tax as first reported (`as_known_on` - the row known at the filing's
  dissemination time, never a later restatement), consolidated where filed, else standalone.
* **SUE**: (PAT of the quarter - PAT of the same quarter a year earlier) / standard deviation of that difference
  over the previous 8 quarters (at least 6 available).
* **Cohorts**: each calendar month's filings among the 500 most liquid names; long the top fifth of SUE, entered at
  the close two sessions after the filing, held 40 sessions; net of per-name delivery round trips; measured
  against the equal-weighted 500 over the same sessions; cohort return = mean over its names.
* **Out-of-sample data**: filings disseminated after 2024-12-31 (the last quarter the store holds). Results since
  then are filed through NSE "Integrated Filing", whose endpoint is not yet known - **blocked on the owner**.

## The bar

G3 and G4 cannot split an out-of-sample window that short into halves, so the out-of-sample test is: at least 12
monthly cohorts, net mean positive with t >= 2, and above the 95th percentile of the same statistic over random
same-size cohorts from the same months' filers. Pass -> CANDIDATE, then the usual paper record.

## Prediction

* **X3a** positive net mean; **X3b** t below 2 at the first 12 cohorts (about 30% chance of passing).

Information, not advice.
