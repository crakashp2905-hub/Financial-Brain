"""Data model behind the chart-pattern workstation views (CP-0235..0262).

Each function is pure: it takes plain rows and returns plain data, so the same logic can back the terminal
tab, the HTML report and the tests. Nothing here places an order, reaches a broker, or imports the paper or
execution packages (see ``tests/test_pattern_offline.py``). Every value shown to a trader is labelled with its
evidence tier, and a missing input is shown as ``NOT RECORDED`` rather than defaulted.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

NOT_RECORDED = "NOT RECORDED"
ALLOWED_ACTIONS = ("Add to paper watchlist", "Simulate next session")
STATES = ("CANDIDATE", "CONFIRMED", "FAILED", "INVALIDATED", "EXPIRED")


class ActionRefused(PermissionError):
    """The requested action is not one of the paper-only actions."""


def status_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Count rows per state with the latest known timestamp in each group (CP-0235)."""
    out: dict[str, dict[str, Any]] = {s: {"count": 0, "latest_known_at": None} for s in STATES}
    for row in rows:
        state = str(row.get("state", ""))
        if state not in out:
            continue
        bucket = out[state]
        bucket["count"] += 1
        stamp = row.get("known_at")
        if stamp is not None and (bucket["latest_known_at"] is None or stamp > bucket["latest_known_at"]):
            bucket["latest_known_at"] = stamp
    return out


def filter_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    symbol: str | None = None,
    family: str | None = None,
    timeframe: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    state: str | None = None,
    min_liquidity: float | None = None,
    min_evidence: str | None = None,
    sector: str | None = None,
) -> list[dict[str, Any]]:
    """Filter by the controls named in CP-0237 and CP-0383. Missing liquidity never passes a liquidity floor."""
    tiers = ["DESCRIPTIVE", "HISTORICAL_REPLICATED", "FORWARD_PAPER", "EXTERNALLY_CONFIRMED"]
    out: list[dict[str, Any]] = []
    for row in rows:
        if symbol is not None and row.get("symbol") != symbol:
            continue
        if family is not None and row.get("family") != family:
            continue
        if timeframe is not None and row.get("timeframe") != timeframe:
            continue
        if state is not None and row.get("state") != state:
            continue
        if sector is not None and row.get("sector") != sector:
            continue
        known = row.get("known_at")
        if date_from is not None and (known is None or known < date_from):
            continue
        if date_to is not None and (known is None or known > date_to):
            continue
        if min_liquidity is not None:
            liq = row.get("liquidity")
            if liq is None or liq < min_liquidity:
                continue
        if min_evidence is not None:
            have = row.get("evidence_tier", "DESCRIPTIVE")
            if have not in tiers or tiers.index(have) < tiers.index(min_evidence):
                continue
        out.append(dict(row))
    return out


def detail(row: Mapping[str, Any]) -> dict[str, Any]:
    """Pattern detail page content (CP-0239): geometry, observed bars, recognition time, invalidation and tests."""
    return {
        "title": f"{row.get('symbol', NOT_RECORDED)} {row.get('detector', NOT_RECORDED)}",
        "geometry": dict(row.get("geometry", {})) or NOT_RECORDED,
        "observed_bars": row.get("observed_bars", NOT_RECORDED),
        "recognised_at": row.get("known_at", NOT_RECORDED),
        "invalidation": row.get("invalidated_at", NOT_RECORDED),
        "tests": list(row.get("tests", [])) or NOT_RECORDED,
        "evidence_tier": row.get("evidence_tier", "DESCRIPTIVE"),
        "label": "RESEARCH ONLY",
    }


def compare_with_baseline(
    row: Mapping[str, Any], *, momentum: float | None, week52_position: float | None
) -> dict[str, Any]:
    """Side-by-side context against momentum and 52-week position (CP-0240). Missing inputs stay missing."""
    return {
        "pattern_state": row.get("state", NOT_RECORDED),
        "momentum": NOT_RECORDED if momentum is None else momentum,
        "week52_position": NOT_RECORDED if week52_position is None else week52_position,
        "note": "descriptive comparison; neither side is a forecast",
    }


def why_not_trade(row: Mapping[str, Any]) -> list[str]:
    """Reasons a candidate is not a trade (CP-0241). Always non-empty: the panel never says 'trade'."""
    reasons: list[str] = []
    if row.get("state") != "CONFIRMED":
        reasons.append("the pattern is not confirmed by a close beyond its level")
    if row.get("evidence_tier", "DESCRIPTIVE") == "DESCRIPTIVE":
        reasons.append("evidence tier is DESCRIPTIVE: no replicated or forward evidence exists")
    if row.get("risk_verdict") not in (None, "PASS"):
        reasons.append(f"risk gate verdict: {row.get('risk_verdict')}")
    if row.get("earliest_entry_at") is None:
        reasons.append("no earliest eligible entry bar is recorded")
    reasons.append("costs and gap risk are not modelled as guaranteed; see the paper methodology")
    return reasons


