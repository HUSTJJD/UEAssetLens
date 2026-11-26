#include "UALDuckDbIndexSink.h"

#include "UALYamlIndexSink.h"
#include "UALTypes.h"

#include "Containers/StringConv.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "HAL/FileManager.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

// 默认构建（UAL_WITH_DUCKDB=0）不包含此头文件；宏开启时由宿主工程的
// ThirdParty DuckDB C API 提供，Build.cs 不链接 DuckDB。
#if UAL_WITH_DUCKDB
#include <duckdb.h>
#endif

DEFINE_LOG_CATEGORY_STATIC(LogUALDuckDbSink, Log, All);

namespace
{
#if UAL_WITH_DUCKDB
	/** 大小写敏感的确定性字典序（UE 合成版 operator< 是大小写不敏感的）。 */
	bool LexLess(const FString& A, const FString& B)
	{
		return A.Compare(B, ESearchCase::CaseSensitive) < 0;
	}

	FString FloatText(float Value)
	{
		return FMath::IsFinite(Value) ? FString::SanitizeFloat((double)Value) : FString(TEXT("0.0"));
	}

	/** 复杂字段用引擎官方 Json 序列化器转文本，不手写 YAML / TOON 字符串。 */
	FString StringArrayText(const TArray<FString>& Values)
	{
		TArray<TSharedPtr<FJsonValue>> Array;
		Array.Reserve(Values.Num());
		for (const FString& Value : Values)
		{
			Array.Add(MakeShared<FJsonValueString>(Value));
		}
		FString Out;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
		FJsonSerializer::Serialize(Array, Writer);
		return Out.IsEmpty() ? FString(TEXT("[]")) : Out;
	}

	FString FloatArrayText(const TArray<float>& Values, int32 FixedLength)
	{
		TArray<TSharedPtr<FJsonValue>> Array;
		Array.Reserve(FixedLength);
		for (int32 Index = 0; Index < FixedLength; ++Index)
		{
			const float Value = Values.IsValidIndex(Index) ? Values[Index] : 0.0f;
			Array.Add(MakeShared<FJsonValueNumber>(FMath::IsFinite(Value) ? (double)Value : 0.0));
		}
		FString Out;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
		FJsonSerializer::Serialize(Array, Writer);
		return Out.IsEmpty() ? FString(TEXT("[]")) : Out;
	}

	FString StringMapText(const TMap<FString, FString>& Map)
	{
		TSharedRef<FJsonObject> Obj = MakeShared<FJsonObject>();
		TArray<FString> Keys;
		Map.GetKeys(Keys);
		Keys.Sort(&LexLess);
		for (const FString& Key : Keys)
		{
			const FString* Value = Map.Find(Key);
			if (Value != nullptr)
			{
				Obj->SetStringField(Key, *Value);
			}
		}
		FString Out;
		const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&Out);
		FJsonSerializer::Serialize(Obj, Writer);
		return Out.IsEmpty() ? FString(TEXT("{}")) : Out;
	}

	bool Exec(duckdb_connection Connection, const char* Sql)
	{
		duckdb_result Result = {};
		const duckdb_state State = duckdb_query(Connection, Sql, &Result);
		const bool bOk = (State == DuckDBSuccess);
		if (!bOk)
		{
			const char* Error = duckdb_result_error(&Result);
			UE_LOG(LogUALDuckDbSink, Error, TEXT("DuckDB statement failed: %s"),
				(Error != nullptr) ? UTF8_TO_TCHAR(Error) : TEXT("unknown"));
		}
		duckdb_destroy_result(&Result);
		return bOk;
	}

	/** 全参数绑定，绝不拼接 SQL 值。 */
	bool BindAndExec(duckdb_connection Connection, const char* Sql, const TArray<FString>& Values)
	{
		duckdb_prepared_statement Statement = nullptr;
		if (duckdb_prepare(Connection, Sql, &Statement) != DuckDBSuccess)
		{
			if (Statement != nullptr)
			{
				duckdb_destroy_prepared(&Statement);
			}
			return false;
		}

		bool bOk = true;
		for (int32 Index = 0; Index < Values.Num(); ++Index)
		{
			const FTCHARToUTF8 Utf8(*Values[Index]);
			if (duckdb_bind_varchar(Statement, static_cast<idx_t>(Index + 1), Utf8.Get()) != DuckDBSuccess)
			{
				bOk = false;
				break;
			}
		}
		if (bOk)
		{
			duckdb_result Result = {};
			bOk = (duckdb_execute_prepared(Statement, &Result) == DuckDBSuccess);
			duckdb_destroy_result(&Result);
		}
		duckdb_destroy_prepared(&Statement);
		return bOk;
	}
