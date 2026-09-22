"""Laya: a non-autoregressive System One backend (Convai Innovations, Apache-2.0).

``system1``'s docstring reserved this slot - "jev / cloud slot in behind the same
interface when available". Laya is the open one: an encoder that answers a *typed*
question in a single forward pass, with no text generated at any point.

    choice   one of an allowed set, with a probability per option
    score    an ordinal rating, with a distribution over levels
    noul     a binary probability

Only ``choice`` is used here, because that is the shape every decision in this system
already has, and it maps onto ``Decision`` exactly.

Two things make it worth wiring in, and one thing does not.

**Speed, but not here.** Laya is quoted at 33-40 ms on a T4 and 193-464 ms on a CPU, and
the first version of this module repeated that as a reason to adopt it. Measured on this
machine (Intel Iris Xe, no CUDA, torch on 4 threads) it takes **~1.1-2.8 s** per filing -
about what ``llama3.1:8b`` takes for the same typed decision. The quoted figure was
someone else's hardware, and quoting it was the same error this project keeps catching
elsewhere: a number that flatters, taken without measuring. On a GPU the speed argument
returns; on this laptop it does not, and the case for Laya rests on the paragraph below.

**It cannot be instructed by its input.** This is the part that matters more than speed.
Every filing, headline and PDF this system reads is untrusted text, and the standing rule
(C20) is that it is framed as data and never followed. With a generative model that rule
is a *mitigation*: the model could in principle obey text inside the document, and the
wrapper is what discourages it. An encoder with no decoder has nowhere to obey *to* -
there is no continuation to hijack, only a distribution over a fixed label set chosen
before the document was read. The framing is kept anyway, because calibration is measured
under a template and must stay the one that was measured (ADR-0003), but the guarantee
underneath it is structural rather than behavioural.

**What it does not do is exempt itself from measurement.** Laya's own README is direct
about this: both checkpoints are over-confident as shipped, and refitting temperature on
held-out data moves mean ECE from 0.466 to 0.081. That is the same finding this project
made the hard way when in-sample thresholds of 89.1% became 65.4% held out. So a Laya
checkpoint enters as a *candidate*, is benchmarked on our own labelled filings, gets
per-label thresholds fitted on the fit set and judged on the holdout, and becomes a route
only if it clears the bar. Reputation is not a qualification, and neither is a benchmark
someone else ran.
"""
from __future__ import annotations

import time
from functools import lru_cache

from ..security import untrusted
from . import backends
from .system1 import Decision

DEFAULT_REPO = "convaiinnovations/laya"
MULTILINGUAL = "multilingual"
LABEL = "the filing or headline to classify"
QUESTION = "answer"


#: Two environment facts this machine needs, learned the hard way rather than read.
#:
#: ``HF_HOME`` - the checkpoint is ~800 MB and the default cache is on C:, which has
#: under 30 GB free. Same move as the Ollama store.
#: ``HF_HUB_DISABLE_SYMLINKS`` - huggingface_hub links blobs into snapshots, and Windows
#: refuses the link without Developer Mode or admin (WinError 1314). Copying instead
#: costs disk and nothing else.
ENV_HINT = ("set HF_HOME to a directory on a drive with room, and "
            "HF_HUB_DISABLE_SYMLINKS=1 on Windows (the hub links blobs into snapshots, "
            "which needs a privilege a normal account does not hold)")


@lru_cache(maxsize=4)
def agent(repo: str = DEFAULT_REPO, subfolder: str | None = None):
    """Load a checkpoint once per process. A cold build costs seconds."""
    try:
        import laya
    except ImportError as e:                    # pragma: no cover - environment
        raise backends.BackendUnavailable(
            "laya is not installed (pip install laya)") from e
    try:
        return laya.load(repo, subfolder=subfolder) if subfolder else laya.load(repo)
    except OSError as e:                        # pragma: no cover - environment
        # WinError 1314 is the symlink privilege, and its message says nothing about
        # what to do. Answer the question the error raises.
        raise backends.BackendUnavailable(
            f"laya {repo}: {e}\n  {ENV_HINT}") from e
    except Exception as e:                      # noqa: BLE001 - network, weights, shapes
        raise backends.BackendUnavailable(f"laya {repo}: {e}") from e


def question(instruction: str, choices: list[str],
             notes: dict[str, str] | None = None) -> dict:
    """The typed question, which *is* the prompt for calibration purposes.

    Laya has no prompt in the generative sense, but it does have an instruction and a
    criteria map, and changing either changes what the probabilities mean. ADR-0003
    applies unchanged: a threshold measured under one question does not describe another.
    """
    criteria = {c: (notes or {}).get(c) or c for c in choices}
    return {QUESTION: {"type": "choice", "instructions": instruction,
                       "criteria": criteria}}


def laya_decide(model: str, instruction: str, state: str, choices: list[str], *,
                notes: dict[str, str] | None = None,
                repo: str = DEFAULT_REPO, subfolder: str | None = None) -> Decision:
    """One forward pass, one label, a probability per option - the router's shape."""
    if not 2 <= len(choices) <= 20:
        # Above about 20 labels the published accuracy collapses (0.425 on Banking77's
        # 77 labels at the default head budget). Nothing here needs more than a handful,
        # so the limit is a refusal rather than a silently worse answer.
        raise ValueError("2..20 choices; Laya degrades badly on high-cardinality sets")
    ag = agent(repo, subfolder)
    t0 = time.perf_counter()
    try:
        res = ag.predict(untrusted.wrap(state, label=LABEL),
                         question(instruction, choices, notes))
    except Exception as e:                      # noqa: BLE001 - torch, memory, shapes
        raise backends.BackendUnavailable(f"laya {model}: {e}") from e

    a = (res.get("answers") or {}).get(QUESTION) or {}
    probs = {c: float(a.get("probabilities", {}).get(c, 0.0)) for c in choices}
    total = sum(probs.values())
    if total <= 0:
        probs = {c: 1 / len(choices) for c in choices}
    else:
        probs = {c: v / total for c, v in probs.items()}
    label = max(probs, key=probs.get)
    return Decision(label=label, probs=probs, confidence=probs[label], model=model,
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                    extra={"backend": "laya", "repo": repo, "subfolder": subfolder,
                           "off_menu": total <= 0,
                           "raw_confidence": a.get("confidence"),
                           # The README's own warning, carried into the record so a
                           # reader of a stored decision cannot mistake this for
                           # calibrated probability.
                           "uncalibrated": True})
