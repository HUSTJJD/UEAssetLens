// Copyright AnnBuildTools. All Rights Reserved.

#pragma once

#include "IModelContextProtocolTool.h"
#include "ModelContextProtocolToolResults.h"

/**
 * One UAL tool exposed through Epic's in-editor MCP server.
 *
 * Name / description / inputSchema all come from Server/ual_mcp/tool_schemas.yaml,
 * which is generated from TOOL_DEFS - the same contract used by the Python
 * stdio server and the generated reference docs. This keeps all three frontends
 * from drifting apart. The DOM stays FJsonObject (SDK-mandated signatures).
 */
class FUALEngineMcpTool : public IModelContextProtocolTool
{
public:
	FUALEngineMcpTool(FString InName, FString InDescription, TSharedPtr<FJsonObject> InInputSchema);

	virtual FString GetName() const override { return Name; }
	virtual FString GetDescription() const override { return Description; }
	virtual TSharedPtr<FJsonObject> GetInputJsonSchema() const override;
	virtual TSharedPtr<FJsonObject> GetAnnotations() const override;

	/** Synchronous fallback (used by MCP Inspector and unit-style calls). */
	virtual FModelContextProtocolToolResult Run(const TSharedPtr<FJsonObject>& Params) override;

	/** Preferred path: keep the editor game thread free while Python runs. */
	virtual void RunAsync(const FModelContextProtocolToolRequestId& RequestId,
		const TSharedPtr<FJsonObject>& Params, const FResultCallback& OnComplete) override;

private:
	FModelContextProtocolToolResult Execute(const FString& ParamsJson) const;

	FString Name;
	FString Description;
	TSharedPtr<FJsonObject> InputSchema;
};
