"""UAL MCP tools. Pure string-returning functions, no MCP SDK dependency.

Static tools are the original read-only TOON/YAML retrieval surface. Runtime
tools come only from the shipped extension registry and are imported lazily;
every result is bounded TOON or single-asset YAML. Never return a dict to the
model. The DuckDB connection stays read-only; canonical artifacts are written
outside the index under a deterministic, non-LLM-visible runtime root.
"""
from __future__ import annotations

import importlib
import inspect
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

import duckdb
import yaml

from ual_common import db, extensions
from ual_common.toon import HEADER as TOON_HEADER, Table, render_toon
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

# TOON field-order contracts for runtime sections. Tools/gen_docs.py publishes
# every top-level *_FIELDS literal; keep these aligned with the bounded tables
# rendered by ual_common's runtime modules.
EXTENSIONS_FIELDS = ["key", "namespace", "version", "priority", "applies_to", "capabilities"]
INDEX_REVISION_FIELDS = ["key", "value"]
EXTENSION_FIELD_SEARCH_FIELDS = ["asset_path", "namespace", "field_name", "value_type", "value_display", "provenance"]
EXTENSION_CATALOG_FIELDS = ["namespace", "field_name", "value_type", "provenance", "asset_count"]
ASSET_ROLE_FIELDS = ["asset_path", "role", "provenance"]
ASSEMBLY_FIELDS = ["asset_path", "relation", "target_role"]
OFFICIAL_TOOL_FIELDS = ["name", "toolset", "accessMode", "inverse", "risk"]
RESCAN_PATH_FIELDS = ["asset_path"]
DIFF_LAYER_STATS_FIELDS = ["layer", "status", "changed_count", "changed_fields", "canonical_sha256", "line_count", "artifact", "next_cursor", "description", "source"]
DIFF_LAYER_FIELDS = ["line_no", "text"]
DIFF_MISMATCH_FIELDS = ["artifact", "reason", "first_sha256", "second_sha256"]
PLAN_OPERATION_FIELDS = ["id", "tool", "accessMode", "inverse", "risk", "arguments_sha256"]
PLAN_DIFF_FIELDS = ["id", "tool", "accessMode", "risk", "arguments_sha256", "change"]
PLAN_RESULT_FIELDS = ["id", "tool", "accessMode", "status", "dry_run", "result_status", "reason"]
VERIFY_RESULT_FIELDS = ["id", "tool", "status", "index_state", "detail"]
RISK_FIELDS = ["risk"]
EXPANSION_SECTION_FIELDS = ["section", "count"]
ERROR_FIELDS = ["code", "detail"]
VALIDATION_STAGE_FIELDS = ["stage", "status", "result", "artifact"]
ASSERTION_FIELDS = ["id", "status", "result"]
LIFECYCLE_EVENT_FIELDS = ["event_id", "event", "status", "hook_count", "payload_keys"]
WORKFLOW_STEP_FIELDS = ["step", "name", "status", "result", "artifact"]
ALLOWLIST_ISSUE_FIELDS = ["rule_id", "kind", "message"]
RUNTIME_RESULT_FIELDS = ["key", "value"]
ARTIFACT_PAGE_FIELDS = ["line_no", "text"]
IMPORT_GAMESPEC_FIELDS = ["key", "value"]

# Builtin-only registry: project/user YAML layers are deliberately excluded,
# and manifest impl references resolve solely to ual_common.extensions_builtins.
def _runtime_defs_with_contracts() -> list[dict[str, Any]]:
    loaded = extensions.load_builtin_registry()
    schemas: dict[str, dict[str, Any]] = {}
    for spec, _impl in loaded.registry.resolve("*", namespace="tool"):
        schema = dict(spec.schema)
        handler = str(schema.get("handler") or spec.name)
        schemas[handler] = schema
    defs = extensions.tool_defs_from_registry(loaded.registry)
    for tool in defs:
        schema = schemas.get(str(tool["name"]), {})
        mode = str(tool["accessMode"])
        read_only = bool(tool["readOnlyHint"])
        tool["annotations"] = dict(
            schema.get("annotations")
            or {"accessMode": mode, "readOnlyHint": read_only}
        )
        tool["meta"] = dict(schema.get("meta") or {"accessMode": mode})
    return defs


