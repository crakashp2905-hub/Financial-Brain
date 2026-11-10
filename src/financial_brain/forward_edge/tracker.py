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
