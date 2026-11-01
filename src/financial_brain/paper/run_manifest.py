"""Deterministic identity for a paper-trading run.

The manifest records what was run and what it was allowed to consume. It does not
claim that a paper result is forward evidence or grant any broker permission.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .capabilities import require


@dataclass(frozen=True)
class PaperRunManifest:
    run_id: str
    day: str
    mode: str
    capability: str
    capability_version: str
    data_hash: str
    required_inputs: tuple[str, ...]
    evidence_mode: str = "paper"

    def canonical(self) -> bytes:
        require(self.capability)
        if self.mode not in {"daily", "intraday", "options"}:
            raise ValueError("unknown paper mode")
        if self.evidence_mode not in {"paper", "replay"}:
            raise ValueError("invalid evidence mode")
        if not self.run_id or not self.day or len(self.data_hash) != 64:
            raise ValueError("incomplete paper manifest")
        payload = {
            "run_id": self.run_id, "day": self.day, "mode": self.mode,
            "capability": self.capability, "capability_version": self.capability_version,
            "data_hash": self.data_hash, "required_inputs": sorted(set(self.required_inputs)),
            "evidence_mode": self.evidence_mode,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical()).hexdigest()