#endif
}

FUALDuckDbIndexSink::FUALDuckDbIndexSink(const FString& InName, const FString& InProjectDir)
	: Name(InName)
	, ProjectDir(InProjectDir)
{
	if (ProjectDir.IsEmpty())
	{
		ProjectDir = FPaths::ProjectDir();
	}
	DatabasePath = FPaths::Combine(ProjectDir, TEXT(".ual"), TEXT("index.duckdb"));
}

FUALDuckDbIndexSink::~FUALDuckDbIndexSink()
{
	CloseDuckDb();
}

bool FUALDuckDbIndexSink::Begin()
{
	Records.Reset();
	Edges.Reset();
	YamlFallback.Reset();

#if UAL_WITH_DUCKDB
	bBegun = BeginDuckDb();
#else
	// 默认路径：完整 16 键 YAML 文档流，输出与 FUALYamlIndexSink 完全一致。
	YamlFallback = MakeUnique<FUALYamlIndexSink>(Name, ProjectDir);
	bBegun = YamlFallback->Begin();
#endif
	UE_LOG(LogUALDuckDbSink, Log, TEXT("Begin index sink (DuckDB C API=%d, ok=%d)"),
		(int32)(UAL_WITH_DUCKDB), bBegun ? 1 : 0);
	return bBegun;
}

void FUALDuckDbIndexSink::AppendRecord(const FUALAssetRecord& Record)
{
	if (!bBegun)
	{
		return;
	}
#if UAL_WITH_DUCKDB
	Records.Add(Record);
#else
	if (YamlFallback.IsValid())
	{
		YamlFallback->AppendRecord(Record);
	}
#endif
}

void FUALDuckDbIndexSink::AppendEdge(const FUALEdgeRecord& Edge)
{
	if (!bBegun)
	{
		return;
	}
#if UAL_WITH_DUCKDB
	Edges.Add(Edge);
#else
	if (YamlFallback.IsValid())
	{
		YamlFallback->AppendEdge(Edge);
	}
#endif
}

bool FUALDuckDbIndexSink::Commit()
{
	if (!bBegun)
	{
		return false;
	}
#if UAL_WITH_DUCKDB
	const bool bOk = CommitDuckDb();
#else
	const bool bOk = YamlFallback.IsValid() && YamlFallback->Commit();
#endif
	Records.Reset();
	Edges.Reset();
	bBegun = false;
	YamlFallback.Reset();
	return bOk;
}

bool FUALDuckDbIndexSink::BeginDuckDb()
{
#if UAL_WITH_DUCKDB
	IFileManager::Get().MakeDirectory(*FPaths::GetPath(DatabasePath), true);
	const FTCHARToUTF8 PathUtf8(*DatabasePath);

	duckdb_database Db = nullptr;
	if (duckdb_open(PathUtf8.Get(), &Db) != DuckDBSuccess)
	{
		UE_LOG(LogUALDuckDbSink, Error, TEXT("duckdb_open failed."));
		return false;
	}
	Database = reinterpret_cast<void*>(Db);

	duckdb_connection Conn = nullptr;
	if (duckdb_connect(Db, &Conn) != DuckDBSuccess)
	{
		CloseDuckDb();
		return false;
	}
	Connection = reinterpret_cast<void*>(Conn);

	// 中间表只承载 16 键 Core Record 的确定性文本；assets / asset_edges 的
	// 类型化 upsert 仍由 Python 侧 ual-cli 完成。DELETE 保证全量重建语义。
	static const char* Statements[] =
	{
		"CREATE TABLE IF NOT EXISTS ual_cpp_asset_staging ("
		"asset_path VARCHAR PRIMARY KEY, logical_id VARCHAR, asset_class VARCHAR, parent_class VARCHAR, "
		"taxonomy VARCHAR, tokens VARCHAR, content_hash VARCHAR, duration VARCHAR, bounds_size VARCHAR, "
		"skeleton_path VARCHAR, dominant_limb VARCHAR, fingerprint_vec VARCHAR, interface_spec VARCHAR, "
		"domain_spec VARCHAR, yaml_cache VARCHAR)",
		"CREATE TABLE IF NOT EXISTS ual_cpp_edge_staging ("
		"source_path VARCHAR, target_path VARCHAR, edge_type VARCHAR, weight VARCHAR, "
		"PRIMARY KEY (source_path, target_path, edge_type))",
		"DELETE FROM ual_cpp_asset_staging",
		"DELETE FROM ual_cpp_edge_staging"
	};
	for (const char* Sql : Statements)
	{
		if (!Exec(Conn, Sql))
		{
			CloseDuckDb();
			return false;
		}
	}
	return true;
#else
	return false;
#endif
}