RUNTIME_TOOL_DEFS = _runtime_defs_with_contracts()
ALL_TOOL_DEFS = TOOL_DEFS + RUNTIME_TOOL_DEFS
STATIC_TOOL_NAMES = tuple(d["mcp_name"] for d in TOOL_DEFS)
RUNTIME_TOOL_NAMES = tuple(d["mcp_name"] for d in RUNTIME_TOOL_DEFS)
TOOL_NAMES = tuple(d["mcp_name"] for d in ALL_TOOL_DEFS)
if len(set(TOOL_NAMES)) != len(TOOL_NAMES):
    raise ValueError("duplicate MCP tool name in static + runtime catalogs")
extensions.validate_tool_names(
    [{**d, "accessMode": "Read", "readOnlyHint": True} for d in TOOL_DEFS]
    + RUNTIME_TOOL_DEFS
)

RUNTIME_INPUT_PROPERTIES: dict[str, set[str]] = {
    d["mcp_name"]: set(d.get("inputSchema", {}).get("properties", {}))
    for d in RUNTIME_TOOL_DEFS
}
RUNTIME_ARTIFACT_ROOT = Path(tempfile.gettempdir()) / "ual_mcp_runtime"
RUNTIME_ARTIFACT_SUBDIR = ".ual"
MAX_RUNTIME_ROWS = 20
MAX_RUNTIME_VALUE = 240
_POSIX_LOCAL_PATH_RE = re.compile(r"(?<![\w/])/(?:home|Users|tmp|var|mnt|private)/[^\s\"']+")
_RANDOM_ID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    r"|\b[0-9a-fA-F]{32}\b"
)
_PAYLOAD_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")
_FORBIDDEN_PAYLOAD_KEYS = frozenset({
    "absolute_path", "callable", "cloud", "code", "content_hash", "created_at",
    "endpoint", "eval", "exec", "file_path", "gpu", "guid", "handler", "host_path",
    "impl", "import", "lambda", "local_path", "module", "network", "pickle",
    "random_id", "request_id", "script", "session_id", "timestamp", "updated_at",
    "url", "uuid", "vlm",
})
_HOST_APPROVAL_IDS: dict[str, str] = {
    "UEAssetLens.AssetKernelToolset.RequestRescan": "RequestRescan",
    "UEAssetLens.GameSpecToolset.RunValidationLoop": "RunValidationLoop",
    "UEAssetLens.GameSpecToolset.RunFps3CWorkflow": "RunFps3CWorkflow",
    "UEAssetLens.GameSpecToolset.ImportGameSpec": "ImportGameSpec",
}


def _split_approvals(value: Any) -> set[str]:
    if isinstance(value, str):
        return {part.strip() for part in re.split(r"[,\s;]+", value) if part.strip()}
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()


def _require_host_execution(
    name: str,
    *,
    dry_run: bool,
    approved_by: str,
    approvals: Any,
    executor: Any,
) -> None:
    """Fail closed before any nested Execute/Write runtime module is called."""
    if bool(dry_run):
        return
    op_id = _HOST_APPROVAL_IDS.get(name)
    if op_id is None:
        return
    supplied = _split_approvals(approvals)
    if (
        not str(approved_by or "").strip()
        or supplied != {op_id}
        or not callable(executor)
    ):
        raise ValueError(
            f"{name} requires approved_by, explicit approval id {op_id} "
            "and an injected host executor"
        )


