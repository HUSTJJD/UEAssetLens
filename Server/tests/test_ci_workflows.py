"""Read-only contracts for the docs, Python CI and release workflows.

These checks parse the workflow YAML only. They do not run a CI job, a UE SDK,
a C++ compiler, mkdocs, a generator or a network service.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
DOCS = WORKFLOWS / "docs.yml"
PYTHON_CI = WORKFLOWS / "python-ci.yml"
RELEASE = WORKFLOWS / "release.yml"
ALL_WORKFLOWS = (DOCS, PYTHON_CI, RELEASE)

REQUIRED_DOCS_PATHS = (
    "Server/tests/**",
    "Tools/run_tests.ps1",
    "Tools/setup_mcp.ps1",
    "Tools/bench_index.py",
    ".github/workflows/python-ci.yml",
    ".github/workflows/release.yml",
)
EXISTING_DOCS_PATHS = (
    "docs/**", "mkdocs.yml", "Config/UAL/**", "Server/schema/**",
    "Server/ual_common/**", "Server/ual_mcp/**", "Server/ual_cli/**",
    "Tools/gen_docs.py", "Tools/gen_taxonomy.py",
    "Tools/audit_tool_annotations.py", "Tools/audit_engine_toolsets.py",
    "requirements*.txt", "CLAUDE.md", ".github/workflows/docs.yml",
)
OFFLINE_GATE_COMMANDS = (
    "python Tools/gen_docs.py --check",
    "python Tools/gen_taxonomy.py --check",
    "python Tools/audit_tool_annotations.py --check",
    "python Tools/audit_engine_toolsets.py --check",
    "python -m mkdocs build --strict",
    "python -m pytest Server/tests -q",
    "python Server/ual_cli/ual_cli.py self-test",
    "python Server/ual_mcp/server.py --self-test",
    "python Server/ual_mcp/engine_bridge.py --self-test",
    "python Tools/bench_index.py --count 10000 --incremental",
)
FORBIDDEN_TOKENS = (
    "unrealbuildtool", "build.bat", "runuat", "unrealeditor-cmd",
    "unrealeditor.exe", "msbuild", "clang++", "g++", "cmake --build",
    "git push", "github-push-action", "${{ secrets.", "secrets:",
    "openai", "anthropic", "vlm", "cloud", "gpu", "curl ", "wget ",
    "docker run",
)
ABS_LOCAL_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")
ISO_DATE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.I,
)


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _on(data: dict) -> dict:
    # PyYAML implements YAML 1.1, where the unquoted key `on` resolves to True.
    triggers = data["on"] if "on" in data else data[True]
    assert isinstance(triggers, dict)
    return triggers


def _steps(data: dict) -> list:
    steps = []
    for job in data["jobs"].values():
        steps.extend(job.get("steps", []))
    return steps


def _run_text(step: dict) -> str:
    run = step.get("run", "")
    return run if isinstance(run, str) else ""


def _all_runs(path: Path) -> str:
    return "\n".join(_run_text(step) for step in _steps(_load(path)))


def test_docs_workflow_path_triggers_cover_python_ci_and_release_inputs() -> None:
    triggers = _on(_load(DOCS))
    for event in ("push", "pull_request"):
        paths = triggers[event]["paths"]
        for required in REQUIRED_DOCS_PATHS + EXISTING_DOCS_PATHS:
            assert required in paths, (event, required)
    assert triggers["push"]["branches"] == ["main"]
    assert "workflow_dispatch" in triggers


def test_docs_workflow_has_only_docs_jobs() -> None:
    jobs = _load(DOCS)["jobs"]
    assert set(jobs) == {"build", "deploy"}
    for name in jobs:
        assert not any(token in name.lower()
                       for token in ("cpp", "compile", "unreal"))


def test_python_ci_preserves_offline_gate_steps() -> None:
    runs = _all_runs(PYTHON_CI)
    for command in OFFLINE_GATE_COMMANDS:
        assert command in runs, command


def test_python_ci_runs_http_dryrun_with_temporary_config_root() -> None:
    steps = [step for step in _steps(_load(PYTHON_CI))
             if "setup_mcp.ps1" in _run_text(step)]
    assert len(steps) == 1
    step = steps[0]
    run = _run_text(step)
    assert step.get("shell") == "pwsh" or "pwsh" in run
    assert "-DryRun" in run
    assert "-Transport Http" in run
    assert "ConfigRoot" in run
    assert "GetTempPath()" in run or "RUNNER_TEMP" in run
    assert "http://localhost:8000/mcp" in run
    # DryRun must not persist client configuration.
    assert "Test-Path" in run
    lowered = run.lower()
    for forbidden in ("bearer", "authorization", "api_key", "api-key",
                      "-httpurl", "https://", "0.0.0.0"):
        assert forbidden not in lowered, forbidden


def test_release_workflow_calls_offline_noredist_verification() -> None:
    runs = _all_runs(RELEASE)
    assert "python Tools/package_plugin.py --out-dir dist" in runs
    assert "Tools/package_plugin.py --verify-only" in runs
    # One call for the archive, one for the extracted release descriptor.
    assert runs.count("--verify-only") >= 2
    lowered = RELEASE.read_text(encoding="utf-8").lower()
    assert "ueassetlensmcp" not in lowered
    assert "modelcontextprotocol" not in lowered


def test_workflows_have_no_cpp_compile_secrets_auto_push_or_external_services() -> None:
    for path in ALL_WORKFLOWS:
        lowered = path.read_text(encoding="utf-8").lower()
        for token in FORBIDDEN_TOKENS:
            assert token not in lowered, (path.name, token)


def test_workflows_have_no_local_paths_timestamps_or_random_ids() -> None:
    for path in ALL_WORKFLOWS:
        text = path.read_text(encoding="utf-8")
        assert not ABS_LOCAL_PATH.search(text), path.name
        assert not ISO_DATE.search(text), path.name
        assert not UUID.search(text), path.name
