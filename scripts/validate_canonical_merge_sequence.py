"""Check the planned PR merge order against captured exact-SHA provenance."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PLAN=ROOT/"docs/edge-program/canonical-merge-sequence.v1.json"
PROVENANCE=ROOT/"docs/edge-program/capability-provenance.v1.json"
def validate() -> None:
    plan=json.loads(PLAN.read_text(encoding="utf-8")); source=json.loads(PROVENANCE.read_text(encoding="utf-8"))
    claimed=plan.get("canonical_sha256"); unsigned=dict(plan); unsigned["canonical_sha256"]=None
    digest=hashlib.sha256(json.dumps(unsigned,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
    if claimed != digest: raise ValueError("merge sequence hash mismatch")
    by={row["source_id"]:row for row in source["sources"]}
    if plan.get("baseline_main_sha") != by["main"]["commit_sha"]: raise ValueError("baseline SHA differs from provenance")
    expected=[("PR-37","PR-36"),("PR-36","PR-38")]
    seq=plan.get("sequence",[])
    if [row.get("source") for row in seq] != ["PR-37","PR-36","PR-38"]: raise ValueError("unsafe or incomplete merge order")
    for row in seq:
        if row.get("head_sha") != by[row["source"]]["head"]["sha"]: raise ValueError("planned PR SHA differs from capture")
    edges={(edge["from"],edge["to"]) for edge in source["dependency_edges"] if edge.get("verified")}
    if not set(expected).issubset(edges): raise ValueError("required graph dependency edge is missing")
    if plan.get("merge_execution_status") != "PLAN_ONLY_NOT_MERGED": raise ValueError("plan must not misrepresent merge execution")
if __name__=="__main__":
    validate(); print("canonical merge sequence matches exact-SHA dependency graph")
