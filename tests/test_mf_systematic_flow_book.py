from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.config import Config
from financial_brain.forward_edge.data_permissions import (
    DataPermission, DataUse, PermissionStatus,
)
from financial_brain.kernel.mf_systematic_flows import (
    MFSystematicFlowBasis, MFSystematicFlowEvent,
    MFSystematicFlowEventKind, MFSystematicFlowInstruction,
    MFSystematicFlowKind,
)
from financial_brain.paper.mf_systematic_flow_book import (
    MFSystematicFlowBookError, create_systematic_plan, record_systematic_event,
    systematic_instruction_snapshot,
)
from financial_brain.storage.db import Database


NOW = datetime(2026, 11, 23, 5, tzinfo=timezone.utc)


@pytest.fixture
def database(tmp_path):
    db = Database(Config(data_root=tmp_path).ensure())
    db.migrate()
    with db.connect() as con:
        con.execute("INSERT INTO mf_paper_accounts VALUES (?, ?, ?)",
                    ["account-1", Decimal(0), NOW])
    return db


def _instruction(kind=MFSystematicFlowKind.STP, **changes):
    values = {
        "instruction_id": f"{kind.value}-nov", "plan_id": "systematic-plan-1",
        "account_id": "account-1", "folio_id": "SYNTHETIC-FOLIO",
        "kind": kind, "scheduled_for": date(2026, 11, 23),
        "source_scheme": "111111", "source_plan": "DIRECT",
        "source_option": "GROWTH", "source_isin": "INF111111111",
        "amount_inr": Decimal("10000"),
    }
    if kind is MFSystematicFlowKind.STP:
        values.update(target_scheme="222222", target_plan="DIRECT",
                      target_option="GROWTH", target_isin="INF222222222")
    return MFSystematicFlowInstruction(**(values | changes))


def _event(instruction, kind, minutes, **changes):
    at = NOW + timedelta(minutes=minutes)
    values = {
        "event_id": f"{instruction.instruction_id}-{kind.value.lower()}",
        "kind": kind, "effective_at": at,
        "basis": MFSystematicFlowBasis.SIMULATED_ASSUMPTION,
    }
    return MFSystematicFlowEvent(**(values | changes))


def _permission():
    return DataPermission(
        source_id="PLATFORM", use=DataUse.PAPER_SIMULATION,
        status=PermissionStatus.USE_ALLOWED, effective_from=date(2026, 11, 23),
        effective_through=None, evidence_ref="paper-sim-approval", reviewer="owner",
    )


def _create(con, instruction):
    return create_systematic_plan(con, plan_id=instruction.plan_id,
        instructions=(instruction,), now=NOW)


def test_stp_events_are_durable_idempotent_and_never_spend_before_payout(database):
    instruction = _instruction()
    with database.connect() as con:
        _create(con, instruction)
        request = _event(instruction, MFSystematicFlowEventKind.REDEMPTION_REQUESTED, 1)
        record_systematic_event(con, instruction_id=instruction.instruction_id,
                                event=request, now=request.effective_at)
        premature = _event(instruction, MFSystematicFlowEventKind.PURCHASE_FUNDS_APPLIED, 2,
                           amount_inr=Decimal("100"))
        with pytest.raises(MFSystematicFlowBookError, match="ordering"):
            record_systematic_event(con, instruction_id=instruction.instruction_id,
                                    event=premature, now=premature.effective_at)
        value = _event(instruction, MFSystematicFlowEventKind.VALUE_CONFIRMED, 2,
                       amount_inr=Decimal("9500"), gross_inr=Decimal("10000"),
                       exit_load_inr=Decimal("500"), other_charges_inr=Decimal(0))
        record_systematic_event(con, instruction_id=instruction.instruction_id,
                                event=value, now=value.effective_at)
        payout = _event(instruction, MFSystematicFlowEventKind.PAYOUT_AVAILABLE, 3,
                        amount_inr=Decimal("9500"))
        record_systematic_event(con, instruction_id=instruction.instruction_id,
                                event=payout, now=payout.effective_at)
        assert record_systematic_event(con, instruction_id=instruction.instruction_id,
             event=payout, now=payout.effective_at)["status"] == "already_recorded"
        funding = _event(instruction, MFSystematicFlowEventKind.PURCHASE_FUNDS_APPLIED, 4,
                         amount_inr=Decimal("9000"))
        record_systematic_event(con, instruction_id=instruction.instruction_id,
                                event=funding, now=funding.effective_at)
        allotment = _event(instruction, MFSystematicFlowEventKind.ALLOTMENT_CONFIRMED, 5,
                           amount_inr=Decimal("9000"), nav_per_unit=Decimal("90"),
                           units=Decimal("100"))
        record_systematic_event(con, instruction_id=instruction.instruction_id,
                                event=allotment, now=allotment.effective_at)
    with database.connect() as con:
        snapshot = systematic_instruction_snapshot(con, instruction_id=instruction.instruction_id)
        assert snapshot["stage"] == "ALLOTMENT_CONFIRMED"
        assert snapshot["available_to_redeploy_inr"] == Decimal("500")
        assert len(snapshot["events"]) == 5


