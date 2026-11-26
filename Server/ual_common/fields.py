"""Typed extension fields: declarative registry, projection and bounded reads.

The 16-key asset record is never widened. Values come only from the existing
``domain_spec`` / ``interface_spec`` maps through registered builtin callables;
all writes go to the separate ``extension_payload`` table. Batch outputs use the
single TOON renderer. No clock, local disk path or random identifier is emitted.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from . import artifacts, extensions, extensions_builtins
from .registry import NAMESPACES
from .settings import LAYERS
from .toon import Table, render_toon

VALUE_TYPES = ("string", "integer", "number", "boolean", "yaml")
MAX_SEARCH_LIMIT = 100
MAX_CATALOG_LIMIT = 200
MAX_ASSEMBLY_LIMIT = 100
MAX_DISPLAY_LINES = 4
MAX_DISPLAY_CHARS = 160

PAYLOAD_COLUMNS = (
    "asset_path", "namespace", "field_name", "value_type",
    "value_text", "value_number", "value_bool", "provenance",
)
INSERT_SQL = (
    "INSERT INTO extension_payload (asset_path, namespace, field_name, value_type, "
    "value_text, value_number, value_bool, provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

_INT_RE = re.compile(r"^[+-]?\d+$")
_NUM_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_UNIX_LOCAL_PATH_RE = re.compile(r"(?<![\w/])/(?:home|Users|tmp|var)/[^\s\"']+")
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NOISE_KEY_RE = re.compile(r"(?i)(updated[_-]?at|timestamp|session[_-]?id|absolute[_-]?path|local[_-]?path)")


def _scrub_text(value: Any) -> str:
    """Strip volatile host details from every emitted scalar."""
    text = artifacts.canonicalize_text("" if value is None else str(value))
    text = _UNIX_LOCAL_PATH_RE.sub("<local-path>", text)
    return _ISO_DATE_RE.sub("<date>", text)


def _safe_data(value: Any) -> Any:
    """Return deterministic YAML-safe data with noise keys removed."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value) if math.isfinite(value) else 0.0
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, (list, tuple)):
        return [_safe_data(item) for item in value]
    if isinstance(value, set):
        return [_safe_data(item) for item in sorted(value, key=str)]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, raw_value in sorted(value.items(), key=lambda pair: str(pair[0])):
            key = _scrub_text(raw_key)
            if _NOISE_KEY_RE.search(key):
                continue
            result[key] = _safe_data(raw_value)
        return result
    return _scrub_text(value)


def _yaml_text(value: Any) -> str | None:
    """Canonicalize a typed YAML value via the one YAML emitter."""
    try:
        parsed = yaml.safe_load(value) if isinstance(value, str) else value
        return artifacts.canonical_yaml(_safe_data(parsed))
    except (yaml.YAMLError, TypeError, ValueError, AttributeError):
        return None


def _typed(value_type: str, raw: Any) -> dict[str, Any] | None:
    """Strict scalar coercion; return None when the value is not admissible."""
    if value_type == "string":
        if not isinstance(raw, str):
            return None
        text = _scrub_text(raw)
        return {"value_text": text, "value_number": None, "value_bool": None, "value": text}

    if value_type == "integer":
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
            return None
        text = str(raw).strip()
        if not _INT_RE.fullmatch(text):
            return None
        number = int(text)
        return {"value_text": str(number), "value_number": float(number), "value_bool": None, "value": number}

    if value_type == "number":
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
            return None
        text = str(raw).strip()
        if not _NUM_RE.fullmatch(text):
            return None
        number = float(text)
        if not math.isfinite(number):
            return None
        return {"value_text": f"{number:.4f}", "value_number": number, "value_bool": None, "value": number}

    if value_type == "boolean":
        if isinstance(raw, bool):
            flag = raw
        elif isinstance(raw, str) and raw.strip().lower() in ("true", "false"):
            flag = raw.strip().lower() == "true"
        else:
            return None
        text = "true" if flag else "false"
        return {"value_text": text, "value_number": None, "value_bool": flag, "value": flag}

    text = _yaml_text(raw)
    if text is None:
        return None
    return {"value_text": text, "value_number": None, "value_bool": None, "value": text}
    return None


