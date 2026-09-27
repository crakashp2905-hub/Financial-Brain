"""The Thesis Adversary: a mandatory gate whose job is to be right that the thesis is wrong.

A bull researcher and a bear researcher produce two opinions, and a chair picks one. That is not
adversarial review, it is a vote with extra steps - the bear was assigned its side before it
looked, so its argument is advocacy and everybody discounts it accordingly.

The adversary is different in one specific way: it is not asked for *a* counter-argument, it is
asked for **the strongest reason this thesis fails**, and the decision cannot advance until one
is on the record. The question is not "what could go wrong" - every thesis survives that - but
"what is the most likely way this specific reasoning is mistaken, and what observation would
show it".

## Structure is enforced here; content is not

The content comes from a model, and this module cannot tell a profound objection from a fluent
one. What it *can* do is refuse the shapes an empty objection takes, and those are enumerable:

    generic          "macro conditions could deteriorate" - true of every long ever taken
    unfalsifiable    no observation named, so it can never be resolved either way
    uncited          an assertion about the company with no evidence behind it
    restated         the thesis's own primary uncertainty, handed back as an objection
    agreeable        a "risk" the thesis already accounts for, which is not an objection

Each is a deterministic check. A model that wants to pass this gate has to name something
specific, cite something, and say what would settle it - and a model that does that has done the
work whether or not it meant to.

## Why a mechanism, and why it must be different from the thesis's

The most valuable field is ``mechanism``: *how* the thesis breaks, not *that* it might. And it is
checked against the thesis's own stated ``primary_uncertainty``, because an adversary that
returns the uncertainty the author already identified has added nothing. The author already knew
that. The gate is for what the author did not think of.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Phrases that are true of nearly every equity long and therefore distinguish nothing. Matched
#: as whole phrases, and a challenge is rejected only when it is *mostly* made of them.
GENERIC = (
    "macro", "market conditions", "market could", "economic slowdown", "geopolitical",
    "interest rates could", "sentiment could", "volatility could", "recession",
    "competition could", "execution risk", "regulatory risk", "unforeseen",
    "broader market", "global factors", "black swan",
)
#: A challenge has to name something checkable. These are the shapes that do.
FALSIFIABLE = (
    "if ", "unless ", "when ", "would show", "would confirm", "would disconfirm",
    "watch for", "look for", "observable", "reported", "filing", "disclose",
    "below", "above", "exceeds", "falls", "misses", "guidance", "margin",
)
#: Minimum words. Short enough to allow a genuinely crisp objection, long enough that a
#: three-word placeholder cannot pass.
MIN_WORDS = 12
#: Share of a challenge's words that may come from the GENERIC list before it reads as boilerplate.
MAX_GENERIC_SHARE = 0.25
#: Token overlap with the thesis's own primary_uncertainty above which the challenge is a
#: restatement of what the author already flagged.
MAX_RESTATEMENT_OVERLAP = 0.60


@dataclass
class Challenge:
    """The adversary's output. Typed, because prose cannot be gated."""
    #: How the thesis breaks - the causal story, not the outcome.
    mechanism: str
    #: What would be observed if the challenge is right. This is what makes it resolvable.
    observable: str
    #: Evidence ids supporting the challenge. An assertion about a company with nothing behind
    #: it is a hunch, and the thesis it attacks had to cite; so does this.
    evidence: list[str] = field(default_factory=list)
    #: The adversary's own estimate that the thesis fails this way, in [0, 1]. Recorded and
    #: never used to size anything - it is scored later against what happened
    #: (``decisions/quality.py``), which is the only way a probability earns its keep.
    probability: float = 0.0
    author: str = "agent:adversary"


@dataclass
class Review:
    passed: bool
    failures: list[str] = field(default_factory=list)
    measured: dict = field(default_factory=dict)

    def describe(self) -> str:
        return ("challenge accepted" if self.passed
                else "challenge rejected: " + "; ".join(self.failures))


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9%.]+", (text or "").lower()) if len(w) > 2]


def _generic_share(text: str) -> float:
    low = (text or "").lower()
    hits = sum(len(_words(g)) for g in GENERIC if g in low)
    total = len(_words(text)) or 1
    return min(1.0, hits / total)


