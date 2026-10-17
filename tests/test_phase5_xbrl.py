import re
from datetime import date

import duckdb
import pytest

from financial_brain.docintel import xbrl_results as X
from financial_brain.ingest import xbrl_results as I
from financial_brain.providers.base import FetchError, FetchResult
from financial_brain.providers.nse_results import NSEResults

NS = "http://www.bseindia.com/xbrl/fin/2020-03-31/in-bse-fin"


def instance(facts_one, facts_four=None, *, segment_revenue=99.0, start="2024-10-01", end="2024-12-31"):
    ctx = (f'<xbrli:context id="OneD"><xbrli:entity><xbrli:identifier scheme="s">X</xbrli:identifier>'
           f'</xbrli:entity><xbrli:period><xbrli:startDate>{start}</xbrli:startDate>'
           f'<xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>'
           f'<xbrli:context id="FourD"><xbrli:entity><xbrli:identifier scheme="s">X</xbrli:identifier>'
           f'</xbrli:entity><xbrli:period><xbrli:startDate>2024-04-01</xbrli:startDate>'
           f'<xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>'
           f'<xbrli:context id="OneReportableSegmentRevenue01D"><xbrli:entity><xbrli:identifier '
           f'scheme="s">X</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>{start}'
           f'</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate></xbrli:period><xbrli:scenario>'
           f'<d/></xbrli:scenario></xbrli:context>')
    body = "".join(f'<in-bse-fin:{k} contextRef="OneD" unitRef="INR" decimals="-7">{v}</in-bse-fin:{k}>'
                   for k, v in facts_one.items())
    body += "".join(f'<in-bse-fin:{k} contextRef="FourD" unitRef="INR" decimals="-7">{v}</in-bse-fin:{k}>'
                    for k, v in (facts_four or {}).items())
    body += (f'<in-bse-fin:RevenueFromOperations contextRef="OneReportableSegmentRevenue01D" '
             f'unitRef="INR">{segment_revenue}</in-bse-fin:RevenueFromOperations>')
    body += ('<in-bse-fin:ReportingQuarter contextRef="OneD">Third quarter</in-bse-fin:ReportingQuarter>')
    return (f'<?xml version="1.0"?><xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" '
            f'xmlns:in-bse-fin="{NS}">{ctx}{body}</xbrli:xbrl>').encode()


NONBANK = {"RevenueFromOperations": 1000.0, "OtherIncome": 50.0, "Income": 1050.0, "Expenses": 800.0,
           "ProfitBeforeTax": 250.0, "ProfitLossForPeriod": 190.0,
           "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations": 9.5}


def test_segment_contexts_are_not_added_to_the_company_figure():
    inst = X.parse(instance(NONBANK))
    assert inst.current()["RevenueFromOperations"] == 1000.0          # not 1000 + 99


def test_quarter_and_year_to_date_are_kept_apart():
    inst = X.parse(instance(NONBANK, {"RevenueFromOperations": 2900.0}))
    assert inst.facts["Q"]["RevenueFromOperations"] == 1000.0
    assert inst.facts["YTD"]["RevenueFromOperations"] == 2900.0


def test_canonical_fields_and_arithmetic():
    inst = X.parse(instance(NONBANK))
    c = inst.canonical()
    assert (c["revenue"], c["pat"], c["eps_basic"]) == (1000.0, 190.0, 9.5)
    assert X.checks(c) == {"income adds up": True}
    assert X.checks({**c, "total_income": 5000.0}) == {"income adds up": False}


def test_bank_format_falls_back_to_its_own_tags():
    bank = {"InterestEarned": 850.0, "OtherIncome": 270.0, "Income": 1120.0,
            "ExpenditureExcludingProvisionsAndContingencies": 840.0,
            "ProfitLossFromOrdinaryActivitiesBeforeTax": 240.0, "ProfitLossForThePeriod": 180.0}
    c = X.parse(instance(bank)).canonical()
    assert (c["revenue"], c["total_income"], c["total_expenses"], c["pbt"], c["pat"]) == \
        (850.0, 1120.0, 840.0, 240.0, 180.0)
    assert X.checks(c)["income adds up"]


