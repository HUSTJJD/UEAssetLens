"""Targeted offline tests for the engine toolset audit and integration boundary."""
from __future__ import annotations

import importlib.util
import json
import re
import socket
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = PLUGIN_ROOT / "Server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from ual_common.artifacts import canonical_yaml
from ual_common.toon import parse_toon

SCRIPT_PATH = PLUGIN_ROOT / "Tools" / "audit_engine_toolsets.py"
SPEC = importlib.util.spec_from_file_location("ual_audit_engine_toolsets_under_test", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)

GATEWAY_REGISTRY = PLUGIN_ROOT / "Config" / "UAL" / "gateway" / "integrations.yaml"
EXTENSION_MANIFEST = PLUGIN_ROOT / "Config" / "UAL" / "extensions" / "integrations.yaml"
TOON_HEADER = "# UEAssetLens TOON v1 (Zero-VLM)"
WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:[\\/]")
TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
RANDOM_ID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b|\b[0-9a-fA-F]{32,64}\b"
)
REQUIRED_INTEGRATION_FIELDS = {
    "enabled", "license", "requires_gpu", "requires_cloud", "requires_vlm",
    "status", "process_isolation", "reason",
}


def _run(capsys, *argv: str):
    code = audit.main(list(argv))
    output = capsys.readouterr().out
    return code, output, parse_toon(output)


def _repo_relative(path: Path) -> str:
    return path.relative_to(audit.ROOT).as_posix()


def _write_yaml(path: Path, payload: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_yaml(payload), encoding="utf-8", newline="\n")
    return _repo_relative(path)


def _read_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_check_passes_on_current_files(capsys):
    code, output, parsed = _run(capsys, "--check")
    assert code == 0
    assert output.startswith(TOON_HEADER)
    assert parsed["summary"]["result"] == "PASS"
    assert parsed["summary"]["inventory"] == "../../Docs/ai-capabilities/toolset-inventory.yaml"
    assert parsed["summary"]["schemas"] == "Server/ual_mcp/tool_schemas.yaml"
    assert int(parsed["summary"]["official_tool_count"]) == 878
    assert int(parsed["summary"]["official_read"]) >= 1
    assert int(parsed["summary"]["official_write"]) >= 1
    assert int(parsed["summary"]["official_execute"]) >= 1
    assert int(parsed["summary"]["official_unannotated"]) >= 1
    assert int(parsed["summary"]["ual_static_count"]) == audit.EXPECTED_UAL_STATIC_COUNT
    assert int(parsed["summary"]["ual_runtime_count"]) == audit.EXPECTED_UAL_RUNTIME_COUNT
    assert int(parsed["summary"]["ual_total_count"]) == audit.EXPECTED_UAL_TOTAL_COUNT
    assert int(parsed["summary"]["expected_ual_static_count"]) == audit.EXPECTED_UAL_STATIC_COUNT
    assert int(parsed["summary"]["expected_ual_runtime_count"]) == audit.EXPECTED_UAL_RUNTIME_COUNT
    assert int(parsed["summary"]["expected_ual_total_count"]) == audit.EXPECTED_UAL_TOTAL_COUNT
    assert int(parsed["summary"]["duplicate_qualified_names"]) == 0
    assert int(parsed["summary"]["bare_ual_names"]) == 0
    assert len(parsed["tables"]["issues"]) <= audit.MAX_ISSUES


