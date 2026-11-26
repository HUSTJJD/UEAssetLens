#include "UEAssetLensEditorModule.h"

#include "FUniversalReflectionExtractor.h"
#include "UALDomainFeatureExtractor.h"
#include "UALTypedFeatureExtractor.h"
#include "UALTypes.h"
#include "UEYaml.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "Async/Async.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/ScopeLock.h"

DEFINE_LOG_CATEGORY_STATIC(LogUALEditor, Log, All);

namespace UALEditorShared
{
	static bool LexLess(const FString& A, const FString& B)
	{
		return A.Compare(B, ESearchCase::CaseSensitive) < 0;
	}

	static const TCHAR* ExtractorBindingKey()
	{
		return TEXT("asset_type.editor_extractors");
	}

	/** 把 Editor-only 的提取器注册表挂到 Runtime 单例上，保持 Runtime 侧类型无关。 */
	class FExtractorRegistryBinding : public IUALExtensionBinding
	{
	public:
		static FString Kind() { return FString(TEXT("editor_extractor_registry")); }
		virtual FString GetBindingKind() const override { return Kind(); }

		TSharedPtr<FUALExtractorRegistry> Extractors = MakeShared<FUALExtractorRegistry>();
		bool bBuiltinExtractorsRegistered = false;
	};

	// 单个写入锁 + 已写入的 generation：并发的线程池任务中，旧快照会被跳过，
	// 保证 events.yaml / removed.yaml 这一对文件最终反映最新状态。
	static FCriticalSection GWriteLock;
	static int32 GLastWrittenGeneration = 0;

	static double SafeNumber(float Value)
	{
		return (FMath::Abs(Value) <= TNumericLimits<float>::Max()) ? (double)Value : 0.0;
	}

	/**
	 * 与 UALYamlIndexSink 完全相同的 16 个顶层键（含空的 edges 数组）。
	 * 不添加 event / sequence 等第 17 个键；文本一律经 UEYaml emitter 生成。
	 */
	static FString BuildSixteenKeyDoc(const FUALAssetRecord& Record)
	{
		TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
		Obj->SetStringField(TEXT("asset_path"), Record.AssetPath);
		Obj->SetStringField(TEXT("logical_id"), Record.LogicalId);
		Obj->SetStringField(TEXT("asset_class"), Record.AssetClass);
		Obj->SetStringField(TEXT("parent_class"), Record.ParentClass);

		TArray<TSharedPtr<FJsonValue>> Taxonomy;
		for (const FString& Term : Record.Taxonomy)
		{
			Taxonomy.Add(MakeShared<FJsonValueString>(Term));
		}
		Obj->SetArrayField(TEXT("taxonomy"), Taxonomy);
		Obj->SetStringField(TEXT("tokens"), FString::Join(Record.Tokens, TEXT(" ")));
		Obj->SetStringField(TEXT("content_hash"), Record.ContentHash);
		Obj->SetNumberField(TEXT("duration"), SafeNumber(Record.Duration));

		TArray<TSharedPtr<FJsonValue>> Bounds;
		for (int32 Index = 0; Index < 3; ++Index)
		{
			const float Value = Record.BoundsSize.IsValidIndex(Index) ? Record.BoundsSize[Index] : 0.0f;
			Bounds.Add(MakeShared<FJsonValueNumber>(SafeNumber(Value)));
		}
		Obj->SetArrayField(TEXT("bounds_size"), Bounds);

		Obj->SetStringField(TEXT("skeleton_path"), Record.SkeletonPath);
		Obj->SetStringField(TEXT("dominant_limb"), Record.DominantLimb);

		TArray<TSharedPtr<FJsonValue>> Fingerprint;
		for (int32 Index = 0; Index < 16; ++Index)
		{
			const float Value = Record.FingerprintVec.IsValidIndex(Index) ? Record.FingerprintVec[Index] : 0.0f;
			Fingerprint.Add(MakeShared<FJsonValueNumber>(SafeNumber(Value)));
		}
		Obj->SetArrayField(TEXT("fingerprint_vec"), Fingerprint);

		TSharedPtr<FJsonObject> Interface = MakeShared<FJsonObject>();
		TArray<FString> InterfaceKeys;
		Record.InterfaceSpec.GetKeys(InterfaceKeys);
		InterfaceKeys.Sort(&LexLess);
		for (const FString& Key : InterfaceKeys)
		{
			const FString* Value = Record.InterfaceSpec.Find(Key);
			if (Value != nullptr)
			{
				Interface->SetStringField(Key, *Value);
			}
		}
		Obj->SetObjectField(TEXT("interface_spec"), Interface);

		TSharedPtr<FJsonObject> Domain = MakeShared<FJsonObject>();
		TArray<FString> DomainKeys;
		Record.DomainSpec.GetKeys(DomainKeys);
		DomainKeys.Sort(&LexLess);
		for (const FString& Key : DomainKeys)
		{
			const FString* Value = Record.DomainSpec.Find(Key);
			if (Value != nullptr)
			{
				Domain->SetStringField(Key, *Value);
			}
		}
		Obj->SetObjectField(TEXT("domain_spec"), Domain);

		Obj->SetStringField(TEXT("yaml_cache"), Record.YamlCache);
		Obj->SetArrayField(TEXT("edges"), TArray<TSharedPtr<FJsonValue>>());

		return UEYaml::EmitDocument(Obj);
	}

