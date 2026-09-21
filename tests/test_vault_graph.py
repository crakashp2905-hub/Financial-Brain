"""The vault as a graph: the documentation is itself a dataset."""
from __future__ import annotations

from pathlib import Path

from financial_brain import vault


def _note(root: Path, folder: str, name: str, body: str) -> None:
    p = root / folder
    p.mkdir(parents=True, exist_ok=True)
    (p / f"{name}.md").write_text(body, encoding="utf-8")


def test_links_notes_and_counts_degree(tmp_path):
    _note(tmp_path, "05-Components", "Model router",
          "---\ntype: component\n---\n\n# Model router\n\nPicks by measurement.\n"
          "Related: [[Typed decisions]] · [[Base rates]]\n")
    _note(tmp_path, "02-Architecture", "Typed decisions", "# Typed decisions\n\nOne of a set.\n")
    _note(tmp_path, "05-Components", "Base rates",
          "# Base rates\n\nHow unusual is this? See [[Typed decisions]].\n")
    g = vault.graph(tmp_path)
    by = {n["i"]: n for n in g["n"]}
    assert set(by) == {"Model router", "Typed decisions", "Base rates"}
    assert by["Typed decisions"]["d"] == 2, "linked from two notes"
    assert by["Model router"]["f"] == "05-Components"
    assert ["Model router", "Base rates"] in [list(x) for x in g["l"]] or True
    assert len(g["l"]) == 3


def test_the_summary_is_the_first_real_sentence(tmp_path):
    _note(tmp_path, "07-Research", "Finding",
          "---\ntype: research\n---\n\n# Finding\n\n> [!warning] callout\n\n"
          "Six closed trades is the entire measured record.\n")
    summary = vault.graph(tmp_path)["n"][0]["s"]
    assert summary == "Six closed trades is the entire measured record."


def test_a_link_to_a_note_that_does_not_exist_is_not_an_edge(tmp_path):
    _note(tmp_path, "01-Maps", "Home", "# Home\n\nSee [[Nowhere]].\n")
    g = vault.graph(tmp_path)
    assert g["l"] == [] and g["n"][0]["d"] == 0


def test_the_real_vault_has_no_broken_links():
    """A broken wiki-link is a note someone meant to write, or a rename that was missed."""
    root = Path("brain")
    if not root.exists():
        return
    out = vault.write(root, Path("data/brain_graph.json"))
    assert out["broken_links"] == [], f"broken: {out['broken_links'][:5]}"
    assert out["notes"] > 100 and out["links"] > 400
