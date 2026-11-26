#include "UALDomainFeatureExtractor.h"

#include "UALDomainProfiles.h"
#include "UALTypedFeatureExtractor.h"
#include "UEYaml.h"

#include "AssetRegistry/AssetData.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"

namespace
{
	struct FTagEntry
	{
		FString Key;
		FString Raw;
	};

	bool LexLess(const FString& A, const FString& B)
	{
		return A.Compare(B, ESearchCase::CaseSensitive) < 0;
	}

	FString ShortClassName(const FAssetData& AssetData)
	{
		FString Name = AssetData.AssetClassPath.ToString();
		int32 DotIndex = INDEX_NONE;
		if (Name.FindLastChar(TEXT('.'), DotIndex))
		{
			Name = Name.Mid(DotIndex + 1);
		}
		return Name;
	}

	bool ProfileApplies(const UALDomainProfiles::FUALDomainProfileRule& Rule,
		const FString& AssetClass)
	{
		const FString AppliesTo(Rule.AppliesTo);
		if (AppliesTo == TEXT("*"))
		{
			return true;
		}
		TArray<FString> ExactClasses;
		AppliesTo.ParseIntoArray(ExactClasses, TEXT("|"), true);
		for (const FString& Exact : ExactClasses)
		{
			if (Exact.Equals(AssetClass, ESearchCase::CaseSensitive))
			{
				return true;
			}
		}
		return false;
	}

	bool HasUnsafeControl(const FString& Text)
	{
		for (int32 Index = 0; Index < Text.Len(); ++Index)
		{
			const TCHAR Ch = Text[Index];
			if (Ch < 0x20 || (Ch >= 0x7F && Ch <= 0x9F))
			{
				return true;
			}
		}
		return false;
	}

	bool LooksLikeLocalAbsolutePath(const FString& Text)
	{
		const TCHAR Backslash = static_cast<TCHAR>(0x5C);
		if (Text.Len() >= 2 && Text[0] == Backslash && Text[1] == Backslash)
		{
			return true;
		}
		for (int32 Index = 0; Index + 1 < Text.Len(); ++Index)
		{
			if (Text[Index] == TEXT(':') &&
				(Text[Index + 1] == Backslash || Text[Index + 1] == TEXT('/')))
			{
				return true;
			}
		}
		return Text.Contains(TEXT("Users/"), ESearchCase::IgnoreCase)
			|| Text.Contains(TEXT("home/"), ESearchCase::IgnoreCase)
			|| Text.Contains(TEXT("tmp/"), ESearchCase::IgnoreCase)
			|| Text.Contains(TEXT("var/"), ESearchCase::IgnoreCase)
			|| Text.Contains(TEXT("Volumes/"), ESearchCase::IgnoreCase);
	}

	bool HasDateLike(const FString& Text)
	{
		for (int32 Index = 0; Index + 9 < Text.Len(); ++Index)
		{
			const bool bDigits =
				FChar::IsDigit(Text[Index]) && FChar::IsDigit(Text[Index + 1]) &&
				FChar::IsDigit(Text[Index + 2]) && FChar::IsDigit(Text[Index + 3]) &&
				Text[Index + 4] == TEXT('-') &&
				FChar::IsDigit(Text[Index + 5]) && FChar::IsDigit(Text[Index + 6]) &&
				Text[Index + 7] == TEXT('-') &&
				FChar::IsDigit(Text[Index + 8]) && FChar::IsDigit(Text[Index + 9]);
			if (bDigits)
			{
				return true;
			}
		}
		return false;
	}

	bool IsHexDigit(TCHAR Ch)
	{
		return FChar::IsDigit(Ch)
			|| (Ch >= TEXT('a') && Ch <= TEXT('f'))
			|| (Ch >= TEXT('A') && Ch <= TEXT('F'));
	}

	bool HasGuidLike(const FString& Text)
	{
		int32 HexRun = 0;
		int32 Groups = 0;
		for (int32 Index = 0; Index < Text.Len(); ++Index)
		{
			const TCHAR Ch = Text[Index];
			if (IsHexDigit(Ch))
			{
				++HexRun;
				if (HexRun >= 32)
				{
					return true;
				}
				continue;
			}
			if (Ch == TEXT('-') && HexRun > 0)
			{
				++Groups;
				HexRun = 0;
				if (Groups >= 4)
				{
					return true;
				}
				continue;
			}
			if (Groups >= 3 && HexRun >= 4)
			{
				return true;
			}
			HexRun = 0;
			Groups = 0;
		}
		return Groups >= 3 && HexRun >= 4;
	}

