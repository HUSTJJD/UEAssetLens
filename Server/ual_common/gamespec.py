"""Deterministic GameSpec expansion and bounded offline validation.

The full GameSpec is persisted as a canonical YAML artifact. Model-facing
results are bounded TOON summaries plus repository-relative artifact pointers.
No inference service, engine compile, PIE, network access, clock, random ID or
local absolute path is used here. Concurrent transaction/revision modules are
imported lazily so this module remains importable before they land.
"""
from __future__ import annotations

import importlib
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from . import artifacts, extensions, gateway, settings
from .toon import Table, render_toon


def _revision_module() -> Any:
    """Resolve the revision module at call time so tests/hosts can inject it."""
    name = f"{__package__}.revision"
    module = sys.modules.get(name)
    return module if module is not None else importlib.import_module(name)

SUPPORTED_KEYWORDS: tuple[str, ...] = ("fps", "3c", "shooter", "射击")
GAME_SPEC_FIELDS: tuple[str, ...] = (
    "domain", "entities", "rules", "input", "ui", "assertions",
)
VALIDATION_STAGES: tuple[str, ...] = (
    "compile", "PIE", "snapshot", "assert", "repair", "rollback",
)
EXPANSION_SECTION_FIELDS: tuple[str, ...] = ("section", "count")
ERROR_FIELDS: tuple[str, ...] = ("code", "detail")
VALIDATION_STAGE_FIELDS: tuple[str, ...] = ("stage", "status", "result", "artifact")
ASSERTION_FIELDS: tuple[str, ...] = ("id", "status", "result")
ARTIFACT_PREFIX = ".ual"
_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
_LABEL_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")

IMPORT_SCHEMA_VERSION = "1"
IMPORT_ALLOWED_KEYS = frozenset({
    *GAME_SPEC_FIELDS,
    "template", "template_id", "template_version", "version",
    "game_spec", "spec", "assets", "assembly", "operations",
    "assertions", "assertion_checks", "validation", "schema_version",
})
IMPORT_FORBIDDEN_KEYS = frozenset({
    "impl", "handler", "eval", "exec", "code", "script", "callable", "pickle",
})
IMPORT_FORBIDDEN_KEY_PARTS = (
    "timestamp", "updated_at", "created_at", "generated_at", "modified_at",
    "datetime", "session", "request_id", "trace_id", "correlation_id",
    "uuid", "guid", "random", "nonce", "client_id", "run_id", "job_id",
    "local_path", "absolute_path", "host_path", "file_path",
    "impl", "handler", "eval", "exec", "code", "script", "callable", "pickle",
)
_EXTERNAL_MARKERS = (
    "vlm", "cloud", "gpu", "network", "http", "url", "socket", "endpoint",
    "inference", "openai", "anthropic", "comfyui", "trellis", "audiocraft",
    "unirig", "blender", "boto3", "requests", "urllib", "httpx",
    "transformers", "torch", "tensorflow", "cv2", "llm", "generative",
    "embedding", "diffusion", "vision_model", "localhost", "external_service",
    "model_service", "uuid", "random", "nonce", "session", "trace",
    "correlation",
)
_IMPORT_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*\Z")
_GAME_PATH_RE = re.compile(r"/Game(?:/[A-Za-z0-9_.\-]+)*/?\Z")
_ASSERTION_KINDS = frozenset({
    "required_keys", "unique_ids", "known_asset_ids", "whitelisted_tools",
    "required_stages", "non_empty", "forbidden_keys",
})
_ASSERTION_TARGETS = frozenset({
    "all", "game_spec", "game_spec.assertions", "assets", "assembly",
    "operations", "validation",
})
_TODO_MARKER = "TODO" + "(human)"
_IMPORT_ERROR_DETAILS = {
    "schema_invalid": "structured host GameSpec schema is incomplete",
    "forbidden_key": "executable or noise keys are not allowed",
    "forbidden_reference": "external references are not allowed",
    "relationship_invalid": "declared template asset and operation relations are inconsistent",
    "operation_invalid": "official tool whitelist validation failed",
    "template_invalid": "declarative template is unavailable",
    "artifact_write_failed": "cannot write canonical artifact",
    "payload_invalid": "host payload is not plain declarative data",
}


def _label(value: Any) -> str:
    """Bound caller-supplied labels so no path-like text reaches outputs."""
    text = str(value)
    return text if _LABEL_RE.fullmatch(text) and ".." not in text else "invalid"


def _official_tool_names(*, project_root: Path | None = None) -> set[str]:
    """Load exact bare tool names from the declarative gateway whitelist."""
    resolved = settings.load_namespace("gateway", project_root=project_root)
    rows: list[Any] = list(resolved.data.get("tools") or [])
    for rule in resolved.rules:
        tools = rule.get("tools")
        if isinstance(tools, list):
            rows.extend(tools)
    names = {
        str(item.get("name", ""))
        for item in rows
        if isinstance(item, dict) and item.get("name")
    }
    if not names or any(name.startswith("ual_") for name in names):
        raise ValueError("gateway whitelist is unavailable or has unqualified names")
    return names


def _validate_operations(
    operations: Any, *, project_root: Path | None = None
) -> list[dict[str, Any]]:
    """Reject any operation that is not an exact whitelisted bare tool name."""
    if not isinstance(operations, list) or not operations:
        raise ValueError("template operations must be a non-empty list")
    allowed = _official_tool_names(project_root=project_root)
    forbidden_keys = {"impl", "handler", "eval", "exec", "code", "script"}
    result: list[dict[str, Any]] = []
    for item in operations:
        if not isinstance(item, dict):
            raise ValueError("template operation must be a mapping")
        if forbidden_keys.intersection(item):
            raise ValueError("template operation must not carry executable references")
        for key in ("tool", "inverse"):
            name = str(item.get(key, ""))
            if name not in allowed:
                raise ValueError(f"operation {key} is not whitelisted: {name}")
            if name.startswith("ual_"):
                raise ValueError("bare wire names are forbidden")
        result.append(dict(item))
    return result


