#pragma once

#include "CoreMinimal.h"

// ---------------------------------------------------------------------------
// Source-level, offline-only host executor / host validation contract.
//
// This Runtime header is the C++ mirror of
// Server/ual_common/host_executor.py. It declares value types, two injected
// interfaces and one fail-closed registry. The default registry has no
// executor, no validation runner and an empty allowlist; a request is
// delegated only after explicit per-operation approval, an allowlist hit and
// a dry-run check. Host-only capabilities (compile / PIE / rescan / native
// diff) additionally require an injected validation runner and are never
// delegated to the toolset executor.
//
// Deliberately depends on CoreMinimal only: no host SDK headers, no toolset
// registration, no index writer, no external service, no asset writes, no
// binary export, no UHT reflection and no new module dependency. Deterministic
// metadata fields (ArtifactPointer / CanonicalSha256 / ArgumentsSha256) are
// supplied by an injected offline adapter as text; this module computes no
// clock values, local paths or random identifiers.
//
// Status: offline / source-level adapter contract only. Real UE host
// execution, real writes, compile / PIE, rescan and UE native binary diff
// remain specification / not landed. No compile verification is claimed.
// ---------------------------------------------------------------------------

/** Declared capability classes; host-only ones are never delegated. */
enum class EUALHostCapability : uint8
{
	Toolset,
	Compile,
	PIE,
	Rescan,
	NativeDiff
};

/** Fail-closed outcome set; OfflineAdapter means a fake injected adapter. */
enum class EUALHostExecutionStatus : uint8
{
	Rejected,
	DryRun,
	DeferredOffline,
	OfflineAdapter,
	Failed
};

/** One bounded operation; arguments are flat data strings only. */
struct UEASSETLENS_API FUALHostOperation
{
	FString Id;
	FString Tool;
	EUALHostCapability Capability = EUALHostCapability::Toolset;
	TMap<FString, FString> Arguments;
};

/** Approval, dry-run and paging inputs for one bounded host request. */
struct UEASSETLENS_API FUALHostExecutionRequest
{
	TArray<FUALHostOperation> Operations;
	FString ApprovedBy;
	TArray<FString> PerOperationApprovals;
	bool bDryRun = true;
	int32 MaxRows = 32;
};

/** Always a dry-run validation request; never a real compile / PIE start. */
struct UEASSETLENS_API FUALHostValidationRequest
{
	EUALHostCapability Capability = EUALHostCapability::Toolset;
	TArray<FString> OperationIds;
	FString ApprovedBy;
	bool bDryRun = true;
};

/** One bounded canonical result row; text is supplied by the offline adapter. */
struct UEASSETLENS_API FUALHostExecutionRow
{
	FString Id;
	FString Tool;
	FString Capability;
	FString Status;
	FString Reason;
	FString ArgumentsSha256;
	FString ResultStatus;
	bool bDryRun = true;
};

/** Bounded, deterministic result metadata; no filesystem side effects. */
struct UEASSETLENS_API FUALHostExecutionResult
{
	EUALHostExecutionStatus Status = EUALHostExecutionStatus::Rejected;
	FString Reason;
	FString ArtifactPointer;
	FString CanonicalSha256;
	bool bDryRun = true;
	int32 OperationCount = 0;
	int32 Returned = 0;
	bool bTruncated = false;
	TArray<FUALHostExecutionRow> Rows;
};

/** Injected offline toolset adapter contract; UAL supplies no real one. */
class UEASSETLENS_API IUALHostToolsetExecutor
{
public:
	virtual ~IUALHostToolsetExecutor() = default;

	/** Stable offline adapter marker; no path or clock value. */
	virtual FString GetAdapterKind() const = 0;

	/** Must stay a deterministic dry-run adapter; never calls a real host. */
	virtual FUALHostExecutionResult Execute(const FUALHostExecutionRequest& Request) = 0;
};

/** Injected offline validation adapter contract; UAL supplies no real one. */
class UEASSETLENS_API IUALHostValidationRunner
{
public:
	virtual ~IUALHostValidationRunner() = default;

	/** Stable offline runner marker; no path or clock value. */
	virtual FString GetRunnerKind() const = 0;

	/** Dry-run validation only; returns false with a bounded reason. */
	virtual bool RunValidation(const FUALHostValidationRequest& Request, FString& OutReason) const = 0;
};

/** Fail-closed registry of injected adapters plus an immutable allowlist. */
class UEASSETLENS_API FUALHostExecutorRegistry
{
public:
	FUALHostExecutorRegistry();
	explicit FUALHostExecutorRegistry(const TArray<FString>& InAllowlist);

	/** Registration is opt-in; a null pointer is refused and never enabled. */
	bool RegisterToolsetExecutor(const TSharedPtr<IUALHostToolsetExecutor>& Executor);
	bool RegisterValidationRunner(const TSharedPtr<IUALHostValidationRunner>& Runner);

	/** Return to the fail-closed default; the allowlist is immutable. */
	void Clear();

	bool IsFailClosed() const;
	bool IsAllowlisted(const FString& Tool) const;
	bool HasExplicitPerOperationApproval(const FUALHostExecutionRequest& Request) const;
	TArray<FString> SnapshotAllowlist() const;

	/** Gate approval / allowlist / dry-run; never delegates host-only ops. */
	FUALHostExecutionResult Execute(const FUALHostExecutionRequest& Request) const;

	/** Gate a host-only capability; never starts a real host job. */
	FUALHostExecutionResult ValidateHostCapability(const FUALHostValidationRequest& Request) const;

	static bool IsHostOnlyCapability(EUALHostCapability Capability);
	static const TCHAR* CapabilityToString(EUALHostCapability Capability);
	static const TCHAR* StatusToString(EUALHostExecutionStatus Status);

private:
	TArray<FString> Allowlist;
	TSharedPtr<IUALHostToolsetExecutor> ToolsetExecutor;
	TSharedPtr<IUALHostValidationRunner> ValidationRunner;
};
