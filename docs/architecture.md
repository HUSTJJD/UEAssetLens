# 架构说明

本文说明 UAL 的数据从哪里来、存成什么、指纹如何计算，以及「零 VLM」红线在工程上的含义。字段契约以 `Server/schema/PROTOCOL.md` v3.0 与 `Server/schema/init.sql` 为准；「UAL 是什么、完整能力、现在做到哪一步」的唯一事实源是 [项目定位与能力规格](positioning.md)，升级规格主文见 [资产全生命周期架构规格](asset-lifecycle-architecture.md)。本文只描述数据流与实现，不定义产品；状态使用 **已实现 / 规格 / 未落地**。

**当前计数**：DuckDB `schema_meta.schema_version` `3.0`（冻结 16 键 Core Record + 独立 `extension_payload`）；
`Server/ual_mcp/tool_schemas.yaml` `schema_version` `4.0`；10 个既有只读工具 + 20 个 registry-driven runtime tools（共 30 个工具）；
`ual-cli` 既有 5 个命令 + `self-test` + 22 个离线内核命令（新增 `read-artifact`、`import-gamespec`，见 [快速开始](quickstart.md) §2）；
100000 资产实测冷建库 **2.727s**、未变增量 **0.0796s**，两个目标均满足，见 [性能基准](benchmarks.md)。
2026-11-27 完整离线门禁已全部通过：168 项 pytest、`ual-cli self-test` 17/17、`server.py --self-test`、
`engine_bridge.py --self-test`、`gen_docs.py --check`、`gen_taxonomy.py --check`、
`audit_tool_annotations.py --check`、`audit_engine_toolsets.py --check`、`mkdocs build --strict`、
`bench_index.py --count 10000 --incremental` smoke、`setup_mcp.ps1 -DryRun -Transport Http`。
非编译类 UBT 检查（`-Mode=QueryTargets` / `Capabilities` / `AvailableTargets` / `EnabledPlugins` /
`TargetDetails -NoIntellisense` / `CompileDetails -Compiler=VisualStudio2022 -SyntaxCheckOnly` /
`GenerateClangDatabase -NoExecCodeGenActions -Compiler=VisualStudio2022`）与 22 个翻译单元的 MSVC `/Zs` 仅语法检查，
以及 2026-11-27 用户明确授权后的**本地 UE6 UBT Build 已通过**（`AnnEditor Win64 Development`，19 个 action、
UHT 写入 4 个生成文件、UBA 97.24 秒、`Result: Succeeded`、总耗时 136.42 秒、输出 `UnrealEditor.exe`，
仅 C4996 `UE_LOG` 弃用告警、无编译错误，见 [已知限制](known-limitations.md) §1）。
**CI 仍不编译 C++**；真实 UE 写入 / 编辑器编译 / PIE / UE 自动化用例仍为**规格 / 未落地**。以上不代表所有规格已验证。

## 1. UE 侧采集层

采集层位于 C++ 插件 `Source/`（本仓库根即插件根），由四个部件组成，覆盖实时与离线两条路径：

| 部件 | 触发方式 | 职责 | 状态 |
| --- | --- | --- | --- |
| `FAssetRegistry` 监听 | 编辑器运行期新增 / 删除 / 更新 | 维护增量变更集合；真实触发重扫的 hooks 为源码级 | **已实现（标记）** / 真实重扫 **规格** |
| `UUALIndexBuilderCommandlet` | `-run=UEAssetLensEditor.UALIndexBuilder`，无头命令行 | 按 `-Roots=` / `-IncludePlugins` 收集挂载根，逐个 `GetAssetsByPath(..., recursive)` 并按 `PackageName` 去重，调用提取器，产出冻结 16 键 YAML 文档流 | **已实现（离线 / 源码级）**；本地 UE6 UBT Build 已通过；真实 UE 端到端扫描仍 **规格** |
| `FUALExtractorRegistry` | 按 `AssetClass` 分派 | 优先级 100 的领域提取器优先，优先级 0 的通用兜底保底（不设类型白名单） | **已实现（源码级 / 历史实机）** |
| `FUniversalReflectionExtractor` | 任意 `AssetClass`，含未知类型 | 只读 `FAssetData.TagsAndValues`，剥离 `SM_/AM_/T_/MI_/BP_/WBP_` 前缀做分词，生成 `logical_id`、`taxonomy`、`tokens`、`content_hash` 与 16 维指纹 | **已实现** |

