"""Versioned, review-gated allowlist for duplicate and reference gates.

Invalid, expired or unreviewed entries never suppress a gate. Issue reports are
bounded TOON and contain no source paths, clock values, reviewers or local paths.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from . import artifacts, settings
from .toon import Table, render_toon

MAX_REPORT_LIMIT = 200
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_OUTPUT_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_UNIX_LOCAL_PATH_RE = re.compile(r"(?<![\w/])/(?:home|Users|tmp|var)/[^\s\"']+")
REVIEW_GATE_ISSUE = ("allowlist.review_gate", "review", "suppression requires approved_by and review_ref")


def _clean(value: Any) -> str:
    text = artifacts.canonicalize_text("" if value is None else str(value))
    text = _UNIX_LOCAL_PATH_RE.sub("<local-path>", text)
    return _OUTPUT_DATE_RE.sub("<date>", text)


@dataclass(frozen=True)
class _Entry:
    rule_id: str
    kind: str
    rule: str = ""
    pair: tuple[str, str] = ()
    path: str = ""
    value: str = ""
    has_value: bool = False


@dataclass
class Allowlist:
    version: int = 1
    as_of: date = field(default_factory=date.today)
    duplicates: list[_Entry] = field(default_factory=list)
    references: list[_Entry] = field(default_factory=list)
    _config_issues: list[dict[str, str]] = field(default_factory=list)
    _call_issues: set[tuple[str, str, str]] = field(default_factory=set)

    def issues(self) -> list[dict[str, str]]:
        rows = list(self._config_issues)
        rows.extend({
            "rule_id": rule_id, "kind": kind, "message": message,
        } for rule_id, kind, message in self._call_issues)
        unique = {
            (str(row["rule_id"]), str(row["kind"]), str(row["message"])): row
            for row in rows
        }
        return [unique[key] for key in sorted(unique)]

    def _review_allowed(self, approved_by: str, review_ref: str) -> bool:
        if str(approved_by or "").strip() and str(review_ref or "").strip():
            return True
        self._call_issues.add(REVIEW_GATE_ISSUE)
        return False

    def suppress_duplicate(
        self, lhs: str, rhs: str, *, approved_by: str = "", review_ref: str = ""
    ) -> bool:
        if not self._review_allowed(approved_by, review_ref):
            return False
        wanted = tuple(sorted((str(lhs or ""), str(rhs or ""))))
        return any(entry.pair == wanted for entry in self.duplicates)

    def suppress_reference(
        self, kind: str, value: str, path: str, *,
        approved_by: str = "", review_ref: str = "",
    ) -> bool:
        if not self._review_allowed(approved_by, review_ref):
            return False
        wanted_rule, wanted_value, wanted_path = str(kind or ""), str(value or ""), str(path or "")
        for entry in self.references:
            if entry.rule != wanted_rule or entry.path != wanted_path:
                continue
            if entry.has_value and entry.value != wanted_value:
                continue
            return True
        return False

    def report(self, *, limit: int = 100) -> str:
        return render_report(self.issues(), limit=limit)


def _issue(rule_id: str, kind: str, message: str) -> dict[str, str]:
    return {
        "rule_id": _clean(rule_id),
        "kind": _clean(kind),
        "message": _clean(message),
    }


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not _ISO_DATE_RE.fullmatch(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _as_of(value: Any) -> date:
    return _parse_date(value) or date.today()


def _enabled(entry: dict[str, Any]) -> bool:
    value = entry.get("enabled", True)
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off")
    return bool(value)


def _contains_wildcard(value: str) -> bool:
    return "*" in value or "?" in value


def _required(
    entry: Any, rule_id: str, as_of_value: date, issues: list[dict[str, str]]
) -> bool:
    if not isinstance(entry, dict):
        issues.append(_issue(rule_id, "invalid", "allowlist entry must be a mapping"))
        return False
    valid = True
    if not str(entry.get("reason") or "").strip() or not str(entry.get("owner") or "").strip():
        issues.append(_issue(rule_id, "invalid", "allowlist entry is missing review metadata"))
        valid = False
    review_by = _parse_date(entry.get("review_by"))
    if review_by is None:
        issues.append(_issue(rule_id, "invalid", "allowlist entry requires an ISO review date"))
        valid = False
    elif review_by < as_of_value:
        issues.append(_issue(rule_id, "expired", "allowlist entry is expired; gate stays active"))
        valid = False
    return valid


def _load_pair(
    index: int, raw: Any, as_of_value: date, issues: list[dict[str, str]]
) -> _Entry | None:
    rule_id = f"duplicates[{index}]"
    if not _required(raw, rule_id, as_of_value, issues) or not _enabled(raw):
        return None
    pair = raw.get("pair")
    if (not isinstance(pair, (list, tuple)) or len(pair) != 2 or
            any(not isinstance(item, (str, int, float)) for item in pair)):
        issues.append(_issue(rule_id, "invalid", "duplicate entry requires a two-item pair"))
        return None
    lhs, rhs = str(pair[0]), str(pair[1])
    if _contains_wildcard(lhs) or _contains_wildcard(rhs):
        issues.append(_issue(rule_id, "invalid", "duplicate pair cannot use a wildcard"))
        return None
    return _Entry(rule_id=rule_id, kind="duplicate", pair=tuple(sorted((lhs, rhs))))


def _load_reference(
    index: int, raw: Any, as_of_value: date, issues: list[dict[str, str]]
) -> _Entry | None:
    rule_id = f"references[{index}]"
    if not _required(raw, rule_id, as_of_value, issues) or not _enabled(raw):
        return None
    rule, path = str(raw.get("rule") or ""), str(raw.get("path") or "")
    has_value = "value" in raw and raw.get("value") is not None
    value = str(raw.get("value") or "") if has_value else ""
    if (not rule or not path or _contains_wildcard(rule) or
            _contains_wildcard(path) or (has_value and _contains_wildcard(value))):
        issues.append(_issue(rule_id, "invalid", "reference entry requires exact rule and path"))
        return None
    return _Entry(rule_id=rule_id, kind="reference", rule=rule, path=path,
                  value=value, has_value=has_value)


def load_allowlist(*, project_root: Any = None, as_of: Any = None) -> Allowlist:
    """Resolve the versioned allowlist across the four preference layers."""
    resolved = settings.load_namespace("allowlists", project_root=project_root)
    data = dict(resolved.data or {})
    as_of_value = _as_of(as_of)
    issues: list[dict[str, str]] = []
    version = data.get("version")
    version_ok = (
        (isinstance(version, int) and not isinstance(version, bool) and version == 1)
        or (isinstance(version, str) and version.strip() == "1")
    )
    if not version_ok:
        issues.append(_issue("allowlist.version", "config", "unsupported allowlist version; gates remain active"))

    duplicates_raw = data.get("duplicates", [])
    references_raw = data.get("references", [])
    if not isinstance(duplicates_raw, list):
        issues.append(_issue("allowlist.duplicates", "config", "duplicates must be a list"))
        duplicates_raw = []
    if not isinstance(references_raw, list):
        issues.append(_issue("allowlist.references", "config", "references must be a list"))
        references_raw = []

    duplicates: list[_Entry] = []
    references: list[_Entry] = []
    if version_ok:
        for index, raw in enumerate(duplicates_raw):
            entry = _load_pair(index, raw, as_of_value, issues)
            if entry is not None:
                duplicates.append(entry)
        for index, raw in enumerate(references_raw):
            entry = _load_reference(index, raw, as_of_value, issues)
            if entry is not None:
                references.append(entry)
    return Allowlist(
        version=1 if version_ok else -1,
        as_of=as_of_value,
        duplicates=duplicates,
        references=references,
        _config_issues=issues,
    )


def _row_value(row: Any, key: str) -> str:
    if isinstance(row, dict):
        value = row.get(key, "")
    else:
        value = getattr(row, key, "")
    return _clean(value)


def render_report(issues: list[dict[str, str]], *, limit: int = 100) -> str:
    """Pure bounded TOON for allowlist validation and review issues."""
    source = list(issues or [])
    rows = [(
        _row_value(row, "rule_id"),
        _row_value(row, "kind"),
        _row_value(row, "message"),
    ) for row in source]
    rows.sort()
    size = max(1, min(int(limit or 100), MAX_REPORT_LIMIT))
    shown = rows[:size]
    return render_toon(
        [Table("allowlist_issues", ["rule_id", "kind", "message"],
               [list(row) for row in shown])],
        summary={
            "total": len(rows),
            "returned": len(shown),
            "truncated": "true" if len(rows) > len(shown) else "false",
            "limit": size,
        },
    )
