"""Offline resource gateway for the UAL edit-transaction layer.

The gateway never calls Unreal Engine.  It resolves the official tool whitelist
from the four-layer ``gateway`` settings namespace (shipped defaults live in
``Config/UAL/gateway/official_tools.yaml``), validates operations deterministically
and, when a host supplies one, delegates to an injected Python executor.

Design rules enforced here:

* tool names are the exact *bare* official UFUNCTION names from settings YAML;
  qualified MCP names belong to the host ToolsetRegistry and are never guessed;
* no local absolute paths, timestamps, random identifiers or non-``/Game``
  asset paths enter an operation, and therefore never enter a persisted artifact;
* YAML is only ever read with ``yaml.safe_load`` (in ``settings`` / callers),
  and TOON/YAML output is only ever emitted by ``toon.render_toon`` /
  ``artifacts.canonical_yaml``;
* a missing executor is an offline deferral, never an engine call.
"""
from __future__ import annotations

import copy
import inspect
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import artifacts, settings, toon

NAMESPACE = "gateway"
WHITELIST_FIELDS = ("name", "toolset", "accessMode", "inverse", "risk")
EXECUTION_FIELDS = (
    "id", "tool", "accessMode", "status", "dry_run",
    "arguments_sha256", "result_status", "reason",
)
MAX_ROWS = 500
MAX_TEXT = 160

_SCALAR_TYPES = (str, int, float, bool, type(None))
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WIRE_NAME_RE = re.compile(r"^ual_[A-Za-z0-9_]+$")
_WINDOWS_ABS_RE = re.compile(r"[A-Za-z]:[\\/][^\s\"',]+")
_POSIX_ABS_RE = re.compile(r"(?<![\w])/(?!Game(?:/|$))[^\s\"',)\]]+")
_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)?\b|\b\d{2}:\d{2}:\d{2}\b"
)
_RANDOM_ID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b|\b[0-9a-fA-F]{32}\b"
)
_FORBIDDEN_KEY_RE = re.compile(
    r"(?i)(?:^|_)(?:timestamp|timestamps|updated_at|created_at|modified_at|"
    r"generated_at|datetime|date|time|uuid|guid|session_id|request_id|"
    r"trace_id|correlation_id|random|random_id|nonce|client_id|run_id|job_id|"
    r"local_path|absolute_path|host_path|file_path|code|script|eval|exec|"
    r"handler|impl|callable|pickle)(?:$|_)"
)
_ASSET_PATH_KEY_RE = re.compile(
    r"(?i)(?:^|_)(?:asset_paths?|source_path|target_path|destination_path|"
    r"folder_path|material_path|collection_path|object_path|package_path)(?:$|_)"
)
_GAME_PATH_RE = re.compile(r"^/Game(?:/[A-Za-z0-9_.\-]+)*/?$")
_BASELINE_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INVERSE_MODES = frozenset({"restore_baseline", "idempotent", "refuse"})
_INVERSE_SOURCES = frozenset({"baseline", "arguments", "constant"})


def _normalise_inverse_spec(raw: Any) -> dict:
    """Validate one declarative inverse spec; absent specs fail closed."""
    if not isinstance(raw, Mapping):
        return {"mode": "refuse", "reason": "inverse_spec_missing"}
    mode = str(raw.get("mode") or "refuse").strip()
    if mode not in _INVERSE_MODES:
        raise ValueError("gateway inverse mode is invalid")
    spec: dict[str, Any] = {
        "mode": mode,
        "reason": bounded_text(raw.get("reason"), 80),
        "require_baseline_keys": [
            str(item) for item in list(raw.get("require_baseline_keys") or [])
            if isinstance(item, str) and _BASELINE_KEY_RE.match(item)
        ],
        "expect_baseline": copy.deepcopy(dict(raw.get("expect_baseline") or {})),
        "argument_map": {},
    }
    raw_map = raw.get("argument_map")
    if not isinstance(raw_map, Mapping):
        raw_map = {}
    for key, reference in raw_map.items():
        if not isinstance(key, str) or not _BASELINE_KEY_RE.match(key):
            raise ValueError("gateway inverse argument key is invalid")
        if not isinstance(reference, Mapping):
            raise ValueError("gateway inverse argument reference is invalid")
        source = str(reference.get("source") or "")
        if source not in _INVERSE_SOURCES:
            raise ValueError("gateway inverse argument source is invalid")
        spec["argument_map"][key] = copy.deepcopy(dict(reference))
    return spec


