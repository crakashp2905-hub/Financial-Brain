---
type: concept
tags:
  - concept
  - decision-engine
  - phase3
updated: 2026-09-22
---

# Expected value and sizing

"The model is 82% confident" is not a reason to buy anything. It says nothing about how
much is made when right, how much is lost when wrong, or how much to put on. Three numbers
replace it:

| | |
|---|---|
| **expected value** | Σ probability × return over named scenarios |
| **downside** | the probability-weighted loss, and the worst case |
| **size** | risk budget ÷ the distance to invalidation |

## The sizing rule inverts the usual order

Most systems go **confidence → size**, which sizes up exactly when the model is most sure
and therefore most wrong in the tail. This goes:

> **thesis → invalidation → size**

The position is whatever loses no more than the risk budget (0.5% of the book) if the
invalidation level trades. A thesis whose invalidation sits far away gets a small position;
a tight one can carry more. The position is capped at 10% of the book regardless.

Nothing here predicts anything. It is arithmetic over numbers the thesis already had to
state, and its purpose is to make a bad trade **fail the sum rather than fail in the
market**.

## What a decision must survive

At `RISK_REVIEWED`, if a decision states scenarios they are checked:

* the probabilities sum to one;
* **at least one scenario loses money** - a distribution where nothing goes wrong is not a
  distribution, it is a hope;
* the expected value is positive.

A trade that cannot survive its own sum does not reach paper. The scenarios themselves come
from [[Historical analogues]], not from a model's opinion.

## A worked slip worth keeping

The specification this was built from gave an example: 35% × +25%, 45% × +10%, 20% × −18%,
and reported the expected value as **8.15%**. The sum is **9.65%**. The direction of the
error is the instructive part - it made the trade look *worse* than it was, but an
arithmetic slip in the other direction is what makes a losing trade look acceptable. The
test now asserts the computed sum, not the quoted one.

Related: [[Historical analogues]] · [[Decision quality]] · [[Decision contract]] ·
[[Investment Committee]]
