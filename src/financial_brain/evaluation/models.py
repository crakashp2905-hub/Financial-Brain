"""Financial-Brain model benchmark: choose models by measurement, not reputation.

Every candidate - rules, FinBERT, each local Ollama model, later cloud models and Jev -
answers the same labelled tasks through the same typed ``decide`` interface. For each
(task, model) we record:

* accuracy and macro-F1 (macro, because "neutral" dominates and a model that always says
  neutral would otherwise look good);
* mean latency - on *this* machine, which is what the router pays;
* a **calibrated acceptance threshold**: the lowest confidence ``t`` at which the answers
  the model gives with confidence >= t are right at least ``target`` of the time, and
  the share of inputs it can then answer on its own (coverage). A model whose
  confidences carry no information gets no usable threshold, however accurate it is on
  average - the router cannot tell its good answers from its bad ones.

Results go to ``model_bench`` (append-only); the router reads the latest row per pair.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..events.classify import classify
from ..llm import backends, system1

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"

SENTIMENT_CHOICES = ["positive", "negative", "neutral"]
SENTIMENT_NOTES = {"positive": "states a favourable fact for shareholders (order won, "
                               "upgrade, profit growth, dividend)",
                   "negative": "states an adverse fact (auditor resigned, tax demand, "
                               "penalty, default, key executive quit)",
                   "neutral": "routine, procedural, or no stated direction"}


def _rows(task: str) -> list[dict]:
    if task == "sentiment":
        return [{"state": r["headline"], "gold": r["gold"]} for r in
                json.loads((FIXTURES / "sentiment_labels.json").read_text("utf-8"))["rows"]]
    if task == "event_type":
        out = []
        for f in ("announcement_labels.json", "announcement_labels_holdout.json"):
            for r in json.loads((FIXTURES / f).read_text("utf-8"))["rows"]:
                out.append({"state": f"BSE subcategory: {r['subcategory']}\n"
                                     f"Subject: {r['subject']}\nHeadline: {r['headline']}",
                            "gold": r["gold_type"], "raw": r})
        return out
    raise ValueError(f"unknown task {task}")


def choices(task: str) -> list[str]:
    if task == "sentiment":
        return SENTIMENT_CHOICES
    return sorted({r["gold"] for r in _rows(task)})


INSTRUCTION = {
    "sentiment": "Classify this Indian stock-exchange filing by its likely effect on the "
                 "company's shareholders, judging only from what the text states.",
    "event_type": "Classify this Indian stock-exchange (BSE) announcement into the event "
                  "type that best describes what happened.",
}


def decider(task: str, model: str):
    """A function state -> Decision for this (task, model), or raise if unsupported."""
    ch = choices(task)
    if model == "rules":
        if task != "event_type":
            raise ValueError("rules only exist for event_type")

        def rules(row):
            r = row["raw"]
            kind, _, rule = classify(r["category"], r["subcategory"], r["headline"],
                                     r["subject"])
            conf = 0.5 if rule in ("no rule matched", "subcategory General") else 1.0
            return system1.Decision(label=kind, probs={}, confidence=conf, model="rules")
        return rules
    if model == "finbert":
        if task != "sentiment":
            raise ValueError("finbert only does sentiment")
        from ..config import load
        fb = backends.FinBERT(load().data_root / "models" / "finbert")
        return lambda row: system1.finbert_decide(fb, row["state"])
    notes = SENTIMENT_NOTES if task == "sentiment" else None
    return lambda row: system1.ollama_decide(model, INSTRUCTION[task], row["state"], ch,
                                             notes=notes)


def score(golds: list[str], decisions: list, target: float) -> dict:
    labels = sorted(set(golds) | {d.label for d in decisions})
    correct = [d.label == g for d, g in zip(decisions, golds)]
    f1s = []
    for lab in set(golds):
        tp = sum(1 for d, g in zip(decisions, golds) if d.label == lab == g)
        fp = sum(1 for d, g in zip(decisions, golds) if d.label == lab != g)
        fn = sum(1 for d, g in zip(decisions, golds) if g == lab != d.label)
        f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    threshold, coverage = None, 0.0
    for t in sorted({round(d.confidence, 4) for d in decisions}):
        kept = [c for d, c in zip(decisions, correct) if d.confidence >= t]
        if kept and sum(kept) / len(kept) >= target:
            threshold, coverage = t, len(kept) / len(decisions)
            break
    confusion = Counter((g, d.label) for d, g in zip(decisions, golds) if d.label != g)
    return {"n": len(golds), "accuracy": sum(correct) / len(golds),
            "macro_f1": sum(f1s) / len(f1s), "threshold": threshold, "coverage": coverage,
            "target": target, "labels": labels,
            "top_confusions": [f"{g} -> {p} x{n}" for (g, p), n in confusion.most_common(5)]}


def bench(con, task: str, model: str, *, target: float = 0.9, limit: int | None = None,
          record: bool = True) -> dict:
    rows = _rows(task)[:limit] if limit else _rows(task)
    decide = decider(task, model)
    decisions = [decide(r) for r in rows]
    out = score([r["gold"] for r in rows], decisions, target)
    out.update(task=task, model=model,
               latency_ms=sum(d.latency_ms for d in decisions) / len(decisions))
    if record:
        con.execute("""INSERT INTO model_bench (run_at, task, model, n, accuracy, macro_f1,
                       latency_ms, target, threshold, coverage, detail)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    [datetime.now(timezone.utc), task, model, out["n"], out["accuracy"],
                     out["macro_f1"], out["latency_ms"], target, out["threshold"],
                     out["coverage"], json.dumps(out)])
    return out
