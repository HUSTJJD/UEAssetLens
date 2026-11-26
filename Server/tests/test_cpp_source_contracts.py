"""Static C++ source contracts for the declarative M2 domain extractor.

These checks read source text only. They do not invoke a UE SDK, compiler,
linker, editor process or runtime test; C++ compilation remains unverified.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
PUBLIC = REPO / "Source" / "UEAssetLensEditor" / "Public"
PRIVATE = REPO / "Source" / "UEAssetLensEditor" / "Private"
RUNTIME_PRIVATE = REPO / "Source" / "UEAssetLens" / "Private"
MCP_PRIVATE = REPO / "Source" / "UEAssetLensMCP" / "Private"
RULES = REPO / "Server" / "ual_common" / "taxonomy.yaml"
FIELDS = REPO / "Config" / "UAL" / "fields" / "default.yaml"

FEATURE_HEADER = PUBLIC / "UALFeatureProfiles.h"
DOMAIN_HEADER = PUBLIC / "UALDomainProfiles.h"
EXTRACTOR_HEADER = PUBLIC / "UALDomainFeatureExtractor.h"
DOMAIN_CPP = PRIVATE / "UALDomainFeatureExtractor.cpp"
MODULE_CPP = PRIVATE / "UEAssetLensEditorModule.cpp"
COMMANDLET_CPP = PRIVATE / "UALIndexBuilderCommandlet.cpp"
DUCKDB_CPP = PRIVATE / "UALDuckDbIndexSink.cpp"
EXTENSION_REGISTRY_CPP = RUNTIME_PRIVATE / "UALExtensionRegistry.cpp"
MCP_MODULE_CPP = MCP_PRIVATE / "UALEngineMcpModule.cpp"
MCP_TOOL_CPP = MCP_PRIVATE / "UALEngineMcpTool.cpp"

NEW_HEADERS = (DOMAIN_HEADER, EXTRACTOR_HEADER)
OWNED_CXX_FILES = (
    FEATURE_HEADER, DOMAIN_HEADER, EXTRACTOR_HEADER,
    DOMAIN_CPP, MODULE_CPP, COMMANDLET_CPP,
)
FORBIDDEN = (
    "GetAsset(",
    "FDateTime",
    "FGuid::NewGuid",
    "FPlatformTime",
    "FString::Printf",
    "TODO" + "(human)",
)
LOCAL_DRIVE_RE = re.compile(r"[A-Za-z]:[\\/]")
UNIX_PREFIXES = (
    "/" + "Users/", "/" + "home/", "/" + "tmp/", "/" + "var/", "/" + "Volumes/",
)
NAMESPACES = (
    "asset_type", "policy", "field", "diff", "tool",
    "lifecycle", "sink", "template", "gateway",
)


RUNTIME_PUBLIC = REPO / "Source" / "UEAssetLens" / "Public"
TYPES_H = RUNTIME_PUBLIC / "UALTypes.h"
YAML_SINK_CPP = PRIVATE / "UALYamlIndexSink.cpp"
TAXONOMY_HEADER = PUBLIC / "UALTaxonomyRules.h"
GENERATED_HEADERS = (TAXONOMY_HEADER, FEATURE_HEADER, DOMAIN_HEADER)
CONTRACT_TESTS_CPP = (
    REPO / "Source" / "UEAssetLensTests" / "Private" / "UALContractTests.cpp"
)

STAGING_KEYS = (
    "asset_path", "logical_id", "asset_class", "parent_class", "taxonomy",
    "tokens", "content_hash", "duration", "bounds_size", "skeleton_path",
    "dominant_limb", "fingerprint_vec", "interface_spec", "domain_spec",
    "yaml_cache", "edges",
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_new_headers_start_with_pragma_once_and_cpp_has_own_include_first() -> None:
    for header in NEW_HEADERS:
        assert _text(header).startswith("#pragma once\n")
        assert "#pragma once" in _text(header)
    for header in (FEATURE_HEADER, DOMAIN_HEADER, EXTRACTOR_HEADER):
        assert "#pragma once" in _text(header)
    assert _text(DOMAIN_CPP).splitlines()[0] == '#include "UALDomainFeatureExtractor.h"'
    assert "Priority() const override { return 110; }" in _text(EXTRACTOR_HEADER)


def test_owned_cxx_sources_have_no_volatile_or_loading_patterns() -> None:
    for path in OWNED_CXX_FILES:
        source = _text(path)
        for forbidden in FORBIDDEN:
            assert forbidden not in source, (path.name, forbidden)
        assert not LOCAL_DRIVE_RE.search(source), path.name
        for prefix in UNIX_PREFIXES:
            assert prefix not in source, (path.name, prefix)
    domain = _text(DOMAIN_CPP)
    assert "content_hash" not in domain
    assert '"domain_spec"' not in domain
    assert "UEYaml::EmitDocument" in domain
    assert "FUALTypedFeatureExtractor Baseline;" in domain
    assert "if (!Baseline.Extract(AssetData, OutRecord))" in domain
    assert "AssetData.IsValid()" in domain
    assert "GDomainProfileRuleTableCount" in domain
    assert "GDomainFieldRuleTableCount" in domain
    assert "ActorMetaData" in domain
    assert "static const" not in domain
    assert "GDomainProfileRuleTable[] = {" not in domain
    assert "GDomainFieldRuleTable[] = {" not in domain


def test_generated_domain_header_has_six_families_and_all_field_rules() -> None:
    rules = _yaml(RULES)
    fields = _yaml(FIELDS)
    families = fields["families"]
    assert len(families) == 6
    domain_rules = rules["domain_profiles"]["rules"]
    assert len(domain_rules) == len(families)
    header = _text(DOMAIN_HEADER)
    assert "struct FUALDomainProfileRule" in header
    assert "struct FUALDomainFieldRule" in header
    assert "GDomainProfileRuleTable[]" in header
    assert "GDomainFieldRuleTable[]" in header
    for family_name, family in families.items():
        assert f'TEXT("{family_name}")' in header
        for field_name, spec in family["fields"].items():
            assert f'TEXT("{field_name}")' in header
            assert f'TEXT("{spec["key"]}")' in header
    for rule in domain_rules:
        assert f'TEXT("{rule["family"]}")' in header
        assert f'TEXT("{rule["key_prefix"]}")' in header
        assert f'TEXT("{rule["tag_fragments"]}")' in header
    assert 'TEXT("*")' in header  # common applicability comes from the fields config


def test_shared_registry_registers_domain_extractor_once() -> None:
    module = _text(MODULE_CPP)
    commandlet = _text(COMMANDLET_CPP)
    assert '#include "UALDomainFeatureExtractor.h"' in module
    assert "bBuiltinExtractorsRegistered" in module
    assert "MakeShared<FUALDomainFeatureExtractor>()" in module
    assert "GetSharedExtractorRegistry()" in commandlet
    assert "FUALExtractorRegistry Registry" not in commandlet
    assert "BuildSixteenKeyDoc" in module
    assert "RegisterAssetRegistryCallbacks" in module


def test_extension_registry_keeps_nine_fixed_namespaces() -> None:
    source = _text(EXTENSION_REGISTRY_CPP)
    match = re.search(r"namespace UALExtensionNames\s*\{(.*?)\n\}", source, re.DOTALL)
    assert match is not None
    found = re.findall(r'TEXT\("([a-z_]+)"\)', match.group(1))
    assert found == list(NAMESPACES)
    assert "UE_ARRAY_COUNT(Names)" in source


def test_engine_mcp_annotations_and_async_contracts() -> None:
    module = _text(MCP_MODULE_CPP)
    assert 'TryGetStringField(TEXT("accessMode")' in module
    assert 'TryGetBoolField(TEXT("readOnlyHint")' in module
    assert 'TryGetStringField(TEXT("readOnlyHint")' in module
    assert "NormalizeAccessMode" in module
    tool = _text(MCP_TOOL_CPP)
    assert "FUALEngineMcpTool::RunAsync" in tool
    assert "Async(EAsyncExecution::ThreadPool" in tool


def test_duckdb_include_is_guarded() -> None:
    source = _text(DUCKDB_CPP)
    assert "#if UAL_WITH_DUCKDB" in source
    include_re = re.compile(r"^\s*#\s*include\s*<duckdb\.h>", re.MULTILINE)
    includes = list(include_re.finditer(source))
    assert includes
    for match in includes:
        prefix = source[:match.start()]
        opens = len(re.findall(r"^\s*#\s*if\s+UAL_WITH_DUCKDB\b", prefix, re.MULTILINE))
        closes = len(re.findall(r"^\s*#\s*endif\b", prefix, re.MULTILINE))
        assert opens > closes


def test_generated_headers_are_deterministic_and_declared() -> None:
    rules = _yaml(RULES)
    fields = _yaml(FIELDS)
    family_names = [item["name"] for item in rules["families"]]

    for header in GENERATED_HEADERS:
        source = _text(header)
        assert "#pragma once" in source, header.name
        assert "AUTO-GENERATED by Tools/gen_taxonomy.py" in source, header.name
        assert "DO NOT EDIT" in source, header.name
        for forbidden in FORBIDDEN:
            assert forbidden not in source, (header.name, forbidden)
        assert not LOCAL_DRIVE_RE.search(source), header.name
        for prefix in UNIX_PREFIXES:
            assert prefix not in source, (header.name, prefix)

    taxonomy = _text(TAXONOMY_HEADER)
    feature = _text(FEATURE_HEADER)
    domain = _text(DOMAIN_HEADER)

    taxonomy_block = re.search(r"GRules\[\] = \{(.*?)\n\s*\};", taxonomy, re.S)
    profile_block = re.search(r"GProfileRuleTable\[\] = \{(.*?)\n\s*\};", feature, re.S)
    domain_block = re.search(r"GDomainProfileRuleTable\[\] = \{(.*?)\n\s*\};", domain, re.S)
    assert taxonomy_block is not None and profile_block is not None and domain_block is not None

    taxonomy_rows = re.findall(
        r"^\s*\{ TEXT\(\"([^\"]+)\"\), GPatterns_", taxonomy_block.group(1), re.M
    )
    profile_rows = re.findall(
        r"^\s*\{ TEXT\(\"([^\"]+)\"\), TEXT\(\"[^\"]+\"\) \}", profile_block.group(1), re.M
    )
    domain_rows = re.findall(
        r"^\s*\{ TEXT\(\"([^\"]+)\"\), TEXT\(\"[^\"]*\"\), TEXT\(\"[^\"]+\"\), TEXT\(\"[^\"]*\"\), \d+ \}",
        domain_block.group(1), re.M,
    )
    assert taxonomy_rows == family_names
    assert profile_rows == family_names + ["Misc"]
    assert domain_rows == list(fields["families"])
    assert "GRuleCount = UE_ARRAY_COUNT(GRules)" in taxonomy
    assert "GProfileRuleTableCount = UE_ARRAY_COUNT(GProfileRuleTable)" in feature
    assert "GDomainProfileRuleTableCount" in domain
    assert "GDomainFieldRuleTableCount" in domain


def test_cpp_mirror_and_yaml_sink_keep_frozen_16_keys_and_null_fingerprint() -> None:
    types_h = _text(TYPES_H)
    assert "UEAssetLens v3.0" in types_h
    assert "tokens VARCHAR" in types_h
    assert "tokens VARCHAR[]" not in types_h
    assert "mirror version 3.0" in types_h
    assert "SetNumZeroed(16)" not in types_h
    mirror_keys = re.search(r"// staging keys: ([a-z_,]+)", types_h)
    assert mirror_keys is not None
    assert tuple(mirror_keys.group(1).split(",")) == STAGING_KEYS
    assert "updated_at" not in STAGING_KEYS and len(STAGING_KEYS) == 16

    sink = _text(YAML_SINK_CPP)
    emitted = re.findall(
        r"^\s*Obj->Set(?:String|Number|Array|Object)?Field\(TEXT\(\"([a-z_]+)\"\)",
        sink, re.M,
    )
    assert tuple(dict.fromkeys(emitted)) == STAGING_KEYS
    assert "updated_at" not in emitted
    assert "FString::Printf" not in sink
    assert "UEYaml::EmitDocument" in sink and "UEYaml::EmitStream" in sink
    assert "MakeShared<FJsonValueNull>()" in sink
    assert "Record.FingerprintVec.Num() == 16" in sink
    assert "IsValidIndex(i) ? Record.FingerprintVec[i] : 0.0f" not in sink
    assert "SetNumZeroed(16)" not in sink


def test_ue_automation_contract_test_source_is_present() -> None:
    assert CONTRACT_TESTS_CPP.is_file()
    source = _text(CONTRACT_TESTS_CPP)
    assert "#if WITH_DEV_AUTOMATION_TESTS" in source
    assert "#endif // WITH_DEV_AUTOMATION_TESTS" in source
    # UALFingerprintTests.cpp already provides the module entry point.
    assert "IMPLEMENT_MODULE" not in source
    for name in (
        "UEAssetLens.Contracts.Registry",
        "UEAssetLens.Contracts.DomainProfiles",
        "UEAssetLens.Contracts.SixteenKeys",
    ):
        assert name in source
    assert "FUALExtensionRegistry::Namespaces()" in source
    assert "GDomainProfileRuleTableCount" in source
    assert "FUALFingerprint::Dim" in source
    assert "FUALAssetRecord" in source
    assert "FingerprintVec" in source
    assert "updated_at is not a staging key" in source
    for forbidden in FORBIDDEN:
        assert forbidden not in source, forbidden
    assert not LOCAL_DRIVE_RE.search(source)
    for prefix in UNIX_PREFIXES:
        assert prefix not in source
