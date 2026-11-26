"""Declarative, offline fake native payload adapter for L1/L3/L4 diff artifacts.

The adapter is a pure, source-level contract.  It reads one declarative YAML
file with ``artifacts.read_artifact`` (safe data only), canonicalizes fixed
sample payload rows, produces canonical YAML artifacts in memory and returns
bounded TOON/YAML views.  Every layer declares ``loader: none``; the shipped
default is also ``none``.  Anything that would load an asset, carry a raw
payload, resolve a callable from configuration, write an index, write a
``.uasset`` or start a host capability is rejected before it can enter an
artifact.

All YAML is emitted only by :func:`ual_common.artifacts.canonical_yaml` and all
TOON only by :func:`ual_common.toon.render_toon`.  Identifiers are deterministic
content digests; no clock values, local absolute paths or random identifiers
are produced.

Status: offline / source-level adapter contract only.  Real UE native binary
diff, real writes, compile / PIE and index rescan remain specification / not
landed.  This module does not claim a UE native diff implementation.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import artifacts, toon

CONTRACT_NAME = "native_payload_adapter"
SUPPORTED_LAYERS: tuple[str, ...] = ("L1", "L3", "L4")
DEFAULT_LAYERS = "L1,L3,L4"
CONFIG_RELATIVE = "Config/UAL/diff/native_payloads.yaml"
CONFIG_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_PREFIX = ".ual/"
SUMMARY_FIELDS: tuple[str, ...] = (
    "layer", "status", "loader", "source", "changed_count", "changed_fields",
    "canonical_sha256", "line_count", "artifact", "next_cursor",
)
DETERMINISM_FIELDS: tuple[str, ...] = (
    "artifact", "reason", "first_sha256", "second_sha256",
)
MAX_ROWS = 64
MAX_TEXT = 160
MAX_CHANGED_FIELDS = 8
MAX_TOON_ROWS = 20

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")
_WINDOWS_ABS_RE = re.compile(r"[A-Za-z]:[\\/]")
_POSIX_ABS_RE = re.compile(r"(?:^|\s)/(?:home|Users|tmp|var|Volumes|mnt)/")
_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)?\b|\b\d{2}:\d{2}:\d{2}\b"
)
_RANDOM_ID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b|\b[0-9a-fA-F]{32}\b"
)
_GAME_PATH_RE = re.compile(r"^/Game(?:/[A-Za-z0-9_.\-]+)*/?$")
_ASSET_PATH_KEY_RE = re.compile(r"(?i)(?:^|_)asset_paths?(?:$|_)")

_CONFIG_FORBIDDEN_KEYS = frozenset({
    "impl", "handler", "callable", "module", "script", "code", "eval", "exec",
    "pickle", "lambda", "factory", "resolver", "ref", "pointer", "delegate",
})
_PAYLOAD_FORBIDDEN_KEYS = frozenset(_CONFIG_FORBIDDEN_KEYS | {
    "blob", "bytes", "binary_payload", "raw_binary", "raw_binary_export",
    "timestamp", "updated_at", "created_at", "session_id", "run_id",
    "request_id", "random_id", "local_path", "absolute_path", "host_path",
    "file_path", "uuid", "guid", "nonce", "date", "time",
})
_BOUNDARY_FLAGS = (
    "asset_load", "binary_payload", "raw_binary_export", "index_write", "host_call",
)
_SCALAR_TYPES = (str, int, float, bool, type(None))


@dataclass(frozen=True)
class NativeLayerSpec:
    """One validated, loader-free declarative layer specification."""

    layer_id: str
    source: str
    loader: str
    fields: tuple[str, ...]
    fake_payloads: tuple[Any, ...]


@dataclass(frozen=True)
class NativeDiffBuild:
    """Pure in-memory canonical artifacts; no filesystem writes are performed."""

    diff_id: str
    manifest: dict[str, Any]
    payloads: dict[str, dict[str, Any]]
    texts: dict[str, str]
    report: dict[str, Any]


def _assert_clean_text(text: str) -> None:
    """Reject volatile text and code hints before any artifact is produced."""
    lowered = text.lower()
    if any(
        pattern.search(text)
        for pattern in (_WINDOWS_ABS_RE, _POSIX_ABS_RE, _TIMESTAMP_RE, _RANDOM_ID_RE)
    ):
        raise ValueError("native payload contains volatile text")
    if "\\" in text or "://" in text or "!!python" in lowered or "__" in text:
        raise ValueError("native payload contains forbidden text")
    if "eval(" in lowered or "exec(" in lowered or "pickle" in lowered:
        raise ValueError("native payload contains a code hint")
    if len(text) > 4096:
        raise ValueError("native payload text is too long")


def _check_config_value(value: Any) -> None:
    """Walk configuration data; never resolve a callable or dynamic reference."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
                raise ValueError("native payload config has an invalid key")
            if key.lower() in _CONFIG_FORBIDDEN_KEYS:
                raise ValueError("native payload config declares an executable key")
            _assert_clean_text(key)
            _check_config_value(item)
        return
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_ROWS:
            raise ValueError("native payload config list is too long")
        for item in value:
            _check_config_value(item)
        return
    if isinstance(value, str):
        _assert_clean_text(value)
        return
    if isinstance(value, _SCALAR_TYPES):
        return
    raise ValueError("native payload config must contain only data scalars")


