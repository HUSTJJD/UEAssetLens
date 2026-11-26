"""Contract tests for bounded, deterministic GameSpec expansion/validation."""
from __future__ import annotations

import ast
import copy
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from ual_common import artifacts, gamespec, settings
from ual_common.toon import parse_toon

REPO = Path(__file__).resolve().parents[2]
GAMESPEC_SOURCE = REPO / "Server" / "ual_common" / "gamespec.py"
WORKFLOW_SOURCE = REPO / "Server" / "ual_common" / "workflow.py"
TEMPLATE_SOURCE = REPO / "Config" / "UAL" / "templates" / "fps_3c.yaml"
TOON_HEADER = "# UEAssetLens TOON v1 (Zero-VLM)"
WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"']+")
TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*")
GUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
BANNED_IMPORTS = {
    "requests", "urllib", "http", "socket", "httpx", "boto3", "openai",
    "anthropic", "transformers", "torch", "tensorflow", "cv2", "PIL",
}


def _install_revision(monkeypatch: Any) -> None:
    monkeypatch.setitem(
        sys.modules,
        "ual_common.revision",
        SimpleNamespace(index_revision=lambda con: "revision-fixture"),
    )


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


def _whitelist(project_root: Path) -> set[str]:
    resolved = settings.load_namespace("gateway", project_root=project_root)
    return {str(item["name"]) for item in resolved.data["tools"]}


def test_load_template_is_declarative_and_whitelisted(tmp_path: Path) -> None:
    payload = gamespec.load_template("fps_3c", project_root=tmp_path)
    assert str(payload["version"]) == "1"
    assert set(gamespec.GAME_SPEC_FIELDS) <= set(payload["game_spec"])
    assert {"impl", "handler", "eval", "exec", "code"}.isdisjoint(payload)
    allowed = _whitelist(tmp_path)
    assert all(
        op["tool"] in allowed and op["inverse"] in allowed
        for op in payload["operations"]
    )
    assert [stage for stage in payload["validation"]["stages"]] == list(
        gamespec.VALIDATION_STAGES
    )
    assert len(payload["assets"]) >= 13


