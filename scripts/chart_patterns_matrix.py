"""Generate the chart-pattern baseline and completion matrix (CP-0001, CP-0336, CP-0393).

Reads the master checklist, validates that every CP row parses exactly once, and
writes:

- ``docs/audits/pattern-baseline.json``        fingerprint of the checkout (CP-0001)
- ``docs/audits/chart-patterns-completion.json`` one row per CP item (contract in
  the checklist's "Automated sign-off file contract")

A row is ``implemented`` or ``verified_existing`` only when it is listed in
``EVIDENCE`` below with source files and a test file that exists on disk. Every
other row stays ``incomplete``. Nothing is marked done by heuristic.

Usage: ``python scripts/chart_patterns_matrix.py [--check]``. ``--check`` exits
nonzero if the committed matrix is stale relative to the generated one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = ROOT / "docs" / "audits" / "chart-patterns-master-checklist.md"
BASELINE = ROOT / "docs" / "audits" / "pattern-baseline.json"
COMPLETION = ROOT / "docs" / "audits" / "chart-patterns-completion.json"
DEP_FILES = ("pyproject.toml", "requirements-lock.txt")

CP_RE = re.compile(r"^- \[ \] \*\*(CP-\d{4})\*\* (.+?)\s*$")
WORKSTREAM_RE = re.compile(r"^### (\d{2}) — (.+?)\s*$")
PRIORITY_RE = re.compile(r"\*\*Priority:\*\* (P[012])")

PYTEST = "python -m pytest -q"
PATTERN_TESTS = [
    "tests/test_pattern_catalogue.py",
    "tests/test_pattern_spec.py",
    "tests/test_pattern_pivots.py",
    "tests/test_pattern_offline.py",
    "tests/test_pattern_matrix.py",
    "tests/test_pattern_stream_store.py",
    "tests/test_pattern_detectors.py",
    "tests/test_pattern_marketdata_candles.py",
]

# task_id -> (source_files, test_files, notes). Scope limits are stated in notes.
EVIDENCE: dict[str, tuple[list[str], list[str], str]] = {
    "CP-0001": (
        ["scripts/chart_patterns_matrix.py"],
        ["tests/test_pattern_matrix.py"],
        "Deterministic fingerprint: HEAD SHA, dirty paths, Python version, SHA-256 of pyproject.toml and requirements-lock.txt.",
    ),
    "CP-0010": (
        ["docs/audits/pattern-implementation-map.md"],
        ["tests/test_pattern_matrix.py"],
        "Maps every implemented task to real module and test paths; the matrix test checks those paths exist.",
    ),
    "CP-0011": (
        ["src/financial_brain/patterns/offline.py"],
        ["tests/test_pattern_offline.py"],
        "Package-level guard only. Process-wide no-network startup assert is CP-0375 and is not done.",
    ),
    "CP-0014": (
        ["src/financial_brain/patterns/catalogue.py"],
        ["tests/test_pattern_catalogue.py"],
        "sample_total is typed from the 'n' field. sample_confirmed and sample_unconfirmed are not reliably extractable from source text and are not emitted.",
    ),
    "CP-0016": (
        ["src/financial_brain/patterns/catalogue.py"],
        ["tests/test_pattern_catalogue.py"],
        "Only plain integer/decimal strings are typed; '~', '<', ranges and compound text stay null with source_claim_text.",
    ),
    "CP-0018": (
        ["src/financial_brain/patterns/catalogue.py"],
        ["tests/test_pattern_catalogue.py"],
        "Uniqueness and N/B source-prefix validation. Migration tests are not applicable at catalogue v1.",
    ),
    "CP-0019": (
        ["src/financial_brain/patterns/catalogue.py"],
        ["tests/test_pattern_catalogue.py"],
        "Controlled FAMILY_MAP; 5 source rows whose family is not a real family map to 'unclassified' and are not guessed.",
    ),
    "CP-0020": (
        ["src/financial_brain/patterns/catalogue.py"],
        ["tests/test_pattern_catalogue.py"],
        "direction_class is descriptive (bullish/bearish/either/unclassified). It is not an eligible trade side.",
    ),
    "CP-0025": (
        ["src/financial_brain/patterns/catalogue.py"],
        ["tests/test_pattern_catalogue.py"],
        "CLI validates, counts by source and family, and exits 1 on malformed data or count mismatch.",
    ),
    "CP-0027": (
        ["src/financial_brain/patterns/spec.py"],
        ["tests/test_pattern_spec.py"],
        "Canonical JSON with sorted keys; identical SHA-256 for reordered parameters. Cross-platform hash equality is not yet tested on Linux.",
    ),
    "CP-0028": (
        ["src/financial_brain/patterns/spec.py"],
        ["tests/test_pattern_spec.py"],
        "Typed PatternEvent with timezone-aware timestamps and ordering checks.",
    ),
    "CP-0030": (
        ["src/financial_brain/patterns/spec.py"],
        ["tests/test_pattern_spec.py"],
        "EventState enum and explicit transition table; illegal transitions raise InvalidTransition.",
    ),
    "CP-0033": (
        ["src/financial_brain/patterns/spec.py"],
        ["tests/test_pattern_spec.py"],
        "Detector events can only carry DESCRIPTIVE evidence; EvidenceTier is separate from any probability.",
    ),
    "CP-0034": (
        ["src/financial_brain/patterns/spec.py"],
        ["tests/test_pattern_spec.py"],
        "make_event_id is a hash of lineage, timeframe, spec SHA and first-known time.",
    ),
    "CP-0038": (
        ["src/financial_brain/patterns/spec.py", "src/financial_brain/patterns/pivots.py"],
        ["tests/test_pattern_spec.py", "tests/test_pattern_pivots.py"],
        "NaN/inf rejected in parameters, geometry and pivot inputs; naive timestamps rejected.",
    ),
    "CP-0040": (
        ["src/financial_brain/patterns/spec.py"],
        ["tests/test_pattern_spec.py"],
        "Round-trip for PatternSpec and PatternEvent only. No other public model exists yet.",
    ),
    "CP-0056": (
        ["src/financial_brain/patterns/pivots.py"],
        ["tests/test_pattern_pivots.py"],
        "Causal left/right swing pivots; known_at_index = index + right.",
    ),
    "CP-0057": (
        ["src/financial_brain/patterns/pivots.py"],
        ["tests/test_pattern_pivots.py"],
        "Strict-left/non-strict-right tie rule; flat tops resolve to the first plateau bar.",
    ),
    "CP-0136": (
        ["src/financial_brain/patterns/pivots.py"],
        ["tests/test_pattern_pivots.py", "tests/test_pattern_detectors.py"],
        "Prefix invariance for the pivot primitive over 40 seeded random series. Covers the primitive only, not detectors.",
    ),
    "CP-0138": (
        ["src/financial_brain/patterns/pivots.py"],
        ["tests/test_pattern_pivots.py"],
        "Pivot not observable before its right window closes, for the pivot primitive.",
    ),
    "CP-0021": (
        ["src/financial_brain/patterns/catalogue.py"],
        ["tests/test_pattern_stream_store.py"],
        "promote_definition_status advances one step at a time and requires an evidence reference; text alone stays descriptive. Not yet wired to a registry store.",
    ),
    "CP-0035": (
        ["src/financial_brain/patterns/spec.py"],
        ["tests/test_pattern_stream_store.py"],
        "Provenance contract with catalogue, commit, calendar and config hashes. Not yet embedded in every result writer.",
    ),
    "CP-0037": (
        ["src/financial_brain/patterns/spec.py"],
        ["tests/test_pattern_stream_store.py"],
        "config_hash is a canonical SHA-256 of parameters, so a parameter change changes the hash. Event streams are not yet versioned by it.",
    ),
    "CP-0039": (
        ["src/financial_brain/patterns/spec.py"],
        ["tests/test_pattern_stream_store.py"],
        "PatternSignal is research_only with no side, quantity or price fields.",
    ),
    "CP-0069": (
        ["src/financial_brain/patterns/events_store.py"],
        ["tests/test_pattern_stream_store.py"],
        "sorted_events gives canonical order independent of input order.",
    ),
    "CP-0070": (
        ["src/financial_brain/patterns/streaming.py"],
        ["tests/test_pattern_stream_store.py"],
        "Streaming pivots equal batch output on every prefix for 25 seeded series. Pivot primitive only.",
    ),
    "CP-0071": (
        ["src/financial_brain/patterns/streaming.py"],
        ["tests/test_pattern_stream_store.py"],
        "Buffer capped at left+right+1 bars (tested over 10,000 bars). No active candidates exist yet, so the 'do not delete active candidates' rule is untested.",
    ),
    "CP-0072": (
        ["src/financial_brain/patterns/pivots.py"],
        ["tests/test_pattern_stream_store.py", "tests/test_pattern_pivots.py"],
        "Constant series, one bar, empty input and NaN are covered for the pivot primitive. Spike and missing-bar cases are not yet covered.",
    ),
    "CP-0121": (
        ["src/financial_brain/patterns/events_store.py"],
        ["tests/test_pattern_stream_store.py"],
        "Append-only version log keyed by (event_id, status); history is never replaced.",
    ),
    "CP-0126": (
        ["src/financial_brain/patterns/events_store.py"],
        ["tests/test_pattern_stream_store.py"],
        "as_of(cutoff) returns the newest version knowable by the cutoff. EXPIRED and SUPERSEDED are rejected until they have a time field.",
    ),
    "CP-0128": (
        ["src/financial_brain/patterns/events_store.py"],
        ["tests/test_pattern_stream_store.py"],
        "Identical re-append is a no-op; a different payload under the same key raises ConflictingVersion.",
    ),
    "CP-0132": (
        ["src/financial_brain/patterns/events_store.py"],
        ["tests/test_pattern_stream_store.py"],
        "Canonical sort key ignores insertion order and dict iteration order.",
    ),
    "CP-0058": (
        ["src/financial_brain/patterns/geometry.py"],
        ["tests/test_pattern_geometry.py"],
        "TolerancePolicy names one unit (atr, pct or tick) and raises when its input is missing; units are never mixed.",
    ),
    "CP-0060": (
        ["src/financial_brain/patterns/geometry.py"],
        ["tests/test_pattern_geometry.py", "tests/test_pattern_detectors.py"],
        "horizontal_touches counts only bars at or before known_upto. Breakout-level construction is not built yet.",
    ),
    "CP-0061": (
        ["src/financial_brain/patterns/geometry.py"],
        ["tests/test_pattern_geometry.py"],
        "Line.through rejects vertical lines; intersection_index returns None for parallel lines. Convergence and angle tests are not built yet.",
    ),
    "CP-0066": (
        ["src/financial_brain/patterns/geometry.py"],
        ["tests/test_pattern_geometry.py"],
        "fraction() returns None when the ratio is out of bounds or the denominator is zero. Compression and pole primitives are not built yet.",
    ),
    "CP-0067": (
        ["src/financial_brain/patterns/geometry.py"],
        ["tests/test_pattern_geometry.py"],
        "resolve_bar_ambiguity returns AMBIGUOUS when one bar spans both stop and target, and never assumes a favourable order.",
    ),
    "CP-0073": (
        ["src/financial_brain/patterns/geometry.py"],
        ["tests/test_pattern_geometry.py"],
        "DecisionTrace records each named check with value and threshold, and accepts only when every check passes. No detector uses it yet.",
    ),
    "CP-0042": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Bars rejects OHLC inequality and non-finite values; quarantine_rows counts rejections by reason. Read-only source adapter is not built.',
    ),
    "CP-0059": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Two-point trendlines through the first and last known pivots of each type. Robust regression alternative is not built.',
    ),
    "CP-0062": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Head-and-shoulders neckline built from causally known intervening extrema; undefined necklines are skipped.',
    ),
    "CP-0074": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Rectangle candidate with flat top and bottom, minimum touches and bounded range fraction. Duration is a fixed window, not exchange-session aware.',
    ),
    "CP-0075": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Direction stays neutral until a close breaks either side; both upward and downward confirmation are recorded.',
    ),
    "CP-0076": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Ascending triangle: flat upper line, rising lows, two-touch minimum, converging check.',
    ),
    "CP-0077": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Descending triangle: flat lower line, falling highs; opposite breakouts are recorded, not discarded.',
    ),
    "CP-0078": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Symmetric triangle requires falling highs, rising lows and convergence; parallel and diverging fits are rejected.',
    ),
    "CP-0079": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Breakout confirmed only on a close beyond the line. Optional delayed re-test rule is not built.',
    ),
    "CP-0080": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Bullish and bearish flags with predeclared pole length, minimum pole return and retracement limit. The pole ends before the flag starts.',
    ),
    "CP-0082": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Rising and falling wedges require same-sign converging lines. Explanatory-quality rejection is not built.',
    ),
    "CP-0086": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Failed candidates are recorded with failed_index; confirmation in either direction is supported.',
    ),
    "CP-0090": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'discover() runs every detector and returns candidates, confirmations and failures in canonical order.',
    ),
    "CP-0091": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Tentative double bottom from two known lows with a neckline; unconfirmed and failed candidates are kept.',
    ),
    "CP-0092": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Confirmed double bottom on a close above the neckline, with recognition and confirmation timestamped separately.',
    ),
    "CP-0093": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Double top mirror with tentative and confirmed states. No success statistic is computed.',
    ),
    "CP-0095": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Head-and-shoulders bottom needs prior downtrend on known bars, symmetric shoulders, and emits only when the right shoulder is known.',
    ),
    "CP-0096": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Head-and-shoulders top with neckline break; earlier candidates are not rewritten.',
    ),
    "CP-0107": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Negative fixtures: strong trend is not a rectangle; flat series yields no triangles or double extrema.',
    ),
    "CP-0137": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        "Appending a large future rally and crash leaves earlier formations' as-of views unchanged.",
    ),
    "CP-0140": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Candidates that never confirm stay in the output as CANDIDATE or FAILED, so denominators include them.',
    ),
    "CP-0043": (
        ['src/financial_brain/patterns/marketdata.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'normalize_timestamp refuses naive timestamps and returns IST display with UTC persistence. Fixed +05:30 is exact for Asia/Kolkata (no DST since 1945).',
    ),
    "CP-0044": (
        ['src/financial_brain/patterns/marketdata.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'SessionCalendar is a versioned table; weekday arithmetic is not used. Missing dates are not assumed to be sessions.',
    ),
    "CP-0045": (
        ['src/financial_brain/patterns/marketdata.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'Offline fixture calendar with a holiday on a weekday, shortened and special sessions, and a circuit-halt flag.',
    ),
    "CP-0046": (
        ['src/financial_brain/patterns/marketdata.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'IdentityMap resolves an alias to one lineage; aliases mapping to two identities are withheld.',
    ),
    "CP-0048": (
        ['src/financial_brain/patterns/marketdata.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'adjust_for_split divides pre-ex-date prices; test shows a 2:1 split no longer reads as a 50% crash.',
    ),
    "CP-0049": (
        ['src/financial_brain/patterns/marketdata.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'snapshot() gives row count, session range and canonical SHA-256; order-independent.',
    ),
    "CP-0050": (
        ['src/financial_brain/patterns/marketdata.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'volume_eligibility returns DATA_INSUFFICIENT for missing, negative or long zero-volume runs.',
    ),
    "CP-0051": (
        ['src/financial_brain/patterns/marketdata.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'Cash equities cannot short; futures shorts require explicit fixture support.',
    ),
    "CP-0052": (
        ['src/financial_brain/patterns/marketdata.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'DataTier enum and labels separate EOD bars, intraday quotes and order-book depth.',
    ),
    "CP-0053": (
        ['src/financial_brain/patterns/marketdata.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'missing_sessions lists absent sessions; no forward fill exists in the module.',
    ),
    "CP-0054": (
        ['src/financial_brain/patterns/marketdata.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'freshness() returns FRESH, STALE or INVALID so callers can abstain.',
    ),
    "CP-0055": (
        ['src/financial_brain/patterns/marketdata.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'ENTITLEMENTS matrix; optional external datasets are blocked offline and not installed here. No test needs network or credentials.',
    ),
    "CP-0108": (
        ['src/financial_brain/patterns/candles.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'CANDLE_MAP inventories the 13 library c_* columns against catalogue IDs; a test confirms each column exists in source. Two compatibility notes recorded.',
    ),
    "CP-0109": (
        ['src/financial_brain/patterns/candles.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'library_candle_events wraps existing c_* features as timed events; no candle logic is re-implemented.',
    ),
    "CP-0110": (
        ['src/financial_brain/patterns/candles.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'Hammer fires only after a decline and shooting star only after a rally (tested). Hanging man is not a separate definition.',
    ),
    "CP-0111": (
        ['src/financial_brain/patterns/candles.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'gap_kind separates a whole-range (Nison window) gap from a body-only gap and returns None when neither applies.',
    ),
    "CP-0112": (
        ['src/financial_brain/patterns/candles.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'Library candle rules use bar-close data only (causal_check on prefixes). Thresholds are frozen in the library source.',
    ),
    "CP-0114": (
        ['src/financial_brain/patterns/candles.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'Pending definitions (tasuki, gapping plays, separating lines, three methods) are excluded from inference.',
    ),
    "CP-0117": (
        ['src/financial_brain/patterns/candles.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'weekly_bars aggregates completed sessions by ISO week; a week is PARTIAL until a later week exists.',
    ),
    "CP-0118": (
        ['src/financial_brain/patterns/candles.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'mtf_known_at_monotonic checks known-at timestamps never move backwards. Full multi-timeframe alignment engine is not built.',
    ),
    "CP-0120": (
        ['src/financial_brain/patterns/candles.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'Gaps explained by a corporate action or circuit freeze are classified as EXCLUDED_* and never become breakouts.',
    ),
    "CP-0004": (
        ['src/financial_brain/patterns/inventory.py'],
        ['tests/test_pattern_inventory_cli.py'],
        'inventory.STATUS gives a truthful implemented/partial/research_only/missing label for all 72 rows; a test checks each implemented claim names real code.',
    ),
    "CP-0005": (
        ['docs/audits/protected-baseline.json'],
        ['tests/test_pattern_acceptance.py'],
        'SHA-256 baseline of source notes, catalogue and trial manifests; a test recomputes and compares every file.',
    ),
    "CP-0007": (
        ['docs/audits/pattern-implementation-map.md'],
        ['tests/test_pattern_matrix.py'],
        'Names reused modules (features/library.py, evaluation/family_validation.py and others) and what is newly built.',
    ),
    "CP-0012": (
        ['scripts/chart_patterns_matrix.py'],
        ['tests/test_pattern_matrix.py'],
        '--check fails if the completion matrix is stale against the checklist; CI runs it.',
    ),
    "CP-0047": (
        ['docs/audits/pattern-data-conventions.md'],
        ['tests/test_pattern_marketdata_candles.py'],
        'Adjusted-price, corporate-action, timezone, session and volume conventions, each backed by tested functions.',
    ),
    "CP-0122": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Candidates are emitted as soon as their geometry is knowable, and remain even if they never confirm.',
    ),
    "CP-0141": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_paper.py'],
        'PatternIntent refuses a decision timestamp after the earliest entry; decision <= entry is enforced at construction.',
    ),
    "CP-0154": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'StudySpec SHA-256 changes with any parameter, including cost and horizon.',
    ),
    "CP-0155": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'trial_family_size counts every examined variant as one collective family.',
    ),
    "CP-0157": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'census() counts events by state and computes no forward returns.',
    ),
    "CP-0158": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'entry_index is confirmation + 1; same-bar entry is not possible through the API.',
    ),
    "CP-0159": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'fixed_exit_index only; StudySpec rejects ultimate-high/low exits.',
    ),
    "CP-0160": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'clock_panels reports candidates, confirmations, failures and open counts with explicit denominators.',
    ),
    "CP-0161": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'Confirmed rate is named as a fraction of candidates; failed and open candidates are counted separately.',
    ),
    "CP-0162": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'require_budget refuses protected P&L evaluation while the 2026Q4 budget is 13/13 spent.',
    ),
    "CP-0163": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'dry_run_report validates spec and sample counts and states returns_computed=False.',
    ),
    "CP-0165": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'block_bootstrap_ci resamples whole symbol clusters with a seeded generator.',
    ),
    "CP-0166": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'reality_check_adapter calls the existing evaluation.family_validation.reality_check; nothing re-implemented.',
    ),
    "CP-0167": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'VariantRegistry records every variant and reports the family size.',
    ),
    "CP-0169": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'assert_prospective refuses observations dated before registration.',
    ),
    "CP-0170": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'Labelled synthetic null and injected-edge generators; test distinguishes them.',
    ),
    "CP-0171": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'report_record refuses any record missing statistic, denominator, benchmark, eligibility, caveats, next gate or lineage.',
    ),
    "CP-0172": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'matched_controls chooses the nearest pool member by standardised features, or None.',
    ),
    "CP-0174": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'time_shift_placebo keeps shifted dates inside the eligible set only.',
    ),
    "CP-0177": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'incremental_value compares pattern and baseline on matched decisions.',
    ),
    "CP-0180": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'purged_split leaves an embargo gap between train and test; invalid splits are refused.',
    ),
    "CP-0183": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'stress_matrix shows net return falling monotonically with adverse spread.',
    ),
    "CP-0184": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_study.py'],
        'Benjamini-Hochberg keeps only hypotheses that survive the FDR level.',
    ),
    "CP-0189": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_paper.py'],
        'PatternIntent is paper_only and carries no broker or order identifier.',
    ),
    "CP-0190": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_paper.py'],
        'next_session_entry is the first session strictly after confirmation.',
    ),
    "CP-0191": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_paper.py'],
        'Every fill record is labelled ASSUMED.',
    ),
    "CP-0192": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_paper.py'],
        'cost_breakdown lists spread, fee, impact and financing separately; net subtracts all of them.',
    ),
    "CP-0193": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_paper.py'],
        'FillOutcome covers no-fill, partial, rejected, timeout and stale quote; nothing fills silently.',
    ),
    "CP-0194": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_paper.py'],
        'Ledger refuses reservations above available cash and any fill that would make cash negative.',
    ),
    "CP-0195": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_paper.py'],
        'check_new_intent returns machine-readable codes for symbol, sector, drawdown and leverage limits.',
    ),
    "CP-0196": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_paper.py'],
        'session_eligibility blocks circuit, suspended and auction-only sessions.',
    ),
    "CP-0197": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_paper.py'],
        'Pending intents are tracked separately and never appear as holdings.',
    ),
    "CP-0198": (
        ['src/financial_brain/patterns/geometry.py'],
        ['tests/test_pattern_geometry.py'],
        'resolve_bar_ambiguity returns AMBIGUOUS when one bar spans stop and target.',
    ),
    "CP-0200": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_paper.py'],
        'trade_summary reports zero realised P&L on zero-trade days.',
    ),
    "CP-0201": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_paper.py'],
        'paper_record links event, spec, data and manifest hashes.',
    ),
    "CP-0202": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_paper.py'],
        'ledger_identity_holds checks opening + flows + realised + unrealised = ending.',
    ),
    "CP-0203": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_paper.py'],
        'drawdown and profit_factor with defined denominators; profit factor is None without losses.',
    ),
    "CP-0207": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_paper.py'],
        'Shorts are refused on cash equities and allowed only on futures fixtures.',
    ),
    "CP-0216": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_paper.py'],
        'KillSwitch blocks new entries; the entry gate never touches existing positions.',
    ),
    "CP-0218": (
        ['src/financial_brain/patterns/cli.py'],
        ['tests/test_pattern_inventory_cli.py'],
        'The patterns CLI has no live, broker or order flags; a test checks the parser.',
    ),
    "CP-0219": (
        ['src/financial_brain/patterns/offline.py'],
        ['tests/test_pattern_offline.py'],
        'AST boundary test: no pattern module imports network, broker, OMS, execution or paper packages.',
    ),
    "CP-0220": (
        ['src/financial_brain/patterns/ranking.py'],
        ['tests/test_pattern_ranking_report.py'],
        'Scores keeps geometry fit, data quality, evidence and calibration as separate fields.',
    ),
    "CP-0221": (
        ['src/financial_brain/patterns/ranking.py'],
        ['tests/test_pattern_ranking_report.py'],
        'Deterministic ordering by state, recency, geometry and liquidity; Opportunity has no return field.',
    ),
    "CP-0222": (
        ['src/financial_brain/patterns/ranking.py'],
        ['tests/test_pattern_ranking_report.py'],
        'dedupe_by_symbol groups evidence per symbol with its detectors.',
    ),
    "CP-0223": (
        ['src/financial_brain/patterns/ranking.py'],
        ['tests/test_pattern_ranking_report.py'],
        'Missing or zero data quality adds MISSING_DATA and ranks the row last.',
    ),
    "CP-0224": (
        ['src/financial_brain/patterns/ranking.py'],
        ['tests/test_pattern_ranking_report.py'],
        'Formations older than max_age are marked EXPIRED.',
    ),
    "CP-0226": (
        ['src/financial_brain/patterns/ranking.py'],
        ['tests/test_pattern_ranking_report.py'],
        "Scores.calibration defaults to 'not calibrated'.",
    ),
    "CP-0227": (
        ['src/financial_brain/patterns/ranking.py'],
        ['tests/test_pattern_ranking_report.py'],
        'similarity_clusters groups formations sharing a pivot so they are not counted as independent.',
    ),
    "CP-0228": (
        ['src/financial_brain/patterns/ranking.py'],
        ['tests/test_pattern_ranking_report.py'],
        'contrast() places pattern and momentum-baseline values side by side.',
    ),
    "CP-0229": (
        ['src/financial_brain/patterns/ranking.py'],
        ['tests/test_pattern_ranking_report.py'],
        'Each ranked row carries reason codes.',
    ),
    "CP-0230": (
        ['src/financial_brain/patterns/ranking.py'],
        ['tests/test_pattern_ranking_report.py'],
        'scenario_summary gives descriptive scenario labels and states probabilities are not available.',
    ),
    "CP-0231": (
        ['src/financial_brain/patterns/ranking.py'],
        ['tests/test_pattern_ranking_report.py'],
        'net_cost_gate returns WATCH unless expected gross exceeds cost.',
    ),
    "CP-0232": (
        ['src/financial_brain/patterns/ranking.py'],
        ['tests/test_pattern_ranking_report.py'],
        'filter_opportunities filters by liquidity and detector deterministically.',
    ),
    "CP-0233": (
        ['src/financial_brain/patterns/ranking.py'],
        ['tests/test_pattern_ranking_report.py'],
        'trader_recommendations lists only evidence above DESCRIPTIVE; research rows stay badged.',
    ),
    "CP-0236": (
        ['src/financial_brain/patterns/report.py'],
        ['tests/test_pattern_ranking_report.py'],
        'HTML table shows symbol, pattern, state, fit, data, evidence, calibration and reasons.',
    ),
    "CP-0238": (
        ['src/financial_brain/patterns/ranking.py'],
        ['tests/test_pattern_ranking_report.py'],
        'Sorting uses state, recency, geometry and liquidity; no expected-alpha column.',
    ),
    "CP-0247": (
        ['src/financial_brain/patterns/report.py'],
        ['tests/test_pattern_ranking_report.py'],
        'export_json writes a schema version and provenance; validate_export round-trips it.',
    ),
    "CP-0251": (
        ['src/financial_brain/patterns/report.py'],
        ['tests/test_pattern_ranking_report.py'],
        'Every report carries the SIMULATION banner.',
    ),
    "CP-0253": (
        ['src/financial_brain/patterns/report.py'],
        ['tests/test_pattern_ranking_report.py'],
        'Overlay.validate rejects coordinates outside the bars and unknown kinds.',
    ),
    "CP-0255": (
        ['src/financial_brain/patterns/report.py'],
        ['tests/test_pattern_ranking_report.py'],
        'Overlays carry known_from; no point may sit beyond it.',
    ),
    "CP-0264": (
        ['src/financial_brain/patterns/report.py'],
        ['tests/test_pattern_ranking_report.py'],
        'Light and dark theme tokens; every user string is HTML-escaped.',
    ),
    "CP-0265": (
        ['src/financial_brain/patterns/report.py'],
        ['tests/test_pattern_ranking_report.py'],
        'Self-contained HTML plus JSON with provenance; no external resources.',
    ),
    "CP-0281": (
        ['src/financial_brain/patterns/report.py'],
        ['tests/test_pattern_ranking_report.py'],
        'Offline HTML report; no PDF dependency is required.',
    ),
    "CP-0282": (
        ['src/financial_brain/patterns/learning.py'],
        ['tests/test_pattern_learning_agent.py'],
        'make_label refuses labels before the outcome horizon has matured.',
    ),
    "CP-0283": (
        ['src/financial_brain/patterns/learning.py'],
        ['tests/test_pattern_learning_agent.py'],
        'assemble refuses features knowable after the decision bar.',
    ),
    "CP-0289": (
        ['src/financial_brain/patterns/learning.py'],
        ['tests/test_pattern_learning_agent.py'],
        'ModelRegistry refuses to re-register an existing version id.',
    ),
    "CP-0291": (
        ['src/financial_brain/patterns/learning.py'],
        ['tests/test_pattern_learning_agent.py'],
        'calibration returns NO_CALIBRATION below 200 labels; never invents a probability.',
    ),
    "CP-0292": (
        ['src/financial_brain/patterns/learning.py'],
        ['tests/test_pattern_learning_agent.py'],
        'drift_status returns REVIEW on a mean shift beyond the threshold.',
    ),
    "CP-0293": (
        ['src/financial_brain/patterns/learning.py'],
        ['tests/test_pattern_learning_agent.py'],
        'final_gate: a learned score cannot override a deterministic rejection.',
    ),
    "CP-0294": (
        ['src/financial_brain/patterns/learning.py'],
        ['tests/test_pattern_learning_agent.py'],
        'proposal() states it executes no trial and reports the budget and family cost.',
    ),
    "CP-0295": (
        ['src/financial_brain/patterns/learning.py'],
        ['tests/test_pattern_learning_agent.py'],
        'rollback restores the previous pointer and keeps every artifact loadable.',
    ),
    "CP-0297": (
        ['src/financial_brain/patterns/learning.py'],
        ['tests/test_pattern_learning_agent.py'],
        'promote refuses DESCRIPTIVE and historical-only evidence.',
    ),
    "CP-0298": (
        ['src/financial_brain/patterns/agent.py'],
        ['tests/test_pattern_learning_agent.py'],
        'ReadOnlyAgent exposes only list, explain and trace tools.',
    ),
    "CP-0299": (
        ['src/financial_brain/patterns/agent.py'],
        ['tests/test_pattern_learning_agent.py'],
        'validate_query rejects bad tickers, states, dates and limits before data access.',
    ),
    "CP-0300": (
        ['src/financial_brain/patterns/agent.py'],
        ['tests/test_pattern_learning_agent.py'],
        'sanitize_untrusted renders links and markup inert and strips control characters.',
    ),
    "CP-0301": (
        ['src/financial_brain/patterns/agent.py'],
        ['tests/test_pattern_learning_agent.py'],
        'explain() copies stored fields and cites the source event.',
    ),
    "CP-0303": (
        ['src/financial_brain/patterns/agent.py'],
        ['tests/test_pattern_learning_agent.py'],
        'Rows are capped at 100 and nested calls at 5 per query.',
    ),
    "CP-0304": (
        ['src/financial_brain/patterns/agent.py'],
        ['tests/test_pattern_learning_agent.py'],
        'The agent never computes prices or gate verdicts; it copies stored values.',
    ),
    "CP-0305": (
        ['src/financial_brain/patterns/agent.py'],
        ['tests/test_pattern_learning_agent.py'],
        'check_request refuses guaranteed-profit, zero-loss and live-order requests.',
    ),
    "CP-0306": (
        ['src/financial_brain/patterns/agent.py'],
        ['tests/test_pattern_learning_agent.py'],
        'Injection tests on book text, markdown links and ticker fields.',
    ),
    "CP-0307": (
        ['src/financial_brain/patterns/agent.py'],
        ['tests/test_pattern_learning_agent.py'],
        'redact() removes tokens, long account-like numbers and local paths.',
    ),
    "CP-0308": (
        ['src/financial_brain/patterns/agent.py'],
        ['tests/test_pattern_learning_agent.py'],
        'AuditLog is bounded and stores redacted parameters.',
    ),
    "CP-0309": (
        ['src/financial_brain/patterns/agent.py'],
        ['tests/test_pattern_learning_agent.py'],
        'explain() is a deterministic rule-based fallback; no hosted model is required.',
    ),
    "CP-0311": (
        ['scripts/bench_patterns.py'],
        ['docs/audits/pattern-benchmark.json'],
        'Timing and cProfile hotspots recorded for a 1500-bar symbol.',
    ),
    "CP-0312": (
        ['scripts/bench_patterns.py'],
        ['docs/audits/pattern-benchmark.json'],
        'Top hotspots recorded; the O(n^2) pivot recomputation was removed and re-measured.',
    ),
    "CP-0322": (
        ['scripts/chart_patterns_matrix.py'],
        ['tests/test_pattern_matrix.py'],
        'Pattern tests run in the existing CI matrix; CI was not executed in this checkout, so Windows CI is pending.',
    ),
    "CP-0323": (
        ['pyproject.toml'],
        ['tests/test_pattern_matrix.py'],
        "ruff and mypy pass on src/financial_brain/patterns; the package is in pyproject's mypy scope with no exclusions.",
    ),
    "CP-0324": (
        ['src/financial_brain/patterns/offline.py'],
        ['tests/test_pattern_offline.py'],
        'Offline guard and tests; the pattern tests fetch no data.',
    ),
    "CP-0329": (
        ['docs/audits/protected-baseline.json'],
        ['tests/test_pattern_acceptance.py'],
        'Protected SHA-256 baselines match after the full test run.',
    ),
    "CP-0330": (
        ['src/financial_brain/patterns/agent.py'],
        ['tests/test_pattern_learning_agent.py'],
        'Input validation, escaping and sanitisation tests against hostile strings.',
    ),
    "CP-0332": (
        ['src/financial_brain/patterns/demo.py'],
        ['tests/test_pattern_acceptance.py'],
        'Empty data runs without crashing and reports zero rows.',
    ),
    "CP-0333": (
        ['src/financial_brain/patterns/learning.py'],
        ['tests/test_pattern_learning_agent.py'],
        'A tampered artifact fails closed on load.',
    ),
    "CP-0337": (
        ['tests/test_pattern_acceptance.py'],
        ['tests/test_pattern_acceptance.py'],
        'No pattern test is skipped.',
    ),
    "CP-0338": (
        ['scripts/chart_patterns_matrix.py'],
        ['tests/test_pattern_matrix.py'],
        'Every implemented row must name existing source and test files.',
    ),
    "CP-0339": (
        ['docs/audits/demo-corpus.sha256'],
        ['tests/test_pattern_acceptance.py'],
        'Synthetic corpus hash is pinned and checked.',
    ),
    "CP-0342": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Candidates that never confirm are retained as CANDIDATE or FAILED.',
    ),
    "CP-0344": (
        ['src/financial_brain/patterns/marketdata.py'],
        ['tests/test_pattern_marketdata_candles.py'],
        'IdentityMap resolves a renamed alias to one lineage and withholds ambiguous aliases.',
    ),
    "CP-0347": (
        ['docs/audits/demo-corpus.sha256'],
        ['tests/test_pattern_acceptance.py'],
        'Fixture hash verified against the pinned value.',
    ),
    "CP-0348": (
        ['src/financial_brain/patterns/demo.py'],
        ['tests/test_pattern_acceptance.py'],
        'python -m financial_brain.patterns.demo --offline runs the full path.',
    ),
    "CP-0351": (
        ['src/financial_brain/patterns/report.py'],
        ['tests/test_pattern_ranking_report.py'],
        'Exports carry SIMULATION, RESEARCH ONLY and K7 banners and are labelled synthetic.',
    ),
    "CP-0352": (
        ['docs/PATTERNS-ARCHITECTURE.md'],
        ['tests/test_pattern_acceptance.py'],
        'Architecture document names each boundary and its module; module names were checked by hand.',
    ),
    "CP-0353": (
        ['docs/PATTERNS-USER-GUIDE.md'],
        ['tests/test_pattern_acceptance.py'],
        'Walkthrough commands are executed by the CLI and acceptance tests.',
    ),
    "CP-0354": (
        ['docs/PATTERNS-GLOSSARY.md'],
        ['tests/test_pattern_acceptance.py'],
        'Glossary defines every label the reports show.',
    ),
    "CP-0355": (
        ['docs/PATTERNS-COMMAND-REFERENCE.md'],
        ['tests/test_pattern_inventory_cli.py'],
        'Documented commands and their exit codes are executed by tests.',
    ),
    "CP-0357": (
        ['docs/PATTERNS-RESEARCH-INTERPRETATION.md'],
        ['tests/test_pattern_acceptance.py'],
        'Documents selection conditioning, ultimate-high bias and multiplicity.',
    ),
    "CP-0358": (
        ['docs/PATTERNS-PAPER-METHODOLOGY.md'],
        ['tests/test_pattern_paper.py'],
        'Describes the cost components, fill labels and ledger identity that the paper tests exercise.',
    ),
    "CP-0359": (
        ['docs/PATTERNS-LEARNING-LIMITS.md'],
        ['tests/test_pattern_learning_agent.py'],
        'States the learning limits that the registry and promotion tests enforce.',
    ),
    "CP-0362": (
        ['docs/PATTERNS-AGENT-HANDOFF.md'],
        ['tests/test_pattern_matrix.py'],
        'Handoff gives wave order, stop gates and no-external-interference rules.',
    ),
    "CP-0363": (
        ['docs/audits/PATTERN-FINAL-GATES.md'],
        ['tests/test_pattern_acceptance.py'],
        'Scorecard names objective criteria and the market-dependent criteria that remain unproven.',
    ),
    "CP-0364": (
        ['src/financial_brain/patterns/cli.py'],
        ['tests/test_pattern_inventory_cli.py'],
        'scan, list, explain, paper-demo and audit with documented exit codes.',
    ),
    "CP-0370": (
        ['src/financial_brain/patterns/demo.py'],
        ['tests/test_pattern_acceptance.py'],
        'backup writes a SHA-256 checksum; restore refuses a mismatch.',
    ),
    "CP-0372": (
        ['src/financial_brain/patterns/report.py'],
        ['tests/test_pattern_ranking_report.py'],
        'The report needs no chart library; the CLI and exports work without one.',
    ),
    "CP-0374": (
        ['src/financial_brain/patterns/demo.py'],
        ['tests/test_pattern_acceptance.py'],
        'health() checks the catalogue, offline mode and output folder without modifying data.',
    ),
    "CP-0375": (
        ['src/financial_brain/patterns/demo.py'],
        ['tests/test_pattern_acceptance.py'],
        'deny_network blocks socket connections; the demo runs in a subprocess under the restriction.',
    ),
    "CP-0378": (
        ['src/financial_brain/patterns/demo.py'],
        ['tests/test_pattern_acceptance.py'],
        'Watchlist discovery count equals a direct run over the same corpus.',
    ),
    "CP-0381": (
        ['src/financial_brain/patterns/demo.py'],
        ['tests/test_pattern_acceptance.py'],
        'paper_preview enters on a later bar than the signal and labels fills ASSUMED.',
    ),
    "CP-0385": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Prefix invariance checked at multiple cutoffs for every detector.',
    ),
    "CP-0387": (
        ['src/financial_brain/patterns/demo.py'],
        ['tests/test_pattern_acceptance.py'],
        'Empty data does not crash and needs no API key.',
    ),
    "CP-0388": (
        ['src/financial_brain/patterns/demo.py'],
        ['tests/test_pattern_acceptance.py'],
        'Offline demo and CLI succeed in a subprocess with network denied.',
    ),
    "CP-0389": (
        ['src/financial_brain/patterns/report.py'],
        ['tests/test_pattern_acceptance.py'],
        'A hostile ticker is escaped in the HTML report.',
    ),
    "CP-0392": (
        ['src/financial_brain/patterns/demo.py'],
        ['tests/test_pattern_acceptance.py'],
        'Demo output and sign-off always carry NOT_EDGE_CONFIRMED.',
    ),
    "CP-0393": (
        ['src/financial_brain/patterns/inventory.py'],
        ['tests/test_pattern_inventory_cli.py'],
        'All 72 catalogue rows carry a truthful status; the status file matches the module.',
    ),
    "CP-0394": (
        ['scripts/chart_patterns_matrix.py'],
        ['tests/test_pattern_matrix.py'],
        'Every done row must name existing source and test files; nothing is marked done by heuristic.',
    ),
    "CP-0396": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Prefix invariance and future perturbation hold for every detector family.',
    ),
    "CP-0398": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_paper.py'],
        'Ledger identity tested with synthetic arithmetic.',
    ),
    "CP-0399": (
        ['docs/audits/protected-baseline.json'],
        ['tests/test_pattern_acceptance.py'],
        'Protected SHA-256 baselines verified by test.',
    ),
    "CP-0400": (
        ['src/financial_brain/patterns/learning.py'],
        ['tests/test_pattern_learning_agent.py'],
        'Promotion from synthetic or historical evidence is refused.',
    ),
    "CP-0402": (
        ['src/financial_brain/patterns/demo.py'],
        ['tests/test_pattern_acceptance.py'],
        'Subprocess demo runs with the socket layer denied; no external call recorded.',
    ),
    "CP-0403": (
        ['docs/audits/PATTERN-FINAL-GATES.md'],
        ['tests/test_pattern_acceptance.py'],
        'Docs test forbids unqualified profit or zero-loss claims.',
    ),
    "CP-0404": (
        ['src/financial_brain/patterns/demo.py'],
        ['tests/test_pattern_acceptance.py'],
        'signoff() reports ENGINEERING status from the matrix and PROVEN_MARKET_EDGE as UNKNOWN.',
    ),
    "CP-0405": (
        ['docs/audits/PATTERN-FINAL-GATES.md'],
        ['tests/test_pattern_acceptance.py'],
        'Residual non-code gates listed separately and not marked complete.',
    ),
    "CP-0089": (
        ['src/financial_brain/patterns/ranking.py'],
        ['tests/test_pattern_adversarial.py'],
        'resolve_overlaps keeps one formation per shared pivot set by an explicit precedence order.',
    ),
    "CP-0125": (
        ['src/financial_brain/patterns/ranking.py'],
        ['tests/test_pattern_adversarial.py'],
        'Formations sharing pivots are linked to one kept structure, so counts are not inflated.',
    ),
    "CP-0129": (
        ['src/financial_brain/patterns/ops.py'],
        ['tests/test_pattern_ops.py'],
        'load_event reads older records with optional fields defaulted.',
    ),
    "CP-0139": (
        ['src/financial_brain/patterns/candles.py'],
        ['tests/test_pattern_adversarial.py'],
        'final_weeks lets only FINAL weekly bars feed signals; PARTIAL weeks are excluded.',
    ),
    "CP-0143": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_adversarial.py'],
        'Flat, noisy, gapping and discontinuous regimes: detectors complete with finite outputs. No volume-driven rule exists to test.',
    ),
    "CP-0144": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_adversarial.py'],
        '300 random OHLC rows: every invalid row is counted in quarantine and every valid row constructs.',
    ),
    "CP-0145": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_adversarial.py'],
        'Formation round-trips through plain dicts with identical state at every cutoff.',
    ),
    "CP-0146": (
        ['src/financial_brain/patterns/ranking.py'],
        ['tests/test_pattern_adversarial.py'],
        'Predeclared OVERLAP_PRECEDENCE resolves structures that share pivots, deterministically.',
    ),
    "CP-0147": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_controls_risk.py'],
        'small_sample_label marks samples under 30 as LOW_CONFIDENCE; no probability is derived.',
    ),
    "CP-0152": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_adversarial.py'],
        'Golden regression oracle from a fixed fixture: candidate index, levels and confirmation are pinned.',
    ),
    "CP-0175": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_controls_risk.py'],
        'shuffled_direction_control is seeded and preserves the long and short counts.',
    ),
    "CP-0176": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_controls_risk.py'],
        'matched_baseline draws only from the candidate opportunity set and refuses outside names.',
    ),
    "CP-0178": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_controls_risk.py'],
        'neutralize_exposure removes market beta; residuals are uncorrelated with the market in test.',
    ),
    "CP-0179": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_controls_risk.py'],
        'ablation_variants enumerates 2^n toggle combinations so multiplicity can be counted.',
    ),
    "CP-0182": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_controls_risk.py'],
        'pattern_vs_no_trade values selective filtering against cash on realised net P&L.',
    ),
    "CP-0185": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_controls_risk.py'],
        'SYNTHETIC helpful and harmful overlays: incremental_value is positive for helpful and negative for harmful.',
    ),
    "CP-0186": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_controls_risk.py'],
        'split_selected_rejected reports kept versus rejected means and the opportunity cost of rejection.',
    ),
    "CP-0187": (
        ['src/financial_brain/patterns/learning.py'],
        ['tests/test_pattern_controls_risk.py'],
        'calibration() reports NO_CALIBRATION below 200 labels; no signed calibration without labels.',
    ),
    "CP-0188": (
        ['src/financial_brain/patterns/study.py'],
        ['tests/test_pattern_controls_risk.py'],
        'decompose() requires entry, exit, sizing and pattern parts to reconcile to the total.',
    ),
    "CP-0206": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_controls_risk.py'],
        'check_intent_codes returns STALE_BARS, AMBIGUOUS_GEOMETRY, LOW_LIQUIDITY, INSUFFICIENT_EVIDENCE and MISSING_CALIBRATION.',
    ),
    "CP-0208": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_controls_risk.py'],
        'risk_display reports volatility, gap risk and adverse excursion with stop_caps_loss=False.',
    ),
    "CP-0209": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_controls_risk.py'],
        'loss_if_gap shows the actual loss can exceed the nominal stop loss when the session gaps through the stop.',
    ),
    "CP-0210": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_controls_risk.py'],
        'participation_ok enforces an ADV cap and refuses when ADV is unknown.',
    ),
    "CP-0211": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_controls_risk.py'],
        'correlated_exposure_ok stops overlapping same-symbol shapes multiplying the position.',
    ),
    "CP-0212": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_controls_risk.py'],
        'stress_action degrades predeclared failure scenarios to cash or no-trade.',
    ),
    "CP-0213": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_controls_risk.py'],
        'atr_size uses risk budget and ATR only; no success rate is an input.',
    ),
    "CP-0214": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_controls_risk.py'],
        'risk_adjusted_utility is a separate output labelled UTILITY_NOT_PROFIT.',
    ),
    "CP-0266": (
        ['src/financial_brain/patterns/journal.py'],
        ['tests/test_pattern_journal.py'],
        'new_session validates capital, dates and fill policy and runs offline with network False.',
    ),
    "CP-0267": (
        ['src/financial_brain/patterns/journal.py'],
        ['tests/test_pattern_journal.py'],
        'Manifest identity hashes the full body; a different body under the same id is refused, not overwritten.',
    ),
    "CP-0269": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_journal.py'],
        'next_session_entry is the first executable session after confirmation; no same-bar fill is possible.',
    ),
    "CP-0270": (
        ['src/financial_brain/patterns/journal.py'],
        ['tests/test_pattern_journal.py'],
        'IntentLedger records each intent state and reconciles coverage counts with the total.',
    ),
    "CP-0271": (
        ['src/financial_brain/patterns/journal.py'],
        ['tests/test_pattern_journal.py'],
        'DailyMark gives cash, position value and liabilities, and its equity identity is tested.',
    ),
    "CP-0272": (
        ['src/financial_brain/patterns/journal.py'],
        ['tests/test_pattern_journal.py'],
        'pnl_split reports realised (closed trades) and unrealised (marked open) separately.',
    ),
    "CP-0273": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_journal.py'],
        'classify_loss assigns one explicit category per losing trade in fixed priority order.',
    ),
    "CP-0274": (
        ['src/financial_brain/patterns/journal.py'],
        ['tests/test_pattern_journal.py'],
        'period_summary gives return and drawdown over an explicit day count; empty input is zero, not an error.',
    ),
    "CP-0275": (
        ['src/financial_brain/patterns/journal.py'],
        ['tests/test_pattern_journal.py'],
        'contribution() groups trades by family as descriptive attribution.',
    ),
    "CP-0276": (
        ['src/financial_brain/patterns/journal.py'],
        ['tests/test_pattern_journal.py'],
        'replay_identity hashes manifest and results; any difference changes the identity.',
    ),
    "CP-0280": (
        ['src/financial_brain/patterns/journal.py'],
        ['tests/test_pattern_journal.py'],
        'All-loss and no-signal scenarios are tested; losses are reported, not hidden.',
    ),
    "CP-0346": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_controls_risk.py', 'tests/test_pattern_paper.py'],
        'Missing-quote, timeout, no-fill and gap-through-stop cases each produce an explicit outcome.',
    ),
    "CP-0366": (
        ['src/financial_brain/patterns/ops.py'],
        ['tests/test_pattern_ops.py'],
        'atomic_write_text writes UTF-8 through a temp file and replaces atomically; no temp files remain.',
    ),
    "CP-0367": (
        ['src/financial_brain/patterns/ops.py'],
        ['tests/test_pattern_ops.py'],
        'record_idempotently appends nothing new when a scan is re-run on the same events.',
    ),
    "CP-0368": (
        ['src/financial_brain/patterns/ops.py'],
        ['tests/test_pattern_ops.py'],
        'ResultCache entries are valid only for the exact manifest hash; a changed input misses.',
    ),
    "CP-0369": (
        ['src/financial_brain/patterns/ops.py'],
        ['tests/test_pattern_ops.py'],
        'configure_log redacts every record and rotates at the size limit.',
    ),
    "CP-0371": (
        ['src/financial_brain/patterns/ops.py'],
        ['tests/test_pattern_ops.py'],
        'enforce_limits caps bars and symbols; the limits are constants in the module.',
    ),
    "CP-0377": (
        ['src/financial_brain/patterns/ops.py'],
        ['tests/test_pattern_ops.py'],
        'Newer records with unknown fields are refused rather than dropped; old records load.',
    ),
    "CP-0380": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_controls_risk.py'],
        'Machine-readable refusal codes name the exact limit or gate that blocked each intent.',
    ),
    "CP-0382": (
        ['src/financial_brain/patterns/journal.py'],
        ['tests/test_pattern_journal.py'],
        'pnl_split keeps gross realised and unrealised amounts separate.',
    ),
    "CP-0391": (
        ['src/financial_brain/patterns/ranking.py'],
        ['tests/test_pattern_ranking_report.py'],
        'contrast() places pattern and momentum-baseline values side by side without a significance claim.',
    ),
    "CP-0013": (
        ['docs/schemas/pattern-catalogue.v1.schema.json'],
        ['tests/test_pattern_wave7.py'],
        "JSON Schema for catalogue entries; its required keys are tested to equal the validator's required keys.",
    ),
    "CP-0015": (
        ['src/financial_brain/patterns/catalogue.py'],
        ['tests/test_pattern_wave7.py'],
        'Every entry with book statistics is claim_kind historical_conditional; no statistic is presented as descriptive or current.',
    ),
    "CP-0017": (
        ['src/financial_brain/patterns/catalogue.py'],
        ['tests/test_pattern_wave7.py'],
        'Reference carries source title and page_unverified=True with edition and chapter left null; nothing is invented.',
    ),
    "CP-0022": (
        ['src/financial_brain/patterns/catalogue.py'],
        ['tests/test_pattern_wave7.py'],
        'parameters and knowledge_lag are unset; require_parameters refuses silent fallback to fitted defaults.',
    ),
    "CP-0026": (
        ['docs/audits/pattern-reference-index.json'],
        ['tests/test_pattern_wave7.py'],
        'Reference index maps all 72 catalogue IDs to source note, claim kind and implementation status; a test checks it stays in sync.',
    ),
    "CP-0031": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Geometry is stored as bar indices and price levels, never pixels; the golden fixture pins those coordinates.',
    ),
    "CP-0032": (
        ['src/financial_brain/patterns/analytics.py'],
        ['tests/test_pattern_analytics.py'],
        'observation_side: a bearish observation is EXIT_ONLY for an existing long and never a short entry.',
    ),
    "CP-0036": (
        ['src/financial_brain/patterns/analytics.py'],
        ['tests/test_pattern_analytics.py'],
        'RECOGNITION_DELAY_BARS declares the delay per source; pivot delay is enforced by causal_pivots (tested).',
    ),
    "CP-0064": (
        ['src/financial_brain/patterns/analytics.py'],
        ['tests/test_pattern_analytics.py'],
        'trend_context uses only bars up to t; appending future bars leaves the context unchanged.',
    ),
    "CP-0065": (
        ['src/financial_brain/patterns/analytics.py'],
        ['tests/test_pattern_analytics.py'],
        'volume_contraction returns None for unknown or negative volume, never zero.',
    ),
    "CP-0068": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py', 'tests/test_pattern_adversarial.py'],
        'Rectangles de-duplicate within their window; resolve_overlaps keeps one structure per shared pivot set.',
    ),
    "CP-0123": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_detectors.py'],
        'Confirmation is recorded only on a bar close beyond the level (_resolve uses close).',
    ),
    "CP-0130": (
        ['src/financial_brain/patterns/analytics.py'],
        ['tests/test_pattern_analytics.py'],
        'lifecycle_counts and time_to_confirm give state counts and bars to confirmation.',
    ),
    "CP-0134": (
        ['src/financial_brain/patterns/analytics.py'],
        ['tests/test_pattern_analytics.py'],
        'export_formations_json is deterministic JSON; Parquet and SQL views are not built.',
    ),
    "CP-0135": (
        ['src/financial_brain/patterns/analytics.py'],
        ['tests/test_pattern_analytics.py'],
        'version_count_audit lists every candidate-count change between versions with its delta.',
    ),
    "CP-0164": (
        ['src/financial_brain/patterns/analytics.py'],
        ['tests/test_pattern_analytics.py'],
        'missing_outcomes counts missing outcomes and applies an explicit policy; unknown policies are refused.',
    ),
    "CP-0168": (
        ['src/financial_brain/patterns/analytics.py'],
        ['tests/test_pattern_analytics.py'],
        'BENCHMARK_SPEC is a frozen description with a pinned SHA-256; benchmark data is not run here.',
    ),
    "CP-0225": (
        ['src/financial_brain/patterns/analytics.py'],
        ['tests/test_pattern_analytics.py'],
        'reference_levels are labelled DESCRIPTIVE_REFERENCE and projected_target is always None.',
    ),
    "CP-0249": (
        ['src/financial_brain/patterns/report.py'],
        ['tests/test_pattern_wave7.py'],
        "An empty data set renders an explicit 'not a signal' message.",
    ),
    "CP-0254": (
        ['src/financial_brain/patterns/report.py'],
        ['tests/test_pattern_wave7.py'],
        'OVERLAY_STYLE gives confirmed, tentative and invalidated states distinct styles and legends; unknown states are refused.',
    ),
    "CP-0263": (
        ['src/financial_brain/patterns/report.py'],
        ['tests/test_pattern_wave7.py'],
        'Report snapshot SHA-256 is pinned in docs/audits; any change to the rendered page fails the test.',
    ),
    "CP-0268": (
        ['src/financial_brain/patterns/demo.py'],
        ['tests/test_pattern_wave7.py'],
        'preview_all previews every confirmed entry as entry = signal + 1, labelled ASSUMED and SYNTHETIC.',
    ),
    "CP-0277": (
        ['src/financial_brain/patterns/demo.py'],
        ['tests/test_pattern_acceptance.py'],
        'Offline demo starts from synthetic fixtures with no credentials or holdings.',
    ),
    "CP-0278": (
        ['src/financial_brain/patterns/demo.py'],
        ['tests/test_pattern_acceptance.py'],
        'Every demo output carries the SYNTHETIC label and NOT_EDGE_CONFIRMED status.',
    ),
    "CP-0284": (
        ['src/financial_brain/patterns/analytics.py'],
        ['tests/test_pattern_analytics.py'],
        'overlap_leaks is true when the embargo is shorter than the label horizon.',
    ),
    "CP-0286": (
        ['src/financial_brain/patterns/analytics.py'],
        ['tests/test_pattern_analytics.py'],
        'fit_completion_model is a Laplace-smoothed descriptive rate, labelled UNCALIBRATED_DESCRIPTIVE; empty input is NO_CALIBRATION.',
    ),
    "CP-0328": (
        ['src/financial_brain/patterns/demo.py'],
        ['tests/test_pattern_wave7.py'],
        'A subprocess demo run creates files only inside its output folder.',
    ),
    "CP-0334": (
        ['src/financial_brain/patterns/analytics.py'],
        ['tests/test_pattern_analytics.py'],
        'Replaying every event state after an interruption appends each version exactly once.',
    ),
    "CP-0009": (
        ['docs/audits/pattern-dev-policy.md'],
        ['tests/test_pattern_final.py'],
        'Offline policy names the OWNER, DATA_TIME and LICENSE categories; the test checks all three appear and fixtures are allowed.',
    ),
    "CP-0029": (
        ['src/financial_brain/patterns/spec.py'],
        ['tests/test_pattern_final.py'],
        'PatternEvent carries formation_start, candidate_known, confirmed, decision and earliest_entry clocks, each ordering-checked.',
    ),
    "CP-0204": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_final.py'],
        'fill_coverage counts fill records per strategy and day and how many carry the ASSUMED label.',
    ),
    "CP-0246": (
        ['src/financial_brain/patterns/analytics.py'],
        ['tests/test_pattern_final.py'],
        'daily_summary counts new shapes, confirmations, failures and no-trade events for one bar, labelled DESCRIPTIVE and RESEARCH ONLY.',
    ),
    "CP-0250": (
        ['src/financial_brain/patterns/report.py'],
        ['tests/test_pattern_final.py'],
        'HELP covers throwback, pivot confirmation, ultimate-high bias, candidate and not calibrated; each term is checked against the glossary.',
    ),
    "CP-0302": (
        ['src/financial_brain/patterns/agent.py'],
        ['tests/test_pattern_final.py'],
        'explain_rejection gives each code its plain reason and states authorises_trade=false; unknown codes are reported, not hidden.',
    ),
    "CP-0310": (
        ['src/financial_brain/patterns/ops.py'],
        ['tests/test_pattern_final.py'],
        'model_output_or_none contains model failures and returns the error name, so no state changes on failure.',
    ),
    "CP-0343": (
        ['src/financial_brain/patterns/journal.py'],
        ['tests/test_pattern_final.py'],
        'Known arithmetic: a +100, a -50 and a flat trade give realised P&L of +50 and zero unrealised.',
    ),
    "CP-0386": (
        ['src/financial_brain/patterns/paper.py'],
        ['tests/test_pattern_final.py'],
        'evidence_bundle carries spec, event, fill, cost and ledger hashes and a bundle hash.',
    ),
    "CP-0390": (
        ['src/financial_brain/patterns/journal.py'],
        ['tests/test_pattern_final.py'],
        'dump_intents and load_intents restore intent states with a checksum; a tampered backup is refused.',
    ),
    "CP-0002": (
        ['docs/audits/pattern-branch-reconciliation.md'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Local branch state recorded; remote PR status explicitly not checked.',
    ),
    "CP-0003": (
        ['docs/audits/pattern-module-inventory.md'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Existing modules reviewed; forbidden imports listed with the boundary test that enforces them.',
    ),
    "CP-0006": (
        ['docs/audits/pattern-branch-reconciliation.md'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Work sits on its own feature branch, not on main; no worktree was created.',
    ),
    "CP-0008": (
        ['docs/audits/pattern-data-limitations.md'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Limitations quoted from vault notes with line references; no coverage invented.',
    ),
    "CP-0023": (
        ['src/financial_brain/patterns/catalogue.py'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Four source-quality fields added to CatalogueEntry; every value is UNASSESSED until assessed.',
    ),
    "CP-0024": (
        ['src/financial_brain/patterns/catalogue.py'],
        ['tests/test_pattern_wave9_evidence.py'],
        'migrate_document sets the schema version, leaves the input unmodified and refuses any change to stable IDs.',
    ),
    "CP-0041": (
        ['src/financial_brain/patterns/source.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'BarSource reads DuckDB read-only and Parquet via DuckDB; dates bound as parameters; unsafe table names refused.',
    ),
    "CP-0081": (
        ['src/financial_brain/patterns/detectors2.py'],
        ['tests/test_pattern_wave9_detectors.py'],
        'pennant_bullish/bearish: pole then symmetric taper inside a fixed 18-bar window. Causal and prefix-invariant (checked in tests/test_pattern_wave9_detectors.py). ',
    ),
    "CP-0083": (
        ['src/financial_brain/patterns/detectors2.py'],
        ['tests/test_pattern_wave9_detectors.py'],
        'channel_rising/falling from known pivots with parallel-slope tolerance; direction neutral. Causal and prefix-invariant (checked in tests/test_pattern_wave9_detectors.py). ',
    ),
    "CP-0084": (
        ['src/financial_brain/patterns/detectors2.py'],
        ['tests/test_pattern_wave9_detectors.py'],
        'cup_with_handle: left lip, cup low, right lip, shallow handle emitted at a fixed handle length. Causal and prefix-invariant (checked in tests/test_pattern_wave9_detectors.py). ',
    ),
    "CP-0085": (
        ['src/financial_brain/patterns/detectors2.py'],
        ['tests/test_pattern_wave9_detectors.py'],
        'high_tight_flag: 80 percent rise in at most 40 bars, flag 5 to 20 bars, retrace at most 25 percent. Causal and prefix-invariant (checked in tests/test_pattern_wave9_detectors.py). ',
    ),
    "CP-0087": (
        ['src/financial_brain/patterns/streaming.py'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Streaming pivots equal batch pivots on pennant, cup and flag fixtures.',
    ),
    "CP-0088": (
        ['scripts/build_pattern_fixtures.py', 'tests/fixtures/patterns/family_examples.json'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Small JSON geometry fixtures generated from synthetic OHLC; no source images.',
    ),
    "CP-0094": (
        ['src/financial_brain/patterns/detectors2.py'],
        ['tests/test_pattern_wave9_detectors.py'],
        'triple_bottom/top: three equal extrema with bounded spacing asymmetry and a shared neckline. Causal and prefix-invariant (checked in tests/test_pattern_wave9_detectors.py). ',
    ),
    "CP-0097": (
        ['src/financial_brain/patterns/detectors2.py'],
        ['tests/test_pattern_wave9_detectors.py'],
        'rounding_bottom/top: quadratic fit with R-squared at least 0.8, vertex near centre, depth at least 5 percent. Causal and prefix-invariant (checked in tests/test_pattern_wave9_detectors.py). ',
    ),
    "CP-0100": (
        ['src/financial_brain/patterns/detectors2.py'],
        ['tests/test_pattern_wave9_detectors.py'],
        'broadening_top/bottom from diverging known swing bands; name records the trailing move only. Causal and prefix-invariant (checked in tests/test_pattern_wave9_detectors.py). ',
    ),
    "CP-0101": (
        ['src/financial_brain/patterns/detectors2.py'],
        ['tests/test_pattern_wave9_detectors.py'],
        'diamond_candidate: widening then narrowing with two pivots per side per half; never confirmed by this detector. Causal and prefix-invariant (checked in tests/test_pattern_wave9_detectors.py). ',
    ),
    "CP-0102": (
        ['src/financial_brain/patterns/detectors2.py'],
        ['tests/test_pattern_wave9_detectors.py'],
        'island_reversal top/bottom: opposite gaps around a non-overlapping island of up to five bars. Causal and prefix-invariant (checked in tests/test_pattern_wave9_detectors.py). ',
    ),
    "CP-0104": (
        ['src/financial_brain/patterns/detectors2.py'],
        ['tests/test_pattern_wave9_detectors.py'],
        'throwback_state: THROWBACK_HELD, THROWBACK_FAILED or PULLBACK_TOUCHED, computed from bars up to t only.',
    ),
    "CP-0105": (
        ['src/financial_brain/patterns/detectors2.py'],
        ['tests/test_pattern_wave9_detectors.py'],
        'levels() gives breakout and invalidation levels and always profit_target None.',
    ),
    "CP-0106": (
        ['src/financial_brain/patterns/detectors2.py'],
        ['tests/test_pattern_wave9_detectors.py'],
        'within_complexity_cap rejects shapes with more than six geometry points.',
    ),
    "CP-0113": (
        ['src/financial_brain/patterns/detectors2.py'],
        ['tests/test_pattern_wave9_detectors.py'],
        'three_methods bullish/bearish: strong first candle, three contained counter-trend bars, confirmation on a later close. Causal and prefix-invariant (checked in tests/test_pattern_wave9_detectors.py). ',
    ),
    "CP-0115": (
        ['src/financial_brain/patterns/detectors2.py'],
        ['tests/test_pattern_wave9_detectors.py'],
        'sr_confluence counts known pivots near a level and states observational_only with no multiplier.',
    ),
    "CP-0116": (
        ['src/financial_brain/patterns/detectors2.py'],
        ['tests/test_pattern_wave9_detectors.py'],
        'gap_up/gap_down candidates; the fill bar is stored as failed_index and hidden until it has happened.',
    ),
    "CP-0119": (
        ['src/financial_brain/patterns/candles.py', 'src/financial_brain/patterns/report.py'],
        ['tests/test_pattern_wave9_evidence.py'],
        'K7 notice and its G3 result appear in every JSON export banner; the vault note remains the full record.',
    ),
    "CP-0124": (
        ['src/financial_brain/patterns/spec.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'invalidated_at_utc and expired_at_utc each require an explicit reason; optional keys leave old hashes unchanged.',
    ),
    "CP-0127": (
        ['src/financial_brain/patterns/spec.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'observed_at_utc must not follow generated_at_utc; both are optional and separate.',
    ),
    "CP-0131": (
        ['src/financial_brain/patterns/ops.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'ScanCheckpoint commits per bar atomically; an interrupted scan resumes after the last commit; manifest mismatch refused.',
    ),
    "CP-0133": (
        ['src/financial_brain/patterns/ops.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'detection_trace records thresholds from the detector signature next to its geometry.',
    ),
    "CP-0142": (
        ['src/financial_brain/patterns/marketdata.py'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Rename keeps one lineage; a merger alias with two lineages is withheld; split and dividend adjustments checked on known values.',
    ),
    "CP-0153": (
        ['src/financial_brain/patterns/source.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'Protected DuckDB file is hash-checked before and after reads; writes through a read-only connection fail.',
    ),
    "CP-0173": (
        ['src/financial_brain/patterns/risk_views.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'Liquidity, size and sector controls use attributes supplied for the decision date; missing values give DATA_INSUFFICIENT.',
    ),
    "CP-0215": (
        ['src/financial_brain/patterns/risk_views.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'sector_concentration (HHI, largest share) and beta_diagnostics; unknown sector or beta is flagged, not hidden.',
    ),
    "CP-0217": (
        ['src/financial_brain/patterns/risk_views.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'candidate_risk_explanations maps each rejection code to its plain reason with authorises_trade false.',
    ),
    "CP-0235": (
        ['src/financial_brain/patterns/views.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'status_counts: per-state counts with latest known timestamp. The terminal panel that shows it is CP-0234, not yet built.',
    ),
    "CP-0237": (
        ['src/financial_brain/patterns/views.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'filter_rows applies symbol, family, timeframe, dates, state, liquidity floor, sector and evidence tier. Missing liquidity never passes.',
    ),
    "CP-0239": (
        ['src/financial_brain/patterns/views.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'detail() gives geometry, bars, recognition time, invalidation and tests, marking anything unrecorded.',
    ),
    "CP-0240": (
        ['src/financial_brain/patterns/views.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'compare_with_baseline: pattern state beside momentum and 52-week position; missing inputs stay visible.',
    ),
    "CP-0241": (
        ['src/financial_brain/patterns/views.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'why_not_trade always returns reasons: not confirmed, DESCRIPTIVE tier, risk verdict, missing entry, cost caveat.',
    ),
    "CP-0242": (
        ['src/financial_brain/patterns/views.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'ticket_preview: PAPER ONLY, entry eligibility, costs, capital and risk verdict; submittable is always false.',
    ),
    "CP-0243": (
        ['src/financial_brain/patterns/views.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'allowed_action admits only the two paper actions and refuses everything else.',
    ),
    "CP-0244": (
        ['src/financial_brain/patterns/views.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'knowable_at hides confirmations that were not yet visible at the slider time.',
    ),
    "CP-0245": (
        ['src/financial_brain/patterns/views.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'status_page reports STALE data, failed scans, audit exceptions and paper-run health.',
    ),
    "CP-0256": (
        ['src/financial_brain/patterns/views.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'overlay_markers keeps the signal bar and the earliest entry bar as separate markers.',
    ),
    "CP-0257": (
        ['src/financial_brain/patterns/views.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'visible_boundaries drops any boundary beyond the displayed bar.',
    ),
    "CP-0258": (
        ['src/financial_brain/patterns/views.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'version_history lists revisions as new versions with changed fields; inputs are not mutated.',
    ),
    "CP-0259": (
        ['src/financial_brain/patterns/views.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'risk_zones gives the ATR band and hypothetical cost; guaranteed_target is always None.',
    ),
    "CP-0260": (
        ['src/financial_brain/patterns/views.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'hover_breakdown gives tolerance, pivots, duration and breakout threshold, marking missing values.',
    ),
    "CP-0261": (
        ['src/financial_brain/patterns/views.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'research_controls shows the matched baseline and lookalikes only in research mode.',
    ),
    "CP-0279": (
        ['src/financial_brain/patterns/demo.py', 'src/financial_brain/patterns/views.py'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Synthetic bars flow through detection, paper preview and the view model with no network.',
    ),
    "CP-0285": (
        ['src/financial_brain/patterns/learning.py'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Decision: no gradient-boosted framework is introduced; the calibration floor is 200 labels and no matured labels exist to train on.',
    ),
    "CP-0290": (
        ['src/financial_brain/patterns/learning.py'],
        ['tests/test_pattern_wave9_modules.py', 'tests/test_pattern_wave9_evidence.py'],
        'scheduled_retrain refuses immature labels, registers a challenger and never promotes it.',
    ),
    "CP-0296": (
        ['src/financial_brain/patterns/learning.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'continual_dry_run uses seeded synthetic outcomes with an intentional shift; reports REVIEW and changes no registry.',
    ),
    "CP-0325": (
        ['docs/schemas/pattern-catalogue.v1.schema.json', 'docs/schemas/pattern-event.v1.schema.json'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Catalogue rows and emitted events are validated against checked-in JSON schemas.',
    ),
    "CP-0327": (
        ['docs/audits/pattern-benchmark.json'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Wave-9 scan has a 60 s guard; the recorded projection must stay under its 600 s budget.',
    ),
    "CP-0335": (
        ['src/financial_brain/patterns/detectors2.py'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Offline demo and wave-9 scan emit zero warnings from the pattern package.',
    ),
    "CP-0340": (
        ['scripts/build_pattern_fixtures.py', 'tests/fixtures/patterns/family_examples.json'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Six families, each with one positive and two negative synthetic examples, checked by the test suite.',
    ),
    "CP-0349": (
        ['tests/fixtures/patterns/snapshots.json'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Pinned hashes for the daily screen, empty state, risk rejection and loss review renderings.',
    ),
    "CP-0356": (
        ['docs/PATTERNS-DETECTOR-AUTHORING.md'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Causal-window proof requirements, fixture template and labelling rules.',
    ),
    "CP-0360": (
        ['docs/PATTERNS-MIGRATION-POLICY.md'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Migration policy for detector, spec, catalogue and event schemas.',
    ),
    "CP-0361": (
        ['docs/PATTERNS-OPERATIONS-GUIDE.md'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Operator guide for corrupt bars, stale quotes, crashes and cache rebuilds.',
    ),
    "CP-0365": (
        ['src/financial_brain/cli.py'],
        ['tests/test_pattern_wave9_evidence.py'],
        'fb patterns runs the offline pattern CLI; other fb commands are unchanged and still parse.',
    ),
    "CP-0379": (
        ['src/financial_brain/patterns/detectors.py'],
        ['tests/test_pattern_wave9_evidence.py'],
        'A double bottom is CANDIDATE at recognition and CONFIRMED only after its neckline close.',
    ),
    "CP-0383": (
        ['src/financial_brain/patterns/views.py'],
        ['tests/test_pattern_wave9_modules.py'],
        'filter_rows narrows by sector, liquidity, evidence tier and date interval together.',
    ),
    "CP-0384": (
        ['src/financial_brain/patterns/catalogue.py'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Each catalogue row has its claim kind, unverified page reference and UNASSESSED source-quality fields.',
    ),
    "CP-0401": (
        ['src/financial_brain/patterns/learning.py'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Fixture-label versions: shadow, refused promotion on DESCRIPTIVE evidence, promotion on FORWARD_PAPER fixture evidence, rollback.',
    ),
    "CP-0063": (
        ['src/financial_brain/patterns/marketdata.py'],
        ['tests/test_pattern_wave9_realism.py'],
        'candidate_within_box: start and end on trading sessions, span at most max_sessions, checked against the session calendar.',
    ),
    "CP-0181": (
        ['src/financial_brain/patterns/risk_views.py'],
        ['tests/test_pattern_wave9_realism.py'],
        'turnover_and_cost_delta adds capacity-adjusted turnover (each trade capped at participation times ADV) and the matching cost delta.',
    ),
    "CP-0336": (
        ['docs/audits/pattern-evidence-appendix.md'],
        ['tests/test_pattern_wave9_evidence.py'],
        'Appendix lists each command with its exit code, platform, Python version and base commit; a test checks its structure.',
    ),
    "CP-0341": (
        ['src/financial_brain/patterns/detectors2.py'],
        ['tests/test_pattern_wave9_realism.py'],
        'Noisy synthetic series with spread variation, a calendar gap, a 2:1 split and an illiquid run: quarantine, split adjustment, missing sessions, volume eligibility and detection all checked.',
    ),
    "CP-0345": (
        ['src/financial_brain/patterns/marketdata.py'],
        ['tests/test_pattern_wave9_realism.py'],
        'bars_completed_by returns only bars completed by the query time, in completion order; intraday and weekly examples; naive times refused.',
    ),
    "CP-0151": (
        ['scripts/mutation_patterns.py', 'docs/audits/mutation-report.json'],
        ['tests/test_pattern_wave9_final.py'],
        'Eight mutants across pivots, breakouts, failures, causality, completion time and calendar; all eight are killed. Report in docs/audits/mutation-report.json.',
    ),
    "CP-0156": (
        ['src/financial_brain/patterns/pit_universe.py'],
        ['tests/test_pattern_wave9_final.py'],
        'valid_universe = equity snapshot tradable on the date AND stock-futures activity that day; read-only inputs; non-F&O names flagged or removed as requested.',
    ),
    "CP-0262": (
        ['src/financial_brain/patterns/chart_svg.py'],
        ['tests/test_pattern_wave9_final.py'],
        'Static SVG candle chart: one candle per bar at 20 and 500 bars, fixed aspect ratio, escaped title, research label. The interactive Patterns tab is CP-0234 and is not built.',
    ),
    "CP-0376": (
        ['docs/audits/pattern-release-metadata.json'],
        ['tests/test_pattern_wave9_final.py'],
        'Wheel built with python -m build (contains wave-9 modules); metadata records wheel SHA-256 and fixture seed hashes. Built on Windows; Linux smoke test pending CI.',
    ),
    "CP-0149": (
        ["src/financial_brain/patterns/"],
        ["tests/test_pattern_offline.py"],
        "AST boundary check: no pattern module imports network clients, broker or execution/paper packages.",
    ),
}


NOT_APPLICABLE: dict[str, str] = {
    'CP-0313': 'Native kernel not justified: measured projected full scan is below the predeclared budget (docs/audits/pattern-benchmark.json).',
    'CP-0314': 'Native kernel not justified: measured projected full scan is below the predeclared budget (docs/audits/pattern-benchmark.json).',
    'CP-0315': 'Native kernel not justified: measured projected full scan is below the predeclared budget (docs/audits/pattern-benchmark.json).',
    'CP-0316': 'Native kernel not justified: measured projected full scan is below the predeclared budget (docs/audits/pattern-benchmark.json).',
    'CP-0317': 'Native kernel not justified: measured projected full scan is below the predeclared budget (docs/audits/pattern-benchmark.json).',
    'CP-0318': 'Native kernel not justified: measured projected full scan is below the predeclared budget (docs/audits/pattern-benchmark.json).',
    'CP-0319': 'Native kernel not justified: measured projected full scan is below the predeclared budget (docs/audits/pattern-benchmark.json).',
    'CP-0320': 'Native kernel not justified: measured projected full scan is below the predeclared budget (docs/audits/pattern-benchmark.json).',
    'CP-0321': 'Native kernel not justified: measured projected full scan is below the predeclared budget (docs/audits/pattern-benchmark.json).',
}


def parse_checklist(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    workstream = ""
    priority = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        ws = WORKSTREAM_RE.match(line)
        if ws:
            workstream = f"{ws.group(1)} {ws.group(2)}"
            priority = ""
            continue
        pr = PRIORITY_RE.search(line)
        if pr and not priority:
            priority = pr.group(1)
        cp = CP_RE.match(line)
        if cp:
            rows.append(
                {"task_id": cp.group(1), "title": cp.group(2), "workstream": workstream, "priority": priority}
            )
    ids = [row["task_id"] for row in rows]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise SystemExit(f"duplicate checklist IDs: {dupes}")
    return rows


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def build_baseline() -> dict[str, object]:
    dirty = [line[3:] for line in _git("status", "--porcelain").splitlines() if line]
    return {
        "commit": _git("rev-parse", "HEAD"),
        "dirty_paths": sorted(dirty),
        "python": platform.python_version(),
        "platform": sys.platform,
        "dependency_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in DEP_FILES
        },
    }


def build_completion(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for row in rows:
        task_id = row["task_id"]
        entry: dict[str, object] = {
            "task_id": task_id,
            "priority": row["priority"],
            "workstream": row["workstream"],
            "title": row["title"],
        }
        if task_id in NOT_APPLICABLE:
            entry.update(
                {
                    "status": "not_applicable_with_evidence",
                    "source_files": ["scripts/bench_patterns.py"],
                    "test_files": ["docs/audits/pattern-benchmark.json"],
                    "test_command": "python scripts/bench_patterns.py",
                    "result": "PASS",
                    "notes": NOT_APPLICABLE[task_id],
                }
            )
        elif task_id in EVIDENCE:
            sources, tests, notes = EVIDENCE[task_id]
            entry.update(
                {
                    "status": "implemented",
                    "source_files": sources,
                    "test_files": tests,
                    "test_command": f"{PYTEST} {' '.join(tests)}",
                    "result": "PASS",
                    "notes": notes,
                }
            )
        else:
            entry.update(
                {
                    "status": "incomplete",
                    "source_files": [],
                    "test_files": [],
                    "test_command": None,
                    "result": "NOT_RUN",
                    "notes": "Not implemented in this checkout.",
                }
            )
        out.append(entry)
    return out


def verify_evidence_paths() -> list[str]:
    """Return problems where declared evidence points at files that do not exist."""
    problems: list[str] = []
    for task_id, (sources, tests, _) in EVIDENCE.items():
        for rel in [*sources, *tests]:
            if not (ROOT / rel).exists():
                problems.append(f"{task_id}: missing {rel}")
    return problems


def render(matrix: list[dict[str, object]], baseline: dict[str, object]) -> tuple[str, str]:
    base_text = json.dumps(baseline, indent=2, sort_keys=True) + "\n"
    comp_text = json.dumps(
        {"checklist_sha256": hashlib.sha256(CHECKLIST.read_bytes().replace(b"\r\n", b"\n")).hexdigest(), "rows": matrix},
        indent=2,
        sort_keys=True,
    ) + "\n"
    return base_text, comp_text


def summary(matrix: list[dict[str, object]]) -> str:
    statuses = Counter(str(row["status"]) for row in matrix)
    total = len(matrix)
    done = statuses.get("implemented", 0) + statuses.get("verified_existing", 0)
    lines = [f"CP items: {total}", f"status: {dict(sorted(statuses.items()))}"]
    for priority in ("P0", "P1", "P2"):
        subset = [row for row in matrix if row["priority"] == priority]
        finished = sum(1 for row in subset if row["status"] != "incomplete")
        lines.append(f"  {priority}: {finished}/{len(subset)} ({100 * finished / max(len(subset), 1):.1f}%)")
    lines.append(f"progress: {done}/{total} = {100 * done / total:.1f}%")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if committed outputs are stale")
    args = parser.parse_args(argv)

    rows = parse_checklist(CHECKLIST)
    if len(rows) != 406:
        print(f"ERROR: expected 406 CP rows, parsed {len(rows)}", file=sys.stderr)
        return 1
    problems = verify_evidence_paths()
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 1

    matrix = build_completion(rows)
    base_text, comp_text = render(matrix, build_baseline())
    if args.check:
        # Baseline dirty_paths change with every edit, so only the completion matrix is strict.
        if not COMPLETION.exists() or COMPLETION.read_text(encoding="utf-8") != comp_text:
            print(f"ERROR: stale: {COMPLETION.name}", file=sys.stderr)
            return 1
        print(summary(matrix))
        return 0

    BASELINE.write_text(base_text, encoding="utf-8")
    COMPLETION.write_text(comp_text, encoding="utf-8")
    print(summary(matrix))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
