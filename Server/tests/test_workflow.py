"""Contract tests for the bounded ten-step FPS 3C workflow."""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ual_common import artifacts, workflow
from ual_common.toon import parse_toon, render_toon

REPO = Path(__file__).resolve().parents[2]
WORKFLOW_SOURCE = REPO / "Server" / "ual_common" / "workflow.py"
TOON_HEADER = "# UEAssetLens TOON v1 (Zero-VLM)"
WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"']+")
TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*")
GUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
HEX_NAME = re.compile(r"[0-9a-f]{64}\.yaml\Z")


def _assert_clean(text: str) -> None:
    assert not WINDOWS_PATH.search(text)
    assert not TIMESTAMP.search(text)
    assert not GUID.search(text)
    assert "/tmp/" not in text and "/home/" not in text
    assert "TODO(human)" not in text
    assert "content_hash" not in text
    assert "ual_" not in text


def _assert_report(text: str) -> dict[str, Any]:
    assert text.startswith(TOON_HEADER)
    _assert_clean(text)
    return parse_toon(text)


def _fixed_id(label: str) -> str:
    return artifacts.digest({"fixture": label})


def _pointer(kind: str, identifier: str) -> str:
    return f".ual/{kind}/{identifier}.yaml"


def _stub_report(summary: dict[str, Any]) -> str:
    return render_toon([], summary=summary)


def _install_revision(monkeypatch: Any) -> None:
    monkeypatch.setitem(
        sys.modules,
        "ual_common.revision",
        SimpleNamespace(index_revision=lambda con: "revision-fixture"),
    )


