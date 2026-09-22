---
type: component
tags:
  - component
  - system-one
  - model
status: candidate
updated: 2026-09-22
---

# Laya

A non-autoregressive System One engine (Convai Innovations, **Apache-2.0**, weights on
Hugging Face). [[Typed decisions]] reserved this slot from the beginning - "jev / cloud
slot in behind the same interface when available" - and Laya is the open one.

| | |
|---|---|
| `laya` | ModernBERT-large, 421M, English, 512 ctx |
| `laya-multilingual` | mmBERT-base, 322M, 100+ languages, 1024 ctx |
| primitives | **choice** (one of a set), **score** (ordinal), **noul** (binary) |
| trained by | RL against strictly proper scoring rules |

Only `choice` is used here: it is the shape every decision in this system already has.

## Why it is worth wiring in

**It cannot be instructed by its input.** This matters more than the speed. Every filing,
headline and PDF this system reads is untrusted text, and the standing rule is that it is
framed as data and never followed. With a generative model that rule is a *mitigation* -
the model could in principle obey text inside the document, and the wrapper is what
discourages it. An encoder with no decoder has nowhere to obey **to**: there is no
continuation to hijack, only a distribution over a label set fixed before the document was
read. See [[Untrusted text boundary]].

**Speed, measured twice.** On this machine (Intel Iris Xe, no CUDA, torch on 4 threads),
over twelve real filing headlines with a warm-up call discarded:

| checkpoint | median | min | max |
|---|---|---|---|
| `laya` (421M) | **561ms** | 477 | 858 |
| `laya-multilingual` (322M) | **395ms** | 222 | 8074 |

Against `llama3.1:8b`'s ~2.4s for the same typed decision, roughly **four to six times
faster** - and the multilingual median falls inside the 193-464ms CPU band the README
quotes.

The number took two passes to get right, and that is the part worth keeping. The vendor's
figure was first repeated here without measuring. It was then "corrected" to 1.1-2.8s -
but those were the **first calls after load**, timing lazy initialisation rather than
steady state. Quoting an unmeasured number and measuring the wrong thing are the same
mistake in different clothes, and both are cheap to make in whichever direction suits the
argument at hand. See [[Beating the median is not an edge]].

The multilingual maximum of 8s is one outlier in twelve and is not explained; until it is,
the English checkpoint is the one to benchmark on.

## What it is not exempt from

Its own README is direct: **both checkpoints ship over-confident, mean ECE 0.466, falling
to 0.081 after refitting temperature on held-out data.** That is the same lesson this
project learned the hard way when an 89.1% in-sample threshold became **65.4% held out**
with 23.6% wrong-when-accepted.

So Laya enters as a **candidate**, not a route:

1. registered in `models.toml` with `backend = "laya"` - eligible for benchmarking;
2. `fb models bench` measures it on our own labelled filings;
3. per-label thresholds fitted on the fit set, **judged on the holdout**;
4. only then does [[Model router]] route anything to it.

Reputation is not a qualification, and neither is a benchmark someone else ran. Every
stored Laya decision carries `uncalibrated: True` so no later reader mistakes a raw
probability for a calibrated one. [[Calibration belongs to a prompt]] applies unchanged:
Laya has no prompt in the generative sense, but it has an instruction and a criteria map,
and changing either voids the threshold.

## Known limits, taken as refusals

* **High cardinality.** Published accuracy falls to 0.425 on 77 labels. `laya_decide`
  refuses above 20 - nothing here needs more, and a refusal beats a quietly worse answer.
* **Ordinal scores** are its weakest primitive (SST-5: 37.2%). Unused.
* **Typed-decisions zero-shot** is near chance on the base checkpoints; the README says
  fine-tuning is required for production. Untested here, and not assumed.

Related: [[Typed decisions]] · [[Model router]] · [[Calibration belongs to a prompt]] ·
[[Untrusted text boundary]] · [[Licensing ledger]]
