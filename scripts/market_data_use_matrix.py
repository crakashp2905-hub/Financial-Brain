"""Build a source-by-product market-data use matrix without inferring permission."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "docs/edge-program/market-data-sources.v1.json"
PERMISSIONS = ROOT / "docs/edge-program/data-permissions.v1.json"
RIGHTS_REVIEW = ROOT / "docs/edge-program/market-data-rights-review.v1.md"
OUTPUT = ROOT / "docs/edge-program/market-data-use-matrix.v1.json"
KITE_PERMISSION_ALIAS = "KITE_CONNECT_READ_ONLY"
DIMENSIONS = (
    "private_research", "retained_history", "real_time_algorithmic_use", "derivatives_depth",
    "synthetic_or_replay", "paper_simulation", "redistribution", "permitted_retention_period",
)


def _sha(path: Path) -> str:
    # Hash canonical text bytes so Git's CRLF conversion cannot make the same
    # source registry produce a different matrix on Windows and Linux.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def build() -> dict[str, Any]:
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))["sources"]
    permissions = json.loads(PERMISSIONS.read_text(encoding="utf-8"))["permissions"]
    by_key = {(row["source_id"], row["use"]): row for row in permissions}
    rows = []
    for source in sorted(sources, key=lambda item: item["id"]):
        source_id = source["id"]
        permission_id = KITE_PERMISSION_ALIAS if source_id == "kite_connect_quotes_history_instruments" else source_id
        data_class = f"{source['category']} {' '.join(source['products'])}".lower()
        has_derivatives = source_id in {
            "kite_connect_quotes_history_instruments", "nse_reference_and_fno_files",
            "nse_licensed_realtime_depth", "expired_options_history_and_chain",
        } or any(term in data_class for term in ("derivative", "option", "f&o", "depth", "contract"))

        def permission(use: str, _permission_source_id: str = permission_id) -> dict[str, Any]:
            record = by_key.get((_permission_source_id, use))
            return {
                "status": record["status"] if record else "UNKNOWN_RIGHTS",
                "blocks_operational_use": not bool(record and record["status"] == "USE_ALLOWED"),
                "permission_use": use,
                "evidence_ref": record["evidence_ref"] if record else None,
                "scope_gap": "No source/use permission record." if record is None else record["notes"],
            }

        dimensions = {
            "private_research": permission("RESEARCH"),
            "retained_history": permission("RETENTION"),
            "real_time_algorithmic_use": permission("PROSPECTIVE_PAPER_CAPTURE"),
            "derivatives_depth": ({
                "status": "UNKNOWN_RIGHTS", "blocks_operational_use": True,
                "permission_use": None, "evidence_ref": None,
                "scope_gap": "No product-specific derivatives depth entitlement or use permission is recorded.",
            } if has_derivatives else {
                "status": "NOT_APPLICABLE", "blocks_operational_use": True,
                "permission_use": None, "evidence_ref": None,
                "scope_gap": "This source/product does not provide derivatives depth.",
            }),
            "synthetic_or_replay": {
                "status": "UNKNOWN_RIGHTS", "blocks_operational_use": True,
                "permission_use": "RESEARCH+RETENTION", "evidence_ref": "docs/edge-program/data-permissions.v1.json",
                "scope_gap": "Replay of provider history inherits source retention/research restrictions; generated synthetic fixtures are separately listed.",
            },
            "paper_simulation": permission("PAPER_SIMULATION"),
            "redistribution": permission("REDISTRIBUTION"),
            "permitted_retention_period": {
                "status": permission("RETENTION")["status"],
                "blocks_operational_use": True, "period": None,
                "permission_use": "RETENTION", "evidence_ref": "docs/edge-program/data-permissions.v1.json",
                "scope_gap": "No permission period, expiry, purge rule or post-subscription retention duration is recorded.",
            },
        }
        rows.append({
            "source_id": source_id, "provider": source["provider"], "category": source["category"],
            "products": source["products"], "source_access_status": source["status"],
            "permission_source_id": permission_id, "uses": dimensions,
        })
    rows.append({
        "source_id": "LOCAL_DETERMINISTIC_SYNTHETIC_FIXTURES", "provider": "Financial Brain test fixtures",
        "category": "synthetic_test_data", "products": ["deterministic synthetic prices/events; no observed exchange records"],
        "source_access_status": "LOCAL_FIXTURE", "permission_source_id": None,
        "uses": {
            key: ({"status": "SYNTHETIC_ONLY_ALLOWED", "blocks_operational_use": False,
                   "scope_gap": "Local deterministic software-test fixtures only; not market evidence or a live-paper data source."}
                  if key in {"private_research", "synthetic_or_replay", "paper_simulation"} else
                  {"status": "NOT_APPLICABLE", "blocks_operational_use": True,
                   "scope_gap": "Synthetic fixtures do not grant provider data rights or represent live executable quotes."})
            for key in DIMENSIONS
        },
    })
    return {
        "schema": "fb.market_data_use_matrix.v1",
        "as_of": "2026-11-26",
        "source_registry_sha256": _sha(SOURCES),
        "permission_register_sha256": _sha(PERMISSIONS),
        "rights_review_sha256": _sha(RIGHTS_REVIEW),
        "dimension_definitions": {
            key: "UNKNOWN_RIGHTS blocks use unless a current, exact source/product/use approval is evidenced."
            for key in DIMENSIONS
        },
        "source_count": len(sources),
        "synthetic_fixture_rows_are_not_provider_sources": True,
        "sources": rows,
        "limitations": [
            "This is an operational deny-by-default matrix, not legal advice or a permission grant.",
            "Paid subscription, public download and technical accessibility do not imply rights for a listed use.",
            "Real-time algorithmic/non-display use and derivatives depth need explicit, source-specific contract scope.",
            "Only generated deterministic fixtures are marked synthetic-only allowed; historic/provider-data replay inherits provider restrictions.",
        ],
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = json.dumps(build(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != expected:
            raise SystemExit("market-data use matrix is stale")
        print("market-data use matrix is current; unresolved provider rights remain blocked")
    else:
        OUTPUT.write_text(expected, encoding="utf-8", newline="\n")
        print(f"wrote {OUTPUT.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
