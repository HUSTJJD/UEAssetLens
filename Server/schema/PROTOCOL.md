# UAL 三层数据格式协议 v3.0

本文档是消费者之间的**唯一契约**。C++ 写入侧（UE 插件）、Python 读取侧（MCP Server / CLI）与 CI 门禁都必须遵守。
v3.0 **冻结 16 键 Core Record**，新增独立的 `extension_payload` 类型化扩展表、运行时工具注册契约与有界工件协议；
`schema_meta.schema_version` 为 `3.0`，`Server/ual_mcp/tool_schemas.yaml` 的 `schema_version` 为 `4.0`（两套契约互不联动）。
定位表述以 `docs/positioning.md` 为唯一事实源；本文只定义数据格式。

## 0. 角色分工（铁律）

| 层 | 格式 | 消费者 | 禁止事项 |
|---|---|---|---|
| L1 存储 | DuckDB `.ual/index.duckdb` | `ual-cli`、`ual_mcp` | 禁止把整库 JSON 导出给 LLM |
| L2 精读 | YAML `*.spec.yaml` / 工件 YAML | `UEAssetLens.AssetSemanticToolset.InspectAsset`、Git Diff、Code Review | 禁止用于批量候选列表 |
| L3 批量 | TOON | MCP 批量工具、CLI 报告 | 禁止逐条输出完整 YAML |

所有产物零 VLM、零云端推理、零外部 GPU；禁止本地绝对路径、时间戳、随机 ID 进入 YAML / TOON 输出；
批量路径只走 `ual_common/toon.py`，单资产精读只走 `ual_common/yamlspec.py`，工件 YAML 只走
`ual_common/artifacts.py::canonical_yaml`（`yaml.safe_dump` 的唯一封装）。禁止在别处手写 TOON / YAML 字符串拼接。

## 1. L1：DuckDB

DDL 见 `init.sql`。要点：

- `fingerprint_vec FLOAT[16]` 固定 16 维，缺失值写 `NULL`（不写全零，全零会导致余弦相似度退化）。
- `content_hash` **只用于增量变更检测**，不用于查重。`.uasset` 重存会改变二进制哈希但语义未变。
- `interface_spec` / `domain_spec` 是 `MAP(VARCHAR,VARCHAR)`，数值类型被擦除。约定：标量写纯文本，向量写 `[a,b,c]`，列表/对象写 **YAML 文本片段**（如 `"[hand_r_weapon]"`、`"{windup: [0.0, 0.35]}"`），不再是 JSON 字符串。
- FTS 索引是快照，`INSERT` 不自动维护；每次批量灌库后必须执行 `rebuild_fts.sql`。
- `array_cosine_similarity` 需要 DuckDB ≥ 1.1；已在 2.0.0 实测通过。
- `schema_meta` 含 `schema_version=3.0`、`fingerprint_dims=16`、`engine_version=UE6`。
- `index_revision` 是 `schema_meta` + 有序资产路径 / `content_hash` + 有序边 + 有序扩展字段的确定性哈希；
  它只用于 diff / plan / GameSpec 的版本锚点，不参与查重，也不读取 `updated_at`。

### 1.1 C++ → Python 的 16 键 YAML 交换契约（冻结）

UE 侧 `UALYamlIndexSink` 写入 `.ual/staging/*.yaml`，`---` 分隔的多文档流，**一个文档一个资产**。键名严格如下
（snake_case，缺字段按默认值补齐）。C++ 用 `FJsonObject` 作内存 DOM，经 `UEYaml.h` 的 emitter 落盘；
所有字符串显式双引号，含 `:` / `#` / `/` / `(` / `[` / `"` 的值必须加引号。

```yaml
---
"asset_path": "/Game/Characters/Knight/Anim/AM_Knight_HeavySlash_02"
"logical_id": "AnimMontage:AM_Knight_HeavySlash_02"
"asset_class": "AnimMontage"
"parent_class": "AnimSequenceBase"
"taxonomy": ["Asset", "Animation", "Montage"]
"tokens": "knight heavy slash combat"
"content_hash": "sha1:..."
"duration": 1.15
"bounds_size": [0.0, 0.0, 0.0]
"skeleton_path": "/Game/Characters/Knight/SK_Knight"
"dominant_limb": "hand_r"
"fingerprint_vec": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
"interface_spec":
  "skeleton": "/Game/Characters/Knight/SK_Knight"
  "sockets_referenced": "[hand_r_weapon]"
"domain_spec":
  "duration": "1.15"
  "forward_cm": "185.0"
  "phases": "{windup: [0.0, 0.35]}"
"yaml_cache": "# UEAssetLens AssetSpec v2.0 ..."
"edges": [{"target_path": "/Game/VFX/NS_Slash_Heavy", "edge_type": "CO_OCCURS", "weight": 0.88}]
```