def _canonicalize_payload(value: Any, *, key_name: str = "") -> Any:
    """Sorted, bounded, loader-free canonical projection of a fake payload."""
    if isinstance(value, (bytes, bytearray)):
        raise ValueError("native payload cannot carry a raw payload")
    if isinstance(value, str):
        _assert_clean_text(value)
        if _ASSET_PATH_KEY_RE.search(key_name) and not _GAME_PATH_RE.match(value):
            raise ValueError("native payload asset path must be a /Game path")
        return artifacts.canonicalize_text(value)[:MAX_TEXT]
    if isinstance(value, _SCALAR_TYPES):
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            key = str(raw_key)
            if not _KEY_RE.fullmatch(key) or key.lower() in _PAYLOAD_FORBIDDEN_KEYS:
                raise ValueError("native payload contains a forbidden key")
            _assert_clean_text(key)
            result[key] = _canonicalize_payload(item, key_name=key)
        return result
    if isinstance(value, (list, tuple)):
        items = [_canonicalize_payload(item, key_name=key_name) for item in list(value)[:MAX_ROWS]]
        if items and all(isinstance(item, Mapping) for item in items):
            items.sort(key=artifacts.canonical_yaml)
        return items
    raise ValueError("native payload must contain only data scalars")


def load_native_payload_config(*, config_root: Path | None = None) -> dict[str, Any]:
    """Load and validate the declarative adapter config; no code is resolved."""
    root = Path(config_root) if config_root is not None else CONFIG_ROOT
    try:
        doc = artifacts.read_artifact(root, CONFIG_RELATIVE)
    except Exception as exc:
        raise ValueError("native payload config is not declarative YAML data") from exc
    if not isinstance(doc, Mapping) or "rules" in doc or "id" in doc:
        raise ValueError("native payload config must not join the diff ruleset")
    data = doc.get(CONTRACT_NAME)
    if not isinstance(data, Mapping):
        raise ValueError("native payload config has no adapter block")
    _check_config_value(data)
    try:
        version = int(data.get("version", -1))
        page_limit = int(data.get("page_limit", 80))
    except (TypeError, ValueError) as exc:
        raise ValueError("native payload config version/page_limit is invalid") from exc
    if version != 1 or page_limit < 1:
        raise ValueError("native payload config version/page_limit is invalid")
    if str(data.get("status", "")) != "offline_source_level_contract":
        raise ValueError("native payload config status must stay offline")
    if str(data.get("default_loader", "none")) != "none":
        raise ValueError("native payload config default loader must be none")
    host_only = data.get("host_only")
    if not isinstance(host_only, Mapping):
        raise ValueError("native payload config is missing host-only metadata")
    if str(host_only.get("status", "")) != "spec_not_landed":
        raise ValueError("host-only capability must stay spec_not_landed")
    if str(host_only.get("real_ue_native_diff", "")) != "spec_not_landed":
        raise ValueError("real UE native diff must stay spec_not_landed")
    if str(host_only.get("requires_injected_host_executor", "false")).lower() != "true":
        raise ValueError("host-only metadata must require an injected host executor")
    boundaries = data.get("boundaries")
    if not isinstance(boundaries, Mapping) or any(
        boundaries.get(flag) is not False for flag in _BOUNDARY_FLAGS
    ):
        raise ValueError("native payload boundaries must all stay disabled")
    # Validate every declared loader and fake payload row before handing the
    # configuration back to a caller; no layer may override loader: none.
    layer_specs(data)
    return dict(data)