写入通过 `IUALIndexSink` 抽象。本仓库提供可无第三方库编译的 `UALYamlIndexSink`（写 `.ual/staging/*.yaml`）；生产环境的 DuckDB C API Appender 仍是源码级预留 / **规格**，其生产实现与端到端写入验证未落地（本地 UE6 UBT Build 已通过，不改变此状态）。Python 侧 `ual-cli build --from-yaml` 负责灌库，`fields.py` 把类型化扩展写入独立 `extension_payload` 表。

### 1.1 为什么能覆盖所有资产类型

覆盖能力由三点共同保证，任何一层出现白名单都会破坏它：

1. **扫描层不挑类型**：只对挂载根做路径过滤，不传 `ARFilter` 的 class 条件。`-Roots=` 可显式列出根；`-IncludePlugins` 追加所有含内容插件的挂载根（默认仍为 `/Game`，需显式开启才能纳入插件资产）。
2. **提取层兜底**：`FUALExtractorRegistry::FallbackExtractor` 是 `FUniversalReflectionExtractor`，`CanExtract()` 恒返回 `true`。领域提取器（Priority 100）只是增强，不是准入门槛。
3. **存储层不设枚举**：`assets.asset_class` 是 `VARCHAR`，`taxonomy` 是 `VARCHAR[]`；`ual_common/db.py` 在记录缺 taxonomy 时按规则补全，并在 `logical_id` 与已有记录冲突时追加确定性路径哈希，避免 `UNIQUE` 约束把新类型资产挡在门外。

`taxonomy` / M2 profile 的声明式事实源是 `Server/ual_common/taxonomy.yaml` 与 `Config/UAL/fields/default.yaml`：`ual_common/taxonomy.py` / `domain.py` 直接读取它们，`Tools/gen_taxonomy.py` 渲染 `UALTaxonomyRules.h`、`UALFeatureProfiles.h` 与 `UALDomainProfiles.h` 三个生成头。两侧均为大小写敏感的有序子串匹配，未命中规则落入 `Misc` 族，但**仍然入索引**。新增规则只需改声明式 YAML 并重跑生成器，`--check` 负责阻止三个生成头漂移；C++ 头与 `UALDomainFeatureExtractor` 为源码级，本地 UE6 UBT Build 已通过但 CI 仍不编译 C++，真实 UE 端到端反射级深度规格仍**规格**。

边界：这只覆盖元数据层。`Server/ual_common/domain.py` 与 `fields.py` 已实现 **Python M2 reference**（离线投影，不加载 UObject）；`UALDomainFeatureExtractor` / `UALDomainProfiles.h` 是 **C++ source-level zero-load domain extractor / third generated header**，不加载 mesh / animation。本地 UE6 UBT Build 已通过、CI 仍不编译 C++；真实 UE 反射级领域规格仍 **规格**，见 `known-limitations.md` §3 / §14 / §17。

## 2. 存储层：DuckDB v3.0

唯一事实源是 `.ual/index.duckdb`。已在 **DuckDB 2.0.0** 实测：`FLOAT[3]`、`FLOAT[16]`、`VARCHAR[]`、`MAP(VARCHAR,VARCHAR)`、`PRAGMA create_fts_index`、`array_cosine_similarity()` 均可用。

### 2.1 C++ → Python 的 staging YAML 文档流（冻结硬契约）

UE 侧 `UALYamlIndexSink` 不直接写 DuckDB，而是输出 `.ual/staging/<name>.yaml`：一个 YAML 文档、键名 snake_case、顶层恰好 16 个键，再由 `ual-cli build --from-yaml` upsert 入表。权威定义见 `Server/schema/PROTOCOL.md` v3.0 §1。**DuckDB v3.0 有意冻结这 16 键 Core Record**；类型化扩展进入独立 `extension_payload` 表，禁止新增第 17 个 staging 键，字段变更必须四处同步并 bump `schema_meta.schema_version`。

| 键 | 类型 | 说明 |
| --- | --- | --- |
| `asset_path` | string | 主键，资产唯一 ID |
| `logical_id` | string | `AssetClass:Name`；同名同类跨目录碰撞时由灌库侧追加确定性 `:8位路径哈希` |
| `asset_class` / `parent_class` | string | UClass 与父类 |
| `taxonomy` | string[] | 分类路径数组 |
| `tokens` | string | 空格连接的 VARCHAR（FTS 输入），见下方澄清 |
| `content_hash` | string | 仅增量变更检测，禁止查重 |
| `duration` | float | 时长（秒） |
| `bounds_size` | float[3] | 包围盒尺寸 |
| `skeleton_path` / `dominant_limb` | string | 骨架路径 / 主肢体 |
| `fingerprint_vec` | float[16] | 16 维指纹，缺失写 `NULL` |
| `interface_spec` / `domain_spec` | map<string,string> | 结构化载荷，值统一字符串 |
| `yaml_cache` | string | 预生成带注释的 YAML 规格 |
| `edges` | array | 元素结构见下 |

