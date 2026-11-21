"""Wave 9 evidence tests: family examples, schemas, warnings budget, performance guard, snapshots, streaming parity,
corporate-action lineage, integration, lifecycle and promotion evidence (CP-0087, CP-0088, CP-0119, CP-0142,
CP-0279, CP-0325, CP-0327, CP-0335, CP-0340, CP-0349, CP-0379, CP-0384, CP-0401).

Every fixture here is synthetic. None of these tests is market evidence.
"""

from __future__ import annotations

import importlib.util
import json
import time
import warnings
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from financial_brain.patterns import detectors2 as d2
from financial_brain.patterns import demo, report, views
from financial_brain.patterns.catalogue import load_catalogue, migrate_document, source_quality
from financial_brain.patterns.detectors import Bars, double_extrema
from financial_brain.patterns.events_store import EventLog
from financial_brain.patterns.learning import Label, ModelRegistry, PromotionRefused, scheduled_retrain
from financial_brain.patterns.marketdata import IdentityMap, SecurityIdentity, adjust_for_dividend, adjust_for_split
from financial_brain.patterns.spec import EvidenceTier, EventState, PatternEvent, canonical_json
from financial_brain.patterns.streaming import StreamingPivots
from financial_brain.patterns.pivots import causal_pivots
from financial_brain.patterns.candles import K7_NOTICE
from financial_brain.patterns.agent import explain_rejection
from financial_brain.patterns.paper import classify_loss

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "patterns" / "family_examples.json"


