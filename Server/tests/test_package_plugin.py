"""Offline NoRedist contracts for the release packaging tool.

Builds small temporary archives and descriptors. No network, no UE SDK, no
C++ compile and no real release job are involved.
"""
from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "Tools" / "package_plugin.py"
DEV_DESCRIPTOR = REPO / "UEAssetLens.uplugin"


def _load_module():
    spec = importlib.util.spec_from_file_location("ual_package_plugin", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


package_plugin = _load_module()


def _descriptor(*, dev_module: bool = False,
                official_plugin: bool = False) -> str:
    modules = [
        {"Name": "UEAssetLens", "Type": "Runtime", "LoadingPhase": "Default"},
        {"Name": "UEAssetLensEditor", "Type": "Editor",
         "LoadingPhase": "PostEngineInit"},
    ]
    plugins = [{"Name": "UEYaml", "Enabled": True}]
    if dev_module:
        modules.append({"Name": "UEAssetLensMCP", "Type": "Editor",
                        "LoadingPhase": "PostEngineInit"})
    if official_plugin:
        plugins.append({"Name": "ModelContextProtocol", "Enabled": True,
                        "Optional": True})
    return json.dumps({
        "FileVersion": 3,
        "Version": 3,
        "VersionName": "3.0.0",
        "FriendlyName": "UEAssetLens",
        "Modules": modules,
        "Plugins": plugins,
    }, indent="\t")


GOOD_ARCHIVE_ENTRIES = {
    "UEAssetLens/UEAssetLens.uplugin": _descriptor(),
    "UEAssetLens/Server/ual_mcp/server.py": "# offline python server\n",
    "LICENSE": "licence\n",
}


def _write_zip(path: Path, entries: dict) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            payload = (content.encode("utf-8") if isinstance(content, str)
                       else content)
            zf.writestr(name, payload)
    return path


def _reject(path: Path, needle: str) -> str:
    with pytest.raises(SystemExit) as exc:
        package_plugin.verify_only(path)
    message = str(exc.value)
    assert "NoRedist" in message
    assert needle in message
    # No local absolute paths leak into the check output.
    assert str(path.parent) not in message
    return message


def _cli(*args: object) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(arg) for arg in args)],
        cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )


def test_archive_with_dev_only_module_is_rejected(tmp_path: Path) -> None:
    entries = dict(GOOD_ARCHIVE_ENTRIES)
    entries["UEAssetLens/Source/UEAssetLensMCP/Private/UALEngineMcpModule.cpp"] = (
        "// development only\n")
    archive = _write_zip(tmp_path / "dev-module.zip", entries)
    _reject(archive, "UEAssetLensMCP")


def test_archive_with_official_plugin_artifact_is_rejected(tmp_path: Path) -> None:
    entries = dict(GOOD_ARCHIVE_ENTRIES)
    entries["UEAssetLens/Plugins/ModelContextProtocol/ModelContextProtocol.uplugin"] = (
        "{}")
    archive = _write_zip(tmp_path / "official-plugin.zip", entries)
    _reject(archive, "ModelContextProtocol")


def test_descriptor_enabling_optional_official_plugin_is_rejected(
        tmp_path: Path) -> None:
    entries = dict(GOOD_ARCHIVE_ENTRIES)
    entries["UEAssetLens/UEAssetLens.uplugin"] = _descriptor(official_plugin=True)
    archive = _write_zip(tmp_path / "enabled-plugin.zip", entries)
    _reject(archive, "ModelContextProtocol")


def test_descriptor_with_dev_only_module_is_rejected(tmp_path: Path) -> None:
    entries = dict(GOOD_ARCHIVE_ENTRIES)
    entries["UEAssetLens/UEAssetLens.uplugin"] = _descriptor(dev_module=True)
    archive = _write_zip(tmp_path / "dev-module-descriptor.zip", entries)
    _reject(archive, "UEAssetLensMCP")


def test_standalone_descriptor_with_official_plugin_is_rejected(
        tmp_path: Path) -> None:
    descriptor = tmp_path / "UEAssetLens.uplugin"
    descriptor.write_text(_descriptor(official_plugin=True), encoding="utf-8")
    _reject(descriptor, "ModelContextProtocol")


def test_clean_archive_and_standalone_descriptor_pass(tmp_path: Path) -> None:
    archive = _write_zip(tmp_path / "clean.zip", GOOD_ARCHIVE_ENTRIES)
    assert package_plugin.verify_only(archive) == len(GOOD_ARCHIVE_ENTRIES)
    descriptor = tmp_path / "UEAssetLens.uplugin"
    descriptor.write_text(_descriptor(), encoding="utf-8")
    assert package_plugin.verify_only(descriptor) == 1


