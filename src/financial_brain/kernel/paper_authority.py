"""Cross-engine single-paper-authority claims for a strategy and exchange session."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import date
from pathlib import Path
from typing import Iterable


class PaperAuthorityError(ValueError):
    """A strategy/session is already assigned to a different paper engine or account."""


class PaperExecutionAuthorityRegistry:
    """Atomically bind each strategy and exchange date to one engine/account pair.

    Both paper engines must point at the same registry path. Claims are durable for the
    exchange date, including after a process crash, so an uncertain run cannot silently
    switch books. Repeating the identical claim is idempotent.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()

    def claim(self, *, strategy_id: str, exchange_date: str, engine_id: str,
              account_id: str) -> str:
        return self.claim_many([{
            "strategy_id": strategy_id, "exchange_date": exchange_date,
            "engine_id": engine_id, "account_id": account_id,
        }])[0]

    def claim_many(self, claims: Iterable[dict[str, str]]) -> list[str]:
        """Validate all requested claims, then insert them as one SQLite transaction."""
        rows = list(claims)
        if not rows:
            raise PaperAuthorityError("at least one strategy authority claim is required")
        normalized: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise PaperAuthorityError("authority claims must be objects")
            strategy_id = row.get("strategy_id", "").strip()
            engine_id = row.get("engine_id", "").strip()
            account_id = row.get("account_id", "").strip()
            exchange_date = row.get("exchange_date", "")
            try:
                parsed_date = date.fromisoformat(exchange_date)
            except (TypeError, ValueError) as exc:
                raise PaperAuthorityError("exchange_date must be an ISO calendar date") from exc
            if not strategy_id or not engine_id or not account_id or parsed_date.isoformat() != exchange_date:
                raise PaperAuthorityError("strategy, engine, account, and canonical exchange date are required")
            normalized.append({"strategy_id": strategy_id, "exchange_date": exchange_date,
                               "engine_id": engine_id, "account_id": account_id})
        keys = [(row["strategy_id"], row["exchange_date"]) for row in normalized]
        if len(keys) != len(set(keys)):
            raise PaperAuthorityError("duplicate strategy/session claims are not allowed")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""CREATE TABLE IF NOT EXISTS paper_authority_claims (
                strategy_id TEXT NOT NULL,
                exchange_date TEXT NOT NULL,
                engine_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                claim_id TEXT NOT NULL,
                PRIMARY KEY (strategy_id, exchange_date)
            )""")
            connection.execute("BEGIN IMMEDIATE")
            for row in normalized:
                existing = connection.execute(
                    "SELECT engine_id, account_id FROM paper_authority_claims "
                    "WHERE strategy_id=? AND exchange_date=?",
                    (row["strategy_id"], row["exchange_date"]),
                ).fetchone()
                if existing is not None and existing != (row["engine_id"], row["account_id"]):
                    raise PaperAuthorityError(
                        f"{row['strategy_id']} on {row['exchange_date']} is already claimed by "
                        f"engine={existing[0]} account={existing[1]}"
                    )
            claim_ids = []
            for row in normalized:
                claim_id = hashlib.sha256(json.dumps(
                    row, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode()).hexdigest()
                connection.execute(
                    "INSERT OR IGNORE INTO paper_authority_claims "
                    "(strategy_id, exchange_date, engine_id, account_id, claim_id) VALUES (?, ?, ?, ?, ?)",
                    (row["strategy_id"], row["exchange_date"], row["engine_id"], row["account_id"], claim_id),
                )
                claim_ids.append(claim_id)
            connection.execute("COMMIT")
            return claim_ids
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()


def configured_paper_authority_path(anchor: Path) -> Path:
    """Resolve the shared authority DB, with an override for deployments using split roots."""
    configured = os.environ.get("FINANCIAL_BRAIN_PAPER_AUTHORITY_DB")
    if configured and configured.strip():
        return Path(configured).expanduser().resolve()
    return Path(anchor).resolve() / "paper_authority.sqlite3"
