from datetime import date

import pytest

from financial_brain.config import Config
from financial_brain.ingest.mfnav_history import ingest_export, validate_request
from financial_brain.storage.db import Database

URL = "https://www.amfiindia.com/net-asset-value/nav-download"
PAYLOAD = b"""Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date
Open Ended Schemes(Equity Scheme - Large Cap Fund)
Axis Mutual Fund
135762;INF846K01WO1;-;Axis Bluechip Fund;Direct Plan;Growth Option;29.8856;18-Sep-2026
"""


def test_request_limit_accepts_exactly_90_inclusive_days() -> None:
    validate_request(date(2026, 1, 1), date(2026, 3, 31), URL)
    with pytest.raises(ValueError, match="90 calendar days"):
        validate_request(date(2026, 1, 1), date(2026, 4, 1), URL)


def test_request_rejects_bad_order_and_non_amfi_url() -> None:
    with pytest.raises(ValueError, match="precede"):
        validate_request(date(2026, 3, 1), date(2026, 2, 28), URL)
    with pytest.raises(ValueError, match="official HTTPS AMFI"):
        validate_request(date(2026, 9, 18), date(2026, 9, 18), "https://example.com/nav")


def test_history_export_rejects_rows_outside_declared_window(tmp_path) -> None:
    with pytest.raises(ValueError, match="outside"):
        ingest_export(PAYLOAD, start=date(2026, 9, 19), end=date(2026, 9, 20),
                      source_url=URL, cfg=Config(data_root=tmp_path))


def test_history_import_archives_and_records_observation_revision(tmp_path) -> None:
    cfg = Config(data_root=tmp_path).ensure()
    result = ingest_export(PAYLOAD, start=date(2026, 9, 18), end=date(2026, 9, 18),
                           source_url=URL, cfg=cfg)
    assert result["rows"] == 1 and result["max_days_per_export"] == 90
    with Database(cfg).connect() as con:
        row = con.execute("""SELECT nav, source_published_at, retrieved_at,
                                     observed_at, source_document_sha256, lake_key
                              FROM mf_nav_observations""").fetchone()
    assert row[0] == pytest.approx(29.8856)
    assert row[1] is None
    assert row[2] is not None and row[3] is not None
    assert row[4] == result["source_document_sha256"]
    assert row[5] == result["lake_key"]
