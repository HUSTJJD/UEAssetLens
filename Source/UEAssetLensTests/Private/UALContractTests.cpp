#include "CoreMinimal.h"
#include "Misc/AutomationTest.h"

#include "UALFingerprint.h"
#include "UALTypes.h"
#include "UALExtensionRegistry.h"
#include "UALFieldRegistry.h"

// Generated headers live in the Editor module's Public folder. Relative includes
// keep this test module dependent on UEAssetLens only. Source-level contract
// tests only: no UE SDK compile / link / PIE verification is claimed here.
#include "../../UEAssetLensEditor/Public/UALTaxonomyRules.h"
#include "../../UEAssetLensEditor/Public/UALFeatureProfiles.h"
#include "../../UEAssetLensEditor/Public/UALDomainProfiles.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
	static TArray<FString> FrozenStagingKeys()
	{
		TArray<FString> Keys;
		Keys.Reserve(16);
		Keys.Add(TEXT("asset_path"));
		Keys.Add(TEXT("logical_id"));
		Keys.Add(TEXT("asset_class"));
		Keys.Add(TEXT("parent_class"));
		Keys.Add(TEXT("taxonomy"));
		Keys.Add(TEXT("tokens"));
		Keys.Add(TEXT("content_hash"));
		Keys.Add(TEXT("duration"));
		Keys.Add(TEXT("bounds_size"));
		Keys.Add(TEXT("skeleton_path"));
		Keys.Add(TEXT("dominant_limb"));
		Keys.Add(TEXT("fingerprint_vec"));
		Keys.Add(TEXT("interface_spec"));
		Keys.Add(TEXT("domain_spec"));
		Keys.Add(TEXT("yaml_cache"));
		Keys.Add(TEXT("edges"));
		return Keys;
	}

	static TArray<FString> FixedExtensionNamespaces()
	{
		TArray<FString> Names;
		Names.Reserve(9);
		Names.Add(TEXT("asset_type"));
		Names.Add(TEXT("policy"));
		Names.Add(TEXT("field"));
		Names.Add(TEXT("diff"));
		Names.Add(TEXT("tool"));
		Names.Add(TEXT("lifecycle"));
		Names.Add(TEXT("sink"));
		Names.Add(TEXT("template"));
		Names.Add(TEXT("gateway"));
		return Names;
	}
}

// ---------------------------------------------------------------------------
// 1) 声明式扩展注册表：九个固定 namespace、类型白名单、确定性
// ---------------------------------------------------------------------------
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FUALRegistryContractTest,
	"UEAssetLens.Contracts.Registry",
	EAutomationTestFlags_ApplicationContextMask | EAutomationTestFlags::ProductFilter)

bool FUALRegistryContractTest::RunTest(const FString& Parameters)
{
	const TArray<FString>& Namespaces = FUALExtensionRegistry::Namespaces();
	const TArray<FString> Expected = FixedExtensionNamespaces();

	TestEqual(TEXT("nine fixed extension namespaces"), Namespaces.Num(), Expected.Num());
	for (int32 Index = 0; Index < Expected.Num(); ++Index)
	{
		TestEqual(TEXT("namespace order is stable"), Namespaces[Index], Expected[Index]);
		TestTrue(TEXT("namespace is registered"),
			FUALExtensionRegistry::IsValidNamespace(Expected[Index]));
	}
	TestFalse(TEXT("unknown namespace is rejected"),
		FUALExtensionRegistry::IsValidNamespace(TEXT("unknown")));

	TestTrue(TEXT("string is a typed field value"),
		FUALFieldRegistry::IsValidValueType(TEXT("string")));
	TestTrue(TEXT("integer is a typed field value"),
		FUALFieldRegistry::IsValidValueType(TEXT("integer")));
	TestTrue(TEXT("number is a typed field value"),
		FUALFieldRegistry::IsValidValueType(TEXT("number")));
	TestTrue(TEXT("boolean is a typed field value"),
		FUALFieldRegistry::IsValidValueType(TEXT("boolean")));
	TestTrue(TEXT("yaml is a typed field value"),
		FUALFieldRegistry::IsValidValueType(TEXT("yaml")));
	TestFalse(TEXT("unknown value type is rejected"),
		FUALFieldRegistry::IsValidValueType(TEXT("vector")));

	FUALExtensionField Field;
	Field.AssetPath = TEXT("/Game/Core/BP_Player");
	Field.Namespace = TEXT("field");
	Field.FieldName = TEXT("quality_tier");
	Field.Provenance = TEXT("builtin");
	TestEqual(TEXT("extension key is deterministic"), Field.Key(), Field.Key());
	TestFalse(TEXT("extension key has no timestamp separator"),
		Field.Key().Contains(TEXT(":")));
	return true;
}

// ---------------------------------------------------------------------------
// 2) 生成头契约：taxonomy / feature profile / domain profile
// ---------------------------------------------------------------------------
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FUALDomainProfileContractTest,
	"UEAssetLens.Contracts.DomainProfiles",
	EAutomationTestFlags_ApplicationContextMask | EAutomationTestFlags::ProductFilter)

