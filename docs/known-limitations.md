# 已知限制

本页如实列出 UAL 当前实现边界，不把「设计目标」写成「已验证结论」。条目随 M1~M4 里程碑推进更新。

| # | 限制 | 关联里程碑 | 当前状态 |
| --- | --- | --- | --- |
| 1 | UE C++ 侧需本地 UE5 SDK 编译 | M1 | 源码随仓，未在 CI 中编译验证 |
| 2 | DuckDB C API Appender 生产实现未附带 | M1 | 提供 JSONL Sink + Python upsert；Appender 未落地 |
| 3 | 领域提取器未全量实现 | M2 | 指纹数学与 YAML 生成器已就绪，UE 反射深度提取待补 |
| 4 | TOON 为自定义方言 | M3 | 已有正式协议与转义规则、往返解析测试；非社区标准 |
| 5 | FTS 索引是快照且需要 `fts` 扩展 | M1 | 灌库后强制重建；扩展不可用时降级 `ILIKE` |
| 6 | `MAP(VARCHAR,VARCHAR)` 丢失数值类型 | M1 | 序列化约定已固化在 `PROTOCOL.md`，读取端按 key 解析 |
| 7 | 10 万级冷建库 <30s / 增量 <1s | M1 | 设计目标，**无实测基准** |
| 8 | 16 维指纹存在方向余弦主导的假阳性 | M2 | 种子数据实测 0.914 误报，需人工复核 |
| 9 | `verify-references` 是正则启发式 | M4 | 非 UE 反射级解析，存在动态资产/retarget 误报 |
| 10 | 白名单机制 | M4 | 仅文档约定，**尚未实现** |
| 11 | 引擎内 MCP 依赖 UE6 实验性插件 | M5 | C++ 模块实机编译**零警告通过**；编辑器内 `tools/list` 运行时联调仍待办，见 §11 |
| 12 | 每次工具调用启动一个 Python 进程 | M5 | 百毫秒级延迟；已用 `RunAsync` 避免阻塞 game thread，见 §12 |
| 13 | Python MCP Server 硬依赖官方 SDK 2.x | M3 | 无手写协议降级；SDK 缺失即报错退出，离线环境走 `ual-cli`，见 §13 |
| 14 | 全类型覆盖改造的 C++ 部分未经编译验证 | M1+ | pytest 44 项、CLI self-test 通过；C++ 仅静态审查，需本地 UE6 SDK 编译，见 §14 |

---

## 1. UE C++ 侧需本地 UE5 SDK 编译

`Source/` 中的插件必须链接本机对应版本 UE5 引擎才能编译；仓库只提供源码，CI 只覆盖 Python 侧（pytest + self-test）。贡献者需手动用 UnrealBuildTool 编译并运行 `UEAssetLensTests` 自动化用例。C++ 代码刻意不 `#include <duckdb.h>`，因此可在无 ThirdParty 库的环境下编译验证。

## 2. DuckDB C API Appender 未附带

生产写入路径设计为 C++ Appender 高速列式写入；当前随仓的是 `UALYamlIndexSink` → Python `ingest_records()` 参数化 upsert。正确性优先，吞吐与冷建库耗时未压到目标区间。`IUALIndexSink` 已留好替换点。

## 3. 领域提取器 M2 未全量实现

`FUniversalReflectionExtractor` 覆盖 100% UObject，但 StaticMesh 顶点径向直方图、AnimMontage 动能相位切分、Material 参数聚类、Blueprint/DataTable 领域字段的 UE 反射代码尚未全量落地。Python 侧 `fingerprint.py` 与 `yamlspec.py` 已实现对应算法，种子数据即用它们生成，因此服务端链路可完整验证；UE 端到端产出的真实资产规格仍需补完 M2。

**注意区分两个概念**：「覆盖所有资产类型」指任何 `AssetClass` 都能入索引并产出通用记录（由通用兜底提取器保证）；「每类资产都有同等深度的领域规格」依赖 M2，目前仍未实现。详见 §14。

## 4. TOON 为自定义方言

TOON 不是社区标准。`Server/schema/PROTOCOL.md` 已定义语法、转义（逗号/双引号/换行触发包裹，冒号不转义）与四个工具的字段顺序；`ual_common/toon.py` 是唯一编解码实现，`test_toon_roundtrip_with_escaping` 覆盖含逗号与引号的值。跨版本升级仍需同步生成端与消费端。

## 5. FTS 索引是快照

DuckDB FTS 不随 `INSERT` 自动更新。`ual-cli build` 每次灌库后调用 `rebuild_fts()`（`drop_fts_index` + `create_fts_index`）。若 `fts` 扩展在受限环境不可用，`db.search_assets()` 降级为 `tokens ILIKE`，BM25 排序消失、召回精度下降。

## 6. `MAP(VARCHAR,VARCHAR)` 丢失数值类型

`interface_spec` / `domain_spec` 的值统一为字符串。约定已固化：标量写纯文本，向量写 `[a,b,c]`，列表/对象写 JSON 字符串；数值范围过滤只在 `v_search` 的 `TRY_CAST` 投影列（`forward_cm`）上做，避免字典序失真。新增字段必须同步 `PROTOCOL.md`。

