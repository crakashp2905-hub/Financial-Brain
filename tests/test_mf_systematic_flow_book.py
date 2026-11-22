from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from financial_brain.config import Config
from financial_brain.forward_edge.data_permissions import (
    DataPermission, DataUse, PermissionStatus,
)
from financial_brain.ingest.mf_scheme_terms import persist as persist_scheme_terms
from financial_brain.kernel.mf_systematic_flows import (
    MFSystematicFlowBasis, MFSystematicFlowEvent,
    MFSystematicFlowEventKind, MFSystematicFlowInstruction,
    MFSystematicFlowKind,
)
from financial_brain.kernel.fund_transactions import (
    SchemeTransactionCalendar, SchemeTransactionRule, TransactionKind,
)
from financial_brain.kernel.trading_clock import BusinessCalendar, CalendarDomain
from financial_brain.paper.mf_systematic_flow_book import (
    MFSystematicFlowBookError, apply_systematic_transfer_purchase,
    confirm_systematic_redemption_value, create_systematic_plan,
    record_systematic_event, release_systematic_payout, request_systematic_redemption,
    systematic_instruction_snapshot,
)
from financial_brain.providers.mf_scheme_terms import MFChargeRule, SchemeCommercialTerms
from financial_brain.paper.mf_redemption_settlement import settlement_snapshot
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


def _scheme_calendar():
    start, end = date(2026, 11, 20), date(2026, 12, 31)
    business = BusinessCalendar(
        calendar_id="111111", domain=CalendarDomain.SCHEME,
        coverage_start=start, source_as_of=end,
        open_days=frozenset(start + timedelta(days=offset)
                            for offset in range((end - start).days + 1)
                            if (start + timedelta(days=offset)).weekday() < 5),
        source_ref="test-scheme-calendar", source_sha256="c" * 64,
        recorded_at=NOW - timedelta(days=1),
    )
    rule = SchemeTransactionRule(
        scheme_id="111111", transaction_kind=TransactionKind.REDEMPTION,
        effective_from=start, effective_to=None, cutoff_local=time(14),
        before_cutoff_lag=0, after_cutoff_lag=1, closed_day_lag=1,
        published_at=NOW - timedelta(days=1), source_ref="test-redemption-cutoff",
        source_sha256="d" * 64,
    )
    return SchemeTransactionCalendar({"111111": business}, (rule,))


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


def test_systematic_redemption_reserves_actual_lots_and_recovers_after_partial_write(
        database, monkeypatch):
    instruction = _instruction(amount_inr=None, units=Decimal("150"))
    with database.connect() as con:
        _create(con, instruction)
        con.execute("""INSERT INTO mf_paper_lots (
            account_id, lot_id, scheme_code, plan, option, isin, allotted_on,
            lock_in_until, units_original, units_remaining, purchase_nav,
            exit_loads_json, exit_loads_sha256, subscription_event_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL, ?, ?)""",
            [instruction.account_id, "lot-source", instruction.source_scheme,
             instruction.source_plan, instruction.source_option, instruction.source_isin,
             date(2026, 11, 1), Decimal("150"), Decimal("150"), Decimal("100"),
             "subscribe-source", NOW])

        original = __import__(
            "financial_brain.paper.mf_systematic_flow_book", fromlist=["record_systematic_event"]
        ).record_systematic_event
        calls = 0

        def fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("simulated crash after settlement request")
            return original(*args, **kwargs)

        monkeypatch.setattr(
            "financial_brain.paper.mf_systematic_flow_book.record_systematic_event", fail_once,
        )
        with pytest.raises(RuntimeError, match="simulated crash"):
            request_systematic_redemption(
                con, instruction_id=instruction.instruction_id,
                requested_at=NOW, now=NOW, scheme_calendar=_scheme_calendar(),
            )
        assert settlement_snapshot(
            con, account_id=instruction.account_id,
            settlement_id=f"systematic:{instruction.instruction_id}",
        )["state"] == "REDEMPTION_PENDING"

        result = request_systematic_redemption(
            con, instruction_id=instruction.instruction_id,
            requested_at=NOW, now=NOW, scheme_calendar=_scheme_calendar(),
        )
        assert result["status"] == "recorded"
        assert result["settlement_id"] == f"systematic:{instruction.instruction_id}"
        snapshot = systematic_instruction_snapshot(
            con, instruction_id=instruction.instruction_id,
        )
        assert snapshot["stage"] == "REDEMPTION_REQUESTED"
        assert len(snapshot["events"]) == 1


