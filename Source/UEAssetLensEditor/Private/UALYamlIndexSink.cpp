#include "UALYamlIndexSink.h"

#include "UALTypes.h"
#include "UEYaml.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Misc/Paths.h"
#include "Misc/FileHelper.h"
#include "HAL/FileManager.h"

DEFINE_LOG_CATEGORY_STATIC(LogUALYamlSink, Log, All);

namespace
{
	// 把非有限浮点转成 0，保证 YAML 里绝不出现 NaN / Infinity。
	static double SafeNumber(float V)
	{
		return (FMath::Abs(V) <= TNumericLimits<float>::Max()) ? (double)V : 0.0;
	}
}

FUALYamlIndexSink::FUALYamlIndexSink(const FString& InName, const FString& InProjectDir)
	: Name(InName)
	, ProjectDir(InProjectDir)
{
	if (ProjectDir.IsEmpty())
	{
		ProjectDir = FPaths::ProjectDir();
	}
}

FUALYamlIndexSink::~FUALYamlIndexSink() = default;

bool FUALYamlIndexSink::Begin()
{
	Records.Reset();
	Edges.Reset();

	const FString Dir = FPaths::Combine(ProjectDir, TEXT(".ual"), TEXT("staging"));
	IFileManager::Get().MakeDirectory(*Dir, true);

	// 文件名消毒，避免路径分隔符 / 空格破坏输出路径。
	FString SafeName = Name;
	SafeName.ReplaceInline(TEXT("/"), TEXT("_"));
	SafeName.ReplaceInline(TEXT("\\"), TEXT("_"));
	SafeName.ReplaceInline(TEXT(" "), TEXT("_"));
	if (SafeName.IsEmpty())
	{
		SafeName = TEXT("index");
	}
	OutputPath = FPaths::Combine(Dir, SafeName + TEXT(".yaml"));

	bBegun = true;
	UE_LOG(LogUALYamlSink, Log, TEXT("Begin YAML sink -> %s"), *OutputPath);
	return true;
}

void FUALYamlIndexSink::AppendRecord(const FUALAssetRecord& Record)
{
	if (!bBegun)
	{
		return;
	}
	Records.Add(Record);
}

void FUALYamlIndexSink::AppendEdge(const FUALEdgeRecord& Edge)
{
	if (!bBegun)
	{
		return;
	}
	Edges.Add(Edge);
}