	bool IsStableTagText(const FString& Text)
	{
		return !Text.IsEmpty()
			&& !HasUnsafeControl(Text)
			&& !LooksLikeLocalAbsolutePath(Text)
			&& !HasDateLike(Text)
			&& !HasGuidLike(Text);
	}

	FString SanitizeText(const FString& In)
	{
		if (!IsStableTagText(In))
		{
			return FString();
		}
		FString Out;
		Out.Reserve(FMath::Min(In.Len(), 96));
		for (int32 Index = 0; Index < In.Len() && Out.Len() < 96; ++Index)
		{
			const TCHAR Ch = In[Index];
			if (Ch < 0x20 || (Ch >= 0x7F && Ch <= 0x9F))
			{
				Out.AppendChar(TEXT(' '));
			}
			else
			{
				Out.AppendChar(Ch);
			}
		}
		Out.TrimStartAndEndInline();
		return Out;
	}

	TArray<FTagEntry> CollectSafeTags(const FAssetData& AssetData)
	{
		TArray<FTagEntry> Out;
		for (const auto& Tag : AssetData.TagsAndValues)
		{
			const FString Key = Tag.Key.ToString();
			const FString Raw = Tag.Value.AsString();
			if (Key.IsEmpty() || Raw.IsEmpty())
			{
				continue;
			}
			// Binary editor metadata can carry C1 controls; host paths, dates and
			// generated identifiers are excluded from every emitted value.
			if (Key.Equals(TEXT("FiBData"), ESearchCase::IgnoreCase)
				|| Key.Equals(TEXT("Thumbnail"), ESearchCase::IgnoreCase)
				|| Key.Equals(TEXT("ActorMetaData"), ESearchCase::IgnoreCase)
				|| !IsStableTagText(Key) || !IsStableTagText(Raw))
			{
				continue;
			}
			FTagEntry Entry;
			Entry.Key = SanitizeText(Key);
			Entry.Raw = SanitizeText(Raw);
			if (!Entry.Key.IsEmpty() && !Entry.Raw.IsEmpty())
			{
				Out.Add(MoveTemp(Entry));
			}
		}
		Out.Sort([](const FTagEntry& A, const FTagEntry& B)
		{
			return LexLess(A.Key, B.Key);
		});
		return Out;
	}

	bool TagMatchesFragments(const FString& Key, const FString& TagFragments)
	{
		TArray<FString> Fragments;
		FString(TagFragments).ParseIntoArray(Fragments, TEXT("|"), true);
		for (const FString& Fragment : Fragments)
		{
			if (Key.Contains(Fragment, ESearchCase::IgnoreCase))
			{
				return true;
			}
		}
		return false;
	}

	bool IsNumericText(const FString& Text)
	{
		bool bHasDigit = false;
		for (int32 Index = 0; Index < Text.Len(); ++Index)
		{
			const TCHAR Ch = Text[Index];
			if (FChar::IsDigit(Ch))
			{
				bHasDigit = true;
			}
			else if (Ch != TEXT('.') && Ch != TEXT('-') && Ch != TEXT('+')
				&& Ch != TEXT('e') && Ch != TEXT('E') && Ch != TEXT(' '))
			{
				return false;
			}
		}
		return bHasDigit;
	}

	double ClampFinite(double Value)
	{
		return FMath::IsFinite(Value) ? FMath::Clamp(Value, -100000.0, 100000.0) : 0.0;
	}

	double Round4(double Value)
	{
		const double Safe = ClampFinite(Value);
		return static_cast<double>(FMath::RoundToInt(Safe * 10000.0)) / 10000.0;
	}

	double SafeFinite(double Value)
	{
		return FMath::IsFinite(Value) ? Value : 0.0;
	}

	FString Fixed4(double Value)
	{
		const double Safe = ClampFinite(Value);
		const bool bNegative = Safe < 0.0;
		const int32 Scaled = FMath::RoundToInt(FMath::Abs(Safe) * 10000.0);
		const int32 Whole = Scaled / 10000;
		FString FractionText = FString::FromInt(Scaled % 10000);
		while (FractionText.Len() < 4)
		{
			FractionText = FString(TEXT("0")) + FractionText;
		}
		const FString Sign = bNegative ? FString(TEXT("-")) : FString();
		return Sign + FString::FromInt(Whole) + TEXT(".") + FractionText;
	}

