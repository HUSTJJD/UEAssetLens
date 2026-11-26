"""Contract tests for offline edit transactions (no UE SDK, no engine writes)."""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from ual_common import artifacts, toon, transaction

HEADER = "# UEAssetLens TOON v1 (Zero-VLM)"


class FakeRevision:
    def __init__(self, value="rev-1"):
        self.value = value

    def index_revision(self, con):
        return self.value


class FakeLifecycle:
    def __init__(self):
        self.events = []

    def emit(self, event, payload, *, project_root=None, root=Path(".ual")):
        self.events.append((event, dict(payload)))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    revision = FakeRevision()
    lifecycle = FakeLifecycle()
    monkeypatch.setattr(transaction, "revision", revision, raising=False)
    monkeypatch.setattr(transaction, "lifecycle", lifecycle, raising=False)
    return SimpleNamespace(
        root=tmp_path / ".ual",
        project_root=tmp_path,
        con=object(),
        revision=revision,
        lifecycle=lifecycle,
    )


def _call(env, function, *args, **kwargs):
    return function(
        *args,
        project_root=env.project_root,
        root=Path(".ual"),
        **kwargs,
    )


def _plan(env, intent="add tags", candidates=("/Game/Hero/M_Hero",), **kwargs):
    out = _call(
        env,
        transaction.plan_edit,
        env.con,
        intent,
        list(candidates),
        **kwargs,
    )
    assert out.startswith(HEADER)
    return out, toon.parse_toon(out)["summary"]["plan_id"]


def _plan_artifact(env, plan_id):
    return artifacts.read_artifact(env.root, f"plans/{plan_id}.yaml")


def test_plan_id_is_deterministic_and_artifact_is_complete(env):
    out1, pid1 = _plan(env, "add tags", ("/Game/B", "/Game/A"))
    out2, pid2 = _plan(env, "add tags", ("/Game/B", "/Game/A"))
    assert out1 == out2
    assert pid1 == pid2
    assert pid1 == artifacts.digest(
        {
            "intent": "add tags",
            "candidates": ["/Game/A", "/Game/B"],
            "index_revision": "rev-1",
        }
    )
    plan = _plan_artifact(env, pid1)
    assert plan["state"] == "planned"
    assert plan["scope"] == "/Game"
    assert plan["baseline_revision"] == "rev-1"
    assert plan["candidates"] == ["/Game/A", "/Game/B"]
    assert [op["tool"] for op in plan["operations"]] == ["AddUserAssetTags"] * 2
    assert [op["tool"] for op in plan["inverse_ops"]] == ["AddUserAssetTags"] * 2
    assert all(
        {"id", "tool", "arguments", "accessMode", "inverse", "risk"} <= set(op)
        for op in plan["operations"] + plan["inverse_ops"]
    )
    assert plan["diff_preview"].startswith(HEADER)
    parsed = toon.parse_toon(out1)
    assert parsed["summary"]["artifact"] == f".ual/plans/{pid1}.yaml"
    assert "diff_preview" in parsed["tables"]
    assert ("OnPlanned", {"plan_id": pid1}) in [
        (event, {"plan_id": payload.get("plan_id")})
        for event, payload in env.lifecycle.events
    ]


def test_operations_yaml_is_safe_loaded_and_validated(env):
    source = artifacts.canonical_yaml(
        [{"tool": "SaveAsset", "arguments": {"asset_path": "/Game/Hero/M_Hero"}}]
    )
    _out, pid = _plan(env, operations_yaml=source)
    plan = _plan_artifact(env, pid)
    assert [op["tool"] for op in plan["operations"]] == ["SaveAsset"]
    assert [op["tool"] for op in plan["inverse_ops"]] == ["SaveAsset"]
    bad = artifacts.canonical_yaml([{"tool": "NoSuchTool", "arguments": {}}])
    with pytest.raises(ValueError):
        _plan(env, operations_yaml=bad)
    unsafe = artifacts.canonical_yaml(
        [{"tool": "SaveAsset", "arguments": {"asset_path": "D:/work/A.uasset"}}]
    )
    with pytest.raises(ValueError):
        _plan(env, operations_yaml=unsafe)


