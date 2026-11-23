# 架构说明

本文说明 UAL 的数据从哪里来、存成什么、指纹如何计算，以及「零 VLM」红线在工程上的含义。字段契约以 `Server/schema/PROTOCOL.md` 与 `Server/schema/init.sql` 为准。

## 1. UE 侧采集层

采集层位于 C++ 插件 `Source/`（本仓库根即插件根），由四个部件组成，覆盖实时与离线两条路径：

| 部件 | 触发方式 | 职责 |
| --- | --- | --- |
| `FAssetRegistry` 监听 | 编辑器运行期新增 / 删除 / 更新 | 维护增量变更集合，不负责重活提取 |
| `UUALIndexBuilderCommandlet` | `-run=UALIndexBuilder`，无头命令行 | 按 `-Roots=` / `-IncludePlugins` 收集挂载根，逐个 `GetAssetsByPath(..., recursive)` 并按 `PackageName` 去重，调用提取器，产出 JSONL |
| `FUALExtractorRegistry` | 按 `AssetClass` 分派 | 优先级 100 的领域提取器优先，优先级 0 的通用兜底保底（不设类型白名单） |
| `FUniversalReflectionExtractor` | 任意 `AssetClass`，含未知类型 | 只读 `FAssetData.TagsAndValues`，剥离 `SM_/AM_/T_/MI_/BP_/WBP_` 前缀做分词，生成 `logical_id`、`taxonomy`、`tokens`、`content_hash` 与 16 维指纹；taxonomy 由共享规则表生成 |

写入通过 `IUALIndexSink` 抽象。本仓库提供可无第三方库编译的 `UALYamlIndexSink`（写 `.ual/staging/*.yaml`）；生产环境的 DuckDB C API Appender 实现需随 ThirdParty 提供，尚未附带。Python 侧 `ual-cli build --from-yaml` 负责灌库。

### 1.1 为什么能覆盖所有资产类型

覆盖能力由三点共同保证，任何一层出现白名单都会破坏它：

1. **扫描层不挑类型**：只对挂载根做路径过滤，不传 `ARFilter` 的 class 条件。`-Roots=` 可显式列出根；`-IncludePlugins` 追加所有含内容插件的挂载根（默认仍为 `/Game`，需显式开启才能纳入插件资产）。
2. **提取层兜底**：`FUALExtractorRegistry::FallbackExtractor` 是 `FUniversalReflectionExtractor`，`CanExtract()` 恒返回 `true`。领域提取器（Priority 100）只是增强，不是准入门槛。
3. **存储层不设枚举**：`assets.asset_class` 是 `VARCHAR`，`taxonomy` 是 `VARCHAR[]`；`ual_common/db.py` 在记录缺 taxonomy 时按规则补全，并在 `logical_id` 与已有记录冲突时追加确定性路径哈希，避免 `UNIQUE` 约束把新类型资产挡在门外。

`taxonomy` 的唯一事实源是 `Server/ual_common/taxonomy.yaml`：`ual_common/taxonomy.py` 直接读取它，`Tools/gen_taxonomy.py` 把它渲染成 `Source/UEAssetLensEditor/Public/UALTaxonomyRules.h`。两侧均为大小写敏感的有序子串匹配，未命中规则落入 `Misc` 族，但**仍然入索引**。新增 UE 资产类型族只需改 JSON 并重跑生成器，`--check` 负责阻止生成物漂移。

边界：这只覆盖元数据层。领域深度字段（顶点直方图、材质参数聚类等）仍由 M2 负责，见 `known-limitations.md` §3 / §14。

## 2. 存储层：DuckDB

唯一事实源是 `.ual/index.duckdb`。已在 **DuckDB 2.0.0** 实测：`FLOAT[3]`、`FLOAT[16]`、`VARCHAR[]`、`MAP(VARCHAR,VARCHAR)`、`PRAGMA create_fts_index`、`array_cosine_similarity()` 均可用。