def layer_specs(data: Mapping[str, Any]) -> dict[str, NativeLayerSpec]:
    """Validate and canonicalize the three loader-free layer declarations."""
    raw = data.get("layers")
    if not isinstance(raw, Mapping) or set(raw) != set(SUPPORTED_LAYERS):
        raise ValueError("native payload config must declare exactly L1/L3/L4")
    specs: dict[str, NativeLayerSpec] = {}
    for layer_id in SUPPORTED_LAYERS:
        item = raw.get(layer_id)
        if not isinstance(item, Mapping):
            raise ValueError(f"native payload layer is invalid: {layer_id}")
        loader = str(item.get("loader", data.get("default_loader", "none")))
        if loader != "none":
            raise ValueError("native payload loader must be none")
        source = str(item.get("source", ""))
        _assert_clean_text(source)
        fields = tuple(str(field) for field in (item.get("fields") or []))
        payloads = item.get("fake_payloads")
        if not isinstance(payloads, list) or not payloads or len(payloads) > MAX_ROWS:
            raise ValueError(f"native payload rows are invalid: {layer_id}")
        rows = _canonicalize_payload(payloads)
        if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
            raise ValueError(f"native payload rows must be mappings: {layer_id}")
        specs[layer_id] = NativeLayerSpec(
            layer_id=layer_id,
            source=source,
            loader=loader,
            fields=fields,
            fake_payloads=tuple(rows),
        )
    return specs


def canonicalize_payload(
    layer_id: str,
    payload: Any,
    *,
    config_root: Path | None = None,
) -> Any:
    """Public canonicalization entry point for one supported layer."""
    data = load_native_payload_config(config_root=config_root)
    specs = layer_specs(data)
    if layer_id not in specs:
        raise ValueError(f"unsupported native payload layer: {layer_id}")
    return _canonicalize_payload(payload)


def _parse_layers(layers: Any, specs: Mapping[str, NativeLayerSpec]) -> tuple[str, ...]:
    requested = {part.strip() for part in str(layers).split(",") if part.strip()}
    unknown = sorted(requested - set(specs))
    if unknown:
        raise ValueError(f"unknown native payload layer(s): {','.join(unknown)}")
    if not requested:
        raise ValueError("at least one native payload layer is required")
    return tuple(layer for layer in SUPPORTED_LAYERS if layer in requested)


def _relative(diff_id: str, filename: str) -> str:
    return PUBLIC_PREFIX + artifacts.safe_relative(
        f"native_diffs/{diff_id}/{filename}"
    )


def _changed_paths(lhs: Any, rhs: Any, prefix: str = "$") -> list[str]:
    """Stable field paths only; payload values never enter a bounded summary."""
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
    if isinstance(lhs, list):
        paths = []
        if len(lhs) != len(rhs):
            paths.append(f"{prefix}.length")
        for index in range(min(len(lhs), len(rhs))):
            paths.extend(_changed_paths(lhs[index], rhs[index], f"{prefix}[{index}]"))
        return paths
    if artifacts.canonical_yaml(lhs) != artifacts.canonical_yaml(rhs):
        return [prefix]
    return []


def _select_row(rows: Sequence[Any], asset_path: str) -> dict[str, Any]:
    for row in rows:
        if isinstance(row, Mapping) and str(row.get("asset_path", "")) == asset_path:
            return dict(row)
    return {}