def load_template(
    template: str = "fps_3c", *, project_root: Path | None = None
) -> dict[str, Any]:
    """Resolve one declarative GameSpec template via settings.load_namespace()."""
    resolved = settings.load_namespace("templates", project_root=project_root)
    for rule in resolved.active_rules():
        identifiers = {str(rule.get("id", "")), str(rule.get("template_id", ""))}
        if template not in identifiers:
            continue
        payload = dict(rule)
        if str(payload.get("version", "1")) != "1":
            raise ValueError("template version must be 1")
        spec = payload.get("game_spec")
        if not isinstance(spec, dict) or any(key not in spec for key in GAME_SPEC_FIELDS):
            raise ValueError("GameSpec schema fields are incomplete")
        for key in ("assets", "assembly", "operations", "assertions", "validation"):
            if not isinstance(payload.get(key), list) and key != "validation":
                raise ValueError(f"template {key} must be a list")
        if not isinstance(payload.get("validation"), dict):
            raise ValueError("template validation must be a mapping")
        if any(key in payload for key in ("impl", "handler", "eval", "exec", "code")):
            raise ValueError("template must not declare executable references")
        payload["operations"] = _validate_operations(
            payload["operations"], project_root=project_root
        )
        return payload
    raise ValueError(f"template is unavailable: {template}")


def _match_intent(intent: Any) -> tuple[bool, tuple[str, ...]]:
    text = str(intent).strip().casefold()
    matched = tuple(keyword for keyword in SUPPORTED_KEYWORDS if keyword in text)
    return bool(matched), matched


def _expand_template(
    payload: dict[str, Any],
    *,
    template: str,
    project_root: Path | None,
) -> dict[str, Any]:
    """Invoke the allowlisted Python template builtin with declarative data."""
    loaded = extensions.load_registry(project_root=project_root)
    matches = loaded.registry.resolve(
        "*", capability="expand", namespace="template"
    )
    found: tuple[Any, Any] | None = None
    for spec, impl in matches:
        if spec.name == template or spec.key == f"template.{template}":
            found = (spec, impl)
            break
    if found is None or not callable(found[1]):
        raise ValueError("fps_3c expansion builtin is unavailable")
    spec, impl = found
    schema = dict(spec.schema)
    schema.update({
        "template_id": str(payload.get("template_id") or template),
        "version": str(payload.get("version") or "1"),
        "game_spec": payload["game_spec"],
        "assets": payload["assets"],
        "operations": payload["operations"],
        "assertions": payload["assertions"],
    })
    expanded = impl(
        {"intent": "fps_3c"},
        {"name": spec.name, "schema": schema},
    )
    if not isinstance(expanded, dict):
        raise ValueError("template builtin returned an invalid payload")
    return {
        "template_id": str(expanded.get("template_id") or template),
        "version": str(expanded.get("version") or "1"),
        "spec": dict(expanded.get("spec") or payload["game_spec"]),
        "assets": list(expanded.get("assets") or payload["assets"]),
        "operations": list(expanded.get("operations") or payload["operations"]),
        "assertions": list(expanded.get("assertions") or payload["assertions"]),
    }


def _relative(kind: str, game_spec_id: str) -> str:
    if not _ID_RE.fullmatch(game_spec_id):
        raise ValueError("game_spec_id must be a canonical sha256 digest")
    return artifacts.safe_relative(f"{kind}/{game_spec_id}.yaml")


def _pointer(relative: str) -> str:
    return f"{ARTIFACT_PREFIX}/{artifacts.safe_relative(relative)}"


def _section_rows(payload: dict[str, Any]) -> list[list[Any]]:
    spec = payload["spec"]
    return [
        ["schema_fields", len(GAME_SPEC_FIELDS)],
        ["entities", len(spec.get("entities") or [])],
        ["rules", len(spec.get("rules") or [])],
        ["input", len(spec.get("input") or [])],
        ["ui", len(spec.get("ui") or [])],
        ["assets", len(payload["assets"])],
        ["assembly", len(payload["assembly"])],
        ["operations", len(payload["operations"])],
        ["assertions", len(payload["assertions"])],
        ["validation_stages", len(payload["validation"].get("stages") or [])],
    ]


def render_expansion(report: dict[str, Any]) -> str:
    """Pure bounded TOON renderer for GameSpec expansion reports."""
    summary = dict(report.get("summary") or {})
    sections = [
        [str(row.get("section", "")), int(row.get("count", 0))]
        for row in (report.get("sections") or [])[:16]
    ]
    errors = [
        [str(row.get("code", "")), str(row.get("detail", ""))]
        for row in (report.get("errors") or [])[:8]
    ]
    return render_toon(
        [
            Table("sections", EXPANSION_SECTION_FIELDS, sections),
            Table("errors", ERROR_FIELDS, errors),
        ],
        summary=summary,
    )


def render_validation(report: dict[str, Any]) -> str:
    """Pure bounded TOON renderer for validation-loop reports."""
    summary = dict(report.get("summary") or {})
    stages = [
        [
            str(row.get("stage", "")), str(row.get("status", "")),
            str(row.get("result", "")), str(row.get("artifact", "")),
        ]
        for row in (report.get("stages") or [])[:8]
    ]
    assertions = [
        [
            str(row.get("id", "")), str(row.get("status", "")),
            str(row.get("result", "")),
        ]
        for row in (report.get("assertions") or [])[:32]
    ]
    errors = [
        [str(row.get("code", "")), str(row.get("detail", ""))]
        for row in (report.get("errors") or [])[:8]
    ]
    return render_toon(
        [
            Table("stages", VALIDATION_STAGE_FIELDS, stages),
            Table("assertions", ASSERTION_FIELDS, assertions),
            Table("errors", ERROR_FIELDS, errors),
        ],
        summary=summary,
    )


