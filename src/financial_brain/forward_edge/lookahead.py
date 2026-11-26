"""Point-in-time availability and prefix-invariance checks (FE-0043)."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Sequence, TypeVar

T = TypeVar("T")


class LookaheadError(ValueError):
    """Input was unavailable at decision time or a future perturbation changed the prefix."""


@dataclass(frozen=True)
class AvailableInput:
    input_id: str
    sha256: str
    available_at: datetime


def validate_point_in_time(inputs: Sequence[AvailableInput], decision_time: datetime) -> str:
    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        raise LookaheadError("decision_time must be timezone aware")
    decision = decision_time.astimezone(timezone.utc)
    seen: set[str] = set()
    rows = []
    for item in inputs:
        if not item.input_id.strip() or len(item.sha256) != 64 or any(c not in "0123456789abcdef" for c in item.sha256):
            raise LookaheadError("each input requires an ID and lowercase SHA-256")
        if item.input_id in seen:
            raise LookaheadError(f"duplicate input ID {item.input_id}")
        seen.add(item.input_id)
        if item.available_at.tzinfo is None or item.available_at.utcoffset() is None:
            raise LookaheadError(f"input {item.input_id} has a naive availability clock")
        available = item.available_at.astimezone(timezone.utc)
        if available > decision:
            raise LookaheadError(f"input {item.input_id} was unavailable at decision time")
        rows.append({"input_id": item.input_id, "sha256": item.sha256, "available_at": available.isoformat()})
    if not rows:
        raise LookaheadError("decision requires at least one frozen input")
    raw = json.dumps(sorted(rows, key=lambda x: x["input_id"]), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def assert_prefix_invariant(values: Sequence[T], evaluate: Callable[[Sequence[T]], Any], *,
                            split: int, perturbation: Sequence[T]) -> None:
    """Feature/order output bytes through `split` must survive arbitrary future-row changes.

    ``evaluate`` may return aligned sequences, pandas objects, or a mapping such as
    ``{"features": ..., "intended_orders": ...}``. Canonical JSON comparison avoids
    dataframe truth-value ambiguity and checks the combined decision surface.
    """
    if split < 1 or split > len(values) or len(perturbation) != len(values) - split:
        raise LookaheadError("split or future perturbation length is invalid")
    prefix = list(values[:split])
    altered = prefix + list(perturbation)
    original_output = evaluate(values)
    altered_output = evaluate(altered)
    if _canonical_bytes(_prefix(original_output, split)) != _canonical_bytes(_prefix(altered_output, split)):
        raise LookaheadError("future data perturbation changed the evaluated prefix output")


def _prefix(output: Any, split: int) -> Any:
    if isinstance(output, Mapping):
        if not output:
            raise LookaheadError("evaluation mapping must include features or intended orders")
        return {str(key): _prefix(value, split) for key, value in output.items()}
    if isinstance(output, (list, tuple)):
        if len(output) < split:
            raise LookaheadError("evaluation output is shorter than the requested prefix")
        return output[:split]
    if hasattr(output, "iloc"):
        selected = output.iloc[:split]
        if hasattr(selected, "to_dict"):
            try:
                return selected.to_dict(orient="split")
            except TypeError:
                return selected.to_dict()
        return selected
    raise LookaheadError("evaluation must return a sequence or pandas object")


def _json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "tolist"):
        return _json_value(value.tolist())
    if hasattr(value, "item"):
        return _json_value(value.item())
    return value


def _canonical_bytes(value: Any) -> bytes:
    try:
        encoded = json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise LookaheadError("evaluated prefix must have deterministic finite JSON representation") from exc
    return encoded.encode("utf-8")
