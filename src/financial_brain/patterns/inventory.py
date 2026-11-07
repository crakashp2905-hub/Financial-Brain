"""Truthful status for each of the 72 catalogue rows (CP-0004, CP-0393).

Labels: ``implemented`` (a detector or candle wrapper covers the definition),
``partial`` (some of the definition is covered; the gap is stated), ``research_only``
(not formalisable as a shape; kept as a description), ``missing`` (no code).
A label never comes from the catalogue's own ``repo_status`` text.
"""

from __future__ import annotations

from typing import Final

STATUS: Final[dict[str, tuple[str, str]]] = {
    # Nison (candles, gaps, levels)
    "N01": ("implemented", "candles.CANDLE_MAP c_hammer (downtrend context)"),
    "N02": ("implemented", "candles.CANDLE_MAP c_shooting_star (uptrend context)"),
    "N03": ("implemented", "candles.CANDLE_MAP c_bull_engulf, c_bear_engulf"),
    "N04": ("implemented", "candles.CANDLE_MAP c_dark_cloud"),
    "N05": ("implemented", "candles.CANDLE_MAP c_piercing"),
    "N06": ("implemented", "candles.CANDLE_MAP c_morning_star, c_evening_star"),
    "N07": ("missing", "doji star / abandoned baby not modelled"),
    "N08": ("partial", "c_bull_harami only; bearish harami and cross variants missing"),
    "N09": ("missing", "tweezers not modelled"),
    "N10": ("partial", "c_marubozu approximates belt hold; not the same shape"),
    "N11": ("missing", "counterattack lines not modelled"),
    "N12": ("missing", "upside-gap two crows / mat hold not modelled"),
    "N13": ("implemented", "candles.CANDLE_MAP c_three_soldiers, c_three_crows"),
    "N14": ("missing", "three mountain / Buddha / river not modelled"),
    "N15": ("missing", "dumpling / fry-pan / tower not modelled"),
    "N16": ("partial", "candles.gap_kind range gap primitive; window event not emitted"),
    "N17": ("missing", "pending definitions excluded from inference (candles.PENDING_DEFINITIONS)"),
    "N18": ("partial", "c_doji only; long-legged, gravestone, tri-star and high-wave not separated"),
    "N19": ("missing", "springs and upthrusts not modelled"),
    "N20": ("research_only", "change of polarity: descriptive, not formalised"),
    "N21": ("research_only", "retracement levels: fraction primitive exists; no pattern rule"),
    "N22": ("research_only", "indicator combinations: not formalised as a shape"),
    "N23": ("research_only", "volume and open interest combinations: not formalised"),
    "N24": ("research_only", "Elliott wave counts: not formalisable without subjective labels"),
    "N25": ("research_only", "market profile: not formalised"),
    # Bulkowski (chart patterns)
    "B01": ("missing", "broadening bottom not modelled"),
    "B02": ("missing", "right-angled ascending broadening not modelled"),
    "B03": ("missing", "right-angled descending broadening not modelled"),
    "B04": ("missing", "broadening top not modelled"),
    "B05": ("missing", "broadening wedge ascending not modelled"),
    "B06": ("missing", "broadening wedge descending not modelled"),
    "B07": ("missing", "bump-and-run reversal bottom not modelled"),
    "B08": ("missing", "bump-and-run reversal top not modelled"),
    "B09": ("missing", "cup with handle not modelled"),
    "B10": ("missing", "dead-cat bounce not modelled"),
    "B11": ("missing", "diamond not modelled"),
    "B12": ("implemented", "detectors.double_extrema (double_bottom)"),
    "B13": ("implemented", "detectors.double_extrema (double_top)"),
    "B14": ("partial", "detectors.flags; pennants not separated from triangles"),
    "B15": ("missing", "high and tight flag not modelled"),
    "B16": ("partial", "candles.gap_kind; area, breakaway, exhaustion not classified"),
    "B17": ("partial", "c_hammer context; hanging man not a separate rule"),
    "B18": ("implemented", "detectors.head_shoulders (bottom)"),
    "B19": ("missing", "complex head-and-shoulders bottom not modelled"),
    "B20": ("implemented", "detectors.head_shoulders (top)"),
    "B21": ("missing", "complex head-and-shoulders top not modelled"),
    "B22": ("missing", "weekly horn bottom not modelled"),
    "B23": ("missing", "weekly horn top not modelled"),
    "B24": ("partial", "library inside_bar feature; not a catalogue detector"),
    "B25": ("missing", "island reversal not modelled"),
    "B26": ("missing", "measured move down not modelled"),
    "B27": ("missing", "measured move up not modelled"),
    "B28": ("missing", "one-day reversal not modelled"),
    "B29": ("partial", "library outside_bar feature; not a catalogue detector"),
    "B30": ("missing", "weekly pipe bottom not modelled"),
    "B31": ("missing", "weekly pipe top not modelled"),
    "B32": ("implemented", "detectors.rectangle (both breakout directions)"),
    "B33": ("implemented", "detectors.rectangle (both breakout directions)"),
    "B34": ("missing", "rounding bottom not modelled"),
    "B35": ("missing", "rounding top not modelled"),
    "B36": ("missing", "scallops not modelled"),
    "B37": ("missing", "shark-32 not modelled"),
    "B38": ("implemented", "detectors.triangles (triangle_ascending)"),
    "B39": ("implemented", "detectors.triangles (triangle_descending)"),
    "B40": ("implemented", "detectors.triangles (triangle_symmetric)"),
    "B41": ("implemented", "detectors.triangles (triangle_symmetric)"),
    "B42": ("missing", "triple bottom not modelled"),
    "B43": ("missing", "triple top not modelled"),
    "B44": ("implemented", "detectors.triangles (wedge_falling)"),
    "B45": ("implemented", "detectors.triangles (wedge_rising)"),
    "B46": ("missing", "weekly reversal downside not modelled"),
    "B47": ("missing", "weekly reversal upside not modelled"),
}


def inventory(entries: list[str]) -> list[dict[str, str]]:
    """Status rows for the given catalogue IDs, in order. Unknown IDs raise."""
    out = []
    for entry_id in entries:
        if entry_id not in STATUS:
            raise KeyError(f"no inventory status for {entry_id}")
        status, note = STATUS[entry_id]
        out.append({"id": entry_id, "status": status, "evidence": note})
    return out


def counts() -> dict[str, int]:
    tally: dict[str, int] = {}
    for status, _ in STATUS.values():
        tally[status] = tally.get(status, 0) + 1
    return dict(sorted(tally.items()))
