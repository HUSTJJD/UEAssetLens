"""Read-only policy gate: DuckDB index -> PolicyEngine -> bounded TOON report.

The gate only *projects* columns that already exist in ``init.sql`` into the
flat dictionaries consumed by ``ual_common.policy``. It never writes to the
index and never changes the 16-key record contract, so no ``schema_version``
bump and no four-place field sync are needed.

Rules come from the four-layer preference resolver (builtin < plugin < project
< user). The CLI passes ``--project`` so team and personal overrides apply
without code changes. Every result is a bounded TOON report: no full-table
dump, no absolute paths, no timestamps, and stable ordering.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import duckdb

from . import extensions, policy
from .registry import ExtensionRegistry
from .settings import ResolvedSettings, load_namespace

GATE_NAMESPACE = "policies"


def load_index_assets(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """Deterministic asset view for the policy engine (read-only, bounded).

    Only ``asset_path`` / ``logical_id`` / ``asset_class`` / the two spec maps
    and hard ``DEPENDS_ON`` edges are read. ``CO_OCCURS`` is a statistical hint
    produced from name-token similarity, not a declared dependency, so
    reference rules must never see it.
    """
    rows = con.execute(
        "SELECT asset_path, logical_id, asset_class, domain_spec, interface_spec "
        "FROM assets ORDER BY asset_path"
    ).fetchall()
    deps: dict[str, list[str]] = {}
    for source, target in con.execute(
        "SELECT source_path, target_path FROM asset_edges "
        "WHERE edge_type = 'DEPENDS_ON' ORDER BY source_path, target_path"
    ).fetchall():
        deps.setdefault(str(source), []).append(str(target))

    assets: list[dict[str, Any]] = []
    for path, logical_id, asset_class, domain_spec, interface_spec in rows:
        domain = dict(domain_spec or {})
        interface = dict(interface_spec or {})
        assets.append({
            "asset_path": str(path),
            "logical_id": str(logical_id or ""),
            "asset_class": str(asset_class or ""),
            "domain_spec": domain,
            "tags": sorted(set(domain) | set(interface)),
            "dependency_paths": deps.get(str(path), []),
        })
    return assets


def run_policy_gate(
    con: duckdb.DuckDBPyConnection,
    *,
    project_root: Path | None = None,
    namespace: str = GATE_NAMESPACE,
    limit: int = 100,
    registry: ExtensionRegistry | None = None,
) -> tuple[str, list[policy.Violation], ResolvedSettings]:
    """Resolve rules, validate the index snapshot and render a bounded report."""
    resolved = load_namespace(namespace, project_root=project_root)
    active_registry = registry
    if active_registry is None:
        # Declarative `Config/UAL/extensions/*.yaml` manifests make `kind:
        # custom` rules work in the CLI without importing arbitrary code.
        active_registry = extensions.load_registry(
            project_root=project_root,
        ).registry
    engine = policy.PolicyEngine(resolved, registry=active_registry)
    violations = engine.validate_many(load_index_assets(con))
    return policy.render_report(violations, limit=limit), violations, resolved


def decide_exit_code(
    violations: Sequence[policy.Violation],
    *,
    fail_on: str = "error",
    strict: bool = False,
) -> int:
    """Decide the CI exit code for a set of policy violations.

    Two independent conditions must both hold before a finding can block:
    ``severity >= fail_on`` and (``rule.enforce`` or ``--strict``). Builtin
    rules ship with ``enforce: false``, so a default run stays green while
    still emitting the full TOON report. Unknown severity / threshold values
    degrade to the same normalisation ``PolicyEngine`` applies, rather than
    raising: a typo must never turn every CI run red.

    Pure function: no printing, no I/O, returns exactly 0 or 1.
    """
    ranks = {name: rank for rank, name in enumerate(policy.SEVERITIES)}
    threshold = ranks.get(fail_on, ranks["error"])
    default_rank = ranks["warning"]
    for violation in violations:
        if ranks.get(violation.severity, default_rank) < threshold:
            continue
        if strict or violation.enforce:
            return 1
    return 0
