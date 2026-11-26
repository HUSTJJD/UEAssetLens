"""Allowlist validation, expiry, review gating and bounded reporting."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from ual_common import allowlist
from ual_common.toon import parse_toon


def _write_config(root: Path, data: dict) -> None:
    directory = root / "Config" / "UAL" / "allowlists"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "team.yaml").write_text(
        yaml.safe_dump(data, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )


def _entry(**overrides) -> dict:
    entry = {
        "reason": "reviewed false positive",
        "owner": "art-team",
        "review_by": date(2026, 12, 31),
    }
    entry.update(overrides)
    return entry


def _config() -> dict:
    return {
        "version": 1,
        "duplicates": [
            _entry(pair=["/Game/Props/SM_Chair_A", "/Game/Props/SM_Chair_A_Painted"]),
            _entry(pair=["/Game/A/Expired", "/Game/B/Expired"], review_by=date(2020, 1, 1)),
            {"pair": ["/Game/A/Invalid", "/Game/B/Invalid"], "reason": "missing owner"},
        ],
        "references": [
            _entry(path="/Game/UI/DebugOnly/WBP_Probe", rule="missing_asset", value="Probe"),
            _entry(path="/Game/UI/DebugOnly/*", rule="missing_asset", value="Wildcard"),
            _entry(path="/Game/Disabled", rule="missing_asset", value="Off", enabled=False),
        ],
    }


def test_valid_invalid_expired_review_gating_and_unordered_pair(tmp_path: Path) -> None:
    _write_config(tmp_path, _config())
    resolved = allowlist.load_allowlist(project_root=tmp_path, as_of=date(2026, 11, 26))
    kinds = {(issue["rule_id"], issue["kind"]) for issue in resolved.issues()}
    assert ("duplicates[1]", "expired") in kinds
    assert ("duplicates[2]", "invalid") in kinds
    assert ("references[1]", "invalid") in kinds
    assert all({"rule_id", "kind", "message"} == set(issue) for issue in resolved.issues())

    lhs = "/Game/Props/SM_Chair_A"
    rhs = "/Game/Props/SM_Chair_A_Painted"
    assert resolved.suppress_duplicate(lhs, rhs, approved_by="reviewer", review_ref="PR-1") is True
    assert resolved.suppress_duplicate(rhs, lhs, approved_by="reviewer", review_ref="PR-1") is True
    assert resolved.suppress_duplicate("/Game/A/Expired", "/Game/B/Expired",
                                       approved_by="reviewer", review_ref="PR-1") is False
    assert resolved.suppress_duplicate("/Game/A/Invalid", "/Game/B/Invalid",
                                       approved_by="reviewer", review_ref="PR-1") is False
    assert resolved.suppress_duplicate(lhs, rhs, approved_by="", review_ref="PR-1") is False
    assert ("allowlist.review_gate", "review") in {
        (issue["rule_id"], issue["kind"]) for issue in resolved.issues()
    }

    assert resolved.suppress_reference(
        "missing_asset", "Probe", "/Game/UI/DebugOnly/WBP_Probe",
        approved_by="reviewer", review_ref="PR-1",
    ) is True
    assert resolved.suppress_reference(
        "missing_asset", "Other", "/Game/UI/DebugOnly/WBP_Probe",
        approved_by="reviewer", review_ref="PR-1",
    ) is False
    assert resolved.suppress_reference(
        "other_rule", "Probe", "/Game/UI/DebugOnly/WBP_Probe",
        approved_by="reviewer", review_ref="PR-1",
    ) is False
    assert resolved.suppress_reference(
        "missing_asset", "Probe", "/Game/UI/DebugOnly/Other",
        approved_by="reviewer", review_ref="PR-1",
    ) is False
    assert resolved.suppress_reference(
        "missing_asset", "Wildcard", "/Game/UI/DebugOnly/Any",
        approved_by="reviewer", review_ref="PR-1",
    ) is False
    assert resolved.suppress_reference(
        "missing_asset", "Off", "/Game/Disabled",
        approved_by="reviewer", review_ref="PR-1",
    ) is False


def test_default_is_empty_and_bad_version_never_suppresses(tmp_path: Path) -> None:
    clean = allowlist.load_allowlist(project_root=tmp_path / "clean", as_of=date(2026, 11, 26))
    assert clean.version == 1
    assert clean.duplicates == [] and clean.references == []
    assert clean.issues() == []

    _write_config(tmp_path, {
        "version": 2,
        "duplicates": [_entry(pair=["/Game/A", "/Game/B"])],
        "references": [],
    })
    bad_version = allowlist.load_allowlist(project_root=tmp_path, as_of=date(2026, 11, 26))
    assert ("allowlist.version", "config") in {
        (issue["rule_id"], issue["kind"]) for issue in bad_version.issues()
    }
    assert bad_version.duplicates == []
    assert bad_version.suppress_duplicate("/Game/A", "/Game/B",
                                          approved_by="reviewer", review_ref="PR-2") is False


def test_report_is_bounded_toon_without_clock_or_local_paths(tmp_path: Path) -> None:
    _write_config(tmp_path, _config())
    resolved = allowlist.load_allowlist(project_root=tmp_path, as_of=date(2026, 11, 26))
    report = resolved.report(limit=1)
    parsed = parse_toon(report)
    assert report.startswith("# UEAssetLens TOON v1 (Zero-VLM)")
    assert parsed["summary"]["truncated"] == "true"
    assert len(parsed["tables"]["allowlist_issues"]) == 1
    assert "2026-" not in report and "2020-" not in report
    assert "C:\\" not in report and "D:/" not in report
    assert "updated_at" not in report

    custom = allowlist.render_report([
        {"rule_id": "a", "kind": "invalid", "message": "first"},
        {"rule_id": "b", "kind": "expired", "message": "second"},
    ], limit=1)
    assert parse_toon(custom)["summary"]["truncated"] == "true"
    assert custom == allowlist.render_report([
        {"rule_id": "b", "kind": "expired", "message": "second"},
        {"rule_id": "a", "kind": "invalid", "message": "first"},
    ], limit=1)