### 2.1 C++ → Python 的 staging YAML 文档流（硬契约）

UE 侧 `UALYamlIndexSink` 不直接写 DuckDB，而是输出 `.ual/staging/<name>.yaml`：一个 YAML 文档、键名 snake_case、顶层恰好 16 个键，再由 `ual-cli build --from-yaml` upsert 入表。权威定义见 `Server/schema/PROTOCOL.md` §1。

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
- **JSONL 契约不含 `updated_at`**：`assets.updated_at` 由 Python 灌库侧的 `DEFAULT CURRENT_TIMESTAMP` 填充，C++ 侧不写入，以保证 YAML/TOON 输出不含时间戳、Git Diff 稳定。C++ `FUALAssetRecord` 中的同名字段仅供生产 DuckDB C API Appender 直接写入。

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
```

`v_search` 视图把 `domain_spec` 里的 `forward_cm` / `strike_window` / `hint` 投影为可过滤列，因此不必新增物理列即可支持 `filters.min_forward_cm` 等硬约束。

**FTS 是快照，不是实时索引**：DuckDB 的 `fts` 索引不会随 `INSERT` 自动更新。每次批量灌库后必须执行 `Server/schema/rebuild_fts.sql`（`drop_fts_index` + `create_fts_index`）；`ual-cli build` 已内置该步骤。若扩展不可用，`db.search_assets` 会降级为 `tokens ILIKE`，精度下降但结果不为空。

`MAP(VARCHAR,VARCHAR)` 会擦除数值类型。约定：标量写纯文本，向量写 `[a,b,c]`，列表/对象写 JSON 字符串；读取方按 `PROTOCOL.md` 的 key 契约解析。范围过滤只在 `v_search` 的投影列上做，不在 MAP 内做。

## 3. 16 维确定性指纹

实现见 `Source/UEAssetLens/Private/UALFingerprint.cpp` 与 Python 参考实现 `Server/ual_common/fingerprint.py`，两者维度语义一致：

| 资产族 | 1~3 维 | 中间维 | 末段 |
| --- | --- | --- | --- |
| Mesh | 归一化包围盒长宽比（3） | 顶点质心距离 8-bin 径向直方图（8） | 材质槽 / 插槽数特征（5） |
| Animation | 时长归一化 + RootMotion 方向余弦（4） | 四肢末端累计弧长占比（4） | 动能曲线 8-bin（8） |

所有维度来自几何、运动学或参数签名，不含像素、图像特征或模型嵌入。零除保护、NaN/Inf 过滤、长度补齐/截断在 C++ 与 Python 两侧都强制实现；缺失指纹写 `NULL` 而非全零向量（全零会让余弦退化）。

**抗旋转的边界**：径向直方图对刚体旋转与等比缩放不敏感，但**抗旋转 ≠ 形状唯一标识**。两个外观不同、统计分布接近的资产会有高相似度。实测种子数据里 `AM_Knight_HeavySlash_02` 与 `AM_Knight_SpinSweep_04` 的余弦达 **0.914**，原因是两者 RootMotion 方向同为 `[1,0,0]`、时长维度接近——这是方向余弦设计的真实误报，不是 bug。因此：

- 指纹只用于**候选粗排**，默认阈值 0.85 是召回取向，不是高置信判定；
- 阈值以上的候选必须用 `ual_inspect_asset` 精读 YAML 或人工确认；
- 不能当几何哈希、不能当资产唯一 ID（唯一 ID 始终是 `asset_path`）。

改进方向：把方向余弦拆成符号 + 幅度两维、增加动能峰值位置编码，或引入按 `asset_class` 分组后再比对的二级漏斗。

## 4. 增量构建与 `content_hash` 的用途边界

`ual-cli build --incremental` 读取库内 `asset_path → content_hash`，跳过哈希未变的记录；UE Commandlet 侧也支持 `-Incremental`。

| 字段 | 回答的问题 | 稳定性 |
| --- | --- | --- |
| `content_hash` | 「这条记录自上次构建以来是否变过？」 | 对字节敏感，语义不敏感 |
| `fingerprint_vec` | 「两份资产在语义上是否相近？」 | 对语义敏感，对重存不敏感 |

UE 的二进制重存（重新导入、引擎升级、Cook、序列化顺序变化）会改变字节哈希但语义不变，因此 **`content_hash` 严禁用于查重**。查重一律走 `fingerprint_vec` + 余弦相似度。

## 5. 「零 VLM」红线的工程含义

不调用视觉/多模态模型、不做渲染截图比对、不引入 CLIP/embedding、不依赖 GPU 或外部推理 API。由此得到：

- **确定性 / 可复现**：同一工程、同一提取器版本产出的 DuckDB / YAML / TOON 可逐字节一致，可进 Git Diff 审计；没有模型版本漂移。
- **可离线 / 可入 CI**：无网络、无 API Key；Commandlet 与两个门禁能在构建机上运行。
- **可解释**：每条语义都能追溯到某个反射属性、某条边或某个数学维度，误报可定位到具体维度。
- **代价**：语义上限由元数据质量决定。命名混乱、标签缺失、自定义属性未注册时语义会变薄；UAL 不会「看图补语义」。这是能力边界，不是缺陷掩饰。

## 6. 与官方 Unreal MCP 的集成

宿主源码树（UE6，`BranchName: UE6`）随附 Epic 第一方 `ModelContextProtocol` 插件（`FriendlyName: Unreal MCP`，`IsExperimentalVersion: true`，`EnabledByDefault: false`）。它在编辑器进程内提供 HTTP loopback server（`http://127.0.0.1:8000/mcp`），有完整的 `AddTool` / `OnRefreshTools` / ResourceProvider 扩展点，并可选通过 `ToolsetRegistry` 暴露数十个内置工具集。

