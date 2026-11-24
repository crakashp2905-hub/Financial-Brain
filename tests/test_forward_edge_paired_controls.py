from datetime import date, timedelta

import pytest

from financial_brain.forward_edge.paired_controls import (
    EvaluationObservation,
    compare_matched_controls,
)


def _obs(identity, *, session, instrument, net, notional=10_000, risk=0.02,
         side="BUY", sector="IT", liquidity="L1"):
    return EvaluationObservation(identity, session, instrument, side, sector, liquidity,
                                 notional, risk, net)


def test_same_session_matched_pairs_measure_incremental_after_cost_return() -> None:
    day = date(2026, 11, 16)
    candidates = (_obs("c1", session=day, instrument="A", net=0.012),
                  _obs("c2", session=day, instrument="B", net=-0.004, notional=20_000, risk=0.04))
    controls = (_obs("x1", session=day, instrument="C", net=0.003, notional=10_500, risk=0.021),
                _obs("x2", session=day, instrument="D", net=0.001, notional=19_000, risk=0.039))
    report = compare_matched_controls(candidates, controls)
    assert len(report.pairs) == 2
    assert report.mean_incremental_net_return == pytest.approx(0.002)
    assert not report.unmatched_candidate_ids
    assert report.report_hash


def test_controls_are_not_reused_and_unmatched_candidates_are_retained() -> None:
    day = date(2026, 11, 16)
    candidates = (_obs("c1", session=day, instrument="A", net=0.01),
                  _obs("c2", session=day, instrument="B", net=0.01))
    controls = (_obs("x1", session=day, instrument="C", net=0.0),)
    report = compare_matched_controls(candidates, controls)
    assert len(report.pairs) == 1
    assert len(report.unmatched_candidate_ids) == 1
    assert report.unused_control_ids == ()


def test_session_side_sector_and_liquidity_mismatch_are_not_matched() -> None:
    day = date(2026, 11, 16)
    candidate = _obs("c", session=day, instrument="A", net=0.02)
    controls = (_obs("wrong-session", session=day + timedelta(days=1), instrument="B", net=0),
                _obs("wrong-sector", session=day, instrument="C", net=0, sector="BANK"),
                _obs("wrong-side", session=day, instrument="D", net=0, side="SELL"),
                _obs("wrong-liquidity", session=day, instrument="E", net=0, liquidity="L2"))
    report = compare_matched_controls((candidate,), controls)
    assert report.pairs == ()
    assert report.unmatched_candidate_ids == ("c",)
    assert len(report.unused_control_ids) == 4