三个易错点：

- **`tokens` 是字符串，不是数组**。C++ 内部 `FUALAssetRecord.Tokens` 是 `TArray<FString>`，sink 用 `FString::Join` 拍平成空格分隔字符串，以匹配 FTS 的 `VARCHAR` 列；Python 灌库侧 `normalize` 兼容数组形式，但 canonical 存储是字符串。`taxonomy` 才是字符串数组。
- **边的 source 隐含在行内**：`edges` 元素仅含 `target_path` / `edge_type`（`DEPENDS_ON` | `CO_OCCURS`）/ `weight`，按 source 聚合，source 即该行 `asset_path`；灌库后展开为 `asset_edges(source_path, target_path, edge_type, weight)`。
- **staging YAML 契约不含 `updated_at`**：`assets.updated_at` 由 Python 灌库侧的 `DEFAULT CURRENT_TIMESTAMP` 填充，C++ 侧不写入，以保证 YAML / TOON 输出不含时间戳、Git Diff 稳定。

### 2.2 DuckDB 表结构

```sql
CREATE TABLE assets (
    asset_path      VARCHAR PRIMARY KEY,
    logical_id      VARCHAR NOT NULL UNIQUE,
    asset_class     VARCHAR NOT NULL,
    parent_class    VARCHAR NOT NULL,
    taxonomy        VARCHAR[],
    tokens          VARCHAR,                  -- FTS 输入
    content_hash    VARCHAR NOT NULL,        -- 仅用于增量变更检测
    duration        FLOAT DEFAULT 0.0,
    bounds_size     FLOAT[3],
    skeleton_path   VARCHAR,
    dominant_limb   VARCHAR,
    fingerprint_vec FLOAT[16],               -- 16 维确定性指纹
    interface_spec  MAP(VARCHAR, VARCHAR),
    domain_spec     MAP(VARCHAR, VARCHAR),
    yaml_cache      VARCHAR,                 -- 预生成带注释 YAML
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE asset_edges (
    source_path VARCHAR NOT NULL,
    target_path VARCHAR NOT NULL,
    edge_type   VARCHAR NOT NULL,            -- 'DEPENDS_ON' | 'CO_OCCURS'
    weight      FLOAT DEFAULT 1.0,
    PRIMARY KEY (source_path, target_path, edge_type)
);

CREATE TABLE extension_payload (
    asset_path   VARCHAR NOT NULL,
    namespace    VARCHAR NOT NULL,
    field_name   VARCHAR NOT NULL,
    value_type   VARCHAR NOT NULL,           -- string|integer|number|boolean|yaml
    value_text   VARCHAR,
    value_number DOUBLE,
    value_bool   BOOLEAN,
    provenance   VARCHAR NOT NULL,           -- builtin|plugin|project|user
    PRIMARY KEY (asset_path, namespace, field_name)
);
```

`v_search` 视图把 `domain_spec` 里的 `forward_cm` / `strike_window` / `hint` 投影为可过滤列，因此不必新增物理列即可支持 `filters.min_forward_cm` 等硬约束。

**FTS 是快照，不是实时索引**：DuckDB 的 `fts` 索引不会随 `INSERT` 自动更新。每次批量灌库后必须执行 `Server/schema/rebuild_fts.sql`（`drop_fts_index` + `create_fts_index`）；`ual-cli build` 已内置该步骤。若扩展不可用，`db.search_assets` 会降级为 `tokens ILIKE`，精度下降但结果不为空。

`MAP(VARCHAR,VARCHAR)` 会擦除数值类型。Core Record 约定：标量写纯文本，向量写 `[a,b,c]`，列表 / 对象写 YAML 文本片段；读取方按 `PROTOCOL.md` 的 key 契约解析。类型化字段由 `Config/UAL/fields/default.yaml` / `extensions/builtin.yaml` 声明，经 `fields.py` 写入 `extension_payload`，不再把数值挤进 MAP 字符串。所有规范工件一律走 `artifacts.py` canonical emitter；禁止手写 YAML / TOON 字符串拼接；输出不含本地绝对路径、时间戳、随机 ID。

## 3. 16 维确定性指纹

实现见 `Source/UEAssetLens/Private/UALFingerprint.cpp` 与 Python 参考实现 `Server/ual_common/fingerprint.py`，两者维度语义一致：

