"""Versioned Four-Node specifications and ambiguity gates (FB-2501 to FB-2508).

The original rules are archived verbatim in ``docs/strategies/four_node/source`` with SHA-256 sums.
Every ambiguous rule is an ``Ambiguity`` with named alternative interpretations. A variant is
executable only when every ambiguity it touches has an owner-confirmed choice recorded; otherwise
``executable()`` is False and signals fail closed. Alternatives are never merged into one trial.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

SOURCE_SHA256 = "c5e8b1ccdd40b9dd13e6386c1689b23909a1ab7aae42da82466dd62225b8bc15"
PINE_SHA256 = "f316a055468c7607596e23fbd18acfb4d038592e6d51f1052cf194b94d17c4e7"
SOURCE_PATH = Path("docs/strategies/four_node/source/original_rules_2025-08-22.txt")


@dataclass(frozen=True)
class Ambiguity:
    id: str
    text: str
    alternatives: tuple[str, ...]
    checklist: str


AMBIGUITIES: tuple[Ambiguity, ...] = (
    Ambiguity("BUY_EXCEPTION_SIDE", "Buy exception says close *below* N2; normal buy says above.",
              ("LITERAL_BELOW", "MIRROR_ABOVE"), "FB-2503"),
    Ambiguity("BUY_N3_MID", "Buy rule says N3 <= 20C Mid, same as sell.",
              ("LITERAL_LE_MID", "MIRROR_GE_MID"), "FB-2504"),
    Ambiguity("N2_BUY_COMPARATOR", "'N2 <= 20H High for Buy' (typo '20H').",
              ("LITERAL_LE_HIGH", "MIRROR_GE_HIGH"), "FB-2505"),
    Ambiguity("N4_DEFINITION", "N4 is not formally defined.",
              ("NEXT_OPPOSITE_FRACTAL", "BREAKOUT_CANDLE"), "FB-2506"),
    Ambiguity("SIGNIFICANT_CANDLE", "Wick 'equal height both sides' more than body.",
              ("BOTH_WICKS_GT_BODY", "COMBINED_WICK_GT_BODY"), "FB-2507"),
    Ambiguity("FIB_ANCHOR", "Daily Fibonacci anchors not specified.",
              ("PREV_DAY_LOW_TO_HIGH", "PREV_DAY_HIGH_TO_LOW"), "FB-2508"),
    Ambiguity("RETURN_CANDLE", "'Return candle' and exception timing unspecified.",
              ("NO_OPPOSITE_CANDLE_SINCE_N3", "NO_OPPOSITE_CANDLE_AT_BREAKOUT"), "FB-2503"),
)


@dataclass
class Variant:
    name: str
    market: str                       # XAUUSD or IN
    executable_intent: bool           # original XAU reference is never executable
    choices: dict[str, str] = field(default_factory=dict)
    confirmed_by: dict[str, str] = field(default_factory=dict)
    params: dict[str, float] = field(default_factory=dict)

    def choose(self, ambiguity_id: str, alternative: str, *, owner: str) -> None:
        amb = {a.id: a for a in AMBIGUITIES}[ambiguity_id]
        if alternative not in amb.alternatives:
            raise ValueError(f"{alternative} is not an alternative for {ambiguity_id}")
        if not owner:
            raise ValueError("only an owner-confirmed choice counts")
        self.choices[ambiguity_id] = alternative
        self.confirmed_by[ambiguity_id] = owner

    def unresolved(self) -> list[str]:
        return [a.id for a in AMBIGUITIES if a.id not in self.choices]

    def executable(self) -> bool:
        return self.executable_intent and not self.unresolved()


def original_xau() -> Variant:
    """Non-executable research reference with the source's XAU pip parameters."""
    return Variant("FourNode_XAU_Original_v1", "XAUUSD", False,
                   params={"pip": 0.10, "tp": 20, "max_sl": 15, "max_d1": 15, "max_d2": 10,
                           "min_struct": 20, "trail_start": 10, "trail_step": 5, "n4_wick": 5})


def india_v1() -> Variant:
    """Paper-only India variant; distances in ATR multiples, not pips. Blocked until choices exist."""
    return Variant("FourNode_India_v1", "IN", True,
                   params={"tp_atr": 2.0, "max_sl_atr": 1.5, "max_d1_atr": 1.5, "max_d2_atr": 1.0,
                           "min_struct_atr": 2.0, "trail_start_atr": 1.0, "trail_step_atr": 0.5,
                           "n4_wick_atr": 0.5})


def source_intact(root: Path) -> bool:
    import hashlib
    return hashlib.sha256((root / SOURCE_PATH).read_bytes()).hexdigest() == SOURCE_SHA256