def test_apply_requires_approver_and_explicit_approval_ids(env):
    _out, pid = _plan(env)
    with pytest.raises(ValueError):
        _call(
            env,
            transaction.apply_plan,
            env.con,
            pid,
            approvals="op-001",
            dry_run=False,
        )
    with pytest.raises(ValueError):
        _call(
            env,
            transaction.apply_plan,
            env.con,
            pid,
            approved_by="human",
            dry_run=False,
        )
    with pytest.raises(ValueError):
        _call(
            env,
            transaction.apply_plan,
            env.con,
            pid,
            approved_by="human",
            approvals="all",
            dry_run=False,
        )
    assert _plan_artifact(env, pid)["state"] == "planned"


def test_dry_run_and_offline_default_never_execute(env):
    _out, pid = _plan(env)
    dry = toon.parse_toon(
        _call(
            env,
            transaction.apply_plan,
            env.con,
            pid,
            approved_by="human",
            approvals="op-001",
        )
    )
    assert dry["summary"]["state"] == "approved"
    assert dry["summary"]["status"] == "dry_run"
    assert _plan_artifact(env, pid)["state"] == "approved"
    assert not (env.root / "staging" / "rescan.yaml").exists()

    offline = toon.parse_toon(
        _call(
            env,
            transaction.apply_plan,
            env.con,
            pid,
            approved_by="human",
            approvals="op-001",
            dry_run=False,
        )
    )
    assert offline["summary"]["state"] == "applied"
    assert offline["summary"]["status"] == "deferred_offline"
    plan = _plan_artifact(env, pid)
    assert plan["state"] == "applied"
    assert [row["status"] for row in plan["results"]] == ["deferred_offline"]
    assert plan["state_history"] == ["planned", "approved", "applied"]
    assert (env.root / "staging" / "rescan.yaml").exists()
    assert "OnCommitted" in [event for event, _ in env.lifecycle.events]
    assert "OnPublished" not in [event for event, _ in env.lifecycle.events]


def test_executor_commits_and_publishes(env):
    _out, pid = _plan(env)

    def executor(tool, arguments):
        assert tool == "AddUserAssetTags"
        return {"status": "ok"}

    committed = toon.parse_toon(
        _call(
            env,
            transaction.apply_plan,
            env.con,
            pid,
            approved_by="human",
            approvals="op-001",
            dry_run=False,
            executor=executor,
        )
    )
    assert committed["summary"]["state"] == "committed"
    assert committed["summary"]["status"] == "executed"
    assert _plan_artifact(env, pid)["state"] == "committed"
    assert {event for event, _ in env.lifecycle.events} >= {
        "OnPlanned", "OnCommitted", "OnPublished",
    }


def test_verify_reports_bounded_results_and_residual_risks(env):
    _out, pid = _plan(env)
    out = _call(env, transaction.verify_edit, env.con, pid)
    parsed = toon.parse_toon(out)
    assert out.startswith(HEADER)
    assert parsed["summary"]["index_consistent"] == "true"
    assert parsed["summary"]["baseline_revision"] == "rev-1"
    assert parsed["summary"]["current_revision"] == "rev-1"
    assert len(parsed["tables"]["per_op_result"]) == 1
    assert {row["risk"] for row in parsed["tables"]["residual_risks"]} == {
        "offline_host_required", "incremental_rescan_required",
    }
    assert "offline_host_required" in out and "incremental_rescan_required" in out


