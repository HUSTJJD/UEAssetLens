"""Offline security and preference-boundary tests for declarative extensions.

All manifests in this file are data. Malicious callables are represented only
as inert strings; no external service, engine process, GPU, cloud inference,
network call or arbitrary Python code is invoked.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from ual_common import artifacts, extensions, settings
from ual_common import extensions_builtins as builtins
from ual_common.registry import NAMESPACES
from ual_common.toon import parse_toon

EXTERNAL_IDS = frozenset({
    "gateway.integration.comfyui",
    "gateway.integration.trellis",
    "gateway.integration.audiocraft",
    "gateway.integration.unirig_pipeline",
})
LOCAL_IDS = frozenset({
    "gateway.integration.offline_agent_orchestration",
    "gateway.integration.runtime_tool_audit",
})
RESOURCE_FLAGS = (
    "requires_gpu", "requires_cloud", "requires_vlm", "requires_network",
)
ARGUMENTS = {"asset_path": "/Game/Core/BP_Safe"}


@pytest.fixture()
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    plugin_root = tmp_path / "UEAssetLens"
    project_root = tmp_path / "Project"
    plugin_root.mkdir()
    project_root.mkdir()
    monkeypatch.setattr(settings, "PLUGIN_ROOT", plugin_root)
    return (
        plugin_root,
        project_root,
        project_root / "Config" / "UAL",
        project_root / "Saved" / "Config" / "UAL",
    )


def _write_manifest(config_root: Path, filename: str, rules: list[dict]) -> None:
    artifacts.write_artifact(config_root, f"extensions/{filename}", {"rules": rules})


def _base_rule(
    namespace: str,
    name: str,
    impl: str,
    *,
    schema: dict | None = None,
    config: dict | None = None,
    capabilities: list[str] | None = None,
) -> dict:
    rule = {
        "id": f"{namespace}.{name}",
        "namespace": namespace,
        "name": name,
        "enabled": True,
        "version": "1",
        "priority": 0,
        "applies_to": ["*"],
        "capabilities": capabilities or ["custom"],
        "impl": impl,
    }
    if schema is not None:
        rule["schema"] = schema
    if config is not None:
        rule["config"] = config
    return rule


def _rule_sections(rule: dict) -> list[dict]:
    sections = [rule]
    for key in ("config", "schema"):
        value = rule.get(key)
        if isinstance(value, dict):
            sections.append(value)
    return sections


def _spy_executor():
    calls: list[tuple[str, dict]] = []

    def executor(tool: str, arguments: dict) -> dict:
        calls.append((tool, dict(arguments)))
        return {"ok": True}

    return calls, executor


def test_legitimate_nine_namespace_manifests_resolve_static_builtins(roots):
    _, project_root, project_config_root, user_root = roots
    prefix = extensions.BUILTIN_PREFIX
    expected = [
        ("asset_type", "role_resolver", "asset_type_descriptor", {
            "default_role": "unclassified", "roles": [], "assembly": [],
        }),
        ("policy", "no_draft_names", "no_draft_names", {
            "forbidden": ["Draft"], "message": "draft assets are not publishable",
        }),
        ("field", "asset_role", "field_from_domain", {
            "source": "domain_spec", "key": "feature.role", "default": "unclassified",
        }),
        ("diff", "l0_metadata", "diff_layer", {
            "layer": "L0", "kind": "fields", "source": "index.metadata",
            "fields": ["asset_path", "asset_class"],
        }),
        ("tool", "security_probe", "runtime_tool", {
            "handler": "ual_security_probe",
            "mcp_name": "UEAssetLens.SecurityProbe.ReadProbe",
            "accessMode": "Read",
            "description": "Deterministic offline probe",
            "inputSchema": {"type": "object", "properties": {}},
        }),
        ("lifecycle", "pre_validate", "lifecycle_hook", {"status": "observed"}),
        ("sink", "yaml", "yaml_sink", {"format": "yaml", "encoding": "utf-8"}),
        ("template", "security_template", "fps_3c_template", {
            "template_id": "security_template", "version": "1",
        }),
        ("gateway", "official", "official_gateway", {
            "whitelist": "Config/UAL/gateway/official_tools.yaml",
            "default_status": "deferred_offline",
            "allow_executor_injection": False,
        }),
    ]
    rules = [
        _base_rule(namespace, name, prefix + callable_name, schema=schema)
        for namespace, name, callable_name, schema in expected
    ]
    _write_manifest(project_config_root, "legitimate.yaml", rules)

    loaded = extensions.load_registry(
        project_root=project_root, user_root=user_root
    )
    assert len(loaded.registry) == 9
    assert {row["namespace"] for row in loaded.registry.snapshot()} == set(NAMESPACES)
    for namespace, name, callable_name, _schema in expected:
        entry = loaded.registry.get(namespace, name)
        assert entry is not None
        spec, impl = entry
        assert spec.namespace == namespace and spec.name == name
        assert impl is getattr(builtins, callable_name)
        assert impl.__module__ == extensions.BUILTIN_MODULE

    tool_defs = extensions.tool_defs_from_registry(loaded.registry)
    assert len(tool_defs) == 1
    extensions.validate_tool_names(tool_defs)
    assert tool_defs[0]["mcp_name"] == "UEAssetLens.SecurityProbe.ReadProbe"

    text = extensions.render_registry_snapshot(loaded.registry, limit=20)
    assert text == extensions.render_registry_snapshot(loaded.registry, limit=20)
    assert list(parse_toon(text)["tables"]) == ["extensions"]
    assert "D:/" not in text and "C:\\" not in text
    assert "updated_at" not in text and "TODO(human)" not in text


def _malicious_rule(name: str, **overrides) -> dict:
    rule = _base_rule("policy", f"evil_{name}", extensions.BUILTIN_PREFIX + "no_draft_names")
    rule.update(overrides)
    return rule


MALICIOUS_RULES = [
    pytest.param(_malicious_rule("os_system", impl="os.system"), id="os_system"),
    pytest.param(
        _malicious_rule("nested_os", impl=extensions.BUILTIN_PREFIX + "os.system"),
        id="nested_os_system",
    ),
    pytest.param(
        _malicious_rule(
            "nested_module",
            impl=extensions.BUILTIN_PREFIX + "nested.module.runtime_tool",
        ),
        id="nested_dotted_module",
    ),
    pytest.param(
        _malicious_rule("private", impl=extensions.BUILTIN_PREFIX + "_normalize_scalar"),
        id="private_non_allowlisted",
    ),
    pytest.param(
        _malicious_rule("arbitrary", impl="some.module.callable"),
        id="arbitrary_impl",
    ),
    pytest.param(
        _malicious_rule("importlib", impl="importlib.import_module"),
        id="importlib_impl",
    ),
    pytest.param(_malicious_rule("none_impl", impl=None), id="non_string_impl"),
    pytest.param(
        _malicious_rule("list_impl", impl=["os", "system"]),
        id="list_impl",
    ),
    pytest.param(
        _malicious_rule("nested_impl", config={"module": "os", "impl": "system"}),
        id="nested_impl_reference",
    ),
    pytest.param(
        _malicious_rule("executor", config={"executor": "os.system"}),
        id="executor_reference",
    ),
    pytest.param(_malicious_rule("eval", schema={"eval": "1 + 1"}), id="eval_key"),
    pytest.param(_malicious_rule("exec", schema={"exec": "1 + 1"}), id="exec_key"),
    pytest.param(_malicious_rule("code", schema={"code": "1 + 1"}), id="code_key"),
    pytest.param(_malicious_rule("script", schema={"script": "1 + 1"}), id="script_key"),
    pytest.param(_malicious_rule("pickle", schema={"pickle": "payload"}), id="pickle_key"),
    pytest.param(
        _malicious_rule("dotted_handler", schema={"handler": "os.system"}),
        id="dotted_handler",
    ),
    pytest.param(
        _malicious_rule("executable_text", schema={"description": "os.system"}),
        id="executable_text",
    ),
    pytest.param(
        _malicious_rule("object_reference", schema={"object": {"call": "payload"}}),
        id="object_reference",
    ),
]


@pytest.mark.parametrize("rule", MALICIOUS_RULES)
def test_malicious_manifests_are_rejected(roots, rule):
    _, project_root, project_config_root, user_root = roots
    _write_manifest(project_config_root, "evil.yaml", [rule])
    with pytest.raises(ValueError):
        extensions.load_registry(project_root=project_root, user_root=user_root)


def test_python_object_tags_never_execute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project_root = tmp_path / "Project"
    config_root = project_root / "Config" / "UAL" / "extensions"
    config_root.mkdir(parents=True, exist_ok=True)
    raw = """rules:
  - id: policy.evil_python_tag
    namespace: policy
    name: evil_python_tag
    enabled: true
    impl: !!python/object/apply:os.system [ual-security-probe]
