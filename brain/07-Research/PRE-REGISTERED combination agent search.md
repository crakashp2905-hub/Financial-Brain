---
type: research
tags:
  - research
  - pre-registration
  - agent
  - technical
date: 2026-10-15
status: pre-registered (search phase may run now; validation locked until a trial is available)
---

# PRE-REGISTERED combination agent search

Written on 2026-10-15, before anything below is computed. The owner asked for an agent over the knowledge base that
works through combinations of chart, candle and indicator signals to find the best way to trade them, with a System
One model alongside it. This fixes how that search is done so that its result means something.

## The search space (fixed)

* **Names**: F&O stocks on days with a live near-month contract (`data/basis_contracts.parquet`), daily bars adjusted
  for corporate actions.
* **Triggers (51)**: the screener's vocabulary (`paper/signals.py`) - 31 events (side as a chart trader states it;
  "watch" events fire one way), 7 state flips taken both up and down (14), 3 signed events both ways (6).
* **Contexts (10)**: none; the name above / below its 200-session mean; the Nifty above / below its 200-session mean;
  the name's 60-session volatility in the top / bottom third of F&O names that day; that day's volume >= 2x / <= 0.5x
  its 20-session mean; delivery share rising (last 20 sessions above the 120 before - new data).
* **Sides**: long, short. **Horizons**: 1, 5, 20 sessions. Full grid: 51 x 10 x 2 x 3 = **3,060 combinations**.
* **Trade**: signal at the close of t, enter at the close of t+1, exit h sessions later; outcome = side x (stock -
  equal-weighted F&O) - 20 bp (stock future + basket hedge, as trial 7). One event per name, trigger and context per
  five sessions.

## The System One model's role (fixed)

Before any outcome is computed, the model is asked once per (trigger, context) pair which side and horizon the
pairing's mechanism implies - one of seven typed answers (long or short x 1, 5, 20 sessions, or "no mechanism") via
`llm/system1.ollama_decide`. Only its choice is tested, so the family shrinks from 3,060 to at most 510 - **the model
earns its place by lowering the multiple-testing penalty, not by predicting returns**. Its context is each
primitive's definition, the trader's claimed side and the family/source - **never a test result**: the knowledge
base's results span 2023-2026, the validation period, and giving them to the model would leak the answer. If the
local model server is busy with another client, the full grid is searched instead and the family is 3,060.

## Search phase (may run now)

Search sample: entry dates **2016-01-01 to 2022-12-31**. Every tested combination's monthly-mean outcome is stored.
Selection: >= 60 events over >= 24 months and monthly t >= 3.0 in the search sample; the ten highest t, at most one
per (trigger, context). The full list of what was tried is kept - its size is the family size.

## Validation phase (locked)

Entry dates **2023-01-01 to 2026-10-08**, the ten selected combinations only. A combination passes if its monthly t
>= 2 with the searched sign and it beats its G4 null (random F&O names at the same entry dates, same side and
horizon); the family is judged by Hansen's SPA and PBO over every tried combination's full-period series. **It is
not run until a trial is available** (2027Q1, or an owner amendment). Stated limit: single triggers were already
measured over 2022-26 in trial 7, so that period is not pristine for them; the combinations with contexts are new,
and paper trading after selection is the clean test.

## Predictions

* **Q1** the search phase finds 10 combinations at search-sample t >= 3 (with 510-3,060 tries, noise alone yields
  several).
* **Q2** at most two hold in validation; SPA does not reject.

Information, not advice.