def _expansion_error(
    code: str, detail: str, *, template: str
) -> str:
    return render_expansion({
        "summary": {
            "status": "error",
            "template": _label(template),
            "supported_keywords": "|".join(SUPPORTED_KEYWORDS),
            "artifact": "",
            "canonical_sha256": "",
        },
        "sections": [],
        "errors": [{"code": code, "detail": detail}],
    })


def expand_game_spec(
    con: Any,
    intent: str,
    template: str = "fps_3c",
    *,
    project_root: Path | None = None,
    root: Path = Path(".ual"),
) -> str:
    """Expand a supported FPS 3C intent into a full GameSpec artifact."""
    supported, markers = _match_intent(intent)
    if not supported:
        return render_expansion({
            "summary": {
                "status": "unsupported_intent",
                "template": _label(template),
                "supported_keywords": "|".join(SUPPORTED_KEYWORDS),
                "artifact": "",
                "canonical_sha256": "",
            },
            "sections": [],
            "errors": [{
                "code": "unsupported_intent",
                "detail": "only FPS 3C keywords are supported",
            }],
        })

    try:
        payload_template = load_template(template, project_root=project_root)
        revision = _revision_module()

        raw_revision = str(revision.index_revision(con))
        game_spec_id = artifacts.digest({
            "intent": str(intent),
            "template": template,
            "index_revision": raw_revision,
        })
        expanded = _expand_template(
            payload_template, template=template, project_root=project_root
        )
        relative = _relative("gamespecs", game_spec_id)
        pointer = _pointer(relative)
        payload: dict[str, Any] = {
            "game_spec_id": game_spec_id,
            "index_revision_sha256": artifacts.digest({"index_revision": raw_revision}),
            "template": _label(template),
            "template_version": expanded["version"],
            "intent_markers": list(markers),
            "intent_sha256": artifacts.digest({"intent": str(intent)}),
            "spec": expanded["spec"],
            "assets": expanded["assets"],
            "assembly": payload_template["assembly"],
            "operations": expanded["operations"],
            "assertions": expanded["assertions"],
            "validation": payload_template["validation"],
            "artifact": pointer,
        }
        meta = artifacts.write_artifact(root, relative, payload)
        report = {
            "summary": {
                "status": "expanded",
                "game_spec_id": game_spec_id,
                "template": _label(template),
                "index_revision_sha256": payload["index_revision_sha256"],
                "intent_markers": "|".join(markers),
                "asset_count": len(payload["assets"]),
                "operation_count": len(payload["operations"]),
                "relation_count": len(payload["assembly"]),
                "assertion_count": len(payload["assertions"]),
                "artifact": meta["artifact"] and pointer,
                "canonical_sha256": meta["canonical_sha256"],
            },
            "sections": [
                {"section": section, "count": count}
                for section, count in _section_rows(payload)
            ],
            "errors": [],
        }
        return render_expansion(report)
    except ModuleNotFoundError:
        return _expansion_error(
            "integration_pending", "revision module is not available", template=template
        )
    except ValueError:
        return _expansion_error(
            "template_invalid", "declarative template validation failed", template=template
        )
    except OSError:
        return _expansion_error(
            "artifact_write_failed", "cannot write canonical artifact", template=template
        )


def _target(spec: dict[str, Any], name: str) -> Any:
    targets: dict[str, Any] = {
        "all": spec,
        "game_spec": spec.get("spec") or {},
        "game_spec.assertions": (spec.get("spec") or {}).get("assertions") or [],
        "assets": spec.get("assets") or [],
        "assembly": spec.get("assembly") or [],
        "operations": spec.get("operations") or [],
        "validation": spec.get("validation") or {},
    }
    return targets.get(name)


def _has_forbidden_key(value: Any, forbidden: set[str], depth: int = 0) -> str:
    if depth > 8:
        return "depth_limit"
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key) in forbidden:
                return str(key)
            hit = _has_forbidden_key(child, forbidden, depth + 1)
            if hit:
                return hit
    elif isinstance(value, (list, tuple)):
        for child in value:
            hit = _has_forbidden_key(child, forbidden, depth + 1)
            if hit:
                return hit
    return ""


def _required_keys(value: Any, required: list[Any]) -> tuple[bool, str]:
    keys = [str(item) for item in required]
    if isinstance(value, dict):
        missing = [key for key in keys if key not in value]
        return not missing, "ok" if not missing else "missing_" + "|".join(missing)
    if isinstance(value, list):
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                return False, f"item_{index}_not_mapping"
            missing = [key for key in keys if key not in item]
            if missing:
                return False, "missing_" + "|".join(missing)
        return True, "ok"
    return False, "unsupported_target"


def _unique_ids(value: Any) -> tuple[bool, str]:
    if not isinstance(value, list):
        return False, "unsupported_target"
    ids = [str(item.get("id", "")) for item in value if isinstance(item, dict)]
    if len(ids) != len(value) or any(not item for item in ids):
        return False, "missing_id"
    return len(ids) == len(set(ids)), "ok"


def _known_asset_ids(spec: dict[str, Any]) -> tuple[bool, str]:
    assets = spec.get("assets") or []
    relations = spec.get("assembly") or []
    ids = {
        str(item.get("id", ""))
        for item in assets
        if isinstance(item, dict) and item.get("id")
    }
    for index, item in enumerate(relations):
        if not isinstance(item, dict):
            return False, f"relation_{index}_not_mapping"
        if str(item.get("source", "")) not in ids or str(item.get("target", "")) not in ids:
            return False, f"relation_{index}_unknown_asset"
    return True, "ok"


