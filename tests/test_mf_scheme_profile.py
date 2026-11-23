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
    "manager,objective,effective_from,effective_to,source_url,source_document_sha256,"
    "source_published_at\n"
    f"135762,Direct Plan,Growth Option,INF846K01WO1,Very High,Nifty 50 TRI,TRI,"
    f"A Manager,Long term capital growth,2026-01-01,,{URL},{HASH},"
    "2026-11-20T10:00:00+05:30\n"
).encode()


def test_scheme_profile_parser_preserves_source_and_effective_dates() -> None:
    row = parse(CSV, date(2026, 11, 23))[0]
    assert row["scheme_code"] == "135762"
    assert row["benchmark_return_type"] == "TRI"
    assert row["effective_from"] == date(2026, 1, 1)
    assert row["effective_to"] is None
    assert row["source_url"] == URL and row["source_document_sha256"] == HASH


@pytest.mark.parametrize("payload", [
    CSV.replace(HASH.encode(), b"bad"),
    CSV.replace(b",Nifty 50 TRI,TRI,", b",Nifty 50 TRI,,"),
    CSV.replace(b"2026-01-01,,", b"2026-12-01,2026-01-01,"),
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
                                     manager, objective, effective_from, observed_on,
                                     source_document_sha256, lake_key
                              FROM mf_scheme_profile_disclosures""").fetchall()
    assert len(rows) == 1
    assert rows[0][:6] == ("135762", "Nifty 50 TRI", "TRI", "A Manager",
                           "Long term capital growth", date(2026, 1, 1))
    assert rows[0][6] == date(2026, 11, 23)
    assert rows[0][7] == HASH
    assert rows[0][8].startswith("LOCAL_IMPORT/mf_scheme_profiles/")