def test_filings_that_never_define_the_company_contexts_still_parse():
    """~2020-23 filings reference OneD/FourD without defining them; the period is in the facts."""
    raw = instance(NONBANK).decode()
    raw = re.sub(r'<xbrli:context id="OneD">.*?</xbrli:context>', "", raw, flags=re.S)
    raw = re.sub(r'<xbrli:context id="FourD">.*?</xbrli:context>', "", raw, flags=re.S)
    raw = raw.replace("</xbrli:xbrl>",
                      '<in-bse-fin:DateOfStartOfReportingPeriod contextRef="OneD">2024-10-01'
                      '</in-bse-fin:DateOfStartOfReportingPeriod>'
                      '<in-bse-fin:DateOfEndOfReportingPeriod contextRef="OneD">2024-12-31'
                      '</in-bse-fin:DateOfEndOfReportingPeriod></xbrli:xbrl>')
    inst = X.parse(raw.encode())
    assert inst.canonical()["revenue"] == 1000.0           # and the segment context is still excluded
    assert inst.period == ("2024-10-01", "2024-12-31")


def test_period_and_text_facts():
    inst = X.parse(instance(NONBANK))
    assert inst.period == ("2024-10-01", "2024-12-31") and inst.text["ReportingQuarter"] == "Third quarter"


def test_garbage_and_empty_are_refused():
    with pytest.raises(X.XBRLError):
        X.parse(b"<html>blocked</html")
    with pytest.raises(X.XBRLError):
        X.parse(instance({}))


def test_pre_xbrl_placeholder_link_is_not_a_file():
    assert NSEResults.has_xbrl({"xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/-"}) is False
    assert NSEResults.has_xbrl({"xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/A.xml"}) is True


def test_provider_refuses_hosts_that_are_not_nse():
    p = NSEResults(min_interval=0)
    with pytest.raises(FetchError):
        p._get("https://example.com/a.xml")


# --------------------------------------------------------------------------- the store
DDL = """
CREATE TABLE xbrl_filings(seq_number VARCHAR PRIMARY KEY, symbol VARCHAR, isin VARCHAR, company VARCHAR,
  period_start DATE, period_end DATE, basis VARCHAR, relating_to VARCHAR, audited VARCHAR, cumulative BOOLEAN,
  is_bank BOOLEAN, filed_at TIMESTAMP, xbrl_url VARCHAR, lake_key VARCHAR, status VARCHAR NOT NULL,
  attempted_at TIMESTAMP WITH TIME ZONE);
CREATE TABLE xbrl_facts(seq_number VARCHAR NOT NULL, concept VARCHAR NOT NULL, ctx VARCHAR NOT NULL,
  value DOUBLE, PRIMARY KEY (seq_number, concept, ctx));
CREATE TABLE financial_results(isin VARCHAR NOT NULL, company VARCHAR, period_end DATE NOT NULL,
  basis VARCHAR NOT NULL, filed_at TIMESTAMP NOT NULL, news_id VARCHAR, revenue DOUBLE, other_income DOUBLE,
  total_income DOUBLE, total_expenses DOUBLE, pbt DOUBLE, pat DOUBLE, eps_basic DOUBLE,
  unit_multiplier BIGINT, checks_passed BOOLEAN NOT NULL, lake_key VARCHAR, source_page INTEGER,
  observed_at TIMESTAMPTZ NOT NULL, PRIMARY KEY (isin, period_end, basis, filed_at));
CREATE TABLE lake_manifest(key VARCHAR PRIMARY KEY, source VARCHAR, dataset VARCHAR, business_date DATE,
  filename VARCHAR, url VARCHAR, retrieved_at TIMESTAMPTZ, sha256 VARCHAR, size_bytes BIGINT,
  http_status INTEGER, content_type VARCHAR);
"""


