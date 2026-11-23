from scripts.capability_ownership import CAPABILITIES, ROOT, build


def test_capability_contracts_have_one_existing_canonical_owner():
    report = build()
    capabilities = report["capabilities"]
    assert len({item["id"] for item in capabilities}) == len(capabilities)
    assert len(capabilities) == len(CAPABILITIES)
    for item in capabilities:
        owner = ROOT / item["owner"]
        assert owner.exists(), f"{item['id']}: missing owner {item['owner']}"
        for relative in item["implementations"]:
            assert (ROOT / relative).exists(), f"{item['id']}: missing implementation {relative}"
        assert item["boundary"].strip()


def test_module_inventory_covers_all_strategy_feed_and_broker_oms_modules():
    report = build()
    assignments = {row["path"]: row for row in report["module_inventory"]}
    assert assignments
    required = []
    for folder in ("strategies", "providers", "ingest", "execution/oms", "portfolio",
                   "paper", "signals", "opportunity", "interfaces", "api", "commands"):
        required.extend((ROOT / "src/financial_brain" / folder).rglob("*.py"))
    for path in required:
        if path.name != "__init__.py":
            relative = path.relative_to(ROOT).as_posix()
            assert relative in assignments
            assert assignments[relative]["module_owner"] == relative
    assert any(row["module_role"] == "signal_report_or_trader_renderer" for row in assignments.values())


def test_every_order_signal_and_ledger_module_has_a_canonical_owner():
    report = build()
    assignments = {row["path"]: row for row in report["module_inventory"]}
    for path in (ROOT / "src/financial_brain").rglob("*.py"):
        name = path.name.lower()
        if path.name == "__init__.py" or not any(
            token in name for token in ("oms", "order", "signal", "ledger", "journal")
        ):
            continue
        relative = path.relative_to(ROOT).as_posix()
        assert relative in assignments, f"missing capability owner: {relative}"
        assert assignments[relative]["module_owner"] == relative
        assert assignments[relative]["module_role"]


def test_capability_inventory_has_one_contract_for_each_trader_core_category():
    report = build()
    categories = {item["category"] for item in report["capabilities"]}
    assert {"strategy", "feed", "oms", "ledger", "signals", "renderer", "output"} <= categories
    assert len({item["id"] for item in report["capabilities"]}) == len(report["capabilities"])
    assert all(item["owner"] and item["owner_symbol"] for item in report["capabilities"])


def test_each_strategy_candidate_names_its_record_owner_and_catalogue_owner():
    report = build()
    candidates = report["strategy_candidates"]
    assert candidates
    assert len({item["strategy_id"] for item in candidates}) == len(candidates)
    for item in candidates:
        assert item["canonical_owner"] == item["source_ref"]
        assert (ROOT / item["source_ref"].split("#", 1)[0]).is_file()
        assert item["catalogue_owner"] == "src/financial_brain/forward_edge/eligibility.py::build_catalogue"


def test_every_listed_module_path_exists_and_has_one_assignment():
    modules = build()["module_inventory"]
    paths = [item["path"] for item in modules]
    assert len(paths) == len(set(paths))
    capability_ids = [item["capability_id"] for item in modules]
    assert len(capability_ids) == len(set(capability_ids))
    assert all((ROOT / path).is_file() for path in paths)
    assert all(item["module_owner"] == item["path"] for item in modules)
