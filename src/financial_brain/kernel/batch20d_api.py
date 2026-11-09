"""Local API, integration and import tools (FB-0602, FB-0603, FB-0604, FB-0606, FB-0607, FB-0608, FB-0610, FB-0612,
FB-0616, FB-0618).

* ``route_status`` answers only the versioned routes; unversioned or unknown paths get 404.
* ``StreamEvent`` is a typed event: QUOTE, ALERT or FILL. Any other kind is refused.
* ``method_allowed`` ties HTTP methods to permission levels. READ is GET only; POST needs PAPER.
* ``validate_manifest`` checks a plugin manifest. Sandboxing must be enabled.
* ``import_alert`` treats an alert as untrusted external data. Any key that asks for an order is refused.
* ``import_mql5_csv`` parses a fixed-header transaction export with type checks.
* ``provider_status`` reports NO_PROVIDER when no data provider is configured.
* ``export_rows`` writes rows as CSV, JSON or Markdown.
* ``paginate`` returns one page with a size cap.
* ``request_id`` gives deterministic request ids for observability.
"""
from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

ROUTES = {"/v1/instruments", "/v1/bars", "/v1/portfolio", "/v1/orders"}
STREAM_KINDS = {"QUOTE", "ALERT", "FILL"}
LEVELS: dict[str, set[str]] = {
    "READ": {"read"},
    "ANALYSE": {"read", "analyse"},
    "PAPER": {"read", "analyse", "paper"},
}
MAX_PAGE = 100
MQL5_HEADER = ["ticket", "symbol", "side", "volume", "price"]


def route_status(path: str) -> int:
    return 200 if path in ROUTES else 404


@dataclass(frozen=True)
class StreamEvent:
    kind: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.kind not in STREAM_KINDS:
            raise ValueError(f"unknown stream event kind {self.kind!r}")


def method_allowed(method: str, level: str) -> bool:
    if level not in LEVELS:
        raise KeyError(f"unknown level {level!r}")
    if method == "GET":
        return "read" in LEVELS[level]
    if method == "POST":
        return "paper" in LEVELS[level]
    return False


def validate_manifest(manifest: Mapping[str, Any]) -> list[str]:
    errors = [f"missing {k}" for k in ("name", "version", "api_version") if k not in manifest]
    if manifest.get("sandbox") is not True:
        errors.append("sandbox must be enabled")
    return errors


def import_alert(text: str) -> dict[str, Any]:
    data = json.loads(text)
    if "symbol" not in data or "condition" not in data:
        raise ValueError("alert needs symbol and condition")
    if any(k in data for k in ("order", "submit", "quantity")):
        raise ValueError("alerts cannot carry order instructions")
    return {**data, "source": "EXTERNAL_UNTRUSTED"}


def import_mql5_csv(text: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != MQL5_HEADER:
        raise ValueError(f"header must be {MQL5_HEADER}")
    rows: list[dict[str, Any]] = []
    for row in reader:
        volume = float(row["volume"])
        if volume <= 0:
            raise ValueError("volume must be positive")
        rows.append({
            "ticket": row["ticket"],
            "symbol": row["symbol"],
            "side": row["side"],
            "volume": volume,
            "price": Decimal(row["price"]),
        })
    return rows


def provider_status(provider: str | None) -> str:
    return "NO_PROVIDER" if provider is None else f"PROVIDER:{provider}"


def export_rows(rows: Sequence[Mapping[str, Any]], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(list(rows), sort_keys=True, default=str)
    if not rows:
        return ""
    headers = list(rows[0].keys())
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        return buf.getvalue()
    if fmt == "markdown":
        lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
        lines += ["| " + " | ".join(str(r[h]) for h in headers) + " |" for r in rows]
        return "\n".join(lines)
    raise ValueError(f"unknown format {fmt!r}")


def paginate(items: Sequence[Any], page: int, size: int) -> dict[str, Any]:
    if not (1 <= size <= MAX_PAGE):
        raise ValueError(f"page size must be between 1 and {MAX_PAGE}")
    if page < 1:
        raise ValueError("page starts at 1")
    pages = max(1, -(-len(items) // size))
    start = (page - 1) * size
    return {"items": list(items[start : start + size]), "page": page, "pages": pages}


def request_id(seq: int) -> str:
    if seq < 0:
        raise ValueError("sequence must be non-negative")
    return f"req-{seq:08d}"
