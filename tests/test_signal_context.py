from datetime import datetime, timezone

import duckdb
import pytest

from financial_brain.signals.context import ContextError, read_only_context, research_packet

NOW = datetime(2026, 11, 23, 10, tzinfo=timezone.utc)
HASH = "f" * 64


def fixture_db():
    con = duckdb.connect(":memory:")
    con.execute("""CREATE TABLE lake_manifest(key VARCHAR, sha256 VARCHAR)""")
    con.execute("""CREATE TABLE announcements(news_id VARCHAR, isin VARCHAR, event_type VARCHAR,
        materiality VARCHAR, headline VARCHAR, subject VARCHAR, published_at TIMESTAMP,
        submitted_at TIMESTAMP, observed_at TIMESTAMPTZ, evidence_key VARCHAR)""")
    con.execute("""CREATE TABLE announcement_news(news_id VARCHAR, url VARCHAR, headline VARCHAR,
        how VARCHAR, extracted_at TIMESTAMPTZ)""")
    con.execute("""CREATE TABLE news_articles(url VARCHAR, title VARCHAR, published_at TIMESTAMP,
        fetched_at TIMESTAMPTZ, lake_key VARCHAR)""")
    con.execute("INSERT INTO lake_manifest VALUES ('event:1', ?), ('article:1', ?)", [HASH, HASH])
    con.execute("""INSERT INTO announcements VALUES
        ('event-1','INE000A01001','RESULTS','HIGH','Results filed','Q2 results',
         '2026-11-23 14:00:00',NULL,'2026-11-23 08:35:00+00','event:1'),
        ('event-future','INE000A01001','ORDER_WIN','MEDIUM','Future event','Later',
         '2026-11-23 16:00:00',NULL,'2026-11-23 09:00:00+00','event:1'),
        ('event-late-observation','INE000A01001','RATING','HIGH','Late observed','Late',
         '2026-11-23 12:00:00',NULL,'2026-11-23 12:00:00+00','event:1')""")
    con.execute("""INSERT INTO announcement_news VALUES
        ('event-1','https://news.example/a','Linked headline','quoted','2026-11-23 08:36:00+00')""")
    con.execute("""INSERT INTO news_articles VALUES
        ('https://news.example/a','Published article','2026-11-23 16:00:00',
         '2026-11-23 08:35:00+00','article:1')""")
    return con


def test_context_includes_only_pre_cutoff_observed_events_and_news():
    con = fixture_db()
    result = read_only_context(con, isin="INE000A01001", as_of=NOW)
    assert [item["news_id"] for item in result["events"]] == ["event-1"]
    assert result["events"][0]["source_sha256"] == HASH
    # The linked article was published after the decision cutoff, even though its filing was known.
    assert result["news"] == []
    assert result["execution_allowed"] is False
    con.close()


def test_research_packet_preserves_candidate_without_order_fields():
    con = fixture_db()
    packet = research_packet(con, {"ticker": "ABC", "isin": "INE000A01001"}, as_of=NOW)
    assert packet["candidate"]["ticker"] == "ABC"
    assert packet["decision_mode"] == "READ_ONLY_RESEARCH"
    assert packet["execution_allowed"] is False
    assert "order" not in packet and "intent" not in packet
    con.close()


def test_linked_headline_is_included_only_when_its_own_publication_time_is_available():
    con = fixture_db()
    con.execute("UPDATE news_articles SET published_at='2026-11-23 15:00:00'")
    result = read_only_context(con, isin="INE000A01001", as_of=NOW)
    assert len(result["news"]) == 1
    assert result["news"][0]["availability"] == "PUBLISHED_BEFORE_CUTOFF"
    con.close()


def test_context_requires_asof_and_candidate_identity():
    con = fixture_db()
    with pytest.raises(ContextError):
        read_only_context(con, isin=" ", as_of=NOW)
    with pytest.raises(ContextError):
        research_packet(con, {"ticker": "ABC"}, as_of=NOW)
    con.close()
