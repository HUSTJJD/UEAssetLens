"""Deterministic 16-dim fingerprint math (Python reference implementation).

Mirrors Source/UEAssetLens/Private/UALFingerprint.cpp. Zero-VLM: every dimension
comes from geometry, kinematics or parameter signatures — never from a model.
"""
from __future__ import annotations

import math
from typing import Sequence

DIMS = 16
_EPS = 1e-9


def normalize_l2(vec: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(float(v) * float(v) for v in vec))
    if norm < _EPS:
        return [0.0] * len(vec)
    return [float(v) / norm for v in vec]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    na = math.sqrt(sum(float(x) * float(x) for x in a))
    nb = math.sqrt(sum(float(x) * float(x) for x in b))
    if na < _EPS or nb < _EPS:
        return 0.0
    return sum(float(x) * float(y) for x, y in zip(a, b)) / (na * nb)


def radial_histogram(points: Sequence[Sequence[float]], bins: int = 8) -> list[float]:
    """Distance-to-centroid histogram, normalized by max radius.

    Invariant to rotation and uniform scale; NOT a unique shape identifier —
    two visually different meshes can share a histogram.
    """
    if bins <= 0:
        return []
    pts = [(float(p[0]), float(p[1]), float(p[2])) for p in points if len(p) >= 3]
    if not pts:
        return [0.0] * bins
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    cz = sum(p[2] for p in pts) / len(pts)
    radii = [math.sqrt((x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2) for x, y, z in pts]
    rmax = max(radii) or 1.0
    hist = [0.0] * bins
    for r in radii:
        idx = min(bins - 1, int((r / rmax) * bins))
        hist[idx] += 1.0
    total = sum(hist) or 1.0
    return [h / total for h in hist]


def _pad(vec: Sequence[float]) -> list[float]:
    out = [float(v) if math.isfinite(float(v)) else 0.0 for v in vec][:DIMS]
    return out + [0.0] * (DIMS - len(out))


def mesh_fingerprint(
    bounds_size: Sequence[float],
    hist8: Sequence[float],
    material_slots: int,
    sockets: int,
) -> list[float]:
    """3 normalized aspect ratios + 8-bin radial histogram + 5 slot features."""
    sx, sy, sz = (abs(float(bounds_size[i])) if i < len(bounds_size) else 0.0 for i in range(3))
    vmax = max(sx, sy, sz, _EPS)
    ratio = normalize_l2([sx / vmax, sy / vmax, sz / vmax])
    hist = list(hist8)[:8] + [0.0] * max(0, 8 - len(hist8))
    m, s = max(0, int(material_slots)), max(0, int(sockets))
    slots = [
        math.log1p(m) / 5.0,
        math.log1p(s) / 5.0,
        m / (m + s + 1.0),
        1.0 if s else 0.0,
        1.0 if m > 1 else 0.0,
    ]
    return _pad(ratio + hist + slots)


def animation_fingerprint(
    duration: float,
    root_delta: Sequence[float],
    limb_arc_lengths: Sequence[float],
    kinetic_curve: Sequence[float],
) -> list[float]:
    """1 duration + 3 root-motion direction cosines + 4 limb arcs + 8 kinetic bins."""
    d = min(1.0, max(0.0, float(duration) / 5.0))
    direction = normalize_l2(list(root_delta)[:3] + [0.0] * max(0, 3 - len(root_delta)))
    arcs = list(limb_arc_lengths)[:4] + [0.0] * max(0, 4 - len(limb_arc_lengths))
    total_arcs = sum(abs(float(a)) for a in arcs) or 1.0
    arcs = [abs(float(a)) / total_arcs for a in arcs]
    kinetic = list(kinetic_curve)[:8] + [0.0] * max(0, 8 - len(kinetic_curve))
    return _pad([d] + direction + arcs + kinetic)
