"""Index revision determinism and bounded single-asset diff inputs."""
from __future__ import annotations

from pathlib import Path

from ual_common import artifacts, db, fields, revision


def _record(
    asset_path: str,
    asset_class: str,
    *,
    content_hash: str = "sha1:a",
    edges=None,
    yaml_cache: str = "identity: {}\n",
) -> dict:
    return {
        "asset_path": asset_path,
        "logical_id": f"{asset_class}:{asset_path.rsplit('/', 1)[-1]}",
        "asset_class": asset_class,
        "parent_class": "UObject",
        "taxonomy": ["Asset", "Misc", asset_class],
        "tokens": "revision asset",
        "content_hash": content_hash,
        "duration": 1.0,
        "bounds_size": [1.0, 2.0, 3.0],
        "fingerprint_vec": [0.01] * 16,
        "interface_spec": {"skeleton": "/Game/SK_Test"},
        "domain_spec": {"feature.role": "prop", "feature.quality_tier": "3"},
        "yaml_cache": yaml_cache,
        "edges": edges or [],
    }


def _index(tmp_path: Path):
    con = db.open_write(tmp_path / "index.duckdb")
    left = "/Game/Props/SM_Chair"
    right = "/Game/Props/SM_Table"
    records = [
        _record(left, "StaticMesh", edges=[{
            "target_path": right, "edge_type": "DEPENDS_ON", "weight": 1.0,
        }]),
        _record(right, "StaticMesh", edges=[{
            "target_path": left, "edge_type": "CO_OCCURS", "weight": 0.5,
        }]),
    ]
    db.ingest_records(con, records, rebuild=False)
    fields.materialize(con, project_root=tmp_path)
    return con, left, right


def test_revision_is_stable_and_only_tracks_index_changes(tmp_path: Path) -> None:
    con, left, _right = _index(tmp_path)
    initial = revision.index_revision(con)
    assert len(initial) == 64
    fields.materialize(con, project_root=tmp_path)
    assert revision.index_revision(con) == initial

    con.execute(
        "UPDATE assets SET updated_at = TIMESTAMP '2020-01-01 00:00:00' WHERE asset_path = ?",
        [left],
    )
    assert revision.index_revision(con) == initial

    con.execute("UPDATE assets SET content_hash = ? WHERE asset_path = ?", ["sha1:b", left])
    changed_hash = revision.index_revision(con)
    assert changed_hash != initial

    con.execute(
        "UPDATE extension_payload SET value_text = ? WHERE asset_path = ? AND namespace = ? AND field_name = ?",
        ["4", left, "field", "quality_tier"],
    )
    assert revision.index_revision(con) != changed_hash

    con.execute(
        "INSERT INTO asset_edges (source_path, target_path, edge_type, weight) VALUES (?, ?, ?, ?)",
        [left, "/Game/External/Thing", "DEPENDS_ON", 1.0],
    )
    changed_edges = revision.index_revision(con)
    assert len(changed_edges) == 64
    assert "updated_at" not in changed_edges
    con.close()


def test_engine_version_snapshot_edges_and_removal(tmp_path: Path) -> None:
    con, left, right = _index(tmp_path)
    assert revision.engine_version(con) == "UE6"
    con.execute("UPDATE schema_meta SET value = ? WHERE key = ?", ["UE5", "engine_version"])
    assert revision.engine_version(con) == "UE5"

    noisy_yaml = (
        "identity:\n"
        "  asset_path: /Game/Props/SM_Chair\n"
        "updated_at: 2026-11-26T12:00:00Z\n"
        "local: C:\\Temp\\asset.uasset\n"
        "guid: 123e4567-e89b-12d3-a456-426614174000\n"
    )
    con.execute("UPDATE assets SET yaml_cache = ? WHERE asset_path = ?", [noisy_yaml, left])
    snapshot = revision.asset_snapshot(con, left)
    assert snapshot["asset_path"] == left
    assert "updated_at" not in snapshot
    assert snapshot["yaml_cache"] == artifacts.canonicalize_text(noisy_yaml)
    assert "C:\\Temp" not in snapshot["yaml_cache"]
    assert "2026-11-26" not in snapshot["yaml_cache"]
    assert "123e4567" not in snapshot["yaml_cache"]
    assert "updated_at" not in snapshot["yaml_cache"]
    assert revision.asset_snapshot(con, "/Game/Missing") == {}

    edges = revision.edge_rows(con, left)
    assert edges
    assert [row["direction"] for row in edges] == sorted(row["direction"] for row in edges)
    assert all(left in (row["source_path"], row["target_path"]) for row in edges)
    assert all("updated_at" not in row for row in edges)

    removed = revision.remove_assets(con, [
        right, right, "/Engine/NotGame", "/Game/A' OR 1=1",
    ])
    assert removed == 1
    assert con.execute(
        "SELECT COUNT(*) FROM assets WHERE asset_path = ?", [right]
    ).fetchone()[0] == 0
    assert con.execute(
        "SELECT COUNT(*) FROM extension_payload WHERE asset_path = ?", [right]
    ).fetchone()[0] == 0
    assert con.execute(
        "SELECT COUNT(*) FROM asset_edges WHERE source_path = ? OR target_path = ?",
        [right, right],
    ).fetchone()[0] == 0
    assert con.execute(
        "SELECT COUNT(*) FROM assets WHERE asset_path = ?", [left]
    ).fetchone()[0] == 1
    con.close()
