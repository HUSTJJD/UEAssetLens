# 已知限制

本页如实列出 UAL 当前实现边界，不把「设计目标」写成「已验证结论」。状态口径（**已实现 / 规格 / 未落地**）、「UAL 是什么」与完整能力全景见 [项目定位与能力规格](positioning.md) §3；正式规格主文见 [资产全生命周期架构规格](asset-lifecycle-architecture.md)。本页只列边界与未验证项，不重复定义产品。

**当前计数**：DuckDB `schema_meta.schema_version` `3.0`（冻结 16 键 Core Record + 独立 `extension_payload`）；
`Server/ual_mcp/tool_schemas.yaml` `schema_version` `4.0`；10 个既有只读工具 + 20 个 registry-driven runtime tools（共 30 个工具）；
`ual-cli` 既有 5 个命令 + `self-test` + 22 个离线内核命令：`list-extensions`、
`list-extension-fields`、`search-extension-fields`、`get-assembly-graph`、`list-official-tools`、
`get-index-revision`、`get-lifecycle-events`、`allowlist-audit`、`diff-assets`、`read-diff-layer`、
`verify-diff`、`plan-edit`、`apply-plan`、`verify-edit`、`rollback-edit`、`expand-gamespec`、
`run-validation-loop`、`run-workflow`、`request-rescan`、`apply-events`、`read-artifact`、`import-gamespec`。新增离线门禁命令覆盖
`gen_taxonomy.py --check`、`audit_tool_annotations.py --check`、`audit_engine_toolsets.py --check`、
`server.py --self-test`、`bench_index.py --count 10000 --incremental`、
`setup_mcp.ps1 -DryRun -Transport Http`。`Tools/bench_index.py --count 100000 --incremental` 实测冷建库
**2.727s**、未变增量 **0.0796s**（<30s / <1s 目标，**两个目标均满足**），方法见 [性能基准](benchmarks.md)。
`Config/UAL/gateway/integrations.yaml` 中的 P1 / P2 外部服务保持 `disabled` / license-reviewed / Zero-VLM boundary，
**not executed**。

**离线门禁全部通过（2026-11-27）**：`pytest Server/tests -q` 168 项；`ual-cli self-test` 17/17；
`server.py --self-test`、`engine_bridge.py --self-test`；`gen_docs.py --check`、`gen_taxonomy.py --check`、
`audit_tool_annotations.py --check`、`audit_engine_toolsets.py --check`；`mkdocs build --strict`；
`bench_index.py --count 10000 --incremental` smoke；`setup_mcp.ps1 -DryRun -Transport Http`。
干净的纯真实工程索引已从 staging 重建到 `.ual/real_project.duckdb`（命令与读数见 §14）：15 资产 / 56 边 /
75 行 `extension_payload`，`schema_version` `3.0`、`engine_version` `UE6`，FTS `ann character` 返回 3 条有界命中，
`index_revision` 两次读取稳定，验证 **PASS**。**本地 UE6 UBT Build 已通过**（2026-11-27 用户明确授权；
`AnnEditor Win64 Development`，19 个 action、UHT processed AnnEditor 写入 4 个生成文件、UBA local executor
97.24 秒、`Result: Succeeded`、`Total execution time: 136.42 seconds`、输出 `UnrealEditor.exe`，
仅 C4996 `UE_LOG` 弃用告警、无编译错误，完整命令与读数见 §1）；此前非编译类 UBT 检查与 22 个翻译单元的
MSVC `/Zs` 仅语法检查仍只是源码级证据。**CI 仍不编译 C++**；真实 UE host executor、真实 `.uasset` 写入、
编辑器编译 / PIE、UE 自动化用例仍为**规格 / 未落地**，C++ 编译通过不代表上述运行时能力已验证，
并不代表所有规格已验证。

