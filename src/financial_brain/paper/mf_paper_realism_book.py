"""Build an as-of MF paper-realism assessment from persisted book evidence."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from ..kernel.mf_paper_realism import (
    MFPaperRealismAssessment, MFRealismBasis, MFRealismDimension, MFRealismFactor,
)

_EXPECTED_PAYMENT_MILESTONES = 3
_SETTLEMENT_CONFIDENCE = {
    "REDEMPTION_PENDING": Decimal("0.10"),
    "VALUE_CONFIRMED": Decimal("0.50"),
    "PAYOUT_PENDING": Decimal("0.75"),
    "CASH_AVAILABLE_SIMULATED": Decimal("0.85"),
    "CASH_AVAILABLE_OBSERVED": Decimal("1.00"),
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("assessed_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _aggregate_sources(rows: list[tuple[str, str | None, str | None, str | None]]) -> tuple[
    MFRealismBasis, str | None, str | None, str | None,
]:
    """Preserve evidence lineage when every input fact is source-attested."""
    if not rows or any(
        basis != MFRealismBasis.OBSERVED_EVIDENCE.value
        or not source_id or not source_ref
        or not re.fullmatch(r"[0-9a-fA-F]{64}", source_hash or "")
        for basis, source_id, source_ref, source_hash in rows
    ):
        return MFRealismBasis.SIMULATED_ASSUMPTION, None, None, None
    canonical = json.dumps(sorted(rows), separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    source_ids = "+".join(sorted({row[1] or "" for row in rows}))
    if len(source_ids) > 120:
        source_ids = "MULTI_SOURCE:" + hashlib.sha256(source_ids.encode()).hexdigest()[:24]
    return (
        MFRealismBasis.OBSERVED_EVIDENCE,
        source_ids,
        f"mf-paper-realism:aggregate:{digest[:24]}",
        digest,
    )


def _factor(
    dimension: MFRealismDimension,
    value: Decimal,
    assessed_at: datetime,
    rationale: str,
    sources: list[tuple[str, str | None, str | None, str | None]],
) -> MFRealismFactor:
    basis, source_id, evidence_ref, source_sha256 = _aggregate_sources(sources)
    return MFRealismFactor(
        dimension=dimension, value=value, basis=basis, assessed_at=assessed_at,
        rationale=rationale, source_id=source_id, evidence_ref=evidence_ref,
        source_sha256=source_sha256,
    )


def assess_account_mf_realism(
    con, *, account_id: str, assessed_at: datetime,
) -> MFPaperRealismAssessment:
    """Score the four MF execution-realism dimensions using only facts received by the cutoff.

    This is an operational completeness score, not a profitability or empirical
    execution-fidelity estimate. Absent dimensions remain absent and therefore
    contribute zero under ``MFPaperRealismAssessment``.
    """
    cutoff = _utc(assessed_at)
    account = con.execute(
        "SELECT 1 FROM mf_paper_accounts WHERE account_id=?", [account_id],
    ).fetchone()
    if account is None:
        raise ValueError("MF paper account does not exist")

    transitions = con.execute("""SELECT o.order_id, o.transaction_type, t.state,
        t.basis, t.source_id, t.source_ref, t.source_sha256, t.received_at, t.sequence
        FROM mf_paper_orders o JOIN mf_paper_order_transitions t USING (order_id)
        WHERE o.account_id=? AND o.created_at<=? AND t.received_at<=?
        ORDER BY o.order_id, t.sequence""", [account_id, cutoff, cutoff]).fetchall()
    latest: dict[str, tuple[Any, ...]] = {}
    for row in transitions:
        latest[str(row[0])] = row
    accepted = {
        order_id: row for order_id, row in latest.items() if row[2] == "ACCEPTED"
    }
    purchase_orders = {
        order_id: row for order_id, row in accepted.items()
        if row[1] in {"PURCHASE", "SWITCH_IN"}
    }
    factors: list[MFRealismFactor] = []
    if accepted:
        purchase_eligibility_rows = con.execute("""SELECT order_id, state,
            eligible_nav_date, eligibility_sha256, evidence_json FROM
            mf_paper_purchase_eligibility WHERE updated_at<=?""", [cutoff]).fetchall()
        purchase_eligibility = {str(row[0]): row for row in purchase_eligibility_rows}
        redemption_rows = con.execute("""SELECT order_id, eligible_nav_date,
            eligibility_sha256 FROM mf_paper_redemption_settlements
            WHERE account_id=? AND created_at<=? AND updated_at<=? AND order_id IS NOT NULL""",
            [account_id, cutoff, cutoff]).fetchall()
        redemption_eligibility = {str(row[0]): row for row in redemption_rows}
        resolved = 0
        eligible_sources = []
        for order_id, row in accepted.items():
            if order_id in purchase_orders:
                item = purchase_eligibility.get(order_id)
                complete = bool(item and item[2] is not None and item[3])
            else:
                item = redemption_eligibility.get(order_id)
                complete = bool(item and item[1] is not None and item[2])
            if complete:
                resolved += 1
                eligible_sources.append((str(row[3]), row[4], row[5], row[6]))
        factors.append(_factor(
            MFRealismDimension.ELIGIBLE_NAV_CERTAINTY,
            Decimal(resolved) / Decimal(len(accepted)), cutoff,
            f"{resolved}/{len(accepted)} accepted MF orders have a frozen eligible-NAV date and evidence hash.",
            eligible_sources if resolved == len(accepted) else [],
        ))

    if purchase_orders:
        ids = sorted(purchase_orders)
        placeholders = ",".join("?" for _ in ids)
        payment_rows = con.execute(f"""SELECT order_id, milestone_kind, basis,
            source_id, source_ref, source_sha256 FROM mf_paper_purchase_payment_events
            WHERE order_id IN ({placeholders}) AND received_at<=?""",
            [*ids, cutoff]).fetchall()
        payment_sources = [(str(row[2]), row[3], row[4], row[5]) for row in payment_rows]
        expected = _EXPECTED_PAYMENT_MILESTONES * len(purchase_orders)
        recorded = min(len(payment_rows), expected)
        factors.append(_factor(
            MFRealismDimension.PAYMENT_TIME_EVIDENCE,
            Decimal(recorded) / Decimal(expected), cutoff,
            f"{recorded}/{expected} expected initiated, credited, and available-for-use payment clocks were received by the cutoff.",
            payment_sources if recorded == expected else [],
        ))

    lots = con.execute("""SELECT l.scheme_code, l.plan, l.option, l.isin,
        l.allotted_on, l.exit_loads_json, l.exit_loads_sha256,
        t.source_id, t.source_ref, t.source_sha256
        FROM mf_paper_lots l LEFT JOIN LATERAL (
          SELECT source_id, source_ref, source_sha256
          FROM mf_scheme_commercial_terms t
          WHERE t.scheme_code=l.scheme_code AND t.plan=l.plan AND t.option=l.option
            AND t.isin=l.isin AND t.source_sha256=l.exit_loads_sha256
            AND t.effective_from<=l.allotted_on
            AND (t.effective_to IS NULL OR t.effective_to>=l.allotted_on)
            AND t.published_at<=? AND t.observed_at<=?
          ORDER BY t.published_at DESC, t.observed_at DESC, t.record_id DESC LIMIT 1
        ) t ON TRUE
        WHERE l.account_id=? AND l.created_at<=? ORDER BY l.lot_id""",
        [cutoff, cutoff, account_id, cutoff]).fetchall()
    if lots:
        known = [row for row in lots if row[5] is not None and row[6] is not None and row[7]]
        load_sources = [
            (MFRealismBasis.OBSERVED_EVIDENCE.value, row[7], row[8], row[9])
            for row in known
        ]
        factors.append(_factor(
            MFRealismDimension.LOAD_COMPLETENESS,
            Decimal(len(known)) / Decimal(len(lots)), cutoff,
            f"{len(known)}/{len(lots)} MF lots have a schedule hash matching effective, as-known commercial terms.",
            load_sources if len(known) == len(lots) else [],
        ))

    settlements = con.execute("""SELECT state, eligible_nav_date, eligibility_sha256,
        payout_basis, payout_evidence_ref, payout_source_id, payout_source_sha256
        FROM mf_paper_redemption_settlements WHERE account_id=?
          AND created_at<=? AND updated_at<=? ORDER BY created_at, settlement_id""",
        [account_id, cutoff, cutoff]).fetchall()
    if settlements:
        values = []
        settlement_sources = []
        all_observed_payouts = True
        for row in settlements:
            state, eligible_date, eligibility_hash, basis, evidence_ref, source_id, source_hash = row
            if state == "CASH_AVAILABLE" and basis == "OBSERVED_EVIDENCE":
                value = _SETTLEMENT_CONFIDENCE["CASH_AVAILABLE_OBSERVED"]
                settlement_sources.append((str(basis), source_id, evidence_ref, source_hash))
            elif state == "CASH_AVAILABLE":
                value = _SETTLEMENT_CONFIDENCE["CASH_AVAILABLE_SIMULATED"]
                all_observed_payouts = False
            else:
                value = _SETTLEMENT_CONFIDENCE[str(state)]
                all_observed_payouts = False
            if eligible_date is None or not eligibility_hash:
                value = min(value, Decimal("0.10"))
            values.append(value)
        average = sum(values, Decimal(0)) / Decimal(len(values))
        factors.append(_factor(
            MFRealismDimension.REDEMPTION_SETTLEMENT_CONFIDENCE,
            average, cutoff,
            f"Mean settlement-state confidence across {len(settlements)} as-of redemption records; payout proof is source-attested only when every record is observed and paid.",
            settlement_sources if all_observed_payouts else [],
        ))

    return MFPaperRealismAssessment(account_id, cutoff, tuple(factors))


__all__ = ["assess_account_mf_realism"]
