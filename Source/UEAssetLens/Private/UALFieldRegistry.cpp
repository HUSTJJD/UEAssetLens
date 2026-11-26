#include "UALFieldRegistry.h"

namespace UALFieldDetail
{
	static bool LexLess(const FString& A, const FString& B)
	{
		return A.Compare(B, ESearchCase::CaseSensitive) < 0;
	}

	static bool TryNumber(const FString& Text, double& OutValue)
	{
		if (Text.IsEmpty())
		{
			return false;
		}
		bool bDigit = false;
		bool bDot = false;
		bool bExponent = false;
		for (int32 Index = 0; Index < Text.Len(); ++Index)
		{
			const TCHAR Ch = Text[Index];
			if (FChar::IsDigit(Ch))
			{
				bDigit = true;
				continue;
			}
			if ((Ch == TEXT('+') || Ch == TEXT('-')) &&
				(Index == 0 || Text[Index - 1] == TEXT('e') || Text[Index - 1] == TEXT('E')))
			{
				continue;
			}
			if (Ch == TEXT('.') && !bDot && !bExponent)
			{
				bDot = true;
				continue;
			}
			if ((Ch == TEXT('e') || Ch == TEXT('E')) && bDigit && !bExponent)
			{
				bExponent = true;
				continue;
			}
			return false;
		}
		if (!bDigit)
		{
			return false;
		}
		const double Value = FCString::Atod(*Text);
		if (!FMath::IsFinite(Value))
		{
			return false;
		}
		OutValue = Value;
		return true;
	}

	static bool TryBoolean(const FString& Text, bool& OutValue)
	{
		if (Text.Equals(TEXT("true"), ESearchCase::IgnoreCase))
		{
			OutValue = true;
			return true;
		}
		if (Text.Equals(TEXT("false"), ESearchCase::IgnoreCase))
		{
			OutValue = false;
			return true;
		}
		return false;
	}

	static bool IsIntegerText(const FString& Text)
	{
		return !Text.Contains(TEXT(".")) && !Text.Contains(TEXT("e")) && !Text.Contains(TEXT("E"));
	}

	static bool LooksLikeYamlFragment(const FString& Text)
	{
		return Text.StartsWith(TEXT("[")) || Text.StartsWith(TEXT("{")) || Text.Contains(TEXT(": "));
	}

	static FString InferValueType(const FString& Text, FUALExtensionField& Field)
	{
		double Number = 0.0;
		bool bBoolean = false;
		if (TryBoolean(Text, bBoolean))
		{
			Field.ValueBool = bBoolean;
			return FString(TEXT("boolean"));
		}
		if (TryNumber(Text, Number))
		{
			Field.ValueNumber = Number;
			return FString(IsIntegerText(Text) ? TEXT("integer") : TEXT("number"));
		}
		if (LooksLikeYamlFragment(Text))
		{
			return FString(TEXT("yaml"));
		}
		return FString(TEXT("string"));
	}
}

FUALFieldRegistry& FUALFieldRegistry::Get()
{
	static FUALFieldRegistry Instance;
	return Instance;
}

bool FUALFieldRegistry::IsValidValueType(const FString& ValueType)
{
	return ValueType == TEXT("string") || ValueType == TEXT("integer") ||
		ValueType == TEXT("number") || ValueType == TEXT("boolean") || ValueType == TEXT("yaml");
}

bool FUALFieldRegistry::Register(const FUALFieldSpec& Spec)
{
	if (!FUALExtensionRegistry::IsValidNamespace(Spec.Namespace) || Spec.FieldName.IsEmpty() ||
		Spec.AppliesTo.Num() == 0 || !IsValidValueType(Spec.ValueType))
	{
		return false;
	}
	const FString Key = Spec.Key();
	if (Specs.Contains(Key))
	{
		return false;
	}
	Specs.Add(Key, Spec);
	return true;
}

bool FUALFieldRegistry::Unregister(const FString& Key)
{
	return Specs.Remove(Key) > 0;
}

