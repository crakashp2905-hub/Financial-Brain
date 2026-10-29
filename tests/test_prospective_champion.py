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


def test_forward_record_attests_write_time(monkeypatch):
    monkeypatch.setattr(champion, "record", _fake_record)
    ledger = duckdb.connect()
    assert prospective.record(_research(), ledger, TODAY, now=NOW) == {champion.CHAMPION: 1}
    report = prospective.audit(ledger)
    assert report["attested_days"] == 1
    assert report["unattested_days"] == 0
    assert report["resolved_attested_days"] == 0
    assert report["invalid_attestations"] == 0
    with pytest.raises(champion.ChampionError, match="already attested"):
        prospective.record(_research(), ledger, TODAY, now=NOW)


def test_attestation_fails_closed_after_pick_mutation(monkeypatch):
    monkeypatch.setattr(champion, "record", _fake_record)
    ledger = duckdb.connect()
    prospective.record(_research(), ledger, TODAY, now=NOW)
    ledger.execute("UPDATE champion_picks SET cost = .5 WHERE day = ?", [TODAY.isoformat()])
    assert prospective.verified_days(ledger) == set()
    assert prospective.audit(ledger)["invalid_attestations"] == 1


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
