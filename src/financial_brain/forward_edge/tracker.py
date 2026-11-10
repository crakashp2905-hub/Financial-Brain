"""Status of the 152 forward-edge items (FE-0001 .. FE-0152) with evidence and blockers.

Statuses (strict):

* ``DONE``        - an executable test in ``tests/`` covers the item's pass criterion *within the stated scope*.
* ``PARTIAL``     - some code and tests exist; the item's pass criterion is not fully met (gap recorded).
* ``BLOCKED``     - needs an external provider, owner action or real-world evidence that cannot be produced offline.
                    Recorded as a blocker. Never simulated.
* ``NOT_STARTED`` - no implementation yet.

``DONE`` is only accepted when every listed test function exists in the test suite (checked by
``tests/test_forward_edge_tracker.py``). Item text lives in ``docs/forward-edge/fe-items.json``, extracted from the
master plan. ``render()`` writes ``docs/forward-edge/TRACKER.md``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Entry:
    status: str
    tests: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    note: str = ""


F = "test_forward_edge_f00.py"
G = "test_forward_edge_f01.py"

BASELINE_TESTS = ("test_baseline_records_git_head_sources_and_missing_prerequisites",
                  "test_baseline_write_is_deterministic_for_fixed_clock")
TRIAL_TESTS = ("test_q4_2026_protected_budget_is_exhausted_at_13_of_13", "test_protected_records_match_pinned_digests",
               "test_retrospective_edit_of_the_trial_ledger_is_detected",
               "test_new_protected_trial_refused_when_budget_is_spent",
               "test_new_q4_trial_needs_budget_amendment_as_well_as_trial_approval",
               "test_agent_cannot_authorize_a_protected_trial")
MODE_TESTS = ("test_live_cash_is_unavailable_by_default", "test_flipping_the_build_flag_alone_does_not_enable_cash",
              "test_live_cash_with_human_approval_and_compliance_is_granted_only_when_all_gates_pass",
              "test_broker_dryrun_needs_verified_compliance",
              "test_only_live_paper_emits_paper_fills_and_no_mode_sends_physical_orders",
              "test_session_mode_is_immutable")
APPROVAL_TESTS = ("test_software_actor_identifiers_cannot_approve",
                  "test_non_human_actor_type_is_rejected_even_with_a_human_looking_name",
                  "test_expired_future_and_stale_approvals_are_rejected",
                  "test_wrong_kind_or_subject_and_naive_clock_are_rejected")
AUDIT_TESTS = ("test_audit_is_red_on_this_repository_and_never_green_without_custody",
               "test_audit_turns_red_when_a_protected_record_changes", "test_audit_cli_exit_code_is_nonzero_while_blocked")
CUSTODY_CLASS = ("test_no_receipt_is_the_honest_local_only_fallback", "test_independent_receipt_from_a_pinned_root_verifies",
                 "test_self_signed_local_receipt_cannot_count_as_independent", "test_forged_signature_is_unverified",
                 "test_altered_payload_and_replayed_receipt_are_conflicted", "test_false_clock_receipt_before_decision_is_conflicted",
                 "test_late_receipt_is_late_and_decision_after_entry_is_conflicted", "test_default_provider_refuses_to_fabricate_a_receipt")
OMS_TESTS = ("test_rejected_fills_leave_state_bit_identical", "test_fill_that_would_overspend_is_refused_without_mutation",
             "test_cannot_mark_filled_with_open_quantity", "test_full_buy_reduces_buying_power",
             "test_partial_fill_releases_only_its_proportion_of_reservation", "test_ten_thousand_random_transitions_reconcile",
             "test_duplicate_postback_is_one_economic_fill", "test_same_provider_id_with_different_content_is_refused",
             "test_stale_acknowledgement_after_fill_is_refused", "test_fill_racing_a_cancel_is_one_fill_and_the_cancel_resolves_after")
EVENT_TESTS = ("test_failed_action_is_unknown_not_acknowledged", "test_unknown_is_never_replayed_without_reconciliation",
               "test_reconciliation_that_found_the_order_closes_it_without_resend",
               "test_resolution_requires_a_named_reconciler_and_an_unknown_key",
               "test_unknown_state_survives_checkpoint_and_resume")
PILOT_TESTS = ("test_pilot_win_and_loss_reconcile_oms_cash_to_ledger", "test_pilot_repeat_run_posts_no_phantom_fill",
               "test_pilot_failed_dispatch_is_unknown_and_posts_nothing")

BLK_BROKER = "BLK-BROKER: no broker account, API credentials or Kite Connect sandbox (none exist offline)"
BLK_DATA = "BLK-DATA: licensed real-time quote/depth entitlement not available (plan: no sandbox; free plan excludes quotes)"
BLK_TSA = "BLK-TSA: no independent timestamp provider or pinned trust root configured (docs/forward-edge/trust-roots.json absent)"
BLK_COMPLIANCE = "BLK-COMPLIANCE: SEBI retail-algo permissions, exchange algo IDs and static IP need broker/owner evidence"
BLK_MARKET = "BLK-MARKET: needs observed live NSE/BSE sessions (cannot be produced offline)"
BLK_GOV = "BLK-GOVERNANCE: 2026Q4 protected budget 13/13 spent; new hypotheses need an ADR amendment or 2027Q1"
BLK_OWNER = "BLK-OWNER: requires the owner's own decision or signature"
BLK_GITHUB = "BLK-GITHUB: branch main is not protected (GitHub API 404 'Branch not protected', read-only check 2026-11-10)"
BLK_LINUX = "BLK-LINUX: no Linux runner here (WSL not installed); CI on Linux needs a push, not run by this session"
BLK_TIME = "BLK-TIME: needs elapsed calendar time of scheduled sessions (>=60 independent sessions as a floor)"

OVERRIDES: dict[str, Entry] = {
    # ---- F00 ----
    "FE-0001": Entry("DONE", BASELINE_TESTS, (), "docs/forward-edge/baseline-2026-11-10.json captured with gh PR list."),
    "FE-0002": Entry("DONE", TRIAL_TESTS, (), "2026Q4 ledger 13/13; pins guard retrospective edits; no ledger write made."),
    "FE-0003": Entry("NOT_STARTED", (), (), "Immutable eligibility registry belongs to F02 (FE-0022..0024)."),
    "FE-0004": Entry("DONE", ("test_charter_hash_is_deterministic_and_sensitive_to_every_field",
                              "test_revision_is_a_new_version_with_a_new_clock",
                              "test_malformed_charters_are_rejected"), (), "Hash over canonical JSON; no signing key."),
    "FE-0005": Entry("DONE", MODE_TESTS, (), "Five modes; grant only via resolve_mode; flag alone insufficient."),
    "FE-0006": Entry("DONE", MODE_TESTS, (), "LIVE_CASH refused by build policy; needs compliance and human approval."),
    "FE-0007": Entry("BLOCKED", (), (BLK_GITHUB,), "Owner must enable branch protection and required checks."),
    "FE-0008": Entry("DONE", APPROVAL_TESTS, (), "Human-only, scoped, expiring approvals. Real owner approvals not yet recorded."),
    "FE-0009": Entry("NOT_STARTED", (), (), "Disposable DuckDB ledger harness not built."),
    "FE-0010": Entry("DONE", AUDIT_TESTS, (BLK_TSA, BLK_LINUX), "python -m financial_brain.forward_edge.audit; currently RED."),
    # ---- F01 ----
    "FE-0011": Entry("PARTIAL", EVENT_TESTS[:2], (), "In-memory outbox: a failed action is UNKNOWN, never acknowledged. No durable store."),
    "FE-0012": Entry("NOT_STARTED", (), (), "Durable write-ahead log and monotonic IDs not built."),
    "FE-0013": Entry("PARTIAL", EVENT_TESTS, (BLK_BROKER,), "Kernel and OMS block replay of UNKNOWN; no broker reconciliation."),
    "FE-0014": Entry("DONE", OMS_TESTS[:3], (), "Validate-before-mutate; bit-identical after rejection."),
    "FE-0015": Entry("PARTIAL", ("test_full_buy_reduces_buying_power",) + PILOT_TESTS[:1], (),
                     "OMS cash moves with fills and the pilot reconciles to its ledger. Other engines keep separate ledgers."),
    "FE-0016": Entry("DONE", ("test_partial_fill_releases_only_its_proportion_of_reservation",
                              "test_ten_thousand_random_transitions_reconcile"), (), "10,000 seeded transitions reconcile."),
    "FE-0017": Entry("DONE", PILOT_TESTS, (), "Repeat run returns DUPLICATE_SUPPRESSED with no ledger posting."),
    "FE-0018": Entry("PARTIAL", ("test_pilot_win_and_loss_reconcile_oms_cash_to_ledger",), (),
                     "Buy and sell in the pilot balance. Dividends, splits, futures variation and option settlement are not implemented."),
    "FE-0019": Entry("DONE", OMS_TESTS[6:], (), "One economic fill per provider fill ID; stale ACK and cancel race covered."),
    "FE-0020": Entry("NOT_STARTED", (), (), "Power-loss injection needs the durable journal (FE-0011/0012)."),
    "FE-0021": Entry("NOT_STARTED", (), (), "Journal rollover, compaction and backup restore not built."),
    # ---- F02 ----
    "FE-0022": Entry("NOT_STARTED", (), (), "Strategy inventory: next wave."),
    "FE-0023": Entry("NOT_STARTED", (), (), "StrategySpec schema not built."),
    "FE-0024": Entry("NOT_STARTED", (), (), "Status taxonomy not implemented in code."),
    "FE-0025": Entry("NOT_STARTED", (), (), "Benchmark registry not built."),
    "FE-0026": Entry("NOT_STARTED", (), (), "Shadow adapters not built."),
    "FE-0027": Entry("BLOCKED", (), (BLK_GOV,),
                     "Only recorded M1 trial 'M1 cost-aware momentum book' (docs/trials/ledger.jsonl, 2026-10-13) verdict FAIL at G3."),
    "FE-0028": Entry("NOT_STARTED", (), (), "Virtual subaccounts not built."),
    "FE-0029": Entry("NOT_STARTED", (), (), "Retirement and version handoff not built."),
    "FE-0030": Entry("NOT_STARTED", (), (), "Null and placebo controllers not built."),
    "FE-0031": Entry("NOT_STARTED", (), (), "Correlation deduplication not built."),
    "FE-0032": Entry("NOT_STARTED", (), (), "Selection gate on shadow results not built."),
    # ---- F03 ----
    "FE-0033": Entry("NOT_STARTED", (), (), "Normalized event type not built."),
    "FE-0034": Entry("NOT_STARTED", (), (), "Venue calendar not built here."),
    "FE-0035": Entry("BLOCKED", (), (BLK_DATA, BLK_BROKER), "Offline fixture adapter also not yet built."),
    "FE-0036": Entry("NOT_STARTED", (), (BLK_DATA,), "Append-only quote archive not built."),
    "FE-0037": Entry("NOT_STARTED", (), (), "Quote-gap detection not built."),
    "FE-0038": Entry("NOT_STARTED", (), (), "Decision-time snapshot not built (see evidence/decision_manifest.py for the existing manifest)."),
    "FE-0039": Entry("NOT_STARTED", (), (), "Instrument master gate not built."),
    "FE-0040": Entry("NOT_STARTED", (), (BLK_DATA,), "Option depth snapshots need licensed depth data."),
    "FE-0041": Entry("NOT_STARTED", (), (), "Minute OHLCV archive not built."),
    "FE-0042": Entry("NOT_STARTED", (), (), "Data gate reason codes not built."),
    "FE-0043": Entry("NOT_STARTED", (), (), "Prefix-invariance tests not built for the forward path."),
    "FE-0044": Entry("NOT_STARTED", (), (), "Coverage score not built."),
    # ---- F04 ----
    "FE-0045": Entry("NOT_STARTED", (), (BLK_MARKET, BLK_TIME), "Cohort schedule freeze not built."),
    "FE-0046": Entry("PARTIAL", (), (), "Canonical manifest exists (evidence/decision_manifest.py); cross-platform hash not verified."),
    "FE-0047": Entry("PARTIAL", CUSTODY_CLASS[1:4], (BLK_TSA,), "Verifier abstraction and pinned Ed25519 root check. No real provider."),
    "FE-0048": Entry("PARTIAL", CUSTODY_CLASS[4:7], (BLK_TSA,), "Receipt classification exists; not yet wired to a pre-entry recording step."),
    "FE-0049": Entry("NOT_STARTED", (), (), "Signal/abstention persistence not built."),
    "FE-0050": Entry("DONE", CUSTODY_CLASS, (), "Five terminal states; only VERIFIED is formal-claim eligible."),
    "FE-0051": Entry("NOT_STARTED", (), (BLK_TSA,), "champion.verified_days is still fed by the caller; not wired to this verifier."),
    "FE-0052": Entry("DONE", CUSTODY_CLASS[3:7], (), "False clock, replayed receipt, altered payload, late, decision after entry: all fail closed."),
    "FE-0053": Entry("DONE", ("test_no_receipt_is_the_honest_local_only_fallback",), (),
                     "LOCAL_ONLY downgrade at classification level; the pipeline that uses it is FE-0051."),
    "FE-0054": Entry("NOT_STARTED", (), (), "Daily attestation bundle not built."),
    # ---- F05 ----
    "FE-0055": Entry("NOT_STARTED", (), (), "Engine adapter not built."),
    "FE-0056": Entry("NOT_STARTED", (), (), "Bar-completion fill timing not built for the forward path."),
    "FE-0057": Entry("NOT_STARTED", (), (), "Marketable-limit simulator not built."),
    "FE-0058": Entry("NOT_STARTED", (), (), "Queue model not built."),
    "FE-0059": Entry("NOT_STARTED", (), (), "Adverse selection and partial fills not built."),
    "FE-0060": Entry("NOT_STARTED", (), (), "Effective-dated cost tables not built."),
    "FE-0061": Entry("NOT_STARTED", (), (), "Cost stress scenarios not built."),
    "FE-0062": Entry("NOT_STARTED", (), (BLK_DATA,), "Option multi-leg quotes need depth data."),
    "FE-0063": Entry("NOT_STARTED", (), (), "Futures roll model not built."),
    "FE-0064": Entry("NOT_STARTED", (), (), "FX simulation not built (no FX execution is implied)."),
    "FE-0065": Entry("NOT_STARTED", (), (), "Execution receipts not built."),
    "FE-0066": Entry("NOT_STARTED", (), (), "Counterfactual fills not built."),
    "FE-0067": Entry("NOT_STARTED", (), (), "Settlement identity checks not built."),
    "FE-0068": Entry("NOT_STARTED", (), (), "Outage no-trade outcome not built."),
    # ---- F06 ----
    "FE-0069": Entry("NOT_STARTED", (), (), "Preflight not built."),
    "FE-0070": Entry("NOT_STARTED", (), (BLK_MARKET,), "Session supervisor not built; injected-clock tests possible."),
    "FE-0071": Entry("NOT_STARTED", (), (), "Opening-range freeze not built for the forward path."),
    "FE-0072": Entry("NOT_STARTED", (), (), "Append-only decision journal not built for paper orders."),
    "FE-0073": Entry("NOT_STARTED", (), (BLK_DATA,), "Websocket resubscribe needs a feed."),
    "FE-0074": Entry("NOT_STARTED", (), (), "Alerting not built."),
    "FE-0075": Entry("NOT_STARTED", (), (), "End-of-session close not built."),
    "FE-0076": Entry("NOT_STARTED", (), (), "Completeness report not built."),
    "FE-0077": Entry("NOT_STARTED", (), (), "Backup and restore drill not built."),
    "FE-0078": Entry("NOT_STARTED", (), (), "Runbooks not written."),
    # ---- F07 ----
    "FE-0079": Entry("NOT_STARTED", (), (BLK_MARKET,), "Shadow logging needs live observations."),
    "FE-0080": Entry("NOT_STARTED", (), (), "Horizon freeze not built."),
    "FE-0081": Entry("NOT_STARTED", (), (), "Portfolio net alpha not built."),
    "FE-0082": Entry("NOT_STARTED", (), (), "Paired controls not built."),
    "FE-0083": Entry("NOT_STARTED", (), (), "Factor-controlled uplift not built."),
    "FE-0084": Entry("NOT_STARTED", (), (), "Abstention accounting not built."),
    "FE-0085": Entry("NOT_STARTED", (), (), "ORB nulls not built."),
    "FE-0086": Entry("NOT_STARTED", (), (), "Futures carry comparison not built."),
    "FE-0087": Entry("NOT_STARTED", (), (BLK_DATA,), "Options spread evaluation needs executable quotes."),
    "FE-0088": Entry("DONE", TRIAL_TESTS[2:], (), "Trial budget and approval gate; no bypass path."),
    "FE-0089": Entry("NOT_STARTED", (), (), "Exploratory archive not built."),
    "FE-0090": Entry("NOT_STARTED", (), (), "Parameter-freeze enforcement not built."),
    # ---- F08 ----
    "FE-0091": Entry("NOT_STARTED", (), (), "Daily portfolio return not built for forward cohorts."),
    "FE-0092": Entry("NOT_STARTED", (), (), "HAC or block bootstrap not built."),
    "FE-0093": Entry("NOT_STARTED", (), (), "Cluster-robust attribution not built."),
    "FE-0094": Entry("NOT_STARTED", (), (), "Family-wise error control not built."),
    "FE-0095": Entry("NOT_STARTED", (), (), "Risk metrics with uncertainty not built."),
    "FE-0096": Entry("NOT_STARTED", (), (BLK_DATA,), "Capacity needs ADV and depth data."),
    "FE-0097": Entry("NOT_STARTED", (), (BLK_TIME,), "Power calculator not built."),
    "FE-0098": Entry("NOT_STARTED", (), (), "Outperformance probability not built."),
    "FE-0099": Entry("NOT_STARTED", (), (), "Stress scenarios not built."),
    "FE-0100": Entry("NOT_STARTED", (), (), "Chronological block diagnostics not built."),
    "FE-0101": Entry("NOT_STARTED", (), (), "Stop rules not written into a charter."),
    "FE-0102": Entry("NOT_STARTED", (), (), "Claim-tier labels not implemented."),
    "FE-0103": Entry("NOT_STARTED", (), (), "Falsification report not built."),
    # ---- F09 ----
    "FE-0104": Entry("NOT_STARTED", (), (), "Capital allocation not built."),
    "FE-0105": Entry("NOT_STARTED", (), (), "Portfolio exposure limits not built."),
    "FE-0106": Entry("NOT_STARTED", (), (), "Vol targeting not built."),
    "FE-0107": Entry("NOT_STARTED", (), (), "Pending exposure aggregation not built."),
    "FE-0108": Entry("NOT_STARTED", (), (), "Persistent kill switch not built."),
    "FE-0109": Entry("NOT_STARTED", (), (), "Per-strategy P&L attribution not built."),
    "FE-0110": Entry("NOT_STARTED", (), (), "Regime throttles not built."),
    "FE-0111": Entry("NOT_STARTED", (), (BLK_DATA,), "Capacity report needs ADV and depth."),
    "FE-0112": Entry("NOT_STARTED", (), (), "Disaster stress tests not built."),
    "FE-0113": Entry("NOT_STARTED", (), (), "NO_TRADE as explicit decisions in the forward path not built."),
    # ---- F10 ----
    "FE-0114": Entry("NOT_STARTED", (), (), "Cockpit not built."),
    "FE-0115": Entry("NOT_STARTED", (), (), "Strategy list not built."),
    "FE-0116": Entry("NOT_STARTED", (), (), "Signal trace view not built."),
    "FE-0117": Entry("NOT_STARTED", (), (), "Order blotter not built."),
    "FE-0118": Entry("NOT_STARTED", (), (), "P&L view not built."),
    "FE-0119": Entry("NOT_STARTED", (), (), "Session timeline not built."),
    "FE-0120": Entry("NOT_STARTED", (), (), "Cross-strategy scorecard not built."),
    "FE-0121": Entry("NOT_STARTED", (), (), "Evidence bundle export not built."),
    "FE-0122": Entry("NOT_STARTED", (), (), "Read-only UI not built."),
    "FE-0123": Entry("NOT_STARTED", (), (), "Operator decisions not built."),
    # ---- F11 ----
    "FE-0124": Entry("NOT_STARTED", (), (), "Outcome maturation queue not built."),
    "FE-0125": Entry("NOT_STARTED", (), (), "Versioned training not built."),
    "FE-0126": Entry("NOT_STARTED", (), (), "Locked cohort separation not built."),
    "FE-0127": Entry("NOT_STARTED", (), (), "Net challenger comparison not built."),
    "FE-0128": Entry("PARTIAL", ("test_independent_receipt_from_a_pinned_root_verifies",
                                 "test_audit_is_red_on_this_repository_and_never_green_without_custody"), (BLK_TSA,),
                     "Only VERIFIED is claim-eligible. The champion promotion path is not gated by it yet."),
    "FE-0129": Entry("PARTIAL", APPROVAL_TESTS[:2] + ("test_live_cash_with_human_approval_and_compliance_is_granted_only_when_all_gates_pass",),
                     (BLK_OWNER,), "Agent actors rejected at the approval and mode gates. Champion-change path not built."),
    "FE-0130": Entry("NOT_STARTED", (), (), "Transition log not built."),
    "FE-0131": Entry("NOT_STARTED", (), (), "Automatic rollback not built."),
    # ---- F12 ----
    "FE-0132": Entry("PARTIAL", ("test_only_live_paper_emits_paper_fills_and_no_mode_sends_physical_orders",), (BLK_BROKER,),
                     "No broker adapter exists; physical-order gate is False everywhere."),
    "FE-0133": Entry("BLOCKED", (), (BLK_COMPLIANCE,), "Owner and broker must confirm permissions."),
    "FE-0134": Entry("BLOCKED", (), (BLK_COMPLIANCE,), "Static IP and algo identifiers need broker evidence."),
    "FE-0135": Entry("BLOCKED", (), (BLK_MARKET, BLK_BROKER), "Ten market-hours rehearsals need a broker dry-run."),
    "FE-0136": Entry("BLOCKED", (), (BLK_BROKER,), "Broker status reconciliation needs a broker."),
    "FE-0137": Entry("NOT_STARTED", (), (BLK_OWNER,), "Capital-risk proposal template not written."),
    "FE-0138": Entry("BLOCKED", (), (BLK_BROKER,), "Contract-note reconciliation needs broker statements."),
    "FE-0139": Entry("BLOCKED", (), (BLK_BROKER, BLK_OWNER), "Micro pilot needs approval and a broker."),
    "FE-0140": Entry("BLOCKED", (), (BLK_BROKER,), "Observed broker slippage needs broker fills."),
    "FE-0141": Entry("NOT_STARTED", (), (), "Scaling policy not written."),
    "FE-0142": Entry("NOT_STARTED", (), (BLK_OWNER,), "DISARM latch not built (approval kind DISARM_RESET exists)."),
    # ---- F13 ----
    "FE-0143": Entry("NOT_STARTED", (), (), "CLI not built (audit CLI exists under python -m)."),
    "FE-0144": Entry("NOT_STARTED", (), (), "Paper run command not built."),
    "FE-0145": Entry("NOT_STARTED", (), (), "Close command not built."),
    "FE-0146": Entry("NOT_STARTED", (), (), "Report command not built."),
    "FE-0147": Entry("NOT_STARTED", (), (), "Fixture pack not built."),
    "FE-0148": Entry("BLOCKED", (), (BLK_LINUX,), "Windows run only; the Linux hash comparison has not been run."),
    "FE-0149": Entry("PARTIAL", (), (), "This tracker and fe-items.json. Evidence is test-linked for DONE items only."),
    "FE-0150": Entry("PARTIAL", (), (BLK_LINUX,), "Full Windows suite log in docs/forward-edge/logs; see the log for the result."),
    "FE-0151": Entry("NOT_STARTED", (), (), "Operator documentation not written."),
    "FE-0152": Entry("NOT_STARTED", (), (), "Release labels not implemented."),
}


def entries() -> dict[str, Entry]:
    items = json.loads(Path(__file__).resolve().parents[3].joinpath("docs/forward-edge/fe-items.json").read_text(encoding="utf-8"))
    out: dict[str, Entry] = {}
    for item in items:
        out[item["id"]] = OVERRIDES.get(item["id"], Entry("NOT_STARTED", (), (), f"Not started (wave {item['wave']})."))
    return out


def render(path: Path | None = None) -> str:
    items = json.loads(Path(__file__).resolve().parents[3].joinpath("docs/forward-edge/fe-items.json").read_text(encoding="utf-8"))
    table = entries()
    counts: dict[str, int] = {}
    for e in table.values():
        counts[e.status] = counts.get(e.status, 0) + 1
    lines = ["# Forward-edge tracker (FE-0001 .. FE-0152)", "",
             "Generated by `financial_brain.forward_edge.tracker.render`. Statuses: DONE (test-linked, in stated scope), "
             "PARTIAL, BLOCKED (external or owner evidence; not simulated), NOT_STARTED.", "",
             "Totals: " + ", ".join(f"{k} {counts.get(k, 0)}" for k in ("DONE", "PARTIAL", "BLOCKED", "NOT_STARTED")),
             "", "| ID | Wave | Item | Status | Tests | Blockers | Note |", "|---|---|---|---|---|---|---|"]
    for item in items:
        e = table[item["id"]]
        title = item["item"].replace("|", "/")
        tests = "<br>".join(f"`{t}`" for t in e.tests) or "-"
        blockers = "<br>".join(b.split(":")[0] for b in e.blockers) or "-"
        lines.append(f"| {item['id']} | {item['wave']} | {title} | {e.status} | {tests} | {blockers} | {e.note.replace('|', '/')} |")
    text = "\n".join(lines) + "\n"
    if path is not None:
        path.write_text(text, encoding="utf-8")
    return text


UNBLOCK = {
    "BLK-BROKER": "Owner obtains a broker account with API access; a sandbox or fixture stands in offline.",
    "BLK-DATA": "Owner licenses real-time quote and depth data under the exchange/vendor terms; fixtures stand in offline.",
    "BLK-TSA": "Owner selects an independent timestamp provider and pins its trust root in docs/forward-edge/trust-roots.json.",
    "BLK-COMPLIANCE": "Broker and owner confirm SEBI retail-algo permissions, exchange IDs and static IP in writing.",
    "BLK-MARKET": "Elapsed NSE/BSE sessions with independent timestamps; cannot be shortened or simulated.",
    "BLK-GOVERNANCE": "New ADR amendment by the owner, or a preregistered hypothesis in 2027Q1.",
    "BLK-OWNER": "The owner makes and records the decision; agents cannot.",
    "BLK-GITHUB": "Owner enables branch protection on main with required checks and review.",
    "BLK-LINUX": "Run the test matrix on a Linux runner (CI after a push, or a WSL/Linux machine).",
    "BLK-TIME": "Calendar time: the preregistered number of scheduled sessions must elapse.",
}


def render_blockers(path: Path | None = None) -> str:
    table = entries()
    lines = ["# Blocker matrix", "",
             "Each blocker is an external provider, owner action or elapsed evidence. None is simulated in this repository.", "",
             "| Blocker | Items affected | Unblocked by |", "|---|---|---|"]
    seen: dict[str, list[str]] = {}
    for fe_id, e in table.items():
        for b in e.blockers:
            seen.setdefault(b.split(":")[0], []).append(fe_id)
    for key in sorted(seen):
        ids = ", ".join(sorted(seen[key]))
        lines.append(f"| {key} | {ids} | {UNBLOCK.get(key, '')} |")
    text = "\n".join(lines) + "\n"
    if path is not None:
        path.write_text(text, encoding="utf-8")
    return text


# ---- F02 (registry) statuses, added in wave F02 ----
F02_TESTS = "test_forward_edge_f02.py"
OVERRIDES.update({
    "FE-0022": Entry("PARTIAL", ("test_inventory_lists_the_existing_strategy_modules_with_hashes",), (),
                     "File-level inventory with hashes. Duplicate and adapter analysis not done; existing strategies/specs.py not reconciled."),
    "FE-0023": Entry("DONE", ("test_missing_required_fields_fail_registration", "test_spec_validation_rules",
                              "test_spec_is_immutable"), (), "StrategySpec in forward_edge/registry.py; frozen and validated."),
    "FE-0024": Entry("DONE", ("test_failed_strategy_cannot_become_eligible_frozen_or_verified",
                              "test_paper_enrolment_needs_a_human_and_has_a_benchmark",
                              "test_verified_needs_custody"), (),
                     "Seven statuses; FAILED only moves to RETIRED. All 13 ledger trials register as FAILED in a test."),
    "FE-0025": Entry("PARTIAL", ("test_benchmark_must_be_frozen_before_first_forward_result",), (),
                     "Freeze-before-result rule and kinds. Per-family benchmark definitions not built."),
    "FE-0026": Entry("PARTIAL", ("test_failed_exploratory_and_shadow_families_never_get_order_intents",
                                 "test_shadow_signal_has_no_quantity_and_rejects_unknown_values"), (),
                     "Gate and signal type exist. No adapter yet for each registrable family."),
    "FE-0028": Entry("NOT_STARTED", (), (), "Virtual paper subaccounts not built."),
    "FE-0029": Entry("PARTIAL", ("test_transition_creates_a_new_version_and_keeps_the_old_one",), (),
                     "Versioned transitions with history. Retirement handoff policy not written."),
    "FE-0030": Entry("PARTIAL", ("test_null_portfolio_holds_nothing_every_day", "test_placebo_is_seeded_and_sized"), (),
                     "Null and seeded placebo generators. Not yet enforced inside every study."),
    "FE-0031": Entry("PARTIAL", ("test_correlated_candidates_are_deduplicated",), (),
                     "Greedy correlation dedupe. Effective-trial count and family cap not wired into multiplicity control."),
    "FE-0032": Entry("PARTIAL", ("test_selection_on_shadow_results_is_exploratory_unless_preregistered",), (),
                     "Label function only; not enforced on evaluation paths."),
})


# ---- F03 (market data) statuses, added in wave F03 ----
OVERRIDES.update({
    "FE-0033": Entry("DONE", ("test_event_keeps_all_four_clocks_in_utc_order",
                              "test_impossible_clock_order_and_naive_times_are_rejected",
                              "test_decision_sees_only_what_was_available_then",
                              "test_content_hash_changes_when_any_field_changes"), (),
                     "NormalizedEvent in forward_edge/market_data.py."),
    "FE-0034": Entry("PARTIAL", ("test_next_session_skips_weekends_and_holidays",
                                 "test_calendar_needs_a_version_and_holiday_list"), (),
                     "Versioned calendar file drives next-session logic. The NSE holiday list is not bundled: owner must supply the source file."),
    "FE-0035": Entry("BLOCKED", ("test_fixture_feed_replays_in_deterministic_order",), (BLK_DATA, BLK_BROKER),
                     "Offline fixture replay works. The live Kite adapter is not implemented and cannot be without credentials and data rights."),
    "FE-0036": Entry("PARTIAL", ("test_archive_is_hash_chained_and_detects_edits", "test_dropped_updates_are_counted_not_hidden"), (),
                     "Hash-chained append-only archive of normalized events. Raw wire messages are not retained."),
    "FE-0037": Entry("DONE", ("test_stale_gapped_or_disconnected_feed_abstains",), (),
                     "Abstention decision on stale, gapped, disconnected or dropped feeds. Health inputs come from the caller."),
    "FE-0038": Entry("PARTIAL", ("test_decision_snapshot_hash_covers_every_input",), (),
                     "Input hashes captured. No reconstruction tool yet."),
    "FE-0039": Entry("PARTIAL", ("test_instrument_gate_emits_reason_codes",), (),
                     "Gate function with reason codes. Not yet wired into intent emission."),
    "FE-0040": Entry("PARTIAL", ("test_option_mid_without_two_sided_quotes_is_not_executable",), (BLK_DATA,),
                     "Executable-quote check function. Option intents and depth snapshots not built."),
    "FE-0041": Entry("PARTIAL", ("test_close_divergence_is_recorded_not_corrected",), (),
                     "Close divergence recorded. Minute OHLCV archive not built."),
    "FE-0042": Entry("PARTIAL", ("test_data_gate_reason_codes_are_deterministic_and_complete",), (),
                     "Deterministic reason codes. Not yet attached to rejected trades."),
    "FE-0043": Entry("DONE", ("test_feature_is_prefix_invariant_and_ignores_the_future",), (),
                     "Prefix invariance and future perturbation hold for the one feature implemented (feature_at)."),
    "FE-0044": Entry("PARTIAL", ("test_coverage_is_reported_by_session_name_and_notional",), (),
                     "Coverage calculation. Not yet included in P&L reports."),
})


# ---- F04 (decision custody) statuses, added in wave F04 ----
OVERRIDES.update({
    "FE-0045": Entry("DONE", ("test_schedule_rejects_overlapping_and_unsorted_cohorts",
                              "test_schedule_hash_is_pinned_once_observations_begin"), (),
                     "Nonoverlapping sorted schedule; hash pinned once observations begin. Calendar-aware cohorts need the holiday source (FE-0034)."),
    "FE-0046": Entry("PARTIAL", ("test_manifest_canonical_bytes_are_stable_and_hash_matches_golden",
                                 "test_manifest_rejects_floats_bad_hashes_and_late_decisions"), (),
                     "Canonical ASCII JSON manifest with no floats. The golden hash is self-consistency only; the Linux digest match is not run here."),
    "FE-0048": Entry("PARTIAL", ("test_manifest_must_be_recorded_before_entry_and_never_overwritten",
                                 "test_receipt_cannot_be_attached_twice_or_to_unknown_sessions"), (BLK_TSA,),
                     "Journal refuses recording at or after entry and never overwrites. No external receipt is obtainable offline."),
    "FE-0049": Entry("PARTIAL", ("test_zero_signal_and_unsuccessful_sessions_stay_in_the_denominator",), (),
                     "Outcomes and denominators in memory. Durable persistence is not built (FE-0011/0012 gap)."),
    "FE-0051": Entry("PARTIAL", ("test_status_is_local_only_without_a_receipt_and_verified_only_with_a_pinned_one",), (BLK_TSA,),
                     "verified_session_ids counts only VERIFIED custody. champion.verified_days is not rewired to it."),
    "FE-0054": Entry("PARTIAL", ("test_bundle_verifies_and_detects_tampering", "test_bundle_copy_verifies_on_another_location"), (),
                     "Bundle with per-file and per-manifest digests verifies after copy. No signature; not verified on a second machine."),
})


# ---- F05 (execution) statuses, added in wave F05 ----
OVERRIDES.update({
    "FE-0055": Entry("PARTIAL", ("test_same_signal_from_two_engines_gives_the_same_intent",), (),
                     "Canonical OrderIntent adapter. The legacy paper engine and kernel pilot are not rewired to it."),
    "FE-0056": Entry("DONE", ("test_fill_never_uses_a_quote_before_decision_plus_latency",
                              "test_no_quote_after_latency_is_a_recorded_miss"), (), "forward_edge/execution.py."),
    "FE-0057": Entry("DONE", ("test_buy_pays_the_ask_and_sell_receives_the_bid_never_the_mid",
                              "test_tick_rounding_goes_against_the_trader",
                              "test_visible_size_caps_the_fill_and_limit_protects_the_buyer"), (), "No mid fills."),
    "FE-0058": Entry("DONE", ("test_touching_the_limit_is_not_a_fill_and_queue_is_disclosed_unknown",
                              "test_trade_through_fills_at_the_limit_not_the_better_print"), (),
                     "Conservative trade-through rule; queue position disclosed unknown."),
    "FE-0059": Entry("PARTIAL", ("test_adverse_selection_moves_price_against_the_trader",
                                 "test_price_band_rejects_and_freeze_quantity_slices"), (),
                     "Adverse shift, price band, freeze slicing, partials. Inventory across slices not simulated."),
    "FE-0060": Entry("PARTIAL", ("test_cost_rule_is_selected_by_effective_date_and_side",
                                 "test_total_is_rounded_once_from_unrounded_parts"), (BLK_OWNER,),
                     "Effective-dated mechanism tested with FIXTURE rates. Real 2026 levy tables must be supplied and checked against the NSE sources."),
    "FE-0061": Entry("DONE", ("test_stress_reports_which_scenarios_stay_positive",), (),
                     "1x/1.5x/2x scenarios; positive set reported."),
    "FE-0062": Entry("PARTIAL", ("test_a_structure_with_one_unexecutable_leg_is_not_executable",
                                 "test_margin_comes_from_the_exchange_or_the_trade_is_blocked"), (BLK_DATA,),
                     "Executable-leg and official-margin gates. Legging loss and expiry settlement not built."),
    "FE-0064": Entry("PARTIAL", ("test_fx_execution_is_never_allowed",), (), "FX execution refused; no FX simulator."),
    "FE-0065": Entry("DONE", ("test_fidelities_are_reported_separately_and_never_summed_together",), (),
                     "Three fidelities; mixing raises."),
    "FE-0066": Entry("PARTIAL", ("test_fill_bounds_bracket_the_expected_price",), (),
                     "Best/expected/worst price only; no per-trade counterfactual set or confidence interval."),
    "FE-0067": Entry("PARTIAL", ("test_post_trade_identity_catches_a_cash_or_position_error",), (),
                     "Cash and position identity per fill. Settlement, financing and margin identities not built."),
    "FE-0068": Entry("DONE", ("test_outage_is_a_no_trade_not_a_favourable_fill",), (), "NO_TRADE with reason codes."),
})


# ---- F06 (session ops) statuses, added in wave F06 ----
OVERRIDES.update({
    "FE-0069": Entry("DONE", ("test_blocked_preflight_leaves_no_intents_but_writes_a_report",
                              "test_a_missing_check_counts_as_failed_not_as_passed"), (),
                     "Check results are supplied by the caller; live probes (feeds, credentials) are not implemented."),
    "FE-0070": Entry("DONE", ("test_supervisor_states_follow_the_injected_clock_and_calendar",), (),
                     "Startup, late start, closed, weekend and holiday states from an injected clock."),
    "FE-0071": Entry("DONE", ("test_range_freezes_only_from_a_complete_window",
                              "test_restart_cannot_turn_historical_data_into_live_knowledge"), (), "Freeze-on-complete only."),
    "FE-0072": Entry("PARTIAL", ("test_journal_replay_reproduces_the_identical_hash_and_detects_edits",), (),
                     "Hash-chained in-memory journal. Not yet persisted or wired to paper orders."),
    "FE-0073": Entry("PARTIAL", ("test_network_flap_with_replays_and_disorder_yields_one_ordered_stream",), (BLK_DATA,),
                     "Dedupe and ordering logic. No websocket client (needs a feed)."),
    "FE-0074": Entry("PARTIAL", ("test_one_alert_per_root_cause_and_entries_halt",), (),
                     "Once-per-cause and entry halt. No delivery channel; token-expiry detection needs a broker session."),
    "FE-0075": Entry("PARTIAL", ("test_end_of_session_finds_forgotten_and_unapproved_inventory",), (),
                     "Inventory check only. Valuation and expiry handling not built."),
    "FE-0076": Entry("PARTIAL", ("test_missing_segment_or_incident_downgrades_the_session",), (),
                     "Report and downgrade rule. Not written as an immutable file."),
    "FE-0077": Entry("PARTIAL", ("test_backup_restores_to_recorded_hashes_and_detects_corruption",), (),
                     "File-level backup and verify. Quote archive, OMS and ledger are not wired; no scheduled drill."),
    "FE-0078": Entry("PARTIAL", ("test_runbooks_cover_each_phase_and_unknown_phase_is_refused",), (),
                     "Generated runbook text. No one-click operator UI."),
})


# ---- F07 (research tools) statuses, added in wave F07 ----
OVERRIDES.update({
    "FE-0079": Entry("PARTIAL", ("test_opportunities_are_sealed_before_outcomes_and_cannot_be_rewritten",), (BLK_MARKET,),
                     "Sealed shadow log mechanism. No per-family signal producers wired; live logging needs observed sessions."),
    "FE-0080": Entry("DONE", ("test_horizon_and_parameters_cannot_change_after_forward_data",), (),
                     "Horizon frozen once forward data exists."),
    "FE-0081": Entry("DONE", ("test_net_alpha_is_portfolio_level_after_costs_financing_and_cash_flows",), (),
                     "Decimal net return and alpha with external cash flows."),
    "FE-0082": Entry("PARTIAL", ("test_paired_comparison_isolates_value_over_the_control_not_market_direction",), (),
                     "Paired differences. Size/risk/sector/liquidity-matched control construction not built."),
    "FE-0083": Entry("PARTIAL", ("test_pattern_uplift_vanishes_when_a_known_factor_explains_it",
                                 "test_uplift_needs_enough_observations"), (),
                     "OLS uplift with controls on synthetic data; naive standard errors, no HAC, no real chart-pattern data run."),
    "FE-0084": Entry("DONE", ("test_abstention_keeps_a_fixed_denominator_and_counts_missed_winners",), (),
                     "Fixed denominator with missed-winner count."),
    "FE-0085": Entry("PARTIAL", ("test_intraday_edge_is_measured_beyond_the_same_time_of_day_baseline",), (),
                     "Excess over a same-time baseline. No ORB data run."),
    "FE-0086": Entry("PARTIAL", ("test_futures_return_pays_spread_roll_and_funding",), (),
                     "Net-of-drag formula. No matched-alternative comparison."),
    "FE-0087": Entry("PARTIAL", ("test_option_spread_without_executable_quotes_is_ineligible_even_with_a_mid_return",), (BLK_DATA,),
                     "Mid returns refused. Assignment and expiry stress not built."),
    "FE-0089": Entry("DONE", ("test_negative_inconclusive_and_no_signal_cohorts_stay_discoverable",), (),
                     "In-memory archive API; persistence follows the FE-0011/0012 gap."),
    "FE-0090": Entry("DONE", ("test_parameter_tuning_after_prospective_start_is_a_new_trial",), (),
                     "Frozen-parameter assertion; wiring into the runner is part of FE-0144."),
})