def test_systematic_amount_withdrawal_is_not_silently_converted_to_units(database):
    instruction = _instruction(units=None, amount_inr=Decimal("10000"))
    with database.connect() as con:
        _create(con, instruction)
        with pytest.raises(MFSystematicFlowBookError, match="amount-to-units adapter"):
            request_systematic_redemption(
                con, instruction_id=instruction.instruction_id,
                requested_at=NOW, now=NOW, scheme_calendar=_scheme_calendar(),
            )


def test_systematic_payout_credits_canonical_cash_and_recovers_after_event_failure(
        database, monkeypatch):
    import financial_brain.paper.mf_systematic_flow_book as flow_book

    instruction = _instruction(MFSystematicFlowKind.SWP, target_scheme=None,
        target_plan=None, target_option=None, target_isin=None,
        amount_inr=None, units=Decimal("1"))
    payout_at = NOW + timedelta(minutes=2)
    with database.connect() as con:
        _create(con, instruction)
        con.execute("""INSERT INTO mf_paper_lots (
            account_id, lot_id, scheme_code, plan, option, isin, allotted_on,
            lock_in_until, units_original, units_remaining, purchase_nav,
            exit_loads_json, exit_loads_sha256, subscription_event_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL, ?, ?)""",
            [instruction.account_id, "lot-payout", instruction.source_scheme,
             instruction.source_plan, instruction.source_option, instruction.source_isin,
             date(2026, 11, 1), Decimal("1"), Decimal("1"), Decimal("100"),
             "subscribe-payout", NOW])
        request_systematic_redemption(
            con, instruction_id=instruction.instruction_id,
            requested_at=NOW, now=NOW, scheme_calendar=_scheme_calendar(),
        )
        value = _event(instruction, MFSystematicFlowEventKind.VALUE_CONFIRMED, 1,
            amount_inr=Decimal("90"), gross_inr=Decimal("100"),
            exit_load_inr=Decimal("10"), other_charges_inr=Decimal("0"))
        record_systematic_event(con, instruction_id=instruction.instruction_id,
                                event=value, now=value.effective_at)
        con.execute("""UPDATE mf_paper_redemption_settlements SET
            state='PAYOUT_PENDING', gross_inr=?, exit_load_inr=?, net_inr=?,
            payout_due_date=? WHERE account_id=? AND settlement_id=?""",
            [Decimal("100"), Decimal("10"), Decimal("90"), date(2026, 11, 23),
             instruction.account_id, f"systematic:{instruction.instruction_id}"])

        original = flow_book.record_systematic_event
        calls = 0

        def fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("simulated crash after cash release")
            return original(*args, **kwargs)

        monkeypatch.setattr(flow_book, "record_systematic_event", fail_once)
        with pytest.raises(RuntimeError, match="after cash release"):
            release_systematic_payout(
                con, instruction_id=instruction.instruction_id,
                available_at=payout_at, now=payout_at,
            )
        cash = con.execute("SELECT cash_inr FROM mf_paper_accounts WHERE account_id=?",
                            [instruction.account_id]).fetchone()[0]
        assert Decimal(cash) == Decimal("90")

        replay = release_systematic_payout(
            con, instruction_id=instruction.instruction_id,
            available_at=payout_at, now=payout_at,
        )
        assert replay["state"] == "CASH_AVAILABLE"
        snapshot = systematic_instruction_snapshot(
            con, instruction_id=instruction.instruction_id,
        )
        assert snapshot["stage"] == "PAYOUT_AVAILABLE"
        assert snapshot["available_to_redeploy_inr"] == Decimal("90")
        cash = con.execute("SELECT cash_inr FROM mf_paper_accounts WHERE account_id=?",
                            [instruction.account_id]).fetchone()[0]
        assert Decimal(cash) == Decimal("90")


