#!/usr/bin/env python3
"""Read-only audit of generated MCP tool annotations.

Usage:
    python Tools/audit_tool_annotations.py --check

Checks the committed ``Server/ual_mcp/tool_schemas.yaml`` contract: qualified
``UEAssetLens.*`` names, explicit Read/Write/Execute annotations, consistent
``readOnlyHint`` values, no bare ``ual_*`` wire names, no duplicate names and no
missing annotation/meta carriers. The script never writes, formats or
regenerates a file; ``--check`` exits 1 when the contract has drifted.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_RELATIVE = "Server/ual_mcp/tool_schemas.yaml"
SCHEMA_PATH = ROOT / SCHEMA_RELATIVE
EXPECTED_SCHEMA_VERSION = "4.0"
EXPECTED_TOOL_COUNT = 30
EXPECTED_ACCESS_COUNTS = {"Read": 24, "Write": 3, "Execute": 3}
ACCESS_MODES = frozenset(("Read", "Write", "Execute"))
QUALIFIED_NAME_RE = re.compile(r"^UEAssetLens\.[A-Za-z0-9_.-]+$")
BARE_NAME_RE = re.compile(r"(?:^|\.)ual_", re.IGNORECASE)
NAME_KEYS = ("name", "mcp_name", "wireName", "toolName")
MODE_KEYS = ("accessMode", "AIAccessMode")


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().casefold()
        if text == "true":
            return True
        if text == "false":
            return False
    return None


def _carriers(
    tool: Mapping[str, Any], label: str
) -> tuple[list[tuple[str, Mapping[str, Any]]], list[str]]:
    """Collect direct and nested annotation/meta sources without mutating data."""
    carriers: list[tuple[str, Mapping[str, Any]]] = []
    issues: list[str] = []
    for key in ("annotations", "meta"):
        if key not in tool:
            issues.append(f"tool {label}: missing {key} mapping")
            continue
        value = tool[key]
        if not isinstance(value, Mapping) or not value:
            issues.append(f"tool {label}: {key} must be a non-empty mapping")
            continue
        carriers.append((key, value))
        if key == "annotations":
            if not any(mode_key in value for mode_key in MODE_KEYS):
                issues.append(f"tool {label}: annotations is missing accessMode")
            if "readOnlyHint" not in value:
                issues.append(f"tool {label}: annotations is missing readOnlyHint")
        elif not any(mode_key in value for mode_key in MODE_KEYS):
            issues.append(f"tool {label}: meta is missing AIAccessMode")
    if "accessMode" in tool or "readOnlyHint" in tool:
        carriers.append(("tool", tool))
    if not carriers:
        issues.append(f"tool {label}: missing annotations/meta fields")
    return carriers, issues


def _annotation_values(tool: Mapping[str, Any], label: str) -> list[str]:
    carriers, issues = _carriers(tool, label)
    modes: list[tuple[str, str, str]] = []
    hints: list[tuple[str, bool]] = []
    for source, data in carriers:
        for key in MODE_KEYS:
            if key in data:
                modes.append((source, key, str(data[key])))
        if "readOnlyHint" in data:
            hint = _as_bool(data["readOnlyHint"])
            if hint is None:
                issues.append(f"tool {label}: {source}.readOnlyHint must be boolean")
            else:
                hints.append((source, hint))
    if not modes:
        issues.append(f"tool {label}: missing explicit accessMode")
    if not hints:
        issues.append(f"tool {label}: missing explicit readOnlyHint")
    if modes:
        distinct_modes = {mode for _, _, mode in modes}
        if len(distinct_modes) > 1:
            issues.append(f"tool {label}: conflicting accessMode values {sorted(distinct_modes)}")
        for source, key, mode in modes:
            if mode not in ACCESS_MODES:
                issues.append(f"tool {label}: {source}.{key} must be Read, Write or Execute")
    if hints:
        distinct_hints = {hint for _, hint in hints}
        if len(distinct_hints) > 1:
            issues.append(f"tool {label}: conflicting readOnlyHint values")
        if True in distinct_hints and (not modes or not all(mode == "Read" for _, _, mode in modes)):
            issues.append(f"tool {label}: readOnlyHint true is allowed only for Read")
        if False in distinct_hints and modes and all(mode == "Read" for _, _, mode in modes):
            issues.append(f"tool {label}: Read tool must declare readOnlyHint true")
    return issues


def validate(payload: Any) -> list[str]:
    issues: list[str] = []
    if not isinstance(payload, Mapping):
        return [f"{SCHEMA_RELATIVE}: root must be a mapping"]
    version = str(payload.get("schema_version", ""))
    if version != EXPECTED_SCHEMA_VERSION:
        issues.append(
            f"{SCHEMA_RELATIVE}: schema_version must be {EXPECTED_SCHEMA_VERSION} "
            f"(found {version or 'missing'})"
        )
    tools = payload.get("tools")
    if not isinstance(tools, list) or not tools:
        return issues + [f"{SCHEMA_RELATIVE}: tools must be a non-empty list"]
    if len(tools) != EXPECTED_TOOL_COUNT:
        issues.append(
            f"{SCHEMA_RELATIVE}: expected {EXPECTED_TOOL_COUNT} tools, found {len(tools)}"
        )
    mode_counts = Counter(
        str(tool.get("accessMode", ""))
        for tool in tools if isinstance(tool, Mapping)
    )
    for mode, expected in EXPECTED_ACCESS_COUNTS.items():
        if mode_counts[mode] != expected:
            issues.append(
                f"{SCHEMA_RELATIVE}: expected {expected} {mode} tools, found {mode_counts[mode]}"
            )
    seen: set[str] = set()
    for index, tool in enumerate(tools, start=1):
        label = f"#{index}"
        if not isinstance(tool, Mapping):
            issues.append(f"tool {label}: must be a mapping")
            continue
        name = tool.get("name")
        if isinstance(name, str) and name:
            label = name
        if not isinstance(name, str) or not QUALIFIED_NAME_RE.fullmatch(name):
            issues.append(f"tool {label}: name must be a qualified UEAssetLens.* name")
        else:
            if name in seen:
                issues.append(f"tool {label}: duplicate MCP tool name")
            seen.add(name)
        for key in NAME_KEYS:
            value = tool.get(key)
            if isinstance(value, str) and BARE_NAME_RE.search(value):
                issues.append(f"tool {label}: bare ual_* wire names are forbidden ({key})")
        issues.extend(_annotation_values(tool, label))
    return sorted(set(issues))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit generated MCP tool annotations")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 on drift; validation is read-only in every mode",
    )
    parser.parse_args(argv)
    try:
        payload = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        print(
            f"DRIFT: cannot parse {SCHEMA_RELATIVE} ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 1
    issues = validate(payload)
    if issues:
        print(
            f"Tool annotation drift detected in {SCHEMA_RELATIVE}; "
            "regenerate the schema and commit the generated result.",
            file=sys.stderr,
        )
        for issue in issues:
            print(f"  {issue}", file=sys.stderr)
        return 1
    tools = payload.get("tools") if isinstance(payload, Mapping) else []
    print(
        f"OK: {SCHEMA_RELATIVE} has {len(tools or [])} qualified tools "
        "with explicit annotations/meta."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
