"""Contract tests for the offline resource gateway (no UE, no network)."""
from __future__ import annotations

import re

import pytest

from ual_common import gateway, toon

HEADER = "# UEAssetLens TOON v1 (Zero-VLM)"


@pytest.fixture()
def gw():
    return gateway.load_gateway()


def _op(tool="AddUserAssetTags", **arguments):
    return {
        "id": "op-001",
        "tool": tool,
        "arguments": {"asset_path": "/Game/Hero/M_Hero", **arguments},
        "baseline": {
            "asset": "/Game/Hero/M_Hero",
            "assets": ["/Game/Hero/M_Hero"],
            "tags": ["baseline"],
            "metadata": {},
            "properties": {},
            "material": "",
            "enabled": False,
            "transform": {},
            "editor_open": False,
            "asset_exists": False,
            "destination_exists": False,
            "folder_exists": False,
            "collection_exists": False,
        },
        "accessMode": "Write",
        "inverse": "RemoveUserAssetTags",
        "risk": "low",
    }


def test_whitelist_comes_from_settings_and_is_bounded(gw):
    rows = gw.tools(limit=5)
    assert len(rows) == 5
    assert len(gw.tools()) >= 29
    assert rows[0]["accessMode"] == rows[0]["access_mode"] == "Write"
    text = gw.render_whitelist(limit=5)
    parsed = toon.parse_toon(text)
    assert text.startswith(HEADER)
    assert parsed["summary"]["returned"] == "5"
    assert parsed["summary"]["truncated"] == "true"
    assert len(parsed["tables"]["official_tools"]) == 5
    assert "ual_" not in text


@pytest.mark.parametrize(
    "operation",
    [
        {"tool": "NoSuchTool", "arguments": {}},
        {"tool": "ual_save_asset", "arguments": {}},
        {"tool": "UEAssetLens.AssetToolset.SaveAsset", "arguments": {}},
        {"tool": "saveasset", "arguments": {}},
        {"tool": "SaveAsset", "accessMode": "Read", "arguments": {}},
        {"tool": "SaveAsset", "arguments": []},
        {"tool": "SaveAsset", "arguments": {"asset_path": "D:/work/A.uasset"}},
        {"tool": "SaveAsset", "arguments": {"asset_path": "C:\\Users\\me\\A.uasset"}},
        {"tool": "SaveAsset", "arguments": {"asset_path": "/Engine/Foo"}},
        {"tool": "SaveAsset", "arguments": {"asset_path": "/Script/Engine.Actor"}},
        {"tool": "SaveAsset", "arguments": {"asset_path": "Content/Foo"}},
        {"tool": "SaveAsset", "arguments": {"timestamp": "2026-11-26T10:00:00Z"}},
        {"tool": "SaveAsset", "arguments": {"session_id": "s1"}},
        {"tool": "SaveAsset", "arguments": {"uuid": "550e8400-e29b-41d4-a716-446655440000"}},
        {"tool": "SaveAsset", "arguments": {"updated_at": "now"}},
        {"tool": "SaveAsset", "arguments": {"script": "print(1)"}},
    ],
)
def test_whitelist_rejects_invalid_operations(gw, operation):
    ok, reason = gw.validate(operation)
    assert ok is False
    assert isinstance(reason, str) and reason
    assert not re.search(r"[A-Za-z]:[\\/]", reason)


def test_valid_operation_passes_and_inverse_comes_from_yaml(gw):
    operation = _op()
    assert gw.validate(operation) == (True, "ok")
    inverse = gw.inverse(operation)
    assert inverse["tool"] == "AddUserAssetTags"
    assert inverse["inverse"] == "AddUserAssetTags"
    assert inverse["arguments"] == {
        "asset_paths": ["/Game/Hero/M_Hero"],
        "tags": ["baseline"],
    }
    assert inverse == gw.inverse(operation)
    assert gw.inverse(inverse)["tool"] == operation["tool"]
    assert gw.validate(inverse)[0] is True
    with pytest.raises(ValueError):
        gw.inverse({"tool": "NoSuchTool", "arguments": {}})


def test_execute_statuses_and_render_execution(gw):
    operation = _op("SaveAsset")
    dry = gw.execute(operation)
    assert dry["status"] == "dry_run" and dry["dry_run"] is True
    offline = gw.execute(operation, dry_run=False)
    assert offline["status"] == "deferred_offline"
    calls: list = []

    def executor(tool, arguments):
        calls.append((tool, dict(arguments)))
        return {"status": "ok"}

    executed = gw.execute(operation, dry_run=False, executor=executor)
    assert executed["status"] == "executed"
    assert executed["result"] == {"status": "ok"}
    assert calls == [("SaveAsset", {"asset_path": "/Game/Hero/M_Hero"})]

    def single_arg_executor(operation_arg):
        return {"status": "ok", "tool": operation_arg["tool"]}

    assert gw.execute(operation, dry_run=False, executor=single_arg_executor)["status"] == "executed"
    assert gw.execute({"tool": "NoSuchTool", "arguments": {}}, dry_run=False, executor=executor)["status"] == "rejected"
    text = gw.render_execution([dry, offline, executed], limit=2)
    parsed = toon.parse_toon(text)
    assert text.startswith(HEADER)
    assert parsed["summary"]["returned"] == "2"
    assert parsed["summary"]["truncated"] == "true"
    assert [row["status"] for row in parsed["tables"]["execution"]] == ["dry_run", "deferred_offline"]
    single = toon.parse_toon(gw.render_execution(executed))
    assert single["summary"]["returned"] == "1"
    assert single["tables"]["execution"][0]["status"] == "executed"


def test_outputs_have_no_paths_timestamps_or_random_ids(gw):
    text = gw.render_whitelist() + gw.render_execution([gw.execute(_op())])
    assert "D:/" not in text and ":\\" not in text
    assert not re.search(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", text)
    assert "ual_" not in text


def test_inverse_restores_baseline_and_refuses_irreversible_operation(gw):
    operation = _op(tags=["new"])
    operation["baseline"]["tags"] = ["old"]
    inverse = gw.inverse(operation)
    calls: list[tuple[str, dict]] = []

    def executor(tool, arguments):
        calls.append((tool, dict(arguments)))
        return {"status": "ok"}

    result = gw.execute(inverse, dry_run=False, executor=executor)
    assert result["status"] == "executed"
    assert calls == [(
        "AddUserAssetTags",
        {"asset_paths": ["/Game/Hero/M_Hero"], "tags": ["old"]},
    )]
    irreversible = _op("Delete")
    with pytest.raises(ValueError, match="not recoverable"):
        gw.inverse(irreversible)
