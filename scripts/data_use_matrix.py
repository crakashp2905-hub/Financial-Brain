"""Build or verify the provider/product data-use matrix without granting rights."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCES = Path("docs/edge-program/market-data-sources.v1.json")
OUTPUT = Path("docs/edge-program/data-use-matrix.v1.json")
DIMENSIONS = (
    "PRIVATE_RESEARCH",
    "RETAINED_HISTORY",
    "REALTIME_ALGORITHMIC_USE",
    "DERIVATIVES_DEPTH",
    "SYNTHETIC_REPLAY",
    "PAPER_SIMULATION",
    "REDISTRIBUTION",
    "PERMITTED_RETENTION",
)
RIGHTS_REF = "docs/edge-program/market-data-rights-review.v1.md"


def build_matrix(root: Path = ROOT) -> dict[str, Any]:
    raw = (root / SOURCES).read_bytes()
    inventory = json.loads(raw.decode("utf-8"))
    entries = []
    seen: set[tuple[str, str]] = set()
    for source in sorted(inventory.get("sources", []), key=lambda row: row["id"]):
        products = source.get("products", [])
        if not products:
            raise ValueError(f"source has no declared products: {source.get('id')}")
        provider_configured = source.get("provider") != "No provider configured"
        status = "UNKNOWN_RIGHTS" if provider_configured else "NOT_CONFIGURED"
        for product in sorted(set(products)):
            key = (source["id"], product)
            if key in seen:
                raise ValueError(f"duplicate provider/product pair: {key}")
            seen.add(key)
            entries.append({
                "source_id": source["id"],
                "provider": source["provider"],
                "product": product,
                "provider_configured": provider_configured,
                "use_status": {dimension: status for dimension in DIMENSIONS},
                "evidence_ref": RIGHTS_REF,
                "authorization_inferred": False,
            })
    if not entries:
        raise ValueError("market-data source inventory has no provider/product records")
    return {
        "schema": "fb.provider_product_data_use_matrix.v1",
        "source_inventory": SOURCES.as_posix(),
        "source_inventory_sha256": hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(),
        "dimensions": list(DIMENSIONS),
        "status_semantics": {
            "UNKNOWN_RIGHTS": "Provider/product use is not established by reviewed evidence; operational use remains denied.",
            "NOT_CONFIGURED": "No provider is configured for this product; it is unavailable and not authorized.",
        },
        "products": entries,
        "synthetic_replay_policy": {
            "status": "INTERNAL_FIXTURE_ONLY",
            "evidence_paths": ["src/financial_brain/forward_edge/fixtures.py",
                               "src/financial_brain/forward_edge/fixture_adapter.py"],
            "provider_data_included": False,
            "grants_provider_rights": False,
            "note": "Synthetic fixture replay is separate from replay of provider-derived market data; it cannot grant rights to that data.",
        },
    }


def rendered_bytes(root: Path = ROOT) -> bytes:
    return (json.dumps(build_matrix(root), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify generated matrix freshness")
    args = parser.parse_args()
    expected = rendered_bytes(ROOT)
    output = ROOT / OUTPUT
    if args.check:
        if not output.is_file() or output.read_bytes().replace(b"\r\n", b"\n") != expected:
            raise SystemExit("data-use matrix is missing or stale; run python scripts/data_use_matrix.py")
        print("provider/product data-use matrix is current")
    else:
        output.write_bytes(expected)
        print(f"wrote {OUTPUT.as_posix()} ({len(build_matrix()['products'])} products)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
