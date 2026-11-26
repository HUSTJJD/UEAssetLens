"""Direct argparse.Namespace coverage for the ual-cli offline kernel.

This test intentionally avoids the official MCP SDK: ual_mcp.tools is a pure
string-returning dispatch layer. All artifacts live in pytest temp directories.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from ual_cli import ual_cli
from ual_common import artifacts, db
from ual_common.toon import HEADER as TOON_HEADER, parse_toon
from ual_mcp import tools

HERO = "/Game/Characters/Knight/Anim/AM_Knight_HeavySlash_02"
DUPLICATE_LHS = "/Game/Characters/Knight/Anim/AM_Knight_HeavySlash_02"
DUPLICATE_RHS = "/Game/Characters/Knight/Anim/AM_Knight_HeavySlash_03"


@pytest.fixture
def kernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    project = tmp_path / "project"
    project.mkdir()
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(tools, "RUNTIME_ARTIFACT_ROOT", runtime)
    index = tmp_path / "index.duckdb"
    assert ual_cli.cmd_build(argparse.Namespace(
        demo=True, from_yaml=None, index=str(index), incremental=False,
        project=str(project), engine_cmd=None,
    )) == 0
    return SimpleNamespace(tmp=tmp_path, project=project, runtime=runtime, index=index)


def _call(func, namespace: argparse.Namespace, capsys: pytest.CaptureFixture[str]):
    code = func(namespace)
    captured = capsys.readouterr()
    return code, captured.out


def _assert_clean_output(text: str) -> None:
    assert not ual_cli.LOCAL_PATH_RE.search(text)
    assert not ual_cli.TIMESTAMP_RE.search(text)
    assert not ual_cli.RANDOM_ID_RE.search(text)
    assert "updated_at:" not in text
    assert "SELECT * FROM v_search" not in text
    assert len(text) < 400000


def _write_yaml(path: Path, payload, *, stream: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = artifacts.canonical_yaml_stream(payload) if stream else artifacts.canonical_yaml(payload)
    path.write_text(text, encoding="utf-8", newline="\n")


def _record() -> dict:
    return {
        "asset_path": "/Game/Events/BP_Indexed",
        "logical_id": "Blueprint:BP_Indexed",
        "asset_class": "Blueprint",
        "parent_class": "UObject",
        "taxonomy": ["Asset", "Gameplay", "Blueprint"],
        "tokens": "indexed event blueprint",
        "content_hash": "sha1:BP_Indexed",
        "duration": 0.0,
        "bounds_size": None,
        "skeleton_path": None,
        "dominant_limb": None,
        "fingerprint_vec": None,
        "interface_spec": {},
        "domain_spec": {"feature.role": "gameplay"},
        "yaml_cache": "# UEAssetLens AssetSpec v2.0 (Auto-generated, Zero-VLM)\n",
        "edges": [],
    }


def _base(kernel: SimpleNamespace, **overrides) -> argparse.Namespace:
    values = {"index": str(kernel.index), "project": str(kernel.project)}
    values.update(overrides)
    return argparse.Namespace(**values)


def test_new_runtime_commands_dispatch_bounded_toon(kernel, capsys):
    outputs: list[str] = []
    simple_calls = [
        (ual_cli.cmd_list_extensions, _base(kernel, limit=8)),
        (ual_cli.cmd_list_extension_fields, _base(kernel, limit=8)),
        (ual_cli.cmd_search_extension_fields, _base(
            kernel, namespace="", field_name="", value_contains="", limit=8)),
        (ual_cli.cmd_get_assembly_graph, _base(kernel, asset_path=HERO, limit=8)),
        (ual_cli.cmd_list_official_tools, _base(kernel, limit=8)),
        (ual_cli.cmd_get_index_revision, _base(kernel)),
        (ual_cli.cmd_allowlist_audit, _base(
            kernel, allowlist_project=None, as_of="2026-11-26",
            approved_by="reviewer", review_ref="PR-1",
            strict_allowlist=True, limit=20)),
    ]
    for func, namespace in simple_calls:
        code, out = _call(func, namespace, capsys)
        assert code == 0
        assert out.startswith(TOON_HEADER)
        outputs.append(out)

    code, diff_out = _call(ual_cli.cmd_diff_assets, _base(
        kernel, lhs=HERO, rhs=HERO, layers="L0,L1,L2,L3,L4"), capsys)
    assert code == 0 and diff_out.startswith(TOON_HEADER)
    diff_id = parse_toon(diff_out)["summary"]["diff_id"]
    assert len(diff_id) == 64
    assert {row["layer"] for row in parse_toon(diff_out)["tables"]["layer_stats"]} == {
        "L0", "L1", "L2", "L3", "L4",
    }
    outputs.append(diff_out)

    dependent_calls = [
        (ual_cli.cmd_read_diff_layer, _base(
            kernel, diff_id=diff_id, layer="L0", offset=0, limit=20)),
        (ual_cli.cmd_verify_diff, _base(kernel, diff_id=diff_id)),
    ]
    for func, namespace in dependent_calls:
        code, out = _call(func, namespace, capsys)
        assert code == 0 and out.startswith(TOON_HEADER)
        outputs.append(out)

    code, plan_out = _call(ual_cli.cmd_plan_edit, _base(
        kernel, intent="tag", candidates=HERO, scope="/Game", max_ops=1,
        operations_file=None), capsys)
    assert code == 0 and plan_out.startswith(TOON_HEADER)
    plan_id = parse_toon(plan_out)["summary"]["plan_id"]
    outputs.append(plan_out)
    for func, namespace in [
        (ual_cli.cmd_apply_plan, _base(
            kernel, plan_id=plan_id, approved_by="reviewer",
            approvals="op-001", dry_run=True)),
        (ual_cli.cmd_verify_edit, _base(kernel, plan_id=plan_id)),
        (ual_cli.cmd_rollback_edit, _base(
            kernel, plan_id=plan_id, approved_by="reviewer",
            approvals="op-001.inverse", dry_run=True)),
    ]:
        code, out = _call(func, namespace, capsys)
        assert code == 0 and out.startswith(TOON_HEADER)
        outputs.append(out)

    plan_artifact = parse_toon(plan_out)["summary"]["artifact"]
    code, artifact_out = _call(ual_cli.cmd_read_artifact, _base(
        kernel, relative=plan_artifact, offset=0, limit=20), capsys)
    assert code == 0 and artifact_out.startswith(TOON_HEADER)
    outputs.append(artifact_out)

    from ual_mcp import engine_bridge

    payload_file = kernel.tmp / "import-gamespec.yaml"
    _write_yaml(payload_file, engine_bridge._self_test_import_payload())
    code, import_out = _call(ual_cli.cmd_import_gamespec, _base(
        kernel, payload="", payload_file=str(payload_file), dry_run=True,
        approved_by="", approvals=""), capsys)
    assert code == 0 and import_out.startswith(TOON_HEADER)
    outputs.append(import_out)

    code, spec_out = _call(ual_cli.cmd_expand_gamespec, _base(
        kernel, intent="fps 3c shooter", template="fps_3c"), capsys)
    assert code == 0 and spec_out.startswith(TOON_HEADER)
    game_spec_id = parse_toon(spec_out)["summary"]["game_spec_id"]
    outputs.append(spec_out)
    for func, namespace in [
        (ual_cli.cmd_run_validation_loop, _base(
            kernel, game_spec_id=game_spec_id, runner="offline", dry_run=True,
            approved_by="reviewer", approvals="RunValidationLoop")),
        (ual_cli.cmd_run_workflow, _base(
            kernel, intent="fps 3c shooter", dry_run=True,
            approved_by="reviewer", approvals="RunFps3CWorkflow")),
        (ual_cli.cmd_request_rescan, _base(
            kernel, asset_paths=HERO, reason="index consistency", dry_run=True,
            approved_by="reviewer", approvals="RequestRescan")),
        (ual_cli.cmd_get_lifecycle_events, _base(kernel, limit=20)),
    ]:
        code, out = _call(func, namespace, capsys)
        assert code == 0 and out.startswith(TOON_HEADER)
        outputs.append(out)

    combined = "".join(outputs)
    _assert_clean_output(combined)
    assert "yaml_cache:" not in combined
    assert "fingerprint_vec:" not in combined


def test_custom_rule_loading_from_builtin_extension_manifest(kernel, capsys):
    custom_root = kernel.tmp / "custom_project"
    _write_yaml(custom_root / "Config" / "UAL" / "policies" / "team.yaml", {
        "rules": [{
            "id": "custom.no_draft_names", "kind": "custom",
            "applies_to": ["*"], "extension": "no_draft_names",
            "severity": "error", "enforce": False, "enabled": True,
            "message": "draft/WIP assets must be reviewed before publishing",
        }],
    })
    draft = _record()
    draft.update({
        "asset_path": "/Game/Temp/BP_Draft",
        "logical_id": "Blueprint:BP_Draft",
        "content_hash": "sha1:BP_Draft",
    })
    stream = kernel.tmp / "draft-events.yaml"
    _write_yaml(stream, [draft], stream=True)
    code, build_out = _call(ual_cli.cmd_build, argparse.Namespace(
        demo=False, from_yaml=str(stream), index=str(kernel.index),
        incremental=False, project=str(custom_root), engine_cmd=None), capsys)
    assert code == 0 and "extension_payload" in build_out
    code, policy_out = _call(ual_cli.cmd_lint_policy, argparse.Namespace(
        index=str(kernel.index), project=str(custom_root), namespace="policies",
        limit=100, fail_on="error", strict=True), capsys)
    assert code == 1
    assert "custom.no_draft_names" in policy_out and "BP_Draft" in policy_out
    _assert_clean_output(build_out + policy_out)


def test_allowlist_gate_valid_invalid_expired_and_review_gating(kernel, capsys):
    valid_root = kernel.tmp / "allowlist_valid"
    bad_root = kernel.tmp / "allowlist_bad"
    _write_yaml(valid_root / "Config" / "UAL" / "allowlists" / "team.yaml", {
        "version": 1,
        "duplicates": [{
            "pair": [DUPLICATE_LHS, DUPLICATE_RHS], "reason": "reviewed variant",
            "owner": "test", "review_by": "2999-12-31",
        }],
        "references": [],
    })
    _write_yaml(bad_root / "Config" / "UAL" / "allowlists" / "team.yaml", {
        "version": 1,
        "duplicates": [
            {"pair": [DUPLICATE_LHS, DUPLICATE_RHS], "reason": "missing owner",
             "review_by": "2999-12-31"},
            {"pair": [DUPLICATE_LHS, DUPLICATE_RHS], "reason": "expired",
             "owner": "test", "review_by": "2000-01-01"},
        ],
        "references": [],
    })
    base = {
        "index": str(kernel.index), "project": str(kernel.project),
        "as_of": "2026-11-26", "limit": 20, "threshold": 0.95, "diff_only": False,
    }
    audit_code, audit_out = _call(ual_cli.cmd_allowlist_audit, argparse.Namespace(
        **base, allowlist_project=str(bad_root), approved_by="reviewer",
        review_ref="PR-1", strict_allowlist=True), capsys)
    audit_rows = parse_toon(audit_out)["tables"]["allowlist_issues"]
    assert audit_code == 1
    assert {"invalid", "expired"} <= {row["kind"] for row in audit_rows}

    bad_code, bad_out = _call(ual_cli.cmd_lint_duplicates, argparse.Namespace(
        **base, allowlist_project=str(bad_root), approved_by="reviewer",
        review_ref="PR-1", strict_allowlist=False), capsys)
    unreviewed_code, unreviewed_out = _call(ual_cli.cmd_lint_duplicates, argparse.Namespace(
        **base, allowlist_project=str(valid_root), approved_by="reviewer",
        review_ref="", strict_allowlist=False), capsys)
    duplicate_reviewed_code, duplicate_reviewed_out = _call(
        ual_cli.cmd_lint_duplicates, argparse.Namespace(
            **base, allowlist_project=str(valid_root), approved_by="reviewer",
            review_ref="PR-1", strict_allowlist=False), capsys)
    assert bad_code == 1 and "duplicate_pairs: 1" in bad_out
    assert unreviewed_code == 1 and "review" in unreviewed_out
    assert duplicate_reviewed_code == 0 and "duplicate_pairs: 0" in duplicate_reviewed_out

    # Reference findings can be suppressed by an exact asset-path rule or a
    # repository-relative scan-file rule, but only with both approvals.
    scan = kernel.tmp / "scan"
    scan.mkdir()
    (scan / "bad.yaml").write_text(
        "montage: /Game/Missing/AM_Hallucinated\nvfx: User.NotARealParam\n",
        encoding="utf-8",
    )
    reference_root = kernel.tmp / "allowlist_references"
    _write_yaml(reference_root / "Config" / "UAL" / "allowlists" / "team.yaml", {
        "version": 1,
        "duplicates": [],
        "references": [
            {"path": "/Game/Missing/AM_Hallucinated", "rule": "missing_asset",
             "reason": "runtime generated", "owner": "test", "review_by": "2999-12-31"},
            {"path": "scan/bad.yaml", "rule": "unknown_material_param",
             "value": "NotARealParam", "reason": "runtime parameter",
             "owner": "test", "review_by": "2999-12-31"},
        ],
    })
    verify_base = {
        "index": str(kernel.index), "project": str(kernel.tmp),
        "scan_dirs": str(scan), "limit": 20, "as_of": "2026-11-26",
    }
    no_allowlist_code, no_allowlist_out = _call(
        ual_cli.cmd_verify_references,
        argparse.Namespace(**verify_base, allowlist_project=None,
                           approved_by="", review_ref="", strict_allowlist=False),
        capsys,
    )
    reference_reviewed_code, reference_reviewed_out = _call(
        ual_cli.cmd_verify_references,
        argparse.Namespace(**verify_base, allowlist_project=str(reference_root),
                           approved_by="reviewer", review_ref="PR-1",
                           strict_allowlist=False),
        capsys,
    )
    reference_unreviewed_code, reference_unreviewed_out = _call(
        ual_cli.cmd_verify_references,
        argparse.Namespace(**verify_base, allowlist_project=str(reference_root),
                           approved_by="reviewer", review_ref="",
                           strict_allowlist=False),
        capsys,
    )
    assert no_allowlist_code == 1
    assert reference_reviewed_code == 0 and "issues: 0" in reference_reviewed_out
    assert reference_unreviewed_code == 1 and "review" in reference_unreviewed_out

    combined = "".join([
        audit_out, bad_out, unreviewed_out, duplicate_reviewed_out,
        no_allowlist_out, reference_reviewed_out, reference_unreviewed_out,
    ])
    _assert_clean_output(combined)
    duplicate_audit = yaml.safe_load(duplicate_reviewed_out)
    reference_audit = yaml.safe_load(reference_reviewed_out)
    assert duplicate_audit["kind"] == "duplicate_audit"
    assert duplicate_audit["duplicate_pairs"] == 0
    assert reference_audit["kind"] == "reference_audit"
    assert reference_audit["issues"] == 0


def test_apply_events_reads_canonical_streams_and_writes_index(kernel, capsys):
    staging = kernel.project / ".ual" / "staging"
    _write_yaml(staging / "events.yaml", [_record()], stream=True)
    _write_yaml(staging / "removed.yaml", [{
        "asset_path": HERO, "change_kind": "removed",
    }], stream=True)
    code, out = _call(ual_cli.cmd_apply_events, argparse.Namespace(
        index=str(kernel.index), project=str(kernel.project),
        events=ual_cli.DEFAULT_EVENTS, removed=ual_cli.DEFAULT_REMOVED), capsys)
    assert code == 0 and out.startswith(TOON_HEADER)
    assert ".ual/staging/events.yaml" in out
    assert ".ual/staging/removed.yaml" in out
    assert ".ual/audit/events.yaml" in out
    assert "OnIndexed" in out
    _assert_clean_output(out)
    assert "domain_spec:" not in out and "yaml_cache:" not in out
    con = db.open_readonly(kernel.index)
    try:
        paths = {row[0] for row in con.execute("SELECT asset_path FROM assets").fetchall()}
        assert "/Game/Events/BP_Indexed" in paths and HERO not in paths
        assert int(con.execute("SELECT COUNT(*) FROM extension_payload").fetchone()[0]) > 0
        assert db.search_assets(con, query="indexed event", limit=1)
    finally:
        con.close()


def test_benchmark_small_count(kernel, capsys):
    code, out = _call(ual_cli.cmd_benchmark, argparse.Namespace(
        project=str(kernel.project), count=8, incremental=True, dry_run=True), capsys)
    assert code == 0 and out.startswith(TOON_HEADER)
    parsed = parse_toon(out)
    assert parsed["summary"]["assets"] == "8"
    assert parsed["summary"]["incremental_measured"] == "true"
    assert len(parsed["tables"]["benchmark"]) == 2
    _assert_clean_output(out)


def test_cmd_self_test_passes(kernel, capsys):
    code, out = _call(ual_cli.cmd_self_test, argparse.Namespace(), capsys)
    assert code == 0
    assert out.startswith(TOON_HEADER)
    parsed = parse_toon(out)
    assert parsed["summary"]["result"] == "PASS"
    assert all(row["status"] == "PASS" for row in parsed["tables"]["self_test"])
    assert len(parsed["tables"]["self_test"]) >= 17
    _assert_clean_output(out)
