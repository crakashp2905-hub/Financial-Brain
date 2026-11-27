"""Validate the exact-SHA PR #40 capability contract comparison."""
from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/"docs/edge-program/pr40-contract-reconciliation.v1.json"
PROVENANCE=ROOT/"docs/edge-program/capability-provenance.v1.json"
def validate() -> None:
    report=json.loads(REPORT.read_text(encoding="utf-8-sig")); provenance=json.loads(PROVENANCE.read_text(encoding="utf-8"))
    by={s["source_id"]:s for s in provenance["sources"]}
    compare=report["comparison"]
    if compare["main_commit_sha"] != by["main"]["commit_sha"]: raise ValueError("main source SHA mismatch")
    if compare["main_registry_blob_sha"] != by["main"]["capability_registry"]["git_blob_sha1"]: raise ValueError("main registry blob mismatch")
    if compare["pr40_head_sha"] != by["PR-40"]["head_sha"]: raise ValueError("PR #40 head SHA mismatch")
    if compare["pr40_merge_commit_sha"] != by["PR-40"]["merge_commit_sha"]: raise ValueError("PR #40 merge SHA mismatch")
    if compare["pr40_registry_blob_sha"] != by["PR-40"]["capability_registry"]["git_blob_sha1"]: raise ValueError("PR #40 registry blob mismatch")
    rows=report.get("rows", []); ids=[r["contract_id"] for r in rows]
    if len(ids)!=len(set(ids)) or len(ids)!=25: raise ValueError("expected 25 unique trader-core capability contracts")
    summary={key:sum(r["decision"]==key for r in rows) for key in ("KEEP","MERGE","REIMPLEMENT","REJECT")}
    if {"contracts":len(rows), **summary} != report.get("summary"): raise ValueError("decision counts do not match rows")
    if summary != {"KEEP":21,"MERGE":0,"REIMPLEMENT":4,"REJECT":0}: raise ValueError("unexpected reconciliation decisions")
    removed=set(by["PR-40"]["capability_registry"]["delta_vs_main"]["capabilities"]["removed"])
    if removed != {r["contract_id"] for r in rows if r["decision"]=="REIMPLEMENT"}: raise ValueError("reimplementation rows disagree with exact registry delta")
    if any(r["decision"]=="KEEP" and not r["exact_contract_match"] for r in rows): raise ValueError("KEEP row is not an exact contract match")
if __name__=="__main__":
    validate(); print("PR #40 exact-SHA capability contract reconciliation is valid")