def _bounded_import_value(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "depth_limit"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _runtime_scrub(value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:32]:
            text = str(key)
            if (
                not _PAYLOAD_KEY_RE.match(text)
                or text.casefold() in _FORBIDDEN_PAYLOAD_KEYS
            ):
                raise ValueError("import_game_spec payload key is forbidden")
            result[text] = _bounded_import_value(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_bounded_import_value(item, depth + 1) for item in list(value)[:32]]
    return _runtime_scrub(value)


def _safe_import_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("import_game_spec payload must be a mapping")
    result = _bounded_import_value(dict(payload))
    if not isinstance(result, dict):
        raise ValueError("import_game_spec payload must be a mapping")
    return result


def _invoke_import_executor(
    executor: Any, *, approved_by: str, approvals: Any, payload: Mapping[str, Any]
) -> None:
    context = {
        "status": "host_validation_requested",
        "approved_by": _runtime_scrub(approved_by),
        "approvals": sorted(_split_approvals(approvals)),
        "dry_run": False,
        "payload_keys": sorted(str(key) for key in payload)[:16],
    }
    name = "UEAssetLens.GameSpecToolset.ImportGameSpec"
    try:
        try:
            executor(name, context)
        except TypeError:
            executor({"tool": name, "arguments": context})
    except Exception as exc:
        raise ValueError(f"host executor failed: {type(exc).__name__}") from exc


def _artifact_page_payload(result: Any) -> dict[str, Any]:
    if isinstance(result, Mapping):
        return dict(result)
    if isinstance(result, str):
        try:
            payload = yaml.safe_load(result)
        except yaml.YAMLError:
            payload = {"lines": [result]}
        return dict(payload) if isinstance(payload, Mapping) else {"lines": [result]}
    return {"lines": [result]}


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


def _runtime_module(name: str) -> Any:
    """Import one fixed, allowlisted runtime module on demand."""
    return importlib.import_module(f"ual_common.{name}")


def _runtime_scrub(value: Any) -> str:
    """Bound and canonicalize one model-visible scalar without hand-built TOON."""
    from ual_common import artifacts

    text = artifacts.canonicalize_text("" if value is None else str(value))
    text = _POSIX_LOCAL_PATH_RE.sub("<local-path>", text)
    text = _RANDOM_ID_RE.sub("<random-id>", text)
    return text.replace("\r", " ").replace("\n", " | ")[:MAX_RUNTIME_VALUE]


def _runtime_records(payload: Any) -> list[Any]:
    """Pick bounded records from a typed runtime result; never a raw index dump."""
    if isinstance(payload, dict):
        for key in ("rows", "events", "tools", "items", "results", "stages", "sections", "steps", "lines"):
            value = payload.get(key)
            if isinstance(value, (list, tuple)):
                return list(value)
        return [payload]
    if isinstance(payload, (list, tuple)):
        return list(payload)
    return [payload]


def _runtime_key_value_rows(payload: Any, limit: int) -> list[list[str]]:
    if isinstance(payload, dict):
        items = sorted(payload.items(), key=lambda pair: str(pair[0]))
    elif isinstance(payload, (list, tuple)):
        items = [(f"row_{index + 1}", value) for index, value in enumerate(payload)]
    else:
        items = [("value", payload)]
    return [
        [_runtime_scrub(key), _runtime_scrub(value)]
        for key, value in items[: max(1, min(int(limit), MAX_RUNTIME_ROWS))]
    ]


def _runtime_record_rows(payload: Any, fields: list[str], limit: int) -> list[list[str]]:
    bounded = max(1, min(int(limit), MAX_RUNTIME_ROWS))
    records = _runtime_records(payload)[:bounded]
    rows: list[list[str]] = []
    for record in records:
        if isinstance(record, dict):
            rows.append([_runtime_scrub(record.get(field, "")) for field in fields])
        else:
            row = [_runtime_scrub(record)] + [""] * (len(fields) - 1)
            rows.append(row[:len(fields)])
    return rows


def _render_runtime_payload(
    payload: Any,
    *,
    section: str,
    fields: list[str],
    summary: dict[str, Any] | None = None,
    limit: int = MAX_RUNTIME_ROWS,
) -> str:
    if isinstance(payload, str) and payload.startswith(TOON_HEADER):
        return payload
    bounded = max(1, min(int(limit), MAX_RUNTIME_ROWS))
    records = _runtime_records(payload)
    if fields in (INDEX_REVISION_FIELDS, RUNTIME_RESULT_FIELDS):
        rows = _runtime_key_value_rows(payload, bounded)
    else:
        rows = _runtime_record_rows(payload, fields, bounded)
    safe_summary = {
        str(key): _runtime_scrub(value)
        for key, value in (summary or {}).items()
    }
    safe_summary.update({
        "section": section,
        "returned": len(rows),
        "truncated": len(records) > len(rows),
    })
    return render_toon([Table(section, fields, rows)], summary=safe_summary)


def _runtime_output(
    result: Any,
    *,
    section: str,
    fields: list[str],
    summary: dict[str, Any] | None = None,
) -> str:
    if isinstance(result, str) and result.startswith(TOON_HEADER):
        return result
    return _render_runtime_payload(result, section=section, fields=fields, summary=summary)


def _invoke_runtime_callable(
    fn: Callable[..., Any],
    con: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Call a known runtime callable, injecting only the deterministic artifact root."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(con, *args, **kwargs)
    params = list(sig.parameters.values())
    positional = [
        param for param in params
        if param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    wants_con = bool(positional) and positional[0].name in {"con", "connection", "conn", "db"}
    call_kwargs = {key: value for key, value in kwargs.items() if key in sig.parameters}
    if "root" in sig.parameters:
        call_kwargs["root"] = Path(RUNTIME_ARTIFACT_ROOT) / RUNTIME_ARTIFACT_SUBDIR
    if wants_con:
        try:
            sig.bind(con, *args, **call_kwargs)
        except TypeError:
            return fn(*args, **call_kwargs)
        return fn(con, *args, **call_kwargs)
    try:
        sig.bind(*args, **call_kwargs)
    except TypeError:
        return fn(con, *args, **call_kwargs)
    return fn(*args, **call_kwargs)


def _call_runtime(
    module_name: str,
    function_names: tuple[str, ...],
    con: Any,
    *args: Any,
    **kwargs: Any,
) -> Any:
    module = _runtime_module(module_name)
    for name in function_names:
        fn = getattr(module, name, None)
        if callable(fn):
            return _invoke_runtime_callable(fn, con, *args, **kwargs)
    raise RuntimeError(
        f"runtime module {module_name!r} has none of {function_names!r}"
    )


def ual_list_extensions(con: Any, limit: int = 100) -> str:
    """Bounded snapshot of the shipped nine-namespace extension registry."""
    del con
    loaded = extensions.load_builtin_registry()
    return extensions.render_registry_snapshot(loaded.registry, limit=limit)


def ual_get_index_revision(con: Any) -> str:
    """Deterministic read-only index change-detection revision."""
    result = _call_runtime(
        "revision", ("render_revision", "get_index_revision", "index_revision"), con
    )
    payload = result if isinstance(result, dict) else {"index_revision": result}
    return _runtime_output(
        payload, section="index_revision", fields=INDEX_REVISION_FIELDS,
        summary={"source": "revision"},
    )


def ual_search_extension_fields(
    con: Any,
    namespace: str = "",
    field_name: str = "",
    value_contains: str = "",
    limit: int = 20,
) -> str:
    fields = _runtime_module("fields")
    direct = getattr(fields, "search_extension_fields", None)
    if callable(direct):
        result = _invoke_runtime_callable(
            direct, con, namespace=namespace or None, field_name=field_name or None,
            value_contains=value_contains or None, limit=limit,
        )
    else:
        rows = _invoke_runtime_callable(
            fields.search, con, namespace=namespace or None,
            field_name=field_name or None, value_contains=value_contains or None,
            limit=limit,
        )
        result = _invoke_runtime_callable(fields.render_search, con, rows, limit=limit)
    return _runtime_output(
        result, section="extension_fields", fields=EXTENSION_FIELD_SEARCH_FIELDS
    )


def ual_list_extension_fields(con: Any, limit: int = 50) -> str:
    fields = _runtime_module("fields")
    direct = getattr(fields, "list_extension_fields", None)
    if callable(direct):
        result = _invoke_runtime_callable(direct, con, limit=limit)
    else:
        rows = _invoke_runtime_callable(fields.catalog, con, limit=limit)
        result = _invoke_runtime_callable(fields.render_catalog, con, rows, limit=limit)
    return _runtime_output(
        result, section="extension_catalog", fields=EXTENSION_CATALOG_FIELDS
    )


def ual_get_assembly_graph(con: Any, asset_path: str, limit: int = 20) -> str:
    result = _call_runtime(
        "fields", ("get_assembly_graph", "assembly_graph"), con, asset_path, limit=limit
    )
    return _runtime_output(result, section="asset_role", fields=ASSET_ROLE_FIELDS)


def ual_list_official_tools(con: Any, limit: int = 100) -> str:
    try:
        result = _call_runtime(
            "allowlist",
            ("list_official_tools", "render_official_tools", "render_whitelist"),
            con, limit=limit,
        )
    except (ModuleNotFoundError, RuntimeError):
        gateway = _runtime_module("gateway")
        result = gateway.load_gateway().render_whitelist(limit)
    return _runtime_output(result, section="official_tools", fields=OFFICIAL_TOOL_FIELDS)


def ual_request_rescan(
    con: Any,
    asset_paths: str = "",
    reason: str = "index consistency",
    dry_run: bool = True,
    approved_by: str = "",
    approvals: str = "",
    *,
    executor: Any = None,
) -> str:
    name = "UEAssetLens.AssetKernelToolset.RequestRescan"
    _require_host_execution(
        name,
        dry_run=dry_run,
        approved_by=approved_by,
        approvals=approvals,
        executor=executor,
    )
    result = _call_runtime(
        "transaction", ("request_rescan",), con,
        asset_paths=asset_paths, reason=reason, dry_run=dry_run,
        approved_by=approved_by, approvals=approvals, executor=executor,
    )
    return _runtime_output(result, section="asset_paths", fields=RESCAN_PATH_FIELDS)


def ual_diff_assets(
    con: Any,
    lhs: str,
    rhs: str,
    layers: str = "L0,L1,L2,L3,L4",
) -> str:
    result = _call_runtime(
        "asset_diff", ("diff_assets",), con, lhs, rhs, layers=layers
    )
    return _runtime_output(result, section="layer_stats", fields=DIFF_LAYER_STATS_FIELDS)


def ual_read_diff_layer(
    con: Any,
    diff_id: str,
    layer: str,
    offset: int = 0,
    limit: int = 80,
) -> str:
    """Page one canonical YAML diff artifact as bounded TOON (never raw YAML)."""
    result = _call_runtime(
        "asset_diff", ("read_diff_layer",), con, diff_id, layer,
        offset=offset, limit=limit,
    )
    if isinstance(result, str) and result.startswith(TOON_HEADER):
        return result
    import yaml

    if isinstance(result, str):
        try:
            payload = yaml.safe_load(result)
        except yaml.YAMLError:
            payload = {"lines": [result]}
    else:
        payload = result
    if not isinstance(payload, dict):
        payload = {}
    raw_lines = payload.get("lines") or []
    if isinstance(raw_lines, str):
        raw_lines = [raw_lines]
    elif not isinstance(raw_lines, (list, tuple)):
        raw_lines = []
    size = max(1, min(int(limit or 80), 200))
    start = max(0, int(offset or 0))
    lines = [str(item) for item in list(raw_lines)[:size]]
    rows = [
        [start + index + 1, _runtime_scrub(line)]
        for index, line in enumerate(lines)
    ]
    total = int(payload.get("total", start + len(rows)) or 0)
    summary = {
        "diff_id": _runtime_scrub(diff_id),
        "layer": _runtime_scrub(layer),
        "offset": start,
        "limit": size,
        "returned": len(rows),
        "total": total,
        "truncated": bool(payload.get("truncated", total > start + len(rows))),
        "next_cursor": _runtime_scrub(payload.get("next_cursor", "")),
        "source": "canonical_yaml",
    }
    return render_toon([Table("diff_layer", DIFF_LAYER_FIELDS, rows)], summary=summary)


def ual_read_artifact(
    con: Any,
    relative: str,
    offset: int = 0,
    limit: int = 80,
) -> str:
    """Page one canonical .ual artifact as bounded TOON; never return raw YAML."""
    del con
    from ual_common import artifacts

    rel = artifacts.safe_relative(str(relative or ""))
    page_relative = rel if rel.startswith(".ual/") else f".ual/{rel}"
    module = _runtime_module("artifacts")
    reader = getattr(module, "read_artifact_page", None)
    if not callable(reader):
        raise RuntimeError("runtime module 'artifacts' has no read_artifact_page")
    payload = _artifact_page_payload(
        reader(
            Path(RUNTIME_ARTIFACT_ROOT),
            page_relative,
            offset=max(0, int(offset or 0)),
            limit=max(1, min(int(limit or 80), 200)),
        )
    )
    raw_lines = payload.get("lines") or []
    if isinstance(raw_lines, str):
        raw_lines = [raw_lines]
    elif not isinstance(raw_lines, (list, tuple)):
        raw_lines = []
    start = int(payload.get("offset", 0) or 0)
    size = int(payload.get("limit", len(raw_lines)) or len(raw_lines) or 1)
    lines = [str(item) for item in list(raw_lines)[:size]]
    rows = [
        [start + index + 1, _runtime_scrub(line)]
        for index, line in enumerate(lines)
    ]
    total = int(payload.get("total_lines", start + len(rows)) or 0)
    summary = {
        "relative": _runtime_scrub(payload.get("relative", page_relative)),
        "offset": start,
        "limit": size,
        "returned": len(rows),
        "total": total,
        "truncated": start + len(rows) < total,
        "next_cursor": _runtime_scrub(payload.get("next_cursor", "")),
        "source": "canonical_yaml",
    }
    return render_toon(
        [Table("artifact_page", ARTIFACT_PAGE_FIELDS, rows)], summary=summary
    )


def ual_verify_diff_determinism(con: Any, diff_id: str) -> str:
    result = _call_runtime(
        "asset_diff", ("verify_diff_determinism",), con, diff_id
    )
    return _runtime_output(result, section="mismatches", fields=DIFF_MISMATCH_FIELDS)


def ual_plan_edit(
    con: Any,
    intent: str,
    candidates: str = "",
    scope: str = "/Game",
    max_ops: int = 16,
    operations: str = "",
) -> str:
    result = _call_runtime(
        "transaction", ("plan_edit",), con, intent,
        candidates=candidates, scope=scope, max_ops=max_ops,
        operations_yaml=operations,
    )
    return _runtime_output(result, section="operations", fields=PLAN_OPERATION_FIELDS)


def ual_apply_plan(
    con: Any,
    plan_id: str,
    approved_by: str = "",
    dry_run: bool = True,
    approvals: str = "",
    *,
    executor: Any = None,
) -> str:
    result = _call_runtime(
        "transaction", ("apply_plan",), con, plan_id,
        approved_by=approved_by, dry_run=dry_run, approvals=approvals,
        executor=executor,
    )
    return _runtime_output(result, section="operations", fields=PLAN_OPERATION_FIELDS)


def ual_verify_edit(con: Any, plan_id: str) -> str:
    result = _call_runtime("transaction", ("verify_edit",), con, plan_id)
    return _runtime_output(result, section="per_op_result", fields=VERIFY_RESULT_FIELDS)


def ual_rollback_edit(
    con: Any,
    plan_id: str,
    approved_by: str = "",
    dry_run: bool = True,
    approvals: str = "",
    *,
    executor: Any = None,
) -> str:
    result = _call_runtime(
        "transaction", ("rollback_edit",), con, plan_id,
        approved_by=approved_by, dry_run=dry_run, approvals=approvals,
        executor=executor,
    )
    return _runtime_output(result, section="reverted_ops", fields=PLAN_OPERATION_FIELDS)


def ual_expand_gamespec(con: Any, intent: str, template: str = "fps_3c") -> str:
    result = _call_runtime(
        "gamespec", ("expand_game_spec",), con, intent, template=template
    )
    return _runtime_output(result, section="sections", fields=EXPANSION_SECTION_FIELDS)


def ual_run_validation_loop(
    con: Any,
    game_spec_id: str,
    runner: str = "offline",
    dry_run: bool = True,
    approved_by: str = "",
    approvals: str = "",
    *,
    executor: Any = None,
) -> str:
    name = "UEAssetLens.GameSpecToolset.RunValidationLoop"
    _require_host_execution(
        name,
        dry_run=dry_run,
        approved_by=approved_by,
        approvals=approvals,
        executor=executor,
    )
    effective_runner = "offline" if dry_run else (runner if runner != "offline" else "host")
    result = _call_runtime(
        "gamespec", ("run_validation_loop",), con, game_spec_id,
        runner=effective_runner, dry_run=dry_run,
        host_runner=executor if not dry_run else None,
    )
    return _runtime_output(result, section="stages", fields=VALIDATION_STAGE_FIELDS)


def ual_get_lifecycle_events(con: Any, limit: int = 50) -> str:
    result = _call_runtime(
        "lifecycle", ("get_lifecycle_events", "lifecycle_events", "events"),
        con, limit=limit,
    )
    return _runtime_output(
        result, section="lifecycle_events", fields=LIFECYCLE_EVENT_FIELDS
    )


def ual_run_fps_3c_workflow(
    con: Any,
    intent: str,
    dry_run: bool = True,
    approved_by: str = "",
    approvals: str = "",
    *,
    executor: Any = None,
) -> str:
    name = "UEAssetLens.GameSpecToolset.RunFps3CWorkflow"
    _require_host_execution(
        name,
        dry_run=dry_run,
        approved_by=approved_by,
        approvals=approvals,
        executor=executor,
    )
    result = _call_runtime(
        "workflow", ("run_fps_3c_workflow", "run_fps_3c", "run_workflow"),
        con, intent, dry_run=dry_run, approved_by=approved_by,
        approvals=approvals, executor=executor,
    )
    return _runtime_output(result, section="steps", fields=WORKFLOW_STEP_FIELDS)


def ual_import_game_spec(
    con: Any,
    payload: Any = None,
    dry_run: bool = True,
    approved_by: str = "",
    approvals: str = "",
    *,
    executor: Any = None,
) -> str:
    """Validate one structured host GameSpec payload; default to offline dry-run."""
    name = "UEAssetLens.GameSpecToolset.ImportGameSpec"
    _require_host_execution(
        name,
        dry_run=dry_run,
        approved_by=approved_by,
        approvals=approvals,
        executor=executor,
    )
    safe_payload = _safe_import_payload(payload)
    if not dry_run:
        _invoke_import_executor(
            executor,
            approved_by=approved_by,
            approvals=approvals,
            payload=safe_payload,
        )
    result = _call_runtime(
        "gamespec", ("import_game_spec",), con, safe_payload
    )
    if isinstance(result, str) and result.startswith(TOON_HEADER):
        return result
    return _runtime_output(
        result,
        section="import_game_spec",
        fields=IMPORT_GAMESPEC_FIELDS,
        summary={
            "dry_run": bool(dry_run),
            "status": "dry_run" if dry_run else "host_validated",
        },
    )


_RUNTIME_IMPLEMENTATIONS: dict[str, Callable[..., str]] = {
    "ual_list_extensions": ual_list_extensions,
    "ual_get_index_revision": ual_get_index_revision,
    "ual_search_extension_fields": ual_search_extension_fields,
    "ual_list_extension_fields": ual_list_extension_fields,
    "ual_get_assembly_graph": ual_get_assembly_graph,
    "ual_list_official_tools": ual_list_official_tools,
    "ual_request_rescan": ual_request_rescan,
    "ual_diff_assets": ual_diff_assets,
    "ual_read_diff_layer": ual_read_diff_layer,
    "ual_verify_diff_determinism": ual_verify_diff_determinism,
    "ual_plan_edit": ual_plan_edit,
    "ual_apply_plan": ual_apply_plan,
    "ual_verify_edit": ual_verify_edit,
    "ual_rollback_edit": ual_rollback_edit,
    "ual_expand_gamespec": ual_expand_gamespec,
    "ual_run_validation_loop": ual_run_validation_loop,
    "ual_get_lifecycle_events": ual_get_lifecycle_events,
    "ual_run_fps_3c_workflow": ual_run_fps_3c_workflow,
    "ual_read_artifact": ual_read_artifact,
    "ual_import_game_spec": ual_import_game_spec,
}

RUNTIME_HANDLERS: dict[str, Callable[..., str]] = {}
for _runtime_def in RUNTIME_TOOL_DEFS:
    _handler = _RUNTIME_IMPLEMENTATIONS.get(str(_runtime_def["name"]))
    if _handler is None:
        raise ValueError(f"runtime tool has no Python handler: {_runtime_def['name']!r}")
    RUNTIME_HANDLERS[str(_runtime_def["mcp_name"])] = _handler


def dispatch_runtime_tool(
    con: Any,
    name: str,
    arguments: dict[str, Any] | None,
    *,
    executor: Any = None,
) -> str:
    """Dispatch one qualified runtime tool; unknown names are rejected."""
    handler = RUNTIME_HANDLERS.get(name)
    if handler is None:
        raise KeyError(
            f"unknown UAL runtime tool: {name!r}; expected one of {RUNTIME_TOOL_NAMES}"
        )
    allowed = RUNTIME_INPUT_PROPERTIES.get(name, set())
    args = {
        key: value
        for key, value in (arguments or {}).items()
        if key in allowed
    }
    if executor is not None:
        try:
            parameters = inspect.signature(handler).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "executor" in parameters:
            args["executor"] = executor
    return str(handler(con, **args))
