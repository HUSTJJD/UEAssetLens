#include "UALIndexBuilderCommandlet.h"

#include "UALExtractorRegistry.h"
#include "FUniversalReflectionExtractor.h"
#include "UALTypedFeatureExtractor.h"
#include "UALYamlIndexSink.h"
#include "UEYaml.h"
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

DEFINE_LOG_CATEGORY_STATIC(LogUALIndexBuilder, Log, All);

namespace
{
	/** Deterministic name tokenizer shared by the co-occurrence heuristic. */
	TSet<FString> NameTokens(const FString& Name)
	{
		TSet<FString> Out;
		FString Current;
		for (int32 i = 0; i < Name.Len(); ++i)
		{
			const TCHAR Ch = Name[i];
			if (FChar::IsAlnum(Ch))
			{
				const bool bCamelBoundary = FChar::IsUpper(Ch) && Current.Len() > 0 &&
					(FChar::IsLower(Name[i - 1]) || FChar::IsDigit(Name[i - 1]));
				if (bCamelBoundary)
				{
					Out.Add(Current.ToLower());
					Current.Empty();
				}
				Current.AppendChar(Ch);
			}
			else if (Current.Len() > 0)
			{
				Out.Add(Current.ToLower());
				Current.Empty();
			}
		}
		if (Current.Len() > 0)
		{
			Out.Add(Current.ToLower());
		}
		return Out;
	}

	double Jaccard(const TSet<FString>& A, const TSet<FString>& B)
	{
		if (A.Num() == 0 || B.Num() == 0)
		{
			return 0.0;
		}
		int32 Intersection = 0;
		for (const FString& Token : A)
		{
			if (B.Contains(Token))
			{
				++Intersection;
			}
		}
		const int32 Union = A.Num() + B.Num() - Intersection;
		return (Union > 0) ? (double)Intersection / (double)Union : 0.0;
	}

