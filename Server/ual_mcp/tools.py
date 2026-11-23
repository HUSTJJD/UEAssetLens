"""The four UAL MCP tools. Pure string-returning functions, no MCP SDK dependency.

Batch tools -> TOON. Single-asset tool -> YAML. Never return a dict to the model.
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

# The single source of truth for tool names, descriptions and input schemas.
# Tools/gen_docs.py parses this literal via AST (no import of this module) to
# emit tool_schemas.yaml for the C++ in-editor MCP frontend and the generated
# reference docs. Keep it in sync with the @sdk.tool() signatures in server.py.
TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "ual_search_assets",
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
        "description": "Deep single-asset contract with semantic annotations; returns YAML.",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "logical_id or asset_path"}},
            "required": ["key"],
        },
    },
    {
        "name": "ual_get_cooccurring_bundle",
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


TOOL_NAMES = (
    "ual_search_assets", "ual_inspect_asset",
    "ual_get_cooccurring_bundle", "ual_find_similar_assets",
)