	/** 删除流：每个文档只有 asset_path / change_kind 两个键。 */
	static FString BuildRemovedDoc(const FString& AssetPath)
	{
		TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
		Obj->SetStringField(TEXT("asset_path"), AssetPath);
		Obj->SetStringField(TEXT("change_kind"), TEXT("removed"));
		return UEYaml::EmitDocument(Obj);
	}

	static bool AtomicWrite(const FString& FinalPath, const FString& TempPath, const FString& Text)
	{
		IFileManager& FileManager = IFileManager::Get();
		FileManager.MakeDirectory(*FPaths::GetPath(FinalPath), true);
		if (!FFileHelper::SaveStringToFile(Text, *TempPath,
			FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))
		{
			return false;
		}
		if (!FileManager.Move(*FinalPath, *TempPath, true, true))
		{
			FileManager.Delete(*TempPath);
			return false;
		}
		return true;
	}

	static void WriteDeltaFiles(const TArray<FUALEditorDeltaEntry>& Snapshot, int32 Generation,
		const TSharedPtr<FUALExtractorRegistry>& Registry)
	{
		FScopeLock Lock(&GWriteLock);
		if (Generation < GLastWrittenGeneration)
		{
			return; // 旧快照，跳过，避免覆盖较新的结果
		}

		const FString StagingDir = FPaths::Combine(FPaths::ProjectDir(), TEXT(".ual"), TEXT("staging"));
		TArray<FString> EventDocs;
		TArray<FString> RemovedDocs;

		// Snapshot 已按 AssetPath 排序；重复事件在回调侧按路径合并。
		for (const FUALEditorDeltaEntry& Entry : Snapshot)
		{
			if (Entry.Kind == TEXT("removed"))
			{
				RemovedDocs.Add(BuildRemovedDoc(Entry.AssetPath));
				continue;
			}
			FUALAssetRecord Record;
			if (Registry.IsValid() && Registry->ExtractRecord(Entry.AssetData, Record))
			{
				EventDocs.Add(BuildSixteenKeyDoc(Record));
			}
		}

		const FString EventsText = UEYaml::EmitStream(EventDocs);
		const FString RemovedText = UEYaml::EmitStream(RemovedDocs);
		const FString Suffix = FString::FromInt(Generation);

		const FString EventsFinal = FPaths::Combine(StagingDir, TEXT("events.yaml"));
		const FString RemovedFinal = FPaths::Combine(StagingDir, TEXT("removed.yaml"));
		const FString EventsTemp = FPaths::Combine(StagingDir,
			TEXT("events.yaml.") + Suffix + TEXT(".tmp"));
		const FString RemovedTemp = FPaths::Combine(StagingDir,
			TEXT("removed.yaml.") + Suffix + TEXT(".tmp"));

		const bool bEventsOk = AtomicWrite(EventsFinal, EventsTemp, EventsText);
		const bool bRemovedOk = AtomicWrite(RemovedFinal, RemovedTemp, RemovedText);
		if (bEventsOk && bRemovedOk)
		{
			GLastWrittenGeneration = Generation;
		}
		UE_LOG(LogUALEditor, Log,
			TEXT("[UAL delta] generation=%d events=%d removed=%d writes(events=%d removed=%d)"),
			Generation, EventDocs.Num(), RemovedDocs.Num(), bEventsOk ? 1 : 0, bRemovedOk ? 1 : 0);
	}
}

