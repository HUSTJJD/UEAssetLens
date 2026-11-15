"""End-to-end contract tests: DDL -> JSONL ingest -> MCP tools -> CI gates."""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import pytest

from ual_common import db
from ual_common.fingerprint import (
    animation_fingerprint, cosine_similarity, mesh_fingerprint,
    radial_histogram,
)
from ual_common.toon import Table, parse_toon, render_toon
from ual_common.yamlspec import build_yaml
from ual_mcp import tools
from ual_cli import ual_cli

SEED = Path(__file__).parent / "seed.jsonl"
HERO = "/Game/Characters/Knight/Anim/AM_Knight_HeavySlash_02"


@pytest.fixture(scope="module")
def index(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("ual") / "index.duckdb"
    con = db.open_write(path)
    stats = db.ingest_records(con, db.load_jsonl(SEED))
    assert stats == {"assets": 9, "edges": 5}
    con.close()
    return path


@pytest.fixture(scope="module")
def ro(index):
    con = db.open_readonly(index)
    yield con
    con.close()


def test_schema_is_idempotent(index):
    con = db.open_write(index)
    db.ensure_schema(con)
    version = con.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()[0]
    assert version == "2.0"
    assert con.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 9
    con.close()


def test_fixed_array_and_fts_are_live(ro):
    assert ro.execute("SELECT COUNT(*) FROM asset_edges").fetchone()[0] == 5
    rows = db.search_assets(ro, query="heavy slash", asset_class="AnimMontage", limit=5)
    assert rows and all(r["score"] is not None for r in rows)
    assert len(ro.execute("SELECT fingerprint_vec FROM assets WHERE asset_path = ?", [HERO]).fetchone()[0]) == 16


def test_hard_filters_and_domain_scope(ro):
    rows = db.search_assets(
        ro, asset_class="AnimMontage",
        filters={"min_forward_cm": 100.0, "duration_min": 0.8,
                 "duration_max": 1.5, "dominant_limb": "hand_r"},
        domain_scope="/Game/Characters/Knight", limit=8,
    )
    assert {r["logical_id"] for r in rows} == {
        "AnimMontage:AM_Knight_HeavySlash_02",
        "AnimMontage:AM_Knight_HeavySlash_03",
        "AnimMontage:AM_Knight_DashThrust_01",
    }
    assert all(float(r["forward_cm"]) >= 100.0 for r in rows)


def test_duplicate_pair_is_detected(ro):
    pairs = db.duplicate_pairs(ro, 0.95)
    assert len(pairs) == 1
    assert pairs[0]["similarity"] > 0.99
    assert "HeavySlash_02" in pairs[0]["lhs"] and "HeavySlash_03" in pairs[0]["rhs"]


def test_cooccurrence_bundle(ro):
    bundle = db.cooccurring(ro, HERO, 5)
    assert [r["asset_class"] for r in bundle] == ["SoundWave", "NiagaraSystem"]
    assert bundle[0]["hint"] == "Mono|0.85s|3D_Combat"


def test_fingerprint_math():
    pts = [(x, y, z) for x in range(-3, 4) for y in range(-2, 3) for z in range(-8, 9)]
    rotated = [(-y, x, z) for x, y, z in pts]
    assert radial_histogram(pts) == pytest.approx(radial_histogram(rotated))
    mesh = mesh_fingerprint([60, 40, 180], radial_histogram(pts), 3, 6)
    anim = animation_fingerprint(1.15, [185, 0, 0], [0.5, 0.2, 0.2, 0.1], [0.1] * 8)
    assert len(mesh) == len(anim) == 16
    assert all(math.isfinite(v) for v in mesh + anim)
    assert cosine_similarity(mesh, mesh) == pytest.approx(1.0)
    assert cosine_similarity([0] * 16, mesh) == 0.0
    assert cosine_similarity([], []) == 0.0


def test_toon_roundtrip_with_escaping():
    tricky = 'Once,Once|"quoted"|User.SlashColor'
    text = render_toon(
        [Table("candidates", ["logical_id", "hint", "score"], [["A:X", tricky, 0.9876]])],
        summary={"matched_total": 1},
    )
    assert text.startswith("# UEAssetLens TOON v1 (Zero-VLM)")
    parsed = parse_toon(text)
    assert parsed["summary"]["matched_total"] == "1"
    assert parsed["tables"]["candidates"][0]["logical_id"] == "A:X"
    assert parsed["tables"]["candidates"][0]["hint"] == tricky
    assert parsed["tables"]["candidates"][0]["score"] == "0.9876"


def test_yaml_has_no_timestamps_or_local_paths(tmp_path):
    con = db.open_write(tmp_path / "y.duckdb")
    db.ingest_records(con, db.load_jsonl(SEED))
    asset = db.get_asset(con, HERO)
    refs = db.referencer_paths(con, HERO)
    text = build_yaml(asset, refs)
    con.close()
    assert text.startswith("# UEAssetLens AssetSpec v2.0")
    assert "updated_at" not in text and "2026-" not in text
    assert "C:\\" not in text and "D:/" not in text
    assert text == build_yaml(asset, refs)  # deterministic => clean Git diffs


def test_mcp_tools_obey_protocol(ro):
    search = parse_toon(tools.ual_search_assets(ro, query="heavy slash", asset_class="AnimMontage", limit=8))
    assert list(search["tables"]) == ["candidates", "co_occurring_bundle"]
    assert list(search["tables"]["candidates"][0]) == [
        "logical_id", "duration", "forward_cm", "strike_window", "limb", "score"
    ]
    yaml_text = tools.ual_inspect_asset(ro, "AnimMontage:AM_Knight_HeavySlash_02")
    assert yaml_text.startswith("# UEAssetLens AssetSpec v2.0")
    assert "graph_context:" in yaml_text
    similar = parse_toon(tools.ual_find_similar_assets(ro, HERO, 0.85))
    assert similar["tables"]["similar_assets"][0]["similarity"] != ""
    bundle = parse_toon(tools.ual_get_cooccurring_bundle(ro, HERO, 5))
    assert len(bundle["tables"]["co_occurring_bundle"]) == 2


def test_cli_gates_return_expected_exit_codes(index, tmp_path):
    assert ual_cli.cmd_lint_duplicates(
        argparse.Namespace(index=str(index), threshold=0.95, diff_only=False)) == 1
    assert ual_cli.cmd_lint_duplicates(
        argparse.Namespace(index=str(index), threshold=0.99999, diff_only=False)) == 0

    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "ok.yaml").write_text(f"skeleton: /Game/Characters/Knight/SK_Knight\nmontage: {HERO}\n", encoding="utf-8")
    assert ual_cli.cmd_verify_references(argparse.Namespace(
        index=str(index), scan_dirs=str(scan), project=str(tmp_path))) == 0
    (scan / "bad.yaml").write_text(
        "montage: /Game/Missing/AM_Hallucinated\nvfx: User.NotARealParam\n", encoding="utf-8")
    assert ual_cli.cmd_verify_references(argparse.Namespace(
        index=str(index), scan_dirs=str(scan), project=str(tmp_path))) == 1

    out = tmp_path / "shared.parquet"
    assert ual_cli.cmd_export_parquet(argparse.Namespace(index=str(index), out=str(out))) == 0
    assert out.exists() and out.stat().st_size > 0


