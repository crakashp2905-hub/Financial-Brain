"""F14 - durability and the remaining offline items (FE-0003, 0009, 0011-0013, 0020, 0021, 0028, 0049, 0063, 0072)."""
from __future__ import annotations

import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from financial_brain.forward_edge import books, disposable, durable as U, eligibility, futures
from financial_brain.kernel.oms import InsufficientCash, OmsError

REPO = Path(__file__).resolve().parents[1]
KEY = "entry:INFY:2027-01-04"


# ---- FE-0012 monotonic, durable, chained journal ----

def test_sequence_is_monotonic_gap_free_and_survives_reopen(tmp_path: Path) -> None:
    j = U.DurableJournal(tmp_path / "j.log")
    assert [j.append({"t": "RECEIPT"}), j.append({"t": "SIGNAL", "side": "BUY"})] == [1, 2]
    again = U.DurableJournal(tmp_path / "j.log")
    assert again.seq == 2 and again.head == j.head and again.append({"t": "RISK", "decision": "ALLOW"}) == 3


def test_a_torn_final_write_is_discarded_and_earlier_records_survive(tmp_path: Path) -> None:
    j = U.DurableJournal(tmp_path / "j.log")
    j.append({"t": "RECEIPT"})
    j.append_torn({"t": "SIGNAL", "side": "BUY"})
    again = U.DurableJournal(tmp_path / "j.log")
    assert again.torn_tail_discarded and again.seq == 1 and again.records() == [{"t": "RECEIPT"}]
    assert again.append({"t": "SIGNAL", "side": "BUY"}) == 2          # and the log keeps working


def test_editing_or_dropping_a_middle_record_is_detected(tmp_path: Path) -> None:
    p = tmp_path / "j.log"
    j = U.DurableJournal(p)
    for rec in ({"t": "RECEIPT"}, {"t": "SIGNAL", "side": "BUY"}, {"t": "RISK", "decision": "ALLOW"}):
        j.append(rec)
    lines = p.read_text(encoding="utf-8").splitlines()
    p.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")       # drop the middle record
    with pytest.raises(U.JournalCorrupt):
        U.DurableJournal(p)
    p.write_text("\n".join([lines[0].replace("RECEIPT", "RECEIPX"), lines[1], lines[2]]) + "\n", encoding="utf-8")
    with pytest.raises(U.JournalCorrupt):
        U.DurableJournal(p)


# ---- FE-0011 / FE-0013 outbox through recovery ----

def test_outbox_is_written_before_the_action_and_an_unacked_key_is_never_blindly_resent(tmp_path: Path) -> None:
    j = U.DurableJournal(tmp_path / "j.log")
    b = U.SimBroker()
    with pytest.raises(U.Crash):
        U.run_session(j, b, KEY, crash_at="send", mode="effect_only")      # venue got it, the journal never heard back
    assert b.received == [KEY]
    j2 = U.DurableJournal(tmp_path / "j.log")
    assert U.fold(j2)["outbox"][KEY] == "PENDING"                          # intent was durable before the action
    U.recover(j2, b, KEY)
    assert b.received == [KEY]                                             # reconciled against the venue: not sent twice


# ---- FE-0020 power loss at every stage ----

def _reference(tmp_path: Path) -> dict:
    j = U.DurableJournal(tmp_path / "ref.log")
    return U.run_session(j, U.SimBroker(), KEY)


CASES = [(s, m) for s in U.STAGES for m in U.MODES] + [("send", "effect_only")]


@pytest.mark.parametrize("stage, mode", CASES)
def test_after_restart_no_economic_event_is_lost_duplicated_or_invented(tmp_path: Path, stage: str, mode: str) -> None:
    ref = _reference(tmp_path)
    path = tmp_path / "live.log"
    broker = U.SimBroker()
    with pytest.raises(U.Crash):
        U.run_session(U.DurableJournal(path), broker, KEY, crash_at=stage, mode=mode)
    restarted = U.DurableJournal(path)                                     # a new process opens the same file
    final = U.recover(restarted, broker, KEY)
    assert U.state_hash(final) == U.state_hash(ref)                        # same economics as an uninterrupted run
    assert broker.received == [KEY]                                        # exactly one send
    assert len(final["fills"]) == 1 and next(iter(final["fills"].values()))["qty"] == 10   # one fill, not zero, not two
    assert U.DurableJournal(path).records() == restarted.records()         # and the log is internally consistent


