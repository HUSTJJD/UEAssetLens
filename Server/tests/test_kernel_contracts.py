"""Phase 1-5 contract tests for the offline asset kernel.

These tests intentionally avoid the MCP SDK and UE SDK: they verify the frozen
16-key staging contract, v3.0 typed extension payload, declarative extension
manifests, custom policy rules, runtime tool annotations and four-place schema
sync using only DuckDB / PyYAML / stdlib.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "Server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from ual_common import artifacts, db, extensions, policy_gate  # noqa: E402

NAMESPACES = {
    "asset_type", "policy", "field", "diff", "tool",
    "lifecycle", "sink", "template", "gateway",
}


def _record(asset_path: str) -> dict:
    return {
        "asset_path": asset_path,
        "logical_id": "Blueprint:" + asset_path.rsplit("/", 1)[-1],
        "asset_class": "Blueprint",
        "parent_class": "Actor",
        "taxonomy": ["Asset", "Gameplay"],
        "tokens": "draft blueprint",
        "content_hash": "fixture",
        "duration": 0.0,
        "bounds_size": None,
        "skeleton_path": None,
        "dominant_limb": None,
        "fingerprint_vec": None,
        "interface_spec": {},
        "domain_spec": {},
        "yaml_cache": None,
        "edges": [],
    }


def test_four_place_v3_contract_is_in_sync():
    protocol = (ROOT / "Server/schema/PROTOCOL.md").read_text(encoding="utf-8")
    ddl = (ROOT / "Server/schema/init.sql").read_text(encoding="utf-8")
    types_h = (ROOT / "Source/UEAssetLens/Public/UALTypes.h").read_text(encoding="utf-8")
    db_py = (ROOT / "Server/ual_common/db.py").read_text(encoding="utf-8")

    assert "v3.0" in protocol and "extension_payload" in protocol
    assert "schema_version', '3.0'" in ddl
    assert "CREATE TABLE IF NOT EXISTS extension_payload" in ddl
    assert "engine_version" in ddl
    assert "FUALExtensionField" in types_h
    assert "FUALExtensionRecord" in types_h
    assert "EXTENSION_PAYLOAD_COLUMNS" in db_py

    # The 16-key staging document is unchanged. `edges` is the 16th staging
    # key; the DuckDB writer keeps it out of ASSET_COLUMNS.
    assert db.ASSET_COLUMNS == (
        "asset_path", "logical_id", "asset_class", "parent_class", "taxonomy",
        "tokens", "content_hash", "duration", "bounds_size", "skeleton_path",
        "dominant_limb", "fingerprint_vec", "interface_spec", "domain_spec",
        "yaml_cache",
    )
    assert "edges" not in db.ASSET_COLUMNS
    assert not (set(db.ASSET_COLUMNS) & set(db.EXTENSION_PAYLOAD_COLUMNS[1:]))
    assert "# UAL 三层数据格式协议 v3.0" in protocol


def test_builtin_extension_registry_is_data_driven_and_deterministic():
    first = extensions.load_builtin_registry()
    second = extensions.load_builtin_registry()
    snapshot = first.registry.snapshot()
    assert snapshot == second.registry.snapshot()
    assert NAMESPACES <= {item["namespace"] for item in snapshot}
    assert all("impl" not in item for item in snapshot)
    text = artifacts.canonical_yaml(snapshot)
    assert "D:/" not in text and "C:\\" not in text
    assert "updated_at" not in text and "TODO(human)" not in text
    assert all(source.startswith("builtin:") for source in first.provenance)

    tool_defs = extensions.tool_defs_from_registry(first.registry)
    assert len(tool_defs) == 20
    extensions.validate_tool_names(tool_defs)
    assert all(tool["mcp_name"].startswith("UEAssetLens.") for tool in tool_defs)
    assert all(not tool["mcp_name"].startswith("ual_") for tool in tool_defs)
    assert all(tool["accessMode"] in ("Read", "Write", "Execute") for tool in tool_defs)
    assert all(tool["readOnlyHint"] == (tool["accessMode"] == "Read") for tool in tool_defs)

    report = extensions.render_registry_snapshot(first.registry, limit=100)
    assert report.startswith("# UEAssetLens TOON v1 (Zero-VLM)")
    assert "D:/" not in report and "C:\\" not in report
    assert "updated_at" not in report


def test_generated_tool_schemas_are_annotated_and_registry_driven():
    payload = yaml.safe_load(
        (ROOT / "Server/ual_mcp/tool_schemas.yaml").read_text(encoding="utf-8")
    )
    assert payload["schema_version"] == "4.0"
    assert "TOOL_DEFS" in payload["source"]
    assert "Config/UAL/extensions/tools.yaml" in payload["source"]
    tools = payload["tools"]
    assert len(tools) == 30
    assert len({tool["name"] for tool in tools}) == len(tools)
    for tool in tools:
        assert tool["name"].startswith("UEAssetLens.")
        assert not tool["name"].startswith("ual_")
        assert tool["accessMode"] in ("Read", "Write", "Execute")
        assert tool["readOnlyHint"] == (tool["accessMode"] == "Read")
        assert tool["annotations"]["accessMode"] == tool["accessMode"]
        assert tool["annotations"]["readOnlyHint"] == tool["readOnlyHint"]
        assert tool["meta"]["accessMode"] == tool["accessMode"]
        assert tool["inputSchema"]["type"] == "object"


def test_custom_policy_rule_loads_from_declarative_extension_manifest(tmp_path):
    con = db.open_write(tmp_path / "index.duckdb")
    db.ingest_records(con, [_record("/Game/Draft/BP_Draft")])
    try:
        empty = tmp_path / "default_project"
        empty.mkdir()
        report, violations, _ = policy_gate.run_policy_gate(
            con, project_root=empty, limit=20
        )
        assert "custom.no_draft_names" not in {v.rule_id for v in violations}
        assert report.startswith("# UEAssetLens TOON v1 (Zero-VLM)")

        project = tmp_path / "project"
        artifacts.write_artifact(
            project,
            "Config/UAL/policies/team.yaml",
            {
                "rules": [{
                    "id": "custom.no_draft_names",
                    "kind": "custom",
                    "applies_to": ["*"],
                    "extension": "no_draft_names",
                    "severity": "warning",
                    "enforce": False,
                    "enabled": True,
                    "forbidden": ["Draft"],
                    "message": "draft assets are not publishable",
                }]
            },
        )
        report, violations, _ = policy_gate.run_policy_gate(
            con, project_root=project, limit=20
        )
        assert "custom.no_draft_names" in {v.rule_id for v in violations}
        assert report.startswith("# UEAssetLens TOON v1 (Zero-VLM)")
        assert "D:/" not in report and "C:\\" not in report
        assert "updated_at" not in report
    finally:
        con.close()


def test_extension_payload_table_is_typed_and_bounded(tmp_path):
    con = db.open_write(tmp_path / "index.duckdb")
    try:
        db.ingest_records(con, [_record("/Game/Core/BP_Player")])
        rows = [{
            "asset_path": "/Game/Core/BP_Player",
            "namespace": "field",
            "field_name": "quality_tier",
            "value_type": "integer",
            "value_text": None,
            "value_number": 3.0,
            "value_bool": None,
            "provenance": "builtin",
        }]
        assert db.ingest_extension_payload(con, rows) == 1
        found = db.search_extension_payload(con, field_name="quality_tier", limit=5)
        assert len(found) == 1
        assert found[0]["value_display"] == "3.0000"
        catalog = db.extension_payload_catalog(con, limit=5)
        assert any(row["field_name"] == "quality_tier" for row in catalog)
        assert db.remove_assets(con, ["/Game/Core/BP_Player"]) == 1
        assert db.search_extension_payload(con, limit=5) == []
    finally:
        con.close()


def test_canonical_artifact_emitter_is_stable_and_rejects_absolute_paths():
    payload = {"b": 2, "a": [1, "x"]}
    first = artifacts.canonical_yaml(payload)
    assert first == artifacts.canonical_yaml(payload)
    assert first.endswith("\n")
    assert "D:/" not in first and "C:\\" not in first
    stream = artifacts.canonical_yaml_stream([{"a": 1}, {"a": 2}])
    assert stream.startswith("---")
    assert list(yaml.safe_load_all(stream)) == [{"a": 1}, {"a": 2}]
    with pytest.raises(ValueError):
        artifacts.safe_relative("C:/temp/plan.yaml")
    with pytest.raises(ValueError):
        artifacts.safe_relative("../plan.yaml")


# ---------------------------------------------------------------------------
# C3 / C4: exact field / type / version parsing across the four contract files.
# The C++ mirror is a comment block, so every place can be parsed without
# changing schema_meta.schema_version or regenerating docs/reference/*.
# ---------------------------------------------------------------------------

PROTOCOL_MD = ROOT / "Server" / "schema" / "PROTOCOL.md"
INIT_SQL = ROOT / "Server" / "schema" / "init.sql"
UAL_TYPES_H = ROOT / "Source" / "UEAssetLens" / "Public" / "UALTypes.h"
DB_PY = ROOT / "Server" / "ual_common" / "db.py"

STAGING_KEYS = (
    "asset_path", "logical_id", "asset_class", "parent_class", "taxonomy",
    "tokens", "content_hash", "duration", "bounds_size", "skeleton_path",
    "dominant_limb", "fingerprint_vec", "interface_spec", "domain_spec",
    "yaml_cache", "edges",
)

CORE_TYPES = {
    "asset_path": "VARCHAR",
    "logical_id": "VARCHAR",
    "asset_class": "VARCHAR",
    "parent_class": "VARCHAR",
    "taxonomy": "VARCHAR[]",
    "tokens": "VARCHAR",
    "content_hash": "VARCHAR",
    "duration": "FLOAT",
    "bounds_size": "FLOAT[3]",
    "skeleton_path": "VARCHAR",
    "dominant_limb": "VARCHAR",
    "fingerprint_vec": "FLOAT[16]",
    "interface_spec": "MAP(VARCHAR, VARCHAR)",
    "domain_spec": "MAP(VARCHAR, VARCHAR)",
    "yaml_cache": "VARCHAR",
}

EXTENSION_TYPES = {
    "asset_path": "VARCHAR",
    "namespace": "VARCHAR",
    "field_name": "VARCHAR",
    "value_type": "VARCHAR",
    "value_text": "VARCHAR",
    "value_number": "DOUBLE",
    "value_bool": "BOOLEAN",
    "provenance": "VARCHAR",
}

SCHEMA_VERSION = "3.0"
_DDL_TYPE_RE = re.compile(
    r"^(VARCHAR\[\]|FLOAT\[\d+\]|MAP\(VARCHAR,\s*VARCHAR\)|DOUBLE|BOOLEAN|FLOAT|VARCHAR|TIMESTAMP)(?=\s|$)"
)


def _ddl_columns(script: str, table: str) -> dict[str, str]:
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS " + re.escape(table) + r"\s*\((.*?)\);",
        script, re.S,
    )
    assert match is not None, table
    columns: dict[str, str] = {}
    for raw in match.group(1).splitlines():
        code = raw.split("--", 1)[0].strip()
        code = code.rstrip(",").strip()
        if not code:
            continue
        decl = re.match(r"^([a-z_]+)\s+(.+)$", code)
        if decl is None:
            continue
        found = _DDL_TYPE_RE.match(decl.group(2).strip())
        if found is None:
            continue
        columns[decl.group(1)] = found.group(1)
    return columns


def _protocol_staging_doc(protocol: str) -> dict[str, Any]:
    match = re.search(r"```yaml\n(.*?)\n```", protocol, re.S)
    assert match is not None
    doc = yaml.safe_load(match.group(1))
    assert isinstance(doc, dict)
    return doc


def _protocol_example_types(doc: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in doc.items():
        if key == "edges":
            continue
        if isinstance(value, str):
            result[key] = "VARCHAR"
        elif isinstance(value, dict):
            result[key] = "MAP(VARCHAR, VARCHAR)"
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            result[key] = "FLOAT"
        elif isinstance(value, list):
            if key == "taxonomy":
                result[key] = "VARCHAR[]"
            elif key == "bounds_size":
                assert len(value) == 3
                result[key] = "FLOAT[3]"
            elif key == "fingerprint_vec":
                assert len(value) == 16
                result[key] = "FLOAT[16]"
            else:
                raise AssertionError(f"unexpected list field: {key}")
        else:
            raise AssertionError(f"unexpected field type: {key} -> {type(value).__name__}")
    return result


def _cpp_mirror(text: str) -> tuple[str, tuple[str, ...], dict[str, str], dict[str, str]]:
    block = re.search(
        r"// ---- four-place mirror.*?// ---- end four-place mirror ----", text, re.S
    )
    assert block is not None
    payload = block.group(0)
    version = re.search(r"// mirror version (\d+\.\d+)", payload)
    keys = re.search(r"// staging keys: ([a-z_,]+)", payload)
    assert version is not None and keys is not None
    core: dict[str, str] = {}
    extension: dict[str, str] = {}
    section: dict[str, str] | None = None
    for raw in payload.splitlines():
        line = raw.strip()
        if line == "// core fields:":
            section = core
            continue
        if line == "// extension fields:":
            section = extension
            continue
        if section is None:
            continue
        item = re.match(r"^//\s+([a-z_]+)\s+(.+?)\s*$", line)
        if item is not None:
            section[item.group(1)] = item.group(2)
    return version.group(1), tuple(keys.group(1).split(",")), core, extension


def _py_tuple(text: str, name: str) -> tuple[str, ...]:
    match = re.search(
        rf"{re.escape(name)}: tuple\[str, \.\.\.\] = \((.*?)\)", text, re.S
    )
    assert match is not None, name
    return tuple(re.findall(r'"([a-z_]+)"', match.group(1)))


def _db_cast_types(text: str) -> dict[str, str]:
    casts: dict[str, str] = {}
    for match in re.finditer(
        r'"CAST\(\? AS ([^"]+)\)"\s*if\s*c\s*==\s*"([a-z_]+)"', text
    ):
        casts[match.group(2)] = match.group(1)
    for match in re.finditer(
        r'"CAST\(\? AS ([^"]+)\)"\s*if\s*c\s+in\s+\(([^)]+)\)', text
    ):
        for column in re.findall(r'"([a-z_]+)"', match.group(2)):
            casts[column] = match.group(1)
    return casts


def _python_value_type(column: str, value: Any) -> str:
    if isinstance(value, dict):
        return "MAP(VARCHAR, VARCHAR)"
    if isinstance(value, list):
        if column == "taxonomy":
            return "VARCHAR[]"
        if column == "bounds_size":
            return "FLOAT[3]"
        if column == "fingerprint_vec":
            return "FLOAT[16]"
        raise AssertionError(f"unexpected list field: {column}")
    if isinstance(value, str):
        return "VARCHAR"
    if isinstance(value, float):
        return "FLOAT"
    raise AssertionError(f"unexpected field type: {column} -> {type(value).__name__}")


def test_four_place_field_type_version_parsing_is_consistent():
    protocol = PROTOCOL_MD.read_text(encoding="utf-8")
    ddl = INIT_SQL.read_text(encoding="utf-8")
    types_h = UAL_TYPES_H.read_text(encoding="utf-8")
    db_py = DB_PY.read_text(encoding="utf-8")

    protocol_doc = _protocol_staging_doc(protocol)
    protocol_types = _protocol_example_types(protocol_doc)
    protocol_extension = _ddl_columns(protocol, "extension_payload")
    ddl_assets = _ddl_columns(ddl, "assets")
    ddl_extension = _ddl_columns(ddl, "extension_payload")
    cpp_version, cpp_staging, cpp_core, cpp_extension = _cpp_mirror(types_h)
    db_assets = _py_tuple(db_py, "ASSET_COLUMNS")
    db_extension = _py_tuple(db_py, "EXTENSION_PAYLOAD_COLUMNS")
    db_casts = _db_cast_types(db_py)

    # Versions: v3.0 in all four places, schema_meta.schema_version stays 3.0.
    protocol_version = re.search(r"^# UAL 三层数据格式协议 v(\d+\.\d+)", protocol, re.M)
    ddl_version = re.search(r"DuckDB storage schema, v(\d+\.\d+)", ddl)
    db_version = re.search(r"UEAssetLens v(\d+\.\d+)", db_py)
    assert protocol_version is not None and ddl_version is not None and db_version is not None
    assert protocol_version.group(1) == ddl_version.group(1) == db_version.group(1) == cpp_version == SCHEMA_VERSION
    assert "schema_version=3.0" in protocol
    assert re.search(r"schema_meta VALUES \('schema_version', '3\.0'\)", ddl) is not None
    assert "'fingerprint_dims', '16'" in ddl and "fingerprint_dims=16" in protocol
    assert "'engine_version', 'UE6'" in ddl and "engine_version=UE6" in protocol

    # Field names: frozen 16 staging keys, 15 asset columns, no updated_at.
    assert tuple(protocol_doc) == STAGING_KEYS
    assert db_assets + ("edges",) == STAGING_KEYS
    assert cpp_staging == STAGING_KEYS
    assert tuple(cpp_core) == db_assets == db.ASSET_COLUMNS
    assert tuple(k for k in ddl_assets if k != "updated_at") == db_assets
    assert ddl_assets.get("updated_at") == "TIMESTAMP"
    assert "updated_at" not in STAGING_KEYS and "updated_at" not in db.ASSET_COLUMNS
    assert len(STAGING_KEYS) == 16 and len(cpp_core) == 15

    # Types: PROTOCOL example / DDL / C++ mirror / db.py casts + normalizer.
    assert protocol_types == CORE_TYPES
    assert {k: v for k, v in ddl_assets.items() if k != "updated_at"} == CORE_TYPES
    assert cpp_core == CORE_TYPES
    sample = {
        "asset_path": "/Game/Core/BP_Player",
        "logical_id": "Blueprint:BP_Player",
        "asset_class": "Blueprint",
        "parent_class": "Actor",
        "taxonomy": ["Asset", "Blueprint"],
        "tokens": "core player",
        "content_hash": "sha1:contract",
        "duration": 1.5,
        "bounds_size": [1.0, 2.0, 3.0],
        "skeleton_path": "/Game/Core/SK_Player",
        "dominant_limb": "hand_r",
        "fingerprint_vec": [0.125] * 16,
        "interface_spec": {"skeleton": "/Game/Core/SK_Player"},
        "domain_spec": {"hint": "Once|1.2s|User.SlashColor"},
        "yaml_cache": "# UEAssetLens AssetSpec v2.0 (Auto-generated, Zero-VLM)",
        "edges": [],
    }
    normalized = db.normalize_record(sample)
    db_types = dict(db_casts)
    for column, value in normalized.items():
        if column == "edges" or column in db_types:
            continue
        db_types[column] = _python_value_type(column, value)
    assert db_types == CORE_TYPES
    assert set(db_casts) == {
        "taxonomy", "bounds_size", "fingerprint_vec", "interface_spec", "domain_spec",
    }
    assert "canonical storage" in db_py and "space-joined VARCHAR" in db_py

    # Typed extension payload: independent table, identical eight columns/types.
    assert tuple(protocol_extension) == tuple(ddl_extension) == db_extension == tuple(cpp_extension)
    assert db_extension == db.EXTENSION_PAYLOAD_COLUMNS
    assert protocol_extension == ddl_extension == cpp_extension == EXTENSION_TYPES
    assert len(db_extension) == 8
    assert "value_number" in db_py and "value_bool" in db_py and "value_text" in db_py
    assert '"?" for _ in EXTENSION_PAYLOAD_COLUMNS' in db_py
