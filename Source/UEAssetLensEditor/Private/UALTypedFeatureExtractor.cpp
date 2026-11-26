#include "UALTypedFeatureExtractor.h"

#include "FUniversalReflectionExtractor.h"
#include "UALFeatureProfiles.h"
#include "UALTaxonomyRules.h"
#include "UALTypes.h"

#include "AssetRegistry/AssetData.h"

DEFINE_LOG_CATEGORY_STATIC(LogUALTypedFeatures, Log, All);

namespace
{
	// The rule tables shared by both extractors (profile fragments, capability
	// rules, naming prefixes) live in the generated UALFeatureProfiles.h,
	// rendered from Server/ual_common/taxonomy.yaml. Profiles and capabilities
	// are AssetRegistry Tag *names*, never loaded UObject properties, so this
	// extractor stays strictly zero-load.

	FString FamilyFor(const FString& AssetClass)
	{
		using namespace UALTaxonomyRules;
		for (int32 i = 0; i < GRuleCount; ++i)
		{
			for (int32 j = 0; j < GRules[i].PatternCount; ++j)
			{
				if (AssetClass.Contains(GRules[i].Patterns[j], ESearchCase::CaseSensitive))
				{
					return FString(GRules[i].Name);
				}
			}
		}
		return FString(GFallback);
	}

	FString ProfileKeysFor(const FString& Family)
	{
		// The generated table has one rule per YAML family (plus the fallback),
		// so a newly added family can no longer silently lose its profile.
		for (int32 i = 0; i < UALFeatureProfiles::GProfileRuleTableCount; ++i)
		{
			const UALFeatureProfiles::FFeatureProfileRule& Rule =
				UALFeatureProfiles::GProfileRuleTable[i];
			if (Family.Equals(Rule.Family, ESearchCase::IgnoreCase))
			{
				return FString(Rule.Keys);
			}
		}
		return FString(UALFeatureProfiles::GDefaultProfileKeys);
	}

	bool IsNumericTag(const FString& Value)
	{
		if (Value.IsEmpty())
		{
			return false;
		}
		bool bDigit = false;
		for (int32 i = 0; i < Value.Len(); ++i)
		{
			const TCHAR Ch = Value[i];
			if (FChar::IsDigit(Ch))
			{
				bDigit = true;
			}
			else if (Ch != TEXT('.') && Ch != TEXT('-') && Ch != TEXT('+') &&
			         Ch != TEXT('e') && Ch != TEXT('E') && Ch != TEXT(' '))
			{
				return false;
			}
		}
		return bDigit;
	}

	int32 CountNameTokens(const FString& Name)
	{
		int32 Count = 0;
		bool bInToken = false;
		for (int32 i = 0; i < Name.Len(); ++i)
		{
			const TCHAR Ch = Name[i];
			if (FChar::IsAlnum(Ch))
			{
				// CamelCase boundary: a new uppercase after a lowercase/digit starts a token.
				const bool bBoundary = bInToken && FChar::IsUpper(Ch) &&
					(i > 0 && (FChar::IsLower(Name[i - 1]) || FChar::IsDigit(Name[i - 1])));
				if (!bInToken || bBoundary)
				{
					++Count;
				}
				bInToken = true;
			}
			else
			{
				bInToken = false;
			}
		}
		return Count;
	}

	FString FlowList(const TArray<FString>& Items)
	{
		FString Out = TEXT("[");
		for (int32 i = 0; i < Items.Num(); ++i)
		{
			if (i > 0)
			{
				Out += TEXT(", ");
			}
			Out += Items[i];
		}
		Out += TEXT("]");
		return Out;
	}

	FString FlowPairs(const TArray<TPair<FString, FString>>& Pairs)
	{
		FString Out = TEXT("{");
		for (int32 i = 0; i < Pairs.Num(); ++i)
		{
			if (i > 0)
			{
				Out += TEXT(", ");
			}
			Out += Pairs[i].Key;
			Out += TEXT(": ");
			Out += Pairs[i].Value;
		}
		Out += TEXT("}");
		return Out;
	}

	FString Fixed(double Value)
	{
		return FString::Printf(TEXT("%.4f"), Value);
	}
}

bool FUALTypedFeatureExtractor::CanExtract(const FAssetData& AssetData) const
{
	// Whole-catalog enhancer: accept every UObject-derived asset. Single-type
	// extractors must narrow this, but this class explicitly owns all 24 families.
	return AssetData.IsValid();
}