def test_engine_bridge_dispatches_all_four_tools(ro):
    from ual_mcp import engine_bridge

    cases = [
        ("ual_search_assets", {"asset_class": "AnimMontage", "limit": 2}),
        ("ual_inspect_asset", {"key": "AnimMontage:AM_Knight_HeavySlash_02"}),
        ("ual_get_cooccurring_bundle", {"asset_path": HERO, "top_k": 2}),
        ("ual_find_similar_assets", {"asset_path": HERO, "threshold": 0.95}),
    ]
    for name, arguments in cases:
        text = engine_bridge.dispatch(ro, name, arguments)
        assert text.startswith("# UEAssetLens"), name
    with pytest.raises(KeyError):
        engine_bridge.dispatch(ro, "ual_not_a_tool", {})


def test_tool_schemas_contract_matches_python_server():
    import json

    from ual_mcp import tools as mcp_tools

    schema_path = Path(__file__).resolve().parents[1] / "ual_mcp" / "tool_schemas.json"
    payload = json.loads(schema_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "2.0"
    assert [t["name"] for t in payload["tools"]] == list(mcp_tools.TOOL_NAMES)
    for tool in payload["tools"]:
        assert tool["inputSchema"]["type"] == "object"


def test_server_tool_schemas_match_tool_defs(ro):
    """The SDK-generated schemas must stay aligned with the shared TOOL_DEFS."""
    import asyncio

    from ual_mcp import server, tools as mcp_tools

    sdk = server._create_sdk_server()
    server._register(sdk, ro)
    generated = {tool.name: tool.input_schema for tool in asyncio.run(sdk.list_tools())}
    assert set(generated) == set(mcp_tools.TOOL_NAMES)
    for tool in mcp_tools.TOOL_DEFS:
        declared = set(tool["inputSchema"].get("properties", {}))
        actual = set(generated[tool["name"]].get("properties", {}))
        assert declared == actual, tool["name"]
