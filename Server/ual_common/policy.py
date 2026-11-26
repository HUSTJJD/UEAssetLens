"""Declarative asset policy engine (naming / directory / format / reference).

Rules are data, not code: they come from the four-layer preference resolver
``ual_common.settings`` and can be overridden per plugin, project or user.
Custom predicates are looked up in ``ExtensionRegistry`` under the ``policy``
namespace, which is how users extend the framework without editing its source.

The engine never writes anything. A rule is *reported* by default and only
blocks a gate when ``enforce: true`` and severity is ``error``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .registry import ExtensionRegistry
from .settings import ResolvedSettings
from .toon import Table, render_toon

VIOLATION_FIELDS = ["rule_id", "kind", "severity", "asset_path", "message"]
KINDS = ("naming", "directory", "format", "reference", "custom")
SEVERITIES = ("info", "warning", "error")


@dataclass(frozen=True)
class Violation:
    rule_id: str
    kind: str
    severity: str
    asset_path: str
    message: str
    enforce: bool = False

    @property
    def blocking(self) -> bool:
        return self.enforce and self.severity == "error"


def asset_name(asset: dict[str, Any]) -> str:
    explicit = asset.get("asset_name")
    if explicit:
        return str(explicit)
    return str(asset.get("asset_path", "")).rstrip("/").rsplit("/", 1)[-1]


def _applies(rule: dict[str, Any], asset: dict[str, Any]) -> bool:
    classes = rule.get("applies_to") or ["*"]
    if "*" in classes:
        return True
    return str(asset.get("asset_class", "")) in [str(c) for c in classes]


def _value_for(rule: dict[str, Any], asset: dict[str, Any]) -> str:
    target = rule.get("target", "asset_name")
    if target == "asset_name":
        return asset_name(asset)
    if target == "package_path":
        return str(asset.get("asset_path", ""))
    if target == "suffix":
        path = str(asset.get("asset_path", ""))
        return path.rsplit(".", 1)[-1] if "." in path else ""
    if target == "logical_id":
        return str(asset.get("logical_id", ""))
    return str(rule.get("target", ""))


def _pattern_violations(rule: dict[str, Any], asset: dict[str, Any]) -> list[str]:
    pattern = rule.get("pattern")
    if not pattern:
        return []
    expect_match = str(rule.get("expect", "match")) != "no_match"
    value = _value_for(rule, asset)
    try:
        matched = re.search(str(pattern), value) is not None
    except re.error as exc:
        return [f"invalid regex in rule: {exc}"]
    if matched != expect_match:
        return [str(rule.get("message") or f"value {value!r} violates {rule['id']}")]
    return []


def _format_violations(rule: dict[str, Any], asset: dict[str, Any]) -> list[str]:
    messages: list[str] = []
    path = str(asset.get("asset_path", ""))
    allowed = rule.get("allowed_suffixes")
    if allowed and path:
        suffix = path.rsplit(".", 1)[-1] if "." in path else ""
        if suffix not in [str(s) for s in allowed]:
            messages.append(str(rule.get("message") or f"suffix .{suffix} is not allowed"))

    tags = asset.get("tags")
    if tags is None:
        tags = list((asset.get("domain_spec") or {}).keys())
    tag_set = {str(t) for t in tags}
    for required in rule.get("required_tags") or []:
        if str(required) not in tag_set:
            messages.append(f"missing required tag/field: {required}")
    for forbidden in rule.get("forbidden_tags") or []:
        if str(forbidden) in tag_set:
            messages.append(f"forbidden tag/field: {forbidden}")

    max_bytes = rule.get("max_bytes")
    if max_bytes is not None and asset.get("size_bytes") is not None:
        if int(asset["size_bytes"]) > int(max_bytes):
            messages.append(f"size {asset['size_bytes']} exceeds {max_bytes} bytes")

    max_value = rule.get("max_tag_value_length")
    if max_value is not None:
        for key, value in (asset.get("domain_spec") or {}).items():
            if len(str(value)) > int(max_value):
                messages.append(f"domain_spec[{key}] exceeds {max_value} chars")
    return messages


def _reference_violations(rule: dict[str, Any], asset: dict[str, Any]) -> list[str]:
    messages: list[str] = []
    deps = [str(d) for d in (asset.get("dependency_paths") or [])]
    max_deps = rule.get("max_dependencies")
    if max_deps is not None and len(deps) > int(max_deps):
        messages.append(f"{len(deps)} dependencies exceed {max_deps}")
    for prefix in rule.get("forbidden_target_prefixes") or []:
        for dep in deps:
            if dep.startswith(str(prefix)):
                messages.append(f"forbidden dependency target: {dep}")
                break
    return messages


def _custom_violations(
    rule: dict[str, Any], asset: dict[str, Any], registry: ExtensionRegistry | None
) -> list[str]:
    name = rule.get("extension") or rule.get("id")
    if registry is None:
        return [f"custom rule requires an ExtensionRegistry: {name}"]
    matches = registry.resolve(
        str(asset.get("asset_class", "*")), namespace="policy"
    )
    for spec, impl in matches:
        if spec.name == name or spec.key == name:
            if not callable(impl):
                return [f"extension {name} is not callable"]
            outcome = impl(asset, rule)
            if outcome is True or outcome is None:
                return []
            if isinstance(outcome, str):
                return [outcome]
            if isinstance(outcome, (list, tuple)):
                return [str(item) for item in outcome]
    return [f"custom extension not registered: {name}"]


class PolicyEngine:
    def __init__(
        self,
        rules: Sequence[dict[str, Any]] | ResolvedSettings,
        registry: ExtensionRegistry | None = None,
    ) -> None:
        if isinstance(rules, ResolvedSettings):
            rules = rules.active_rules()
        self.rules = [
            rule for rule in rules
            if rule.get("enabled", True) and str(rule.get("kind", "")) in KINDS
        ]
        self.registry = registry

    def validate(self, asset: dict[str, Any]) -> list[Violation]:
        found: list[Violation] = []
        for rule in self.rules:
            if not _applies(rule, asset):
                continue
            kind = str(rule["kind"])
            if kind in ("naming", "directory"):
                messages = _pattern_violations(rule, asset)
            elif kind == "format":
                messages = _format_violations(rule, asset)
            elif kind == "reference":
                messages = _reference_violations(rule, asset)
            else:
                messages = _custom_violations(rule, asset, self.registry)
            severity = str(rule.get("severity", "warning"))
            if severity not in SEVERITIES:
                severity = "warning"
            for message in messages:
                found.append(Violation(
                    rule_id=str(rule["id"]),
                    kind=kind,
                    severity=severity,
                    asset_path=str(asset.get("asset_path", "")),
                    message=message,
                    enforce=bool(rule.get("enforce", False)),
                ))
        return sorted(found, key=lambda v: (v.rule_id, v.asset_path, v.message))

    def validate_many(self, assets: Iterable[dict[str, Any]]) -> list[Violation]:
        found: list[Violation] = []
        for asset in assets:
            found.extend(self.validate(asset))
        return sorted(found, key=lambda v: (v.rule_id, v.asset_path, v.message))


def render_report(violations: Sequence[Violation], *, limit: int = 100) -> str:
    """Bounded TOON report; full results stay queryable via validate_many()."""
    limit = max(1, min(int(limit or 100), 500))
    rows = [[
        v.rule_id, v.kind, v.severity, v.asset_path, v.message,
    ] for v in violations[:limit]]
    blocking = sum(1 for v in violations if v.blocking)
    return render_toon(
        [Table("violations", VIOLATION_FIELDS, rows)],
        summary={
            "total": len(violations),
            "blocking": blocking,
            "returned": len(rows),
            "truncated": "true" if len(violations) > limit else "false",
        },
    )


def has_blocking(violations: Sequence[Violation]) -> bool:
    return any(v.blocking for v in violations)
