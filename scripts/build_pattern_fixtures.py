"""Build the family example fixtures (CP-0088, CP-0340): one positive and two negatives per supported family.

Run from the repository root:

    python scripts/build_pattern_fixtures.py

Writes ``tests/fixtures/patterns/family_examples.json``. Each example stores its OHLC rows and the detector names
that must (positive) or must not (negative) fire on it. The test suite reloads the file and checks every
expectation, so a detector change that breaks an example fails the build.
"""

from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures" / "patterns" / "family_examples.json"
sys.path.insert(0, str(ROOT / "src"))

from financial_brain.patterns import detectors2 as d2  # noqa: E402
from financial_brain.patterns.detectors import (  # noqa: E402
    Bars,
    double_extrema,
    flags,
    head_shoulders,
    rectangle,
)


def mid_bars(closes: list[float], spread: float) -> list[list[float]]:
    opens = [closes[0]] + [(closes[i - 1] + closes[i]) / 2 for i in range(1, len(closes))]
    rows = []
    for o, c in zip(opens, closes, strict=True):
        rows.append([round(o, 4), round(max(o, c) + spread, 4), round(min(o, c) - spread, 4), round(c, 4)])
    return rows


def to_bars(rows: list[list[float]]) -> Bars:
    return Bars(tuple(r[0] for r in rows), tuple(r[1] for r in rows), tuple(r[2] for r in rows), tuple(r[3] for r in rows))


def _rectangle_positive() -> list[float]:
    rng = random.Random(21)
    closes = [100 + (2.0 if i % 2 else -2.0) + rng.uniform(-0.2, 0.2) for i in range(40)]
    closes += [104.5, 106.0, 107.5, 108.0, 108.5]
    return closes


def _rectangle_negative_trend() -> list[float]:
    return [100 + 0.8 * i for i in range(60)]


def _rectangle_negative_wide() -> list[float]:
    return [100 + (20.0 if i % 2 else -20.0) for i in range(60)]


def _flag_positive() -> list[float]:
    closes = [100 + 1.4 * i for i in range(9)]  # pole, about +11 %
    peak = closes[-1]
    closes += [peak - 0.3 * (i + 1) * (1 if i % 2 == 0 else -0.5) for i in range(8)]  # shallow flag
    closes += [peak + 1.5, peak + 2.5, peak + 3.5]
    return closes


def _flag_negative_deep() -> list[float]:
    closes = [100 + 1.4 * i for i in range(9)]
    peak = closes[-1]
    closes += [peak - 1.4 * (i + 1) for i in range(8)]  # retraces most of the pole
    return closes


def _flag_negative_no_pole() -> list[float]:
    return [100 + 0.05 * i for i in range(30)]


def _double_positive() -> list[float]:
    closes = [100, 97, 94, 91, 90, 92, 95, 97, 96, 93, 91, 90.4, 92, 95, 98, 101, 103]
    return [float(c) for c in closes]


def _double_negative_single() -> list[float]:
    closes = [100, 97, 94, 91, 90, 92, 95, 98, 101, 104, 106]
    return [float(c) for c in closes]


def _double_negative_unequal() -> list[float]:
    closes = [100, 97, 94, 91, 90, 92, 95, 97, 96, 93, 88, 84, 86, 90]
    return [float(c) for c in closes]


def _hs_positive() -> list[float]:
    pts = [100, 106, 102, 110, 102, 106, 100, 96]
    closes: list[float] = []
    for a, b in zip(pts, pts[1:], strict=False):
        closes.extend(a + (b - a) * k / 4 for k in range(4))
    closes.append(pts[-1])
    return closes


def _hs_negative_rising() -> list[float]:
    return [100 + 0.6 * i + 2.5 * math.sin(i / 2.5) for i in range(70)]


def _pennant_positive() -> list[float]:
    closes = [100 + 1.5 * i for i in range(9)]
    for k in range(18):
        closes.append(112 + (2.0 - 0.1 * k) * (1 if k % 2 == 0 else -1))
    closes += [116, 118, 120]
    return closes


def _pennant_negative_widening() -> list[float]:
    closes = [100 + 1.5 * i for i in range(9)]
    for k in range(18):
        closes.append(112 + (0.5 + 0.2 * k) * (1 if k % 2 == 0 else -1))
    closes += [116, 118, 120]
    return closes


