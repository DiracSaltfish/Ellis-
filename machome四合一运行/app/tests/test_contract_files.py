#!/usr/bin/env python3
"""Dependency-free consistency checks for the checked-in Hub wire contracts."""

from __future__ import annotations

import json
import re
from pathlib import Path


APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
CONTRACTS = ROOT / "contracts"


def load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    assert value.get("$schema") == "https://json-schema.org/draft/2020-12/schema", path
    assert value.get("type") == "object", path
    assert set(value.get("required", ())) <= set(value.get("properties", {})), path
    return value


def main() -> int:
    files = sorted(CONTRACTS.glob("*.schema.json"))
    assert {path.name for path in files} == {
        "module-command.schema.json",
        "module-command-result.schema.json",
        "module-event.schema.json",
        "module-status.schema.json",
        "owner-deployment-manifest.schema.json",
    }
    schemas = {path.name: load(path) for path in files}

    for path, schema in ((CONTRACTS / name, value) for name, value in schemas.items()):
        encoded = json.dumps(schema)
        for reference in re.findall(r'"\$ref":\s*"([^"]+)"', encoded):
            target = reference.split("#", 1)[0]
            if target:
                assert (path.parent / target).is_file(), (path, reference)

    command_schema = schemas["module-command.schema.json"]
    contract_actions = set(command_schema["$defs"]["action"]["enum"])
    source = (APP / "src" / "agent" / "ModuleWorker.cpp").read_text(encoding="utf-8")
    capability_start = source.index("QJsonArray commands")
    capability_end = source.index("QJsonObject payload", capability_start)
    capability_block = source[capability_start:capability_end]
    candidates = set(re.findall(r'QStringLiteral\("([^\"]+)"\)', capability_block))
    generic = {
        "refresh", "open_legacy_ui", "start_service", "stop_service",
        "restart_service", "acknowledge_uncertain",
    }
    source_actions = {
        value for value in candidates
        if value in generic or value.startswith(("premium_", "webull_", "redemption_"))
    }
    assert contract_actions == source_actions, {
        "missing_from_contract": sorted(source_actions - contract_actions),
        "not_advertised_by_agent": sorted(contract_actions - source_actions),
    }

    config = json.loads((ROOT / "config" / "modules.local-test.json").read_text(encoding="utf-8"))
    assert config["schema_version"] == 1
    assert {module["id"] for module in config["modules"]} == {"upload", "premium", "webull", "redemption"}
    owner_template = json.loads(
        (ROOT / "config" / "modules.owner.template.json").read_text(encoding="utf-8")
    )
    owner_modules = {module["id"]: module for module in owner_template["modules"]}
    for module_id, module in owner_modules.items():
        assert "open_legacy_ui" not in module["allowed_actions"], module_id
        assert "open_legacy_ui" not in module["approval_required_actions"], module_id
    assert {
        "webull_collector_start",
        "webull_show_login",
    } <= set(owner_modules["webull"]["approval_required_actions"])
    manifest_schema = schemas["owner-deployment-manifest.schema.json"]
    assert manifest_schema["properties"]["schema_version"]["const"] == 1
    assert (
        manifest_schema["properties"]["kind"]["const"]
        == "machome_owner_deployment_manifest"
    )
    assert manifest_schema["properties"]["path_base"]["const"] == "manifest_directory"
    print(f"CONTRACTS_OK schemas={len(files)} actions={len(contract_actions)} modules=4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
