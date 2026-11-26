from scripts.validate_ci_check_matrix import validate


def test_documented_ci_check_matrix_matches_workflow_without_duplicate_pattern_steps() -> None:
    validate()
