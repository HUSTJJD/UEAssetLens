"""Regression: declarative YAML is the sole source for THREE C++ headers.

The taxonomy/feature-profile headers remain byte-compatible with the earlier
generator. The third header adds M2 domain profiles and typed field rules, and
``--check`` now enforces all three. The C++ extractor must consume generated
tables rather than carrying another anonymous-namespace rule table.
"""
from __future__ import annotations

import copy
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
GEN_SCRIPT = REPO / "Tools" / "gen_taxonomy.py"
RULES = REPO / "Server" / "ual_common" / "taxonomy.yaml"
FIELDS = REPO / "Config" / "UAL" / "fields" / "default.yaml"
PUBLIC = REPO / "Source" / "UEAssetLensEditor" / "Public"
PRIVATE = REPO / "Source" / "UEAssetLensEditor" / "Private"
TAXONOMY_HEADER = PUBLIC / "UALTaxonomyRules.h"
FEATURE_HEADER = PUBLIC / "UALFeatureProfiles.h"
DOMAIN_HEADER = PUBLIC / "UALDomainProfiles.h"
DOMAIN_EXTRACTOR_HEADER = PUBLIC / "UALDomainFeatureExtractor.h"
DOMAIN_CPP = PRIVATE / "UALDomainFeatureExtractor.cpp"
MODULE_CPP = PRIVATE / "UEAssetLensEditorModule.cpp"
COMMANDLET_CPP = PRIVATE / "UALIndexBuilderCommandlet.cpp"
TYPED_CPP = PRIVATE / "UALTypedFeatureExtractor.cpp"
UNIVERSAL_CPP = PRIVATE / "FUniversalReflectionExtractor.cpp"

RULES_RELATIVE = Path("Server") / "ual_common" / "taxonomy.yaml"
FIELDS_RELATIVE = Path("Config") / "UAL" / "fields" / "default.yaml"
TAXONOMY_RELATIVE = (
    Path("Source") / "UEAssetLensEditor" / "Public" / "UALTaxonomyRules.h"
)
FEATURE_RELATIVE = (
    Path("Source") / "UEAssetLensEditor" / "Public" / "UALFeatureProfiles.h"
)
DOMAIN_RELATIVE = (
    Path("Source") / "UEAssetLensEditor" / "Public" / "UALDomainProfiles.h"
)


@pytest.fixture(scope="module")
def gen() -> Any:
    """Load the generator as a module (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location("ual_gen_taxonomy", GEN_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rules() -> dict[str, Any]:
    with RULES.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    assert isinstance(data, dict)
    return data


@pytest.fixture(scope="module")
def fields() -> dict[str, Any]:
    with FIELDS.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    assert isinstance(data, dict)
    return data


def _temp_root(tmp_path: Path) -> Path:
    """A standalone plugin tree: only the two YAML sources are copied."""
    root = tmp_path / "plugin"
    (root / "Server" / "ual_common").mkdir(parents=True)
    (root / "Config" / "UAL" / "fields").mkdir(parents=True)
    (root / "Source" / "UEAssetLensEditor" / "Public").mkdir(parents=True)
    shutil.copyfile(RULES, root / RULES_RELATIVE)
    shutil.copyfile(FIELDS, root / FIELDS_RELATIVE)
    return root


def _cli(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the generator as a real CLI, in a temp tree, with PyYAML importable."""
    env = dict(os.environ)
    site_packages = Path(yaml.__file__).resolve().parents[1]
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(site_packages), env.get("PYTHONPATH", "")) if part
    )
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=script.parent.parent,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _production_only(text: str) -> str:
    """Drop the opt-in, default-off CDO reflection blocks (documented exception)."""
    return re.sub(
        r"#if defined\(UAL_ENABLE_CDO_REFLECTION\).*?#endif",
        "",
        text,
        flags=re.DOTALL,
    )