def _whitelisted_tools(
    spec: dict[str, Any], *, project_root: Path | None
) -> tuple[bool, str]:
    allowed = _official_tool_names(project_root=project_root)
    for index, item in enumerate(spec.get("operations") or []):
        if not isinstance(item, dict):
            return False, f"operation_{index}_not_mapping"
        if str(item.get("tool", "")) not in allowed:
            return False, f"operation_{index}_tool"
        if str(item.get("inverse", "")) not in allowed:
            return False, f"operation_{index}_inverse"
    return True, "ok"


def _static_assertions(
    spec: dict[str, Any], *, project_root: Path | None
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in spec.get("assertions") or []:
        if not isinstance(item, dict):
            continue
        assertion_id = str(item.get("id", ""))
        kind = str(item.get("kind", ""))
        target = _target(spec, str(item.get("target", "")))
        status = "fail"
        result = "unsupported_kind"
        if kind == "required_keys":
            ok, detail = _required_keys(target, list(item.get("required") or []))
            status, result = ("pass" if ok else "fail"), detail
        elif kind == "unique_ids":
            ok, detail = _unique_ids(target or spec.get("assets") or [])
            status, result = ("pass" if ok else "fail"), detail
        elif kind == "known_asset_ids":
            ok, detail = _known_asset_ids(spec)
            status, result = ("pass" if ok else "fail"), detail
        elif kind == "whitelisted_tools":
            ok, detail = _whitelisted_tools(spec, project_root=project_root)
            status, result = ("pass" if ok else "fail"), detail
        elif kind == "required_stages":
            stages = [str(value) for value in (target or {}).get("stages", [])]
            expected = [str(value) for value in item.get("required") or []]
            ok = stages == expected
            status, result = ("pass" if ok else "fail"), ("ok" if ok else "stage_order")
        elif kind == "non_empty":
            ok = bool(target)
            status, result = ("pass" if ok else "fail"), ("ok" if ok else "empty")
        elif kind == "forbidden_keys":
            forbidden = {str(value) for value in item.get("forbidden") or []}
            hit = _has_forbidden_key(target, forbidden)
            status, result = ("fail" if hit else "pass"), (hit or "ok")
        rows.append({"id": assertion_id, "status": status, "result": result})
    return rows


def _safe_payload(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "depth_limit"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:256]
    if isinstance(value, dict):
        return {
            str(key): _safe_payload(child, depth + 1)
            for key, child in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_safe_payload(child, depth + 1) for child in value[:32]]
    return {"type": type(value).__name__}


def _host_result(
    stage: str,
    context: dict[str, Any],
    host_runner: Callable[[str, dict[str, Any]], Any],
) -> tuple[str, str]:
    try:
        raw = host_runner(stage, context)
    except Exception as exc:  # host exceptions are reduced to a bounded marker
        return "host_error", type(exc).__name__
    digest = artifacts.digest({"stage": stage, "result": _safe_payload(raw)})[:12]
    status = "host_reported"
    if isinstance(raw, dict):
        raw_status = str(raw.get("status", "")).casefold()
        if raw_status in {"fail", "failed", "error"}:
            status = "host_reported_fail"
        elif raw_status in {"deferred_offline", "skipped"}:
            status = raw_status
    elif isinstance(raw, str):
        first = raw.strip().splitlines()[0].casefold() if raw.strip() else ""
        if "fail" in first or "error" in first:
            status = "host_reported_fail"
    result = f"host_python:{digest}"
    if stage in {"compile", "PIE"}:
        result += "|engine_not_run_by_lens"
    return status, result


def _validation_error(code: str, detail: str, *, dry_run: bool) -> str:
    stages = [
        {
            "stage": stage,
            "status": "not_run",
            "result": code,
            "artifact": "",
        }
        for stage in VALIDATION_STAGES
    ]
    return render_validation({
        "summary": {
            "status": "error",
            "game_spec_id": "",
            "runner": "offline",
            "dry_run": dry_run,
            "artifact": "",
            "canonical_sha256": "",
            "assertions_passed": 0,
            "assertions_total": 0,
            "engine_validation": "not_performed",
        },
        "stages": stages,
        "assertions": [],
        "errors": [{"code": code, "detail": detail}],
    })


def run_validation_loop(
    con: Any,
    game_spec_id: str,
    runner: str = "offline",
    dry_run: bool = True,
    *,
    project_root: Path | None = None,
    root: Path = Path(".ual"),
    host_runner: Callable[[str, dict[str, Any]], Any] | None = None,
) -> str:
    """Run bounded validation; only deterministic static assertions can pass."""
    del con  # read-only connection is accepted for API symmetry, never mutated
    try:
        source_relative = _relative("gamespecs", game_spec_id)
    except ValueError:
        return _validation_error(
            "invalid_game_spec_id", "canonical digest required", dry_run=dry_run
        )
    source_pointer = _pointer(source_relative)
    if runner != "offline" and not callable(host_runner):
        return _validation_error(
            "python_host_runner_required",
            "injected runner must be a Python callable",
            dry_run=dry_run,
        )
    try:
        spec = artifacts.read_artifact(root, source_relative)
    except Exception:
        return _validation_error(
            "gamespec_artifact_missing",
            "canonical GameSpec artifact is unavailable",
            dry_run=dry_run,
        )
    if not isinstance(spec, dict):
        return _validation_error(
            "gamespec_artifact_invalid",
            "canonical GameSpec artifact must be a mapping",
            dry_run=dry_run,
        )
    try:
        assertions = _static_assertions(spec, project_root=project_root)
    except ValueError:
        return _validation_error(
            "gateway_whitelist_invalid",
            "declarative gateway whitelist is invalid",
            dry_run=dry_run,
        )
    passed = sum(1 for row in assertions if row["status"] == "pass")
    total = len(assertions)
    static_ok = total > 0 and passed == total
    runner_mode = "offline" if runner == "offline" else "host"
    validation_relative = _relative("validation", game_spec_id)
    validation_pointer = _pointer(validation_relative)
    context = {
        "game_spec_id": game_spec_id,
        "source_artifact": source_pointer,
        "runner": runner_mode,
        "dry_run": bool(dry_run),
        "assertion_count": total,
        "assertion_pass_count": passed,
    }
    stages: list[dict[str, str]] = []
    for stage in VALIDATION_STAGES:
        artifact = validation_pointer
        if stage in {"compile", "PIE", "snapshot"}:
            if runner_mode == "offline":
                status = "deferred_offline"
                result = {
                    "compile": "engine_compile_not_run",
                    "PIE": "pie_not_run",
                    "snapshot": "engine_snapshot_not_run",
                }[stage]
            else:
                status, result = _host_result(stage, context, host_runner)
        elif stage == "assert":
            status = "pass" if static_ok else "fail"
            result = f"static_assertions:{passed}/{total}"
            if runner_mode == "host":
                _host_result(stage, context, host_runner)
        elif stage in {"repair", "rollback"}:
            if runner_mode == "offline":
                status = "skipped" if static_ok else "deferred_offline"
                result = "not_required" if static_ok else "requires_host_runner"
            else:
                status, result = _host_result(stage, context, host_runner)
                if status in {"deferred_offline", "skipped"}:
                    status = "host_reported"
        else:  # pragma: no cover - fixed stage tuple
            status, result = "not_run", "unknown_stage"
        stages.append({
            "stage": stage,
            "status": status,
            "result": result,
            "artifact": artifact,
        })
    payload = {
        "game_spec_id": game_spec_id,
        "runner": runner_mode,
        "dry_run": bool(dry_run),
        "source_artifact": source_pointer,
        "artifact": validation_pointer,
        "engine_validation": "not_performed_by_lens",
        "host_runner": "python_callable" if runner_mode == "host" else "none",
        "assertions_passed": passed,
        "assertions_total": total,
        "stages": stages,
        "assertions": assertions,
    }
    try:
        meta = artifacts.write_artifact(root, validation_relative, payload)
    except OSError:
        return _validation_error(
            "artifact_write_failed", "cannot write canonical artifact", dry_run=dry_run
        )
    report = {
        "summary": {
            "status": "static_assertions_pass" if static_ok else "static_assertions_fail",
            "game_spec_id": game_spec_id,
            "runner": runner_mode,
            "dry_run": bool(dry_run),
            "assertions_passed": passed,
            "assertions_total": total,
            "artifact": meta["artifact"] and validation_pointer,
            "canonical_sha256": meta["canonical_sha256"],
            "engine_validation": "not_performed_by_lens",
        },
        "stages": stages,
        "assertions": assertions,
        "errors": [],
    }
    return render_validation(report)


def _reject(code: str) -> None:
    raise ValueError(code)


def _plain_import(value: Any, depth: int = 0) -> Any:
    if depth > 10:
        _reject("payload_invalid")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _plain_import(child, depth + 1)
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_plain_import(child, depth + 1) for child in value]
    _reject("payload_invalid")
    return None


