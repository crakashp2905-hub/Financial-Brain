from dataclasses import replace

import pytest

from financial_brain.forward_edge import charter
from financial_brain.forward_edge.decision_custody import Cohort, CohortSchedule

SHA = "a" * 64


def identity(**changes):
    values = {
        "strategy_code_sha256": SHA,
        "model_code_sha256": "b" * 64,
        "data_snapshot_sha256": "c" * 64,
        "data_rights_id": "kite-connect-owner-account",
        "data_rights_sha256": "d" * 64,
        "market_calendar_id": "NSE-CASH-v1",
        "market_calendar_sha256": "e" * 64,
        "universe_sha256": "f" * 64,
        "execution_cost_id": "owner-cost-schedule-v1",
        "execution_cost_sha256": "1" * 64,
        "benchmarks": (("NIFTY50_CASH", "2" * 64),),
    }
    values.update(changes)
    return charter.EvaluationIdentity(**values)


def experiment(*, version=1, prior=None, start="2026-10-05", end="2026-10-30", evaluation_identity=None):
    return charter.ExperimentCharter(
        charter_id="strategy-A", version=version, strategy_id="A", code_sha256=SHA, capital_inr="1000000",
        start_on=start, end_on=end, universe=("INFY", "TCS"), benchmarks=("NIFTY50_CASH",),
        horizon_sessions=3, parameters=(("lookback", "20"),), prior_sha256=prior,
        evaluation_identity=evaluation_identity or identity(),
    )


def schedule(spec, *, cohort_id="C1", start="2026-10-05", end="2026-10-08"):
    return CohortSchedule(spec.sha256(), (Cohort(cohort_id, start, end),))


def test_every_source_cost_calendar_and_benchmark_digest_changes_identity_hash():
    base = identity()
    spec = experiment(evaluation_identity=base)
    changes = [
        {"model_code_sha256": "3" * 64},
        {"data_snapshot_sha256": "4" * 64},
        {"data_rights_sha256": "5" * 64},
        {"market_calendar_sha256": "6" * 64},
        {"universe_sha256": "7" * 64},
        {"execution_cost_sha256": "8" * 64},
        {"benchmarks": (("NIFTY50_CASH", "9" * 64),)},
    ]
    for change in changes:
        changed = experiment(evaluation_identity=replace(base, **change))
        assert changed.sha256() != spec.sha256()
    assert charter.EvaluationIdentity.from_dict(base.as_dict()) == base


def test_durable_registry_rejects_retroactive_benchmark_and_endpoint_edits(tmp_path):
    path = tmp_path / "charters.jsonl"
    registry = charter.CharterRegistry(path)
    v1 = experiment()
    v1_schedule = schedule(v1)
    digest = registry.register(v1, v1_schedule)
    assert digest == v1.sha256()
    assert registry.register(v1, v1_schedule) == digest  # idempotent retry
    registry.record_cohort_close("strategy-A", "C1", closed_on="2026-10-09", outcome_sha256="9" * 64)
    with pytest.raises(charter.CharterError, match="immutable"):
        registry.record_cohort_close("strategy-A", "C1", closed_on="2026-10-09", outcome_sha256="8" * 64)

    favorable_benchmark = replace(
        v1, evaluation_identity=replace(identity(), benchmarks=(("NIFTY50_CASH", "3" * 64),)))
    with pytest.raises(charter.CharterError, match="next version"):
        registry.register(favorable_benchmark, schedule(favorable_benchmark))

    changed_endpoint = CohortSchedule(v1.sha256(), (Cohort("C1", "2026-10-05", "2026-10-07"),))
    with pytest.raises(charter.CharterError, match="next version"):
        registry.register(v1, changed_endpoint)

    v2 = v1.revise(
        new_start_on="2026-10-15", end_on="2026-11-20",
        evaluation_identity=replace(identity(), market_calendar_sha256="4" * 64,
                                    benchmarks=(("NIFTY50_CASH", "3" * 64),)),
    )
    v2_schedule = schedule(v2, cohort_id="C2", start="2026-10-15", end="2026-10-20")
    assert registry.register(v2, v2_schedule) == v2.sha256()

    recovered = charter.CharterRegistry(path)
    recovered_v2, recovered_schedule, recovered_sha = recovered.latest("strategy-A")
    assert recovered_v2 == v2 and recovered_schedule == v2_schedule and recovered_sha == v2.sha256()
    assert recovered.register(v2, v2_schedule) == recovered_sha


def test_protected_charter_requires_all_evaluation_identity_components():
    legacy = replace(experiment(), evaluation_identity=None)
    # This is a compatibility-only charter and cannot enter the protected registry.
    with pytest.raises(charter.CharterError, match="complete market/data-rights/model/cost identity"):
        legacy.require_evaluation_identity()
    with pytest.raises(charter.CharterError, match="SHA-256"):
        identity(market_calendar_sha256="not-a-hash")
    with pytest.raises(charter.CharterError, match="identifiers"):
        identity(data_rights_id=" ")


@pytest.mark.parametrize(
    ("close_fields", "message"),
    [
        ({"closed_on": "2026-10-07", "outcome_sha256": "9" * 64}, "frozen endpoint"),
        ({"closed_on": "2026-10-09", "outcome_sha256": "bad"}, "content-addressed"),
        ({"closed_on": "2026-10-09", "outcome_sha256": "9" * 64, "cohort_id": "UNKNOWN"},
         "frozen endpoint"),
    ],
)
def test_registry_revalidates_cohort_closure_events_during_recovery(tmp_path, close_fields, message):
    path = tmp_path / "charters.jsonl"
    registry = charter.CharterRegistry(path)
    spec = experiment()
    frozen_schedule = schedule(spec)
    registry.register(spec, frozen_schedule)
    registry.journal.append({
        "t": "COHORT_CLOSED",
        "charter_id": "strategy-A",
        "charter_sha256": spec.sha256(),
        "cohort_id": "C1",
        "closed_on": "2026-10-09",
        "outcome_sha256": "9" * 64,
        **close_fields,
    })
    with pytest.raises(charter.CharterError, match=message):
        charter.CharterRegistry(path)