| # | 限制 | 关联里程碑 | 当前状态 |
| --- | --- | --- | --- |
| 1 | UE C++ 侧需本地 UE6 SDK 编译 | M1 | 源码随仓，**CI 不编译 C++**；2026-11-27 本地 UE6 UBT Build 已通过（`Result: Succeeded`，见 §1）；真实 UE 写入 / 编辑器编译 / PIE / UE 自动化用例仍 **规格 / 未落地**；2026-11-24 实机编译另作历史记录，见 §11 |
| 2 | DuckDB C API Appender 生产实现未附带 | M1 | `IUALIndexSink` / Appender 仍为源码级预留；提供 16 键 YAML Sink、Python upsert 与 canonical YAML artifact sink，**已实现（离线）** |
| 3 | 领域提取器深度边界 | M2 | **Python M2 reference + C++ zero-load domain extractor implemented at source level**；本地 UE6 UBT Build 已通过、CI 仍不编译 C++；未加载 mesh / animation，真实 UE 端到端反射级深度规格仍 **规格** |
| 4 | TOON 为自定义方言 | M3 | 已有正式协议与转义规则、往返解析测试；非社区标准 |
| 5 | FTS 索引是快照且需要 `fts` 扩展 | M1 | 灌库后强制重建；扩展不可用时降级 `ILIKE` |
| 6 | `MAP(VARCHAR,VARCHAR)` 丢失数值类型 | M1 | Core Record 的 `interface_spec` / `domain_spec` 仍按字符串约定；类型化字段已由独立 `extension_payload` 表承载，**已实现** |
| 7 | 10 万级冷建库 / 增量性能 | M1 | **已实现（离线测量）**：100000 资产冷建库 2.727s、未变增量 0.0796s；见 §7 / [性能基准](benchmarks.md) |
| 8 | 16 维指纹存在方向余弦主导的假阳性 | M2 | 种子数据实测 0.914 误报，需人工复核 |
| 9 | `verify-references` 是正则启发式 | M4 | 非 UE 反射级解析，存在动态资产 / retarget 误报 |
| 10 | 白名单机制 | M4 | **已实现**：`allowlist.py` + 声明式清单 + review 元数据 / 审批引用 / 有效期；见 §10 |
| 11 | 引擎内 MCP 依赖 UE6 实验性插件 | M5 | 2026-11-24 C++ 实机编译与 headless `tools/list` / `tools/call` 联调是**历史记录**，本轮离线门禁未复核；仅开发期启用，`Experimental + NoRedist`，不可随发布包分发 |
| 12 | 每次工具调用启动一个 Python 进程 | M5 | 百毫秒级延迟；已用 `RunAsync` 避免阻塞 game thread，见 §12 |
| 13 | Python MCP Server 硬依赖官方 SDK 2.x | M3 | 无手写协议降级；SDK 缺失即报错退出，离线环境走 `ual-cli`，见 §13 |
| 14 | 全类型覆盖改造的 C++ 部分不在 CI 编译 | M1+ | 168 项 pytest / `ual-cli self-test` 17/17 等离线门禁已通过；本地 UE6 UBT Build 已通过（见 §1），但 CI 仍只覆盖 Python；真实 UE 端到端验证仍 **规格 / 未落地**，见 §14 |
| 15 | `custom` 策略规则 | Phase 0 | **已实现**：`lint-policy` 装载声明式扩展清单，custom 谓词经 `ExtensionRegistry` 解析；见 §15 |
| 16 | 真实 UE host executor / 写入 / compile / PIE | Phase 3–5 | **规格 / 未落地**：离线 transaction / gateway / validation loop 只产出 dry-run 工件，不写真实 `.uasset`、不启动 PIE、不运行 UE 自动化用例；C++ 编译通过不改变此状态 |
| 17 | C++ 源码级 hooks 的编译验证 | Phase 1–5 | **已实现（宿主编译验证）**：2026-11-27 本地 UE6 UBT Build `Result: Succeeded`（见 §1）；CI 仍不编译 C++；`UEAssetLensTests` 自动化用例、headless 编辑器联调、真实 UE 写入 / PIE 仍需宿主实机验证，为 **规格 / 未落地** |

