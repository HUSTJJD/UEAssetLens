#pragma once

#include "CoreMinimal.h"
#include "IAssetExtractor.h"
#include "UALTypes.h"

// ---------------------------------------------------------------------------
// FUALExtractorRegistry：提取器注册 / 注销 / 按优先级选取。
//
//   * 领域提取器（Priority 100）通过 Register 注入，内部按优先级降序维护。
//   * 内置兜底：始终持有一个 FUniversalReflectionExtractor（Priority 0），
//     当没有任何提取器命中时保证仍能产出一条通用反射记录。
//   * ExtractRecord 是 Commandlet / 增量回调的统一入口。
// ---------------------------------------------------------------------------
class FUALExtractorRegistry
{
public:
	FUALExtractorRegistry();

	/** 注册提取器（按 Priority 降序重排）。返回是否成功。 */
	bool Register(TSharedPtr<IAssetExtractor> Extractor);

	/** 按对象地址注销。返回是否找到并移除。 */
	bool Unregister(const IAssetExtractor* ExtractorPtr);

	/** 清空所有已注册提取器（内置兜底不受影响）。 */
	void Clear();

	/** 当前已注册（不含内置兜底）的提取器数量。 */
	int32 Num() const { return Extractors.Num(); }

	/**
	 * 对单个资产选最高优先级且 CanExtract==true 的提取器执行 Extract。
	 * 若全部未命中，回退到内置通用兜底提取器。返回是否成功产出记录。
	 */
	bool ExtractRecord(const FAssetData& AssetData, FUALAssetRecord& OutRecord);

private:
	void SortByPriority();

	// 已注册提取器（按 Priority 降序）。
	TArray<TSharedPtr<IAssetExtractor>> Extractors;

	// 内置兜底，构造时创建，永不为空。
	TSharedPtr<IAssetExtractor> FallbackExtractor;
};
