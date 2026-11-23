import json

import pytest

from scripts.validate_ci_matrix import MATRIX, WORKFLOW, validate


def test_ci_matrix_matches_required_workflow_gates_and_documents_combined_tests():
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    validate(matrix, WORKFLOW.read_text(encoding="utf-8"))
    assert matrix["categories"]["unit"]["command"] == matrix["categories"]["integration"]["command"]


def test_ci_matrix_rejects_duplicate_chart_pattern_steps():
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    workflow = WORKFLOW.read_text(encoding="utf-8")
    label = matrix["chart_pattern_steps"]["completion_matrix_step"]
    with pytest.raises(ValueError, match="occur exactly once"):
        validate(matrix, workflow + f"\n      - name: {label}\n")
