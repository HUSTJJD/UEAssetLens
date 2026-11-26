#pragma once

#include "CoreMinimal.h"

// ---------------------------------------------------------------------------
// 模块级共享扩展注册表（Runtime 模块，仅依赖 CoreMinimal）。
//
// 这是 Python 侧 Server/ual_common/registry.py::ExtensionRegistry 的 C++ 镜像：
// 九个固定 namespace、namespace.name 唯一键、按 priority / 精确匹配 / 键序
// 的确定性解析。Python 侧是声明式事实源（YAML 扩展清单），本注册表承载
// 进程内注册；Editor 模块与无头 Commandlet 通过同一个 Get() 实例共享。
//
// 本文件刻意不引用 AssetRegistry / Editor / Json 类型。实现（如 Editor 侧的
// 提取器注册表）通过 IUALExtensionBinding 做类型擦除后挂到条目上。
// ---------------------------------------------------------------------------

/** 类型擦除的实现载体；Runtime 侧不感知具体 Editor 类型。 */
struct UEASSETLENS_API IUALExtensionBinding
{
	virtual ~IUALExtensionBinding() = default;

	/** 仅用于诊断与类型核对的稳定标记，不含时间戳 / 路径。 */
	virtual FString GetBindingKind() const = 0;
};

/** 声明式扩展描述；对应 Python 的 ExtensionSpec（schema 用确定性文本承载）。 */
struct UEASSETLENS_API FUALExtensionSpec
{
	FString Namespace;                 // 九个固定 namespace 之一
	FString Name;                      // namespace.name 唯一键的 name 部分
	FString Version;
	int32 Priority = 0;
	TArray<FString> AppliesTo;         // 精确资产类名，或 "*" 通配
	TArray<FString> Capabilities;
	FString SchemaText;                // 确定性 schema/契约文本，不用 Json DOM

	FUALExtensionSpec()
	{
		Version = TEXT("1");
		AppliesTo.Add(TEXT("*"));
	}

	FString Key() const { return Namespace + TEXT(".") + Name; }
};

/**
 * 进程内单例。Register 拒绝重复键与非法 namespace；Resolve / Snapshot
 * 与插入顺序无关，排序为「精确匹配 > 通配，优先级降序，键升序」。
 *
 * 无锁设计：注册发生在主线程启动期（或 Commandlet 的 Main），后台任务只读取
 * 值拷贝，不在工作线程上改写本注册表。
 */
class UEASSETLENS_API FUALExtensionRegistry
{
public:
	static FUALExtensionRegistry& Get();

	/** 九个固定 namespace，顺序稳定。 */
	static const TArray<FString>& Namespaces();
	static bool IsValidNamespace(const FString& Namespace);

	/** 返回 false：键重复、namespace 未知、name 为空或 AppliesTo 为空。 */
	bool Register(const FUALExtensionSpec& Spec,
		const TSharedPtr<IUALExtensionBinding>& Binding = TSharedPtr<IUALExtensionBinding>());

	bool Unregister(const FString& Key);

	/** 按资产类 / namespace / capability 过滤，返回确定性排序的描述列表。 */
	TArray<FUALExtensionSpec> Resolve(const FString& AssetClass,
		const FString& Namespace = FString(), const FString& Capability = FString()) const;

	/** 按键升序的文档 / CI 视图；不含实现载体。 */
	TArray<FUALExtensionSpec> Snapshot() const;

	/** 取回可选的类型擦除实现载体；未注册返回无效指针。 */
	TSharedPtr<IUALExtensionBinding> FindBinding(const FString& Key) const;

	int32 Num() const { return Entries.Num(); }
	void Clear();

private:
	struct FEntry
	{
		FUALExtensionSpec Spec;
		TSharedPtr<IUALExtensionBinding> Binding;
	};

	TMap<FString, FEntry> Entries;
};
