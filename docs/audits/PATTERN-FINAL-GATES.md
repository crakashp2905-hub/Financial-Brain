# Chart patterns: final gates, scorecard and residuals

Engineering status is computed from `docs/audits/chart-patterns-completion.json`. Market edge is never claimed.

| Rating | Value |
|---|---|
| ENGINEERING_TRADER_READY | Only when every row is implemented, verified, or not-applicable with evidence |
| PROVEN_MARKET_EDGE | UNKNOWN. Needs genuine forward evidence this checkout cannot produce |

## Residual gates that code cannot close

Recorded here, not marked done:

- Independent timestamp custody and exchange data rights (OWNER / LICENSE).
- Live broker fill calibration and real market-time forward cohorts (DATA_TIME).
- Windows CI execution of the full matrix. Only the local Windows run was done here.
- Cross-platform event-hash equality on Linux.
- Integration with the shared OMS risk gate. The architecture boundary deliberately forbids it from pattern code.

## Scorecard criteria

- Causality: prefix invariance holds for every detector; future perturbation leaves earlier formations unchanged.
- Correctness: 72 catalogue rows carry a truthful status; implemented rows have positive and negative fixtures.
- Research integrity: protected records unchanged; book statistics stay conditional; unconfirmed formations retained.
- Paper economics: next-session entries, modelled costs, labelled fills, ledger identity, risk codes.
- Usability: offline demo, escaped report, banners, empty-data mode, no network.
- Learning safety: immature labels rejected; promotion refused without forward evidence.

Neither a subjective "10/10" nor a profit claim appears here.
