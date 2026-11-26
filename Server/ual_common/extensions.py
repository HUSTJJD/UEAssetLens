"""Declarative extension manifests -> process-local ExtensionRegistry.

The asset kernel's nine extension namespaces are data, not code. Manifests
live in ``Config/UAL/extensions/*.yaml`` and resolve through the same four
preference layers as policies (builtin < plugin < project < user). A manifest
may only reference the fixed callables in :mod:`ual_common.extensions_builtins`;
resolution is a static name lookup, never a YAML-controlled import.

The loader deliberately has no DuckDB / MCP SDK dependency: ``Tools/gen_docs.py``
uses it in the docs-only CI job to publish a registry snapshot into
``tool_schemas.yaml``. No timestamps, absolute paths or random identifiers are
emitted by this module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from . import extensions_builtins as _builtins
from . import settings as settings_module
from .registry import NAMESPACES, ExtensionRegistry, ExtensionSpec
from .settings import PLUGIN_ROOT, ResolvedSettings, load_namespace


class _ImportlibSentinel:
    """Compatibility sentinel proving YAML never reaches a dynamic importer."""

    @staticmethod
    def import_module(name: str) -> Any:
        raise RuntimeError(f"dynamic module import is forbidden: {name!r}")


# Tests and callers may patch this attribute; no manifest code path calls it.
importlib = _ImportlibSentinel()

EXTENSION_NAMESPACE = "extensions"
# A project-independent sentinel root: builtin manifests must generate exactly
# the same tool catalog on every machine. Host-specific plugin/project/user
# layers are opt-in at server startup, never during docs generation / drift checks.
BUILTIN_ONLY_ROOT = PLUGIN_ROOT / ".ual" / "generator"
BUILTIN_MODULE = "ual_common.extensions_builtins"
BUILTIN_PREFIX = f"{BUILTIN_MODULE}."
BUILTIN_CALLABLES: Mapping[str, Callable[..., Any]] = MappingProxyType({
    "no_draft_names": _builtins.no_draft_names,
    "asset_type_descriptor": _builtins.asset_type_descriptor,
    "field_from_domain": _builtins.field_from_domain,
    "diff_layer": _builtins.diff_layer,
    "lifecycle_hook": _builtins.lifecycle_hook,
    "yaml_sink": _builtins.yaml_sink,
    "fps_3c_template": _builtins.fps_3c_template,
    "official_gateway": _builtins.official_gateway,
    "runtime_tool": _builtins.runtime_tool,
})
ACCESS_MODES = ("Read", "Write", "Execute")
TOOL_NAMESPACE = "tool"

# Keys that would turn declarative data into an executable reference. The one
# exception is a top-level ``impl``, which is validated by _resolve_builtin().
_FORBIDDEN_REFERENCE_KEYS = frozenset({
    "impl", "callable", "eval", "exec", "code", "script", "pickle", "marshal",
    "module", "import", "import_module", "importlib", "loader", "builtins",
    "globals", "locals", "factory", "constructor", "lambda", "system",
    "subprocess", "os", "sys", "executor", "apply", "object",
})
_SAFE_HANDLER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DANGEROUS_HANDLERS = frozenset({
    "eval", "exec", "compile", "open", "system", "popen", "spawn",
    "import_module", "__import__", "getattr", "setattr", "delattr",
})
_FORBIDDEN_TEXT_MARKERS = (
    "!!python/", "tag:yaml.org,2002:python/", "os.system",
    "importlib.import_module", "__import__", "subprocess.",
)


@dataclass(frozen=True)
class ExtensionLoadResult:
    registry: ExtensionRegistry
    resolved: ResolvedSettings
    provenance: tuple[str, ...]


def _resolve_builtin(ref: Any) -> Callable[..., Any]:
    """Resolve one exact name in the single, statically imported builtin module."""
    if not isinstance(ref, str) or not ref.startswith(BUILTIN_PREFIX):
        raise ValueError(
            f"extension impl must be an exact {BUILTIN_MODULE} callable: {ref!r}"
        )
    name = ref[len(BUILTIN_PREFIX):]
    if (
        not name
        or "." in name
        or not name.isidentifier()
        or name.startswith("_")
        or name not in BUILTIN_CALLABLES
    ):
        raise ValueError(f"unknown builtin extension implementation: {ref!r}")
    fn = BUILTIN_CALLABLES[name]
    if (
        not callable(fn)
        or getattr(fn, "__module__", None) != BUILTIN_MODULE
        or getattr(fn, "__qualname__", None) != name
    ):
        raise ValueError(f"extension impl is not a builtin function: {ref!r}")
    return fn


def _validate_handler(value: Any, path: str) -> None:
    if (
        not isinstance(value, str)
        or not _SAFE_HANDLER_RE.fullmatch(value)
        or value.lower() in _DANGEROUS_HANDLERS
    ):
        raise ValueError(f"{path} must be a bare declarative handler name: {value!r}")


def _validate_manifest_references(
    node: Any,
    *,
    path: str,
    root: bool = False,
) -> None:
    """Reject executable reference keys and dotted call strings in all rules."""
    if isinstance(node, Mapping):
        for key, value in node.items():
            key_text = str(key)
            normalized = key_text.lower()
            child_path = f"{path}.{key_text}" if path else key_text
            if root and normalized == "impl":
                if not isinstance(value, str):
                    raise ValueError(f"{child_path} must be a string")
                _resolve_builtin(value)
                continue
            if normalized in _FORBIDDEN_REFERENCE_KEYS:
                raise ValueError(f"{child_path} is an executable reference and is forbidden")
            if normalized == "handler":
                _validate_handler(value, child_path)
            if isinstance(value, str) and any(
                marker in value for marker in _FORBIDDEN_TEXT_MARKERS
            ):
                raise ValueError(f"{child_path} contains a forbidden executable reference")
            _validate_manifest_references(value, path=child_path)
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            _validate_manifest_references(value, path=f"{path}[{index}]")


def _as_tuple(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def load_registry(
    *,
    project_root: Path | None = None,
    plugin_roots: Iterable[Path] = (),
    user_root: Path | None = None,
    namespace: str = EXTENSION_NAMESPACE,
    builtin_only: bool = False,
) -> ExtensionLoadResult:
    """Load enabled offline manifests from the four-layer preference stack."""
    resolved = load_namespace(
        namespace,
        project_root=project_root,
        plugin_roots=plugin_roots,
        user_root=user_root,
        include_plugin_layer=not builtin_only,
        include_project_layers=not builtin_only,
    )
    # Validate every rule, including disabled ones, so an override cannot later
    # activate a malicious executable reference without a fresh load-time error.
    for rule in resolved.rules:
        _validate_manifest_references(rule, path=f"rule[{rule.get('id', '?')}]", root=True)

    registry = ExtensionRegistry()
    for rule in resolved.active_rules():
        if not settings_module.is_active_rule(rule):
            continue
        ns = str(rule.get("namespace", ""))
        if ns not in NAMESPACES:
            raise ValueError(
                f"extension {rule.get('id')!r} has unknown namespace {ns!r}; "
                f"expected one of {NAMESPACES}"
            )
        raw_schema = rule.get("schema") or {}
        raw_config = rule.get("config") or {}
        if not isinstance(raw_schema, Mapping) or not isinstance(raw_config, Mapping):
            raise ValueError(f"extension {rule.get('id')!r} schema/config must be mappings")
        schema = dict(raw_schema)
        config = dict(raw_config)
        if ns == "gateway":
            injection = rule.get(
                "allow_executor_injection", config.get("allow_executor_injection")
            )
            if injection is not None and "allow_executor_injection" not in schema:
                schema["allow_executor_injection"] = bool(injection)
        spec = ExtensionSpec(
            namespace=ns,
            name=str(rule.get("name") or rule["id"]),
            version=str(rule.get("version", "1")),
            priority=int(rule.get("priority", 0)),
            applies_to=_as_tuple(rule.get("applies_to"), ("*",)),
            capabilities=_as_tuple(rule.get("capabilities"), ()),
            schema=schema,
        )
        ref = rule.get("impl")
        impl: Any = _resolve_builtin(ref) if ref is not None else config
        registry.register(spec, impl=impl)
    return ExtensionLoadResult(
        registry=registry,
        resolved=resolved,
        provenance=tuple(sorted(resolved.provenance.values())),
    )


def load_builtin_registry() -> ExtensionLoadResult:
    """Load only shipped manifests; used by docs generation and drift gates."""
    return load_registry(builtin_only=True)


def tool_defs_from_registry(registry: ExtensionRegistry) -> list[dict[str, Any]]:
    """Publish ``tool`` namespace entries as generated MCP tool contracts."""
    defs: list[dict[str, Any]] = []
    for spec, _impl in registry.resolve("*", namespace=TOOL_NAMESPACE):
        schema = dict(spec.schema)
        mcp_name = str(schema.get("mcp_name", ""))
        if not mcp_name.startswith("UEAssetLens."):
            raise ValueError(
                f"runtime tool {spec.key} must use a qualified UEAssetLens name: {mcp_name!r}"
            )
        access_mode = str(schema.get("accessMode", "Execute"))
        if access_mode not in ACCESS_MODES:
            raise ValueError(
                f"runtime tool {spec.key} has invalid accessMode {access_mode!r}"
            )
        input_schema = dict(schema.get("inputSchema") or {"type": "object", "properties": {}})
        if input_schema.get("type") != "object":
            raise ValueError(f"runtime tool {spec.key} inputSchema.type must be object")
        handler = str(schema.get("handler") or spec.name)
        defs.append({
            "name": handler,
            "mcp_name": mcp_name,
            "description": str(schema.get("description") or f"UAL runtime extension {spec.key}"),
            "inputSchema": input_schema,
            "accessMode": access_mode,
            "readOnlyHint": access_mode == "Read",
            "handler": handler,
            "extension": spec.key,
        })
    return sorted(defs, key=lambda item: item["mcp_name"])


def validate_tool_names(defs: Iterable[dict[str, Any]]) -> None:
    """CI guard: qualified names, explicit AIAccessMode, no bare ual_* aliases."""
    seen: set[str] = set()
    for tool in defs:
        name = str(tool.get("mcp_name", ""))
        if not name.startswith("UEAssetLens."):
            raise ValueError(f"tool name is not qualified: {name!r}")
        if name.startswith("ual_") or name.lower().startswith("ual_"):
            raise ValueError(f"bare ual_* tool names are forbidden: {name!r}")
        if tool.get("accessMode") not in ACCESS_MODES:
            raise ValueError(f"tool {name} is missing an explicit AIAccessMode")
        if name in seen:
            raise ValueError(f"duplicate MCP tool name: {name}")
        seen.add(name)


def render_registry_snapshot(registry: ExtensionRegistry, *, limit: int = 100) -> str:
    """Bounded, deterministic TOON view for CLI / MCP consumption."""
    from .toon import Table, render_toon

    snapshot = registry.snapshot()[: max(1, min(int(limit or 100), 500))]
    rows = [[
        item["key"], item["namespace"], item["version"], item["priority"],
        "|".join(item["applies_to"]), "|".join(item["capabilities"]),
    ] for item in snapshot]
    return render_toon(
        [Table(
            "extensions",
            ["key", "namespace", "version", "priority", "applies_to", "capabilities"],
            rows,
        )],
        summary={
            "total": len(registry.snapshot()),
            "returned": len(rows),
            "truncated": "true" if len(registry.snapshot()) > len(rows) else "false",
        },
    )
