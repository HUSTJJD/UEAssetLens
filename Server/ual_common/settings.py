"""Four-layer preference resolution for UEAssetLens.

Layers, lowest to highest precedence::

    builtin  <Plugin>/Config/UAL/                 (shipped defaults)
    plugin   <Project>/Plugins/*/Config/UAL/      (extension plugin defaults)
    project  <Project>/Config/UAL/                (team conventions, in Git)
    user     <Project>/Saved/Config/UAL/          (personal, gitignored)

Each namespace maps to a subdirectory (``policies``, ``taxonomy``,
``profiles``, ``extensions``, ``templates``). Files are plain YAML:

* a mapping with ``id`` becomes one rule;
* a mapping with ``rules: [...]`` contributes several rules;
* a bare list contributes rules;
* any other mapping is deep-merged into the settings dict.

Rules merge by ``id`` (later layers win) while keeping first-seen order, so
the same inputs always produce byte-identical output. ``enabled: false`` is
preserved rather than dropped, letting the policy engine explain why a rule
did not fire.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

LAYERS = ("builtin", "plugin", "project", "user")
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIRNAME = "UAL"


@dataclass
class ResolvedSettings:
    data: dict[str, Any] = field(default_factory=dict)
    rules: list[dict[str, Any]] = field(default_factory=list)
    provenance: dict[str, str] = field(default_factory=dict)
    layers: list[tuple[str, Path]] = field(default_factory=list)

    def active_rules(self) -> list[dict[str, Any]]:
        return [r for r in self.rules if r.get("enabled", True)]


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk upwards for a ``*.uproject`` (mirrors engine_bridge.py bootstrap)."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if any(candidate.glob("*.uproject")):
            return candidate
    return None


def layer_roots(
    project_root: Path | None = None,
    *,
    plugin_roots: Iterable[Path] = (),
    user_root: Path | None = None,
) -> list[tuple[str, Path]]:
    project = project_root or find_project_root()
    roots: list[tuple[str, Path]] = [
        ("builtin", PLUGIN_ROOT / "Config" / CONFIG_DIRNAME)
    ]
    extras = list(plugin_roots)
    if project is not None:
        extras = sorted((project / "Plugins").glob(f"*/Config/{CONFIG_DIRNAME}")) + extras
        roots.append(("user", user_root or (project / "Saved" / "Config" / CONFIG_DIRNAME)))
        roots.append(("project", project / "Config" / CONFIG_DIRNAME))
    for extra in extras:
        roots.append(("plugin", Path(extra)))
    # Stable order: builtin, plugin, project, user (LAYERS declares precedence).
    return sorted(roots, key=lambda item: (LAYERS.index(item[0]), str(item[1])))


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = _deep_merge(current, value)
        else:
            result[key] = value
    return result


def _as_rules(doc: Any) -> list[dict[str, Any]]:
    if isinstance(doc, list):
        return [dict(item) for item in doc if isinstance(item, dict)]
    if not isinstance(doc, dict):
        return []
    if isinstance(doc.get("rules"), list):
        return [dict(item) for item in doc["rules"] if isinstance(item, dict)]
    if "id" in doc:
        return [dict(doc)]
    return []


def load_namespace(
    namespace: str,
    *,
    project_root: Path | None = None,
    plugin_roots: Iterable[Path] = (),
    user_root: Path | None = None,
) -> ResolvedSettings:
    """Resolve one namespace across all four layers without side effects."""
    resolved = ResolvedSettings()
    seen_order: list[str] = []
    merged_rules: dict[str, dict[str, Any]] = {}

    for layer_name, root in layer_roots(
        project_root, plugin_roots=plugin_roots, user_root=user_root
    ):
        directory = root / namespace
        resolved.layers.append((layer_name, directory))
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            text = path.read_text(encoding="utf-8")
            doc = yaml.safe_load(text) if text.strip() else None
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
            if not rules and isinstance(doc, dict):
                payload = {k: v for k, v in doc.items() if k != "rules"}
                if payload:
                    resolved.data = _deep_merge(resolved.data, payload)

    resolved.rules = [merged_rules[rule_id] for rule_id in seen_order]
    return resolved


def load_rules(
    namespace: str = "policies", **kwargs: Any
) -> ResolvedSettings:
    """Shortcut used by the policy engine and CI gates."""
    return load_namespace(namespace, **kwargs)