	/** A package path that is a class/script reference is not an indexable asset. */
	bool IsIndexableDependency(const FName& PackageName)
	{
		const FString Path = PackageName.ToString();
		return !Path.IsEmpty() && !Path.StartsWith(TEXT("/Script"), ESearchCase::IgnoreCase);
	}
}

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
		*FPaths::Combine(StagingDir, TEXT("*.yaml")), /*Files=*/true, /*Directories=*/false);

	for (const FString& File : Files)
	{
		FString Content;
		const FString FullPath = FPaths::Combine(StagingDir, File);
		if (!FFileHelper::LoadFileToString(Content, *FullPath))
		{
			continue;
		}

		// YAML 是缩进敏感的多文档流：单个文档缩进/语法错误会让整批灌库失败。
		// ParseStream 按列 0 的 "---" 切分后逐文档解析；损坏文档会被跳过，
		// 因此 -Incremental 发现哈希数异常偏少时应回退为全量重建。
		const TArray<TSharedPtr<FJsonObject>> Docs = UEYaml::ParseStream(Content);
		for (const TSharedPtr<FJsonObject>& Doc : Docs)
		{
			if (!Doc.IsValid())
			{
				continue;
			}
			FString Path;
			FString Hash;
			if (Doc->TryGetStringField(TEXT("asset_path"), Path) &&
				Doc->TryGetStringField(TEXT("content_hash"), Hash))
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

	// 3) 提取器注册表：通用兜底（Priority 0）+ 全资产特征规范化器（Priority 100）。
	//    Typed 提取器内部先复用 Universal 的 16 键结果，再追加 feature.* 键；
	//    单一 AssetClass 专属提取器可继续以 Priority 100 Register 注入。
	FUALExtractorRegistry Registry;
	Registry.Register(MakeShared<FUniversalReflectionExtractor>());
	Registry.Register(MakeShared<FUALTypedFeatureExtractor>());

	// 4) 增量：读取已有清单的 content_hash。
	TMap<FString, FString> ExistingHashes;
	if (bIncremental)
	{
		const FString StagingDir = FPaths::Combine(ProjectDir, TEXT(".ual"), TEXT("staging"));
		ExistingHashes = LoadExistingHashes(StagingDir);
		UE_LOG(LogUALIndexBuilder, Log, TEXT("Incremental: loaded %d existing content_hash(es)"),
			ExistingHashes.Num());
	}

	// 5) 打开 Sink（生产为 DuckDB C API Appender，这里为 YAML 文档流参考实现）。
	FUALYamlIndexSink Sink(OutName, ProjectDir);
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

	// 6b) 共现分组：同目录 + 名称 token Jaccard，纯确定性数学，不加载资源。
	//     每个资产最多产出 8 条 CO_OCCURS 边，避免大目录边数爆炸。
	const int32 MaxCoOccurrence = 8;
	TMap<FString, TArray<int32>> FolderGroups;
	TArray<TSet<FString>> AssetTokens;
	AssetTokens.SetNum(Assets.Num());
	for (int32 i = 0; i < Assets.Num(); ++i)
	{
		FolderGroups.FindOrAdd(FPaths::GetPath(Assets[i].PackageName.ToString())).Add(i);
		AssetTokens[i] = NameTokens(Assets[i].AssetName.ToString());
	}

	int32 Written = 0;
	int32 Skipped = 0;
	int32 EdgeCount = 0;
	int32 CoOccurCount = 0;

	for (int32 AssetIdx = 0; AssetIdx < Assets.Num(); ++AssetIdx)
	{
		const FAssetData& Asset = Assets[AssetIdx];
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
		//    /Script/... 是 UClass 引用而非资产包，在此过滤，避免污染引用图谱。
		TArray<FAssetIdentifier> Dependencies;
		AR.GetDependencies(FAssetIdentifier(Asset.PackageName), Dependencies,
			UE::AssetRegistry::EDependencyCategory::All);
		for (const FAssetIdentifier& Dep : Dependencies)
		{
			if (!IsIndexableDependency(Dep.PackageName))
			{
				continue;
			}
			FUALEdgeRecord Edge;
			Edge.SourcePath = Record.AssetPath;
			Edge.TargetPath = Dep.PackageName.ToString();
			Edge.EdgeType = EUALEdgeType::DEPENDS_ON;
			Edge.Weight = 1.0f;
			Sink.AppendEdge(Edge);
			++EdgeCount;
		}

		// 8) CO_OCCURS 边：同目录资产按名称 token 相似度装配。
		//    权重 = 0.5 + 0.5 * Jaccard，范围 [0.5, 1.0]，完全可复现。
		const FString Folder = FPaths::GetPath(Asset.PackageName.ToString());
		const TArray<int32>* Peers = FolderGroups.Find(Folder);
		if (Peers != nullptr && Peers->Num() > 1)
		{
			TArray<TPair<double, FString>> Scored;
			for (int32 PeerIdx : *Peers)
			{
				if (PeerIdx == AssetIdx)
				{
					continue;
				}
				Scored.Add(TPair<double, FString>(
					Jaccard(AssetTokens[AssetIdx], AssetTokens[PeerIdx]),
					Assets[PeerIdx].PackageName.ToString()));
			}
			Scored.Sort([](const TPair<double, FString>& A, const TPair<double, FString>& B)
			{
				return (A.Key > B.Key) || (A.Key == B.Key && A.Value < B.Value);
			});
			for (int32 k = 0; k < Scored.Num() && k < MaxCoOccurrence; ++k)
			{
				FUALEdgeRecord Edge;
				Edge.SourcePath = Record.AssetPath;
				Edge.TargetPath = Scored[k].Value;
				Edge.EdgeType = EUALEdgeType::CO_OCCURS;
				Edge.Weight = 0.5f + 0.5f * static_cast<float>(Scored[k].Key);
				Sink.AppendEdge(Edge);
				++EdgeCount;
				++CoOccurCount;
			}
		}
	}

	const bool bCommitted = Sink.Commit();
	UE_LOG(LogUALIndexBuilder, Log,
		TEXT("=== UALIndexBuilder done: total=%d written=%d skipped=%d edges=%d cooccurs=%d commit=%d ==="),
		Assets.Num(), Written, Skipped, EdgeCount, CoOccurCount, bCommitted ? 1 : 0);

	return bCommitted ? 0 : 1;
}
