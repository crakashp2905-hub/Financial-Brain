from datetime import datetime, timezone

import pytest

from financial_brain.forward_edge.research_library import (
    ResearchLibraryError,
    ResearchVariantJournal,
    canonical_sha256,
)


def _open(journal, *, variants=None):
    variants = variants or [{"variant_id": "a", "parameters": {"window": 5}}]
    return journal.open_run(mechanism_id="test", mechanism_source_sha256="a" * 64,
                            variants=variants, seed=17,
                            at_utc=datetime(2026, 1, 1, tzinfo=timezone.utc))


def test_every_declared_variant_must_be_recorded_before_closing_and_recovery(tmp_path):
    path = tmp_path / "research.jsonl"
    journal = ResearchVariantJournal(path)
    run_id = _open(journal, variants=[{"variant_id": name, "parameters": {}} for name in ("a", "b")])
    digests = journal.variant_digests(run_id)
    journal.record_result(run_id, digests["a"], outcome="EVALUATED", details={"trades": 0})
    with pytest.raises(ResearchLibraryError, match="unlogged"):
        journal.close_run(run_id)
    journal.abort_pending(run_id, reason="interrupted")
    journal.close_run(run_id)

    recovered = ResearchVariantJournal(path)
    assert recovered.run_state(run_id) == {
        "variant_count": 2, "recorded_count": 2,
        "outcomes": {digests["a"]: "EVALUATED", digests["b"]: "ABORTED"}, "closed": True,
    }


def test_duplicate_results_and_nonresearch_promotion_are_rejected(tmp_path):
    journal = ResearchVariantJournal(tmp_path / "research.jsonl")
    run_id = _open(journal)
    digest = next(iter(journal.variant_digests(run_id).values()))
    journal.record_result(run_id, digest, outcome="EVALUATED", details={"score": float("nan")})
    with pytest.raises(ResearchLibraryError, match="pending"):
        journal.record_result(run_id, digest, outcome="FAILED", details={})
    assert canonical_sha256({"score": float("nan")}) != canonical_sha256({"score": float("inf")})


def test_variant_budget_and_duplicate_ids_are_rejected(tmp_path):
    journal = ResearchVariantJournal(tmp_path / "research.jsonl", max_variants_per_run=1)
    with pytest.raises(ResearchLibraryError, match="declare"):
        _open(journal, variants=[{"variant_id": str(i), "parameters": {}} for i in range(2)])
    journal = ResearchVariantJournal(tmp_path / "duplicates.jsonl", max_variants_per_run=2)
    with pytest.raises(ResearchLibraryError, match="unique"):
        _open(journal, variants=[{"variant_id": "same", "parameters": {}},
                                 {"variant_id": "same", "parameters": {}}])


def test_bounded_search_durably_records_one_result_per_frozen_variant(monkeypatch, tmp_path):
    from financial_brain.evaluation import bounded_search as search

    monkeypatch.setattr(search.pd, "read_parquet", lambda _path: __import__("pandas").DataFrame(
        {"start": ["2016-01-01"], "symbol": ["ABC"]}))
    bars = __import__("pandas").DataFrame({"session": [__import__("pandas").Timestamp("2016-01-04")]})
    monkeypatch.setattr(search, "load_data", lambda _symbols, db: ({"ABC": bars}, __import__("pandas").Series(dtype=float)))
    monkeypatch.setattr(search, "evaluate", lambda *_args, **_kwargs: __import__("pandas").DataFrame(
        columns=["variant", "family", "symbol", "signal_day", "net", "benchmark_net", "excess_net"]))

    def summarize(_events, catalogue):
        rows = [{"variant": v.name, "family": v.family, "trades": 0} for v in catalogue]
        return {"status": "TRAINING_SEARCH_ONLY_NOT_A_TRIAL", "searched_variants": len(catalogue),
                "selected_on_training": None, "all_variants": rows}

    monkeypatch.setattr(search, "summarize", summarize)
    monkeypatch.setattr(search, "multiplicity_diagnostic", lambda *_args: {"status": "NO_EVENTS"})
    audit_path = tmp_path / "search.jsonl"
    result = search.run(basis_path=tmp_path / "basis.parquet", research_db=tmp_path / "db.duckdb",
                        sample_size=1, random_budget=1, seed=7, audit_path=audit_path)
    state = ResearchVariantJournal(audit_path).run_state(result["research_audit_run_id"])
    assert state["closed"] is True
    assert state["recorded_count"] == result["searched_variants"]
    assert set(state["outcomes"].values()) == {"EVALUATED"}
