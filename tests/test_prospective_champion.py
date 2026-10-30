from datetime import date, datetime, timezone

import duckdb
import pytest

from financial_brain.evaluation import champion, prospective


TODAY = date(2026, 10, 29)
NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


def _research(last_day=TODAY):
    con = duckdb.connect()
    con.execute("CREATE TABLE adjusted_prices (business_date DATE)")
    con.execute("INSERT INTO adjusted_prices VALUES (?)", [last_day])
    return con


def _fake_record(_research_con, ledger, day):
    ledger.execute(champion.SCHEMA)
    ledger.execute("INSERT INTO champion_picks VALUES (?,?,?,?,?)",
                   [day.isoformat(), champion.CHAMPION, "TEST", "L1", 0.001])
    return {champion.CHAMPION: 1}


def _manifest(ledger, day=TODAY):
    digest = prospective._pick_digest(ledger, day.isoformat())
    h = "a" * 64
    return {"schema": "fb.decision.v1", "day": day.isoformat(), "decision_id": "test-decision",
            "author": "paper-test", "strategy_sha256": h, "model_sha256": h,
            "data_sha256": h, "universe_sha256": h, "picks_sha256": digest,
            "calendar_sha256": h, "cost_model_sha256": h, "risk_limits_sha256": h,
            "decision_at_utc": NOW.isoformat(),
            "data_watermark_at_utc": "2026-10-29T11:59:01Z",
            "exchange_session_id": "NSE-CM-2026-10-29",
            "entry_earliest_at_utc": "2026-10-30T03:45:00Z",
            "exit_scheduled_at_utc": "2026-11-30T10:00:00Z",
            "holding_sessions": 20, "benchmark_id": "NIFTY500-TRI",
            "capital_inr": "100000",
            "predicted_return_fraction": "0.01", "predicted_cost_fraction": "0.001",
            "forecast": {"p10_fraction": "-0.05", "p50_fraction": "0.01",
                         "p90_fraction": "0.06", "probability_exceeds_cost": "0.5"},
            "no_trade_reasons": [],
            "selections": [{"lineage": "L1", "symbol": "TEST", "weight_fraction": "1", "quantity": 1}],
            "exclusions": [], "sources": [{"source_id": "test-source", "sha256": h,
                                      "published_at_utc": "2026-10-29T11:59:00Z",
                                      "received_at_utc": "2026-10-29T11:59:01Z",
                                      "available_at_utc": "2026-10-29T11:59:01Z"}]}


def test_forward_record_attests_write_time(monkeypatch):
    monkeypatch.setattr(champion, "record", _fake_record)
    ledger = duckdb.connect()
    assert prospective.record(_research(), ledger, TODAY, now=NOW) == {champion.CHAMPION: 1}
    report = prospective.audit(ledger)
    assert report["attested_days"] == 1
    assert report["unattested_days"] == 0
    assert report["resolved_attested_days"] == 0
    assert report["invalid_attestations"] == 0
    assert prospective.verified_days(ledger) == set()  # local digest is not independent time proof
    with pytest.raises(champion.ChampionError, match="already attested"):
        prospective.record(_research(), ledger, TODAY, now=NOW)


def test_attestation_fails_closed_after_pick_mutation(monkeypatch):
    monkeypatch.setattr(champion, "record", _fake_record)
    ledger = duckdb.connect()
    prospective.record(_research(), ledger, TODAY, now=NOW)
    ledger.execute("UPDATE champion_picks SET cost = .5 WHERE day = ?", [TODAY.isoformat()])
    assert prospective.verified_days(ledger) == set()
    assert prospective.audit(ledger)["invalid_attestations"] == 1


