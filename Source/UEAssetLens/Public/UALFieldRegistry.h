#pragma once

#include "CoreMinimal.h"
#include "UALTypes.h"
#include "UALExtensionRegistry.h"

// ---------------------------------------------------------------------------
// FUALFieldRegistry：类型化字段规格的进程内注册表（Phase 1 骨架）。
//
// 规格来自注册数据（插件 / 共享扩展注册表），本文件不含任何匿名 namespace
// 里的 static const 业务规则表，也不做资源加载、不引入 VLM / embedding。
// Project() 只读取 FUALAssetRecord 现有的 InterfaceSpec / DomainSpec 两张
// MAP，把它们投影成独立的 FUALExtensionField 行；16 键 staging 文档不变。
// ---------------------------------------------------------------------------

/** 字段规格；ValueType ∈ string | integer | number | boolean | yaml。 */
struct UEASSETLENS_API FUALFieldSpec
{
	FString Namespace;                 // 九个固定 namespace 之一
	FString FieldName;                 // 与 InterfaceSpec / DomainSpec 的键对应，或 "*"
	FString ValueType;
	TArray<FString> AppliesTo;         // 精确资产类名，或 "*" 通配
	FString DefaultValue;
	FString Version;
	int32 Priority = 0;

	FUALFieldSpec()
	{
		Namespace = TEXT("field");
		ValueType = TEXT("string");
		Version = TEXT("1");
		AppliesTo.Add(TEXT("*"));
	}

	FString Key() const { return Namespace + TEXT(".") + FieldName; }
};

class UEASSETLENS_API FUALFieldRegistry
{
public:
	static FUALFieldRegistry& Get();

	/** 返回 false：键重复、namespace 非法、字段名空、值类型非法或 AppliesTo 为空。 */
	bool Register(const FUALFieldSpec& Spec);
	bool Unregister(const FString& Key);

	/** 精确匹配 > 通配，优先级降序，键升序；与插入顺序无关。 */
	TArray<FUALFieldSpec> Resolve(const FString& AssetClass) const;

	/** 按键升序的稳定快照，供文档 / CI 与共享扩展注册表使用。 */
	TArray<FUALFieldSpec> Snapshot() const;

	/** 从共享扩展注册表导入 namespace=field 的声明式字段规格（重复键跳过）。 */
	int32 ImportFrom(const FUALExtensionRegistry& Extensions);

	/**
	 * 把 16 键记录中的两个 MAP 投影成独立的类型化扩展字段。
	 * 无规格的键按确定性规则推断类型；带 DefaultValue 且缺失的规格补默认值。
	 * 输出按 (AssetPath, Namespace, FieldName, Provenance) 排序。
	 * 不读 FAssetData、不加载资源、不写时间戳 / 绝对路径 / 随机 ID。
	 */
	void Project(const FUALAssetRecord& Record, TArray<FUALExtensionField>& OutFields) const;

	int32 Num() const { return Specs.Num(); }
	void Clear() { Specs.Reset(); }

	static bool IsValidValueType(const FString& ValueType);

private:
	TMap<FString, FUALFieldSpec> Specs;
};
