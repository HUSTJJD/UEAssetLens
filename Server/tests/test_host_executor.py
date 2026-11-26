"""Offline contract tests for the source-level host executor adapter.

These checks are pure Python: no UE SDK, no official toolset, no compile / PIE,
no index writes and no external processes. They prove the fail-closed default,
explicit per-operation approval, allowlist and dry-run gates, deterministic
fake adapter output, host-only validation gating and canonical emitters.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "Server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from ual_common import artifacts, host_executor as host, toon  # noqa: E402

ALLOWLIST = ("SetScalarParameter", "CompileHostOp", "PIEHostOp")
_FORBIDDEN_OUTPUT_TOKENS = (
    "vlm", "gpu", "network", "socket", "http", "cloud", "duckdb", "import",
)
_SOURCE_FORBIDDEN = (
    "import socket", "urllib", "requests.", "http.client", "duckdb",
    "importlib", "__import__", "eval(", "exec(", "GetAsset(", "FHttpModule",
    "UEditorAssetLibrary", "StartPIE", "CompileBlueprint", "TODO" + "(human)",
)


def _op(
    op_id: str = "op-1",
    tool: str = "SetScalarParameter",
    capability: str = "toolset",
    arguments: dict | None = None,
) -> host.HostOperation:
    return host.HostOperation(
        id=op_id,
        tool=tool,
        capability=capability,
        arguments=arguments if arguments is not None else {"asset_path": "/Game/Demo/MI_Test"},
    )


def _request(
    operations: tuple[host.HostOperation, ...],
    *,
    approved_by: str = "reviewer",
    approvals: tuple[str, ...] | None = None,
    dry_run: bool = False,
) -> host.HostExecutionRequest:
    return host.HostExecutionRequest(
        operations=operations,
        approved_by=approved_by,
        approvals=tuple(approvals) if approvals is not None else tuple(item.id for item in operations),
        dry_run=dry_run,
    )


def _registry(
    *,
    executor: host.FakeOfflineHostToolsetExecutor | None = None,
    runner: host.FakeOfflineValidationRunner | None = None,
    allowlist: tuple[str, ...] = ALLOWLIST,
) -> host.HostExecutorRegistry:
    registry = host.HostExecutorRegistry(allowlist=allowlist)
    if executor is not None:
        registry.register_executor(executor)
    if runner is not None:
        registry.register_validation_runner(runner)
    return registry


def _assert_clean_output(text: str) -> None:
    assert not re.search(r"[A-Za-z]:[\\/]", text), text[:160]
    assert not re.search(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*", text), text[:160]
    assert not re.search(r"\b[0-9a-fA-F]{32}\b", text), text[:160]
    body = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    lowered = body.lower()
    for token in _FORBIDDEN_OUTPUT_TOKENS:
        assert token not in lowered, (token, text[:160])


def test_fail_closed_registry_never_calls_executor_without_approver_or_per_op_approval() -> None:
    fake = host.FakeOfflineHostToolsetExecutor()
    registry = _registry(executor=fake)
    operations = (_op(),)
    assert host.default_registry().fail_closed is True
    assert host.default_registry().allowlist == ()
    snapshot = registry.snapshot()
    assert snapshot["executor_injected"] is True
    assert snapshot["validation_runner_injected"] is False
    assert snapshot["real_host_executor"] == "spec_not_landed"

    report = registry.execute(_request(operations, approved_by=""))
    assert report["status"] == "rejected"
    assert "approved_by" in report["reason"]
    assert fake.calls == []

    report = registry.execute(_request(operations, approvals=()))
    assert report["status"] == "rejected"
    assert "per_operation" in report["reason"]
    assert fake.calls == []

    report = registry.execute(_request(operations, approvals=("op-1", "op-2")))
    assert report["status"] == "rejected"
    assert fake.calls == []

    report = registry.execute(_request(operations, dry_run=True))
    assert report["status"] == "dry_run"
    assert report["rows"][0]["reason"] == "dry_run_no_host_call"
    assert fake.calls == []

    empty = _registry()
    report = empty.execute(_request(operations))
    assert report["status"] == "deferred_offline"
    assert report["reason"] == "host_toolset_executor_not_injected"

    registry.clear()
    assert registry.fail_closed is True
    assert registry.execute(_request(operations))["status"] == "deferred_offline"
    assert fake.calls == []


def test_fake_toolset_adapter_is_deterministic_dry_run_and_allowlist_gated() -> None:
    fake = host.FakeOfflineHostToolsetExecutor()
    registry = _registry(executor=fake)
    operations = (_op("op-1"), _op("op-2"))
    request = _request(operations)
    first = registry.execute(request)
    second = registry.execute(request)

    assert first["status"] == "offline_adapter"
    assert [row["result_status"] for row in first["rows"]] == ["dry_run", "dry_run"]
    assert len(fake.calls) == 2
    assert artifacts.canonical_yaml(host.canonical_payload(first)) == artifacts.canonical_yaml(
        host.canonical_payload(second)
    )
    assert host.render_execution(first) == host.render_execution(second)
    assert host.render_artifact(first) == host.render_artifact(second)
    assert first["rows"][0]["arguments_sha256"] == host.arguments_digest(operations[0].arguments)

    before = len(fake.calls)
    assert registry.execute(_request((_op("op-3", tool="SaveAsset"),)))["status"] == "rejected"
    forbidden_value = _op("op-4", arguments={"asset_path": "C:" + "/temp/index.ddc"})
    assert registry.execute(_request((forbidden_value,)))["status"] == "rejected"
    assert len(fake.calls) == before

    text = host.render_execution(first, limit=1)
    parsed = toon.parse_toon(text)
    assert text.startswith(toon.HEADER)
    assert parsed["summary"]["returned"] == "1"
    assert parsed["summary"]["truncated"] == "true"
    assert len(parsed["tables"]["host_execution"]) == 1
    for output in (text, host.render_artifact(first), host.render_page(first, limit=5)):
        _assert_clean_output(output)


def test_host_validation_runner_rejects_compile_and_pie_unless_injected() -> None:
    fake = host.FakeOfflineHostToolsetExecutor()
    registry = _registry(executor=fake)
    compile_ops = (_op("op-compile", tool="CompileHostOp", capability="compile"),)
    pie_ops = (_op("op-pie", tool="PIEHostOp", capability="PIE"),)
    for operations in (compile_ops, pie_ops):
        report = registry.execute(_request(operations))
        assert report["status"] == "rejected"
        assert report["reason"] == "injected_validation_runner_required"
    assert fake.calls == []

    for capability in ("compile", "PIE"):
        result = registry.run_validation(host.HostValidationRequest(
            capability=capability,
            approved_by="reviewer",
            operation_ids=("op-1",),
        ))
        assert result["status"] == "rejected"
        assert result["reason"] == "injected_validation_runner_required"
        assert result["host_only"] is True
        assert result["real_host_capability"] == "spec_not_landed"

    runner = host.FakeOfflineValidationRunner()
    registry.register_validation_runner(runner)
    validated = registry.run_validation(host.HostValidationRequest(
        capability="compile",
        approved_by="reviewer",
        operation_ids=("op-compile",),
    ))
    assert validated["status"] == "dry_run"
    assert validated["runner_status"] == "dry_run"
    assert runner.calls == [("compile", ("op-compile",))]
    assert registry.run_validation(host.HostValidationRequest(
        capability="PIE", approved_by="reviewer", dry_run=False,
    ))["status"] == "rejected"

    # Even with an injected runner, host-only operations never reach the toolset executor.
    report = registry.execute(_request(compile_ops))
    assert report["status"] == "deferred_offline"
    assert report["reason"] == "real_host_capability_spec_not_landed"
    assert fake.calls == []


def test_canonical_artifact_metadata_bounded_pages_and_status_honesty() -> None:
    fake = host.FakeOfflineHostToolsetExecutor()
    registry = _registry(executor=fake)
    operations = tuple(_op(f"op-{index:02d}") for index in range(6))
    report = registry.execute(_request(operations))
    meta = host.artifact_metadata(report)
    assert re.fullmatch(r"[0-9a-f]{64}", meta["canonical_sha256"])
    assert meta["artifact"].startswith(".ual/host/")
    assert ".." not in meta["artifact"] and ":" not in meta["artifact"]
    assert meta["real_host_executor"] == "spec_not_landed"
    assert meta["execution_kind"] == "offline_source_level_adapter"
    assert meta["line_count"] > 0
    assert meta["operation_count"] == 6
    assert host.render_artifact(report) == artifacts.canonical_yaml(meta)

    page = yaml.safe_load(host.render_page(report, offset=0, limit=5))
    assert page["returned"] <= 5
    assert page["limit"] == 5
    assert page["total"] >= page["returned"]
    assert isinstance(page["lines"], list)
    assert page["canonical_sha256"] == meta["canonical_sha256"]

    for risk in (
        "real_ue_host_executor_spec_not_landed",
        "real_uasset_write_spec_not_landed",
        "compile_pie_spec_not_landed",
        "asset_registry_rescan_spec_not_landed",
        "ue_native_binary_diff_spec_not_landed",
    ):
        assert risk in report["residual_risks"]
        assert risk in registry.snapshot()["residual_risks"]

    outputs = (
        host.render_execution(report),
        host.render_artifact(report),
        host.render_page(report),
        host.render_snapshot(registry),
    )
    assert outputs[3].startswith(toon.HEADER)
    for output in outputs:
        _assert_clean_output(output)


def test_protocols_and_source_level_boundaries() -> None:
    registry = host.default_registry()
    assert isinstance(host.FakeOfflineHostToolsetExecutor(), host.HostToolsetExecutor)
    assert isinstance(host.FakeOfflineValidationRunner(), host.HostValidationRunner)
    with pytest.raises(TypeError):
        registry.register_executor(object())
    with pytest.raises(TypeError):
        registry.register_validation_runner(object())

    source = (ROOT / "Server" / "ual_common" / "host_executor.py").read_text(encoding="utf-8")
    for token in _SOURCE_FORBIDDEN:
        assert token not in source, token
    assert "write_artifact(" not in source
    assert "yaml.safe_load" not in source
    assert "artifacts.canonical_yaml" in source
    assert "toon.render_toon" in source
