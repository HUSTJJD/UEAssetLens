"""DuckDB storage layer for UEAssetLens v3.0.

Read path is strictly read-only (MCP Server). The only writer is ual-cli build.
Field names are the contract in Server/schema/PROTOCOL.md. The 16-key Core
Record remains frozen; typed extensions live in the separate
``extension_payload`` table and are synced four places: PROTOCOL.md, init.sql,
UALTypes.h and this module.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

import duckdb
import yaml

from .taxonomy import build_taxonomy

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schema"
DEFAULT_INDEX = ROOT / ".ual" / "index.duckdb"

ASSET_COLUMNS: tuple[str, ...] = (
    "asset_path", "logical_id", "asset_class", "parent_class", "taxonomy",
    "tokens", "content_hash", "duration", "bounds_size", "skeleton_path",
    "dominant_limb", "fingerprint_vec", "interface_spec", "domain_spec", "yaml_cache",
)

# Phase 1: typed extension payload, deliberately NOT part of the frozen 16-key
# staging document stream. No updated_at / timestamps / random ids / local paths.
EXTENSION_PAYLOAD_COLUMNS: tuple[str, ...] = (
    "asset_path", "namespace", "field_name", "value_type", "value_text",
    "value_number", "value_bool", "provenance",
)


def resolve_index(path: str | Path | None = None) -> Path:
    return Path(path) if path else DEFAULT_INDEX


def open_readonly(path: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    target = resolve_index(path)
    if not target.exists():
        raise FileNotFoundError(
            f"UAL index not found: {target}. Run `ual-cli build --demo` first."
        )
    return duckdb.connect(str(target), read_only=True)


def open_write(path: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    target = resolve_index(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(target))
    ensure_schema(con)
    return con


def split_sql(script: str) -> list[str]:
    """Strip whole-line comments, then split on semicolons.

    init.sql reference queries live in comments, so this is safe.
    """
    code = "\n".join(
        line for line in script.splitlines()
        if line.strip() and not line.strip().startswith("--")
    )
    return [stmt.strip() for stmt in code.split(";") if stmt.strip()]


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Idempotently apply init.sql. FTS index creation is tolerated if already present."""
    script = (SCHEMA_DIR / "init.sql").read_text(encoding="utf-8")
    for stmt in split_sql(script):
        try:
            con.execute(stmt)
        except Exception as exc:  # pragma: no cover - depends on DuckDB build
            is_fts = "fts_index" in stmt.lower()
            already = "already exists" in str(exc).lower() or "already" in str(exc).lower()
            if is_fts and already:
                continue
            raise


def rebuild_fts(con: duckdb.DuckDBPyConnection) -> None:
    """DuckDB FTS indexes are snapshots; rebuild after every bulk ingest."""
    try:
        con.execute("PRAGMA drop_fts_index('assets')")
    except Exception:
        pass
    con.execute("PRAGMA create_fts_index('assets', 'asset_path', 'tokens', 'logical_id')")


# --------------------------------------------------------------------------- ingest

def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _as_str_map(value: Any) -> dict[str, str]:
    """Normalize a MAP(VARCHAR,VARCHAR) payload so every value is a string.

    YAML staging may carry native sequences/mappings; serialize those to compact
    YAML flow-text fragments (the convention documented in PROTOCOL.md §1).
    Scalars stay as text; nothing is serialized to a foreign format.
    """
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for k, v in value.items():
        if isinstance(v, str):
            result[str(k)] = v
        elif isinstance(v, (list, tuple, dict)):
            result[str(k)] = yaml.safe_dump(
                list(v) if isinstance(v, tuple) else v,
                sort_keys=False, allow_unicode=True, default_flow_style=True,
            ).strip()
        else:
            result[str(k)] = "" if v is None else str(v)
    return result


