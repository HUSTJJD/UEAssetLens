"""Dependency-free MCP stdio fallback (newline-delimited JSON-RPC).

Used when neither `mcp` 1.x (FastMCP) nor 2.x (MCPServer) is installed.
Implements initialize / ping / tools.list / tools.call only — UAL needs no others.
"""
from __future__ import annotations

import json
import sys
import traceback
from typing import Any

import duckdb

from . import tools

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


def _dispatch(con: duckdb.DuckDBPyConnection, name: str, args: dict[str, Any]) -> str:
    args = args or {}
    if name == "ual_search_assets":
        return tools.ual_search_assets(
            con, query=args.get("query"), asset_class=args.get("asset_class"),
            filters=args.get("filters"), domain_scope=args.get("domain_scope"),
            limit=args.get("limit", 8),
        )
    if name == "ual_inspect_asset":
        return tools.ual_inspect_asset(con, args.get("key") or args.get("asset_path") or "")
    if name == "ual_get_cooccurring_bundle":
        return tools.ual_get_cooccurring_bundle(con, args["asset_path"], args.get("top_k", 5))
    if name == "ual_find_similar_assets":
        return tools.ual_find_similar_assets(con, args["asset_path"], args.get("threshold", 0.85))
    raise KeyError(f"unknown tool: {name}")


def _send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def serve_stdio(con: duckdb.DuckDBPyConnection) -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        req_id = request.get("id")
        method = request.get("method")
        if req_id is None and method and method.startswith("notifications/"):
            continue
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "ueassetlens", "version": "2.0.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOL_DEFS}
            elif method == "tools/call":
                params = request.get("params") or {}
                text = _dispatch(con, params.get("name", ""), params.get("arguments") or {})
                result = {"content": [{"type": "text", "text": text}], "isError": False}
            else:
                raise KeyError(f"unknown method: {method}")
            if req_id is not None:
                _send({"jsonrpc": "2.0", "id": req_id, "result": result})
        except Exception as exc:  # keep the stdio loop alive on tool errors
            if req_id is not None:
                _send({
                    "jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"},
                })
            sys.stderr.write(traceback.format_exc())
            sys.stderr.flush()
