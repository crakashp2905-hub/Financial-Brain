# ADR-0007: A dynamic evidence standard

Status: **ACCEPTED 2026-11-16** by the owner ("Accept, register P3"), unchanged from the proposal written the same
day at the owner's instruction ("if it's too strict then we have to provide thresholds which are dynamic";
"optimise according to the realistic edge"). Implemented in `evaluation/dynamic_standard.py`
(`tests/test_dynamic_standard.py`).

**What acceptance does and does not change.** The dynamic tier is now the research verdict for registrations made
under this ADR, and the prior class and family size are required fields of every such registration. The broker
path is **not** rewired by this acceptance: `execution/oms/rollout.py` and `services/trading.verdict` still read
ADR-0004 and ADR-0006 verdicts. Letting a dynamic tier reach stage TICKET is a separate, reviewed code change that
needs its own explicit owner decision. Nothing here arms a stage, raises a limit or spends an ADR-0004 trial.

## The problem

ADR-0004's G3 asks for |t| >= 2 in each six-year half, which needs an annual Sharpe above about 0.8. Durable
equity edges run 0.3 to 0.7. Simulated on a true Sharpe of 0.5 over 10.5 years, a fixed "t >= 2 in both halves"
bar passes about one such edge in twenty, so the bar rejects realistic edges and false ones alike. Fourteen of
fourteen 2026Q4 trials failed it. ADR-0006 lowered the bar once by hand (full-sample t >= 2.5); nobody qualified.

A fixed number is wrong in both directions. It is too strict for a published, mechanism-backed effect that
replicates in four liquidity tiers, and too lenient for the best of forty chart rules.

## The decision

Keep what protects against self-deception, and make the significance bar a function of what was known before the
data was scored.

Unchanged from ADR-0004: pre-registration (G1), net of per-name costs (G2), the matched random-book null (G4),
forward confirmation (G5), and the rule that a losing context becomes a hypothesis, never a fix.

Replaced: G3's fixed bar and the binary verdict.

1. **Evidence** is the full-sample t on the registered net series, autocorrelation-robust (Newey-West, automatic
   lag; the smaller of the robust and the plain t is used). The net mean must be positive in **both** halves.
2. **Family.** The one-sided p-value is Sidak-adjusted by the number of variants of the idea examined in this
   project. Every extra look raises the bar for the whole family. This is what makes the threshold dynamic in the
   direction that matters, and it replaces a hard quarterly cap as the brake on searching: looks are still
   logged (`docs/trials/dynamic/ledger.jsonl`), and each one is paid for by every sibling.
3. **Prior class**, fixed at registration:

   | class | meaning | prior odds against |
   |---|---|---|
   | A | published across markets, an economic mechanism, **and** replicated in independent tiers here | 3 : 1 |
   | B | a mechanism or a publication, not both, or not replicated here | 9 : 1 |
   | C | found by search; technical, chart, candle and data-mined rules | 49 : 1 |

4. **Posterior.** P(edge is real) from the minimum Bayes factor exp(-z²/2) (Harvey & Liu 2015) and the prior odds.
5. **Tiers** are thresholds on the posterior, so the t a strategy needs is not a constant:

   | tier | posterior | what it permits (once accepted) |
   |---|---|---|
   | CANDIDATE | >= 0.90, and G4 passed | what ADR-0004 CANDIDATE permits |
   | CANDIDATE_SMALL | >= 0.70 | what ADR-0006 permits: stage TICKET only, every order approved by the owner |
   | INCUBATE | >= 0.50 | forward paper with priority; no capital |
   | FAIL | below | nothing |

   Required full-sample t, by prior class and family size (CANDIDATE_SMALL / CANDIDATE):

   | family | A | B | C |
   |---|---|---|---|
   | 1 | 1.97 / 2.57 | 2.47 / 2.96 | 3.08 / 3.49 |
   | 5 | 2.58 / 3.08 | 3.00 / 3.43 | 3.53 / 3.90 |
   | 20 | 3.03 / 3.47 | 3.40 / 3.79 | 3.88 / 4.22 |
   | 100 | 3.49 / 3.88 | 3.81 / 4.17 | 4.26 / 4.57 |

   A missing null caps the tier at CANDIDATE_SMALL; a failed null caps it at INCUBATE.
6. **Live evidence moves the grade both ways.** The live record's z is combined with the backtest's
   (information-weighted), with no multiplicity haircut because it was not searched. A live z at or below -1.64
   demotes one tier. ADR-0004's band and FAILED_EARLY rules still apply.
7. **Size follows evidence.** Capital fraction = quarter-Kelly on the *shrunk* Sharpe (observed Sharpe x posterior
   x 0.5 backtest haircut), capped at 10% of risk capital for CANDIDATE_SMALL and 30% for CANDIDATE. ADR-0006's
   rupee limits (Rs 25,000 deployed, Rs 10,000 an order) stay as they are until the owner changes them.

## Measured error rates (simulation, 10.5 years monthly, `dynamic_standard.calibrate`)

| true annual Sharpe | family | class A reaches CANDIDATE_SMALL | class B | class C |
|---|---|---|---|---|
| 0 (no edge) | 1 | 2.0% | 0.7% | 0.1% |
| 0 (no edge), best of 10 | 10 | 2.2% | 0.8% | 0.2% |
| 0.5 | 1 | 35% | 18% | 6% |
| 0.7 | 1 | 59% | 40% | 19% |

The false-pass rate does not rise when the best of ten null series is picked, which is the property the family
adjustment exists for. Power for a Sharpe-0.5 class-A edge is about a third; under ADR-0004's bar it is about 5%.

## What it says about the record (post hoc; see `docs/trials/dynamic/RESULTS-2026-11-16.md`)

118 past experiments re-scored from their saved series: 114 FAIL, and the rest are one book and three avoid
filters. The priors and family sizes were assigned after the results were known, so this is a ranking of where
forward evidence should be collected, not a set of verdicts. Under this ADR a verdict needs a registration made
*before* the series is scored.

## Still the owner's to decide

* Whether `services/trading.verdict` and `rollout.py` should read the dynamic tier (see above).
* Whether the family adjustment replaces the quarterly trial cap or sits beside it.
* The prior odds and tier thresholds above are judgement calls. They were fixed before any series was scored
  under the ADR in force; changing them later to admit a strategy is the trap ADR-0006 already names.
