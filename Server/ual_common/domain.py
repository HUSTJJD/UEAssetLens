"""Declarative Python M2 reference extractor for typed domain descriptors.

Family vocabulary lives in ``Config/UAL/fields/*.yaml`` and resolves through the
four preference layers. This module only projects values already present in a
Core Record's ``domain_spec`` / ``interface_spec`` maps or its declared identity.
It performs no UE reflection, asset loading, model inference or database access.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from . import artifacts, settings
from .fields import VALUE_TYPES, coerce_value, _scrub_text

MAX_DESCRIPTORS = 200


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = _merge(current, value)
        else:
            result[key] = value
    return result


def _resolved(config: Any) -> settings.ResolvedSettings:
    """Accept None, a dict, a YAML file, a project root or ResolvedSettings."""
    if isinstance(config, settings.ResolvedSettings):
        return config
    if config is None:
        return settings.load_namespace("fields", project_root=settings.find_project_root())
    if isinstance(config, dict):
        if "data" in config and isinstance(config.get("data"), dict):
            rules = config.get("rules") if isinstance(config.get("rules"), list) else []
            return settings.ResolvedSettings(data=dict(config["data"]), rules=list(rules))
        return settings.ResolvedSettings(data=dict(config))
    path = Path(config)
    if path.is_file():
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(doc, dict):
            return settings.ResolvedSettings()
        rules = [dict(item) for item in doc.get("rules", []) if isinstance(item, dict)] \
            if isinstance(doc.get("rules"), list) else []
        data = {key: value for key, value in doc.items() if key != "rules"}
        return settings.ResolvedSettings(data=data, rules=rules)
    root = path
    if path.name == "fields" and path.parent.name == "UAL" and len(path.parents) >= 3:
        root = path.parents[2]
    return settings.load_namespace("fields", project_root=root)


def _data(resolved: settings.ResolvedSettings) -> dict[str, Any]:
    """Merge data-style config and optional rule-style family overrides."""
    data = dict(resolved.data or {})
    families = data.get("families")
    if not isinstance(families, dict):
        families = {}
        data["families"] = families
    for rule in resolved.rules or []:
        if not isinstance(rule, dict):
            continue
        rule_families = rule.get("families")
        if isinstance(rule_families, dict):
            for name, body in rule_families.items():
                if isinstance(body, dict):
                    families[str(name)] = _merge(families.get(str(name), {}), body)
        if "fields" not in rule and "family" not in rule:
            continue
        explicit = rule.get("family") or rule.get("name")
        name = str(explicit or rule.get("id") or "rule")
        if not explicit and "." in name:
            name = name.rsplit(".", 1)[-1]
        body = {key: value for key, value in rule.items() if key not in ("id", "rules")}
        families[name] = _merge(families.get(name, {}), body)
    return data


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        try:
            return {str(key): item for key, item in value}
        except (TypeError, ValueError):
            return {}
    return {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _enabled(value: Any) -> bool:
    item = value.get("enabled", True) if isinstance(value, dict) else True
    if isinstance(item, str):
        return item.strip().lower() not in ("false", "0", "no", "off")
    return bool(item)


def _family_entries(data: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    raw = data.get("families") or {}
    entries: list[tuple[str, dict[str, Any]]] = []
    if isinstance(raw, dict):
        for name in sorted(raw, key=str):
            if isinstance(raw[name], dict):
                entries.append((str(name), dict(raw[name])))
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("id") or "family")
                entries.append((name, dict(item)))
    return entries


def _field_entries(family: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    raw = family.get("fields") or {}
    entries: list[tuple[str, dict[str, Any]]] = []
    if isinstance(raw, dict):
        for name in sorted(raw, key=str):
            if isinstance(raw[name], dict):
                spec = dict(raw[name])
                spec.setdefault("name", str(name))
                entries.append((str(name), spec))
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                spec = dict(item)
                name = str(spec.get("name") or spec.get("key") or "field")
                spec["name"] = name
                entries.append((name, spec))
    return entries


def _source_container(record: dict[str, Any], source: str) -> dict[str, Any]:
    if source in ("record", "asset", "identity"):
        return record
    return _as_mapping(record.get(source))


def _family_hint(record: dict[str, Any], data: dict[str, Any]) -> str:
    defaults = _as_mapping(data.get("defaults"))
    key = str(defaults.get("family_key", ""))
    if not key:
        return ""
    return _scrub_text(_as_mapping(record.get("domain_spec")).get(key, ""))


def _matches_family(
    name: str,
    family: dict[str, Any],
    asset_class: str,
    data: dict[str, Any],
    record: dict[str, Any],
) -> tuple[bool, bool]:
    applies = tuple(str(item) for item in _as_list(family.get("applies_to") or ("*",)))
    wildcard = "*" in applies
    aliases = _as_mapping(data.get("aliases"))
    alias = str(aliases.get(asset_class, ""))
    hint = _family_hint(record, data)
    exact = (
        asset_class in applies
        or name in applies
        or (alias and (alias == name or alias in applies))
        or (hint and hint.lower() in (name.lower(), alias.lower()))
    )
    return wildcard, bool(exact and asset_class != "*" and not wildcard)


def extract_domain_fields(record: dict[str, Any], config: Any = None) -> list[dict[str, Any]]:
    """Project one record into deterministic typed field descriptors."""
    resolved = _resolved(config)
    data = _data(resolved)
    defaults = _as_mapping(data.get("defaults"))
    asset_class = _scrub_text((record or {}).get("asset_class", ""))
    record_map = record if isinstance(record, dict) else {}
    candidates: list[tuple[int, int, str, str, dict[str, Any], str, bool, str]] = []

    for family_name, family in _family_entries(data):
        if not _enabled(family):
            continue
        wildcard, exact = _matches_family(family_name, family, asset_class, data, record_map)
        if not wildcard and not exact:
            continue
        family_priority = int(family.get("priority", defaults.get("priority", 0)) or 0)
        for field_name, spec in _field_entries(family):
            if not _enabled(spec):
                continue
            namespace = _scrub_text(spec.get("namespace") or family.get("namespace") or defaults.get("namespace") or "field")
            name = _scrub_text(spec.get("name") or field_name)
            if not name:
                continue
            item_source = _scrub_text(spec.get("source") or family.get("source") or defaults.get("source") or "domain_spec")
            priority = int(spec.get("priority", family_priority) or 0)
            candidates.append((int(exact), priority, namespace, name, spec, item_source, wildcard, _scrub_text(family_name)))

    candidates.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
    descriptors: dict[tuple[str, str], dict[str, Any]] = {}
    for exact_rank, _priority, namespace, name, spec, item_source, wildcard, family_name in candidates:
        row_key = (namespace, name)
        if row_key in descriptors or len(descriptors) >= MAX_DESCRIPTORS:
            continue
        value_type = str(spec.get("type") or spec.get("value_type") or "string")
        spec_status = ""
        if value_type not in VALUE_TYPES:
            value_type, spec_status = "string", "invalid_spec"
        key = _scrub_text(spec.get("key") or name)
        declared_default = spec.get("default")
        container = _source_container(record_map, item_source)
        missing = key not in container
        raw = container.get(key, declared_default)
        typed, status = coerce_value(
            value_type, raw, declared_default,
            missing=missing, nullable=bool(spec.get("nullable", declared_default is None)),
        )
        if spec_status:
            status = spec_status
        provenance = _scrub_text(item_source) if not missing else "default"
        descriptors[row_key] = {
            "asset_path": _scrub_text(record_map.get("asset_path", "")),
            "namespace": namespace,
            "field_name": name,
            "name": name,
            "value_type": value_type,
            "value_text": typed["value_text"],
            "value_number": typed["value_number"],
            "value_bool": typed["value_bool"],
            "value": typed["value"],
            "provenance": provenance,
            "status": status,
            "source": item_source,
            "key": key,
            "family": family_name,
            "exact": "true" if exact_rank else "false",
            "wildcard": "true" if wildcard else "false",
        }
    return [descriptors[key] for key in sorted(descriptors)]
