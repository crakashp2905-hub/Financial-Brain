"""Deterministic reanalysis notebook and hashed/signed evidence-bundle export (FE-0121)."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class EvidenceBundleError(ValueError):
    """Evidence bundle is unsafe, inconsistent or fails verification."""


_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


@dataclass(frozen=True)
class EvidenceBundleReport:
    output: Path
    bundle_sha256: str
    manifest_sha256: str
    file_count: int
    signature_status: str


def _safe_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    parts = normalized.split("/")
    if (not _NAME.fullmatch(normalized) or normalized.startswith("/")
            or any(part in {"", ".", ".."} for part in parts)
            or (len(normalized) > 1 and normalized[1] == ":")):
        raise EvidenceBundleError(f"unsafe archive member name: {name!r}")
    return normalized


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _notebook(*, entries: list[dict[str, object]], command: tuple[str, ...]) -> bytes:
    expected = {str(row["path"]): str(row["sha256"]) for row in entries}
    code = (
        "from pathlib import Path\n"
        "import hashlib\n"
        f"expected = {expected!r}\n"
        "for name, digest in expected.items():\n"
        "    path = Path(name)\n"
        "    actual = hashlib.sha256(path.read_bytes()).hexdigest()\n"
        "    if actual != digest:\n"
        "        raise RuntimeError(f'Hash mismatch: {name}')\n"
        "print(f'Verified {len(expected)} frozen inputs')\n"
    )
    run_cell = (
        "import subprocess\n"
        f"command = {list(command)!r}\n"
        "if command:\n"
        "    print('Running frozen reproduction command:', command)\n"
        "    subprocess.run(command, check=True)\n"
        "else:\n"
        "    print('No reproduction command was declared in this bundle.')\n"
    )
    notebook = {
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": [
                "# Frozen experiment reanalysis\n",
                "Verify every included input hash before running the declared reproduction command.\n",
                "This notebook does not assert that the experiment succeeded or is profitable.\n"]},
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
             "source": code.splitlines(keepends=True)},
            {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
             "source": run_cell.splitlines(keepends=True)},
        ],
        "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python",
                                     "name": "python3"},
                     "language_info": {"name": "python", "version": "3"}},
        "nbformat": 4, "nbformat_minor": 5,
    }
    return json.dumps(notebook, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8") + b"\n"


def export_evidence_bundle(*, files: Mapping[str, Path], output: Path,
                           reproduction_command: tuple[str, ...] = (),
                           signing_private_key: bytes | None = None) -> EvidenceBundleReport:
    """Create a deterministic ZIP with frozen inputs, hashes, notebook and optional local signature.

    A signature is only reported as locally signed. It becomes trusted only when verification
    receives the owner's independently distributed public key. Input files are copied as bytes;
    no included notebook or script is executed during export.
    """
    if not files:
        raise EvidenceBundleError("at least one frozen experiment file is required")
    if any(not part or part in {".", ".."} for part in reproduction_command):
        raise EvidenceBundleError("reproduction command arguments must be explicit non-empty strings")
    payloads: dict[str, bytes] = {}
    for name, source in files.items():
        safe = _safe_name(name)
        if safe in payloads or safe in {"MANIFEST.json", "SHA256SUMS.txt", "attestation.json",
                                        "reanalysis.ipynb"}:
            raise EvidenceBundleError(f"duplicate or reserved archive member: {safe}")
        path = Path(source)
        if not path.is_file() or path.is_symlink():
            raise EvidenceBundleError(f"evidence input must be an existing regular file: {path}")
        payloads[safe] = path.read_bytes()
    entries = [{"path": name, "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}
               for name, data in sorted(payloads.items())]
    notebook_bytes = _notebook(entries=entries, command=reproduction_command)
    entries.append({"path": "reanalysis.ipynb", "size_bytes": len(notebook_bytes),
                    "sha256": hashlib.sha256(notebook_bytes).hexdigest()})
    manifest_core: dict[str, object] = {
        "schema": "financial-brain.evidence-bundle.v1",
        "files": entries,
        "reproduction_command": list(reproduction_command),
    }
    manifest_hash = hashlib.sha256(_canonical(manifest_core)).hexdigest()
    manifest = {**manifest_core, "manifest_sha256": manifest_hash}
    payloads["reanalysis.ipynb"] = notebook_bytes
    payloads["MANIFEST.json"] = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    checksum_lines = [f"{row['sha256']}  {row['path']}" for row in entries]
    checksum_lines.append(f"{hashlib.sha256(payloads['MANIFEST.json']).hexdigest()}  MANIFEST.json")
    payloads["SHA256SUMS.txt"] = ("\n".join(checksum_lines) + "\n").encode("utf-8")
    signature_status = "UNSIGNED"
    if signing_private_key is not None:
        try:
            private_key = Ed25519PrivateKey.from_private_bytes(signing_private_key)
        except ValueError as exc:
            raise EvidenceBundleError("Ed25519 private key must be exactly 32 raw bytes") from exc
        public_key = private_key.public_key().public_bytes_raw()
        signature = private_key.sign(bytes.fromhex(manifest_hash))
        attestation = {"scheme": "Ed25519", "scope": "manifest_sha256",
                       "manifest_sha256": manifest_hash,
                       "public_key_sha256": hashlib.sha256(public_key).hexdigest(),
                       "public_key_b64": base64.b64encode(public_key).decode("ascii"),
                       "signature_b64": base64.b64encode(signature).decode("ascii"),
                       "trust_status": "LOCAL_SIGNATURE_UNVERIFIED"}
        payloads["attestation.json"] = json.dumps(attestation, sort_keys=True,
                                                   indent=2).encode("utf-8") + b"\n"
        signature_status = "LOCAL_SIGNATURE_UNVERIFIED"

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=output_path.name + ".", suffix=".tmp",
                                          dir=output_path.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary_name, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=9) as archive:
            for name, data in sorted(payloads.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                info.create_system = 3
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        os.replace(temporary_name, output_path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    bundle_hash = hashlib.sha256(output_path.read_bytes()).hexdigest()
    return EvidenceBundleReport(output_path, bundle_hash, manifest_hash, len(entries), signature_status)


def verify_evidence_bundle(path: Path, *, trusted_public_key: bytes | None = None) -> EvidenceBundleReport:
    """Verify archive paths, content hashes and (when supplied) an external trusted signing key."""
    archive_path = Path(path)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise EvidenceBundleError("duplicate archive members are not allowed")
            for name in names:
                _safe_name(name)
            manifest = json.loads(archive.read("MANIFEST.json"))
            files = manifest["files"]
            core = {key: manifest[key] for key in
                    ("schema", "files", "reproduction_command")}
            manifest_hash = hashlib.sha256(_canonical(core)).hexdigest()
            if (manifest.get("schema") != "financial-brain.evidence-bundle.v1"
                    or manifest.get("manifest_sha256") != manifest_hash):
                raise EvidenceBundleError("manifest digest or schema verification failed")
            expected_names = {str(row["path"]) for row in files} | {
                "MANIFEST.json", "SHA256SUMS.txt", "reanalysis.ipynb"}
            attested = "attestation.json" in names
            if attested:
                expected_names.add("attestation.json")
            if set(names) != expected_names:
                raise EvidenceBundleError("archive members do not match the manifest")
            for row in files:
                name = _safe_name(str(row["path"]))
                payload = archive.read(name)
                if len(payload) != row["size_bytes"] or hashlib.sha256(payload).hexdigest() != row["sha256"]:
                    raise EvidenceBundleError(f"input hash verification failed for {name}")
            sums = archive.read("SHA256SUMS.txt").decode("utf-8").splitlines()
            for line in sums:
                digest, name = line.split("  ", maxsplit=1)
                payload = archive.read(_safe_name(name))
                if hashlib.sha256(payload).hexdigest() != digest:
                    raise EvidenceBundleError(f"checksum list verification failed for {name}")
            signature_status = "UNSIGNED"
            if attested:
                statement = json.loads(archive.read("attestation.json"))
                embedded_key = base64.b64decode(statement["public_key_b64"], validate=True)
                signature = base64.b64decode(statement["signature_b64"], validate=True)
                if statement.get("manifest_sha256") != manifest_hash:
                    raise EvidenceBundleError("signature manifest digest does not match")
                if hashlib.sha256(embedded_key).hexdigest() != statement.get("public_key_sha256"):
                    raise EvidenceBundleError("embedded signing key digest does not match")
                key_bytes = trusted_public_key if trusted_public_key is not None else embedded_key
                if trusted_public_key is not None and embedded_key != trusted_public_key:
                    raise EvidenceBundleError("signer does not match the supplied trusted public key")
                try:
                    Ed25519PublicKey.from_public_bytes(key_bytes).verify(
                        signature, bytes.fromhex(manifest_hash))
                except (InvalidSignature, ValueError) as exc:
                    raise EvidenceBundleError("Ed25519 signature verification failed") from exc
                signature_status = ("SIGNED_TRUSTED" if trusted_public_key is not None
                                    else "SIGNED_UNVERIFIED")
            elif trusted_public_key is not None:
                raise EvidenceBundleError("trusted signature verification requested but bundle is unsigned")
    except (KeyError, OSError, zipfile.BadZipFile, json.JSONDecodeError, ValueError) as exc:
        if isinstance(exc, EvidenceBundleError):
            raise
        raise EvidenceBundleError(f"invalid evidence bundle: {exc}") from exc
    bundle_hash = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    return EvidenceBundleReport(archive_path, bundle_hash, manifest_hash, len(files), signature_status)