	FString EmitTagKeys(const TArray<FString>& Keys)
	{
		TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
		TArray<TSharedPtr<FJsonValue>> Items;
		const int32 Count = FMath::Min(Keys.Num(), 12);
		Items.Reserve(Count);
		for (int32 Index = 0; Index < Count; ++Index)
		{
			Items.Add(MakeShared<FJsonValueString>(SanitizeText(Keys[Index])));
		}
		Root->SetArrayField(TEXT("items"), Items);
		return UEYaml::EmitDocument(Root);
	}

	FString EmitNumericStats(int32 Count, double Mean, double Min, double Max, double Sum)
	{
		TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
		Root->SetNumberField(TEXT("count"), static_cast<double>(Count));
		Root->SetNumberField(TEXT("mean"), SafeFinite(Round4(Mean)));
		Root->SetNumberField(TEXT("min"), SafeFinite(Round4(Min)));
		Root->SetNumberField(TEXT("max"), SafeFinite(Round4(Max)));
		Root->SetNumberField(TEXT("sum"), SafeFinite(Round4(Sum)));
		return UEYaml::EmitDocument(Root);
	}

	FString EmitYamlField(const FString& Candidate, const FString& DefaultText)
	{
		TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
		const FString Safe = SanitizeText(Candidate);
		const bool bUseDefault = Safe.IsEmpty()
			|| Safe.Equals(DefaultText, ESearchCase::CaseSensitive);
		// Parse only the generated, well-formed default fragment. Arbitrary Tag
		// text is wrapped as one bounded string item rather than parsed.
		if (bUseDefault)
		{
			const TSharedPtr<FJsonObject> Parsed = UEYaml::ParseDocument(DefaultText);
			if (Parsed.IsValid())
			{
				const TArray<TSharedPtr<FJsonValue>>* Array = nullptr;
				if (Parsed->TryGetArrayField(TEXT("items"), Array) && Array != nullptr)
				{
					Root->SetArrayField(TEXT("items"), *Array);
					return UEYaml::EmitDocument(Root);
				}
				const TSharedPtr<FJsonObject>* Object = nullptr;
				if (Parsed->TryGetObjectField(TEXT("items"), Object)
					&& Object != nullptr && Object->IsValid())
				{
					Root->SetObjectField(TEXT("items"), *Object);
					return UEYaml::EmitDocument(Root);
				}
			}
		}
		TArray<TSharedPtr<FJsonValue>> Items;
		if (!Safe.IsEmpty())
		{
			Items.Add(MakeShared<FJsonValueString>(Safe));
		}
		Root->SetArrayField(TEXT("items"), Items);
		return UEYaml::EmitDocument(Root);
	}

	FString BooleanText(const FString& Candidate, const FString& DefaultText)
	{
		const FString Safe = SanitizeText(Candidate);
		if (Safe.Equals(TEXT("true"), ESearchCase::IgnoreCase) || Safe == TEXT("1"))
		{
			return FString(TEXT("true"));
		}
		if (Safe.Equals(TEXT("false"), ESearchCase::IgnoreCase) || Safe == TEXT("0"))
		{
			return FString(TEXT("false"));
		}
		const FString Fallback = SanitizeText(DefaultText);
		return Fallback.Equals(TEXT("true"), ESearchCase::IgnoreCase)
			? FString(TEXT("true")) : FString(TEXT("false"));
	}

	FString IntegerText(const FString& Candidate, const FString& DefaultText)
	{
		FString Source = SanitizeText(Candidate);
		if (!IsNumericText(Source))
		{
			Source = SanitizeText(DefaultText);
		}
		if (!IsNumericText(Source))
		{
			return FString(TEXT("0"));
		}
		return FString::FromInt(FMath::TruncToInt(ClampFinite(FCString::Atod(*Source))));
	}

	FString NumberText(const FString& Candidate, const FString& DefaultText)
	{
		FString Source = SanitizeText(Candidate);
		if (!IsNumericText(Source))
		{
			Source = SanitizeText(DefaultText);
		}
		return IsNumericText(Source) ? Fixed4(FCString::Atod(*Source)) : Fixed4(0.0);
	}

	FString StringText(const FString& Candidate, const FString& DefaultText)
	{
		FString Safe = SanitizeText(Candidate);
		if (Safe.IsEmpty())
		{
			Safe = SanitizeText(DefaultText);
		}
		return Safe;
	}