def normalize_record(raw: dict[str, Any]) -> dict[str, Any]:
    fp = raw.get("fingerprint_vec")
    if fp is None or len(fp) != 16:
        fp = None
    else:
        fp = [_as_float(v) for v in fp]
    bounds = raw.get("bounds_size")
    if bounds is not None and len(bounds) == 3:
        bounds = [_as_float(v) for v in bounds]
    else:
        bounds = None
    edges = []
    for edge in raw.get("edges") or []:
        if not isinstance(edge, dict) or not edge.get("target_path"):
            continue
        edges.append({
            "target_path": str(edge["target_path"]),
            "edge_type": str(edge.get("edge_type", "DEPENDS_ON")).upper(),
            "weight": _as_float(edge.get("weight"), 1.0),
        })
    return {
        "asset_path": str(raw.get("asset_path", "")),
        "logical_id": str(raw.get("logical_id", "")),
        "asset_class": str(raw.get("asset_class", "")),
        "parent_class": str(raw.get("parent_class", "UObject")),
        "taxonomy": [str(t) for t in (raw.get("taxonomy") or [])],
        # UE C++ sink may emit tokens as an array of terms; canonical storage
        # is a single space-joined VARCHAR for the FTS index.
        "tokens": (
            " ".join(str(t) for t in raw["tokens"])
            if isinstance(raw.get("tokens"), (list, tuple))
            else str(raw.get("tokens") or "")
        ),
        "content_hash": str(raw.get("content_hash") or ""),
        "duration": _as_float(raw.get("duration")),
        "bounds_size": bounds,
        "skeleton_path": raw.get("skeleton_path") or None,
        "dominant_limb": raw.get("dominant_limb") or None,
        "fingerprint_vec": fp,
        "interface_spec": _as_str_map(raw.get("interface_spec")),
        "domain_spec": _as_str_map(raw.get("domain_spec")),
        "yaml_cache": raw.get("yaml_cache") or None,
        "edges": edges,
    }


def load_yaml_docs(path: str | Path) -> list[dict[str, Any]]:
    """Load a YAML document stream (.ual/staging/*.yaml, seed.yaml).

    A single malformed/indented document aborts the whole stream: YAML is
    indentation-sensitive, so callers must treat a batch as all-or-nothing.
    """
    with open(path, "r", encoding="utf-8") as handle:
        try:
            docs = [d for d in yaml.safe_load_all(handle) if d is not None]
        except yaml.YAMLError as exc:
            raise ValueError(f"{path}: invalid YAML document stream: {exc}") from exc
    records = []
    for i, doc in enumerate(docs, 1):
        if not isinstance(doc, dict):
            raise ValueError(f"{path}: document {i}: expected a YAML mapping, got {type(doc).__name__}")
        records.append(doc)
    return records


def _disambiguate_logical_id(
    con: duckdb.DuckDBPyConnection, asset_path: str, logical_id: str
) -> str:
    """Keep a deterministic, display-friendly logical_id without violating UNIQUE.

    The UE sink builds ``<AssetClass>:<AssetName>`` from FAssetData alone, so two
    assets in different folders -- or two UClasses with the same short name in
    different modules -- can collide. Rather than reject the second asset (which
    would silently drop whole asset types), append a stable 8-char path hash.
    Repeated ingests of the same asset always produce the same suffix.
    """
    if not logical_id:
        return logical_id
    row = con.execute(
        "SELECT asset_path FROM assets WHERE logical_id = ?", [logical_id]
    ).fetchone()
    if row is None or row[0] == asset_path:
        return logical_id
    suffix = hashlib.sha1(asset_path.encode("utf-8")).hexdigest()[:8]
    return f"{logical_id}:{suffix}"


