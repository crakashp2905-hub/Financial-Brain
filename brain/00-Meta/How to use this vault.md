---
type: meta
tags:
  - meta
---

# How to use this vault


## Shape
| Folder | Holds |
|---|---|
| `00-Meta` | this, [[Home]], templates |
| `01-Maps` | Maps of Content — the entry points |
| `02-Architecture` | one note per principle or contract |
| `03-Resources` | one note per external repo/library |
| `04-Data-Sources` | one note per data source |
| `05-Components` | one note per thing we must build |
| `06-Decisions` | open and settled decisions |
| `07-Research` | findings, datasets, model strategy |
| `08-Domain` | Indian market concepts |
| `09-Journal` | daily notes |

## Conventions
- **One idea per note.** If a note needs two headings that don't relate, split it.
- **Link, don't copy.** A red link is a to-do, not an error.
- **Frontmatter is queryable**: `type`, `verdict`, `phase`, `status`, `tier`, `license`.
- **Tags** mirror frontmatter: `#resource`, `#component`, `#decision`, `#concept`,
  `#verdict/integrate`, `#phase/0`, `#tier/1`.

## Recommended plugins
Dataview (the MOCs contain optional queries) · Templater · Git · Excalidraw.
None are required — every map works as plain links.

## Relationship to `docs/`
The vault is for thinking; `docs/` holds the canonical long-form documents
(ARCHITECTURE, RESOURCES, FEASIBILITY-INDIA, BUILD-FLOW). When a vault note and a doc
disagree, **the doc wins** — update the doc, then the note.