def _external_marker(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in _EXTERNAL_MARKERS)


def _check_import_string(text: str) -> None:
    if _external_marker(text) or gateway.has_forbidden_text(text) or _TODO_MARKER in text:
        _reject("forbidden_reference")
    lowered = text.casefold()
    if "content" in lowered and "hash" in lowered:
        _reject("forbidden_key")


def _check_import_key(key: Any) -> None:
    if not isinstance(key, str) or not _IMPORT_KEY_RE.fullmatch(key):
        _reject("payload_invalid")
    lowered = key.casefold()
    if (
        lowered in IMPORT_FORBIDDEN_KEYS
        or any(part in lowered for part in IMPORT_FORBIDDEN_KEY_PARTS)
    ):
        _reject("forbidden_key")
    _check_import_string(key)


def _check_declarative_string(text: str) -> None:
    lowered = text.casefold()
    service_markers = (
        "vlm", "cloud", "gpu", "network", "http", "socket", "endpoint",
        "openai", "anthropic", "comfyui", "trellis", "audiocraft",
        "unirig", "blender", "localhost",
    )
    if (
        _label(text) == "invalid"
        or any(marker in lowered for marker in service_markers)
        or gateway.has_forbidden_text(text)
        or _TODO_MARKER in text
        or ("content" in lowered and "hash" in lowered)
    ):
        _reject("forbidden_reference")


def _walk_import_payload(
    value: Any, depth: int = 0, *, declarative: bool = False
) -> None:
    if depth > 10:
        _reject("payload_invalid")
    if isinstance(value, Mapping):
        for key, child in value.items():
            _check_import_key(key)
            child_declarative = declarative or str(key) in {
                "forbidden", "required", "spec_fields", "asset_ids", "operation_ids",
            }
            _walk_import_payload(
                child, depth + 1, declarative=child_declarative
            )
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _walk_import_payload(child, depth + 1, declarative=declarative)
        return
    if value is None or isinstance(value, (int, float, bool)):
        return
    if isinstance(value, str):
        if declarative:
            _check_declarative_string(value)
        else:
            _check_import_string(value)
        return
    _reject("payload_invalid")


def _labels(values: Any, code: str) -> list[str]:
    if not isinstance(values, list) or not values:
        _reject(code)
    labels: list[str] = []
    for item in values:
        if not isinstance(item, str):
            _reject(code)
        label = _label(item)
        if label == "invalid":
            _reject(code)
        labels.append(label)
    if len(labels) != len(set(labels)):
        _reject(code)
    return labels