def test_stp_subscription_uses_cash_and_lots_once_after_payout(database, monkeypatch):
    import financial_brain.paper.mf_systematic_flow_book as flow_book

    instruction = _instruction(amount_inr=None, units=Decimal("1"))
    payout_at = NOW + timedelta(minutes=2)
    decision_at = NOW + timedelta(minutes=3)
    allotment_at = NOW + timedelta(minutes=4)
    terms_hash = "a" * 64
    nav_id = "stp-target-nav-observation"
    nav_hash = "c" * 64
    permissions = tuple(DataPermission(
        source_id=source_id, use=use, status=PermissionStatus.USE_ALLOWED,
        effective_from=date(2026, 11, 1), effective_through=None,
        evidence_ref="reviewed-terms", reviewer="owner",
    ) for source_id in ("AMC", "AMFI")
        for use in (DataUse.RESEARCH, DataUse.RETENTION, DataUse.PAPER_SIMULATION))
    terms = SchemeCommercialTerms(
        scheme_code=instruction.target_scheme or "", plan=instruction.target_plan or "",
        option=instruction.target_option or "", isin=instruction.target_isin or "",
        effective_from=date(2026, 11, 1), effective_to=None,
        ter_percent=None, aum_inr=None, exit_loads=(), charge_schedule=(),
        minimum_initial_inr=Decimal("0"),
        minimum_additional_inr=Decimal("0"), minimum_redemption_inr=Decimal("0"),
        exit_restrictions=(), source_id="AMC",
        source_ref="terms-stp", source_sha256=terms_hash,
        published_at=datetime(2026, 11, 1, 8, tzinfo=timezone.utc),
        observed_at=datetime(2026, 11, 1, 8, tzinfo=timezone.utc),
    )

    with database.connect() as con:
        _create(con, instruction)
        con.execute("""INSERT INTO mf_paper_lots (
            account_id, lot_id, scheme_code, plan, option, isin, allotted_on,
            lock_in_until, units_original, units_remaining, purchase_nav,
            exit_loads_json, exit_loads_sha256, subscription_event_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL, ?, ?)""",
            [instruction.account_id, "lot-stp-source", instruction.source_scheme,
             instruction.source_plan, instruction.source_option, instruction.source_isin,
             date(2026, 11, 1), Decimal("1"), Decimal("1"), Decimal("100"),
             "subscribe-stp-source", NOW])
        request_systematic_redemption(
            con, instruction_id=instruction.instruction_id,
            requested_at=NOW, now=NOW, scheme_calendar=_scheme_calendar(),
        )
        value = _event(instruction, MFSystematicFlowEventKind.VALUE_CONFIRMED, 1,
            amount_inr=Decimal("90"), gross_inr=Decimal("100"),
            exit_load_inr=Decimal("10"), other_charges_inr=Decimal("0"))
        record_systematic_event(con, instruction_id=instruction.instruction_id,
                                event=value, now=value.effective_at)
        con.execute("""UPDATE mf_paper_redemption_settlements SET
            state='PAYOUT_PENDING', gross_inr=?, exit_load_inr=?, net_inr=?,
            payout_due_date=? WHERE account_id=? AND settlement_id=?""",
            [Decimal("100"), Decimal("10"), Decimal("90"), date(2026, 11, 23),
             instruction.account_id, f"systematic:{instruction.instruction_id}"])
        release_systematic_payout(
            con, instruction_id=instruction.instruction_id,
            available_at=payout_at, now=payout_at,
        )
        persist_scheme_terms(con, terms, permissions=permissions)
        nav_available_at = decision_at - timedelta(seconds=1)
        con.execute("""INSERT INTO mf_nav_observations (
            observation_id, scheme_code, isin_growth, isin_reinvest, scheme_name,
            fund_house, scheme_type, plan, option, nav, nav_date, lake_key,
            row_number, row_sha256, source_published_at, observed_at, ingested_at)
            VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL, ?, ?)""",
            [nav_id, instruction.target_scheme, instruction.target_isin,
             "Example Target Scheme", "AMC", "EQUITY", instruction.target_plan,
             instruction.target_option, 10.0, date(2026, 11, 23), "lake-stp-nav",
             nav_hash, nav_available_at, nav_available_at])

        kwargs = dict(
            instruction_id=instruction.instruction_id, investment_inr=Decimal("80"),
            nav_per_unit=Decimal("10"), nav_observation_id=nav_id,
            terms=terms, permissions=permissions, decision_at=decision_at,
            allotted_on=date(2026, 11, 23), now=allotment_at,
        )
        with pytest.raises(MFSystematicFlowBookError, match="as-known target-scheme"):
            apply_systematic_transfer_purchase(
                con, **(kwargs | {"nav_observation_id": "wrong-nav-id"}),
            )

        original = flow_book.record_systematic_event
        calls = 0

        def fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("simulated crash after target subscription")
            return original(*args, **kwargs)

        monkeypatch.setattr(flow_book, "record_systematic_event", fail_once)
        with pytest.raises(RuntimeError, match="after target subscription"):
            apply_systematic_transfer_purchase(con, **kwargs)
        cash = con.execute("SELECT cash_inr FROM mf_paper_accounts WHERE account_id=?",
                            [instruction.account_id]).fetchone()[0]
        assert Decimal(cash) == Decimal("10")
        lots = con.execute("""SELECT units_remaining FROM mf_paper_lots
            WHERE account_id=? AND lot_id=?""",
            [instruction.account_id, f"{instruction.instruction_id}:stp-lot"]).fetchall()
        assert len(lots) == 1 and Decimal(lots[0][0]) == Decimal("8")

        replay = apply_systematic_transfer_purchase(con, **kwargs)
        assert replay["status"] == "recorded"
        second_replay = apply_systematic_transfer_purchase(con, **kwargs)
        assert second_replay["status"] == "already_recorded"
        snapshot = systematic_instruction_snapshot(
            con, instruction_id=instruction.instruction_id,
        )
        assert snapshot["stage"] == "ALLOTMENT_CONFIRMED"
        assert snapshot["available_to_redeploy_inr"] == Decimal("10")
        cash = con.execute("SELECT cash_inr FROM mf_paper_accounts WHERE account_id=?",
                            [instruction.account_id]).fetchone()[0]
        assert Decimal(cash) == Decimal("10")
        lots = con.execute("""SELECT units_remaining FROM mf_paper_lots
            WHERE account_id=? AND lot_id=?""",
            [instruction.account_id, f"{instruction.instruction_id}:stp-lot"]).fetchall()
        assert len(lots) == 1 and Decimal(lots[0][0]) == Decimal("8")


