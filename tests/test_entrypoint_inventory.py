import json

from scripts.entrypoint_inventory import DEFAULT_OUTPUT, ROOT, build, render


def test_entrypoint_inventory_is_current_and_covers_all_contract_owner_symbols():
    report = build(ROOT)
    assert DEFAULT_OUTPUT.read_bytes().replace(b"\r\n", b"\n") == render(report)
    assert report["domains"] == [
        "forward_edge", "paper", "risk", "execution", "evaluation", "forecasting", "strategies", "portfolio"
    ]
    assert report["unresolved_contract_owner_ids"] == []
    assert {row["contract_id"] for row in report["contract_owner_bindings"]} == {
        row["id"] for row in json.loads((ROOT / "docs/edge-program/authoritative-contracts.v1.json").read_text())["contracts"]
    }
    assert all(row["exact_symbol"] for row in report["contract_owner_bindings"])


def test_repeated_callable_names_remain_distinct_candidate_ids():
    report = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    repeated_run = next(row for row in report["duplicate_symbol_names"] if row["name"] == "run")
    assert repeated_run["count"] > 1
    assert len(set(repeated_run["candidate_ids"])) == repeated_run["count"]


def test_unreviewed_candidates_are_not_mislabeled_as_authoritative():
    report = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    unreviewed = [row for row in report["candidates"] if row["related_role"] == "UNREVIEWED"]
    assert len(unreviewed) == report["unreviewed_candidate_count"]
    assert all(not row["canonical_contract_ids"] for row in unreviewed)
