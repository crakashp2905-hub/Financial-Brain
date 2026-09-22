---
type: concept
tags:
  - concept
  - memory
---

# Procedural memory

Three kinds of memory, and only one of them is a model:

| Kind | Where it lives | What it holds |
|---|---|---|
| **Episodic** | `decisions`, `paper_trades`, `decision_postmortems` | what we did, when, and what happened |
| **Semantic** | [[Evidence ledger]], [[C24 Knowledge Graph]] | what is true, and how we know |
| **Procedural** | this vault, [[MOC Strategies]], `constitution/` | **how to do things, and how they fail** |

Procedural memory is the part that stops a mistake recurring without needing a model to
remember it. [[Learning from losses]] writes rules from the trade record; the strategy
notes write them from the research record; the [[C14 Investment Constitution]] writes them
from the owner. All three are text a person can argue with, and code reads the ones that
must bind.

## Why not just train on it

A model that has "read the playbook" cannot tell you which rule it applied, and cannot be
argued out of a rule that is wrong. Every rule here is either (a) prose a human reads
before deciding, or (b) a typed check some code evaluates - `invalidation_checks`, the
constitution's rules, [[Learning from losses]] gates. Nothing important lives only in
weights.

## On ontologies

An ontology layer (OntoBricks and similar) would formalise what these notes state
loosely: strategy *is-a* claim, claim *depends-on* feature, feature *derived-from*
source, failure *caused-by* gate. The pull is real - it would let "which live decisions
rest on a signal that just failed revalidation?" be a query rather than a search.

The reason it is not built yet: we have 166 notes and seven tested strategies. An ontology
pays for itself when the graph is too big to hold in a head, and ours is not. The cheap
version - consistent frontmatter, `depends-on` links, and a test that fails on a broken
link - already answers most of those questions. Revisit when the strategy count passes a
few dozen, not before.

Related: [[MOC Strategies]] · [[Learning from losses]] · [[Situational awareness]]
