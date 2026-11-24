// Copyright AnnBuildTools. All Rights Reserved.

#include "UALEngineMcpBridge.h"

#include "UALEngineMcpLog.h"

#include "UEYaml.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

#include "HAL/FileManager.h"
#include "HAL/PlatformProcess.h"
#include "Interfaces/IPluginManager.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/FileHelper.h"
#include "Misc/Guid.h"
#include "Misc/Paths.h"

namespace
{
	const TCHAR* const SettingsSection = TEXT("/Script/UEAssetLensMCP.UALEngineMcpSettings");

	FString ReadSetting(const TCHAR* Key, const FString& Fallback)
	{
		FString Value;
		if (GConfig)
		{
			GConfig->GetString(SettingsSection, Key, Value, GEditorPerProjectIni);
		}
		return Value.IsEmpty() ? Fallback : Value;
	}

	FString ResolveDefaultPythonExecutable()
	{
		// UE6 ships its own Python 3.11 interpreter, and the official
		// PythonRequirements mechanism installs plugin dependencies into
		// <Project>/Intermediate/PipInstall/Lib/site-packages using that
		// interpreter. The bridge runs as an external child process, so it
		// must use the same ABI instead of whatever "python" resolves to on
		// PATH (which may be a different major version without duckdb).
#if PLATFORM_WINDOWS
		const TCHAR* PlatformDir = TEXT("Win64");
		const TCHAR* RelativeExe = TEXT("python.exe");
#elif PLATFORM_MAC
		const TCHAR* PlatformDir = TEXT("Mac");
		const TCHAR* RelativeExe = TEXT("bin/python3");
#elif PLATFORM_LINUX
		const TCHAR* PlatformDir = TEXT("Linux");
		const TCHAR* RelativeExe = TEXT("bin/python3");
#else
		return TEXT("python");
#endif
		const FString Candidate = FPaths::ConvertRelativePathToFull(
			FPaths::Combine(FPaths::EngineDir(), TEXT("Binaries"), TEXT("ThirdParty"),
				TEXT("Python3"), PlatformDir, RelativeExe));
		return IFileManager::Get().FileExists(*Candidate) ? Candidate : TEXT("python");
	}

	FString ResolvePluginDir()
	{
		TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("UEAssetLens"));
		const FString Base = Plugin.IsValid()
			? Plugin->GetBaseDir()
			: FPaths::Combine(FPaths::ProjectPluginsDir(), TEXT("UEAssetLens"));
		return FPaths::ConvertRelativePathToFull(Base);
	}
}

FUALEngineMcpBridge& FUALEngineMcpBridge::Get()
{
	static FUALEngineMcpBridge Instance;
	return Instance;
}

FUALEngineMcpBridge::FUALEngineMcpBridge()
{
	const FString PluginDir = ResolvePluginDir();

	Paths.BridgeScript = FPaths::ConvertRelativePathToFull(
		FPaths::Combine(PluginDir, TEXT("Server"), TEXT("ual_mcp"), TEXT("engine_bridge.py")));
	Paths.SchemaFile = FPaths::ConvertRelativePathToFull(
		FPaths::Combine(PluginDir, TEXT("Server"), TEXT("ual_mcp"), TEXT("tool_schemas.yaml")));
	Paths.WorkingDirectory = FPaths::ConvertRelativePathToFull(
		FPaths::Combine(PluginDir, TEXT("Server")));
	Paths.TempDirectory = FPaths::ConvertRelativePathToFull(
		FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("UEAssetLens"), TEXT("MCP")));

	const FString DefaultIndex = FPaths::ConvertRelativePathToFull(
		FPaths::Combine(FPaths::ProjectDir(), TEXT(".ual"), TEXT("index.duckdb")));
	Paths.IndexFile = ReadSetting(TEXT("IndexPath"), DefaultIndex);
	Paths.PythonExecutable = ReadSetting(TEXT("PythonExecutable"), ResolveDefaultPythonExecutable());

	UE_LOGF(LogUALEngineMcp, Log, "UAL engine MCP bridge: python=%ls index=%ls",
		*Paths.PythonExecutable, *Paths.IndexFile);
}

