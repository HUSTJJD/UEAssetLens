#!/usr/bin/env python3
"""UEAssetLens MCP Server entry point.

Requires the official MCP Python SDK 2.x (`mcp>=2,<3`). In v2 the high-level
server class is `MCPServer` (v1's `FastMCP` was renamed). There is deliberately
no hand-written protocol fallback: a missing or incompatible SDK fails loudly
instead of silently serving a partial MCP implementation.

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

HERO = "/Game/Characters/Knight/Anim/AM_Knight_HeavySlash_02"


def _create_sdk_server():
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:
        raise RuntimeError(
            "The official MCP SDK 2.x is required (mcp>=2,<3). "
            "Install it with `python -m pip install -r requirements.txt`."
        ) from exc
    return MCPServer("ueassetlens")


def _register(sdk: Any, con: duckdb.DuckDBPyConnection) -> None:
    @sdk.tool(name="UEAssetLens.AssetSemanticToolset.SearchAssets")
    def ual_search_assets(
        query: Optional[str] = None,
        asset_class: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        domain_scope: Optional[str] = None,
        limit: int = 8,
    ) -> str:
        """Hybrid retrieval (FTS BM25 + hard filters); returns TOON candidates and bundle."""
        return tools.ual_search_assets(con, query, asset_class, filters, domain_scope, limit)

    @sdk.tool(name="UEAssetLens.AssetSemanticToolset.InspectAsset")
    def ual_inspect_asset(key: str) -> str:
        """Deep single-asset contract; returns annotated YAML."""
        return tools.ual_inspect_asset(con, key)

    @sdk.tool(name="UEAssetLens.AssetSemanticToolset.GetCooccurringBundle")
    def ual_get_cooccurring_bundle(asset_path: str, top_k: int = 5) -> str:
        """Co-occurrence graph bundle recommendation; returns TOON."""
        return tools.ual_get_cooccurring_bundle(con, asset_path, top_k)

    @sdk.tool(name="UEAssetLens.AssetSemanticToolset.FindSimilarAssets")
    def ual_find_similar_assets(asset_path: str, threshold: float = 0.85) -> str:
        """16-dim fingerprint duplicate detection; returns TOON."""
        return tools.ual_find_similar_assets(con, asset_path, threshold)

    @sdk.tool(name="UEAssetLens.AssetGraphToolset.GetDependencies")
    def ual_get_dependencies(
        asset_path: str, max_depth: int = 2,
        edge_type: str = "DEPENDS_ON", limit: int = 50,
    ) -> str:
        """Bounded downstream dependency walk; returns TOON."""
        return tools.ual_get_dependencies(con, asset_path, max_depth, edge_type, limit)

    @sdk.tool(name="UEAssetLens.AssetGraphToolset.GetReferencers")
    def ual_get_referencers(
        asset_path: str, max_depth: int = 2,
        edge_type: str = "DEPENDS_ON", limit: int = 50,
    ) -> str:
        """Reverse impact surface; returns TOON."""
        return tools.ual_get_referencers(con, asset_path, max_depth, edge_type, limit)

    @sdk.tool(name="UEAssetLens.AssetGraphToolset.GetIndexHealth")
    def ual_get_index_health(top_n: int = 5) -> str:
        """Index quality report; returns TOON."""
        return tools.ual_get_index_health(con, top_n)

    @sdk.tool(name="UEAssetLens.AssetGraphToolset.FindDuplicates")
    def ual_find_duplicates(threshold: float = 0.95, limit: int = 20) -> str:
        """Whole-index fingerprint duplicate pairs; returns TOON."""
        return tools.ual_find_duplicates(con, threshold, limit)

    @sdk.tool(name="UEAssetLens.AssetSemanticToolset.SearchByFeatures")
    def ual_search_by_features(
        feature_key: Optional[str] = None,
        value_contains: Optional[str] = None,
        asset_class: Optional[str] = None,
        family: Optional[str] = None,
        limit: int = 20,
    ) -> str:
        """Retrieve assets by normalized typed-extractor feature keys; returns TOON."""
        return tools.ual_search_by_features(
            con, feature_key, value_contains, asset_class, family, limit)

    @sdk.tool(name="UEAssetLens.AssetSemanticToolset.ListFeatureKeys")
    def ual_list_feature_keys(limit: int = 50) -> str:
        """Catalog of feature keys present in the index; returns TOON."""
        return tools.ual_list_feature_keys(con, limit)


def run_server(con: duckdb.DuckDBPyConnection) -> None:
    sdk = _create_sdk_server()
    _register(sdk, con)
    asyncio.run(sdk.run_stdio_async())


def self_test() -> int:
    tmp = Path(tempfile.gettempdir()) / "ual_self_test.duckdb"
    tmp.unlink(missing_ok=True)
    con = db.open_write(tmp)
    stats = db.ingest_records(con, db.load_yaml_docs(SERVER_ROOT / "tests" / "seed.yaml"))
    con.close()
    con = db.open_readonly(tmp)
    checks = [
        ("UEAssetLens.AssetSemanticToolset.SearchAssets", tools.ual_search_assets(
            con, query="heavy slash", asset_class="AnimMontage",
            filters={"min_forward_cm": 100.0, "duration_max": 1.5}, limit=8)),
        ("UEAssetLens.AssetSemanticToolset.InspectAsset", tools.ual_inspect_asset(con, "AnimMontage:AM_Knight_HeavySlash_02")),
        ("UEAssetLens.AssetSemanticToolset.GetCooccurringBundle", tools.ual_get_cooccurring_bundle(con, HERO, 5)),
        ("UEAssetLens.AssetSemanticToolset.FindSimilarAssets", tools.ual_find_similar_assets(con, HERO, 0.85)),
        ("UEAssetLens.AssetGraphToolset.GetDependencies", tools.ual_get_dependencies(con, HERO, 3)),
        ("UEAssetLens.AssetGraphToolset.GetReferencers", tools.ual_get_referencers(con, HERO, 3)),
        ("UEAssetLens.AssetGraphToolset.GetIndexHealth", tools.ual_get_index_health(con, 5)),
        ("UEAssetLens.AssetGraphToolset.FindDuplicates", tools.ual_find_duplicates(con, 0.95, 10)),
        ("UEAssetLens.AssetSemanticToolset.SearchByFeatures", tools.ual_search_by_features(con, "kinematics")),
        ("UEAssetLens.AssetSemanticToolset.ListFeatureKeys", tools.ual_list_feature_keys(con, 20)),
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
    except Exception as exc:
        print(f"[ual] MCP server failed to start: {exc}", file=sys.stderr)
        return 2
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
