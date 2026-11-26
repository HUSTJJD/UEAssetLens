"""Deterministic, offline edit transactions for UEAssetLens.

Plan / Apply / Verify / Rollback / RequestRescan never write a ``.uasset`` and
never write the DuckDB index.  They persist canonical YAML artifacts under a
repository-relative root (``.ual`` by default), stage incremental rescan requests
and delegate only to an injected Python executor supplied by the host.  Every
public function returns bounded TOON built exclusively with ``toon.render_toon``.

``revision`` and ``lifecycle`` are delivered by the index/lifecycle modules.
They are resolved lazily so that tool dispatch still works while those modules
are unavailable; a missing or failing lifecycle hook degrades to a no-op and a
missing revision API degrades to an empty, deterministic revision marker.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from . import artifacts, gateway, toon

revision: Any = None
lifecycle: Any = None

_PLAN_FIELDS = ("id", "tool", "accessMode", "inverse", "risk", "arguments_sha256")
_DIFF_FIELDS = ("id", "tool", "accessMode", "risk", "arguments_sha256", "change")
_RESULT_FIELDS = ("id", "tool", "accessMode", "status", "dry_run", "result_status", "reason")
_VERIFY_FIELDS = ("id", "tool", "status", "index_state", "detail")
_RESCAN_RELATIVE = "staging/rescan.yaml"
_OFFLINE_RISKS = ("offline_host_required", "incremental_rescan_required")
_GAME_PATH_RE = re.compile(r"^/Game(?:/[A-Za-z0-9_.\-]+)*/?$")

_BASELINE_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _bounded_baseline_value(value: Any, depth: int = 0) -> Any:
    """Reduce index-derived baseline state to bounded canonical scalars."""
    if depth > 4:
        return ""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return gateway.bounded_text(value, 160)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:16]:
            text = str(key)
            if _BASELINE_KEY_RE.match(text):
                result[text] = _bounded_baseline_value(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_bounded_baseline_value(item, depth + 1) for item in list(value)[:16]]
    return ""


def _record(con: Any, asset_path: str) -> Any:
    """Read one bounded asset record; never expose a raw index dump."""
    if not asset_path:
        return None
    try:
        from . import db
    except ImportError:
        return None
    getter = getattr(db, "get_asset", None)
    if not callable(getter):
        return None
    try:
        return getter(con, asset_path)
    except Exception:
        return None


def _first_named_path(arguments: Mapping[str, Any], names: Sequence[str]) -> str:
    for name in names:
        for text in _iter_strings(arguments.get(name)):
            candidate = str(text).strip()
            if _is_game_path(candidate):
                return candidate
    return ""


def _capture_baseline(
    con: Any, arguments: Mapping[str, Any], raw_baseline: Any
) -> dict[str, Any]:
    """Build a deterministic plan-time baseline snapshot from read-only state."""
    paths = sorted({
        str(text).strip()
        for text in _iter_strings(arguments)
        if _is_game_path(str(text).strip())
    })
    asset = _first_named_path(
        arguments, ("asset_path", "object_path", "asset", "assets", "path")
    ) or (paths[0] if paths else "")
    source = _first_named_path(
        arguments, ("source", "source_path", "from")
    ) or asset
    destination = _first_named_path(
        arguments, ("destination", "destination_path", "target", "target_path", "to")
    ) or (paths[-1] if len(paths) > 1 else "")
    folder = _first_named_path(
        arguments, ("folder", "folder_path", "path")
    ) or (paths[0] if paths else "")
    collection = _first_named_path(
        arguments, ("collection", "collection_path")
    ) or (paths[0] if paths else "")
    record = _record(con, asset)
    domain = record.get("domain_spec") if isinstance(record, Mapping) else {}
    domain = domain if isinstance(domain, Mapping) else {}
    enabled = domain.get("nanite_enabled")
    transform = domain.get("transform")
    parameter_value = domain.get("parameter_value")
    baseline: dict[str, Any] = {
        "asset": asset,
        "assets": paths,
        "source": source,
        "destination": destination,
        "folder": folder,
        "collection": collection,
        "material": _bounded_baseline_value(domain.get("material") or ""),
        "tags": _bounded_baseline_value(
            (record.get("taxonomy") if isinstance(record, Mapping) else [])
            or domain.get("tags") or []
        ),
        "metadata": _bounded_baseline_value(domain.get("metadata") or {}),
        "properties": _bounded_baseline_value(domain.get("properties") or {}),
        "enabled": bool(enabled) if isinstance(enabled, bool) else False,
        "transform": _bounded_baseline_value(transform) if isinstance(transform, Mapping) else {},
        "parameter_value": _bounded_baseline_value(parameter_value),
        "editor_open": False,
        "asset_exists": isinstance(record, Mapping),
        "destination_exists": isinstance(_record(con, destination), Mapping),
        "folder_exists": isinstance(_record(con, folder), Mapping),
        "collection_exists": isinstance(_record(con, collection), Mapping),
    }
    if isinstance(raw_baseline, Mapping):
        for key, value in raw_baseline.items():
            text = str(key)
            if _BASELINE_KEY_RE.match(text):
                baseline[text] = _bounded_baseline_value(value)
    return baseline


def _require_host_approval(
    op_name: str, approved_by: Any, approvals: Any, executor: Any
) -> tuple[str, list[str]]:
    """Fail closed for one non-dry-run Execute op with explicit approval id."""
    approver = _safe(approved_by, 120)
    supplied = sorted({
        str(item).strip() for item in _split_values(approvals)
        if str(item).strip()
    })
    if not approver or supplied != [op_name] or not callable(executor):
        raise ValueError(
            f"{op_name} requires approved_by, explicit approval id and an injected host executor"
        )
    return approver, supplied


def _invoke_host_executor(
    executor: Any,
    op_name: str,
    approved_by: str,
    approvals: Sequence[str],
    arguments: Mapping[str, Any],
) -> None:
    """Delegate one structured, deterministic host operation to a Python callable."""
    context = {
        "approved_by": approved_by,
        "approvals": list(approvals),
        "dry_run": False,
        "arguments": _bounded_baseline_value(dict(arguments)),
    }
    try:
        try:
            executor(op_name, context)
        except TypeError:
            executor({"tool": op_name, "arguments": context})
    except Exception as exc:
        raise ValueError(f"host executor failed: {type(exc).__name__}") from exc


_INTENT_TOOLS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("tag", "tags"), "AddUserAssetTags"),
    (("metadata", "meta"), "UpdateMetadataTags"),
    (("material",), "SetMaterial"),
    (("scalar", "parameter", "param"), "SetScalarParameter"),
    (("vector",), "SetVectorParameter"),
    (("texture",), "SetTextureParameter"),
    (("switch",), "SetStaticSwitchParameter"),
    (("override",), "SetParameterOverride"),
    (("lod", "lods"), "GenerateLods"),
    (("nanite",), "SetNaniteEnabled"),
    (("folder", "directory"), "CreateFolder"),
    (("collection",), "AddAssetsToCollection"),
    (("transform",), "SetActorTransform"),
    (("property", "properties"), "SetProperties"),
    (("duplicate", "clone", "copy"), "Duplicate"),
    (("move", "rename", "relocate"), "Move"),
    (("delete", "remove", "destroy"), "Delete"),
    (("reload", "refresh"), "ReloadAsset"),
    (("save", "persist"), "SaveAsset"),
)


def _revision_module() -> Any:
    global revision
    if revision is None:
        try:
            from . import revision as module
        except ImportError:
            return None
        revision = module
    return revision


def _lifecycle_module() -> Any:
    global lifecycle
    if lifecycle is None:
        try:
            from . import lifecycle as module
        except ImportError:
            return None
        lifecycle = module
    return lifecycle


def _index_revision(con: Any) -> Any:
    """Raw revision value for plan_id; artifacts/reports render it via ``_safe``."""
    module = _revision_module()
    if module is None or not hasattr(module, "index_revision"):
        return ""
    try:
        return module.index_revision(con)
    except Exception:
        return ""


def _emit(event: str, payload: dict, project_root: Path | None, root: Path) -> None:
    module = _lifecycle_module()
    if module is None or not hasattr(module, "emit"):
        return
    # lifecycle writers take a filesystem root; the pointer they return stays
    # repository-relative and the absolute root is never persisted in content.
    base = _artifact_root(project_root, root)
    try:
        module.emit(event, payload, project_root=project_root, root=base)
    except TypeError:
        try:
            module.emit(event, payload)
        except Exception:
            return
    except Exception:
        return


def _artifact_root(project_root: Path | None, root: Path) -> Path:
    base = Path(root)
    if not base.is_absolute() and project_root is not None:
        return Path(project_root) / base
    return base


def _root_label(root: Path) -> str:
    return Path(root).name or ".ual"


def _pointer(root: Path, relative: str) -> str:
    """Repository-relative artifact pointer; never contains a local absolute root."""
    return f"{_root_label(root)}/{artifacts.safe_relative(relative)}"


def _safe(value: Any, limit: int = 160) -> str:
    return gateway.bounded_text(value, limit)


def _bounded_limit(value: Any, default: int = 100) -> int:
    return gateway.bounded_limit(value, default)


def _split_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,\s;]+", value) if part.strip()]
    if isinstance(value, Mapping):
        return [value.get("asset_path") or value.get("path") or ""]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items: list[Any] = []
        for item in value:
            items.extend(_split_values(item))
        return items
    return [value]


def _is_game_path(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(_GAME_PATH_RE.match(value))
        and not gateway.has_forbidden_text(value)
    )


def _in_scope(path: str, scope: str) -> bool:
    base = scope.rstrip("/")
    return path == base or path.startswith(base + "/")


def _normalise_scope(scope: Any) -> str:
    text = str(scope or "/Game").strip()
    if not _is_game_path(text):
        raise ValueError("scope must be a /Game path")
    return text


def _normalise_candidates(candidates: Any, scope: str) -> list[str]:
    paths: list[str] = []
    for value in _split_values(candidates):
        text = str(value).strip()
        if not text:
            continue
        if not _is_game_path(text) or not _in_scope(text, scope):
            raise ValueError("candidates must be /Game asset paths inside scope")
        if text not in paths:
            paths.append(text)
    return sorted(paths)


def _iter_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        out: list[str] = []
        for item in value.values():
            out.extend(_iter_strings(item))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(_iter_strings(item))
        return out
    return []


def _paths_from_ops(ops: Sequence[Any]) -> list[str]:
    paths: set[str] = set()
    for op in ops:
        if isinstance(op, Mapping):
            for text in _iter_strings(op.get("arguments")):
                if _is_game_path(text):
                    paths.add(text)
    return sorted(paths)


def _normalise_operation(
    raw: Any, gw: gateway.Gateway, index: int, scope: str, con: Any
) -> dict:
    if not isinstance(raw, Mapping):
        raise ValueError("each operation must be a mapping")
    if not isinstance(raw.get("arguments"), Mapping):
        raise ValueError("operation arguments must be a mapping")
    tool = str(raw.get("tool") or "")
    spec = gw.spec(tool)
    if not spec:
        raise ValueError("operation tool is not in the gateway whitelist")
    arguments = copy.deepcopy(dict(raw.get("arguments") or {}))
    baseline = _capture_baseline(con, arguments, raw.get("baseline"))
    operation = {
        "id": str(raw.get("id") or f"op-{index + 1:03d}"),
        "tool": tool,
        "arguments": arguments,
        "baseline": baseline,
        "accessMode": spec["accessMode"],
        "inverse": spec["inverse"],
        "risk": spec["risk"],
    }
    ok, reason = gw.validate(operation)
    if not ok:
        raise ValueError(f"operation rejected: {reason}")
    for text in _iter_strings(arguments) + _iter_strings(baseline):
        if text.startswith("/") and (not _is_game_path(text) or not _in_scope(text, scope)):
            raise ValueError("operation asset path must be a /Game path inside scope")
    return operation


def _operations_from_yaml(
    text: str, gw: gateway.Gateway, cap: int, scope: str, con: Any
) -> list[dict]:
    source = str(text or "")
    if not source.strip():
        return []
    try:
        doc = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise ValueError("operations_yaml is not valid YAML") from exc
    if isinstance(doc, Mapping):
        raw_ops = doc.get("operations", doc.get("ops", []))
    else:
        raw_ops = doc
    if not isinstance(raw_ops, list):
        raise ValueError("operations_yaml must contain an operation list")
    if len(raw_ops) > cap:
        raise ValueError("operation count exceeds max_ops")
    return [
        _normalise_operation(item, gw, index, scope, con)
        for index, item in enumerate(raw_ops)
    ]


def _operations_from_intent(
    intent: str, candidates: Sequence[str], scope: str, cap: int,
    gw: gateway.Gateway, con: Any,
) -> list[dict]:
    tokens = set(re.findall(r"[a-z0-9_]+", intent.lower()))
    tool = ""
    for keywords, candidate_tool in _INTENT_TOOLS:
        if tokens.intersection(keywords) and gw.spec(candidate_tool):
            tool = candidate_tool
            break
    if not tool:
        tool = "SaveAsset" if gw.spec("SaveAsset") else gw.tools(1)[0]["name"]
    arguments_list: list[dict] = (
        [{"asset_path": path} for path in candidates] if candidates else [{}]
    )
    operations = [
        _normalise_operation(
            {
                "id": f"op-{index + 1:03d}",
                "tool": tool,
                "arguments": dict(arguments),
            },
            gw,
            index,
            scope,
            con,
        )
        for index, arguments in enumerate(arguments_list[:cap])
    ]
    return operations


def _diff_rows(ops: Sequence[Any]) -> list[dict]:
    rows: list[dict] = []
    for op in ops:
        if not isinstance(op, Mapping):
            continue
        arguments = op.get("arguments") if isinstance(op.get("arguments"), Mapping) else {}
        rows.append(
            {
                "id": _safe(op.get("id"), 80),
                "tool": _safe(op.get("tool"), 80),
                "accessMode": _safe(op.get("accessMode"), 32),
                "risk": _safe(op.get("risk"), 32),
                "arguments_sha256": gateway.arguments_digest(arguments),
                "change": "planned",
            }
        )
    return rows


def _diff_preview_text(rows: Sequence[dict]) -> str:
    """TOON preview only; the baseline revision lives in the plan, not the text.

    A ``query_summary:`` header would be escaped by canonical YAML into a
    ``y:\\n`` sequence, which naive local-path gates misread as a drive path.
    """
    table = toon.Table(
        "diff_preview",
        _DIFF_FIELDS,
        [[row[field] for field in _DIFF_FIELDS] for row in rows],
    )
    return toon.render_toon([table])


def _write_plan(base: Path, relative: str, payload: dict) -> dict:
    return artifacts.write_artifact(base, relative, payload)


def _read_plan(base: Path, plan_id: Any, root: Path) -> tuple[dict, str, str]:
    pid = str(plan_id or "").strip()
    relative = artifacts.safe_relative(f"plans/{pid}.yaml")
    try:
        payload = artifacts.read_artifact(base, relative)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"plan artifact not found: {relative}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("plan artifact is not a mapping")
    return dict(payload), relative, _pointer(root, relative)


def _write_rescan(
    base: Path,
    root: Path,
    paths: Sequence[str],
    reason: Any,
    dry_run: bool,
    con: Any,
    approved_by: Any = "",
    approvals: Sequence[str] = (),
    host_executor: str = "none",
) -> dict:
    clean_paths = sorted({path for path in paths if _is_game_path(path)})
    payload = {
        "version": 1,
        "state": "staged" if dry_run else "requested",
        "status": "dry_run" if dry_run else "deferred_offline",
        "dry_run": bool(dry_run),
        "reason": _safe(reason, 160),
        "approved_by": _safe(approved_by, 120),
        "approvals": [_safe(item, 80) for item in approvals],
        "host_executor": _safe(host_executor, 40),
        "asset_paths": clean_paths,
        "path_count": len(clean_paths),
        "index_revision": _safe(_index_revision(con), 160),
    }
    meta = artifacts.write_artifact(base, _RESCAN_RELATIVE, payload)
    return {
        "artifact": _pointer(root, meta["artifact"]),
        "canonical_sha256": meta["canonical_sha256"],
        "payload": payload,
    }


def _bounded_results(results: Sequence[Any]) -> list[dict]:
    rows: list[dict] = []
    for item in results:
        row = dict(item) if isinstance(item, Mapping) else {}
        inner = row.get("result")
        status = row.get("result_status") or (
            inner.get("status") if isinstance(inner, Mapping) else ""
        )
        rows.append(
            {
                "id": _safe(row.get("id"), 80),
                "tool": _safe(row.get("tool"), 80),
                "accessMode": _safe(row.get("accessMode"), 32),
                "status": _safe(row.get("status"), 32),
                "dry_run": bool(row.get("dry_run", False)),
                "result_status": _safe(status, 40),
                "reason": _safe(row.get("reason"), 120),
            }
        )
    return rows


def _op_rows(ops: Any, limit: int) -> list[dict]:
    items = list(ops) if isinstance(ops, (list, tuple)) else []
    rows: list[dict] = []
    for op in items[:limit]:
        if not isinstance(op, Mapping):
            continue
        arguments = op.get("arguments") if isinstance(op.get("arguments"), Mapping) else {}
        rows.append(
            {
                "id": _safe(op.get("id"), 80),
                "tool": _safe(op.get("tool"), 80),
                "accessMode": _safe(op.get("accessMode"), 32),
                "inverse": _safe(op.get("inverse"), 80),
                "risk": _safe(op.get("risk"), 32),
                "arguments_sha256": gateway.arguments_digest(arguments),
            }
        )
    return rows


def _as_report(report: Any) -> dict:
    if isinstance(report, Mapping):
        return dict(report)
    if isinstance(report, str):
        return {"diff_preview": report}
    return {}


def _as_list(value: Any) -> list:
    if isinstance(value, Mapping):
        value = value.get("rows", value.get("items", []))
    return list(value) if isinstance(value, (list, tuple)) else []


def _table_rows(rows: list[dict], fields: Sequence[str]) -> list[list[Any]]:
    return [[row.get(field, "") for field in fields] for row in rows]


def _string_list(value: Any) -> list[str]:
    return [_safe(item, 80) for item in _as_list(value) if str(item).strip()]


def _parse_diff_rows(text: Any) -> list[dict]:
    parsed = toon.parse_toon(str(text or ""))
    rows = parsed.get("tables", {}).get("diff_preview", [])
    if rows:
        return [
            {field: _safe(row.get(field), 120) for field in _DIFF_FIELDS}
            for row in rows
        ]
    if str(text or "").strip():
        return [
            {
                "id": "",
                "tool": "",
                "accessMode": "",
                "risk": "",
                "arguments_sha256": "",
                "change": _safe(text, 160),
            }
        ]
    return []


def render_plan(report: Any) -> str:
    """Pure bounded TOON for a plan / apply report."""
    data = _as_report(report)
    limit = _bounded_limit(data.get("limit", 100))
    operations = _op_rows(data.get("operations"), limit)
    inverse_ops = _op_rows(data.get("inverse_ops"), limit)
    results = _bounded_results(_as_list(data.get("results"))[:limit])
    preview_rows = data.get("diff_preview_rows")
    preview = (
        [{field: _safe(row.get(field), 120) for field in _DIFF_FIELDS} for row in _as_list(preview_rows)[:limit]]
        if preview_rows is not None
        else _parse_diff_rows(data.get("diff_preview"))
    )
    total = len(_as_list(data.get("operations")))
    preview_text = str(data.get("diff_preview") or "")
    summary = {
        "plan_id": _safe(data.get("plan_id"), 80),
        "state": _safe(data.get("state"), 40),
        "status": _safe(data.get("status"), 40),
        "scope": _safe(data.get("scope"), 120),
        "index_revision": _safe(data.get("index_revision"), 120),
        "artifact": _safe(data.get("artifact"), 160),
        "canonical_sha256": _safe(data.get("canonical_sha256"), 80),
        "operation_count": total,
        "returned": len(operations),
        "total": total,
        "truncated": len(operations) < total,
        "diff_preview_sha256": artifacts.sha256_text(preview_text),
        "diff_preview_returned": len(preview),
        "dry_run": bool(data.get("dry_run", data.get("status") == "dry_run")),
        "residual_risks": "|".join(_string_list(data.get("residual_risks"))),
    }
    tables = [
        toon.Table("operations", _PLAN_FIELDS, _table_rows(operations, _PLAN_FIELDS)),
        toon.Table("inverse_ops", _PLAN_FIELDS, _table_rows(inverse_ops, _PLAN_FIELDS)),
        toon.Table("diff_preview", _DIFF_FIELDS, _table_rows(preview, _DIFF_FIELDS)),
    ]
    if results:
        tables.append(toon.Table("results", _RESULT_FIELDS, _table_rows(results, _RESULT_FIELDS)))
    return toon.render_toon(tables, summary)


def render_verify(report: Any) -> str:
    """Pure bounded TOON for a verification report."""
    data = _as_report(report)
    limit = _bounded_limit(data.get("limit", 100))
    raw_rows = _as_list(data.get("per_op_result"))[:limit]
    rows = [
        {
            "id": _safe(row.get("id"), 80),
            "tool": _safe(row.get("tool"), 80),
            "status": _safe(row.get("status"), 40),
            "index_state": _safe(row.get("index_state"), 40),
            "detail": _safe(row.get("detail"), 160),
        }
        for row in raw_rows
        if isinstance(row, Mapping)
    ]
    risks = _string_list(data.get("residual_risks"))
    total = len(_as_list(data.get("per_op_result")))
    summary = {
        "plan_id": _safe(data.get("plan_id"), 80),
        "state": _safe(data.get("state"), 40),
        "status": _safe(data.get("status"), 40),
        "artifact": _safe(data.get("artifact"), 160),
        "baseline_revision": _safe(data.get("baseline_revision"), 120),
        "current_revision": _safe(data.get("current_revision"), 120),
        "index_consistent": bool(data.get("index_consistent", False)),
        "revision_changed": bool(data.get("revision_changed", False)),
        "residual_risks": "|".join(risks),
        "returned": len(rows),
        "total": total,
        "truncated": len(rows) < total,
    }
    return toon.render_toon(
        [
            toon.Table("per_op_result", _VERIFY_FIELDS, _table_rows(rows, _VERIFY_FIELDS)),
            toon.Table("residual_risks", ("risk",), [[risk] for risk in risks]),
        ],
        summary,
    )


def render_rollback(report: Any) -> str:
    """Pure bounded TOON for a rollback report."""
    data = _as_report(report)
    limit = _bounded_limit(data.get("limit", 100))
    ops = _op_rows(data.get("reverted_ops"), limit)
    paths = [_safe(path, 160) for path in _as_list(data.get("rescan_paths"))[:limit]]
    total = len(_as_list(data.get("reverted_ops")))
    summary = {
        "plan_id": _safe(data.get("plan_id"), 80),
        "state": _safe(data.get("state"), 40),
        "status": _safe(data.get("status"), 40),
        "dry_run": bool(data.get("dry_run", True)),
        "approved_by": _safe(data.get("approved_by"), 80),
        "approvals": "|".join(_string_list(data.get("approvals"))),
        "artifact": _safe(data.get("artifact"), 160),
        "canonical_sha256": _safe(data.get("canonical_sha256"), 80),
        "rescan_artifact": _safe(data.get("rescan_artifact"), 160),
        "returned": len(ops),
        "total": total,
        "truncated": len(ops) < total,
        "residual_risks": "|".join(_string_list(data.get("residual_risks"))),
    }
    return toon.render_toon(
        [
            toon.Table("reverted_ops", _PLAN_FIELDS, _table_rows(ops, _PLAN_FIELDS)),
            toon.Table("rescan_paths", ("asset_path",), [[path] for path in paths]),
        ],
        summary,
    )


def render_rescan(report: Any) -> str:
    """Pure bounded TOON for a rescan request."""
    data = _as_report(report)
    limit = _bounded_limit(data.get("limit", 100))
    all_paths = _as_list(data.get("asset_paths"))
    paths = [_safe(path, 160) for path in all_paths[:limit]]
    summary = {
        "state": _safe(data.get("state"), 40),
        "status": _safe(data.get("status"), 40),
        "dry_run": bool(data.get("dry_run", True)),
        "reason": _safe(data.get("reason"), 160),
        "approved_by": _safe(data.get("approved_by"), 120),
        "approvals": "|".join(_string_list(data.get("approvals"))),
        "host_executor": _safe(data.get("host_executor"), 40),
        "artifact": _safe(data.get("artifact"), 160),
        "canonical_sha256": _safe(data.get("canonical_sha256"), 80),
        "index_revision": _safe(data.get("index_revision"), 120),
        "path_count": len(all_paths),
        "returned": len(paths),
        "total": len(all_paths),
        "truncated": len(paths) < len(all_paths),
    }
    return toon.render_toon(
        [toon.Table("asset_paths", ("asset_path",), [[path] for path in paths])],
        summary,
    )


def plan_edit(
    con: Any,
    intent: Any,
    candidates: Any = None,
    scope: str = "/Game",
    max_ops: int = 16,
    operations_yaml: str = "",
    *,
    project_root: Path | None = None,
    root: Path = Path(".ual"),
) -> str:
    """Create a deterministic dry-run plan plus its inverse operations."""
    base = _artifact_root(project_root, root)
    gw = gateway.load_gateway(project_root=project_root)
    intent_raw = str(intent or "")
    scope_text = _normalise_scope(scope)
    candidate_paths = _normalise_candidates(candidates, scope_text)
    cap = _bounded_limit(max_ops, 16)
    cap = min(cap, 64)
    revision_value = _index_revision(con)
    plan_id = artifacts.digest(
        {
            "intent": intent_raw,
            "candidates": sorted(candidate_paths),
            "index_revision": revision_value,
        }
    )
    if str(operations_yaml or "").strip():
        operations = _operations_from_yaml(operations_yaml, gw, cap, scope_text, con)
    else:
        operations = _operations_from_intent(
            intent_raw, candidate_paths, scope_text, cap, gw, con
        )
    inverse_ops: list[dict] = []
    for operation in operations:
        inverse_op = gw.inverse(operation)
        operation["inverse"] = inverse_op["tool"]
        ok, reason = gw.validate(operation)
        if not ok:
            raise ValueError(f"operation rejected: {reason}")
        inverse_ops.append(inverse_op)
    preview_rows = _diff_rows(operations)
    diff_preview = _diff_preview_text(preview_rows)
    relative = artifacts.safe_relative(f"plans/{plan_id}.yaml")
    payload = {
        "version": 1,
        "plan_id": plan_id,
        "state": "planned",
        "status": "planned",
        "intent": _safe(intent_raw, 240),
        "scope": scope_text,
        "candidates": sorted(candidate_paths),
        "baseline_revision": _safe(revision_value, 160),
        "index_revision": _safe(revision_value, 160),
        "operations": operations,
        "inverse_ops": inverse_ops,
        "diff_preview": diff_preview,
        "diff_preview_rows": preview_rows,
        "approved_by": "",
        "approvals": [],
        "results": [],
        "state_history": ["planned"],
        "residual_risks": list(_OFFLINE_RISKS),
    }
    meta = _write_plan(base, relative, payload)
    pointer = _pointer(root, meta["artifact"])
    _emit(
        "OnPlanned",
        {
            "plan_id": plan_id,
            "state": "planned",
            "operation_count": len(operations),
            "artifact": pointer,
        },
        project_root,
        root,
    )
    return render_plan(
        {
            "plan_id": plan_id,
            "state": "planned",
            "status": "planned",
            "scope": scope_text,
            "index_revision": _safe(revision_value, 160),
            "artifact": pointer,
            "canonical_sha256": meta["canonical_sha256"],
            "operations": operations,
            "inverse_ops": inverse_ops,
            "diff_preview": diff_preview,
            "diff_preview_rows": preview_rows,
            "results": [],
            "residual_risks": list(_OFFLINE_RISKS),
        }
    )


def apply_plan(
    con: Any,
    plan_id: Any,
    approved_by: str = "",
    dry_run: bool = True,
    approvals: Any = "",
    *,
    project_root: Path | None = None,
    root: Path = Path(".ual"),
    executor: Any = None,
) -> str:
    """Gate approvals, then delegate Write ops; offline hosts stay deferred."""
    base = _artifact_root(project_root, root)
    gw = gateway.load_gateway(project_root=project_root)
    plan, relative, pointer = _read_plan(base, plan_id, root)
    operations = _as_list(plan.get("operations"))
    all_ids = {str(op.get("id")) for op in operations if isinstance(op, Mapping)}
    required = [
        str(op.get("id"))
        for op in operations
        if isinstance(op, Mapping)
        and str(op.get("accessMode")) in ("Write", "Execute")
    ]
    supplied = [str(item) for item in _split_values(approvals) if str(item).strip()]
    approver = _safe(approved_by, 120)
    if not approver or any(op_id not in set(supplied) for op_id in required) or any(
        approval not in all_ids for approval in supplied
    ):
        raise ValueError(
            "apply_plan requires approved_by and explicit approval ids for every Write/Execute op"
        )
    plan["approved_by"] = approver
    plan["approvals"] = sorted(set(supplied))
    history = _as_list(plan.get("state_history"))
    if "approved" not in history:
        history.append("approved")
    plan["state_history"] = history
    results: list[dict] = []
    status = "dry_run"
    state = "approved"
    if not dry_run:
        for operation in operations:
            ok, reason = gw.validate(operation)
            if not ok:
                results.append(
                    {
                        "id": _safe(operation.get("id"), 80),
                        "tool": _safe(operation.get("tool"), 80),
                        "accessMode": _safe(operation.get("accessMode"), 32),
                        "status": "rejected",
                        "dry_run": False,
                        "result_status": "",
                        "reason": _safe(reason, 120),
                    }
                )
            else:
                results.append(gw.execute(operation, dry_run=False, executor=executor))
        executed = bool(results) and all(row["status"] == "executed" for row in results)
        state = "committed" if executed else "applied"
        status = "executed" if executed else "deferred_offline"
        if any(row["status"] in ("rejected", "failed") for row in results):
            status = "failed"
        if "applied" not in history:
            history.append("applied")
        if executed and "committed" not in history:
            history.append("committed")
        plan["state_history"] = history
        rescan = _write_rescan(
            base,
            root,
            _paths_from_ops(operations) or _as_list(plan.get("candidates")),
            "apply index consistency",
            False,
            con,
            approver,
            sorted(set(supplied)),
            "python_callable" if callable(executor) else "none",
        )
        plan["rescan_artifact"] = rescan["artifact"]
        _emit(
            "OnCommitted",
            {
                "plan_id": plan.get("plan_id"),
                "state": state,
                "artifact": pointer,
                "status": status,
            },
            project_root,
            root,
        )
        if executed:
            _emit(
                "OnPublished",
                {
                    "plan_id": plan.get("plan_id"),
                    "state": state,
                    "artifact": pointer,
                    "operation_count": len(results),
                },
                project_root,
                root,
            )
    plan["state"] = state
    plan["status"] = status
    plan["dry_run"] = bool(dry_run)
    plan["results"] = _bounded_results(results)
    plan["index_revision_after"] = _safe(_index_revision(con), 160)
    executed_any = any(row["status"] == "executed" for row in plan["results"])
    plan["residual_risks"] = (
        ["incremental_rescan_required"]
        if executed_any
        else list(_OFFLINE_RISKS)
    )
    meta = _write_plan(base, relative, plan)
    return render_plan(
        {
            "plan_id": plan.get("plan_id"),
            "state": state,
            "status": status,
            "scope": plan.get("scope"),
            "index_revision": plan.get("index_revision"),
            "artifact": pointer,
            "canonical_sha256": meta["canonical_sha256"],
            "operations": operations,
            "inverse_ops": _as_list(plan.get("inverse_ops")),
            "diff_preview": plan.get("diff_preview"),
            "diff_preview_rows": _as_list(plan.get("diff_preview_rows")),
            "results": plan["results"],
            "dry_run": bool(dry_run),
            "residual_risks": plan["residual_risks"],
        }
    )


def verify_edit(
    con: Any,
    plan_id: Any,
    *,
    project_root: Path | None = None,
    root: Path = Path(".ual"),
) -> str:
    """Compare the current index revision with the plan baseline; read-only."""
    base = _artifact_root(project_root, root)
    plan, _relative, pointer = _read_plan(base, plan_id, root)
    operations = _as_list(plan.get("operations"))
    results = _as_list(plan.get("results"))
    baseline = _safe(plan.get("baseline_revision"), 160)
    current = _safe(_index_revision(con), 160)
    revision_changed = current != baseline
    executed = any(
        isinstance(row, Mapping) and str(row.get("status")) == "executed"
        for row in results
    )
    index_consistent = revision_changed == executed
    result_by_id = {
        str(row.get("id")): row for row in results if isinstance(row, Mapping)
    }
    per_op_result = []
    for index, operation in enumerate(operations[:100]):
        if not isinstance(operation, Mapping):
            continue
        op_id = str(operation.get("id") or f"op-{index + 1:03d}")
        result = result_by_id.get(op_id, {})
        per_op_result.append(
            {
                "id": _safe(op_id, 80),
                "tool": _safe(operation.get("tool"), 80),
                "status": _safe(result.get("status") or "not_applied", 40),
                "index_state": (
                    "consistent" if index_consistent else ("stale" if executed else "offline")
                ),
                "detail": _safe(
                    "baseline revision matches current revision"
                    if index_consistent
                    else "incremental rescan required before verification",
                    160,
                ),
            }
        )
    residual = ["incremental_rescan_required"]
    if not executed:
        residual.insert(0, "offline_host_required")
    return render_verify(
        {
            "plan_id": plan.get("plan_id"),
            "state": plan.get("state"),
            "status": plan.get("status"),
            "artifact": pointer,
            "baseline_revision": baseline,
            "current_revision": current,
            "index_consistent": index_consistent,
            "revision_changed": revision_changed,
            "per_op_result": per_op_result,
            "residual_risks": residual,
        }
    )


def rollback_edit(
    con: Any,
    plan_id: Any,
    approved_by: str = "",
    dry_run: bool = True,
    approvals: Any = "",
    *,
    project_root: Path | None = None,
    root: Path = Path(".ual"),
    executor: Any = None,
) -> str:
    """Use the plan-time inverse operations; stage a rescan request."""
    base = _artifact_root(project_root, root)
    gw = gateway.load_gateway(project_root=project_root)
    plan, relative, pointer = _read_plan(base, plan_id, root)
    inverse_ops = _as_list(plan.get("inverse_ops"))
    for operation in inverse_ops:
        ok, reason = gw.validate(operation)
        if not ok:
            raise ValueError(f"inverse operation rejected: {reason}")
    all_ids = {str(op.get("id")) for op in inverse_ops if isinstance(op, Mapping)}
    required = [
        str(op.get("id"))
        for op in inverse_ops
        if isinstance(op, Mapping)
        and str(op.get("accessMode")) in ("Write", "Execute")
    ]
    supplied = [str(item) for item in _split_values(approvals) if str(item).strip()]
    approver = _safe(approved_by, 120)
    if not dry_run and (
        not approver
        or any(op_id not in set(supplied) for op_id in required)
        or any(approval not in all_ids for approval in supplied)
    ):
        raise ValueError(
            "rollback_edit requires approved_by and explicit approval ids for every Write/Execute inverse op"
        )
    results: list[dict] = []
    status = "dry_run"
    if not dry_run:
        for operation in inverse_ops:
            results.append(gw.execute(operation, dry_run=False, executor=executor))
        executed = bool(results) and all(row["status"] == "executed" for row in results)
        status = "executed" if executed else "deferred_offline"
        if any(row["status"] in ("rejected", "failed") for row in results):
            status = "failed"
        _emit(
            "OnCommitted",
            {
                "plan_id": plan.get("plan_id"),
                "state": "rolled_back",
                "artifact": pointer,
                "status": status,
            },
            project_root,
            root,
        )
    rescan_paths = (
        _paths_from_ops(inverse_ops)
        or _paths_from_ops(_as_list(plan.get("operations")))
        or [path for path in _as_list(plan.get("candidates")) if _is_game_path(path)]
    )
    rescan = _write_rescan(
        base,
        root,
        rescan_paths,
        "rollback index consistency",
        bool(dry_run),
        con,
        approver,
        sorted(set(supplied)),
        "python_callable" if callable(executor) else "none",
    )
    history = _as_list(plan.get("state_history"))
    if not dry_run and "approved" not in history:
        history.append("approved")
    if "rolled_back" not in history:
        history.append("rolled_back")
    plan["state_history"] = history
    plan["state"] = "rolled_back"
    plan["status"] = status
    plan["dry_run"] = bool(dry_run)
    plan["approved_by"] = approver
    plan["approvals"] = sorted(set(supplied))
    plan["results"] = _bounded_results(results)
    plan["rescan_artifact"] = rescan["artifact"]
    plan["residual_risks"] = list(_OFFLINE_RISKS)
    meta = _write_plan(base, relative, plan)
    return render_rollback(
        {
            "plan_id": plan.get("plan_id"),
            "state": "rolled_back",
            "status": status,
            "dry_run": bool(dry_run),
            "approved_by": plan["approved_by"],
            "approvals": plan["approvals"],
            "artifact": pointer,
            "canonical_sha256": meta["canonical_sha256"],
            "rescan_artifact": rescan["artifact"],
            "reverted_ops": inverse_ops,
            "rescan_paths": rescan["payload"]["asset_paths"],
            "residual_risks": plan["residual_risks"],
        }
    )


def request_rescan(
    con: Any,
    asset_paths: Any = "",
    reason: str = "index consistency",
    dry_run: bool = True,
    approved_by: str = "",
    approvals: Any = "",
    *,
    project_root: Path | None = None,
    root: Path = Path(".ual"),
    executor: Any = None,
) -> str:
    """Stage a deterministic incremental rescan request; no engine writes."""
    base = _artifact_root(project_root, root)
    paths = _split_values(asset_paths)
    for path in paths:
        if str(path).strip() and not _is_game_path(str(path).strip()):
            raise ValueError("asset_paths must be /Game asset paths")
    clean_paths = sorted({str(path).strip() for path in paths if _is_game_path(str(path).strip())})
    approver, approved_ids = ("", [])
    if not dry_run:
        approver, approved_ids = _require_host_approval(
            "RequestRescan", approved_by, approvals, executor
        )
        _invoke_host_executor(
            executor,
            "RequestRescan",
            approver,
            approved_ids,
            {"asset_paths": clean_paths, "reason": _safe(reason, 160)},
        )
    staged = _write_rescan(
        base,
        root,
        clean_paths,
        reason,
        bool(dry_run),
        con,
        approver,
        approved_ids,
        "python_callable" if callable(executor) else "none",
    )
    payload = staged["payload"]
    return render_rescan(
        {
            "state": payload["state"],
            "status": payload["status"],
            "dry_run": payload["dry_run"],
            "reason": payload["reason"],
            "approved_by": payload["approved_by"],
            "approvals": payload["approvals"],
            "host_executor": payload["host_executor"],
            "artifact": staged["artifact"],
            "canonical_sha256": staged["canonical_sha256"],
            "index_revision": payload["index_revision"],
            "asset_paths": payload["asset_paths"],
        }
    )
