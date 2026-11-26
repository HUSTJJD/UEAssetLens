"""Pure builtin implementations referenced by declarative extension manifests.

Every callable in this module is deliberately offline and deterministic.  The
loader only resolves names under this module, so a project/user YAML manifest
cannot execute arbitrary code.  No VLM, network, GPU, clock, random ID, local
absolute path or DuckDB connection is used here.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from .artifacts import canonical_yaml as canonical_text
from .artifacts import digest, sha256_text, write_artifact

_FORBIDDEN_PATH_FRAGMENTS = ("..", ":", "\\")
_RESOURCE_REQUIREMENT_FLAGS = (
    "requires_gpu", "requires_cloud", "requires_vlm", "requires_network",
)
_EXTERNAL_CAPABILITIES = frozenset({"external_disabled", "external_generative"})


def no_draft_names(asset: dict[str, Any], rule: dict[str, Any]) -> list[str]:
    """Custom policy predicate used by `kind: custom` rules."""
    path = str(asset.get("asset_path", ""))
    forbidden = [str(item) for item in (rule.get("forbidden") or ("Draft", "WIP", "Temp"))]
    hits = [token for token in forbidden if token.lower() in path.lower()]
    if not hits:
        return []
    message = str(rule.get("message") or "draft/WIP assets are not publishable")
    return [f"{message}: {','.join(hits)}"]


def asset_type_descriptor(context: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Resolve role / assembly hints from declarative class patterns."""
    asset = context.get("asset") or {}
    asset_class = str(asset.get("asset_class", ""))
    schema = spec.get("schema") or {}
    role = str(schema.get("default_role") or "unclassified")
    for item in schema.get("roles") or []:
        pattern = str(item.get("match") or "")
        if pattern and pattern.lower() in asset_class.lower():
            role = str(item.get("role") or role)
            break
    relations = []
    for item in schema.get("assembly") or []:
        if str(item.get("role") or "") == role:
            relations.append({
                "relation": str(item.get("relation") or "assembles_with"),
                "target_role": str(item.get("target_role") or "unclassified"),
            })
    return {"role": role, "assembly": sorted(relations, key=lambda row: (row["relation"], row["target_role"]))}


def field_from_domain(context: dict[str, Any], spec: dict[str, Any]) -> Any:
    """Extract one raw typed-field value from an already-projected asset record."""
    record = context.get("record") or {}
    schema = spec.get("schema") or {}
    source = str(schema.get("source") or "domain_spec")
    key = str(schema.get("key") or spec.get("name") or "")
    values = dict(record.get(source) or {})
    if key in values:
        return values[key]
    return schema.get("default")


def _normalize_scalar(value: Any) -> Any:
    if isinstance(value, str):
        text = re.sub(r"[A-Za-z]:[\\/][^\s]+", "<local-path>", value)
        text = re.sub(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b", "<guid>", text)
        text = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*", "<timestamp>", text)
        text = "".join(ch for ch in text if ord(ch) >= 0x20 or ch in "\n\t")
        return text.strip()
    if isinstance(value, dict):
        return {str(k): _normalize_scalar(v) for k, v in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize_scalar(item) for item in value]
    return value


def diff_layer(context: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Produce one declarative diff layer; canonicalization is owned by artifacts."""
    schema = dict(spec.get("schema") or {})
    layer = str(schema.get("layer") or "")
    kind = str(schema.get("kind") or "")
    source = str(schema.get("source") or "")
    asset = context.get("asset") or {}
    edges = context.get("edges") or []
    if kind == "fields":
        fields = [str(item) for item in (schema.get("fields") or [])]
        payload: dict[str, Any] = {field: asset.get(field) for field in fields}
    elif kind == "graph":
        payload = {"edges": list(edges)}
    elif kind == "text":
        payload = {"text": str(asset.get(str(schema.get("field") or "yaml_cache")) or "")}
    elif kind == "digest":
        payload = {"canonical_sha256": digest({"asset": asset, "edges": edges, "source": source})}
    else:
        payload = {"unsupported_kind": kind}
    return {
        "layer": layer,
        "kind": kind,
        "source": source,
        "status": "ok" if kind in {"fields", "graph", "text", "digest"} else "unsupported",
        "payload": payload,
    }


def lifecycle_hook(context: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Deterministic lifecycle observation; host code owns real side effects."""
    payload = context.get("payload") or {}
    return {
        "hook": str(spec.get("name") or "unknown"),
        "event": str(context.get("event") or "unknown"),
        "status": str((spec.get("schema") or {}).get("status") or "observed"),
        "payload_keys": sorted(str(key) for key in payload),
    }


def yaml_sink(context: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Write one canonical YAML artifact below a caller-provided root."""
    root = Path(str(context.get("root") or "."))
    relative = str(context.get("relative_path") or "")
    result = write_artifact(root, relative, context.get("payload") or {})
    result["sink"] = str(spec.get("name") or "yaml")
    return result


def fps_3c_template(context: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Return the declarative FPS 3C template; no model inference in Lens."""
    schema = dict(spec.get("schema") or {})
    return {
        "template_id": str(schema.get("template_id") or "fps_3c"),
        "version": str(schema.get("version") or "1"),
        "intent": str(context.get("intent") or ""),
        "spec": schema.get("game_spec") or {},
        "assets": schema.get("assets") or [],
        "operations": schema.get("operations") or [],
        "assertions": schema.get("assertions") or [],
    }


def _spec_sections(spec: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    sections: list[Mapping[str, Any]] = [spec]
    for key in ("config", "schema"):
        value = spec.get(key)
        if isinstance(value, Mapping):
            sections.append(value)
    return tuple(sections)


def _executor_injection_allowed(spec: Mapping[str, Any]) -> bool:
    """Deny by default; disabled/external specs can never opt in."""
    sections = _spec_sections(spec)
    if any(section.get("enabled", True) is False for section in sections):
        return False
    for section in sections:
        if any(section.get(flag) is True for flag in _RESOURCE_REQUIREMENT_FLAGS):
            return False
        capabilities = section.get("capabilities") or ()
        if isinstance(capabilities, str):
            capabilities = (capabilities,)
        if any(str(item) in _EXTERNAL_CAPABILITIES for item in capabilities):
            return False
    for section in sections:
        if "allow_executor_injection" in section:
            return section["allow_executor_injection"] is True
    return False


def official_gateway(context: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Offline gateway adapter; executor injection is explicitly opt-in only."""
    tool = str(context.get("tool") or "")
    arguments = dict(context.get("arguments") or {})
    executor = context.get("executor")
    arguments_sha256 = digest(arguments)
    deferred = {
        "tool": tool,
        "access_mode": str(context.get("access_mode") or "Write"),
        "status": "deferred_offline",
        "arguments_sha256": arguments_sha256,
        "gateway": str(spec.get("name") or "official"),
    }
    if (
        executor is None
        or not callable(executor)
        or not _executor_injection_allowed(spec)
    ):
        return deferred
    result = executor(tool, arguments)
    return {
        "tool": tool,
        "access_mode": str(context.get("access_mode") or "Write"),
        "status": "executed",
        "arguments_sha256": arguments_sha256,
        "result": result,
        "gateway": str(spec.get("name") or "official"),
    }


def runtime_tool(context: dict[str, Any], spec: dict[str, Any]) -> str:
    """Dispatch a declarative tool to a named handler supplied by tools.py."""
    handler = context.get("handler")
    name = str((spec.get("schema") or {}).get("handler") or spec.get("name") or "")
    if handler is None:
        raise ValueError(f"runtime tool handler is unavailable: {name}")
    return str(handler(name, context.get("arguments") or {}))
