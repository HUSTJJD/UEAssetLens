#!/usr/bin/env python3
"""Offline audit for engine toolsets and the generated UAL tool contract.

Default ``--check`` is stdlib + PyYAML only and opens no socket.  Optional
``--live-url`` is restricted to the two official loopback URLs, uses a bounded
urllib JSON-RPC handshake, treats every HTTP response as data and never prints
the full ``tools/list`` payload.  All machine-facing text is TOON rendered by
``ual_common.toon.render_toon``; YAML data is only read with ``yaml.safe_load``
and canonicalized through ``ual_common.artifacts.canonical_yaml`` when a nested
untrusted value must be reduced to a bounded label.
"""
from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = ROOT / "Server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from ual_common.artifacts import canonical_yaml  # noqa: E402
from ual_common.toon import Table, render_toon  # noqa: E402

DEFAULT_INVENTORY = "../../Docs/ai-capabilities/toolset-inventory.yaml"
DEFAULT_SCHEMAS = "Server/ual_mcp/tool_schemas.yaml"
DEFAULT_UPROJECT = "../../Ann.uproject"
PLUGIN_DESCRIPTOR = "UEAssetLens.uplugin"
EXTENSIONS_RELATIVE = "Config/UAL/extensions"
GATEWAY_INTEGRATIONS = "Config/UAL/gateway/integrations.yaml"

ALLOWED_LIVE_URLS = frozenset({
    "http://localhost:8000/mcp",
    "http://127.0.0.1:8000/mcp",
})
ACCESS_MODE_ORDER = ("Read", "Write", "Execute")
ACCESS_MODES = frozenset(ACCESS_MODE_ORDER)
UNANNOTATED = "unannotated"
EXPECTED_UAL_STATIC_COUNT = 10
EXPECTED_UAL_RUNTIME_COUNT = 20
EXPECTED_UAL_TOTAL_COUNT = 30
NAME_KEYS = ("name", "mcp_name", "wireName", "toolName", "qualified_name")
MODE_KEYS = ("accessMode", "AIAccessMode", "access_mode")
KIND_KEYS = ("kind", "origin", "registration", "source_kind", "tool_kind")
ALLOWED_BUILTIN_PREFIX = "ual_common.extensions_builtins."
PROTOCOL_VERSION = "2025-11-25"
MAX_INPUT_BYTES = 8_000_000
MAX_RESPONSE_BYTES = 512_000
MAX_ISSUES = 12
MAX_LIVE_NAMES = 2000
MAX_LIVE_PAGES = 5
LIVE_TIMEOUT_SECONDS = 3.0

_WINDOWS_ABS_RE = re.compile(r"[A-Za-z]:[\\/][^\s,]+")
_UNC_ABS_RE = re.compile(r"\\\\+[^\s,]+")
_POSIX_ABS_RE = re.compile(r"(?<!\S)/(?!Game(?:/|$))[^\s,]+")
_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)?\b|\b\d{2}:\d{2}:\d{2}\b"
)
_RANDOM_ID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b|\b[0-9a-fA-F]{32,64}\b"
)
_BARE_UAL_RE = re.compile(r"(?:^|\.)ual_[A-Za-z0-9_]+", re.IGNORECASE)