def _overlap(a: str, b: str) -> float:
    wa, wb = set(_words(a)), set(_words(b))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def review(challenge: Challenge, *, thesis: str, primary_uncertainty: str,
           invalidation_conditions: list[str] | None = None) -> Review:
    """Check the challenge is an objection rather than the shape of one."""
    r = Review(passed=True)
    body = f"{challenge.mechanism} {challenge.observable}"
    nwords = len(_words(body))
    gshare = _generic_share(body)
    falsifiable = any(k in body.lower() for k in FALSIFIABLE)
    restate = _overlap(challenge.mechanism, primary_uncertainty)
    echo = _overlap(challenge.mechanism, thesis)

    r.measured = {"words": nwords, "generic_share": gshare,
                  "falsifiable": falsifiable, "restatement_overlap": restate,
                  "thesis_overlap": echo, "evidence_count": len(challenge.evidence)}

    if nwords < MIN_WORDS:
        r.failures.append(
            f"{nwords} words: too short to name a mechanism (minimum {MIN_WORDS})")
    if gshare > MAX_GENERIC_SHARE:
        r.failures.append(
            f"{gshare:.0%} of the challenge is boilerplate risk language that is true of "
            f"every equity long and therefore distinguishes nothing")
    if not falsifiable:
        r.failures.append(
            "nothing observable is named, so the challenge can never be resolved either "
            "way - an objection that cannot be settled is not an objection")
    if not challenge.evidence:
        r.failures.append(
            "no evidence cited; the thesis had to cite and so does the challenge")
    if restate > MAX_RESTATEMENT_OVERLAP:
        r.failures.append(
            f"{restate:.0%} overlap with the thesis's own stated primary uncertainty - the "
            f"author already knew this, and the gate exists for what they did not")
    if not 0.0 <= challenge.probability <= 1.0:
        r.failures.append(f"probability {challenge.probability} outside [0, 1]")

    # Already-accounted-for: the challenge names exactly an invalidation condition the thesis
    # itself listed. The author has pre-committed to exiting on it, so it is not an objection to
    # the thesis, it is part of it.
    for cond in invalidation_conditions or []:
        if _overlap(challenge.mechanism, cond) > MAX_RESTATEMENT_OVERLAP:
            r.failures.append(
                f"the challenge restates an invalidation condition the thesis already "
                f"commits to ({cond[:60]!r}), which the thesis handles by design")
            break

    r.passed = not r.failures
    return r


def gate(con, decision_id: str, challenge: Challenge, *, content: dict) -> Review:
    """Review the challenge and record it against the decision, pass or fail.

    A rejected challenge is stored too. A decision whose adversary needed four attempts is a
    different object from one whose adversary got it right first time, and only keeping the
    failures makes that visible later.
    """
    r = review(challenge, thesis=content.get("thesis", ""),
               primary_uncertainty=content.get("primary_uncertainty", ""),
               invalidation_conditions=content.get("invalidation_conditions") or [])
    con.execute("""CREATE TABLE IF NOT EXISTS thesis_challenges (
        decision_id VARCHAR, seq INTEGER, mechanism VARCHAR, observable VARCHAR,
        evidence VARCHAR, probability DOUBLE, author VARCHAR,
        passed BOOLEAN, failures VARCHAR, measured VARCHAR, reviewed_at TIMESTAMP)""")
    seq = con.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM thesis_challenges "
                      "WHERE decision_id = ?", [decision_id]).fetchone()[0]
    import json
    from datetime import datetime, timezone
    con.execute("""INSERT INTO thesis_challenges VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [decision_id, seq, challenge.mechanism, challenge.observable,
                 json.dumps(challenge.evidence), challenge.probability, challenge.author,
                 r.passed, json.dumps(r.failures), json.dumps(r.measured),
                 datetime.now(timezone.utc)])
    return r


def attempts(con, decision_id: str) -> list[dict]:
    """Every challenge made against a decision, in order, with its verdict."""
    if not con.execute("""SELECT COUNT(*) FROM duckdb_tables()
                          WHERE table_name = 'thesis_challenges'""").fetchone()[0]:
        return []
    rows = con.execute("""SELECT seq, mechanism, observable, probability, author, passed,
                          failures FROM thesis_challenges WHERE decision_id = ?
                          ORDER BY seq""", [decision_id]).fetchall()
    import json
    return [{"seq": r[0], "mechanism": r[1], "observable": r[2], "probability": r[3],
             "author": r[4], "passed": r[5], "failures": json.loads(r[6])} for r in rows]
