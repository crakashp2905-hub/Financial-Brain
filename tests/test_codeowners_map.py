from scripts.validate_codeowners_map import validate


def test_codeowners_routes_every_documented_domain_path() -> None:
    assert validate() == []