def test_recovery_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "live.log"
    broker = U.SimBroker()
    with pytest.raises(U.Crash):
        U.run_session(U.DurableJournal(path), broker, KEY, crash_at="fill", mode="before")
    a = U.recover(U.DurableJournal(path), broker, KEY)
    b = U.recover(U.DurableJournal(path), broker, KEY)
    assert U.state_hash(a) == U.state_hash(b) and broker.received == [KEY]


# ---- FE-0021 rollover, compaction, backup, restore ----

def test_rollover_keeps_the_state_hash_and_restore_reproduces_it(tmp_path: Path) -> None:
    j = U.DurableJournal(tmp_path / "live.log")
    U.run_session(j, U.SimBroker(), KEY)
    before = U.state_hash(U.fold(j))
    after = U.rollover(j, tmp_path / "archive" / "seg1.log")
    assert after == before and (tmp_path / "archive" / "seg1.log").exists()
    assert len(j.records()) == 1 and j.records()[0]["t"] == "SNAPSHOT"
    digest = U.backup(j, tmp_path / "bk" / "live.bak")
    restored = U.restore(tmp_path / "bk" / "live.bak", tmp_path / "restored" / "live.log")
    assert U.state_hash(U.fold(restored)) == before
    assert digest == __import__("hashlib").sha256((tmp_path / "restored" / "live.log").read_bytes()).hexdigest()
    assert restored.append({"t": "RISK", "decision": "ALLOW"}) == 2         # restored log continues the chain


def test_a_crash_between_compaction_steps_never_loses_the_old_segment(tmp_path: Path) -> None:
    j = U.DurableJournal(tmp_path / "live.log")
    U.run_session(j, U.SimBroker(), KEY)
    (tmp_path / "live.compact.tmp").write_text("half written", encoding="utf-8")      # leftover from a crash
    assert U.state_hash(U.fold(U.DurableJournal(tmp_path / "live.log"))) == U.state_hash(U.fold(j))


# ---- FE-0049 / FE-0072 persistence and replay hash ----

def test_decision_records_persist_and_replay_to_the_same_head_hash(tmp_path: Path) -> None:
    j = U.DurableJournal(tmp_path / "d.log")
    for i in range(5):
        j.append({"t": "SIGNAL", "side": "BUY" if i % 2 else "SELL"})
    replay = U.DurableJournal(tmp_path / "copy.log")
    for r in j.records():
        replay.append(r)
    assert replay.head == j.head == U.DurableJournal(tmp_path / "d.log").head


# ---- FE-0009 disposable ledgers ----

def test_scratch_database_is_temporary_and_needs_no_credentials() -> None:
    with disposable.disposable_duckdb(REPO) as (con, path):
        con.execute("CREATE TABLE t(x INTEGER)")
        con.execute("INSERT INTO t VALUES (1)")
        assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
        assert path.exists()
    assert not path.exists()


@pytest.mark.parametrize("rel", ["data/financial_brain.duckdb", "data/fo_history.duckdb", "data/anything.duckdb",
                                 "docs/trials/ledger.duckdb", "brain/x.duckdb", "elsewhere/financial_brain.duckdb"])
def test_real_data_paths_are_refused(rel: str) -> None:
    with pytest.raises(disposable.ProtectedPathError):
        disposable.open_for_write(REPO / rel, REPO)


# ---- FE-0003 frozen eligibility list ----

