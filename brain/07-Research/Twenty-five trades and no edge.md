---
type: research
tags:
  - research
  - validation
date: 2026-09-21
---

# Twenty-five trades and no edge

Two independent measurements, both saying the same thing: **nothing here has been shown
to make money yet.**

## 1. The committee, replayed over 18 sessions

25 closed paper trades, 90-day horizons, entry and exit on adjusted closes, excess over
the Nifty net of costs.

| | |
|---|---|
| Hit rate | 52% - **indistinguishable from chance** (33-70% at 95%) |
| Mean excess | **+3.15%** per trade |
| Median | +1.03% · worst -18.9% · best +43.3% |

That looks positive until the control runs.

## 2. The control: the same universe, bought blindly

Every candidate those sessions showed the committee - companies that filed something
material that day - priced the same way, whether or not the committee chose it:

| | trades | hit | mean excess |
|---|---|---|---|
| Committee selected | 25 | 52% | +3.15% |
| **Universe bought blindly** | **46** | **57%** | **+5.00%** |
| **Committee contribution** | | | **&minus;1.85% per trade** |

**The positive return belongs to the universe, not to the judgement.** Buying everything
the sessions surfaced beat picking from it. On 25 trades this is directional rather than
conclusive - but it is the opposite of evidence for the committee, and it is exactly what
[[The control]] was built to catch.

## 3. Three pre-registered factors, through the firewall

Registered with their priors *before* testing ([[Alpha Validation Firewall]], Bonferroni
over every trial ever run):

| Hypothesis | Verdict | Why |
|---|---|---|
| 12-1 momentum | REJECT | deflated Sharpe 0.00 < 0.95 after 19 trials |
| Low volatility | REJECT | top quintile **-0.37%/period net of costs**; deflated Sharpe |
| Short-term reversal | REJECT | IC t=1.02 (p=0.31); **-0.78%/period net**, 81% turnover; IC sign held in only 67% of years |

The reversal rejection was predicted in its own pre-registration: "the hypothesis most
likely to be killed by the cost gate", and 81% turnover against a 0.71% round trip did
exactly that.

## What this does and does not mean

- It does **not** mean these effects are absent in Indian equities. It means that at our
  liquidity floor, our cost model, and this trial count, they do not clear the bar.
- The +3.15% is carried by a handful of names: +28.5%, +20.1%, +17.4%, all small or mid
  caps. [[Own-record scorecard]] slices by liquidity for exactly this reason - small caps
  show +12.8% over **five** trades, which is the shape of an illusion, not an edge.
- Every trade fell in a RISK_OFF regime, so [[Situational awareness]] has no variation to
  learn from yet. The regime feature is currently inert, not useless.

## The two lessons the record does support

[[Learning from losses]] confirmed two rules (>= 5 trades, >= 2% gap):

- **CREDIT_RATING prompts**: 4 of 5 lost, mean -3.2% against +4.7% elsewhere.
- **Mid-liquidity names**: -0.4% against +5.5% elsewhere.

Both now gate new trades of that kind, with their supporting decisions attached.

Related: [[First measured record]] · [[The unanswered question]] · [[Replay harness]]