FString FUALYamlIndexSink::BuildRecordYaml(const FUALAssetRecord& Record,
	const TArray<const FUALEdgeRecord*>& RowEdges) const
{
	// FJsonObject 仍是内存 DOM；UEYaml::EmitDocument 负责 YAML 文本落盘。
	TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();

	Obj->SetStringField(TEXT("asset_path"), Record.AssetPath);
	Obj->SetStringField(TEXT("logical_id"), Record.LogicalId);
	Obj->SetStringField(TEXT("asset_class"), Record.AssetClass);
	Obj->SetStringField(TEXT("parent_class"), Record.ParentClass);

	// taxonomy: array of strings.
	TArray<TSharedPtr<FJsonValue>> TaxArr;
	for (const FString& T : Record.Taxonomy)
	{
		TaxArr.Add(MakeShared<FJsonValueString>(T));
	}
	Obj->SetArrayField(TEXT("taxonomy"), TaxArr);

	// tokens: canonical VARCHAR text for the DuckDB FTS index. The C++ record
	// stores terms as an array; join them here so the YAML contract matches
	// Server/ual_common/db.py (which also tolerates the legacy array form).
	Obj->SetStringField(TEXT("tokens"), FString::Join(Record.Tokens, TEXT(" ")));

	Obj->SetStringField(TEXT("content_hash"), Record.ContentHash);
	Obj->SetNumberField(TEXT("duration"), SafeNumber(Record.Duration));

	// bounds_size：恰好 3 元数组（不足补 0）
	TArray<TSharedPtr<FJsonValue>> Bounds;
	for (int32 i = 0; i < 3; ++i)
	{
		const float V = Record.BoundsSize.IsValidIndex(i) ? Record.BoundsSize[i] : 0.0f;
		Bounds.Add(MakeShared<FJsonValueNumber>(SafeNumber(V)));
	}
	Obj->SetArrayField(TEXT("bounds_size"), Bounds);

	Obj->SetStringField(TEXT("skeleton_path"), Record.SkeletonPath);
	Obj->SetStringField(TEXT("dominant_limb"), Record.DominantLimb);

	// fingerprint_vec：恰好 16 元数组（不足补 0）
	TArray<TSharedPtr<FJsonValue>> Fp;
	for (int32 i = 0; i < 16; ++i)
	{
		const float V = Record.FingerprintVec.IsValidIndex(i) ? Record.FingerprintVec[i] : 0.0f;
		Fp.Add(MakeShared<FJsonValueNumber>(SafeNumber(V)));
	}
	Obj->SetArrayField(TEXT("fingerprint_vec"), Fp);

	// interface_spec / domain_spec：字符串 -> 字符串对象（MAP(VARCHAR,VARCHAR)）。
	TSharedPtr<FJsonObject> Iface = MakeShared<FJsonObject>();
	for (const TPair<FString, FString>& P : Record.InterfaceSpec)
	{
		Iface->SetStringField(P.Key, P.Value);
	}
	Obj->SetObjectField(TEXT("interface_spec"), Iface);

	TSharedPtr<FJsonObject> Domain = MakeShared<FJsonObject>();
	for (const TPair<FString, FString>& P : Record.DomainSpec)
	{
		Domain->SetStringField(P.Key, P.Value);
	}
	Obj->SetObjectField(TEXT("domain_spec"), Domain);

	Obj->SetStringField(TEXT("yaml_cache"), Record.YamlCache);

	// edges：数组，元素含 target_path / edge_type / weight
	TArray<TSharedPtr<FJsonValue>> EdgeArr;
	for (const FUALEdgeRecord* E : RowEdges)
	{
		if (!E)
		{
			continue;
		}
		TSharedPtr<FJsonObject> EObj = MakeShared<FJsonObject>();
		EObj->SetStringField(TEXT("target_path"), E->TargetPath);
		EObj->SetStringField(TEXT("edge_type"), UAL::MakeEdgeTypeString(E->EdgeType));
		EObj->SetNumberField(TEXT("weight"), SafeNumber(E->Weight));
		EdgeArr.Add(MakeShared<FJsonValueObject>(EObj));
	}
	Obj->SetArrayField(TEXT("edges"), EdgeArr);

	// 自写缩进 emitter：所有字符串显式双引号，避免中文/冒号/引号/换行破坏语义。
	return UEYaml::EmitDocument(Obj);
}

bool FUALYamlIndexSink::Commit()
{
	if (!bBegun)
	{
		return false;
	}

	// 按 source_path 聚合边。
	TMap<FString, TArray<const FUALEdgeRecord*>> EdgesBySource;
	for (const FUALEdgeRecord& E : Edges)
	{
		EdgesBySource.FindOrAdd(E.SourcePath).Add(&E);
	}

	// 记录已有的 asset_path 集合，用于提示孤儿边。
	TSet<FString> RecordPaths;
	for (const FUALAssetRecord& R : Records)
	{
		RecordPaths.Add(R.AssetPath);
	}

	static const TArray<const FUALEdgeRecord*> EmptyEdges;
	TArray<FString> Docs;
	for (const FUALAssetRecord& R : Records)
	{
		const TArray<const FUALEdgeRecord*>* Found = EdgesBySource.Find(R.AssetPath);
		const TArray<const FUALEdgeRecord*>& RowEdges = Found ? *Found : EmptyEdges;
		Docs.Add(BuildRecordYaml(R, RowEdges));
	}

	for (const TPair<FString, TArray<const FUALEdgeRecord*>>& Pair : EdgesBySource)
	{
		if (!RecordPaths.Contains(Pair.Key))
		{
			UE_LOG(LogUALYamlSink, Verbose,
				TEXT("%d edge(s) for un-indexed source %s were skipped"),
				Pair.Value.Num(), *Pair.Key);
		}
	}

	// 多文档流：每个文档由 "---" 起始，替代旧的一行一个对象的交换格式。
	const FString FileContent = UEYaml::EmitStream(Docs);

	const bool bOk = FFileHelper::SaveStringToFile(FileContent, *OutputPath,
		FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);

	UE_LOG(LogUALYamlSink, Log,
		TEXT("Commit YAML sink: %d records / %d edges / %d docs -> %s (ok=%d)"),
		Records.Num(), Edges.Num(), Docs.Num(), *OutputPath, bOk ? 1 : 0);

	Records.Reset();
	Edges.Reset();
	bBegun = false;
	return bOk;
}
