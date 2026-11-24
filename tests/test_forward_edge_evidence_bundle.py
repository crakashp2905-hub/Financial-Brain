import json
import zipfile

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from financial_brain.forward_edge.evidence_bundle import (
    EvidenceBundleError,
    export_evidence_bundle,
    verify_evidence_bundle,
)


def test_bundle_is_deterministic_hashed_and_contains_reanalysis_notebook(tmp_path):
    source = tmp_path / "frozen.csv"
    source.write_text("session,net_alpha\n2026-11-14,0.01\n", encoding="utf-8")
    first = export_evidence_bundle(files={"inputs/frozen.csv": source},
                                   output=tmp_path / "first.zip",
                                   reproduction_command=("python", "reproduce.py"))
    second = export_evidence_bundle(files={"inputs/frozen.csv": source},
                                    output=tmp_path / "second.zip",
                                    reproduction_command=("python", "reproduce.py"))
    assert first.bundle_sha256 == second.bundle_sha256
    assert first.file_count == 2  # frozen input plus the generated reanalysis notebook
    assert verify_evidence_bundle(first.output).bundle_sha256 == first.bundle_sha256
    with zipfile.ZipFile(first.output) as archive:
        names = set(archive.namelist())
        assert {"MANIFEST.json", "SHA256SUMS.txt", "reanalysis.ipynb", "inputs/frozen.csv"} <= names
        notebook = json.loads(archive.read("reanalysis.ipynb"))
        assert notebook["nbformat"] == 4
        assert "Hash mismatch" in "".join(notebook["cells"][1]["source"])
        assert "python" in "".join(notebook["cells"][2]["source"])


def test_optional_signature_is_only_trusted_with_external_matching_public_key(tmp_path):
    source = tmp_path / "report.json"
    source.write_text('{"rows": 2}\n', encoding="utf-8")
    private = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public_bytes = private.public_key().public_bytes_raw()
    bundle = export_evidence_bundle(files={"report.json": source}, output=tmp_path / "signed.zip",
                                    signing_private_key=bytes(range(32)))
    assert bundle.signature_status == "LOCAL_SIGNATURE_UNVERIFIED"
    assert verify_evidence_bundle(bundle.output).signature_status == "SIGNED_UNVERIFIED"
    assert verify_evidence_bundle(bundle.output,
                                  trusted_public_key=public_bytes).signature_status == "SIGNED_TRUSTED"
    with pytest.raises(EvidenceBundleError, match="trusted public key"):
        verify_evidence_bundle(bundle.output, trusted_public_key=b"x" * 32)


def test_bundle_refuses_traversal_names_and_detects_content_tampering(tmp_path):
    source = tmp_path / "report.json"
    source.write_text("ok", encoding="utf-8")
    with pytest.raises(EvidenceBundleError, match="unsafe archive"):
        export_evidence_bundle(files={"../secret": source}, output=tmp_path / "bad.zip")
    bundle = export_evidence_bundle(files={"report.json": source}, output=tmp_path / "bundle.zip")
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(bundle.output) as original, zipfile.ZipFile(tampered, "w") as changed:
        for info in original.infolist():
            payload = b"tampered" if info.filename == "report.json" else original.read(info.filename)
            changed.writestr(info, payload)
    with pytest.raises(EvidenceBundleError, match="hash verification failed"):
        verify_evidence_bundle(tampered)