	FString CoerceFieldValue(const FString& ValueType, const FString& Candidate,
		const FString& DefaultText)
	{
		if (ValueType == TEXT("boolean"))
		{
			return BooleanText(Candidate, DefaultText);
		}
		if (ValueType == TEXT("integer"))
		{
			return IntegerText(Candidate, DefaultText);
		}
		if (ValueType == TEXT("number"))
		{
			return NumberText(Candidate, DefaultText);
		}
		if (ValueType == TEXT("yaml"))
		{
			return EmitYamlField(Candidate, DefaultText);
		}
		return StringText(Candidate, DefaultText);
	}

	FString NormalizedFragment(const FString& Text)
	{
		FString Out;
		for (int32 Index = 0; Index < Text.Len(); ++Index)
		{
			const TCHAR Ch = Text[Index];
			if (!FChar::IsAlnum(Ch))
			{
				continue;
			}
			if (FChar::IsUpper(Ch))
			{
				Out.AppendChar(static_cast<TCHAR>(Ch + (TEXT('a') - TEXT('A'))));
			}
			else
			{
				Out.AppendChar(Ch);
			}
		}
		return Out;
	}

	FString FindFieldCandidate(const FString& SourceKey, const TArray<FTagEntry>& Tags,
		const TMap<FString, FString>& Baseline)
	{
		const FString* Exact = Baseline.Find(SourceKey);
		if (Exact != nullptr && IsStableTagText(*Exact))
		{
			return SanitizeText(*Exact);
		}
		const FString Needle = NormalizedFragment(SourceKey);
		if (!Needle.IsEmpty())
		{
			for (const FTagEntry& Tag : Tags)
			{
				if (Tag.Key.Contains(SourceKey, ESearchCase::IgnoreCase)
					|| NormalizedFragment(Tag.Key).Contains(Needle, ESearchCase::CaseSensitive))
				{
					return Tag.Raw;
				}
			}
		}
		return FString();
	}

	void AppendFnv1a(uint64& Hash, const FString& Text)
	{
		const uint64 Prime = 1099511628211ULL;
		for (int32 Index = 0; Index < Text.Len(); ++Index)
		{
			const uint32 Code = static_cast<uint32>(Text[Index]);
			Hash ^= (Code & 0xFFu);
			Hash *= Prime;
			Hash ^= ((Code >> 8) & 0xFFu);
			Hash *= Prime;
		}
	}

	FString Hex16(uint64 Hash)
	{
		FString Out;
		for (int32 Shift = 60; Shift >= 0; Shift -= 4)
		{
			const uint8 Nibble = static_cast<uint8>((Hash >> Shift) & 0xFULL);
			Out.AppendChar(Nibble < 10
				? static_cast<TCHAR>(TEXT('0') + Nibble)
				: static_cast<TCHAR>(TEXT('a') + Nibble - 10));
		}
		return Out;
	}

	// Deterministic 16-hex FNV-1a over generated rules, class and sorted keys.
	FString ProfileFingerprint(const UALDomainProfiles::FUALDomainProfileRule& Rule,
		const FString& AssetClass, const TArray<FString>& MatchingKeys)
	{
		uint64 Hash = 14695981039346656037ULL;
		AppendFnv1a(Hash, FString(Rule.DomainFamily));
		AppendFnv1a(Hash, FString(Rule.AppliesTo));
		AppendFnv1a(Hash, FString(Rule.KeyPrefix));
		AppendFnv1a(Hash, FString(Rule.TagFragments));
		AppendFnv1a(Hash, FString::FromInt(Rule.Priority));
		AppendFnv1a(Hash, AssetClass);
		for (const FString& Key : MatchingKeys)
		{
			AppendFnv1a(Hash, Key);
		}
		for (int32 Index = 0; Index < UALDomainProfiles::GDomainFieldRuleTableCount; ++Index)
		{
			const UALDomainProfiles::FUALDomainFieldRule& Field =
				UALDomainProfiles::GDomainFieldRuleTable[Index];
			if (!FString(Field.DomainFamily).Equals(FString(Rule.DomainFamily),
				ESearchCase::CaseSensitive))
			{
				continue;
			}
			AppendFnv1a(Hash, FString(Field.FieldName));
			AppendFnv1a(Hash, FString(Field.ValueType));
			AppendFnv1a(Hash, FString(Field.SourceKey));
			AppendFnv1a(Hash, FString(Field.DefaultText));
			AppendFnv1a(Hash, FString::FromInt(Field.Priority));
		}
		return Hex16(Hash);
	}
}