def test_yaml_is_the_single_source_of_all_three_headers(
    gen: Any, rules: dict[str, Any], fields: dict[str, Any]
) -> None:
    assert gen.load_rules(RULES) == rules
    assert gen.load_fields(FIELDS) == fields
    gen.validate(rules, fields)

    taxonomy_text = TAXONOMY_HEADER.read_text(encoding="utf-8")
    feature_text = FEATURE_HEADER.read_text(encoding="utf-8")
    domain_text = DOMAIN_HEADER.read_text(encoding="utf-8")
    assert taxonomy_text == gen.render_taxonomy_rules(rules)
    assert feature_text == gen.render_feature_profiles(rules)
    assert domain_text == gen.render_domain_profiles(rules, fields)
    for header_text in (taxonomy_text, feature_text, domain_text):
        assert "AUTO-GENERATED" in header_text
        assert "#pragma once" in header_text
    assert domain_text.startswith("#pragma once\n")

    for family in rules["families"]:
        assert f'TEXT("{family["name"]}")' in taxonomy_text
    for rule in rules["profiles"]["rules"]:
        assert f'{{ TEXT("{rule["family"]}"), TEXT("{rule["keys"]}") }},' in feature_text
    assert f'TEXT("{rules["profiles"]["default"]}")' in feature_text
    for rule in rules["capability_rules"]:
        assert (
            f'{{ TEXT("{rule["name"]}"), TEXT("{rule["tag_fragment"]}") }},'
            in feature_text
        )
    for prefix in rules["prefixes"]:
        assert f'TEXT("{prefix}"),' in feature_text

    for rule in rules["domain_profiles"]["rules"]:
        assert f'TEXT("{rule["family"]}")' in domain_text
        assert f'TEXT("{rule["key_prefix"]}")' in domain_text
        assert f'TEXT("{rule["tag_fragments"]}")' in domain_text
    for family_name, family in fields["families"].items():
        assert f'TEXT("{family_name}")' in domain_text
        for field_name, spec in family["fields"].items():
            assert f'TEXT("{field_name}")' in domain_text
            assert f'TEXT("{spec["key"]}")' in domain_text

    # Editing either YAML is what changes the generated headers (no side channel).
    mutated = copy.deepcopy(rules)
    mutated["families"].append({"name": "Hologram", "match": ["Hologram"]})
    mutated["profiles"]["rules"].append({"family": "Hologram", "keys": "Holo|Beam"})
    mutated["capability_rules"].append({"name": "has_holo", "tag_fragment": "Holo"})
    mutated["prefixes"].append("HG_")
    mutated["domain_profiles"]["rules"].append({
        "family": "hologram",
        "taxonomy_family": "Hologram",
        "key_prefix": "domain.hologram",
        "tag_fragments": "Holo|Beam",
        "priority": 20,
    })
    mutated_fields = copy.deepcopy(fields)
    mutated_fields["families"]["hologram"] = {
        "applies_to": ["Hologram"],
        "priority": 20,
        "taxonomy_family": "Hologram",
        "fields": {"hologram_beam": {"type": "string", "key": "beam", "default": ""}},
    }
    gen.validate(mutated, mutated_fields)
    assert gen.render_taxonomy_rules(mutated) != taxonomy_text
    mutated_feature = gen.render_feature_profiles(mutated)
    assert mutated_feature != feature_text
    for token in ('TEXT("Hologram")', 'TEXT("Holo|Beam")', 'TEXT("has_holo")', 'TEXT("HG_")'):
        assert token in mutated_feature
    mutated_domain = gen.render_domain_profiles(mutated, mutated_fields)
    assert mutated_domain != domain_text
    for token in ('TEXT("hologram")', 'TEXT("domain.hologram")', 'TEXT("Holo|Beam")',
                  'TEXT("hologram_beam")'):
        assert token in mutated_domain


