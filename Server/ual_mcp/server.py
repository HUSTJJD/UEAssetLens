#!/usr/bin/env python3
"""UEAssetLens MCP Server entry point.

Compatibility order:
  1. mcp 1.x  -> FastMCP
  2. mcp 2.x  -> MCPServer (FastMCP was renamed)
  3. none     -> dependency-free stdio JSON-RPC fallback (stdio_server.py)

Usage:
  python Server/ual_mcp/server.py --index .ual/index.duckdb
  python Server/ual_mcp/server.py --self-test
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import duckdb

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from ual_common import db  # noqa: E402
from ual_mcp import tools  # noqa: E402
from ual_mcp.stdio_server import serve_stdio  # noqa: E402

HERO = "/Game/Characters/Knight/Anim/AM_Knight_HeavySlash_02"


def _create_sdk_server():
    try:
        from mcp.server.fastmcp import FastMCP  # mcp 1.x
        return FastMCP("ueassetlens"), "mcp1"
    except ImportError:
        pass
    try:
        from mcp.server.mcpserver import MCPServer  # mcp 2.x
        return MCPServer("ueassetlens"), "mcp2"
    except ImportError:
        return None, None


def _register(sdk: Any, con: duckdb.DuckDBPyConnection) -> None:
    @sdk.tool()
    def ual_search_assets(
        query: Optional[str] = None,
        asset_class: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        domain_scope: Optional[str] = None,
        limit: int = 8,
    ) -> str:
        """Hybrid retrieval (FTS BM25 + hard filters); returns TOON candidates and bundle."""
        return tools.ual_search_assets(con, query, asset_class, filters, domain_scope, limit)

    @sdk.tool()
    def ual_inspect_asset(key: str) -> str:
        """Deep single-asset contract; returns annotated YAML."""
        return tools.ual_inspect_asset(con, key)

    @sdk.tool()
    def ual_get_cooccurring_bundle(asset_path: str, top_k: int = 5) -> str:
        """Co-occurrence graph bundle recommendation; returns TOON."""
        return tools.ual_get_cooccurring_bundle(con, asset_path, top_k)

    @sdk.tool()
    def ual_find_similar_assets(asset_path: str, threshold: float = 0.85) -> str:
        """16-dim fingerprint duplicate detection; returns TOON."""
        return tools.ual_find_similar_assets(con, asset_path, threshold)


def run_server(con: duckdb.DuckDBPyConnection) -> None:
    sdk, flavor = _create_sdk_server()
    if sdk is not None:
        try:
            _register(sdk, con)
        except Exception as exc:
            sys.stderr.write(f"[ual] SDK tool registration failed ({exc}); using stdio fallback\n")
        else:
            if flavor == "mcp1":
                sdk.run(transport="stdio")
            else:
                asyncio.run(sdk.run_stdio_async())
            return
    serve_stdio(con)


def self_test() -> int:
    tmp = Path(tempfile.gettempdir()) / "ual_self_test.duckdb"
    tmp.unlink(missing_ok=True)
    con = db.open_write(tmp)
    stats = db.ingest_records(con, db.load_jsonl(SERVER_ROOT / "tests" / "seed.jsonl"))
    con.close()
    con = db.open_readonly(tmp)
    checks = [
        ("ual_search_assets", tools.ual_search_assets(
            con, query="heavy slash", asset_class="AnimMontage",
            filters={"min_forward_cm": 100.0, "duration_max": 1.5}, limit=8)),
        ("ual_inspect_asset", tools.ual_inspect_asset(con, "AnimMontage:AM_Knight_HeavySlash_02")),
        ("ual_get_cooccurring_bundle", tools.ual_get_cooccurring_bundle(con, HERO, 5)),
        ("ual_find_similar_assets", tools.ual_find_similar_assets(con, HERO, 0.85)),
    ]
    print(f"[ual] seeded {stats['assets']} assets / {stats['edges']} edges")
    for name, output in checks:
        print(f"\n===== {name} =====\n{output.rstrip()}")
    con.close()
    ok = all(output.strip() for _, output in checks)
    print(f"\n[ual] self-test: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="UEAssetLens MCP Server")
    parser.add_argument("--index", default=None, help="Path to .ual/index.duckdb (read-only)")
    parser.add_argument("--self-test", action="store_true", help="Run offline end-to-end checks")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    try:
        con = db.open_readonly(args.index)
    except FileNotFoundError as exc:
        print(f"[ual] {exc}", file=sys.stderr)
        return 2
    try:
        run_server(con)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
