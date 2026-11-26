"""UAL MCP tools. Pure string-returning functions, no MCP SDK dependency.

Batch tools -> TOON. Single-asset tool -> YAML. Never return a dict to the model.
Two toolset families: AssetSemanticToolset (retrieval/features) and
AssetGraphToolset (dependency graph / index health). All tools are read-only.
"""
from __future__ import annotations

from typing import Any

import duckdb

from ual_common import db
from ual_common.toon import Table, render_toon
from ual_common.yamlspec import HEADER as YAML_HEADER, build_yaml

CANDIDATE_FIELDS = ["logical_id", "duration", "forward_cm", "strike_window", "limb", "score"]
BUNDLE_FIELDS = ["logical_id", "asset_class", "weight", "hint"]
SIMILAR_FIELDS = ["logical_id", "asset_class", "similarity", "shared_class"]
DEPENDENCY_FIELDS = ["logical_id", "asset_path", "depth", "edge_type", "weight", "via_path"]
HEALTH_FIELDS = ["metric", "value", "detail"]
HOTSPOT_FIELDS = ["logical_id", "asset_class", "referenced_by"]
FAMILY_FIELDS = ["family", "asset_count"]
DUPLICATE_FIELDS = ["lhs_id", "rhs_id", "similarity"]
FEATURE_FIELDS = ["logical_id", "asset_class", "feature_key", "feature_value"]
FEATURE_KEY_FIELDS = ["feature_key", "spec_source", "asset_count"]

# The single source of truth: `name` is the Python implementation name,
# `mcp_name` is the externally visible MCP tool name (official qualified form).
# Tools/gen_docs.py parses this literal via AST (no import of this module) to
# emit tool_schemas.yaml for the C++ in-editor MCP frontend and the generated
# reference docs. `mcp_name` must match @sdk.tool(name=...) in server.py.
TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "ual_search_assets",
        "mcp_name": "UEAssetLens.AssetSemanticToolset.SearchAssets",
        "description": "Multi-funnel hybrid asset retrieval; returns compact TOON candidates + co-occurring bundle.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language keywords (DuckDB FTS BM25)"},
                "asset_class": {"type": "string", "description": "e.g. AnimMontage, NiagaraSystem, StaticMesh"},
                "filters": {"type": "object", "description": "Hard constraints: skeleton, duration_min/max, min/max_forward_cm, dominant_limb"},
                "domain_scope": {"type": "string", "description": "Directory whitelist, e.g. /Game/Characters/Knight"},
                "limit": {"type": "integer", "default": 8},
            },
        },
    },
    {
        "name": "ual_inspect_asset",
        "mcp_name": "UEAssetLens.AssetSemanticToolset.InspectAsset",
        "description": "Deep single-asset contract with semantic annotations; returns YAML.",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "logical_id or asset_path"}},
            "required": ["key"],
        },
    },
    {
        "name": "ual_get_cooccurring_bundle",
        "mcp_name": "UEAssetLens.AssetSemanticToolset.GetCooccurringBundle",
        "description": "Co-occurrence graph bundle recommendation; returns TOON.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_path": {"type": "string"},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["asset_path"],
        },
    },
    {
        "name": "ual_find_similar_assets",
        "mcp_name": "UEAssetLens.AssetSemanticToolset.FindSimilarAssets",
        "description": "Deterministic 16-dim fingerprint duplicate detection; returns TOON.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_path": {"type": "string"},
                "threshold": {"type": "number", "default": 0.85},
            },
            "required": ["asset_path"],
        },
    },
    {
        "name": "ual_get_dependencies",
        "mcp_name": "UEAssetLens.AssetGraphToolset.GetDependencies",
        "description": "Bounded multi-hop dependency walk (what this asset references); returns TOON.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_path": {"type": "string", "description": "Source package path, e.g. /Game/Characters/Knight/AM_Knight_HeavySlash_02"},
                "max_depth": {"type": "integer", "default": 2, "description": "BFS depth, 1..6"},
                "edge_type": {"type": "string", "default": "DEPENDS_ON", "description": "DEPENDS_ON | CO_OCCURS | ANY"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["asset_path"],
        },
    },
    {
        "name": "ual_get_referencers",
        "mcp_name": "UEAssetLens.AssetGraphToolset.GetReferencers",
        "description": "Reverse impact surface: who references this asset, transitively; returns TOON.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "asset_path": {"type": "string"},
                "max_depth": {"type": "integer", "default": 2},
                "edge_type": {"type": "string", "default": "DEPENDS_ON", "description": "DEPENDS_ON | CO_OCCURS | ANY"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["asset_path"],
        },
    },
    {
        "name": "ual_get_index_health",
        "mcp_name": "UEAssetLens.AssetGraphToolset.GetIndexHealth",
        "description": "Index quality report: edge counts, broken/orphan/external references, hotspots and family distribution; returns TOON.",
        "inputSchema": {
            "type": "object",
            "properties": {"top_n": {"type": "integer", "default": 5}},
        },
    },
    {
        "name": "ual_find_duplicates",
        "mcp_name": "UEAssetLens.AssetGraphToolset.FindDuplicates",
        "description": "Whole-index 16-dim fingerprint duplicate pairs (use InspectAsset to confirm); returns TOON.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "threshold": {"type": "number", "default": 0.95},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "ual_search_by_features",
        "mcp_name": "UEAssetLens.AssetSemanticToolset.SearchByFeatures",
        "description": "Retrieve assets by normalized feature.* keys emitted by the typed extractor; returns TOON.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "feature_key": {"type": "string", "description": "Key substring, e.g. feature.mesh or kinematics; omit to list all feature.* keys"},
                "value_contains": {"type": "string", "description": "Optional case-insensitive value substring"},
                "asset_class": {"type": "string"},
                "family": {"type": "string", "description": "taxonomy family, e.g. Animation"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "ual_list_feature_keys",
        "mcp_name": "UEAssetLens.AssetSemanticToolset.ListFeatureKeys",
        "description": "Catalog of feature.* keys present in the index with asset counts; returns TOON.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 50}},
        },
    },
]


