# The learning loop

The ask: losing trades should become lessons, and the same mistake should not be made twice. The risk in doing that automatically is old
and specific: with a few dozen trades, "avoid what lost" fits noise, and a rule fitted to noise then loses money on its own. ADR-0004
says it in one line: *a losing context becomes a hypothesis, never a fix*. This is the loop that follows from it: it learns from every
mistake it can see, says how sure it is, measures its own error rate, and changes nothing without a person.

```
 CAPTURE ─► DIAGNOSE ─► HYPOTHESISE & VERIFY ─► DECIDE ─► ACT ─► MEASURE
 context     autopsy      nominate, then confirm   draft,     paper      v1 vs v2 on the same days,
 at entry    incidents    once on fresh sessions   register,  live-v2    decay watch, guard recurrence
 per trade   + guards     (frozen windows)         approve    cohort
 and per                  calibrated: false        (a person)
 run                      confirmation measured
```

Everything runs from one nightly command, `fb learn run`, in the 18:45 refresh. Everything it learns is written to an append-only ledger
(`data/lessons.duckdb`, backed up with the other irreplaceable evidence). Nothing in the loop writes to the live runner, the OMS or a
live rule.

## 1. Capture

Every intraday paper trade stores the situation it was taken in: the market's move since the open, the daily trend, the name's gap, the
opening range, the minute, the quoted spread, the VWAP, the liquidity bucket. `learning/features.py` defines each dimension **once**, in
two forms - for a table of closed trades and for one decision as it happens - and a test feeds both the same random inputs and requires
identical answers. That matters because a rule found on a pandas column and applied by different code at run time drifts silently.
Every dimension is known at the decision time; what is only known after the fill (slippage, the cost of walking the book) is an outcome,
not context.

## 2. Diagnose

**Trade autopsy** (`fb learn autopsy`). Each closed trade is rebuilt from the quote archive - every poll from entry to the close, the same
prices the engine acted on - in units of its own risk, and each loss gets exactly one primary cause: *cost-dominated* (the signal made
money, costs ate it), *noise stop* (stopped out, then price went on to reach 1R in the trade's favour), *gave back* (was 1R up and still
lost), *never worked* (stopped, never 0.25R up), *stopped*, *time loss*. The thresholds are fixed constants, not fitted. This needs no
statistics and shows where the money leaks before any pattern is confirmable.

**Incidents** (`fb learn incidents`). Operational mistakes are mistakes. Between 6 October and 5 November the live session was lost on
seven mornings: five missing Kite logins, one network failure, one closed console. Every lost session, failed refresh step and failed
pre-open check is an incident with a cause from a small fixed vocabulary, and every cause maps to a **guard**: when it was introduced and
whether it is *active*. A guard that exists in code but is not scheduled is not a guard, and the report says so (UNGUARDED, GUARD NOT
ACTIVE, GUARDED, with recurrences after the guard counted).

**Your own trading** (`fb journal`). The owner's habit review (overtrading, averaging down, selling winners early, chasing events,
ignoring one's own stop) is read as one more source of lessons and shown beside the rest.

## 3. Hypothesise and verify: the protocol (`learning/protocol.py`)

A lesson claims: *"ORB trades taken against the market are worse than the other ORB trades the same day, and lose."* Both halves matter.
"Worse than the rest of that day" makes it a cause and not a bystander: when one real effect exists every cell that overlaps it inherits a
loss, and without this the first calibration run confirmed 29 lessons from one planted effect. "And loses" makes skipping it worth anything.

1. **Nominate.** Each night every cell (strategy x dimension x value) with at least 12 sessions and 30 trades is examined on all sessions so
   far: per-day contrast against the strategy's other trades that day, one-sided t, Benjamini-Hochberg across the cells of its family
   (execution, market, setup, calendar), p <= 0.01, own loss of at least 10 bp. Overlapping candidates are pruned to the strongest. A
   nomination **freezes** the cell's definition and its discovery window.
2. **Confirm, once, on fresh sessions.** Only sessions *after* the nomination count. After 20 of them and 30 trades in the cell it is decided
   exactly once: CONFIRMED if the new contrast is negative with p <= 0.05 / (nominations at the same look) and the cell's own net is
   negative, else REFUTED. The sample size that triggers the decision depends on how many trades arrived, never on how they came out, so
   the p-value means what it says. A cell that never collects the trades EXPIRES after 60 sessions.
3. **Terminal.** Confirmed, refuted and expired are final. A refuted cell can only be nominated again from data after its decision: the same
   numbers are never tested twice.
4. **Decay.** A standing lesson keeps being watched; if its last 30 sessions no longer show the loss it is marked DECAYING (an alert; nothing
   is retired by machine).

The first version re-split all sessions every night, so yesterday's confirmation data became tomorrow's discovery data and every night was
another look at the same numbers. That design had no error rate anyone could state. This one does, and **measures it**.

### Calibration (`fb learn calibrate`)