bool FUALEngineMcpBridge::CallTool(const FString& ToolName, const FString& ParamsJson,
	FString& OutText, FString& OutError) const
{
	const FString Stem = FGuid::NewGuid().ToString(EGuidFormats::Digits);
	IFileManager::Get().MakeDirectory(*Paths.TempDirectory, true);

	const FString RequestPath = FPaths::Combine(Paths.TempDirectory, Stem + TEXT("_request.yaml"));
	const FString ResponsePath = FPaths::Combine(Paths.TempDirectory, Stem + TEXT("_response.txt"));

	auto Cleanup = [&RequestPath, &ResponsePath]()
	{
		IFileManager::Get().Delete(*RequestPath, false, true);
		IFileManager::Get().Delete(*ResponsePath, false, true);
	};

	// ParamsJson 是 SDK 把 FJsonObject 序列化后的内部边界值（不是磁盘格式）。
	// 先反序列化回 FJsonObject DOM，再连同 tool 名组装成 wrapper，由共享
	// emitter 输出 YAML —— 绝不用 FString::Printf 手工拼接请求：中文、冒号、
	// 引号、换行都会破坏 YAML 缩进语义。
	TSharedPtr<FJsonObject> ArgsObject;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(ParamsJson);
	FJsonSerializer::Deserialize(Reader, ArgsObject);

	TSharedRef<FJsonObject> Wrapper = MakeShared<FJsonObject>();
	Wrapper->SetStringField(TEXT("tool"), ToolName);
	Wrapper->SetObjectField(TEXT("arguments"),
		ArgsObject.IsValid() ? ArgsObject.ToSharedRef() : MakeShared<FJsonObject>());
	const FString RequestYaml = UEYaml::EmitDocument(Wrapper);

	if (!FFileHelper::SaveStringToFile(RequestYaml, *RequestPath,
		FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
	{
		OutError = FString::Printf(TEXT("Cannot write UAL MCP request file: %s"), *RequestPath);
		return false;
	}

	// Quoting is required: Windows install paths and project paths commonly contain spaces.
	const FString Args = FString::Printf(
		TEXT("\"%s\" --request \"%s\" --response \"%s\" --index \"%s\""),
		*Paths.BridgeScript, *RequestPath, *ResponsePath, *Paths.IndexFile);

	int32 ReturnCode = -1;
	FString StdOut;
	FString StdErr;
	const bool bLaunched = FPlatformProcess::ExecProcess(
		*Paths.PythonExecutable, *Args, &ReturnCode, &StdOut, &StdErr, *Paths.WorkingDirectory);

	if (!bLaunched)
	{
		OutError = FString::Printf(
			TEXT("Cannot launch UAL Python bridge. PythonExecutable=%s (override it in DefaultEditorPerProject.ini)"),
			*Paths.PythonExecutable);
		Cleanup();
		return false;
	}

	if (ReturnCode != 0)
	{
		OutError = FString::Printf(TEXT("UAL Python bridge failed (exit %d): %s"),
			ReturnCode, *StdErr.Left(512));
		Cleanup();
		return false;
	}

	// Read raw bytes and decode explicitly as UTF-8; LoadFileToString treats
	// BOM-less UTF-8 as ANSI on Windows and would mangle Chinese annotations.
	TArray<uint8> Bytes;
	if (!FFileHelper::LoadFileToArray(Bytes, *ResponsePath))
	{
		OutError = FString::Printf(TEXT("UAL Python bridge produced no response: %s"), *ResponsePath);
		Cleanup();
		return false;
	}
	Bytes.Add(0);
	OutText = FString(UTF8_TO_TCHAR(reinterpret_cast<const ANSICHAR*>(Bytes.GetData())));

	Cleanup();
	return true;
}
