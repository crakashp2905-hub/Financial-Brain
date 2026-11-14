"""F13 - release gates, fixture pack, CLI (FE-0143 .. FE-0152). Offline; fixtures are synthetic and prove nothing about alpha."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from financial_brain.forward_edge import cli, pipeline, release, tracker, trials
from financial_brain.forward_edge.fixtures import EXPECTED, fixture_pack
from financial_brain.forward_edge.modes import Mode, ModeError
from financial_brain.forward_edge.release import ReleaseLabel

REPO = Path(__file__).resolve().parents[1]

# Canonical signature of each fixture session, committed as a file. Asserted on every platform: a Linux CI run must
# produce exactly these bytes (FE-0148).
GOLDEN = json.loads((REPO / "tests/fixtures/forward_edge/golden_signatures.json").read_text(encoding="utf-8"))


# ---- FE-0147 fixture pack ----

@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_failure_class_has_a_complete_example_with_the_expected_outcome(name: str) -> None:
    r = pipeline.run_session(fixture_pack()[name])
    assert r["outcome"] == EXPECTED[name]
    assert r["ledger_balanced"] is True and r["cash_equals_net"] is True
    assert r["physical_orders"] is False and r["broker"] == "NONE" and r["custody"] == "LOCAL_ONLY"


def test_non_trading_outcomes_post_nothing_to_the_ledger() -> None:
    for name in ("no_trade", "rejection", "late_quote", "unknown_order", "missed_fill"):
        r = pipeline.run_session(fixture_pack()[name])
        assert r["net_pnl"] == "0" and not any(t.get("stage") == "ledger" for t in r["trace"]), name


def test_unknown_order_is_recorded_and_never_resent() -> None:
    r = pipeline.run_session(fixture_pack()["unknown_order"])
    assert {"stage": "order", "state": "UNKNOWN", "resend_allowed": False} in r["trace"]


def test_the_adverse_fill_is_worse_than_the_touch_price_and_loses_money() -> None:
    r = pipeline.run_session(fixture_pack()["adverse_fill"])
    fill = next(t for t in r["trace"] if t["stage"] == "execution")
    assert fill["reason"].endswith("+ADVERSE") and float(fill["price"]) > 1500.0 and float(r["net_pnl"]) < 0


# ---- FE-0148 byte-identical signatures ----

def test_golden_file_covers_every_fixture() -> None:
    assert set(GOLDEN) == set(fixture_pack())


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_session_signatures_match_the_canonical_hashes(name: str) -> None:
    assert pipeline.run_session(fixture_pack()[name])["signature"] == GOLDEN[name]


def test_running_a_session_twice_gives_identical_bytes() -> None:
    fx = fixture_pack()["win"]
    assert json.dumps(pipeline.run_session(fx), sort_keys=True) == json.dumps(pipeline.run_session(fx), sort_keys=True)


# ---- the pipeline refuses to leave paper ----

@pytest.mark.parametrize("mode", [Mode.LIVE_CASH, Mode.BROKER_DRYRUN])
def test_pipeline_refuses_cash_and_broker_modes(mode: Mode) -> None:
    with pytest.raises(ModeError, match=mode.value):
        pipeline.run_session(fixture_pack()["win"], mode=mode)


def test_pipeline_refuses_any_named_broker() -> None:
    with pytest.raises(PermissionError):
        pipeline.run_session(fixture_pack()["win"], broker="KITE")


def test_blocked_preflight_leaves_no_decision_and_no_trade() -> None:
    fx = fixture_pack()["win"]
    fx["preflight"] = {"calendar": True, "credentials": True, "disk": True, "feeds": False, "data_contracts": True,
                       "risk": True, "system_health": True, "broker_disabled": True}
    r = pipeline.run_session(fx)
    assert r["outcome"] == "NO_TRADE" and r["reason"] == "PREFLIGHT_BLOCKED" and r["custody"] == "NOT_RECORDED"


# ---- FE-0145 close is idempotent and never verifies incomplete or LOCAL_ONLY evidence ----

def test_close_is_idempotent_and_local_only_is_never_verified() -> None:
    r = pipeline.run_session(fixture_pack()["win"])
    a, b = pipeline.close_session(r), pipeline.close_session(r)
    assert a == b and a["status"] == "CLOSED" and a["verified"] is False and a["eligibility"] == "ELIGIBLE_LOCAL_ONLY"


def test_incomplete_evidence_is_closed_as_incomplete() -> None:
    r = dict(pipeline.run_session(fixture_pack()["win"]))
    r["segments"] = [s for s in r["segments"] if s != "fills"]
    closed = pipeline.close_session(r)
    assert closed["status"] == "CLOSED_INCOMPLETE" and closed["missing"] == ["fills"] and closed["verified"] is False


# ---- FE-0143 .. FE-0146 commands ----

def test_preflight_command_has_exact_exit_codes() -> None:
    out, code = cli.cmd_preflight(None, [])
    assert code == 0 and out["ok"] is True
    out, code = cli.cmd_preflight(None, ["feeds"])
    assert code == 2 and out["failures"] == ["feeds"]


def test_paper_run_is_dry_run_by_default_and_refuses_a_non_dry_run() -> None:
    out, code = cli.cmd_paper_run("win", dry_run=True)
    assert code == 0 and out["outcome"] == "WIN" and out["physical_orders"] is False
    out, code = cli.cmd_paper_run("win", dry_run=False)
    assert code == cli.EXIT["USAGE"] and "broker" in out["error"]
    assert cli.cmd_paper_run("nope", dry_run=True)[1] == cli.EXIT["USAGE"]


def test_close_command_exit_code_reflects_completeness() -> None:
    out, code = cli.cmd_close("win")
    assert code == 0 and out["status"] == "CLOSED"


def test_report_carries_coverage_benchmark_missing_data_and_never_promotes() -> None:
    out, code = cli.cmd_report("2027-01-04")
    assert code == cli.EXIT["INSUFFICIENT"] and out["real_money_enabled"] is False
    assert out["mode"] == "FIXTURE_REPLAY" and out["evidence_class"] == "HISTORICAL_REPLAY"
    assert out["tier"] == "HISTORICAL_REPLAY" and out["independently_attested"] is False
    assert out["promotion_verdict"] != "PROMOTION_GRADE"
    assert "EXECUTION_STRESS_MISSING" in out["reasons"]
    assert out["benchmark"] and out["coverage"] and "INDEPENDENT_TIMESTAMP_RECEIPTS_NOT_CONFIGURED" in out["missing_data"]
    assert out["independent_forward_sessions"] == 0


def test_cli_main_prints_json_and_returns_the_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["paper-run", "--fixture", "loss"]) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "LOSS"
    assert cli.main(["report"]) == 4


# ---- FE-0149 capability matrix ----

def test_committed_capability_matrix_matches_the_tracker_and_claims_no_independent_evidence() -> None:
    committed = (REPO / "docs/forward-edge/capability-matrix.json").read_text(encoding="utf-8")
    assert committed == tracker.render_capability_matrix()
    m = json.loads(committed)
    assert m["independently_evidenced"] == []
    assert len(m["implemented"]) + len(m["partial"]) + len(m["blocked"]) + len(m["not_started"]) == 152


# ---- FE-0150 no regressions in protected records or existing workflows ----

def test_protected_records_are_still_intact_and_legacy_kernel_entry_points_import() -> None:
    assert trials.verify_pins(REPO) == []
    from financial_brain.kernel.events import EventKernel  # noqa: F401
    from financial_brain.kernel.oms import Oms  # noqa: F401
    from financial_brain.kernel.pilot import run_pilot, synthetic_tape  # noqa: F401


# ---- FE-0151 docs ----

def test_operations_guide_has_the_required_sections_and_commands() -> None:
    text = (REPO / "docs/forward-edge/OPERATIONS.md").read_text(encoding="utf-8")
    for section in ("Source rights", "Privacy", "Source configuration", "Troubleshooting", "Audit procedure"):
        assert f"## {section}" in text
    assert "forward_edge.cli paper-run" in text and "PHYSICAL_ORDERS_ENABLED" in text


# ---- FE-0152 release labels ----

FULL = {
    "tests_green": True,
    "protected_records_intact": True,
    "physical_orders_disabled": True,
    "lint_green": True,
    "types_green": True,
    "migrations_green": True,
    "coverage_green": True,
    "ubuntu_green": True,
    "windows_green": True,
    "skip_count_reported": True,
    "skip_count": 1,
}


def test_label_never_exceeds_the_evidence() -> None:
    assert release.highest_label({}) is None
    assert release.highest_label(FULL) is ReleaseLabel.PAPER_READY
    release.assert_label_allowed(ReleaseLabel.PAPER_READY, FULL)
    for higher in (ReleaseLabel.FORWARD_VERIFIED, ReleaseLabel.BROKER_READY, ReleaseLabel.LIVE_APPROVED):
        with pytest.raises(release.LabelError):
            release.assert_label_allowed(higher, FULL)


def test_each_label_needs_everything_below_it() -> None:
    fv = {**FULL, "independent_custody_verified": True, "promotion_grade_verdict": True, "verified_sessions": 80}
    assert release.highest_label(fv) is ReleaseLabel.FORWARD_VERIFIED
    assert release.highest_label({**fv, "verified_sessions": 59}) is ReleaseLabel.PAPER_READY
    br = {**fv, "broker_compliance_verified": True, "rehearsal_gate_open": True}
    assert release.highest_label(br) is ReleaseLabel.BROKER_READY
    assert release.highest_label({**br, "owner_live_approval_valid": True}) is ReleaseLabel.LIVE_APPROVED
    assert release.highest_label({**FULL, "tests_green": False}) is None


def test_release_gate_fails_closed_for_each_ci_check_or_missing_skip_summary() -> None:
    assert release.ci_quality_gate(FULL) is True
    for key in release.REQUIRED_CI_CHECKS:
        assert release.highest_label({**FULL, key: False}) is None
    assert release.highest_label({key: value for key, value in FULL.items()
                                  if key != "skip_count"}) is None
    assert release.highest_label({**FULL, "skip_count_reported": False}) is None
    assert release.highest_label({**FULL, "skip_count": -1}) is None
    assert release.highest_label({**FULL, "skip_count": True}) is None


def test_ci_workflow_exposes_platform_checks_and_test_skip_summary() -> None:
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "os: [ubuntu-latest, windows-latest]" in workflow
    assert "name: Lint" in workflow and "python -m ruff check src tests" in workflow
    assert "Type check" in workflow and "mypy" in workflow
    assert "Schema migrations" in workflow and "tests/test_migrations.py" in workflow
    assert "Critical-module coverage" in workflow
    assert "Test suite (with skip summary)" in workflow
    assert "python -m coverage run --source=financial_brain -m pytest -q -ra" in workflow


def test_this_repository_today_can_claim_at_most_paper_ready() -> None:
    today = {**FULL, "independent_custody_verified": False, "promotion_grade_verdict": False}
    assert release.highest_label(today) is ReleaseLabel.PAPER_READY
