---
type: research
tags:
  - research
  - pre-registration
  - out-of-sample
date: 2026-10-13
status: pre-registered (out of sample only)
---

# PRE-REGISTERED out-of-sample batch three

Written on 2026-10-13, after [[Result of edges batch three]]. Both ideas below were **chosen after seeing in-sample
results**, so no in-sample number can make either a CANDIDATE; neither spends a trial. They are judged only on data
after the cutoff (2026-10-13), by `fb oos`, under G5 of [[ADR-0004 one evidence standard]]: 12 rebalances with a
positive net mean, and an early fail after three month ends below the in-sample band. Paper-traded in the zoo from
2026-10-14.

## EAPR - short into scheduled results

The mirror of EAP: every F&O name whose results board meeting is intimated 3-15 sessions ahead is **sold** in stock
futures at the intimation's close and bought back at the close of the session after the meeting; the Nifty is bought
for the same notional. Costs 14 bp a round trip (stock future + Nifty), charged on top of EAP's own (so the in-sample
band is -EAP - 28 bp per event). Zoo: `edge_eapr_announcement_short`. Hold for the band: 10 sessions.

*Why it might be real*: holders who know the date de-risk into it; analysts' pre-results notes skew cautious in India.
*Why it might not*: it is the sign-flip of a failed test, the oldest way to manufacture a result.

## COMBO - momentum book and rich-basis carry, equal capital

Half the capital in M1 ([[PRE-REGISTERED cost-aware momentum book]]) and half in C1 (`carry_basis_10`), fixed - no
re-weighting by realised performance. The two were the only sleeves positive in both halves; the combination is
monitored from the two zoo records (`edge_m1_momentum_book`, `carry_basis_10`) as 0.5 x each net excess. Band: the
two sleeves' 2022-26 per-session moments combined assuming independence (carry is market-neutral; stated, not
measured).

## What would make either tradeable

G5 CONFIRMED, then the owner's decision to arm stage TICKET (ADR-0005), where every order still needs the owner's
approval. Nothing about either changes because of its paper record.

Information, not advice.