---

## 1. UE C++ 侧需本地 UE6 SDK 编译

`Source/` 中的插件必须链接本机对应版本 UE6 引擎才能编译；仓库只提供源码，**CI 只覆盖 Python 侧**（pytest + self-test + 生成 / 严格文档门禁）。C++ 代码刻意不 `#include <duckdb.h>`，因此可在无 ThirdParty 库的环境下编译验证。2026-11-24 的实机全量编译与运行时联调记录见 §11。

**本地 UE6 UBT Build 已通过（2026-11-27，用户明确授权）**：在宿主工程执行
`dotnet D:/learning/AnnBuildTools/AnnUnrealEngine/Engine/Binaries/DotNET/UnrealBuildTool/UnrealBuildTool.dll -Mode=Build AnnEditor Win64 Development -Project=D:/learning/AnnBuildTools/Ann/Ann.uproject -Compiler=VisualStudio2022 -NoHotReload -WaitMutex`。
UBT 运行 **19 个 action**，UHT processed **AnnEditor**（**4 个 generated files written**），
Unreal Build Accelerator **local executor 97.24 秒**，`Result: Succeeded`，
`Total execution time: 136.42 seconds`，输出
`D:/learning/AnnBuildTools/AnnUnrealEngine/Engine/Binaries/Win64/UnrealEditor.exe`；
仅有 **C4996 `UE_LOG` 弃用告警**，**无编译错误**。

此前通过的非编译类 UBT 检查（`-Mode=QueryTargets`、`-Mode=Query -Query=Capabilities` /
`AvailableTargets` / `EnabledPlugins` / `TargetDetails -NoIntellisense` /
`CompileDetails`（配 `-Compiler=VisualStudio2022 -SyntaxCheckOnly`）、
`-Mode=GenerateClangDatabase -NoExecCodeGenActions -Compiler=VisualStudio2022`）与 22 个 UAL 翻译单元
（含生成的 `.gen.cpp` 与插件源码）的 MSVC `/Zs` 仅语法检查仍只是源码级证据，不是运行时验证。

**CI 仍不编译 C++**，本次构建是宿主工具链上的显式授权动作；`UEAssetLensTests` UE 自动化用例、
headless 编辑器联调、真实 `.uasset` 写入 / 编辑器编译 / PIE 仍为**规格 / 未落地**。
C++ 编译通过不代表上述运行时能力或所有规格已验证。

## 2. DuckDB C API Appender 未附带（源码级预留）

生产高速写入路径设计为 C++ Appender 列式写入；`IUALIndexSink` 已留好替换点，但 Appender 仍是**源码级 / 规格**，其生产实现与端到端写入验证仍未落地（本地 UE6 UBT Build 已通过，不改变此状态）。当前随仓的是 `UALYamlIndexSink` → Python `ingest_records()` 参数化 upsert，以及 `ual_common/artifacts.py` canonical YAML 工件 sink。离线事务不写 DuckDB 索引；MCP 侧连接始终 `read_only=True`。

## 3. 领域提取器 M2：Python 参考 + C++ 源码级 zero-load 提取器

`FUniversalReflectionExtractor` 覆盖 100% UObject。`Server/ual_common/domain.py` / `fields.py` 已实现 **Python M2 参考领域提取器**；`Source/UEAssetLensEditor/Private/UALDomainFeatureExtractor.cpp` 与由 `Tools/gen_taxonomy.py` 生成的第三个头 `UALDomainProfiles.h`（声明式来源：`Server/ual_common/taxonomy.yaml` 与 `Config/UAL/fields/default.yaml`）提供 **C++ zero-load domain extractor at source level**。它只读 `FAssetData` / 确定性字段，不加载 mesh / animation、不做 UE 深度反射、不调用 VLM / 云 / GPU / 外部生成式服务；Python / C++ 两侧都只投影既有 16 键 / MAP 与独立 `extension_payload` 表。

