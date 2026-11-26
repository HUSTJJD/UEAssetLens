#pragma once

#include "IAssetExtractor.h"

// ---------------------------------------------------------------------------
// FUALTypedFeatureExtractor：全资产类型特征规范化提取器（Priority 100）。
//
// 与「单一 AssetClass 专属提取器」不同，本类的职责是让 *每一种* UObject 派生
// 资产都获得可检索、可 diff 的规范化特征，而不只是原始 AssetRegistry Tag。
// 因此 CanExtract() 对所有资产返回 true：它是 Universal 提取结果之上的增强层，
// 内部第一步就调用 FUniversalReflectionExtractor 产出基础 16 键，随后追加
// feature.* 键。若该步骤失败会返回 false，由 FUALExtractorRegistry 的
// FallbackExtractor 兜底，保证任何资产都不会被漏掉。
//
// 硬约束（与 IAssetExtractor.h / CLAUDE.md 一致）：
//   * 只读 FAssetData.TagsAndValues，绝不 GetAsset() / 加载贴图或网格；
//   * 所有特征来自反射元数据、路径与确定性数学，Zero-VLM，无 embedding / GPU；
//   * 不新增 FUALAssetRecord 字段、不改 staging 16 键交换契约；
//   * 无时间戳、无随机 ID、无本地绝对路径，输出可逐字节复现。
// ---------------------------------------------------------------------------
class FUALTypedFeatureExtractor : public IAssetExtractor
{
public:
	virtual int32 Priority() const override { return 100; }
	virtual bool CanExtract(const FAssetData& AssetData) const override;
	virtual bool Extract(const FAssetData& AssetData, FUALAssetRecord& OutRecord) override;
};
