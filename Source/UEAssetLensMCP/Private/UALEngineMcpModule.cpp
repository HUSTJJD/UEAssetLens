// Copyright AnnBuildTools. All Rights Reserved.

#include "UALEngineMcpModule.h"

#include "UALEngineMcpBridge.h"
#include "UALEngineMcpLog.h"
#include "UALEngineMcpTool.h"

#include "UEYaml.h"
#include "Dom/JsonObject.h"
#include "IModelContextProtocolModule.h"
#include "IModelContextProtocolTool.h"
#include "Misc/FileHelper.h"
#include "Modules/ModuleManager.h"

DEFINE_LOG_CATEGORY(LogUALEngineMcp);

namespace
{
	struct FToolDescriptor
	{
		FString Name;
		FString Description;
		TSharedPtr<FJsonObject> InputSchema;
		FString AccessMode;      // read | write | execute（小写规范值）
		bool bReadOnlyHint = false;
	};

	/** 只接受 Epic 约定的三个值；未知 / 缺失按 Execute 处理。 */
	FString NormalizeAccessMode(const FString& Raw)
	{
		const FString Lower = Raw.ToLower();
		if (Lower == TEXT("read") || Lower == TEXT("write") || Lower == TEXT("execute"))
		{
			return Lower;
		}
		return FString(TEXT("execute"));
	}

	bool LoadDescriptors(TArray<FToolDescriptor>& OutDescriptors)
	{
		const FString SchemaPath = FUALEngineMcpBridge::Get().GetPaths().SchemaFile;
		FString Raw;
		if (!FFileHelper::LoadFileToString(Raw, *SchemaPath))
		{
			UE_LOGF(LogUALEngineMcp, Error,
				"Cannot read %ls - run `python Tools/gen_docs.py` to regenerate it.", *SchemaPath);
			return false;
		}

		// tool_schemas.yaml 是磁盘格式；解析后仍构造 FJsonObject 喂给 Epic SDK：
		// inputSchema 必须是 FJsonObject（GetInputJsonSchema 的强制签名），Json
		// 模块依赖因此保留。UEYaml.h 用 ryml 完成 YAML -> FJsonObject。
		TSharedPtr<FJsonObject> Root = UEYaml::ParseDocument(Raw);
		if (!Root.IsValid())
		{
			UE_LOGF(LogUALEngineMcp, Error, "Invalid YAML in %ls", *SchemaPath);
			return false;
		}

		const TArray<TSharedPtr<FJsonValue>>* Tools = nullptr;
		if (!Root->TryGetArrayField(TEXT("tools"), Tools) || Tools == nullptr)
		{
			UE_LOGF(LogUALEngineMcp, Error, "Missing 'tools' array in %ls", *SchemaPath);
			return false;
		}

		TArray<FString> MissingAnnotations;
		for (const TSharedPtr<FJsonValue>& Value : *Tools)
		{
			if (!Value.IsValid())
			{
				continue;
			}
			const TSharedPtr<FJsonObject> Object = Value->AsObject();
			if (!Object.IsValid())
			{
				continue;
			}

			FToolDescriptor Descriptor;
			if (!Object->TryGetStringField(TEXT("name"), Descriptor.Name))
			{
				continue;
			}
			Object->TryGetStringField(TEXT("description"), Descriptor.Description);

			const TSharedPtr<FJsonObject>* SchemaObject = nullptr;
			if (Object->TryGetObjectField(TEXT("inputSchema"), SchemaObject)
				&& SchemaObject != nullptr && SchemaObject->IsValid())
			{
				Descriptor.InputSchema = *SchemaObject;
			}

			// 注释字段在描述符顶层（不是 annotations 子对象）。
			FString RawAccessMode;
			const bool bHasAccessMode = Object->TryGetStringField(TEXT("accessMode"), RawAccessMode)
				&& !RawAccessMode.IsEmpty();
			const FString LowerAccessMode = RawAccessMode.ToLower();
			const bool bValidAccessMode = bHasAccessMode &&
				(LowerAccessMode == TEXT("read") || LowerAccessMode == TEXT("write") ||
				 LowerAccessMode == TEXT("execute"));
			Descriptor.AccessMode = bValidAccessMode
				? NormalizeAccessMode(RawAccessMode)
				: FString(TEXT("execute"));

			bool bHint = false;
			bool bHasHint = Object->TryGetBoolField(TEXT("readOnlyHint"), bHint);
			if (!bHasHint)
			{
				FString HintText;
				bHasHint = Object->TryGetStringField(TEXT("readOnlyHint"), HintText);
				if (bHasHint)
				{
					bHint = HintText.Equals(TEXT("true"), ESearchCase::IgnoreCase);
				}
			}
			// readOnlyHint 只有 Read 允许为 true；缺失按 false。
			Descriptor.bReadOnlyHint = bHasHint && bHint && Descriptor.AccessMode == TEXT("read");

			if (!bValidAccessMode || !bHasHint)
			{
				MissingAnnotations.Add(Descriptor.Name);
			}

			OutDescriptors.Add(MoveTemp(Descriptor));
		}

		// 启动时一次性列出缺失 / 非法注释的工具；未标注按 Execute 处理。
		if (MissingAnnotations.Num() > 0)
		{
			const FString MissingList = FString::Join(MissingAnnotations, TEXT(", "));
			UE_LOGF(LogUALEngineMcp, Warning,
				"Tools with missing/invalid accessMode or missing readOnlyHint (treated as Execute / readOnlyHint=false): %ls",
				*MissingList);
		}
		return OutDescriptors.Num() > 0;
	}
}