注意区分两层：**staging YAML 文档流是交换格式（16 个键，不含 `updated_at`）**，DuckDB `assets` 表才是持久化结构。
`updated_at` 由灌库侧的 `DEFAULT CURRENT_TIMESTAMP` 填充，C++ 侧不写入，以保证 YAML/TOON 输出不含时间戳、Git Diff 稳定。
YAML 缩进敏感：单个文档语法/缩进错误会让整批灌库失败，`-Incremental` 发现哈希数异常偏少时应回退全量重建。

`ual-cli build --from-yaml <path>` 负责 upsert（`ON CONFLICT ... DO UPDATE`）、写入 `asset_edges`、重建 FTS 索引。
`ual-cli apply-events` 读取事件侧的 `.ual/staging/events.yaml`（同样是 16 键文档流）与
`.ual/staging/removed.yaml`（`asset_path` + `change_kind`），由 CLI 写入索引；MCP 工具侧永不写索引。

`domain_spec` 中约定以下键供检索层直接投影（不新增物理列，保持 DDL 与规格 §3.1 一致）：

| 键 | 值格式 | 用途 |
|---|---|---|
| `forward_cm` | 纯数字字符串，如 `"185.0"` | `filters.min_forward_cm` / `max_forward_cm` 硬约束 |
| `strike_window` | `"0.35..0.58"` | TOON `strike_window` 字段 |
| `root_motion_delta` | YAML 向量文本 `"[185.0, 0.0, 0.0]"` | YAML 运动学位移 |
| `phases` | YAML 文本片段（流式映射） | YAML 三相位区间 |
| `notifies` | YAML 文本片段（流式列表） | YAML Notify 列表 |
| `hint` | `"Once\|1.2s\|User.SlashColor"` | TOON `hint` 字段 |

`init.sql` 中的 `v_search` 视图用 `TRY_CAST(domain_spec['forward_cm'] AS DOUBLE)` 把这些键投影为可过滤列。
`feature.*` 键由 C++ `FUALTypedFeatureExtractor`（Priority 100）从 `FAssetData.TagsAndValues` 与确定性数学产出，
写入既有 `domain_spec` MAP，**不新增列、不改 16 键交换契约**。profile / capability / prefix 规则由
`Server/ual_common/taxonomy.yaml` 生成 `UALFeatureProfiles.h`，禁止再在 C++ 里维护匿名 `static const` 表。

### 1.2 独立类型化扩展表 `extension_payload`（v3.0）

Phase 1 刻意不把自定义字段塞进 16 键 Core Record，也不把 JSON 列塞回 `assets`：

```sql
CREATE TABLE IF NOT EXISTS extension_payload (
    asset_path   VARCHAR NOT NULL,
    namespace    VARCHAR NOT NULL,
    field_name   VARCHAR NOT NULL,
    value_type   VARCHAR NOT NULL,            -- string|integer|number|boolean|yaml
    value_text   VARCHAR,
    value_number DOUBLE,
    value_bool   BOOLEAN,
    provenance   VARCHAR NOT NULL,           -- builtin|plugin|project|user
    PRIMARY KEY (asset_path, namespace, field_name)
);
```

字段 spec 来自 `Config/UAL/extensions/*.yaml` 与 `Config/UAL/fields/default.yaml`，按
`namespace + field_name` 唯一注册；`ual-cli build` / `apply-events` 调 `fields.materialize()` 投影既有
`domain_spec` / `interface_spec`，不加载 UObject。C++ 侧 `FUALExtensionField` / `FUALFieldRegistry`
与 Python `db.EXTENSION_PAYLOAD_COLUMNS` 是同一契约；任何字段变更必须同步四处并递增
`schema_meta.schema_version`：`Server/schema/PROTOCOL.md`、`Server/schema/init.sql`、
`Source/UEAssetLens/Public/UALTypes.h`、`Server/ual_common/db.py`。

可选的 `-EmitExtensions` 写出 `.ual/staging/extensions.yaml`，是**独立文档流**，不是 16 键 staging 文档；
默认关闭以免 `ual-cli build` 的多文档 glob 混入非 16 键记录。

## 2. L2：YAML 单资产规格与工件

`UEAssetLens.AssetSemanticToolset.InspectAsset` 直接返回 `assets.yaml_cache`。生成器必须：