def _pennant_negative_no_pole() -> list[float]:
    closes = [112.0 + 0.1 * i for i in range(9)]
    for k in range(18):
        closes.append(112 + (2.0 - 0.1 * k) * (1 if k % 2 == 0 else -1))
    closes += [116, 118, 120]
    return closes


def _cup_positive() -> list[float]:
    pts = [(0, 90), (10, 100), (40, 84), (65, 99.6), (71, 96.5), (80, 101), (95, 104)]
    closes: list[float] = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
        closes.extend(y0 + (y1 - y0) * (x - x0) / (x1 - x0) for x in range(x0, x1))
    closes.append(104.0)
    return closes


def _cup_negative_shallow() -> list[float]:
    pts = [(0, 90), (10, 100), (40, 97), (65, 99.6), (71, 98.9), (80, 101), (95, 104)]
    closes: list[float] = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
        closes.extend(y0 + (y1 - y0) * (x - x0) / (x1 - x0) for x in range(x0, x1))
    closes.append(104.0)
    return closes


def _cup_negative_deep_handle() -> list[float]:
    pts = [(0, 90), (10, 100), (40, 84), (65, 99.6), (71, 88.0), (80, 101), (95, 104)]
    closes: list[float] = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
        closes.extend(y0 + (y1 - y0) * (x - x0) / (x1 - x0) for x in range(x0, x1))
    closes.append(104.0)
    return closes


FAMILIES: dict[str, dict[str, object]] = {
    "rectangle": {
        "detector": "rectangle", "spread": 0.6, "run": rectangle,
        "positive": _rectangle_positive, "negatives": [_rectangle_negative_trend, _rectangle_negative_wide],
    },
    "flag": {
        "detector": "flag_bullish", "spread": 0.2, "run": lambda b: flags(b),
        "positive": _flag_positive, "negatives": [_flag_negative_deep, _flag_negative_no_pole],
    },
    "double_bottom": {
        "detector": "double_bottom", "spread": 0.3, "run": lambda b: double_extrema(b, window=12),
        "positive": _double_positive, "negatives": [_double_negative_single, _double_negative_unequal],
    },
    "head_shoulders_top": {
        "detector": "head_shoulders_top", "spread": 0.3, "run": lambda b: head_shoulders(b, window=20),
        "positive": _hs_positive, "negatives": [_hs_negative_rising, _double_negative_single],
    },
    "pennant": {
        "detector": "pennant_bullish", "spread": 0.2, "run": d2.pennant,
        "positive": _pennant_positive, "negatives": [_pennant_negative_widening, _pennant_negative_no_pole],
    },
    "cup_with_handle": {
        "detector": "cup_with_handle", "spread": 0.3, "run": d2.cup_handle,
        "positive": _cup_positive, "negatives": [_cup_negative_shallow, _cup_negative_deep_handle],
    },
}


def _fires(run: object, bars: Bars, name: str) -> bool:
    found = run(bars)  # type: ignore[operator]
    return any(f.detector == name or (name == "rectangle" and f.detector == "rectangle") for f in found)


def build() -> dict[str, object]:
    examples: list[dict[str, object]] = []
    problems: list[str] = []
    for family, spec in FAMILIES.items():
        spread = float(spec["spread"])  # type: ignore[arg-type]
        run = spec["run"]
        detector = str(spec["detector"])
        pos_rows = mid_bars(spec["positive"]() if callable(spec["positive"]) else [], spread)  # type: ignore[operator]
        if not _fires(run, to_bars(pos_rows), detector):
            problems.append(f"{family}: positive example does not fire {detector}")
        examples.append({"family": family, "role": "positive", "detector": detector, "expect_fire": True,
                         "ohlc": pos_rows})
        for idx, neg in enumerate(spec["negatives"], start=1):  # type: ignore[union-attr]
            rows = mid_bars(neg(), spread)
            if _fires(run, to_bars(rows), detector):
                problems.append(f"{family}: negative {idx} fires {detector}")
            examples.append({"family": family, "role": f"negative_{idx}", "detector": detector,
                             "expect_fire": False, "ohlc": rows})
    if problems:
        raise SystemExit("fixture expectations failed:\n" + "\n".join(problems))
    return {
        "schema": "patterns-family-examples/1",
        "note": "Synthetic OHLC examples built by scripts/build_pattern_fixtures.py. Not market data.",
        "examples": examples,
    }


def main() -> int:
    payload = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT} with {len(payload['examples'])} examples")  # type: ignore[index]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
