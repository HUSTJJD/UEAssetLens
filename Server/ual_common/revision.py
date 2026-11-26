"""Deterministic index revision and bounded single-asset diff inputs.

The revision is a change-detection value only. It never reads a clock column and
never returns raw rows; duplicate detection remains fingerprint-based elsewhere.
"""
from __future__ import annotations

import math
import re
from typing import Any, Sequence

from . import artifacts

SNAPSHOT_COLUMNS = (
    "asset_path", "logical_id", "asset_class", "parent_class", "taxonomy",
    "tokens", "content_hash", "duration", "bounds_size", "skeleton_path",
    "dominant_limb", "fingerprint_vec", "interface_spec", "domain_spec", "yaml_cache",
)
PAYLOAD_COLUMNS = (
    "asset_path", "namespace", "field_name", "value_type", "value_text",
    "value_number", "value_bool", "provenance",
)
MAX_EDGES = 200
_NOISE_KEY_RE = re.compile(r"(?i)(updated[_-]?at|timestamp|session[_-]?id|absolute[_-]?path|local[_-]?path)")
_UNIX_LOCAL_PATH_RE = re.compile(r"(?<![\w/])/(?:home|Users|tmp|var)/[^\s\"']+")
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _scrub(value: Any) -> str:
    text = artifacts.canonicalize_text("" if value is None else str(value))
    text = _UNIX_LOCAL_PATH_RE.sub("<local-path>", text)
    return _ISO_DATE_RE.sub("<date>", text)


def _canonical(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        number = float(value)
        return 0.0 if number == 0.0 else (number if math.isfinite(number) else 0.0)
    if isinstance(value, str):
        return _scrub(value)
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, set):
        return [_canonical(item) for item in sorted(value, key=str)]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, raw_value in sorted(value.items(), key=lambda pair: str(pair[0])):
            key = _scrub(raw_key)
            if _NOISE_KEY_RE.search(key):
                continue
            result[key] = _canonical(raw_value)
        return result
    return _scrub(value)


def _query(con: Any, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    cur = con.execute(sql, list(params))
    columns = [description[0] for description in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def _sorted_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical = [_canonical(row) for row in rows]
    return sorted(canonical, key=lambda row: artifacts.canonical_yaml(row))


def index_revision(con: Any) -> str:
    """SHA256 over canonical, sorted change-detection inputs; not a dedup key."""
    schema_meta = _query(con, "SELECT key, value FROM schema_meta ORDER BY key")
    assets = _query(
        con,
        "SELECT asset_path, content_hash FROM assets ORDER BY asset_path, content_hash",
    )
    edges = _query(
        con,
        "SELECT source_path, target_path, edge_type, weight FROM asset_edges "
        "ORDER BY source_path, target_path, edge_type, weight",
    )
    try:
        payload = _query(
            con,
            "SELECT asset_path, namespace, field_name, value_type, value_text, "
            "value_number, value_bool, provenance FROM extension_payload "
            "ORDER BY asset_path, namespace, field_name, value_type, provenance",
        )
    except Exception:
        payload = []
    return artifacts.digest({
        "schema_meta": _sorted_rows(schema_meta),
        "assets": _sorted_rows(assets),
        "asset_edges": _sorted_rows(edges),
        "extension_payload": _sorted_rows(payload),
    })


def engine_version(con: Any) -> str:
    rows = _query(con, "SELECT value FROM schema_meta WHERE key = ?", ["engine_version"])
    if not rows or rows[0].get("value") in (None, ""):
        return "UE6"
    return _scrub(rows[0]["value"]) or "UE6"


def asset_snapshot(con: Any, asset_path: str) -> dict[str, Any]:
    """One bounded L0/L1/L3 row without clock columns or host-local paths."""
    columns = ", ".join(SNAPSHOT_COLUMNS)
    rows = _query(
        con,
        f"SELECT {columns} FROM assets WHERE asset_path = ? LIMIT 1",
        [str(asset_path)],
    )
    if not rows:
        return {}
    row = rows[0]
    snapshot = {column: _canonical(row.get(column)) for column in SNAPSHOT_COLUMNS}
    snapshot["yaml_cache"] = artifacts.canonicalize_text(str(row.get("yaml_cache") or ""))
    return snapshot


def edge_rows(con: Any, asset_path: str) -> list[dict[str, Any]]:
    """Sorted outgoing/incoming edges for one asset only."""
    sql = (
        "SELECT direction, source_path, target_path, edge_type, weight FROM ("
        "SELECT 'out' AS direction, source_path, target_path, edge_type, weight "
        "FROM asset_edges WHERE source_path = ? UNION ALL "
        "SELECT 'in' AS direction, source_path, target_path, edge_type, weight "
        "FROM asset_edges WHERE target_path = ?) q "
        "ORDER BY direction, source_path, target_path, edge_type, weight LIMIT ?"
    )
    rows = _query(con, sql, [str(asset_path), str(asset_path), MAX_EDGES])
    return [_canonical(row) for row in rows]


def remove_assets(con: Any, paths: list[str]) -> int:
    """Delete sorted unique /Game paths from the three index tables."""
    wanted = sorted({
        str(path).strip() for path in (paths or [])
        if str(path).strip().startswith("/Game")
    })
    if not wanted:
        return 0
    placeholders = ", ".join("?" for _ in wanted)
    count = int(con.execute(
        f"SELECT COUNT(*) FROM assets WHERE asset_path IN ({placeholders})",
        wanted,
    ).fetchone()[0])
    con.execute(
        f"DELETE FROM extension_payload WHERE asset_path IN ({placeholders})",
        wanted,
    )
    con.execute(
        f"DELETE FROM asset_edges WHERE source_path IN ({placeholders}) "
        f"OR target_path IN ({placeholders})",
        wanted + wanted,
    )
    con.execute(
        f"DELETE FROM assets WHERE asset_path IN ({placeholders})",
        wanted,
    )
    return count
