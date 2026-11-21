"""Validate the source, credential-presence, latency, and retention inventory."""

from __future__ import annotations

import json
import sys
from pathlib import Path


MATRIX = Path("docs/edge-program/market-data-sources.v1.json")
STATUSES = {"MISSING", "LICENSED", "UNVERIFIED", "READY"}
REQUIRED_SOURCE_FIELDS = {
    "id", "provider", "category", "products", "status", "access", "latency",
    "retention", "evidence_paths", "limitations",
}
OPTIONAL_SOURCE_FIELDS = {"runtime_source_id"}


def validate(path: Path, root: Path) -> list[str]:
    errors: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"cannot read source matrix: {exc}"]
    if not isinstance(payload, dict) or payload.get("schema") != "fb.edge_program.market_data_sources.v1":
        return ["invalid source-matrix schema"]
    if payload.get("credential_values_recorded") is not False:
        errors.append("credential values must never be recorded")
    definitions = payload.get("status_definitions")
    if not isinstance(definitions, dict) or set(definitions) != STATUSES:
        errors.append("status definitions must include exactly MISSING, LICENSED, UNVERIFIED, READY")
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        return errors + ["sources must be a nonempty list"]

    seen: set[str] = set()
    seen_runtime: set[str] = set()
    for index, source in enumerate(sources):
        label = f"source {index}"
        if (not isinstance(source, dict)
                or not REQUIRED_SOURCE_FIELDS.issubset(source)
                or set(source) - REQUIRED_SOURCE_FIELDS - OPTIONAL_SOURCE_FIELDS):
            errors.append(f"{label}: missing or unexpected fields")
            continue
        identifier = source["id"]
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            errors.append(f"{label}: empty or duplicate id")
        else:
            seen.add(identifier)
            label = identifier
        runtime_source_id = source.get("runtime_source_id")
        if runtime_source_id is not None and (
                not isinstance(runtime_source_id, str) or not runtime_source_id.strip()):
            errors.append(f"{label}: runtime_source_id must be a nonempty string")
        elif runtime_source_id is not None:
            if runtime_source_id in seen_runtime:
                errors.append(f"{label}: duplicate runtime_source_id")
            seen_runtime.add(runtime_source_id)
        if source["status"] not in STATUSES:
            errors.append(f"{label}: unsupported status")
        if not isinstance(source["products"], list) or not source["products"]:
            errors.append(f"{label}: products must be a nonempty list")
        access = source["access"]
        if not isinstance(access, dict) or set(access) != {
                "method", "credential_flags", "authorization_evidence", "runtime_ready"}:
            errors.append(f"{label}: invalid access record")
        else:
            if not isinstance(access["runtime_ready"], bool):
                errors.append(f"{label}: runtime_ready must be boolean")
            flags = access["credential_flags"]
            if not isinstance(flags, list):
                errors.append(f"{label}: credential_flags must be a list")
            else:
                for flag in flags:
                    if not isinstance(flag, dict) or set(flag) != {"name", "present", "observed_from"}:
                        errors.append(f"{label}: credential flags may contain names and booleans only")
                        continue
                    if not isinstance(flag["name"], str) or not isinstance(flag["observed_from"], str):
                        errors.append(f"{label}: malformed credential-presence flag")
                    if not isinstance(flag["present"], bool):
                        errors.append(f"{label}: credential presence must be boolean")
        latency = source["latency"]
        if not isinstance(latency, dict) or set(latency) != {
                "availability", "measured", "observed_clock_fields", "measurement_gap"}:
            errors.append(f"{label}: invalid latency record")
        elif not isinstance(latency["measured"], bool):
            errors.append(f"{label}: latency measured must be boolean")
        retention = source["retention"]
        if not isinstance(retention, dict) or set(retention) != {"status", "evidence", "gap"}:
            errors.append(f"{label}: invalid retention record")
        elif retention["status"] not in STATUSES:
            errors.append(f"{label}: unsupported retention status")
        paths = source["evidence_paths"]
        if not isinstance(paths, list) or len(set(map(str, paths))) != len(paths):
            errors.append(f"{label}: evidence_paths must be a unique list")
            continue
        if not paths and source["status"] != "MISSING":
            errors.append(f"{label}: only explicitly missing sources may have no evidence paths")
        for relative in paths:
            if not isinstance(relative, str) or "\\" in relative or Path(relative).is_absolute():
                errors.append(f"{label}: invalid evidence path {relative!r}")
                continue
            resolved = (root / relative).resolve()
            if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
                errors.append(f"{label}: evidence path does not exist: {relative}")
    return errors


if __name__ == "__main__":
    problems = validate(MATRIX, Path("."))
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        raise SystemExit(1)
    print("Market-data source inventory schema and evidence paths verified; credential values absent")
