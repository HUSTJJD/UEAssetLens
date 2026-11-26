#include "UALHostExecutor.h"

// Source-level implementation of the offline host executor contract. It uses
// only CoreMinimal value types: no host SDK, no toolset registration, no index
// writer, no asset writes, no binary export and no external service. Real UE
// host execution, real writes, compile / PIE, rescan and UE native binary diff
// remain spec_not_landed; this file claims no compile verification.

namespace UALHostExecutorText
{
	static bool IsOperationId(const FString& Text)
	{
		if (Text.IsEmpty() || Text.Len() > 64)
		{
			return false;
		}
		for (int32 Index = 0; Index < Text.Len(); ++Index)
		{
			const TCHAR Char = Text[Index];
			const bool bAlphaNumeric =
				(Char >= 'A' && Char <= 'Z') ||
				(Char >= 'a' && Char <= 'z') ||
				(Char >= '0' && Char <= '9');
			const bool bSeparator = Char == '_' || Char == '.' || Char == '-';
			if (!bAlphaNumeric && !bSeparator)
			{
				return false;
			}
		}
		return true;
	}

	static bool IsBareToolName(const FString& Text)
	{
		if (Text.IsEmpty() || Text.Len() > 64)
		{
			return false;
		}
		for (int32 Index = 0; Index < Text.Len(); ++Index)
		{
			const TCHAR Char = Text[Index];
			const bool bLetter = (Char >= 'A' && Char <= 'Z') || (Char >= 'a' && Char <= 'z');
			const bool bDigit = Index > 0 && Char >= '0' && Char <= '9';
			if (!bLetter && !bDigit)
			{
				return false;
			}
		}
		return true;
	}