def test_duplicate_and_missing_access_mode_detection_with_temp_yaml(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    inventory = {
        "schema_version": 1,
        "summary": {"total_tools": 1},
        "toolsets": [{
            "name": "EditorToolset",
            "plugin": "EditorToolset",
            "tools": [{
                "name": "FindAssets",
                "access_mode": "Read",
                "header": "AssetToolset.h",
            }],
        }],
    }
    schemas = {
        "schema_version": "4.0",
        "tools": [
            {
                "name": "UEAssetLens.AssetSemanticToolset.SearchAssets",
                "accessMode": "Read",
                "kind": "static",
            },
            {
                "name": "UEAssetLens.AssetSemanticToolset.SearchAssets",
                "accessMode": "Read",
                "kind": "static",
            },
            {"name": "ual_bad_alias", "kind": "static"},
            {
                "name": "UEAssetLens.AssetGraphToolset.Bad",
                "accessMode": "Admin",
                "kind": "runtime",
            },
        ],
    }
    inventory_rel = _write_yaml(tmp_path / "inventory.yaml", inventory)
    schemas_rel = _write_yaml(tmp_path / "schemas.yaml", schemas)
    code, output, parsed = _run(
        capsys, "--check", "--inventory", inventory_rel, "--schemas", schemas_rel,
    )
    assert code == 1
    assert parsed["summary"]["result"] == "FAIL"
    assert int(parsed["summary"]["duplicate_qualified_names"]) >= 1
    assert int(parsed["summary"]["missing_access_mode"]) >= 1
    assert int(parsed["summary"]["bare_ual_names"]) >= 1
    assert int(parsed["summary"]["invalid_access_mode"]) >= 1
    assert parsed["summary"]["inventory"] == inventory_rel
    assert parsed["summary"]["schemas"] == schemas_rel
    assert not WINDOWS_PATH_RE.search(output)
    assert not TIMESTAMP_RE.search(output)


def test_non_loopback_url_rejected_without_network(monkeypatch, capsys):
    def forbidden_network_call(*args, **kwargs):
        raise AssertionError("network attempted")

    monkeypatch.setattr(audit, "_open_url", forbidden_network_call)
    code, output, parsed = _run(capsys, "--live-url", "https://example.com/mcp")
    assert code == 1
    assert parsed["summary"]["result"] == "FAIL"
    assert parsed["summary"]["reason"] == "rejected_non_loopback"
    assert "example.com" not in output
    assert len(parsed["tables"]["errors"]) <= audit.MAX_ISSUES


def test_default_check_does_not_open_socket(monkeypatch, capsys):
    def forbidden_network_call(*args, **kwargs):
        raise AssertionError("network attempted")

    monkeypatch.setattr(audit, "_open_url", forbidden_network_call)
    monkeypatch.setattr(socket, "socket", forbidden_network_call)
    code, _, parsed = _run(capsys, "--check")
    assert code == 0
    assert parsed["summary"]["result"] == "PASS"


@pytest.mark.parametrize("url,accepted", [
    ("http://localhost:8000/mcp", True),
    ("http://127.0.0.1:8000/mcp", True),
    ("https://localhost:8000/mcp", False),
    ("http://localhost:8000/mcp/", False),
    ("http://localhost:8001/mcp", False),
    ("http://0.0.0.0:8000/mcp", False),
    ("http://[::1]:8000/mcp", False),
    ("http://example.com:8000/mcp", False),
    ("http://localhost:8000/mcp?scope=all", False),
])
def test_validate_live_url_accepts_only_exact_loopback_urls(url, accepted):
    if accepted:
        audit._validate_live_url(url)
    else:
        with pytest.raises(audit.AuditError) as excinfo:
            audit._validate_live_url(url)
        assert excinfo.value.code == "rejected_non_loopback"


def test_toon_output_has_no_absolute_paths_timestamps_or_random_ids(capsys):
    code, output, parsed = _run(capsys, "--check")
    assert code == 0
    assert parsed["header"] == TOON_HEADER
    assert not WINDOWS_PATH_RE.search(output)
    assert ":\\" not in output
    assert "\\\\" not in output
    assert not TIMESTAMP_RE.search(output)
    assert not RANDOM_ID_RE.search(output)
    assert parsed["summary"]["inventory"].startswith("../../")
    assert parsed["summary"]["schemas"].startswith("Server/")
    assert len(parsed["tables"]["issues"]) <= audit.MAX_ISSUES


def test_enablement_check_is_offline_and_keeps_no_redist_boundary(monkeypatch, capsys):
    def forbidden_network_call(*args, **kwargs):
        raise AssertionError("network attempted")

    monkeypatch.setattr(audit, "_open_url", forbidden_network_call)
    code, output, parsed = _run(capsys, "--enablement-check")
    assert code == 0
    assert parsed["summary"]["result"] == "PASS"
    assert parsed["summary"]["mcp_optional"] == "true"
    assert parsed["summary"]["no_redist_required"] == "true"
    assert parsed["summary"]["uproject"] == "../../Ann.uproject"
    assert not WINDOWS_PATH_RE.search(output)
    assert not TIMESTAMP_RE.search(output)


def test_integration_registry_enforces_disabled_external_services():
    payload = _read_yaml(GATEWAY_REGISTRY)
    text = GATEWAY_REGISTRY.read_text(encoding="utf-8")
    assert payload["schema_version"] == 1
    assert "rules" not in payload and "tools" not in payload
    assert payload["zero_vlm_red_line"]["external_generative_execution_by_ual"] is False
    assert payload["zero_vlm_red_line"]["external_generative_redistribution_by_ual"] is False
    assert payload["zero_vlm_red_line"]["external_generative_invocation_by_ual"] is False
    assert "NOT executed" in payload["zero_vlm_red_line"]["statement"]
    assert "NOT redistributed" in payload["zero_vlm_red_line"]["statement"]
    boundary = payload["official_mcp_boundary"]
    assert boundary["experimental"] is True
    assert boundary["no_redist"] is True
    assert boundary["loopback_only"] is True
    assert boundary["auth"] is False
    assert boundary["non_loopback_exposure"] is False
    entries = payload["integrations"]
    assert len(entries) == 6
    assert {entry["name"] for entry in entries} == {
        "ComfyUI", "TRELLIS", "AudioCraft", "UniRig+Blender+Interchange",
        "Agent orchestration", "Runtime tool audit",
    }
    external_names = {
        "ComfyUI", "TRELLIS", "AudioCraft", "UniRig+Blender+Interchange",
    }
    offline_names = {"Agent orchestration", "Runtime tool audit"}
    for entry in entries:
        assert REQUIRED_INTEGRATION_FIELDS <= set(entry)
        assert isinstance(entry["license"], str) and entry["license"]
        assert isinstance(entry["reason"], str) and entry["reason"]
        assert entry["requires_cloud"] is False
        if entry["name"] in external_names:
            assert entry["enabled"] is False
            assert entry["status"] == "规格"
            assert entry["process_isolation"] is True
            assert entry["requires_gpu"] is True or entry["requires_vlm"] is True
            assert entry["executed_by_ual"] is False
            assert entry["redistributed_by_ual"] is False
        else:
            assert entry["name"] in offline_names
            assert entry["enabled"] is True
            assert entry["status"] == "已实现"
            assert entry["requires_gpu"] is False
            assert entry["requires_cloud"] is False
            assert entry["requires_vlm"] is False
    assert "ComfyUI" in text and "GPL-3.0" in text
    assert not WINDOWS_PATH_RE.search(text)
    assert not TIMESTAMP_RE.search(text)
    assert not RANDOM_ID_RE.search(text)
    assert "TODO" not in text and "(human)" not in text


def test_extension_manifest_loads_without_changing_tool_count():
    from ual_common import gateway
    from ual_common.extensions import load_builtin_registry, tool_defs_from_registry

    manifest = _read_yaml(EXTENSION_MANIFEST)
    text = EXTENSION_MANIFEST.read_text(encoding="utf-8")
    rules = manifest["rules"]
    assert len(rules) == 6
    for rule in rules:
        assert rule["namespace"] == "gateway"
        assert rule["namespace"] != "tool"
        assert "." not in rule["name"]
        impl = rule.get("impl")
        assert impl is None or impl == "ual_common.extensions_builtins.official_gateway"
        assert "mcp_name" not in rule.get("schema", {})
        assert "inputSchema" not in rule.get("schema", {})
        enabled = rule.get("enabled", True) is True
        external_spec = "external_disabled" in rule.get("capabilities", [])
        assert enabled is not external_spec
    runtime_rules = [rule for rule in rules if rule["name"] == "runtime_tool_audit"]
    assert len(runtime_rules) == 1
    for carrier in (runtime_rules[0]["config"], runtime_rules[0]["schema"]):
        assert carrier["no_redist"] is True
        assert carrier["loopback_only"] is True
        assert carrier["auth"] is False
        assert carrier["non_loopback_exposure"] is False
    assert not WINDOWS_PATH_RE.search(text)
    assert not TIMESTAMP_RE.search(text)
    assert not RANDOM_ID_RE.search(text)
    assert "TODO" not in text and "(human)" not in text

    loaded = load_builtin_registry()
    snapshot = loaded.registry.snapshot()
    rule_names = {rule["name"] for rule in rules}
    integration_rows = [
        row for row in snapshot
        if row["namespace"] == "gateway" and row["name"] in rule_names
    ]
    assert len(integration_rows) == 2
    assert all(row["namespace"] == "gateway" for row in integration_rows)
    tool_rules = [rule for rule in loaded.resolved.rules if rule.get("namespace") == "tool"]
    assert len(tool_defs_from_registry(loaded.registry)) == len(tool_rules)
    assert len(gateway.load_gateway().tools()) >= 29


class _FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        self._body = body
        self.headers = headers

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit < 0 else self._body[:limit]

    def close(self) -> None:
        return None


def test_fake_loopback_tools_list_is_bounded_and_uses_no_auth(monkeypatch, capsys):
    facts = audit._audit_contract(audit.DEFAULT_INVENTORY, audit.DEFAULT_SCHEMAS, True)
    live_names = ["EditorToolset.FindAssets", *sorted(facts.ual_names)]
    requests: list[object] = []

    def fake_open_url(request, timeout):
        requests.append(request)
        sequence = len(requests)
        headers = {"Content-Type": "application/json"}
        if sequence == 1:
            headers["Mcp-Session-Id"] = "session-local"
            body = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"protocolVersion": "2025-11-25"},
            }).encode("utf-8")
        elif sequence == 2:
            body = b""
        else:
            body = json.dumps({
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"tools": [{"name": name} for name in live_names]},
            }).encode("utf-8")
        return _FakeResponse(body, headers)

    monkeypatch.setattr(audit, "_open_url", fake_open_url)
    code, output, parsed = _run(capsys, "--live-url", "http://localhost:8000/mcp")
    assert code == 0
    assert parsed["summary"]["result"] == "PASS"
    assert parsed["summary"]["live_scope"] == "loopback"
    assert int(parsed["summary"]["live_count"]) == len(live_names)
    assert int(parsed["summary"]["ual_contract_missing_from_live"]) == 0
    assert int(parsed["summary"]["ual_contract_unexpected_in_live"]) == 0
    assert len(parsed["tables"]["issues"]) <= audit.MAX_ISSUES
    assert "UEAssetLens.AssetSemanticToolset.InspectAsset" not in output or len(facts.ual_names) < 2
    assert not WINDOWS_PATH_RE.search(output)
    assert not TIMESTAMP_RE.search(output)
    assert [json.loads(request.data)["method"] for request in requests] == [
        "initialize", "notifications/initialized", "tools/list",
    ]
    assert all(request.full_url == "http://localhost:8000/mcp" for request in requests)
    assert all(
        "authorization" not in {key.lower() for key in request.headers}
        for request in requests
    )
