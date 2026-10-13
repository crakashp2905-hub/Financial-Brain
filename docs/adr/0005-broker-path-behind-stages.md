# ADR-0005: A broker path, closed until a strategy is confirmed and a human arms a stage

Status: accepted 2026-10-13 (owner instruction: build the order management system, reconciliation, pre-trade risk
gate, broker kill switch, audit log, staged rollout and execution measurement)

Supersedes the structural claim in [[Kite readiness]] and `providers/kite_data.py` that no order endpoint exists
anywhere in the package. That claim still holds for `providers/`: the read-only client keeps its GET allowlist and
no order code is added to it. The order path lives in `execution/oms/` and nowhere else.

## Context

The owner asked for the execution layer to be built. The research record says no strategy has an edge that would
justify real money (ADR-0004: N2 is an avoid filter; M1 failed G3). Both are true, so the layer is built now and
gated so that building it changes nothing until the evidence does.

## Decision

1. **Default is no broker.** The OMS runs against `PaperBroker` unless every lock below is open.
2. **Five independent locks before `KiteBroker` sends anything**, each checked on every order:
   * `FB_LIVE_ORDERS=1` in the environment (the owner sets it; nothing in the repo does);
   * a rollout stage >= 1 written by a human actor (`fb live arm --by <name>`; refused for `agent:` actors);
   * the strategy's verdict: stage 1 needs `CANDIDATE` or better, stages 2-3 need `CONFIRMED` (ADR-0004 G5);
   * the broker kill switch not tripped;
   * the pre-trade risk gate passing.
3. **Stages** (`execution/oms/rollout.py`), each with pass/fail criteria written down before it starts:
   * 0 `OFF`: paper only;
   * 1 `TICKET`: every order waits for a human `fb live approve <id>`;
   * 2 `SMALL_AUTO`: automatic submission, total deployed <= Rs 50,000, one order <= Rs 10,000;
   * 3 `SCALED`: limits the owner writes into the stage file.
   Promotion is **never automatic** - only a human can move a stage up. Demotion is automatic: any failure criterion
   drops one stage and trips the kill switch.
4. **Limit orders only**, priced inside a band around the last price; no market orders from the API.
5. **Every state change is journaled exactly once** in `data/oms.duckdb` (hash-chained, idempotency key per event),
   never in the research database.
6. **The model cannot approve.** Approvals, arming, promotion and kill-switch resets refuse any actor that starts with
   `agent:`, as `decisions/record.py` already does for `HUMAN_APPROVED`.

## What Claude will and will not do

Claude builds and tests this code against fakes and `PaperBroker`. Claude does not set `FB_LIVE_ORDERS`, arm a stage,
approve an order, enter credentials, or run the OMS against the live broker. Those are the owner's actions.

## Regulatory note (to verify with the broker before stage 1)

SEBI's retail algorithmic-trading framework routes API orders through the broker with a strategy/algo identifier and
order-rate thresholds; Kite requires a static IP registered for order APIs. Confirm the current rules with Zerodha
before arming stage 1 - this ADR does not establish compliance.