def build_native_diff(
    lhs: Any,
    rhs: Any,
    layers: str = DEFAULT_LAYERS,
    *,
    config_root: Path | None = None,
) -> NativeDiffBuild:
    """Build deterministic canonical YAML artifacts in memory, twice-stable."""
    data = load_native_payload_config(config_root=config_root)
    specs = layer_specs(data)
    selected = _parse_layers(layers, specs)
    layers_key = ",".join(selected)
    lhs_id = _canonicalize_payload(str(lhs), key_name="asset_path")
    rhs_id = _canonicalize_payload(str(rhs), key_name="asset_path")
    diff_id = artifacts.digest({
        "contract": CONTRACT_NAME,
        "contract_version": int(data["version"]),
        "lhs": lhs_id,
        "rhs": rhs_id,
        "layers": layers_key,
    })
    host_only = _canonicalize_payload(dict(data["host_only"]))
    manifest_rel = _relative(diff_id, "manifest.yaml")

    payloads: dict[str, dict[str, Any]] = {}
    texts: dict[str, str] = {}
    layer_stats: list[dict[str, Any]] = []
    for layer_id in selected:
        spec = specs[layer_id]
        rows = list(spec.fake_payloads)
        changed = sorted(_changed_paths(_select_row(rows, str(lhs_id)), _select_row(rows, str(rhs_id))))
        layer_rel = _relative(diff_id, f"{layer_id}.yaml")
        layer_payload = _canonicalize_payload({
            "contract": CONTRACT_NAME,
            "contract_version": int(data["version"]),
            "diff_id": diff_id,
            "layer": layer_id,
            "source": spec.source,
            "loader": spec.loader,
            "materialization": str(data["materialization"]),
            "status": str(data["status"]),
            "host_only": dict(host_only),
            "lhs": lhs_id,
            "rhs": rhs_id,
            "fields": list(spec.fields),
            "fake_payload_rows": rows,
            "changes": {
                "count": len(changed),
                "fields": changed[:MAX_CHANGED_FIELDS],
                "truncated": len(changed) > MAX_CHANGED_FIELDS,
            },
        })
        text = artifacts.canonical_yaml(layer_payload)
        page = artifacts.page_lines(text, offset=0, limit=int(data["page_limit"]))
        digest = artifacts.sha256_text(text)
        payloads[layer_rel] = layer_payload
        texts[layer_rel] = text
        layer_stats.append({
            "layer": layer_id,
            "status": str(data["status"]),
            "loader": spec.loader,
            "source": spec.source,
            "changed_count": len(changed),
            "changed_fields": changed[:MAX_CHANGED_FIELDS],
            "canonical_sha256": digest,
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
        "artifact": manifest_rel,
        "canonical_sha256": canonical_sha256,
        "contract": CONTRACT_NAME,
        "contract_version": int(data["version"]),
        "default_loader": str(data["default_loader"]),
        "diff_id": diff_id,
        "host_only": dict(host_only),
        "layer_artifacts": {
            str(item["layer"]): {
                "artifact": item["artifact"],
                "canonical_sha256": item["canonical_sha256"],
                "changed_count": item["changed_count"],
                "changed_fields": list(item["changed_fields"]),
                "line_count": item["line_count"],
                "loader": item["loader"],
                "next_cursor": item["next_cursor"],
                "source": item["source"],
                "status": item["status"],
            }
            for item in sorted(layer_stats, key=lambda row: str(row["layer"]))
        },
        "lhs": lhs_id,
        "layers": layers_key,
        "next_cursor": next_cursor,
        "rhs": rhs_id,
        "status": str(data["status"]),
    })
    payloads[manifest_rel] = manifest
    texts[manifest_rel] = artifacts.canonical_yaml(manifest)
    report = {
        "artifact": manifest_rel,
        "canonical_sha256": canonical_sha256,
        "contract": CONTRACT_NAME,
        "contract_version": int(data["version"]),
        "default_loader": str(data["default_loader"]),
        "diff_id": diff_id,
        "host_only": dict(host_only),
        "layer_stats": layer_stats,
        "layers": layers_key,
        "next_cursor": next_cursor,
        "status": str(data["status"]),
    }
    return NativeDiffBuild(diff_id, manifest, payloads, texts, report)


def artifact_metadata(build: NativeDiffBuild) -> dict[str, Any]:
    """Deterministic, bounded metadata for the canonical manifest artifact."""
    text = artifacts.canonical_yaml(_canonicalize_payload(dict(build.manifest)))
    page = artifacts.page_lines(text, offset=0, limit=artifacts.MAX_PAGE_LINES)
    return {
        "artifact": str(build.manifest["artifact"]),
        "canonical_sha256": artifacts.sha256_text(text),
        "contract": CONTRACT_NAME,
        "contract_version": int(build.manifest["contract_version"]),
        "default_loader": str(build.manifest["default_loader"]),
        "diff_id": build.diff_id,
        "line_count": len(text.splitlines()),
        "next_cursor": str(page["next_cursor"]),
        "real_ue_native_diff": "spec_not_landed",
        "status": str(build.manifest["status"]),
    }