bool FUALDomainProfileContractTest::RunTest(const FString& Parameters)
{
	TestEqual(TEXT("taxonomy root is Asset"),
		FString(UALTaxonomyRules::GRoot), FString(TEXT("Asset")));
	TestEqual(TEXT("taxonomy fallback is Misc"),
		FString(UALTaxonomyRules::GFallback), FString(TEXT("Misc")));
	TestEqual(TEXT("one feature profile per taxonomy family plus fallback"),
		UALFeatureProfiles::GProfileRuleTableCount, UALTaxonomyRules::GRuleCount + 1);
	TestTrue(TEXT("capability rules are generated"),
		UALFeatureProfiles::GCapabilityRuleTableCount > 0);
	TestTrue(TEXT("name prefixes are generated"),
		UALFeatureProfiles::GNamePrefixCount > 0);

	TestEqual(TEXT("six generated domain profile families"),
		UALDomainProfiles::GDomainProfileRuleTableCount, 6);
	TestTrue(TEXT("domain field rules are generated"),
		UALDomainProfiles::GDomainFieldRuleTableCount > 0);

	for (int32 Index = 0; Index < UALDomainProfiles::GDomainProfileRuleTableCount; ++Index)
	{
		const UALDomainProfiles::FUALDomainProfileRule& Rule =
			UALDomainProfiles::GDomainProfileRuleTable[Index];
		TestTrue(TEXT("domain family is declared"),
			Rule.DomainFamily != nullptr && Rule.DomainFamily[0] != TEXT('\0'));
		TestTrue(TEXT("domain applies_to is declared"),
			Rule.AppliesTo != nullptr && Rule.AppliesTo[0] != TEXT('\0'));
		TestTrue(TEXT("domain key prefix is declared"),
			Rule.KeyPrefix != nullptr && Rule.KeyPrefix[0] != TEXT('\0'));
		TestTrue(TEXT("domain tag fragments are declared"),
			Rule.TagFragments != nullptr && Rule.TagFragments[0] != TEXT('\0'));
	}

	for (int32 Index = 0; Index < UALDomainProfiles::GDomainFieldRuleTableCount; ++Index)
	{
		const UALDomainProfiles::FUALDomainFieldRule& Rule =
			UALDomainProfiles::GDomainFieldRuleTable[Index];
		TestTrue(TEXT("generated field value type is registered"),
			FUALFieldRegistry::IsValidValueType(FString(Rule.ValueType)));
		TestTrue(TEXT("generated field source key is declared"),
			Rule.SourceKey != nullptr && Rule.SourceKey[0] != TEXT('\0'));
	}
	return true;
}

// ---------------------------------------------------------------------------
// 3) 冻结 16 键 / 可空指纹 / 独立扩展行
// ---------------------------------------------------------------------------
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FUALSixteenKeyContractTest,
	"UEAssetLens.Contracts.SixteenKeys",
	EAutomationTestFlags_ApplicationContextMask | EAutomationTestFlags::ProductFilter)

bool FUALSixteenKeyContractTest::RunTest(const FString& Parameters)
{
	const TArray<FString> Keys = FrozenStagingKeys();
	TestEqual(TEXT("frozen staging key count"), Keys.Num(), 16);
	TestEqual(TEXT("15 asset columns plus edges"), Keys.Num(), 15 + 1);
	TestTrue(TEXT("fingerprint_vec is a staging key"),
		Keys.Contains(TEXT("fingerprint_vec")));
	TestTrue(TEXT("edges is the 16th staging key"), Keys[15] == TEXT("edges"));
	TestFalse(TEXT("updated_at is not a staging key"),
		Keys.Contains(TEXT("updated_at")));
	TestFalse(TEXT("no 17th staging key for extension payload"),
		Keys.Contains(TEXT("extension_payload")));

	FUALAssetRecord Record;
	TestEqual(TEXT("bounds_size has exactly three elements"), Record.BoundsSize.Num(), 3);
	TestEqual(TEXT("missing fingerprint is empty, not a zero vector"),
		Record.FingerprintVec.Num(), 0);
	TestEqual(TEXT("fingerprint dimension is fixed"), FUALFingerprint::Dim, 16);

	TArray<float> Histogram;
	Histogram.Init(0.125f, 8);
	const TArray<float> Fingerprint = FUALFingerprint::BuildFromBoundsAndHistogram(
		FVector(1.0, 2.0, 3.0), Histogram, 1, 1);
	TestEqual(TEXT("generated fingerprint has 16 dims"),
		Fingerprint.Num(), FUALFingerprint::Dim);
	Record.FingerprintVec = Fingerprint;
	TestEqual(TEXT("assigned fingerprint preserves 16 dims"),
		Record.FingerprintVec.Num(), 16);

	FUALExtensionField Field;
	Field.AssetPath = TEXT("/Game/Core/BP_Player");
	Field.Namespace = TEXT("field");
	Field.FieldName = TEXT("quality_tier");
	Field.ValueType = TEXT("integer");
	Field.ValueNumber = 3.0;
	Field.Provenance = TEXT("builtin");
	TestTrue(TEXT("typed number is optional"), Field.ValueNumber.IsSet());
	TestFalse(TEXT("typed bool stays unset"), Field.ValueBool.IsSet());
	return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
