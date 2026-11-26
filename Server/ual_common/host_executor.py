"""Offline, source-level host executor and host validation adapter contracts.

This module contains only pure Python protocols, value types and an injectable
registry.  Nothing here resolves code from configuration, opens a connection,
starts a process, loads an asset, writes a ``.uasset``, writes an index, or
invokes a real Unreal Engine host / official toolset / editor capability.

The default registry is fail-closed: no executor, no validation runner and an
empty allowlist.  A call is delegated to an injected adapter only when all of
the following hold:

* every operation has an explicit, matching per-operation approval plus a
  non-empty ``approved_by``;
* every bare tool name is in the injected allowlist and all arguments are
  bounded data scalars without volatile text;
* ``dry_run`` is False (dry-run never reaches any injected adapter);
* host-only capabilities (compile / PIE / rescan / native diff) have an
  injected validation runner -- and even then they are never delegated to the
  toolset executor, because the real host capability remains a specification.

All emitted text is bounded TOON from :mod:`ual_common.toon` or canonical YAML
from :mod:`ual_common.artifacts`.  Artifact pointers are repository-relative
and their identifiers are deterministic content digests: no clock values,
local absolute paths or random identifiers are produced.

Status: offline / source-level adapter contracts only.  The real UE host
executor, real writes, compile / PIE, rescan and UE native binary diff remain
specification / not landed.
"""
from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from . import artifacts, toon

CONTRACT_NAME = "host_executor_contract"
CONTRACT_VERSION = 1
CAPABILITIES: tuple[str, ...] = ("toolset", "compile", "PIE", "rescan", "native_diff")
HOST_ONLY_CAPABILITIES: frozenset[str] = frozenset(
    {"compile", "PIE", "rescan", "native_diff"}
)
RESIDUAL_RISKS: tuple[str, ...] = (
    "real_ue_host_executor_spec_not_landed",
    "real_uasset_write_spec_not_landed",
    "compile_pie_spec_not_landed",
    "asset_registry_rescan_spec_not_landed",
    "ue_native_binary_diff_spec_not_landed",
)
EXECUTION_FIELDS: tuple[str, ...] = (
    "id", "tool", "capability", "status", "dry_run",
    "arguments_sha256", "result_status", "reason",
)
MAX_OPERATIONS = 64
MAX_ROWS = 64
MAX_ARGUMENTS = 32
MAX_TEXT = 160

_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_TOOL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,63}$")
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_GAME_PATH_RE = re.compile(r"^/Game(?:/[A-Za-z0-9_.\-]+)*/?$")
_ASSET_PATH_KEY_RE = re.compile(
    r"(?i)(?:^|_)(?:asset_paths?|source_path|target_path|destination_path|"
    r"object_path|package_path|folder_path)(?:$|_)"
)
_FORBIDDEN_KEY_RE = re.compile(
    r"(?i)(?:^|_)(?:timestamp|updated_at|created_at|modified_at|generated_at|"
    r"datetime|date|time|uuid|guid|session_id|run_id|request_id|random_id|"
    r"nonce|local_path|absolute_path|host_path|file_path|impl|handler|module|"
    r"callable|eval|exec|script|code|blob|binary|pickle)(?:$|_)"
)
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
_SCALAR_TYPES = (str, int, float, bool, type(None))


@dataclass(frozen=True)
class HostOperation:
    """One validated, offline host operation (arguments are data scalars)."""

    id: str
    tool: str
    capability: str = "toolset"
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HostExecutionRequest:
    """Approval, allowlist and dry-run inputs for one bounded host request."""

    operations: Sequence[HostOperation] = ()
    approved_by: str = ""
    approvals: Sequence[str] = ()
    dry_run: bool = True
    limit: int = MAX_ROWS


@dataclass(frozen=True)
class HostValidationRequest:
    """Always a dry-run validation request; never a real compile / PIE call."""

    capability: str
    operation_ids: Sequence[str] = ()
    approved_by: str = ""
    dry_run: bool = True


@runtime_checkable
class HostToolsetExecutor(Protocol):
    """Injected host Toolset adapter; UAL never supplies a real one."""

    def execute(self, request: HostExecutionRequest) -> Mapping[str, Any]:
        ...


@runtime_checkable
class HostValidationRunner(Protocol):
    """Injected host validation adapter; UAL never supplies a real one."""

    def run(self, request: HostValidationRequest) -> Mapping[str, Any]:
        ...


