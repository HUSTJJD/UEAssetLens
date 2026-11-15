#include "UEAssetLensEditorModule.h"

#include "UALExtractorRegistry.h"
#include "FUniversalReflectionExtractor.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"

DEFINE_LOG_CATEGORY_STATIC(LogUALEditor, Log, All);

void FUEAssetLensEditorModule::StartupModule()
{
	UE_LOG(LogUALEditor, Log, TEXT("UEAssetLensEditor module startup."));

	// 注册通用兜底提取器；领域提取器（Priority 100）可由其它模块继续 Register。
	EditorRegistry = MakeShared<FUALExtractorRegistry>();
	EditorRegistry->Register(MakeShared<FUniversalReflectionExtractor>());
	UE_LOG(LogUALEditor, Log,
		TEXT("Registered %d extractor(s); universal fallback (Priority 0) ready."),
		EditorRegistry->Num());

	RegisterAssetRegistryCallbacks();
}

void FUEAssetLensEditorModule::ShutdownModule()
{
	UnregisterAssetRegistryCallbacks();
	EditorRegistry.Reset();
	UE_LOG(LogUALEditor, Log,
		TEXT("UEAssetLensEditor module shutdown. Delta marks: +%d / -%d / ~%d"),
		AddedCount, RemovedCount, UpdatedCount);
}

void FUEAssetLensEditorModule::MarkAdded()
{
	++AddedCount;
	UE_LOG(LogUALEditor, Log, TEXT("[UAL delta] ADDED (total added=%d)"), AddedCount);
}

void FUEAssetLensEditorModule::MarkRemoved()
{
	++RemovedCount;
	UE_LOG(LogUALEditor, Log, TEXT("[UAL delta] REMOVED (total removed=%d)"), RemovedCount);
}

void FUEAssetLensEditorModule::MarkUpdated()
{
	++UpdatedCount;
	UE_LOG(LogUALEditor, Log, TEXT("[UAL delta] UPDATED (total updated=%d)"), UpdatedCount);
}

void FUEAssetLensEditorModule::RegisterAssetRegistryCallbacks()
{
	FAssetRegistryModule& ARM =
		FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
	IAssetRegistry& AR = ARM.Get();

	// 使用泛型 lambda（auto&&...）以兼容不同 UE 版本里委托参数签名的差异；
	// 回调只做增量标记，不读取 AssetData、不加载资源、不触发全量扫描。
	OnAssetAddedHandle = AR.OnAssetAdded().AddLambda([this](auto&&...) { MarkAdded(); });
	OnAssetRemovedHandle = AR.OnAssetRemoved().AddLambda([this](auto&&...) { MarkRemoved(); });
	OnAssetUpdatedHandle = AR.OnAssetUpdated().AddLambda([this](auto&&...) { MarkUpdated(); });

	UE_LOG(LogUALEditor, Log,
		TEXT("AssetRegistry delta delegates attached (incremental marking only, no full scan)."));
}

void FUEAssetLensEditorModule::UnregisterAssetRegistryCallbacks()
{
	if (!FModuleManager::Get().IsModuleLoaded(TEXT("AssetRegistry")))
	{
		return;
	}
	FAssetRegistryModule& ARM =
		FModuleManager::GetModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
	IAssetRegistry& AR = ARM.Get();

	AR.OnAssetAdded().Remove(OnAssetAddedHandle);
	AR.OnAssetRemoved().Remove(OnAssetRemovedHandle);
	AR.OnAssetUpdated().Remove(OnAssetUpdatedHandle);
}

IMPLEMENT_MODULE(FUEAssetLensEditorModule, UEAssetLensEditor)
