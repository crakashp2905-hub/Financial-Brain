"""The knowledge base (``research/knowledge.py``): build, search, read one card. It lives in its own file; the research
database is only read."""
from __future__ import annotations

from ..research import knowledge as KB


def build(con) -> dict:
    return KB.build(con)


def search(query: str, limit: int = 25) -> list[dict]:
    return KB.search(query, limit=limit)


def card(card_id: str) -> dict:
    rows = KB.search(card_id.split(":", 1)[-1], limit=200)
    match = [r for r in rows if r["card_id"] == card_id]
    return {"card": match[0] if match else None, "tests": KB.card_tests(card_id)}
