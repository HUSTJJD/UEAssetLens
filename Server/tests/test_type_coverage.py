"""Regression: the index accepts every asset class, not a hard-coded shortlist.

These tests exercise the three guarantees behind "cover all UE asset types":
  1. Any UObject class (including plugin-defined and future engine types) gets a
     three-level taxonomy from the shared rule table.
  2. All of them survive DuckDB ingest, taxonomy backfill and class filtering.
  3. logical_id collisions across folders/modules never abort an ingest.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from ual_common import artifacts, db
from ual_common.taxonomy import build_taxonomy

REPO = Path(__file__).resolve().parents[2]

# (asset_class, expected family). Includes classic UE types, newer engine types,
# plugin-defined types and a completely unknown UObject subclass.
CASES: list[tuple[str, str]] = [
    ("AnimMontage", "Animation"),
    ("StaticMesh", "Mesh"),
    ("Texture2D", "Texture"),
    ("MaterialInstanceConstant", "Material"),
    ("SoundWave", "Audio"),
    ("Blueprint", "Blueprint"),
    ("UserWidget", "UI"),
    ("NiagaraSystem", "VFX"),
    ("DataTable", "Data"),
    ("LevelSequence", "Cinematic"),
    ("PhysicsAsset", "Physics"),
    ("BehaviorTree", "AI"),
    ("StateTree", "AI"),
    ("GameplayAbility", "Gameplay"),
    ("PCGGraph", "PCG"),
    ("GroomAsset", "Groom"),
    ("GeometryCache", "Geometry"),
    ("MediaPlayer", "Media"),
    ("World", "World"),
    ("FontFace", "Text"),
    ("StringTable", "Text"),
    ("MetaSoundSource", "Audio"),
    ("ReflectionCapture", "Rendering"),
    ("DeveloperSettings", "Editor"),
    ("UnLuaNovaSettings", "Scripting"),
    ("CustomPluginThing", "Misc"),
    ("ZzzUnknownUObject", "Misc"),
]


def make_record(asset_class: str, index: int) -> dict[str, Any]:
    name = f"Asset_{index:03d}"
    return {
        "asset_path": f"/Game/Types/{asset_class}/{name}",
        "logical_id": f"{asset_class}:{name}",
        "asset_class": asset_class,
        # taxonomy deliberately omitted: ingest must backfill it from the rules.
        "tokens": f"{name.lower()} {asset_class.lower()}",
        "content_hash": "sha1:coverage",
        "fingerprint_vec": [0.01 * (index + 1)] * 16,
    }


@pytest.fixture(scope="module")
def type_index(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("ual_types") / "index.duckdb"
    con = db.open_write(path)
    records = [make_record(cls, idx) for idx, (cls, _) in enumerate(CASES)]
    stats = db.ingest_records(con, records)
    assert stats["assets"] == len(CASES)
    con.close()
    return path


@pytest.mark.parametrize("asset_class,expected_family", CASES)
def test_taxonomy_covers_any_asset_class(asset_class: str, expected_family: str) -> None:
    taxonomy = build_taxonomy(asset_class)
    assert taxonomy[0] == "Asset"
    assert taxonomy[1] == expected_family
    assert taxonomy[-1] == asset_class
    # C++ uses AddUnique: when family == asset_class (e.g. Blueprint / World)
    # the path legitimately collapses to two levels.
    assert len(taxonomy) in (2, 3)


def test_all_types_survive_ingest_and_are_filterable(type_index: Path) -> None:
    con = db.open_readonly(type_index)
    assert con.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == len(CASES)
    assert con.execute(
        "SELECT COUNT(DISTINCT asset_class) FROM assets"
    ).fetchone()[0] == len(CASES)
    for asset_class, expected_family in CASES:
        row = con.execute(
            "SELECT taxonomy FROM assets WHERE asset_class = ?", [asset_class]
        ).fetchone()
        assert row is not None, asset_class
        assert list(row[0]) == build_taxonomy(asset_class)
        assert list(row[0])[1] == expected_family
    con.close()


def test_unknown_class_is_still_searchable(type_index: Path) -> None:
    con = db.open_readonly(type_index)
    rows = db.search_assets(
        con, query="zzzunknownuobject", asset_class="ZzzUnknownUObject", limit=5
    )
    assert [r["asset_class"] for r in rows] == ["ZzzUnknownUObject"]
    con.close()


def test_logical_id_collision_is_resolved_deterministically(tmp_path: Path) -> None:
    con = db.open_write(tmp_path / "collide.duckdb")
    base = {
        "asset_class": "AnimMontage",
        "logical_id": "AnimMontage:AM_Dup",
        "tokens": "dup",
        "content_hash": "sha1:x",
        "fingerprint_vec": [0.1] * 16,
    }
    first = {**base, "asset_path": "/Game/A/AM_Dup"}
    second = {**base, "asset_path": "/Game/B/AM_Dup"}
    db.ingest_records(con, [first, second])

    rows = dict(con.execute("SELECT asset_path, logical_id FROM assets").fetchall())
    assert rows["/Game/A/AM_Dup"] == "AnimMontage:AM_Dup"
    assert rows["/Game/B/AM_Dup"].startswith("AnimMontage:AM_Dup:")
    assert len(rows["/Game/B/AM_Dup"]) == len("AnimMontage:AM_Dup") + 9

    # Re-ingesting the same pair must not produce a different suffix.
    db.ingest_records(con, [first, second])
    again = dict(con.execute("SELECT asset_path, logical_id FROM assets").fetchall())
    assert again == rows
    con.close()


def test_generated_cpp_rules_are_in_sync() -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO / "Tools" / "gen_taxonomy.py"), "--check"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_null_fingerprint_is_excluded_from_duplicate_cosine_checks(tmp_path: Path) -> None:
    """Staging NULL / absent / invalid fingerprints must stay SQL NULL.

    A zero-filled 16-vector used to make missing fingerprints look like real
    ones; both duplicate_pairs() and find_similar() must ignore NULL vectors.
    """
    con = db.open_write(tmp_path / "null_fingerprint.duckdb")
    try:
        duplicate_paths = ["/Game/Core/BP_Dup_A", "/Game/Core/BP_Dup_B"]
        null_paths = [
            "/Game/Core/BP_NoFingerprint",
            "/Game/Core/SM_AbsentFingerprint",
            "/Game/Core/SM_InvalidFingerprint",
        ]
        fingerprint = [0.2] * 16
        docs: list[dict[str, Any]] = []

        for index, path in enumerate(duplicate_paths):
            record = make_record("Blueprint", index)
            record["asset_path"] = path
            record["logical_id"] = "Blueprint:" + path.rsplit("/", 1)[-1]
            record["fingerprint_vec"] = list(fingerprint)
            docs.append(record)

        explicit_null = make_record("Blueprint", 2)
        explicit_null["asset_path"] = null_paths[0]
        explicit_null["logical_id"] = "Blueprint:" + null_paths[0].rsplit("/", 1)[-1]
        explicit_null["fingerprint_vec"] = None
        docs.append(explicit_null)

        absent = make_record("StaticMesh", 3)
        absent["asset_path"] = null_paths[1]
        absent["logical_id"] = "StaticMesh:" + null_paths[1].rsplit("/", 1)[-1]
        absent.pop("fingerprint_vec", None)
        docs.append(absent)

        invalid = make_record("StaticMesh", 4)
        invalid["asset_path"] = null_paths[2]
        invalid["logical_id"] = "StaticMesh:" + null_paths[2].rsplit("/", 1)[-1]
        invalid["fingerprint_vec"] = [0.2] * 15
        docs.append(invalid)

        staging = tmp_path / "staging.yaml"
        staging.write_text(artifacts.canonical_yaml_stream(docs), encoding="utf-8")
        assert db.ingest_records(con, db.load_yaml_docs(staging)) == {"assets": 5, "edges": 0}

        rows = con.execute(
            "SELECT asset_path FROM assets WHERE fingerprint_vec IS NULL ORDER BY asset_path"
        ).fetchall()
        assert [row[0] for row in rows] == sorted(null_paths)
        assert con.execute(
            "SELECT COUNT(*) FROM assets WHERE fingerprint_vec IS NOT NULL"
        ).fetchone()[0] == 2

        pairs = db.duplicate_pairs(con, threshold=0.99)
        assert len(pairs) == 1
        assert {pairs[0]["lhs"], pairs[0]["rhs"]} == set(duplicate_paths)
        for null_path in null_paths:
            assert db.find_similar(con, null_path, threshold=0.0) == []
        assert {
            row["asset_path"] for row in db.find_similar(con, duplicate_paths[0], threshold=0.0)
        } == {duplicate_paths[1]}

        metrics = {item["metric"]: item["value"] for item in db.index_health(con)["metrics"]}
        assert metrics["missing_fingerprint"] == len(null_paths)
    finally:
        con.close()