def test_observed_payout_requires_paper_permission_and_replays(database):
    instruction = _instruction(MFSystematicFlowKind.SWP, target_scheme=None,
        target_plan=None, target_option=None, target_isin=None)
    with database.connect() as con:
        _create(con, instruction)
        for event in (
            _event(instruction, MFSystematicFlowEventKind.REDEMPTION_REQUESTED, 1),
            _event(instruction, MFSystematicFlowEventKind.VALUE_CONFIRMED, 2,
                   amount_inr=Decimal("9500"), gross_inr=Decimal("10000"),
                   exit_load_inr=Decimal("500"), other_charges_inr=Decimal(0)),
        ):
            record_systematic_event(con, instruction_id=instruction.instruction_id,
                                    event=event, now=event.effective_at)
        at = NOW + timedelta(minutes=3)
        observed = MFSystematicFlowEvent(
            event_id="swp-payout", kind=MFSystematicFlowEventKind.PAYOUT_AVAILABLE,
            effective_at=at, basis=MFSystematicFlowBasis.OBSERVED_EVIDENCE,
            amount_inr=Decimal("9500"), source_id="PLATFORM", source_ref="receipt",
            source_sha256="f" * 64,
        )
        with pytest.raises(MFSystematicFlowBookError, match="lacks PAPER_SIMULATION"):
            record_systematic_event(con, instruction_id=instruction.instruction_id,
                                    event=observed, now=at)
        result = record_systematic_event(con, instruction_id=instruction.instruction_id,
            event=observed, permissions=(_permission(),), now=at)
        assert result["stage"] == "PAYOUT_AVAILABLE"


def test_missed_instruction_is_terminal_and_plan_schedule_is_frozen(database):
    instruction = _instruction(MFSystematicFlowKind.SWP, target_scheme=None,
        target_plan=None, target_option=None, target_isin=None)
    with database.connect() as con:
        _create(con, instruction)
        missed = _event(instruction, MFSystematicFlowEventKind.MISSED, 1,
                        reason="scheme suspended")
        record_systematic_event(con, instruction_id=instruction.instruction_id,
                                event=missed, now=missed.effective_at)
        retry = _event(instruction, MFSystematicFlowEventKind.REDEMPTION_REQUESTED, 2)
        with pytest.raises(MFSystematicFlowBookError, match="ordering"):
            record_systematic_event(con, instruction_id=instruction.instruction_id,
                                    event=retry, now=retry.effective_at)
        changed = _instruction(MFSystematicFlowKind.SWP, target_scheme=None,
            target_plan=None, target_option=None, target_isin=None,
            amount_inr=Decimal("11000"))
        with pytest.raises(MFSystematicFlowBookError, match="different schedule"):
            _create(con, changed)


def test_systematic_plan_requires_existing_account_and_event_time_not_backdated(database):
    absent = _instruction(MFSystematicFlowKind.SWP, target_scheme=None,
        target_plan=None, target_option=None, target_isin=None, account_id="missing")
    with database.connect() as con:
        with pytest.raises(MFSystematicFlowBookError, match="paper account does not exist"):
            _create(con, absent)
        instruction = _instruction()
        _create(con, instruction)
        day_before = NOW - timedelta(days=1)
        backdated = MFSystematicFlowEvent(
            event_id="backdated-request", kind=MFSystematicFlowEventKind.REDEMPTION_REQUESTED,
            effective_at=day_before, basis=MFSystematicFlowBasis.SIMULATED_ASSUMPTION,
        )
        with pytest.raises(MFSystematicFlowBookError, match="precedes its scheduled date"):
            record_systematic_event(con, instruction_id=instruction.instruction_id,
                                    event=backdated, now=NOW)