def _inverse_reference(
    reference: Mapping[str, Any],
    *,
    arguments: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> Any:
    """Resolve one declarative inverse argument; never evaluates YAML as code."""
    source = str(reference.get("source") or "")
    if source == "constant":
        return copy.deepcopy(reference.get("value"))
    table = baseline if source == "baseline" else arguments
    key = str(reference.get("key") or "")
    if not key or key not in table:
        raise KeyError(key)
    return copy.deepcopy(table[key])


def _roundtrip_baseline(operation: Mapping[str, Any], arguments: Mapping[str, Any]) -> dict:
    """Bounded explicit state for inverting an already-generated inverse op."""
    raw_baseline = operation.get("baseline")
    baseline = copy.deepcopy(dict(raw_baseline)) if isinstance(raw_baseline, Mapping) else {}
    original = operation.get("arguments")
    original_args = dict(original) if isinstance(original, Mapping) else {}
    paths = sorted({
        str(item) for item in _iter_inverse_strings(original_args)
        if _GAME_PATH_RE.match(str(item))
    })
    first = paths[0] if paths else ""
    baseline.update({
        "asset": first,
        "assets": paths,
        "source": first,
        "destination": paths[-1] if len(paths) > 1 else first,
        "folder": first,
        "collection": first,
        "material": "",
        "tags": copy.deepcopy(original_args.get("tags") or []),
        "metadata": copy.deepcopy(original_args.get("metadata") or {}),
        "properties": copy.deepcopy(original_args.get("properties") or {}),
        "enabled": False,
        "transform": {},
        "parameter_value": None,
        "editor_open": False,
        "folder_exists": False,
        "asset_exists": False,
        "destination_exists": False,
        "collection_exists": False,
    })
    for key in ("asset_paths", "asset_path", "source_path", "destination_path"):
        value = arguments.get(key)
        if isinstance(value, str) and _GAME_PATH_RE.match(value):
            baseline["asset"] = value
            baseline["assets"] = [value]
            break
    return {
        str(key): copy.deepcopy(value)
        for key, value in baseline.items()
        if _BASELINE_KEY_RE.match(str(key))
    }


def _iter_inverse_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        out: list[str] = []
        for item in value.values():
            out.extend(_iter_inverse_strings(item))
        return out
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        out = []
        for item in value:
            out.extend(_iter_inverse_strings(item))
        return out
    return []


def bounded_limit(value: Any, default: int = 100, maximum: int = MAX_ROWS) -> int:
    """Clamp a caller-supplied row limit into a deterministic bounded range."""
    try:
        size = int(value)
    except (TypeError, ValueError):
        size = default
    return max(1, min(size, maximum))


def has_forbidden_text(text: Any) -> bool:
    """True for local paths, timestamps, random ids or bare ``ual_*`` wire names."""
    if not isinstance(text, str):
        return False
    return any(
        pattern.search(text)
        for pattern in (
            _WINDOWS_ABS_RE, _POSIX_ABS_RE, _TIMESTAMP_RE, _RANDOM_ID_RE,
            _WIRE_NAME_RE,
        )
    ) or "\\" in text or "file://" in text.lower()


def bounded_text(value: Any, limit: int = MAX_TEXT) -> str:
    """Deterministic, redacted scalar for TOON/YAML payloads."""
    if value is None:
        text = ""
    elif isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)
    text = artifacts.canonicalize_text(text)
    text = _POSIX_ABS_RE.sub("<non-game-path>", text)
    text = _RANDOM_ID_RE.sub("<random-id>", text)
    text = _WIRE_NAME_RE.sub("<wire-name>", text)
    text = "".join(ch for ch in text if ord(ch) >= 0x20 or ch == "\t")
    text = text.replace("\r", " ").replace("\n", " ").strip()
    try:
        size = max(1, min(int(limit), 4096))
    except (TypeError, ValueError):
        size = MAX_TEXT
    return text[:size]


