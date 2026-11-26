#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleManager.h"
#include "Templates/SharedPointer.h"
#include "AssetRegistry/AssetData.h"
#include "HAL/CriticalSection.h"
#include "UALExtensionRegistry.h"
#include "UALExtractorRegistry.h"

// ---------------------------------------------------------------------------
// FUEAssetLensEditorModule：Editor 模块入口。
//
//   * 提取器注册在**模块级共享的** FUALExtensionRegistry 单例所承载的
//     FUALExtractorRegistry 上；Editor 与 -run=UALIndexBuilder Commandlet 共用，
//     不再各建一个局部栈。Python 侧 Server/ual_common/registry.py 的
//     ExtensionRegistry 是声明式镜像（YAML 扩展清单），C++ 侧负责进程内注册。
//   * GetEditorRegistry() 保持原有签名，供外部插件继续注入领域提取器。
//   * AssetRegistry 的三个委托只读 FAssetData 元数据（零加载）：路径合并后
//     把完整 16 键记录写入 .ual/staging/events.yaml，删除路径写入
//     .ual/staging/removed.yaml，均走 UEYaml emitter；文件 I/O 在线程池完成。
//     RequestIncrementalRescan() 可显式重放同一份确定性快照；灌库交给
//     Python 侧 `ual-cli apply-events`，C++ 不直接改索引。
// ---------------------------------------------------------------------------

/** 逐路径合并后的待写增量事件（进程内状态，不直接序列化）。 */
struct FUALEditorDeltaEntry
{
	FString AssetPath;     // /Game/... 形式的包路径
	FAssetData AssetData;  // 只读元数据拷贝，供 16 键记录投影
	FString Kind;          // added | updated | removed
	int32 Sequence = 0;    // 首次观察时分配的稳定序号（不写进产物）
};

class FUEAssetLensEditorModule : public IModuleInterface
{
public:
	virtual void StartupModule() override;
	virtual void ShutdownModule() override;

	/** 共享扩展注册表（Runtime 模块单例）；Editor 与 Commandlet 都取到同一实例。 */
	FUALExtensionRegistry& GetExtensionRegistry() const { return FUALExtensionRegistry::Get(); }

	/** 供其它模块注入领域提取器（Priority 100）；签名保持兼容。 */
	const TSharedPtr<class FUALExtractorRegistry>& GetEditorRegistry() const { return EditorRegistry; }

	/** 从共享扩展注册表取（必要时创建）共享提取器注册表并注册两个内置提取器。 */
	static TSharedPtr<FUALExtractorRegistry> GetSharedExtractorRegistry();

	/** 显式重放当前合并后的增量快照（确定性、线程池写盘，不阻塞 game thread）。 */
	void RequestIncrementalRescan();

private:
	void RegisterAssetRegistryCallbacks();
	void UnregisterAssetRegistryCallbacks();

	/** 委托回调：只做内存合并 + 投递线程池任务，不加载资源、不做文件 I/O。 */
	void QueueDelta(const FAssetData& AssetData, const TCHAR* Kind);

	/** 构造排序后的快照并投递线程池写盘；无待写事件时为空操作。 */
	void FlushDeltas();

	int32 AddedCount = 0;
	int32 RemovedCount = 0;
	int32 UpdatedCount = 0;
	int32 ScheduledDeltaWriteCount = 0;
	int32 NextDeltaSequence = 1;

	FCriticalSection PendingDeltasLock;
	TMap<FString, FUALEditorDeltaEntry> PendingDeltas;

	FDelegateHandle OnAssetAddedHandle;
	FDelegateHandle OnAssetRemovedHandle;
	FDelegateHandle OnAssetUpdatedHandle;

	TSharedPtr<class FUALExtractorRegistry> EditorRegistry;
};