def listing(seq, *, to="31-Dec-2024", cons="Consolidated", cum="Non-cumulative", disc="09-Jan-2025 21:40:06",
            xbrl="https://nsearchives.nseindia.com/corporate/xbrl/A_%s.xml"):
    return {"seqNumber": seq, "symbol": "X", "isin": "INE000000001", "companyName": "X Ltd",
            "fromDate": "01-Oct-2024", "toDate": to, "consolidated": cons, "cumulative": cum,
            "relatingTo": "Third Quarter", "audited": "Audited", "bank": "N",
            "exchdisstime": disc, "broadCastDate": disc, "xbrl": xbrl % seq if "%s" in xbrl else xbrl}


class Fake(NSEResults):
    def __init__(self, rows, files):
        super().__init__(min_interval=0)
        self.rows, self.files = rows, files

    def filings(self, symbol, *, period="Quarterly"):
        return self.rows

    def fetch_xbrl(self, url):
        if url not in self.files:
            raise FetchError("404", retryable=False, status=404)
        return FetchResult(payload=self.files[url], url=url, retrieved_at=self._now(),
                           filename=url.rsplit("/", 1)[-1])


class Cfg:
    def __init__(self, p):
        self.lake = p


@pytest.fixture
def env(tmp_path):
    con = duckdb.connect()
    con.execute(DDL)
    from financial_brain.lake.store import RawLake
    return con, RawLake(tmp_path / "lake")


def url(seq):
    return f"https://nsearchives.nseindia.com/corporate/xbrl/A_{seq}.xml"


def test_a_filing_is_stored_with_its_dissemination_time_and_headline_figures(env):
    con, lake = env
    p = Fake([listing("1")], {url("1"): instance(NONBANK)})
    s = I.ingest_symbol(con, lake, p, "X")
    assert s[I.STORED] == 1 and s["facts"] > 0
    r = con.execute("SELECT basis, filed_at, revenue, pat, checks_passed FROM financial_results").fetchone()
    assert r[0] == "consolidated" and str(r[1]) == "2025-01-09 21:40:06"
    assert (r[2], r[3], r[4]) == (1000.0, 190.0, True)


def test_a_restatement_is_a_second_row_not_an_update(env):
    con, lake = env
    revised = {**NONBANK, "ProfitLossForPeriod": 150.0}
    rows = [listing("1"), listing("2", disc="20-Feb-2025 10:00:00")]
    p = Fake(rows, {url("1"): instance(NONBANK), url("2"): instance(revised)})
    I.ingest_symbol(con, lake, p, "X")
    assert con.execute("SELECT COUNT(*) FROM financial_results").fetchone()[0] == 2
    before = I.as_known_on(con, "INE000000001", date(2025, 1, 31))
    after = I.as_known_on(con, "INE000000001", date(2025, 3, 1))
    assert before[0]["pat"] == 190.0 and after[0]["pat"] == 150.0     # what was knowable, when
    assert I.as_known_on(con, "INE000000001", date(2024, 12, 31)) == []


def test_pre_xbrl_cumulative_and_mismatched_filings_are_handled_not_dropped(env):
    con, lake = env
    rows = [listing("1", xbrl="https://nsearchives.nseindia.com/corporate/xbrl/-"),
            listing("2", cum="Cumulative"),
            listing("3", to="30-Sep-2024")]                       # listing says Sep; the file says Dec
    p = Fake(rows, {url("2"): instance(NONBANK), url("3"): instance(NONBANK)})
    s = I.ingest_symbol(con, lake, p, "X")
    assert (s[I.NO_XBRL], s[I.STORED], s[I.MISMATCH]) == (1, 1, 1)
    assert con.execute("SELECT COUNT(*) FROM financial_results").fetchone()[0] == 0   # none promoted
    assert con.execute("SELECT COUNT(*) FROM xbrl_facts WHERE seq_number = '2'").fetchone()[0] > 0
    st = dict(con.execute("SELECT seq_number, status FROM xbrl_filings").fetchall())
    assert st == {"1": I.NO_XBRL, "2": I.STORED, "3": I.MISMATCH}


