"""Contract tests for the read-only policy gate (no UE, no SDK plugins)."""
from __future__ import annotations

from pathlib import Path

from ual_common import db, policy, policy_gate
from ual_common.toon import parse_toon


def _record(path: str, asset_class: str, **overrides) -> dict:
    record = {
        "asset_path": path,
        "logical_id": f"{asset_class}:{path.rsplit('/', 1)[-1]}",
        "asset_class": asset_class,
        "parent_class": "UObject",
        "tokens": "policy gate fixture",
        "content_hash": "fixture",
        "domain_spec": {},
        "interface_spec": {},
        "edges": [],
    }
    record.update(overrides)
    return record


def _index(tmp_path: Path):
    con = db.open_write(tmp_path / "index.duckdb")
    db.ingest_records(con, [
        _record(
            "/Game/Core/BP_Player", "Blueprint",
            domain_spec={"feature.role": "player"},
            interface_spec={"exposed_params": "SlashColor"},
            edges=[
                {"target_path": "/Script/Engine.Actor", "edge_type": "DEPENDS_ON"},
                {"target_path": "/Game/Mesh/SM_Weapon", "edge_type": "DEPENDS_ON"},
                {"target_path": "/Game/Misc/Neighbour", "edge_type": "CO_OCCURS"},
            ],
        ),
        _record(
            "/Game/Temp/badName", "Blueprint",
            domain_spec={"FiBData": "blob"},
        ),
    ], rebuild=False)
    return con


def _violation(severity: str, enforce: bool) -> policy.Violation:
    return policy.Violation(
        rule_id="test.rule", kind="naming", severity=severity,
        asset_path="/Game/Temp/badName", message="fixture", enforce=enforce,
    )


def test_index_assets_are_projected_deterministically(tmp_path):
    con = _index(tmp_path)
    try:
        assets = policy_gate.load_index_assets(con)
        assert [a["asset_path"] for a in assets] == [
            "/Game/Core/BP_Player", "/Game/Temp/badName",
        ]
        good = assets[0]
        assert good["tags"] == ["exposed_params", "feature.role"]
        # CO_OCCURS must not be mistaken for a declared dependency.
        assert good["dependency_paths"] == [
            "/Game/Mesh/SM_Weapon", "/Script/Engine.Actor",
        ]
        assert assets == policy_gate.load_index_assets(con)
        assert con.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 2
    finally:
        con.close()


def test_gate_renders_bounded_report_without_writing(tmp_path):
    con = _index(tmp_path)
    try:
        report, violations, resolved = policy_gate.run_policy_gate(
            con, project_root=tmp_path, limit=2
        )
        assert "naming.blueprint.prefix" in {r["id"] for r in resolved.active_rules()}
        assert {"format.no_binary_blob_tags", "reference.no_script_targets"} <= {
            v.rule_id for v in violations
        }
        parsed = parse_toon(report)
        assert parsed["summary"]["returned"] == "2"
        assert parsed["summary"]["truncated"] == "true"
        assert "D:/" not in report and "C:\\" not in report
        assert not policy.has_blocking(violations)  # builtin rules report only
        assert con.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 2
    finally:
        con.close()


def test_exit_code_default_mode_is_report_first():
    assert policy_gate.decide_exit_code([]) == 0
    assert policy_gate.decide_exit_code([_violation("warning", False)]) == 0
    assert policy_gate.decide_exit_code([_violation("error", False)]) == 0
    assert policy_gate.decide_exit_code([_violation("error", True)]) == 1


def test_exit_code_fail_on_sets_the_severity_threshold():
    assert policy_gate.decide_exit_code([_violation("warning", True)], fail_on="warning") == 1
    assert policy_gate.decide_exit_code([_violation("warning", True)], fail_on="error") == 0
    assert policy_gate.decide_exit_code([_violation("info", True)], fail_on="warning") == 0


def test_exit_code_strict_drops_the_enforce_requirement():
    assert policy_gate.decide_exit_code(
        [_violation("warning", False)], fail_on="warning", strict=True
    ) == 1
    assert policy_gate.decide_exit_code(
        [_violation("info", False)], fail_on="warning", strict=True
    ) == 0


def test_exit_code_normalises_unknown_labels_without_failing_open():
    # A mistyped threshold degrades to the documented default (error), not to
    # "block everything"; an unknown severity degrades to warning, matching
    # PolicyEngine's own normalisation.
    assert policy_gate.decide_exit_code(
        [_violation("warning", True)], fail_on="typo"
    ) == 0
    assert policy_gate.decide_exit_code(
        [_violation("error", True)], fail_on="typo"
    ) == 1
    assert policy_gate.decide_exit_code(
        [_violation("typo", True)], fail_on="warning"
    ) == 1
