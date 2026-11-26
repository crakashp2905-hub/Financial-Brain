"""Read gateway for datasets requiring one-time, owner-approved holdout access.

The expected digest must come from the frozen dataset manifest. The gateway consumes the
approval before opening the data file, then verifies the bytes against that manifest before
returning them to the evaluator.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from financial_brain.forward_edge.approvals import Approval
from financial_brain.research.holdout_access import HoldoutAccessLedger, HoldoutReceipt


class SealedDatasetError(PermissionError):
    """A sealed dataset could not be safely authorized, read, or verified."""


class SealedDatasetReader:
    """Resolve named datasets within a root and require a consumed receipt before reading.

    This is a local application boundary. Filesystem permissions and deployment controls
    must prevent evaluators from opening sealed files directly around this gateway.
    """

    def __init__(self, root: Path | str, ledger: HoldoutAccessLedger, *, owner_id: str,
                 max_bytes: int = 512 * 1024 * 1024) -> None:
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise SealedDatasetError("sealed dataset root must be a directory")
        if not owner_id.strip():
            raise SealedDatasetError("configured human owner identity is required")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise SealedDatasetError("max_bytes must be a positive integer")
        self.ledger = ledger
        self.owner_id = owner_id
        self.max_bytes = max_bytes

    def _resolve(self, relative_path: Path | str) -> Path:
        relative = Path(relative_path)
        if relative.is_absolute() or not relative.parts or any(part in {".", ".."} for part in relative.parts):
            raise SealedDatasetError("dataset path must be a safe relative path")
        candidate = self.root / relative
        current = self.root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise SealedDatasetError("symlinks are not allowed in sealed dataset paths")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self.root)
        except (OSError, ValueError) as exc:
            raise SealedDatasetError("dataset path is missing or escapes its configured root") from exc
        if not resolved.is_file():
            raise SealedDatasetError("sealed dataset path must identify a file")
        return resolved

    def _read_after_authorization(self, path: Path) -> bytes:
        try:
            with path.open("rb") as stream:
                payload = stream.read(self.max_bytes + 1)
        except OSError as exc:
            raise SealedDatasetError("authorized dataset could not be read") from exc
        if len(payload) > self.max_bytes:
            raise SealedDatasetError("sealed dataset exceeds the configured size limit")
        return payload

    def read_bytes(self, *, relative_path: Path | str, dataset_id: str,
                   dataset_sha256: str, run_id: str, preregistration_sha256: str,
                   approval: Approval, now: datetime) -> tuple[HoldoutReceipt, bytes]:
        """Consume the exact one-use approval, read once, verify, then expose dataset bytes."""
        path = self._resolve(relative_path)
        receipt = self.ledger.consume(
            dataset_id=dataset_id,
            dataset_sha256=dataset_sha256,
            run_id=run_id,
            preregistration_sha256=preregistration_sha256,
            authorized_owner_id=self.owner_id,
            approval=approval,
            now=now,
        )
        payload = self._read_after_authorization(path)
        if hashlib.sha256(payload).hexdigest() != dataset_sha256:
            raise SealedDatasetError("sealed dataset bytes do not match the frozen manifest hash")
        return receipt, payload


__all__ = ["SealedDatasetError", "SealedDatasetReader"]