**注意区分两个概念**：「覆盖所有资产类型」指任何 `AssetClass` 都能入索引并产出通用记录（由通用兜底提取器保证）；「每类资产都有 UE 反射级深度规格」仍依赖宿主实机复核。当前 C++ M2 domain extractor 是**源码级实现**，已随 2026-11-27 本地 UE6 UBT Build 编译通过（见 §1），但 CI 仍不编译 C++；没有加载 mesh / animation、没有真实 UE 端到端验证结论。Python 侧 `fingerprint.py` / `yamlspec.py` / `domain.py` 与种子数据使离线链路可完整自检；真实 UE 端到端产出的深度资产规格仍需 UE SDK 实机复核。详见 §14 / §17。

## 4. TOON 为自定义方言

TOON 不是社区标准。`Server/schema/PROTOCOL.md` 已定义语法、转义（逗号 / 双引号 / 换行触发包裹，冒号不转义）与工具字段顺序；`ual_common/toon.py` 是唯一编解码实现，`test_toon_roundtrip_with_escaping` 覆盖含逗号与引号的值。跨版本升级仍需同步生成端与消费端。禁止手写 TOON 字符串拼接。

## 5. FTS 索引是快照

DuckDB FTS 不随 `INSERT` 自动更新。`ual-cli build` 每次灌库后调用 `rebuild_fts()`（`drop_fts_index` + `create_fts_index`）。若 `fts` 扩展在受限环境不可用，`db.search_assets()` 降级为 `tokens ILIKE`，BM25 排序消失、召回精度下降。

## 6. `MAP(VARCHAR,VARCHAR)` 丢失数值类型，但类型化扩展表已落地

Core Record 的 `interface_spec` / `domain_spec` 值统一为字符串。约定已固化：标量写纯文本，向量写 `[a,b,c]`，列表 / 对象写 YAML 文本片段；数值范围过滤只在 `v_search` 的 `TRY_CAST` 投影列上做，避免字典序失真。DuckDB v3.0 冻结 16 键主表，新增字段不进入第 17 个 staging 键；`Server/ual_common/fields.py` 把声明式 typed fields 写入独立 `extension_payload` 表，**已实现**。新增字段必须四处同步并 bump `schema_meta.schema_version`。

## 7. 性能目标已实测（离线基准）

`Tools/bench_index.py --count 100000 --incremental` 实测：冷建库 **2.727s**（目标 <30s），未变增量 **0.0796s**（目标 <1s），**两个目标均满足**。冷建库包含 schema、SQL `range(?)` 批量生成 100000 行与 FTS 重建；未变增量只做只读变更探测，无写入。数字是 Python / DuckDB 离线基准，**不是** UE 编辑器编译、PIE、C++ 提取器编译或真实 `.uasset` 写入验证；本地 UE6 UBT Build 已通过（见 §1）但 **CI 仍不编译 C++**，真实 UE 写入 / PIE / UE 自动化用例仍**规格 / 未落地**。报告不写时间戳、本地绝对路径、随机 ID，也不把整库原始数据塞进 LLM。CI 用 `--count 10000 --incremental` 作 smoke。详见 [性能基准](benchmarks.md)。

## 8. 指纹假阳性

实测种子库：`AM_Knight_HeavySlash_02` 与 `AM_Knight_HeavySlash_03` 相似度 0.9999（真重复），但 `AM_Knight_HeavySlash_02` 与 `AM_Knight_SpinSweep_04` 也达 0.914（假阳性）。原因是所有 RootMotion 均沿 +X，方向余弦 3 维完全相同，叠加时长维度接近。默认阈值 0.85 是**召回取向**，命中后必须 `UEAssetLens.AssetSemanticToolset.InspectAsset` 精读 YAML 或人工确认。`content_hash` 只用于增量变更检测，不用于查重。改进方向见 [架构说明](architecture.md) §3。

