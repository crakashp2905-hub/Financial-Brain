# Four-Node v3 family: ten strategies, one preregistered run (2026-11-18)

Preregistration: [PREREGISTRATION.md](PREREGISTRATION.md) (sha256 `e75af74f…`, written 14:35 UTC, before the run). v1 and v2 are unchanged.

**Verdict: all ten FAIL.** No strategy is positive in all three periods. Each one that looks good in one period turns negative in another, which is what noise looks like.

Costs per round trip: intraday 14.0 bps, delivery 32.4 bps (S01, S02).

| Strategy | Discovery n / net bps / t | Validation n / net bps / t / Holm p | Test n / net bps / t | Verdict |
|---|---|---|---|---|
| CONTROL_v2 | 293 / -2.2 / -0.36 | 132 / -2.1 / -0.26 / nan | 280 / -8.0 / -1.68 | CONTROL |
| S01_hold_1d | 291 / -11.9 / -1.20 | 132 / -18.3 / -1.61 / 1.00 | 279 / -6.9 / -0.79 | FAIL |
| S02_hold_3d | 291 / -10.2 / -1.02 | 132 / -18.6 / -1.64 / 1.00 | 278 / -5.8 / -0.66 | FAIL |
| S03_trend_15m | 119 / -0.7 / -0.07 | 72 / +9.7 / 1.06 / 1.00 | 116 / -18.8 / -2.91 | FAIL |
| S04_trend_daily | 126 / -12.4 / -1.44 | 66 / +18.1 / 1.79 / 0.36 | 122 / -15.3 / -2.25 | FAIL |
| S05_volume | 28 / -18.6 / -1.15 | 11 / -6.7 / -0.30 / 1.00 | 26 / -18.0 / -1.19 | FAIL |
| S06_morning | 3 / -37.3 / -0.48 | 1 / +0.0 / 0.00 / 1.00 | 2 / -32.9 / -0.57 | FAIL |
| S07_t15_be | 293 / -0.6 / -0.09 | 132 / +1.9 / 0.23 / 1.00 | 280 / -8.6 / -1.88 | FAIL |
| S08_vol_regime | 198 / -1.4 / -0.17 | 93 / +11.0 / 1.02 / 1.00 | 223 / -10.3 / -1.85 | FAIL |
| S09_orb_align | 76 / +19.8 / 1.41 | 42 / -9.1 / -0.74 / 1.00 | 96 / -16.3 / -2.13 | FAIL |
| S10_combo | 0 / +0.0 / 0.00 | 0 / +0.0 / 0.00 / 1.00 | 0 / +0.0 / 0.00 | FAIL |

## What it says

- **Holding overnight (S01, S02) is worse.** Delivery costs of about 32 bps swamp the extra move, and validation drops to about -18 bps per trade.
- **The trend and volatility filters (S03, S04, S08) and the 1.5 R breakeven exit (S07)** improve validation, but they lose in the test period (t from -1.9 to -2.9). No result is consistent across periods.
- **Opening-range alignment (S09)** is the best in discovery (+19.8 bps, t 1.41), then negative in validation and test.
- **S05, S06 and S10 hardly trade** (0-28 trades per period), so they are not real tests:
  - Volume of at least 1.5x average is rare on a breakout bar that also has to pass the v2 filters.
  - Nodes need four bars, so the earliest possible breakout is 10:15. The morning window therefore left only 10:15-11:30, and almost no v2 setups form that early.
  - S10 combines both and got no trades at all.
  - These are design flaws in the preregistration, recorded as such. Fixing them would be a new trial.
- The control has 293 discovery trades against v2's 294, because v3 blocks a new entry in a stock until the previous trade has exited, including across sessions. Its results are otherwise identical.

## Conclusion

Across 128 v1 variants, v2 and ten v3 changes, Four-Node shows no edge on NSE stocks that survives costs. The gross edge is real but small (about +6 to +12 bps per trade), and no filter or exit tried here improves it consistently. Four-Node stays research-only.
