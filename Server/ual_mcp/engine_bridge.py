#!/usr/bin/env python3
"""Stateless request/response bridge for UAL's in-editor Unreal MCP tools.

The C++ module Source/UEAssetLensMCP launches this script once per tool call via
FPlatformProcess::ExecProcess. Arguments and results travel through UTF-8 files
rather than stdout so that Chinese annotations in TOON/YAML survive intact on
Windows (ExecProcess returns stdout as an ANSI-ish FString).

Request JSON : {"tool": "ual_search_assets", "arguments": {...}}
Response     : raw TOON or YAML text (UTF-8, no BOM)

Usage:
  python engine_bridge.py --request req.json --response resp.txt [--index .ual/index.duckdb]
  python engine_bridge.py --self-test
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from ual_common import db  # noqa: E402
from ual_mcp import tools  # noqa: E402

TOOL_NAMES = tuple(tools.TOOL_NAMES)


def dispatch(con: Any, name: str, arguments: dict[str, Any]) -> str:
    """Map an MCP tool name onto the shared, already-tested Python implementation."""
    if name not in TOOL_NAMES:
        raise KeyError(f"unknown UAL tool: {name!r}; expected one of {TOOL_NAMES}")
    args = arguments or {}
    if name == "ual_search_assets":
        return tools.ual_search_assets(
            con,
            query=args.get("query"),
            asset_class=args.get("asset_class"),
            filters=args.get("filters"),
            domain_scope=args.get("domain_scope"),
            limit=args.get("limit", 8),
        )
    if name == "ual_inspect_asset":
        return tools.ual_inspect_asset(con, args.get("key") or args.get("asset_path") or "")
    if name == "ual_get_cooccurring_bundle":
        return tools.ual_get_cooccurring_bundle(con, args["asset_path"], args.get("top_k", 5))
    if name == "ual_find_similar_assets":
        return tools.ual_find_similar_assets(con, args["asset_path"], args.get("threshold", 0.85))
    raise AssertionError("unreachable")


def handle(request_path: Path, response_path: Path, index: str | None) -> int:
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
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


def self_test() -> int:
    index = Path(tempfile.gettempdir()) / "ual_bridge_self_test.duckdb"
    index.unlink(missing_ok=True)
    con = db.open_write(index)
    db.ingest_records(con, db.load_jsonl(SERVER_ROOT / "tests" / "seed.jsonl"))
    con.close()

    hero = "/Game/Characters/Knight/Anim/AM_Knight_HeavySlash_02"
    cases = [
        ("ual_search_assets", {"query": "heavy slash", "asset_class": "AnimMontage", "limit": 3}),
        ("ual_inspect_asset", {"key": "AnimMontage:AM_Knight_HeavySlash_02"}),
        ("ual_get_cooccurring_bundle", {"asset_path": hero, "top_k": 3}),
        ("ual_find_similar_assets", {"asset_path": hero, "threshold": 0.95}),
    ]
    ok = True
    ro = db.open_readonly(index)
    try:
        for name, arguments in cases:
            try:
                text = dispatch(ro, name, arguments)
                passed = bool(text.strip()) and text.startswith("# UEAssetLens")
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
    parser.add_argument("--request", help="Path to the request JSON file")
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