UAL 用 **`IModelContextProtocolModule::AddTool`** 把 4 个工具直接注册进该 server，而不是自定义 `UToolsetDefinition`。原因是 `ModelContextProtocolToolsetRegistryAdapter` 会把工具名限定为 `<ToolsetName>.<ToolName>`；UAL 的 4 个名称是 `PROTOCOL.md` 之外的另一份对外契约（Python MCP Server、`tool_schemas.yaml`、生成文档三处共用），不能被改写。此外 4 个工具全部只读，不需要 Toolset 的权限分级与元工具发现机制。

调用链：

| 环节 | 职责 |
| --- | --- |
| `FUALEngineMcpTool` | `GetName` / `GetInputJsonSchema` / `GetAnnotations` / `RunAsync`；`RunAsync` 把调用丢到线程池 |
| `FUALEngineMcpBridge` | 写 UTF-8 请求文件 → `FPlatformProcess::ExecProcess` 拉起 Python → 按 UTF-8 显式解码响应文件 |
| `engine_bridge.py` | 白名单分派到 `ual_mcp/tools.py` 的 4 个函数，返回 TOON / YAML 文本 |
| `.ual/index.duckdb` | 只读，与 `ual-cli`、独立 Python MCP Server 共用同一份索引 |

`Server/ual_mcp/tool_schemas.yaml` 由 `Tools/gen_docs.py` 从 `TOOL_DEFS` 生成，是工具名、描述与 `inputSchema` 的唯一事实源；`--check` 同时保护它与 5 个生成页面，任何一侧漂移都会让 CI 失败。

这形成**双通道、单一索引**：编辑器开着时 Agent 走官方 HTTP server；CI、无头构建机与无编辑器环境仍走 `ual-cli` / 独立 Python MCP。官方 server 不能替代后者——它依赖编辑器进程、仅 loopback、无认证、标记为实验性，且无法在构建机上运行 `lint-duplicates` 或 `verify-references`。

边界与未验证项见 [已知限制](known-limitations.md)。