A loop that cannot state its own error rate is a story generator. Calibration runs the *same* protocol code over simulated histories built
by resampling the real ledger's days (so cell sizes and trades per day are realistic) and reports: the **false-confirmation rate** with the
outcomes replaced by noise with no relation to any context (demeaned, shuffled within each day, sign-flipped by day), and the **power** and
time-to-confirm with a loss of 20, 40 and 60 bp planted in one cell. The thresholds in `Params` were set from the null, which carries no
effects and so cannot overfit one. The latest result is in `fb learn status`.

## 4. Decide: a person, and only a person

The loop can nominate, confirm, draft and measure. It cannot activate anything.

* `fb learn draft <lesson>` writes a pre-registration for the skip rule (hypothesis, evidence, the exact rule, the forward test, what would show
  it is wrong, the limits). Read it, edit it, commit it as `brain/07-Research/PRE-REGISTERED skip rule SK-nnnn.md`.
* `fb learn register <lesson> --prereg ... --from <date>` adds the rule as **SHADOW**: no effect on trading. The loader refuses a rule whose
  pre-registration is missing or was written after a result (the git audit).
* **Shadow.** Each night every closed live trade from `from` onward is asked *would the rule have declined you?* v2 is v1 minus those trades.
  After 60 sessions and 30 declined trades the verdict is PASSING (the declined trades lose, t <= -2, and v2 beats v1), FAILING (it removes
  winners) or INCONCLUSIVE.
* `fb learn approve SK-nnnn --actor "Your Name"` puts a PASSING rule in force. It needs a person at an interactive terminal and a typed
  confirmation, so a script, a scheduled job or an agent cannot answer it; software-looking names are refused; the approval carries the hash
  of the rule as written (editing the rule voids it), expires in 180 days (the rule silently returns to shadow), and is recorded in the ledger
  with the approver's name.

## 5. Act: the live-v2 cohort

An ACTIVE rule becomes entry filters on the built-in strategies in a **separate paper cohort**, `live-v2` (`fb paper-intraday --v2`,
`scripts/paper_intraday_v2.cmd`), with its own ledger beside the unchanged live-v1. Register its task (`scripts/register_paper_v2_task.ps1`)
only when a rule is active; with none active it exits at once. The filter sees the decision-time context, may decline the entry, and a
declined entry ends the strategy for that name today exactly as a taken one does. `tests/test_learning_conformance.py` runs v1 and v2 over the
same simulated sessions, for every dimension and value, and requires that v2's trades are v1's trades **minus exactly the declined entries,
identical in every field**. The engine refactor that made this possible was checked against the committed engine on random sessions
(identical trades and skips).

## 6. Measure

* `fb learn ab`: v1 and v2 on the **same days** - which entries only v1 took (the declined ones), which only v2 took (exposure a skip freed,
  the one thing the shadow cannot see), and the paired per-day difference with its t.
* The decay watch above, and the operational recurrence per cause before and after its guard.
* The digest (Fridays, one alert per ISO week) and the terminal panel summarise it.

## What you do

| when | what |
|---|---|
| never, nightly | `fb learn run` runs in the refresh (after the close, because it reads the paper ledger) and sends alerts |
| an alert "a loss pattern repeated on new sessions" | `fb learn status`, read the lesson; if it is worth testing, `fb learn draft` |
| after committing the pre-registration | `fb learn register ...` |
| an alert "skip rule PASSING" | `fb learn approve SK-nnnn --actor "Your Name"` at a terminal, then register the v2 task once |
| an alert "may have stopped holding" | review; `fb learn retire SK-nnnn --actor ... --reason ...` if it has |
| the weekly digest | skim it; it names what needs you |
| an operational cause shows GUARD NOT ACTIVE | register the task it names |

## Commands

`fb learn run | status | autopsy | incidents | ab | digest | calibrate | draft | register | approve | retire`; `fb paper-intraday --v2`.

## Limits, stated

* **Small samples stay small.** One strategy and a few dozen live sessions cannot support a rule today, by design. The loop is built to be
  ready, and honest, when the sample is; until then it reports leaks and incidents, which need no statistics.
* **Slots freed by a skip.** The engine stops opening trades at five times the account. A skipped trade can free room for one that v1 never took;
  the shadow cannot credit it, the cohort can, and `ab` shows it.
* **Paper is not real.** Fills are modelled from the quoted book; a paper result is not a real-money result. Real orders remain behind the OMS
  locks (ADR-0005) and nothing here touches them.
* **Budget.** Whether a shadow evaluation spends the quarterly trial budget (ADR-0004) is the owner's call. The code requires only that a
  rule was registered before it was judged.
* **Skip rules only.** A change to how a trade is entered or exited is a different strategy: it needs its own pre-registered cohort.

## Files

`src/financial_brain/learning/`: `features.py` (the dimensions), `protocol.py` (nominate, confirm, decay), `calibrate.py` (the audit),
`autopsy.py`, `incidents.py`, `personal.py`, `store.py` (the ledger), `shadow.py`, `governance.py` (approval), `activation.py` (the v2 cohort and
the A/B), `pipeline.py` (a night), `report.py` (status, panel, digest). Engine hooks: `paper/intraday.py` (`StrategyRule.base`/`skip`,
`decision_context`), `paper/intraday_runner.py` (`day_context_fn`).
