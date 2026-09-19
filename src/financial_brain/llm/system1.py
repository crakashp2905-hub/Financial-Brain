"""Typed decisions with probabilities: a local "System One" layer (ADR-0002).

TypeSafe's Jev (Sept 2026) popularised the idea: software does not want prose, it wants
*one of an allowed set of answers* with a probability it can act on. This module gives
every backend that shape:

    decide(state, choices) -> Decision(label, probs, confidence, model, latency, cost)

* **rules** (Tier 0) - a deterministic function; confidence 1.0 when a specific rule
  fired, lower when it fell through to a catch-all.
* **finbert** (Tier 1) - softmax over its three sentiment classes.
* **ollama** (Tiers 1-2) - the choices are shown as single-letter options; the model
  emits one token and Ollama's top log-probabilities over the option letters,
  renormalised to the allowed set, are the distribution. One forward pass, no text to
  parse, never an answer outside the set.
* **jev / cloud** - slot in behind the same interface when available.

Raw model probabilities are not calibrated. The router never trusts them as-is: the
model benchmark measures, per task and model, the confidence above which answers were
right often enough, and only that threshold is used to accept or escalate.
"""
from __future__ import annotations

import json
import math
import string
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import backends

KEYS = string.ascii_uppercase + string.ascii_lowercase          # up to 52 choices


@dataclass
class Decision:
    label: str
    probs: dict[str, float]
    confidence: float
    model: str
    latency_ms: int = 0
    input_tokens: int = 0
    extra: dict = field(default_factory=dict)


def _prompt(instruction: str, state: str, choices: list[str],
            notes: dict[str, str] | None) -> str:
    lines = [f"{KEYS[i]}. {c}" + (f" - {notes[c]}" if notes and notes.get(c) else "")
             for i, c in enumerate(choices)]
    return (f"{instruction}\n\nOptions:\n" + "\n".join(lines) +
            f"\n\nInput:\n{state}\n\nAnswer with the single option letter only.")


def _word_prompt(instruction: str, state: str, choices: list[str]) -> str:
    return (f"{instruction}\n\nInput:\n{state}\n\nAnswer with exactly one word from: "
            + ", ".join(choices) + ".")


def _word_key(tok: str, choices: list[str]) -> str | None:
    """The choice a first token begins, if it begins exactly one of them."""
    t = tok.strip().lower()
    if not t:
        return None
    hits = [c for c in choices if c.lower().startswith(t)]
    return hits[0] if len(hits) == 1 else None


def ollama_decide(model: str, instruction: str, state: str, choices: list[str], *,
                  notes: dict[str, str] | None = None, timeout: int = 600,
                  style: str = "letters") -> Decision:
    """``style="letters"`` shows lettered options (general models); ``"words"`` asks for
    the choice word itself (specialists fine-tuned to answer "positive", "negative"...)."""
    if not 2 <= len(choices) <= len(KEYS):
        raise ValueError(f"2..{len(KEYS)} choices")
    keys = KEYS[:len(choices)]
    words = style == "words"
    body = {"model": model, "stream": False, "logprobs": True, "top_logprobs": 20,
            "think": False,                    # thinking models must answer, not muse
            "messages": [{"role": "system", "content": "You are a precise classifier."},
                         {"role": "user", "content":
                          _word_prompt(instruction, state, choices) if words else
                          _prompt(instruction, state, choices, notes)}],
            # num_ctx: a typed decision prompt is < 1k tokens. Ollama otherwise sizes the
            # KV cache for its default 16k context (~2 GB), which fails to allocate on a
            # 16 GB laptop and forces needless reloads.
            "options": {"temperature": 0, "num_predict": 1, "seed": 7, "num_ctx": 2048}}
    t0 = time.perf_counter()
    try:
        out = _post(body, timeout)
    except urllib.error.HTTPError as e:
        if e.code != 400:
            raise backends.BackendUnavailable(f"ollama {model}: {e}") from e
        body.pop("think")                      # model has no thinking switch
        out = _post(body, timeout)
    except OSError as e:
        raise backends.BackendUnavailable(f"ollama {model}: {e}") from e
    mass = {c: 0.0 for c in choices}
    lp = (out.get("logprobs") or [{}])[0]
    for cand in lp.get("top_logprobs") or [lp] if lp else []:
        tok = (cand.get("token") or "").strip().rstrip(".):")
        c = _word_key(tok, choices) if words else (
            choices[keys.index(tok)] if tok in keys else None)
        if c:
            mass[c] += math.exp(cand["logprob"])
    total = sum(mass.values())
    if total <= 0:                             # the model answered off-menu
        probs = {c: 1 / len(choices) for c in choices}
    else:
        probs = {c: v / total for c, v in mass.items()}
    label = max(probs, key=probs.get)
    return Decision(label=label, probs=probs, confidence=probs[label], model=model,
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                    input_tokens=out.get("prompt_eval_count", 0),
                    extra={"off_menu": total <= 0, "captured_mass": total})


def _post(body: dict, timeout: int) -> dict:
    req = urllib.request.Request(f"{backends.OLLAMA}/api/chat",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def finbert_decide(fb: backends.FinBERT, state: str) -> Decision:
    c = fb(state)
    return Decision(label=c.parsed["label"], probs={}, confidence=c.parsed["confidence"],
                    model="finbert", latency_ms=c.latency_ms, input_tokens=c.input_tokens)