def test_every_known_strategy_has_one_status_lineage_and_reason() -> None:
    data = json.loads((REPO / eligibility.OUT_REL).read_text(encoding="utf-8"))
    ids = [e["id"] for e in data["entries"]]
    assert len(ids) == len(set(ids))
    assert sum(e["id"].startswith("TRIAL:") for e in data["entries"]) == 13
    for e in data["entries"]:
        assert e["status"] in ("FAILED", "SHADOW_ONLY") and e["lineage"] and e["exclusion_reason"]
        assert e["prospective_observation"] is False


def test_committed_eligibility_list_matches_the_generator_so_a_new_strategy_needs_a_decision() -> None:
    assert (REPO / eligibility.OUT_REL).read_text(encoding="utf-8") == eligibility.render(REPO)


def test_all_thirteen_recorded_trials_are_failed_in_the_list() -> None:
    data = json.loads((REPO / eligibility.OUT_REL).read_text(encoding="utf-8"))
    assert {e["status"] for e in data["entries"] if e["id"].startswith("TRIAL:")} == {"FAILED"}


# ---- FE-0028 separate books ----

def test_books_never_share_cash_or_reservations() -> None:
    bs = books.BookSet({"M1": D("100000"), "ORB": D("50000")})
    bs.submit("ORB", client_id="o1", side="BUY", quantity=10, limit=None, reserve=D("40000"))
    with pytest.raises(InsufficientCash, match="needs 40000, available 10000"):
        bs.submit("ORB", client_id="o2", side="BUY", quantity=10, limit=None, reserve=D("40000"))   # ORB is out of cash...
    assert bs.oms("M1").available("M1") == D("100000")                                         # ...while M1 is untouched
    with pytest.raises(OmsError):
        bs.submit("M1", client_id="x", side="BUY", quantity=1, limit=None, reserve=D("1"), account="ORB")
    with pytest.raises(books.BookError, match="implicitly"):
        bs.transfer("M1", "ORB", D("1"))


def test_comparison_shows_each_book_and_a_total() -> None:
    bs = books.BookSet({"A": D("100"), "B": D("300")})
    cmp = bs.comparison()
    assert cmp["A"]["cash"] == D("100") and cmp["TOTAL"]["cash"] == D("400")
    with pytest.raises(books.BookError):
        books.BookSet({"A": D("0")})


# ---- FE-0063 futures roll ----

def test_contango_roll_costs_a_long_and_backwardation_pays_it() -> None:
    c = futures.roll(lots=2, lot_size_old=250, lot_size_new=250, near_price=D("100"), far_price=D("101"), fee_per_leg=D("10"))
    assert c["roll_cost"] == D("520") and c["credit"] is False and c["new_lots"] == 2 and c["residual_units"] == 0
    b = futures.roll(lots=2, lot_size_old=250, lot_size_new=250, near_price=D("100"), far_price=D("99"), fee_per_leg=D("10"))
    assert b["roll_cost"] == D("-480") and b["credit"] is True


def test_a_lot_size_change_leaves_a_reported_residual() -> None:
    r = futures.roll(lots=3, lot_size_old=250, lot_size_new=200, near_price=D("100"), far_price=D("100"), fee_per_leg=D("0"))
    assert r["new_lots"] == 3 and r["residual_units"] == 150
    with pytest.raises(futures.FuturesError):
        futures.roll(lots=0, lot_size_old=1, lot_size_new=1, near_price=D("1"), far_price=D("1"), fee_per_leg=D("0"))


def test_variation_margin_sums_to_the_total_price_move_and_financing_scales_with_days() -> None:
    settles = [D("100"), D("102"), D("101"), D("105")]
    vm = futures.variation_margin(settles, 500)
    assert sum(vm) == (settles[-1] - settles[0]) * 500 and vm == [D("1000"), D("-500"), D("2000")]
    assert futures.variation_margin(settles, 500, "SHORT") == [-x for x in vm]
    assert futures.financing_cost(D("100000"), D("0.10"), 30) == D("100000") * D("0.10") * 30 / 365
