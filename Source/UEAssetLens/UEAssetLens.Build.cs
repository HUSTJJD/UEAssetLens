using UnrealBuildTool;

// Runtime 底座模块：只放与引擎无关的数据类型、确定性指纹数学工具与模块入口。
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
