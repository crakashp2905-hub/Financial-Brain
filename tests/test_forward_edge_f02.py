"""F02 - one strategy registry (FE-0022 .. FE-0032). Offline; uses the real trial ledger read-only."""
from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from financial_brain.forward_edge import registry as R
from financial_brain.forward_edge.registry import Benchmark, Registry, RegistryError, Status, StrategySpec

REPO = Path(__file__).resolve().parents[1]
SHA = "b" * 64


def _spec(**over) -> StrategySpec:
    base = dict(strategy_id="M1", family="momentum", version=1, code_sha256=SHA, asset_scope=("NSE_EQ",),
                decision_clock="close_t", direction="LONG", exit_rule="time_20", horizon_sessions=20,
                data_requirements=("adjusted_close",), source_lineage="docs/trials/ledger.jsonl#M1",
                status=Status.EXPLORATORY)
    base.update(over)
    return StrategySpec(**base)


# ---- FE-0023 immutable, validated spec ----

@pytest.mark.parametrize("field", ["strategy_id", "family", "decision_clock", "exit_rule", "source_lineage"])
def test_missing_required_fields_fail_registration(field: str) -> None:
    with pytest.raises(RegistryError, match="missing fields"):
        _spec(**{field: " "})


def test_spec_validation_rules() -> None:
    with pytest.raises(RegistryError, match="sha256"):
        _spec(code_sha256="nothex")
    with pytest.raises(RegistryError, match="direction"):
        _spec(direction="SIDEWAYS")
    with pytest.raises(RegistryError, match="horizon"):
        _spec(horizon_sessions=0)
    with pytest.raises(RegistryError, match="exclusion reason"):
        _spec(status=Status.FAILED)
    with pytest.raises(RegistryError, match="benchmark"):
        _spec(status=Status.PAPER_ELIGIBLE)


def test_spec_is_immutable() -> None:
    spec = _spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.status = Status.VERIFIED  # type: ignore[misc]


# ---- FE-0024 / FE-0029 status taxonomy, versioned transitions ----

def test_failed_strategy_cannot_become_eligible_frozen_or_verified() -> None:
    reg = Registry()
    reg.register(_spec(status=Status.FAILED, exclusion_reason="G3 fail", benchmark_id=None))
    for target in (Status.PAPER_ELIGIBLE, Status.FROZEN_CHALLENGER, Status.VERIFIED, Status.SHADOW_ONLY):
        with pytest.raises(RegistryError, match="not allowed"):
            reg.transition("M1", target, reason="try", human_approved=True, custody_verified=True)
    assert reg.get("M1").status is Status.FAILED


def test_paper_enrolment_needs_a_human_and_has_a_benchmark() -> None:
    reg = Registry()
    reg.register(_spec(status=Status.SHADOW_ONLY, benchmark_id="NIFTY_CASH"))
    with pytest.raises(RegistryError, match="human"):
        reg.transition("M1", Status.PAPER_ELIGIBLE, reason="x")
    enrolled = reg.transition("M1", Status.PAPER_ELIGIBLE, reason="preregistered", human_approved=True)
    assert enrolled.can_place_paper_intents
    frozen = reg.transition("M1", Status.FROZEN_CHALLENGER, reason="frozen charter")
    assert frozen.status is Status.FROZEN_CHALLENGER


def test_paper_status_without_benchmark_is_refused_at_spec_level() -> None:
    reg = Registry()
    reg.register(_spec(status=Status.SHADOW_ONLY))  # no benchmark
    with pytest.raises(RegistryError, match="benchmark"):
        reg.transition("M1", Status.PAPER_ELIGIBLE, reason="x", human_approved=True)
    assert reg.get("M1").status is Status.SHADOW_ONLY


def test_transition_creates_a_new_version_and_keeps_the_old_one() -> None:
    reg = Registry()
    original = reg.register(_spec(status=Status.EXPLORATORY))
    nxt = reg.transition("M1", Status.FAILED, reason="ledger FAIL at G3")
    assert nxt.version == 2 and nxt.history and nxt.history[0].startswith("v1:EXPLORATORY->FAILED")
    assert original.status is Status.EXPLORATORY  # the old object is untouched
    with pytest.raises(RegistryError, match="already registered"):
        reg.register(_spec())


def test_verified_needs_custody() -> None:
    reg = Registry()
    reg.register(_spec(status=Status.FROZEN_CHALLENGER, benchmark_id="NIFTY_CASH"))
    with pytest.raises(RegistryError, match="independently verified"):
        reg.transition("M1", Status.VERIFIED, reason="x")


# ---- the real 2026Q4 trial ledger maps to FAILED entries ----

