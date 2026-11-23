from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from financial_brain.learning_audit import audit

def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')

def _ready_repo(root: Path) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    _write(root / 'docs/edge-program/v3.0-pr-graph-2026-11-21.json', {'observed_at_utc': stamp, 'pull_requests': [{'number': 40, 'state': 'merged', 'depends_on': []}, {'number': 41, 'state': 'merged', 'depends_on': [40]}]})
    _write(root / 'docs/edge-program/v3.0-status.json', {'total_items': 1, 'assessment_summary': {'as_of_utc': stamp}, 'items': [{'id': 'SL-00-14', 'status': 'COMPLETE', 'evidence': ['src/financial_brain/learning_audit.py'], 'test_evidence': ['tests/test_learning_audit.py']}]})
    (root / 'src/financial_brain').mkdir(parents=True, exist_ok=True)
    (root / 'src/financial_brain/learning_audit.py').write_text('# fixture\n', encoding='utf-8')
    _write(root / 'tests/test_learning_audit.py', {'artifact': 'present'})
    _write(root / 'docs/edge-program/market-data-sources.v1.json', {'observed_at_utc': stamp, 'sources': [{'id': 'market-data', 'status': 'READY', 'retention': {'status': 'VERIFIED'}}]})
    _write(root / 'docs/edge-program/canonical-migration-audit.v1.json', {'captured_at_utc': stamp, 'ledger': {'unresolved_records': 0, 'incomplete_projection_accounts': 0, 'accounts_without_opening_balance': 0, 'source_integrity_errors': {'missing_source_events': 0, 'mismatched_source_events': 0, 'rejected_source_rows': 0, 'unscanned_ledger_events': 0}}, 'pnl_promotion_gate': {'status': 'ELIGIBLE_FOR_REVIEW', 'reason_codes': []}})

def test_learning_audit_fails_closed_for_unmerged_pr_incomplete_evidence_and_permissions(tmp_path: Path) -> None:
    _ready_repo(tmp_path)
    _write(tmp_path / 'docs/edge-program/v3.0-pr-graph-2026-11-21.json', {'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'pull_requests': [{'number': 40, 'state': 'open', 'depends_on': []}]})
    _write(tmp_path / 'docs/edge-program/v3.0-status.json', {'total_items': 1, 'assessment_summary': {'as_of_utc': datetime.now(timezone.utc).isoformat()}, 'items': [{'id': 'SL-00-14', 'status': 'PARTIAL', 'evidence': []}]})
    _write(tmp_path / 'docs/edge-program/market-data-sources.v1.json', {'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'sources': [{'id': 'market-data', 'status': 'LICENSED', 'retention': {'status': 'UNVERIFIED'}}]})
    result = audit(tmp_path)
    assert result['status'] == 'BLOCKED'
    assert result['blocker_count'] >= 3
    assert any(('PR #40 is open' in blocker for blocker in result['blockers']))
    assert any(('SL-00-14 is PARTIAL' in blocker for blocker in result['blockers']))
    assert any(('retention permission is not verified' in blocker for blocker in result['blockers']))

def test_learning_audit_ready_requires_all_three_evidence_domains(tmp_path: Path) -> None:
    _ready_repo(tmp_path)
    result = audit(tmp_path)
    assert result['status'] == 'READY'
    assert result['blocker_count'] == 0

def test_learning_audit_missing_artifact_is_a_blocker(tmp_path: Path) -> None:
    _ready_repo(tmp_path)
    (tmp_path / 'tests/test_learning_audit.py').unlink()
    result = audit(tmp_path)
    assert result['status'] == 'BLOCKED'
    assert any(('references missing test evidence' in blocker for blocker in result['blockers']))

def test_learning_audit_blocks_pnl_promotion_when_legacy_positions_are_unresolved(tmp_path: Path) -> None:
    _ready_repo(tmp_path)
    path = tmp_path / 'docs/edge-program/canonical-migration-audit.v1.json'
    report = json.loads(path.read_text(encoding='utf-8'))
    report['ledger']['unresolved_records'] = 51
    report['ledger']['incomplete_projection_accounts'] = 1
    report['pnl_promotion_gate'] = {'status': 'BLOCKED', 'reason_codes': ['UNRESOLVED_LEGACY_POSITIONS']}
    path.write_text(json.dumps(report), encoding='utf-8')
    result = audit(tmp_path)
    assert result['status'] == 'BLOCKED'
    assert any(('canonical portfolio audit reports 51 unresolved records' in blocker for blocker in result['blockers']))
    assert any(('canonical P&L promotion gate is BLOCKED' in blocker for blocker in result['blockers']))

def test_learning_audit_missing_test_evidence_is_a_blocker(tmp_path: Path) -> None:
    _ready_repo(tmp_path)
    status_path = tmp_path / 'docs/edge-program/v3.0-status.json'
    status = json.loads(status_path.read_text(encoding='utf-8'))
    del status['items'][0]['test_evidence']
    status_path.write_text(json.dumps(status), encoding='utf-8')
    result = audit(tmp_path)
    assert result['status'] == 'BLOCKED'
    assert any(('without test evidence' in blocker for blocker in result['blockers']))

def test_learning_audit_stale_dependency_and_permission_snapshots_fail_closed(tmp_path: Path) -> None:
    _ready_repo(tmp_path)
    graph_path = tmp_path / 'docs/edge-program/v3.0-pr-graph-2026-11-21.json'
    graph = json.loads(graph_path.read_text(encoding='utf-8'))
    graph['observed_at_utc'] = '2020-01-01T00:00:00+00:00'
    graph_path.write_text(json.dumps(graph), encoding='utf-8')
    permissions_path = tmp_path / 'docs/edge-program/market-data-sources.v1.json'
    permissions = json.loads(permissions_path.read_text(encoding='utf-8'))
    permissions['observed_at_utc'] = '2020-01-01T00:00:00+00:00'
    permissions_path.write_text(json.dumps(permissions), encoding='utf-8')
    portfolio_path = tmp_path / 'docs/edge-program/canonical-migration-audit.v1.json'
    portfolio = json.loads(portfolio_path.read_text(encoding='utf-8'))
    portfolio['captured_at_utc'] = '2020-01-01T00:00:00+00:00'
    portfolio_path.write_text(json.dumps(portfolio), encoding='utf-8')
    result = audit(tmp_path, now=datetime(2026, 11, 21, tzinfo=timezone.utc))
    assert result['status'] == 'BLOCKED'
    assert any(('PR graph is stale' in blocker for blocker in result['blockers']))
    assert any(('market-data permissions is stale' in blocker for blocker in result['blockers']))
    assert any(('canonical portfolio audit is stale' in blocker for blocker in result['blockers']))

def test_fb_learning_audit_cli_returns_success_only_when_ready(tmp_path: Path, capsys) -> None:
    from financial_brain.cli import main
    _ready_repo(tmp_path)
    assert main(['learning', 'audit', '--repo', str(tmp_path)]) == 0
    assert 'learning audit: READY' in capsys.readouterr().out
    _write(tmp_path / 'docs/edge-program/v3.0-pr-graph-2026-11-21.json', {'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'pull_requests': [{'number': 40, 'state': 'open', 'depends_on': []}]})
    assert main(['learning', 'audit', '--repo', str(tmp_path), '--json']) == 1
    assert '"status": "BLOCKED"' in capsys.readouterr().out
