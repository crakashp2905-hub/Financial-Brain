"""Offline demo, health check, backup and dual sign-off (stages 22, 24, 26).

``python -m financial_brain.patterns.demo --offline --out DIR`` runs the whole path
on the seeded synthetic corpus with networking disabled. Everything it writes is
labelled SYNTHETIC. The sign-off reports ``PROVEN_MARKET_EDGE`` as UNKNOWN: market
edge needs genuine forward evidence this checkout cannot produce.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sys
from pathlib import Path
from typing import Any

import numpy as np

from financial_brain.patterns import offline
from financial_brain.patterns.candles import K7_NOTICE
from financial_brain.patterns.detectors import Bars, discover
from financial_brain.patterns.paper import CostModel, FillOutcome, PatternIntent, cost_breakdown, simulate_fill
from financial_brain.patterns.ranking import Opportunity, Scores, rank, trader_recommendations
from financial_brain.patterns.report import export_json, render_html
from financial_brain.patterns.spec import canonical_json, sha256_hex

SEED = 20261107
SYNTHETIC_LABEL = "SYNTHETIC"
NOT_EDGE = "NOT_EDGE_CONFIRMED"


def synthetic_corpus(seed: int = SEED, symbols: int = 3, n: int = 160) -> dict[str, Bars]:
    """Seeded multi-symbol corpus with range phases, breakouts and noise (CP-0339, CP-0341)."""
    rng = np.random.default_rng(seed)
    out: dict[str, Bars] = {}
    for s in range(symbols):
        closes: list[float] = []
        level = 100.0 + 5 * s
        for i in range(n):
            phase = (i // 14) % 2
            target = level + (2.0 if phase == 0 else -2.0)
            level = target + float(rng.normal(0, 0.4))
            if i % 53 == 52:
                level += 4.0  # breakout-like jump
            closes.append(round(max(level, 5.0), 4))
        out[f"SYN{s + 1:02d}"] = Bars.from_closes(closes, spread=0.6)
    return out


def corpus_hash(corpus: dict[str, Bars]) -> str:
    payload = {k: {"o": list(b.open), "h": list(b.high), "l": list(b.low), "c": list(b.close)} for k, b in sorted(corpus.items())}
    return sha256_hex(canonical_json(payload))


def deny_network() -> None:
    """Process-wide network denial for the offline demo (CP-0375)."""

    def blocked(*_args: Any, **_kwargs: Any) -> None:
        raise offline.OfflineViolation("network access blocked in offline demo")

    socket.socket.connect = blocked  # type: ignore[method-assign]
    socket.create_connection = blocked  # type: ignore[assignment]


def run_pipeline(corpus: dict[str, Bars]) -> dict[str, Any]:
    opportunities: list[Opportunity] = []
    formations_by_symbol: dict[str, list[Any]] = {}
    for symbol, bars in sorted(corpus.items()):
        forms = discover(bars)
        formations_by_symbol[symbol] = forms
        for f in forms:
            # Placeholder fit: confirmed shapes rank above unconfirmed ones. Not a probability.
            fit = 1.0 if f.confirmed_index is not None else 0.5
            opportunities.append(Opportunity(symbol, f, Scores(fit, 1.0, "DESCRIPTIVE"), 0.5))
    last = max((len(b) for b in corpus.values()), default=0) - 1
    ranked = rank(opportunities, now=last, max_age=60)
    rows = []
    for opp, codes in ranked:
        rows.append(
            {
                "symbol": opp.symbol,
                "detector": opp.formation.detector,
                "state": opp.formation.state_at(last) or "NONE",
                "geometry_fit": round(opp.scores.geometry_fit, 3),
                "data_quality": opp.scores.data_quality,
                "research_evidence": opp.scores.research_evidence,
                "calibration": opp.scores.calibration,
                "reasons": ";".join(codes),
            }
        )
    return {
        "rows": rows,
        "formation_count": sum(len(v) for v in formations_by_symbol.values()),
        "recommendations": len(trader_recommendations(ranked)),
    }


def _bar_time(index: int) -> str:
    """Synthetic bar index as an aware UTC timestamp, one bar per day from 2026-01-01."""
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    return (base + timedelta(days=index)).isoformat()


def preview_all(corpus: dict[str, Bars]) -> list[dict[str, Any]]:
    """Preview every confirmed formation's next-session entry before anything is executed (CP-0268)."""
    previews: list[dict[str, Any]] = []
    for symbol, bars in sorted(corpus.items()):
        for f in discover(bars):
            if f.confirmed_index is None or f.confirmed_index + 1 >= len(bars):
                continue
            previews.append(
                {
                    "label": SYNTHETIC_LABEL,
                    "symbol": symbol,
                    "detector": f.detector,
                    "signal_index": f.confirmed_index,
                    "entry_index": f.confirmed_index + 1,
                    "fill_label": "ASSUMED",
                }
            )
    return previews