def render_artifact(build: NativeDiffBuild) -> str:
    """Canonical YAML artifact metadata; emitted only by the shared emitter."""
    return artifacts.canonical_yaml(artifact_metadata(build))


def render_page(
    build: NativeDiffBuild,
    layer: Any,
    *,
    offset: int = 0,
    limit: int = 80,
) -> str:
    """One bounded canonical YAML page for one layer artifact."""
    layer_id = str(layer)
    if layer_id not in SUPPORTED_LAYERS:
        raise ValueError(f"unsupported native payload layer: {layer_id}")
    relative = _relative(build.diff_id, f"{layer_id}.yaml")
    text = build.texts.get(relative)
    if text is None:
        raise ValueError(f"native payload layer is not part of this build: {layer_id}")
    page = artifacts.page_lines(text, offset=offset, limit=limit)
    return artifacts.canonical_yaml({
        "artifact": relative,
        "canonical_sha256": artifacts.sha256_text(text),
        "diff_id": build.diff_id,
        "layer": layer_id,
        "offset": page["offset"],
        "limit": page["limit"],
        "returned": page["returned"],
        "total": page["total"],
        "truncated": page["truncated"],
        "next_cursor": page["next_cursor"],
        "lines": list(page["lines"]),
    })


def render_summary(build: NativeDiffBuild, *, limit: int = MAX_TOON_ROWS) -> str:
    """Pure bounded TOON summary; full payloads stay in canonical artifacts."""
    stats = list(build.report["layer_stats"])
    size = max(1, min(int(limit or MAX_TOON_ROWS), MAX_TOON_ROWS))
    shown = stats[:size]
    rows = [
        [
            item["layer"], item["status"], item["loader"], item["source"],
            item["changed_count"], "|".join(str(f) for f in item["changed_fields"]),
            item["canonical_sha256"], item["line_count"], item["artifact"],
            item["next_cursor"],
        ]
        for item in shown
    ]
    return toon.render_toon(
        [toon.Table("native_layers", list(SUMMARY_FIELDS), rows)],
        summary={
            "contract": CONTRACT_NAME,
            "contract_version": build.report["contract_version"],
            "default_loader": build.report["default_loader"],
            "diff_id": build.diff_id,
            "artifact": build.report["artifact"],
            "canonical_sha256": build.report["canonical_sha256"],
            "next_cursor": build.report["next_cursor"],
            "status": build.report["status"],
            "total": len(stats),
            "returned": len(shown),
            "truncated": len(stats) > len(shown),
        },
    )


def determinism_report(first: NativeDiffBuild, second: NativeDiffBuild) -> dict[str, Any]:
    """Byte-for-byte comparison of two independent canonical builds."""
    mismatches: list[dict[str, str]] = []
    for relative in sorted(set(first.texts) | set(second.texts)):
        first_text = first.texts.get(relative)
        second_text = second.texts.get(relative)
        if first_text != second_text:
            mismatches.append({
                "artifact": relative,
                "reason": "canonical_text_mismatch",
                "first_sha256": artifacts.sha256_text(first_text) if first_text is not None else "",
                "second_sha256": artifacts.sha256_text(second_text) if second_text is not None else "",
            })
    return {
        "artifact_count": len(first.texts),
        "canonical_sha256": str(first.manifest["canonical_sha256"]),
        "deterministic": not mismatches,
        "diff_id": first.diff_id,
        "mismatches": mismatches,
    }


def render_determinism(report: Mapping[str, Any]) -> str:
    """Pure bounded TOON view of a two-build determinism comparison."""
    mismatches = [dict(item) for item in (report.get("mismatches") or [])]
    rows = [
        [
            item.get("artifact", ""), item.get("reason", ""),
            item.get("first_sha256", ""), item.get("second_sha256", ""),
        ]
        for item in mismatches[:MAX_TOON_ROWS]
    ]
    return toon.render_toon(
        [toon.Table("native_mismatches", list(DETERMINISM_FIELDS), rows)],
        summary={
            "artifact_count": int(report.get("artifact_count", 0)),
            "canonical_sha256": str(report.get("canonical_sha256", "")),
            "deterministic": bool(report.get("deterministic", False)),
            "diff_id": str(report.get("diff_id", "")),
            "mismatch_count": len(mismatches),
            "returned": len(rows),
            "truncated": len(mismatches) > len(rows),
        },
    )