def _walk_payload(node: Any, *, key_name: str = "") -> tuple[bool, str]:
    """Recursively reject forbidden keys/values and non-data Python objects."""
    if isinstance(node, Mapping):
        for key, value in node.items():
            if (
                not isinstance(key, str)
                or not _KEY_RE.match(key)
                or _FORBIDDEN_KEY_RE.search(key)
                or has_forbidden_text(key)
            ):
                return False, "operation contains a forbidden key"
            ok, reason = _walk_payload(value, key_name=key)
            if not ok:
                return False, reason
        return True, "ok"
    if isinstance(node, (list, tuple)):
        for item in node:
            ok, reason = _walk_payload(item, key_name=key_name)
            if not ok:
                return False, reason
        return True, "ok"
    if isinstance(node, _SCALAR_TYPES):
        if isinstance(node, str):
            if has_forbidden_text(node):
                return False, "operation contains a forbidden value"
            if _ASSET_PATH_KEY_RE.search(key_name) and not _GAME_PATH_RE.match(node):
                return False, "operation contains a non-/Game asset path"
        return True, "ok"
    return False, "operation values must be scalars, lists or mappings"


def arguments_digest(arguments: Any) -> str:
    """Stable hash of validated arguments; never used for deduplication."""
    try:
        return artifacts.digest(dict(arguments) if isinstance(arguments, Mapping) else {})
    except Exception:
        return ""


def _safe_result_value(value: Any) -> Any:
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, str):
        return bounded_text(value)
    if isinstance(value, Mapping):
        return {
            bounded_text(key, 80): _safe_result_value(item)
            for key, item in list(value.items())[:20]
        }
    if isinstance(value, (list, tuple)):
        return [_safe_result_value(item) for item in list(value)[:20]]
    return bounded_text(value)


def _invoke_executor(
    executor: Callable[..., Any], tool: str, arguments: dict, operation: dict
) -> Any:
    """Support the host convention ``executor(tool, arguments)`` and ``executor(op)``."""
    try:
        signature = inspect.signature(executor)
    except (TypeError, ValueError):
        return executor(tool, arguments)
    try:
        signature.bind(tool, arguments)
    except TypeError:
        return executor(operation)
    return executor(tool, arguments)


