// Copyright AnnBuildTools. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleManager.h"

struct IModelContextProtocolTool;

/**
 * Registers the four UAL tools with Epic's in-editor MCP server via
 * IModelContextProtocolModule::AddTool.
 *
 * We deliberately do NOT use UToolsetDefinition: ToolsetRegistry qualifies
 * every name as "<ToolsetName>.<ToolName>", which would break the 1:1 contract
 * shared with the Python MCP server and with tool_schemas.yaml.
 *
 * The module is a no-op when the ModelContextProtocol plugin is not enabled.
 */
class FUALEngineMcpModule : public IModuleInterface
{
public:
	virtual void StartupModule() override;
	virtual void ShutdownModule() override;

private:
	void RegisterTools();
	void HandleRefreshTools();

	TArray<TSharedRef<IModelContextProtocolTool>> RegisteredTools;
	FDelegateHandle RefreshToolsHandle;
};
