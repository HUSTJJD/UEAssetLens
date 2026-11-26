"""Offline deterministic five-layer asset diff artifacts.

The complete canonical YAML payloads are written below the caller-supplied
artifact root. MCP-facing functions return only bounded TOON summaries or a
bounded canonical YAML page. Every layer is a deterministic projection of data
already present in the read-only index: no UObject loading, UE reflection,
raw binary export, VLM, cloud, GPU, network, clock, or random identifier is
used. Layer ids, descriptions, source metadata and canonicalization patterns
are declarative data owned by ``Config/UAL/diff/layers.yaml``.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import artifacts, extensions, extensions_builtins, revision, settings
from .toon import Table, render_toon

_PUBLIC_PREFIX = ".ual/diffs/"
_DIFF_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_LAYER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_MAX_PATTERN_LENGTH = 512
_MAX_REPLACEMENT_LENGTH = 128
_MAX_CHANGED_FIELDS = 8
_MAX_TOON_ROWS = 20
_DEFAULT_PAGE_LIMIT = 80


@dataclass(frozen=True)
class _LayerSpec:
    layer_id: str
    description: str
    source: str
    kind: str
    fields: tuple[str, ...]
    projection: dict[str, Any]
    patterns: tuple[tuple[re.Pattern[str], str], ...]
    noise_keys: frozenset[str]


@dataclass(frozen=True)
class _Build:
    diff_id: str
    manifest: dict[str, Any]
    payloads: dict[str, dict[str, Any]]
    texts: dict[str, str]
    report: dict[str, Any]


def _compile_patterns(value: Any) -> tuple[tuple[re.Pattern[str], str], ...]:
    """Compile declarative regex substitutions; never resolve code from YAML."""
    rows = value if isinstance(value, list) else []
    if not rows:
        raise ValueError("diff layer canonicalization patterns are required")
    compiled: list[tuple[re.Pattern[str], str]] = []
    for row in rows:
        if not isinstance(row, Mapping) or str(row.get("kind", "")) != "regex":
            raise ValueError("only declarative regex canonicalization is allowed")
        pattern = str(row.get("pattern", ""))
        replacement = str(row.get("replacement", ""))
        if not pattern or len(pattern) > _MAX_PATTERN_LENGTH:
            raise ValueError("diff canonicalization pattern is empty or too long")
        if len(replacement) > _MAX_REPLACEMENT_LENGTH:
            raise ValueError("diff canonicalization replacement is too long")
        try:
            compiled.append((re.compile(pattern), replacement))
        except re.error as exc:
            raise ValueError(f"invalid diff canonicalization pattern: {exc}") from exc
    return tuple(compiled)


def _load_layer_specs(project_root: Path | None) -> dict[str, _LayerSpec]:
    resolved = settings.load_namespace("diff", project_root=project_root)
    specs: dict[str, _LayerSpec] = {}
    for rule in resolved.active_rules():
        layer_id = str(rule.get("id", ""))
        if not _LAYER_ID_RE.fullmatch(layer_id):
            raise ValueError(f"invalid diff layer id: {layer_id!r}")
        if layer_id in specs:
            raise ValueError(f"duplicate diff layer id: {layer_id}")
        fields = tuple(str(item) for item in (rule.get("fields") or []))
        projection = dict(rule.get("projection") or {})
        noise_keys = frozenset(str(item).lower() for item in (rule.get("noise_keys") or []))
        specs[layer_id] = _LayerSpec(
            layer_id=layer_id,
            description=str(rule.get("description", "")),
            source=str(rule.get("source", "")),
            kind=str(rule.get("kind", "")),
            fields=fields,
            projection=projection,
            patterns=_compile_patterns(rule.get("canonicalization")),
            noise_keys=noise_keys,
        )
    if not specs:
        raise ValueError("no active diff layers are configured")
    return dict(sorted(specs.items()))


def _canonicalize_payload(value: Any, spec: _LayerSpec) -> Any:
    """Recursively canonicalize strings/keys, sort mappings and edge-like rows."""
    if isinstance(value, str):
        text = artifacts.canonicalize_text(value)
        for regex, replacement in spec.patterns:
            text = regex.sub(lambda _match, fixed=replacement: fixed, text)
        return artifacts.canonicalize_text(text)
    if isinstance(value, bytes):
        return _canonicalize_payload(value.decode("utf-8", errors="replace"), spec)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        normalized_keys: list[tuple[str, Any]] = []
        for key, item in value.items():
            key_text = str(_canonicalize_payload(str(key), spec))
            if key_text.lower() in spec.noise_keys:
                continue
            normalized_keys.append((key_text, item))
        for key_text, item in sorted(normalized_keys, key=lambda pair: pair[0]):
            result[key_text] = _canonicalize_payload(item, spec)
        return result
    if isinstance(value, (list, tuple, set)):
        items = value if isinstance(value, (list, tuple)) else sorted(value, key=artifacts.canonical_yaml)
        normalized = [_canonicalize_payload(item, spec) for item in items]
        if normalized and all(isinstance(item, Mapping) for item in normalized):
            normalized.sort(key=artifacts.canonical_yaml)
        return normalized
    return value


def _load_diff_extensions(
    project_root: Path | None,
) -> dict[str, tuple[Any, Any, dict[str, Any]]]:
    loaded = extensions.load_registry(project_root=project_root)
    resolved = loaded.registry.resolve("*", namespace="diff", capability="diff")
    by_layer: dict[str, tuple[Any, Any, dict[str, Any]]] = {}
    for spec, impl in resolved:
        schema = dict(spec.schema)
        layer_id = str(schema.get("layer", ""))
        if not layer_id:
            raise ValueError("diff extension is missing schema.layer")
        if impl is not extensions_builtins.diff_layer:
            raise ValueError("diff extension must resolve to the builtin diff_layer")
        if layer_id in by_layer:
            raise ValueError(f"duplicate diff extension for layer {layer_id}")
        by_layer[layer_id] = (spec, impl, schema)
    return by_layer


def _validate_extension(layer_spec: _LayerSpec, schema: dict[str, Any]) -> None:
    if str(schema.get("source", "")) != layer_spec.source:
        raise ValueError(f"diff source mismatch for layer {layer_spec.layer_id}")
    if str(schema.get("kind", "")) != layer_spec.kind:
        raise ValueError(f"diff kind mismatch for layer {layer_spec.layer_id}")
    schema_fields = schema.get("fields")
    if schema_fields is None and schema.get("field") is not None:
        schema_fields = [schema.get("field")]
    if schema_fields:
        configured = tuple(str(item) for item in schema_fields)
        if configured != layer_spec.fields:
            raise ValueError(f"diff field mismatch for layer {layer_spec.layer_id}")


def _parse_layers(layers: Any, specs: Mapping[str, _LayerSpec]) -> tuple[tuple[str, ...], str]:
    requested = {part.strip() for part in str(layers).split(",") if part.strip()}
    unknown = sorted(requested - set(specs))
    if unknown:
        raise ValueError(f"unknown diff layer(s): {','.join(unknown)}")
    if not requested:
        raise ValueError("at least one diff layer is required")
    ordered = tuple(layer_id for layer_id in specs if layer_id in requested)
    return ordered, ",".join(ordered)


def _diff_id(
    lhs: str,
    rhs: str,
    layers: str,
    engine_version: str,
    index_revision: str,
) -> str:
    return artifacts.digest({
        "lhs": lhs,
        "rhs": rhs,
        "layers": layers,
        "engine_version": engine_version,
        "index_revision": index_revision,
    })


def _public_rel(diff_id: str, filename: str) -> str:
    return artifacts.safe_relative(f"{_PUBLIC_PREFIX}{diff_id}/{filename}")


def _manifest_rel(diff_id: str) -> str:
    return _public_rel(diff_id, "manifest.yaml")


def _layer_rel(diff_id: str, layer_id: str) -> str:
    return _public_rel(diff_id, f"{layer_id}.yaml")


def _disk_rel(public_rel: str) -> str:
    relative = artifacts.safe_relative(public_rel)
    if not relative.startswith(_PUBLIC_PREFIX):
        raise ValueError(f"artifact pointer must start with {_PUBLIC_PREFIX!r}")
    return artifacts.safe_relative("diffs/" + relative[len(_PUBLIC_PREFIX):])


def _changed_paths(lhs: Any, rhs: Any, prefix: str = "$") -> list[str]:
    """Return stable field paths only; values never enter an MCP summary."""
    if type(lhs) is not type(rhs):
        return [prefix]
    if isinstance(lhs, Mapping):
        paths: list[str] = []
        for key in sorted(set(lhs) | set(rhs)):
            child = f"{prefix}.{key}"
            if key not in lhs or key not in rhs:
                paths.append(child)
            else:
                paths.extend(_changed_paths(lhs[key], rhs[key], child))
        return paths
    if isinstance(lhs, (list, tuple)):
        paths = []
        if len(lhs) != len(rhs):
            paths.append(f"{prefix}.length")
        for index in range(min(len(lhs), len(rhs))):
            paths.extend(_changed_paths(lhs[index], rhs[index], f"{prefix}[{index}]"))
        for index in range(min(len(lhs), len(rhs)), max(len(lhs), len(rhs))):
            paths.append(f"{prefix}[{index}]")
        return paths
    if artifacts.canonical_yaml(lhs) != artifacts.canonical_yaml(rhs):
        return [prefix]
    return []


def _current_metadata(con: Any, spec: _LayerSpec) -> tuple[str, str]:
    engine = str(_canonicalize_payload(revision.engine_version(con), spec))
    index = str(_canonicalize_payload(revision.index_revision(con), spec))
    return engine, index


def _build_artifacts(
    con: Any,
    lhs: Any,
    rhs: Any,
    layers: str,
    *,
    project_root: Path | None,
) -> _Build:
    """Pure builder: canonical payloads/texts only, with no filesystem writes."""
    layer_specs = _load_layer_specs(project_root)
    extension_specs = _load_diff_extensions(project_root)
    selected, layers_key = _parse_layers(layers, layer_specs)
    for layer_id in selected:
        if layer_id not in extension_specs:
            raise ValueError(f"no builtin diff extension for layer {layer_id}")
    first_spec = next(iter(layer_specs.values()))

    raw_lhs = str(lhs)
    raw_rhs = str(rhs)
    lhs_id = str(_canonicalize_payload(raw_lhs, first_spec))
    rhs_id = str(_canonicalize_payload(raw_rhs, first_spec))
    engine_version, index_revision = _current_metadata(con, first_spec)
    diff_id = _diff_id(lhs_id, rhs_id, layers_key, engine_version, index_revision)
    manifest_rel = _manifest_rel(diff_id)

    contexts = {
        "lhs": {
            "asset": revision.asset_snapshot(con, raw_lhs),
            "edges": revision.edge_rows(con, raw_lhs),
        },
        "rhs": {
            "asset": revision.asset_snapshot(con, raw_rhs),
            "edges": revision.edge_rows(con, raw_rhs),
        },
    }

    payloads: dict[str, dict[str, Any]] = {}
    texts: dict[str, str] = {}
    layer_stats: list[dict[str, Any]] = []
    for layer_id in selected:
        layer_spec = layer_specs[layer_id]
        ext_spec, impl, schema = extension_specs[layer_id]
        _validate_extension(layer_spec, schema)
        spec_dict = {"name": ext_spec.name, "schema": schema}
        left = _canonicalize_payload(impl(contexts["lhs"], spec_dict), layer_spec)
        right = _canonicalize_payload(impl(contexts["rhs"], spec_dict), layer_spec)
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            raise ValueError(f"diff extension for layer {layer_id} returned a non-mapping")
        if str(left.get("status", "")) != "ok" or str(right.get("status", "")) != "ok":
            raise ValueError(f"diff extension for layer {layer_id} did not report ok")
        changed = sorted(_changed_paths(left.get("payload"), right.get("payload")))
        layer_payload = _canonicalize_payload({
            "diff_id": diff_id,
            "layer": layer_id,
            "description": layer_spec.description,
            "source": layer_spec.source,
            "kind": layer_spec.kind,
            "extension": ext_spec.name,
            "fields": list(layer_spec.fields),
            "projection": layer_spec.projection,
            "engine_version": engine_version,
            "index_revision": index_revision,
            "lhs": dict(left),
            "rhs": dict(right),
            "changes": {
                "count": len(changed),
                "fields": changed[:_MAX_CHANGED_FIELDS],
                "truncated": len(changed) > _MAX_CHANGED_FIELDS,
            },
        }, layer_spec)
        layer_rel = _layer_rel(diff_id, layer_id)
        text = artifacts.canonical_yaml(layer_payload)
        page = artifacts.page_lines(text, offset=0, limit=_DEFAULT_PAGE_LIMIT)
        canonical_sha256 = artifacts.sha256_text(text)
        payloads[layer_rel] = layer_payload
        texts[layer_rel] = text
        layer_stats.append({
            "layer": layer_id,
            "status": str(left.get("status", "")),
            "description": layer_spec.description,
            "source": layer_spec.source,
            "projection": layer_spec.projection,
            "changed_count": len(changed),
            "changed_fields": changed[:_MAX_CHANGED_FIELDS],
            "canonical_sha256": canonical_sha256,
            "line_count": len(text.splitlines()),
            "artifact": layer_rel,
            "next_cursor": str(page["next_cursor"]),
        })

    canonical_sha256 = artifacts.digest({
        str(item["artifact"]): str(item["canonical_sha256"])
        for item in sorted(layer_stats, key=lambda row: str(row["layer"]))
    })
    next_cursor = next(
        (str(item["next_cursor"]) for item in layer_stats if item["next_cursor"]), ""
    )
    manifest = _canonicalize_payload({
        "diff_id": diff_id,
        "artifact": manifest_rel,
        "lhs": lhs_id,
        "rhs": rhs_id,
        "layers": layers_key,
        "engine_version": engine_version,
        "index_revision": index_revision,
        "canonical_sha256": canonical_sha256,
        "next_cursor": next_cursor,
        "layer_artifacts": {
            str(item["layer"]): {
                "artifact": item["artifact"],
                "canonical_sha256": item["canonical_sha256"],
                "line_count": item["line_count"],
                "next_cursor": item["next_cursor"],
                "status": item["status"],
                "description": item["description"],
                "source": item["source"],
                "projection": item["projection"],
                "changed_count": item["changed_count"],
                "changed_fields": list(item["changed_fields"]),
            }
            for item in sorted(layer_stats, key=lambda row: str(row["layer"]))
        },
    }, first_spec)
    payloads[manifest_rel] = manifest
    texts[manifest_rel] = artifacts.canonical_yaml(manifest)
    report = {
        "diff_id": diff_id,
        "artifact": manifest_rel,
        "canonical_sha256": canonical_sha256,
        "next_cursor": next_cursor,
        "layer_stats": layer_stats,
    }
    return _Build(diff_id, manifest, payloads, texts, report)


def _write_build(build: _Build, root: Path) -> None:
    for public_rel in sorted(build.payloads):
        disk_rel = _disk_rel(public_rel)
        written = artifacts.write_artifact(root, disk_rel, build.payloads[public_rel])
        if written["canonical_sha256"] != artifacts.sha256_text(build.texts[public_rel]):
            raise ValueError(f"canonical artifact mismatch: {public_rel}")


def _read_canonical(
    root: Path,
    public_rel: str,
    spec: _LayerSpec,
) -> tuple[Any, str]:
    payload = artifacts.read_artifact(root, _disk_rel(public_rel))
    canonical = _canonicalize_payload(payload, spec)
    return canonical, artifacts.canonical_yaml(canonical)


def _validate_diff_id(value: Any) -> str:
    diff_id = str(value)
    if not _DIFF_ID_RE.fullmatch(diff_id):
        raise ValueError("diff_id must be a 64-character lowercase sha256 hex digest")
    return diff_id


def _validate_manifest(
    manifest: Any,
    diff_id: str,
    specs: Mapping[str, _LayerSpec],
) -> None:
    if not isinstance(manifest, Mapping):
        raise ValueError("diff manifest must be a mapping")
    expected_id = _diff_id(
        str(manifest.get("lhs", "")),
        str(manifest.get("rhs", "")),
        str(manifest.get("layers", "")),
        str(manifest.get("engine_version", "")),
        str(manifest.get("index_revision", "")),
    )
    if expected_id != diff_id:
        raise ValueError("diff_id does not match manifest inputs")
    if str(manifest.get("artifact", "")) != _manifest_rel(diff_id):
        raise ValueError("diff manifest artifact pointer is invalid")
    selected, normalized = _parse_layers(str(manifest.get("layers", "")), specs)
    if normalized != str(manifest.get("layers", "")):
        raise ValueError("diff manifest layer selection is not normalized")
    entries = manifest.get("layer_artifacts")
    if not isinstance(entries, Mapping) or set(entries) != set(selected):
        raise ValueError("diff manifest layer artifacts do not match selected layers")
    hashes: dict[str, str] = {}
    for layer_id in selected:
        meta = entries.get(layer_id)
        if not isinstance(meta, Mapping):
            raise ValueError(f"diff manifest layer metadata is invalid: {layer_id}")
        if str(meta.get("artifact", "")) != _layer_rel(diff_id, layer_id):
            raise ValueError(f"diff manifest layer pointer is invalid: {layer_id}")
        if int(meta.get("line_count", -1)) < 0:
            raise ValueError(f"diff manifest line_count is invalid: {layer_id}")
        hashes[str(meta.get("artifact", ""))] = str(meta.get("canonical_sha256", ""))
    if artifacts.digest(hashes) != str(manifest.get("canonical_sha256", "")):
        raise ValueError("diff manifest canonical_sha256 does not match layer hashes")


def _sha256_or_empty(text: str | None) -> str:
    return artifacts.sha256_text(text) if text is not None else ""


def _mismatch(
    artifact: str,
    reason: str,
    first: str | None,
    second: str | None,
) -> dict[str, str]:
    return {
        "artifact": artifact,
        "reason": reason,
        "first_sha256": _sha256_or_empty(first),
        "second_sha256": _sha256_or_empty(second),
    }


def _spec_for_public_rel(
    public_rel: str,
    diff_id: str,
    specs: Mapping[str, _LayerSpec],
    first_spec: _LayerSpec,
) -> _LayerSpec:
    if public_rel == _manifest_rel(diff_id):
        return first_spec
    filename = str(public_rel).rsplit("/", 1)[-1]
    layer_id = filename[:-5] if filename.endswith(".yaml") else filename
    return specs.get(layer_id, first_spec)


def render_summary(report: Mapping[str, Any]) -> str:
    """Pure bounded TOON summary; full payloads remain on disk."""
    stats = [dict(item) for item in (report.get("layer_stats") or []) if isinstance(item, Mapping)]
    rows: list[list[Any]] = []
    for item in sorted(stats, key=lambda row: str(row.get("layer", "")))[:_MAX_TOON_ROWS]:
        rows.append([
            item.get("layer", ""),
            item.get("status", ""),
            item.get("changed_count", 0),
            "|".join(str(field) for field in (item.get("changed_fields") or [])),
            item.get("canonical_sha256", ""),
            item.get("line_count", 0),
            item.get("artifact", ""),
            item.get("next_cursor", ""),
            item.get("description", ""),
            item.get("source", ""),
        ])
    summary = {
        "diff_id": str(report.get("diff_id", "")),
        "artifact": str(report.get("artifact", "")),
        "canonical_sha256": str(report.get("canonical_sha256", "")),
        "next_cursor": str(report.get("next_cursor", "")),
        "total": len(stats),
        "returned": len(rows),
        "truncated": len(stats) > len(rows),
    }
    table = Table(
        "layer_stats",
        [
            "layer", "status", "changed_count", "changed_fields",
            "canonical_sha256", "line_count", "artifact", "next_cursor",
            "description", "source",
        ],
        rows,
    )
    return render_toon([table], summary=summary)


def render_determinism(report: Mapping[str, Any]) -> str:
    """Pure bounded TOON determinism report; artifact bodies are never emitted."""
    mismatches = [
        dict(item)
        for item in (report.get("mismatches") or [])
        if isinstance(item, Mapping)
    ]
    mismatches.sort(key=lambda row: (str(row.get("artifact", "")), str(row.get("reason", ""))))
    rows: list[list[Any]] = []
    for item in mismatches[:_MAX_TOON_ROWS]:
        rows.append([
            item.get("artifact", ""),
            item.get("reason", ""),
            item.get("first_sha256", ""),
            item.get("second_sha256", ""),
        ])
    summary = {
        "diff_id": str(report.get("diff_id", "")),
        "deterministic": bool(report.get("deterministic", False)),
        "artifact_count": int(report.get("artifact_count", 0)),
        "canonical_sha256": str(report.get("canonical_sha256", "")),
        "mismatch_count": len(mismatches),
        "returned": len(rows),
        "truncated": len(mismatches) > len(rows),
    }
    table = Table(
        "mismatches",
        ["artifact", "reason", "first_sha256", "second_sha256"],
        rows,
    )
    return render_toon([table], summary=summary)


def diff_assets(
    con: Any,
    lhs: Any,
    rhs: Any,
    layers: str = "L0,L1,L2,L3,L4",
    *,
    project_root: Path | None = None,
    root: Path = Path(".ual"),
) -> str:
    """Write all selected canonical layer artifacts and return bounded TOON."""
    build = _build_artifacts(con, lhs, rhs, layers, project_root=project_root)
    _write_build(build, root)
    return render_summary(build.report)


def read_diff_layer(
    con: Any,
    diff_id: Any,
    layer: Any,
    offset: int = 0,
    limit: int = 80,
    *,
    project_root: Path | None = None,
    root: Path = Path(".ual"),
) -> str:
    """Validate an existing diff and return one bounded canonical YAML page."""
    valid_id = _validate_diff_id(diff_id)
    specs = _load_layer_specs(project_root)
    layer_id = str(layer)
    if layer_id not in specs:
        raise ValueError(f"unknown diff layer: {layer_id}")
    first_spec = next(iter(specs.values()))
    manifest_rel = _manifest_rel(valid_id)
    manifest, manifest_text = _read_canonical(root, manifest_rel, first_spec)
    _validate_manifest(manifest, valid_id, specs)
    current_engine, current_index = _current_metadata(con, first_spec)
    if current_engine != str(manifest.get("engine_version", "")):
        raise ValueError("diff artifact was built for a different engine_version")
    if current_index != str(manifest.get("index_revision", "")):
        raise ValueError("diff artifact was built for a different index_revision")
    selected, _ = _parse_layers(str(manifest.get("layers", "")), specs)
    if layer_id not in selected:
        raise ValueError(f"layer {layer_id} is not part of diff {valid_id}")
    entries = manifest.get("layer_artifacts")
    if not isinstance(entries, Mapping):
        raise ValueError("diff manifest layer artifacts are unavailable")
    meta = entries.get(layer_id)
    if not isinstance(meta, Mapping):
        raise ValueError(f"diff layer metadata is unavailable: {layer_id}")
    layer_rel = artifacts.safe_relative(str(meta.get("artifact", "")))
    if layer_rel != _layer_rel(valid_id, layer_id):
        raise ValueError(f"diff layer artifact pointer is invalid: {layer_id}")
    _layer_payload, layer_text = _read_canonical(root, layer_rel, specs[layer_id])
    if artifacts.sha256_text(layer_text) != str(meta.get("canonical_sha256", "")):
        raise ValueError(f"diff layer canonical_sha256 mismatch: {layer_id}")
    if len(layer_text.splitlines()) != int(meta.get("line_count", -1)):
        raise ValueError(f"diff layer line_count mismatch: {layer_id}")
    manifest_page = artifacts.page_lines(
        manifest_text, offset=0, limit=artifacts.MAX_PAGE_LINES
    )
    if manifest_page["total"] != len(manifest_text.splitlines()):
        raise ValueError("diff manifest page metadata mismatch")
    page = artifacts.page_lines(layer_text, offset=int(offset), limit=int(limit))
    response = {
        "layer": layer_id,
        "offset": page["offset"],
        "limit": page["limit"],
        "returned": page["returned"],
        "total": page["total"],
        "truncated": page["truncated"],
        "next_cursor": page["next_cursor"],
        "lines": list(page["lines"]),
    }
    return artifacts.canonical_yaml(_canonicalize_payload(response, specs[layer_id]))


def verify_diff_determinism(
    con: Any,
    diff_id: Any,
    *,
    project_root: Path | None = None,
    root: Path = Path(".ual"),
) -> str:
    """Rebuild twice with the pure builder and byte-compare canonical artifacts."""
    valid_id = _validate_diff_id(diff_id)
    specs = _load_layer_specs(project_root)
    first_spec = next(iter(specs.values()))
    manifest_rel = _manifest_rel(valid_id)
    manifest, _manifest_text = _read_canonical(root, manifest_rel, first_spec)
    _validate_manifest(manifest, valid_id, specs)

    lhs = str(manifest.get("lhs", ""))
    rhs = str(manifest.get("rhs", ""))
    layers = str(manifest.get("layers", ""))
    first = _build_artifacts(con, lhs, rhs, layers, project_root=project_root)
    second = _build_artifacts(con, lhs, rhs, layers, project_root=project_root)
    mismatches: list[dict[str, str]] = []
    if first.diff_id != valid_id:
        mismatches.append(_mismatch(manifest_rel, "rebuilt_diff_id_mismatch", first.diff_id, valid_id))
    current_engine, current_index = _current_metadata(con, first_spec)
    if current_engine != str(manifest.get("engine_version", "")):
        mismatches.append(_mismatch(manifest_rel, "engine_version_changed", current_engine, str(manifest.get("engine_version", ""))))
    if current_index != str(manifest.get("index_revision", "")):
        mismatches.append(_mismatch(manifest_rel, "index_revision_changed", current_index, str(manifest.get("index_revision", ""))))

    for public_rel in sorted(set(first.texts) | set(second.texts)):
        first_text = first.texts.get(public_rel)
        second_text = second.texts.get(public_rel)
        if first_text != second_text:
            mismatches.append(_mismatch(public_rel, "rebuild_mismatch", first_text, second_text))
        spec = _spec_for_public_rel(public_rel, valid_id, specs, first_spec)
        try:
            _payload, disk_text = _read_canonical(root, public_rel, spec)
        except (FileNotFoundError, ValueError):
            mismatches.append(_mismatch(public_rel, "disk_artifact_missing", first_text, None))
            continue
        if disk_text != first_text:
            mismatches.append(_mismatch(public_rel, "disk_artifact_mismatch", first_text, disk_text))

    report = {
        "diff_id": valid_id,
        "deterministic": not mismatches,
        "artifact_count": len(first.texts),
        "canonical_sha256": str(first.manifest.get("canonical_sha256", "")),
        "mismatches": mismatches,
    }
    return render_determinism(report)
