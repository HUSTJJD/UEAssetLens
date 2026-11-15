#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleManager.h"
#include "Templates/SharedPointer.h"
#include "AssetRegistry/AssetData.h"

// ---------------------------------------------------------------------------
// FUEAssetLensEditorModule：Editor 模块入口。
//
// 启动时：
//   * 创建提取器注册表并注册 FUniversalReflectionExtractor（Priority 0 兜底）；
//   * 挂接 AssetRegistry 的 OnAssetAdded / OnAssetRemoved / OnAssetUpdated，
//     回调里仅做增量标记（计数 + 日志），不启动全量扫描。
// 全量 / 增量构建由 -run=UALIndexBuilder Commandlet 负责。
// ---------------------------------------------------------------------------
class FUEAssetLensEditorModule : public IModuleInterface
{
public:
	virtual void StartupModule() override;
	virtual void ShutdownModule() override;

	/** 供其它模块注入领域提取器（Priority 100）。 */
	const TSharedPtr<class FUALExtractorRegistry>& GetEditorRegistry() const { return EditorRegistry; }

private:
	void RegisterAssetRegistryCallbacks();
	void UnregisterAssetRegistryCallbacks();

	// 增量标记：只计数 / 写日志，不做任何资源加载或全量扫描。
	void MarkAdded();
	void MarkRemoved();
	void MarkUpdated();

	int32 AddedCount = 0;
	int32 RemovedCount = 0;
	int32 UpdatedCount = 0;

	FDelegateHandle OnAssetAddedHandle;
	FDelegateHandle OnAssetRemovedHandle;
	FDelegateHandle OnAssetUpdatedHandle;

	TSharedPtr<class FUALExtractorRegistry> EditorRegistry;
};
