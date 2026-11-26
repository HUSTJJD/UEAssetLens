"""Registry-driven runtime MCP tool contract tests.

All checks are offline. Concurrent runtime modules are imported with
``pytest.importorskip`` so this file can land alongside their implementation.
The engine-side full suite is run by the main session once every module exists.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

for _module in (
    "fields", "allowlist", "lifecycle", "revision", "gateway",
    "transaction", "asset_diff", "gamespec", "workflow",
    "artifacts", "extensions",
):
    pytest.importorskip(f"ual_common.{_module}")

from ual_common import db, extensions, fields  # noqa: E402
from ual_common.toon import HEADER as TOON_HEADER, parse_toon  # noqa: E402
from ual_mcp import engine_bridge, tools  # noqa: E402

SEED = Path(__file__).parent / "seed.yaml"
DETERMINISTIC_ID = "0" * 64

EXPECTED_STATIC_NAMES = (
    "UEAssetLens.AssetSemanticToolset.SearchAssets",
    "UEAssetLens.AssetSemanticToolset.InspectAsset",
    "UEAssetLens.AssetSemanticToolset.GetCooccurringBundle",
    "UEAssetLens.AssetSemanticToolset.FindSimilarAssets",
    "UEAssetLens.AssetGraphToolset.GetDependencies",
    "UEAssetLens.AssetGraphToolset.GetReferencers",
    "UEAssetLens.AssetGraphToolset.GetIndexHealth",
    "UEAssetLens.AssetGraphToolset.FindDuplicates",
    "UEAssetLens.AssetSemanticToolset.SearchByFeatures",
    "UEAssetLens.AssetSemanticToolset.ListFeatureKeys",
)
EXPECTED_RUNTIME_NAMES = frozenset({
    "UEAssetLens.AssetDiffToolset.DiffAssets",
    "UEAssetLens.AssetDiffToolset.ReadDiffLayer",
    "UEAssetLens.AssetDiffToolset.VerifyDiffDeterminism",
    "UEAssetLens.AssetEditTransactionToolset.ApplyPlan",
    "UEAssetLens.AssetEditTransactionToolset.PlanEdit",
    "UEAssetLens.AssetEditTransactionToolset.RollbackEdit",
    "UEAssetLens.AssetEditTransactionToolset.VerifyEdit",
    "UEAssetLens.AssetKernelToolset.GetAssemblyGraph",
    "UEAssetLens.AssetKernelToolset.GetIndexRevision",
    "UEAssetLens.AssetKernelToolset.GetLifecycleEvents",
    "UEAssetLens.AssetKernelToolset.ListExtensionFields",
    "UEAssetLens.AssetKernelToolset.ListExtensions",
    "UEAssetLens.AssetKernelToolset.ListOfficialTools",
    "UEAssetLens.AssetKernelToolset.RequestRescan",
    "UEAssetLens.AssetKernelToolset.SearchExtensionFields",
    "UEAssetLens.GameSpecToolset.ExpandGameSpec",
    "UEAssetLens.GameSpecToolset.ImportGameSpec",
    "UEAssetLens.GameSpecToolset.RunFps3CWorkflow",
    "UEAssetLens.GameSpecToolset.RunValidationLoop",
    "UEAssetLens.ArtifactToolset.ReadArtifact",
})
EXPECTED_TOOL_NAMES = EXPECTED_STATIC_NAMES + tuple(sorted(EXPECTED_RUNTIME_NAMES))

_WINDOWS_ABS_RE = re.compile(r"[A-Za-z]:[\\/]")
_POSIX_ABS_RE = re.compile(r"(?<![\w/])/(?:home|Users|tmp|var|mnt|private)/")
_TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
_RANDOM_ID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    r"|\b[0-9a-fA-F]{32}\b"
)
_BARE_WIRE_RE = re.compile(r"(?<![A-Za-z0-9_.])ual_[A-Za-z0-9_]+")


@pytest.fixture(scope="module")
def ro(tmp_path_factory):
    path = tmp_path_factory.mktemp("ual_runtime") / "index.duckdb"
    con = db.open_write(path)
    db.ingest_records(con, db.load_yaml_docs(SEED))
    fields.materialize(con)
    con.close()
    con = db.open_readonly(path)
    yield con
    con.close()


@pytest.fixture()
def runtime_root(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    monkeypatch.setattr(tools, "RUNTIME_ARTIFACT_ROOT", root)
    return root


def _identifiers() -> dict[str, str]:
    return {
        "diff_id": DETERMINISTIC_ID,
        "plan_id": DETERMINISTIC_ID,
        "game_spec_id": DETERMINISTIC_ID,
        "workflow_id": DETERMINISTIC_ID,
    }


def test_registry_snapshot_and_qualified_names():
    loaded = extensions.load_builtin_registry()
    snapshot = loaded.registry.snapshot()
    assert snapshot
    assert len({row["namespace"] for row in snapshot}) == 9

    text = extensions.render_registry_snapshot(loaded.registry, limit=8)
    assert text.startswith(TOON_HEADER)
    parsed = parse_toon(text)
    assert list(parsed["tables"]) == ["extensions"]
    assert len(parsed["tables"]["extensions"]) <= 8
    assert parsed["summary"]["truncated"] in ("true", "false")

    assert len(tools.STATIC_TOOL_NAMES) == 10
    assert len(tools.RUNTIME_TOOL_NAMES) == 20
    assert len(tools.ALL_TOOL_DEFS) == len(tools.TOOL_NAMES) == 30
    assert tools.TOOL_NAMES == EXPECTED_TOOL_NAMES
    assert len(set(tools.TOOL_NAMES)) == 30
    assert set(tools.RUNTIME_TOOL_NAMES) == set(EXPECTED_RUNTIME_NAMES)
    assert set(tools.RUNTIME_HANDLERS) == set(tools.RUNTIME_TOOL_NAMES)

    validated = [
        {**tool, "accessMode": "Read"} for tool in tools.TOOL_DEFS
    ] + tools.RUNTIME_TOOL_DEFS
    extensions.validate_tool_names(validated)
    for name in tools.TOOL_NAMES:
        assert name.startswith("UEAssetLens.")
        assert not name.startswith("ual_")
        assert not _BARE_WIRE_RE.search(name)
    for tool in tools.RUNTIME_TOOL_DEFS:
        assert tool["mcp_name"].startswith("UEAssetLens.")
        assert tool["accessMode"] in ("Read", "Write", "Execute")
        assert isinstance(tool["handler"], str) and tool["handler"].startswith("ual_")


def test_runtime_defs_have_explicit_access_mode_and_handlers():
    by_name = {tool["mcp_name"]: tool for tool in tools.RUNTIME_TOOL_DEFS}
    assert set(by_name) == set(EXPECTED_RUNTIME_NAMES)
    for name, tool in by_name.items():
        assert tool["accessMode"] in ("Read", "Write", "Execute")
        assert tool["readOnlyHint"] == (tool["accessMode"] == "Read")
        assert tool["annotations"] == {
            "accessMode": tool["accessMode"],
            "readOnlyHint": tool["readOnlyHint"],
        }
        assert tool["meta"] == {"accessMode": tool["accessMode"]}
        assert callable(tools.RUNTIME_HANDLERS[name])
        assert set(tool["inputSchema"].get("properties", {})) == tools.RUNTIME_INPUT_PROPERTIES[name]


def test_sdk_lists_dynamic_tools(ro):
    pytest.importorskip("mcp.server.mcpserver")
    from ual_mcp import server

    sdk = server._create_sdk_server()
    server._register(sdk, ro)
    listed = asyncio.run(sdk.list_tools())
    by_name = {tool.name: tool for tool in listed}
    assert set(by_name) == set(tools.TOOL_NAMES) == set(EXPECTED_TOOL_NAMES)
    assert len(by_name) == 30
    for tool in tools.ALL_TOOL_DEFS:
        listed_tool = by_name[tool["mcp_name"]]
        declared = set(tool["inputSchema"].get("properties", {}))
        actual = set(listed_tool.input_schema.get("properties", {}))
        assert declared == actual, tool["mcp_name"]
        mode = str(tool.get("accessMode") or "Read")
        assert listed_tool.annotations is not None
        assert listed_tool.annotations.read_only_hint == (mode == "Read")
        assert listed_tool.meta == {"accessMode": mode}
        assert not hasattr(listed_tool.annotations, "accessMode")


def test_bridge_dispatch_all_tools_and_output_hygiene(ro, runtime_root):
    cases = engine_bridge.self_test_cases()
    assert len(engine_bridge.static_self_test_cases()) == 10
    assert len(cases) == len(tools.TOOL_NAMES) == 30
    assert {name for name, _ in cases} == set(EXPECTED_TOOL_NAMES)

    identifiers = _identifiers()
    outputs: list[tuple[str, str]] = []
    for name, arguments in cases:
        text = engine_bridge.dispatch(
            ro, name, engine_bridge.resolve_case_arguments(arguments, identifiers)
        )
        assert text.startswith("# UEAssetLens"), name
        if name in tools.RUNTIME_TOOL_NAMES:
            assert text.startswith(TOON_HEADER), name
        engine_bridge.capture_case_identifiers(text, identifiers)
        outputs.append((name, text))

    for name, text in outputs:
        assert not _BARE_WIRE_RE.search(text), name
        assert not _WINDOWS_ABS_RE.search(text), name
        assert not _POSIX_ABS_RE.search(text), name
        assert not _TIMESTAMP_RE.search(text), name
        assert not _RANDOM_ID_RE.search(text), name


def test_unknown_tools_are_rejected(ro, runtime_root):
    with pytest.raises(KeyError):
        engine_bridge.dispatch(ro, "ual_not_a_tool", {})
    with pytest.raises(KeyError):
        engine_bridge.dispatch(ro, "UEAssetLens.NotRegistered.Tool", {})
    with pytest.raises(KeyError):
        tools.dispatch_runtime_tool(ro, "ual_list_extensions", {"limit": 1})


def test_new_runtime_tools_dispatch_bounded_dry_run(ro, runtime_root):
    plan_text = tools.dispatch_runtime_tool(
        ro,
        "UEAssetLens.AssetEditTransactionToolset.PlanEdit",
        {"intent": "tag", "candidates": "/Game/Hero/M_Hero", "max_ops": 1},
    )
    relative = parse_toon(plan_text)["summary"]["artifact"]
    page_text = tools.dispatch_runtime_tool(
        ro,
        "UEAssetLens.ArtifactToolset.ReadArtifact",
        {"relative": relative, "limit": 20},
    )
    import_text = tools.dispatch_runtime_tool(
        ro,
        "UEAssetLens.GameSpecToolset.ImportGameSpec",
        {"payload": engine_bridge._self_test_import_payload(), "dry_run": True},
    )
    for text in (page_text, import_text):
        assert text.startswith(TOON_HEADER)
        assert not _BARE_WIRE_RE.search(text)
        assert not _WINDOWS_ABS_RE.search(text)
        assert not _TIMESTAMP_RE.search(text)
        assert not _RANDOM_ID_RE.search(text)
    assert "artifact_page" in parse_toon(page_text)["tables"]
    assert parse_toon(import_text)["summary"]["status"] == "imported"


@pytest.mark.parametrize(
    "name,arguments,approval_id",
    [
        (
            "UEAssetLens.AssetKernelToolset.RequestRescan",
            {"asset_paths": "/Game/Hero/M_Hero"},
            "RequestRescan",
        ),
        (
            "UEAssetLens.GameSpecToolset.RunValidationLoop",
            {"game_spec_id": DETERMINISTIC_ID},
            "RunValidationLoop",
        ),
        (
            "UEAssetLens.GameSpecToolset.RunFps3CWorkflow",
            {"intent": "fps 3c shooter"},
            "RunFps3CWorkflow",
        ),
        (
            "UEAssetLens.GameSpecToolset.ImportGameSpec",
            {"payload": {"template": "fps_3c", "spec": {}}},
            "ImportGameSpec",
        ),
    ],
)
def test_execute_tools_fail_closed_without_approvals_or_host_executor(
    ro, runtime_root, name, arguments, approval_id
):
    calls: list[tuple[str, object]] = []

    def executor(tool: str, context: object) -> dict[str, str]:
        calls.append((tool, context))
        return {"status": "ok"}

    with pytest.raises(ValueError):
        tools.dispatch_runtime_tool(
            ro, name, {**arguments, "dry_run": False}, executor=executor
        )
    assert calls == []
    with pytest.raises(ValueError):
        tools.dispatch_runtime_tool(
            ro,
            name,
            {
                **arguments,
                "dry_run": False,
                "approved_by": "reviewer",
                "approvals": approval_id,
            },
        )
    assert calls == []
    with pytest.raises(ValueError):
        tools.dispatch_runtime_tool(
            ro,
            name,
            {**arguments, "dry_run": False, "approvals": approval_id},
            executor=executor,
        )
    assert calls == []
