"""Read-only trader research workstation over saved, authorized artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .diagnostics import calibration_report, selective_performance
from .ensemble_walkforward import compare_ensembles, evaluate_ensemble_paper
from .inverse_strategy import import_history, regime_report


def compare_reports(paths: list[Path]) -> list[dict]:
    """Compare action experiments without selecting a winner on final holdout."""
    rows = []
    for path in paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        strategy = report["strategy"]
        rows.append({"artifact": str(path), "model": strategy["model"],
                     "validation_macro_f1": strategy["validation_action_metrics"]["macro_f1"],
                     "holdout_macro_f1_descriptive": strategy["independent_action_holdout"]["macro_f1"],
                     "majority_holdout_accuracy": strategy["majority_action_baseline"]["holdout_accuracy"],
                     "rule_fidelity": strategy["rule_fidelity"], "health": strategy["health"]})
    return sorted(rows, key=lambda row: row["validation_macro_f1"], reverse=True)


def _boolean_values(series: pd.Series) -> np.ndarray:
    values = series.astype(str).str.strip().str.lower()
    if not values.isin({"true", "false", "1", "0"}).all():
        raise ValueError("correct must contain only true/false or 1/0")
    return values.isin({"true", "1"}).to_numpy(bool)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fb inverse-tool", description="Read-only research analysis")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("compare", help="compare saved inference reports")
    p.add_argument("reports", nargs="+", type=Path)
    p = sub.add_parser("rules", help="inspect a versioned rule specification")
    p.add_argument("spec", type=Path)
    p = sub.add_parser("regime", help="descriptive as-of regime outcomes")
    p.add_argument("history", type=Path)
    p.add_argument("--authorized", action="store_true")
    p = sub.add_parser("ensemble", help="prequential validated-strategy comparison")
    p.add_argument("panel", type=Path)
    p.add_argument("--strategies", nargs="+", required=True)
    p.add_argument("--validated", nargs="+", required=True)
    p.add_argument("--min-train", type=int, default=60)
    p.add_argument("--paper", action="store_true", help="include fixed-threshold after-cost paper metrics")
    p.add_argument("--min-expected-alpha", type=float, default=0.0)
    p = sub.add_parser("selective", help="completed paper coverage and cost curve")
    p.add_argument("paper", type=Path)
    p = sub.add_parser("execution", help="minute-bar execution comparison")
    p.add_argument("minute_bars", type=Path)
    p.add_argument("--qty", type=float, required=True)
    p.add_argument("--fee-bp", type=float, required=True)
    p.add_argument("--sigma-d", type=float, required=True)
    p.add_argument("--half-spread-bp", type=float, required=True)
    p = sub.add_parser("counterfactual", help="read stored as-of decision context")
    p.add_argument("context_id")
    args = parser.parse_args(argv)
    if args.command == "compare":
        output = {"experiments": compare_reports(args.reports),
                  "warning": "Never select a policy using final-holdout performance."}
    elif args.command == "rules":
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        output = {key: spec[key] for key in ("version_hash", "source_model", "validation_fidelity",
                                             "features", "rules", "limitations")}
    elif args.command == "regime":
        output = regime_report(import_history(args.history, authorized=args.authorized))
    elif args.command == "ensemble":
        panel = pd.read_csv(args.panel)
        rows = compare_ensembles(panel, tuple(args.strategies), validated=set(args.validated),
                                 min_train=args.min_train)
        if rows.empty:
            output = {"status": "insufficient_completed_history"}
        else:
            rows["squared_error"] = (rows.forecast - rows.realized) ** 2
            output = {"mse_by_policy": rows.groupby("policy").squared_error.mean().to_dict(),
                      "first_forecast": str(rows.decision_ts.min()), "n": len(rows) // 4,
                      "warning": "Forecast error is not trading alpha; candidates must be independently validated."}
            if args.paper:
                output["paper"] = evaluate_ensemble_paper(
                    panel, tuple(args.strategies), validated=set(args.validated),
                    min_train=args.min_train, min_expected_alpha=args.min_expected_alpha)
    elif args.command == "selective":
        paper = pd.read_csv(args.paper)
        paper["correct"] = _boolean_values(paper.correct)
        output = {"calibration": calibration_report(paper.reliability.to_numpy(float),
                                                    paper.correct.to_numpy(bool)),
                  "selective": selective_performance(paper)}
    elif args.command == "execution":
        from ..execution.algos import Day, Order
        from .execution_compare import compare_execution
        bars = pd.read_csv(args.minute_bars)
        required = {"minute", "open", "high", "low", "close", "volume", "expected_volume_share"}
        if not required <= set(bars):
            raise ValueError(f"missing minute-bar columns: {sorted(required - set(bars))}")
        day = Day(*(bars[column].to_numpy(float) for column in
                    ("minute", "open", "high", "low", "close", "volume", "expected_volume_share")),
                  args.sigma_d, args.half_spread_bp / 1e4)
        output = compare_execution(day, Order(args.qty, 0, len(bars) - 1), fee_bp=args.fee_bp)
    else:
        from ..config import load
        from ..storage.db import Database
        from .counterfactual_store import get_context
        with Database(load(), read_only=True).connect() as con:
            output = get_context(con, args.context_id)
    print(json.dumps(output, indent=2, default=lambda value: value.item() if isinstance(value, np.generic)
                     else str(value)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
