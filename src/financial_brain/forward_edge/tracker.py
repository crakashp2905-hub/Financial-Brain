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
               "test_unknown_state_survives_checkpoint_and_resume",
               "test_durable_kernel_recovers_events_and_outbox_after_restart")
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
    "FE-0003": Entry("DONE", ("test_catalogue_covers_source_registry_and_historical_trials_with_explanations",
                               "test_catalogue_change_requires_new_version_and_source_edits_are_detected",
                               "test_missing_exclusion_reason_and_unknown_status_are_rejected"), (),
                     "Versioned hash-pinned catalogue covers checked-in external references and historical trial ledger; all candidates carry lineage and a conservative exclusion reason. No candidate is paper-eligible or verified."),
    "FE-0004": Entry("DONE", ("test_charter_hash_is_deterministic_and_sensitive_to_every_field",
                              "test_revision_is_a_new_version_with_a_new_clock",
                              "test_malformed_charters_are_rejected"), (), "Hash over canonical JSON; no signing key."),
    "FE-0005": Entry("DONE", MODE_TESTS, (), "Five modes; grant only via resolve_mode; flag alone insufficient."),
    "FE-0006": Entry("DONE", MODE_TESTS, (), "LIVE_CASH refused by build policy; needs compliance and human approval."),
    "FE-0007": Entry("BLOCKED", (), (BLK_GITHUB,), "Owner must enable branch protection and required checks."),
    "FE-0008": Entry("DONE", APPROVAL_TESTS, (), "Human-only, scoped, expiring approvals. Real owner approvals not yet recorded."),
    "FE-0009": Entry("DONE", ("test_disposable_ledger_isolated_append_only_and_removed_on_close",
                               "test_disposable_ledger_refuses_repository_and_existing_paths",
                               "test_temporary_disposable_ledger_needs_no_credentials"), (),
                     "Creates only under a caller-provided temporary root; refuses repository overlap and overwrite; event IDs are idempotent and content-bound."),
    "FE-0010": Entry("DONE", AUDIT_TESTS, (BLK_TSA, BLK_LINUX), "python -m financial_brain.forward_edge.audit; currently RED."),
    # ---- F01 ----
    "FE-0011": Entry("PARTIAL", EVENT_TESTS[:2] + (EVENT_TESTS[-1],), (), "SQLite WAL outbox records PENDING before action, then DISPATCHED or UNKNOWN. Cross-process external side-effect atomicity still needs reconciliation."),
    "FE-0012": Entry("PARTIAL", (EVENT_TESTS[-1],), (), "SQLite FULL-synchronous WAL persists ordered events, watermark, dispatch and reconciliation state. Crash injection at every transition remains incomplete."),
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
    "FE-0023": Entry("DONE", ("test_forward_strategy_spec_validates_all_required_execution_metadata",), (),
                     "Shared StrategySpec now hashes and round-trips every required contract field; forward validation fails closed on missing or malformed metadata."),
    "FE-0024": Entry("PARTIAL", ("test_strategy_eligibility_cannot_omit_lineage_or_use_unrecognized_state",
                                  "test_catalogue_covers_source_registry_and_historical_trials_with_explanations"), (),
                     "Seven strict eligibility states and hash-linked exclusion records exist. Catalogue candidates are classified; runtime registry transitions and universal strategy mapping are not yet wired."),
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
    "FE-0033": Entry("DONE", ("test_all_market_event_kinds_roundtrip_with_clocks",
                               "test_naive_clock_or_event_not_available_at_decision_is_rejected",
                               "test_malformed_market_payloads_fail_closed"), (),
                     "One normalized immutable event supports tick, quote, depth, bar, halt, auction, corporate action and broker update while preserving UTC event, receipt, availability and optional decision clocks."),
    "FE-0034": Entry("PARTIAL", ("test_next_session_uses_sourced_records_and_includes_special_sessions",
                                  "test_missing_calendar_coverage_and_future_horizon_fail_closed",
                                  "test_bad_sources_times_venues_and_duplicate_session_records_are_rejected"), (),
                     "Calendar requires explicit source-hashed NSE/BSE session records, supports special sessions and fails closed outside coverage. Official holiday/special-session data and corporate-action effective-date wiring are not populated."),
    "FE-0035": Entry("BLOCKED", (), (BLK_DATA, BLK_BROKER), "Offline fixture adapter also not yet built."),
    "FE-0036": Entry("PARTIAL", ("test_archive_is_append_only_idempotent_and_roundtrips_provenance",
                                  "test_archive_recovers_rows_after_close_and_reopen"), (BLK_DATA,),
                     "SQLite WAL archive persists normalized event clocks, sequence, source, evidence ID and content hash; real entitled feed and append-only integrity anchoring remain outstanding."),
    "FE-0037": Entry("PARTIAL", ("test_feed_health_reports_sequence_gaps_and_transport_lag",), (),
                     "Reports receipt count, sequence gaps, late timestamps, maximum transport lag and last receipt from stored events. Reconnect/drop event integration and real coverage are not wired."),
    "FE-0038": Entry("PARTIAL", ("test_decision_snapshot_excludes_late_data_and_is_reproducible",), (),
                     "Decision snapshot binds event IDs, content hashes and availability times and excludes events not available by decision time. Per-strategy feature/input hashes and append-only snapshot storage are not yet wired."),
    "FE-0039": Entry("PARTIAL", ("test_dated_instrument_eligibility_fails_closed_on_status_rights_and_ticket_rules",), (),
                     "Existing effective-dated instrument master now validates ISIN, expiry, option strike, rights status, tick/lot/freeze rules, lifecycle status and trading phase. No authoritative exchange/broker instrument-master ingestion or mapping is configured."),
    "FE-0040": Entry("NOT_STARTED", (), (BLK_DATA,), "Option depth snapshots need licensed depth data."),
    "FE-0041": Entry("NOT_STARTED", (), (), "Minute OHLCV archive not built."),
    "FE-0042": Entry("PARTIAL", ("test_good_observed_quote_passes_and_reason_order_is_stable",
                                  "test_each_bad_data_condition_abstains_with_reason",
                                  "test_future_and_naive_quote_timestamps_are_rejected"), (),
                     "A shared deterministic data gate abstains on missing/stale/future quotes, wide spread, zero volume, circuit limits, freeze limits, non-continuous sessions and missing auction data. Kite adapters and every paper runner still need to consume the gate."),
    "FE-0043": Entry("PARTIAL", ("test_point_in_time_inputs_are_hashable_only_when_available",
                                  "test_lookahead_contract_rejects_naive_and_duplicate_inputs",
                                  "test_prefix_output_survives_future_perturbations_and_detects_leakage"), (),
                     "Reusable point-in-time input hash and future-perturbation contract are implemented. Existing strategy feature families are not yet universally evaluated against this contract."),
    "FE-0044": Entry("PARTIAL", ("test_coverage_report_uses_explicit_denominators_and_labels_fixture_sources",
                                  "test_coverage_rejects_intents_outside_declared_sessions_and_naive_clock"), (BLK_DATA,),
                     "Coverage report calculates session, name, quote/depth and intent-notional fractions against caller-declared expected populations. Fixture sources are labeled; live provenance is not authenticated and the report is not yet attached to P&L claims."),
    # ---- F04 ----
    "FE-0045": Entry("NOT_STARTED", (), (BLK_MARKET, BLK_TIME), "Cohort schedule freeze not built."),
    "FE-0046": Entry("PARTIAL", (), (), "Canonical manifest exists (evidence/decision_manifest.py); cross-platform hash not verified."),
    "FE-0047": Entry("PARTIAL", CUSTODY_CLASS[1:4], (BLK_TSA,), "Verifier abstraction and pinned Ed25519 root check. No real provider."),
    "FE-0048": Entry("PARTIAL", CUSTODY_CLASS[4:7], (BLK_TSA,), "Receipt classification exists; not yet wired to a pre-entry recording step."),
    "FE-0049": Entry("PARTIAL", ("test_journal_persists_all_outcomes_and_keeps_zero_signal_denominator",
                                  "test_journal_rejects_conflicts_missing_reasons_and_unregistered_strategies",
                                  "test_journal_recovers_decisions_after_restart_and_reports_missing_cohort_members"), (),
                     "Durable local journal records submitted, withheld, abstained, no-signal and unsuccessful outcomes, retains zero-signal sessions, detects conflicting IDs and exposes missing cohort entries. It is not yet connected to the session runner or independent timestamp custody."),
    "FE-0050": Entry("DONE", CUSTODY_CLASS, (), "Five terminal states; only VERIFIED is formal-claim eligible."),
    "FE-0051": Entry("NOT_STARTED", (), (BLK_TSA,), "champion.verified_days is still fed by the caller; not wired to this verifier."),
    "FE-0052": Entry("DONE", CUSTODY_CLASS[3:7], (), "False clock, replayed receipt, altered payload, late, decision after entry: all fail closed."),
    "FE-0053": Entry("DONE", ("test_no_receipt_is_the_honest_local_only_fallback",), (),
                     "LOCAL_ONLY downgrade at classification level; the pipeline that uses it is FE-0051."),
    "FE-0054": Entry("NOT_STARTED", (), (), "Daily attestation bundle not built."),
    # ---- F05 ----
    "FE-0055": Entry("PARTIAL", ("test_same_candidate_produces_equivalent_versioned_engine_intents",
                                  "test_adapter_rejects_spec_mismatch_or_missing_forward_contract",
                                  "test_intent_identity_changes_with_decision_or_signal_payload"), (),
                     "Versioned adapter creates deterministic canonical intents and projections for kernel OMS and legacy paper. Existing session runners have not yet adopted the adapter; equivalence is contract-tested rather than end-to-end."),
    "FE-0056": Entry("PARTIAL", ("test_completed_bar_or_quote_yields_latency_delayed_timed_intent",
                                  "test_incomplete_bar_future_event_and_wrong_event_kind_are_rejected"), (),
                     "Shared timed-intent contract requires an explicitly completed bar or available quote and enforces decision plus latency before fill. Existing paper adapters have not all been wired through it."),
    "FE-0057": Entry("PARTIAL", ("test_buy_uses_ask_and_caps_to_visible_ask_quantity",
                                  "test_sell_uses_bid_and_rounds_limit_down_to_tick",
                                  "test_zero_or_missing_visible_size_never_produces_a_fillable_quantity"), (),
                     "Quote planner uses side-correct touch, tick rounding, slippage cap and visible-size quantity cap. It returns a plan, not a fill; depth ladders, latency changes and runner integration remain."),
    "FE-0058": Entry("PARTIAL", ("test_candle_touch_without_trade_evidence_is_a_missed_fill",
                                  "test_unknown_queue_position_is_disclosed_and_conservatively_missed",
                                  "test_known_queue_position_produces_missed_partial_or_full_fill",
                                  "test_invalid_queue_claim_or_book_is_rejected_or_no_fill"), (),
                     "Passive model requires trade-through evidence, discloses unknown queue position and defaults to no fill when queue is unknown; known queue supports partial fills. Real order-book sequencing and runner integration remain."),
    "FE-0059": Entry("PARTIAL", ("test_adverse_selection_and_order_slices_update_inventory",
                                  "test_visible_depth_causes_partial_fill_and_no_liquidity_misses",
                                  "test_adverse_price_limit_and_freeze_limit_reject_without_inventory",
                                  "test_sell_slippage_is_adverse_and_inventory_direction_is_negative"), (),
                     "Scenario model applies adverse slippage, price limits, visible-size partial fills, slices and freeze rejects with explicit inventory delta. It is not yet wired to OMS, and real adverse-selection calibration is unavailable."),
    "FE-0060": Entry("PARTIAL", ("test_effective_cost_resolution_returns_each_charge_with_schedule_lineage",
                                  "test_schedule_identity_is_immutable_and_effective_ranges_cannot_overlap",
                                  "test_missing_or_out_of_effective_date_schedule_fails_closed"), (),
                     "Effective-dated immutable schedule wraps existing Indian STT/exchange/SEBI/stamp/brokerage/GST/DP/impact model and itemizes fill-side charges. Rates are owner-configured, not independently validated against every current broker tariff; OMS/ledger charging remains unwired."),
    "FE-0061": Entry("PARTIAL", ("test_sensitivity_reports_required_multipliers_and_net_profit_survival",
                                  "test_unobserved_assumptions_are_marked_uncalibrated",
                                  "test_missing_or_invalid_estimates_fail_closed"), (),
                     "Reports net P&L under fixed 1x/1.5x/2x slippage and impact multipliers including existing statutory/broker fees. Calibration is provenance-labeled; actual observed base estimates and attachment to profit claims remain outstanding."),
    "FE-0062": Entry("PARTIAL", ("test_executable_multileg_quotes_report_legging_and_unverified_margin",
                                  "test_expiry_intrinsic_is_only_a_scenario_and_verified_margin_is_explicit",
                                  "test_non_executable_or_stale_leg_quotes_fail_closed"), (BLK_DATA,),
                     "Two-sided visible option-leg quotes, stale/thin-leg rejects, leg-order loss bound, debit risk and explicitly unverified margin/settlement states are implemented. Real depth, broker margin, actual exchange expiry settlement and paper-runner wiring remain outstanding."),
    "FE-0063": Entry("PARTIAL", ("test_roll_preserves_negative_basis_and_reconciles_lot_margin_financing_and_mtm",
                                  "test_roll_rejects_future_expiry_or_asynchronous_marks"), (),
                     "Calculates observed-input basis (including negative), changed lot quantities, MTM, margin delta and financing. No runner ledger integration, verified broker margin, continuous-contract adjustment or authenticated source marks."),
    "FE-0064": Entry("NOT_STARTED", (), (), "FX simulation not built (no FX execution is implied)."),
    "FE-0065": Entry("NOT_STARTED", (), (), "Execution receipts not built."),
    "FE-0066": Entry("NOT_STARTED", (), (), "Counterfactual fills not built."),
    "FE-0067": Entry("NOT_STARTED", (), (), "Settlement identity checks not built."),
    "FE-0068": Entry("PARTIAL", ("test_live_complete_inputs_can_continue_but_stale_and_down_are_no_trade",
                                  "test_missing_inputs_and_unknown_broker_state_are_journaled_without_fallback",
                                  "test_no_trade_writer_refuses_evaluate_result"), (),
                     "Stale/down feed, unknown broker state and missing required inputs create explicit NO_TRADE decisions and durable journal records with fallback_fill=False. Live adapters still need to call the gate before all entry paths."),
    # ---- F06 ----
    "FE-0069": Entry("NOT_STARTED", (), (), "Preflight not built."),
    "FE-0070": Entry("NOT_STARTED", (), (BLK_MARKET,), "Session supervisor not built; injected-clock tests possible."),
    "FE-0071": Entry("NOT_STARTED", (), (), "Opening-range freeze not built for the forward path."),
    "FE-0072": Entry("PARTIAL", ("test_order_lifecycle_is_append_only_and_replay_hash_is_deterministic",
                                  "test_order_journal_rejects_gaps_illegal_transitions_decreasing_fills_and_id_conflicts",
                                  "test_order_journal_restores_identical_replay_after_restart"), (),
                     "SQLite WAL order journal persists monotonic immutable transitions, accounting payloads and deterministic replay hash. Existing paper OMS and session runner call sites are not yet connected."),
    "FE-0073": Entry("NOT_STARTED", (), (BLK_DATA,), "Websocket resubscribe needs a feed."),
    "FE-0074": Entry("PARTIAL", ("test_high_alert_deduplicates_root_cause_and_halts_entries",
                                  "test_alert_identity_is_content_bound_and_unknown_clear_is_rejected"), (),
                     "SQLite alert book covers stale quotes, token expiry, aborted sessions, duplicate orders and ledger differences, deduplicates root causes, and halts entries until cleared. No external notification channel or all-runner alert emitters are connected."),
    "FE-0075": Entry("PARTIAL", ("test_session_close_values_positions_and_checks_carry_and_expiry",
                                  "test_stale_missing_and_late_marks_never_authorize_carry",
                                  "test_session_close_journal_is_immutable_and_idempotent"), (),
                     "Deterministic paper valuation, expiry disposition, explicit next-session carry authorization and immutable SQLite close reports are implemented. Existing OMS/session runners, authoritative settlement marks and automatic close orders remain unconnected."),
    "FE-0076": Entry("PARTIAL", ("test_missing_segments_or_close_marks_downgrade_evidence",
                                  "test_only_complete_incident_free_session_is_eligible",
                                  "test_completeness_journal_is_immutable_idempotent_and_restartable"), (),
                     "Immutable SQLite session completeness report hashes declared/received segments, incidents and close-report evidence; missing data or incidents downgrade eligibility. Live session runner and independently attested source evidence are not connected."),
    "FE-0077": Entry("PARTIAL", ("test_backup_restore_drill_preserves_sqlite_and_manifest_bytes",
                                  "test_backup_rejects_overwrite_and_restore_detects_tampering",
                                  "test_restore_rejects_manifest_path_traversal_and_hash_edit"), (),
                     "Verified SQLite online snapshots and file backups restore into an isolated new directory after manifest/content hash and SQLite integrity checks. Scheduling, offsite retention, production source discovery and a full OMS/archive/manifest restore rehearsal remain unconnected."),
    "FE-0078": Entry("DONE", ("test_forward_edge_runbooks_cover_fail_closed_operational_recovery",), (),
                     "Paper-only runbooks cover preflight, feed/token failure, unknown orders, reconciliation, close/carry, backup/restore, evidence and escalation. Automated runbook execution and live provider-specific procedures remain outside this documentation item."),
    # ---- F07 ----
    "FE-0079": Entry("PARTIAL", ("test_shadow_log_captures_every_family_scan_including_no_signal_and_no_outcomes",
                                  "test_shadow_scan_is_idempotent_and_conflicts_are_rejected",
                                  "test_shadow_scan_rejects_duplicate_candidates_and_naive_time"), (BLK_MARKET,),
                     "Immutable outcome-blind scan journal covers all seven requested strategy families, including explicit zero-opportunity scans and session coverage. Live passive observations and real-feed source evidence remain blocked."),
    "FE-0080": Entry("PARTIAL", ("test_horizon_choices_are_frozen_before_first_eligible_session",
                                  "test_post_start_freeze_and_mechanics_mismatch_fail_closed",
                                  "test_horizon_units_and_ranges_are_explicit"), (),
                     "Immutable, content-hashed cohort declaration freezes session or intraday minute horizons before the first eligible session and prevents edits under the same cohort ID. Existing strategy runners and preregistered cohort registry are not yet wired."),
    "FE-0081": Entry("PARTIAL", ("test_after_cost_net_alpha_includes_cash_days_and_carried_exposure",
                                  "test_missing_exchange_session_and_invalid_costs_fail_closed",
                                  "test_leveraged_return_uses_equity_denominator_and_exposure_is_checked"), (),
                     "Daily capital-weighted portfolio returns include cash days, supplied charges, financing, gross/net carried exposure and compounded benchmark-relative after-cost alpha. Live OMS/accounting reconciliation and authenticated complete daily marks remain unconnected."),
    "FE-0082": Entry("PARTIAL", ("test_same_session_matched_pairs_measure_incremental_after_cost_return",
                                  "test_controls_are_not_reused_and_unmatched_candidates_are_retained",
                                  "test_session_side_sector_and_liquidity_mismatch_are_not_matched"), (),
                     "Deterministic nearest match uses same session, distinct instrument, direction, sector, liquidity bucket and bounded ex-ante size/risk differences; after-cost incremental return and unmatched cases are reported. Full frozen cohort/outcome lineage and population-scale matching remain unconnected."),
    "FE-0083": Entry("PARTIAL", ("test_factor_adjusted_pattern_coefficient_removes_known_factor_only_effect",
                                  "test_adjusted_coefficient_detects_injected_incremental_pattern_component",
                                  "test_pattern_uplift_requires_both_groups_and_identifiable_design"), (),
                     "Descriptive after-cost regression controls 12-1 momentum, distance from the 52-week high, volatility and session effects; deterministic synthetic controls verify factor removal and injected uplift. Dependence, search multiplicity, live cohort lineage and promotion-grade inference remain unaddressed."),
    "FE-0084": Entry("PARTIAL", ("test_all_opportunities_count_and_abstention_missed_winners_are_reported",
                                  "test_missing_opportunity_or_changed_policy_cannot_be_silently_dropped",
                                  "test_abstentions_need_reasons_and_frozen_policy_precedes_cohort"), (),
                     "Frozen-policy evaluation requires an exact opportunity universe, separates realized policy return from counterfactual outcomes, counts missed winners/avoided losses, and retains unresolved cases. No live decision runner or full-universe forward outcomes are connected."),
    "FE-0085": Entry("PARTIAL", ("test_intraday_signal_compares_after_cost_with_same_time_side_null",
                                  "test_sparse_null_bins_stay_unmatched_instead_of_being_dropped",
                                  "test_previous_session_or_duplicate_observations_are_not_accepted_as_peers"), (),
                     "Descriptive same-session minute-bin/side null compares after-cost signal return against other instruments and reports sparse bins as unmatched. Feed-wide candidate generation, side-return normalization lineage, dependence and multiple-test correction remain unconnected."),
    "FE-0086": Entry("PARTIAL", ("test_negative_basis_and_futures_carry_are_compared_after_all_costs",
                                  "test_unmatched_or_different_session_alternatives_are_retained",
                                  "test_invalid_cost_and_duplicate_observation_rejected"), (),
                     "Keeps negative basis visible, calculates futures price P&L and subtracts supplied spread, roll and financing costs before matching a same-session alternative by direction, sector, liquidity, notional and duration. Authenticated futures/benchmark marks, continuous cohort capture and official roll inputs remain external."),
    "FE-0087": Entry("PARTIAL", ("test_vertical_spread_uses_entry_and_exit_bid_ask_and_fees",
                                  "test_physical_expiry_reports_gross_assignment_cash_and_shares",
                                  "test_mid_only_stale_or_nonmatching_exit_data_is_rejected"), (BLK_DATA,),
                     "Executable entry/exit bid/ask premiums, contract lot multipliers, fees and physical-delivery/cash-settlement expiry scenarios are reported; stale quotes fail closed. Real licensed depth, authoritative settlement/assignment rules and forward cohort outcomes remain blocked."),
    "FE-0088": Entry("DONE", TRIAL_TESTS[2:], (), "Trial budget and approval gate; no bypass path."),
    "FE-0089": Entry("PARTIAL", ("test_negative_inconclusive_and_no_signal_cohorts_are_archived_and_discoverable",
                                  "test_archive_is_immutable_and_no_signal_requires_zero_opportunities"), (),
                     "Append-only cohort archive preserves positive, negative, inconclusive, no-signal and unresolved results with version/evidence hashes and discovery queries. Automatic capture by all research/evaluation runners is not yet wired."),
    "FE-0090": Entry("PARTIAL", ("test_exact_frozen_strategy_is_allowed_and_parameter_changes_are_refused",
                                  "test_unknown_or_pre_cohort_runs_fail_closed",
                                  "test_cohort_binding_cannot_be_overwritten"), (),
                     "Immutable cohort binding freezes strategy/code/model/parameter hashes before first eligible date and blocks prospective runs on changed hashes. Runtime adapters and required human approval/ADRs for new protected trials are not yet connected."),
    # ---- F08 ----
    "FE-0091": Entry("PARTIAL", ("test_daily_after_cost_portfolio_pnl_reconciles_to_ledger_accounts",
                                  "test_mismatch_is_explicit_and_can_fail_closed",
                                  "test_ledger_reconciliation_requires_explicit_pnl_accounts"), (),
                     "FE-0081 daily capital-weighted return model now has an exact-decimal bridge to opening/closing double-entry P&L accounts and an explicit fail-closed mismatch gate. Production paper OMS daily account snapshots and full cash/position valuation wiring remain unconnected."),
    "FE-0092": Entry("PARTIAL", ("test_stationary_bootstrap_is_reproducible_and_labels_inference_limits",
                                  "test_longer_stationary_blocks_retain_dependence_in_clustered_returns",
                                  "test_invalid_sample_order_block_length_and_replicate_count_rejected"), (),
                     "Reproducible Politis-Romano-style stationary-block percentile intervals preserve dependence in ordered daily/overlapping returns and disclose that family-wise adjustment is still needed. Block-length selection, registered analysis plans and full population data remain unconnected."),
    "FE-0093": Entry("PARTIAL", ("test_two_way_cluster_report_accounts_for_both_date_and_instrument_dependence",
                                  "test_cluster_attribution_rejects_duplicate_cells_and_too_few_clusters",
                                  "test_cluster_values_must_be_finite"), (),
                     "Two-way date/instrument clustered mean-attribution diagnostic uses inclusion/exclusion variance, small-sample corrections and reports clipped negative variance. It is not promotion-grade for small cluster counts and is not wired to the cohort attribution pipeline."),
    "FE-0094": Entry("PARTIAL", ("test_joint_stationary_reality_check_is_reproducible_and_family_wide",
                                  "test_familywise_adjustment_flags_large_persistent_alpha",
                                  "test_frozen_candidate_universe_must_be_complete_and_chronological"), (),
                     "Joint stationary-block White Reality Check tests the best mean net alpha across the complete frozen candidate set while resampling dates jointly to retain time and strategy dependence. Protected trial registry, formal hypothesis family capture and prespecified method selection are not wired to research runs."),
    "FE-0095": Entry("PARTIAL", ("test_risk_metrics_include_cash_days_and_dependence_aware_intervals",
                                  "test_profit_factor_is_unavailable_without_losses_and_cash_turnover_is_zero",
                                  "test_risk_report_rejects_unordered_sessions_and_equity_destroying_loss"), (),
                     "Stationary-block intervals accompany max drawdown, worst daily loss, 95% conditional drawdown, profit factor and cumulative turnover, with cash days retained and undefined profit factor disclosed. Sample-plan calibration, live-ledger sourcing and risk/capacity gates remain unconnected."),
    "FE-0096": Entry("PARTIAL", ("test_capacity_curve_degrades_net_alpha_and_caps_at_supported_depth",
                                  "test_fixture_or_low_coverage_never_produces_size_recommendation",
                                  "test_cost_curve_must_be_monotone_and_no_extrapolation_is_allowed"), (BLK_DATA,),
                     "Liquidity-linked cost curve refuses unsupported extrapolation, requires quote/trade coverage and caps conditional size at visible depth; fixture or unauthenticated calibration cannot recommend a size. Real licensed ADV/depth and live cost calibration remain blocked."),
    "FE-0097": Entry("PARTIAL", ("test_provisional_60_session_floor_never_promotes_automatically",
                                  "test_insufficient_sample_continues_and_precision_at_max_is_inconclusive",
                                  "test_exclusions_count_and_plan_contract_cannot_be_violated"), (BLK_TIME,),
                     "Frozen stopping plan requires at least 60 eligible sessions, a predeclared confidence/precision target and a hard maximum; missing precision is inconclusive and no result auto-promotes. Valid independent exchange-session accrual and empirical power design remain subject to elapsed forward observations."),
    "FE-0098": Entry("PARTIAL", ("test_positive_mean_is_insufficient_if_either_lower_bound_includes_zero",
                                  "test_dual_positive_lcb_is_only_a_statistical_signal_not_promotion",
                                  "test_sample_and_receipt_gates_and_interval_consistency_are_enforced"), (BLK_TSA, BLK_TIME),
                     "Dual cash and risk-matched benchmark lower-bound assessment fails closed on intervals touching zero, fewer than 60 eligible sessions or absent verified independent receipts; positive statistical evidence never auto-promotes. Independent timestamp custody and elapsed prospective sample remain blocked."),
    "FE-0099": Entry("PARTIAL", ("test_cost_fill_and_outage_stresses_include_missed_fill_effects",
                                  "test_stress_requires_explicit_baseline_and_valid_assumptions",
                                  "test_zero_fill_is_explicit_and_costs_do_not_turn_into_fictitious_profit"), (),
                     "Joint stress runner compares baseline with inflated fees, adverse fill bps, partial/no fills and outage-exposed orders; reports net P&L, filled notional, foregone counterfactual P&L and cost drag. Stress parameters remain caller-supplied scenarios, not live calibrated probabilities."),
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
