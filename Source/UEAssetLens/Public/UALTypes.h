#pragma once

#include "CoreMinimal.h"
#include "UALTypes.generated.h"

// ---------------------------------------------------------------------------
// UEAssetLens v3.0 公共数据类型（C++ 镜像）
//
// 与 PROTOCOL.md / init.sql / db.py 四处同步；本轮只修正滞后的 C++ 镜像，
// 不新增字段、不递增 schema_meta.schema_version（逻辑契约仍是 v3.0）。
//
// * 冻结 16 键 staging 文档 = 15 个 ASSET_COLUMNS 键 + edges；禁止第 17 个键。
//   updated_at 是 DuckDB 持久化列（DEFAULT CURRENT_TIMESTAMP），不是 staging 键，
//   C++ 侧不把它写进 YAML / TOON 产物。
// * 类型化扩展只进入独立 extension_payload 表 / 独立文档流；FUALExtensionField
//   与 db.EXTENSION_PAYLOAD_COLUMNS 是同一契约，绝不塞回 FUALAssetRecord。
// * Tokens 是 C++ 内部词项数组，写入端用 FString::Join 拍平为空格分隔的
//   VARCHAR 文本；taxonomy 才是 VARCHAR[]。
// * BoundsSize 固定 3 个元素，顺序 [X, Y, Z]，缺省补 0。
// * FingerprintVec 为 FLOAT[16] 的可空字段：空数组表示缺失，写入端必须输出
//   NULL / 缺省，绝不补 16 个 0（全零会让余弦查重退化）。用 TArray<float>
//   是为了让数组类字段在 YAML / DuckDB 之间拥有一致的序列化形态。
// * InterfaceSpec / DomainSpec 为字符串到字符串的映射（DuckDB MAP(VARCHAR,VARCHAR)）。
//
// ---- four-place mirror (parsed by Server/tests/test_kernel_contracts.py) ----
// mirror version 3.0
// staging keys: asset_path,logical_id,asset_class,parent_class,taxonomy,tokens,content_hash,duration,bounds_size,skeleton_path,dominant_limb,fingerprint_vec,interface_spec,domain_spec,yaml_cache,edges
// core fields:
//   asset_path VARCHAR
//   logical_id VARCHAR
//   asset_class VARCHAR
//   parent_class VARCHAR
//   taxonomy VARCHAR[]
//   tokens VARCHAR
//   content_hash VARCHAR
//   duration FLOAT
//   bounds_size FLOAT[3]
//   skeleton_path VARCHAR
//   dominant_limb VARCHAR
//   fingerprint_vec FLOAT[16]
//   interface_spec MAP(VARCHAR, VARCHAR)
//   domain_spec MAP(VARCHAR, VARCHAR)
//   yaml_cache VARCHAR
// extension fields:
//   asset_path VARCHAR
//   namespace VARCHAR
//   field_name VARCHAR
//   value_type VARCHAR
//   value_text VARCHAR
//   value_number DOUBLE
//   value_bool BOOLEAN
//   provenance VARCHAR
// ---- end four-place mirror ----
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
	TArray<FString> Tokens;     // tokens    VARCHAR（sink 拍平为空格分隔文本）

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

	// fingerprint_vec FLOAT[16]：可空确定性指纹。空数组 = 缺失，写入端输出 NULL；
	// 恰好 16 个有限 float 才写入数组，绝不补 16 个 0。
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	TArray<float> FingerprintVec;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	TMap<FString, FString> InterfaceSpec;  // interface_spec MAP(VARCHAR,VARCHAR)

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	TMap<FString, FString> DomainSpec;     // domain_spec MAP(VARCHAR,VARCHAR)

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	FString YamlCache;          // yaml_cache   YAML 文本缓存（由 Python 侧灌库阶段生成）

	// 非 16 键 staging 字段：仅为既有写入端保留的进程内持久化时间标记。
	// YAML / TOON / 工件一律不写它；DuckDB 的 updated_at 由 DEFAULT 填充。
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "UEAssetLens")
	FString UpdatedAt;          // updated_at (persistence-only, never staged)

	// 只保证 bounds_size 的 3 元约束；fingerprint_vec 保持空数组 = 缺失/NULL。
	FUALAssetRecord()
		: Duration(0.0f)
	{
		BoundsSize.SetNumZeroed(3);
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

// ---------------------------------------------------------------------------
// 类型化扩展载荷（Phase 1 骨架）
//
// 这些是**独立于 16 键 staging 文档**的值类型：Python 侧对应单独的
// `extension_payload` 表 / 独立文档流，C++ 侧绝不把它们塞进 FUALAssetRecord，
// 也不给 staging 文档增加第 17 个顶层键。FUALAssetRecord 与其 16 键契约保持不变。
//
// 列概念（与 Python 侧逐字对齐）：
//   asset_path / namespace / field_name / value_type / value_text /
//   value_number (nullable) / value_bool (nullable) / provenance
//   value_type ∈ string | integer | number | boolean | yaml
//
// 确定性约束：AssetPath 只保存 /Game/... 形式的包路径；Provenance 只保存
// 稳定的来源标记；产物不含时间信息、会话态标识或本机绝对路径。
// ---------------------------------------------------------------------------

/** 一行扩展字段记录；对应独立 extension_payload 表的一行。纯 C++ 值类型，不参与反射。 */
struct UEASSETLENS_API FUALExtensionField
{
	FString AssetPath;
	FString Namespace;
	FString FieldName;
	FString ValueType;
	FString ValueText;
	TOptional<double> ValueNumber;
	TOptional<bool> ValueBool;
	// 确定性来源标记：C++ 投影写 domain_spec / interface_spec / default；
	// Python 声明式扩展清单写 builtin / plugin / project / user。
	FString Provenance;

	/** 稳定排序键；不写进产物，仅供确定性排序 / 去重使用。 */
	FString Key() const
	{
		return AssetPath + TEXT("|") + Namespace + TEXT(".") + FieldName
			+ TEXT("|") + Provenance;
	}
};

/** 单个资产的扩展字段集合；只用于进程内传递，不是 16 键 staging 文档。 */
struct UEASSETLENS_API FUALExtensionRecord
{
	FString AssetPath;
	TArray<FUALExtensionField> Fields;
};
