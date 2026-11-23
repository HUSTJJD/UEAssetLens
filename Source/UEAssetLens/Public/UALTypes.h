#pragma once

#include "CoreMinimal.h"
#include "UALTypes.generated.h"

// ---------------------------------------------------------------------------
// UEAssetLens v2.0 公共数据类型
//
// 字段严格对应 DuckDB 中的两张表：
//   assets(asset_path, logical_id, asset_class, parent_class, taxonomy VARCHAR[],
//          tokens VARCHAR[], content_hash, duration, bounds_size FLOAT[3],
//          skeleton_path, dominant_limb, fingerprint_vec FLOAT[16],
//          interface_spec MAP(VARCHAR,VARCHAR), domain_spec MAP(VARCHAR,VARCHAR),
//          yaml_cache, updated_at)
//   asset_edges(source_path, target_path, edge_type, weight)
//
// 约定（务必遵守）：
//   * BoundsSize     固定 3 个元素，顺序 [X, Y, Z]，缺省补 0。
//   * FingerprintVec 固定 16 个 float，顺序由 FUALFingerprint 定义，缺省补 0。
//     用 TArray<float> 而非 FVector 是为了让所有数组类字段在 YAML / DuckDB
//     之间拥有一致的序列化形态；长度约束由构造函数与写入端共同保证。
//   * InterfaceSpec / DomainSpec 为字符串到字符串的映射（DuckDB MAP(VARCHAR,VARCHAR)）。
//   * 所有时间戳以 ISO8601 字符串保存，避免时区 / 精度差异。
// ---------------------------------------------------------------------------

UENUM(BlueprintType)
enum class EUALEdgeType : uint8
{
	DEPENDS_ON  UMETA(DisplayName = "DEPENDS_ON"),
	CO_OCCURS   UMETA(DisplayName = "CO_OCCURS")
};

// 边类型的字符串化辅助，供 YAML / DuckDB 写入端使用，保证 'DEPENDS_ON'|'CO_OCCURS' 字面一致。
namespace UAL
{
	inline const TCHAR* EdgeTypeToString(EUALEdgeType Type)
	{
		switch (Type)
		{
		case EUALEdgeType::DEPENDS_ON: return TEXT("DEPENDS_ON");
		case EUALEdgeType::CO_OCCURS:  return TEXT("CO_OCCURS");
		default:                       return TEXT("DEPENDS_ON");
		}
	}

	inline FString MakeEdgeTypeString(EUALEdgeType Type)
	{
		return FString(EdgeTypeToString(Type));
	}
}

USTRUCT(BlueprintType)
struct UEASSETLENS_API FUALAssetRecord
{
	GENERATED_BODY()

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	FString AssetPath;          // asset_path          /Game/... 形式的包路径

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	FString LogicalId;          // logical_id          <AssetClass>:<AssetName>

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	FString AssetClass;         // asset_class

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	FString ParentClass;        // parent_class

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	TArray<FString> Taxonomy;   // taxonomy  VARCHAR[]  按 AssetClass 生成的层级

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	TArray<FString> Tokens;     // tokens    VARCHAR[]  名称分词结果

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	FString ContentHash;        // content_hash 确定性变更检测哈希

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	float Duration;             // duration      秒；静态资产为 0

	// bounds_size FLOAT[3]：固定 3 元素 [X, Y, Z]。
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	TArray<float> BoundsSize;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	FString SkeletonPath;       // skeleton_path

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	FString DominantLimb;       // dominant_limb

	// fingerprint_vec FLOAT[16]：固定 16 个 float 的确定性指纹，缺省补 0。
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	TArray<float> FingerprintVec;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	TMap<FString, FString> InterfaceSpec;  // interface_spec MAP(VARCHAR,VARCHAR)

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	TMap<FString, FString> DomainSpec;     // domain_spec MAP(VARCHAR,VARCHAR)

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	FString YamlCache;          // yaml_cache   YAML 文本缓存（由 Python 侧灌库阶段生成）

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	FString UpdatedAt;          // updated_at   ISO8601 UTC 时间戳

	// 保证所有定长数组从一开始就满足约束（3 / 16 元素，全 0）。
	FUALAssetRecord()
		: Duration(0.0f)
	{
		BoundsSize.SetNumZeroed(3);
		FingerprintVec.SetNumZeroed(16);
	}
};

USTRUCT(BlueprintType)
struct UEASSETLENS_API FUALEdgeRecord
{
	GENERATED_BODY()

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	FString SourcePath;   // source_path

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	FString TargetPath;   // target_path

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	EUALEdgeType EdgeType; // edge_type  'DEPENDS_ON' | 'CO_OCCURS'

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	float Weight;         // weight

	FUALEdgeRecord()
		: EdgeType(EUALEdgeType::DEPENDS_ON)
		, Weight(1.0f)
	{
	}
};