def _extract_spec(payload: Mapping) -> dict[str, Any]:
    nested = [key for key in ("game_spec", "spec") if key in payload]
    if len(nested) > 1:
        _reject("schema_invalid")
    if nested:
        raw = payload[nested[0]]
    else:
        raw = {key: payload.get(key) for key in GAME_SPEC_FIELDS}
    if not isinstance(raw, Mapping) or set(raw) != set(GAME_SPEC_FIELDS):
        _reject("schema_invalid")
    spec = dict(_plain_import(raw))
    if not isinstance(spec.get("domain"), str) or _label(spec["domain"]) == "invalid":
        _reject("schema_invalid")
    for key in ("entities", "rules", "input", "ui", "assertions"):
        values = spec.get(key)
        if not isinstance(values, list) or not values:
            _reject("schema_invalid")
        identifiers: list[str] = []
        for item in values:
            if isinstance(item, Mapping):
                identifier = _label(item.get("id", ""))
            elif isinstance(item, str):
                identifier = _label(item)
            else:
                _reject("schema_invalid")
            if identifier == "invalid":
                _reject("schema_invalid")
            identifiers.append(identifier)
        if len(identifiers) != len(set(identifiers)):
            _reject("schema_invalid")
    return spec


def _operation_tool_map(operations: Any) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    if not isinstance(operations, list):
        return result
    for item in operations:
        if isinstance(item, Mapping) and item.get("id"):
            result[_label(item["id"])] = (
                str(item.get("tool", "")), str(item.get("inverse", "")),
            )
    return result


def _declared_tool_map(
    embedded: Mapping, operations: Any
) -> dict[str, tuple[str, str]]:
    raw = embedded.get("operation_tools")
    if not isinstance(raw, Mapping):
        return _operation_tool_map(operations)
    declared: dict[str, tuple[str, str]] = {}
    for key, value in raw.items():
        identifier = _label(key)
        if isinstance(value, Mapping):
            tool, inverse = str(value.get("tool", "")), str(value.get("inverse", ""))
        elif isinstance(value, (list, tuple)) and len(value) >= 2:
            tool, inverse = str(value[0]), str(value[1])
        else:
            _reject("relationship_invalid")
        if identifier == "invalid":
            _reject("relationship_invalid")
        declared[identifier] = (tool, inverse)
    return declared


def _ids_from_rows(rows: Any, code: str) -> list[str]:
    if not isinstance(rows, list) or not rows:
        _reject(code)
    identifiers: list[str] = []
    for item in rows:
        if isinstance(item, Mapping):
            identifier = _label(item.get("id", ""))
        elif isinstance(item, str):
            identifier = _label(item)
        else:
            _reject(code)
        if identifier == "invalid":
            _reject(code)
        identifiers.append(identifier)
    if len(identifiers) != len(set(identifiers)):
        _reject(code)
    return identifiers


def _template_declaration(
    payload: Mapping, *, project_root: Path | None
) -> dict[str, Any]:
    raw = payload.get("template")
    if raw is None:
        raw = payload.get("template_id") or "fps_3c"
    embedded = _plain_import(raw) if isinstance(raw, Mapping) else None
    loaded: dict[str, Any] = {}
    if isinstance(raw, str):
        template_id = _label(raw)
        if template_id == "invalid":
            _reject("template_invalid")
        try:
            loaded = dict(load_template(template_id, project_root=project_root))
        except ValueError:
            _reject("template_invalid")
        _walk_import_payload(loaded)
    elif embedded is not None:
        template_id = _label(
            embedded.get("id") or embedded.get("template_id") or "fps_3c"
        )
        if template_id == "invalid" or str(embedded.get("version") or "1") != "1":
            _reject("template_invalid")
        if "game_spec" in embedded:
            spec = embedded["game_spec"]
            if not isinstance(spec, Mapping) or not set(GAME_SPEC_FIELDS) <= set(spec):
                _reject("template_invalid")
        if isinstance(embedded.get("operations"), list):
            try:
                embedded["operations"] = _validate_operations(
                    embedded["operations"], project_root=project_root
                )
            except ValueError:
                _reject("operation_invalid")
            loaded = dict(embedded)
        else:
            try:
                loaded = dict(load_template(template_id, project_root=project_root))
                _walk_import_payload(loaded)
            except ValueError:
                loaded = {}
    else:
        _reject("template_invalid")

    assets_data = loaded.get("assets") if isinstance(loaded.get("assets"), list) else []
    assembly_data = loaded.get("assembly") if isinstance(loaded.get("assembly"), list) else []
    operations_data = loaded.get("operations") if isinstance(loaded.get("operations"), list) else []
    assertions_data = loaded.get("assertions") if isinstance(loaded.get("assertions"), list) else []
    validation_data = loaded.get("validation") if isinstance(loaded.get("validation"), Mapping) else {}
    if embedded is not None:
        assets_data = embedded.get("assets") if isinstance(embedded.get("assets"), list) else assets_data
        assembly_data = embedded.get("assembly") if isinstance(embedded.get("assembly"), list) else assembly_data
        operations_data = embedded.get("operations") if isinstance(embedded.get("operations"), list) else operations_data
        assertions_data = embedded.get("assertions") if isinstance(embedded.get("assertions"), list) else assertions_data
        validation_data = embedded.get("validation") if isinstance(embedded.get("validation"), Mapping) else validation_data

    declared_asset_ids = (
        embedded.get("asset_ids") if embedded is not None else None
    ) or _ids_from_rows(assets_data, "template_invalid")
    declared_operation_ids = (
        embedded.get("operation_ids") if embedded is not None else None
    ) or _ids_from_rows(operations_data, "template_invalid")
    spec_fields = (
        embedded.get("spec_fields") if embedded is not None else None
    ) or (
        list(loaded["game_spec"])
        if isinstance(loaded.get("game_spec"), Mapping) and loaded["game_spec"]
        else list(GAME_SPEC_FIELDS)
    )
    version = str(
        (embedded.get("version") if embedded is not None else None)
        or loaded.get("version")
        or "1"
    )
    return {
        "id": template_id,
        "version": version,
        "spec_fields": _labels(spec_fields, "template_invalid"),
        "asset_ids": _labels(declared_asset_ids, "template_invalid"),
        "operation_ids": _labels(declared_operation_ids, "template_invalid"),
        "operation_tools": _declared_tool_map(
            embedded if embedded is not None else {}, operations_data
        ),
        "assets": _plain_import(assets_data),
        "assembly": _plain_import(assembly_data),
        "operations": _plain_import(operations_data),
        "assertions": _plain_import(assertions_data),
        "validation": _plain_import(validation_data),
    }


