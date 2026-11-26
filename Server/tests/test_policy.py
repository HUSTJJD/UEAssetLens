"""Framework skeleton tests: layered preferences, policy engine, extension registry.

These are deliberately independent of UE and of the DuckDB index, so the
asset-kernel foundations can be validated in CI without an engine.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ual_common import policy, settings
from ual_common.registry import ExtensionRegistry, ExtensionSpec, register_callable
from ual_common.toon import parse_toon


def _asset(**overrides):
    asset = {
        "asset_path": "/Game/Core/Gameplay/BP_AnnCharacter",
        "asset_class": "Blueprint",
        "logical_id": "Blueprint:BP_AnnCharacter",
        "domain_spec": {},
        "dependency_paths": [],
    }
    asset.update(overrides)
    return asset


def _write_layer(root: Path, subdir: str, filename: str, rules: list[dict]):
    directory = root / "Config" / "UAL" / "policies" if subdir == "project" else (
        root / "Saved" / "Config" / "UAL" / "policies"
    )
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(
        yaml.safe_dump({"rules": rules}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def test_builtin_rules_are_data_and_default_to_non_blocking(tmp_path):
    resolved = settings.load_namespace("policies", project_root=tmp_path)
    ids = [r["id"] for r in resolved.rules]
    assert "naming.blueprint.prefix" in ids
    assert "directory.content_root" in ids
    assert "reference.no_script_targets" in ids
    # The strict opt-in rule is present in data but excluded from active rules.
    assert "naming.strict_pascal" not in [r["id"] for r in resolved.active_rules()]
    assert not any(r.get("enforce") for r in resolved.active_rules())
    assert resolved.provenance["naming.blueprint.prefix"] == "builtin:default.yaml"


def test_four_layer_override_keeps_first_seen_order(tmp_path):
    _write_layer(tmp_path, "project", "team.yaml", [
        {"id": "naming.blueprint.prefix", "kind": "naming", "pattern": "^CH_",
         "severity": "error", "enforce": True, "message": "team rule"},
        {"id": "team.extra", "kind": "naming", "pattern": "^X_", "severity": "info"},
    ])
    _write_layer(tmp_path, "user", "me.yaml", [
        {"id": "naming.blueprint.prefix", "kind": "naming", "pattern": "^ME_",
         "severity": "warning", "enforce": False, "message": "personal rule"},
    ])
    resolved = settings.load_namespace("policies", project_root=tmp_path)
    by_id = {r["id"]: r for r in resolved.rules}
    assert by_id["naming.blueprint.prefix"]["pattern"] == "^ME_"
    assert by_id["naming.blueprint.prefix"]["enforce"] is False
    assert resolved.provenance["naming.blueprint.prefix"] == "user:me.yaml"
    assert resolved.provenance["team.extra"] == "project:team.yaml"
    # Later layers override in place; insertion order stays deterministic.
    assert resolved.rules[0]["id"] == "naming.blueprint.prefix"
    names = [name for name, _ in settings.layer_roots(tmp_path)]
    assert names[0] == "builtin" and names[-1] == "user"


def test_policy_detects_naming_format_and_reference_violations(tmp_path):
    engine = policy.PolicyEngine(
        settings.load_namespace("policies", project_root=tmp_path)
    )
    violations = engine.validate(_asset(
        asset_path="/Game/Core/Gameplay/badName",
        domain_spec={"FiBData": "blob"},
        dependency_paths=["/Script/Engine.Actor", "/Game/Core/Mesh/SM_Weapon"],
    ))
    ids = {v.rule_id for v in violations}
    assert "naming.blueprint.prefix" in ids
    assert "format.no_binary_blob_tags" in ids
    assert "reference.no_script_targets" in ids
    assert not policy.has_blocking(violations)


def test_applies_to_and_expect_no_match(tmp_path):
    engine = policy.PolicyEngine(
        settings.load_namespace("policies", project_root=tmp_path)
    )
    clean = engine.validate(_asset())
    assert "naming.blueprint.prefix" not in {v.rule_id for v in clean}
    assert "directory.no_temp_folders" not in {v.rule_id for v in clean}
    temp = engine.validate(_asset(asset_path="/Game/Temp/BP_Draft"))
    assert "directory.no_temp_folders" in {v.rule_id for v in temp}
    mesh = engine.validate(_asset(asset_path="/Game/Mesh/weapon", asset_class="StaticMesh"))
    assert "naming.mesh.prefix" in {v.rule_id for v in mesh}
    assert "naming.blueprint.prefix" not in {v.rule_id for v in mesh}


def test_enforce_true_and_severity_error_is_blocking():
    rules = [{
        "id": "team.mandatory", "kind": "naming", "pattern": "^OK_",
        "severity": "error", "enforce": True, "message": "must start with OK_",
    }]
    violations = policy.PolicyEngine(rules).validate(_asset())
    assert policy.has_blocking(violations)
    rules[0]["enforce"] = False
    assert not policy.has_blocking(policy.PolicyEngine(rules).validate(_asset()))


def test_custom_rule_goes_through_extension_registry():
    registry = ExtensionRegistry()
    register_callable(
        registry, "policy", "no_draft_names",
        lambda asset, rule: ["draft assets are not publishable"]
        if "Draft" in asset["asset_path"] else [],
    )
    rules = [{
        "id": "custom.no_draft", "kind": "custom", "extension": "no_draft_names",
        "severity": "warning", "enforce": False,
    }]
    engine = policy.PolicyEngine(rules, registry=registry)
    assert engine.validate(_asset()).__len__() == 0
    hits = engine.validate(_asset(asset_path="/Game/Core/BP_Draft"))
    assert len(hits) == 1 and "draft" in hits[0].message
    missing = policy.PolicyEngine([{
        "id": "custom.absent", "kind": "custom", "extension": "not_registered",
    }], registry=registry).validate(_asset())
    assert "not registered" in missing[0].message


def test_registry_resolution_is_deterministic_and_typed():
    registry = ExtensionRegistry()
    high = ExtensionSpec(namespace="asset_type", name="anim", priority=100,
                         applies_to=("AnimMontage",), capabilities=("diff",))
    low = ExtensionSpec(namespace="asset_type", name="generic", priority=0,
                        applies_to=("*",), capabilities=("diff",))
    registry.register(low, impl="low")
    registry.register(high, impl="high")
    with pytest.raises(ValueError):
        registry.register(high)
    with pytest.raises(ValueError):
        ExtensionSpec(namespace="bogus", name="x")
    resolved = registry.resolve("AnimMontage", capability="diff")
    assert [spec.name for spec, _ in resolved] == ["anim", "generic"]
    assert registry.resolve("StaticMesh", capability="diff")[0][0].name == "generic"
    assert registry.snapshot() == registry.snapshot()
    assert "impl" not in registry.snapshot()[0]


def test_report_is_bounded_toon_and_deterministic(tmp_path):
    engine = policy.PolicyEngine(
        settings.load_namespace("policies", project_root=tmp_path)
    )
    violations = engine.validate_many([_asset(asset_path=f"/Game/Bad{i}") for i in range(6)])
    text = policy.render_report(violations)
    assert text.startswith("# UEAssetLens TOON v1 (Zero-VLM)")
    parsed = parse_toon(text)
    assert parsed["summary"]["total"] == str(len(violations))
    assert len(parsed["tables"]["violations"]) == len(violations)
    assert text == policy.render_report(violations)
    assert "D:/" not in text and "C:\\" not in text
    assert "updated_at" not in text