TArray<FUALFieldSpec> FUALFieldRegistry::Resolve(const FString& AssetClass) const
{
	struct FCandidate
	{
		FUALFieldSpec Spec;
		bool bExact = false;
	};

	TArray<FString> Keys;
	Specs.GetKeys(Keys);
	Keys.Sort(&UALFieldDetail::LexLess);

	TArray<FCandidate> Candidates;
	for (const FString& Key : Keys)
	{
		const FUALFieldSpec* Spec = Specs.Find(Key);
		if (Spec == nullptr)
		{
			continue;
		}
		const bool bWildcard = Spec->AppliesTo.Contains(TEXT("*"));
		bool bMatchedClass = false;
		for (const FString& Applies : Spec->AppliesTo)
		{
			if (Applies == AssetClass)
			{
				bMatchedClass = true;
				break;
			}
		}
		if (!bWildcard && !bMatchedClass)
		{
			continue;
		}

		FCandidate Candidate;
		Candidate.Spec = *Spec;
		Candidate.bExact = bMatchedClass && !bWildcard && !AssetClass.IsEmpty();
		Candidates.Add(MoveTemp(Candidate));
	}

	Candidates.Sort([](const FCandidate& A, const FCandidate& B)
	{
		if (A.bExact != B.bExact)
		{
			return A.bExact;
		}
		if (A.Spec.Priority != B.Spec.Priority)
		{
			return A.Spec.Priority > B.Spec.Priority;
		}
		return UALFieldDetail::LexLess(A.Spec.Key(), B.Spec.Key());
	});

	TArray<FUALFieldSpec> Result;
	Result.Reserve(Candidates.Num());
	for (const FCandidate& Candidate : Candidates)
	{
		Result.Add(Candidate.Spec);
	}
	return Result;
}

TArray<FUALFieldSpec> FUALFieldRegistry::Snapshot() const
{
	TArray<FString> Keys;
	Specs.GetKeys(Keys);
	Keys.Sort(&UALFieldDetail::LexLess);

	TArray<FUALFieldSpec> Result;
	Result.Reserve(Keys.Num());
	for (const FString& Key : Keys)
	{
		const FUALFieldSpec* Spec = Specs.Find(Key);
		if (Spec != nullptr)
		{
			Result.Add(*Spec);
		}
	}
	return Result;
}

int32 FUALFieldRegistry::ImportFrom(const FUALExtensionRegistry& Extensions)
{
	int32 Imported = 0;
	for (const FUALExtensionSpec& Ext : Extensions.Snapshot())
	{
		if (Ext.Namespace != TEXT("field"))
		{
			continue;
		}
		FUALFieldSpec Spec;
		Spec.Namespace = Ext.Namespace;
		Spec.FieldName = Ext.Name;
		Spec.ValueType = IsValidValueType(Ext.SchemaText) ? Ext.SchemaText : FString(TEXT("string"));
		Spec.AppliesTo = Ext.AppliesTo;
		Spec.Version = Ext.Version;
		Spec.Priority = Ext.Priority;
		if (Register(Spec))
		{
			++Imported;
		}
	}
	return Imported;
}