class Gateway:
    """Immutable whitelist plus offline validation/delegation behaviour."""

    def __init__(
        self,
        entries: Sequence[Mapping[str, Any]],
        *,
        version: int = 1,
        project_root: Path | None = None,
    ) -> None:
        self._entries = [dict(entry) for entry in entries]
        self._by_name = {entry["name"]: entry for entry in self._entries}
        self.version = version
        self.project_root = Path(project_root) if project_root is not None else None
        self.whitelist_sha256 = artifacts.digest(self._entries)

    def spec(self, tool: str) -> dict:
        """Copy of one whitelist entry (``{}`` when the tool is not whitelisted)."""
        return dict(self._by_name.get(str(tool), {}))

    def tools(self, limit: int = 100) -> list[dict]:
        """Bounded whitelist rows; ``accessMode`` and ``access_mode`` are synonyms."""
        return [dict(entry) for entry in self._entries[: bounded_limit(limit)]]

    def render_whitelist(self, limit: int = 100) -> str:
        """Bounded TOON view of the whitelist; never emits MCP wire names."""
        rows = self.tools(limit)
        total = len(self._entries)
        summary = {
            "version": self.version,
            "whitelist_sha256": self.whitelist_sha256,
            "returned": len(rows),
            "total": total,
            "truncated": len(rows) < total,
        }
        table = toon.Table(
            "official_tools",
            WHITELIST_FIELDS,
            [[bounded_text(row[field], 80) for field in WHITELIST_FIELDS] for row in rows],
        )
        return toon.render_toon([table], summary)

    def validate(self, operation: dict) -> tuple[bool, str]:
        """Validate an operation without executing it. Returns ``(ok, reason)``."""
        if not isinstance(operation, Mapping):
            return False, "operation must be a mapping"
        tool = operation.get("tool")
        if not isinstance(tool, str) or not tool:
            return False, "tool must be a bare official tool name"
        if _WIRE_NAME_RE.match(tool) or "." in tool:
            return False, "MCP wire names are not gateway tool names"
        entry = self._by_name.get(tool)
        if entry is None:
            return False, "tool is not in the gateway whitelist"
        if not isinstance(operation.get("arguments"), Mapping):
            return False, "arguments must be a mapping"
        declared = operation.get("accessMode", operation.get("access_mode"))
        if declared is not None and str(declared) != entry["accessMode"]:
            return False, "accessMode does not match the whitelist"
        inverse = operation.get("inverse")
        if inverse is not None and str(inverse) not in self._by_name:
            return False, "inverse tool is not in the gateway whitelist"
        ok, reason = _walk_payload(dict(operation))
        return (True, "ok") if ok else (False, reason)

    def inverse_spec(self, tool: str) -> dict:
        """Copy of one declarative inverse spec; unknown tools fail closed."""
        entry = self._by_name.get(str(tool))
        if entry is None:
            return {"mode": "refuse", "reason": "tool_not_whitelisted"}
        return copy.deepcopy(dict(entry.get("inverse_spec") or {}))

    def inverse(self, operation: dict) -> dict:
        """Restore a bounded baseline, or refuse nonrecoverable operations."""
        ok, reason = self.validate(operation)
        if not ok:
            raise ValueError(f"cannot invert an invalid operation: {reason}")
        source = self._by_name[operation["tool"]]
        target_name = str(source["inverse"])
        target = self._by_name.get(target_name)
        if target is None:
            raise ValueError("inverse tool is not in the gateway whitelist")
        spec = self.inverse_spec(str(source["name"]))
        mode = str(spec.get("mode") or "refuse")
        if mode == "refuse":
            raise ValueError(
                f"inverse operation is not recoverable: {source['name']} "
                f"({bounded_text(spec.get('reason'), 80) or 'baseline_unavailable'})"
            )
        raw_baseline = operation.get("baseline")
        baseline = copy.deepcopy(dict(raw_baseline)) if isinstance(raw_baseline, Mapping) else {}
        for key in spec.get("require_baseline_keys") or []:
            if key not in baseline:
                raise ValueError(
                    f"inverse operation requires baseline key: {source['name']}.{key}"
                )
        for key, expected in dict(spec.get("expect_baseline") or {}).items():
            if baseline.get(str(key)) != expected:
                raise ValueError(
                    f"inverse operation baseline is not recoverable: {source['name']}.{key}"
                )
        args = operation.get("arguments")
        arguments = copy.deepcopy(dict(args)) if isinstance(args, Mapping) else {}
        inverse_arguments: dict[str, Any] = {}
        for key, reference in dict(spec.get("argument_map") or {}).items():
            try:
                value = _inverse_reference(
                    reference, arguments=arguments, baseline=baseline
                )
            except KeyError:
                raise ValueError(
                    f"inverse operation baseline is incomplete: {source['name']}.{key}"
                ) from None
            if value is None:
                raise ValueError(
                    f"inverse operation baseline is incomplete: {source['name']}.{key}"
                )
            if _ASSET_PATH_KEY_RE.search(key):
                if isinstance(value, str) and (not value or not _GAME_PATH_RE.match(value)):
                    raise ValueError(
                        f"inverse operation requires a /Game path: {source['name']}.{key}"
                    )
                if isinstance(value, (list, tuple)) and (
                    not value or any(
                        not isinstance(item, str) or not _GAME_PATH_RE.match(item)
                        for item in value
                    )
                ):
                    raise ValueError(
                        f"inverse operation requires /Game paths: {source['name']}.{key}"
                    )
            inverse_arguments[key] = value
        op_id = bounded_text(operation.get("id"), 80)
        inverse_op = {
            "id": f"{op_id}.inverse" if op_id else "inverse",
            "tool": target_name,
            "arguments": inverse_arguments,
            "accessMode": target["accessMode"],
            "inverse": source["name"],
            "risk": target["risk"],
            "baseline": _roundtrip_baseline(operation, inverse_arguments),
        }
        inverse_ok, inverse_reason = self.validate(inverse_op)
        if not inverse_ok:
            raise ValueError(f"inverse operation rejected: {inverse_reason}")
        return inverse_op

    def execute(
        self,
        operation: dict,
        *,
        dry_run: bool = True,
        executor: Callable[..., Any] | None = None,
    ) -> dict:
        """Validate, then delegate to an injected Python executor. Never calls UE."""
        ok, reason = self.validate(operation)
        tool = operation.get("tool") if isinstance(operation, Mapping) else None
        arguments = (
            dict(operation.get("arguments") or {})
            if isinstance(operation, Mapping)
            and isinstance(operation.get("arguments"), Mapping)
            else {}
        )
        result: dict = {
            "id": bounded_text(operation.get("id") if isinstance(operation, Mapping) else "", 80),
            "tool": bounded_text(tool, 80),
            "accessMode": self.spec(str(tool or ""))["accessMode"]
            if self.spec(str(tool or ""))
            else "",
            "arguments_sha256": arguments_digest(arguments),
            "dry_run": bool(dry_run),
            "status": "rejected",
            "reason": bounded_text(reason, MAX_TEXT),
            "result": {},
            "result_status": "",
        }
        if not ok:
            return result
        result["reason"] = ""
        if dry_run:
            result["status"] = "dry_run"
            return result
        if executor is None:
            result["status"] = "deferred_offline"
            return result
        try:
            raw = _invoke_executor(executor, str(tool), arguments, dict(operation))
        except Exception as exc:  # host executor failures are data, not crashes
            result["status"] = "failed"
            result["reason"] = bounded_text(type(exc).__name__, 80)
            return result
        result["status"] = "executed"
        result["result"] = (
            {bounded_text(key, 80): _safe_result_value(value) for key, value in raw.items()}
            if isinstance(raw, Mapping)
            else {"value": _safe_result_value(raw)}
        )
        raw_status = result["result"].get("status", "")
        result["result_status"] = bounded_text(raw_status, 40)
        return result

    def render_execution(self, rows: Any, *, limit: int = 100) -> str:
        """Bounded TOON view of ``execute`` results (pure, no I/O)."""
        if isinstance(rows, Mapping):
            if "status" in rows or "tool" in rows:
                items = [rows]
            else:
                items = rows.get("rows", rows.get("results", rows.get("operations", [])))
        elif isinstance(rows, (list, tuple)):
            items = rows
        elif rows is None:
            items = []
        else:
            items = []
        items = list(items) if isinstance(items, (list, tuple)) else []
        bounded = items[: bounded_limit(limit)]
        summary = {
            "returned": len(bounded),
            "total": len(items),
            "truncated": len(bounded) < len(items),
        }
        table_rows: list[list[Any]] = []
        for item in bounded:
            row = dict(item) if isinstance(item, Mapping) else {}
            inner = row.get("result")
            status = row.get("result_status") or (
                inner.get("status") if isinstance(inner, Mapping) else ""
            )
            table_rows.append(
                [
                    bounded_text(row.get("id"), 80),
                    bounded_text(row.get("tool"), 80),
                    bounded_text(row.get("accessMode"), 32),
                    bounded_text(row.get("status"), 32),
                    bool(row.get("dry_run", False)),
                    bounded_text(row.get("arguments_sha256"), 80),
                    bounded_text(status, 40),
                    bounded_text(row.get("reason"), MAX_TEXT),
                ]
            )
        return toon.render_toon([toon.Table("execution", EXECUTION_FIELDS, table_rows)], summary)


