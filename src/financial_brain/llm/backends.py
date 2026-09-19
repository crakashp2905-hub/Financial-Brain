"""Model backends behind the router (ADR-0002). Each returns a ``Completion``.

``ollama``     local models over Ollama's HTTP API (stdlib only). Free; data never
               leaves the machine. Structured output is enforced with a JSON schema
               (``format``), so a label is always one of the allowed values.
``anthropic``  cloud tiers through the Anthropic SDK; only via ``gate`` and only when
               the owner has enabled cloud calls.
``finbert``    ProsusAI FinBERT (int8 ONNX, ``data/models/finbert``) - a 110 MB encoder
               for financial sentiment: positive / negative / neutral, no generation.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


class BackendUnavailable(RuntimeError):
    pass


@dataclass
class Completion:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    parsed: dict | None = None
    extra: dict = field(default_factory=dict)


def ollama_models() -> set[str]:
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=3) as r:
            return {m["name"] for m in json.load(r)["models"]}
    except (urllib.error.URLError, OSError):
        return set()


def ollama(model: str, system: str, prompt: str, *, schema: dict | None = None,
           max_tokens: int = 512, temperature: float = 0.0, timeout: int = 600) -> Completion:
    body = {"model": model, "stream": False, "think": False,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "options": {"temperature": temperature, "num_predict": max_tokens, "seed": 7}}
    if schema:
        body["format"] = schema
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 400 and body.get("think") is False:
            body.pop("think")                  # models without a thinking switch
            return _retry(req.full_url, body, model, t0, timeout, schema)
        raise BackendUnavailable(f"ollama {model}: HTTP {e.code} {e.read()[:200]!r}") from e
    except (urllib.error.URLError, OSError) as e:
        raise BackendUnavailable(f"ollama not reachable at {OLLAMA}: {e}") from e
    return _completion(out, model, t0, schema)


def _retry(url, body, model, t0, timeout, schema):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return _completion(json.load(r), model, t0, schema)


def _completion(out: dict, model: str, t0: float, schema) -> Completion:
    text = out.get("message", {}).get("content", "")
    parsed = None
    if schema:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
    return Completion(text=text, model=model, input_tokens=out.get("prompt_eval_count", 0),
                      output_tokens=out.get("eval_count", 0),
                      latency_ms=int((time.perf_counter() - t0) * 1000), parsed=parsed)


def anthropic(model: str, system: str, prompt: str, *, schema: dict | None = None,
              max_tokens: int = 4000) -> Completion:
    try:
        import anthropic as sdk
    except ImportError as e:
        raise BackendUnavailable("the anthropic package is not installed") from e
    client = sdk.Anthropic()
    t0 = time.perf_counter()
    kwargs = {}
    if schema:
        kwargs["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
    try:
        r = client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                   messages=[{"role": "user", "content": prompt}], **kwargs)
    except sdk.AuthenticationError as e:
        raise BackendUnavailable("no usable Anthropic credentials") from e
    if r.stop_reason == "refusal":
        raise BackendUnavailable("the model declined this request")
    text = "".join(b.text for b in r.content if b.type == "text")
    parsed = None
    if schema:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
    return Completion(text=text, model=r.model, input_tokens=r.usage.input_tokens,
                      output_tokens=r.usage.output_tokens,
                      latency_ms=int((time.perf_counter() - t0) * 1000), parsed=parsed)


class FinBERT:
    """ProsusAI FinBERT sentiment on onnxruntime. Loads once; ~20 ms per sentence."""
    LABELS = ("positive", "negative", "neutral")

    def __init__(self, root: Path):
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as e:
            raise BackendUnavailable("pip install '.[models]' for FinBERT") from e
        if not (root / "model_int8.onnx").exists():
            raise BackendUnavailable(f"FinBERT not found in {root}")
        self.np = np
        self.tok = Tokenizer.from_file(str(root / "tokenizer.json"))
        self.tok.enable_truncation(max_length=256)
        self.sess = ort.InferenceSession(str(root / "model_int8.onnx"),
                                         providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}

    def __call__(self, text: str) -> Completion:
        np = self.np
        t0 = time.perf_counter()
        enc = self.tok.encode(text)
        feed = {"input_ids": np.array([enc.ids], dtype=np.int64),
                "attention_mask": np.array([enc.attention_mask], dtype=np.int64)}
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.array([enc.type_ids], dtype=np.int64)
        logits = self.sess.run(None, feed)[0][0]
        p = np.exp(logits - logits.max())
        p = p / p.sum()
        i = int(p.argmax())
        return Completion(text=self.LABELS[i], model="finbert", input_tokens=len(enc.ids),
                          latency_ms=int((time.perf_counter() - t0) * 1000),
                          parsed={"label": self.LABELS[i], "confidence": float(p[i])})