## 9. `verify-references` 是静态启发式

正则提取 `/Game/...`、`skeleton:`、`User.<Param>` 与 `Set*ParameterValue`，不做 Lua / YAML AST 解析，也不查询 UE 反射。运行期动态生成的资产、跨骨架 retarget、代码动态创建的材质参数都会被误报。它的定位是拦截 AI 幻觉提交，不是引用完整性证明。

## 10. 白名单机制已实现

`Config/UAL/allowlists/default.yaml` 与 `Server/ual_common/allowlist.py` 已实现版本化白名单：**已实现**。`duplicates` / `references` 条目必须带 `reason` / `owner` / `review_by`；过期、无效或缺少审批引用（`approved_by` / `review_ref`）的条目不豁免门禁。`pair` 按无序对精确匹配，`rule` / `path` 不得使用通配符；有界 `allowlist_issues` TOON 报告不输出本地路径、时间戳、随机 ID 或 reviewer。白名单只豁免报告，不改指纹、索引或 Core Record。落地前请勿依赖旧文档中「白名单尚未实现」的过期表述；review 流程细节见 [CI 门禁](ci-gates.md) §5。

## 11. 历史引擎内 MCP 编译与运行时联调记录（headless 联调本轮未复核）

**历史记录（2026-11-24）**：宿主工程 `Ann` 的 `*.Target.cs` 曾适配 UE6，`AnnEditor Win64 Development` 全量编译 **4450 actions / `Result: Succeeded`**；`Source/UEAssetLensMCP/` 曾按官方 `ModelContextProtocol` 头文件实机编译并链接；headless editor 端到端联调曾闭环（`tools/list` 出现全部限定名工具 → `tools/call` 返回真实 TOON / YAML）。bridge 走引擎内嵌 Python 与 UE 官方 `PythonRequirements`。**本轮已另有 2026-11-27 本地 UE6 UBT Build 通过（见 §1），但 headless 编辑器联调 / `tools/list` / `tools/call`、真实 UE 写入 / PIE 与 UE 自动化用例均未复核**；这些历史运行时联调结果不能当作当前已验证结论。

**仍存在的环境抖动**：本机 UE6 源码树的 `Engine/Binaries` 混有不同 BuildId 的 DLL，`-run=` commandlet 启动时偶发
`Plugin 'DatasmithContent' / 'OnlineServices' failed to load`（模块被判为 out-of-date 而跳过），随后 `EngineExit()`。
这与 UAL 代码无关。`ual-cli build` 固定使用已验证的参数集：
`-nullrhi -Unattended -NoSplash -NoSound -stdout -FullStdOutLogOutput -run=UEAssetLensEditor.UALIndexBuilder -Out=index`；
`-run=` 必须用 `Module.Class` 限定名。若仍失败，重跑一次或先做一次引擎全量编译即可恢复；本轮离线门禁不执行这些命令，headless 编辑器联调与真实 UE 写入 / PIE 仍为**规格 / 未落地**（本地 UE6 UBT Build 已通过，见 §1）。

**降级路径不变**：`engine_bridge.py --self-test`、pytest、CLI 自检与文档漂移门禁都不依赖 UE SDK；
`ModelContextProtocol` 声明为 `Optional` 插件依赖，插件未启用时模块只打一条 Warning，不影响 UAL 其余功能。官方 MCP / Toolset 为 `Experimental + NoRedist`，loopback-only、无认证，不可随发布包分发。

## 12. 引擎内工具每次调用启动一个 Python 进程

**现象**：`FUALEngineMcpBridge::CallTool` 用 `FPlatformProcess::ExecProcess` 拉起 `engine_bridge.py`，进程退出后读取 UTF-8 响应文件。一次调用包含 Python 解释器启动、DuckDB 打开与索引查询，延迟在百毫秒量级。

