"""One bounded JSON contract for local CLI and MCP consumers.

No request can name a file, SQL statement, broker operation, or approval actor.
The caller selects a configured data root; protocol callers cannot change it.
"""
from __future__ import annotations

import json
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from ..agents import brain
from ..config import Config
from ..services import ServiceError
from ..services import market as market_service
from ..storage.db import Database
from . import snapshot as verified_snapshot

CONTRACT = "financial-brain.read.v1"
OPERATIONS = {
    "health": {"description": "Saved-snapshot and kill-switch availability, not a trading green light",
               "arguments": []},
    "opportunities": {"description": "Bounded saved research opportunities", "arguments": ["limit"]},
    "brain": {"description": "Read-only specialist verdicts over saved opportunities",
              "arguments": ["limit"]},
    "market_world": {"description": "Archived market world state as of a date", "arguments": ["as_of"]},
}
MAX_LIMIT = 50


def data_root() -> Path:
    raw = os.environ.get("FB_DATA_ROOT")
    return (Path(raw).expanduser() if raw else Path(__file__).resolve().parents[3] / "data").resolve()


def _read_json(path: Path):
    if not path.is_file():
        raise FileNotFoundError(f"required artifact missing: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _limit(args: dict) -> int:
    value = args.get("limit", 10)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LIMIT:
        raise ValueError(f"limit must be an integer from 1 to {MAX_LIMIT}")
    return value


def _normal(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): _normal(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normal(v) for v in value]
    return value


def _execute(name: str, args: dict, root: Path):
    if name == "health":
        snapshot_path = root / "terminal" / "opportunities.json"
        state_path = root / "killswitch.json"
        snapshot = None
        manifest = None
        snapshot_error = None
        try:
            snapshot, manifest = verified_snapshot.read(snapshot_path)
        except verified_snapshot.SnapshotError as exc:
            snapshot_error = str(exc)
        state = _read_json(state_path) if state_path.is_file() else None
        if state is not None and not isinstance(state, dict):
            raise ValueError("kill-switch state must be an object")
        return {"snapshot_present": snapshot_path.is_file(), "snapshot_verified": snapshot is not None,
                "snapshot_error": snapshot_error, "opportunities": len(snapshot or []),
                "latest_asof": manifest.get("asof") if manifest else None,
                "kill_switch_present": state is not None,
                "kill_switch_level": state.get("level") if state else None,
                "research_db_present": (root / "financial_brain.duckdb").is_file(),
                "note": "Artifact presence is not data freshness, edge validation, or permission to trade."}
    if name in {"opportunities", "brain"}:
        snapshot, manifest = verified_snapshot.read(root / "terminal" / "opportunities.json")
        limit = _limit(args)
        if name == "opportunities":
            return {"items": snapshot[:limit], "returned": min(limit, len(snapshot)),
                    "total": len(snapshot), "manifest": manifest,
                    "evidence_limit": "research snapshot, not a buy signal"}
        state_path = root / "killswitch.json"
        state = _read_json(state_path) if state_path.is_file() else {}
        return {**brain.run(snapshot, state, limit=limit), "manifest": manifest}
    if name == "market_world":
        raw = args.get("as_of")
        as_of = date.fromisoformat(raw) if raw is not None else None
        cfg = Config(data_root=root)
        if not cfg.db_path.is_file():
            raise FileNotFoundError("required artifact missing: financial_brain.duckdb")
        with Database(cfg, read_only=True).connect() as con:
            return market_service.world_state(con, as_of=as_of)
    raise ValueError("unknown operation")


def call(name: str, arguments: dict | None = None, *, root: Path | None = None) -> dict:
    """Return a stable envelope; policy and data errors never become an order."""
    args = arguments or {}
    now = datetime.now(timezone.utc).isoformat()
    result = {"contract": CONTRACT, "operation": name, "generated_at": now,
              "read_only": True, "ok": False}
    if name not in OPERATIONS:
        return {**result, "error": {"code": "unknown_operation", "message": "operation not exposed"}}
    if not isinstance(args, dict):
        return {**result, "error": {"code": "bad_request", "message": "arguments must be an object"}}
    extra = set(args) - set(OPERATIONS[name]["arguments"])
    if extra:
        return {**result, "error": {"code": "bad_request",
                                  "message": f"unsupported arguments: {', '.join(sorted(extra))}"}}
    try:
        data = _execute(name, args, root or data_root())
        return {**result, "ok": True, "data": _normal(data)}
    except verified_snapshot.SnapshotError as exc:
        return {**result, "error": {"code": "not_available", "message": str(exc)}}
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return {**result, "error": {"code": "bad_request", "message": str(exc)}}
    except FileNotFoundError as exc:
        return {**result, "error": {"code": "not_available", "message": str(exc)}}
    except ServiceError as exc:
        return {**result, "error": exc.as_dict()}
    except duckdb.Error:
        return {**result, "error": {"code": "not_available",
                                  "message": "research database is unavailable or busy"}}