| 资产族 | 1~3 维 | 中间维 | 末段 |
| --- | --- | --- | --- |
| Mesh | 归一化包围盒长宽比（3） | 顶点质心距离 8-bin 径向直方图（8） | 材质槽 / 插槽数特征（5） |
| Animation | 时长归一化 + RootMotion 方向余弦（4） | 四肢末端累计弧长占比（4） | 动能曲线 8-bin（8） |

所有维度来自几何、运动学或参数签名，不含像素、图像特征、模型嵌入或外部生成式服务。零除保护、NaN / Inf 过滤、长度补齐 / 截断在 C++ 与 Python 两侧都强制实现；缺失指纹写 `NULL` 而非全零向量（全零会让余弦退化）。

**抗旋转的边界**：径向直方图对刚体旋转与等比缩放不敏感，但**抗旋转 ≠ 形状唯一标识**。两个外观不同、统计分布接近的资产会有高相似度。实测种子数据里 `AM_Knight_HeavySlash_02` 与 `AM_Knight_SpinSweep_04` 的余弦达 **0.914**，原因是两者 RootMotion 方向同为 `[1,0,0]`、时长维度接近——这是方向余弦设计的真实误报，不是 bug。因此：

- 指纹只用于**候选粗排**，默认阈值 0.85 是召回取向，不是高置信判定；
- 阈值以上的候选必须用 `UEAssetLens.AssetSemanticToolset.InspectAsset` 精读 YAML、查 review-gated allowlist 或人工确认；
- 不能当几何哈希、不能当资产唯一 ID（唯一 ID 始终是 `asset_path`）；
- `content_hash` 严禁用于查重。

## 4. 增量构建与 `content_hash` 的用途边界

`ual-cli build --incremental` 读取库内 `asset_path → content_hash`，跳过哈希未变的记录；UE Commandlet 侧也支持 `-Incremental`。

| 字段 | 回答的问题 | 稳定性 |
| --- | --- | --- |
| `content_hash` | 「这条记录自上次构建以来是否变过？」 | 对字节敏感，语义不敏感 |
| `fingerprint_vec` | 「两份资产在语义上是否相近？」 | 对语义敏感，对重存不敏感 |

UE 的二进制重存（重新导入、引擎升级、Cook、序列化顺序变化）会改变字节哈希但语义不变，因此 **`content_hash` 严禁用于查重**。查重一律走 `fingerprint_vec` + 余弦相似度。`Tools/bench_index.py --count 100000 --incremental` 使用 SQL `range(?)` 在 DuckDB 内生成确定性合成资产，冷建库 2.727s、未变增量 0.0796s，两个目标均满足；该数字不是 C++ 编译或 PIE 验证。

## 5. 「零 VLM」红线的工程含义

不调用视觉 / 多模态模型、外部生成式服务、CLIP / embedding、云端推理、GPU 或外部 API。由此得到：

- **确定性 / 可复现**：同一工程、同一提取器版本产出的 DuckDB / YAML / TOON 可逐字节一致，可进 Git Diff 审计；没有模型版本漂移。
- **可离线 / 可入 CI**：无网络、无 API Key；Commandlet、三条门禁、离线内核命令、生成漂移检查、annotation / engine toolset audits 与基准能在构建机上运行。
- **可解释**：每条语义都能追溯到某个反射属性、某条边、某个数学维度或声明式字段。
- **代价**：语义上限由元数据质量决定。命名混乱、标签缺失、自定义属性未注册时语义会变薄；UAL 不会「看图补语义」。
- **边界**：`Config/UAL/gateway/integrations.yaml` 中的 P1 / P2 外部服务保持 `disabled` / license-reviewed / Zero-VLM boundary，**not executed**；不接外部生成式服务，不执行真实 UE compile / PIE。

## 6. 与官方 Unreal MCP 的集成

宿主源码树（UE6，`BranchName: UE6`）随附 Epic 第一方 `ModelContextProtocol` 插件（`FriendlyName: Unreal MCP`，`IsExperimentalVersion: true`，`EnabledByDefault: false`）。它在编辑器进程内提供 HTTP loopback server（`http://localhost:8000/mcp`），有完整的 `AddTool` / `OnRefreshTools` / ResourceProvider 扩展点，并可选通过 `ToolsetRegistry` 暴露数十个内置工具集。