**影响**：交互式 Ask 的体感延迟高于常驻进程方案；高频批量调用（例如一次会话几十次检索）成本明显。

**当前缓解**：`FUALEngineMcpTool::RunAsync` 把调用放到线程池，不阻塞编辑器 game thread；请求 / 响应走文件而非 stdout，保证中文语义注释不乱码。替换点集中在 `FUALEngineMcpBridge`，未来可改为常驻 Python 服务或 DuckDB C API，不影响工具 schema 与上层契约。

## 13. Python MCP Server 硬依赖官方 SDK 2.x

**现象**：`Server/ual_mcp/server.py` 只从 `mcp.server.mcpserver` 导入 `MCPServer`，`requirements.txt` 固定为 `mcp>=2,<3`。仓库早期曾附带一个手写的 `stdio_server.py`（仅实现 `initialize` / `ping` / `tools/list` / `tools/call`，协议版本写死 `2024-11-05`），现已删除。

**影响**：未安装或安装了 1.x SDK 的环境无法启动 Python MCP Server，进程打印可读错误并以退出码 2 结束。离线 / 受限环境不再有「能跑但语义不完整」的第三条路径。

**决策依据**：手写实现缺少 resources / prompts / 分页 / 协议版本协商，而官方 SDK v2 已能同时服务 2025 与 2026 两代协议修订；维护第三套协议语义的收益低于风险。工具名、描述与 `inputSchema` 的唯一事实源已迁至 `Config/UAL/extensions/tools.yaml` 的 registry snapshot，由 `gen_docs.py` 生成 `tool_schemas.yaml` 供前端使用，`audit_tool_annotations.py --check` 校验限定名与 annotations。

**当前缓解**：无 SDK 的离线 / CI 场景本来就走 `ual-cli`（`build` / `lint-duplicates` / `lint-policy` / `verify-references` / `export-parquet`），不依赖 MCP；`server.py --self-test` 也不导入 SDK，可在纯 DuckDB 环境验证工具输出。

## 14. 全类型覆盖的实际边界：C++ 不在 CI 编译

**类型无关性已成立**：`UALIndexBuilderCommandlet` 不按 `AssetClass` 过滤；`FUALExtractorRegistry` 内置 Priority 0 的 `FUniversalReflectionExtractor`，其 `CanExtract()` 恒为 `true`；`init.sql` 的 `asset_class` 是无枚举约束的 `VARCHAR`。因此新增任何 UObject 派生类（含插件自定义类型、新版引擎类型）都无需改代码即可入索引。

本次补齐的三处实现：

| 项 | 改动 | 验证状态 |
| --- | --- | --- |
| 扫描范围 | Commandlet 新增 `-Roots=/Game,/PluginA` 与 `-IncludePlugins`（自动纳入所有含内容插件的挂载根），按 `PackageName` 去重 | 2026-11-24 历史实机产出真实索引；2026-11-27 本地 UE6 UBT Build 已通过（见 §1），CI 仍不编译 C++，真实 UE 端到端扫描仍为**规格** |
| taxonomy / feature / domain profiles | `Server/ual_common/taxonomy.yaml`（24 族）与 `Config/UAL/fields/default.yaml` 为声明式单一事实源；`gen_taxonomy.py` 生成 `UALTaxonomyRules.h`、`UALFeatureProfiles.h` 与 `UALDomainProfiles.h` 三个头，配套源码级 zero-load `UALDomainFeatureExtractor` | 168 项 pytest（覆盖 27 种类型）+ `Tools/gen_taxonomy.py --check` 漂移门禁已通过；本地 UE6 UBT Build 已通过、CI 仍不编译 C++，真实 UE 端到端反射级深度规格仍 **规格** |
| `logical_id` 碰撞 | `ual_common/db.py` 在冲突且 `asset_path` 不同时追加确定性 8 字符路径哈希 | pytest 覆盖重复灌库的幂等性 |

