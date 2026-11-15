// Copyright AnnBuildTools. All Rights Reserved.

#include "UALEngineMcpModule.h"

#include "UALEngineMcpBridge.h"
#include "UALEngineMcpLog.h"
#include "UALEngineMcpTool.h"

#include "Dom/JsonObject.h"
#include "IModelContextProtocolModule.h"
#include "IModelContextProtocolTool.h"
#include "Misc/FileHelper.h"
#include "Modules/ModuleManager.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

DEFINE_LOG_CATEGORY(LogUALEngineMcp);

namespace
{
	struct FToolDescriptor
	{
		FString Name;
		FString Description;
		TSharedPtr<FJsonObject> InputSchema;
	};

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

		TSharedPtr<FJsonObject> Root;
		const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Raw);
		if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
		{
			UE_LOGF(LogUALEngineMcp, Error, "Invalid JSON in %ls", *SchemaPath);
			return false;
		}

		const TArray<TSharedPtr<FJsonValue>>* Tools = nullptr;
		if (!Root->TryGetArrayField(TEXT("tools"), Tools) || Tools == nullptr)
		{
			UE_LOGF(LogUALEngineMcp, Error, "Missing 'tools' array in %ls", *SchemaPath);
			return false;
		}

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
			OutDescriptors.Add(MoveTemp(Descriptor));
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
			MoveTemp(Descriptor.Name), MoveTemp(Descriptor.Description), MoveTemp(Descriptor.InputSchema));
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
