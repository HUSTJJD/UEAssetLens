using UnrealBuildTool;

// Editor 模块：提取器接口 / 注册表 / 通用反射兜底 / IndexSink / 无头 Commandlet / 模块入口。
// 依赖 AssetRegistry 只读 FAssetData（不加载贴图/网格）。Json/JsonUtilities 保留：
// FJsonObject 仍是内存 DOM，UEYaml.h 只负责 YAML 文本 <-> FJsonObject。
public class UEAssetLensEditor : ModuleRules
{
	public UEAssetLensEditor(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

		// symbol errors. Keep separate compilation units.

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"UEYaml",
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
			"UnrealEd",      // UCommandlet 所在的编辑器命令环境
			"Projects"       // IPluginManager::Get()（扫描插件挂载根）
		});
	}
}
