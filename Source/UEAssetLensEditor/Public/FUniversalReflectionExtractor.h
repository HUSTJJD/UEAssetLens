#pragma once

#include "CoreMinimal.h"
#include "IAssetExtractor.h"

// ---------------------------------------------------------------------------
// FUniversalReflectionExtractor：通用反射兜底提取器（Priority 0）。
//
// 只读 FAssetData 的 TagsAndValues / PackageName / AssetName / AssetClass，
// 绝不把贴图、网格、蓝图 CDO 加载进显存（无头离线索引硬约束）。
// 对所有 UObject 资产都能产出一条确定性记录：
//   logical_id / taxonomy / tokens / content_hash / 16 维指纹 / 若干元数据。
//
// 可选：定义 UAL_ENABLE_CDO_REFLECTION=1 才会编译 DataAsset / Blueprint CDO 的
// 顶层属性遍历；默认关闭，因为 GetAsset() 会加载资源，与"无头不加载"冲突。
// ---------------------------------------------------------------------------
class FUniversalReflectionExtractor : public IAssetExtractor
{
public:
	virtual int32 Priority() const override { return 0; }
	virtual bool CanExtract(const FAssetData& AssetData) const override;
	virtual bool Extract(const FAssetData& AssetData, FUALAssetRecord& OutRecord) override;

private:
	/** 剥离 SM_/SK_/AM_/T_/MI_/M_/BP_/WBP_ 等 UE 命名前缀。 */
	static FString StripPrefix(const FString& AssetName);

	/** 驼峰 + 下划线/分隔符分词，小写化并去重。 */
	static void TokenizeName(const FString& InName, TArray<FString>& OutTokens);

	/** 按 AssetClass 生成 ["Asset", Family, Class] 层级。 */
	static void BuildTaxonomy(const FString& AssetClass, TArray<FString>& OutTaxonomy);

	/** 对 asset_path + 排序后的 Tag-Value 做 SHA1，得到确定性 content_hash。 */
	static FString ComputeContentHash(const FAssetData& AssetData);

	/** 仅用路径/名称/类名做确定性 16 维指纹编码（不加载资源）。 */
	static TArray<float> ComputeNameFingerprint(
		const FString& AssetPath, const FString& AssetName, const FString& AssetClass);

	/** 从 Tag-Value 填充 duration / bounds_size / skeleton_path / parent_class / spec。 */
	static void FillFromTags(const FAssetData& AssetData, FUALAssetRecord& OutRecord);
};