def test_external_anchor_is_required_and_reverified(monkeypatch):
    monkeypatch.setattr(champion, "record", _fake_record)
    ledger = duckdb.connect()
    prospective.record(_research(), ledger, TODAY, now=NOW)
    anchor_time = NOW.replace(minute=1)
    with pytest.raises(champion.ChampionError, match="manifest required"):
        prospective.attach_anchor(ledger, TODAY, provider="test-signer",
                                  anchored_at_utc=anchor_time, receipt="signed-batch")
    prospective.attach_manifest(ledger, TODAY, _manifest(ledger))
    prospective.attach_anchor(ledger, TODAY, provider="test-signer",
                              anchored_at_utc=anchor_time, receipt="signed-batch")
    assert prospective.verified_days(ledger) == set()
    assert prospective.verified_days(ledger, verifier=lambda *args: args[-1] == "signed-batch") == {str(TODAY)}
    assert prospective.audit(ledger, verifier=lambda *args: args[-1] == "signed-batch")[
        "independently_verified_days"] == 1
    def unavailable(*args):
        raise ConnectionError("external verifier unavailable")
    assert prospective.verified_days(ledger, verifier=unavailable) == set()
    ledger.execute("UPDATE champion_pick_anchors SET receipt = 'tampered'")
    assert prospective.verified_days(ledger, verifier=lambda *args: args[-1] == "signed-batch") == set()


def test_manifest_mutation_or_pick_only_legacy_anchor_never_verifies(monkeypatch):
    monkeypatch.setattr(champion, "record", _fake_record)
    ledger = duckdb.connect()
    prospective.record(_research(), ledger, TODAY, now=NOW)
    prospective.attach_manifest(ledger, TODAY, _manifest(ledger))
    prospective.attach_anchor(ledger, TODAY, provider="test-signer",
                              anchored_at_utc=NOW.replace(minute=1), receipt="signed-batch")
    verifier = lambda *args: args[-1] == "signed-batch"
    assert prospective.verified_days(ledger, verifier=verifier) == {str(TODAY)}
    ledger.execute("UPDATE champion_decision_manifests SET manifest_json = '{}' WHERE day = ?", [str(TODAY)])
    assert prospective.verified_days(ledger, verifier=verifier) == set()
    ledger.execute("UPDATE champion_decision_manifests SET manifest_json = ? WHERE day = ?",
                   [prospective.canonical_bytes(_manifest(ledger)).decode(), str(TODAY)])
    ledger.execute("UPDATE champion_pick_anchors SET manifest_sha256 = NULL")
    assert prospective.verified_days(ledger, verifier=verifier) == set()


def test_forward_record_rejects_backfill_and_future_outcome(monkeypatch):
    monkeypatch.setattr(champion, "record", _fake_record)
    ledger = duckdb.connect()
    with pytest.raises(champion.ChampionError, match="current"):
        prospective.record(_research(date(2026, 10, 20)), ledger,
                           date(2026, 10, 20), now=NOW)
    with pytest.raises(champion.ChampionError, match="future outcomes"):
        prospective.record(_research(TODAY), ledger, date(2026, 10, 28), now=NOW)
    with pytest.raises(champion.ChampionError, match="completed cash session"):
        prospective.record(_research(TODAY), ledger, TODAY,
                           now=datetime(2026, 10, 29, 8, tzinfo=timezone.utc))
    assert prospective.audit(ledger)["status"] == "NO_PICKS"


def test_old_picks_stay_unattested():
    ledger = duckdb.connect()
    ledger.execute(champion.SCHEMA)
    ledger.execute("INSERT INTO champion_picks VALUES (?,?,?,?,?)",
                   [TODAY.isoformat(), champion.CHAMPION, "TEST", "L1", 0.001])
    assert prospective.audit(ledger)["unattested_days"] == 1
    with pytest.raises(champion.ChampionError, match="unattested picks"):
        prospective.record(_research(), ledger, TODAY, now=NOW)


def test_standings_exclude_unattested_cohorts():
    ledger = duckdb.connect()
    ledger.execute(champion.SCHEMA)
    ledger.execute("INSERT INTO champion_cohorts VALUES (?,?,?,?,?,?,?)",
                   [TODAY.isoformat(), champion.CHAMPION, 1, .1, .01, .09, .02])
    ledger.execute("INSERT INTO champion_cohorts VALUES (?,?,?,?,?,?,?)",
                   [TODAY.isoformat(), "challenger", 1, .2, .01, .19, .02])
    assert champion.standings(ledger, eligible_days=prospective.verified_days(ledger))[0]["cohorts"] == 0
