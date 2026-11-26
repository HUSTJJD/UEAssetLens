"""Canonical YAML artifacts for the UAL asset kernel.

This is the single YAML emitter for plans, diffs, GameSpecs, audit events and
rescan requests.  Callers never concatenate YAML strings by hand.  Artifacts
are addressed by repository-relative paths only: no local absolute paths,
timestamps or random identifiers are written into their content.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

MAX_PAGE_LINES = 200
ARTIFACT_PREFIX = ".ual"
_FORBIDDEN = ("..", ":", "\\")

_GUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\"']+")
_TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*")
_NOISE_KEY_RE = re.compile(r"(?im)^\s*(updated_at|timestamp|revision|session_id|ActorMetaData|FiBData)\s*:.*$")

_PAGE_WINDOWS_PATH_RE = re.compile(
    r"(?<!\w)[A-Za-z]:[\\/](?!n\b|r\b|t\b|b\b|f\b)[^\s\"',]+"
)
_PAGE_UNC_PATH_RE = re.compile(r"(?<!\w)\\\\\\\\[A-Za-z0-9_.-]+")
_PAGE_POSIX_PATH_RE = re.compile(r"(?<!\w)/(?!Game(?:/|$))[^\s\"',)\]]+")
_PAGE_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)?\b|\b\d{2}:\d{2}:\d{2}\b"
)
_PAGE_RANDOM_ID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b|\b[0-9a-fA-F]{32}\b"
)
_PAGE_NOISE_KEY_RE = re.compile(
    r"(?im)^\s*(?:updated_at|timestamp|created_at|generated_at|modified_at|"
    r"datetime|session_id|request_id|trace_id|correlation_id|uuid|guid|"
    r"random_id|nonce|client_id|run_id|job_id|local_path|absolute_path|"
    r"host_path|file_path|content_hash|ActorMetaData|FiBData)\s*:.*$"
)
_PAGE_WIRE_NAME_RE = re.compile(r"\bual_[A-Za-z0-9_]+\b")
_TODO_MARKER = "TODO" + "(human)"


def canonical_yaml(payload: Any) -> str:
    """Stable YAML with sorted keys, UTF-8 text and LF line endings."""
    return yaml.safe_dump(
        payload, sort_keys=True, allow_unicode=True,
        default_flow_style=False, width=100,
    ).replace("\r\n", "\n")


def canonical_text(payload: Any) -> str:
    """Backwards-compatible alias used by extension builtins."""
    return canonical_yaml(payload)


def canonical_yaml_stream(payloads: list[Any]) -> str:
    """Stable multi-document YAML, for staging/event streams only."""
    return yaml.safe_dump_all(
        payloads, sort_keys=True, allow_unicode=True, default_flow_style=False,
        explicit_start=True, width=100,
    ).replace("\r\n", "\n")


def digest(payload: Any) -> str:
    return hashlib.sha256(canonical_yaml(payload).encode("utf-8")).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_relative(relative: str | Path) -> str:
    """Reject absolute / escaping artifact paths before any filesystem write."""
    text = str(relative).replace("\\", "/")
    if not text or text.startswith("/") or any(part in text for part in _FORBIDDEN):
        raise ValueError(f"artifact path must be repository-relative: {text!r}")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts:
        raise ValueError("artifact path is empty")
    return "/".join(parts)


def write_artifact(root: Path, relative: str | Path, payload: Any) -> dict[str, str]:
    """Write canonical YAML under root; return only bounded metadata."""
    rel = safe_relative(relative)
    target = Path(root) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    text = canonical_yaml(payload)
    target.write_text(text, encoding="utf-8", newline="\n")
    return {"artifact": rel, "canonical_sha256": sha256_text(text)}


def read_artifact(root: Path, relative: str | Path) -> Any:
    rel = safe_relative(relative)
    text = (Path(root) / rel).read_text(encoding="utf-8")
    return yaml.safe_load(text)


def canonicalize_text(value: str) -> str:
    """Strip engine export noise so byte-for-byte diff artifacts are stable."""
    text = _GUID_RE.sub("<guid>", value)
    text = _WINDOWS_PATH_RE.sub("<local-path>", text)
    text = _TIMESTAMP_RE.sub("<timestamp>", text)
    text = _NOISE_KEY_RE.sub("", text)
    text = "".join(ch for ch in text if ord(ch) >= 0x20 or ch in "\n\t")
    return text.replace("\r\n", "\n").strip()


def page_lines(text: str, *, offset: int = 0, limit: int = 80) -> dict[str, Any]:
    """Bounded line page with a deterministic next cursor."""
    lines = text.splitlines()
    start = max(0, int(offset or 0))
    size = max(1, min(int(limit or 80), MAX_PAGE_LINES))
    page = lines[start: start + size]
    end = start + len(page)
    return {
        "offset": start,
        "limit": size,
        "returned": len(page),
        "total": len(lines),
        "truncated": end < len(lines),
        "next_cursor": str(end) if end < len(lines) else "",
        "lines": page,
    }


def _page_window(offset: Any, limit: Any) -> tuple[int, int]:
    try:
        start = max(0, int(offset))
    except (TypeError, ValueError):
        start = 0
    try:
        size = max(1, min(int(limit), MAX_PAGE_LINES))
    except (TypeError, ValueError):
        size = MAX_PAGE_LINES
    return start, size


def _artifact_page_location(root: Path, relative: str | Path) -> tuple[Path, str]:
    """Resolve one repository-relative ``.ual/`` path without leaking the root."""
    rel = safe_relative(str(relative).replace("\\", "/"))
    root_path = Path(root)
    is_ual_root = root_path.name == ARTIFACT_PREFIX
    if rel.startswith(ARTIFACT_PREFIX + "/"):
        inner = safe_relative(rel[len(ARTIFACT_PREFIX) + 1:])
        pointer = f"{ARTIFACT_PREFIX}/{inner}"
        base = root_path.parent if is_ual_root else root_path
        target = base / pointer
    elif is_ual_root:
        inner = rel
        pointer = f"{ARTIFACT_PREFIX}/{inner}"
        base = root_path.parent
        target = root_path / inner
    else:
        raise ValueError("artifact path must be under .ual/")
    resolved_base = (base / ARTIFACT_PREFIX).resolve()
    resolved_target = target.resolve()
    if not resolved_target.is_relative_to(resolved_base):
        raise ValueError("artifact path escapes .ual/")
    return resolved_target, pointer


def _string_has_noise(text: str) -> bool:
    if any(
        pattern.search(text)
        for pattern in (
            _PAGE_WINDOWS_PATH_RE,
            _PAGE_POSIX_PATH_RE,
            _PAGE_TIMESTAMP_RE,
            _PAGE_RANDOM_ID_RE,
            _PAGE_NOISE_KEY_RE,
            _PAGE_WIRE_NAME_RE,
        )
    ):
        return True
    return (
        "\\" in text
        or "file://" in text.casefold()
        or "content_hash" in text
        or _TODO_MARKER in text
    )


def _plain_data(value: Any, depth: int = 0) -> bool:
    if depth > 12:
        return False
    if value is None or isinstance(value, (int, float, bool)):
        return True
    if isinstance(value, str):
        return not _string_has_noise(value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str)
            and not _string_has_noise(key)
            and _plain_data(child, depth + 1)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return all(_plain_data(child, depth + 1) for child in value)
    return False


def _assert_clean_page_text(text: str) -> None:
    if any(
        pattern.search(text)
        for pattern in (
            _PAGE_TIMESTAMP_RE,
            _PAGE_RANDOM_ID_RE,
            _PAGE_NOISE_KEY_RE,
            _PAGE_WIRE_NAME_RE,
        )
    ) or "file://" in text.casefold() or "content_hash" in text or _TODO_MARKER in text:
        raise ValueError("artifact contains forbidden output markers")


def read_artifact_page(
    root: Path,
    relative: str | Path,
    *,
    offset: int = 0,
    limit: int = MAX_PAGE_LINES,
) -> str:
    """Return one bounded, canonical YAML page of a full ``.ual/`` artifact."""
    target, pointer = _artifact_page_location(root, relative)
    if not target.is_file():
        raise FileNotFoundError("canonical artifact is unavailable")
    try:
        parsed = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("artifact is not canonical YAML") from exc
    if not _plain_data(parsed):
        raise ValueError("artifact payload is not plain canonical data")
    canonical = canonical_yaml(parsed)
    _assert_clean_page_text(canonical)
    start, size = _page_window(offset, limit)
    page = page_lines(canonical, offset=start, limit=size)
    return canonical_yaml({
        "relative": pointer,
        "offset": page["offset"],
        "limit": page["limit"],
        "total_lines": page["total"],
        "next_cursor": page["next_cursor"],
        "sha256": sha256_text(canonical),
        "lines": list(page["lines"]),
    })
