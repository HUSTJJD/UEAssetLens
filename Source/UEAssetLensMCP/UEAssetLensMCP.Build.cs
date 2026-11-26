// Copyright AnnBuildTools. All Rights Reserved.

using UnrealBuildTool;

public class UEAssetLensMCP : ModuleRules
{
	public UEAssetLensMCP(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"UEYaml",
			"Core",
			"CoreUObject",
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"Engine",
			"Json",           // FJsonObject DOM + GetInputJsonSchema() (SDK contract)
			"JsonUtilities",
			"Projects",
			// Epic's first-party in-editor MCP server (UE6 Experimental + NoRedist).
			// The plugin reference in UEAssetLens.uplugin stays Optional; this
			// module is development-only and is not redistributed with releases.
			// Engines without the plugin simply do not build/activate this module.
			"ModelContextProtocol",
		});
	}
}
