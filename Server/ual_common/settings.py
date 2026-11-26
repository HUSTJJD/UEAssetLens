"""Four-layer preference resolution for UEAssetLens.

Layers, lowest to highest precedence::

    builtin  <Plugin>/Config/UAL/                 (shipped defaults)
    plugin   <Plugin>/Plugins/*/Config/UAL/       (extension plugin defaults)
    project  <Project>/Config/UAL/                (team conventions, in Git)
    user     <Project>/Saved/Config/UAL/          (personal, gitignored)

Each namespace maps to a subdirectory (``policies``, ``taxonomy``,
``profiles``, ``extensions``, ``templates``). Files are plain YAML:

* a mapping with ``id`` becomes one rule;
* a mapping with ``rules: [...]`` contributes several rules and may also carry
  ``profiles`` / ``presets`` mappings;
* a bare list contributes rules;
* other mapping sections are deep-merged into the settings dict.

Rules merge by ``id`` (later layers win) while keeping first-seen order, so
the same inputs always produce byte-identical output. ``enabled: false`` is
preserved rather than dropped, letting the policy engine explain why a rule
did not fire. ``extends: <profile_id>`` inherits data from a named profile or
another rule; the overriding rule keeps its own ``id`` and provenance.

Only a locally bound ``yaml.SafeLoader`` is used, and explicit Python object
tags are rejected before parsing. No timestamps, local absolute paths or
random identifiers are emitted by this module.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

LAYERS = ("builtin", "plugin", "project", "user")
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIRNAME = "UAL"
PROFILE_SECTIONS = ("profiles", "presets")
RESOURCE_REQUIREMENT_FLAGS = (
    "requires_gpu", "requires_cloud", "requires_vlm", "requires_network",
)
EXTERNAL_MARKER_FLAGS = (
    "external_generative", "external_generative_service", "external_service",
)
EXTERNAL_CAPABILITIES = frozenset({"external_disabled", "external_generative"})
# Bind the loader and loader function at import time: later monkeypatching of
# ``yaml.safe_load`` / ``yaml.load`` cannot turn preference YAML into code.
_YAML_LOAD = yaml.load
_SAFE_LOADER = yaml.SafeLoader
_PYTHON_TAG_MARKERS = ("!!python/", "tag:yaml.org,2002:python/")


@dataclass
class ResolvedSettings:
    data: dict[str, Any] = field(default_factory=dict)
    rules: list[dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, str] = field(default_factory=dict)
    layers: list[tuple[str, Path]] = field(default_factory=list)
    profile_provenance: dict[str, str] = field(default_factory=dict)
    inheritance: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def active_rules(self) -> list[dict[str, Any]]:
        """Return enabled, offline-only rules in deterministic first-seen order."""
        return [rule for rule in self.rules if is_active_rule(rule)]


def _sections(rule: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    sections: list[Mapping[str, Any]] = [rule]
    for key in ("config", "schema"):
        value = rule.get(key)
        if isinstance(value, Mapping):
            sections.append(value)
    return tuple(sections)


def is_active_rule(rule: Any) -> bool:
    """Check top-level and nested disabled / external-resource boundaries.

    A disabled integration can never become active merely because a higher
    preference layer sets its top-level ``enabled`` flag. Any rule declaring a
    GPU, cloud, VLM, network or external-generative requirement is excluded
    from the active callable/executor view.
    """
    if not isinstance(rule, Mapping) or not bool(rule.get("enabled", True)):
        return False
    sections = _sections(rule)
    for section in sections:
        if not bool(section.get("enabled", True)):
            return False
        if any(section.get(flag) is True for flag in RESOURCE_REQUIREMENT_FLAGS):
            return False
        if any(section.get(flag) is True for flag in EXTERNAL_MARKER_FLAGS):
            return False
    capabilities = rule.get("capabilities") or ()
    if isinstance(capabilities, str):
        capabilities = (capabilities,)
    if any(str(item) in EXTERNAL_CAPABILITIES for item in capabilities):
        return False
    return True


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk upwards for a ``*.uproject`` (mirrors engine_bridge.py bootstrap)."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if any(candidate.glob("*.uproject")):
            return candidate
    return None


def _plugin_layer_roots() -> list[Path]:
    """Scan the formal spec's plugin layer beneath the UEAssetLens plugin."""
    plugins_dir = PLUGIN_ROOT / "Plugins"
    if not plugins_dir.is_dir():
        return []
    return sorted(
        plugin / "Config" / CONFIG_DIRNAME
        for plugin in plugins_dir.glob("*")
        if plugin.is_dir()
    )


