# Runbook: the order management system (ADR-0005)

For the owner. Read this before `fb live promote --to 1 --runbook-read`. Nothing below happens without you: Claude
does not set `FB_LIVE_ORDERS`, arm a stage, approve an order, reset the kill switch, or log in to Kite.

## The locks, and where each lives

| Lock | Where | Who opens it |
|---|---|---|
| `FB_LIVE_ORDERS=1` | your environment (never the repo, never `.env` committed) | you |
| rollout stage >= 1 | `data/live/stage.json`, via `fb live promote --to N --by <you>` | you, one step at a time, criteria enforced |
| strategy verdict | ADR-0004: CANDIDATE for TICKET, CONFIRMED (G5) for SMALL_AUTO and up | the evidence |
| kill switch clear | `data/live/killswitch.json` | anyone trips it; only you reset it |
| pre-trade gate | `execution/oms/risk_gate.py`, limits from the stage | the code |

`fb live status` prints every closed lock in words.

## Before stage 1 (TICKET)

1. **Regulatory.** Confirm with Zerodha the current SEBI retail-algo rules for API orders: whether your order rate needs
   a registered strategy / algo id, and the static-IP requirement for order APIs. Register the static IP on the Kite
   developer console. ADR-0005 does not establish compliance.
2. **Rehearse.** `fb live rehearse` during market hours, on 10 separate sessions: the OMS works the strategy's current
   targets (Rs 50,000) against `PaperBroker` priced by live Kite quotes, with the real ban list and the real gate.
   Each in-session run records a `REHEARSAL_SESSION`.
3. **Journal.** `fb live verify` must say the chain is intact.
4. `fb live promote --to 1 --by <you> --runbook-read`. It refuses with the unmet criteria listed if anything is short.

At TICKET, every order stops at `AWAITING_APPROVAL`; `fb live approve <id> --by <you>` sends it, `decline` drops it.
Unapproved tickets are cancelled at the session's end.

## Daily

* **Token.** Kite access tokens expire each morning (~06:00 IST). Log in with `scripts/kite_login.cmd` before 09:15.
  Without a token every placement fails closed (`KiteNotConfigured`), and `fb live status` shows it.
* **Ban list.** Read each morning from NSE (`fo_secban.csv`). If it cannot be read, **every** order is refused that
  day - by design; an order must not pass because a check could not run.
* **Do not trade by hand** in a symbol the OMS manages. Reconciliation compares the journal with Kite's book for those
  symbols, and a manual trade there is a mismatch: it halts the OMS and drops a stage.

## When something goes wrong

| Symptom | What the system does | What you do |
|---|---|---|
| Kite API down / timeouts on placement | the order goes to `UNKNOWN`; the next polls look it up by its tag; never re-sent | nothing, unless it stays UNKNOWN; check the Kite order book by tag (`fb...` 16-char id) |
| Order absent from Kite's book after 3 polls | `NOT_FOUND` (terminal); a retry is a new order with a new id | decide whether to resubmit |
| Token expired mid-session | placements fail closed; polling fails; reconciliation cannot run | log in again; `fb live status`; working orders stay at Kite and are seen on the next poll |
| Reconciliation mismatch | kill switch trips, open orders cancelled at Kite, stage drops one | find the cause (manual trade? partial fill missed?) before `fb live reset` |
| Journal chain broken | stage drops one, kill switch trips | do not reset until you know why a past row changed |
| Kill switch tripped | no new orders; open orders cancelled | positions are **not** flattened automatically. Flatten by hand in Kite if the cause makes holding wrong; otherwise hold |
| A session's loss > 5% of the stage's capital | demotion + kill switch | review before any reset |
| Network / power loss on this PC | nothing new is sent; working orders stay at Kite (DAY validity: cancelled by the exchange at the close) | on restart: `fb live status`, then a cycle reconciles |

## Stage criteria (fixed 2026-10-13, `execution/oms/rollout.py`)

* **TICKET**: strategy CANDIDATE+, 10 rehearsal sessions, journal intact, this runbook read.
* **SMALL_AUTO** (Rs 50,000 deployed max, Rs 10,000 an order): strategy **CONFIRMED** (12 out-of-sample rebalances,
  inside the backtest band), 20 sessions and 20 filled orders at TICKET, zero reconciliation mismatches, no kill-switch
  trip in the last 10 sessions, median shortfall at most 2x the simulator's prediction.
* **SCALED**: 60 sessions and 60 fills at SMALL_AUTO, the same clean record, shortfall at most 1.5x, live inside the
  backtest band, and limits you write yourself.

Promotion is never automatic. Demotion is.

## Where things are

`data/oms.duckdb` (live journal: orders + hash-chained events) · `data/oms_paper.duckdb` (rehearsals) ·
`data/live/stage.json` · `data/live/killswitch.json` · `fb live status|verify|kill|reset|approve|decline|promote|rehearse`
· `fb oos` (strategy out-of-sample status, measured fills, meta-label readiness).
