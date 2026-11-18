# Four-Node v3 family: ten strategies, preregistered 2026-11-18 before any v3 run

Every v3 strategy is **FourNode_India_v2 plus one change**. v1 and v2 are not modified. v2 runs
beside the family as the control; v2 is deterministic, so its rerun is not a new look.

Diagnosis from v2: gross +11.7 bps per trade against a 14.0 bps round trip, and about half the
trades end at the 15:15 flatten without reaching the target. Each change below is a guess, made
before seeing any result, at either carrying the move further or picking better setups.

| Id | Change from v2 | Hypothesis |
|---|---|---|
| S01 | Hold overnight: if not stopped, exit at the close of the **next** session (delivery costs) | the move continues past 15:15 |
| S02 | Hold up to **3** sessions, exit at the third close (delivery costs) | same, over a longer horizon |
| S03 | 15-minute trend filter: buy only above SMMA200, sell only below it | trade with the trend |
| S04 | Daily trend filter: buy only if yesterday's daily close > daily SMMA20, sell only if below (point-in-time) | higher-timeframe agreement |
| S05 | Volume confirmation: breakout bar volume ≥ 1.5 × its 20-bar average | real breakouts have volume |
| S06 | Morning entries only: breakout bar between 09:45 and 11:30 | more session left to reach the target |
| S07 | Target 1.5 R, stop to breakeven at +1 R, no other trailing | more target hits |
| S08 | Volatility regime: ATR% above its trailing 60-session median | more movement relative to a fixed cost |
| S09 | Opening-range alignment: buy only above the 09:15–09:45 high, sell only below its low | confluence with the opening range |
| S10 | S03 + S05 + S06 together | combined filters |

Costs: intraday round trip for S03–S10 and the control; delivery round trip (STT on both legs) for
S01 and S02. Both come from `CostModel`, mega bucket, with impact.

## Periods and pass bar (same as v2)

- DISCOVERY 2016-2021, VALIDATION 2022-2023, TEST 2024 onward. Each strategy is run once.
- The 2024+ test period has already been read twice in this programme (the top v1 variant, and v2).
  The v3 strategies are different rules, but this re-use is disclosed here.
- A strategy **PASSES** only if all of these hold:
  1. mean net > 0 in DISCOVERY;
  2. mean net > 0 in VALIDATION, with a Holm-corrected one-sided p < 0.05 across the 10;
  3. mean net > 0 in TEST with t ≥ 2;
  4. at least 100 trades in each period.
- A PASS means SHADOW_ONLY paper candidate, nothing more. ADR-0007 is unchanged. Nothing is retuned
  after the results are seen; a change means v4.
- Logged in `docs/trials/dynamic/ledger.jsonl` as one look, family size 10.
