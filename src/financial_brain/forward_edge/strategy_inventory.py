"""Evidence-tiered inventory for registered, eligible and exploratory strategies (FE-0115)."""
from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from financial_brain.forward_edge.eligibility import build_catalogue


@dataclass(frozen=True)
class StrategyInventoryRow:
    strategy_id: str
    strategy_class: str
    eligibility: str
    frozen_version: str
    enrollment: str
    evidence_tier: str
    source_ref: str
    source_sha256: str
    exclusion_reason: str


@dataclass(frozen=True)
class StrategyInventory:
    rows: tuple[StrategyInventoryRow, ...]
    catalogue_sha256: str
    inventory_sha256: str


def build_strategy_inventory(repo_root: Path, *, frozen_versions: Mapping[str, str] | None = None,
                             enrollments: Mapping[str, str] | None = None,
                             evidence_tiers: Mapping[str, str] | None = None) -> StrategyInventory:
    """List every known source-registry and historical-trial candidate conservatively.

    Optional operating records are supplied by the forward runner; omitted data renders
    as NOT_FROZEN/NOT_ENROLLED/UNVERIFIED, never as a successful enrollment or evidence tier.
    """
    frozen = frozen_versions or {}
    enrolled = enrollments or {}
    tiers = evidence_tiers or {}
    catalogue = build_catalogue(Path(repo_root))
    rows = []
    for candidate in catalogue["candidates"]:
        strategy_id = str(candidate["strategy_id"])
        eligibility = str(candidate["status"])
        if eligibility == "FAILED":
            tier = "FAILED_HISTORICAL"
        elif candidate["source_type"] == "external_reference":
            tier = "REFERENCE_ONLY"
        else:
            tier = tiers.get(strategy_id, "UNVERIFIED")
        rows.append(StrategyInventoryRow(
            strategy_id=strategy_id,
            strategy_class=str(candidate["source_type"]),
            eligibility=eligibility,
            frozen_version=frozen.get(strategy_id, "NOT_FROZEN"),
            enrollment=enrolled.get(strategy_id, "NOT_ENROLLED"),
            evidence_tier=tier,
            source_ref=str(candidate["source_ref"]),
            source_sha256=str(candidate["source_sha256"]),
            exclusion_reason=str(candidate["exclusion_reason"])))
    rows.sort(key=lambda row: (row.eligibility != "FAILED", row.strategy_id))
    body = [{"strategy_id": row.strategy_id, "strategy_class": row.strategy_class,
             "eligibility": row.eligibility, "frozen_version": row.frozen_version,
             "enrollment": row.enrollment, "evidence_tier": row.evidence_tier,
             "source_ref": row.source_ref, "source_sha256": row.source_sha256,
             "exclusion_reason": row.exclusion_reason} for row in rows]
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                       allow_nan=False).encode()).hexdigest()
    return StrategyInventory(tuple(rows), str(catalogue["catalogue_sha256"]), digest)


def render_strategy_inventory(inventory: StrategyInventory, output: Path) -> Path:
    """Write a self-contained inventory with failed candidates visually distinct."""
    body = []
    for row in inventory.rows:
        failed = row.eligibility == "FAILED"
        cells = (row.strategy_id, row.strategy_class, row.eligibility, row.frozen_version,
                 row.enrollment, row.evidence_tier, row.source_ref, row.exclusion_reason)
        body.append("<tr class='failed'>" + "".join(
            "<td>" + html.escape(value) + "</td>" for value in cells) + "</tr>" if failed else
            "<tr>" + "".join("<td>" + html.escape(value) + "</td>" for value in cells) + "</tr>")
    page = ("<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            "<title>Strategy inventory</title><style>body{font:14px system-ui;margin:2rem;color:#1d2321}"
            "table{border-collapse:collapse;width:100%}td,th{padding:7px;border:1px solid #ddd;text-align:left}"
            ".failed{background:#fee8e6;color:#7c1d18}code{overflow-wrap:anywhere}</style><main>"
            "<h1>Strategy inventory</h1><p>Failed historical trials are red; unknown enrollment and evidence remain explicit. "
            "Reference records are not executable validations.</p><p>Catalogue: <code>" + inventory.catalogue_sha256
            + "</code><br>Inventory: <code>" + inventory.inventory_sha256 + "</code></p><div style='overflow:auto'>"
            "<table><thead><tr><th>Strategy</th><th>Class</th><th>Eligibility</th><th>Frozen version</th>"
            "<th>Enrollment</th><th>Evidence tier</th><th>Source</th><th>Disposition</th></tr></thead><tbody>"
            + "".join(body) + "</tbody></table></div></main></html>")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(page, encoding="utf-8")
    return destination