def paper_preview(corpus: dict[str, Bars]) -> dict[str, Any] | None:
    """One clearly synthetic next-session paper intent with modelled costs (CP-0266, CP-0381)."""
    for symbol, bars in sorted(corpus.items()):
        for f in discover(bars):
            if f.confirmed_index is None or f.confirmed_index + 1 >= len(bars):
                continue
            entry = f.confirmed_index + 1
            intent = PatternIntent(
                event_id=f"demo_{symbol}_{f.candidate_known_index}",
                spec_sha256=sha256_hex(f.detector.encode()),
                data_sha256=sha256_hex(symbol.encode()),
                manifest_sha256=sha256_hex(b"demo-manifest"),
                symbol=symbol,
                side="long",
                quantity=100,
                decision_at_utc=_bar_time(f.confirmed_index),
                earliest_entry_at_utc=_bar_time(entry),
                segment="cash_equity",
            )
            model = CostModel(spread_bps=5, fee_bps=3, impact_bps=2, financing_bps_per_day=0.0)
            notional = 100 * float(bars.open[entry])
            costs = cost_breakdown(notional, model, holding_days=5)
            fill = simulate_fill(FillOutcome.FILLED, intent.quantity)
            return {
                "label": SYNTHETIC_LABEL,
                "symbol": symbol,
                "entry_index": entry,
                "quantity": fill.filled,
                "fill_label": fill.label,
                "costs_total": round(costs["total"], 4),
                "event_id": intent.event_id,
            }
    return None


def health(catalogue: Path, out_dir: Path | None) -> dict[str, Any]:
    """Startup checks that do not modify protected data (CP-0374)."""
    checks = {
        "catalogue_present": catalogue.exists(),
        "offline_mode": offline.current_mode() == offline.OFFLINE,
        "out_dir_writable": True if out_dir is None else out_dir.exists() or out_dir.parent.exists(),
    }
    return {"ok": all(checks.values()), "checks": checks}


def backup(events: list[dict[str, Any]]) -> tuple[str, str]:
    """Deterministic backup with a checksum; restore verifies it (CP-0370)."""
    text = json.dumps(events, sort_keys=True, separators=(",", ":"))
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def restore(text: str, checksum: str) -> list[dict[str, Any]]:
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != checksum:
        raise ValueError("backup checksum mismatch; refusing to restore")
    return json.loads(text)


def signoff(matrix_path: Path) -> dict[str, Any]:
    """Dual rating: engineering status from the matrix; market edge is never claimed (CP-0404)."""
    rows = json.loads(matrix_path.read_text(encoding="utf-8"))["rows"]
    total = len(rows)
    done = sum(1 for r in rows if r["status"] in ("implemented", "verified_existing", "not_applicable_with_evidence"))
    engineering = "ENGINEERING_TRADER_READY" if done == total else "ENGINEERING_IN_PROGRESS"
    return {
        "engineering": engineering,
        "completed_items": done,
        "total_items": total,
        "PROVEN_MARKET_EDGE": "UNKNOWN - requires genuine forward evidence; not established by this checkout",
        "status": NOT_EDGE,
        "k7_notice": K7_NOTICE,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline chart-pattern demo (synthetic data only).")
    parser.add_argument("--offline", action="store_true", help="disable network and run the demo")
    parser.add_argument("--out", type=Path, default=None, help="directory for the report and export")
    args = parser.parse_args(argv)
    if not args.offline:
        print("Refusing to run without --offline; this demo is offline-only.", file=sys.stderr)
        return 2
    deny_network()
    corpus = synthetic_corpus()
    result = run_pipeline(corpus)
    preview = paper_preview(corpus)
    summary = {
        "label": SYNTHETIC_LABEL,
        "corpus_sha256": corpus_hash(corpus),
        "formations": result["formation_count"],
        "recommendations": result["recommendations"],
        "paper_preview": preview,
        "status": NOT_EDGE,
    }
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "report.html").write_text(render_html(result["rows"], title="Chart patterns - SYNTHETIC demo"), encoding="utf-8")
        (args.out / "export.json").write_text(
            export_json(result["rows"], provenance={"corpus_sha256": summary["corpus_sha256"], "seed": str(SEED)}),
            encoding="utf-8",
        )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
