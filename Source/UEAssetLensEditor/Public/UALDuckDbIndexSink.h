#pragma once

#include "CoreMinimal.h"
#include "UALIndexSink.h"
#include "UALTypes.h"

// ---------------------------------------------------------------------------
// 可选生产 Sink（IUALIndexSink 的 DuckDB 写入实现骨架）。
//
// UAL_WITH_DUCKDB 默认 0：此时本类只是 FUALYamlIndexSink 的透明转发器，
// 输出仍是 .ual/staging/<name>.yaml 的 16 键 YAML 文档流，不引入任何
// ThirdParty 头文件、不链接 DuckDB，随仓即可编译。
//
// UAL_WITH_DUCKDB=1：由**宿主工程**提供 ThirdParty DuckDB C API 头文件与
// 库。C API 调用全部集中在 .cpp 的 #if 分支里，通过 duckdb_open /
// duckdb_connect / duckdb_prepare / duckdb_bind_varchar 写入确定性的中间表；
// 本头文件和 Build.cs 都不引入 DuckDB 依赖。
//
// 无论哪条路径，16 键 Core Record 契约、已排序的稳定键序、无时间戳 /
// 绝对路径 / 随机 ID 的约束都不变；灌入 assets / asset_edges 仍由 Python
// 侧 `ual-cli` 的参数化 upsert 完成。
// ---------------------------------------------------------------------------

#ifndef UAL_WITH_DUCKDB
#define UAL_WITH_DUCKDB 0
#endif

class FUALDuckDbIndexSink : public IUALIndexSink
{
public:
	FUALDuckDbIndexSink(const FString& InName, const FString& InProjectDir);
	virtual ~FUALDuckDbIndexSink();

	virtual bool Begin() override;
	virtual void AppendRecord(const FUALAssetRecord& Record) override;
	virtual void AppendEdge(const FUALEdgeRecord& Edge) override;
	virtual bool Commit() override;

private:
	bool BeginDuckDb();
	bool CommitDuckDb();
	void CloseDuckDb();

	FString Name;
	FString ProjectDir;
	FString DatabasePath;

	TArray<FUALAssetRecord> Records;
	TArray<FUALEdgeRecord> Edges;
	bool bBegun = false;

	// 不透明句柄：头文件永远不需要 ThirdParty 头文件（即使宏开启）。
#if UAL_WITH_DUCKDB
	void* Database = nullptr;    // duckdb_database
	void* Connection = nullptr;  // duckdb_connection
#endif

	// 宏关闭时的唯一实现路径：完整的 16 键 YAML Sink。
	TUniquePtr<class FUALYamlIndexSink> YamlFallback;
};
