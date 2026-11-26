"""Contract tests for bounded canonical artifact paging."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from ual_common import artifacts

WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"']+")
TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*")
GUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
RANDOM_ID = re.compile(r"\b[0-9a-fA-F]{32}\b")
TODO_MARKER = "TODO" + "(human)"
REQUIRED_PAGE_FIELDS = {
    "relative", "offset", "limit", "total_lines", "next_cursor", "sha256", "lines",
}
LARGE_ARTIFACTS = (
    ("plan", "plans/offline-plan.yaml"),
    ("gamespec", "gamespecs/offline-gamespec.yaml"),
    ("audit", "audit/events.yaml"),
)


def _assert_clean(text: str) -> None:
    assert not WINDOWS_PATH.search(text)
    assert not TIMESTAMP.search(text)
    assert not GUID.search(text)
    assert not RANDOM_ID.search(text)
    assert "/tmp/" not in text and "/home/" not in text
    assert TODO_MARKER not in text
    assert "content_hash" not in text
    assert "ual_" not in text
    assert "file://" not in text.casefold()


def _large_payload(kind: str, count: int = 240) -> dict[str, Any]:
    return {
        "kind": kind,
        "row_count": count,
        "rows": [
            {"id": f"row_{index:04d}", "value": index}
            for index in range(count)
        ],
    }


def _write_large(
    root: Path, relative: str, kind: str
) -> tuple[str, str]:
    payload = _large_payload(kind)
    artifacts.write_artifact(root, relative, payload)
    return artifacts.canonical_yaml(payload), f".ual/{relative}"


@pytest.mark.parametrize("kind,relative", LARGE_ARTIFACTS)
def test_large_artifacts_page_exactly_across_multiple_pages(
    tmp_path: Path, kind: str, relative: str
) -> None:
    root = tmp_path / ".ual"
    full_text, pointer = _write_large(root, relative, kind)
    total_lines = len(full_text.splitlines())
    assert total_lines > artifacts.MAX_PAGE_LINES

    collected: list[str] = []
    hashes: set[str] = set()
    pages = 0
    offset = 0
    while True:
        page_text = artifacts.read_artifact_page(
            tmp_path, pointer, offset=offset, limit=artifacts.MAX_PAGE_LINES
        )
        _assert_clean(page_text)
        page = yaml.safe_load(page_text)
        assert set(page) == REQUIRED_PAGE_FIELDS
        assert page["relative"] == pointer
        assert page["offset"] == offset
        assert page["limit"] == artifacts.MAX_PAGE_LINES
        assert page["total_lines"] == total_lines
        assert page["sha256"] == artifacts.sha256_text(full_text)
        hashes.add(page["sha256"])
        assert page["lines"] == full_text.splitlines()[offset:offset + page["limit"]]
        collected.extend(page["lines"])
        pages += 1
        cursor = page["next_cursor"]
        if not cursor:
            break
        assert cursor == str(offset + len(page["lines"]))
        offset = int(cursor)

    assert pages >= 2
    assert len(hashes) == 1
    assert len(collected) == total_lines
    assert "\n".join(collected) + "\n" == full_text


def test_page_output_is_byte_deterministic_and_hash_matches_full_artifact(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".ual"
    full_text, pointer = _write_large(root, LARGE_ARTIFACTS[0][1], "plan")
    first = artifacts.read_artifact_page(tmp_path, pointer)
    second = artifacts.read_artifact_page(tmp_path, pointer)
    assert first == second
    assert first.endswith("\n")
    _assert_clean(first)
    page = yaml.safe_load(first)
    assert page["sha256"] == artifacts.sha256_text(full_text)
    assert page["sha256"] == artifacts.sha256_text(
        artifacts.canonical_yaml(yaml.safe_load(full_text))
    )


def test_page_accepts_ual_root_with_bare_relative(tmp_path: Path) -> None:
    root = tmp_path / ".ual"
    _full_text, pointer = _write_large(root, LARGE_ARTIFACTS[1][1], "gamespec")
    bare = pointer[len(".ual/"):]
    from_root = artifacts.read_artifact_page(root, bare, limit=50)
    from_project = artifacts.read_artifact_page(tmp_path, pointer, limit=50)
    assert from_root == from_project
    assert yaml.safe_load(from_root)["relative"] == pointer


def test_next_cursor_past_end_and_clamped_limit(tmp_path: Path) -> None:
    root = tmp_path / ".ual"
    full_text, pointer = _write_large(root, LARGE_ARTIFACTS[2][1], "audit")
    total_lines = len(full_text.splitlines())
    first_text = artifacts.read_artifact_page(tmp_path, pointer, limit=1000)
    first = yaml.safe_load(first_text)
    assert first["limit"] == artifacts.MAX_PAGE_LINES
    assert first["next_cursor"] == str(artifacts.MAX_PAGE_LINES)
    past_text = artifacts.read_artifact_page(
        tmp_path, pointer, offset=total_lines + 10, limit=artifacts.MAX_PAGE_LINES
    )
    past = yaml.safe_load(past_text)
    assert past["offset"] == total_lines + 10
    assert past["lines"] == []
    assert past["next_cursor"] == ""
    assert past["total_lines"] == total_lines
    _assert_clean(past_text)


@pytest.mark.parametrize(
    "relative",
    (
        "/etc/passwd",
        "../plans/leak.yaml",
        "C:/temp/plan.yaml",
        r"\\server\share\plan.yaml",
        "Config/UAL/templates/fps_3c.yaml",
        "plans/offline-plan.yaml",
        ".ual/../plan.yaml",
        "/.ual/plans/offline-plan.yaml",
    ),
)
def test_path_traversal_and_non_ual_paths_are_rejected(
    tmp_path: Path, relative: str
) -> None:
    root = tmp_path / ".ual"
    _write_large(root, "plans/offline-plan.yaml", "plan")
    with pytest.raises(ValueError):
        artifacts.read_artifact_page(tmp_path, relative)


@pytest.mark.parametrize(
    "payload",
    (
        {"stamp": "2026-11-27T12:00:00Z"},
        {"path": "D:/tmp/plan.yaml"},
        {"path": r"\\server\share\plan.yaml"},
        {"identifier": "550e8400-e29b-41d4-a716-446655440000"},
        {"content_hash": "sha1:fixture"},
        {"wire": "ual_probe"},
        {"note": TODO_MARKER},
    ),
)
def test_noisy_artifacts_are_rejected(tmp_path: Path, payload: dict[str, Any]) -> None:
    root = tmp_path / ".ual"
    artifacts.write_artifact(root, "plans/noise.yaml", payload)
    with pytest.raises(ValueError):
        artifacts.read_artifact_page(tmp_path, ".ual/plans/noise.yaml")