def test_every_recorded_trial_maps_to_a_failed_registry_entry() -> None:
    rows = [json.loads(line) for line in (REPO / "docs/trials/ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    trials = [r for r in rows if r.get("kind") == "trial"]
    assert len(trials) == 13
    reg = Registry()
    for i, row in enumerate(trials):
        lineage = hashlib.sha256(json.dumps(row, sort_keys=True).encode("utf-8")).hexdigest()
        reg.register(_spec(strategy_id=f"TRIAL-{i + 1:02d}", family=row["name"], code_sha256=lineage,
                           source_lineage=f"docs/trials/ledger.jsonl#trial-{i + 1}", status=Status.FAILED,
                           exclusion_reason=f"ledger verdict {row['verdict']} ({','.join(row['failed'])})"))
    assert len(reg) == 13
    assert all(reg.get(f"TRIAL-{i + 1:02d}").status is Status.FAILED for i in range(13))


# ---- FE-0025 benchmarks frozen before results ----

def test_benchmark_must_be_frozen_before_first_forward_result() -> None:
    b = Benchmark("NIFTY_CASH", "INDEX", SHA, date(2027, 1, 1))
    R.assert_benchmark_frozen_before(b, date(2027, 1, 4))
    with pytest.raises(RegistryError, match="before the first forward result"):
        R.assert_benchmark_frozen_before(b, date(2027, 1, 1))
    with pytest.raises(RegistryError, match="kind"):
        Benchmark("X", "GUESS", SHA, date(2027, 1, 1))


# ---- FE-0026 shadow adapters cannot place orders ----

@pytest.mark.parametrize("status", [Status.FAILED, Status.EXPLORATORY, Status.SHADOW_ONLY])
def test_failed_exploratory_and_shadow_families_never_get_order_intents(status: Status) -> None:
    spec = _spec(status=status, exclusion_reason="r" if status is Status.FAILED else None)
    assert R.order_intent_allowed(spec) is False


def test_only_paper_capable_statuses_get_order_intents() -> None:
    spec = _spec(status=Status.PAPER_ELIGIBLE, benchmark_id="NIFTY_CASH")
    assert R.order_intent_allowed(spec) is True


def test_shadow_signal_has_no_quantity_and_rejects_unknown_values() -> None:
    sig = R.shadow_signal(_spec(), "2027-01-04T09:15:00+05:30", "LONG")
    assert not hasattr(sig, "quantity")
    with pytest.raises(RegistryError):
        R.shadow_signal(_spec(), "2027-01-04", "BUY_100")


# ---- FE-0030 null and placebo controls ----

def test_null_portfolio_holds_nothing_every_day() -> None:
    days = ["2027-01-04", "2027-01-05"]
    assert R.null_portfolio(days) == {"2027-01-04": [], "2027-01-05": []}


def test_placebo_is_seeded_and_sized() -> None:
    universe = ["A", "B", "C", "D", "E"]
    days = ["d1", "d2", "d3"]
    a = R.placebo_portfolio(universe, days, n=2, seed=7)
    assert a == R.placebo_portfolio(universe, days, n=2, seed=7)
    assert a != R.placebo_portfolio(universe, days, n=2, seed=8)
    assert all(len(v) == 2 for v in a.values())
    with pytest.raises(RegistryError):
        R.placebo_portfolio(universe, days, n=6, seed=1)


# ---- FE-0032 selection labels; FE-0031 dedupe ----

def test_selection_on_shadow_results_is_exploratory_unless_preregistered() -> None:
    assert R.selection_label(False) == "EXPLORATORY_ONLY"
    assert R.selection_label(True) == "CONFIRMATORY_CANDIDATE"


def test_correlated_candidates_are_deduplicated() -> None:
    base = [0.01 * ((i * 37) % 11 - 5) for i in range(40)]
    returns = {
        "alpha": base,
        "alpha_copy": [x * 2 + 0.0001 for x in base],   # perfectly correlated with alpha
        "independent": [0.01 * ((i * 53 + 7) % 13 - 6) for i in range(40)],
    }
    kept, dropped = R.dedupe_correlated(returns, threshold=0.9)
    assert "alpha" in kept and "alpha_copy" in dropped and "independent" in kept
    with pytest.raises(RegistryError):
        R.dedupe_correlated(returns, threshold=0)


# ---- FE-0022 file-level inventory ----

def test_inventory_lists_the_existing_strategy_modules_with_hashes() -> None:
    rows = R.inventory(REPO)
    paths = {r["path"] for r in rows}
    assert "src/financial_brain/strategies/breakouts.py" in paths
    assert "src/financial_brain/paper/engine.py" in paths
    assert all(len(r["sha256"]) == 64 for r in rows)
