# CLAUDE.md — UAL 开发与自检规范

本文件是 AI 编程代理在 UEAssetLens 仓库内工作的强制约束。优先级高于任何生成式习惯。

## 1. 先读契约，再写代码

修改任何涉及数据字段的代码前，必须按顺序阅读：

1. `Server/schema/PROTOCOL.md` — 三层格式与 JSONL 交换契约
2. `Server/schema/init.sql` — DuckDB 表结构
3. `Source/UEAssetLens/Public/UALTypes.h` — C++ 侧结构
4. `Server/ual_common/db.py` — Python 侧读写

字段变更必须**四处同步**并递增 `schema_meta.schema_version`。禁止单侧改字段。

## 2. 不可违反的红线

- **禁止引入 VLM / 云端推理 / 外部 GPU**。所有语义必须来自 UE 反射属性、确定性数学或引用图谱。
- **禁止把整库或全量 JSON 返回给 LLM**。批量路径只能走 `ual_common/toon.py`；单资产精读只能走 `ual_common/yamlspec.py`。
- **禁止手写 TOON / YAML 字符串拼接**。必须调用唯一实现，保证转义、精度与 Diff 稳定。
- **禁止用 `content_hash` 做查重**。它只用于增量变更检测（`.uasset` 重存会改变二进制哈希）。查重只用 `fingerprint_vec` 余弦相似度。
- **禁止加载贴图 / 网格进显存**。UE 侧优先 `FAssetData` Tag-Value；领域提取器若必须加载，须显式标注并受 Commandlet 的无头模式约束。
- **禁止在 YAML / TOON 输出本地绝对路径、时间戳、随机 ID** —— 否则 CI Diff 永远脏。
- DuckDB FTS 索引是快照，批量灌库后必须执行 `rebuild_fts.sql`。

## 3. 编码约定

### 跨语言

- `taxonomy` 规则唯一事实源是 `Server/ual_common/taxonomy.json`：Python 侧 `ual_common/taxonomy.py` 直接读取，C++ 侧读 `Tools/gen_taxonomy.py` 生成的 `UALTaxonomyRules.h`。**禁止在任一侧另写一套类名 → 族名映射**；改 JSON 后必须重跑生成器，`--check` 会阻止生成物漂移。
- 索引不设 `AssetClass` 白名单：扫描、提取、灌库三层都不得按类型过滤或拒收。新资产类型应能被通用兜底提取器产出记录；命令行默认只扫 `/Game`，需要插件内容时显式传 `-Roots=` 或 `-IncludePlugins`。
- `logical_id` 在库内有 UNIQUE 约束；同名同类跨目录冲突由 `ual_common/db.py` 追加确定性路径哈希，禁止改成随机 ID 或直接丢弃记录。

### C++（UE5）

- UE 命名法：`F` 结构、`U` 对象、`I` 接口、`E` 枚举；头文件 `#pragma once`。
- `fingerprint_vec` 固定 16 个 `float`；缺失写 `NULL`，**不要写全零向量**（会导致余弦退化）。
- 所有除法必须做零除保护，禁止 NaN / Inf 进入索引。
- 新增提取器：实现 `IAssetExtractor`，`Priority()` 返回 100，`CanExtract()` 必须精确判定 `AssetClass`，不得吞掉其他类型的资产。
- 不直接 `#include <duckdb.h>`；生产写入走 `IUALIndexSink` 抽象，Appender 实现由 ThirdParty 提供。

### Python（Server/）

- Python ≥ 3.10；类型注解完整；只用 `duckdb` / `PyYAML` / `mcp` 三个运行时依赖。
- MCP Server 只读连接 DuckDB（`read_only=True`），绝不写索引。
- SQL 一律参数化，禁止 f-string 拼接用户输入（尤其是 `domain_scope`、`filters`）。
- 工具函数返回 `str`（TOON 或 YAML 文本），不返回 dict —— 这是 MCP 客户端上下文优化的前提。

## 4. 自检清单（提交前必跑）

```bash
python -m pytest Server/tests -q
python Server/ual_cli/ual_cli.py self-test
python Tools/gen_taxonomy.py --check   # taxonomy 规则表无漂移
python Tools/gen_docs.py            # 同步生成参考页
python Tools/gen_docs.py --check    # 漂移门禁
python -m mkdocs build --strict --site-dir _site
```

必须全部通过，且覆盖：

