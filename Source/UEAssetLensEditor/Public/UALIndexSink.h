#pragma once

#include "CoreMinimal.h"
#include "UALTypes.h"

// ---------------------------------------------------------------------------
// IUALIndexSink：索引写入抽象接口。
//
// 生产实现：DuckDB C API Appender（直接写 .ual/index.duckdb 的 assets /
// asset_edges 两张表；需引入 duckdb.h，这里不直接依赖任何第三方头文件）。
// 参考实现：FUALJsonlIndexSink（写 .ual/staging/<name>.jsonl，无需第三方库
// 即可编译验证，由 Python 侧读取后灌入 DuckDB）。
// ---------------------------------------------------------------------------
class IUALIndexSink
{
public:
	virtual ~IUALIndexSink() = default;

	/** 打开输出、准备写入。返回是否成功。 */
	virtual bool Begin() = 0;

	/** 追加一条资产记录。 */
	virtual void AppendRecord(const FUALAssetRecord& Record) = 0;

	/** 追加一条边记录。 */
	virtual void AppendEdge(const FUALEdgeRecord& Edge) = 0;

	/** 落盘并关闭。返回是否成功。 */
	virtual bool Commit() = 0;
};