## 7. 性能目标未实测

「10 万级资产冷建库 <30s、日常增量 <1s」是规格的设计目标，本仓库没有基准脚本、没有在不同硬件/引擎版本/资产规模下的测量数据。**不能作为已验证的性能承诺引用**。补基准时需固定资产集，并记录冷建库、增量构建、查询延迟三组数据。

## 8. 指纹假阳性

实测种子库：`AM_Knight_HeavySlash_02` 与 `AM_Knight_HeavySlash_03` 相似度 0.9999（真重复），但 `AM_Knight_HeavySlash_02` 与 `AM_Knight_SpinSweep_04` 也达 0.914（假阳性）。原因是所有 RootMotion 均沿 +X，方向余弦 3 维完全相同，叠加时长维度接近。默认阈值 0.85 是**召回取向**，命中后必须 `UEAssetLens.AssetSemanticToolset.InspectAsset` 精读 YAML 或人工确认。改进方向见 [架构说明](architecture.md) §3。

## 9. `verify-references` 是静态启发式

正则提取 `/Game/...`、`skeleton:`、`User.<Param>` 与 `Set*ParameterValue`，不做 Lua/YAML AST 解析，也不查询 UE 反射。运行期动态生成的资产、跨骨架 retarget、代码动态创建的材质参数都会被误报。它的定位是拦截 AI 幻觉提交，不是引用完整性证明。

## 10. 白名单机制尚未实现

`docs/ci-gates.md` §4 给出了白名单文件约定，但当前 CLI 只做到「报告 + 非零退出」，没有读取白名单、没有过期校验、没有 code review 集成。落地前请勿依赖该机制绕过门禁。

---

## 11. 引擎内 MCP 编译已验证，运行时联调未闭环

**现象**：`Source/UEAssetLensMCP/` 按 UE6 源码树里 `ModelContextProtocol` 插件的公开头文件编写，**C++ 模块已实机编译通过**；尚未完成编辑器内的端到端联调（启动编辑器 → `tools/list` 出现 4 个工具 → 调用返回 TOON/YAML）。

本地尝试过三次 UBT 编译，前两次失败原因都**不在本次新增代码**：

| 尝试 | 阻塞 |
| --- | --- |
| `AnnEditor Win64 Development -Project=Ann.uproject` | `Ann.Target.cs` / `AnnEditor.Target.cs` 等 4 个文件引用 `EngineIncludeOrderVersion.Unreal5_5`，UE6 已移除该枚举 → `Failed (RulesError)` |
| 临时工程 + `AdditionalPluginDirectories=Ann/Plugins` | 扫描到 `ThirdParty/UnLua`，其 `Lua.Build.cs` 触发 CMake，`cmake_minimum_required` 版本过旧 → `Failed (RulesError)` |
| 仅含 UAL 的隔离工程 + `-Module=UEAssetLensMCP` | **成功**：`Result: Succeeded`，编译并链接出 `UnrealEditor-UEAssetLensMCP.dll`；首轮有 7 条 `UE_LOG` 弃用警告，改用 UE6 的 `UE_LOGF` + `%ls` 后复编译**零警告** |

隔离工程用 `tar` 复制插件源码、只放 UAL 一个插件，绕开了 `Ann` 的 Target 规则与 `ThirdParty/UnLua` 的 CMake 问题。编译产物落在隔离工程的 `Binaries/`，未污染引擎目录。

但这也说明一个独立事实：**宿主工程 `Ann` 自身仍带 UE5.5 时代的 Target 规则**，`Intermediate/Build` 缓存的 `EngineVersion 5.6.0` 与源码树 `UE6` 不一致。在 `Ann` 完成 UE6 适配前，官方 Unreal MCP 能否在该工程启用需要用户确认。

**当前缓解**：`engine_bridge.py --self-test`、pytest、CLI 自检、文档漂移门禁与严格站点构建都不依赖 UE SDK，可独立验证 Python 侧全链路；C++ 侧保持最小 API 面（`AddTool` / `OnRefreshTools` / `MakeTextResult`），并把 `ModelContextProtocol` 声明为 `Optional` 插件依赖——插件未启用时模块不注册工具，不影响 UAL 其余功能。

**待办**：① 让 `Ann` 的 `*.Target.cs` 适配 UE6 后编译整个 `AnnEditor` target；② 在编辑器里确认 `ModelContextProtocol` 已启用；③ 用 MCP Inspector 对 `http://127.0.0.1:8000/mcp` 做 `tools/list` 与 4 次 `tools/call`。

## 12. 引擎内工具每次调用启动一个 Python 进程

**现象**：`FUALEngineMcpBridge::CallTool` 用 `FPlatformProcess::ExecProcess` 拉起 `engine_bridge.py`，进程退出后读取 UTF-8 响应文件。一次调用包含 Python 解释器启动、DuckDB 打开与索引查询，延迟在百毫秒量级。

**影响**：交互式 Ask 的体感延迟高于常驻进程方案；高频批量调用（例如一次会话几十次检索）成本明显。

