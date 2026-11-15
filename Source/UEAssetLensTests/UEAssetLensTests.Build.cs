using UnrealBuildTool;

// 自动化测试模块：仅验证 Runtime 底座里的确定性指纹数学工具（UALFingerprint）。
// 测试代码放在 WITH_DEV_AUTOMATION_TESTS 条件内，发布构建不参与编译。
public class UEAssetLensTests : ModuleRules
{
	public UEAssetLensTests(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",          // AutomationTest 框架 / FMath / TArray
			"CoreUObject",
			"Engine",
			"UEAssetLens"    // FUALFingerprint
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
		});
	}
}
