#include "UALIndexBuilderCommandlet.h"

#include "UALExtractorRegistry.h"
#include "FUniversalReflectionExtractor.h"
#include "UALJsonlIndexSink.h"
#include "UALTypes.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "AssetRegistry/AssetData.h"
#include "AssetRegistry/AssetIdentifier.h"
#include "Interfaces/IPluginManager.h"
#include "Containers/Set.h"
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
	HelpUsage = TEXT("-run=UALIndexBuilder [-Project=<dir>] [-Out=<name>] [-Incremental] "
		"[-Roots=/Game,/PluginA] [-IncludePlugins]");
	HelpParamNames.Add(TEXT("-Project="));
	HelpParamNames.Add(TEXT("-Out="));
	HelpParamNames.Add(TEXT("-Incremental"));
	HelpParamNames.Add(TEXT("-Roots="));
	HelpParamNames.Add(TEXT("-IncludePlugins"));
}

void UUALIndexBuilderCommandlet::ParseParams(const FString& Params,
	FString& OutProject, FString& OutOutput, bool& bOutIncremental,
	TArray<FString>& OutRoots, bool& bOutIncludePlugins) const
{
	OutProject = FPaths::ProjectDir();
	OutOutput = TEXT("index");
	bOutIncremental = false;
	bOutIncludePlugins = false;
	OutRoots.Reset();

	FString Val;
	if (FParse::Value(*Params, TEXT("Project="), Val) && !Val.IsEmpty())
	{
		OutProject = Val;
	}
	if (FParse::Value(*Params, TEXT("Out="), Val) && !Val.IsEmpty())
	{
		OutOutput = Val;
	}
	// 逗号分隔的挂载根；未指定时保持旧行为，只扫 /Game。
	if (FParse::Value(*Params, TEXT("Roots="), Val) && !Val.IsEmpty())
	{
		Val.ParseIntoArray(OutRoots, TEXT(","), /*bCullEmpty=*/true);
	}
	if (OutRoots.IsEmpty())
	{
		OutRoots.Add(TEXT("/Game"));
	}
	bOutIncremental = FParse::Param(*Params, TEXT("Incremental"));
	bOutIncludePlugins = FParse::Param(*Params, TEXT("IncludePlugins"));
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
	TArray<FString> Roots;
	bool bIncludePlugins = false;
	ParseParams(Params, ProjectDir, OutName, bIncremental, Roots, bIncludePlugins);

	UE_LOG(LogUALIndexBuilder, Log, TEXT("=== UALIndexBuilder (-run=UALIndexBuilder) start ==="));
	UE_LOG(LogUALIndexBuilder, Log, TEXT("Project=%s Out=%s Incremental=%d IncludePlugins=%d"),
		*ProjectDir, *OutName, bIncremental ? 1 : 0, bIncludePlugins ? 1 : 0);

	// 1) 收集扫描根。默认保持旧行为只扫 /Game；-Roots 显式指定，
	//    -IncludePlugins 自动追加所有含内容插件的挂载根（UEAbilityKit / UnLuaNova 等）。
	if (bIncludePlugins)
	{
		for (const TSharedRef<IPlugin>& Plugin : IPluginManager::Get().GetEnabledPluginsWithContent())
		{
			FString Mounted = Plugin->GetMountedAssetPath();
			Mounted.RemoveFromEnd(TEXT("/"));
			if (Mounted.StartsWith(TEXT("/")) && Mounted.Len() > 1)
			{
				Roots.AddUnique(Mounted);
			}
		}
	}

	TArray<FString> ScanPaths;
	for (FString Root : Roots)
	{
		Root.TrimStartAndEndInline();
		Root.RemoveFromEnd(TEXT("/"));
		if (!Root.StartsWith(TEXT("/")))
		{
			Root = TEXT("/") + Root;
		}
		if (Root.Len() > 1)
		{
			ScanPaths.AddUnique(Root);
		}
	}

	// 2) 确保 AssetRegistry 就绪：仅扫描元数据，不加载贴图/网格进显存。
	FAssetRegistryModule& ARM =
		FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
	IAssetRegistry& AR = ARM.Get();
	AR.ScanPathsSynchronous(ScanPaths, /*bForceRescan=*/false);
	UE_LOG(LogUALIndexBuilder, Log, TEXT("Scan roots (%d): %s"),
		ScanPaths.Num(), *FString::Join(ScanPaths, TEXT(", ")));

	// 3) 提取器注册表：显式注册通用兜底（Priority 0）。
	//    领域提取器（Priority 100）可由其它模块通过 Register 注入。
	FUALExtractorRegistry Registry;
	Registry.Register(MakeShared<FUniversalReflectionExtractor>());

	// 4) 增量：读取已有清单的 content_hash。
	TMap<FString, FString> ExistingHashes;
	if (bIncremental)
	{
		const FString StagingDir = FPaths::Combine(ProjectDir, TEXT(".ual"), TEXT("staging"));
		ExistingHashes = LoadExistingHashes(StagingDir);
		UE_LOG(LogUALIndexBuilder, Log, TEXT("Incremental: loaded %d existing content_hash(es)"),
			ExistingHashes.Num());
	}

	// 5) 打开 Sink（生产为 DuckDB C API Appender，这里为 JSONL 参考实现）。
	FUALJsonlIndexSink Sink(OutName, ProjectDir);
	if (!Sink.Begin())
	{
		UE_LOG(LogUALIndexBuilder, Error, TEXT("Failed to open index sink."));
		return 1;
	}

	// 6) 遍历全部扫描根下的资产。按 PackageName 去重，避免父子根（如 /Game 与
	//    /Game/Characters）被重复计入；不按 AssetClass 过滤，任何 UObject 派生类
	//    都会由 FUALExtractorRegistry 的通用兜底提取器产出记录。
	TArray<FAssetData> Assets;
	TSet<FName> SeenPackages;
	for (const FString& Root : ScanPaths)
	{
		TArray<FAssetData> RootAssets;
		AR.GetAssetsByPath(FName(*Root), RootAssets, /*bRecursive=*/true);
		for (FAssetData& Asset : RootAssets)
		{
			if (!SeenPackages.Contains(Asset.PackageName))
			{
				SeenPackages.Add(Asset.PackageName);
				Assets.Add(MoveTemp(Asset));
			}
		}
		UE_LOG(LogUALIndexBuilder, Log, TEXT("Root %s -> %d assets"), *Root, RootAssets.Num());
	}
	UE_LOG(LogUALIndexBuilder, Log, TEXT("AssetRegistry returned %d unique assets over %d root(s)"),
		Assets.Num(), ScanPaths.Num());

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

		// 7) DEPENDS_ON 边：只读 AssetRegistry 依赖关系，不加载资源。
		//    用 FAssetIdentifier 核心接口（UE5 IAssetRegistry 稳定签名），
		//    目标可能指向 /Script 或引擎内容，交由 Python 灌库侧过滤。
		TArray<FAssetIdentifier> Dependencies;
		AR.GetDependencies(FAssetIdentifier(Asset.PackageName), Dependencies,
			UE::AssetRegistry::EDependencyCategory::All);
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