bool FUALDuckDbIndexSink::CommitDuckDb()
{
#if UAL_WITH_DUCKDB
	if (Connection == nullptr)
	{
		return false;
	}
	duckdb_connection Conn = reinterpret_cast<duckdb_connection>(Connection);

	Records.Sort([](const FUALAssetRecord& A, const FUALAssetRecord& B)
	{
		return LexLess(A.AssetPath, B.AssetPath);
	});
	Edges.Sort([](const FUALEdgeRecord& A, const FUALEdgeRecord& B)
	{
		if (A.SourcePath != B.SourcePath) { return LexLess(A.SourcePath, B.SourcePath); }
		if (A.TargetPath != B.TargetPath) { return LexLess(A.TargetPath, B.TargetPath); }
		return LexLess(UAL::MakeEdgeTypeString(A.EdgeType), UAL::MakeEdgeTypeString(B.EdgeType));
	});

	static const char* InsertAsset =
		"INSERT INTO ual_cpp_asset_staging "
		"(asset_path, logical_id, asset_class, parent_class, taxonomy, tokens, content_hash, duration, "
		"bounds_size, skeleton_path, dominant_limb, fingerprint_vec, interface_spec, domain_spec, yaml_cache) "
		"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)";
	static const char* InsertEdge =
		"INSERT INTO ual_cpp_edge_staging (source_path, target_path, edge_type, weight) VALUES (?, ?, ?, ?)";

	bool bOk = true;
	for (const FUALAssetRecord& Record : Records)
	{
		TArray<FString> Values;
		Values.Reserve(15);
		Values.Add(Record.AssetPath);
		Values.Add(Record.LogicalId);
		Values.Add(Record.AssetClass);
		Values.Add(Record.ParentClass);
		Values.Add(StringArrayText(Record.Taxonomy));
		Values.Add(FString::Join(Record.Tokens, TEXT(" ")));
		Values.Add(Record.ContentHash);
		Values.Add(FloatText(Record.Duration));
		Values.Add(FloatArrayText(Record.BoundsSize, 3));
		Values.Add(Record.SkeletonPath);
		Values.Add(Record.DominantLimb);
		Values.Add(FloatArrayText(Record.FingerprintVec, 16));
		Values.Add(StringMapText(Record.InterfaceSpec));
		Values.Add(StringMapText(Record.DomainSpec));
		Values.Add(Record.YamlCache);
		if (!BindAndExec(Conn, InsertAsset, Values))
		{
			bOk = false;
			break;
		}
	}

	if (bOk)
	{
		for (const FUALEdgeRecord& Edge : Edges)
		{
			TArray<FString> Values;
			Values.Reserve(4);
			Values.Add(Edge.SourcePath);
			Values.Add(Edge.TargetPath);
			Values.Add(UAL::MakeEdgeTypeString(Edge.EdgeType));
			Values.Add(FloatText(Edge.Weight));
			if (!BindAndExec(Conn, InsertEdge, Values))
			{
				bOk = false;
				break;
			}
		}
	}

	UE_LOG(LogUALDuckDbSink, Log, TEXT("DuckDB staging commit: %d assets / %d edges / ok=%d"),
		Records.Num(), Edges.Num(), bOk ? 1 : 0);
	CloseDuckDb();
	return bOk;
#else
	return false;
#endif
}

void FUALDuckDbIndexSink::CloseDuckDb()
{
#if UAL_WITH_DUCKDB
	if (Connection != nullptr)
	{
		duckdb_connection Conn = reinterpret_cast<duckdb_connection>(Connection);
		duckdb_disconnect(&Conn);
		Connection = nullptr;
	}
	if (Database != nullptr)
	{
		duckdb_database Db = reinterpret_cast<duckdb_database>(Database);
		duckdb_close(&Db);
		Database = nullptr;
	}
#endif
}
