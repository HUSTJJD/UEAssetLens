// Copyright AnnBuildTools. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"

/** All paths/configuration needed to reach the Python-side UAL index. */
struct FUALEngineMcpPaths
{
	FString PythonExecutable;
	FString BridgeScript;
	FString SchemaFile;
	FString IndexFile;
	FString WorkingDirectory;
	FString TempDirectory;
};

/**
 * One-shot bridge to Server/ual_mcp/engine_bridge.py.
 *
 * Uses UTF-8 request/response files instead of stdout: FPlatformProcess::ExecProcess
 * materialises stdout as an ANSI-ish FString, which would corrupt the Chinese
 * semantic annotations carried by UAL's YAML/TOON output on Windows.
 */
class FUALEngineMcpBridge
{
public:
	static FUALEngineMcpBridge& Get();

	const FUALEngineMcpPaths& GetPaths() const { return Paths; }

	/** Runs one tool call synchronously. Safe to invoke from a worker thread. */
	bool CallTool(const FString& ToolName, const FString& ParamsJson,
		FString& OutText, FString& OutError) const;

private:
	FUALEngineMcpBridge();

	FUALEngineMcpPaths Paths;
};
