#include "UALExtensionRegistry.h"

// 九个固定 namespace 与 Python registry.py::NAMESPACES 逐字一致。
// 这是命名空间白名单（不是业务规则表），位于具名命名空间而非匿名 namespace。
namespace UALExtensionNames
{
	static const TCHAR* Names[] =
	{
		TEXT("asset_type"),
		TEXT("policy"),
		TEXT("field"),
		TEXT("diff"),
		TEXT("tool"),
		TEXT("lifecycle"),
		TEXT("sink"),
		TEXT("template"),
		TEXT("gateway")
	};

	static const int32 Count = UE_ARRAY_COUNT(Names);

	static bool LexLess(const FString& A, const FString& B)
	{
		return A.Compare(B, ESearchCase::CaseSensitive) < 0;
	}
}

FUALExtensionRegistry& FUALExtensionRegistry::Get()
{
	// 函数内静态：Runtime 模块只加载一次，Editor 模块与 Commandlet 共享同一实例。
	static FUALExtensionRegistry Instance;
	return Instance;
}

const TArray<FString>& FUALExtensionRegistry::Namespaces()
{
	static const TArray<FString> Result = []()
	{
		TArray<FString> Out;
		Out.Reserve(UALExtensionNames::Count);
		for (int32 Index = 0; Index < UALExtensionNames::Count; ++Index)
		{
			Out.Add(FString(UALExtensionNames::Names[Index]));
		}
		return Out;
	}();
	return Result;
}

bool FUALExtensionRegistry::IsValidNamespace(const FString& Namespace)
{
	const TArray<FString>& All = Namespaces();
	for (const FString& Known : All)
	{
		if (Known == Namespace)
		{
			return true;
		}
	}
	return false;
}

bool FUALExtensionRegistry::Register(const FUALExtensionSpec& Spec,
	const TSharedPtr<IUALExtensionBinding>& Binding)
{
	if (!IsValidNamespace(Spec.Namespace) || Spec.Name.IsEmpty() || Spec.AppliesTo.Num() == 0)
	{
		return false;
	}

	const FString Key = Spec.Key();
	if (Entries.Contains(Key))
	{
		return false;
	}

	FEntry Entry;
	Entry.Spec = Spec;
	Entry.Binding = Binding;
	Entries.Add(Key, MoveTemp(Entry));
	return true;
}

bool FUALExtensionRegistry::Unregister(const FString& Key)
{
	return Entries.Remove(Key) > 0;
}

TArray<FUALExtensionSpec> FUALExtensionRegistry::Resolve(const FString& AssetClass,
	const FString& Namespace, const FString& Capability) const
{
	struct FCandidate
	{
		FUALExtensionSpec Spec;
		bool bExact = false;
	};

	TArray<FString> Keys;
	Entries.GetKeys(Keys);
	Keys.Sort(&UALExtensionNames::LexLess);

	TArray<FCandidate> Candidates;
	for (const FString& Key : Keys)
	{
		const FEntry* Entry = Entries.Find(Key);
		if (Entry == nullptr)
		{
			continue;
		}
		const FUALExtensionSpec& Spec = Entry->Spec;
		if (!Namespace.IsEmpty() && Spec.Namespace != Namespace)
		{
			continue;
		}
		if (!Capability.IsEmpty() && !Spec.Capabilities.Contains(Capability))
		{
			continue;
		}

		const bool bWildcard = Spec.AppliesTo.Contains(TEXT("*"));
		bool bExact = false;
		for (const FString& Applies : Spec.AppliesTo)
		{
			if (Applies == AssetClass)
			{
				bExact = true;
				break;
			}
		}
		if (!bWildcard && !bExact)
		{
			continue;
		}

		FCandidate Candidate;
		Candidate.Spec = Spec;
		Candidate.bExact = bExact && !bWildcard && !AssetClass.IsEmpty();
		Candidates.Add(MoveTemp(Candidate));
	}

	Candidates.Sort([](const FCandidate& A, const FCandidate& B)
	{
		if (A.bExact != B.bExact)
		{
			return A.bExact;                       // 精确匹配优先于通配
		}
		if (A.Spec.Priority != B.Spec.Priority)
		{
			return A.Spec.Priority > B.Spec.Priority; // 优先级降序
		}
		return UALExtensionNames::LexLess(A.Spec.Key(), B.Spec.Key()); // 键升序兜底
	});

	TArray<FUALExtensionSpec> Result;
	Result.Reserve(Candidates.Num());
	for (const FCandidate& Candidate : Candidates)
	{
		Result.Add(Candidate.Spec);
	}
	return Result;
}

TArray<FUALExtensionSpec> FUALExtensionRegistry::Snapshot() const
{
	TArray<FString> Keys;
	Entries.GetKeys(Keys);
	Keys.Sort(&UALExtensionNames::LexLess);

	TArray<FUALExtensionSpec> Result;
	Result.Reserve(Keys.Num());
	for (const FString& Key : Keys)
	{
		const FEntry* Entry = Entries.Find(Key);
		if (Entry != nullptr)
		{
			Result.Add(Entry->Spec);
		}
	}
	return Result;
}

TSharedPtr<IUALExtensionBinding> FUALExtensionRegistry::FindBinding(const FString& Key) const
{
	const FEntry* Entry = Entries.Find(Key);
	return (Entry != nullptr) ? Entry->Binding : TSharedPtr<IUALExtensionBinding>();
}

void FUALExtensionRegistry::Clear()
{
	Entries.Reset();
}