**仍然存在的限制**：

1. **默认扫描根仍是 `/Game`**。插件内容不会自动进入索引，必须显式传 `-Roots=...` 或 `-IncludePlugins`。历史快照中 Ann 工程 `Content/` 下 13 个 `.uasset`、`Plugins/` 下 205 个，不显式配置就会漏掉约 94%。
2. **零加载原则不变**：`FAssetData` 元数据与 Tag-Value 覆盖的是元数据层；`Server/ual_common/domain.py` 是 Python M2 reference，`UALDomainFeatureExtractor` 是 C++ source-level zero-load domain extractor；不加载 mesh / animation，不含 UE 反射深度字段。
3. **C++ 改动未在 CI 编译**：§11 的 2026-11-24 全量编译是历史记录；本地 UE6 UBT Build 已于 2026-11-27 通过（见 §1），但 **CI 仍不编译 C++**；任何新的 C++ 源码级 hooks 都需重新在宿主工具链实机编译确认，真实 UE 端到端验证仍为**规格 / 未落地**。
4. **干净的纯真实工程索引已重建并验证（离线）**：使用 `--index .ual/real_project.duckdb build --project ../.. --from-yaml ../../.ual/staging/index.yaml`
   从 staging（15 个资产 / 56 条边）重建 `.ual/real_project.duckdb`，得到 15 个资产 / 56 条边 /
   75 行 `extension_payload`，`schema_version` `3.0`、`engine_version` `UE6`；FTS `ann character` 返回
   3 条有界命中，`index_revision` 两次读取稳定，位于被忽略 `Intermediate/` 下的只读验证脚本 verdict **PASS**。
   现有 `.ual/index.duckdb` 是 seed + 真实数据的混合产物（24 个资产 / 61 条边），**禁止**当作已完成的
   干净纯真实工程全量索引引用。该结论只覆盖离线灌库 / FTS / revision 稳定性，不代表真实 UE host executor、
   `.uasset` 写入、compile / PIE。

## 15. custom 策略规则已实现

`ual-cli lint-policy` 现在通过 `Server/ual_common/extensions.py` 装载 `<Plugin>/Config/UAL/extensions/*.yaml` 与四级偏好中的声明式扩展清单，`kind: custom` 规则经 `ExtensionRegistry` 的 `policy` namespace 解析到 `ual_common.extensions_builtins` 固定 callable。YAML 本身不 import 任意模块、不执行任意代码。单元测试覆盖自定义谓词分派、四级偏好覆盖与有界 TOON 报告；默认 `enforce: false` 报告优先。团队规范仍应使用声明式 `naming` / `directory` / `format` / `reference` / `custom` 数据，不引入用户代码执行路径。详见 [CI 门禁](ci-gates.md) §3 与 [扩展体系](extensions.md)。

## 16. 真实 UE host executor / 写入 / compile / PIE 仍为规格

`transaction.py` / `gateway.py` / `workflow.py` / `gamespec.py` 提供的是**已实现（离线 dry-run）**编排：plan、审批、inverse、verify、rollback、rescan request、apply-events、五层 diff 工件、GameSpec / FPS 3C 十步链与离线 validation report。它们不写 `.uasset`、不写 DuckDB 索引、不调用官方 Toolset 的真实 Write / Execute、不启动 UE 编译或 PIE。

