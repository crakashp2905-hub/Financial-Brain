from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from financial_brain.config import Config
from financial_brain.ingest import mfnav
from financial_brain.providers.base import FetchResult
from financial_brain.storage.db import Database


def _payload(nav: str) -> bytes:
    return (
        "Scheme Code;ISIN Growth;ISIN Reinvestment;Scheme Name;Plan;Option;NAV;Date\n"
        "Open Ended Schemes(Equity Scheme - Large Cap Fund)\n"
        "Example Mutual Fund\n"
        f"100001;INF000000001;-;Example Fund;Direct Plan;Growth Option;{nav};20-Nov-2026\n"
    ).encode()


class _Provider:
    source, dataset, tier = "AMFI", "mf_nav", 1

    def __init__(self, payload: bytes, retrieved_at: datetime, *, extra: dict | None = None):
        self.payload = payload
        self.retrieved_at = retrieved_at
        self.extra = extra or {}

    def fetch(self, _business_date: date) -> FetchResult:
        return FetchResult(
            payload=self.payload,
            url="https://example.invalid/NAVAll.txt",
            retrieved_at=self.retrieved_at,
            filename="NAVAll.txt",
            content_type="text/plain",
            extra=self.extra,
        )


def test_nav_revisions_are_append_only_and_as_known_queries_do_not_look_ahead(tmp_path):
    cfg = Config(data_root=tmp_path).ensure()
    db = Database(cfg)
    db.migrate()
    nav_date = date(2026, 11, 20)
    first_seen = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    corrected_seen = datetime(2026, 11, 20, 10, 5, tzinfo=timezone.utc)

    with db.connect() as con:
        first = mfnav.ingest(
            con, cfg, nav_date,
            provider=_Provider(_payload("10.2000"), first_seen),
        )
        assert first["observations_recorded"] == 1
        before_first = mfnav.as_known_on(
            con, "100001", nav_date,
            known_at=datetime(2026, 11, 20, 9, 59, tzinfo=timezone.utc),
        )
        assert before_first is None

        first_known = mfnav.as_known_on(con, "100001", nav_date, known_at=first_seen)
        assert first_known is not None and first_known["nav"] == pytest.approx(10.2)
        assert first_known["source_published_at"] is None
        first_key = first_known["lake_key"]

        # Replaying the same payload/key is idempotent, while a changed source file is
        # preserved as a second observation instead of backdating the correction.
        mfnav.ingest(con, cfg, nav_date, provider=_Provider(_payload("10.2000"), first_seen))
        corrected = mfnav.ingest(
            con, cfg, nav_date,
            provider=_Provider(_payload("10.2500"), corrected_seen),
        )

        historical = mfnav.as_known_on(
            con, "100001", nav_date,
            known_at=datetime(2026, 11, 20, 10, 2, tzinfo=timezone.utc),
        )
        latest = mfnav.as_known_on(con, "100001", nav_date, known_at=corrected_seen)
        assert historical is not None and historical["nav"] == pytest.approx(10.2)
        assert latest is not None and latest["nav"] == pytest.approx(10.25)
        assert latest["lake_key"] != first_key
        assert corrected["source_published_at"] is None
        assert con.execute("SELECT count(*) FROM mf_nav_observations").fetchone()[0] == 2
        assert con.execute("SELECT nav FROM mf_nav WHERE scheme_code='100001'").fetchone()[0] == pytest.approx(10.25)


def test_nav_ingest_refuses_naive_availability_timestamps(tmp_path):
    cfg = Config(data_root=tmp_path).ensure()
    db = Database(cfg)
    db.migrate()
    with db.connect() as con, pytest.raises(ValueError, match="retrieved_at must be timezone-aware"):
        mfnav.ingest(
            con, cfg, date(2026, 11, 20),
            provider=_Provider(_payload("10.2"), datetime(2026, 11, 20, 10, 0)),
        )


def test_source_published_timestamp_is_validated_and_preserved(tmp_path):
    cfg = Config(data_root=tmp_path).ensure()
    db = Database(cfg)
    db.migrate()
    retrieved = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    published = datetime(2026, 11, 20, 9, 30, tzinfo=timezone.utc)
    with db.connect() as con:
        mfnav.ingest(
            con, cfg, date(2026, 11, 20),
            provider=_Provider(_payload("10.2"), retrieved,
                               extra={"source_published_at": published}),
        )
        row = mfnav.as_known_on(
            con, "100001", date(2026, 11, 20), known_at=retrieved,
        )
        assert row is not None and row["source_published_at"] == published


def test_ingest_returns_and_attests_quality_report_without_inferred_universe(tmp_path):
    cfg = Config(data_root=tmp_path).ensure()
    db = Database(cfg)
    db.migrate()
    payload = (
        "Scheme Code;ISIN Growth;ISIN Reinvestment;Scheme Name;Plan;Option;NAV;Date\n"
        "Open Ended Schemes(Equity Scheme - Large Cap Fund)\n"
        "Example Mutual Fund\n"
        "100001;INF000000001;-;Example Fund;Direct Plan;Growth Option;10.0;20-Nov-2026\n"
        "100001;INF000000001;-;Example Fund;Direct Plan;Growth Option;11.0;20-Nov-2026\n"
        "100002;-;-;No NAV;Direct Plan;Growth Option;N.A.;20-Nov-2026\n"
    ).encode()
    retrieved = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)

    with db.connect() as con:
        result = mfnav.ingest(
            con, cfg, date(2026, 11, 20),
            provider=_Provider(payload, retrieved),
            stale_after_days=0,
            known_scheme_codes=("100001", "100002", "100003"),
        )

        quality = result["quality"]
        assert quality["repeated_scheme_codes"] == ["100001"]
        assert quality["stale_scheme_codes"] == []
        assert quality["known_universe_supplied"] is True
        assert {item["scheme_code"] for item in quality["unavailable"]} == {
            "100002", "100003",
        }
        evidence = json.loads(con.execute(
            "SELECT value FROM evidence WHERE kind = 'mf_nav_file'"
        ).fetchone()[0])
        assert evidence["quality"] == quality


def test_ingest_rejects_invalid_quality_threshold_before_fetch_or_write(tmp_path):
    cfg = Config(data_root=tmp_path).ensure()
    db = Database(cfg)

    class MustNotFetch:
        def fetch(self, _business_date):
            raise AssertionError("invalid threshold should fail before provider fetch")

    with pytest.raises(ValueError, match="stale_after_days"):
        with db.connect() as con:
            mfnav.ingest(con, cfg, provider=MustNotFetch(), stale_after_days=-1)

