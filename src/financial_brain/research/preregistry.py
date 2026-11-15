"""Proof that a pre-registration was written before its result and never changed after it.

Every test in this project is written down first, as ``brain/07-Research/PRE-REGISTERED <name>.md``, committed, and
only then run; the result is a separate note that links back to it (``[[PRE-REGISTERED <name>]]``). Git already
holds the evidence that this order was kept. This module reads it:

    registered      the commit that first added the pre-registration, and the hash of its text then
    result          the first commit of every result note that links to it - except a note the registration
                    itself cites, as first committed, and that already existed then: that is its declared origin
                    (a filter written after reading a result, saying so), not its result
    edits           every later commit that changed the pre-registration

and gives each a status:

    NO_RESULT_YET           registered, not yet run
    OK                      registered before its result, unchanged since
    AMENDED_BEFORE_RESULT   edited, but every edit predates the first result (allowed: an amendment written
                            before anything was computed, which the note must say)
    RESULT_BEFORE_REGISTRATION   a result was committed before the hypothesis - the order was broken
    CHANGED_AFTER_RESULT    the hypothesis was edited after its result existed - the cardinal violation

A changed hypothesis is a new one: it gets a new file and a new name, never an edit.

Editorial repair (ADR-0004 amendment 6): an edit whose only change is the whitespace inside a ``[[wiki-link]]`` (for
example a line break that splits a link target, which makes the link broken) is EDITORIAL, not a change to the
hypothesis. It is reported under ``editorial`` and does not count as an edit. Any other change still does.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

RESEARCH = Path("brain") / "07-Research"
VIOLATIONS = {"RESULT_BEFORE_REGISTRATION", "CHANGED_AFTER_RESULT"}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True,
                          encoding="utf-8").stdout


def _commits(repo: Path, rel: str) -> list[tuple[str, str]]:
    """(hash, ISO commit time) oldest first, following renames."""
    out = _git(repo, "log", "--follow", "--format=%H %cI", "--", rel)
    rows = [ln.split(" ", 1) for ln in out.splitlines() if ln.strip()]
    return [(h, t) for h, t in reversed(rows)]


def _sha(repo: Path, commit: str, rel: str) -> str:
    text = _git(repo, "show", f"{commit}:{rel}")
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


_WIKILINK = re.compile(r"\[\[[^\]]*\]\]")


def _editorial_only(old: str, new: str) -> bool:
    """True when ``new`` differs from ``old`` only by whitespace inside wiki-links (and is not identical)."""
    old, new = old.replace("\r\n", "\n"), new.replace("\r\n", "\n")

    def squash(t: str) -> str:
        return _WIKILINK.sub(lambda m: " ".join(m.group(0).split()), t)

    return old != new and squash(old) == squash(new)


def audit(repo: Path = Path(".")) -> list[dict]:
    repo = Path(repo)
    folder = repo / RESEARCH
    notes = {p.name: p.read_text(encoding="utf-8", errors="replace") for p in folder.glob("*.md")}
    out: list[dict] = []
    for name in sorted(n for n in notes if n.startswith("PRE-REGISTERED")):
        rel = (RESEARCH / name).as_posix()
        commits = _commits(repo, rel)
        if not commits:
            out.append({"prereg": name, "status": "UNCOMMITTED", "registered": None, "results": [], "edits": [], "editorial": []})
            continue
        reg_hash, reg_time = commits[0]
        stem = re.escape(name[:-3])
        linked = [n for n, txt in notes.items() if n != name and not n.startswith("PRE-REGISTERED")
                  and re.search(r"\[\[" + stem + r"(\|[^\]]*)?\]\]", txt)]
        first_text = _git(repo, "show", f"{reg_hash}:{rel}")
        results, origins = [], []
        for n in linked:
            c = _commits(repo, (RESEARCH / n).as_posix())
            if not c:
                continue
            cited = re.search(r"\[\[" + re.escape(n[:-3]) + r"(\|[^\]]*)?\]\]", first_text)
            if cited and c[0][1] < reg_time:
                origins.append(n)
                continue
            results.append({"note": n, "commit": c[0][0], "time": c[0][1]})
        edits: list[dict] = []
        editorial: list[dict] = []
        prev_text = first_text
        for h, t in commits[1:]:
            text = _git(repo, "show", f"{h}:{rel}")
            if _sha(repo, h, rel) != _sha(repo, reg_hash, rel):
                # Each edit is judged against the version just before it, so a later editorial repair is not
                # held to the original text; a hypothesis change is caught at the commit that made it.
                (editorial if _editorial_only(prev_text, text) else edits).append({"commit": h, "time": t})
            prev_text = text
        first_result = min((r["time"] for r in results), default="")
        if not results:
            status = "NO_RESULT_YET"
        elif first_result < reg_time:
            status = "RESULT_BEFORE_REGISTRATION"
        elif any(e["time"] > first_result for e in edits):
            status = "CHANGED_AFTER_RESULT"
        elif edits:
            status = "AMENDED_BEFORE_RESULT"
        else:
            status = "OK"
        out.append({"prereg": name, "status": status, "registered": reg_time, "registered_commit": reg_hash[:8],
                    "sha256": _sha(repo, reg_hash, rel)[:16], "results": results, "origins": origins,
                    "edits": edits, "editorial": editorial})
    return out


def render(rows: list[dict]) -> str:
    lines = [f"{'pre-registration':62s} {'registered':20s} {'status':26s} result"]
    for r in rows:
        res = r["results"][0]["note"][:40] if r["results"] else "-"
        lines.append(f"{r['prereg'][15:-3][:62]:62s} {(r['registered'] or '-')[:19]:20s} {r['status']:26s} {res}")
    bad = [r for r in rows if r["status"] in VIOLATIONS]
    lines.append(f"\n{len(rows)} pre-registrations; {len(bad)} violation(s)")
    return "\n".join(lines)