def test_rerun_skips_final_rows_and_retries_failures(env):
    con, lake = env
    p = Fake([listing("1"), listing("2")], {url("1"): instance(NONBANK)})     # 2 will 404
    s1 = I.ingest_symbol(con, lake, p, "X")
    assert (s1[I.STORED], s1[I.FETCH_FAILED]) == (1, 1)
    p.files[url("2")] = instance(NONBANK)
    s2 = I.ingest_symbol(con, lake, p, "X")
    assert (s2["skipped"], s2[I.STORED]) == (1, 1)


def test_filings_before_the_cutoff_are_ignored(env):
    con, lake = env
    p = Fake([listing("1", to="31-Mar-2015")], {})
    assert I.ingest_symbol(con, lake, p, "X")["listed"] == 0


def test_provider_source_file_has_no_write_verbs():
    import inspect
    from financial_brain.providers import nse_results
    src = inspect.getsource(nse_results)
    assert 'method="POST"' not in src and 'method="DELETE"' not in src and "/orders" not in src


def test_results_filed_under_an_older_isin_are_found_through_the_lineage(env):
    con, lake = env
    con.execute("CREATE TABLE security_lineage(isin VARCHAR, lineage VARCHAR)")
    con.execute("INSERT INTO security_lineage VALUES ('INE000000001', 'L'), ('INE999999999', 'L')")
    p = Fake([listing("1")], {url("1"): instance(NONBANK)})           # filed under INE000000001
    I.ingest_symbol(con, lake, p, "X")
    got = I.as_known_on(con, "INE999999999", date(2025, 3, 1))        # asked by the successor ISIN
    assert got and got[0]["pat"] == 190.0


def test_results_under_an_isin_the_lineage_never_saw_are_found_through_the_symbol(env):
    con, lake = env
    con.execute("CREATE TABLE security_lineage(isin VARCHAR, lineage VARCHAR)")
    con.execute("INSERT INTO security_lineage VALUES ('INE777777777', 'M')")      # successor only
    con.execute("CREATE TABLE security_listings(isin VARCHAR, ticker VARCHAR)")
    con.execute("INSERT INTO security_listings VALUES ('INE777777777', 'X')")
    p = Fake([listing("1")], {url("1"): instance(NONBANK)})                      # filed under INE000000001, symbol X
    I.ingest_symbol(con, lake, p, "X")
    assert I.as_known_on(con, "INE777777777", date(2025, 3, 1))[0]["pat"] == 190.0


def test_a_network_failure_while_warming_the_session_is_a_fetch_error_not_a_crash(monkeypatch):
    p = NSEResults(min_interval=0)

    def down():
        raise OSError("getaddrinfo failed")
    monkeypatch.setattr(p, "_warm", down)
    monkeypatch.setattr("financial_brain.providers.nse_results.time.sleep", lambda s: None)
    with pytest.raises(FetchError):
        p.filings("TCS")


def test_a_new_provider_warms_its_session_even_on_a_freshly_booted_machine(monkeypatch):
    p = NSEResults(min_interval=0)
    monkeypatch.setattr("financial_brain.providers.nse_results.time.monotonic", lambda: 5.0)   # 5 s after boot
    warmed = []

    def warm():
        warmed.append(1)
        raise OSError("down")
    monkeypatch.setattr(p, "_warm", warm)
    monkeypatch.setattr("financial_brain.providers.nse_results.time.sleep", lambda s: None)
    with pytest.raises(FetchError):
        p.filings("TCS")
    assert warmed
