"""Deterministic lifecycle audit events with bounded TOON reads.

Only payload key names and hook outcomes are persisted. Event ids are content
addresses derived from a sanitized payload basis; no clock, random identifier,
local path or raw payload appears in an artifact or report.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import artifacts, extensions
from .fields import _safe_data, _scrub_text
from .toon import Table, render_toon

EVENTS_REL = "audit/events.yaml"
ARTIFACT_POINTER = ".ual/" + EVENTS_REL
MAX_EVENT_LIMIT = 200
MAX_KEYS = 32
_NOISE_KEY_RE = re.compile(r"(?i)(updated[_-]?at|timestamp|session[_-]?id|absolute[_-]?path|local[_-]?path)")


def _clean_key(value: Any) -> str:
    return _scrub_text("" if value is None else str(value))


def _key_names(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    keys = {
        _clean_key(key) for key in payload
        if key is not None and not _NOISE_KEY_RE.search(str(key))
    }
    return sorted(key for key in keys if key)[:MAX_KEYS]


def _payload_basis(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {"value": artifacts.digest(_safe_data(payload))}
    basis: dict[str, str] = {}
    for raw_key, value in sorted(payload.items(), key=lambda pair: str(pair[0])):
        key = _clean_key(raw_key)
        if not key or _NOISE_KEY_RE.search(key):
            continue
        basis[key] = artifacts.digest(_safe_data(value))
    return basis


def _hook_outcomes(
    event: str, payload: Any, project_root: Path | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    loaded = extensions.load_registry(project_root=project_root)
    registry = getattr(loaded, "registry", loaded)
    safe_payload = payload if isinstance(payload, dict) else {}
    raw_outcomes: list[dict[str, Any]] = []
    stored_outcomes: list[dict[str, Any]] = []
    for spec, impl in registry.resolve("*", namespace="lifecycle", capability=event):
        spec_dict = {
            "name": str(spec.name),
            "namespace": str(spec.namespace),
            "schema": dict(getattr(spec, "schema", {}) or {}),
        }
        try:
            result = impl({"event": event, "payload": safe_payload}, spec_dict) if callable(impl) else {}
            raw_result = dict(result) if isinstance(result, dict) else {}
            raw_result.setdefault("hook", str(spec.name))
            raw_result.setdefault("event", event)
            raw_result.setdefault("status", "observed")
            raw_result.setdefault("payload_keys", _key_names(safe_payload))
            raw_outcomes.append(raw_result)
            raw_keys = raw_result.get("payload_keys") or []
            keys = sorted({
                _clean_key(key) for key in raw_keys
                if key is not None and not _NOISE_KEY_RE.search(str(key))
            })[:MAX_KEYS]
            stored_outcomes.append({
                "hook": _clean_key(raw_result.get("hook") or spec.name),
                "status": _clean_key(raw_result.get("status") or "observed"),
                "payload_keys": keys,
            })
        except Exception:
            raw_outcomes.append({
                "hook": str(spec.name), "event": event,
                "status": "error", "payload_keys": [],
            })
            stored_outcomes.append({
                "hook": _clean_key(spec.name),
                "status": "error",
                "payload_keys": [],
            })
    stored_outcomes.sort(key=lambda row: (row["hook"], row["status"], tuple(row["payload_keys"])))
    return raw_outcomes, stored_outcomes


def _event_id(
    event: str, payload: Any, raw_hooks: list[dict[str, Any]], stored_hooks: list[dict[str, Any]]
) -> str:
    try:
        return artifacts.digest({
            "event": event,
            "payload": {} if payload is None else payload,
            "hooks": raw_hooks,
        })
    except Exception:
        return artifacts.digest({
            "event": event,
            "payload": _payload_basis(payload),
            "hooks": stored_hooks,
        })


def _compact_event(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    event_id = _clean_key(raw.get("event_id"))
    if not event_id:
        return None
    keys = raw.get("payload_keys") or []
    if not isinstance(keys, (list, tuple)):
        keys = []
    clean_keys = sorted({
        _clean_key(key) for key in keys
        if key is not None and not _NOISE_KEY_RE.search(str(key))
    })[:MAX_KEYS]
    hooks = raw.get("hooks") or []
    if not isinstance(hooks, list):
        hooks = []
    statuses = sorted({
        _clean_key(hook.get("status") or "observed")
        for hook in hooks if isinstance(hook, dict)
    })
    return {
        "event_id": event_id,
        "event": _clean_key(raw.get("event")),
        "payload_keys": clean_keys,
        "hooks": [{
            "hook": _clean_key(hook.get("hook")),
            "status": _clean_key(hook.get("status") or "observed"),
            "payload_keys": sorted({
                _clean_key(key) for key in (hook.get("payload_keys") or [])
                if key is not None and not _NOISE_KEY_RE.search(str(key))
            })[:MAX_KEYS],
        } for hook in hooks if isinstance(hook, dict)],
        "hook_count": len(hooks),
        "status": " | ".join(statuses)[:MAX_KEYS * 4],
    }


def _existing_events(root: Path) -> list[dict[str, Any]]:
    if not (root / EVENTS_REL).is_file():
        return []
    try:
        data = artifacts.read_artifact(root, EVENTS_REL)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    compacted = [_compact_event(item) for item in data]
    return [item for item in compacted if item is not None]


def emit(event: Any, payload: Any, *, project_root: Path | None = None, root: Path = Path(".ual")) -> dict[str, Any]:
    """Append one deterministic audit event through the canonical YAML writer."""
    root_path = Path(root or Path(".ual"))
    event_text = _clean_key(event)
    raw_hooks, hooks = _hook_outcomes(event_text, payload, project_root)
    event_id = _event_id(event_text, payload, raw_hooks, hooks)
    key_names = _key_names(payload)
    stored = _compact_event({
        "event_id": event_id,
        "event": event_text,
        "payload_keys": key_names,
        "hooks": hooks,
    }) or {
        "event_id": event_id, "event": event_text, "payload_keys": [],
        "hooks": [], "hook_count": 0, "status": "",
    }
    by_id = {item["event_id"]: item for item in _existing_events(root_path)}
    by_id[event_id] = stored
    events_data = [by_id[key] for key in sorted(by_id)]
    meta = artifacts.write_artifact(root_path, EVENTS_REL, events_data)
    return {
        "event_id": event_id,
        "event": event_text,
        "payload_keys": len(key_names),
        "hooks": len(hooks),
        "events": len(events_data),
        "artifact": ARTIFACT_POINTER,
        "canonical_sha256": meta["canonical_sha256"],
    }


def events(*, root: Path = Path(".ual"), limit: int = 50) -> str:
    """Bounded TOON report of the repository-relative lifecycle audit artifact."""
    root_path = Path(root or Path(".ual"))
    rows = _existing_events(root_path)
    return render_events(rows, limit=limit)


def render_events(rows: list[dict[str, Any]], *, limit: int = 50) -> str:
    """Pure bounded TOON; raw payloads are never accepted as output columns."""
    source = list(rows or [])
    ordered = sorted(source, key=lambda row: str(_row_value(row, "event_id")))
    size = max(1, min(int(limit or 50), MAX_EVENT_LIMIT))
    shown = ordered[:size]
    table_rows = [[
        _clean_key(_row_value(row, "event_id")),
        _clean_key(_row_value(row, "event")),
        _clean_key(_row_value(row, "status")),
        int(_row_value(row, "hook_count", 0) or 0),
        _clean_key(_row_value(row, "payload_keys")),
    ] for row in shown]
    return render_toon(
        [Table("lifecycle_events", ["event_id", "event", "status", "hook_count", "payload_keys"], table_rows)],
        summary={
            "total": len(ordered),
            "returned": len(table_rows),
            "truncated": "true" if len(ordered) > len(table_rows) else "false",
            "limit": size,
        },
    )


def _row_value(row: Any, key: str, default: Any = "") -> Any:
    if isinstance(row, dict):
        value = row.get(key, default)
    else:
        value = getattr(row, key, default)
    if isinstance(value, (list, tuple)):
        return " | ".join(_clean_key(item) for item in value[:MAX_KEYS])
    return value