1. 使用 `identity` / `interface_contract` / `domain_spec` / `graph_context` 四个顶层键。
2. 每个非显然字段附 `#` 语义注释（单位、推断依据、建议对齐对象）。
3. 数组用流式 `[a, b]` 或块式，浮点固定精度，保证 Git Diff 稳定。
4. 输出以 `# UEAssetLens AssetSpec v2.0 (Auto-generated, Zero-VLM)` 开头。
5. 禁止写入绝对本地磁盘路径、时间戳、随机 ID —— 否则 CI Diff 永远脏。

工件 YAML（plan / diff / GameSpec / audit / rescan）统一由 `artifacts.canonical_yaml()` 生成：
`sort_keys=True`、`allow_unicode=True`、LF、稳定键序；`artifacts.safe_relative()` 拒绝绝对路径与 `..`。
完整大结果落盘后，MCP 只返回摘要 + 相对工件指针 + 分页游标 + 内容哈希，禁止把全量原始文本灌进上下文。

## 3. L3：TOON 批量格式（UAL 自定义方言）

TOON = Token-Oriented Object Notation。**头部一次性声明字段 Schema，数据行紧凑排列**，避免重复的 key 与括弧开销。

### 语法

```
<section_name>[<count>]{<field1>,<field2>,...}:
  <value1>,<value2>,...
```

- 每个 section 的 `{...}` 声明一次字段名；后续每行按同序给值。
- `[N]` 声明行数，必须与实际行数一致。
- 字段值分隔符为半角逗号 `,`。**值内含逗号、双引号或换行时必须用双引号包裹**，内部双引号转义为 `\"`。冒号不触发转义（`logical_id` 形如 `AnimMontage:AM_...` 含冒号但保持裸写）。
- 区间值用 `a..b`，列表值用 `a|b`（例如 `Once|1.2s|User.SlashColor`）。
- 缺失值写空串（连续两个逗号），`NULL` 不写进 TOON。
- 非表格型元信息用 `key: value` 放在 section 之前的 `query_summary` 中。
- 所有 TOON 输出首行固定为 `# UEAssetLens TOON v1 (Zero-VLM)`。

### 3.1 静态只读工具（10 个）

| 工具 | section | 字段 |
|---|---|---|
| `UEAssetLens.AssetSemanticToolset.SearchAssets` | `candidates` | `logical_id,duration,forward_cm,strike_window,limb,score` |
| `UEAssetLens.AssetSemanticToolset.SearchAssets` | `co_occurring_bundle` | `logical_id,asset_class,weight,hint` |
| `UEAssetLens.AssetSemanticToolset.GetCooccurringBundle` | `co_occurring_bundle` | 同上 |
| `UEAssetLens.AssetSemanticToolset.FindSimilarAssets` | `similar_assets` | `logical_id,asset_class,similarity,shared_class` |
| `UEAssetLens.AssetSemanticToolset.SearchByFeatures` | `features` | `logical_id,asset_class,feature_key,feature_value` |
| `UEAssetLens.AssetSemanticToolset.ListFeatureKeys` | `feature_keys` | `feature_key,spec_source,asset_count` |
| `UEAssetLens.AssetGraphToolset.GetDependencies` | `dependencies` | `logical_id,asset_path,depth,edge_type,weight,via_path` |
| `UEAssetLens.AssetGraphToolset.GetReferencers` | `referencers` | 同上（反向影响面） |
| `UEAssetLens.AssetGraphToolset.GetIndexHealth` | `metrics` / `hotspots` / `families` | `metric,value,detail` / `logical_id,asset_class,referenced_by` / `family,asset_count` |
| `UEAssetLens.AssetGraphToolset.FindDuplicates` | `duplicate_pairs` | `lhs_id,rhs_id,similarity` |

`GetDependencies` / `GetReferencers` 用有界递归 BFS（`max_depth` 1..6），`via_path` 携带完整访问路径并通过
`list_contains` 检测环路；`edge_type` 只接受 `DEPENDS_ON` / `CO_OCCURS` / `ANY` 三个枚举。所有用户输入走参数化 SQL。

### 3.2 运行时注册工具（20 个，schema_version 4.0）

工具定义来自 `Config/UAL/extensions/tools.yaml` 的 `tool` 命名空间，经 `ExtensionRegistry.snapshot()`
生成 `tool_schemas.yaml` / 文档；Python stdio server 用官方 SDK 2.x 的 `add_tool` 动态注册，C++ AddTool
读取同一份生成契约。禁止新增 `ual_*` 裸名，禁止旧名别名；每个工具显式 `accessMode` 与 `readOnlyHint`。

