"""The one door through which any language model is called (C11 / C16 groundwork).

Off by default. A call happens only when the owner has turned it on
(``FB_LLM_ENABLED=1``) *and* the Anthropic SDK can find credentials - which it reads
from the environment or an ``ant auth login`` profile on its own. This module never
reads, stores, logs or asks for a key.

Rules enforced here rather than hoped for:

* **Purposes are whitelisted.** A model may summarise, extract or argue a side over
  cited evidence. It may not predict prices or returns - ``PURPOSES`` has no such entry
  and an unknown purpose is refused before any network call.
* **Every call is recorded** in ``llm_calls``: purpose, model, hashes of prompt and
  output, token counts, the evidence it was given. Prompts are hashed, not stored, so
  the ledger can prove what was sent without becoming a second copy of it.
* **Output is evidence of the lowest tier.** Callers mint it with
  ``derivation="llm:<model>"`` and ``inputs`` = the cited evidence; it can support a
  decision's argument, never stand in for a Tier-1 fact.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone

MODEL = "claude-opus-5"
PURPOSES = {"summarise", "extract", "argue_for", "argue_against"}


class LLMUnavailable(RuntimeError):
    """The gate is closed: not enabled, SDK missing, or no credentials."""


@dataclass
class Reply:
    text: str
    model: str
    input_tokens: int
    output_tokens: int


def enabled() -> bool:
    return os.environ.get("FB_LLM_ENABLED") == "1"


def _sdk_call(system: str, prompt: str, max_tokens: int) -> Reply:
    try:
        import anthropic
    except ImportError as e:                       # optional dependency
        raise LLMUnavailable("the anthropic package is not installed") from e
    client = anthropic.Anthropic()                 # credentials resolved by the SDK
    try:
        # Server-side fallback: on a policy decline the API re-runs the request on a
        # fallback model inside the same call.
        r = client.beta.messages.create(
            model=MODEL, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": prompt}],
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    except anthropic.AuthenticationError as e:
        raise LLMUnavailable("no usable Anthropic credentials") from e
    if r.stop_reason == "refusal":
        raise LLMUnavailable("the model declined this request")
    text = "".join(b.text for b in r.content if b.type == "text")
    return Reply(text, r.model, r.usage.input_tokens, r.usage.output_tokens)


def call(con, *, purpose: str, system: str, prompt: str, evidence: list[str],
         max_tokens: int = 4000, transport=None) -> Reply:
    """Make one gated, recorded call. ``transport`` replaces the SDK in tests."""
    if purpose not in PURPOSES:
        raise ValueError(f"purpose {purpose!r} is not allowed; allowed: {sorted(PURPOSES)}")
    if transport is None:
        if not enabled():
            raise LLMUnavailable("LLM calls are off (set FB_LLM_ENABLED=1 to allow)")
        transport = _sdk_call
    reply = transport(system, prompt, max_tokens)
    sha = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731
    con.execute("""INSERT INTO llm_calls (called_at, purpose, model, prompt_sha256,
                   output_sha256, input_tokens, output_tokens, evidence)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [datetime.now(timezone.utc), purpose, reply.model, sha(system + "\n" + prompt),
                 sha(reply.text), reply.input_tokens, reply.output_tokens, evidence])
    return reply