def test_rollback_uses_inverse_ops_and_stages_rescan(env):
    _out, pid = _plan(env)
    out = _call(
        env,
        transaction.rollback_edit,
        env.con,
        pid,
        approved_by="human",
        approvals="op-001.inverse",
    )
    parsed = toon.parse_toon(out)
    assert out.startswith(HEADER)
    assert parsed["summary"]["state"] == "rolled_back"
    assert parsed["summary"]["status"] == "dry_run"
    assert parsed["tables"]["reverted_ops"][0]["tool"] == "AddUserAssetTags"
    assert parsed["summary"]["rescan_artifact"] == ".ual/staging/rescan.yaml"
    assert _plan_artifact(env, pid)["state"] == "rolled_back"
    rescan = artifacts.read_artifact(env.root, "staging/rescan.yaml")
    assert rescan["asset_paths"] == ["/Game/Hero/M_Hero"]
    assert rescan["reason"] == "rollback index consistency"


def test_request_rescan_stages_sorted_paths(env):
    out = _call(
        env, transaction.request_rescan, env.con, "/Game/B,/Game/A", "index consistency"
    )
    parsed = toon.parse_toon(out)
    assert out.startswith(HEADER)
    assert parsed["summary"]["artifact"] == ".ual/staging/rescan.yaml"
    assert parsed["summary"]["dry_run"] == "true"
    assert [row["asset_path"] for row in parsed["tables"]["asset_paths"]] == [
        "/Game/A", "/Game/B",
    ]
    payload = artifacts.read_artifact(env.root, "staging/rescan.yaml")
    assert payload["asset_paths"] == ["/Game/A", "/Game/B"]
    assert payload["reason"] == "index consistency"
    with pytest.raises(ValueError):
        _call(env, transaction.request_rescan, env.con, "D:/work/A.uasset")


def test_renderers_are_pure_and_emit_only_toon():
    operation = {
        "id": "op-001",
        "tool": "SaveAsset",
        "arguments": {"asset_path": "/Game/A"},
        "accessMode": "Write",
        "inverse": "SaveAsset",
        "risk": "low",
    }
    report = {
        "plan_id": "abc",
        "state": "planned",
        "artifact": ".ual/plans/abc.yaml",
        "operations": [operation],
        "inverse_ops": [operation],
        "reverted_ops": [operation],
        "per_op_result": [{"id": "op-001", "tool": "SaveAsset", "status": "not_applied"}],
        "residual_risks": ["offline_host_required"],
        "asset_paths": ["/Game/A"],
    }
    for renderer in (
        transaction.render_plan,
        transaction.render_verify,
        transaction.render_rollback,
        transaction.render_rescan,
    ):
        first = renderer(report)
        assert first == renderer(report)
        assert first.startswith(HEADER)
        assert "D:/" not in first and ":\\" not in first


def test_integration_with_real_revision_and_lifecycle(tmp_path, monkeypatch):
    revision_module = pytest.importorskip("ual_common.revision")
    pytest.importorskip("ual_common.lifecycle")
    monkeypatch.setattr(transaction, "revision", None, raising=False)
    monkeypatch.setattr(transaction, "lifecycle", None, raising=False)

    class Cursor:
        description = [("key",), ("value",)]

        def fetchall(self):
            return []

    class Connection:
        def execute(self, sql, params=None):
            return Cursor()

    con = Connection()
    kw = dict(project_root=tmp_path, root=Path(".ual"))
    out = transaction.plan_edit(con, "add tags", ["/Game/A"], **kw)
    pid = toon.parse_toon(out)["summary"]["plan_id"]
    assert pid == artifacts.digest(
        {
            "intent": "add tags",
            "candidates": ["/Game/A"],
            "index_revision": revision_module.index_revision(con),
        }
    )
    applied = transaction.apply_plan(
        con, pid, approved_by="human", approvals="op-001", dry_run=False, **kw
    )
    verified = transaction.verify_edit(con, pid, **kw)
    rolled = transaction.rollback_edit(
        con, pid, approved_by="human", approvals="op-001.inverse", **kw
    )
    rescan = transaction.request_rescan(con, "/Game/A", **kw)
    for text in (out, applied, verified, rolled, rescan):
        assert text.startswith(HEADER)
        assert str(tmp_path) not in text and "D:/" not in text
    assert toon.parse_toon(applied)["summary"]["state"] == "applied"
    assert "offline_host_required" in verified
    assert toon.parse_toon(rolled)["summary"]["state"] == "rolled_back"
    assert (tmp_path / ".ual" / "staging" / "rescan.yaml").is_file()
    assert (tmp_path / ".ual" / "audit" / "events.yaml").is_file()