def _bundle_rows(rows: list[dict[str, Any]]) -> list[list[Any]]:
    return [[
        r["logical_id"], r["asset_class"], round(float(r["weight"]), 4), r.get("hint") or "",
    ] for r in rows]


def ual_search_assets(
    con: duckdb.DuckDBPyConnection,
    query: str | None = None,
    asset_class: str | None = None,
    filters: dict[str, Any] | None = None,
    domain_scope: str | None = None,
    limit: int = 8,
) -> str:
    """Multi-funnel hybrid retrieval -> TOON candidates + top co-occurring bundle."""
    limit = max(1, min(int(limit or 8), 50))
    rows = db.search_assets(
        con, query=query, asset_class=asset_class,
        filters=filters, domain_scope=domain_scope, limit=limit,
    )
    candidates = [[
        r["logical_id"],
        round(float(r["duration"] or 0.0), 4),
        round(float(r["forward_cm"] or 0.0), 4),
        r.get("strike_window") or "",
        r.get("dominant_limb") or "",
        round(float(r["score"] or 0.0), 4),
    ] for r in rows]

    merged: dict[str, dict[str, Any]] = {}
    for r in rows[: min(len(rows), limit)]:
        for edge in db.cooccurring(con, r["asset_path"], top_k=5):
            key = edge["target_path"]
            if key not in merged or edge["weight"] > merged[key]["weight"]:
                merged[key] = edge
    bundle = sorted(merged.values(), key=lambda e: -float(e["weight"]))[:5]

    return render_toon(
        [Table("candidates", CANDIDATE_FIELDS, candidates),
         Table("co_occurring_bundle", BUNDLE_FIELDS, _bundle_rows(bundle))],
        summary={
            "matched_total": len(rows),
            "scope": domain_scope or "/Game",
            "query": query or "",
            "asset_class": asset_class or "",
        },
    )


def ual_inspect_asset(con: duckdb.DuckDBPyConnection, key: str) -> str:
    """Single-asset deep contract -> annotated YAML (zero-latency yaml_cache read)."""
    record = db.get_asset(con, key)
    if record is None:
        return f"{YAML_HEADER}\nerror: asset_not_found\nquery: {key}\n"
    if record.get("yaml_cache"):
        return record["yaml_cache"]
    return build_yaml(record, db.referencer_paths(con, record["asset_path"]))


def ual_get_cooccurring_bundle(
    con: duckdb.DuckDBPyConnection,
    asset_path: str,
    top_k: int = 5,
) -> str:
    """Co-occurrence graph recommendation -> TOON."""
    top_k = max(1, min(int(top_k or 5), 20))
    rows = db.cooccurring(con, asset_path, top_k)
    return render_toon(
        [Table("co_occurring_bundle", BUNDLE_FIELDS, _bundle_rows(rows))],
        summary={"asset_path": asset_path, "matched_total": len(rows), "top_k": top_k},
    )


def ual_find_similar_assets(
    con: duckdb.DuckDBPyConnection,
    asset_path: str,
    threshold: float = 0.85,
) -> str:
    """Deterministic fingerprint duplicate detection -> TOON."""
    threshold = float(threshold)
    rows = db.find_similar(con, asset_path, threshold)
    table_rows = [[
        r["logical_id"], r["asset_class"],
        round(float(r["similarity"]), 4),
        "true" if r["shared_class"] else "false",
    ] for r in rows]
    return render_toon(
        [Table("similar_assets", SIMILAR_FIELDS, table_rows)],
        summary={"asset_path": asset_path, "threshold": threshold, "matched_total": len(rows)},
    )


