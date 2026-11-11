# Learning from mistakes without learning noise

The owner's ask: losing trades should become lessons, and the same mistake should not be made twice. The risk in doing that
automatically is old and specific: with a few dozen trades, "avoid what lost" fits noise, and a rule fitted to noise then loses
money on its own. ADR-0004 says it in one line: *a losing context becomes a hypothesis, never a fix*. This is the loop that
follows from it, what is automated, and where a person decides.

```
 closed paper trades ──► loss-review ──► PROPOSED ──► loss-mine ──► CONFIRMED ──► owner: pre-register a skip rule
   (context recorded)     (nightly)      hypothesis    (nightly)     (or FAILED_      │
                                                                      CONFIRMATION)   ▼
                                                                          docs/shadow/skip-rules.v2.json
                                                                                      │  shadow (nightly)
                                                                                      ▼
                                                        SHADOW ─► PASSING / FAILING / INCONCLUSIVE ─► owner decides
```

| step | what it does | changes a rule? |
|---|---|---|
| **record** | every intraday paper trade stores the situation it was taken in: market move since the open, daily trend, gap, opening-range width, entry minute | no |
| **loss-review** (`fb loss-review --propose`) | groups closed trades by that context; a group that loses at t <= -2 (one observation per day) is written as a PROPOSED hypothesis, with the number of groups examined beside it | no |
| **loss-mine** (`fb loss-mine --apply`) | splits the sessions in time, 60% to find and 40% to confirm. Flagged in the early sessions at t <= -3 *and* surviving Benjamini-Hochberg control over every group examined; CONFIRMED only if still negative at t <= -2 on the later sessions. Otherwise FAILED_CONFIRMATION, which keeps a lucky pattern dead. Needs 40 sessions; says how many it has until then | no |
| **alert** | when a hypothesis becomes CONFIRMED: "needs your decision, nothing changed" | no |
| **owner decision** | pre-register a skip rule (`brain/07-Research/PRE-REGISTERED <name>.md`, committed first) and add it to `docs/shadow/skip-rules.v2.json` with an `active_from` date | the owner's act |
| **shadow** (`fb shadow --apply`) | for each registered rule, asks of every closed live trade from `active_from` onward "would it have been skipped?". v2 is v1 minus those trades: no second runner, no second Kite poll, live-v1 untouched. Verdict after 60 sessions and 30 skipped trades | no |
| **promotion** | PASSING means the trades the rule would have skipped lost, out of sample, at t <= -2 and v2 beat v1. It is a request for a decision. Changing a live rule is a new pre-registered version made by the owner | the owner's act |

The swing decisions have their own gate (`decisions/postmortem.py`, `fb lessons`): every closed paper decision is analysed on what
was knowable at entry and a confirmed lesson blocks the next such trade, citing the trades behind it. It now also has to survive
false-discovery control across every group examined (q <= 0.10), so a noisy group that looked bad is not a rule.

## What stops a mistake from being "learned" wrongly

* A pattern must be found on one set of sessions and hold on another it never saw.
* The number of groups examined is printed and corrected for; about one group in forty looks bad by chance.
* A hypothesis can only move PROPOSED, CONFIRMED, FAILED_CONFIRMATION by machine. REGISTERED, SHADOW and anything later is the
  owner's and is never overwritten.
* A skip rule is refused by the shadow loader unless its pre-registration is committed and the git audit says it came before any
  result. It is judged only on sessions after `active_from`.
* Nothing in this loop writes to the live runner, the OMS or a live rule.

## Where it stands (2026-11-12)

14 live sessions and 6 replay days of closed trades exist, so the miner is waiting (it needs 40 live sessions, so 26 more; at the pace
so far, 14 sessions in five weeks with holidays and missed logins, that is about January). Two older PROPOSED hypotheses stand: "strategy = ORB" (found on 7 sessions; live ORB has
since made money) and "direction = long" (253 trades, 10 days, 12 groups examined). They are exactly the kind the miner is there to
settle: neither is a rule, and neither will become one unless it repeats on later sessions.

## Limits, stated

* **Skip rules only.** A rule that changes how a trade is entered or exited is a different strategy; it needs its own runner as a
  paper cohort (`paper/intraday_runner.py` already separates cohorts by name and rule version), not this counterfactual.
* **Slots freed by a skip.** The engine stops opening trades when open exposure reaches 5x the account. A skipped trade could have
  freed room for one that was never taken, and that trade is not in the ledger, so v2 cannot credit it. The report counts the sessions
  on which the cap was within 10% of binding so the size of the omission is visible.
* **Budget.** Whether a shadow evaluation counts toward the quarterly trial budget (ADR-0004) is the owner's call. The code only
  requires that the rule was registered before it was judged.
* **Small samples stay small.** One strategy (ORB) and 14 sessions cannot support a rule today, by design. The point of the loop is
  that it is ready, and honest, when the sample is.