def _normalize_assets(
    value: Any, declaration: Mapping
) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    rows = value if value is not None else declaration["assets"]
    if not isinstance(rows, list) or not rows:
        _reject("relationship_invalid")
    normalized: list[dict[str, Any]] = []
    identifiers: list[str] = []
    paths: set[str] = set()
    for item in rows:
        if isinstance(item, str):
            row = {"id": _label(item)}
        elif isinstance(item, Mapping):
            row = dict(_plain_import(item))
        else:
            _reject("relationship_invalid")
        identifier = _label(row.get("id", ""))
        if identifier == "invalid":
            _reject("relationship_invalid")
        path = row.get("asset_path")
        if path is not None:
            if not isinstance(path, str) or not _GAME_PATH_RE.fullmatch(path):
                _reject("relationship_invalid")
            paths.add(path)
        normalized.append(row)
        identifiers.append(identifier)
    if (
        len(identifiers) != len(set(identifiers))
        or set(identifiers) != set(declaration["asset_ids"])
    ):
        _reject("relationship_invalid")
    return normalized, set(identifiers), paths


def _normalize_assembly(
    value: Any, declaration: Mapping, asset_ids: set[str]
) -> list[dict[str, Any]]:
    rows = value if value is not None else declaration["assembly"]
    if not isinstance(rows, list) or not rows:
        _reject("relationship_invalid")
    normalized: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, Mapping):
            _reject("relationship_invalid")
        row = dict(_plain_import(item))
        source = _label(row.get("source", ""))
        target = _label(row.get("target", ""))
        relation = _label(row.get("relation", ""))
        if (
            source == "invalid" or target == "invalid" or relation == "invalid"
            or source not in asset_ids or target not in asset_ids
        ):
            _reject("relationship_invalid")
        normalized.append(row)
    return normalized


def _validate_operation_paths(arguments: Any, asset_paths: set[str]) -> None:
    if not isinstance(arguments, Mapping) or not asset_paths:
        return
    for key in ("asset", "assets"):
        raw = arguments.get(key)
        if isinstance(raw, str):
            values: list[Any] = [raw]
        elif isinstance(raw, (list, tuple)):
            values = list(raw)
        else:
            continue
        for value in values:
            if (
                isinstance(value, str)
                and value.startswith("/Game/")
                and value not in asset_paths
            ):
                _reject("relationship_invalid")


def _normalize_operations(
    value: Any,
    declaration: Mapping,
    asset_paths: set[str],
    *,
    project_root: Path | None,
) -> list[dict[str, Any]]:
    rows = value if value is not None else declaration["operations"]
    if not isinstance(rows, list) or not rows:
        _reject("operation_invalid")
    try:
        operations = _validate_operations(rows, project_root=project_root)
    except ValueError:
        _reject("operation_invalid")
    identifiers: list[str] = []
    for operation in operations:
        identifier = _label(operation.get("id", ""))
        if identifier == "invalid":
            _reject("relationship_invalid")
        declared = declaration["operation_tools"].get(identifier)
        if declared and (
            str(operation.get("tool", "")), str(operation.get("inverse", ""))
        ) != declared:
            _reject("relationship_invalid")
        _validate_operation_paths(operation.get("arguments"), asset_paths)
        identifiers.append(identifier)
    if (
        len(identifiers) != len(set(identifiers))
        or set(identifiers) != set(declaration["operation_ids"])
    ):
        _reject("relationship_invalid")
    return operations


def _normalize_assertions(
    payload: Mapping, declaration: Mapping
) -> list[dict[str, Any]]:
    rows = payload.get("assertion_checks")
    top = payload.get("assertions")
    if rows is None and isinstance(top, list) and all(
        isinstance(item, Mapping) and "kind" in item for item in top
    ):
        rows = top
    if rows is None:
        rows = declaration["assertions"]
    if not isinstance(rows, list) or not rows:
        _reject("schema_invalid")
    normalized: list[dict[str, Any]] = []
    identifiers: list[str] = []
    for item in rows:
        if not isinstance(item, Mapping):
            _reject("schema_invalid")
        row = dict(_plain_import(item))
        identifier = _label(row.get("id", ""))
        kind = str(row.get("kind", ""))
        target = _label(row.get("target", ""))
        if (
            identifier == "invalid" or target == "invalid"
            or kind not in _ASSERTION_KINDS or target not in _ASSERTION_TARGETS
        ):
            _reject("schema_invalid")
        for key in ("required", "forbidden"):
            if key in row and not isinstance(row[key], list):
                _reject("schema_invalid")
        normalized.append(row)
        identifiers.append(identifier)
    if len(identifiers) != len(set(identifiers)):
        _reject("schema_invalid")
    return normalized