def _dependency_rows(rows: list[dict[str, Any]]) -> list[list[Any]]:
    return [[
        r["logical_id"], r["asset_path"], int(r["depth"]), r["edge_type"],
        round(float(r["weight"]), 4), r["via_path"],
    ] for r in rows]


def ual_get_dependencies(
    con: duckdb.DuckDBPyConnection,
    asset_path: str,
    max_depth: int = 2,
    edge_type: str = "DEPENDS_ON",
    limit: int = 50,
) -> str:
    """Bounded downstream BFS over the reference graph -> TOON."""
    rows = db.graph_traverse(
        con, asset_path, direction="out", edge_type=edge_type,
        max_depth=max_depth, limit=limit,
    )
    return render_toon(
        [Table("dependencies", DEPENDENCY_FIELDS, _dependency_rows(rows))],
        summary={
            "asset_path": asset_path, "direction": "out",
            "max_depth": max_depth, "edge_type": edge_type,
            "matched_total": len(rows),
        },
    )


def ual_get_referencers(
    con: duckdb.DuckDBPyConnection,
    asset_path: str,
    max_depth: int = 2,
    edge_type: str = "DEPENDS_ON",
    limit: int = 50,
) -> str:
    """Reverse (impact surface) BFS over the reference graph -> TOON."""
    rows = db.graph_traverse(
        con, asset_path, direction="in", edge_type=edge_type,
        max_depth=max_depth, limit=limit,
    )
    return render_toon(
        [Table("referencers", DEPENDENCY_FIELDS, _dependency_rows(rows))],
        summary={
            "asset_path": asset_path, "direction": "in",
            "max_depth": max_depth, "edge_type": edge_type,
            "matched_total": len(rows),
        },
    )


def ual_get_index_health(con: duckdb.DuckDBPyConnection, top_n: int = 5) -> str:
    """Deterministic index quality report -> TOON (never a full database dump)."""
    report = db.index_health(con, top_n)
    metrics = [[m["metric"], m["value"], m["detail"]] for m in report["metrics"]]
    hotspots = [[
        r["logical_id"], r["asset_class"], int(r["referenced_by"]),
    ] for r in report["hotspots"]]
    families = [[r["family"], int(r["asset_count"])] for r in report["families"]]
    return render_toon(
        [Table("metrics", HEALTH_FIELDS, metrics),
         Table("hotspots", HOTSPOT_FIELDS, hotspots),
         Table("families", FAMILY_FIELDS, families)],
        summary={"top_n": top_n, "metrics_total": len(metrics)},
    )


def ual_find_duplicates(
    con: duckdb.DuckDBPyConnection,
    threshold: float = 0.95,
    limit: int = 20,
) -> str:
    """Whole-index fingerprint duplicate pairs -> TOON."""
    threshold = float(threshold)
    limit = max(1, min(int(limit or 20), 200))
    rows = db.duplicate_pairs(con, threshold)[:limit]
    table_rows = [[
        r["lhs_id"], r["rhs_id"], round(float(r["similarity"]), 4),
    ] for r in rows]
    return render_toon(
        [Table("duplicate_pairs", DUPLICATE_FIELDS, table_rows)],
        summary={
            "threshold": threshold, "matched_total": len(rows),
            "note": "fingerprint similarity is recall-oriented; confirm with InspectAsset",
        },
    )


def ual_search_by_features(
    con: duckdb.DuckDBPyConnection,
    feature_key: str | None = None,
    value_contains: str | None = None,
    asset_class: str | None = None,
    family: str | None = None,
    limit: int = 20,
) -> str:
    """Query normalized typed-extractor features without dumping whole assets."""
    rows = db.search_by_features(
        con, feature_key=feature_key, value_contains=value_contains,
        asset_class=asset_class, family=family, limit=limit,
    )
    table_rows = [[
        r["logical_id"], r["asset_class"], r["feature_key"], r["feature_value"],
    ] for r in rows]
    return render_toon(
        [Table("features", FEATURE_FIELDS, table_rows)],
        summary={
            "feature_key": feature_key or "feature.*",
            "value_contains": value_contains or "",
            "asset_class": asset_class or "", "family": family or "",
            "matched_total": len(rows),
        },
    )


def ual_list_feature_keys(con: duckdb.DuckDBPyConnection, limit: int = 50) -> str:
    """Discover the feature vocabulary actually present in this index -> TOON."""
    rows = db.feature_key_catalog(con, limit)
    table_rows = [[
        r["feature_key"], r["spec_source"], int(r["asset_count"]),
    ] for r in rows]
    return render_toon(
        [Table("feature_keys", FEATURE_KEY_FIELDS, table_rows)],
        summary={"matched_total": len(rows), "limit": limit},
    )


# Externally visible MCP tool names (official qualified form). Python
# implementation function names above stay snake_case by design; see
# docs/mcp-positioning.md section 2.1.
TOOL_NAMES = tuple(d["mcp_name"] for d in TOOL_DEFS)
