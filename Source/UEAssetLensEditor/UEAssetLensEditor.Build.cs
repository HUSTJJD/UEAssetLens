using UnrealBuildTool;

// Editor 模块：提取器接口 / 注册表 / 通用反射兜底 / IndexSink / 无头 Commandlet / 模块入口。
// 依赖 AssetRegistry 只读 FAssetData（不加载贴图/网格），Json 仅用于 JSONL 序列化。
public class UEAssetLensEditor : ModuleRules
{
	public UEAssetLensEditor(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"UEAssetLens",   // FUALAssetRecord / FUALEdgeRecord / FUALFingerprint
			"AssetRegistry", // FAssetData / IAssetRegistry / FAssetRegistryModule
			"Json",
			"JsonUtilities"
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"UnrealEd"       // UCommandlet 所在的编辑器命令环境
		});
	}
}