- [ ] DDL 可在空库执行，重复执行幂等
- [ ] JSONL 灌库后 `assets` / `asset_edges` 行数正确，FTS 可检索
- [ ] 4 个 MCP 工具输出符合 `PROTOCOL.md` 的 section / 字段顺序
- [ ] 含逗号的值在 TOON 中被正确引用与还原
- [ ] 余弦自连接能命中阈值并返回非零退出码
- [ ] `verify-references` 能识别不存在的资产路径与错误的材质参数名
- [ ] YAML 输出不含时间戳与绝对本地路径
- [ ] `Tools/gen_docs.py --check` 无文档漂移，`mkdocs build --strict` 零警告
- [ ] `python Server/ual_mcp/engine_bridge.py --self-test` 四项通过

## 4.1 文档站约定

- 站点配置在 `mkdocs.yml`，源文件在 `docs/`；发布工作流是 `.github/workflows/docs.yml`（`main` 分支 + 官方 Pages 部署），Pages Source 必须设为 `GitHub Actions`。
- `docs/reference/*.md` 与 `docs/agent-guide.md` 由 `Tools/gen_docs.py` 生成，**禁止手工编辑**。修改 `init.sql` / `PROTOCOL.md` / `TOOL_DEFS` / argparse / `CLAUDE.md` 后必须重跑生成器并提交结果，否则 CI 的 `--check` 会失败。
- 新增页面必须挂进 `mkdocs.yml` 的 `nav`，且所有相对链接要能通过 `--strict`（断链会直接让发布失败）。
- 生成器只能用 Python 标准库 —— 文档工作流只安装 `requirements-docs.txt`，不装 DuckDB / PyYAML / MCP 运行时依赖。
- `_site/` 与 `site/` 不入库。

## 4.2 引擎内官方 MCP 约定

- `Source/UEAssetLensMCP/` 只在 UE6 且启用 Epic `ModelContextProtocol` 插件时生效。模块在 `PostEngineInit` 检查模块是否已加载，未启用则只打一条 Warning，不影响 `ual-cli` 与独立 Python MCP。
- **禁止改用 `UToolsetDefinition`**：ToolsetRegistry 会把工具名限定成 `<Toolset>.<Tool>`，破坏与 Python 侧共享的 1:1 契约。用 `IModelContextProtocolModule::AddTool`。
- `Server/ual_mcp/tool_schemas.json` 是工具名 / 描述 / `inputSchema` 的唯一事实源，由 `Tools/gen_docs.py` 从 `tools.py` 的 `TOOL_DEFS` 生成，**禁止手工编辑**。改工具签名必须同步 `TOOL_DEFS`、`server.py` 的 `@sdk.tool()` 签名与生成器输出；`gen_docs.py --check` 会比对两者并拦截漂移。
- Python MCP Server **只支持官方 MCP SDK 2.x**（`mcp>=2,<3`，v2 中 `FastMCP` 已更名 `MCPServer`）。禁止重新引入手写 JSON-RPC 协议层；SDK 缺失或不兼容时明确报错退出，不静默降级。
- C++ 侧不引入 DuckDB C API；工具调用委托给 `engine_bridge.py` 子进程，通过 UTF-8 请求/响应文件传参（不要用 stdout，Windows 上会破坏中文注释编码）。
- 4 个工具全部只读，`GetAnnotations()` 必须返回 `accessMode=read` / `readOnlyHint=true`。新增写操作前必须重新评估。
- 耗时调用走 `RunAsync` + 线程池，**禁止在 game thread 上同步等 Python 进程**。
- 每次调用启动一个 Python 进程是已知成本，不要偷偷改成常驻进程而不更新 `docs/known-limitations.md`。
- 官方 server 是 HTTP loopback 且无认证，禁止把它暴露到非 loopback 地址。

UE 侧另需 `UnrealEditor-Cmd.exe` 编译并运行 `UEAssetLensTests` 自动化用例；本仓库 CI 不假定存在 UE SDK。

## 5. 提交与协作

- 提交信息用英文祈使句，例如 `feat(mcp): add TOON bundle serializer`。
- `.ual/`、`.workbuddy/`、`Binaries/`、`Intermediate/` 不入库。
- 不要自动 `git push`。本地提交与外部推送需用户显式确认。
- 本仓库嵌在宿主工程 `Ann/Plugins/UEAssetLens`，是独立 git 仓库；不要在父仓库 `Ann` 中 `git add` 本目录（会产生 gitlink）。

## 6. 遇到不确定时

优先查 `docs/architecture.md` 与 `docs/known-limitations.md`。若仍不确定，**先说明假设再实现**，不要静默猜测字段语义或资产类型。
