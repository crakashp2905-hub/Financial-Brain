"""Families, hypotheses, adapters, synthetic generators, verdicts, release gate and cards (ALG-081, ALG-095..098,
ALG-100, ALG-106..108, ALG-118, ALG-122, ALG-125, ALG-127)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from financial_brain.patterns import demo
from financial_brain.strategies.families import (
    FAMILY_HYPOTHESES,
    DonchianAdapter,
    card,
    cohort_report,
    effective_trials,
    family_complete,
    paired_incremental,
    release_gate,
    synthetic_breakout,
    synthetic_noisy_range,
    synthetic_reversal,
    verdict,
)
from financial_brain.strategies.specs import AlgorithmSpec, BarClock
from financial_brain.patterns.spec import canonical_json

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = ROOT / "src" / "financial_brain" / "strategies"


def _spec(lookback: int) -> AlgorithmSpec:
    return AlgorithmSpec(
        algorithm_id=f"donchian_{lookback}", source_ids=("TV-001",), family="trend_breakout", version="1",
        segment="CASH_EQUITY", timeframe="1D", clock=BarClock("NSE_CASH", signal_bar=0),
        parameters={"lookback": lookback}, cost_model_version="cost-v1",
    )


def test_every_family_declares_null_alternative_sample_and_complexity() -> None:  # ALG-095
    for family in FAMILY_HYPOTHESES:
        assert family_complete(family), family


def test_relabelled_copies_count_as_one_trial() -> None:  # ALG-096
    specs = [_spec(20), _spec(20), _spec(10)]
    assert effective_trials(specs) == 2


def test_cohorts_stay_separate() -> None:  # ALG-098
    out = cohort_report([0.01, -0.02], [0.03])
    assert out["pre_confirmation"]["count"] == 2 and out["post_confirmation"]["count"] == 1


def test_pairing_uses_net_differences_not_win_rate() -> None:  # ALG-100
    out = paired_incremental([0.02, 0.02, -0.5], [0.01, 0.01, 0.0])
    assert out["total_diff"] == pytest.approx(-0.48)
    assert verdict(out) == "REJECT"  # three wins do not outweigh one large loss


def test_donchian_adapter_satisfies_the_common_protocol() -> None:  # ALG-107
    closes, highs = synthetic_breakout(1)
    adapter = DonchianAdapter()
    assert adapter.name == "donchian_20"
    assert isinstance(adapter.signals(closes, highs), list)


@pytest.mark.parametrize("gen", [synthetic_breakout, synthetic_reversal, synthetic_noisy_range])
def test_synthetic_generators_are_seeded_and_labelled(gen) -> None:  # ALG-108
    assert gen(3) == gen(3)
    assert gen(3) != gen(4)


def test_negative_strategy_is_rejected_by_the_verdict() -> None:  # ALG-125
    losing = paired_incremental([-0.01] * 50, [0.0] * 50)
    assert verdict(losing) == "REJECT"
    winning = paired_incremental([0.02] * 50, [0.0] * 50)
    assert verdict(winning) == "KEEP_RESEARCHING"  # still not an edge label


def test_release_gate_blocks_promotion_without_forward_observations() -> None:  # ALG-127
    assert release_gate(0) == "BLOCKED_NO_FORWARD_EVIDENCE"
    assert release_gate(12) == "FORWARD_EVIDENCE_PRESENT_REVIEW_REQUIRED"


def test_no_module_in_the_package_emits_an_edge_label() -> None:  # ALG-106
    for path in STRATEGIES.glob("*.py"):
        assert "EDGE_CONFIRMED" not in path.read_text(encoding="utf-8") or path.name == "families.py"
    assert "EDGE_CONFIRMED" not in canonical_json(verdict(paired_incremental([0.1], [0.0]))).decode()


def test_strategy_card_links_source_hash_and_paper_banner() -> None:  # ALG-122
    out = card("TV-001", _spec(20), "https://www.tradingview.com/script/ZZ0T9Wc7-Donchian-Channel-Strategy/")
    assert out["paper_only"] is True and out["label"] == "SOURCE_CLAIM"
    assert len(out["card_sha256"]) == 64 and len(out["spec_sha256"]) == 64


def test_strategies_package_has_no_route_to_the_order_system() -> None:  # ALG-081
    forbidden = ("financial_brain.execution", "financial_brain.paper", "financial_brain.risk", "socket", "urllib", "requests")
    for path in STRATEGIES.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not any(name == f or name.startswith(f + ".") for f in forbidden), (path.name, name)


def test_offline_demo_runs_with_no_credentials_or_network() -> None:  # ALG-118
    assert demo.main(["--offline"]) == 0