def test_verify_only_is_offline(tmp_path: Path,
                                monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*args, **kwargs):
        raise AssertionError("network access must not be used by --verify-only")

    monkeypatch.setattr(socket, "socket", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    archive = _write_zip(tmp_path / "offline.zip", GOOD_ARCHIVE_ENTRIES)
    assert package_plugin.verify_only(archive) == len(GOOD_ARCHIVE_ENTRIES)


def test_collect_excludes_dev_only_module_and_official_artifacts() -> None:
    names = [dest.as_posix() for _, dest in package_plugin.collect()]
    assert "UEAssetLens/UEAssetLens.uplugin" in names
    assert not any("UEAssetLensMCP" in name for name in names)
    assert not any("modelcontextprotocol" in name.lower() for name in names)


def test_release_descriptor_is_noredist_clean_and_dev_file_unchanged() -> None:
    before = DEV_DESCRIPTOR.read_bytes()
    dev = json.loads(before.decode("utf-8-sig"))
    assert any(m["Name"] == "UEAssetLensMCP" for m in dev["Modules"])
    assert any(p["Name"] == "ModelContextProtocol" for p in dev["Plugins"])
    payload = package_plugin.release_descriptor_bytes(DEV_DESCRIPTOR)
    release = json.loads(payload.decode("utf-8"))
    assert not any(m["Name"] == "UEAssetLensMCP" for m in release["Modules"])
    assert not any(p["Name"] == "ModelContextProtocol"
                   for p in release["Plugins"])
    assert any(p["Name"] == "UEYaml" for p in release["Plugins"])
    assert package_plugin.descriptor_violations(payload.decode("utf-8"),
                                                "release") == []
    assert DEV_DESCRIPTOR.read_bytes() == before


def test_verify_only_cli_rejects_bad_archive(tmp_path: Path) -> None:
    entries = dict(GOOD_ARCHIVE_ENTRIES)
    entries["UEAssetLens/Source/UEAssetLensMCP/Private/UALEngineMcpModule.cpp"] = (
        "// development only\n")
    archive = _write_zip(tmp_path / "cli-bad.zip", entries)
    result = _cli("--verify-only", archive)
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "NoRedist" in combined
    assert str(tmp_path) not in combined


def test_verify_only_cli_accepts_clean_archive(tmp_path: Path) -> None:
    archive = _write_zip(tmp_path / "cli-clean.zip", GOOD_ARCHIVE_ENTRIES)
    result = _cli("--verify-only", archive)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("OK ")
    assert "sha256=" in result.stdout
    assert str(tmp_path) not in result.stdout


def test_verify_only_cli_checks_standalone_descriptor(tmp_path: Path) -> None:
    clean = tmp_path / "release-descriptor.uplugin"
    clean.write_text(_descriptor(), encoding="utf-8")
    good = _cli("--verify-only", clean)
    assert good.returncode == 0, good.stderr
    assert "OK " in good.stdout
    dirty = tmp_path / "dev-descriptor.uplugin"
    dirty.write_text(_descriptor(official_plugin=True), encoding="utf-8")
    bad = _cli("--verify-only", dirty)
    assert bad.returncode != 0
    assert "NoRedist" in bad.stdout + bad.stderr
    assert str(tmp_path) not in bad.stdout + bad.stderr


def test_built_archive_is_noredist_clean(tmp_path: Path) -> None:
    result = _cli("--out-dir", tmp_path)
    assert result.returncode == 0, result.stderr
    archives = sorted(tmp_path.glob("*.zip"))
    assert len(archives) == 1
    with zipfile.ZipFile(archives[0]) as zf:
        names = zf.namelist()
        descriptor = json.loads(
            zf.read("UEAssetLens/UEAssetLens.uplugin").decode("utf-8-sig"))
    assert "UEAssetLens/UEAssetLens.uplugin" in names
    assert not any("UEAssetLensMCP" in name for name in names)
    assert not any("modelcontextprotocol" in name.lower() for name in names)
    assert not any(m["Name"] == "UEAssetLensMCP" for m in descriptor["Modules"])
    assert not any(p["Name"] == "ModelContextProtocol"
                   for p in descriptor["Plugins"])
    assert package_plugin.verify_only(archives[0]) == len(names)