def _neutral(value_type: str) -> dict[str, Any]:
    fallback = {
        "string": "", "integer": 0, "number": 0.0, "boolean": False, "yaml": [],
    }.get(value_type, "")
    return _typed(value_type, fallback) or {
        "value_text": "", "value_number": None, "value_bool": None, "value": "",
    }


def coerce_value(
    value_type: str,
    raw: Any,
    default: Any,
    *,
    missing: bool = False,
    nullable: bool = False,
) -> tuple[dict[str, Any], str]:
    """Coerce one value without raising; bad values deterministically use default."""
    kind = str(value_type) if str(value_type) in VALUE_TYPES else "string"
    if raw is None and nullable:
        return {"value_text": None, "value_number": None, "value_bool": None, "value": None}, "null"
    typed = _typed(kind, raw)
    if typed is not None:
        return typed, "default" if missing else "ok"
    fallback = _typed(kind, default)
    if fallback is not None:
        return fallback, "default" if missing else "invalid_value"
    return _neutral(kind), "invalid_default"


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        try:
            return {str(k): v for k, v in value}
        except (TypeError, ValueError):
            return {}
    return {}


def _source_mapping(record: dict[str, Any], source: str) -> dict[str, Any]:
    if source in ("record", "asset", "identity"):
        return record
    return _as_mapping(record.get(source))


def _display(value_type: str, value_text: Any, value_number: Any, value_bool: Any) -> str:
    """Bounded display value; numeric precision is fixed and independent of DB text."""
    if value_type == "boolean" and value_bool is not None:
        return "true" if bool(value_bool) else "false"
    if value_type == "integer" and value_number is not None:
        return str(int(float(value_number)))
    if value_type == "number" and value_number is not None:
        return f"{float(value_number):.4f}"
    if value_text is None:
        return ""
    lines = [line.strip() for line in _scrub_text(value_text).splitlines() if line.strip()]
    text = " | ".join(lines[:MAX_DISPLAY_LINES])
    if len(text) > MAX_DISPLAY_CHARS:
        text = text[: MAX_DISPLAY_CHARS - 3] + "..."
    return text


def _row_value(row: Any, key: str, default: Any = "") -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _bounded(rows: Iterable[Any], limit: int, cap: int) -> tuple[list[Any], int]:
    size = max(1, min(int(limit or cap), cap))
    return list(rows or [])[:size], size


def _layer(value: Any) -> str:
    prefix = str(value or "builtin").split(":", 1)[0]
    return prefix if prefix in LAYERS else "builtin"


def _provenance_map(loaded: Any) -> dict[str, str]:
    if isinstance(loaded, dict):
        return {str(k): str(v) for k, v in loaded.items()}
    resolved = getattr(loaded, "resolved", None)
    provenance = getattr(resolved, "provenance", None)
    return {str(k): str(v) for k, v in (provenance or {}).items()}


def _spec_dict(spec: Any) -> dict[str, Any]:
    return {
        "name": str(getattr(spec, "name", "")),
        "namespace": str(getattr(spec, "namespace", "")),
        "schema": dict(getattr(spec, "schema", {}) or {}),
    }


@dataclass(frozen=True)
class FieldSpec:
    namespace: str
    name: str
    value_type: str = "string"
    source: str = "domain_spec"
    key: str = ""
    default: Any = None
    applies_to: tuple[str, ...] = ("*",)
    priority: int = 0
    version: str = "1"
    provenance: str = "builtin"

    def __post_init__(self) -> None:
        if self.namespace not in NAMESPACES:
            raise ValueError(f"unknown field namespace: {self.namespace!r}")
        if not self.name or "." in self.name:
            raise ValueError("field name must be non-empty and contain no dot")
        if self.value_type not in VALUE_TYPES:
            raise ValueError(f"invalid field value type: {self.value_type!r}")
        applies = tuple(str(item) for item in (self.applies_to or ("*",)))
        object.__setattr__(self, "applies_to", applies)
        object.__setattr__(self, "priority", int(self.priority or 0))
        object.__setattr__(self, "version", str(self.version or "1"))
        object.__setattr__(self, "provenance", _layer(self.provenance))
        object.__setattr__(self, "source", str(self.source or "domain_spec"))
        object.__setattr__(self, "key", str(self.key or ""))

    @property
    def extension_key(self) -> str:
        return f"{self.namespace}.{self.name}"

    @property
    def source_key(self) -> str:
        return self.key or self.name

    @property
    def nullable(self) -> bool:
        return self.default is None

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": self.value_type,
            "source": self.source,
            "key": self.source_key,
            "default": self.default,
            "nullable": self.nullable,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "key": self.extension_key,
            "namespace": self.namespace,
            "name": self.name,
            "value_type": self.value_type,
            "source": self.source,
            "key_in_source": self.source_key,
            "default": _safe_data(self.default),
            "applies_to": list(self.applies_to),
            "priority": self.priority,
            "version": self.version,
            "provenance": self.provenance,
        }


