# Chart patterns: architecture

Offline, paper-only research module under `src/financial_brain/patterns/`. It never imports the OMS,
a broker client or the live paper engine. A test enforces this (`tests/test_pattern_offline.py`).

```text
Local bars (Bars, quarantine_rows)          marketdata.py: timestamps (IST/UTC), calendar, identity, freshness
  -> causal primitives                      pivots.py, streaming.py, geometry.py (tolerances, levels, ambiguity)
  -> detectors                              detectors.py: rectangle, triangles/wedges, flags, double, head-and-shoulders
  -> formation states                       Formation: CANDIDATE -> CONFIRMED | FAILED, as_of(t) views
  -> catalogue and candles                  catalogue.py (72 rows), candles.py (wraps library c_* features), inventory.py
  -> append-only events                     spec.py (PatternSpec, PatternEvent, lifecycle), events_store.py (as_of replay)
  -> study and controls                     study.py (spec hashes, budget guard, purged splits, FDR, placebo)
  -> paper economics                        paper.py (intents, costs, fills, ledger identity, risk codes)
  -> ranking and reports                    ranking.py (four separate scores), report.py (escaped HTML, JSON export)
  -> learning and agent                     learning.py (immutable registry, labels), agent.py (read-only tools)
  -> entry points                           demo.py (offline demo, sign-off), cli.py (scan, list, explain, paper-demo, audit)
```

Key invariants:

- Causality: `detect(bars[:t+1])` equals the full run restricted to what was knowable at `t`
  (`tests/test_pattern_detectors.py`). Pivot-based detectors read one full-run pivot list and filter
  by `known_at_index`, which is proven equal to the prefix computation.
- Two clocks: candidates and confirmations are stored separately; failed candidates are kept.
- Evidence: detector output is `DESCRIPTIVE`. Only forward paper or external confirmation can promote anything,
  and the learning registry refuses any other tier.
- Protected records: `docs/audits/protected-baseline.json` pins the source notes, catalogue and trial manifests.