	static bool IsForbiddenArgumentKey(const FString& Key)
	{
		return Key.Equals(TEXT("impl"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("handler"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("module"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("script"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("code"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("eval"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("exec"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("pickle"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("blob"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("bytes"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("binary"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("timestamp"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("updated_at"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("created_at"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("session_id"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("run_id"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("request_id"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("random_id"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("local_path"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("absolute_path"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("host_path"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("file_path"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("uuid"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("guid"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("nonce"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("date"), ESearchCase::IgnoreCase)
			|| Key.Equals(TEXT("time"), ESearchCase::IgnoreCase);
	}

	static bool HasForbiddenText(const FString& Text)
	{
		if (Text.Contains(TEXT("://")) || Text.Contains(TEXT(":/"))
			|| Text.Contains(TEXT("..")) || Text.Contains(TEXT("/home/"))
			|| Text.Contains(TEXT("/Users/")) || Text.Contains(TEXT("/tmp/"))
			|| Text.Contains(TEXT("/var/")) || Text.Contains(TEXT("ual_")))
		{
			return true;
		}
		for (int32 Index = 0; Index < Text.Len(); ++Index)
		{
			const TCHAR Char = Text[Index];
			if (Char < ' ' && Char != '\t' && Char != '\n')
			{
				return true;
			}
			if (Char == '\\')
			{
				return true;
			}
		}
		return false;
	}

	static FString Bounded(const FString& Text, int32 Limit = 160)
	{
		return Text.Left(FMath::Clamp(Limit, 1, 4096));
	}

	static bool LexLess(const FString& A, const FString& B)
	{
		return A.Compare(B, ESearchCase::CaseSensitive) < 0;
	}
}

FUALHostExecutorRegistry::FUALHostExecutorRegistry()
	: ToolsetExecutor()
	, ValidationRunner()
{
}

FUALHostExecutorRegistry::FUALHostExecutorRegistry(const TArray<FString>& InAllowlist)
	: ToolsetExecutor()
	, ValidationRunner()
{
	for (const FString& Tool : InAllowlist)
	{
		if (UALHostExecutorText::IsBareToolName(Tool) && !Allowlist.Contains(Tool))
		{
			Allowlist.Add(Tool);
		}
	}
	Allowlist.Sort(&UALHostExecutorText::LexLess);
}

bool FUALHostExecutorRegistry::RegisterToolsetExecutor(
	const TSharedPtr<IUALHostToolsetExecutor>& Executor)
{
	if (!Executor.IsValid())
	{
		return false;
	}
	ToolsetExecutor = Executor;
	return true;
}

bool FUALHostExecutorRegistry::RegisterValidationRunner(
	const TSharedPtr<IUALHostValidationRunner>& Runner)
{
	if (!Runner.IsValid())
	{
		return false;
	}
	ValidationRunner = Runner;
	return true;
}

void FUALHostExecutorRegistry::Clear()
{
	ToolsetExecutor.Reset();
	ValidationRunner.Reset();
}

bool FUALHostExecutorRegistry::IsFailClosed() const
{
	return !ToolsetExecutor.IsValid() && !ValidationRunner.IsValid();
}

bool FUALHostExecutorRegistry::IsAllowlisted(const FString& Tool) const
{
	return Allowlist.Contains(Tool);
}

bool FUALHostExecutorRegistry::HasExplicitPerOperationApproval(
	const FUALHostExecutionRequest& Request) const
{
	if (Request.PerOperationApprovals.Num() != Request.Operations.Num())
	{
		return false;
	}
	TSet<FString> Ids;
	for (const FUALHostOperation& Operation : Request.Operations)
	{
		if (!UALHostExecutorText::IsOperationId(Operation.Id) || Ids.Contains(Operation.Id))
		{
			return false;
		}
		Ids.Add(Operation.Id);
	}
	TSet<FString> Approvals;
	for (const FString& Id : Request.PerOperationApprovals)
	{
		if (Id.IsEmpty() || Approvals.Contains(Id))
		{
			return false;
		}
		Approvals.Add(Id);
	}
	for (const FUALHostOperation& Operation : Request.Operations)
	{
		if (!Approvals.Contains(Operation.Id))
		{
			return false;
		}
	}
	return true;
}

TArray<FString> FUALHostExecutorRegistry::SnapshotAllowlist() const
{
	TArray<FString> Out = Allowlist;
	Out.Sort(&UALHostExecutorText::LexLess);
	return Out;
}

bool FUALHostExecutorRegistry::IsHostOnlyCapability(EUALHostCapability Capability)
{
	switch (Capability)
	{
	case EUALHostCapability::Compile:
	case EUALHostCapability::PIE:
	case EUALHostCapability::Rescan:
	case EUALHostCapability::NativeDiff:
		return true;
	case EUALHostCapability::Toolset:
	default:
		return false;
	}
}

const TCHAR* FUALHostExecutorRegistry::CapabilityToString(EUALHostCapability Capability)
{
	switch (Capability)
	{
	case EUALHostCapability::Toolset: return TEXT("toolset");
	case EUALHostCapability::Compile: return TEXT("compile");
	case EUALHostCapability::PIE: return TEXT("PIE");
	case EUALHostCapability::Rescan: return TEXT("rescan");
	case EUALHostCapability::NativeDiff: return TEXT("native_diff");
	default: return TEXT("toolset");
	}
}

const TCHAR* FUALHostExecutorRegistry::StatusToString(EUALHostExecutionStatus Status)
{
	switch (Status)
	{
	case EUALHostExecutionStatus::Rejected: return TEXT("rejected");
	case EUALHostExecutionStatus::DryRun: return TEXT("dry_run");
	case EUALHostExecutionStatus::DeferredOffline: return TEXT("deferred_offline");
	case EUALHostExecutionStatus::OfflineAdapter: return TEXT("offline_adapter");
	case EUALHostExecutionStatus::Failed: return TEXT("failed");
	default: return TEXT("rejected");
	}
}

FUALHostExecutionResult FUALHostExecutorRegistry::Execute(
	const FUALHostExecutionRequest& Request) const
{
	FUALHostExecutionResult Result;
	Result.bDryRun = Request.bDryRun;
	Result.OperationCount = Request.Operations.Num();
	Result.Status = EUALHostExecutionStatus::Rejected;

	const int32 MaxRows = FMath::Clamp(Request.MaxRows, 1, 64);
	if (Request.Operations.Num() == 0 || Request.Operations.Num() > 64)
	{
		Result.Reason = TEXT("operation_count_invalid");
		return Result;
	}
	if (Request.ApprovedBy.IsEmpty() || UALHostExecutorText::HasForbiddenText(Request.ApprovedBy))
	{
		Result.Reason = TEXT("approved_by_required");
		return Result;
	}
	if (!HasExplicitPerOperationApproval(Request))
	{
		Result.Reason = TEXT("explicit_per_operation_approval_required");
		return Result;
	}
	for (const FUALHostOperation& Operation : Request.Operations)
	{
		if (!UALHostExecutorText::IsBareToolName(Operation.Tool) || !IsAllowlisted(Operation.Tool))
		{
			Result.Reason = TEXT("tool_not_allowlisted");
			return Result;
		}
		for (const TPair<FString, FString>& Pair : Operation.Arguments)
		{
			if (UALHostExecutorText::IsForbiddenArgumentKey(Pair.Key)
				|| UALHostExecutorText::HasForbiddenText(Pair.Key)
				|| UALHostExecutorText::HasForbiddenText(Pair.Value))
			{
				Result.Reason = TEXT("argument_rejected");
				return Result;
			}
		}
	}

	// Bounded rows are built before any delegation; by default no adapter runs.
	TArray<FUALHostExecutionRow> Rows;
	Rows.Reserve(Request.Operations.Num());
	for (const FUALHostOperation& Operation : Request.Operations)
	{
		FUALHostExecutionRow Row;
		Row.Id = UALHostExecutorText::Bounded(Operation.Id, 80);
		Row.Tool = UALHostExecutorText::Bounded(Operation.Tool, 80);
		Row.Capability = FString(CapabilityToString(Operation.Capability));
		Row.bDryRun = Request.bDryRun;
		Rows.Add(MoveTemp(Row));
	}
	Result.Returned = FMath::Min(Rows.Num(), MaxRows);
	Result.bTruncated = Rows.Num() > MaxRows;
	if (Result.bTruncated)
	{
		Rows.SetNum(MaxRows);
	}

	if (Request.bDryRun)
	{
		for (FUALHostExecutionRow& Row : Rows)
		{
			Row.Status = FString(StatusToString(EUALHostExecutionStatus::DryRun));
			Row.Reason = TEXT("dry_run_no_host_call");
		}
		Result.Rows = MoveTemp(Rows);
		Result.Status = EUALHostExecutionStatus::DryRun;
		Result.Reason = TEXT("dry_run_no_host_call");
		return Result;
	}

	bool bHostOnly = false;
	EUALHostCapability HostCapability = EUALHostCapability::Toolset;
	for (const FUALHostOperation& Operation : Request.Operations)
	{
		if (IsHostOnlyCapability(Operation.Capability))
		{
			bHostOnly = true;
			HostCapability = Operation.Capability;
			break;
		}
	}
	if (bHostOnly)
	{
		FUALHostValidationRequest ValidationRequest;
		ValidationRequest.Capability = HostCapability;
		ValidationRequest.ApprovedBy = Request.ApprovedBy;
		ValidationRequest.bDryRun = true;
		for (const FUALHostOperation& Operation : Request.Operations)
		{
			ValidationRequest.OperationIds.Add(Operation.Id);
		}
		FString ValidationReason;
		if (!ValidationRunner.IsValid()
			|| !ValidationRunner->RunValidation(ValidationRequest, ValidationReason))
		{
			for (FUALHostExecutionRow& Row : Rows)
			{
				Row.Status = FString(StatusToString(EUALHostExecutionStatus::Rejected));
				Row.Reason = TEXT("injected_validation_runner_required");
			}
			Result.Rows = MoveTemp(Rows);
			Result.Reason = TEXT("injected_validation_runner_required");
			return Result;
		}
		// Even a validated host-only capability is not delegated. The real
		// host capability is spec_not_landed, never a fake execution.
		for (FUALHostExecutionRow& Row : Rows)
		{
			Row.Status = FString(StatusToString(EUALHostExecutionStatus::DeferredOffline));
			Row.Reason = TEXT("real_host_capability_spec_not_landed");
		}
		Result.Rows = MoveTemp(Rows);
		Result.Status = EUALHostExecutionStatus::DeferredOffline;
		Result.Reason = TEXT("real_host_capability_spec_not_landed");
		return Result;
	}

	if (!ToolsetExecutor.IsValid())
	{
		for (FUALHostExecutionRow& Row : Rows)
		{
			Row.Status = FString(StatusToString(EUALHostExecutionStatus::DeferredOffline));
			Row.Reason = TEXT("host_toolset_executor_not_injected");
		}
		Result.Rows = MoveTemp(Rows);
		Result.Status = EUALHostExecutionStatus::DeferredOffline;
		Result.Reason = TEXT("host_toolset_executor_not_injected");
		return Result;
	}

	const FUALHostExecutionResult AdapterResult = ToolsetExecutor->Execute(Request);
	TArray<FUALHostExecutionRow> AdapterRows;
	for (const FUALHostExecutionRow& Source : AdapterResult.Rows)
	{
		FUALHostExecutionRow Row = Source;
		Row.Id = UALHostExecutorText::Bounded(Row.Id, 80);
		Row.Tool = UALHostExecutorText::Bounded(Row.Tool, 80);
		Row.Capability = UALHostExecutorText::Bounded(Row.Capability, 40);
		Row.Status = UALHostExecutorText::Bounded(Row.Status, 40);
		Row.Reason = UALHostExecutorText::Bounded(Row.Reason, 160);
		Row.ArgumentsSha256 = UALHostExecutorText::Bounded(Row.ArgumentsSha256, 80);
		Row.ResultStatus = UALHostExecutorText::Bounded(Row.ResultStatus, 40);
		AdapterRows.Add(MoveTemp(Row));
		if (AdapterRows.Num() >= MaxRows)
		{
			break;
		}
	}
	Result.bTruncated = Result.bTruncated || AdapterResult.Rows.Num() > AdapterRows.Num();
	Result.Rows = MoveTemp(AdapterRows);
	Result.Returned = Result.Rows.Num();
	Result.ArtifactPointer = UALHostExecutorText::Bounded(AdapterResult.ArtifactPointer, 160);
	Result.CanonicalSha256 = UALHostExecutorText::Bounded(AdapterResult.CanonicalSha256, 80);
	Result.Status = EUALHostExecutionStatus::OfflineAdapter;
	Result.Reason = TEXT("offline_adapter_result");
	return Result;
}

FUALHostExecutionResult FUALHostExecutorRegistry::ValidateHostCapability(
	const FUALHostValidationRequest& Request) const
{
	FUALHostExecutionResult Result;
	Result.Status = EUALHostExecutionStatus::Rejected;
	Result.bDryRun = Request.bDryRun;
	if (!IsHostOnlyCapability(Request.Capability))
	{
		Result.Reason = TEXT("capability_is_not_host_validation");
		return Result;
	}
	if (!Request.bDryRun)
	{
		Result.Reason = TEXT("host_validation_requires_dry_run");
		return Result;
	}
	if (Request.ApprovedBy.IsEmpty())
	{
		Result.Reason = TEXT("approved_by_required");
		return Result;
	}
	if (!ValidationRunner.IsValid())
	{
		Result.Reason = TEXT("injected_validation_runner_required");
		return Result;
	}
	FString Reason;
	if (!ValidationRunner->RunValidation(Request, Reason))
	{
		Result.Reason = UALHostExecutorText::Bounded(Reason, 160);
		return Result;
	}
	Result.Status = EUALHostExecutionStatus::DryRun;
	Result.Reason = TEXT("real_host_capability_spec_not_landed");
	return Result;
}
