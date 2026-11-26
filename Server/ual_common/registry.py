"""Generic extension registry for the UEAssetLens asset kernel.

Every extension point (asset type, policy rule, field, diff layer, tool,
lifecycle hook, index sink, workflow template, gateway channel) registers
through this one object. Registration is keyed by ``namespace.name`` so the
same mechanism serves both process-local Python plugins and the declarative
``<Project>/Config/UAL/extensions/*.yaml`` manifests planned in
``docs/asset-lifecycle-architecture.md``.

Determinism contract: resolution and snapshots never depend on insertion
order; ties are broken by spec key. No timestamps, no absolute paths and no
random identifiers are ever emitted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

# Namespaces are fixed so a typo cannot silently create a parallel ecosystem.
NAMESPACES = (
    "asset_type", "policy", "field", "diff", "tool",
    "lifecycle", "sink", "template", "gateway",
)


@dataclass(frozen=True)
class ExtensionSpec:
    namespace: str
    name: str
    version: str = "1"
    priority: int = 0
    applies_to: tuple[str, ...] = ("*",)
    capabilities: tuple[str, ...] = ()
    schema: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.namespace not in NAMESPACES:
            raise ValueError(
                f"unknown extension namespace {self.namespace!r}; "
                f"expected one of {NAMESPACES}"
            )
        if not self.name or "." in self.name:
            raise ValueError("extension name must be non-empty and contain no dot")
        if not self.applies_to:
            raise ValueError("applies_to must not be empty")

    @property
    def key(self) -> str:
        return f"{self.namespace}.{self.name}"


class ExtensionRegistry:
    """Process-local registry with deterministic, capability-aware resolution."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[ExtensionSpec, Any]] = {}

    def register(self, spec: ExtensionSpec, impl: Any = None) -> ExtensionSpec:
        if spec.key in self._entries:
            raise ValueError(f"extension already registered: {spec.key}")
        self._entries[spec.key] = (spec, impl)
        return spec

    def unregister(self, namespace: str, name: str) -> None:
        self._entries.pop(f"{namespace}.{name}", None)

    def get(self, namespace: str, name: str) -> tuple[ExtensionSpec, Any] | None:
        return self._entries.get(f"{namespace}.{name}")

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)

    def resolve(
        self,
        asset_class: str = "*",
        capability: str | None = None,
        namespace: str | None = None,
    ) -> list[tuple[ExtensionSpec, Any]]:
        """Return matching extensions, highest priority first.

        An exact ``applies_to`` match outranks a ``*`` wildcard at equal
        priority; remaining ties break by key for reproducible ordering.
        """
        matches: list[tuple[ExtensionSpec, Any]] = []
        for key in sorted(self._entries):
            spec, impl = self._entries[key]
            if namespace is not None and spec.namespace != namespace:
                continue
            if capability is not None and capability not in spec.capabilities:
                continue
            if "*" not in spec.applies_to and asset_class not in spec.applies_to:
                continue
            matches.append((spec, impl))

        def order(item: tuple[ExtensionSpec, Any]) -> tuple[int, int, str]:
            spec = item[0]
            exact = 0 if "*" in spec.applies_to else 1
            return (-spec.priority, -exact, spec.key)

        return sorted(matches, key=order)

    def snapshot(self) -> list[dict[str, Any]]:
        """Documentation/CI-facing view; deliberately excludes implementations."""
        result: list[dict[str, Any]] = []
        for key in sorted(self._entries):
            spec = self._entries[key][0]
            result.append({
                "key": spec.key,
                "namespace": spec.namespace,
                "name": spec.name,
                "version": spec.version,
                "priority": spec.priority,
                "applies_to": list(spec.applies_to),
                "capabilities": list(spec.capabilities),
                "schema": dict(spec.schema),
            })
        return result


def register_callable(
    registry: ExtensionRegistry,
    namespace: str,
    name: str,
    fn: Callable[..., Any],
    *,
    applies_to: Iterable[str] = ("*",),
    capabilities: Iterable[str] = (),
    priority: int = 0,
    version: str = "1",
    schema: dict[str, Any] | None = None,
) -> ExtensionSpec:
    """Convenience helper for Python-side plugins."""
    return registry.register(ExtensionSpec(
        namespace=namespace,
        name=name,
        version=version,
        priority=priority,
        applies_to=tuple(applies_to),
        capabilities=tuple(capabilities),
        schema=schema or {},
    ), impl=fn)
