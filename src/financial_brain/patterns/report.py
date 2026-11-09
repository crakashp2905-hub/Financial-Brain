"""Offline trader report and chart overlays (stages 15-16: CP-0234..0265 subset).

- Overlay coordinates are validated against the available bar range (CP-0253) and
  carry the bar index the overlay was known from (CP-0255).
- The HTML report is self-contained (no network, no hosted chart library, CP-0263),
  escapes every user-supplied string (CP-0264, CP-0330), and always shows the
  simulation, research-only and K7 banners (CP-0251, CP-0250).
- The JSON export carries provenance and schema version (CP-0265).
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from typing import Any

from financial_brain.patterns.candles import K7_NOTICE

SCHEMA_VERSION = "patterns-report/1"
KINDS = frozenset({"trendline", "pivot", "zone", "breakout", "neckline", "signal_bar", "entry_bar"})
SIM_BANNER = "SIMULATION - prices are assumptions; nothing here is an observed quote or an order."
RESEARCH_BANNER = "RESEARCH ONLY - descriptive observations, not trade recommendations."
NOT_CALIBRATED = "not calibrated"


class InvalidOverlay(ValueError):
    """Overlay coordinates fall outside the bars it claims to describe."""


@dataclass(frozen=True)
class Overlay:
    kind: str
    points: tuple[tuple[int, float], ...]
    known_from: int
    state: str  # TENTATIVE | CONFIRMED | INVALIDATED (drawn differently, CP-0254)

    def validate(self, n_bars: int) -> None:
        if self.kind not in KINDS:
            raise InvalidOverlay(f"unknown overlay kind {self.kind!r}")
        if not 0 <= self.known_from < n_bars:
            raise InvalidOverlay("known_from outside available bars")
        for idx, price in self.points:
            if not 0 <= idx < n_bars:
                raise InvalidOverlay(f"point index {idx} outside available bars")
            if idx > self.known_from:  # no state may draw what was not yet known
                raise InvalidOverlay("overlay draws a point not yet known (future leakage)")
            if not isinstance(price, int | float) or price != price or price in (float("inf"), float("-inf")):
                raise InvalidOverlay("non-finite price")


def _esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def render_html(rows: list[dict[str, Any]], *, title: str) -> str:
    """Self-contained, escaped HTML. ``rows`` are plain dicts from the ranking layer."""
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{_esc(row.get(k, ''))}</td>" for k in ("symbol", "detector", "state", "geometry_fit", "data_quality", "research_evidence", "calibration", "reasons"))
        body_rows.append(f"<tr>{cells}</tr>")
    if not rows:
        table = '<p class="empty">No patterns were found in this data set. An empty result is not a signal.</p>'
    else:
        table = (
        "<table><thead><tr><th>Symbol</th><th>Pattern</th><th>State</th><th>Fit</th><th>Data</th>"
        "<th>Evidence</th><th>Calibration</th><th>Reasons</th></tr></thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<style>
:root {{ --bg:#fff; --fg:#111; --muted:#555; --warn:#8a4b00; --line:#ccc; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#111; --fg:#eee; --muted:#aaa; --warn:#ffb457; --line:#444; }} }}
:root[data-theme="dark"] {{ --bg:#111; --fg:#eee; --muted:#aaa; --warn:#ffb457; --line:#444; }}
body {{ background:var(--bg); color:var(--fg); font-family:system-ui, sans-serif; margin:16px; }}
.banner {{ color:var(--warn); border:1px solid var(--warn); padding:8px; margin:8px 0; }}
table {{ border-collapse:collapse; width:100%; }}
th, td {{ border:1px solid var(--line); padding:6px; text-align:left; overflow-wrap:anywhere; }}
@media (max-width: 600px) {{ table, thead, tbody, tr, th, td {{ display:block; }} }}
</style>
</head>
<body>
<h1>{_esc(title)}</h1>
<div class="banner">{_esc(SIM_BANNER)}</div>
<div class="banner">{_esc(RESEARCH_BANNER)}</div>
<div class="banner">{_esc(K7_NOTICE)}</div>
{table}
</body>
</html>
"""


OVERLAY_STYLE = {
    "CONFIRMED": {"dash": "solid", "opacity": 1.0, "legend": "confirmed geometry"},
    "TENTATIVE": {"dash": "dashed", "opacity": 0.6, "legend": "tentative (candidate) geometry"},
    "INVALIDATED": {"dash": "dotted", "opacity": 0.4, "legend": "invalidated: shown, never removed"},
}


HELP: dict[str, str] = {
    "throwback": "a return toward a breakout level after confirmation; not guaranteed",
    "pivot confirmation": "a pivot is known only after its right-hand bars have arrived",
    "ultimate-high bias": "choosing an exit with hindsight; never permitted in a study",
    "candidate": "a shape recognisable from past bars; may never confirm",
    "not calibrated": "no valid labelled history supports a probability",
}


def overlay_style(state: str) -> dict[str, Any]:
    """Distinct drawing for each lifecycle state, with a legend entry (CP-0254)."""
    if state not in OVERLAY_STYLE:
        raise InvalidOverlay(f"no style for state {state!r}")
    return dict(OVERLAY_STYLE[state])


def export_json(rows: list[dict[str, Any]], *, provenance: dict[str, str]) -> str:
    """Deterministic JSON export with schema version and provenance (CP-0265, CP-0247)."""
    payload = {
        "schema": SCHEMA_VERSION,
        "provenance": dict(sorted(provenance.items())),
        "banners": [SIM_BANNER, RESEARCH_BANNER, K7_NOTICE],
        "rows": rows,
    }
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def validate_export(text: str) -> dict[str, Any]:
    """Round-trip validator for exports (CP-0247)."""
    data = json.loads(text)
    if data.get("schema") != SCHEMA_VERSION:
        raise ValueError("unknown export schema")
    if not data.get("provenance"):
        raise ValueError("export lacks provenance")
    return data