def _normalize_validation(
    payload: Mapping, declaration: Mapping
) -> dict[str, Any]:
    raw = payload.get("validation")
    if raw is None:
        raw = declaration["validation"]
    if not isinstance(raw, Mapping):
        _reject("schema_invalid")
    validation = dict(_plain_import(raw))
    stages = validation.get("stages")
    if not isinstance(stages, list) or [str(item) for item in stages] != list(
        VALIDATION_STAGES
    ):
        _reject("schema_invalid")
    return validation


def _normalize_import(
    payload: Any, *, project_root: Path | None
) -> tuple[dict[str, Any], str]:
    if not isinstance(payload, Mapping):
        _reject("payload_invalid")
    if set(payload) - IMPORT_ALLOWED_KEYS:
        _reject("schema_invalid")
    _walk_import_payload(payload)
    spec = _extract_spec(payload)
    declaration = _template_declaration(payload, project_root=project_root)
    if set(declaration["spec_fields"]) != set(GAME_SPEC_FIELDS):
        _reject("template_invalid")
    assets, asset_ids, asset_paths = _normalize_assets(
        payload.get("assets"), declaration
    )
    assembly = _normalize_assembly(payload.get("assembly"), declaration, asset_ids)
    operations = _normalize_operations(
        payload.get("operations"), declaration, asset_paths,
        project_root=project_root,
    )
    assertions = _normalize_assertions(payload, declaration)
    validation = _normalize_validation(payload, declaration)
    template_summary = {
        "id": declaration["id"],
        "version": declaration["version"],
        "spec_fields": list(declaration["spec_fields"]),
        "asset_ids": list(declaration["asset_ids"]),
        "operation_ids": list(declaration["operation_ids"]),
    }
    normalized = {
        "kind": "host_gamespec_import",
        "schema_version": IMPORT_SCHEMA_VERSION,
        "source": "structured_host",
        "template": template_summary,
        "template_version": declaration["version"],
        "spec": spec,
        "assets": assets,
        "assembly": assembly,
        "operations": operations,
        "assertions": assertions,
        "validation": validation,
        "source_sha256": artifacts.digest(_plain_import(payload)),
    }
    return normalized, str(declaration["id"])


def _render_import_report(report: dict[str, Any]) -> str:
    summary = dict(report.get("summary") or {})
    sections = [
        [str(row.get("section", "")), int(row.get("count", 0))]
        for row in (report.get("sections") or [])[:16]
    ]
    errors = [
        [str(row.get("code", "")), str(row.get("detail", ""))]
        for row in (report.get("errors") or [])[:8]
    ]
    return render_toon(
        [
            Table("sections", EXPANSION_SECTION_FIELDS, sections),
            Table("errors", ERROR_FIELDS, errors),
        ],
        summary=summary,
    )


def _import_error(code: str, *, template: str) -> str:
    return _render_import_report({
        "summary": {
            "status": "rejected",
            "game_spec_id": "",
            "template": _label(template),
            "source": "structured_host",
            "asset_count": 0,
            "operation_count": 0,
            "relation_count": 0,
            "assertion_count": 0,
            "artifact": "",
            "canonical_sha256": "",
            "sha256": "",
            "engine_validation": "not_performed_by_lens",
            "language_generation": "host_side",
        },
        "sections": [],
        "errors": [{
            "code": code,
            "detail": gateway.bounded_text(_IMPORT_ERROR_DETAILS[code], 80),
        }],
    })


def _template_label(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return ""
    raw = payload.get("template")
    if isinstance(raw, str):
        return _label(raw)
    if isinstance(raw, Mapping):
        return _label(raw.get("id") or raw.get("template_id") or "")
    if isinstance(payload.get("template_id"), str):
        return _label(payload["template_id"])
    return ""


def _error_code(exc: ValueError) -> str:
    text = str(exc).casefold()
    if text in _IMPORT_ERROR_DETAILS:
        return text
    if "whitelist" in text or "operation" in text:
        return "operation_invalid"
    if "template" in text:
        return "template_invalid"
    if "artifact" in text:
        return "artifact_write_failed"
    return "payload_invalid"


def _import_root(project_root: Path | None, root: Path) -> Path:
    base = Path(root)
    if not base.is_absolute() and project_root is not None:
        base = Path(project_root) / base
    if base.name != ARTIFACT_PREFIX:
        base = base / ARTIFACT_PREFIX
    return base


def import_game_spec(
    payload: Any,
    *,
    project_root: Path | None = None,
    root: Path = Path(".ual"),
) -> str:
    """Import one structured host GameSpec as a canonical artifact, offline."""
    template = _template_label(payload)
    try:
        normalized, template = _normalize_import(
            payload, project_root=project_root
        )
        game_spec_id = artifacts.digest(normalized)
        relative = _relative("gamespecs", game_spec_id)
        pointer = _pointer(relative)
        artifact_payload = {
            **normalized,
            "game_spec_id": game_spec_id,
            "artifact": pointer,
        }
        meta = artifacts.write_artifact(
            _import_root(project_root, root), relative, artifact_payload
        )
        report = {
            "summary": {
                "status": "imported",
                "game_spec_id": game_spec_id,
                "template": _label(template),
                "source": "structured_host",
                "asset_count": len(artifact_payload["assets"]),
                "operation_count": len(artifact_payload["operations"]),
                "relation_count": len(artifact_payload["assembly"]),
                "assertion_count": len(artifact_payload["assertions"]),
                "artifact": pointer,
                "canonical_sha256": meta["canonical_sha256"],
                "sha256": meta["canonical_sha256"],
                "engine_validation": "not_performed_by_lens",
                "language_generation": "host_side",
            },
            "sections": [
                {"section": section, "count": count}
                for section, count in _section_rows(artifact_payload)
            ],
            "errors": [],
        }
        return _render_import_report(report)
    except ValueError as exc:
        return _import_error(_error_code(exc), template=template)
    except OSError:
        return _import_error("artifact_write_failed", template=template)
