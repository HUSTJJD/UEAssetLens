#include "UALIndexBuilderCommandlet.h"

#include "UALExtractorRegistry.h"
#include "FUniversalReflectionExtractor.h"
#include "UALJsonlIndexSink.h"
#include "UALTypes.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "AssetRegistry/AssetData.h"
#include "AssetRegistry/AssetIdentifier.h"
#include "Misc/Paths.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "HAL/FileManager.h"
#include "Modules/ModuleManager.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

DEFINE_LOG_CATEGORY_STATIC(LogUALIndexBuilder, Log, All);

UUALIndexBuilderCommandlet::UUALIndexBuilderCommandlet(const FObjectInitializer& ObjectInitializer)
	: Super(ObjectInitializer)
{
	IsClient = false;
	IsEditor = true;
	IsServer = false;
	LogToConsole = true;

	HelpDescription = TEXT("Build the deterministic UEAssetLens asset specification index (offline / headless).");
	HelpUsage = TEXT("-run=UALIndexBuilder [-Project=<dir>] [-Out=<name>] [-Incremental]");
	HelpParamNames.Add(TEXT("-Project="));
	HelpParamNames.Add(TEXT("-Out="));
	HelpParamNames.Add(TEXT("-Incremental"));
}

void UUALIndexBuilderCommandlet::ParseParams(const FString& Params,
	FString& OutProject, FString& OutOutput, bool& bOutIncremental) const
{
	OutProject = FPaths::ProjectDir();
	OutOutput = TEXT("index");
	bOutIncremental = false;

	FString Val;
	if (FParse::Value(*Params, TEXT("Project="), Val) && !Val.IsEmpty())
	{
		OutProject = Val;
	}
	if (FParse::Value(*Params, TEXT("Out="), Val) && !Val.IsEmpty())
	{
		OutOutput = Val;
	}
	bOutIncremental = FParse::Param(*Params, TEXT("Incremental"));
}

TMap<FString, FString> UUALIndexBuilderCommandlet::LoadExistingHashes(const FString& StagingDir) const
{
	TMap<FString, FString> Result;

	TArray<FString> Files;
	IFileManager::Get().FindFiles(Files,
		*FPaths::Combine(StagingDir, TEXT("*.jsonl")), /*Files=*/true, /*Directories=*/false);

	for (const FString& File : Files)
	{
		FString Content;
		const FString FullPath = FPaths::Combine(StagingDir, File);
		if (!FFileHelper::LoadFileToString(Content, *FullPath))
		{
			continue;
		}

		TArray<FString> Lines;
		Content.ParseIntoArrayLines(Lines);
		for (const FString& Line : Lines)
		{
			if (Line.TrimStartAndEnd().IsEmpty())
			{
				continue;
			}
			TSharedPtr<FJsonObject> Obj;
			TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Line);
			if (!FJsonSerializer::Deserialize(Reader, Obj) || !Obj.IsValid())
			{
				continue;
			}
			FString Path;
			FString Hash;
			if (Obj->TryGetStringField(TEXT("asset_path"), Path) &&
				Obj->TryGetStringField(TEXT("content_hash"), Hash))
			{
				Result.Add(Path, Hash);
			}
		}
	}
	return Result;
}

int32 UUALIndexBuilderCommandlet::Main(const FString& Params)
{
	FString ProjectDir;
	FString OutName;
	bool bIncremental = false;
	ParseParams(Params, ProjectDir, OutName, bIncremental);

	UE_LOG(LogUALIndexBuilder, Log, TEXT("=== UALIndexBuilder (-run=UALIndexBuilder) start ==="));
	UE_LOG(LogUALIndexBuilder, Log, TEXT("Project=%s Out=%s Incremental=%d"),
		*ProjectDir, *OutName, bIncremental ? 1 : 0);

	// 1) 确保 AssetRegistry 就绪：仅扫描元数据，不加载贴图/网格进显存。
	FAssetRegistryModule& ARM =
		FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
	IAssetRegistry& AR = ARM.Get();
	TArray<FString> ScanPaths;
	ScanPaths.Add(TEXT("/Game"));
	AR.ScanPathsSynchronous(ScanPaths, /*bForceRescan=*/false);

	// 2) 提取器注册表：显式注册通用兜底（Priority 0）。
	//    领域提取器（Priority 100）可由其它模块通过 Register 注入。
	FUALExtractorRegistry Registry;
	Registry.Register(MakeShared<FUniversalReflectionExtractor>());

	// 3) 增量：读取已有清单的 content_hash。
	TMap<FString, FString> ExistingHashes;
	if (bIncremental)
	{
		const FString StagingDir = FPaths::Combine(ProjectDir, TEXT(".ual"), TEXT("staging"));
		ExistingHashes = LoadExistingHashes(StagingDir);
		UE_LOG(LogUALIndexBuilder, Log, TEXT("Incremental: loaded %d existing content_hash(es)"),
			ExistingHashes.Num());
	}

	// 4) 打开 Sink（生产为 DuckDB C API Appender，这里为 JSONL 参考实现）。
	FUALJsonlIndexSink Sink(OutName, ProjectDir);
	if (!Sink.Begin())
	{
		UE_LOG(LogUALIndexBuilder, Error, TEXT("Failed to open index sink."));
		return 1;
	}

	// 5) 遍历 /Game 下全部资产。
	TArray<FAssetData> Assets;
	AR.GetAssetsByPath(TEXT("/Game"), Assets, /*bRecursive=*/true);
	UE_LOG(LogUALIndexBuilder, Log, TEXT("AssetRegistry returned %d assets under /Game"),
		Assets.Num());

	int32 Written = 0;
	int32 Skipped = 0;
	int32 EdgeCount = 0;

	for (const FAssetData& Asset : Assets)
	{
		FUALAssetRecord Record;
		if (!Registry.ExtractRecord(Asset, Record))
		{
			++Skipped;
			continue;
		}

		// -Incremental：content_hash 未变更则跳过（哈希只作变更检测，不作查重依据）。
		if (bIncremental)
		{
			const FString* PrevHash = ExistingHashes.Find(Record.AssetPath);
			if (PrevHash != nullptr && *PrevHash == Record.ContentHash)
			{
				++Skipped;
				continue;
			}
		}

		Sink.AppendRecord(Record);
		++Written;

		// 6) DEPENDS_ON 边：只读 AssetRegistry 依赖关系，不加载资源。
		//    用 FAssetIdentifier 核心接口（UE5 IAssetRegistry 稳定签名），
		//    目标可能指向 /Script 或引擎内容，交由 Python 灌库侧过滤。
		TArray<FAssetIdentifier> Dependencies;
		AR.GetDependencies(FAssetIdentifier(Asset.PackageName), Dependencies,
			EAssetRegistryDependencyType::All);
		for (const FAssetIdentifier& Dep : Dependencies)
		{
			FUALEdgeRecord Edge;
			Edge.SourcePath = Record.AssetPath;
			Edge.TargetPath = Dep.PackageName.ToString();
			Edge.EdgeType = EUALEdgeType::DEPENDS_ON;
			Edge.Weight = 1.0f;
			Sink.AppendEdge(Edge);
			++EdgeCount;
		}
	}

	const bool bCommitted = Sink.Commit();
	UE_LOG(LogUALIndexBuilder, Log,
		TEXT("=== UALIndexBuilder done: total=%d written=%d skipped=%d edges=%d commit=%d ==="),
		Assets.Num(), Written, Skipped, EdgeCount, bCommitted ? 1 : 0);

	return bCommitted ? 0 : 1;
}
