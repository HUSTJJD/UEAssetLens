"""Deterministic, bounded ten-step FPS 3C workflow orchestration.

The workflow only composes read-only retrieval, declarative policy, dry-run
transactions, offline validation and canonical artifacts. Complete payloads stay
on disk; model-facing output is a ten-row TOON table. Real UE writes, engine
compile and PIE are never performed here. Concurrent modules are imported lazily
and never through ual_mcp.tools, so no import cycle is possible.
"""
from __future__ import annotations

import importlib
import inspect
import re
from pathlib import Path
from typing import Any, Callable, Sequence

from . import artifacts, db, gamespec, policy_gate
from .toon import Table, parse_toon, render_toon

STEP_FIELDS: tuple[str, ...] = ("step", "name", "status", "result", "artifact")
STEP_NAMES: tuple[str, ...] = (
    "intent_to_gamespec",
    "template_expansion",
    "retrieve_assets",
    "policy_gate",
    "plan_edit",
    "approval_dry_run",
    "apply_offline",
    "diff_assets",
    "verify_and_validate",
    "rollback_fallback",
)
_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
_SUMMARY_KEYS: tuple[str, ...] = (
    "status", "asset_count", "operation_count", "relation_count",
    "assertion_count", "assertions_passed", "assertions_total",
    "blocking", "returned", "applied_ops", "inverse_ops",
)
_ALLOWED_RESULT_STATUS = {
    "expanded", "error", "unsupported_intent", "integration_pending",
    "template_invalid", "artifact_write_failed", "invalid_game_spec_id",
    "gamespec_artifact_missing", "gamespec_artifact_invalid",
    "gateway_whitelist_invalid", "python_host_runner_required",
    "reported", "pass", "fail", "failed", "deferred_offline", "skipped",
    "host_reported", "host_reported_fail", "completed",
}
_WORKFLOW_HOST_IDS = frozenset({
    "RunFps3CWorkflow", "RunValidationLoop", "RequestRescan",
    "ApplyPlan", "RollbackEdit",
})


def _split_approvals(value: Any) -> set[str]:
    if isinstance(value, str):
        return {part.strip() for part in re.split(r"[,\s;]+", value) if part.strip()}
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()


def _workflow_approval_ids(operations: Sequence[Any]) -> set[str]:
    """Explicit ids for the workflow and every nested Write/Execute operation."""
    ids = set(_WORKFLOW_HOST_IDS)
    for index, item in enumerate(operations, 1):
        op_id = str(item.get("id") or f"op-{index:03d}") if isinstance(item, dict) else f"op-{index:03d}"
        ids.add(op_id)
        ids.add(f"{op_id}.inverse")
    return ids


def _require_workflow_gate(
    *,
    dry_run: bool,
    approved_by: str,
    approvals: Any,
    executor: Any,
    operations: Sequence[Any],
) -> None:
    if bool(dry_run):
        return
    required = _workflow_approval_ids(operations)
    supplied = _split_approvals(approvals)
    if not str(approved_by or "").strip() or not required.issubset(supplied) or not callable(executor):
        raise ValueError(
            "run_fps_3c requires approved_by, explicit per-op approvals and an injected host executor"
        )


def _module(name: str) -> Any | None:
    """Import one fixed concurrent module without failing at module import time."""
    try:
        return importlib.import_module(f".{name}", package=__package__)
    except ModuleNotFoundError as exc:
        if exc.name == f"ual_common.{name}":
            return None
        raise


def _filtered_kwargs(
    fn: Callable[..., Any], kwargs: dict[str, Any]
) -> dict[str, Any]:
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return {}
    params = signature.parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return dict(kwargs)
    return {key: value for key, value in kwargs.items() if key in params}