def ingest_records(
    con: duckdb.DuckDBPyConnection,
    records: Iterable[dict[str, Any]],
    rebuild: bool = True,
) -> dict[str, int]:
    cols = ", ".join(ASSET_COLUMNS)
    placeholders = ", ".join([
        "CAST(? AS VARCHAR[])" if c == "taxonomy" else
        "CAST(? AS FLOAT[3])" if c == "bounds_size" else
        "CAST(? AS FLOAT[16])" if c == "fingerprint_vec" else
        "CAST(? AS MAP(VARCHAR, VARCHAR))" if c in ("interface_spec", "domain_spec") else "?"
        for c in ASSET_COLUMNS
    ])
    updates = ", ".join(f"{c}=excluded.{c}" for c in ASSET_COLUMNS if c != "asset_path")
    upsert = f"INSERT INTO assets ({cols}) VALUES ({placeholders}) ON CONFLICT (asset_path) DO UPDATE SET {updates}"

    assets = edges = 0
    for raw in records:
        rec = normalize_record(raw)
        if not rec["asset_path"]:
            continue
        # Any UObject asset class must land with a usable three-level taxonomy,
        # even when an upstream sink omitted it. The rule table is shared with
        # the C++ extractor (Server/ual_common/taxonomy.yaml).
        if not rec["taxonomy"] and rec["asset_class"]:
            rec["taxonomy"] = build_taxonomy(rec["asset_class"])
        # logical_id is UNIQUE in init.sql; de-duplicate deterministically so a
        # name/class collision cannot abort the whole ingest.
        rec["logical_id"] = _disambiguate_logical_id(
            con, rec["asset_path"], rec["logical_id"]
        )
        # DuckDB binds Python lists natively to VARCHAR[] / FLOAT[N]; do NOT
        # serialize them to text (e.g. yaml.safe_dump()), CAST(? AS ...) does it.
        params = [rec[c] for c in ASSET_COLUMNS]
        con.execute(upsert, params)
        assets += 1
        con.execute("DELETE FROM asset_edges WHERE source_path = ?", [rec["asset_path"]])
        for edge in rec["edges"]:
            con.execute(
                "INSERT INTO asset_edges (source_path, target_path, edge_type, weight) "
                "VALUES (?, ?, ?, ?) ON CONFLICT DO UPDATE SET weight = excluded.weight",
                [rec["asset_path"], edge["target_path"], edge["edge_type"], edge["weight"]],
            )
            edges += 1
    if rebuild:
        rebuild_fts(con)
    return {"assets": assets, "edges": edges}


# --------------------------------------------------------------------------- typed extension payload (v3.0)

def ingest_extension_payload(
    con: duckdb.DuckDBPyConnection,
    rows: Iterable[dict[str, Any]],
    *,
    clear: bool = False,
) -> int:
    """Upsert typed extension fields; callers never concatenate SQL from input."""
    if clear:
        con.execute("DELETE FROM extension_payload")
    cols = ", ".join(EXTENSION_PAYLOAD_COLUMNS)
    placeholders = ", ".join("?" for _ in EXTENSION_PAYLOAD_COLUMNS)
    key = ("asset_path", "namespace", "field_name")
    updates = ", ".join(
        f"{c}=excluded.{c}" for c in EXTENSION_PAYLOAD_COLUMNS if c not in key
    )
    sql = (
        f"INSERT INTO extension_payload ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT (asset_path, namespace, field_name) DO UPDATE SET {updates}"
    )
    written = 0
    for row in rows:
        con.execute(sql, [row.get(c) for c in EXTENSION_PAYLOAD_COLUMNS])
        written += 1
    return written


def clear_extension_payload(
    con: duckdb.DuckDBPyConnection, asset_path: str | None = None
) -> int:
    if asset_path is None:
        return int(con.execute("DELETE FROM extension_payload").fetchone()[0])
    return int(
        con.execute(
            "DELETE FROM extension_payload WHERE asset_path = ?", [asset_path]
        ).fetchone()[0]
    )