TSharedPtr<FUALExtractorRegistry> FUEAssetLensEditorModule::GetSharedExtractorRegistry()
{
	FUALExtensionRegistry& Shared = FUALExtensionRegistry::Get();
	const FString Key = FString(UALEditorShared::ExtractorBindingKey());

	TSharedPtr<IUALExtensionBinding> Existing = Shared.FindBinding(Key);
	TSharedPtr<UALEditorShared::FExtractorRegistryBinding> Typed;
	if (Existing.IsValid() &&
		Existing->GetBindingKind() == UALEditorShared::FExtractorRegistryBinding::Kind())
	{
		Typed = StaticCastSharedPtr<UALEditorShared::FExtractorRegistryBinding>(Existing);
	}

	if (!Typed.IsValid())
	{
		FUALExtensionSpec Spec;
		Spec.Namespace = TEXT("asset_type");
		Spec.Name = TEXT("editor_extractors");
		Spec.Version = TEXT("1");
		Spec.Priority = 0;
		Spec.Capabilities.Add(TEXT("extract_record"));
		Spec.SchemaText = TEXT("ual_extractor_registry");
		Typed = MakeShared<UALEditorShared::FExtractorRegistryBinding>();
		const TSharedPtr<IUALExtensionBinding> BaseBinding = Typed;
		Shared.Register(Spec, BaseBinding); // 若键已存在则拒绝，保持确定性
	}

	if (!Typed.IsValid())
	{
		return TSharedPtr<FUALExtractorRegistry>();
	}

	// 通用兜底（0）+ 全资产特征规范化器（100）+ 声明式领域提取器（110）
	// 由共享 binding 的标记保证只注册一次；Commandlet 与 Editor 复用同一实例。
	if (!Typed->bBuiltinExtractorsRegistered)
	{
		Typed->Extractors->Register(MakeShared<FUniversalReflectionExtractor>());
		Typed->Extractors->Register(MakeShared<FUALTypedFeatureExtractor>());
		Typed->Extractors->Register(MakeShared<FUALDomainFeatureExtractor>());
		Typed->bBuiltinExtractorsRegistered = true;
	}
	return Typed->Extractors;
}

void FUEAssetLensEditorModule::StartupModule()
{
	UE_LOG(LogUALEditor, Log, TEXT("UEAssetLensEditor module startup."));

	EditorRegistry = GetSharedExtractorRegistry();
	UE_LOG(LogUALEditor, Log,
		TEXT("Shared extension registry: %d spec(s); %d extractor(s) registered (fallback + typed + domain features)."),
		FUALExtensionRegistry::Get().Num(), EditorRegistry.IsValid() ? EditorRegistry->Num() : 0);

	RegisterAssetRegistryCallbacks();
}

void FUEAssetLensEditorModule::ShutdownModule()
{
	UnregisterAssetRegistryCallbacks();
	EditorRegistry.Reset();
	UE_LOG(LogUALEditor, Log,
		TEXT("UEAssetLensEditor module shutdown. Delta marks: +%d / -%d / ~%d; scheduled writes=%d; next sequence=%d"),
		AddedCount, RemovedCount, UpdatedCount, ScheduledDeltaWriteCount, NextDeltaSequence);
}