def _install_concurrent(
    monkeypatch: Any, *, verify_fail: bool = False
) -> dict[str, list[dict[str, Any]]]:
    calls: dict[str, list[dict[str, Any]]] = {
        "plan_edit": [],
        "apply_plan": [],
        "verify_edit": [],
        "rollback_edit": [],
        "request_rescan": [],
        "diff_assets": [],
        "verify_diff_determinism": [],
        "emit": [],
        "search_assets": [],
        "policy_gate": [],
    }
    plan_id = _fixed_id("plan")
    diff_id = _fixed_id("diff")

    def plan_edit(con: Any, **kwargs: Any) -> str:
        calls["plan_edit"].append(kwargs)
        return _stub_report({
            "status": "planned",
            "plan_id": plan_id,
            "artifact": _pointer("plans", plan_id),
        })

    def apply_plan(con: Any, plan_key: str | None = None, **kwargs: Any) -> str:
        calls["apply_plan"].append({"plan_id": plan_key, **kwargs})
        return _stub_report({
            "status": "approved",
            "artifact": _pointer("approvals", plan_id),
        })

    def verify_edit(con: Any, plan_key: str | None = None, **kwargs: Any) -> str:
        calls["verify_edit"].append({"plan_id": plan_key, **kwargs})
        return _stub_report({
            "status": "fail" if verify_fail else "pass",
            "artifact": _pointer("verification", plan_id),
        })

    def rollback_edit(con: Any, plan_key: str | None = None, **kwargs: Any) -> str:
        calls["rollback_edit"].append({"plan_id": plan_key, **kwargs})
        return _stub_report({
            "status": "reverted",
            "artifact": _pointer("rollbacks", plan_id),
        })

    def request_rescan(con: Any, **kwargs: Any) -> str:
        calls["request_rescan"].append(kwargs)
        return _stub_report({"status": "rescan_requested"})

    def diff_assets(
        con: Any, lhs: str | None = None, rhs: str | None = None, **kwargs: Any
    ) -> str:
        calls["diff_assets"].append({"lhs": lhs, "rhs": rhs, **kwargs})
        return _stub_report({
            "status": "diffed",
            "diff_id": diff_id,
            "artifact": _pointer("diffs", diff_id),
        })

    def verify_diff_determinism(
        con: Any, diff_key: str | None = None, **kwargs: Any
    ) -> str:
        calls["verify_diff_determinism"].append({"diff_id": diff_key, **kwargs})
        return _stub_report({"status": "deterministic"})

    def emit(
        event: str | None = None,
        payload: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        calls["emit"].append({"event": event, "payload": payload or {}, **kwargs})

    def events(root: Path | None = None, **kwargs: Any) -> list[str]:
        return ["observed"] * 10

    modules = {
        "transaction": SimpleNamespace(
            plan_edit=plan_edit,
            apply_plan=apply_plan,
            verify_edit=verify_edit,
            rollback_edit=rollback_edit,
            request_rescan=request_rescan,
        ),
        "asset_diff": SimpleNamespace(
            diff_assets=diff_assets,
            verify_diff_determinism=verify_diff_determinism,
        ),
        "lifecycle": SimpleNamespace(emit=emit, events=events),
    }
    monkeypatch.setattr(workflow, "_module", lambda name: modules.get(name))

    def search_assets(con: Any, **kwargs: Any) -> list[dict[str, str]]:
        calls["search_assets"].append(kwargs)
        return [
            {"asset_path": "/Game/FPS3C/A"},
            {"asset_path": "/Game/FPS3C/B"},
        ]

    def run_policy_gate(con: Any, **kwargs: Any) -> tuple[str, list[Any], None]:
        calls["policy_gate"].append(kwargs)
        return (
            _stub_report({
                "total": 0,
                "blocking": 0,
                "returned": 0,
                "truncated": "false",
            }),
            [],
            None,
        )

    monkeypatch.setattr(workflow.db, "search_assets", search_assets)
    monkeypatch.setattr(
        workflow.policy_gate, "run_policy_gate", run_policy_gate
    )
    return calls


def test_ten_step_workflow_is_bounded_dry_run_and_rolls_back(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _install_revision(monkeypatch)
    calls = _install_concurrent(monkeypatch, verify_fail=True)
    root = tmp_path / ".ual"
    intent = "FPS 3C shooter 射击"
    report = workflow.run_fps_3c(
        object(), intent, project_root=tmp_path, root=root, dry_run=True
    )
    parsed = _assert_report(report)
    rows = parsed["tables"]["steps"]
    assert len(rows) == 10
    assert [row["step"] for row in rows] == [str(i) for i in range(1, 11)]
    assert [row["name"] for row in rows] == list(workflow.STEP_NAMES)
    assert list(rows[0]) == list(workflow.STEP_FIELDS)
    assert rows[6]["status"] == "deferred_offline"
    assert "engine_writes_not_executed" in rows[6]["result"]
    assert rows[8]["status"] == "fail"
    assert rows[9]["status"] == "completed"
    assert "dry_run=true" in rows[9]["result"]
    assert calls["apply_plan"][-1]["dry_run"] is True
    assert calls["rollback_edit"][-1]["dry_run"] is True
    assert calls["request_rescan"][-1]["dry_run"] is True
    assert calls["verify_diff_determinism"]
    assert calls["emit"]
    assert parsed["summary"]["applied"] == "false"
    assert parsed["summary"]["engine_validation"] == "deferred_offline"
    pointer = parsed["summary"]["artifact"]
    assert pointer.startswith(".ual/workflow/")
    assert (root / pointer[len(".ual/"):]).is_file()
    _assert_clean((root / pointer[len(".ual/"):]).read_text(encoding="utf-8"))
    assert intent not in report
    assert "/Game/FPS3C" not in report
    assert "BP_FPSCharacter" not in report
    assert "entities" not in report
    assert report == workflow.run_fps_3c(
        object(), intent, project_root=tmp_path, root=root, dry_run=True
    )


def test_unsupported_intent_still_returns_ten_bounded_steps(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _install_revision(monkeypatch)
    _install_concurrent(monkeypatch)
    root = tmp_path / ".ual"
    report = workflow.run_fps_3c(
        object(), "racing game", project_root=tmp_path, root=root
    )
    parsed = _assert_report(report)
    rows = parsed["tables"]["steps"]
    assert len(rows) == 10
    assert rows[0]["status"] == "unsupported_intent"
    assert all(row["status"] == "skipped" for row in rows[1:])
    assert "racing game" not in report
    assert parsed["summary"]["applied"] == "false"


def test_workflow_artifacts_have_no_timestamps_paths_or_random_ids(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _install_revision(monkeypatch)
    _install_concurrent(monkeypatch)
    root = tmp_path / ".ual"
    report = workflow.run_fps_3c(
        object(), "FPS 3C shooter", project_root=tmp_path, root=root
    )
    _assert_report(report)
    files = sorted(path for path in root.rglob("*.yaml") if path.is_file())
    assert files
    for path in files:
        assert HEX_NAME.fullmatch(path.name)
        text = path.read_text(encoding="utf-8")
        _assert_clean(text)
        assert "TODO(human)" not in text
        assert "content_hash" not in text
        assert "ual_" not in text
        assert not WINDOWS_PATH.search(text)
        assert not TIMESTAMP.search(text)
        assert not GUID.search(text)


def test_workflow_does_not_import_tools_module() -> None:
    tree = ast.parse(WORKFLOW_SOURCE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not any("ual_mcp" in name or "tools" in name for name in imported)


def test_non_dry_run_workflow_gate_propagates_dry_run(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _install_revision(monkeypatch)
    calls = _install_concurrent(monkeypatch, verify_fail=True)
    validation_calls: list[dict[str, Any]] = []

    def validation_loop(con: Any, game_spec_id: str, **kwargs: Any) -> str:
        validation_calls.append({"game_spec_id": game_spec_id, **kwargs})
        return _stub_report({"status": "static_assertions_pass"})

    monkeypatch.setattr(workflow.gamespec, "run_validation_loop", validation_loop)
    executor_calls: list[object] = []

    def executor(tool: str, arguments: object) -> dict[str, str]:
        executor_calls.append((tool, arguments))
        return {"status": "ok"}

    template = workflow.gamespec.load_template("fps_3c", project_root=tmp_path)
    operation_ids = [str(item["id"]) for item in template["operations"]]
    approvals = [
        "RunFps3CWorkflow", "RunValidationLoop", "RequestRescan",
        "ApplyPlan", "RollbackEdit",
        *operation_ids,
        *[f"{op_id}.inverse" for op_id in operation_ids],
    ]
    root = tmp_path / ".ual"
    with pytest.raises(ValueError):
        workflow.run_fps_3c(
            object(), "FPS 3C shooter", project_root=tmp_path, root=root,
            dry_run=False, approved_by="reviewer", approvals="", executor=executor,
        )
    assert not calls["apply_plan"]
    assert not executor_calls

    report = workflow.run_fps_3c(
        object(), "FPS 3C shooter", project_root=tmp_path, root=root,
        dry_run=False, approved_by="reviewer", approvals=",".join(approvals),
        executor=executor,
    )
    _assert_report(report)
    assert calls["apply_plan"][-1]["dry_run"] is False
    assert calls["apply_plan"][-1]["approvals"] == ",".join(operation_ids)
    assert calls["rollback_edit"][-1]["dry_run"] is False
    assert calls["rollback_edit"][-1]["approvals"] == ",".join(
        f"{op_id}.inverse" for op_id in operation_ids
    )
    assert calls["request_rescan"][-1]["dry_run"] is False
    assert validation_calls[-1]["dry_run"] is False
    assert validation_calls[-1]["host_runner"] is executor