def test_expand_game_spec_writes_full_artifact_and_returns_bounded_toon(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _install_revision(monkeypatch)
    root = tmp_path / ".ual"
    intent = "FPS 3C shooter 射击"
    report = gamespec.expand_game_spec(
        object(), intent, project_root=tmp_path, root=root
    )
    parsed = _assert_report(report)
    summary = parsed["summary"]
    assert summary["status"] == "expanded"
    assert summary["template"] == "fps_3c"
    assert int(summary["asset_count"]) >= 13
    assert int(summary["operation_count"]) > 0
    pointer = summary["artifact"]
    assert pointer.startswith(".ual/gamespecs/")
    relative = pointer[len(".ual/"):]
    assert (root / relative).is_file()
    payload = artifacts.read_artifact(root, relative)
    assert set(gamespec.GAME_SPEC_FIELDS) <= set(payload["spec"])
    assert payload["artifact"] == pointer
    assert payload["intent_markers"] == ["fps", "3c", "shooter", "射击"]
    assert intent not in payload.get("intent_sha256", "")
    assert all(
        op["tool"] in _whitelist(tmp_path) for op in payload["operations"]
    )
    full_text = (root / relative).read_text(encoding="utf-8")
    _assert_clean(full_text)
    assert intent not in full_text
    assert "/Game/FPS3C" not in report
    assert "BP_FPSCharacter" not in report
    assert "movement.walk_speed" not in report
    assert "player" not in report
    assert report == gamespec.expand_game_spec(
        object(), intent, project_root=tmp_path, root=root
    )


def test_unsupported_intent_returns_bounded_error(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _install_revision(monkeypatch)
    root = tmp_path / ".ual"
    report = gamespec.expand_game_spec(
        object(), "racing game", project_root=tmp_path, root=root
    )
    parsed = _assert_report(report)
    assert parsed["summary"]["status"] == "unsupported_intent"
    assert parsed["tables"]["errors"][0]["code"] == "unsupported_intent"
    assert parsed["tables"]["sections"] == []
    assert "racing game" not in report
    assert not (root / "gamespecs").exists()


def _expanded_id(tmp_path: Path, monkeypatch: Any, root: Path) -> str:
    _install_revision(monkeypatch)
    report = gamespec.expand_game_spec(
        object(), "FPS shooter 3C", project_root=tmp_path, root=root
    )
    return parse_toon(report)["summary"]["game_spec_id"]


def test_offline_validation_defers_engine_stages_and_passes_static_only(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = tmp_path / ".ual"
    game_spec_id = _expanded_id(tmp_path, monkeypatch, root)
    report = gamespec.run_validation_loop(
        object(), game_spec_id, project_root=tmp_path, root=root
    )
    parsed = _assert_report(report)
    stages = parsed["tables"]["stages"]
    assert [row["stage"] for row in stages] == list(gamespec.VALIDATION_STAGES)
    assert stages[0]["status"] == "deferred_offline"
    assert "engine_compile_not_run" in stages[0]["result"]
    assert stages[1]["status"] == "deferred_offline"
    assert "pie_not_run" in stages[1]["result"]
    assert stages[3]["status"] == "pass"
    assert all(
        row["status"] != "pass"
        for row in stages
        if row["stage"] != "assert"
    )
    assert parsed["summary"]["engine_validation"] == "not_performed_by_lens"
    assert all(row["status"] == "pass" for row in parsed["tables"]["assertions"])
    pointer = parsed["summary"]["artifact"]
    assert pointer.startswith(".ual/validation/")
    validation_text = (root / pointer[len(".ual/"):]).read_text(encoding="utf-8")
    _assert_clean(validation_text)
    assert "compile_pass" not in validation_text
    assert "pie_pass" not in validation_text


def test_host_runner_must_be_python_callable(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = tmp_path / ".ual"
    game_spec_id = _expanded_id(tmp_path, monkeypatch, root)
    report = gamespec.run_validation_loop(
        object(),
        game_spec_id,
        runner="host",
        host_runner="not-callable-yaml-data",
        project_root=tmp_path,
        root=root,
    )
    parsed = _assert_report(report)
    assert parsed["tables"]["errors"][0]["code"] == "python_host_runner_required"
    assert all(row["status"] == "not_run" for row in parsed["tables"]["stages"])


def test_host_runner_results_are_bounded_and_never_claim_engine_compile(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = tmp_path / ".ual"
    game_spec_id = _expanded_id(tmp_path, monkeypatch, root)
    seen: list[str] = []

    def host(stage: str, context: dict[str, Any]) -> dict[str, str]:
        seen.append(stage)
        assert context["runner"] == "host"
        assert context["dry_run"] is True
        return {
            "status": "pass",
            "result": "D:/host-only 2026-11-26T12:00:00Z",
        }

    report = gamespec.run_validation_loop(
        object(),
        game_spec_id,
        runner="host",
        dry_run=True,
        host_runner=host,
        project_root=tmp_path,
        root=root,
    )
    parsed = _assert_report(report)
    stages = parsed["tables"]["stages"]
    assert seen == list(gamespec.VALIDATION_STAGES)
    assert stages[0]["status"] == "host_reported"
    assert stages[1]["status"] == "host_reported"
    assert "engine_not_run_by_lens" in stages[0]["result"]
    assert stages[3]["status"] == "pass"
    assert all(
        row["status"] != "pass"
        for row in stages
        if row["stage"] != "assert"
    )
    pointer = parsed["summary"]["artifact"]
    text = (root / pointer[len(".ual/"):]).read_text(encoding="utf-8")
    _assert_clean(text)
    assert "D:/host-only" not in report and "D:/host-only" not in text
    assert "2026-11-26" not in report and "2026-11-26" not in text


def test_no_vlm_network_dynamic_code_or_noise_markers() -> None:
    for source_path in (GAMESPEC_SOURCE, WORKFLOW_SOURCE):
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                roots = {(node.module or "").split(".")[0]}
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"eval", "exec"}
            ):
                raise AssertionError(f"dynamic code call in {source_path.name}")
            else:
                continue
            assert roots.isdisjoint(BANNED_IMPORTS)
        assert "TODO(human)" not in source
        assert "content_hash" not in source
        assert "yaml.load(" not in source
    template = TEMPLATE_SOURCE.read_text(encoding="utf-8")
    assert "TODO(human)" not in template
    assert "content_hash" not in template
    assert "!!python" not in template
    assert "ual_" not in template
    assert not WINDOWS_PATH.search(template)
    assert not TIMESTAMP.search(template)
    assert not GUID.search(template)


TODO_MARKER = "TODO" + "(human)"
IMPORT_FORBIDDEN_MARKERS = (
    "vlm", "cloud", "gpu", "network", "http", "url", "socket", "endpoint",
    "localhost", "inference", "random", "local_path", "file://",
    "content_hash", "ual_", "compile_pass", "pie_pass", "engine_compile",
    "engine_pie",
)


def _assert_import_bounded(text: str) -> None:
    body = "\n".join(text.splitlines()[1:]) if text.startswith(TOON_HEADER) else text
    lowered = body.casefold()
    for marker in IMPORT_FORBIDDEN_MARKERS:
        assert marker not in lowered
    assert TODO_MARKER not in body


def _host_payload(project_root: Path) -> dict[str, Any]:
    template = gamespec.load_template("fps_3c", project_root=project_root)
    return {
        "template": "fps_3c",
        "game_spec": template["game_spec"],
        "assets": template["assets"],
        "assembly": template["assembly"],
        "operations": template["operations"],
        "assertions": template["assertions"],
        "validation": template["validation"],
    }


def _bad_payload_cases() -> list[tuple[Any, str]]:
    cases: list[tuple[Any, str]] = []

    def add(mutate: Any, code: str) -> None:
        cases.append((mutate, code))

    add(lambda p: p["game_spec"]["entities"][0].update({"impl": "fixture"}), "forbidden_key")
    add(lambda p: p["game_spec"]["rules"][0].update({"handler": "fixture"}), "forbidden_key")
    add(lambda p: p["assets"][0].update({"eval": "fixture"}), "forbidden_key")
    add(lambda p: p["game_spec"]["entities"][0].update({"content_hash": "fixture"}), "forbidden_key")
    add(lambda p: p["game_spec"].update({"domain": "vlm_fps"}), "forbidden_reference")
    add(lambda p: p["game_spec"].update({"domain": "cloud_fps"}), "forbidden_reference")
    add(lambda p: p["game_spec"].update({"domain": "gpu_fps"}), "forbidden_reference")
    add(lambda p: p["game_spec"].update({"domain": "network_fps"}), "forbidden_reference")
    add(lambda p: p["game_spec"].update({"domain": "http://example.invalid"}), "forbidden_reference")
    add(lambda p: p["game_spec"].update({"domain": "2026-11-27T12:00:00Z"}), "forbidden_reference")
    add(lambda p: p["assets"][0].update({"asset_path": "D:/tmp/plan.yaml"}), "forbidden_reference")
    add(
        lambda p: p["game_spec"]["entities"][0].update(
            {"id": "550e8400-e29b-41d4-a716-446655440000"}
        ),
        "forbidden_reference",
    )
    add(lambda p: p["game_spec"].update({"domain": TODO_MARKER}), "forbidden_reference")
    add(lambda p: p["operations"][0].update({"tool": "NoSuchTool"}), "operation_invalid")
    add(lambda p: p["operations"][0].update({"inverse": "ual_NoSuchTool"}), "forbidden_reference")
    add(lambda p: p["assembly"][0].update({"target": "not_declared"}), "relationship_invalid")
    add(lambda p: p["assets"][0].update({"id": "not_declared"}), "relationship_invalid")
    return cases


def test_import_game_spec_writes_full_canonical_artifact_and_returns_bounded_toon(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".ual"
    payload = _host_payload(tmp_path)
    report = gamespec.import_game_spec(
        payload, project_root=tmp_path, root=root
    )
    parsed = _assert_report(report)
    _assert_import_bounded(report)
    summary = parsed["summary"]
    assert summary["status"] == "imported"
    assert summary["template"] == "fps_3c"
    assert int(summary["asset_count"]) >= 13
    assert int(summary["operation_count"]) > 0
    assert int(summary["relation_count"]) > 0
    assert int(summary["assertion_count"]) > 0
    pointer = summary["artifact"]
    assert pointer.startswith(".ual/gamespecs/")
    assert summary["canonical_sha256"] == summary["sha256"]

    artifact_path = root / pointer[len(".ual/"):]
    assert artifact_path.is_file()
    full_text = artifact_path.read_text(encoding="utf-8")
    _assert_clean(full_text)
    _assert_import_bounded(full_text)
    assert summary["canonical_sha256"] == artifacts.sha256_text(full_text)
    assert len(full_text.splitlines()) > artifacts.MAX_PAGE_LINES
    artifact = yaml.safe_load(full_text)
    assert set(gamespec.GAME_SPEC_FIELDS) <= set(artifact["spec"])
    assert artifact["game_spec_id"] == summary["game_spec_id"]
    assert artifact["artifact"] == pointer
    assert artifact["source"] == "structured_host"
    assert artifact["template"]["id"] == "fps_3c"
    allowed = _whitelist(tmp_path)
    assert all(op["tool"] in allowed and op["inverse"] in allowed for op in artifact["operations"])

    collected: list[str] = []
    offset = 0
    while True:
        page_text = artifacts.read_artifact_page(
            tmp_path, pointer, offset=offset, limit=artifacts.MAX_PAGE_LINES
        )
        _assert_clean(page_text)
        _assert_import_bounded(page_text)
        page = yaml.safe_load(page_text)
        assert page["relative"] == pointer
        assert page["offset"] == offset
        assert page["limit"] == artifacts.MAX_PAGE_LINES
        assert page["total_lines"] == len(full_text.splitlines())
        assert page["sha256"] == summary["canonical_sha256"]
        collected.extend(page["lines"])
        cursor = page["next_cursor"]
        if not cursor:
            break
        assert cursor == str(offset + len(page["lines"]))
        offset = int(cursor)
    assert "\n".join(collected) + "\n" == full_text

    validation = _assert_report(
        gamespec.run_validation_loop(
            object(), summary["game_spec_id"], project_root=tmp_path, root=root
        )
    )
    assert validation["summary"]["status"] == "static_assertions_pass"
    assert validation["tables"]["errors"] == []
    assert report == gamespec.import_game_spec(
        payload, project_root=tmp_path, root=root
    )


def test_import_game_spec_rejects_forbidden_keys_and_external_references(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".ual"
    payload = _host_payload(tmp_path)
    for mutate, expected_code in _bad_payload_cases():
        bad = copy.deepcopy(payload)
        mutate(bad)
        report = gamespec.import_game_spec(
            bad, project_root=tmp_path, root=root
        )
        parsed = _assert_report(report)
        _assert_import_bounded(report)
        assert parsed["summary"]["status"] == "rejected"
        assert parsed["summary"]["artifact"] == ""
        assert parsed["summary"]["canonical_sha256"] == ""
        assert parsed["tables"]["errors"][0]["code"] == expected_code
        assert "impl" not in report
        assert "handler" not in report
        assert "eval" not in report
        assert not root.exists() or not list(root.rglob("*.yaml"))
