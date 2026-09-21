"""Earnings calls as structure, not as a verdict (C11).

MiMIC (arXiv 2504.09257) argues transcripts and slide decks carry signal in this market;
these tests pin what we took - the split between prepared remarks and Q&A, and local
retrieval - and what we deliberately did not: any reading of what was said, which would
need a labelled set of call text that does not exist yet.
"""
from __future__ import annotations

import json

import pytest

from financial_brain.config import Config
from financial_brain.docintel import calls
from financial_brain.storage.db import Database

TRANSCRIPT = """Acme Industries Limited
Q1 FY27 Earnings Conference Call
July 24, 2026

Moderator: Ladies and gentlemen, good day and welcome to the Acme Industries
Q1 FY27 earnings conference call.

Ravi Menon: Thank you. Revenue for the quarter grew 18% to Rs 1,240 crore, and we
commissioned the Gujarat line ahead of schedule.

Moderator: We will now begin the question-and-answer session.

Analyst One: Could you break out the margin impact of the new line?

Ravi Menon: Roughly 120 basis points, most of it in the second half.
"""


def test_a_transcript_splits_at_the_question_and_answer_turn():
    """Management chooses every word of the prepared remarks; analysts choose the rest.
    A claim from one is not the same kind of claim as a claim from the other."""
    s = calls.segments(TRANSCRIPT)
    assert s["has_qa"]
    assert "commissioned the Gujarat line" in s["commentary"]
    assert "margin impact of the new line" in s["qa"]
    assert "Gujarat" not in s["qa"]


def test_a_presentation_with_no_qa_says_so_rather_than_guessing():
    deck = "Acme Industries\nQ1 FY27 Investor Presentation\nRevenue Rs 1,240 crore"
    s = calls.segments(deck)
    assert s["has_qa"] is False and s["qa"] == "" and s["commentary"] == deck


def test_speakers_are_listed_in_order_of_first_appearance():
    assert calls.speakers(TRANSCRIPT)[:3] == ["Moderator", "Ravi Menon", "Analyst One"]


def test_cosine_is_symmetric_and_bounded():
    a, b = [1.0, 0.0, 1.0], [1.0, 0.0, 1.0]
    assert calls.cosine(a, b) == pytest.approx(1.0)
    assert calls.cosine(a, [0.0, 1.0, 0.0]) == pytest.approx(0.0)
    assert calls.cosine(a, []) == 0.0, "a missing vector is not similarity 1"


def test_an_empty_embedding_is_an_error_not_a_zero_vector(monkeypatch):
    """A silent zero vector would make every document resemble every other one."""
    import financial_brain.docintel.calls as mod
    from financial_brain.llm.backends import BackendUnavailable

    class FakeResponse:
        def read(self):
            return json.dumps({"embedding": []}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    monkeypatch.setattr(mod.json, "load", lambda f: json.loads(f.read()))
    with pytest.raises(BackendUnavailable):
        mod.embed("anything")


@pytest.fixture
def con(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as c:
        yield c


def _call(con, news_id, company, vector, on="2026-07-24", isin="INE000A01001"):
    con.execute("""INSERT INTO call_documents (news_id, isin, company, called_on,
                   event_type, pages, chars, has_qa, embedding, fetched_at)
                   VALUES (?,?,?,?, 'EARNINGS_CALL', 20, 40000, TRUE, ?, NOW())""",
                [news_id, isin, company, on, json.dumps(vector)])


def test_retrieval_ranks_the_most_similar_past_call_first(con):
    _call(con, "n1", "Acme", [1.0, 0.0, 0.0])
    _call(con, "n2", "Nearly Acme", [0.95, 0.31, 0.0])
    _call(con, "n3", "Unrelated", [0.0, 0.0, 1.0])
    got = calls.most_similar(con, "n1", k=2)
    assert [g["company"] for g in got] == ["Nearly Acme", "Unrelated"]
    assert got[0]["similarity"] > got[1]["similarity"]
    assert "n1" not in [g["news_id"] for g in got], "a call is not similar to itself"


def test_retrieval_can_be_confined_to_the_same_company(con):
    _call(con, "n1", "Acme", [1.0, 0.0, 0.0])
    _call(con, "n2", "Acme", [0.9, 0.4, 0.0], on="2026-04-24")
    _call(con, "n3", "Other Co", [0.99, 0.1, 0.0], isin="INE999Z01001")
    same = calls.most_similar(con, "n1", k=5, same_company=True)
    assert [g["company"] for g in same] == ["Acme"]


def test_a_document_without_an_embedding_is_not_retrievable(con):
    con.execute("""INSERT INTO call_documents (news_id, isin, company, called_on,
                   event_type, pages, chars, has_qa, embedding, fetched_at)
                   VALUES ('n1','INE000A01001','Acme',DATE '2026-07-24','EARNINGS_CALL',
                           20, 40000, TRUE, NULL, NOW())""")
    assert calls.most_similar(con, "n1") == []