def _call_flexible(
    fn: Callable[..., Any], con: Any, *args: Any, **kwargs: Any
) -> Any:
    """Call a function whose first argument may or may not be a DB connection."""
    wants_con = False
    try:
        positional = [
            p for p in inspect.signature(fn).parameters.values()
            if p.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        first = positional[0].name if positional else ""
        wants_con = first in {"con", "connection", "conn", "db"}
    except (TypeError, ValueError):
        pass
    filtered = _filtered_kwargs(fn, kwargs)
    if wants_con:
        try:
            return fn(con, *args, **filtered)
        except TypeError:
            return fn(*args, **filtered)
    try:
        return fn(*args, **filtered)
    except TypeError:
        return fn(con, *args, **filtered)


def _safe_payload(value: Any, depth: int = 0) -> Any:
    """Reduce arbitrary Python results to a canonical, bounded digest payload."""
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


def _summary(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        try:
            parsed = parse_toon(value)
            summary = dict(parsed.get("summary") or {})
            errors = (parsed.get("tables") or {}).get("errors") or []
            if errors:
                summary.setdefault("error_code", str(errors[0].get("code", "")))
            return summary
        except Exception:
            return {}
    if isinstance(value, dict):
        return {
            str(key): str(item)
            for key, item in value.items()
            if isinstance(item, (str, int, float, bool))
        }
    if isinstance(value, tuple) and value and isinstance(value[0], str):
        return _summary(value[0])
    return {}


def _clean_pointer(value: Any) -> str:
    text = str(value or "")
    if not text.startswith(".ual/"):
        return ""
    try:
        relative = artifacts.safe_relative(text[len(".ual/"):])
    except ValueError:
        return ""
    return f".ual/{relative}"


def _bounded_result(value: Any) -> dict[str, Any]:
    summary = _summary(value)
    status = str(summary.get("error_code") or summary.get("status", "")).casefold()
    if status not in _ALLOWED_RESULT_STATUS:
        status = "completed"
    digest = artifacts.digest({"result": _safe_payload(value)})[:12]
    detail_keys = [key for key in _SUMMARY_KEYS if key in summary][:5]
    detail = "|".join(f"{key}={summary[key]}" for key in detail_keys)
    return {
        "summary": summary,
        "status": status,
        "artifact": _clean_pointer(summary.get("artifact")),
        "digest": digest,
        "result": "|".join(part for part in (detail, f"sha256={digest}") if part),
    }


def _identifier(bound: dict[str, Any], keys: tuple[str, ...], fallback: str) -> str:
    for key in keys:
        candidate = str(bound["summary"].get(key, ""))
        if _ID_RE.fullmatch(candidate):
            return candidate
    return fallback


def _pointer(relative: str) -> str:
    return f".ual/{artifacts.safe_relative(relative)}"


def _step(
    number: int, status: str, result: str, artifact: str = ""
) -> dict[str, Any]:
    return {
        "step": number,
        "name": STEP_NAMES[number - 1],
        "status": status,
        "result": result[:160],
        "artifact": _clean_pointer(artifact),
    }


def _skipped_steps(rows: list[dict[str, Any]], reason: str) -> None:
    for number in range(len(rows) + 1, 11):
        rows.append(_step(number, "skipped", reason))


def _pending_step(module_name: str) -> tuple[str, str]:
    return "integration_pending", f"module_pending:{module_name}"


def _emit_lifecycle(
    con: Any,
    rows: list[dict[str, Any]],
    *,
    root: Path,
    project_root: Path | None = None,
) -> int:
    """Record bounded lifecycle observations when the concurrent module exists."""
    module = _module("lifecycle")
    if module is None:
        return 0
    emit = getattr(module, "emit", None)
    emitted = 0
    if callable(emit):
        for row in rows:
            payload = {
                "step": row["step"],
                "name": row["name"],
                "status": row["status"],
            }
            try:
                _call_flexible(
                    emit,
                    con,
                    event="workflow_step",
                    payload=payload,
                    project_root=project_root,
                    root=root,
                )
                emitted += 1
            except Exception:
                continue
    events = getattr(module, "events", None)
    if callable(events):
        try:
            observed = _call_flexible(events, con, root=root)
            if isinstance(observed, (list, tuple, dict)):
                return len(observed)
        except Exception:
            pass
    return emitted


def run_fps_3c(
    con: Any,
    intent: str,
    *,
    project_root: Path | None = None,
    root: Path = Path(".ual"),
    dry_run: bool = True,
    approved_by: str = "",
    approvals: Any = "",
    executor: Any = None,
) -> str:
    """Run the deterministic FPS 3C chain without engine writes or inference."""
    rows: list[dict[str, Any]] = []
    template: dict[str, Any] | None = None
    operations: list[dict[str, Any]] = []
    try:
        template = gamespec.load_template("fps_3c", project_root=project_root)
        operations = list(template["operations"])
    except ValueError:
        operations = []
    _require_workflow_gate(
        dry_run=bool(dry_run),
        approved_by=approved_by,
        approvals=approvals,
        executor=executor,
        operations=operations,
    )
    operation_ids = [
        str(item.get("id") or f"op-{index:03d}")
        for index, item in enumerate(operations, 1)
    ]
    inverse_ids = [f"{op_id}.inverse" for op_id in operation_ids]
    expansion = gamespec.expand_game_spec(
        con, intent, project_root=project_root, root=root
    )
    expansion_bound = _bounded_result(expansion)
    game_spec_id = _identifier(
        expansion_bound,
        ("game_spec_id",),
        artifacts.digest({"intent": str(intent), "fallback": "gamespec"}),
    )
    gamespec_artifact = expansion_bound["artifact"] or _pointer(
        f"gamespecs/{game_spec_id}.yaml"
    )
    rows.append(_step(
        1,
        expansion_bound["status"],
        expansion_bound["result"],
        gamespec_artifact,
    ))
    if expansion_bound["status"] != "expanded":
        _skipped_steps(rows, "gamespec_unavailable")
    else:
        try:
            if template is None:
                raise ValueError("template_invalid")
            rows.append(_step(
                2,
                "completed",
                (
                    f"assets={len(template['assets'])}|"
                    f"relations={len(template['assembly'])}|"
                    f"operations={len(operations)}|"
                    f"sha256={artifacts.digest({'operations': operations})[:12]}"
                ),
                gamespec_artifact,
            ))
        except ValueError:
            rows.append(_step(2, "error", "template_invalid"))

        try:
            candidates = db.search_assets(
                con, query="fps 3c shooter", domain_scope="/Game", limit=8
            )
            candidate_paths = sorted({
                str(row.get("asset_path", ""))
                for row in candidates
                if isinstance(row, dict) and row.get("asset_path")
            })
            candidate_digest = artifacts.digest(
                {"candidate_paths": candidate_paths}
            )[:12]
            rows.append(_step(
                3,
                "completed",
                f"candidates={len(candidate_paths)}|sha256={candidate_digest}",
            ))
        except Exception as exc:
            candidate_paths = []
            rows.append(_step(3, "error", f"retrieval_error:{type(exc).__name__}"))

        try:
            policy_result = policy_gate.run_policy_gate(
                con, project_root=project_root, limit=16
            )
            policy_bound = _bounded_result(policy_result)
            rows.append(_step(
                4,
                "reported" if policy_bound["status"] != "fail" else "fail",
                policy_bound["result"],
                policy_bound["artifact"],
            ))
        except Exception as exc:
            rows.append(_step(4, "error", f"policy_error:{type(exc).__name__}"))

        transaction = _module("transaction")
        plan_id = artifacts.digest({
            "intent": str(intent),
            "candidate_paths": candidate_paths,
            "game_spec_id": game_spec_id,
        })
        plan_artifact = ""
        if transaction is None:
            status, result = _pending_step("transaction")
            rows.append(_step(5, status, result))
        else:
            try:
                plan = _call_flexible(
                    transaction.plan_edit,
                    con,
                    intent="fps_3c workflow",
                    candidates=",".join(candidate_paths),
                    scope="/Game",
                    max_ops=min(max(len(operations), 1), 16),
                    operations_yaml=artifacts.canonical_yaml(operations),
                    project_root=project_root,
                    root=root,
                )
                bound = _bounded_result(plan)
                plan_id = _identifier(bound, ("plan_id",), plan_id)
                plan_artifact = bound["artifact"]
                rows.append(_step(
                    5, bound["status"], bound["result"], plan_artifact
                ))
            except Exception as exc:
                rows.append(_step(5, "error", f"plan_error:{type(exc).__name__}"))

        if transaction is None:
            status, result = _pending_step("transaction")
            rows.append(_step(6, status, result))
        else:
            try:
                approval = _call_flexible(
                    transaction.apply_plan,
                    con,
                    plan_id,
                    approved_by=approved_by or "offline_workflow",
                    dry_run=bool(dry_run),
                    approvals=",".join(operation_ids),
                    executor=executor,
                    project_root=project_root,
                    root=root,
                )
                bound = _bounded_result(approval)
                rows.append(_step(
                    6,
                    "completed",
                    f"dry_run={str(bool(dry_run)).lower()}|{bound['result']}",
                    bound["artifact"] or plan_artifact,
                ))
            except Exception as exc:
                rows.append(_step(6, "error", f"approval_error:{type(exc).__name__}"))

        if dry_run:
            rows.append(_step(
                7, "deferred_offline", "engine_writes_not_executed"
            ))
        else:
            rows.append(_step(
                7, "host_delegated", "engine_writes_deferred_to_host"
            ))

        diff_module = _module("asset_diff")
        diff_id = ""
        diff_artifact = ""
        if diff_module is None:
            status, result = _pending_step("asset_diff")
            rows.append(_step(8, status, result))
        else:
            lhs = candidate_paths[0] if candidate_paths else "/Game/FPS3C"
            rhs = (
                candidate_paths[1]
                if len(candidate_paths) > 1
                else lhs
            )
            try:
                diff = _call_flexible(
                    diff_module.diff_assets,
                    con,
                    lhs,
                    rhs,
                    layers="L0,L1,L2,L3,L4",
                    project_root=project_root,
                    root=root,
                )
                bound = _bounded_result(diff)
                diff_id = _identifier(bound, ("diff_id",), diff_id)
                diff_artifact = bound["artifact"]
                deterministic = ""
                verify = getattr(diff_module, "verify_diff_determinism", None)
                if callable(verify) and diff_id:
                    repeated = _call_flexible(
                        verify, con, diff_id, project_root=project_root, root=root
                    )
                    deterministic = f"|repeat={_bounded_result(repeated)['digest']}"
                rows.append(_step(
                    8,
                    bound["status"],
                    bound["result"] + deterministic,
                    diff_artifact,
                ))
            except Exception as exc:
                rows.append(_step(8, "error", f"diff_error:{type(exc).__name__}"))

        validation = gamespec.run_validation_loop(
            con,
            game_spec_id,
            runner="offline" if dry_run else "host",
            dry_run=bool(dry_run),
            host_runner=executor if not dry_run else None,
            project_root=project_root,
            root=root,
        )
        validation_bound = _bounded_result(validation)
        validation_artifact = validation_bound["artifact"] or _pointer(
            f"validation/{game_spec_id}.yaml"
        )
        verify_bound: dict[str, Any] | None = None
        if transaction is None:
            status, result = _pending_step("transaction")
            verify_result = result
        else:
            try:
                verified = _call_flexible(
                    transaction.verify_edit,
                    con,
                    plan_id,
                    project_root=project_root,
                    root=root,
                )
                verify_bound = _bounded_result(verified)
                verify_result = verify_bound["result"]
                status = "completed"
            except Exception as exc:
                verify_result = f"verify_error:{type(exc).__name__}"
                status = "error"
        static_failed = validation_bound["summary"].get("status") == (
            "static_assertions_fail"
        )
        verify_failed = (
            status in {"fail", "error"}
            or (verify_bound or {}).get("status") in {
                "fail", "failed", "host_reported_fail"
            }
        )
        rows.append(_step(
            9,
            "fail" if static_failed or verify_failed else "completed",
            f"offline_validation={validation_bound['digest']}|{verify_result}",
            validation_artifact,
        ))
        rollback_needed = static_failed or verify_failed
        if transaction is None:
            status, result = _pending_step("transaction")
            rows.append(_step(10, status, result))
        elif not rollback_needed:
            rows.append(_step(10, "skipped", "rollback_not_required"))
        else:
            try:
                rollback = _call_flexible(
                    transaction.rollback_edit,
                    con,
                    plan_id,
                    approved_by=approved_by or "offline_workflow",
                    dry_run=bool(dry_run),
                    approvals=",".join(inverse_ids),
                    executor=executor,
                    project_root=project_root,
                    root=root,
                )
                bound = _bounded_result(rollback)
                rescan = getattr(transaction, "request_rescan", None)
                if callable(rescan):
                    _call_flexible(
                        rescan,
                        con,
                        asset_paths=",".join(candidate_paths),
                        reason="rollback_fallback",
                        dry_run=bool(dry_run),
                        approved_by=approved_by or "offline_workflow",
                        approvals="RequestRescan",
                        executor=executor,
                        project_root=project_root,
                        root=root,
                    )
                rows.append(_step(
                    10,
                    "completed",
                    f"dry_run={str(bool(dry_run)).lower()}|{bound['result']}",
                    bound["artifact"],
                ))
            except Exception as exc:
                rows.append(_step(10, "error", f"rollback_error:{type(exc).__name__}"))

    workflow_id = artifacts.digest({
        "intent": str(intent),
        "dry_run": bool(dry_run),
        "game_spec_id": game_spec_id,
        "steps": [[str(row[key]) for key in STEP_FIELDS] for row in rows],
    })
    relative = artifacts.safe_relative(f"workflow/{workflow_id}.yaml")
    workflow_pointer = _pointer(relative)
    lifecycle_event_count = _emit_lifecycle(
        con, rows, root=root, project_root=project_root
    )
    payload = {
        "workflow_id": workflow_id,
        "game_spec_id": game_spec_id,
        "dry_run": bool(dry_run),
        "applied": False,
        "engine_validation": "deferred_offline",
        "artifact": workflow_pointer,
        "lifecycle_event_count": lifecycle_event_count,
        "steps": rows,
    }
    try:
        meta = artifacts.write_artifact(root, relative, payload)
        workflow_artifact = workflow_pointer
        canonical_sha256 = meta["canonical_sha256"]
    except OSError:
        workflow_artifact = ""
        canonical_sha256 = ""
    table_rows = [
        [row["step"], row["name"], row["status"], row["result"], row["artifact"]]
        for row in rows[:10]
    ]
    return render_toon(
        [Table("steps", STEP_FIELDS, table_rows)],
        summary={
            "status": "completed" if len(rows) == 10 else "error",
            "workflow_id": workflow_id,
            "game_spec_id": game_spec_id,
            "dry_run": bool(dry_run),
            "applied": False,
            "engine_validation": "deferred_offline",
            "artifact": workflow_artifact,
            "canonical_sha256": canonical_sha256,
            "lifecycle_event_count": lifecycle_event_count,
        },
    )