def load_gateway(*, project_root: Path | None = None) -> Gateway:
    """Resolve the whitelist through the four-layer settings namespace."""
    root = Path(project_root) if project_root is not None else None
    resolved = settings.load_namespace(NAMESPACE, project_root=root)
    data = dict(resolved.data or {})
    raw: Any = data.get("tools")
    if raw is None:
        raw = [rule for rule in resolved.rules if isinstance(rule, Mapping) and rule.get("name")]
    if isinstance(raw, Mapping):
        raw = [
            {"name": name, **dict(spec)}
            for name, spec in raw.items()
            if isinstance(spec, Mapping)
        ]
    if not isinstance(raw, list) or not raw:
        raise ValueError("gateway whitelist is empty")
    entries: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        if not name or _WIRE_NAME_RE.match(name) or "." in name or name in seen:
            continue
        inverse = str(item.get("inverse") or name).strip()
        mode = str(item.get("access_mode") or item.get("accessMode") or "Write")
        entries.append(
            {
                "name": name,
                "toolset": bounded_text(item.get("toolset"), 80),
                "accessMode": mode,
                "access_mode": mode,
                "inverse": inverse,
                "risk": bounded_text(item.get("risk"), 32),
                "inverse_spec": _normalise_inverse_spec(item.get("inverse_spec")),
            }
        )
        seen.add(name)
    if not entries:
        raise ValueError("gateway whitelist has no valid tools")
    try:
        version = int(data.get("version", 1))
    except (TypeError, ValueError):
        version = 1
    return Gateway(entries, version=version, project_root=root)
