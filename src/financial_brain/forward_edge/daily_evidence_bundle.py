"""Portable, reproducible paper-session evidence bundles with optional Ed25519 signatures."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile

from financial_brain.forward_edge.durable import DurableJournal


class DailyBundleError(ValueError):
    """Daily evidence bundle is incomplete, tampered, or cannot be reproduced."""


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_bundle_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not value or any(part in {"", ".", ".."} for part in path.parts):
        raise DailyBundleError("bundle path must be a safe relative path")
    return path.as_posix()


def _read_inputs(bundle_dir: Path, manifest: dict) -> dict[str, bytes]:
    inputs = manifest.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise DailyBundleError("bundle must declare its exported inputs")
    output = {}
    for entry in inputs:
        if not isinstance(entry, dict):
            raise DailyBundleError("bundle input manifest row is invalid")
        role, relative, expected = entry.get("role"), entry.get("path"), entry.get("sha256")
        if not isinstance(role, str) or not role or role in output:
            raise DailyBundleError("bundle input roles must be unique nonempty names")
        relative = _safe_bundle_path(relative)
        path = bundle_dir.joinpath(*PurePosixPath(relative).parts)
        if path.is_symlink():
            raise DailyBundleError("bundle input cannot be a symbolic link")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise DailyBundleError(f"bundle input is missing: {role}") from exc
        if _sha(data) != expected:
            raise DailyBundleError(f"bundle input hash mismatch: {role}")
        output[role] = data
    if not {"paper_account_archive", "runner_journal"}.issubset(output):
        raise DailyBundleError("bundle needs account archive and runner journal inputs")
    return output


def _verified_account(data: bytes, session_date: str) -> dict:
    try:
        account = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise DailyBundleError("paper account archive is not valid JSON") from exc
    digest = account.get("snapshot_sha256")
    unsigned = {key: value for key, value in account.items() if key != "snapshot_sha256"}
    if not isinstance(digest, str) or _sha(_canonical(unsigned)) != digest:
        raise DailyBundleError("paper account archive snapshot hash is invalid")
    if account.get("session_date") != session_date:
        raise DailyBundleError("paper account archive date differs from the bundle session")
    return account


def render_operator_report(*, session_date: str, session_id: str,
                           inputs: dict[str, bytes]) -> bytes:
    """Render a stable UTF-8 report solely from exported, hash-verified inputs."""
    account = _verified_account(inputs["paper_account_archive"], session_date)
    with tempfile.NamedTemporaryFile(prefix="financial-brain-report-", suffix=".jsonl", delete=False) as temp:
        runner_path = Path(temp.name)
        temp.write(inputs["runner_journal"])
    try:
        records = DurableJournal(runner_path).records()
    except Exception as exc:
        raise DailyBundleError("runner journal failed its durable hash-chain check") from exc
    finally:
        runner_path.unlink(missing_ok=True)
    selected = [row for row in records if row.get("session_id") == session_id]
    plan = next((row for row in selected if row.get("t") == "SESSION_PLAN"), None)
    if plan is None or plan.get("config", {}).get("exchange_date") != session_date:
        raise DailyBundleError("runner journal does not contain the requested dated session plan")
    closed = next((row for row in reversed(selected) if row.get("t") == "SESSION_CLOSED"), None)
    incomplete = next((row for row in reversed(selected) if row.get("t") == "SESSION_INCOMPLETE"), None)
    if incomplete:
        outcome = f"INCOMPLETE — {incomplete.get('reason', 'reason unavailable')}"
    elif closed:
        outcome = str(closed.get("result", {}).get("status", "CLOSED_STATUS_UNKNOWN"))
    else:
        outcome = "UNFINISHED — no terminal session record"
    counts = {kind: sum(row.get("t") == kind for row in selected) for kind in (
        "MARKET_EVENT", "DECISION", "PAPER_INTENT", "PAPER_EXECUTION",
        "PAPER_ORDER_UPDATE", "PAPER_SKIP",
    )}
    inventory = account.get("inventory", [])
    orders = account.get("orders", [])
    lines = [
        "Financial Brain — Paper Session Evidence Report",
        f"Exchange session: {session_date}",
        f"Session ID: {session_id}",
        f"Outcome: {outcome}",
        f"Account: {account['account_id']} ({account['currency']})",
        f"Cash: {account['cash']}",
        f"Available cash: {account['available_cash']}",
        f"NAV: {account['equity']}",
        f"Realized P&L: {account['realized_pnl']}",
        f"Net realized P&L: {account['net_realized_pnl']}",
        f"Fees: {account['fees']}",
        f"Order rows: {len(orders)}; working rows: {sum(row.get('state') not in {
            "FILLED", "CANCELED", "REJECTED", "EXPIRED"} for row in orders)}",
        "Order quantities (order | state | filled | remaining):",
    ]
    for order in orders:
        lines.append(f"  {order['client_id']} | {order['state']} | {order['filled']} | {order['remaining']}")
    lines.append("Inventory (symbol | quantity | average cost | close mark | market value):")
    for position in inventory:
        lines.append(f"  {position['symbol']} | {position['quantity']} | {position['average_cost']} | "
                     f"{position['mark_price']} | {position['market_value']}")
    lines.append("Runner evidence counts:")
    lines.extend(f"  {kind}: {count}" for kind, count in counts.items())
    lines.extend([
        f"Account archive snapshot SHA-256: {account['snapshot_sha256']}",
        "The report describes exported paper evidence. It does not claim live execution or profitability.",
    ])
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_daily_bundle(*, session_date: str, session_id: str, artifact_paths: dict[str, Path],
                       output_dir: Path, signing_key=None) -> dict:
    """Export inputs, deterministic report, and content-addressed manifest atomically.

    `artifact_paths` must include roles `paper_account_archive` and `runner_journal`.
    An optional cryptography Ed25519 private-key object signs the canonical manifest core.
    """
    try:
        from datetime import date
        if date.fromisoformat(session_date).isoformat() != session_date:
            raise ValueError
    except ValueError as exc:
        raise DailyBundleError("session_date must be a canonical ISO exchange date") from exc
    if not isinstance(session_id, str) or not session_id.strip():
        raise DailyBundleError("session id is required")
    if not isinstance(artifact_paths, dict) or not {"paper_account_archive", "runner_journal"}.issubset(artifact_paths):
        raise DailyBundleError("account archive and runner journal paths are required")
    payloads: dict[str, bytes] = {}
    input_manifest = []
    for role, source in sorted(artifact_paths.items()):
        if not isinstance(role, str) or not role or not isinstance(source, Path) or not source.is_file():
            raise DailyBundleError("every bundle input must have a role and an existing file path")
        relative = f"inputs/{_safe_bundle_path(role)}{source.suffix.lower()}"
        if relative in payloads:
            raise DailyBundleError("bundle input path collision")
        data = source.read_bytes()
        payloads[relative] = data
        input_manifest.append({"role": role, "path": relative, "sha256": _sha(data), "size_bytes": len(data)})
    inputs = {entry["role"]: payloads[entry["path"]] for entry in input_manifest}
    report = render_operator_report(session_date=session_date, session_id=session_id, inputs=inputs)
    core = {
        "schema_version": "1.0", "session_date": session_date, "session_id": session_id,
        "inputs": input_manifest, "operator_report_sha256": _sha(report),
        "report_path": "operator-report.txt",
    }
    signature = None
    public_key = None
    if signing_key is not None:
        try:
            from cryptography.hazmat.primitives import serialization
            signature = base64.b64encode(signing_key.sign(_canonical(core))).decode("ascii")
            public_key = base64.b64encode(signing_key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw,
            )).decode("ascii")
        except (AttributeError, ImportError, ValueError) as exc:
            raise DailyBundleError("signing key must be a usable Ed25519 private key") from exc
    public_key_sha256 = _sha(base64.b64decode(public_key)) if public_key else None
    manifest = {**core, "manifest_sha256": _sha(_canonical(core)),
                "signature_algorithm": "Ed25519" if signature else None,
                "signature_base64": signature, "public_key_base64": public_key,
                "public_key_sha256": public_key_sha256}
    output_dir = Path(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    if output_dir.exists():
        try:
            prior = verify_daily_bundle(output_dir)
            existing_manifest = json.loads((output_dir / "manifest.json").read_bytes())
        except DailyBundleError as exc:
            raise DailyBundleError("existing bundle directory is not a valid immutable bundle") from exc
        except (OSError, ValueError) as exc:
            raise DailyBundleError("existing bundle manifest cannot be read") from exc
        if existing_manifest != manifest:
            raise DailyBundleError("daily bundle output already exists with different content")
        return prior
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        for relative, data in payloads.items():
            path = staging.joinpath(*PurePosixPath(relative).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (staging / "operator-report.txt").write_bytes(report)
        (staging / "manifest.json").write_bytes(_canonical(manifest) + b"\n")
        verify_daily_bundle(staging)
        os.replace(staging, output_dir)
        return verify_daily_bundle(output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_daily_bundle(bundle_dir: Path, *, trusted_public_key_sha256: str | None = None) -> dict:
    """Verify all exported hashes/signature and independently reproduce the human report."""
    bundle_dir = Path(bundle_dir)
    try:
        manifest = json.loads((bundle_dir / "manifest.json").read_bytes())
        report = (bundle_dir / "operator-report.txt").read_bytes()
    except (OSError, ValueError) as exc:
        raise DailyBundleError("bundle manifest or operator report is missing/invalid") from exc
    core = {key: manifest.get(key) for key in (
        "schema_version", "session_date", "session_id", "inputs",
        "operator_report_sha256", "report_path",
    )}
    digest = _sha(_canonical(core))
    if manifest.get("manifest_sha256") != digest:
        raise DailyBundleError("daily manifest hash does not match its contents")
    if manifest.get("report_path") != "operator-report.txt" or _sha(report) != manifest.get("operator_report_sha256"):
        raise DailyBundleError("operator report hash does not match the manifest")
    inputs = _read_inputs(bundle_dir, manifest)
    reproduced = render_operator_report(session_date=manifest["session_date"],
                                        session_id=manifest["session_id"], inputs=inputs)
    if reproduced != report:
        raise DailyBundleError("operator report cannot be reproduced from exported inputs")
    algorithm = manifest.get("signature_algorithm")
    signature = manifest.get("signature_base64")
    public_key = manifest.get("public_key_base64")
    if algorithm is None:
        if signature is not None or public_key is not None or manifest.get("public_key_sha256") is not None:
            raise DailyBundleError("unsigned manifest contains partial signature metadata")
        signature_status = "UNSIGNED_HASHED"
    elif algorithm == "Ed25519" and signature and public_key:
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            public_bytes = base64.b64decode(public_key, validate=True)
            if _sha(public_bytes) != manifest.get("public_key_sha256"):
                raise DailyBundleError("manifest public-key hash does not match its contents")
            if trusted_public_key_sha256 is not None and trusted_public_key_sha256 != _sha(public_bytes):
                raise DailyBundleError("signing public key does not match the trusted key identity")
            Ed25519PublicKey.from_public_bytes(public_bytes).verify(
                base64.b64decode(signature, validate=True), _canonical(core))
        except DailyBundleError:
            raise
        except Exception as exc:
            raise DailyBundleError("daily manifest signature verification failed") from exc
        signature_status = "ED25519_TRUSTED" if trusted_public_key_sha256 else "ED25519_VALID_UNPINNED"
    else:
        raise DailyBundleError("manifest signature metadata is incomplete or unsupported")
    allowed = {"manifest.json", "operator-report.txt", *(entry["path"] for entry in manifest["inputs"])}
    all_paths = list(bundle_dir.rglob("*"))
    if any(path.is_symlink() for path in all_paths):
        raise DailyBundleError("bundle cannot contain symbolic links")
    actual = {path.relative_to(bundle_dir).as_posix() for path in all_paths if path.is_file()}
    if actual != allowed:
        raise DailyBundleError("bundle contains missing or undeclared files")
    return {"verified": True, "manifest_sha256": digest,
            "signature_status": signature_status, "input_count": len(inputs),
            "report_reproduced": True}