| 工具类别 | 返回 section | 字段 / 语义 |
|---|---|---|
| `AssetKernelToolset.ListExtensions` / `ListExtensionFields` / `SearchExtensionFields` / `GetAssemblyGraph` | `extensions` / `extension_fields` / `assembly` | 有界注册表快照、类型化字段查询、角色 / 装配关系 |
| `AssetKernelToolset.GetIndexRevision` / `GetLifecycleEvents` / `ListOfficialTools` | `query_summary` / `lifecycle_events` / `official_tools` | 确定性 revision、审计事件、官方工具白名单 |
| `AssetKernelToolset.RequestRescan` | `rescan` | 相对工件指针 + 排序后的 `/Game` 路径 + dry-run 状态 |
| `AssetDiffToolset.DiffAssets` | `diff_layers` | `diff_id,artifact,canonical_sha256,next_cursor` + 五层统计 |
| `AssetDiffToolset.ReadDiffLayer` | `diff_layer` | `line_no,text`，`offset/limit/next_cursor` 有界分页 |
| `AssetDiffToolset.VerifyDiffDeterminism` | `determinism` | `deterministic,artifact_count,canonical_sha256,mismatches` |
| `AssetEditTransactionToolset.PlanEdit` / `VerifyEdit` | `plan` / `verify` | `plan_id,state,operation_count,index_revision,artifact`；`per_op_result,index_consistent,residual_risks` |
| `AssetEditTransactionToolset.ApplyPlan` / `RollbackEdit` | `apply` / `rollback` | dry-run 默认；`approved_by` + 逐 Write/Execute op 审批；`inverse_ops` |
| `GameSpecToolset.ExpandGameSpec` / `RunValidationLoop` / `RunFps3CWorkflow` | `gamespec` / `validation` / `workflow` | GameSpec 摘要、离线验证阶段、十步 3C 链路 |
| `UEAssetLens.ArtifactToolset.ReadArtifact` | `artifact_page` | `line_no,text` 有界分页；`offset` / `limit`（1..200）/ `next_cursor` / `sha256`，只读 canonical 工件，绝不返回整库原始 YAML |
| `UEAssetLens.GameSpecToolset.ImportGameSpec` | `import` / `gamespec` | 结构化宿主 GameSpec 的有界摘要 + canonical 工件指针；`dry_run` 默认 true，验证 schema / 装配 / 白名单 / 审批边界 |

`PlanEdit` / `VerifyEdit` 的 `accessMode` 为 `Read`，`ApplyPlan` / `RollbackEdit` 为 `Write`，
`RequestRescan` / `RunValidationLoop` / `RunFps3CWorkflow` 为 `Execute`。
`ReadArtifact` 为 `Read`（`readOnlyHint=true`），只读分页 canonical 工件，不写索引、不写 `.uasset`；
`ImportGameSpec` 为 `Write`（`readOnlyHint=false`），`dry_run` 默认 true 且只产出离线有界摘要 / canonical 工件，
非 dry-run 必须提供 `approved_by` 与逐 op `approvals`，且只能经注入 host executor / 官方工具白名单执行，
禁止裸写工具直接暴露给客户端。`accessMode` 写进 `tool_schemas.yaml` 顶层字段、`annotations` 与 `_meta`；
未标注工具默认按 `Execute` 处理，CI 拦截。

### 3.3 示例

```toon
# UEAssetLens TOON v1 (Zero-VLM)
query_summary:
  matched_total: 3
  scope: /Game/Characters/Knight

candidates[3]{logical_id,duration,forward_cm,strike_window,limb,score}:
  AnimMontage:AM_Knight_HeavySlash_02,1.15,185.0,0.35..0.58,hand_r,0.96
  AnimMontage:AM_Knight_DashThrust_01,0.90,320.0,0.25..0.45,hand_r,0.89
```

## 4. 事务、diff 与 GameSpec 工件契约

- `plan_id = sha256(intent + sorted(candidates) + index_revision)`；`operations[]` 每项为
  `{id,tool,arguments,accessMode,inverse,risk}`，`tool` 必须命中 `Config/UAL/gateway/official_tools.yaml`
  的官方工具白名单，禁止传入任意字符串执行。`inverse_ops` 在 plan 阶段生成。
  工件：`.ual/plans/<plan_id>.yaml`。状态机：`planned -> approved -> applied -> committed`，
  失败进 `rolled_back` / `discarded`；离线默认 `dry_run=true`，不写 UE 资产、不写 DuckDB 索引。
