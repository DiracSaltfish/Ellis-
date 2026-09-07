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
        "ibkr-native-bridge-config.schema.json",
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
        "refresh", "open_legacy_ui", "set_operating_mode",
        "start_service", "stop_service",
        "restart_service", "acknowledge_uncertain",
    }
    source_actions = {
        value for value in candidates
        if value in generic or value.startswith(("upload_", "premium_", "webull_", "redemption_"))
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
        assert "acknowledge_uncertain" in module["allowed_actions"], module_id
        assert "acknowledge_uncertain" in module["approval_required_actions"], module_id
    upload_schedule = owner_modules["upload"]["settings"]["worker_monitor_schedule"]
    assert upload_schedule["contract"] == "newnavnav-upload-health-monitor-v1"
    assert upload_schedule["timezone"] == "Asia/Shanghai"
    assert upload_schedule["weekdays"] == [1, 2, 3, 4, 5]
    assert {item["source"] for item in upload_schedule["sources"]} == {
        "home-mac",
        "mac-home-private-xop-family-uploader",
        "mac-home-private-nikkei225-pcf-n225m-uploader",
        "mac-home-private-germany-pcf-fdxm-xetra1735-uploader",
        "mac-home-private-164824-t2-inda-uploader",
        "mac-home-private-161226-silver-uploader",
    }
    assert owner_modules["webull"]["settings"]["owner_readiness"].get(
        "allow_scheduled_idle"
    ) is True
    redemption_conditions = owner_modules["redemption"]["settings"][
        "owner_readiness"
    ]["conditions"]
    assert {"path": "health.ok", "equals": True} in redemption_conditions
    assert not any(
        condition.get("path") == "health_probe.ready"
        for condition in redemption_conditions
    )
    install_source = (APP / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "wait_for_agent_shutdown()" in install_source
    assert install_source.count("wait_for_agent_shutdown") >= 3
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
    ibkr_schema = schemas["ibkr-native-bridge-config.schema.json"]
    assert ibkr_schema["additionalProperties"] is False
    tws_properties = ibkr_schema["properties"]["tws"]["properties"]
    assert tws_properties["host"]["type"] == "string"
    assert tws_properties["allowed_private_hosts"]["maxItems"] == 16
    assert tws_properties["allowed_private_hosts"]["uniqueItems"] is True
    schedule_schema = ibkr_schema["properties"]["connection_schedule"]
    assert schedule_schema["additionalProperties"] is False
    assert schedule_schema["properties"]["weekdays"]["uniqueItems"] is True
    assert ibkr_schema["properties"]["subscriptions"]["maxItems"] == 512
    ibkr_production = json.loads(
        (ROOT / "config" / "ibkr-native-bridge.machome-production.json").read_text(
            encoding="utf-8"
        )
    )
    assert ibkr_production["connection_schedule"] == {
        "enabled": True,
        "timezone": "Asia/Shanghai",
        "weekdays": [1, 2, 3, 4, 5],
        "start_time": "09:00",
        "stop_time": "15:06",
    }
    assert "MachomeHub/data/upload/" in ibkr_production["socket_path"]
    assert "MachomeHub/data/upload/" in ibkr_production["health_file"]
    ibkr_example = json.loads(
        (ROOT / "config" / "ibkr-native-bridge.example.json").read_text(
            encoding="utf-8"
        )
    )
    upload_data_prefix = "~/Library/Application Support/MachomeHub/data/upload/"
    assert ibkr_example["socket_path"].startswith(upload_data_prefix)
    assert ibkr_example["health_file"].startswith(upload_data_prefix)
    assert ibkr_example["connection_schedule"] == ibkr_production["connection_schedule"]
    upload_manifest = json.loads(
        (APP / "modules" / "upload" / "SOURCE_MANIFEST.json").read_text(
            encoding="utf-8"
        )
    )
    assert upload_manifest["module"] == "native-upload"
    assert upload_manifest["baseline_access"] == "read-only"
    assert upload_manifest["runtime_dependency_on_baseline"] is False
    assert upload_manifest["default_production_mutation"] is False
    assert len(upload_manifest["sources"]) == 10
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", source["sha256"])
        for source in upload_manifest["sources"]
    )
    print(f"CONTRACTS_OK schemas={len(files)} actions={len(contract_actions)} modules=4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