class FieldRegistry:
    """Field specs plus their registered builtin projection callables."""

    def __init__(self, entries: Iterable[tuple[FieldSpec, Callable[..., Any] | None]] = ()) -> None:
        self._entries: dict[str, tuple[FieldSpec, Callable[..., Any]]] = {}
        for spec, impl in entries:
            self.register(spec, impl=impl)

    def __len__(self) -> int:
        return len(self._entries)

    def register(self, spec: FieldSpec, impl: Callable[..., Any] | None = None) -> FieldSpec:
        key = spec.extension_key
        if key in self._entries:
            raise ValueError(f"field already registered: {key}")
        self._entries[key] = (spec, impl or extensions_builtins.field_from_domain)
        return spec

    @classmethod
    def from_extension_registry(
        cls,
        registry: Any,
        *,
        provenance: dict[str, str] | None = None,
    ) -> "FieldRegistry":
        source = getattr(registry, "registry", registry)
        prov_map = dict(provenance or _provenance_map(registry))
        matches: list[tuple[Any, Any]] = list(source.resolve("*", namespace="field"))
        # resolve("*") intentionally favors wildcard entries. Exact-only specs are
        # declarative data too, so add them through the registry's safe getter.
        known = {str(spec.key) for spec, _ in matches}
        if hasattr(source, "snapshot") and hasattr(source, "get"):
            for item in source.snapshot():
                if item.get("namespace") != "field" or str(item.get("key", "")) in known:
                    continue
                entry = source.get("field", str(item.get("name", "")))
                if entry is not None:
                    matches.append(entry)
                    known.add(str(entry[0].key))

        result = cls()
        for spec, impl in matches:
            schema = dict(getattr(spec, "schema", {}) or {})
            value_type = str(schema.get("type", "string"))
            if value_type not in VALUE_TYPES:
                raise ValueError(f"invalid field value type: {value_type!r}")
            field_spec = FieldSpec(
                namespace=str(spec.namespace),
                name=str(spec.name),
                value_type=value_type,
                source=str(schema.get("source", "domain_spec")),
                key=str(schema.get("key", spec.name)),
                default=schema.get("default"),
                applies_to=tuple(str(item) for item in spec.applies_to),
                priority=int(spec.priority),
                version=str(spec.version),
                provenance=_layer(prov_map.get(str(spec.key))),
            )
            result.register(field_spec, impl=impl if callable(impl) else None)
        return result

    def resolve(self, asset_class: str, *, namespace: str | None = None) -> list[FieldSpec]:
        """Exact applies_to > wildcard, then priority desc, then extension key asc."""
        asset = str(asset_class or "*")
        matches: list[tuple[FieldSpec, bool]] = []
        for key in sorted(self._entries):
            spec, _impl = self._entries[key]
            if namespace is not None and spec.namespace != namespace:
                continue
            wildcard = "*" in spec.applies_to
            exact = asset != "*" and not wildcard and asset in spec.applies_to
            if not wildcard and not exact:
                continue
            matches.append((spec, exact))
        matches.sort(key=lambda item: (-int(item[1]), -item[0].priority, item[0].extension_key))
        return [spec for spec, _exact in matches]

    def snapshot(self) -> list[dict[str, Any]]:
        return [self._entries[key][0].snapshot() for key in sorted(self._entries)]

    def project(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        """Project one record through registered builtin impls, never raising per value."""
        rows: dict[tuple[str, str], dict[str, Any]] = {}
        asset_path = _scrub_text(record.get("asset_path", ""))
        for spec in self.resolve(str(record.get("asset_class", "*"))):
            key = spec.extension_key
            _registered, impl = self._entries.get(key, (spec, extensions_builtins.field_from_domain))
            spec_dict = {"name": spec.name, "schema": spec.schema}
            context = {"record": record, "asset": record}
            status_override = ""
            try:
                raw = impl(context, spec_dict)
            except Exception:
                raw = spec.default
                status_override = "impl_error"
            source_map = _source_mapping(record, spec.source)
            missing = spec.source_key not in source_map
            typed, status = coerce_value(
                spec.value_type, raw, spec.default,
                missing=missing or bool(status_override), nullable=spec.nullable,
            )
            if status_override:
                status = status_override
            row_key = (spec.namespace, spec.name)
            if row_key in rows:
                continue
            rows[row_key] = {
                "asset_path": asset_path,
                "namespace": spec.namespace,
                "field_name": spec.name,
                "value_type": spec.value_type,
                "value_text": typed["value_text"],
                "value_number": typed["value_number"],
                "value_bool": typed["value_bool"],
                "provenance": spec.provenance,
                "status": status,
                "source": spec.source,
                "key": spec.source_key,
                "value": typed["value"],
            }
        return [rows[key] for key in sorted(rows)]


def _extension_and_field(registry: Any, project_root: Path | None) -> tuple[Any, FieldRegistry, dict[str, str]]:
    if isinstance(registry, FieldRegistry):
        loaded = extensions.load_registry(project_root=project_root)
        return getattr(loaded, "registry", loaded), registry, _provenance_map(loaded)
    loaded = registry if registry is not None else extensions.load_registry(project_root=project_root)
    source = getattr(loaded, "registry", loaded)
    prov_map = _provenance_map(loaded)
    return source, FieldRegistry.from_extension_registry(source, provenance=prov_map), prov_map


def _asset_type_rows(
    extension_registry: Any,
    record: dict[str, Any],
    provenance: dict[str, str],
) -> list[dict[str, Any]]:
    asset_path = _scrub_text(record.get("asset_path", ""))
    asset_class = str(record.get("asset_class", ""))
    matches = extension_registry.resolve(asset_class, namespace="asset_type")
    role, relations = "unclassified", []
    layer = "builtin"
    if matches:
        spec, impl = matches[0]
        layer = _layer(provenance.get(str(spec.key)))
        try:
            outcome = impl(
                {"asset": {"asset_path": asset_path, "asset_class": asset_class}, "record": record},
                {"name": spec.name, "schema": dict(spec.schema or {})},
            ) if callable(impl) else extensions_builtins.asset_type_descriptor(
                {"asset": {"asset_path": asset_path, "asset_class": asset_class}},
                {"name": spec.name, "schema": dict(spec.schema or {})},
            )
            if isinstance(outcome, dict):
                role = _scrub_text(outcome.get("role", role))
                raw_relations = outcome.get("assembly") or []
                if isinstance(raw_relations, list):
                    relations = [
                        {
                            "relation": _scrub_text(item.get("relation", "assembles_with")),
                            "target_role": _scrub_text(item.get("target_role", "unclassified")),
                        }
                        for item in raw_relations if isinstance(item, dict)
                    ]
        except Exception:
            role, relations = "unclassified", []
    relations.sort(key=lambda item: (item["relation"], item["target_role"]))

    role_typed, role_status = coerce_value("string", role, "unclassified")
    assembly_typed, assembly_status = coerce_value("yaml", relations, [])
    return [
        {
            "asset_path": asset_path, "namespace": "asset_type", "field_name": "asset_role",
            "value_type": "string", **{k: role_typed[k] for k in ("value_text", "value_number", "value_bool")},
            "provenance": layer, "status": role_status,
        },
        {
            "asset_path": asset_path, "namespace": "asset_type", "field_name": "assembly",
            "value_type": "yaml", **{k: assembly_typed[k] for k in ("value_text", "value_number", "value_bool")},
            "provenance": layer, "status": assembly_status,
        },
    ]


def materialize(con: Any, registry: Any = None, *, project_root: Path | None = None) -> dict[str, int]:
    """Writer-side projection into extension_payload; read-only tools never call this."""
    extension_registry, field_registry, provenance = _extension_and_field(registry, project_root)
    cur = con.execute(
        "SELECT asset_path, asset_class, domain_spec, interface_spec FROM assets ORDER BY asset_path"
    )
    counts = {"assets": 0, "fields": 0, "asset_role": 0, "assembly": 0, "rows": 0}
    for asset_path, asset_class, domain_spec, interface_spec in cur.fetchall():
        record = {
            "asset_path": str(asset_path or ""),
            "asset_class": str(asset_class or ""),
            "domain_spec": _as_mapping(domain_spec),
            "interface_spec": _as_mapping(interface_spec),
        }
        rows = _asset_type_rows(extension_registry, record, provenance)
        field_rows = field_registry.project(record)
        rows.extend(field_rows)
        counts["fields"] += len(field_rows)
        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            row_key = (str(row["namespace"]), str(row["field_name"]))
            if row_key not in unique:
                unique[row_key] = row
        con.execute("DELETE FROM extension_payload WHERE asset_path = ?", [record["asset_path"]])
        for row_key in sorted(unique):
            row = unique[row_key]
            con.execute(INSERT_SQL, [
                _scrub_text(row["asset_path"]), _scrub_text(row["namespace"]),
                _scrub_text(row["field_name"]), _scrub_text(row["value_type"]),
                row["value_text"], row["value_number"], row["value_bool"],
                _layer(row["provenance"]),
            ])
            counts["rows"] += 1
            if row_key == ("asset_type", "asset_role"):
                counts["asset_role"] += 1
            elif row_key == ("asset_type", "assembly"):
                counts["assembly"] += 1
        counts["assets"] += 1
    counts["asset_type"] = counts["asset_role"] + counts["assembly"]
    counts["extension_payload"] = counts["rows"]
    return counts


def search(
    con: Any,
    *,
    namespace: str | None = None,
    field_name: str | None = None,
    value_contains: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Bounded, fully parameterized typed-field search."""
    size = max(1, min(int(limit or 20), MAX_SEARCH_LIMIT))
    where: list[str] = []
    params: list[Any] = []
    if namespace:
        where.append("namespace = ?")
        params.append(str(namespace))
    if field_name:
        where.append("field_name = ?")
        params.append(str(field_name))
    if value_contains:
        pattern = "%" + str(value_contains) + "%"
        where.append(
            "(COALESCE(CAST(value_text AS VARCHAR), '') ILIKE ? OR "
            "COALESCE(CAST(value_number AS VARCHAR), '') ILIKE ? OR "
            "COALESCE(CAST(value_bool AS VARCHAR), '') ILIKE ?)"
        )
        params.extend([pattern, pattern, pattern])
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    sql = (
        "SELECT asset_path, namespace, field_name, value_type, value_text, "
        "value_number, value_bool, provenance FROM extension_payload" + clause +
        " ORDER BY asset_path, namespace, field_name LIMIT ?"
    )
    cur = con.execute(sql, params + [size])
    result: list[dict[str, Any]] = []
    for asset_path, ns, name, value_type, text, number, flag, provenance in cur.fetchall():
        result.append({
            "asset_path": _scrub_text(asset_path),
            "namespace": _scrub_text(ns),
            "field_name": _scrub_text(name),
            "value_type": _scrub_text(value_type),
            "value_display": _display(str(value_type), text, number, flag),
            "provenance": _layer(provenance),
        })
    return result


def catalog(con: Any, *, limit: int = 50) -> list[dict[str, Any]]:
    """Bounded field catalog; no raw payload values are returned."""
    size = max(1, min(int(limit or 50), MAX_CATALOG_LIMIT))
    cur = con.execute(
        "SELECT namespace, field_name, value_type, provenance, "
        "COUNT(DISTINCT asset_path) AS asset_count FROM extension_payload "
        "GROUP BY namespace, field_name, value_type, provenance "
        "ORDER BY namespace, field_name, value_type, provenance LIMIT ?",
        [size],
    )
    return [{
        "namespace": _scrub_text(ns),
        "field_name": _scrub_text(name),
        "value_type": _scrub_text(value_type),
        "provenance": _layer(provenance),
        "asset_count": int(count),
    } for ns, name, value_type, provenance, count in cur.fetchall()]


def assembly_graph(con: Any, asset_path: str, *, limit: int = 20) -> str:
    """Bounded role + declared assembly relations for one asset."""
    size = max(1, min(int(limit or 20), MAX_ASSEMBLY_LIMIT))
    cur = con.execute(
        "SELECT namespace, field_name, value_text, provenance FROM extension_payload "
        "WHERE asset_path = ? AND namespace IN (?, ?) AND field_name IN (?, ?) "
        "ORDER BY namespace, field_name",
        [str(asset_path), "asset_type", "field", "asset_role", "assembly"],
    )
    selected: dict[str, tuple[str, str]] = {}
    for namespace, field_name, value_text, provenance in cur.fetchall():
        name = str(field_name)
        if name not in selected or str(namespace) == "asset_type":
            selected[name] = (str(value_text or ""), _layer(provenance))
    role = _scrub_text(selected.get("asset_role", ("unclassified", "builtin"))[0]) or "unclassified"
    role_provenance = selected.get("asset_role", ("", "builtin"))[1]
    relations: list[tuple[str, str]] = []
    if "assembly" in selected:
        try:
            parsed = yaml.safe_load(selected["assembly"][0])
        except (yaml.YAMLError, TypeError, ValueError):
            parsed = []
        if isinstance(parsed, dict):
            parsed = parsed.get("relations") or []
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    relations.append((
                        _scrub_text(item.get("relation", "assembles_with")),
                        _scrub_text(item.get("target_role", "unclassified")),
                    ))
    relations = sorted(set(relations))
    shown = relations[:size]
    asset = _scrub_text(asset_path)
    return render_toon(
        [
            Table("asset_role", ["asset_path", "role", "provenance"], [[asset, role, role_provenance]]),
            Table("assembly", ["asset_path", "relation", "target_role"],
                  [[asset, relation, target] for relation, target in shown]),
        ],
        summary={
            "asset_path": asset,
            "asset_role": role,
            "total": len(relations),
            "returned": len(shown),
            "truncated": "true" if len(relations) > len(shown) else "false",
            "limit": size,
        },
    )


def render_search(rows: list[dict[str, Any]], *, total: int | None = None, limit: int = 20) -> str:
    """Pure bounded TOON for typed-field search rows."""
    ordered = sorted(
        list(rows or []),
        key=lambda row: (
            str(_row_value(row, "asset_path")), str(_row_value(row, "namespace")),
            str(_row_value(row, "field_name")),
        ),
    )
    shown, size = _bounded(ordered, limit, MAX_SEARCH_LIMIT)
    overall = len(ordered) if total is None else max(int(total), len(shown))
    table_rows = [[
        _scrub_text(_row_value(row, "asset_path")),
        _scrub_text(_row_value(row, "namespace")),
        _scrub_text(_row_value(row, "field_name")),
        _scrub_text(_row_value(row, "value_type")),
        _scrub_text(_row_value(row, "value_display")),
        _layer(_row_value(row, "provenance")),
    ] for row in shown]
    return render_toon(
        [Table("extension_fields", [
            "asset_path", "namespace", "field_name", "value_type", "value_display", "provenance",
        ], table_rows)],
        summary={
            "total": overall,
            "returned": len(table_rows),
            "truncated": "true" if overall > len(table_rows) or len(ordered) > len(table_rows) else "false",
            "limit": size,
        },
    )


def render_catalog(rows: list[dict[str, Any]], *, limit: int = 50) -> str:
    """Pure bounded TOON for the typed-field catalog."""
    ordered = sorted(
        list(rows or []),
        key=lambda row: (
            str(_row_value(row, "namespace")), str(_row_value(row, "field_name")),
            str(_row_value(row, "value_type")), str(_row_value(row, "provenance")),
        ),
    )
    shown, size = _bounded(ordered, limit, MAX_CATALOG_LIMIT)
    table_rows = [[
        _scrub_text(_row_value(row, "namespace")),
        _scrub_text(_row_value(row, "field_name")),
        _scrub_text(_row_value(row, "value_type")),
        _layer(_row_value(row, "provenance")),
        int(_row_value(row, "asset_count", 0) or 0),
    ] for row in shown]
    return render_toon(
        [Table("extension_catalog", [
            "namespace", "field_name", "value_type", "provenance", "asset_count",
        ], table_rows)],
        summary={
            "total": len(ordered),
            "returned": len(table_rows),
            "truncated": "true" if len(ordered) > len(table_rows) else "false",
            "limit": size,
        },
    )