void FUEAssetLensEditorModule::QueueDelta(const FAssetData& AssetData, const TCHAR* Kind)
{
	// 只取包路径与元数据；不触发任何资源加载，不访问 AssetRegistry 之外的资源。
	const FString PackagePath = AssetData.PackageName.ToString();
	if (PackagePath.IsEmpty())
	{
		return;
	}

	{
		FScopeLock Lock(&PendingDeltasLock);

		FUALEditorDeltaEntry& Entry = PendingDeltas.FindOrAdd(PackagePath);
		if (Entry.Sequence == 0)
		{
			Entry.Sequence = NextDeltaSequence++;
		}
		Entry.AssetPath = PackagePath;
		Entry.AssetData = AssetData;
		Entry.Kind = FString(Kind); // 同一路径的重复事件只保留最后一种

		if (Entry.Kind == TEXT("added")) { ++AddedCount; }
		else if (Entry.Kind == TEXT("removed")) { ++RemovedCount; }
		else { ++UpdatedCount; }
	}

	FlushDeltas();
}

void FUEAssetLensEditorModule::RequestIncrementalRescan()
{
	// 不伪造事件、不重新扫描 AssetRegistry：只把已合并的确定性快照再写一次。
	FlushDeltas();
}

void FUEAssetLensEditorModule::FlushDeltas()
{
	TArray<FUALEditorDeltaEntry> Snapshot;
	int32 Generation = 0;
	TSharedPtr<FUALExtractorRegistry> RegistryCopy;
	{
		FScopeLock Lock(&PendingDeltasLock);
		if (PendingDeltas.Num() == 0)
		{
			return;
		}

		TArray<FString> Paths;
		PendingDeltas.GetKeys(Paths);
		Paths.Sort(&UALEditorShared::LexLess);
		Snapshot.Reserve(Paths.Num());
		for (const FString& Path : Paths)
		{
			const FUALEditorDeltaEntry* Found = PendingDeltas.Find(Path);
			if (Found != nullptr)
			{
				Snapshot.Add(*Found);
			}
		}

		Generation = NextDeltaSequence - 1;
		RegistryCopy = EditorRegistry;
		++ScheduledDeltaWriteCount;
	}

	// 投影与文件 I/O 都在线程池；lambda 只捕获值拷贝，模块关闭后仍安全。
	Async(EAsyncExecution::ThreadPool,
		[Snapshot, Generation, RegistryCopy]()
		{
			UALEditorShared::WriteDeltaFiles(Snapshot, Generation, RegistryCopy);
		});
}

void FUEAssetLensEditorModule::RegisterAssetRegistryCallbacks()
{
	FAssetRegistryModule& ARM =
		FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
	IAssetRegistry& AR = ARM.Get();

	// UE5/UE6 三个委托的签名均为 (const FAssetData&)。回调只读元数据并投递
	// 线程池任务：不加载资源、不做文件 I/O、不触发全量扫描。
	OnAssetAddedHandle = AR.OnAssetAdded().AddLambda([this](const FAssetData& AssetData)
	{
		QueueDelta(AssetData, TEXT("added"));
	});
	OnAssetRemovedHandle = AR.OnAssetRemoved().AddLambda([this](const FAssetData& AssetData)
	{
		QueueDelta(AssetData, TEXT("removed"));
	});
	OnAssetUpdatedHandle = AR.OnAssetUpdated().AddLambda([this](const FAssetData& AssetData)
	{
		QueueDelta(AssetData, TEXT("updated"));
	});

	UE_LOG(LogUALEditor, Log,
		TEXT("AssetRegistry delta delegates attached (16-key events.yaml + removed.yaml, zero asset loading)."));
}

void FUEAssetLensEditorModule::UnregisterAssetRegistryCallbacks()
{
	if (!FModuleManager::Get().IsModuleLoaded(TEXT("AssetRegistry")))
	{
		return;
	}
	FAssetRegistryModule& ARM =
		FModuleManager::GetModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry"));
	IAssetRegistry& AR = ARM.Get();

	AR.OnAssetAdded().Remove(OnAssetAddedHandle);
	AR.OnAssetRemoved().Remove(OnAssetRemovedHandle);
	AR.OnAssetUpdated().Remove(OnAssetUpdatedHandle);
}

IMPLEMENT_MODULE(FUEAssetLensEditorModule, UEAssetLensEditor)
