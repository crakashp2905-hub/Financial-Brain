"""Optional, disabled-by-default ingestion of authorized REA evidence exports.

This adapter never launches REA or untrusted software. It only summarizes a sanitized
JSON export prepared in an isolated investigation, for human review.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SECRET = re.compile(r"(?i)(bearer\s+\S+|api[_-]?key\s*[:=]\s*\S+|token\s*[:=]\s*\S+|password\s*[:=]\s*\S+)")


def review_export(path: str | Path, *, enabled: bool = False, authorized: bool = False) -> dict:
    if not enabled or not authorized:
        raise PermissionError("REA evidence import requires explicit enablement and artifact authorization")
    source = Path(path)
    if source.suffix.lower() != ".json" or source.stat().st_size > 2_000_000:
        raise ValueError("expected a JSON evidence export of at most 2 MB")
    raw = source.read_bytes()
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("evidence export must be an object")
    clean = {}
    for field in ("artifact", "version", "hypothesis", "evidence", "limitations"):
        value = parsed.get(field)
        if value is not None:
            clean[field] = SECRET.sub("[REDACTED]", str(value))[:10_000]
    return {"source_sha256": hashlib.sha256(raw).hexdigest(), "review_required": True,
            "official_documentation_verified": False, "evidence": clean,
            "warning": "Do not apply code changes without official documentation and human review."}