**当前缓解**：`FUALEngineMcpTool::RunAsync` 把调用放到线程池，不阻塞编辑器 game thread；请求/响应走文件而非 stdout，保证中文语义注释不乱码。替换点集中在 `FUALEngineMcpBridge`，未来可改为常驻 Python 服务或 DuckDB C API，不影响工具 schema 与上层契约。

## 13. Python MCP Server 硬依赖官方 SDK 2.x

**现象**：`Server/ual_mcp/server.py` 只从 `mcp.server.mcpserver` 导入 `MCPServer`，`requirements.txt` 固定为 `mcp>=2,<3`。仓库早期曾附带一个手写的 `stdio_server.py`（仅实现 `initialize` / `ping` / `tools/list` / `tools/call`，协议版本写死 `2024-11-05`），现已删除。

**影响**：未安装或安装了 1.x SDK 的环境无法启动 Python MCP Server，进程打印可读错误并以退出码 2 结束。离线/受限环境不再有"能跑但语义不完整"的第三条路径。

**决策依据**：手写实现缺少 resources / prompts / 分页 / 协议版本协商，而官方 SDK v2 已能同时服务 2025 与 2026 两代协议修订；维护第三套协议语义的收益低于风险。工具名、描述与 `inputSchema` 的唯一事实源已迁至 `tools.py::TOOL_DEFS`，由 `gen_docs.py` 生成 `tool_schemas.yaml` 供 C++ 引擎内工具使用，并校验与 `server.py` 注册函数一致。

**当前缓解**：无 SDK 的离线/CI 场景本来就走 `ual-cli`（`build` / `lint-duplicates` / `verify-references` / `export-parquet`），不依赖 MCP；`ual_mcp/server.py --self-test` 也不导入 SDK，可在纯 DuckDB 环境验证 4 个工具的输出。

## 14. 全类型覆盖的实际边界（2026-11-16）

**类型无关性已成立**：`UALIndexBuilderCommandlet` 不按 `AssetClass` 过滤；`FUALExtractorRegistry` 内置 Priority 0 的 `FUniversalReflectionExtractor`，其 `CanExtract()` 恒为 `true`；`init.sql` 的 `asset_class` 是无枚举约束的 `VARCHAR`。因此新增任何 UObject 派生类（含插件自定义类型、新版引擎类型）都无需改代码即可入索引。

本次补齐的三处实现：

| 项 | 改动 | 验证状态 |
| --- | --- | --- |
| 扫描范围 | Commandlet 新增 `-Roots=/Game,/PluginA` 与 `-IncludePlugins`（自动纳入所有含内容插件的挂载根），按 `PackageName` 去重 | Python 侧无对应运行时；**C++ 未编译验证** |
| taxonomy | 硬编码 10 族改为 `Server/ual_common/taxonomy.yaml`（24 族）单一事实源；Python `ual_common/taxonomy.py` 与生成头 `UALTaxonomyRules.h` 共用；未命中落 `Misc` | pytest 覆盖 27 种类型 + `Tools/gen_taxonomy.py --check` 漂移门禁 |
| `logical_id` 碰撞 | `ual_common/db.py` 在冲突且 `asset_path` 不同时追加确定性 8 字符路径哈希 | pytest 覆盖重复灌库的幂等性 |

**仍然存在的限制**：

1. **默认扫描根仍是 `/Game`**。插件内容不会自动进入索引，必须显式传 `-Roots=...` 或 `-IncludePlugins`。当前 Ann 工程 `Content/` 下 13 个 `.uasset`、`Plugins/` 下 205 个，不显式配置就会漏掉约 94%。
2. **零加载原则不变**：只读取 `FAssetData` 元数据与 Tag-Value，`UAL_ENABLE_CDO_REFLECTION` 默认关闭；覆盖的是元数据层，不含顶点、材质图、蓝图图表等资产内部结构。
3. **C++ 改动未在 CI 编译**：`Ann` 的 `*.Target.cs` 仍引用 UE6 已移除的 `EngineIncludeOrderVersion.Unreal5_5`（见 §11），无法整体验证 `AnnEditor` target。生成头、扫描根与插件挂载 API 均按 UE6 源码树静态核对，但需本地 UE6 SDK 实机编译确认。
4. **现有 `.ual/index.duckdb` 仍是 9 条种子数据／6 种类型**，不是真实工程索引；要得到全量结果需先用新参数重跑 Commandlet 并灌库。

## 边界之外：当前可用且已验证的部分

以下能力在 **2026-11-16** 已通过 `pytest`（44 项，含 `test_type_coverage.py` 的 27 类分类与灌库回归）、`ual_cli self-test` 端到端验证：

- DuckDB DDL 幂等执行、9 条资产 / 5 条边灌库、FTS 重建与 BM25 召回；
- 硬约束过滤（`duration` / `forward_cm` / `dominant_limb` / `domain_scope`）；
- 4 个 MCP 工具的 TOON / YAML 输出与字段顺序；
- 余弦自连接查重（非零退出）、引用校验（非零退出）、Parquet 导出；
- TOON 转义往返、YAML 确定性输出、16 维指纹数学（旋转不变性、零向量、NaN 防护）。
