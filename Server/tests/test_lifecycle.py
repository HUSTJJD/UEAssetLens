"""Deterministic lifecycle audit artifacts and bounded TOON reports."""
from __future__ import annotations

from pathlib import Path

from ual_common import artifacts, lifecycle
from ual_common.toon import parse_toon


def _payload(secret: str) -> dict:
    return {
        "asset_path": "/Game/Props/SM_Chair",
        "approved_by": "reviewer",
        "updated_at": "2026-11-26T12:00:00Z",
        "timestamp": "2026-11-26T12:00:00Z",
        "local_path": "C:\\Temp\\index.duckdb",
        "secret": secret,
    }


def test_emit_is_deterministic_and_never_persists_raw_payload(tmp_path: Path) -> None:
    root = tmp_path / ".ual"
    first = lifecycle.emit("OnIndexed", _payload("first-secret"),
                           project_root=tmp_path, root=root)
    same = lifecycle.emit("OnIndexed", _payload("first-secret"),
                          project_root=tmp_path, root=root)
    assert first["event_id"] == same["event_id"]
    assert first["artifact"] == ".ual/audit/events.yaml"
    assert same["events"] == 1
    assert first["hooks"] >= 1
    assert "payload_keys" in first

    different = lifecycle.emit("OnIndexed", _payload("second-secret"),
                               project_root=tmp_path, root=root)
    assert different["event_id"] != first["event_id"]
    assert different["events"] == 2

    event_path = root / "audit" / "events.yaml"
    text = event_path.read_text(encoding="utf-8")
    data = artifacts.read_artifact(root, "audit/events.yaml")
    assert isinstance(data, list)
    assert [item["event_id"] for item in data] == sorted(item["event_id"] for item in data)
    assert "first-secret" not in text and "second-secret" not in text
    assert "C:\\Temp" not in text and "D:/" not in text
    assert "2026-11-26" not in text and "updated_at" not in text
    assert "raw_payload" not in text
    for item in data:
        assert set(item) == {"event_id", "event", "payload_keys", "hooks", "hook_count", "status"}
        assert all("first-secret" not in key and "second-secret" not in key
                   for key in item["payload_keys"])
        assert all(set(hook) == {"hook", "status", "payload_keys"} for hook in item["hooks"])


def test_events_and_render_events_are_bounded_toon(tmp_path: Path) -> None:
    root = tmp_path / ".ual"
    empty = lifecycle.events(root=root)
    parsed_empty = parse_toon(empty)
    assert empty.startswith("# UEAssetLens TOON v1 (Zero-VLM)")
    assert parsed_empty["summary"]["total"] == "0"
    assert parsed_empty["tables"]["lifecycle_events"] == []

    lifecycle.emit("OnPlanned", {"asset_path": "/Game/Props/SM_Chair", "note": "private"},
                   project_root=tmp_path, root=root)
    report = lifecycle.events(root=root, limit=50)
    parsed = parse_toon(report)
    assert report.startswith("# UEAssetLens TOON v1 (Zero-VLM)")
    assert parsed["summary"]["total"] == "1"
    assert len(parsed["tables"]["lifecycle_events"]) == 1
    assert "private" not in report
    assert "updated_at" not in report and "C:\\" not in report
    assert "2026-" not in report

    rows = [{
        "event_id": "b", "event": "OnCommitted", "status": "observed",
        "hook_count": 1, "payload_keys": ["asset_path"],
    }, {
        "event_id": "a", "event": "OnIndexed", "status": "observed",
        "hook_count": 1, "payload_keys": ["asset_path"],
    }]
    bounded = lifecycle.render_events(rows, limit=1)
    parsed_bounded = parse_toon(bounded)
    assert parsed_bounded["summary"]["truncated"] == "true"
    assert parsed_bounded["tables"]["lifecycle_events"][0]["event_id"] == "a"
    assert bounded == lifecycle.render_events(list(reversed(rows)), limit=1)
