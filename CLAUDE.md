# CLAUDE.md — UAL 开发与自检规范

本文件是 AI 编程代理在 UEAssetLens 仓库内工作的强制约束。优先级高于任何生成式习惯。

## 1. 先读契约，再写代码

修改任何涉及数据字段的代码前，必须按顺序阅读：

1. `Server/schema/PROTOCOL.md` — 三层格式与 YAML 交换契约
2. `Server/schema/init.sql` — DuckDB 表结构
3. `Source/UEAssetLens/Public/UALTypes.h` — C++ 侧结构
4. `Server/ual_common/db.py` — Python 侧读写

字段变更必须**四处同步**并递增 `schema_meta.schema_version`。禁止单侧改字段。

## 2. 不可违反的红线

- **禁止引入 VLM / 云端推理 / 外部 GPU**。所有语义必须来自 UE 反射属性、确定性数学或引用图谱。
- **禁止把整库或全量原始数据返回给 LLM**。批量路径只能走 `ual_common/toon.py`；单资产精读只能走 `ual_common/yamlspec.py`。
- **禁止手写 TOON / YAML 字符串拼接**。必须调用唯一实现，保证转义、精度与 Diff 稳定；C++ 侧 staging/请求格式必须走 `Plugins/UEYaml/Source/UEYaml/Public/UEYaml.h` 的 emitter，不得用 `FString::Printf` 手工拼接。
- **禁止用 `content_hash` 做查重**。它只用于增量变更检测（`.uasset` 重存会改变二进制哈希）。查重只用 `fingerprint_vec` 余弦相似度。
- **禁止加载贴图 / 网格进显存**。UE 侧优先 `FAssetData` Tag-Value；领域提取器若必须加载，须显式标注并受 Commandlet 的无头模式约束。
- **禁止在 YAML / TOON 输出本地绝对路径、时间戳、随机 ID** —— 否则 CI Diff 永远脏。
- DuckDB FTS 索引是快照，批量灌库后必须执行 `rebuild_fts.sql`。

## 3. 编码约定

### 跨语言

- `taxonomy` 规则唯一事实源是 `Server/ual_common/taxonomy.yaml`：Python 侧 `ual_common/taxonomy.py` 直接读取，C++ 侧读 `Tools/gen_taxonomy.py` 生成的 `UALTaxonomyRules.h`。**禁止在任一侧另写一套类名 → 族名映射**；改 YAML 后必须重跑生成器，`--check` 会阻止生成物漂移。
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
- [ ] YAML 文档流灌库后 `assets` / `asset_edges` 行数正确，FTS 可检索
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
- 生成器依赖 Python 标准库与 PyYAML —— 文档工作流只安装 `requirements-docs.txt`（已含 `PyYAML>=6.0`），不装 DuckDB / MCP 运行时依赖；生成器读写插件自有 YAML 数据文件需要 PyYAML。
- `_site/` 与 `site/` 不入库。

## 4.2 引擎内官方 MCP 约定

