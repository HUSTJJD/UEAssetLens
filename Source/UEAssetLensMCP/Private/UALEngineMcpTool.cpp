// Copyright AnnBuildTools. All Rights Reserved.

#include "UALEngineMcpTool.h"

#include "UALEngineMcpBridge.h"

#include "Async/Async.h"
#include "Dom/JsonObject.h"
#include "JsonDomBuilder.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

namespace
{
	FString SerializeParams(const TSharedPtr<FJsonObject>& Params)
	{
		if (!Params.IsValid())
		{
			return TEXT("{}");
		}
		FString Out;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
		return FJsonSerializer::Serialize(Params.ToSharedRef(), Writer) ? Out : FString(TEXT("{}"));
	}

	bool IsKnownAccessMode(const FString& AccessMode)
	{
		return AccessMode == TEXT("read") || AccessMode == TEXT("write") ||
			AccessMode == TEXT("execute");
	}
}

FUALEngineMcpTool::FUALEngineMcpTool(FString InName, FString InDescription,
	TSharedPtr<FJsonObject> InInputSchema, FString InAccessMode, bool bInReadOnlyHint)
	: Name(MoveTemp(InName))
	, Description(MoveTemp(InDescription))
	, InputSchema(MoveTemp(InInputSchema))
	, AccessMode(MoveTemp(InAccessMode))
	, bReadOnlyHint(bInReadOnlyHint)
{
	// 未标注 / 未知值按 Execute 处理；readOnlyHint 只允许 Read 为 true。
	if (!IsKnownAccessMode(AccessMode))
	{
		AccessMode = TEXT("execute");
	}
	if (AccessMode != TEXT("read"))
	{
		bReadOnlyHint = false;
	}
}

TSharedPtr<FJsonObject> FUALEngineMcpTool::GetInputJsonSchema() const
{
	return InputSchema.IsValid()
		? InputSchema
		: IModelContextProtocolTool::GetInputJsonSchema();
}

TSharedPtr<FJsonObject> FUALEngineMcpTool::GetAnnotations() const
{
	// 逐工具来自 tool_schemas.yaml 的描述符，匹配 ToolsetRegistry 的
	// {"accessMode": "read"|"write"|"execute", "readOnlyHint": ...} 约定。
	FJsonDomBuilder::FObject Annotations;
	Annotations.Set(TEXT("accessMode"), *AccessMode);
	Annotations.Set(TEXT("readOnlyHint"), bReadOnlyHint);
	return Annotations.AsJsonObject().ToSharedPtr();
}

FModelContextProtocolToolResult FUALEngineMcpTool::Execute(const FString& ParamsJson) const
{
	FString Text;
	FString Error;
	if (FUALEngineMcpBridge::Get().CallTool(Name, ParamsJson, Text, Error))
	{
		return UE::ModelContextProtocol::MakeTextResult(Text);
	}
	return UE::ModelContextProtocol::MakeErrorResult(Error);
}

FModelContextProtocolToolResult FUALEngineMcpTool::Run(const TSharedPtr<FJsonObject>& Params)
{
	return Execute(SerializeParams(Params));
}

void FUALEngineMcpTool::RunAsync(const FModelContextProtocolToolRequestId& /*RequestId*/,
	const TSharedPtr<FJsonObject>& Params, const FResultCallback& OnComplete)
{
	// Serialise on the calling (game) thread; only immutable FString values
	// cross into the worker. The MCP contract allows OnComplete from any thread.
	const FString ParamsJson = SerializeParams(Params);
	const FString ToolName = Name;

	Async(EAsyncExecution::ThreadPool, [ToolName, ParamsJson, OnComplete]()
	{
		FString Text;
		FString Error;
		const bool bOk = FUALEngineMcpBridge::Get().CallTool(ToolName, ParamsJson, Text, Error);
		OnComplete(bOk
			? UE::ModelContextProtocol::MakeTextResult(Text)
			: UE::ModelContextProtocol::MakeErrorResult(Error));
	});
}