bool FUALTypedFeatureExtractor::Extract(const FAssetData& AssetData, FUALAssetRecord& OutRecord)
{
	// Step 1: never re-implement the 16-key baseline. Universal owns identity,
	// taxonomy, tokens, content_hash and the deterministic 16-dim fingerprint.
	FUniversalReflectionExtractor Baseline;
	if (!Baseline.Extract(AssetData, OutRecord))
	{
		return false; // let FUALExtractorRegistry's fallback handle it
	}

	const FString ClassPath = AssetData.AssetClassPath.ToString();
	FString AssetClass = ClassPath;
	int32 DotIdx = INDEX_NONE;
	if (AssetClass.FindLastChar(TEXT('.'), DotIdx))
	{
		AssetClass = AssetClass.Mid(DotIdx + 1);
	}
	const FString Family = FamilyFor(AssetClass);
	const FString AssetPath = AssetData.PackageName.ToString();
	const FString AssetName = AssetData.AssetName.ToString();

	// Step 2: normalize every Tag into a stable, diffable feature vocabulary.
	TArray<FString> TagKeys;
	TArray<TPair<FString, FString>> NumericStats;
	TArray<TPair<FString, FString>> ProfilePairs;
	TArray<FString> Capabilities;
	int32 NumericCount = 0;
	double NumericSum = 0.0;
	double NumericMin = TNumericLimits<double>::Max();
	double NumericMax = -TNumericLimits<double>::Max();

	TArray<FString> ProfileRules;
	ProfileKeysFor(Family).ParseIntoArray(ProfileRules, TEXT("|"), true);

	for (const auto& Tag : AssetData.TagsAndValues)
	{
		const FString Key = Tag.Key.ToString();
		const FString Raw = Tag.Value.AsString();
		if (Key.IsEmpty() || Raw.IsEmpty())
		{
			continue;
		}
		// Keep the feature vocabulary aligned with the Universal extractor:
		// binary editor blobs carry no semantics and can contain C1 controls.
		if (Key.Equals(TEXT("FiBData"), ESearchCase::IgnoreCase) ||
			Key.Equals(TEXT("Thumbnail"), ESearchCase::IgnoreCase))
		{
			continue;
		}
		TagKeys.Add(Key);

		if (IsNumericTag(Raw))
		{
			const double Value = FCString::Atod(*Raw);
			if (FMath::IsFinite(Value))
			{
				++NumericCount;
				NumericSum += Value;
				NumericMin = FMath::Min(NumericMin, Value);
				NumericMax = FMath::Max(NumericMax, Value);
			}
		}

		for (const FString& Rule : ProfileRules)
		{
			if (Key.Contains(Rule, ESearchCase::IgnoreCase))
			{
				ProfilePairs.Add(TPair<FString, FString>(Key, Raw.Left(96)));
				break;
			}
		}
	}

	// Capability flags are derived from Tag *names*, never from loaded objects.
	// The generated table fixes the emitted order deterministically.
	for (int32 i = 0; i < UALFeatureProfiles::GCapabilityRuleTableCount; ++i)
	{
		const UALFeatureProfiles::FCapabilityRule& Rule =
			UALFeatureProfiles::GCapabilityRuleTable[i];
		for (const FString& Key : TagKeys)
		{
			if (Key.Contains(Rule.TagFragment, ESearchCase::IgnoreCase))
			{
				Capabilities.Add(FString(Rule.Name));
				break;
			}
		}
	}

	TagKeys.Sort();
	TArray<FString> TopTagKeys;
	for (int32 i = 0; i < TagKeys.Num() && i < 12; ++i)
	{
		TopTagKeys.Add(TagKeys[i]);
	}
	TArray<TPair<FString, FString>> TopProfile;
	for (int32 i = 0; i < ProfilePairs.Num() && i < 12; ++i)
	{
		TopProfile.Add(ProfilePairs[i]);
	}

	const double Mean = (NumericCount > 0) ? (NumericSum / NumericCount) : 0.0;
	const double Min = (NumericCount > 0) ? NumericMin : 0.0;
	const double Max = (NumericCount > 0) ? NumericMax : 0.0;
	NumericStats.Add(TPair<FString, FString>(TEXT("numeric_count"), FString::FromInt(NumericCount)));
	NumericStats.Add(TPair<FString, FString>(TEXT("mean"), Fixed(Mean)));
	NumericStats.Add(TPair<FString, FString>(TEXT("min"), Fixed(Min)));
	NumericStats.Add(TPair<FString, FString>(TEXT("max"), Fixed(Max)));
	NumericStats.Add(TPair<FString, FString>(TEXT("sum"), Fixed(NumericSum)));

	int32 PathDepth = 0;
	for (int32 i = 0; i < AssetPath.Len(); ++i)
	{
		if (AssetPath[i] == TEXT('/'))
		{
			++PathDepth;
		}
	}

	// Step 3: feature.* keys live inside the existing MAP(VARCHAR,VARCHAR), so
	// the 16-key staging contract, init.sql, UALTypes.h and db.py stay untouched.
	OutRecord.DomainSpec.Add(TEXT("feature.family"), Family);
	OutRecord.DomainSpec.Add(TEXT("feature.class_path"), ClassPath);
	OutRecord.DomainSpec.Add(TEXT("feature.path_depth"), FString::FromInt(PathDepth));
	OutRecord.DomainSpec.Add(TEXT("feature.name_tokens"), FString::FromInt(CountNameTokens(AssetName)));
	OutRecord.DomainSpec.Add(TEXT("feature.tag_count"), FString::FromInt(TagKeys.Num()));
	OutRecord.DomainSpec.Add(TEXT("feature.tag_keys"), FlowList(TopTagKeys));
	OutRecord.DomainSpec.Add(TEXT("feature.capabilities"), FlowList(Capabilities));
	OutRecord.DomainSpec.Add(TEXT("feature.stats"), FlowPairs(NumericStats));
	OutRecord.DomainSpec.Add(TEXT("feature.profile"), FlowPairs(TopProfile));
	OutRecord.DomainSpec.Add(TEXT("feature.profile_rule"), ProfileKeysFor(Family));

	// Family vocabulary is searchable through the existing FTS tokens column.
	OutRecord.Tokens.AddUnique(Family.ToLower());
	OutRecord.Tokens.AddUnique(TEXT("feature"));

	UE_LOG(LogUALTypedFeatures, Verbose,
		TEXT("Typed features for %s [%s] family=%s tags=%d numeric=%d profile=%d"),
		*AssetPath, *AssetClass, *Family, TagKeys.Num(), NumericCount, TopProfile.Num());
	return true;
}