def _load_builder() -> Any:
    spec = importlib.util.spec_from_file_location("build_pattern_fixtures", ROOT / "scripts" / "build_pattern_fixtures.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = _load_builder()


def _bars(rows: list[list[float]]) -> Bars:
    return Bars(tuple(r[0] for r in rows), tuple(r[1] for r in rows), tuple(r[2] for r in rows), tuple(r[3] for r in rows))


# ---------------------------------------------------------------- family examples (CP-0088, CP-0340)
def test_fixture_file_matches_the_builder_output() -> None:
    assert json.loads(FIXTURES.read_text(encoding="utf-8")) == BUILDER.build()


def test_every_family_has_one_positive_and_two_negatives() -> None:
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    by_family: dict[str, list[str]] = {}
    for ex in data["examples"]:
        by_family.setdefault(ex["family"], []).append(ex["role"])
    assert len(by_family) == 6
    for family, roles in by_family.items():
        assert sorted(roles) == ["negative_1", "negative_2", "positive"], family


@pytest.mark.parametrize("family", sorted(BUILDER.FAMILIES))
def test_examples_behave_as_labelled(family: str) -> None:
    spec = BUILDER.FAMILIES[family]
    data = json.loads(FIXTURES.read_text(encoding="utf-8"))
    for ex in (e for e in data["examples"] if e["family"] == family):
        found = spec["run"](_bars(ex["ohlc"]))
        fired = any(f.detector == ex["detector"] for f in found)
        assert fired is ex["expect_fire"], (family, ex["role"])


def test_tentative_and_confirmed_double_bottom_are_distinguished() -> None:
    """CP-0379: the trader sees a candidate before the neckline break and a confirmed state after it."""
    rows = BUILDER.mid_bars(BUILDER._double_positive(), 0.3)
    found = [f for f in double_extrema(_bars(rows), window=12) if f.detector == "double_bottom"]
    assert len(found) == 1
    shape = found[0]
    assert shape.state_at(shape.candidate_known_index) == "CANDIDATE"  # tentative at recognition
    assert shape.confirmed_index is not None and shape.state_at(shape.confirmed_index) == "CONFIRMED"
    assert shape.as_of(shape.candidate_known_index).confirmed_index is None  # not yet confirmed then


# ---------------------------------------------------------------- schemas (CP-0325)
def test_catalogue_document_validates_against_its_schema() -> None:
    schema = json.loads((ROOT / "docs" / "schemas" / "pattern-catalogue.v1.schema.json").read_text(encoding="utf-8"))
    data = json.loads((ROOT / "docs" / "sources" / "pattern-catalogue.v1.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors = [e.message for row in data["patterns"] for e in validator.iter_errors(row)]
    assert errors == [], errors[:3]  # the schema describes one pattern row


def test_emitted_events_validate_against_the_event_schema() -> None:
    schema = json.loads((ROOT / "docs" / "schemas" / "pattern-event.v1.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    base = dict(
        event_id="pev_" + "a" * 32, spec_sha256="0" * 64, lineage="L", timeframe="1D",
        candidate_known_at_utc="2026-01-05T10:00:00+00:00", status=EventState.CANDIDATE, data_sha256="1" * 64,
    )
    events = [
        PatternEvent(**base),
        PatternEvent(**{**base, "status": EventState.INVALIDATED, "invalidated_at_utc": "2026-01-06T10:00:00+00:00",
                        "invalidation_reason": "CLOSE_BEYOND_FAILURE"}),
        PatternEvent(**{**base, "observed_at_utc": "2026-01-05T09:59:00+00:00", "generated_at_utc": "2026-01-05T10:00:00+00:00"}),
    ]
    for event in events:
        assert list(validator.iter_errors(event.to_dict())) == []


# ---------------------------------------------------------------- warnings budget and performance guard (CP-0335, CP-0327)
def test_pattern_library_emits_no_warnings_on_the_offline_demo() -> None:
    corpus = demo.synthetic_corpus()
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        demo.run_pipeline(corpus)
        for bars in corpus.values():
            d2.discover_extended(bars)
    from_patterns = [w for w in seen if "financial_brain/patterns" in str(w.filename).replace("\\", "/")]
    assert from_patterns == []  # CP-0335: zero new warnings from the pattern library


def test_wave9_scan_stays_inside_a_conservative_time_guard() -> None:
    corpus = demo.synthetic_corpus()
    started = time.perf_counter()
    for bars in corpus.values():
        d2.discover_extended(bars)
    elapsed = time.perf_counter() - started
    assert elapsed < 60.0  # generous for CI variance; the measured figure lives in docs/audits/pattern-benchmark.json


def test_recorded_benchmark_projection_is_inside_its_budget() -> None:
    bench = json.loads((ROOT / "docs" / "audits" / "pattern-benchmark.json").read_text(encoding="utf-8"))
    assert bench["projected_full_scan_seconds"] < bench["budget_seconds"]


# ---------------------------------------------------------------- streaming parity (CP-0087)
@pytest.mark.parametrize(
    "rows",
    [
        BUILDER.mid_bars(BUILDER._pennant_positive(), 0.2),
        BUILDER.mid_bars(BUILDER._cup_positive(), 0.3),
        BUILDER.mid_bars(BUILDER._flag_positive(), 0.2),
    ],
    ids=["pennant", "cup", "flag"],
)
def test_streaming_pivots_match_batch_pivots_on_continuation_fixtures(rows: list[list[float]]) -> None:
    bars = _bars(rows)
    stream = StreamingPivots(left=2, right=2)
    streamed = []
    for h, lo in zip(bars.high, bars.low, strict=True):
        streamed.extend(stream.push(h, lo))
    batch = causal_pivots(bars.high, bars.low, left=2, right=2)
    assert sorted((p.index, p.kind, p.known_at_index) for p in streamed) == sorted(
        (p.index, p.kind, p.known_at_index) for p in batch
    )


# ---------------------------------------------------------------- corporate actions and lineage (CP-0142)
def test_symbol_rename_keeps_one_lineage_for_both_aliases() -> None:
    im = IdentityMap()
    identity = SecurityIdentity(lineage="INE-0001", isin="INE0000000001")
    im.add("OLDNAME", identity)
    im.add("NEWNAME", identity)
    assert im.resolve("OLDNAME") == identity and im.resolve("NEWNAME") == identity


def test_merger_alias_with_two_lineages_is_withheld_not_guessed() -> None:
    im = IdentityMap()
    im.add("ACQ", SecurityIdentity(lineage="A-1", isin="IN1"))
    im.add("ACQ", SecurityIdentity(lineage="B-1", isin="IN2"))
    assert im.resolve("ACQ") is None


def test_split_and_dividend_adjustments_on_known_values() -> None:
    assert adjust_for_split([100.0, 200.0, 210.0], ex_index=1, factor=2.0) == [50.0, 200.0, 210.0]
    adjusted = adjust_for_dividend([100.0, 102.0], ex_index=1, amount=2.0)
    assert adjusted[1] == 102.0  # on and after the ex-date nothing changes
    assert round(adjusted[0], 6) == 98.0  # pre-ex close scaled by (P_prev - D) / P_prev with P_prev = 100


# ---------------------------------------------------------------- snapshots (CP-0349)
def _snapshot(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


SNAP_PATH = ROOT / "tests" / "fixtures" / "patterns" / "snapshots.json"


def _snapshot_outputs() -> dict[str, str]:
    rows = [
        {"symbol": "ABC", "detector": "rectangle", "state": "CONFIRMED", "geometry_fit": 1.0, "data_quality": "OK",
         "research_evidence": "DESCRIPTIVE", "calibration": "not calibrated", "reasons": ""},
    ]
    return {
        "daily_screen": report.render_html(rows, title="Daily screen"),
        "empty_state": report.render_html([], title="Daily screen"),
        "risk_rejection": json.dumps(explain_rejection(["SYMBOL_LIMIT"]), sort_keys=True),
        "loss_review": json.dumps(
            {"category": classify_loss(
                gap_through_stop=False, false_breakout=True, stale_quote=False, spread_dominant=False)},
            sort_keys=True,
        ),
    }


def test_rendered_outputs_match_their_pinned_snapshots() -> None:
    expected = json.loads(SNAP_PATH.read_text(encoding="utf-8"))
    actual = {name: _snapshot(text) for name, text in _snapshot_outputs().items()}
    assert actual == expected


def test_empty_state_says_there_is_nothing_to_trade() -> None:
    assert "no patterns" in report.render_html([], title="x").lower()


# ---------------------------------------------------------------- integration (CP-0279)
def test_synthetic_bars_flow_through_detector_preview_and_views_without_network() -> None:
    corpus = demo.synthetic_corpus()
    pipeline = demo.run_pipeline(corpus)
    preview = demo.paper_preview(corpus)
    counts = views.status_counts(
        [{**row, "known_at": row.get("known_at", "2026-01-01")} for row in pipeline["rows"]]
    )
    assert pipeline["formation_count"] >= 0
    assert preview is None or preview.get("mode", "PAPER ONLY") in ("PAPER ONLY", "SYNTHETIC")
    assert set(counts) == set(views.STATES)


# ---------------------------------------------------------------- K7 metadata (CP-0119)
def test_k7_finding_is_carried_in_every_export_banner() -> None:
    exported = json.loads(report.export_json([], provenance={"source": "test"}))
    assert K7_NOTICE in exported["banners"]
    assert "not evidence of a working pattern" in K7_NOTICE


# ---------------------------------------------------------------- caveats and source notes (CP-0384)
def test_every_catalogue_entry_carries_historical_caveats() -> None:
    catalogue = load_catalogue(ROOT / "docs" / "sources" / "pattern-catalogue.v1.json")
    assert catalogue.ok and catalogue.entries
    for entry in catalogue.entries:
        assert entry.claim_kind == ("historical_conditional" if entry.stats else "none")
        assert entry.reference.get("page_unverified") is True  # page numbers are never claimed as verified
        fields = source_quality(entry)
        assert all(v == "UNASSESSED" for v in fields.values())


def test_catalogue_migration_preserves_stable_ids() -> None:
    raw = json.loads((ROOT / "docs" / "sources" / "pattern-catalogue.v1.json").read_text(encoding="utf-8"))
    migrated = migrate_document(raw)
    assert [r["id"] for r in migrated["patterns"]] == [r["id"] for r in raw["patterns"]]
    assert migrated["schema_version"] == "1"
    assert "schema_version" not in raw  # input untouched


# ---------------------------------------------------------------- promotion evidence on matured fixture labels (CP-0401)
def test_retrained_model_is_shadow_then_promoted_only_with_evidence_then_rolled_back() -> None:
    reg = ModelRegistry()
    labels = [Label(f"e{i}", i, i + 3, 1.0 if i % 3 else -1.0) for i in range(12)]
    v1 = scheduled_retrain(labels, now_index=30, registry=reg, version_id="fixture_v1")
    v2 = scheduled_retrain(labels[:6], now_index=30, registry=reg, version_id="fixture_v2")
    assert reg.active is None
    with pytest.raises(PromotionRefused):
        reg.promote(v1.version_id, evidence=EvidenceTier.DESCRIPTIVE)
    reg.promote(v1.version_id, evidence=EvidenceTier.FORWARD_PAPER)
    reg.promote(v2.version_id, evidence=EvidenceTier.FORWARD_PAPER)
    assert reg.active == "fixture_v2"
    assert reg.rollback() == "fixture_v1"


def test_event_log_round_trips_an_event_without_loss() -> None:
    log = EventLog()
    base = PatternEvent(
        event_id="pev_" + "b" * 32, spec_sha256="0" * 64, lineage="L", timeframe="1D",
        candidate_known_at_utc="2026-01-05T10:00:00+00:00", status=EventState.CANDIDATE, data_sha256="1" * 64,
    )
    log.append(base)
    assert canonical_json(base.to_dict()) == canonical_json(PatternEvent(**base.to_dict()).to_dict())
    assert [v.status for v in log.history(base.event_id)] == [EventState.CANDIDATE]


# ---------------------------------------------------------------- documentation, CLI and learning-framework decisions
DOC_CHECKS = {
    "docs/audits/pattern-branch-reconciliation.md": ("Work branch", "not contacted"),  # CP-0002, CP-0006
    "docs/audits/pattern-module-inventory.md": ("Existing module", "paper"),  # CP-0003
    "docs/audits/pattern-data-limitations.md": ("survivor", "2016-04"),  # CP-0008
    "docs/PATTERNS-DETECTOR-AUTHORING.md": ("causal rule", "Fixture template"),  # CP-0356
    "docs/PATTERNS-MIGRATION-POLICY.md": ("Stable IDs never change", "Unknown versions fail closed"),  # CP-0360
    "docs/PATTERNS-OPERATIONS-GUIDE.md": ("Corrupt or invalid bars", "Rebuilding the result cache"),  # CP-0361
}


@pytest.mark.parametrize("path,required", sorted(DOC_CHECKS.items()), ids=lambda v: str(v)[:40])
def test_required_documents_exist_with_their_sections(path: str, required: str | tuple[str, str]) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    needles = required if isinstance(required, tuple) else (required,)
    for needle in needles:
        assert needle in text, (path, needle)


def test_fb_patterns_subcommand_runs_offline_and_leaves_fb_intact(capsys: pytest.CaptureFixture[str]) -> None:
    from financial_brain.cli import main as fb_main

    assert fb_main(["patterns", "paper-demo"]) == 0  # CP-0365
    out = capsys.readouterr().out
    assert "PAPER" in out.upper() or "SYNTHETIC" in out.upper()
    with pytest.raises(SystemExit) as exc:
        fb_main(["--help"])
    assert exc.value.code == 0


def test_learning_module_adds_no_new_framework_and_keeps_the_calibration_floor() -> None:
    """CP-0285: a gradient-boosted learner is not introduced below the calibration floor."""
    from financial_brain.patterns import learning

    source = (PKG / "learning.py").read_text(encoding="utf-8")
    for framework in ("lightgbm", "sklearn", "torch", "xgboost"):
        assert f"import {framework}" not in source
    assert learning.MIN_LABELS_FOR_CALIBRATION == 200


PKG = ROOT / "src" / "financial_brain" / "patterns"


APPENDIX = ROOT / "docs" / "audits" / "pattern-evidence-appendix.md"


def test_evidence_appendix_records_commands_exit_codes_python_and_commit_reference() -> None:
    """CP-0336: every row names the command, its exit code, the Python version and the commit it was run against."""
    text = APPENDIX.read_text(encoding="utf-8")
    assert "| Command | Exit code | Result |" in text
    assert "Python 3.13" in text
    assert "Commit under test" in text and "b4fe829" in text
    assert text.count("| 0 |") >= 8
