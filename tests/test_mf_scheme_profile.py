from datetime import date

import pytest

from financial_brain.config import Config
from financial_brain.ingest.mf_scheme_profile import ingest
from financial_brain.providers.amfi_scheme_profile import parse
from financial_brain.providers.base import FetchError
from financial_brain.storage.db import Database

URL = "https://amc.example.in/scheme/sid.pdf"
HASH = "a" * 64
CSV = (
    "scheme_code,plan,option,isin,riskometer,benchmark_name,benchmark_return_type,"
    "manager,objective,effective_from,effective_to,scheme_status,status_effective_from,"
    "successor_scheme_code,status_reason,expense_ratio_pct,expense_ratio_plan,"
    "expense_effective_from,expense_effective_to,aum_inr,aum_as_of,minimum_initial_inr,"
    "minimum_additional_inr,minimum_effective_from,minimum_effective_to,exit_load_schedule,"
    "exit_load_effective_from,exit_load_effective_to,exit_restrictions,"
    "restriction_effective_from,restriction_effective_to,source_url,source_document_sha256,"
    "source_published_at\n"
    f"135762,Direct Plan,Growth Option,INF846K01WO1,Very High,Nifty 50 TRI,TRI,"
    f"A Manager,Long term capital growth,2026-01-01,,ACTIVE,2026-01-01,,,"
    f"0.65,DIRECT,2026-01-01,,1000000,2026-11-01,500,100,2026-01-01,,"
    f"1% before 7 days,2026-01-01,,notice required,2026-01-01,,{URL},{HASH},"
    "2026-11-20T10:00:00+05:30\n"
).encode()


def test_scheme_profile_parser_preserves_source_and_effective_dates() -> None:
    row = parse(CSV, date(2026, 11, 23))[0]
    assert row["scheme_code"] == "135762"
    assert row["benchmark_return_type"] == "TRI"
    assert row["effective_from"] == date(2026, 1, 1)
    assert row["effective_to"] is None
    assert row["scheme_status"] == "ACTIVE"
    assert row["status_effective_from"] == date(2026, 1, 1)
    assert str(row["expense_ratio_pct"]) == "0.65"
    assert row["expense_ratio_plan"] == "DIRECT"
    assert str(row["aum_inr"]) == "1000000"
    assert row["aum_as_of"] == date(2026, 11, 1)
    assert row["exit_load_schedule"] == "1% before 7 days"
    assert row["exit_load_effective_from"] == date(2026, 1, 1)
    assert row["exit_restrictions"] == "notice required"
    assert row["source_url"] == URL and row["source_document_sha256"] == HASH


@pytest.mark.parametrize("payload", [
    CSV.replace(HASH.encode(), b"bad"),
    CSV.replace(b",Nifty 50 TRI,TRI,", b",Nifty 50 TRI,,"),
    CSV.replace(b"2026-01-01,,", b"2026-12-01,2026-01-01,"),
    CSV.replace(b",ACTIVE,2026-01-01,,,0.65", b",MERGED,2026-01-01,,,0.65"),
    CSV.replace(b",0.65,DIRECT,2026-01-01,,", b",0.65,,2026-01-01,,"),
])
def test_scheme_profile_parser_rejects_bad_provenance_or_dates(payload: bytes) -> None:
    with pytest.raises(FetchError):
        parse(payload, date(2026, 11, 23))


def test_scheme_profile_import_is_archived_and_idempotent(tmp_path) -> None:
    cfg = Config(data_root=tmp_path).ensure()
    result = ingest(CSV, date(2026, 11, 23), cfg)
    again = ingest(CSV, date(2026, 11, 23), cfg)
    assert result == again
    with Database(cfg).connect() as con:
        rows = con.execute("""SELECT scheme_code, benchmark_name, benchmark_return_type,
                                     manager, objective, effective_from, scheme_status,
                                     status_effective_from, expense_ratio_pct, aum_inr,
                                     minimum_initial_inr, exit_load_schedule, exit_restrictions,
                                     observed_on,
                                     source_document_sha256, lake_key
                              FROM mf_scheme_profile_disclosures""").fetchall()
    closed_csv = CSV.replace(b",ACTIVE,2026-01-01,,,0.65",
                             b",CLOSED_FOR_SUBSCRIPTION,2026-11-24,,,0.65")
    ingest(closed_csv, date(2026, 11, 24), cfg)
    with Database(cfg).connect() as con:
        lifecycle = con.execute("""SELECT scheme_status, status_effective_from
                                    FROM mf_scheme_profile_disclosures
                                    ORDER BY observed_on""").fetchall()
    assert len(rows) == 1
    assert rows[0][:8] == ("135762", "Nifty 50 TRI", "TRI", "A Manager",
                           "Long term capital growth", date(2026, 1, 1), "ACTIVE",
                           date(2026, 1, 1))
    assert str(rows[0][8]) == "0.65000000"
    assert str(rows[0][9]) == "1000000.00"
    assert str(rows[0][10]) == "500.00"
    assert rows[0][11:13] == ("1% before 7 days", "notice required")
    assert rows[0][13] == date(2026, 11, 23)
    assert rows[0][14] == HASH
    assert rows[0][15].startswith("LOCAL_IMPORT/mf_scheme_profiles/")
    assert lifecycle == [("ACTIVE", date(2026, 1, 1)),
                         ("CLOSED_FOR_SUBSCRIPTION", date(2026, 11, 24))]
