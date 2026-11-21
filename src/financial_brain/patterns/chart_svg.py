"""Static SVG candle chart with a fixed aspect ratio (CP-0262).

The drawing area keeps the requested width-to-height ratio for any bar count. Bar width is clamped by
``views.chart_scale`` so 20-bar and 500-bar views stay readable, and the price axis always starts from the
visible minimum low, so bars are never stretched. Output is escaped and self-contained (no scripts).
"""

from __future__ import annotations

from html import escape

from financial_brain.patterns.detectors import Bars
from financial_brain.patterns.views import chart_scale


def render_candles_svg(bars: Bars, *, width_px: int = 720, height_px: int = 360, title: str = "chart") -> str:
    n = len(bars)
    if n == 0:
        raise ValueError("no bars to draw")
    scale = chart_scale(n, width_px=width_px, height_px=height_px)
    per_bar = scale["px_per_bar"]
    drawn_width = per_bar * n
    lo = min(bars.low)
    hi = max(bars.high)
    span = (hi - lo) or 1.0
    pad = 8.0

    def y(price: float) -> float:
        return pad + (hi - price) / span * (height_px - 2 * pad)

    parts: list[str] = []
    for i in range(n):
        x_mid = (i + 0.5) * per_bar
        o, h, lo_b, c = bars.open[i], bars.high[i], bars.low[i], bars.close[i]
        colour = "#2e7d32" if c >= o else "#c62828"
        parts.append(
            f'<line x1="{x_mid:.2f}" y1="{y(h):.2f}" x2="{x_mid:.2f}" y2="{y(lo_b):.2f}" stroke="{colour}" stroke-width="1"/>'
        )
        top, bottom = y(max(o, c)), y(min(o, c))
        body_w = max(1.0, per_bar * 0.6)
        parts.append(
            f'<rect x="{x_mid - body_w / 2:.2f}" y="{top:.2f}" width="{body_w:.2f}" '
            f'height="{max(1.0, bottom - top):.2f}" fill="{colour}"/>'
        )
    body = "".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width_px} {height_px}" '
        f'width="{width_px}" height="{height_px}" role="img" aria-label="{escape(title)}">'
        f'<title>{escape(title)} (SYNTHETIC OR RESEARCH ONLY)</title>'
        f'<g data-bars="{n}" data-drawn-width="{drawn_width:.2f}">{body}</g></svg>'
    )