class FakeOfflineHostToolsetExecutor:
    """Deterministic offline adapter for contract tests; it never calls a host."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def execute(self, request: HostExecutionRequest) -> dict[str, Any]:
        operations = sorted(request.operations, key=lambda item: str(item.id))
        self.calls.append(tuple(str(item.id) for item in operations))
        rows = [
            {
                "id": str(item.id),
                "tool": str(item.tool),
                "status": "dry_run",
                "arguments_sha256": arguments_digest(item.arguments),
            }
            for item in operations
        ]
        return {
            "status": "dry_run",
            "execution_kind": "fake_offline_adapter",
            "rows": rows,
        }


class FakeOfflineValidationRunner:
    """Deterministic offline validation adapter; it never starts a host job."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def run(self, request: HostValidationRequest) -> dict[str, Any]:
        self.calls.append((str(request.capability), tuple(str(i) for i in request.operation_ids)))
        return {
            "status": "dry_run",
            "capability": str(request.capability),
            "execution_kind": "fake_offline_adapter",
        }


def bounded_text(value: Any, limit: int = MAX_TEXT) -> str:
    """Deterministic, redacted scalar for TOON/YAML payloads."""
    if value is None:
        text = ""
    elif isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)
    text = artifacts.canonicalize_text(text)
    text = "".join(ch for ch in text if ord(ch) >= 0x20 or ch == "\t")
    text = text.replace("\r", " ").replace("\n", " ").strip()
    try:
        size = max(1, min(int(limit), 4096))
    except (TypeError, ValueError):
        size = MAX_TEXT
    return text[:size]


def has_forbidden_text(value: Any) -> bool:
    """True for volatile text that must never enter a persisted artifact."""
    if not isinstance(value, str):
        return False
    if any(
        pattern.search(value)
        for pattern in (
            _WINDOWS_ABS_RE, _POSIX_ABS_RE, _TIMESTAMP_RE, _RANDOM_ID_RE,
        )
    ):
        return True
    return "\\" in value or "://" in value or "ual_" in value or "!!python" in value.lower()


def _validate_scalar(key: str, value: Any) -> str:
    if _FORBIDDEN_KEY_RE.search(key):
        return "operation contains a forbidden key"
    if not isinstance(value, _SCALAR_TYPES) or isinstance(value, bytes):
        return "operation values must be data scalars"
    if isinstance(value, float) and not math.isfinite(value):
        return "operation contains a non-finite number"
    if isinstance(value, str):
        if has_forbidden_text(value):
            return "operation contains forbidden text"
        if _ASSET_PATH_KEY_RE.search(key) and not _GAME_PATH_RE.match(value):
            return "operation contains a non-/Game asset path"
    return "ok"


def _validate_operation(operation: Any) -> str:
    if not isinstance(operation, HostOperation):
        return "operation must be a HostOperation"
    if not _ID_RE.fullmatch(str(operation.id)):
        return "operation id is invalid"
    if not _TOOL_RE.fullmatch(str(operation.tool)):
        return "tool must be a bare tool name"
    if str(operation.capability) not in CAPABILITIES:
        return "capability is not declared"
    if not isinstance(operation.arguments, Mapping):
        return "arguments must be a mapping"
    if len(operation.arguments) > MAX_ARGUMENTS:
        return "operation has too many arguments"
    for key, value in operation.arguments.items():
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            return "operation argument key is invalid"
        reason = _validate_scalar(key, value)
        if reason != "ok":
            return reason
    return "ok"


def _approval_ids(value: Any) -> tuple[str, ...]:
    """Accept a sequence or a comma-separated scalar of explicit approval ids."""
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in re.split(r"[,\s;]+", value) if part.strip())
    if isinstance(value, Sequence):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _canonical_payload(value: Any) -> Any:
    """Sorted, bounded, scalar-only payload for the canonical emitter."""
    if isinstance(value, str):
        return bounded_text(value)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            key = bounded_text(raw_key, 80)
            if _FORBIDDEN_KEY_RE.search(key):
                continue
            result[key] = _canonical_payload(item)
        return result
    if isinstance(value, (list, tuple)):
        items = [_canonical_payload(item) for item in list(value)[:MAX_ROWS]]
        if items and all(isinstance(item, Mapping) for item in items):
            items.sort(key=artifacts.canonical_yaml)
        return items
    return bounded_text(value)