def ticket_preview(
    *, earliest_entry: str | None, costs: float | None, capital_required: float | None, risk_verdict: str | None
) -> dict[str, Any]:
    """Hypothetical paper ticket (CP-0242). It carries no broker identifier and cannot be sent anywhere."""
    return {
        "mode": "PAPER ONLY",
        "entry_eligibility": earliest_entry or NOT_RECORDED,
        "estimated_costs": NOT_RECORDED if costs is None else costs,
        "capital_required": NOT_RECORDED if capital_required is None else capital_required,
        "risk_gate_verdict": risk_verdict or NOT_RECORDED,
        "submittable": False,
    }


def allowed_action(name: str) -> str:
    """Only the two paper actions exist (CP-0243). Anything else is refused."""
    if name not in ALLOWED_ACTIONS:
        raise ActionRefused(f"action {name!r} is not available; only {', '.join(ALLOWED_ACTIONS)}")
    return name


def knowable_at(rows: Sequence[Mapping[str, Any]], t: str) -> list[dict[str, Any]]:
    """Rows as they were knowable at timestamp ``t`` (CP-0244). ISO-8601 UTC strings compare correctly."""
    out: list[dict[str, Any]] = []
    for row in rows:
        known = row.get("known_at")
        if known is None or known > t:
            continue
        visible = dict(row)
        for field in ("confirmed_at", "failed_at", "invalidated_at"):
            stamp = row.get(field)
            if stamp is None or stamp > t:
                visible.pop(field, None)
                if field == "confirmed_at":
                    visible["state"] = "CANDIDATE"
        out.append(visible)
    return out


def status_page(
    *,
    last_session: str | None,
    as_of: str,
    failed_scans: int,
    audit_exceptions: int,
    paper_runs_ok: int,
    paper_runs_failed: int,
) -> dict[str, Any]:
    """Local health page content (CP-0245). A stale or failing input is reported, never hidden."""
    stale = last_session is None or last_session < as_of[:10]
    return {
        "data_freshness": "STALE" if stale else "CURRENT",
        "last_session": last_session or NOT_RECORDED,
        "failed_scans": failed_scans,
        "audit_exceptions": audit_exceptions,
        "paper_run_health": "OK" if paper_runs_failed == 0 and paper_runs_ok > 0 else "ATTENTION",
    }


def overlay_markers(row: Mapping[str, Any]) -> dict[str, Any]:
    """Signal bar and earliest eligible entry bar as separate markers (CP-0256)."""
    return {
        "signal_bar": row.get("signal_index"),
        "earliest_entry_bar": row.get("earliest_entry_index"),
        "separate": row.get("signal_index") != row.get("earliest_entry_index"),
    }


def visible_boundaries(boundaries: Sequence[int], t: int) -> list[int]:
    """Formation boundary indices that are drawable at bar ``t`` (CP-0257). Nothing beyond ``t`` is drawn."""
    return [b for b in boundaries if b <= t]


def version_history(versions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Revisions as new versions with the changed fields listed (CP-0258). Inputs are never mutated."""
    out: list[dict[str, Any]] = []
    for prev, cur in zip(versions, versions[1:], strict=False):
        changed = sorted(k for k in set(prev) | set(cur) if prev.get(k) != cur.get(k))
        out.append({"from_version": prev.get("version"), "to_version": cur.get("version"), "changed": changed})
    return out


def risk_zones(*, atr_value: float | None, last_close: float | None, hypothetical_cost: float | None) -> dict[str, Any]:
    """ATR range and hypothetical cost around the last close (CP-0259). No target is ever returned."""
    if atr_value is None or last_close is None:
        band: Any = NOT_RECORDED
    else:
        band = [round(last_close - atr_value, 6), round(last_close + atr_value, 6)]
    return {
        "atr_range": band,
        "hypothetical_cost": NOT_RECORDED if hypothetical_cost is None else hypothetical_cost,
        "guaranteed_target": None,
        "note": "risk zone only; not a target or a guarantee",
    }


def hover_breakdown(row: Mapping[str, Any]) -> dict[str, Any]:
    """What the hover tooltip explains (CP-0260)."""
    return {
        "level_tolerance": row.get("tolerance", NOT_RECORDED),
        "pivots": row.get("pivots", NOT_RECORDED),
        "duration_bars": row.get("duration_bars", NOT_RECORDED),
        "breakout_threshold": row.get("breakout_threshold", NOT_RECORDED),
    }


def research_controls(*, research_mode: bool, matched_baseline: Any, lookalikes: Sequence[Any]) -> dict[str, Any]:
    """Baseline and unconfirmed lookalikes, shown only in research mode (CP-0261)."""
    if not research_mode:
        return {"shown": False}
    return {"shown": True, "matched_momentum_baseline": matched_baseline, "unconfirmed_lookalikes": list(lookalikes)}


def chart_scale(n_bars: int, *, width_px: int, height_px: int) -> dict[str, float]:
    """Pixel scale that keeps the aspect ratio for 20-bar and 500-bar charts (CP-0262).

    The per-bar width is clamped so a 20-bar view does not stretch into blocks and a 500-bar view does not
    collapse below one pixel. The height-to-width ratio is never changed.
    """
    if n_bars <= 0 or width_px <= 0 or height_px <= 0:
        raise ValueError("bars and pixel sizes must be positive")
    per_bar = min(12.0, max(1.0, width_px / n_bars))
    return {"px_per_bar": per_bar, "aspect_ratio": round(height_px / width_px, 6), "drawn_width_px": per_bar * n_bars}
