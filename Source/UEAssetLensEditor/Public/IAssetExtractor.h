#pragma once

#include "CoreMinimal.h"
#include "AssetRegistry/AssetData.h"
#include "UALTypes.h"

// ---------------------------------------------------------------------------
// IAssetExtractor：领域提取器可插拔接口。
//
// 约定：
//   * Priority() 数值越大优先级越高；通用兜底 FUniversalReflectionExtractor 为 0。
//   * CanExtract / Extract 只允许读取 FAssetData（Tag-Value 元数据），
//     不得把贴图 / 网格 / 蓝图加载进显存（无头离线索引的硬约束）。
//   * 领域提取器优先级 100，通过 FUALExtractorRegistry::Register 注入。
// ---------------------------------------------------------------------------
class IAssetExtractor
{
public:
	virtual ~IAssetExtractor() = default;

	/** 优先级：数值越大越优先被选中。 */
	virtual int32 Priority() const = 0;

	/** 该提取器是否能处理此资产（仅基于 FAssetData，禁止加载 UObject）。 */
	virtual bool CanExtract(const FAssetData& AssetData) const = 0;

	/** 填充 OutRecord；返回 true 表示成功产出一条记录。 */
	virtual bool Extract(const FAssetData& AssetData, FUALAssetRecord& OutRecord) = 0;
};
