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
			// Epic's first-party in-editor MCP server (UE6 Experimental).
			// The plugin reference in UEAssetLens.uplugin is marked Optional, so
			// engines without this plugin simply do not build/activate this module.
			"ModelContextProtocol",
		});
	}
}
