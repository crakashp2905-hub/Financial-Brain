# Financial-Brain as a read-only agent

The existing investment committee (`committee/run.py`) already has price, filing and
governance analysts, a bull/bear debate and a deterministic chair. It can draft a decision
but cannot approve or execute one. The new `agents/brain.py` is a *supervisory coordinator*
over the saved trader-workstation opportunity snapshot, not another LLM that invents a
signal.

Run `fb terminal` to build `data/terminal/opportunities.json`, then `fb brain` to obtain
JSON verdicts. For an explicit archived input: `fb brain --snapshot PATH --kill-switch
PATH --limit 10`. The coordinator never logs in to Kite, calls a model, sends an alert,
approves a decision or places an order. If the kill-switch file is absent, it abstains.

| Specialist | Reads | Fail-closed condition |
|---|---|---|
| Data | snapshot date and instrument identity | missing, future or stale input |
| Evidence | tier and traceable evidence ids | missing ids; non-forward tiers stay research-only |
| Risk | persisted kill switch, size and stop | absent/invalid switch, HALT, missing sizing/invalidation |
| Economics | net, portfolio-adjusted alpha and costs | missing/nonfinite economics; nonpositive alpha means no new risk |
| Adversary | invalidation rule, liquidity and distribution | any of these missing |

The coordinator emits `ABSTAIN`, `NO_NEW_RISK`, `RESEARCH_WATCH`, or `HUMAN_REVIEW`.
`HUMAN_REVIEW` is *not* BUY: a forward evidence label in a JSON file is not itself proof
of calibration, profitability, or current tradability. Every specialist result and an
input digest accompany the verdict. The digest detects accidental input changes; it does
not independently attest when the snapshot was made.

## Next integration gates

1. The opportunity snapshot now has a versioned hash manifest and fail-closed readers.
   Extend it with per-source timestamps, model/strategy versions, data-quality results
   and quote age. Calendar-day freshness remains a conservative stopgap.
2. Replace a bare forward-tier label with a signed/verified link to a completed,
   independent paper cohort and the registered evidence standard. Until then, even
   `HUMAN_REVIEW` must remain informational.
3. Capture and score prospective human decisions and rejected alternatives against cash
   and simple benchmarks after measured costs; keep stock, intraday and options cohorts
   separate.
4. Benchmark coordinator latency and failure modes before placing it in the market-hours
   path. Specialist agents should have bounded compute and no broker credentials.
5. Keep human approval and broker execution in separate, explicitly governed systems.
   Do not add an agent-callable switch that bypasses those gates.

This layer improves process quality and abstention. It does not establish a trading edge.

Run `fb agent-eval` to check the versioned synthetic safety cases in
`agents/eval_cases.v1.json`. The suite verifies fail-closed decisions, untrusted-text
handling, and the absence of execution permission. It is a regression test, not a
benchmark of Indian financial facts, model citations, numerical accuracy, or returns.
