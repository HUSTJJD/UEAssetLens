#include "UALExtractorRegistry.h"
#include "FUniversalReflectionExtractor.h"

DEFINE_LOG_CATEGORY_STATIC(LogUALExtractorRegistry, Log, All);

FUALExtractorRegistry::FUALExtractorRegistry()
{
	// 内置兜底：任何资产都至少能产出一条通用反射记录（Priority 0）。
	FallbackExtractor = MakeShared<FUniversalReflectionExtractor>();
}

bool FUALExtractorRegistry::Register(TSharedPtr<IAssetExtractor> Extractor)
{
	if (!Extractor.IsValid())
	{
		return false;
	}
	Extractors.Add(Extractor);
	SortByPriority();
	UE_LOG(LogUALExtractorRegistry, Verbose, TEXT("Registered extractor (Priority=%d), total=%d"),
		Extractor->Priority(), Extractors.Num());
	return true;
}

bool FUALExtractorRegistry::Unregister(const IAssetExtractor* ExtractorPtr)
{
	if (ExtractorPtr == nullptr)
	{
		return false;
	}
	const int32 Before = Extractors.Num();
	Extractors.RemoveAll([ExtractorPtr](const TSharedPtr<IAssetExtractor>& E)
	{
		return E.IsValid() && E.Get() == ExtractorPtr;
	});
	return Extractors.Num() != Before;
}

void FUALExtractorRegistry::Clear()
{
	Extractors.Reset();
}

void FUALExtractorRegistry::SortByPriority()
{
	// 降序：优先级高的排在前面，先被选中。
	Extractors.Sort([](const TSharedPtr<IAssetExtractor>& A, const TSharedPtr<IAssetExtractor>& B)
	{
		return A->Priority() > B->Priority();
	});
}

bool FUALExtractorRegistry::ExtractRecord(const FAssetData& AssetData, FUALAssetRecord& OutRecord)
{
	for (const TSharedPtr<IAssetExtractor>& E : Extractors)
	{
		if (E.IsValid() && E->CanExtract(AssetData))
		{
			if (E->Extract(AssetData, OutRecord))
			{
				return true;
			}
		}
	}

	// 兜底：没有任何领域提取器命中（或注册表为空）时使用通用反射提取器。
	if (FallbackExtractor.IsValid() && FallbackExtractor->Extract(AssetData, OutRecord))
	{
		UE_LOG(LogUALExtractorRegistry, Verbose, TEXT("Fallback extractor used for %s"),
			*AssetData.AssetName.ToString());
		return true;
	}
	return false;
}
