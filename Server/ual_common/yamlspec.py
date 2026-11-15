"""YAML AssetSpec renderer — UAL's only YAML implementation.

Output is intentionally hand-rendered rather than yaml.dump()-ed: inline semantic
annotations are a first-class part of the contract, and stable formatting keeps
Git diffs clean. No timestamps, no absolute local paths, no random IDs.
"""
from __future__ import annotations

import json
from typing import Any

HEADER = "# UEAssetLens AssetSpec v2.0 (Auto-generated, Zero-VLM)"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text or "0"
    return str(value)


def _seq(values: Any) -> str:
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except (ValueError, TypeError):
            return "[]"
    if not isinstance(values, (list, tuple)):
        return "[]"
    return "[" + ", ".join(_fmt(v) for v in values) + "]"


def _maybe_json(raw: Any, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


def build_yaml(record: dict[str, Any], referencers: list[str] | None = None) -> str:
    """Render one asset spec. `record` is the dict returned by db.get_asset()."""
    spec = record.get("domain_spec") or {}
    iface = record.get("interface_spec") or {}
    referencers = referencers or []
    phases = _maybe_json(spec.get("phases"), {}) or {}
    notifies = _maybe_json(spec.get("notifies"), []) or []
    kin = _maybe_json(spec.get("kinematics"), {}) or {}

    out = [HEADER, "", "identity:"]
    out.append(f"  logical_id: {record.get('logical_id', '')}")
    out.append(f"  asset_path: {record.get('asset_path', '')}")
    out.append(f"  asset_class: {record.get('asset_class', '')}")
    out.append(f"  parent_class: {record.get('parent_class', '')}")
    out.append(f"  taxonomy: {_seq(record.get('taxonomy') or [])}")
    out.append("")
    out.append("# interface_contract: what an Agent may bind to without hallucinating.")
    out.append("interface_contract:")
    out.append(f"  skeleton: {iface.get('skeleton', record.get('skeleton_path') or '~')}")
    out.append(f"  sockets_referenced: {_seq(iface.get('sockets_referenced', []))} # weapon / hitbox attach candidates")
    out.append(f"  material_slots: {_seq(iface.get('material_slots', []))}")
    out.append("")

    duration = _num(record.get("duration"))
    out.append("domain_spec:")
    out.append(f"  duration: {_fmt(duration)} # unit: seconds")
    delta = _maybe_json(spec.get("root_motion_delta"), [0.0, 0.0, 0.0])
    out.append("  kinematics:")
    out.append(
        f"    root_motion_delta: {_seq(delta)} # deterministic root displacement (cm)"
    )
    out.append(f"    forward_cm: {_fmt(_num(spec.get('forward_cm')))}")
    out.append(f"    peak_velocity: {_fmt(_num(kin.get('peak_velocity')))}")
    out.append(f"    is_airborne: {'true' if kin.get('is_airborne') else 'false'}")
    out.append(
        f"    dominant_limbs: {_seq(kin.get('dominant_limbs', [record.get('dominant_limb')]))} # inferred from kinetic energy curve"
    )
    out.append("  phases:")
    for name, default in (("windup", [0.0, 0.0]), ("active_strike", [0.0, 0.0]), ("recovery", [0.0, 0.0])):
        window = phases.get(name, default) if isinstance(phases, dict) else default
        out.append(f"    {name}: {_seq(window)} # auto-split from kinetic peaks")
    out.append(f"  strike_window: {spec.get('strike_window', '')}")
    out.append("  notifies:")
    if notifies:
        for note in notifies:
            if isinstance(note, dict):
                bits = ", ".join(f"{k}: {_fmt(v)}" for k, v in note.items())
                out.append(f"    - {{ {bits} }}")
    else:
        out.append("    []")
    out.append("")
    out.append("# graph_context: dependency edges from asset_edges, not a guess.")
    out.append("graph_context:")
    out.append(f"  referenced_by_count: {_num(record.get('referenced_by_count'), 0):.0f}")
    out.append(f"  top_referencers: {_seq(referencers)}")
    return "\n".join(out) + "\n"