UAL 用 **`IModelContextProtocolModule::AddTool`** 把限定名工具直接注册进该 server，而不是自定义 `UToolsetDefinition`。阶段 2 `UToolsetDefinition` 迁移 **DECIDED AGAINST for now**：避免 ToolsetRegistry 自动拼名破坏与 Python 侧共享的 1:1 契约，也不引入双轨别名。既有 10 个工具名、描述、`inputSchema` 仍来自 `tools.py::TOOL_DEFS`；20 个 runtime tools（共 30 个工具） 来自 `Config/UAL/extensions/tools.yaml` 的 `ExtensionRegistry.snapshot()`。`Tools/gen_docs.py` 合并两者生成 `tool_schemas.yaml` v4.0 与 `docs/reference/mcp-tools.md`；`Tools/audit_tool_annotations.py --check` 校验限定名 / `accessMode` / `readOnlyHint`，`Tools/audit_engine_toolsets.py --check` 校验离线 `tools/list` inventory。DuckDB `schema_meta.schema_version` `3.0` 与 tool schema `4.0` 互不联动。

调用链：

| 环节 | 职责 |
| --- | --- |
| `FUALEngineMcpTool` | `GetName` / `GetInputJsonSchema` / `GetAnnotations` / `RunAsync`；`RunAsync` 把调用丢到线程池 |
| `FUALEngineMcpBridge` | 写 UTF-8 请求文件 → `FPlatformProcess::ExecProcess` 拉起 Python → 按 UTF-8 显式解码响应文件 |
| `engine_bridge.py` | 按限定名分派到工具实现，返回 TOON / YAML 文本 |
| `.ual/index.duckdb` | 只读，与 `ual-cli`、独立 Python MCP Server 共用同一份索引 |

这形成**双通道、单一索引**：编辑器开着时 Agent 走官方 HTTP server；CI、无头构建机与无编辑器环境仍走 `ual-cli` / 独立 Python MCP。官方 server 不能替代后者——它依赖编辑器进程、仅 loopback、无认证、标记为 `Experimental + NoRedist`，且无法在构建机上运行 `lint-duplicates` / `lint-policy` / `verify-references`。客户端 HTTP 配置可用 `Tools/setup_mcp.ps1 -DryRun -Transport Http -ConfigRoot <TempConfigRoot>` 预览（2026-11-27 DryRun 已通过），
官方 server 仅 loopback 且无认证，禁止暴露到非 loopback 地址，不可随发布包分发。

## 7. 离线内核、写入边界与命令

`Server/ual_common/extensions.py` / `fields.py` / `domain.py` / `artifacts.py` / `transaction.py` / `gateway.py` / `asset_diff.py` / `gamespec.py` / `workflow.py` / `lifecycle.py` / `revision.py` / `allowlist.py` 组成 Phase 1–5 的**离线内核**：**已实现（离线）**。它们产出 canonical plan / diff / GameSpec / audit / rescan 工件与有界 TOON，不写 `.uasset`、不写 DuckDB 索引、不调用真实 UE。

MCP DuckDB 连接永远 `read_only=True`；`Write` / `Execute` runtime tools 只做 dry-run、审批、inverse、verify、rollback、rescan request / apply-events。本地 UE6 UBT Build 已通过（`Result: Succeeded`），但 **CI 仍不编译 C++**；真实 UE host executor、真实写入、编辑器编译 / PIE、UE 自动化用例、AssetRegistry 事件重扫均**规格 / 未落地**。

22 个 `ual-cli` 离线命令覆盖扩展 / 字段 / 装配 / 白名单 / diff / 事务 / GameSpec / workflow / rescan / events / 工件分页 / GameSpec 导入，详见 [快速开始](quickstart.md) §2 与 [离线内核实现](kernel-implementation.md)。`python-ci.yml` / `Tools/run_tests.ps1` 安排 `gen_docs.py --check`、`gen_taxonomy.py --check`、`audit_tool_annotations.py --check`、`audit_engine_toolsets.py --check`、pytest、self-tests、10000 资产基准和 HTTP DryRun；2026-11-27 完整离线门禁（168 项 pytest、`ual-cli self-test` 17/17、`server.py --self-test`、`engine_bridge.py --self-test`、`mkdocs build --strict`、benchmark smoke、HTTP DryRun）已全部通过。
干净的纯真实工程索引已从 staging 重建到 `.ual/real_project.duckdb` 并验证 PASS（15 资产 / 56 边 / 75 行 `extension_payload`，`schema_version` `3.0`、`engine_version` `UE6`）；现有 `.ual/index.duckdb` 为 seed + 真实数据混合产物（24 资产 / 61 边），不得引用为干净的纯真实工程全量索引。以上均为离线 / 源码级结论，不代表所有规格已验证。

边界与未验证项见 [已知限制](known-limitations.md)；完整能力状态仍以 [项目定位](positioning.md) 为唯一事实源。