"""
    (config_root / "evil.yaml").write_text(raw, encoding="utf-8")
    monkeypatch.setattr(settings.yaml, "safe_load", yaml.unsafe_load)
    monkeypatch.setattr(settings.yaml, "load", yaml.unsafe_load)
    monkeypatch.setattr(os, "system", lambda *_args: pytest.fail("os.system executed"))
    with pytest.raises(ValueError, match="Python object tags"):
        settings.load_namespace("extensions", project_root=project_root)


def test_importlib_monkeypatch_cannot_resolve_manifest_callable(
    roots, monkeypatch: pytest.MonkeyPatch
):
    calls: list[str] = []

    def fake_import(name: str):
        calls.append(name)
        return os

    monkeypatch.setattr(extensions.importlib, "import_module", fake_import)
    with pytest.raises(ValueError):
        extensions._resolve_builtin(extensions.BUILTIN_PREFIX + "os.system")
    assert calls == []
    assert (
        extensions._resolve_builtin(extensions.BUILTIN_PREFIX + "no_draft_names")
        is builtins.no_draft_names
    )

    _, project_root, project_config_root, user_root = roots
    _write_manifest(project_config_root, "evil.yaml", [
        _malicious_rule("monkeypatch", impl=extensions.BUILTIN_PREFIX + "os.system"),
    ])
    with pytest.raises(ValueError):
        extensions.load_registry(project_root=project_root, user_root=user_root)
    assert calls == []


def test_disabled_external_services_never_become_active_or_executable(tmp_path: Path):
    project_root = tmp_path / "Project"
    project_root.mkdir()
    user_root = project_root / "Saved" / "Config" / "UAL"
    resolved = settings.load_namespace(
        "extensions", project_root=project_root, user_root=user_root
    )
    by_id = {rule["id"]: rule for rule in resolved.rules}
    assert EXTERNAL_IDS <= set(by_id) and LOCAL_IDS <= set(by_id)

    for rule_id in EXTERNAL_IDS:
        rule = by_id[rule_id]
        assert rule["enabled"] is False
        assert rule["license"]
        assert rule["license_review"]
        assert rule["zero_vlm_boundary"] is True
        assert rule["executed_by_ual"] is False
        assert rule["redistributed_by_ual"] is False
        assert rule["invoked_by_ual"] is False
        assert rule["allow_executor_injection"] is False
        assert rule["config"]["enabled"] is False
        assert rule["schema"]["enabled"] is False
        assert rule["config"]["executed_by_ual"] is False
        assert "impl" not in rule
        assert "executor" not in rule

    active_ids = {rule["id"] for rule in resolved.active_rules()}
    assert not (EXTERNAL_IDS & active_ids)
    assert LOCAL_IDS <= active_ids
    for rule in resolved.active_rules():
        for section in _rule_sections(rule):
            assert all(section.get(flag) is not True for flag in RESOURCE_FLAGS)

    loaded = extensions.load_registry(
        project_root=project_root, user_root=user_root
    )
    for rule_id in EXTERNAL_IDS:
        name = by_id[rule_id]["name"]
        assert loaded.registry.get("gateway", name) is None
    for rule_id in LOCAL_IDS:
        name = by_id[rule_id]["name"]
        entry = loaded.registry.get("gateway", name)
        assert entry is not None
        spec, impl = entry
        assert impl is builtins.official_gateway
        assert spec.schema["allow_executor_injection"] is False


def test_higher_layer_cannot_activate_external_service(tmp_path: Path):
    project_root = tmp_path / "Project"
    config_root = project_root / "Config" / "UAL"
    user_root = project_root / "Saved" / "Config" / "UAL"
    rule = _base_rule(
        "gateway",
        "comfyui_spec",
        extensions.BUILTIN_PREFIX + "official_gateway",
        schema={"enabled": True},
        config={
            "enabled": True,
            "requires_gpu": True,
            "requires_cloud": True,
            "requires_vlm": True,
            "requires_network": True,
        },
        capabilities=["external_disabled"],
    )
    rule["id"] = "gateway.integration.comfyui"
    _write_manifest(config_root, "override.yaml", [rule])

    resolved = settings.load_namespace(
        "extensions", project_root=project_root, user_root=user_root
    )
    override = next(
        item for item in resolved.rules
        if item["id"] == "gateway.integration.comfyui"
    )
    assert override["enabled"] is True  # retained for diagnosis, never active
    assert "gateway.integration.comfyui" not in {
        item["id"] for item in resolved.active_rules()
    }
    loaded = extensions.load_registry(
        project_root=project_root, user_root=user_root
    )
    assert loaded.registry.get("gateway", "comfyui_spec") is None


def test_registry_rechecks_active_boundary_even_if_filter_bypassed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    project_root = tmp_path / "Project"
    project_root.mkdir()
    user_root = project_root / "Saved" / "Config" / "UAL"
    monkeypatch.setattr(
        settings.ResolvedSettings, "active_rules", lambda self: self.rules
    )
    loaded = extensions.load_registry(
        project_root=project_root, user_root=user_root
    )
    for name in ("comfyui_spec", "trellis_spec", "audiocraft_spec", "unirig_pipeline_spec"):
        assert loaded.registry.get("gateway", name) is None


DENY_SPECS = [
    pytest.param({}, id="default_deny"),
    pytest.param({"allow_executor_injection": False}, id="top_level_false"),
    pytest.param(
        {"config": {"allow_executor_injection": False}}, id="config_false"
    ),
    pytest.param(
        {"schema": {"allow_executor_injection": False}}, id="schema_false"
    ),
    pytest.param(
        {
            "allow_executor_injection": False,
            "config": {"allow_executor_injection": True},
        },
        id="top_level_precedence",
    ),
    pytest.param(
        {"allow_executor_injection": True, "capabilities": ["external_disabled"]},
        id="external_boundary",
    ),
    pytest.param(
        {"allow_executor_injection": True, "schema": {"requires_gpu": True}},
        id="gpu_requirement",
    ),
    pytest.param(
        {"allow_executor_injection": True, "config": {"enabled": False}},
        id="disabled_spec",
    ),
]


@pytest.mark.parametrize("spec", DENY_SPECS)
def test_official_gateway_never_invokes_disallowed_executor(spec):
    calls, executor = _spy_executor()
    result = builtins.official_gateway(
        {"tool": "SaveAsset", "arguments": ARGUMENTS, "executor": executor},
        spec,
    )
    assert result["status"] == "deferred_offline"
    assert "result" not in result
    assert calls == []


def test_official_gateway_executes_only_with_explicit_opt_in():
    calls, executor = _spy_executor()
    result = builtins.official_gateway(
        {"tool": "SaveAsset", "arguments": ARGUMENTS, "executor": executor},
        {"name": "explicit_opt_in", "allow_executor_injection": True},
    )
    assert result["status"] == "executed"
    assert result["result"] == {"ok": True}
    assert calls == [("SaveAsset", ARGUMENTS)]


def test_registry_gateway_spec_denies_injected_executor(tmp_path: Path):
    project_root = tmp_path / "Project"
    project_root.mkdir()
    user_root = project_root / "Saved" / "Config" / "UAL"
    loaded = extensions.load_registry(
        project_root=project_root, user_root=user_root
    )
    entry = loaded.registry.get("gateway", "offline_agent_orchestration")
    assert entry is not None
    spec, impl = entry
    assert spec.schema["allow_executor_injection"] is False
    calls, executor = _spy_executor()
    result = impl(
        {"tool": "SaveAsset", "arguments": ARGUMENTS, "executor": executor},
        {"name": spec.name, "schema": dict(spec.schema)},
    )
    assert result["status"] == "deferred_offline"
    assert calls == []
