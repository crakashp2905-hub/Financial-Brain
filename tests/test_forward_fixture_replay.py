import pytest

from financial_brain.forward_edge.durable import DurableJournal
from financial_brain.forward_edge.fixtures import EXPECTED, fixture_pack
from financial_brain.forward_edge.fixture_replay import run_fixture_on_forward_runner
from financial_brain.forward_edge.modes import Mode, ModeError
from financial_brain.forward_edge.pipeline import run_session as legacy_pipeline_session


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture_outcome_parity_runs_through_durable_forward_runner(tmp_path, name):
    fixture = fixture_pack()[name]
    journal_path = tmp_path / f"{name}.jsonl"

    replay = run_fixture_on_forward_runner(fixture, journal_path)
    baseline = legacy_pipeline_session(fixture)

    assert replay["outcome"] == EXPECTED[name] == baseline["outcome"]
    assert replay["physical_orders"] is False
    records = DurableJournal(journal_path).records()
    assert any(row["t"] == "SESSION_PLAN" for row in records)
    if name == "unknown_order":
        assert any(row["t"] == "OUTBOX_UNKNOWN" for row in records)
        assert not any(row["t"] in {"PAPER_EXECUTION", "OUTBOX_ACK", "SESSION_CLOSED"} for row in records)
    else:
        assert any(row["t"] == "SESSION_CLOSED" for row in records)
        receipt = replay["runner_result"].get("execution")
        if receipt is not None:
            assert receipt["fidelity"] in {"SIMULATED_QUOTE", "SIMULATED_BAR"}
            quote_hashes = {row["event_sha256"] for row in receipt["quote_inputs"]}
            assert quote_hashes == set(receipt["quote_event_sha256s"])
            for fill in receipt["fills"]:
                assert set(fill["quote_input_ids"]) <= {
                    row["input_id"] for row in receipt["quote_inputs"]
                }


def test_fixture_replay_blocked_preflight_matches_no_trade_baseline(tmp_path):
    fixture = fixture_pack()["win"]
    fixture["preflight"] = {"calendar": True, "credentials": True, "disk": True, "feeds": False,
                            "data_contracts": True, "risk": True, "system_health": True,
                            "broker_disabled": True}

    replay = run_fixture_on_forward_runner(fixture, tmp_path / "blocked.jsonl")
    baseline = legacy_pipeline_session(fixture)

    assert replay["outcome"] == baseline["outcome"] == "NO_TRADE"
    assert replay["reason"] == baseline["reason"] == "PREFLIGHT_BLOCKED"


@pytest.mark.parametrize("mode", [Mode.LIVE_CASH, Mode.BROKER_DRYRUN])
def test_fixture_replay_refuses_modes_that_can_leave_paper(tmp_path, mode):
    with pytest.raises(ModeError):
        run_fixture_on_forward_runner(fixture_pack()["win"], tmp_path / "forbidden.jsonl", mode=mode)
