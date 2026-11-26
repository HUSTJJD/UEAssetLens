"""Offline contract tests for the fake native payload diff adapter.

These checks use only PyYAML / stdlib and the shared canonical emitters. They
prove the loader-none declarative config, host-only spec status, byte-for-byte
determinism across two builds, bounded paging, rejection of volatile / raw
payloads, and that no diff-layer ruleset is disturbed.
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

from ual_common import artifacts, native_diff as native, settings, toon  # noqa: E402

CONFIG_PATH = ROOT / "Config" / "UAL" / "diff" / "native_payloads.yaml"
_FORBIDDEN_OUTPUT_TOKENS = (
    "vlm", "gpu", "network", "socket", "http", "cloud", "duckdb", "import",
)
_CONFIG_FORBIDDEN_TOKENS = (
    "vlm", "gpu", "network", "socket", "http", "cloud", "duckdb", "import",
    "impl", "callable", "handler", "!!python", "module", "eval(", "exec(",
)
_SOURCE_FORBIDDEN = (
    "importlib", "__import__", "duckdb", "GetAsset(", "UObject",
    "FHttpModule", "urllib", "requests.", "write_artifact(",
    "TODO" + "(human)",
)


def _assert_clean_output(text: str) -> None:
    assert not re.search(r"[A-Za-z]:[\\/]", text), text[:160]
    assert not re.search(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*", text), text[:160]
    assert not re.search(r"\b[0-9a-fA-F]{32}\b", text), text[:160]
    body = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    lowered = body.lower()
    for token in _FORBIDDEN_OUTPUT_TOKENS:
        assert token not in lowered, (token, text[:160])


def test_config_is_declarative_loader_none_and_host_only_spec(tmp_path: Path) -> None:
    data = native.load_native_payload_config()
    assert data["contract"] == "native_payload_adapter"
    assert data["version"] == 1
    assert data["status"] == "offline_source_level_contract"
    assert data["default_loader"] == "none"
    assert sorted(data["layers"]) == ["L1", "L3", "L4"]
    for spec in data["layers"].values():
        assert spec["loader"] == "none"
        assert isinstance(spec["fake_payloads"], list) and spec["fake_payloads"]
    assert data["host_only"]["status"] == "spec_not_landed"
    assert data["host_only"]["real_ue_native_diff"] == "spec_not_landed"
    assert data["host_only"]["requires_injected_host_executor"] is True
    for flag in ("asset_load", "binary_payload", "raw_binary_export", "index_write", "host_call"):
        assert data["boundaries"][flag] is False

    text = CONFIG_PATH.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    assert set(doc) == {"native_payload_adapter"}
    assert "rules" not in doc and "id" not in doc
    lowered = text.lower()
    for token in _CONFIG_FORBIDDEN_TOKENS:
        assert token not in lowered, token
    assert not re.search(r"[A-Za-z]:[\\/]", text)
    assert not re.search(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*", text)
    assert not re.search(r"\b[0-9a-fA-F]{32}\b", text)

    # The extra declarative file must not join the five-layer diff ruleset.
    resolved = settings.load_namespace("diff", project_root=tmp_path / "isolated_project")
    assert [str(rule["id"]) for rule in resolved.active_rules()] == ["L0", "L1", "L2", "L3", "L4"]


def test_tampered_loader_or_executable_key_is_rejected(tmp_path: Path) -> None:
    doc = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    doc["native_payload_adapter"]["layers"]["L1"]["loader"] = "host"
    artifacts.write_artifact(tmp_path, native.CONFIG_RELATIVE, doc)
    with pytest.raises(ValueError):
        native.load_native_payload_config(config_root=tmp_path)

    doc = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    doc["native_payload_adapter"]["impl"] = "os.system"
    artifacts.write_artifact(tmp_path, native.CONFIG_RELATIVE, doc)
    with pytest.raises(ValueError):
        native.load_native_payload_config(config_root=tmp_path)

    doc = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    doc["native_payload_adapter"]["host_only"]["real_ue_native_diff"] = "landed"
    artifacts.write_artifact(tmp_path, native.CONFIG_RELATIVE, doc)
    with pytest.raises(ValueError):
        native.load_native_payload_config(config_root=tmp_path)


def test_native_payload_canonicalization_is_byte_for_byte_deterministic() -> None:
    first = native.build_native_diff("/Game/Demo/BP_Player", "/Game/Demo/BP_Enemy")
    second = native.build_native_diff("/Game/Demo/BP_Player", "/Game/Demo/BP_Enemy")
    assert first.diff_id == second.diff_id
    assert first.texts == second.texts
    assert first.payloads == second.payloads
    assert sorted(first.texts) == [
        f".ual/native_diffs/{first.diff_id}/L1.yaml",
        f".ual/native_diffs/{first.diff_id}/L3.yaml",
        f".ual/native_diffs/{first.diff_id}/L4.yaml",
        f".ual/native_diffs/{first.diff_id}/manifest.yaml",
    ]

    report = native.determinism_report(first, second)
    assert report["deterministic"] is True
    assert report["artifact_count"] == 4
    assert report["mismatches"] == []
    assert native.render_determinism(report).startswith(toon.HEADER)

    for relative, text in first.texts.items():
        assert text == artifacts.canonical_yaml(yaml.safe_load(text))
        if not relative.endswith("manifest.yaml"):
            assert artifacts.sha256_text(text) == next(
                item["canonical_sha256"] for item in first.report["layer_stats"]
                if item["artifact"] == relative
            )
        _assert_clean_output(text)
    assert first.manifest["canonical_sha256"] == first.report["canonical_sha256"]

    same_side = native.build_native_diff("/Game/Demo/BP_Player", "/Game/Demo/BP_Player")
    assert same_side.diff_id != first.diff_id
    assert native.determinism_report(first, same_side)["deterministic"] is False

    meta = native.artifact_metadata(first)
    assert re.fullmatch(r"[0-9a-f]{64}", meta["canonical_sha256"])
    assert meta["artifact"].startswith(".ual/native_diffs/")
    assert meta["default_loader"] == "none"
    assert meta["real_ue_native_diff"] == "spec_not_landed"
    assert native.render_artifact(first) == artifacts.canonical_yaml(meta)
    for output in (
        native.render_summary(first),
        native.render_artifact(first),
        native.render_page(first, "L1", limit=5),
    ):
        _assert_clean_output(output)


def test_paging_and_summary_are_bounded() -> None:
    build = native.build_native_diff("/Game/Demo/BP_Player", "/Game/Demo/BP_Enemy")
    summary = native.render_summary(build, limit=2)
    parsed = toon.parse_toon(summary)
    assert summary.startswith(toon.HEADER)
    assert parsed["summary"]["returned"] == "2"
    assert parsed["summary"]["truncated"] == "true"
    assert len(parsed["tables"]["native_layers"]) == 2
    assert parsed["summary"]["default_loader"] == "none"

    page = yaml.safe_load(native.render_page(build, "L3", offset=0, limit=5))
    assert page["layer"] == "L3"
    assert page["returned"] <= 5
    assert page["limit"] == 5
    assert page["total"] >= page["returned"]
    assert isinstance(page["lines"], list)
    big = yaml.safe_load(native.render_page(build, "L1", offset=0, limit=10000))
    assert big["limit"] == artifacts.MAX_PAGE_LINES
    with pytest.raises(ValueError):
        native.render_page(build, "L2")
    with pytest.raises(ValueError):
        native.build_native_diff("/Game/Demo/BP_Player", "/Game/Demo/BP_Enemy", layers="L0")


def test_canonicalization_rejects_volatile_and_raw_payloads() -> None:
    with pytest.raises(ValueError):
        native.canonicalize_payload("L1", {"asset_path": "C:" + "/temp/index.ddc"})
    with pytest.raises(ValueError):
        native.canonicalize_payload("L1", {"asset_path": "/Game/A", "blob": b"\x00\x01"})
    with pytest.raises(ValueError):
        native.canonicalize_payload("L3", {"asset_path": "/Game/A", "impl": "os.system"})
    with pytest.raises(ValueError):
        native.canonicalize_payload(
            "L3", {"asset_path": "/Game/A", "timestamp": "2026-11-27" + "T10:00:00Z"},
        )
    with pytest.raises(ValueError):
        native.canonicalize_payload("L2", {"asset_path": "/Game/A"})

    rows_a = [
        {"asset_path": "/Game/B", "tag": "b"},
        {"asset_path": "/Game/A", "tag": "a"},
    ]
    rows_b = list(reversed(rows_a))
    assert native.canonicalize_payload("L1", rows_a) == native.canonicalize_payload("L1", rows_b)
    assert [row["asset_path"] for row in native.canonicalize_payload("L1", rows_a)] == [
        "/Game/A", "/Game/B",
    ]


def test_native_diff_source_uses_canonical_emitters_only() -> None:
    source = (ROOT / "Server" / "ual_common" / "native_diff.py").read_text(encoding="utf-8")
    for token in _SOURCE_FORBIDDEN:
        assert token not in source, token
    assert "artifacts.canonical_yaml" in source
    assert "toon.render_toon" in source
    assert "yaml.safe_dump" not in source
    assert "yaml.dump(" not in source