def test_all_24_families_have_profile_rules_and_six_domain_profiles(
    gen: Any, rules: dict[str, Any], fields: dict[str, Any]
) -> None:
    families = [entry["name"] for entry in rules["families"]]
    assert len(families) == 24
    assert len(set(families)) == 24
    profile_families = [rule["family"] for rule in rules["profiles"]["rules"]]
    assert len(profile_families) == len(set(profile_families))
    assert set(families).issubset(set(profile_families))
    assert rules["fallback"] in profile_families

    feature_text = FEATURE_HEADER.read_text(encoding="utf-8")
    for family in families:
        assert f'{{ TEXT("{family}"), TEXT(' in feature_text

    domain = rules["domain_profiles"]
    assert domain["version"] == 1
    domain_rules = domain["rules"]
    assert len(domain_rules) == len(fields["families"]) == 6
    assert [rule["family"] for rule in domain_rules] == list(fields["families"])
    assert fields["families"]["common"]["applies_to"] == ["*"]
    assert len({rule["key_prefix"] for rule in domain_rules}) == len(domain_rules)
    profile_keys = {rule["family"]: rule["keys"] for rule in rules["profiles"]["rules"]}
    for rule in domain_rules:
        assert set(rule) == {
            "family", "taxonomy_family", "key_prefix", "tag_fragments", "priority",
        }
        assert rule["taxonomy_family"] in profile_keys
        assert rule["taxonomy_family"] == fields["families"][rule["family"]]["taxonomy_family"]
        assert rule["key_prefix"].startswith("domain.")
        assert rule["tag_fragments"]
        assert "*" not in rule["tag_fragments"]
        assert rule["tag_fragments"] == profile_keys[rule["taxonomy_family"]]
        assert rule["priority"] == fields["families"][rule["family"]]["priority"]

    domain_text = DOMAIN_HEADER.read_text(encoding="utf-8")
    for family_name, family in fields["families"].items():
        assert f'TEXT("{family_name}")' in domain_text
        for field_name in family["fields"]:
            assert f'TEXT("{field_name}")' in domain_text
    assert "GDomainProfileRuleTable[]" in domain_text
    assert "GDomainFieldRuleTable[]" in domain_text

    incomplete = copy.deepcopy(rules)
    incomplete["families"].append({"name": "Hologram", "match": ["Hologram"]})
    with pytest.raises(ValueError, match="profile"):
        gen.validate(incomplete)


def test_generator_rejects_invalid_domain_profiles(
    gen: Any, rules: dict[str, Any], fields: dict[str, Any]
) -> None:
    bad_version = copy.deepcopy(rules)
    bad_version["domain_profiles"]["version"] = 2
    with pytest.raises(ValueError, match="version"):
        gen.validate_domain_profiles(bad_version, fields)

    missing = copy.deepcopy(rules)
    missing["domain_profiles"]["rules"].pop()
    with pytest.raises(ValueError, match="cover every fields family"):
        gen.validate_domain_profiles(missing, fields)

    duplicate = copy.deepcopy(rules)
    duplicate["domain_profiles"]["rules"].append(
        copy.deepcopy(duplicate["domain_profiles"]["rules"][0])
    )
    with pytest.raises(ValueError, match="duplicate"):
        gen.validate_domain_profiles(duplicate, fields)

    bad_taxonomy = copy.deepcopy(rules)
    bad_taxonomy["domain_profiles"]["rules"][0]["taxonomy_family"] = "NoSuchFamily"
    with pytest.raises(ValueError, match="taxonomy_family"):
        gen.validate_domain_profiles(bad_taxonomy, fields)

    bad_prefix = copy.deepcopy(rules)
    bad_prefix["domain_profiles"]["rules"][0]["key_prefix"] = "feature.common"
    with pytest.raises(ValueError, match="domain"):
        gen.validate_domain_profiles(bad_prefix, fields)

    empty_fragments = copy.deepcopy(rules)
    empty_fragments["domain_profiles"]["rules"][0]["tag_fragments"] = ""
    with pytest.raises(ValueError, match="tag_fragments"):
        gen.validate_domain_profiles(empty_fragments, fields)

    wildcard = copy.deepcopy(rules)
    wildcard["domain_profiles"]["rules"][0]["tag_fragments"] = "Bounds|*"
    with pytest.raises(ValueError, match="tag_fragments"):
        gen.validate_domain_profiles(wildcard, fields)

    not_mirrored = copy.deepcopy(rules)
    not_mirrored["domain_profiles"]["rules"][0]["tag_fragments"] = "Bounds"
    with pytest.raises(ValueError, match="mirror"):
        gen.validate_domain_profiles(not_mirrored, fields)

    extra_key = copy.deepcopy(rules)
    extra_key["domain_profiles"]["rules"][0]["extra"] = 1
    with pytest.raises(ValueError, match="exactly five keys"):
        gen.validate_domain_profiles(extra_key, fields)

    bad_fields = copy.deepcopy(fields)
    del bad_fields["families"]["common"]["taxonomy_family"]
    with pytest.raises(ValueError, match="taxonomy_family"):
        gen.validate_domain_profiles(rules, bad_fields)


