"""Data-driven asset taxonomy for UEAssetLens.

The rule table lives in ``taxonomy.yaml`` next to this module and is the single
source of truth shared with the C++ extractor: ``Tools/gen_taxonomy.py`` renders
it into ``Source/UEAssetLensEditor/Public/UALTaxonomyRules.h``.

Any UObject asset class is classifiable: unmatched classes fall back to the
configured ``fallback`` family instead of being rejected. Matching is
case-sensitive, mirroring ``FString::Contains(..., ESearchCase::CaseSensitive)``
on the C++ side, so both implementations produce identical taxonomy arrays.

Reads the rule table via PyYAML (``yaml.safe_load``); PyYAML is a declared
runtime dependency in requirements.txt, not an optional extra.
"""
from __future__ import annotations

import yaml
from functools import lru_cache
from pathlib import Path
from typing import Any

RULES_PATH = Path(__file__).with_name("taxonomy.yaml")


@lru_cache(maxsize=1)
def rules() -> dict[str, Any]:
    """Load and validate the rule table once."""
    with RULES_PATH.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data.get("families"), list) or not data["families"]:
        raise ValueError(f"{RULES_PATH}: 'families' must be a non-empty list")
    for entry in data["families"]:
        if not isinstance(entry.get("name"), str) or not entry.get("match"):
            raise ValueError(f"{RULES_PATH}: every family needs a name and match list")
    return data


def classify(asset_class: str) -> str:
    """Return the family for an asset class; unknown classes use the fallback."""
    data = rules()
    for entry in data["families"]:
        for pattern in entry["match"]:
            if pattern in asset_class:
                return entry["name"]
    return data["fallback"]


def build_taxonomy(asset_class: str) -> list[str]:
    """Return the ordered, de-duplicated ``["Asset", <family>, <class>]`` path.

    Guarantees a three-level taxonomy for every possible asset class, including
    user-defined UObject subclasses and asset types introduced by newer engines.
    """
    data = rules()
    ordered = (data["root"], classify(asset_class), asset_class)
    out: list[str] = []
    for item in ordered:
        if item and item not in out:
            out.append(item)
    return out