def test_systematic_value_confirmation_uses_fifo_nav_and_recovers_once(database, monkeypatch):
    import financial_brain.paper.mf_systematic_flow_book as flow_book

    instruction = _instruction(MFSystematicFlowKind.SWP, target_scheme=None,
        target_plan=None, target_option=None, target_isin=None,
        amount_inr=None, units=Decimal("1"), source_scheme="111111",
        source_isin="INF111111111")
    nav_date = date(2026, 11, 23)
    decision_at = NOW + timedelta(minutes=3)
    nav_available_at = NOW + timedelta(minutes=2)
    nav_id = "systematic-nav-observation"
    terms_hash = "a" * 64
    nav_hash = "b" * 64
    permissions = tuple(DataPermission(
        source_id=source_id, use=use, status=PermissionStatus.USE_ALLOWED,
        effective_from=date(2026, 11, 1), effective_through=None,
        evidence_ref="reviewed-paper-use", reviewer="owner",
    ) for source_id in ("AMC", "AMFI")
        for use in (DataUse.RESEARCH, DataUse.RETENTION, DataUse.PAPER_SIMULATION))
    terms = SchemeCommercialTerms(
        scheme_code=instruction.source_scheme, plan=instruction.source_plan,
        option=instruction.source_option, isin=instruction.source_isin,
        effective_from=date(2026, 11, 1), effective_to=None,
        ter_percent=None, aum_inr=None, exit_loads=(),
        charge_schedule=(MFChargeRule("redemption-test-fee", "REDEEM", "0.01"),),
        minimum_initial_inr=Decimal("0"),
        minimum_additional_inr=Decimal("0"), minimum_redemption_inr=Decimal("0"),
        exit_restrictions=(), source_id="AMC",
        source_ref="source-terms", source_sha256=terms_hash,
        published_at=datetime(2026, 11, 1, 8, tzinfo=timezone.utc),
        observed_at=datetime(2026, 11, 1, 8, tzinfo=timezone.utc),
    )

    with database.connect() as con:
        _create(con, instruction)
        persist_scheme_terms(con, terms, permissions=permissions)
        con.execute("""INSERT INTO mf_paper_lots (
            account_id, lot_id, scheme_code, plan, option, isin, allotted_on,
            lock_in_until, units_original, units_remaining, purchase_nav,
            exit_loads_json, exit_loads_sha256, subscription_event_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)""",
            [instruction.account_id, "lot-systematic-value", instruction.source_scheme,
             instruction.source_plan, instruction.source_option, instruction.source_isin,
             date(2026, 11, 1), Decimal("1"), Decimal("1"), Decimal("100"),
             "[]", terms_hash, "subscribe-systematic-value", NOW])
        con.execute("""INSERT INTO mf_nav_observations (
            observation_id, scheme_code, isin_growth, isin_reinvest, scheme_name,
            fund_house, scheme_type, plan, option, nav, nav_date, lake_key,
            row_number, row_sha256, source_published_at, observed_at, ingested_at)
            VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL, ?, ?)""",
            [nav_id, instruction.source_scheme, instruction.source_isin,
             "Example Source Scheme", "AMC", "EQUITY", instruction.source_plan,
             instruction.source_option, 100.0, nav_date, "lake-systematic-nav",
             nav_hash, nav_available_at, nav_available_at])
        request_systematic_redemption(
            con, instruction_id=instruction.instruction_id,
            requested_at=NOW, now=NOW, scheme_calendar=_scheme_calendar(),
        )

        original = flow_book.record_systematic_event
        calls = 0

        def fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("simulated crash after FIFO redemption")
            return original(*args, **kwargs)

        monkeypatch.setattr(flow_book, "record_systematic_event", fail_once)
        kwargs = dict(
            instruction_id=instruction.instruction_id,
            nav_observation_id=nav_id, redemption_date=nav_date,
            payout_due_date=nav_date, decision_at=decision_at,
            terms=terms, permissions=permissions, now=decision_at,
        )
        with pytest.raises(RuntimeError, match="after FIFO redemption"):
            confirm_systematic_redemption_value(con, **kwargs)
        row = con.execute("""SELECT state, net_inr FROM mf_paper_redemption_settlements
            WHERE account_id=? AND settlement_id=?""",
            [instruction.account_id, f"systematic:{instruction.instruction_id}"]).fetchone()
        assert row[0] == "PAYOUT_PENDING" and Decimal(row[1]) == Decimal("99.99")
        remaining = con.execute("""SELECT units_remaining FROM mf_paper_lots
            WHERE account_id=? AND lot_id=?""",
            [instruction.account_id, "lot-systematic-value"]).fetchone()[0]
        assert Decimal(remaining) == Decimal("0")

        result = confirm_systematic_redemption_value(con, **kwargs)
        assert result["status"] == "recorded"
        assert Decimal(result["other_charges_inr"]) == Decimal("0.01")
        replay = confirm_systematic_redemption_value(con, **kwargs)
        assert replay["status"] == "already_recorded"
        snapshot = systematic_instruction_snapshot(
            con, instruction_id=instruction.instruction_id,
        )
        assert snapshot["stage"] == "VALUE_CONFIRMED"
        assert len(snapshot["events"]) == 2
        assert snapshot["events"][1][12] == nav_id
        assert Decimal(snapshot["events"][1][7]) == Decimal("0.01")
