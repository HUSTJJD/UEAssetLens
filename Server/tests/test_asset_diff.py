"""Contract tests for offline five-layer diff artifacts and bounded reports."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from ual_common import artifacts, asset_diff, db, settings
from ual_common.toon import parse_toon

LEFT = "/Game/Diff/Left"
RIGHT = "/Game/Diff/Right"
GUID = "123e4567-e89b-12d3-a456-426614174000"
STAMP = "2026-11-26T12:34:56Z"
WINDOWS_PATH = r"D:\work\ual\fixture.uasset"
UNIX_PATH = "/Users/fixture/ual/cache.yaml"
RANDOM_ID = "run-a1b2c3d4e5f6"
C1_CONTROL = "\u008a"
SENTINEL = "FULL_PAYLOAD_SENTINEL_NOT_IN_BOUNDED_REPORTS"
TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*")
WINDOWS_PATH_RE = re.compile(r"(?<!\w)[A-Za-z]:[\\/][A-Za-z0-9_.-]")


def _noise_payload(label: str) -> dict:
    return {
        "updated_at": STAMP,
        "revision": "session-revision-noise",
        "session_id": RANDOM_ID,
        "random_id": RANDOM_ID,
        "ActorMetaData": "base64-blob",
        "FiBData": "binary-blob" + C1_CONTROL,
        "guid": GUID,
        "stamp": STAMP,
        "local_path": WINDOWS_PATH,
        "unix_path": UNIX_PATH,
        "run_marker": RANDOM_ID,
        "control": "x" + C1_CONTROL + "y",
        "sentinel": SENTINEL + "-" + label,
    }


def _yaml_cache(label: str) -> str:
    lines = [
        {"index": index, "label": label, "value": f"stable-{index:03d}"}
        for index in range(120)
    ]
    return artifacts.canonical_yaml({"noise": _noise_payload(label), "lines": lines})


def _fixture_assets() -> dict[str, dict]:
    return {
        LEFT: {
            "asset_path": LEFT,
            "logical_id": "Blueprint:DiffLeft",
            "asset_class": "Blueprint",
            "parent_class": "Actor",
            "taxonomy": ["Asset", "Gameplay", "Blueprint"],
            "tokens": "diff left stable tokens",
            "content_hash": "sha1:left",
            "interface_spec": {"exposed_params": "Color", **_noise_payload("interface-left")},
            "domain_spec": {"feature.role": "left", **_noise_payload("domain-left")},
            "yaml_cache": _yaml_cache("left"),
        },
        RIGHT: {
            "asset_path": RIGHT,
            "logical_id": "Blueprint:DiffRight",
            "asset_class": "Blueprint",
            "parent_class": "Actor",
            "taxonomy": ["Asset", "Gameplay", "Blueprint"],
            "tokens": "diff right stable tokens",
            "content_hash": "sha1:right",
            "interface_spec": {"exposed_params": "Speed", **_noise_payload("interface-right")},
            "domain_spec": {"feature.role": "right", **_noise_payload("domain-right")},
            "yaml_cache": _yaml_cache("right"),
        },
    }


def _fixture_edges() -> dict[str, list[dict]]:
    return {
        LEFT: [
            {"target_path": "/Game/Diff/Mesh", "edge_type": "DEPENDS_ON", "weight": 1.0},
            {"target_path": "/Game/Diff/VFX", "edge_type": "CO_OCCURS", "weight": 0.5},
        ],
        RIGHT: [
            {"target_path": "/Game/Diff/Mesh", "edge_type": "DEPENDS_ON", "weight": 1.0},
            {"target_path": "/Game/Diff/Sound", "edge_type": "CO_OCCURS", "weight": 0.75},
        ],
    }


def _install_revision(monkeypatch, *, same_content_hash: bool = False):
    assets = _fixture_assets()
    edges = _fixture_edges()
    if same_content_hash:
        assets[LEFT]["content_hash"] = "sha1:identical"
        assets[RIGHT]["content_hash"] = "sha1:identical"

    monkeypatch.setattr(asset_diff.revision, "index_revision", lambda con: "fixture-index-1")
    monkeypatch.setattr(asset_diff.revision, "engine_version", lambda con: "UE6")
    monkeypatch.setattr(
        asset_diff.revision,
        "asset_snapshot",
        lambda con, asset_path: dict(assets[str(asset_path)]),
    )
    monkeypatch.setattr(
        asset_diff.revision,
        "edge_rows",
        lambda con, asset_path: [dict(row) for row in edges[str(asset_path)]],
    )
    return object()


def _layer_ids(project_root: Path) -> list[str]:
    resolved = settings.load_namespace("diff", project_root=project_root)
    return [str(rule["id"]) for rule in resolved.active_rules()]


def _layer_for_source(project_root: Path, source: str) -> str:
    resolved = settings.load_namespace("diff", project_root=project_root)
    for rule in resolved.active_rules():
        if str(rule.get("source", "")) == source:
            return str(rule["id"])
    raise AssertionError(f"no configured layer for {source}")


def _artifact_texts(root: Path, diff_id: str, layer_ids: list[str]) -> dict[str, str]:
    base = root / "diffs" / diff_id
    paths = [base / "manifest.yaml"] + [base / f"{layer_id}.yaml" for layer_id in layer_ids]
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in paths
    }


def _prepare(
    tmp_path: Path,
    monkeypatch,
    *,
    root_name: str = "ual",
    same_content_hash: bool = False,
) -> dict:
    con = _install_revision(monkeypatch, same_content_hash=same_content_hash)
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    root = tmp_path / root_name
    summary = asset_diff.diff_assets(
        con, LEFT, RIGHT, project_root=project_root, root=root
    )
    parsed = parse_toon(summary)
    diff_id = parsed["summary"]["diff_id"]
    manifest_path = root / "diffs" / diff_id / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    layer_ids = _layer_ids(project_root)
    return {
        "con": con,
        "project_root": project_root,
        "root": root,
        "summary": summary,
        "parsed": parsed,
        "diff_id": diff_id,
        "manifest": manifest,
        "layer_ids": layer_ids,
        "texts": _artifact_texts(root, diff_id, layer_ids),
    }


def _write_index(tmp_path: Path):
    con = db.open_write(tmp_path / "index.duckdb")
    assets = _fixture_assets()
    edges = _fixture_edges()
    records = []
    for asset_path, asset in assets.items():
        record = dict(asset)
        record["edges"] = [dict(row) for row in edges[asset_path]]
        records.append(record)
    db.ingest_records(con, records, rebuild=False)
    return con


def test_five_canonical_artifacts_and_bounded_summary(tmp_path, monkeypatch):
    prepared = _prepare(tmp_path, monkeypatch)
    root = prepared["root"]
    diff_id = prepared["diff_id"]
    layer_ids = prepared["layer_ids"]
    summary = prepared["summary"]
    parsed = prepared["parsed"]

    assert len(layer_ids) == 5
    assert (root / "diffs" / diff_id / "manifest.yaml").is_file()
    for layer_id in layer_ids:
        assert (root / "diffs" / diff_id / f"{layer_id}.yaml").is_file()
    assert len(prepared["texts"]) == 6

    assert parsed["header"] == "# UEAssetLens TOON v1 (Zero-VLM)"
    assert {"diff_id", "artifact", "canonical_sha256", "next_cursor"} <= set(parsed["summary"])
    assert parsed["summary"]["diff_id"] == diff_id
    assert parsed["summary"]["artifact"].startswith(".ual/diffs/")
    assert re.fullmatch(r"[0-9a-f]{64}", parsed["summary"]["canonical_sha256"])
    assert len(parsed["tables"]["layer_stats"]) == len(layer_ids)
    assert all(
        row["artifact"].startswith(".ual/diffs/") for row in parsed["tables"]["layer_stats"]
    )
    assert len(summary) < 5000
    assert SENTINEL not in summary
    assert str(tmp_path) not in summary and str(root) not in summary
    assert "updated_at" not in summary
    assert not TIMESTAMP_RE.search(summary)
    assert not WINDOWS_PATH_RE.search(summary)

    manifest = prepared["manifest"]
    assert manifest["diff_id"] == diff_id
    assert manifest["artifact"].startswith(".ual/diffs/")
    assert manifest["layers"] == ",".join(layer_ids)
    assert set(manifest["layer_artifacts"]) == set(layer_ids)
    assert manifest["canonical_sha256"] == parsed["summary"]["canonical_sha256"]
    assert str(root) not in "\n".join(prepared["texts"].values())


def test_bounded_page_and_canonicalization_strip_noise(tmp_path, monkeypatch):
    prepared = _prepare(tmp_path, monkeypatch)
    text_layer = _layer_for_source(prepared["project_root"], "index.yaml_cache")
    page_text = asset_diff.read_diff_layer(
        prepared["con"],
        prepared["diff_id"],
        text_layer,
        offset=0,
        limit=10,
        project_root=prepared["project_root"],
        root=prepared["root"],
    )
    page = yaml.safe_load(page_text)
    assert set(page) == {
        "layer", "offset", "limit", "returned", "total", "truncated",
        "next_cursor", "lines",
    }
    assert page["layer"] == text_layer
    assert page["offset"] == 0
    assert page["limit"] == 10
    assert page["returned"] <= 10
    assert page["truncated"] is True
    assert page["next_cursor"]
    assert len(page["lines"]) == page["returned"]
    assert SENTINEL not in page_text or page["returned"] < page["total"]

    combined = "\n".join(prepared["texts"].values())
    assert "<guid>" in combined
    assert "<timestamp>" in combined
    assert "<local-path>" in combined
    assert "<random-id>" in combined
    assert GUID not in combined
    assert STAMP not in combined
    assert WINDOWS_PATH not in combined
    assert UNIX_PATH not in combined
    assert RANDOM_ID not in combined
    assert "updated_at" not in combined
    assert "ActorMetaData" not in combined
    assert "FiBData" not in combined
    assert "session_id" not in combined
    assert "random_id" not in combined
    assert not re.search(r"(?im)^\s*(?:revision|timestamp)\s*:", combined)
    assert not TIMESTAMP_RE.search(combined)
    assert not WINDOWS_PATH_RE.search(combined)
    assert "/Users/" not in combined
    assert not any(0x80 <= ord(char) <= 0x9F for char in combined)
    assert str(tmp_path) not in combined and str(prepared["root"]) not in combined
    assert "TODO(human)" not in combined


def test_diff_artifacts_are_byte_identical_and_verify_determinism(tmp_path, monkeypatch):
    first = _prepare(tmp_path, monkeypatch, root_name="first")
    second_root = tmp_path / "second"
    second_summary = asset_diff.diff_assets(
        first["con"], LEFT, RIGHT,
        project_root=first["project_root"], root=second_root,
    )
    second_parsed = parse_toon(second_summary)
    assert second_parsed["summary"]["diff_id"] == first["diff_id"]
    assert second_parsed["summary"]["canonical_sha256"] == first["parsed"]["summary"]["canonical_sha256"]

    first_base = first["root"] / "diffs" / first["diff_id"]
    second_base = second_root / "diffs" / first["diff_id"]
    first_files = sorted(path.name for path in first_base.glob("*.yaml"))
    assert first_files == sorted(path.name for path in second_base.glob("*.yaml"))
    assert len(first_files) == len(first["layer_ids"]) + 1
    for name in first_files:
        assert (first_base / name).read_bytes() == (second_base / name).read_bytes()

    verification = asset_diff.verify_diff_determinism(
        first["con"], first["diff_id"],
        project_root=first["project_root"], root=first["root"],
    )
    parsed = parse_toon(verification)
    assert parsed["header"] == "# UEAssetLens TOON v1 (Zero-VLM)"
    assert parsed["summary"]["deterministic"] == "true"
    assert parsed["summary"]["artifact_count"] == str(len(first_files))
    assert parsed["summary"]["canonical_sha256"] == first["parsed"]["summary"]["canonical_sha256"]
    assert parsed["summary"]["mismatch_count"] == "0"
    assert parsed["tables"]["mismatches"] == []
    assert SENTINEL not in verification
    assert str(tmp_path) not in verification and str(first["root"]) not in verification
    assert "updated_at" not in verification
    assert not TIMESTAMP_RE.search(verification)


def test_content_hash_is_not_used_for_dedup(tmp_path, monkeypatch):
    prepared = _prepare(
        tmp_path, monkeypatch, root_name="same-hash", same_content_hash=True
    )
    metadata_layer = _layer_for_source(prepared["project_root"], "index.metadata")
    rows = prepared["parsed"]["tables"]["layer_stats"]
    metadata_row = next(row for row in rows if row["layer"] == metadata_layer)
    assert int(metadata_row["changed_count"]) > 0
    assert "content_hash" not in metadata_row["changed_fields"]
    assert all("dedup" not in text for text in prepared["texts"].values())


def test_unknown_layers_and_ids_are_rejected(tmp_path, monkeypatch):
    prepared = _prepare(tmp_path, monkeypatch)
    first_layer = prepared["layer_ids"][0]
    with pytest.raises(ValueError):
        asset_diff.diff_assets(
            prepared["con"], LEFT, RIGHT, layers="L9",
            project_root=prepared["project_root"], root=tmp_path / "invalid-layers",
        )
    with pytest.raises(ValueError):
        asset_diff.read_diff_layer(
            prepared["con"], prepared["diff_id"], "L9",
            project_root=prepared["project_root"], root=prepared["root"],
        )
    with pytest.raises(ValueError):
        asset_diff.read_diff_layer(
            prepared["con"], "bad/id", first_layer,
            project_root=prepared["project_root"], root=prepared["root"],
        )


def test_renderers_are_pure_bounded_toon():
    summary_report = {
        "diff_id": "0" * 64,
        "artifact": ".ual/diffs/" + "0" * 64 + "/manifest.yaml",
        "canonical_sha256": "1" * 64,
        "next_cursor": "",
        "layer_stats": [{
            "layer": "configured",
            "status": "ok",
            "changed_count": 0,
            "changed_fields": [],
            "canonical_sha256": "2" * 64,
            "line_count": 1,
            "artifact": ".ual/diffs/" + "0" * 64 + "/configured.yaml",
            "next_cursor": "",
            "description": "deterministic index projection",
            "source": "index.metadata",
        }],
    }
    summary = asset_diff.render_summary(summary_report)
    assert summary == asset_diff.render_summary(summary_report)
    assert parse_toon(summary)["header"] == "# UEAssetLens TOON v1 (Zero-VLM)"
    assert parse_toon(summary)["summary"]["diff_id"] == "0" * 64
    assert len(parse_toon(summary)["tables"]["layer_stats"]) == 1

    determinism_report = {
        "diff_id": "0" * 64,
        "deterministic": True,
        "artifact_count": 6,
        "canonical_sha256": "1" * 64,
        "mismatches": [],
    }
    determinism = asset_diff.render_determinism(determinism_report)
    assert determinism == asset_diff.render_determinism(determinism_report)
    parsed = parse_toon(determinism)
    assert parsed["header"] == "# UEAssetLens TOON v1 (Zero-VLM)"
    assert parsed["summary"]["deterministic"] == "true"
    assert parsed["summary"]["artifact_count"] == "6"
    assert parsed["tables"]["mismatches"] == []


def test_real_index_revision_integration_is_deterministic(tmp_path):
    con = _write_index(tmp_path)
    try:
        project_root = tmp_path / "integration-project"
        project_root.mkdir(parents=True, exist_ok=True)
        root = tmp_path / "integration-ual"
        summary = asset_diff.diff_assets(
            con, LEFT, RIGHT, project_root=project_root, root=root
        )
        parsed = parse_toon(summary)
        diff_id = parsed["summary"]["diff_id"]
        layer_ids = _layer_ids(project_root)
        texts = _artifact_texts(root, diff_id, layer_ids)
        combined = "\n".join(texts.values())

        assert len(texts) == 6
        assert parsed["summary"]["artifact"].startswith(".ual/diffs/")
        assert SENTINEL not in summary
        assert str(tmp_path) not in summary and str(root) not in summary
        assert "updated_at" not in combined
        assert "ActorMetaData" not in combined
        assert "FiBData" not in combined
        assert "session_id" not in combined
        assert "random_id" not in combined
        assert GUID not in combined
        assert STAMP not in combined
        assert WINDOWS_PATH not in combined
        assert UNIX_PATH not in combined
        assert RANDOM_ID not in combined
        assert "<guid>" in combined
        assert "<timestamp>" in combined
        assert "<local-path>" in combined
        assert "<random-id>" in combined
        assert not TIMESTAMP_RE.search(combined)
        assert not WINDOWS_PATH_RE.search(combined)
        assert not any(0x80 <= ord(char) <= 0x9F for char in combined)
        assert str(tmp_path) not in combined and str(root) not in combined

        text_layer = _layer_for_source(project_root, "index.yaml_cache")
        page = yaml.safe_load(asset_diff.read_diff_layer(
            con, diff_id, text_layer, offset=0, limit=10,
            project_root=project_root, root=root,
        ))
        assert page["returned"] <= 10
        assert page["truncated"] is True

        verification = parse_toon(asset_diff.verify_diff_determinism(
            con, diff_id, project_root=project_root, root=root,
        ))
        assert verification["summary"]["deterministic"] == "true"
        assert verification["summary"]["artifact_count"] == "6"
        assert verification["summary"]["mismatch_count"] == "0"
        assert verification["tables"]["mismatches"] == []
        with pytest.raises(ValueError):
            asset_diff.diff_assets(
                con, LEFT, RIGHT, layers="L9",
                project_root=project_root, root=tmp_path / "unknown-layers",
            )
    finally:
        con.close()