void FUALEngineMcpModule::StartupModule()
{
	// ModelContextProtocol is a Runtime module at LoadingPhase Default, so it is
	// already loaded by our PostEngineInit phase when the plugin is enabled.
	if (!FModuleManager::Get().IsModuleLoaded(TEXT("ModelContextProtocol")))
	{
		UE_LOGF(LogUALEngineMcp, Warning,
			"Epic's ModelContextProtocol plugin is not enabled; UAL in-editor MCP tools are not registered. "
			"The standalone Python MCP server and ual-cli remain fully functional.");
		return;
	}

	RegisterTools();

	if (IModelContextProtocolModule* Mcp = IModelContextProtocolModule::Get())
	{
		RefreshToolsHandle = Mcp->OnRefreshTools().AddRaw(this, &FUALEngineMcpModule::HandleRefreshTools);
	}
}

void FUALEngineMcpModule::ShutdownModule()
{
	if (IModelContextProtocolModule* Mcp = IModelContextProtocolModule::Get())
	{
		Mcp->OnRefreshTools().Remove(RefreshToolsHandle);
		for (const TSharedRef<IModelContextProtocolTool>& Tool : RegisteredTools)
		{
			Mcp->RemoveTool(Tool);
		}
	}
	RegisteredTools.Reset();
}

void FUALEngineMcpModule::RegisterTools()
{
	IModelContextProtocolModule* Mcp = IModelContextProtocolModule::Get();
	if (Mcp == nullptr)
	{
		return;
	}

	TArray<FToolDescriptor> Descriptors;
	if (!LoadDescriptors(Descriptors))
	{
		return;
	}

	for (FToolDescriptor& Descriptor : Descriptors)
	{
		TSharedRef<IModelContextProtocolTool> Tool = MakeShared<FUALEngineMcpTool>(
			MoveTemp(Descriptor.Name), MoveTemp(Descriptor.Description), MoveTemp(Descriptor.InputSchema),
			MoveTemp(Descriptor.AccessMode), Descriptor.bReadOnlyHint);
		if (Mcp->AddTool(Tool))
		{
			RegisteredTools.Add(Tool);
		}
		else
		{
			const FString ToolName = Tool->GetName();
			UE_LOGF(LogUALEngineMcp, Warning,
				"Tool name already served by the MCP server: %ls", *ToolName);
		}
	}
	UE_LOGF(LogUALEngineMcp, Log,
		"Registered %d UAL tools with Epic's in-editor MCP server.", RegisteredTools.Num());
}

void FUALEngineMcpModule::HandleRefreshTools()
{
	// ModelContextProtocol.RefreshTools releases every tool before broadcasting,
	// so re-add ours rather than removing them individually.
	RegisteredTools.Reset();
	RegisterTools();
}

IMPLEMENT_MODULE(FUALEngineMcpModule, UEAssetLensMCP);