真实 UE host executor 需要注入官方工具调用通道、AssetRegistry 事件重扫、宿主编译 / PIE runner 与失败回滚。当前为**规格 / 未落地**：离线门禁、干净索引灌库、非编译类 UBT 检查与 MSVC `/Zs` 仅语法检查、本地 UE6 UBT Build（`Result: Succeeded`，见 §1）均已通过，**但 CI 仍不编译 C++**；没有 UE 自动化用例、编辑器联调、真实资产写入 / 编辑器编译或 PIE 验证。C++ 编译通过**不是**真实 UE 运行时能力已验证的证据。`Config/UAL/gateway/integrations.yaml` 中的 P1 / P2 外部服务保持 `disabled` / license-reviewed / Zero-VLM boundary，**not executed**；不接外部生成式服务。官方 MCP / Toolset 仍是 `Experimental + NoRedist`，loopback-only、无认证、不可随发布包分发、禁止暴露到非 loopback 地址。

## 17. C++ 源码级 hooks 编译验证（本地 UBT Build 已通过）

另一代理加入的 C++ source-level hooks（类型专属 handler / `UALDomainFeatureExtractor` / 生命周期钩子 / 事件重扫入口）与第三个生成头 `UALDomainProfiles.h` 已随 2026-11-27 本地 UE6 UBT Build 编译通过（`Result: Succeeded`，19 个 action、UHT 写入 4 个生成文件、输出 `UnrealEditor.exe`、仅 C4996 `UE_LOG` 弃用告警、无编译错误；完整命令与读数见 §1）。§1 列出的非编译类 UBT 检查（QueryTargets / Capabilities / AvailableTargets / EnabledPlugins / TargetDetails / CompileDetails `-SyntaxCheckOnly` / GenerateClangDatabase）与 22 个翻译单元的 MSVC `/Zs` 仅语法检查仍只是源码级证据。**CI 仍不编译 C++**，也没有加载 mesh / animation；`UEAssetLensTests` UE 自动化用例、headless 编辑器联调、真实 `.uasset` 写入 / 编辑器编译 / PIE 都需在具备工具链的宿主环境单独执行，仍为**规格 / 未落地**。编译通过不能写成「真实 UE 运行时已验证」或「所有规格已验证」。

## 边界之外：当前可用且由离线门禁覆盖的范围

以下能力由 `.github/workflows/python-ci.yml` 与 `Tools/run_tests.ps1` 安排执行；**2026-11-27 完整离线门禁已全部通过**（168 项 pytest、`ual-cli self-test` 17/17、`server.py --self-test`、`engine_bridge.py --self-test`、`gen_docs.py --check`、`gen_taxonomy.py --check`、`audit_tool_annotations.py --check`、`audit_engine_toolsets.py --check`、`mkdocs build --strict`、`bench_index.py --count 10000 --incremental` smoke、`setup_mcp.ps1 -DryRun -Transport Http`；100000 资产实测冷建库 2.727s / 未变增量 0.0796s）：

- DuckDB DDL 幂等执行、种子资产 / 边 / `extension_payload` 灌库、FTS 重建与 BM25 召回；
- 硬约束过滤（`duration` / `forward_cm` / `dominant_limb` / `domain_scope`）；
- 10 个既有只读工具 + 20 个 runtime tools（共 30 个工具） 的限定名、`accessMode` / `readOnlyHint` 与 TOON / YAML 输出；
- 余弦自连接查重、引用校验、Parquet 导出；
- `lint-policy` 的四级偏好、custom rules、allowlist review gating、有界 TOON 报告与报告优先 / `--strict` 退出码；
- TOON 转义往返、YAML / artifacts 确定性输出、16 维指纹数学（旋转不变性、零向量、NaN 防护）；
- `gen_docs.py --check`、`gen_taxonomy.py --check`、`audit_tool_annotations.py --check`、`audit_engine_toolsets.py --check`、`mkdocs build --strict`；
- `server.py --self-test`、`engine_bridge.py --self-test`、`bench_index.py --count 10000 --incremental`、
  `setup_mcp.ps1 -DryRun -Transport Http`。

状态口径与完整能力清单仍以 [项目定位与能力规格](positioning.md) 为唯一事实源；本页不把**规格 / 未落地**条目写成已验证能力。
