#!/usr/bin/env python3
"""Stateless request/response bridge for UAL's in-editor Unreal MCP tools.

The C++ module Source/UEAssetLensMCP launches this script once per tool call via
FPlatformProcess::ExecProcess. Arguments and results travel through UTF-8 files
rather than stdout so that Chinese annotations in TOON/YAML survive intact on
Windows (ExecProcess returns stdout as an ANSI-ish FString).

Request YAML : tool / arguments mapping (UTF-8 file, no BOM)
Response     : raw TOON or YAML text (UTF-8, no BOM)

Usage:
  python engine_bridge.py --request req.yaml --response resp.txt [--index .ual/index.duckdb]
  python engine_bridge.py --self-test
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))


def _bootstrap_pip_site_packages() -> None:
    """Expose UE6's official plugin pip dependencies to this child process.

    The editor adds <Project>/Intermediate/PipInstall/Lib/site-packages to its
    own embedded interpreter via PyUtil::AddSitePackagesPath. This script is
    launched as a separate process, so it must add that path before importing
    duckdb / yaml. Dependencies are declared in UEAssetLens.uplugin under
    PythonRequirements and installed by PythonScriptPlugin.
    """
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if any(candidate.glob("*.uproject")):
            site_packages = candidate / "Intermediate" / "PipInstall" / "Lib" / "site-packages"
            if site_packages.is_dir() and str(site_packages) not in sys.path:
                sys.path.insert(0, str(site_packages))
            break


_bootstrap_pip_site_packages()

import yaml  # noqa: E402
from ual_common import db  # noqa: E402
from ual_common.toon import parse_toon  # noqa: E402
from ual_mcp import tools  # noqa: E402

TOOL_NAMES = tuple(tools.TOOL_NAMES)
EXPECTED_TOOL_COUNT = 30
_DETERMINISTIC_ID = "0" * 64
if len(TOOL_NAMES) != EXPECTED_TOOL_COUNT:
    raise ValueError("unexpected UAL tool count")


def dispatch(
    con: Any,
    name: str,
    arguments: dict[str, Any],
    *,
    executor: Any = None,
) -> str:
    """Map an MCP tool name onto the shared, already-tested Python implementation."""
    if name not in TOOL_NAMES:
        raise KeyError(f"unknown UAL tool: {name!r}; expected one of {TOOL_NAMES}")
    args = arguments or {}
    if name in tools.RUNTIME_TOOL_NAMES:
        return tools.dispatch_runtime_tool(con, name, args, executor=executor)
    if name == "UEAssetLens.AssetSemanticToolset.SearchAssets":
        return tools.ual_search_assets(
            con,
            query=args.get("query"),
            asset_class=args.get("asset_class"),
            filters=args.get("filters"),
            domain_scope=args.get("domain_scope"),
            limit=args.get("limit", 8),
        )
    if name == "UEAssetLens.AssetSemanticToolset.InspectAsset":
        return tools.ual_inspect_asset(con, args.get("key") or args.get("asset_path") or "")
    if name == "UEAssetLens.AssetSemanticToolset.GetCooccurringBundle":
        return tools.ual_get_cooccurring_bundle(con, args["asset_path"], args.get("top_k", 5))
    if name == "UEAssetLens.AssetSemanticToolset.FindSimilarAssets":
        return tools.ual_find_similar_assets(con, args["asset_path"], args.get("threshold", 0.85))
    if name == "UEAssetLens.AssetGraphToolset.GetDependencies":
        return tools.ual_get_dependencies(
            con, args.get("asset_path") or "", args.get("max_depth", 2),
            args.get("edge_type", "DEPENDS_ON"), args.get("limit", 50))
    if name == "UEAssetLens.AssetGraphToolset.GetReferencers":
        return tools.ual_get_referencers(
            con, args.get("asset_path") or "", args.get("max_depth", 2),
            args.get("edge_type", "DEPENDS_ON"), args.get("limit", 50))
    if name == "UEAssetLens.AssetGraphToolset.GetIndexHealth":
        return tools.ual_get_index_health(con, args.get("top_n", 5))
    if name == "UEAssetLens.AssetGraphToolset.FindDuplicates":
        return tools.ual_find_duplicates(con, args.get("threshold", 0.95), args.get("limit", 20))
    if name == "UEAssetLens.AssetSemanticToolset.SearchByFeatures":
        return tools.ual_search_by_features(
            con, feature_key=args.get("feature_key"),
            value_contains=args.get("value_contains"),
            asset_class=args.get("asset_class"), family=args.get("family"),
            limit=args.get("limit", 20))
    if name == "UEAssetLens.AssetSemanticToolset.ListFeatureKeys":
        return tools.ual_list_feature_keys(con, args.get("limit", 50))
    raise AssertionError("unreachable")


def handle(request_path: Path, response_path: Path, index: str | None) -> int:
    try:
        request = yaml.safe_load(request_path.read_text(encoding="utf-8"))
        name = request["tool"]
        arguments = request.get("arguments") or {}
        con = db.open_readonly(index)
        try:
            text = dispatch(con, name, arguments)
        finally:
            con.close()
    except Exception as exc:  # surface a readable error to the MCP client
        message = f"UAL bridge error: {type(exc).__name__}: {exc}"
        print(message, file=sys.stderr)
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_text(message, encoding="utf-8", newline="\n")
        return 1

    response_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.write_text(text, encoding="utf-8", newline="\n")
    return 0


def static_self_test_cases() -> list[tuple[str, dict[str, Any]]]:
    """Safe bounded cases for the original ten read-only tools."""
    hero = "/Game/Characters/Knight/Anim/AM_Knight_HeavySlash_02"
    return [
        ("UEAssetLens.AssetSemanticToolset.SearchAssets", {"query": "heavy slash", "asset_class": "AnimMontage", "limit": 3}),
        ("UEAssetLens.AssetSemanticToolset.InspectAsset", {"key": "AnimMontage:AM_Knight_HeavySlash_02"}),
        ("UEAssetLens.AssetSemanticToolset.GetCooccurringBundle", {"asset_path": hero, "top_k": 3}),
        ("UEAssetLens.AssetSemanticToolset.FindSimilarAssets", {"asset_path": hero, "threshold": 0.95}),
        ("UEAssetLens.AssetGraphToolset.GetDependencies", {"asset_path": hero, "max_depth": 3}),
        ("UEAssetLens.AssetGraphToolset.GetReferencers", {"asset_path": hero, "max_depth": 3}),
        ("UEAssetLens.AssetGraphToolset.GetIndexHealth", {"top_n": 5}),
        ("UEAssetLens.AssetGraphToolset.FindDuplicates", {"threshold": 0.95, "limit": 10}),
        ("UEAssetLens.AssetSemanticToolset.SearchByFeatures", {"feature_key": "kinematics"}),
        ("UEAssetLens.AssetSemanticToolset.ListFeatureKeys", {"limit": 20}),
    ]


def _self_test_import_payload() -> dict[str, Any]:
    """Build one structured, declarative host GameSpec payload for self-test."""
    from ual_common import gamespec

    template = gamespec.load_template("fps_3c")
    return {
        "template": "fps_3c",
        "spec": dict(template["game_spec"]),
    }


def runtime_self_test_cases() -> list[tuple[str, dict[str, Any]]]:
    """Safe dry-run / read-only cases for all 20 registry-driven tools."""
    hero = "/Game/Characters/Knight/Anim/AM_Knight_HeavySlash_02"
    return [
        ("UEAssetLens.AssetKernelToolset.ListExtensions", {"limit": 8}),
        ("UEAssetLens.AssetKernelToolset.GetIndexRevision", {}),
        ("UEAssetLens.AssetKernelToolset.SearchExtensionFields", {"limit": 5}),
        ("UEAssetLens.AssetKernelToolset.ListExtensionFields", {"limit": 5}),
        ("UEAssetLens.AssetKernelToolset.GetAssemblyGraph", {"asset_path": hero, "limit": 5}),
        ("UEAssetLens.AssetKernelToolset.ListOfficialTools", {"limit": 5}),
        ("UEAssetLens.AssetKernelToolset.RequestRescan", {"reason": "index consistency", "dry_run": True}),
        ("UEAssetLens.AssetDiffToolset.DiffAssets", {"lhs": hero, "rhs": hero, "layers": "L0"}),
        ("UEAssetLens.AssetDiffToolset.ReadDiffLayer", {"diff_id": "@diff_id", "layer": "L0", "limit": 20}),
        ("UEAssetLens.AssetDiffToolset.VerifyDiffDeterminism", {"diff_id": "@diff_id"}),
        ("UEAssetLens.AssetEditTransactionToolset.PlanEdit", {"intent": "tag", "candidates": hero, "max_ops": 1}),
        ("UEAssetLens.ArtifactToolset.ReadArtifact", {"relative": "@plan_relative", "limit": 20}),
        ("UEAssetLens.AssetEditTransactionToolset.ApplyPlan", {"plan_id": "@plan_id", "approved_by": "self-test", "approvals": "op-001", "dry_run": True}),
        ("UEAssetLens.AssetEditTransactionToolset.VerifyEdit", {"plan_id": "@plan_id"}),
        ("UEAssetLens.AssetEditTransactionToolset.RollbackEdit", {"plan_id": "@plan_id", "approved_by": "self-test", "approvals": "op-001.inverse", "dry_run": True}),
        ("UEAssetLens.GameSpecToolset.ExpandGameSpec", {"intent": "fps 3c shooter", "template": "fps_3c"}),
        ("UEAssetLens.GameSpecToolset.ImportGameSpec", {"payload": _self_test_import_payload(), "dry_run": True}),
        ("UEAssetLens.GameSpecToolset.RunValidationLoop", {"game_spec_id": "@game_spec_id", "runner": "offline", "dry_run": True}),
        ("UEAssetLens.AssetKernelToolset.GetLifecycleEvents", {"limit": 8}),
        ("UEAssetLens.GameSpecToolset.RunFps3CWorkflow", {"intent": "fps 3c shooter", "dry_run": True}),
    ]


def self_test_cases() -> list[tuple[str, dict[str, Any]]]:
    return static_self_test_cases() + runtime_self_test_cases()


def _resolve_case_value(value: Any, identifiers: dict[str, str]) -> Any:
    if isinstance(value, str) and value.startswith("@"):
        return identifiers.get(value[1:], _DETERMINISTIC_ID)
    if isinstance(value, dict):
        return {
            key: _resolve_case_value(item, identifiers)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve_case_value(item, identifiers) for item in value]
    return value


def resolve_case_arguments(
    arguments: dict[str, Any], identifiers: dict[str, str]
) -> dict[str, Any]:
    """Replace @id markers with deterministic IDs captured from earlier cases."""
    return {
        key: _resolve_case_value(value, identifiers)
        for key, value in arguments.items()
    }


def capture_case_identifiers(text: str, identifiers: dict[str, str]) -> None:
    """Record bounded TOON summary IDs for dependent offline artifact cases."""
    summary = parse_toon(text).get("summary") or {}
    for key in ("diff_id", "plan_id", "game_spec_id", "workflow_id"):
        value = str(summary.get(key, ""))
        if value:
            identifiers[key] = value
    artifact = str(summary.get("artifact", ""))
    if artifact.startswith(".ual/"):
        relative = artifact[len(".ual/"):]
        if "plan_id" in summary:
            identifiers["plan_relative"] = relative
        if "game_spec_id" in summary and "runner" not in summary:
            identifiers["gamespec_relative"] = relative
        if "game_spec_id" in summary and "runner" in summary:
            identifiers["validation_relative"] = relative
        if "workflow_id" in summary:
            identifiers["workflow_relative"] = relative


def self_test() -> int:
    index = Path(tempfile.gettempdir()) / "ual_bridge_self_test.duckdb"
    index.unlink(missing_ok=True)
    con = db.open_write(index)
    db.ingest_records(con, db.load_yaml_docs(SERVER_ROOT / "tests" / "seed.yaml"))
    con.close()
    shutil.rmtree(tools.RUNTIME_ARTIFACT_ROOT, ignore_errors=True)

    cases = self_test_cases()
    static_cases = static_self_test_cases()
    ok = len(static_cases) == len(tools.STATIC_TOOL_NAMES)
    ok &= len(cases) == len(tools.TOOL_NAMES)
    ok &= {name for name, _ in cases} == set(tools.TOOL_NAMES)
    print(f"  tool coverage: {'PASS' if ok else 'FAIL'} ({len(cases)} tools)")

    fallback_relative = f"plans/{_DETERMINISTIC_ID}.yaml"
    identifiers = {
        "diff_id": _DETERMINISTIC_ID,
        "plan_id": _DETERMINISTIC_ID,
        "game_spec_id": _DETERMINISTIC_ID,
        "workflow_id": _DETERMINISTIC_ID,
        "plan_relative": fallback_relative,
        "gamespec_relative": f"gamespecs/{_DETERMINISTIC_ID}.yaml",
        "validation_relative": f"validation/{_DETERMINISTIC_ID}.yaml",
        "workflow_relative": f"workflow/{_DETERMINISTIC_ID}.yaml",
    }
    ro = db.open_readonly(index)
    try:
        for name, arguments in cases:
            try:
                text = dispatch(ro, name, resolve_case_arguments(arguments, identifiers))
                passed = bool(text.strip()) and text.startswith("# UEAssetLens")
                if name in tools.RUNTIME_TOOL_NAMES:
                    passed &= text.startswith("# UEAssetLens TOON v1 (Zero-VLM)")
                capture_case_identifiers(text, identifiers)
                print(f"  {name}: {'PASS' if passed else 'FAIL'}")
                ok &= passed
            except Exception as exc:
                print(f"  {name}: FAIL ({type(exc).__name__}: {exc})")
                ok = False
        try:
            dispatch(ro, "ual_not_a_tool", {})
            print("  unknown tool guard: FAIL")
            ok = False
        except KeyError:
            print("  unknown tool guard: PASS")
    finally:
        ro.close()
    print(f"engine_bridge self-test: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="UAL in-editor MCP bridge")
    parser.add_argument("--request", help="Path to the request YAML file")
    parser.add_argument("--response", help="Path to write the TOON/YAML response")
    parser.add_argument("--index", default=None, help="Path to .ual/index.duckdb")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.request or not args.response:
        parser.error("--request and --response are required unless --self-test is used")
    return handle(Path(args.request), Path(args.response), args.index)


if __name__ == "__main__":
    raise SystemExit(main())
