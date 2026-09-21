"""Export the Obsidian vault as a link graph.

The vault in ``brain/`` is the system's own documentation, and its wiki-links are a real
graph: what a note depends on, what a decision touches, which component a data source
feeds. This turns that into JSON so it can be drawn - node size by how often a note is
linked, colour by folder - without Obsidian.

Only the vault's own text is read. The summary of a note is its first ordinary line,
which is how these notes are written: frontmatter, a heading, then one sentence saying
what the note is for.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SKIP_PREFIX = ("#", ">", "|", "-", "*", "[", "```", "!")
FRONTMATTER = re.compile(r"^---.*?---", re.S)
WIKILINK = re.compile(r"\[\[([^\]|#]+)")


def summarise(text: str, limit: int = 220) -> str:
    body = FRONTMATTER.sub("", text)
    for line in body.splitlines():
        s = line.strip()
        if s and not s.startswith(SKIP_PREFIX):
            return re.sub(r"[*`\[\]]", "", s)[:limit]
    return ""


def graph(root: Path) -> dict:
    """{"n": [{i, f, s, d}], "l": [[source, target]]} - ids are note names, as Obsidian
    links them."""
    notes: dict[str, dict] = {}
    edges: list[tuple[str, str]] = []
    for f in sorted(root.rglob("*.md")):
        text = f.read_text(encoding="utf-8")
        folder = f.parent.relative_to(root).parts[0] if f.parent != root else "root"
        notes[f.stem] = {"i": f.stem, "f": folder, "s": summarise(text), "d": 0}
        edges += [(f.stem, t.strip()) for t in WIKILINK.findall(text)]

    links = [[s, t] for s, t in edges if t in notes and s != t]
    for s, t in links:
        notes[s]["d"] += 1
        notes[t]["d"] += 1
    return {"n": list(notes.values()), "l": links}


def write(root: Path, out: Path) -> dict:
    data = graph(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    broken = sorted({t for _, t in
                     [(s, t) for f in root.rglob("*.md")
                      for s in [f.stem]
                      for t in (x.strip() for x in WIKILINK.findall(
                          f.read_text(encoding="utf-8")))]
                     if t not in {n["i"] for n in data["n"]}})
    return {"notes": len(data["n"]), "links": len(data["l"]), "broken_links": broken,
            "out": str(out)}
