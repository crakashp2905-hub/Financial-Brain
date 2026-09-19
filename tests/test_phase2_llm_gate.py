"""P2-5 LLM gate: closed by default, whitelisted purposes, every call recorded."""
from __future__ import annotations

import pytest

from financial_brain.config import Config
from financial_brain.llm import gate
from financial_brain.storage.db import Database


@pytest.fixture
def con(tmp_path):
    d = Database(Config(data_root=tmp_path).ensure())
    d.migrate()
    with d.connect() as c:
        yield c


def fake(system, prompt, max_tokens):
    return gate.Reply("a summary", "fake-model", 120, 30)


def test_closed_by_default(con, monkeypatch):
    monkeypatch.delenv("FB_LLM_ENABLED", raising=False)
    with pytest.raises(gate.LLMUnavailable):
        gate.call(con, purpose="summarise", system="s", prompt="p", evidence=[])


def test_prediction_is_not_a_purpose(con):
    with pytest.raises(ValueError, match="not allowed"):
        gate.call(con, purpose="predict_price", system="s", prompt="p", evidence=[],
                  transport=fake)


def test_every_call_is_recorded_without_the_prompt(con):
    r = gate.call(con, purpose="summarise", system="s", prompt="secret text",
                  evidence=["ev_1", "ev_2"], transport=fake)
    assert r.text == "a summary"
    row = con.execute("SELECT purpose, model, prompt_sha256, evidence, input_tokens "
                      "FROM llm_calls").fetchone()
    assert row[0] == "summarise" and row[1] == "fake-model" and row[4] == 120
    assert "secret" not in row[2] and len(row[2]) == 64
    assert row[3] == ["ev_1", "ev_2"]