def search_extension_payload(
    con: duckdb.DuckDBPyConnection,
    *,
    namespace: str | None = None,
    field_name: str | None = None,
    value_contains: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Bounded, deterministic typed-field view; no whole-table dump leaves CLI."""
    limit = max(1, min(int(limit or 100), 500))
    where: list[str] = []
    params: list[Any] = []
    if namespace:
        where.append("namespace = ?")
        params.append(str(namespace))
    if field_name:
        where.append("field_name = ?")
        params.append(str(field_name))
    if value_contains:
        where.append("LOWER(COALESCE(value_text, '')) LIKE ?")
        params.append("%" + str(value_contains).lower() + "%")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    sql = (
        "SELECT asset_path, namespace, field_name, value_type, value_text, "
        "value_number, value_bool, provenance FROM extension_payload" + clause +
        " ORDER BY asset_path, namespace, field_name LIMIT ?"
    )
    rows = _rows(con, sql, params + [limit])
    for row in rows:
        if row["value_text"] not in (None, ""):
            row["value_display"] = str(row["value_text"])
        elif row["value_number"] is not None:
            row["value_display"] = f"{float(row['value_number']):.4f}"
        elif row["value_bool"] is not None:
            row["value_display"] = "true" if row["value_bool"] else "false"
        else:
            row["value_display"] = ""
    return rows


def extension_payload_catalog(
    con: duckdb.DuckDBPyConnection, *, limit: int = 200
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 200), 500))
    return _rows(
        con,
        "SELECT namespace, field_name, value_type, provenance, "
        "COUNT(DISTINCT asset_path) AS asset_count FROM extension_payload "
        "GROUP BY namespace, field_name, value_type, provenance "
        "ORDER BY namespace, field_name, value_type, provenance LIMIT ?",
        [limit],
    )


def remove_assets(
    con: duckdb.DuckDBPyConnection, paths: Sequence[str]
) -> int:
    """Delete removed asset rows from all index tables; used by apply-events."""
    unique = sorted({str(path) for path in paths if str(path).startswith("/Game/")})
    if not unique:
        return 0
    placeholders = ", ".join("?" for _ in unique)
    con.execute(f"DELETE FROM extension_payload WHERE asset_path IN ({placeholders})", unique)
    con.execute(
        f"DELETE FROM asset_edges WHERE source_path IN ({placeholders}) "
        f"OR target_path IN ({placeholders})",
        unique + unique,
    )
    return int(
        con.execute(
            f"DELETE FROM assets WHERE asset_path IN ({placeholders})", unique
        ).fetchone()[0]
    )


# --------------------------------------------------------------------------- read helpers

def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    cur = con.execute(sql, list(params))
    columns = [d[0] for d in cur.description]
    result = []
    for row in cur.fetchall():
        item: dict[str, Any] = {}
        for key, value in zip(columns, row):
            if key in ("taxonomy", "bounds_size", "fingerprint_vec") and value is not None:
                value = list(value)
            item[key] = value
        result.append(item)
    return result


def get_asset(con: duckdb.DuckDBPyConnection, key: str) -> dict[str, Any] | None:
    rows = _rows(
        con,
        "SELECT * FROM v_search WHERE asset_path = ? OR logical_id = ? LIMIT 1",
        [key, key],
    )
    if not rows:
        return None
    row = rows[0]
    count = con.execute(
        "SELECT COUNT(*) FROM asset_edges WHERE target_path = ? AND edge_type = 'DEPENDS_ON'",
        [row["asset_path"]],
    ).fetchone()[0]
    row["referenced_by_count"] = int(count)
    return row


def referencer_paths(con: duckdb.DuckDBPyConnection, asset_path: str, limit: int = 5) -> list[str]:
    rows = con.execute(
        "SELECT source_path FROM asset_edges WHERE target_path = ? AND edge_type = 'DEPENDS_ON' "
        "ORDER BY weight DESC, source_path LIMIT ?",
        [asset_path, limit],
    ).fetchall()
    return [r[0] for r in rows]


def search_assets(
    con: duckdb.DuckDBPyConnection,
    *,
    query: str | None = None,
    asset_class: str | None = None,
    filters: dict[str, Any] | None = None,
    domain_scope: str | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    filters = filters or {}
    where: list[str] = []
    params: list[Any] = []
    if asset_class:
        where.append("asset_class = ?")
        params.append(asset_class)
    if domain_scope:
        scope = domain_scope.rstrip("/")
        where.append("(asset_path = ? OR asset_path LIKE ?)")
        params.extend([scope, scope + "/%"])
    if filters.get("skeleton"):
        where.append("skeleton_path LIKE ?")
        params.append("%" + str(filters["skeleton"]) + "%")
    if filters.get("duration_min") is not None:
        where.append("duration >= ?")
        params.append(_as_float(filters["duration_min"]))
    if filters.get("duration_max") is not None:
        where.append("duration <= ?")
        params.append(_as_float(filters["duration_max"]))
    if filters.get("min_forward_cm") is not None:
        where.append("forward_cm >= ?")
        params.append(_as_float(filters["min_forward_cm"]))
    if filters.get("max_forward_cm") is not None:
        where.append("forward_cm <= ?")
        params.append(_as_float(filters["max_forward_cm"]))
    if filters.get("dominant_limb"):
        where.append("dominant_limb = ?")
        params.append(str(filters["dominant_limb"]))
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    if query:
        base = (
            "SELECT * FROM (SELECT s.*, "
            "fts_main_assets.match_bm25(s.asset_path, ?) AS score FROM v_search s) t "
            "WHERE t.score IS NOT NULL" + clause.replace(" WHERE ", " AND ") +
            " ORDER BY t.score DESC, t.asset_path LIMIT ?"
        )
        try:
            return _rows(con, base, [query] + params + [limit])
        except Exception:
            # FTS extension/index unavailable: deterministic lexical fallback.
            fallback = (
                "SELECT *, 0.0 AS score FROM v_search WHERE tokens ILIKE ?" +
                clause.replace(" WHERE ", " AND ") +
                " ORDER BY asset_path LIMIT ?"
            )
            return _rows(con, fallback, ["%" + query + "%"] + params + [limit])
    sql = "SELECT *, 0.0 AS score FROM v_search" + clause + " ORDER BY asset_path LIMIT ?"
    return _rows(con, sql, params + [limit])


def find_similar(
    con: duckdb.DuckDBPyConnection,
    asset_path: str,
    threshold: float = 0.85,
    limit: int = 20,
) -> list[dict[str, Any]]:
    sql = """
        SELECT * FROM (
            SELECT a.logical_id, a.asset_path, a.asset_class,
                   array_cosine_similarity(a.fingerprint_vec, t.v) AS similarity,
                   (a.asset_class = t.ac) AS shared_class
            FROM assets a
            CROSS JOIN (
                SELECT fingerprint_vec AS v, asset_class AS ac
                FROM assets WHERE asset_path = ?
            ) t
            WHERE a.asset_path <> ?
              AND a.fingerprint_vec IS NOT NULL AND t.v IS NOT NULL
        ) q
        WHERE q.similarity >= ?
        ORDER BY q.similarity DESC, q.asset_path
        LIMIT ?
    """
    return _rows(con, sql, [asset_path, asset_path, threshold, limit])


def duplicate_pairs(
    con: duckdb.DuckDBPyConnection,
    threshold: float = 0.85,
    asset_paths: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Self-join used by `ual-cli lint-duplicates`."""
    sql = """
        SELECT * FROM (
            SELECT a.asset_path AS lhs, b.asset_path AS rhs,
                   a.logical_id AS lhs_id, b.logical_id AS rhs_id,
                   array_cosine_similarity(a.fingerprint_vec, b.fingerprint_vec) AS similarity
            FROM assets a JOIN assets b ON a.asset_path < b.asset_path
            WHERE a.fingerprint_vec IS NOT NULL AND b.fingerprint_vec IS NOT NULL
        ) q
        WHERE q.similarity >= ?
    """
    params: list[Any] = [threshold]
    if asset_paths:
        placeholders = ", ".join("?" for _ in asset_paths)
        sql += f" AND (q.lhs IN ({placeholders}) OR q.rhs IN ({placeholders}))"
        params += list(asset_paths) + list(asset_paths)
    sql += " ORDER BY q.similarity DESC, q.lhs"
    return _rows(con, sql, params)


def cooccurring(
    con: duckdb.DuckDBPyConnection,
    asset_path: str,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    return _rows(
        con,
        """
        SELECT e.target_path, e.weight,
               COALESCE(a.logical_id, e.target_path) AS logical_id,
               COALESCE(a.asset_class, 'Unknown') AS asset_class,
               COALESCE(a.domain_spec['hint'], '') AS hint
        FROM asset_edges e
        LEFT JOIN assets a ON a.asset_path = e.target_path
        WHERE e.source_path = ? AND e.edge_type = 'CO_OCCURS'
        ORDER BY e.weight DESC, e.target_path
        LIMIT ?
        """,
        [asset_path, top_k],
    )


def all_assets(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    return _rows(con, "SELECT asset_path, logical_id, asset_class FROM assets ORDER BY asset_path")


# --------------------------------------------------------------------------- graph helpers (read-only)

VALID_EDGE_TYPES = ("DEPENDS_ON", "CO_OCCURS", "ANY")


def graph_traverse(
    con: duckdb.DuckDBPyConnection,
    asset_path: str,
    *,
    direction: str = "out",
    edge_type: str = "DEPENDS_ON",
    max_depth: int = 2,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Deterministic bounded BFS over asset_edges.

    direction="out" walks DEPENDS_ON targets (what this asset needs),
    direction="in" walks reverse edges (impact surface: who would break).
    The visited path is carried in via_path and checked with list_contains so
    reference cycles terminate. Every user-supplied value is a bound parameter;
    direction/edge_type are validated against fixed whitelists.
    """
    direction = direction if direction in ("out", "in") else "out"
    edge_type = edge_type.upper() if edge_type.upper() in VALID_EDGE_TYPES else "DEPENDS_ON"
    max_depth = max(1, min(int(max_depth or 2), 6))
    limit = max(1, min(int(limit or 50), 200))
    any_type = edge_type == "ANY"

    if direction == "out":
        seed = """
            SELECT e.target_path AS node, 1 AS depth,
                   e.source_path || '>' || e.target_path AS via_path,
                   e.edge_type AS edge_type, e.weight AS weight
            FROM asset_edges e
            WHERE e.source_path = ? {type_clause}
        """
        step = """
            SELECT e.target_path AS node, w.depth + 1 AS depth,
                   w.via_path || '>' || e.target_path AS via_path,
                   e.edge_type AS edge_type, e.weight AS weight
            FROM w JOIN asset_edges e ON e.source_path = w.node
            WHERE w.depth < ? {type_clause}
              AND NOT list_contains(string_split(w.via_path, '>'), e.target_path)
        """
    else:
        seed = """
            SELECT e.source_path AS node, 1 AS depth,
                   e.target_path || '<' || e.source_path AS via_path,
                   e.edge_type AS edge_type, e.weight AS weight
            FROM asset_edges e
            WHERE e.target_path = ? {type_clause}
        """
        step = """
            SELECT e.source_path AS node, w.depth + 1 AS depth,
                   w.via_path || '<' || e.source_path AS via_path,
                   e.edge_type AS edge_type, e.weight AS weight
            FROM w JOIN asset_edges e ON e.target_path = w.node
            WHERE w.depth < ? {type_clause}
              AND NOT list_contains(string_split(w.via_path, '<'), e.source_path)
        """

    clause = "AND e.edge_type = ?" if not any_type else ""
    params: list[Any] = [asset_path] if any_type else [asset_path, edge_type]
    step_params: list[Any] = [max_depth] if any_type else [max_depth, edge_type]
    sql = f"""
        WITH RECURSIVE w(node, depth, via_path, edge_type, weight) AS (
            {seed.format(type_clause=clause)}
            UNION ALL
            {step.format(type_clause=clause)}
        )
        SELECT COALESCE(a.logical_id, w.node) AS logical_id,
               w.node AS asset_path,
               COALESCE(a.asset_class, 'Unknown') AS asset_class,
               w.depth AS depth, w.edge_type AS edge_type,
               w.via_path AS via_path, w.weight AS weight
        FROM w LEFT JOIN assets a ON a.asset_path = w.node
        ORDER BY w.depth, w.node
        LIMIT ?
    """
    return _rows(con, sql, params + step_params + [limit])


def index_health(con: duckdb.DuckDBPyConnection, top_n: int = 5) -> dict[str, list[dict[str, Any]]]:
    """Read-only index quality report; no schema writes, no external calls."""
    top_n = max(1, min(int(top_n or 5), 20))
    scalars: list[tuple[str, str, str]] = [
        ("assets_total", "SELECT COUNT(*) FROM assets", "indexed assets"),
        ("edges_total", "SELECT COUNT(*) FROM asset_edges", "all graph edges"),
        ("depends_on_edges", "SELECT COUNT(*) FROM asset_edges WHERE edge_type = 'DEPENDS_ON'", "hard/reference edges"),
        ("co_occurs_edges", "SELECT COUNT(*) FROM asset_edges WHERE edge_type = 'CO_OCCURS'", "co-occurrence edges"),
        ("distinct_classes", "SELECT COUNT(DISTINCT asset_class) FROM assets", "UClass coverage"),
        ("missing_fingerprint", "SELECT COUNT(*) FROM assets WHERE fingerprint_vec IS NULL", "cannot participate in dedup"),
        ("missing_taxonomy", "SELECT COUNT(*) FROM assets WHERE taxonomy IS NULL OR len(taxonomy) = 0", "unclassified"),
        ("broken_references",
         "SELECT COUNT(*) FROM asset_edges e LEFT JOIN assets a ON a.asset_path = e.target_path "
         "WHERE a.asset_path IS NULL", "edges whose target is not indexed"),
        ("external_references",
         "SELECT COUNT(*) FROM asset_edges WHERE NOT starts_with(target_path, '/Game')", "engine/plugin/script targets"),
        ("orphan_assets",
         "SELECT COUNT(*) FROM assets a WHERE NOT EXISTS "
         "(SELECT 1 FROM asset_edges e WHERE e.source_path = a.asset_path OR e.target_path = a.asset_path)",
         "no graph edges at all"),
        ("external_reference_targets",
         "SELECT COUNT(DISTINCT target_path) FROM asset_edges WHERE NOT starts_with(target_path, '/Game')",
         "unique out-of-project targets"),
    ]
    metrics = []
    for name, sql, detail in scalars:
        value = con.execute(sql).fetchone()[0]
        metrics.append({"metric": name, "value": int(value), "detail": detail})

    hotspots = _rows(
        con,
        """
        SELECT a.logical_id, a.asset_class, COUNT(*) AS referenced_by
        FROM asset_edges e JOIN assets a ON a.asset_path = e.target_path
        WHERE e.edge_type = 'DEPENDS_ON'
        GROUP BY a.logical_id, a.asset_class
        ORDER BY referenced_by DESC, a.logical_id
        LIMIT ?
        """,
        [top_n],
    )
    families = _rows(
        con,
        """
        SELECT taxonomy[2] AS family, COUNT(*) AS asset_count
        FROM assets WHERE taxonomy IS NOT NULL AND len(taxonomy) >= 2
        GROUP BY family ORDER BY asset_count DESC, family LIMIT ?
        """,
        [top_n],
    )
    return {"metrics": metrics, "hotspots": hotspots, "families": families}


# --------------------------------------------------------------------------- feature helpers (read-only)

def search_by_features(
    con: duckdb.DuckDBPyConnection,
    *,
    feature_key: str | None = None,
    value_contains: str | None = None,
    asset_class: str | None = None,
    family: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Query the normalized `feature.*` keys emitted by FUALTypedFeatureExtractor.

    MAP(VARCHAR,VARCHAR) keys are expanded with map_keys/UNNEST; the value is
    fetched through the MAP subscript so no whole-database dump reaches the model.
    """
    limit = max(1, min(int(limit or 20), 100))
    where: list[str] = []
    params: list[Any] = []
    if feature_key:
        where.append("k.key_name ILIKE ?")
        params.append("%" + str(feature_key) + "%")
    else:
        where.append("k.key_name LIKE 'feature.%'")
    if value_contains:
        where.append("CAST(m.spec[k.key_name] AS VARCHAR) ILIKE ?")
        params.append("%" + str(value_contains) + "%")
    if asset_class:
        where.append("a.asset_class = ?")
        params.append(str(asset_class))
    if family:
        where.append("a.taxonomy[2] = ?")
        params.append(str(family))
    sql = f"""
        SELECT a.logical_id, a.asset_path, a.asset_class,
               m.source AS spec_source, k.key_name AS feature_key,
               CAST(m.spec[k.key_name] AS VARCHAR) AS feature_value
        FROM assets a
        JOIN (
            SELECT asset_path, 'domain_spec' AS source, domain_spec AS spec FROM assets
            UNION ALL
            SELECT asset_path, 'interface_spec' AS source, interface_spec AS spec FROM assets
        ) m ON m.asset_path = a.asset_path,
        UNNEST(map_keys(m.spec)) AS k(key_name)
        WHERE {' AND '.join(where)}
        ORDER BY a.asset_path, k.key_name
        LIMIT ?
    """
    return _rows(con, sql, params + [limit])


def feature_key_catalog(con: duckdb.DuckDBPyConnection, limit: int = 50) -> list[dict[str, Any]]:
    """List available feature keys so an Agent can discover the extractor's vocabulary."""
    limit = max(1, min(int(limit or 50), 200))
    return _rows(
        con,
        """
        SELECT k.key_name AS feature_key, m.source AS spec_source,
               COUNT(DISTINCT a.asset_path) AS asset_count
        FROM assets a
        JOIN (
            SELECT asset_path, 'domain_spec' AS source, domain_spec AS spec FROM assets
            UNION ALL
            SELECT asset_path, 'interface_spec' AS source, interface_spec AS spec FROM assets
        ) m ON m.asset_path = a.asset_path,
        UNNEST(map_keys(m.spec)) AS k(key_name)
        WHERE k.key_name LIKE 'feature.%'
        GROUP BY k.key_name, m.source
        ORDER BY asset_count DESC, k.key_name
        LIMIT ?
        """,
        [limit],
    )