def canonical_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical, deterministic artifact body derived from an execution report."""
    cleaned = _canonical_payload(dict(report))
    return cleaned if isinstance(cleaned, dict) else {}


def arguments_digest(arguments: Any) -> str:
    """Stable content digest of validated arguments; never used for dedup."""
    try:
        source = dict(arguments) if isinstance(arguments, Mapping) else {}
        return artifacts.digest(_canonical_payload(source))
    except Exception:
        return ""


def artifact_metadata(report: Mapping[str, Any]) -> dict[str, Any]:
    """Bounded, deterministic metadata for a repository-relative artifact."""
    payload = canonical_payload(report)
    text = artifacts.canonical_yaml(payload)
    page = artifacts.page_lines(text, offset=0, limit=artifacts.MAX_PAGE_LINES)
    digest = artifacts.sha256_text(text)
    relative = artifacts.safe_relative(f"host/{digest}.yaml")
    return {
        "artifact": f".ual/{relative}",
        "canonical_sha256": digest,
        "contract_version": CONTRACT_VERSION,
        "execution_kind": "offline_source_level_adapter",
        "line_count": len(text.splitlines()),
        "next_cursor": str(page["next_cursor"]),
        "operation_count": int(payload.get("operation_count", 0)),
        "real_host_executor": "spec_not_landed",
        "status": bounded_text(payload.get("status"), 40),
    }


def render_artifact(report: Mapping[str, Any]) -> str:
    """Canonical YAML artifact metadata; no filesystem side effects."""
    return artifacts.canonical_yaml(artifact_metadata(report))


def render_page(report: Mapping[str, Any], *, offset: int = 0, limit: int = 80) -> str:
    """One bounded canonical YAML page of the artifact body."""
    text = artifacts.canonical_yaml(canonical_payload(report))
    page = artifacts.page_lines(text, offset=offset, limit=limit)
    meta = artifact_metadata(report)
    return artifacts.canonical_yaml({
        "artifact": meta["artifact"],
        "canonical_sha256": meta["canonical_sha256"],
        "contract_version": CONTRACT_VERSION,
        "offset": page["offset"],
        "limit": page["limit"],
        "returned": page["returned"],
        "total": page["total"],
        "truncated": page["truncated"],
        "next_cursor": page["next_cursor"],
        "lines": list(page["lines"]),
    })


def render_execution(report: Mapping[str, Any], *, limit: int = MAX_ROWS) -> str:
    """Pure bounded TOON view of one execution report."""
    rows = list(report.get("rows") or [])[: max(1, min(int(limit or MAX_ROWS), MAX_ROWS))]
    table_rows = [
        [bounded_text(row.get(field), MAX_TEXT) if isinstance(row, Mapping) else ""
         for field in EXECUTION_FIELDS]
        for row in rows
    ]
    summary = {
        "contract": CONTRACT_NAME,
        "contract_version": CONTRACT_VERSION,
        "status": bounded_text(report.get("status"), 40),
        "dry_run": bool(report.get("dry_run", True)),
        "operation_count": int(report.get("operation_count", 0)),
        "returned": len(table_rows),
        "total": int(report.get("operation_count", 0)),
        "truncated": bool(report.get("truncated", False)) or len(rows) < int(report.get("operation_count", 0)),
        "allowlist_sha256": bounded_text(report.get("allowlist_sha256"), 80),
    }
    return toon.render_toon(
        [toon.Table("host_execution", list(EXECUTION_FIELDS), table_rows)],
        summary=summary,
    )


class HostExecutorRegistry:
    """Fail-closed registry of injected adapters plus an immutable allowlist."""

    def __init__(self, *, allowlist: Sequence[str] = ()) -> None:
        cleaned: dict[str, None] = {}
        for item in allowlist:
            text = str(item).strip()
            if _TOOL_RE.fullmatch(text):
                cleaned.setdefault(text, None)
        self._allowlist: tuple[str, ...] = tuple(sorted(cleaned))
        self._executor: HostToolsetExecutor | None = None
        self._runner: HostValidationRunner | None = None
        self.allowlist_sha256 = artifacts.digest({
            "allowlist": list(self._allowlist),
            "contract_version": CONTRACT_VERSION,
        })

    @property
    def allowlist(self) -> tuple[str, ...]:
        return self._allowlist

    @property
    def fail_closed(self) -> bool:
        return self._executor is None and self._runner is None

    def register_executor(self, executor: HostToolsetExecutor | None) -> None:
        if executor is None:
            self._executor = None
            return
        if not isinstance(executor, HostToolsetExecutor):
            raise TypeError("host toolset executor does not implement the protocol")
        self._executor = executor

    def register_validation_runner(self, runner: HostValidationRunner | None) -> None:
        if runner is None:
            self._runner = None
            return
        if not isinstance(runner, HostValidationRunner):
            raise TypeError("host validation runner does not implement the protocol")
        self._runner = runner

    def clear(self) -> None:
        """Return to the fail-closed default; the allowlist is immutable."""
        self._executor = None
        self._runner = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "contract": CONTRACT_NAME,
            "contract_version": CONTRACT_VERSION,
            "allowlist": list(self._allowlist),
            "allowlist_sha256": self.allowlist_sha256,
            "executor_injected": self._executor is not None,
            "validation_runner_injected": self._runner is not None,
            "fail_closed": self.fail_closed,
            "execution_kind": "offline_source_level_adapter",
            "real_host_executor": "spec_not_landed",
            "residual_risks": list(RESIDUAL_RISKS),
        }

    def is_allowlisted(self, tool: str) -> bool:
        return str(tool) in self._allowlist

    def _validate_request(
        self, request: HostExecutionRequest, operations: tuple[HostOperation, ...]
    ) -> str:
        if not isinstance(request, HostExecutionRequest):
            return "request must be a HostExecutionRequest"
        if not operations or len(operations) > MAX_OPERATIONS:
            return "operation count is out of range"
        ids = [str(item.id) for item in operations]
        if len(set(ids)) != len(ids):
            return "duplicate operation ids"
        for operation in operations:
            reason = _validate_operation(operation)
            if reason != "ok":
                return reason
            if not self.is_allowlisted(str(operation.tool)):
                return "tool is not in the host allowlist"
        approver = str(request.approved_by or "").strip()
        if not approver or has_forbidden_text(approver):
            return "approved_by_required"
        approvals = set(_approval_ids(request.approvals))
        if approvals != set(ids):
            return "explicit_per_operation_approval_required"
        return "ok"

    @staticmethod
    def _row(
        operation: HostOperation,
        status: str,
        reason: str,
        *,
        dry_run: bool,
        result_status: str = "",
    ) -> dict[str, Any]:
        return {
            "id": bounded_text(operation.id, 80),
            "tool": bounded_text(operation.tool, 80),
            "capability": bounded_text(operation.capability, 40) if isinstance(operation, HostOperation) else "",
            "status": bounded_text(status, 40),
            "dry_run": bool(dry_run),
            "arguments_sha256": arguments_digest(
                operation.arguments if isinstance(operation, HostOperation) else {}
            ),
            "result_status": bounded_text(result_status, 40),
            "reason": bounded_text(reason, MAX_TEXT),
        }

    def _report(
        self,
        request: HostExecutionRequest,
        operations: tuple[HostOperation, ...],
        rows: Sequence[Mapping[str, Any]],
        status: str,
        reason: str,
    ) -> dict[str, Any]:
        bounded_rows = [dict(row) for row in list(rows)[:MAX_ROWS]]
        return {
            "contract": CONTRACT_NAME,
            "contract_version": CONTRACT_VERSION,
            "execution_kind": "offline_source_level_adapter",
            "status": bounded_text(status, 40),
            "reason": bounded_text(reason, MAX_TEXT),
            "dry_run": bool(getattr(request, "dry_run", True)),
            "approved_by": bounded_text(getattr(request, "approved_by", ""), 80),
            "approval_count": len(_approval_ids(getattr(request, "approvals", ()))),
            "allowlist_sha256": self.allowlist_sha256,
            "operation_count": len(operations),
            "returned": len(bounded_rows),
            "truncated": len(rows) > len(bounded_rows),
            "rows": bounded_rows,
            "residual_risks": list(RESIDUAL_RISKS),
        }

    def run_validation(self, request: HostValidationRequest) -> dict[str, Any]:
        """Validate a host-only capability without ever starting a host job."""
        capability = str(getattr(request, "capability", ""))
        base: dict[str, Any] = {
            "capability": bounded_text(capability, 40),
            "contract": CONTRACT_NAME,
            "contract_version": CONTRACT_VERSION,
            "dry_run": bool(getattr(request, "dry_run", True)),
            "execution_kind": "offline_source_level_adapter",
            "host_only": capability in HOST_ONLY_CAPABILITIES,
            "real_host_capability": "spec_not_landed",
        }
        if capability not in HOST_ONLY_CAPABILITIES:
            base.update(status="rejected", reason="capability_is_not_host_validation")
            return base
        if not bool(getattr(request, "dry_run", True)):
            base.update(status="rejected", reason="host_validation_requires_dry_run")
            return base
        if not str(getattr(request, "approved_by", "") or "").strip():
            base.update(status="rejected", reason="approved_by_required")
            return base
        if self._runner is None:
            base.update(status="rejected", reason="injected_validation_runner_required")
            return base
        try:
            raw = self._runner.run(request)
        except Exception as exc:  # adapter failures are data, not crashes
            base.update(status="failed", reason=bounded_text(type(exc).__name__, 80))
            return base
        raw_status = bounded_text(raw.get("status"), 40) if isinstance(raw, Mapping) else ""
        if raw_status != "dry_run":
            base.update(status="rejected", reason="validation_runner_must_stay_offline_dry_run")
            return base
        base.update(status="dry_run", reason="offline_validation_adapter", runner_status=raw_status)
        return base

    def execute(self, request: HostExecutionRequest) -> dict[str, Any]:
        """Gate approval / allowlist / dry-run, then delegate only to injected adapters."""
        operations = tuple(request.operations) if isinstance(request, HostExecutionRequest) else ()
        reason = self._validate_request(request, operations)
        dry_run = bool(getattr(request, "dry_run", True))
        if reason != "ok":
            return self._report(
                request, operations,
                [self._row(op, "rejected", reason, dry_run=dry_run) for op in operations],
                "rejected", reason,
            )
        if dry_run:
            return self._report(
                request, operations,
                [self._row(op, "dry_run", "dry_run_no_host_call", dry_run=True) for op in operations],
                "dry_run", "dry_run_no_host_call",
            )

        host_only = [op for op in operations if str(op.capability) in HOST_ONLY_CAPABILITIES]
        if host_only:
            validation = self.run_validation(HostValidationRequest(
                capability=str(host_only[0].capability),
                operation_ids=tuple(str(op.id) for op in host_only),
                approved_by=str(request.approved_by),
                dry_run=True,
            ))
            if validation["status"] != "dry_run":
                gate_reason = str(validation["reason"])
                return self._report(
                    request, operations,
                    [self._row(op, "rejected", gate_reason, dry_run=False) for op in operations],
                    "rejected", gate_reason,
                )
            # Even a validated host-only capability is not delegated: the real
            # host capability remains a specification, never a fake execution.
            return self._report(
                request, operations,
                [
                    self._row(op, "deferred_offline", "real_host_capability_spec_not_landed", dry_run=False)
                    for op in operations
                ],
                "deferred_offline", "real_host_capability_spec_not_landed",
            )

        if self._executor is None:
            return self._report(
                request, operations,
                [
                    self._row(op, "deferred_offline", "host_toolset_executor_not_injected", dry_run=False)
                    for op in operations
                ],
                "deferred_offline", "host_toolset_executor_not_injected",
            )
        try:
            raw = self._executor.execute(request)
        except Exception as exc:  # adapter failures are data, not crashes
            return self._report(
                request, operations,
                [self._row(op, "failed", type(exc).__name__, dry_run=False) for op in operations],
                "failed", bounded_text(type(exc).__name__, 80),
            )
        raw_rows = raw.get("rows") if isinstance(raw, Mapping) else None
        raw_rows = list(raw_rows) if isinstance(raw_rows, (list, tuple)) else []
        by_id = {
            str(item.get("id", "")): item
            for item in raw_rows
            if isinstance(item, Mapping)
        }
        raw_status = bounded_text(raw.get("status"), 40) if isinstance(raw, Mapping) else ""
        rows = [
            self._row(
                op,
                "offline_adapter",
                "offline_adapter_result",
                dry_run=False,
                result_status=str(
                    by_id.get(str(op.id), {}).get("status", raw_status)
                    if isinstance(by_id.get(str(op.id), {}), Mapping) else raw_status
                ),
            )
            for op in operations
        ]
        return self._report(request, operations, rows, "offline_adapter", "offline_adapter_result")


def default_registry() -> HostExecutorRegistry:
    """Fresh, fail-closed registry with no adapters and an empty allowlist."""
    return HostExecutorRegistry()


def render_snapshot(registry: HostExecutorRegistry, *, limit: int = MAX_ROWS) -> str:
    """Bounded TOON view of the immutable allowlist and injection state."""
    snapshot = registry.snapshot()
    tools = list(snapshot["allowlist"])[: max(1, min(int(limit or MAX_ROWS), MAX_ROWS))]
    return toon.render_toon(
        [toon.Table("host_allowlist", ["tool"], [[bounded_text(tool, 80)] for tool in tools])],
        summary={
            "contract": CONTRACT_NAME,
            "contract_version": CONTRACT_VERSION,
            "allowlist_sha256": snapshot["allowlist_sha256"],
            "fail_closed": snapshot["fail_closed"],
            "executor_injected": snapshot["executor_injected"],
            "validation_runner_injected": snapshot["validation_runner_injected"],
            "returned": len(tools),
            "total": len(snapshot["allowlist"]),
            "truncated": len(tools) < len(snapshot["allowlist"]),
        },
    )
