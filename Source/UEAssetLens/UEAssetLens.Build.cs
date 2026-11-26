using UnrealBuildTool;

// Runtime 底座模块：与引擎无关的数据类型、确定性指纹数学、字段 / 扩展注册表与模块入口。
// 两个注册表（UALFieldRegistry / UALExtensionRegistry）只用 CoreMinimal，
// 不依赖 AssetRegistry / Editor / Json / DuckDB；编辑器侧可反向依赖本模块。
// 编辑器侧（提取器 / Commandlet / Sink）依赖本模块，但本模块不反向依赖任何 Editor 模块。
public class UEAssetLens : ModuleRules
{
	public UEAssetLens(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",          // FString / TArray / TMap / FVector / FMath
			"CoreUObject",   // USTRUCT / UENUM / GENERATED_BODY 反射
			"Engine"         // UObject 基础类型
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
		});
	}
}
