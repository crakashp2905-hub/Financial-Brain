---
type: research
tags:
  - research
  - news
  - nlp
date: 2026-10-04
status: result
---

# Result of the news classifier on filings

Predictions committed at `a6a9155` before training. 605,950 filing events, top-1,000 names, split
train 2016-21 (250k) / valid 2022 (66k) / **test 2023-26 (290k), scored once**.

    P1  text adds >= 0.01 AUC to strength        FAIL   -0.014  (0.644 text-free -> 0.630 with text)
    P2  text-free control >= 0.58                PASS   0.644 test / 0.628 valid
    P3  direction misses the bar and 30 bp       PASS*  +19.1 bp, t = +2.58 over 861 dates (bar 3.70)
    P4  same-day reaction dwarfs the drift       PASS   mean |reaction| 190 bp vs mean AR3 +49 bp

*P3 predicted a failure and got one; it was not a clean zero - the sign is right and t is 2.6.

## What it says

* **Words made strength worse.** The text model lost 0.014 AUC out of sample. Its top features are
  names and dates ("jhawar", "panda", "idbi bank", "00 november", "indiabulls"): it memorised issuers and
  calendar quirks that do not carry into 2023-26. Stripping the company name removed the obvious leak and
  left the group names and the filing templates.
* **Strength is volatility.** Event type plus trailing volatility and turnover reach AUC 0.644 on their
  own. A headline adds nothing to *which filing will be followed by a big three-day move* that the stock's
  own volatility did not already say.
* **Direction is close to nothing.** Test AUC 0.528. The top-decile minus bottom-decile spread is
  +19 bp over three sessions at t = 2.58: right sign, below the Bonferroni bar and below a ~30 bp round
  trip. Note that the top decile's +48.8 bp is simply the sample mean (+48.6 bp); the whole spread is the
  bottom decile *underperforming*, which cannot be harvested long-only.
* **The information is in the reaction, not the drift.** Mean absolute same-day reaction is 190 bp; the
  three sessions after it average +49 bp. The market prices filings by the close.

## What this does not settle

Headlines are company-sourced and terse ("As enclosed", "Outcome of Board Meeting"). A classifier reading
the *body* of the filing, or reading independent press coverage, could do what this one cannot. Neither
is measurable here: bodies are PDFs and the press archive has 2 articles. This tests headline text only.

Related: [[The event archive is real and it is not tradeable]] ·
[[PRE-REGISTERED news classifier on filings]]
