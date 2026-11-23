#pragma once

#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"
#include "UALIndexBuilderCommandlet.generated.h"

// ---------------------------------------------------------------------------
// UUALIndexBuilderCommandlet：无头全量 / 增量索引构建器。
//
//   -run=UALIndexBuilder [-Project=<dir>] [-Out=<name>] [-Incremental]
//                        [-Roots=/Game,/PluginA] [-IncludePlugins]
//
// 流程：确保 AssetRegistry 就绪 → 按扫描根遍历（默认 /Game；-Roots 可显式指定，
//       -IncludePlugins 追加所有含内容插件的挂载根，按 PackageName 去重）→
//       FUALExtractorRegistry::ExtractRecord 产出记录 → IUALIndexSink 写出 →
//       输出统计日志。-Incremental 用 content_hash 比对已有清单跳过未变更资产。
//
// 硬约束：全程只读 FAssetData / 依赖关系元数据，绝不把贴图 / 网格加载进显存。
// ---------------------------------------------------------------------------
UCLASS()
class UUALIndexBuilderCommandlet : public UCommandlet
{
	GENERATED_BODY()

public:
	UUALIndexBuilderCommandlet(const FObjectInitializer& ObjectInitializer);

	//~ Begin UCommandlet interface
	virtual int32 Main(const FString& Params) override;
	//~ End UCommandlet interface

private:
	/** 解析 -Project= / -Out= / -Incremental / -Roots= / -IncludePlugins 参数。 */
	void ParseParams(const FString& Params, FString& OutProject, FString& OutOutput,
		bool& bOutIncremental, TArray<FString>& OutRoots, bool& bOutIncludePlugins) const;

	/** 读取 .ual/staging 下已有 YAML 文档流，建立 asset_path -> content_hash 映射（增量用）。 */
	TMap<FString, FString> LoadExistingHashes(const FString& StagingDir) const;
};