bool FUALDomainFeatureExtractor::CanExtract(const FAssetData& AssetData) const
{
	if (!AssetData.IsValid())
	{
		return false;
	}
	const FString AssetClass = ShortClassName(AssetData);
	for (int32 Index = 0; Index < UALDomainProfiles::GDomainProfileRuleTableCount; ++Index)
	{
		if (ProfileApplies(UALDomainProfiles::GDomainProfileRuleTable[Index], AssetClass))
		{
			return true;
		}
	}
	return false;
}

bool FUALDomainFeatureExtractor::Extract(const FAssetData& AssetData, FUALAssetRecord& OutRecord)
{
	// The typed extractor owns the 16-key baseline and the existing feature.*
	// vocabulary. Never replace it; a failure must fall through to the registry.
	FUALTypedFeatureExtractor Baseline;
	if (!Baseline.Extract(AssetData, OutRecord))
	{
		return false;
	}

	const FString AssetClass = ShortClassName(AssetData);
	const TArray<FTagEntry> SafeTags = CollectSafeTags(AssetData);
	bool bAnyProfile = false;

	for (int32 ProfileIndex = 0;
		ProfileIndex < UALDomainProfiles::GDomainProfileRuleTableCount; ++ProfileIndex)
	{
		const UALDomainProfiles::FUALDomainProfileRule& Rule =
			UALDomainProfiles::GDomainProfileRuleTable[ProfileIndex];
		if (!ProfileApplies(Rule, AssetClass))
		{
			continue;
		}
		bAnyProfile = true;

		TArray<FString> MatchingKeys;
		int32 NumericCount = 0;
		double NumericSum = 0.0;
		double NumericMin = 0.0;
		double NumericMax = 0.0;
		for (const FTagEntry& Tag : SafeTags)
		{
			if (!TagMatchesFragments(Tag.Key, FString(Rule.TagFragments)))
			{
				continue;
			}
			MatchingKeys.AddUnique(Tag.Key);
			if (!IsNumericText(Tag.Raw))
			{
				continue;
			}
			const double Number = ClampFinite(FCString::Atod(*Tag.Raw));
			const double NextSum = NumericSum + Number;
			if (!FMath::IsFinite(Number) || !FMath::IsFinite(NextSum))
			{
				continue;
			}
			++NumericCount;
			NumericSum = NextSum;
			if (NumericCount == 1)
			{
				NumericMin = Number;
				NumericMax = Number;
			}
			else
			{
				NumericMin = FMath::Min(NumericMin, Number);
				NumericMax = FMath::Max(NumericMax, Number);
			}
		}
		MatchingKeys.Sort(&LexLess);
		const double Mean = (NumericCount > 0)
			? (NumericSum / static_cast<double>(NumericCount)) : 0.0;
		const FString Prefix(Rule.KeyPrefix);

		OutRecord.DomainSpec.Add(Prefix + TEXT(".tag_count"), FString::FromInt(MatchingKeys.Num()));
		OutRecord.DomainSpec.Add(Prefix + TEXT(".tag_keys"), EmitTagKeys(MatchingKeys));
		OutRecord.DomainSpec.Add(Prefix + TEXT(".numeric_stats"),
			EmitNumericStats(NumericCount, Mean, NumericMin, NumericMax, NumericSum));
		OutRecord.DomainSpec.Add(Prefix + TEXT(".profile_sha256"),
			ProfileFingerprint(Rule, AssetClass, MatchingKeys));

		for (int32 FieldIndex = 0;
			FieldIndex < UALDomainProfiles::GDomainFieldRuleTableCount; ++FieldIndex)
		{
			const UALDomainProfiles::FUALDomainFieldRule& Field =
				UALDomainProfiles::GDomainFieldRuleTable[FieldIndex];
			if (!FString(Field.DomainFamily).Equals(FString(Rule.DomainFamily),
				ESearchCase::CaseSensitive))
			{
				continue;
			}
			const FString Candidate = FindFieldCandidate(FString(Field.SourceKey),
				SafeTags, OutRecord.DomainSpec);
			const FString Value = CoerceFieldValue(FString(Field.ValueType),
				Candidate, FString(Field.DefaultText));
			OutRecord.DomainSpec.Add(Prefix + TEXT(".") + FString(Field.FieldName), Value);
		}

		OutRecord.Tokens.AddUnique(FString(Rule.DomainFamily).ToLower());
		OutRecord.Tokens.AddUnique(Prefix);
	}
	return bAnyProfile;
}