- `diff_id = sha256(lhs + rhs + layers + engine_version + index_revision)`。五层
  `L0 元数据 / L1 反射投影 / L2 引用图 / L3 载荷文本 / L4 规范化摘要` 的完整结果写入
  `.ual/diffs/<diff_id>/L0..L4.yaml` 与 `manifest.yaml`；规范化剔除 GUID、时间戳、本地绝对路径、
  `updated_at` / `revision` / `ActorMetaData` / `FiBData`、C1 控制字符。MCP 只返回摘要、
  相对工件指针、`next_cursor` 与 `canonical_sha256`；`VerifyDiffDeterminism` 逐字节复验。
- `game_spec_id = sha256(intent + template + index_revision)`。GameSpec 含
  `domain,entities,rules,input,ui,assertions,assets,assembly,operations,validation`，
  完整结果写 `.ual/gamespecs/<id>.yaml`；验证闭环 `compile -> PIE -> snapshot -> assert -> repair -> rollback`
  写 `.ual/validation/<id>.yaml`。离线 runner 对 compile / PIE 明确报告 `deferred_offline`，不冒充真机结果。
- 生命周期 / 审计事件写 `.ual/audit/events.yaml`；索引重扫请求写 `.ual/staging/rescan.yaml`；
  C++ AssetRegistry 事件写 `.ual/staging/events.yaml` / `removed.yaml`，由 `ual-cli apply-events` 落库。
  这些工件都不含时间戳、随机 ID、本地绝对路径或全量原始索引。

## 5. 自定义规则、白名单与外部集成边界

- `kind: custom` 策略规则只能引用 `Config/UAL/extensions/*.yaml` 里 `ual_common.extensions_builtins`
  允许名单内的内建谓词；禁止从 YAML 导入任意 Python 模块或执行代码。四级解析顺序
  `builtin < plugin < project < user`，按 `id` 覆盖并保持首次出现顺序。
- 白名单来自 `Config/UAL/allowlists/default.yaml` 及四层覆盖。条目必须含
  `reason` / `owner` / `review_by`；过期、缺字段、缺 `approved_by` + `review_ref` 评审门禁的条目无效并告警，
  不得静默屏蔽门禁。白名单只豁免报告，不修改指纹或索引数据。
- `Config/UAL/gateway/integrations.yaml` 声明 P1/P2 外部服务。ComfyUI / TRELLIS / AudioCraft /
  UniRig + Blender + Interchange 默认 `enabled: false`，标注许可与 GPU / VLM / 云依赖；
  Zero-VLM 红线意味着 UAL **不执行、不分发、不调用**这些生成式服务。
  Agent 编排与 `tools/list` 审计以离线 Python 工具实现。官方 MCP / Toolset 为
  `Experimental + NoRedist`，仅开发期启用，loopback 无认证，禁止暴露非 loopback 地址。

## 6. 兼容性约束

- TOON 是 UAL 自定义方言，不是社区 TOON 标准；`ual_common/toon.py` 是唯一编解码实现，禁止在别处手写拼接。
- YAML 生成器分工固定：单资产 AssetSpec 走 `ual_common/yamlspec.py`；工件走
  `ual_common/artifacts.py::canonical_yaml`；C++ 侧 staging / 事件 / 请求格式走 `UEYaml.h` emitter。
  禁止 `FString::Printf` 手工拼接。插件自有数据格式统一 YAML；不存在 `.json` / `.jsonl` 双读兼容层。
  Python 侧统一 `yaml.safe_load` / `safe_load_all` / `canonical_yaml`。
- MCP 协议层与 JSON-RPC 属豁免：`inputSchema` 仍是 JSON Schema 对象（由 `tool_schemas.yaml` 解析成
  `FJsonObject` 喂给 SDK），`GetInputJsonSchema()` / `Run(FJsonObject)` 为 Epic
  `IModelContextProtocolTool` 强制虚函数签名，`UALEngineMcpModule.cpp` 解析后仍构造 `FJsonObject`。
- 任何字段变更必须同步修改本文件、`init.sql`、C++ `UALTypes.h` 与 Python `db.py`，并提交协议版本号递增。
- `content_hash` 严禁用于查重；查重只用 `fingerprint_vec` 余弦相似度。
- MCP Server 只读连接 DuckDB（`read_only=True`）；写索引只由 `ual-cli` / Commandlet / 事件监听侧完成。
- CI 不编译 C++、不启动真实 PIE；本轮 C++ 源码改动只做静态审查与本仓库 Python 门禁，未做 UE SDK 实机编译验证。
