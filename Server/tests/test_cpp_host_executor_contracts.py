"""Static C++ source contracts for the offline host executor interface.

These checks read source text only. They do not invoke a UE SDK, compiler,
linker, editor process or runtime test; C++ compilation remains unverified.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "Server"
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

from ual_common import host_executor as host  # noqa: E402

HEADER = REPO / "Source" / "UEAssetLens" / "Public" / "UALHostExecutor.h"
CPP = REPO / "Source" / "UEAssetLens" / "Private" / "UALHostExecutor.cpp"
BUILD_CS = REPO / "Source" / "UEAssetLens" / "UEAssetLens.Build.cs"

FORBIDDEN = (
    "GetAsset(", "FHttpModule", "ISocket", "FSocket", "FDateTime",
    "FGuid::NewGuid", "FPlatformTime", "FString::Printf", "UEditorAssetLibrary",
    "CompileBlueprint", "StartPIE", "PlayInEditor", "LoadObject",
    "StaticLoadObject", "GEditor", "FAssetRegistryModule", "AssetRegistry",
    "ModelContextProtocol", "UnrealEd", "Sockets", "Http", "DuckDB",
    "duckdb", "socket", "GPU", "VLM", "network", "cloud", "import",
    "UCLASS", "GENERATED_BODY",
    ".generated.h", "#include <", "import socket", "urllib", "requests.",
    "D:" + "/", "C:" + "\\", "TODO" + "(human)",
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_header_and_cpp_are_source_level_without_forbidden_includes_or_calls() -> None:
    header = _text(HEADER)
    cpp = _text(CPP)
    assert header.startswith("#pragma once\n")
    assert "#pragma once" in header
    assert cpp.splitlines()[0] == '#include "UALHostExecutor.h"'
    assert re.findall(r'^\s*#include\s+"([^"]+)"', header, re.MULTILINE) == ["CoreMinimal.h"]
    assert re.findall(r'^\s*#include\s+"([^"]+)"', cpp, re.MULTILINE) == ["UALHostExecutor.h"]
    for source in (header, cpp):
        for token in FORBIDDEN:
            assert token not in source, token
        assert not re.search(r"[A-Za-z]:[\\/]", source)
        assert "TODO" + "(human)" not in source


def test_interface_naming_and_fail_closed_defaults() -> None:
    header = _text(HEADER)
    cpp = _text(CPP)
    assert "enum class EUALHostCapability : uint8" in header
    assert "enum class EUALHostExecutionStatus : uint8" in header
    for name in (
        "FUALHostOperation", "FUALHostExecutionRequest", "FUALHostValidationRequest",
        "FUALHostExecutionRow", "FUALHostExecutionResult",
    ):
        assert f"struct UEASSETLENS_API {name}" in header
    assert "class UEASSETLENS_API IUALHostToolsetExecutor" in header
    assert "class UEASSETLENS_API IUALHostValidationRunner" in header
    assert "class UEASSETLENS_API FUALHostExecutorRegistry" in header
    assert "virtual FUALHostExecutionResult Execute(const FUALHostExecutionRequest& Request) = 0;" in header
    assert "virtual bool RunValidation(const FUALHostValidationRequest& Request, FString& OutReason) const = 0;" in header

    declared = re.findall(
        r"^(?:class|struct|enum class)\s+(?:UEASSETLENS_API\s+)?(\w+)",
        header,
        re.MULTILINE,
    )
    assert declared
    assert all(re.fullmatch(r"[EFI]UAL\w+", name) for name in declared), declared

    assert "bool bDryRun = true;" in header
    assert "Status = EUALHostExecutionStatus::Rejected" in header
    assert "PerOperationApprovals" in header
    assert "bool IsFailClosed() const;" in header
    assert "bool IsAllowlisted(const FString& Tool) const;" in header
    assert "bool HasExplicitPerOperationApproval(const FUALHostExecutionRequest& Request) const;" in header
    assert "ToolsetExecutor.Reset();" in cpp
    assert "ValidationRunner.Reset();" in cpp
    assert "RegisterToolsetExecutor" in cpp
    assert "RegisterValidationRunner" in cpp
    for capability in ("Compile", "PIE", "Rescan", "NativeDiff"):
        assert f"EUALHostCapability::{capability}" in cpp
    for reason in (
        "injected_validation_runner_required",
        "host_toolset_executor_not_injected",
        "dry_run_no_host_call",
        "real_host_capability_spec_not_landed",
        "offline_adapter_result",
    ):
        assert reason in cpp


def test_no_new_build_cs_dependency_and_status_stays_honest() -> None:
    header = _text(HEADER)
    cpp = _text(CPP)
    build = _text(BUILD_CS)
    includes = re.findall(r'^\s*#include\s+"([^"]+)"', header + "\n" + cpp, re.MULTILINE)
    assert includes == ["CoreMinimal.h", "UALHostExecutor.h"]
    assert "UALHostExecutor" not in build
    assert "Core" in build and "CoreUObject" in build
    assert "offline / source-level adapter contract only" in header
    assert "specification / not landed" in header
    assert "No compile verification is claimed." in header
    assert "spec_not_landed" in cpp
    assert "claims no compile verification" in cpp
    assert "is implemented" not in header.lower()
    assert "is implemented" not in cpp.lower()


def test_python_and_cpp_contracts_share_capability_names() -> None:
    cpp = _text(CPP)
    header = _text(HEADER)
    assert host.CAPABILITIES == ("toolset", "compile", "PIE", "rescan", "native_diff")
    assert host.HOST_ONLY_CAPABILITIES == frozenset({"compile", "PIE", "rescan", "native_diff"})
    for capability in host.CAPABILITIES:
        needle = f'TEXT("{capability}")'
        assert needle in cpp, capability
    assert "IsHostOnlyCapability" in header
    assert "IsHostOnlyCapability" in cpp
    assert "ValidateHostCapability" in header
    assert "ValidateHostCapability" in cpp