- `Source/UEAssetLensMCP/` 只在 UE6 且启用 Epic `ModelContextProtocol` 插件时生效。模块在 `PostEngineInit` 检查模块是否已加载，未启用则只打一条 Warning，不影响 `ual-cli` 与独立 Python MCP。
- **工具命名必须对齐官方限定名（P0）**：官方对外名为 `{ModuleName}.{ClassName}.{FunctionName}`（`FakeToolset.cpp:49` 拼名、MCP 适配器 `ModelContextProtocolToolsetRegistryAdapter.cpp:588` 注册），源码层 UFUNCTION 为 PascalCase（878 个中 877 个）。`ValidateToolName()` 接受 snake_case 只代表**合法**、不代表**规范**。**禁止新增 `ual_*` 裸名工具**：对外名由 `TOOL_DEFS.mcp_name` 唯一决定，`server.py` 用 `@sdk.tool(name=...)` 显式指定（已核实 MCP SDK 2.x 支持），C++ `AddTool` 直接用该限定名注册；Python 内部实现函数名保留 snake_case，与对外名解耦。目标命名表与硬切迁移清单见 `docs/mcp-positioning.md` §2.1，**不保留旧名别名**。
- **概念按能力分类，实现分两阶段**：UAL 工具（含现有 4 个语义检索工具，限定名见 `docs/mcp-positioning.md` §2.1）与官方工具同属统一工具层，按 `AIAccessMode` 分 `Read`(Query) / `Write`(Edit) / `Execute`(事务)，**不按实现归属分层**。阶段 1 继续用 `IModelContextProtocolModule::AddTool`：ToolsetRegistry 会把工具名限定成 `<Toolset>.<Tool>`（`FakeToolset.cpp:49` 实证），贸然改用 `UToolsetDefinition` 会破坏与 Python 侧共享的 1:1 契约。阶段 2 若要迁移，必须一次性同步 `TOOL_DEFS`、`server.py`、`tool_schemas.yaml`、`docs/reference/*.md` 与所有客户端调用，**不得引入双轨别名**。详见 `docs/mcp-positioning.md`。
- **禁止重复实现官方已有工具**（`FindAssets` / `CreateMaterial` / `Duplicate` / `SaveAsset` / `SetActorTransform` 等 111 个资产域工具）；新增工具前先查父工程 `Docs/ai-capabilities/toolset-inventory.yaml`。语义检索、指纹查重、共现装配、TOON 批量召回与事务编排属 UAL 自建范围（官方 878 工具中语义查重与 dry-run / 回滚能力命中为 0）。
- `Server/ual_mcp/tools.py::TOOL_DEFS` 是工具契约唯一事实源（含 `mcp_name` 对外限定名、描述、`inputSchema`）；`Server/ual_mcp/tool_schemas.yaml`（内容仍是等价的 JSON Schema 对象）与 `docs/reference/mcp-tools.md` 由 `Tools/gen_docs.py` 生成，**禁止手工编辑**。改工具名 / 签名必须同步 `TOOL_DEFS`、`server.py` 的 `@sdk.tool(name=...)` 与生成器输出；`gen_docs.py --check` 会比对并拦截漂移。
- Python MCP Server **只支持官方 MCP SDK 2.x**（`mcp>=2,<3`，v2 中 `FastMCP` 已更名 `MCPServer`）。禁止重新引入手写 JSON-RPC 协议层；SDK 缺失或不兼容时明确报错退出，不静默降级。
- C++ 侧不引入 DuckDB C API；工具调用委托给 `engine_bridge.py` 子进程，通过 UTF-8 请求/响应文件传参（不要用 stdout，Windows 上会破坏中文注释编码）。
- **Python 依赖必须走 UE 官方 `PythonRequirements`**：`UEAssetLens.uplugin` 声明 `duckdb` / `PyYAML` 的精确版本与 wheel hash（引擎 `bPipStrictHashCheck` 默认强制校验），由 `PythonScriptPlugin` 装到 `<Project>/Intermediate/PipInstall/Lib/site-packages`；可用 `UnrealBuildTool -Mode=PipInstall -PipAction=Install` 离线安装。`UALEngineMcpBridge` 的默认解释器由 `FPaths::EngineDir()` 推导为引擎内嵌 Python（Win64/Mac/Linux 分支），`engine_bridge.py` 在导入第三方包前把该 site-packages 加入 `sys.path`。**禁止把本机 Python 绝对路径写进工程 Config，禁止回退到裸 `python`**（ABI 与依赖不匹配会导致 `ModuleNotFoundError`）。`Server/requirements.txt` 只服务独立 Python server / CI，两套环境不要混同。
- 现有 4 个检索工具只读，`GetAnnotations()` 必须返回 `accessMode=read` / `readOnlyHint=true`。**允许新增 `Write` 类工具，但必须经事务层**（`UEAssetLens.AssetEditTransactionToolset.PlanEdit` → 审批 → `UEAssetLens.AssetEditTransactionToolset.ApplyPlan` → `UEAssetLens.AssetEditTransactionToolset.VerifyEdit`），禁止把裸写工具直接暴露给客户端；每个 operation 的 `tool` 必须命中官方工具白名单，`plan_id` 必须确定性（禁止随机 ID / 时间戳 / 本地路径）。新增工具一律显式标注 `AIAccessMode`——未标注按 `Execute` 处理，CI 需拦截。
- 开放编辑后必须保证索引一致：事务 `commit` 阶段触发 `UALIndexBuilder` 增量重扫，或补 AssetRegistry 事件监听；**MCP 工具侧仍保持 DuckDB `read_only=True`**，写索引只由 Commandlet / 事件监听侧完成。
- 耗时调用走 `RunAsync` + 线程池，**禁止在 game thread 上同步等 Python 进程**。
- 每次调用启动一个 Python 进程是已知成本，不要偷偷改成常驻进程而不更新 `docs/known-limitations.md`。
- 官方 MCP / Toolset 为 `Experimental + NoRedist`，只能开发期启用，**不可随发布包分发**；能力分级与降级行为见 `docs/mcp-positioning.md` 第 7 节。客户端统一走 `http://localhost:8000/mcp`（HTTP），Python stdio server 仅作降级路径保留、不删。
- 官方 server 是 HTTP loopback 且无认证，禁止把它暴露到非 loopback 地址。

UE 侧另需 `UnrealEditor-Cmd.exe` 编译并运行 `UEAssetLensTests` 自动化用例；本仓库 CI 不假定存在 UE SDK。

## 5. 提交与协作

- 提交信息用英文祈使句，例如 `feat(mcp): add TOON bundle serializer`。
- `.ual/`、`.workbuddy/`、`Binaries/`、`Intermediate/` 不入库。
- 不要自动 `git push`。本地提交与外部推送需用户显式确认。
- 本仓库嵌在宿主工程 `Ann/Plugins/UEAssetLens`，是独立 git 仓库；不要在父仓库 `Ann` 中 `git add` 本目录（会产生 gitlink）。

## 6. 遇到不确定时

优先查 `docs/architecture.md` 与 `docs/known-limitations.md`。若仍不确定，**先说明假设再实现**，不要静默猜测字段语义或资产类型。
