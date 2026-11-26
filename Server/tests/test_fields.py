"""Typed field projection, declarative domain config and bounded field reads."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ual_common import artifacts, db, domain, fields, settings
from ual_common.extensions_builtins import field_from_domain
from ual_common.registry import ExtensionRegistry, register_callable
from ual_common.toon import parse_toon

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIELDS_CONFIG = ROOT / "Config" / "UAL" / "fields" / "default.yaml"


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )


def _record(**overrides) -> dict:
    record = {
        "asset_path": "/Game/Types/TypedAsset",
        "asset_class": "StaticMesh",
        "parent_class": "StaticMesh",
        "domain_spec": {},
        "interface_spec": {},
    }
    record.update(overrides)
    return record


def _db_record(asset_path: str, asset_class: str, *, domain_spec=None) -> dict:
    return {
        "asset_path": asset_path,
        "logical_id": f"{asset_class}:{asset_path.rsplit('/', 1)[-1]}",
        "asset_class": asset_class,
        "parent_class": "UObject",
        "taxonomy": ["Asset", "Misc", asset_class],
        "tokens": "typed field asset",
        "content_hash": "sha1:typed",
        "duration": 0.0,
        "bounds_size": [0.0, 0.0, 0.0],
        "fingerprint_vec": [0.01] * 16,
        "interface_spec": {"skeleton": ""},
        "domain_spec": domain_spec or {},
        "yaml_cache": "identity: {}\n",
        "edges": [],
    }


def _custom_registry() -> ExtensionRegistry:
    registry = ExtensionRegistry()
    specs = [
        ("good_int", "integer", "good_int", 0),
        ("bad_int", "integer", "bad_int", 0),
        ("good_number", "number", "good_number", 0.0),
        ("bad_number", "number", "bad_number", 0.0),
        ("good_bool", "boolean", "good_bool", False),
        ("bad_bool", "boolean", "bad_bool", False),
        ("good_yaml", "yaml", "good_yaml", []),
        ("bad_yaml", "yaml", "bad_yaml", []),
        ("bad_string", "string", "bad_string", "fallback"),
        ("missing_default", "string", "missing_key", "missing"),
    ]
    for name, value_type, key, default in specs:
        register_callable(
            registry, "field", name, field_from_domain,
            applies_to=("*",), priority=10,
            schema={
                "type": value_type, "source": "domain_spec", "key": key,
                "default": default, "nullable": default is None,
            },
        )
    return registry


def test_typed_projection_defaults_and_determinism() -> None:
    registry = fields.FieldRegistry.from_extension_registry(_custom_registry())
    record = _record(domain_spec={
        "good_int": "3",
        "bad_int": "2.5",
        "good_number": "1.25",
        "bad_number": "nan",
        "good_bool": "true",
        "bad_bool": "1",
        "good_yaml": "{a: [1, 2]}",
        "bad_yaml": "[unclosed",
        "bad_string": 42,
    })
    rows = registry.project(record)
    by_name = {row["field_name"]: row for row in rows}

    assert by_name["good_int"]["value_number"] == 3.0
    assert by_name["good_int"]["value_text"] == "3"
    assert by_name["good_int"]["status"] == "ok"
    assert by_name["bad_int"]["value_number"] == 0.0
    assert by_name["bad_int"]["status"] == "invalid_value"
    assert by_name["good_number"]["value_text"] == "1.2500"
    assert by_name["bad_number"]["value_number"] == 0.0
    assert by_name["good_bool"]["value_bool"] is True
    assert by_name["bad_bool"]["value_bool"] is False
    assert by_name["good_yaml"]["value_text"] == artifacts.canonical_yaml({"a": [1, 2]})
    assert by_name["bad_yaml"]["value_text"] == artifacts.canonical_yaml([])
    assert by_name["bad_string"]["value_text"] == "fallback"
    assert by_name["missing_default"]["value_text"] == "missing"
    assert by_name["missing_default"]["status"] == "default"
    assert [row["field_name"] for row in rows] == sorted(row["field_name"] for row in rows)
    assert rows == registry.project(record)

    with pytest.raises(ValueError):
        fields.FieldSpec("field", "invalid", "bogus")


def test_resolution_precedence_is_exact_then_priority_then_key() -> None:
    registry = ExtensionRegistry()
    for name, applies_to, priority in (
        ("wild_high", ("*",), 100),
        ("exact_low", ("StaticMesh",), 1),
        ("exact_high", ("StaticMesh",), 50),
    ):
        register_callable(
            registry, "field", name, field_from_domain,
            applies_to=applies_to, priority=priority,
            schema={"type": "string", "source": "domain_spec", "key": name, "default": ""},
        )
    field_registry = fields.FieldRegistry.from_extension_registry(registry)
    assert [spec.name for spec in field_registry.resolve("StaticMesh")] == [
        "exact_high", "exact_low", "wild_high",
    ]
    assert [spec.name for spec in field_registry.resolve("OtherClass")] == ["wild_high"]


def test_declarative_domain_families_and_four_layer_override(tmp_path: Path) -> None:
    config = yaml.safe_load(DEFAULT_FIELDS_CONFIG.read_text(encoding="utf-8"))
    families = [
        ("StaticMesh", "mesh_"),
        ("AnimMontage", "anim_"),
        ("MaterialInstanceConstant", "material_"),
        ("Blueprint", "blueprint_"),
        ("DataTable", "data_table_"),
    ]
    for asset_class, prefix in families:
        record = _record(
            asset_class=asset_class,
            parent_class="Actor",
            domain_spec={
                "feature.family": asset_class,
                "duration": "1.15",
                "forward_cm": "185.0",
                "exposed_params": "A,B",
                "row_struct": "FRow",
                "row_count": "3",
                "columns": "[A, B]",
                "hint": "hint",
            },
            interface_spec={
                "material_slots": "[M_A]",
                "sockets_referenced": "[hand_r]",
                "skeleton": "/Game/SK_Test",
            },
        )
        rows = domain.extract_domain_fields(record, config=config)
        assert any(row["field_name"].startswith(prefix) for row in rows)
        assert rows == domain.extract_domain_fields(record, config=config)
        assert all("updated_at" not in row for row in rows)
        assert all("C:\\" not in str(row.get("value_text", "")) for row in rows)

    record = _record(asset_class="StaticMesh")
    _write_yaml(tmp_path / "Config" / "UAL" / "fields" / "team.yaml", {
        "families": {"static_mesh": {"fields": {"mesh_material_slots": {"default": ["team"]}}}},
    })
    _write_yaml(tmp_path / "Saved" / "Config" / "UAL" / "fields" / "me.yaml", {
        "families": {"static_mesh": {"fields": {"mesh_material_slots": {"default": ["user"]}}}},
    })
    resolved = settings.load_namespace("fields", project_root=tmp_path)
    rows = domain.extract_domain_fields(record, config=resolved)
    by_name = {row["field_name"]: row for row in rows}
    assert by_name["mesh_material_slots"]["value_text"] == artifacts.canonical_yaml(["user"])
    assert by_name["mesh_material_slots"]["status"] == "default"
    by_path = {row["field_name"]: row for row in domain.extract_domain_fields(record, config=tmp_path)}
    assert by_path["mesh_material_slots"]["value_text"] == artifacts.canonical_yaml(["user"])
    assert resolved.provenance == resolved.provenance


def test_materialize_assembly_and_bounded_reads(tmp_path: Path) -> None:
    asset_path = "/Game/Props/SM_Chair"
    con = db.open_write(tmp_path / "index.duckdb")
    db.ingest_records(con, [_db_record(
        asset_path, "StaticMesh",
        domain_spec={"feature.role": "prop", "feature.assembly_role": "surface", "feature.quality_tier": "3"},
    )], rebuild=False)
    counts = fields.materialize(con, project_root=tmp_path)
    assert counts["assets"] == 1
    assert counts["fields"] >= 3
    assert counts["asset_role"] == 1
    assert counts["assembly"] == 1
    assert counts["rows"] >= counts["fields"] + 2

    rows = dict(con.execute(
        "SELECT namespace || '.' || field_name, value_text FROM extension_payload WHERE asset_path = ?",
        [asset_path],
    ).fetchall())
    assert rows["asset_type.asset_role"] == "prop"
    assert rows["asset_type.assembly"] == artifacts.canonical_yaml([
        {"relation": "attached_to", "target_role": "character"},
    ])
    assert rows["field.quality_tier"] == "3"

    graph = fields.assembly_graph(con, asset_path, limit=5)
    parsed_graph = parse_toon(graph)
    assert graph.startswith("# UEAssetLens TOON v1 (Zero-VLM)")
    assert parsed_graph["tables"]["asset_role"][0]["role"] == "prop"
    assert parsed_graph["tables"]["assembly"][0]["relation"] == "attached_to"
    assert parsed_graph["tables"]["assembly"][0]["target_role"] == "character"
    assert "domain_spec" not in graph and "updated_at" not in graph

    found = fields.search(con, namespace="asset_type", field_name="asset_role", value_contains="prop", limit=10)
    assert found and found[0]["value_display"] == "prop"
    report = fields.render_search(found, total=11, limit=1)
    parsed_search = parse_toon(report)
    assert report.startswith("# UEAssetLens TOON v1 (Zero-VLM)")
    assert parsed_search["summary"]["truncated"] == "true"
    assert parsed_search["summary"]["total"] == "11"
    assert len(parsed_search["tables"]["extension_fields"]) == 1

    catalog_rows = fields.catalog(con, limit=30)
    assert catalog_rows == fields.catalog(con, limit=30)
    catalog_report = fields.render_catalog(catalog_rows, limit=2)
    parsed_catalog = parse_toon(catalog_report)
    assert catalog_report.startswith("# UEAssetLens TOON v1 (Zero-VLM)")
    assert parsed_catalog["summary"]["truncated"] == "true"
    assert len(parsed_catalog["tables"]["extension_catalog"]) == 2
    assert "updated_at" not in catalog_report

    before = con.execute("SELECT COUNT(*) FROM extension_payload").fetchone()[0]
    fields.materialize(con, project_root=tmp_path)
    assert con.execute("SELECT COUNT(*) FROM extension_payload").fetchone()[0] == before
    con.close()
