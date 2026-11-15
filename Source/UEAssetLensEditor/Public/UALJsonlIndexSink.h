#pragma once

#include "CoreMinimal.h"
#include "UALIndexSink.h"
#include "UALTypes.h"

// ---------------------------------------------------------------------------
// FUALJsonlIndexSink：把记录写成 .ual/staging/<name>.jsonl，一行一个 JSON 对象，
// 供 Python 侧灌入 DuckDB。
//
// 【硬契约 · 与并行 Python 侧严格一致，键名一律 snake_case，不得增删改】
// 每行顶层恰好包含以下 16 个键：
//   asset_path, logical_id, asset_class, parent_class,
//   taxonomy(字符串数组), tokens(空格连接的单个字符串 / VARCHAR), content_hash, duration,
//   bounds_size(3 元浮点数组), skeleton_path, dominant_limb,
//   fingerprint_vec(16 元浮点数组),
//   interface_spec(字符串->字符串对象), domain_spec(字符串->字符串对象),
//   yaml_cache(字符串),
//   edges(数组；元素含 target_path / edge_type / weight)
//
// 说明：
//   * edges 按 source_path 聚合进对应资产行；source 即该行的 asset_path。
//   * updated_at 不在本契约内，由 Python 灌库侧以 CURRENT_TIMESTAMP 填充；
//     生产用的 DuckDB C API Appender 则直接写 FUALAssetRecord::UpdatedAt。
//   * 生产实现是 DuckDB C API Appender（直接写 .ual/index.duckdb）；JSONL 是
//     无需引入任何第三方头文件、仅依赖 UE Json 模块即可编译验证的参考实现。
//
// 实现策略：AppendRecord / AppendEdge 先缓存，Commit 时按 source_path 把 edges
// 合并进对应资产行，再一次性写出，保证"一行一个完整资产对象"。
// ---------------------------------------------------------------------------
class FUALJsonlIndexSink : public IUALIndexSink
{
public:
	explicit FUALJsonlIndexSink(const FString& InName, const FString& InProjectDir);
	virtual ~FUALJsonlIndexSink();

	virtual bool Begin() override;
	virtual void AppendRecord(const FUALAssetRecord& Record) override;
	virtual void AppendEdge(const FUALEdgeRecord& Edge) override;
	virtual bool Commit() override;

private:
	FString BuildRecordJson(const FUALAssetRecord& Record,
		const TArray<const FUALEdgeRecord*>& RowEdges) const;

	FString Name;
	FString ProjectDir;
	FString OutputPath;

	TArray<FUALAssetRecord> Records;
	TArray<FUALEdgeRecord> Edges;
	bool bBegun = false;
};