def test_generated_headers_are_byte_stable(
    gen: Any, rules: dict[str, Any], fields: dict[str, Any],
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert gen.render_taxonomy_rules(rules) == gen.render_taxonomy_rules(copy.deepcopy(rules))
    assert gen.render_feature_profiles(rules) == gen.render_feature_profiles(copy.deepcopy(rules))
    assert gen.render_domain_profiles(rules, fields) == gen.render_domain_profiles(
        copy.deepcopy(rules), copy.deepcopy(fields)
    )
    expected = {rel: renderer(rules, fields) for rel, renderer in gen.RENDERERS}

    for rel, text in expected.items():
        on_disk = (REPO / rel).read_bytes()
        assert on_disk == text.encode("utf-8")
        assert b"\r\n" not in on_disk
        assert not re.search(rb"[A-Za-z]:[\\/]", on_disk)
        assert not re.search(rb"20\d\d-\d\d-\d\d", on_disk)

    root = _temp_root(tmp_path)
    monkeypatch.setattr(gen, "ROOT", root)
    monkeypatch.setattr(sys, "argv", ["gen_taxonomy.py"])
    assert gen.main() == 0
    first = {rel: (root / rel).read_bytes() for rel, _ in gen.RENDERERS}
    assert gen.main() == 0
    second = {rel: (root / rel).read_bytes() for rel, _ in gen.RENDERERS}
    assert first == second
    assert first == {rel: text.encode("utf-8") for rel, text in expected.items()}


def test_check_detects_drift_for_all_three_headers(
    gen: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _temp_root(tmp_path)
    monkeypatch.setattr(gen, "ROOT", root)
    monkeypatch.setattr(sys, "argv", ["gen_taxonomy.py"])
    assert gen.main() == 0
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["gen_taxonomy.py", "--check"])

    assert gen.main() == 0
    clean = capsys.readouterr()
    assert clean.out.count("OK:") == 3
    assert f"OK: {TAXONOMY_RELATIVE} matches {RULES_RELATIVE}" in clean.out
    assert f"OK: {DOMAIN_RELATIVE} matches {RULES_RELATIVE}" in clean.out

    for rel, _ in gen.RENDERERS:
        path = root / rel
        original = path.read_text(encoding="utf-8")
        path.write_text(original + "// drift\n", encoding="utf-8", newline="\n")
        assert gen.main() == 1
        drift = capsys.readouterr()
        assert "DRIFT" in drift.err
        assert str(rel) in drift.err
        path.write_text(original, encoding="utf-8", newline="\n")
        capsys.readouterr()

        path.unlink()
        assert gen.main() == 1
        missing = capsys.readouterr()
        assert "missing" in missing.err
        assert str(rel) in missing.err
        path.write_text(original, encoding="utf-8", newline="\n")
        capsys.readouterr()


def test_generator_cli_output_names_all_three_headers(gen: Any, tmp_path: Path) -> None:
    root = _temp_root(tmp_path)
    tools = root / "Tools"
    tools.mkdir()
    script = tools / "gen_taxonomy.py"
    shutil.copyfile(GEN_SCRIPT, script)

    written = _cli(script)
    assert written.returncode == 0, written.stdout + written.stderr
    assert written.stdout.count("WROTE:") == 3
    for rel, _ in gen.RENDERERS:
        assert str(rel) in written.stdout
        assert (root / rel).read_bytes() == (REPO / rel).read_bytes()

    checked = _cli(script, "--check")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert checked.stdout.count("OK:") == 3
    for rel, _ in gen.RENDERERS:
        assert str(rel) in checked.stdout
    assert f"OK: {TAXONOMY_RELATIVE} matches {RULES_RELATIVE}" in checked.stdout

    for rel, _ in gen.RENDERERS:
        path = root / rel
        original = path.read_text(encoding="utf-8")
        path.write_text(original + "// drift\n", encoding="utf-8", newline="\n")
        drifted = _cli(script, "--check")
        assert drifted.returncode == 1
        assert "DRIFT" in drifted.stderr
        assert str(rel) in drifted.stderr
        path.write_text(original, encoding="utf-8", newline="\n")


def test_no_hardcoded_rule_tables_or_markers_remain(
    rules: dict[str, Any], fields: dict[str, Any]
) -> None:
    typed = TYPED_CPP.read_text(encoding="utf-8")
    universal = UNIVERSAL_CPP.read_text(encoding="utf-8")
    domain = DOMAIN_CPP.read_text(encoding="utf-8")
    both = typed + universal

    for source in (typed, universal):
        assert '#include "UALFeatureProfiles.h"' in source
    for old_name in ("GProfileRules", "GCapabilityRules", "GPrefixes"):
        assert old_name not in both
    assert "static const FFeatureProfileRule" not in both
    assert "static const FCapabilityRule" not in both
    assert "static const TCHAR* const GNamePrefixes" not in both

    for rule in rules["profiles"]["rules"]:
        assert rule["keys"] not in typed
    for rule in rules["capability_rules"]:
        assert rule["name"] not in typed
    for prefix in rules["prefixes"]:
        assert f'TEXT("{prefix}")' not in universal

    production = _production_only(universal)
    assert "GetAsset()" not in production
    assert "GetAsset()" not in typed
    for source in (typed, production, domain):
        assert "FiBData" in source and "Thumbnail" in source
        assert "VLM" not in source.upper()
        assert "GPU" not in source.upper()
        assert "embedding" not in source.lower()
    assert "TagKeys.Sort();" in typed
    assert "Pairs.Sort();" in production
    assert 'OutRecord.DomainSpec.Add(TEXT("feature.family")' in typed
    assert 'OutRecord.DomainSpec.Add(TEXT("feature.capabilities")' in typed
    assert 'OutRecord.DomainSpec.Add(TEXT("feature.profile_rule")' in typed
    assert "FUALFingerprint::MakeEmpty()" in production
    assert "ComputeNameFingerprint" in production
    assert "FUniversalReflectionExtractor Baseline;" in typed

    assert '#include "UALDomainFeatureExtractor.h"' == domain.splitlines()[0]
    assert '#include "UALDomainProfiles.h"' in domain
    assert "FUALTypedFeatureExtractor Baseline;" in domain
    assert "GDomainProfileRuleTableCount" in domain
    assert "GDomainFieldRuleTableCount" in domain
    assert "UEYaml::EmitDocument" in domain
    assert "static const" not in domain
    for banned in (
        "FString::Printf", "GetAsset(", "FDateTime", "FGuid::NewGuid",
        "FPlatformTime", "content_hash",
    ):
        assert banned not in domain
    for rule in rules["domain_profiles"]["rules"]:
        assert rule["family"] not in domain
        assert rule["key_prefix"] not in domain
        assert rule["tag_fragments"] not in domain
    for family_name, family in fields["families"].items():
        assert f'TEXT("{family_name}")' not in domain
        for field_name in family["fields"]:
            assert f'TEXT("{field_name}")' not in domain

    marker = "TODO" + "(human)"
    owned = (
        RULES, FIELDS, GEN_SCRIPT, TAXONOMY_HEADER, FEATURE_HEADER, DOMAIN_HEADER,
        DOMAIN_EXTRACTOR_HEADER, DOMAIN_CPP, MODULE_CPP, COMMANDLET_CPP, Path(__file__),
    )
    for path in owned:
        assert marker not in path.read_text(encoding="utf-8")