class AuditError(Exception):
    """A fixed, data-free failure code; never carries host paths or payloads."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class InventoryFacts:
    tool_count: int
    counts: dict[str, int]
    invalid_modes: tuple[str, ...]
    summary_mismatch: int
    duplicate_qualified: tuple[str, ...]
    qualified_names: frozenset[str]
    primary_names: frozenset[str]
    bare_names: frozenset[str]
    version: str


@dataclass
class UalEntry:
    name: str
    kind: str
    access_mode: str | None
    access_conflict: bool
    bare_aliases: tuple[str, ...]
    source: str
    explicit_kind: bool


@dataclass(frozen=True)
class RuntimeManifest:
    entries: tuple[UalEntry, ...]
    sources: tuple[tuple[str, str, str], ...]
    unsafe_impl: tuple[str, ...]
    names: frozenset[str]


@dataclass(frozen=True)
class AuditResult:
    summary: dict[str, Any]
    sources: tuple[tuple[str, str, str], ...]
    access_modes: tuple[tuple[str, str, int], ...]
    issues: tuple[tuple[str, str, str], ...]
    official_qualified_names: frozenset[str]
    official_primary_names: frozenset[str]
    official_bare_names: frozenset[str]
    ual_names: frozenset[str]
    result: str


def _safe_label(value: Any, limit: int = 96) -> str:
    """Bounded, redacted scalar for any data-derived text in TOON output."""
    if isinstance(value, Mapping) or isinstance(value, (list, tuple, set)):
        try:
            text = canonical_yaml(value)
        except Exception:
            text = str(value)
    else:
        text = str(value or "")
    text = text.replace("\r", " ").replace("\n", " ")
    text = _WINDOWS_ABS_RE.sub("<path>", text)
    text = _UNC_ABS_RE.sub("<path>", text)
    text = _POSIX_ABS_RE.sub("<path>", text)
    text = _TIMESTAMP_RE.sub("<timestamp>", text)
    text = _RANDOM_ID_RE.sub("<id>", text)
    text = _BARE_UAL_RE.sub("<wire-name>", text)
    text = text.replace("\\", "/")
    text = "".join(ch if ord(ch) >= 32 and ord(ch) != 127 else " " for ch in text)
    return text.strip()[: max(1, min(int(limit), 256))]


def _relative_arg(value: Any) -> str:
    """Accept a repository-relative path and never echo an absolute path."""
    raw = str(value or "").replace("\\", "/").strip()
    if not raw or ":" in raw or raw.startswith("/"):
        raise AuditError("invalid_relative_path")
    relative = posixpath.normpath(raw)
    if relative in ("", ".", "..") or relative.startswith("/") or ":" in relative:
        raise AuditError("invalid_relative_path")
    return relative


def _resolve(relative: str) -> Path:
    return (ROOT / Path(relative)).resolve()


def _load_yaml(relative: str) -> dict[str, Any]:
    try:
        raw = _resolve(relative).read_bytes()
        if len(raw) > MAX_INPUT_BYTES:
            raise AuditError("input_too_large")
        text = raw.decode("utf-8-sig")
        payload = yaml.safe_load(text) if text.strip() else None
    except AuditError:
        raise
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise AuditError("yaml_parse_error") from None
    if not isinstance(payload, Mapping):
        raise AuditError("yaml_root_not_mapping")
    return dict(payload)


def _load_json(relative: str) -> dict[str, Any]:
    try:
        raw = _resolve(relative).read_bytes()
        if len(raw) > MAX_INPUT_BYTES:
            raise AuditError("input_too_large")
        payload = json.loads(raw.decode("utf-8-sig"))
    except AuditError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise AuditError("json_parse_error") from None
    if not isinstance(payload, Mapping):
        raise AuditError("json_root_not_mapping")
    return dict(payload)


def _version_tuple(value: Any) -> tuple[int, ...]:
    digits = re.findall(r"\d+", str(value or ""))
    return tuple(int(item) for item in digits[:3]) if digits else (0,)


def _normalize_access_mode(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text.casefold() in {"unset", "unannotated", "none", "null"}:
        return None
    aliases = {
        "read": "Read", "readonly": "Read", "read_only": "Read",
        "write": "Write", "execute": "Execute",
    }
    return aliases.get(text.casefold(), text)


def _entry_name(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, Mapping):
        return ""
    candidates = [str(item.get(key) or "").strip() for key in NAME_KEYS]
    qualified = [value for value in candidates if value.startswith("UEAssetLens.")]
    if qualified:
        return qualified[0]
    return next((value for value in candidates if value), "")


def _bare_aliases(item: Any) -> tuple[str, ...]:
    if not isinstance(item, Mapping):
        text = str(item or "")
        return (text,) if _BARE_UAL_RE.search(text) else ()
    hits: list[str] = []
    for key in NAME_KEYS:
        value = str(item.get(key) or "")
        if value and _BARE_UAL_RE.search(value):
            hits.append(value)
    return tuple(sorted(set(hits)))


def _carriers(item: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    carriers: list[Mapping[str, Any]] = [item]
    for key in ("annotations", "meta", "_meta", "metadata"):
        nested = item.get(key)
        if isinstance(nested, Mapping):
            carriers.append(nested)
    return carriers


def _extract_access_mode(item: Any) -> tuple[str | None, bool]:
    if not isinstance(item, Mapping):
        return None, False
    values: list[str] = []
    for carrier in _carriers(item):
        for key in MODE_KEYS:
            if key not in carrier or carrier[key] in (None, ""):
                continue
            mode = _normalize_access_mode(carrier[key])
            if mode is not None:
                values.append(mode)
    if not values:
        return None, False
    distinct = set(values)
    if len(distinct) > 1:
        return "CONFLICT", True
    return values[0], False


def _top_kind_hint(payload: Mapping[str, Any]) -> str:
    source = " ".join(str(payload.get(key) or "") for key in ("source", "note", "origin", "kind")).lower()
    has_static = "tools.py" in source or "tool_defs" in source or "static" in source
    has_runtime = "extension" in source or "runtime" in source
    if has_static and not has_runtime:
        return "static"
    if has_runtime and not has_static:
        return "runtime"
    return ""


def _entry_kind(
    item: Any,
    source_key: str,
    default_kind: str,
    top_hint: str,
) -> tuple[str, bool]:
    if isinstance(item, Mapping):
        for carrier in _carriers(item):
            for key in KIND_KEYS:
                value = str(carrier.get(key) or "").lower()
                if not value:
                    continue
                if "runtime" in value or "extension" in value:
                    return "runtime", True
                if "static" in value or "tools.py" in value or "tool_defs" in value:
                    return "static", True
        if "extension" in item or "handler" in item:
            return "runtime", True
        if str(item.get("namespace") or "").casefold() == "tool":
            return "runtime", True
    if source_key in {"runtime_tools", "extensions", "runtime_manifest"}:
        return (source_key == "extensions" and default_kind) or "runtime", True
    if source_key == "static_tools":
        return "static", True
    if top_hint:
        return top_hint, False
    return default_kind or "static", False


def _iter_raw_entries(raw: Any) -> list[Any]:
    if isinstance(raw, Mapping):
        entries: list[Any] = []
        for key in sorted(raw, key=str):
            item = raw[key]
            if isinstance(item, Mapping):
                merged = dict(item)
                merged.setdefault("name", key)
                entries.append(merged)
            else:
                entries.append(str(key))
        return entries
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        return list(raw)
    return []


def _group_entries(
    raw: Any,
    source_key: str,
    default_kind: str,
    top_hint: str,
) -> list[UalEntry]:
    entries: list[UalEntry] = []
    for item in _iter_raw_entries(raw):
        if source_key == "extensions" and isinstance(item, Mapping):
            namespace = str(item.get("namespace") or "")
            if namespace and namespace != "tool":
                continue
        name = _entry_name(item)
        kind, explicit = _entry_kind(item, source_key, default_kind, top_hint)
        access_mode, conflict = _extract_access_mode(item)
        entries.append(UalEntry(
            name=name,
            kind=kind,
            access_mode=access_mode,
            access_conflict=conflict,
            bare_aliases=_bare_aliases(item),
            source=source_key,
            explicit_kind=explicit,
        ))
    return entries


def _runtime_manifest() -> RuntimeManifest:
    directory = _resolve(EXTENSIONS_RELATIVE)
    constructed: list[Mapping[str, Any]] = []
    sources: list[tuple[str, str, str]] = []
    unsafe_impl: list[str] = []
    try:
        paths = sorted(directory.glob("*.yaml"))
    except OSError:
        raise AuditError("runtime_manifest_unavailable") from None
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        payload = _load_yaml(relative)
        rules_value = payload.get("rules")
        rules = rules_value if isinstance(rules_value, list) else ([payload] if "id" in payload else [])
        found_tool = False
        for rule in rules:
            if not isinstance(rule, Mapping) or str(rule.get("namespace") or "") != "tool":
                continue
            if rule.get("enabled", True) is False:
                continue
            schema = rule.get("schema") if isinstance(rule.get("schema"), Mapping) else {}
            name = _entry_name({"mcp_name": schema.get("mcp_name"), "name": rule.get("name")})
            impl = str(rule.get("impl") or "")
            if impl and not impl.startswith(ALLOWED_BUILTIN_PREFIX):
                unsafe_impl.append(name)
            constructed.append({
                "name": rule.get("name"),
                "mcp_name": schema.get("mcp_name"),
                "accessMode": schema.get("accessMode"),
                "origin": "runtime",
                "namespace": "tool",
                "impl": impl,
            })
            found_tool = True
        if found_tool:
            sources.append((
                "runtime_manifest",
                _safe_label(relative, 160),
                _safe_label(payload.get("schema_version", payload.get("version", "1")), 32),
            ))
    entries = _group_entries(constructed, "runtime_manifest", "runtime", "runtime")
    return RuntimeManifest(
        entries=tuple(entries),
        sources=tuple(sources),
        unsafe_impl=tuple(unsafe_impl),
        names=frozenset(entry.name for entry in entries if entry.name),
    )


def _collect_ual_entries(payload: Mapping[str, Any], relative: str) -> tuple[list[UalEntry], tuple[tuple[str, str, str], ...], tuple[str, ...]]:
    top_hint = _top_kind_hint(payload)
    primary = _group_entries(payload.get("tools"), "tools", "", top_hint)
    has_runtime_primary = any(entry.kind == "runtime" for entry in primary)
    entries = list(primary)
    if not has_runtime_primary:
        for key, default_kind in (("static_tools", "static"), ("runtime_tools", "runtime"), ("extensions", "runtime")):
            entries.extend(_group_entries(payload.get(key), key, default_kind, top_hint))
    runtime = _runtime_manifest()
    runtime_names = runtime.names
    for entry in entries:
        if entry.name in runtime_names:
            entry.kind = "runtime"
        elif entry.kind == "runtime" and not entry.explicit_kind and entry.source == "tools":
            entry.kind = "static"
    if not runtime_names and top_hint == "runtime":
        for entry in entries:
            if entry.name and entry.name.startswith("UEAssetLens.AssetSemanticToolset."):
                entry.kind = "static"
    sources: list[tuple[str, str, str]] = [(
        "ual_contract",
        _safe_label(relative, 160),
        _safe_label(payload.get("schema_version", ""), 32),
    )]
    sources.extend(runtime.sources)
    return entries, tuple(sources), runtime.unsafe_impl


def _inventory_facts(payload: Mapping[str, Any], relative: str) -> tuple[InventoryFacts, tuple[str, str, str]]:
    toolsets = payload.get("toolsets")
    if not isinstance(toolsets, list):
        raise AuditError("inventory_shape_invalid")
    counts = {mode: 0 for mode in ACCESS_MODE_ORDER}
    counts[UNANNOTATED] = 0
    invalid_modes: list[str] = []
    primary_list: list[str] = []
    qualified_names: set[str] = set()
    primary_names: set[str] = set()
    bare_names: set[str] = set()
    for toolset in toolsets:
        if not isinstance(toolset, Mapping):
            continue
        toolset_name = str(toolset.get("name") or "").strip()
        plugin_name = str(toolset.get("plugin") or "").strip()
        tools = toolset.get("tools")
        if not isinstance(tools, list):
            continue
        for tool in tools:
            if not isinstance(tool, Mapping):
                continue
            name = str(tool.get("name") or "").strip()
            if not name:
                continue
            mode = _normalize_access_mode(tool.get("access_mode"))
            if mode in ACCESS_MODES:
                counts[mode] += 1
            elif mode is None:
                counts[UNANNOTATED] += 1
            else:
                counts[UNANNOTATED] += 1
                invalid_modes.append(name)
            bare_names.add(name)
            header = Path(str(tool.get("header") or "").replace("\\", "/")).name
            header_stem = Path(header).stem
            prefixes = {toolset_name, plugin_name}
            for prefix in prefixes:
                if prefix:
                    qualified_names.add(f"{prefix}.{name}")
            primary = f"{toolset_name}.{name}" if toolset_name else name
            qualified_names.add(primary)
            primary_names.add(primary)
            primary_list.append(primary)
            if header_stem and header_stem != name:
                for prefix in prefixes:
                    if prefix:
                        qualified_names.add(f"{prefix}.{header_stem}.{name}")
    duplicates = tuple(sorted(name for name, count in Counter(primary_list).items() if count > 1))
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    try:
        declared = int(summary.get("total_tools", -1))
    except (TypeError, ValueError):
        declared = -1
    computed = sum(counts.values())
    facts = InventoryFacts(
        tool_count=computed,
        counts=counts,
        invalid_modes=tuple(invalid_modes),
        summary_mismatch=int(declared != computed),
        duplicate_qualified=duplicates,
        qualified_names=frozenset(qualified_names),
        primary_names=frozenset(primary_names),
        bare_names=frozenset(bare_names),
        version=_safe_label(payload.get("schema_version", ""), 32),
    )
    return facts, ("inventory", _safe_label(relative, 160), facts.version)


def _is_legacy_contract(payload: Mapping[str, Any]) -> bool:
    version = (_version_tuple(payload.get("schema_version")) + (0, 0))[:2]
    has_split_groups = any(key in payload for key in ("static_tools", "runtime_tools", "extensions"))
    return (3, 0) <= version < (4, 0) and not has_split_groups


def _issue_rows(category: str, names: Sequence[str], detail: str) -> list[tuple[str, str, str]]:
    return [(category, _safe_label(name, 120), detail) for name in names]


def _bounded_issues(rows: Sequence[tuple[str, str, str]]) -> tuple[tuple[tuple[str, str, str], ...], int, bool]:
    bounded = tuple(rows[:MAX_ISSUES])
    return bounded, len(bounded), len(rows) > len(bounded)


def _audit_contract(inventory_rel: str, schemas_rel: str, allow_runtime_fallback: bool) -> AuditResult:
    inventory_payload = _load_yaml(inventory_rel)
    schemas_payload = _load_yaml(schemas_rel)
    inventory, inventory_source = _inventory_facts(inventory_payload, inventory_rel)
    strict = not _is_legacy_contract(schemas_payload)
    ual_entries, contract_sources, unsafe_impl = _collect_ual_entries(schemas_payload, schemas_rel)

    if allow_runtime_fallback and not strict and not any(entry.kind == "runtime" for entry in ual_entries):
        runtime = _runtime_manifest()
        ual_entries.extend(runtime.entries)

    ual_counts = {mode: 0 for mode in ACCESS_MODE_ORDER}
    ual_counts[UNANNOTATED] = 0
    static_count = 0
    runtime_count = 0
    implicit_read = 0
    missing_access_mode: list[str] = []
    invalid_access_mode: list[str] = []
    access_conflicts: list[str] = []
    bare_names: list[str] = []
    unqualified_names: list[str] = []
    name_counter: Counter[str] = Counter()
    for entry in ual_entries:
        name = entry.name
        name_counter[name] += 1
        if entry.kind == "runtime":
            runtime_count += 1
        else:
            static_count += 1
        if not name or "." not in name:
            unqualified_names.append(name or "<missing-name>")
        for alias in entry.bare_aliases:
            bare_names.append(alias or name or "<missing-name>")
        requires_explicit = strict or entry.kind == "runtime" or entry.source == "runtime_manifest"
        if entry.access_conflict:
            access_conflicts.append(name)
            invalid_access_mode.append(name)
            ual_counts[UNANNOTATED] += 1
        elif entry.access_mode is None:
            if requires_explicit:
                missing_access_mode.append(name)
                ual_counts[UNANNOTATED] += 1
            else:
                implicit_read += 1
                ual_counts["Read"] += 1
        elif entry.access_mode not in ACCESS_MODES:
            invalid_access_mode.append(name)
            ual_counts[UNANNOTATED] += 1
        else:
            ual_counts[entry.access_mode] += 1

    duplicates = tuple(sorted(name for name, count in name_counter.items() if count > 1))
    bare_names = sorted(set(bare_names))
    reimplemented = tuple(sorted(
        name for name in name_counter if name in inventory.qualified_names
    ))
    official_reimplemented_count = len(reimplemented)
    drift_issue_rows: list[tuple[str, str, str]] = []
    drift_issue_rows += _issue_rows("inventory_summary_mismatch", ["<summary>"], "inventory_summary_total_tools_mismatch") if inventory.summary_mismatch else []
    drift_issue_rows += _issue_rows("official_invalid_access_mode", inventory.invalid_modes, "official_access_mode_must_be_Read_Write_Execute_or_unset")
    drift_issue_rows += _issue_rows("official_duplicate_qualified_name", inventory.duplicate_qualified, "official_qualified_name_repeated")
    drift_issue_rows += _issue_rows("duplicate_qualified_name", duplicates, "ual_qualified_name_repeated")
    drift_issue_rows += _issue_rows("missing_access_mode", missing_access_mode, "explicit_Read_Write_or_Execute_required")
    drift_issue_rows += _issue_rows("invalid_access_mode", invalid_access_mode, "accessMode_not_in_allowlist")
    drift_issue_rows += _issue_rows("access_mode_conflict", access_conflicts, "annotation_carriers_disagree")
    drift_issue_rows += _issue_rows("bare_ual_name", bare_names, "bare_ual_wire_alias_forbidden")
    drift_issue_rows += _issue_rows("unqualified_ual_name", unqualified_names, "qualified_wire_name_required")
    drift_issue_rows += _issue_rows("official_reimplemented", reimplemented, "ual_contract_reuses_official_qualified_name")
    drift_issue_rows += _issue_rows("unsafe_extension_impl", unsafe_impl, "extension_impl_must_use_fixed_builtin_allowlist")
    if static_count != EXPECTED_UAL_STATIC_COUNT:
        drift_issue_rows.append(("ual_static_count_mismatch", "<summary>", "ual_static_count_mismatch"))
    if runtime_count != EXPECTED_UAL_RUNTIME_COUNT:
        drift_issue_rows.append(("ual_runtime_count_mismatch", "<summary>", "ual_runtime_count_mismatch"))
    if len(ual_entries) != EXPECTED_UAL_TOTAL_COUNT:
        drift_issue_rows.append(("ual_total_count_mismatch", "<summary>", "ual_total_count_mismatch"))

    drift_count = len(drift_issue_rows)
    result = "PASS" if drift_count == 0 else "FAIL"
    summary: dict[str, Any] = {
        "mode": "check",
        "inventory": _safe_label(inventory_rel, 160),
        "schemas": _safe_label(schemas_rel, 160),
        "official_tool_count": inventory.tool_count,
        "official_read": inventory.counts["Read"],
        "official_write": inventory.counts["Write"],
        "official_execute": inventory.counts["Execute"],
        "official_unannotated": inventory.counts[UNANNOTATED],
        "official_invalid_access_mode": len(inventory.invalid_modes),
        "inventory_summary_mismatch": inventory.summary_mismatch,
        "ual_static_count": static_count,
        "ual_runtime_count": runtime_count,
        "ual_total_count": len(ual_entries),
        "expected_ual_static_count": EXPECTED_UAL_STATIC_COUNT,
        "expected_ual_runtime_count": EXPECTED_UAL_RUNTIME_COUNT,
        "expected_ual_total_count": EXPECTED_UAL_TOTAL_COUNT,
        "ual_read": ual_counts["Read"],
        "ual_write": ual_counts["Write"],
        "ual_execute": ual_counts["Execute"],
        "ual_unannotated": ual_counts[UNANNOTATED],
        "ual_implicit_read": implicit_read,
        "duplicate_qualified_names": len(duplicates) + len(inventory.duplicate_qualified),
        "missing_access_mode": len(missing_access_mode),
        "invalid_access_mode": len(invalid_access_mode) + len(inventory.invalid_modes),
        "access_mode_conflicts": len(access_conflicts),
        "bare_ual_names": len(bare_names),
        "unqualified_ual_names": len(unqualified_names),
        "official_reimplemented": official_reimplemented_count,
        "unsafe_extension_impl": len(unsafe_impl),
        "drift_issues": drift_count,
        "result": result,
    }
    access_rows: tuple[tuple[str, str, int], ...] = tuple(
        [("official", mode, inventory.counts[mode]) for mode in (*ACCESS_MODE_ORDER, UNANNOTATED)]
        + [("ual", mode, ual_counts[mode]) for mode in (*ACCESS_MODE_ORDER, UNANNOTATED)]
    )
    return AuditResult(
        summary=summary,
        sources=(inventory_source, *contract_sources),
        access_modes=access_rows,
        issues=tuple(drift_issue_rows),
        official_qualified_names=inventory.qualified_names,
        official_primary_names=inventory.primary_names,
        official_bare_names=inventory.bare_names,
        ual_names=frozenset(name for name in name_counter if name),
        result=result,
    )


def _render_audit(
    facts: AuditResult,
    *,
    mode: str = "check",
    extra_summary: Mapping[str, Any] | None = None,
    extra_issues: Sequence[tuple[str, str, str]] = (),
    result: str | None = None,
) -> None:
    summary = dict(facts.summary)
    if extra_summary:
        summary.update({str(key): value for key, value in extra_summary.items()})
    summary["mode"] = mode
    final_result = result or facts.result
    summary["result"] = final_result
    rows = list(facts.issues) + list(extra_issues)
    bounded, returned, truncated = _bounded_issues(rows)
    summary["issues_total"] = len(rows)
    summary["issues_returned"] = returned
    summary["issues_truncated"] = truncated
    tables = [
        Table("sources", ["role", "relative_path", "schema_version"], [list(row) for row in facts.sources]),
        Table("access_modes", ["scope", "mode", "count"], [list(row) for row in facts.access_modes]),
        Table("issues", ["category", "name", "detail"], [list(row) for row in bounded]),
    ]
    sys.stdout.write(render_toon(tables, summary))


def _render_error(code: str, *, mode: str = "error") -> None:
    summary = {"mode": mode, "reason": code, "result": "FAIL"}
    tables = [Table("errors", ["code", "detail"], [[code, "offline_audit_failed"]])]
    sys.stdout.write(render_toon(tables, summary))


def _render_help() -> None:
    rows = [
        ["--check", "default_offline_contract_audit"],
        ["--live-url", "loopback_initialize_notifications_initialized_tools_list"],
        ["--enablement-check", "optional_ModelContextProtocol_NoRedist_closure"],
        ["--inventory", "repository_relative_inventory_yaml"],
        ["--schemas", "repository_relative_ual_tool_contract_yaml"],
    ]
    sys.stdout.write(render_toon(
        [Table("options", ["option", "description"], rows)],
        {"mode": "help", "result": "PASS"},
    ))


def _validate_live_url(url: str) -> None:
    parts = urlsplit(str(url or ""))
    if (
        str(url) not in ALLOWED_LIVE_URLS
        or parts.scheme != "http"
        or parts.netloc not in {"localhost:8000", "127.0.0.1:8000"}
        or parts.path != "/mcp"
        or parts.query
        or parts.fragment
        or parts.username
        or parts.password
    ):
        raise AuditError("rejected_non_loopback")


def _header(headers: Any, name: str) -> str:
    if headers is None:
        return ""
    try:
        return str(headers.get(name) or headers.get(name.lower()) or "")
    except AttributeError:
        return ""


def _open_url(request: Any, timeout: float) -> Any:
    """Open one loopback request with proxies and redirects disabled."""
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    return opener.open(request, timeout=timeout)


def _decode_jsonrpc(raw: bytes, content_type: str) -> dict[str, Any]:
    """Decode one bounded JSON or SSE response as data; never print raw bytes."""
    text = raw.decode("utf-8", errors="replace")
    sse = "text/event-stream" in content_type.casefold() or text.lstrip().startswith(("event:", "data:"))
    candidates: list[str] = []
    if sse:
        data_lines = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        candidates.append("\n".join(data_lines))
        candidates.extend(data_lines)
    else:
        candidates.append(text)
    for candidate in candidates:
        if not candidate.strip():
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping) and "error" not in payload:
            return dict(payload)
        if isinstance(payload, Mapping) and "error" in payload:
            raise AuditError("live_jsonrpc_error")
        if isinstance(payload, list):
            found = next((dict(item) for item in payload if isinstance(item, Mapping) and "result" in item), None)
            if found is not None:
                return found
    raise AuditError("live_response_invalid")


def _post_json(
    url: str,
    payload: Mapping[str, Any],
    *,
    session_id: str = "",
    protocol_version: str = PROTOCOL_VERSION,
    expect_result: bool = True,
) -> tuple[dict[str, Any], str]:
    import urllib.request

    data = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": protocol_version,
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    response = _open_url(request, LIVE_TIMEOUT_SECONDS)
    try:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        content_type = _header(getattr(response, "headers", {}), "Content-Type")
        session_header = _header(getattr(response, "headers", {}), "Mcp-Session-Id")
    finally:
        try:
            response.close()
        except Exception:
            pass
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AuditError("live_response_too_large")
    if not raw.strip():
        if expect_result:
            raise AuditError("live_response_invalid")
        return {}, session_header
    envelope = _decode_jsonrpc(raw, content_type)
    if not expect_result:
        return {}, session_header
    result = envelope.get("result")
    if not isinstance(result, Mapping):
        raise AuditError("live_response_invalid")
    return dict(result), session_header


def _live_tools_list(url: str) -> list[str]:
    _validate_live_url(url)
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "ual-engine-toolset-audit", "version": "1"},
        },
    }
    init_result, session_id = _post_json(url, initialize)
    supported_versions = {"2024-11-05", "2025-06-18", "2025-11-25", "2026-07-28"}
    requested_version = str(init_result.get("protocolVersion") or PROTOCOL_VERSION)
    negotiated = requested_version if requested_version in supported_versions else PROTOCOL_VERSION
    notification = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }
    _post_json(
        url, notification, session_id=session_id,
        protocol_version=negotiated, expect_result=False,
    )
    names: list[str] = []
    cursor = ""
    for page in range(MAX_LIVE_PAGES):
        params: dict[str, Any] = {}
        if cursor:
            params["cursor"] = cursor
        request = {
            "jsonrpc": "2.0",
            "id": 2 + page,
            "method": "tools/list",
            "params": params,
        }
        result, _ = _post_json(
            url, request, session_id=session_id,
            protocol_version=negotiated, expect_result=True,
        )
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise AuditError("live_tools_invalid")
        for item in tools:
            if isinstance(item, Mapping):
                name = str(item.get("name") or "").strip()
                if name:
                    names.append(name)
        cursor = str(result.get("nextCursor") or "")
        if len(names) >= MAX_LIVE_NAMES:
            raise AuditError("live_response_too_large")
        if not cursor:
            break
        cursor = _safe_label(cursor, 128)
    else:
        raise AuditError("live_response_too_large")
    return names[:MAX_LIVE_NAMES]


def _run_live(inventory_rel: str, schemas_rel: str, allow_runtime_fallback: bool, url: str) -> int:
    _validate_live_url(url)
    facts = _audit_contract(inventory_rel, schemas_rel, allow_runtime_fallback)
    try:
        names = _live_tools_list(url)
    except AuditError as exc:
        reason = exc.code
    except Exception:
        reason = "live_unavailable"
    else:
        reason = ""
    if reason:
        _render_audit(
            facts, mode="live_url",
            extra_summary={"live_scope": "loopback", "reason": reason, "live_result": "FAIL"},
            extra_issues=[("live_error", "", reason)],
            result="FAIL",
        )
        return 1
    live_counter = Counter(names)
    live_duplicates = sorted(name for name, count in live_counter.items() if count > 1)
    live_bare = sorted({name for name in names if _BARE_UAL_RE.search(name)})
    ual_live = {name for name in names if name.startswith("UEAssetLens.")}
    official_live = [name for name in names if not name.startswith("UEAssetLens.")]
    known_official = 0
    unknown_official: set[str] = set()
    for name in official_live:
        suffix = name.rsplit(".", 1)[-1]
        if name in facts.official_qualified_names or suffix in facts.official_bare_names:
            known_official += 1
        else:
            unknown_official.add(name)
    unknown_official_rows = sorted(unknown_official)
    live_names = set(names)
    live_suffixes = {name.rsplit(".", 1)[-1] for name in official_live}
    inventory_missing = sorted(
        name for name in facts.official_primary_names
        if name not in live_names and name.rsplit(".", 1)[-1] not in live_suffixes
    )
    ual_missing = sorted(facts.ual_names - ual_live)
    ual_unexpected = sorted(ual_live - facts.ual_names)
    drift_rows: list[tuple[str, str, str]] = []
    drift_rows += _issue_rows("live_duplicate_name", live_duplicates, "tools_list_name_repeated")
    drift_rows += _issue_rows("live_bare_ual_name", live_bare, "bare_ual_wire_alias_forbidden")
    drift_rows += _issue_rows("ual_contract_missing_from_live", ual_missing, "contract_tool_absent_from_tools_list")
    drift_rows += _issue_rows("ual_contract_unexpected_in_live", ual_unexpected, "tools_list_ual_tool_absent_from_contract")
    if not names:
        drift_rows.append(("live_empty_tools_list", "", "tools_list_returned_no_names"))
    reported_rows: list[tuple[str, str, str]] = []
    reported_rows += _issue_rows("live_unknown_official_name", unknown_official_rows, "reported_only_host_meta_or_content_tool")
    reported_rows += _issue_rows("inventory_missing_from_live", inventory_missing, "reported_only_dynamic_toolset_not_enabled")
    live_result = "FAIL" if facts.result == "FAIL" or drift_rows else "PASS"
    extra_summary = {
        "live_scope": "loopback",
        "live_count": len(names),
        "live_official_known": known_official,
        "live_official_unknown": len(unknown_official_rows),
        "inventory_missing_from_live": len(inventory_missing),
        "ual_live_count": len(ual_live),
        "ual_contract_count": len(facts.ual_names),
        "ual_contract_missing_from_live": len(ual_missing),
        "ual_contract_unexpected_in_live": len(ual_unexpected),
        "live_duplicate_names": len(live_duplicates),
        "live_bare_ual_names": len(live_bare),
        "live_drift_issues": len(drift_rows),
        "live_result": live_result,
    }
    _render_audit(
        facts, mode="live_url", extra_summary=extra_summary,
        extra_issues=drift_rows + reported_rows, result=live_result,
    )
    return 0 if live_result == "PASS" else 1


def _enablement_facts() -> tuple[dict[str, Any], tuple[tuple[str, str, str], ...], list[list[Any]]]:
    project = _load_json(DEFAULT_UPROJECT)
    descriptor = _load_json(PLUGIN_DESCRIPTOR)
    plugins = project.get("Plugins") if isinstance(project.get("Plugins"), list) else []
    mcp_entries = [
        item for item in plugins
        if isinstance(item, Mapping) and str(item.get("Name") or "") == "ModelContextProtocol"
    ]
    entry = dict(mcp_entries[0]) if len(mcp_entries) == 1 else {}
    targets = entry.get("TargetAllowList")
    targets_list = [str(item) for item in targets] if isinstance(targets, list) else []
    mcp_enabled = entry.get("Enabled") is True
    editor_only = len(targets_list) > 0 and set(targets_list) == {"Editor"}
    project_optional = entry.get("Optional") is True
    deps = descriptor.get("Plugins") if isinstance(descriptor.get("Plugins"), list) else []
    optional_dependency = any(
        isinstance(item, Mapping)
        and str(item.get("Name") or "") == "ModelContextProtocol"
        and item.get("Optional") is True
        for item in deps
    )
    modules = descriptor.get("Modules") if isinstance(descriptor.get("Modules"), list) else []
    mcp_modules = [
        item for item in modules
        if isinstance(item, Mapping) and "UEAssetLensMCP" in str(item.get("Name") or "")
    ]
    mcp_module_editor_only = bool(mcp_modules) and all(
        str(item.get("Type") or "").casefold() == "editor" for item in mcp_modules
    )
    mcp_optional = project_optional or optional_dependency
    no_redist_required = mcp_enabled and editor_only and mcp_optional and mcp_module_editor_only
    result = "PASS" if len(mcp_entries) == 1 and no_redist_required else "FAIL"
    summary = {
        "mode": "enablement_check",
        "uproject": _safe_label(DEFAULT_UPROJECT, 160),
        "plugin_descriptor": _safe_label(PLUGIN_DESCRIPTOR, 160),
        "mcp_entries": len(mcp_entries),
        "mcp_enabled": mcp_enabled,
        "mcp_optional": mcp_optional,
        "editor_only": editor_only,
        "mcp_module_editor_only": mcp_module_editor_only,
        "no_redist_required": no_redist_required,
        "result": result,
    }
    sources = (
        ("uproject", _safe_label(DEFAULT_UPROJECT, 160), "project"),
        ("plugin_descriptor", _safe_label(PLUGIN_DESCRIPTOR, 160), "plugin"),
    )
    rows: list[list[Any]] = [
        ["project_entry", mcp_enabled, "development_only_enablement"],
        ["target_allowlist", editor_only, "Editor_target_allowlist_required"],
        ["optional_dependency", mcp_optional, "soft_dependency_not_bundled"],
        ["mcp_module_scope", mcp_module_editor_only, "Editor_module_only"],
        ["no_redist_required", no_redist_required, "Experimental_NoRedist_not_redistributed"],
    ]
    return summary, sources, rows


def _run_enablement_check() -> int:
    summary, sources, rows = _enablement_facts()
    sys.stdout.write(render_toon(
        [
            Table("sources", ["role", "relative_path", "kind"], [list(row) for row in sources]),
            Table("enablement", ["key", "value", "detail"], rows),
        ],
        summary,
    ))
    return 0 if summary["result"] == "PASS" else 1


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # noqa: ARG002
        _render_error("argument_error")
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description="Offline engine toolset and UAL contract audit", add_help=False)
    parser.add_argument("--help", action="store_true")
    parser.add_argument("--check", action="store_true", help="default offline contract audit")
    parser.add_argument("--live-url", help="loopback MCP tools/list audit")
    parser.add_argument("--enablement-check", action="store_true", help="optional MCP NoRedist enablement closure")
    parser.add_argument("--inventory", default=DEFAULT_INVENTORY, help="repository-relative inventory YAML")
    parser.add_argument("--schemas", default=DEFAULT_SCHEMAS, help="repository-relative UAL tool contract YAML")
    args = parser.parse_args(argv)
    if args.help:
        _render_help()
        return 0
    try:
        inventory_rel = _relative_arg(args.inventory)
        schemas_rel = _relative_arg(args.schemas)
        mode_count = int(args.live_url is not None) + int(args.enablement_check) + int(args.check)
        if mode_count > 1:
            raise AuditError("conflicting_modes")
        if args.enablement_check:
            return _run_enablement_check()
        allow_runtime_fallback = schemas_rel == DEFAULT_SCHEMAS
        if args.live_url is not None:
            return _run_live(inventory_rel, schemas_rel, allow_runtime_fallback, args.live_url)
        facts = _audit_contract(inventory_rel, schemas_rel, allow_runtime_fallback)
        _render_audit(facts)
        return 0 if facts.result == "PASS" else 1
    except AuditError as exc:
        _render_error(exc.code)
        return 1
    except Exception:
        _render_error("internal_error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