def layer_roots(
    project_root: Path | None = None,
    *,
    plugin_roots: Iterable[Path] = (),
    user_root: Path | None = None,
    include_plugin_layer: bool = True,
    include_project_layers: bool = True,
) -> list[tuple[str, Path]]:
    """Return deterministic builtin < plugin < project < user config roots."""
    project = Path(project_root) if project_root is not None else (
        find_project_root() if include_project_layers else None
    )
    roots: list[tuple[str, Path]] = [
        ("builtin", PLUGIN_ROOT / "Config" / CONFIG_DIRNAME)
    ]
    plugin_paths: list[Path] = []
    if include_plugin_layer:
        plugin_paths.extend(_plugin_layer_roots())
        plugin_paths.extend(Path(extra) for extra in plugin_roots)
    # Deduplicate while preserving the first-seen, sorted plugin order.
    seen: set[str] = set()
    for plugin_path in plugin_paths:
        marker = str(plugin_path)
        if marker not in seen:
            seen.add(marker)
            roots.append(("plugin", plugin_path))
    if include_project_layers and project is not None:
        roots.append((
            "project",
            project / "Config" / CONFIG_DIRNAME,
        ))
        roots.append((
            "user",
            Path(user_root) if user_root is not None else (
                project / "Saved" / "Config" / CONFIG_DIRNAME
            ),
        ))
    # Stable order: builtin, plugin, project, user (LAYERS declares precedence).
    return sorted(roots, key=lambda item: (LAYERS.index(item[0]), str(item[1])))


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = _deep_merge(current, value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _as_rules(doc: Any) -> list[dict[str, Any]]:
    if isinstance(doc, list):
        return [copy.deepcopy(dict(item)) for item in doc if isinstance(item, Mapping)]
    if not isinstance(doc, Mapping):
        return []
    if isinstance(doc.get("rules"), list):
        return [
            copy.deepcopy(dict(item))
            for item in doc["rules"]
            if isinstance(item, Mapping)
        ]
    if "id" in doc:
        return [copy.deepcopy(dict(doc))]
    return []


def _safe_load_yaml(text: str, source: str) -> Any:
    if any(marker in text for marker in _PYTHON_TAG_MARKERS):
        raise ValueError(f"{source}: Python object tags are forbidden in UAL YAML")
    try:
        return _YAML_LOAD(text, Loader=_SAFE_LOADER)
    except yaml.YAMLError as exc:
        raise ValueError(f"{source}: invalid YAML") from exc


def _record_profile_sources(
    resolved: ResolvedSettings, doc: Any, source: str, rules: list[dict[str, Any]]
) -> None:
    if isinstance(doc, Mapping):
        for section_name in PROFILE_SECTIONS:
            section = doc.get(section_name)
            if isinstance(section, Mapping):
                for profile_id in section:
                    resolved.profile_provenance[str(profile_id)] = f"{source}:{section_name}"
    for rule in rules:
        if rule.get("kind") == "profile" or rule.get("profile") is True:
            resolved.profile_provenance[str(rule["id"])] = source


def _extends_refs(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    raise ValueError("extends must be a profile id or a list of profile ids")


def _profile_catalog(
    data: Mapping[str, Any], rules: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for section_name in PROFILE_SECTIONS:
        section = data.get(section_name)
        if isinstance(section, Mapping):
            for profile_id, profile in section.items():
                if isinstance(profile, Mapping):
                    entry = copy.deepcopy(dict(profile))
                    entry.setdefault("id", str(profile_id))
                    catalog[str(profile_id)] = entry
    for rule in rules:
        catalog.setdefault(str(rule["id"]), copy.deepcopy(dict(rule)))
    return catalog


def _resolve_extends(
    rule: dict[str, Any],
    catalog: Mapping[str, dict[str, Any]],
    stack: tuple[str, ...],
) -> dict[str, Any]:
    refs = _extends_refs(rule.get("extends"))
    if not refs:
        return copy.deepcopy(dict(rule))
    merged: dict[str, Any] = {}
    for ref in refs:
        if ref in stack:
            raise ValueError(f"cyclic extends profile reference: {' -> '.join((*stack, ref))}")
        profile = catalog.get(ref)
        if profile is None:
            raise ValueError(f"unknown extends profile {ref!r} for rule {rule.get('id')!r}")
        inherited = _resolve_extends(profile, catalog, (*stack, ref))
        parent_data = {
            key: value for key, value in inherited.items()
            if key not in ("id", "extends")
        }
        merged = _deep_merge(merged, parent_data)
    child_data = {
        key: copy.deepcopy(value) for key, value in rule.items()
        if key != "extends"
    }
    merged = _deep_merge(merged, child_data)
    merged["id"] = rule["id"]
    merged["extends"] = copy.deepcopy(rule["extends"])
    return merged


def load_namespace(
    namespace: str,
    *,
    project_root: Path | None = None,
    plugin_roots: Iterable[Path] = (),
    user_root: Path | None = None,
    include_plugin_layer: bool = True,
    include_project_layers: bool = True,
) -> ResolvedSettings:
    """Resolve one namespace across the four layers without side effects."""
    resolved = ResolvedSettings()
    seen_order: list[str] = []
    merged_rules: dict[str, dict[str, Any]] = {}

    for layer_name, root in layer_roots(
        project_root,
        plugin_roots=plugin_roots,
        user_root=user_root,
        include_plugin_layer=include_plugin_layer,
        include_project_layers=include_project_layers,
    ):
        directory = root / namespace
        resolved.layers.append((layer_name, directory))
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            text = path.read_text(encoding="utf-8")
            doc = _safe_load_yaml(text, f"{layer_name}:{path.name}") if text.strip() else None
            if doc is None:
                continue
            source = f"{layer_name}:{path.name}"
            rules = _as_rules(doc)
            for rule in rules:
                rule_id = str(rule.get("id", ""))
                if not rule_id:
                    raise ValueError(f"{source}: rule is missing 'id'")
                if rule_id not in merged_rules:
                    seen_order.append(rule_id)
                merged_rules[rule_id] = rule
                resolved.provenance[rule_id] = source
            if isinstance(doc, Mapping):
                payload = {key: value for key, value in doc.items() if key != "rules"}
                if payload:
                    resolved.data = _deep_merge(resolved.data, payload)
                _record_profile_sources(resolved, doc, source, rules)

    rules_in_order = [copy.deepcopy(merged_rules[rule_id]) for rule_id in seen_order]
    catalog = _profile_catalog(resolved.data, rules_in_order)
    resolved.rules = []
    for rule in rules_in_order:
        rule_id = str(rule["id"])
        refs = _extends_refs(rule.get("extends"))
        if refs:
            resolved.inheritance[rule_id] = refs
        resolved.rules.append(_resolve_extends(rule, catalog, (rule_id,)))
    return resolved


def load_rules(
    namespace: str = "policies", **kwargs: Any
) -> ResolvedSettings:
    """Shortcut used by the policy engine and CI gates."""
    return load_namespace(namespace, **kwargs)