def test_artifacts_have_no_timestamps_absolute_paths_or_random_keys(env):
    _out, pid = _plan(env)
    _call(
        env,
        transaction.apply_plan,
        env.con,
        pid,
        approved_by="human",
        approvals="op-001",
        dry_run=False,
    )
    _call(
        env,
        transaction.rollback_edit,
        env.con,
        pid,
        approved_by="human",
        approvals="op-001.inverse",
    )
    _call(env, transaction.request_rescan, env.con, "/Game/A,/Game/B")
    files = sorted(env.root.rglob("*.yaml"))
    assert len(files) >= 2
    absolute = str(env.root).replace("\\", "/")
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert absolute not in text
        assert str(env.root) not in text
        assert not re.search(r"[A-Za-z]:[\\/]", text)
        assert not re.search(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", text)
        assert "updated_at" not in text and "timestamp:" not in text
        assert "session_id" not in text and "random_id" not in text


def test_rollback_approval_gate_and_baseline_restoration(env):
    _out, pid = _plan(env)
    calls: list[tuple[str, dict]] = []

    def executor(tool, arguments):
        calls.append((tool, dict(arguments)))
        return {"status": "ok"}

    with pytest.raises(ValueError):
        _call(
            env, transaction.rollback_edit, env.con, pid,
            approvals="op-001.inverse", dry_run=False, executor=executor,
        )
    with pytest.raises(ValueError):
        _call(
            env, transaction.rollback_edit, env.con, pid,
            approved_by="human", dry_run=False, executor=executor,
        )
    with pytest.raises(ValueError):
        _call(
            env, transaction.rollback_edit, env.con, pid,
            approved_by="human", approvals="op-001", dry_run=False,
            executor=executor,
        )
    assert calls == []
    report = _call(
        env, transaction.rollback_edit, env.con, pid,
        approved_by="human", approvals="op-001.inverse", dry_run=False,
        executor=executor,
    )
    assert toon.parse_toon(report)["summary"]["status"] == "executed"
    assert calls == [(
        "AddUserAssetTags",
        {"asset_paths": ["/Game/Hero/M_Hero"], "tags": []},
    )]
    plan = _plan_artifact(env, pid)
    assert plan["approved_by"] == "human"
    assert plan["approvals"] == ["op-001.inverse"]


def test_irreversible_inverse_is_refused(env):
    source = artifacts.canonical_yaml([
        {"tool": "Delete", "arguments": {"asset_path": "/Game/Hero/M_Hero"}}
    ])
    with pytest.raises(ValueError, match="not recoverable"):
        _plan(env, operations_yaml=source)


def test_request_rescan_non_dry_run_requires_host_executor(env):
    calls: list[tuple[str, dict]] = []

    def executor(tool, arguments):
        calls.append((tool, dict(arguments)))
        return {"status": "ok"}

    with pytest.raises(ValueError):
        _call(
            env, transaction.request_rescan, env.con, "/Game/Hero/M_Hero",
            dry_run=False, approved_by="human", approvals="RequestRescan",
        )
    assert calls == []
    out = _call(
        env, transaction.request_rescan, env.con, "/Game/Hero/M_Hero",
        dry_run=False, approved_by="human", approvals="RequestRescan",
        executor=executor,
    )
    assert toon.parse_toon(out)["summary"]["dry_run"] == "false"
    assert calls and calls[0][0] == "RequestRescan"