void FUALFieldRegistry::Project(const FUALAssetRecord& Record, TArray<FUALExtensionField>& OutFields) const
{
	OutFields.Reset();
	const TArray<FUALFieldSpec> Applicable = Resolve(Record.AssetClass);

	auto FindSpec = [&Applicable](const FString& FieldName) -> const FUALFieldSpec*
	{
		for (const FUALFieldSpec& Spec : Applicable)
		{
			if (Spec.FieldName == FieldName)
			{
				return &Spec;
			}
		}
		for (const FUALFieldSpec& Spec : Applicable)
		{
			if (Spec.FieldName == TEXT("*"))
			{
				return &Spec;
			}
		}
		return nullptr;
	};

	auto AddField = [&Record, &FindSpec, &OutFields](const FString& FieldName,
		const FString& ValueText, const FString& Provenance)
	{
		const FUALFieldSpec* Spec = FindSpec(FieldName);
		FUALExtensionField Field;
		Field.AssetPath = Record.AssetPath;
		Field.Namespace = (Spec != nullptr) ? Spec->Namespace : FString(TEXT("field"));
		Field.FieldName = FieldName;
		Field.ValueText = ValueText;
		Field.Provenance = Provenance;

		if (Spec != nullptr && IsValidValueType(Spec->ValueType))
		{
			Field.ValueType = Spec->ValueType;
			if (Field.ValueType == TEXT("boolean"))
			{
				bool bBoolean = false;
				if (UALFieldDetail::TryBoolean(ValueText, bBoolean))
				{
					Field.ValueBool = bBoolean;
				}
			}
			else if (Field.ValueType == TEXT("integer") || Field.ValueType == TEXT("number"))
			{
				double Number = 0.0;
				if (UALFieldDetail::TryNumber(ValueText, Number))
				{
					Field.ValueNumber = Number;
				}
			}
		}
		else
		{
			Field.ValueType = UALFieldDetail::InferValueType(ValueText, Field);
		}
		OutFields.Add(MoveTemp(Field));
	};

	// 只读取 16 键记录里已有的两张 MAP；TMap 遍历顺序不稳定，先取键排序。
	// 按 (namespace, field_name) 去重，与 Python extension_payload 的主键一致；
	// 两张 MAP 出现同名键时优先采用更丰富的 domain_spec。
	TSet<FString> Emitted;
	auto SpecKeyFor = [&FindSpec](const FString& Key) -> FString
	{
		const FUALFieldSpec* Spec = FindSpec(Key);
		return (Spec != nullptr ? Spec->Namespace : FString(TEXT("field"))) + TEXT(".") + Key;
	};

	TArray<FString> DomainKeys;
	Record.DomainSpec.GetKeys(DomainKeys);
	DomainKeys.Sort(&UALFieldDetail::LexLess);
	for (const FString& Key : DomainKeys)
	{
		const FString* Value = Record.DomainSpec.Find(Key);
		if (Value == nullptr)
		{
			continue;
		}
		const FString SpecKey = SpecKeyFor(Key);
		if (Emitted.Contains(SpecKey))
		{
			continue;
		}
		AddField(Key, *Value, FString(TEXT("domain_spec")));
		Emitted.Add(SpecKey);
	}

	TArray<FString> InterfaceKeys;
	Record.InterfaceSpec.GetKeys(InterfaceKeys);
	InterfaceKeys.Sort(&UALFieldDetail::LexLess);
	for (const FString& Key : InterfaceKeys)
	{
		const FString* Value = Record.InterfaceSpec.Find(Key);
		if (Value == nullptr)
		{
			continue;
		}
		const FString SpecKey = SpecKeyFor(Key);
		if (Emitted.Contains(SpecKey))
		{
			continue;
		}
		AddField(Key, *Value, FString(TEXT("interface_spec")));
		Emitted.Add(SpecKey);
	}

	// 声明式规格的默认值补齐；来源标记固定，不含时间戳 / 路径。
	for (const FUALFieldSpec& Spec : Applicable)
	{
		if (Spec.DefaultValue.IsEmpty() || Spec.FieldName == TEXT("*") ||
			Emitted.Contains(Spec.Key()))
		{
			continue;
		}
		AddField(Spec.FieldName, Spec.DefaultValue, FString(TEXT("default")));
	}

	OutFields.Sort([](const FUALExtensionField& A, const FUALExtensionField& B)
	{
		if (A.AssetPath != B.AssetPath) { return UALFieldDetail::LexLess(A.AssetPath, B.AssetPath); }
		if (A.Namespace != B.Namespace) { return UALFieldDetail::LexLess(A.Namespace, B.Namespace); }
		if (A.FieldName != B.FieldName) { return UALFieldDetail::LexLess(A.FieldName, B.FieldName); }
		if (A.Provenance != B.Provenance) { return UALFieldDetail::LexLess(A.Provenance, B.Provenance); }
		return UALFieldDetail::LexLess(A.ValueText, B.ValueText);
	});
}
