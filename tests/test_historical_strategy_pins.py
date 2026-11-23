from pathlib import Path

from scripts.validate_historical_strategy_pins import validate


def test_historical_strategy_pins_are_immutable_and_non_promotable() -> None:
    root = Path(__file__).resolve().parents[1]
    assert validate(root) == []
