# UEAssetLens MCP 定位与事务层规格

> 状态：**规格草案 v0，待评审**。本文是人工维护页，不由 `Tools/gen_docs.py` 生成。
> 数据基准：UE 6.0 源码树；官方 `ToolsetRegistry` 实测 905 处 AI 标注 / 878 个唯一工具 /
> 40 个含 C++ 工具的 Toolset（见父工程 `Docs/ai-capabilities/toolset-inventory.yaml`）。
> 本文只定义**方向、分类、契约骨架**，不实现代码；工具名与 schema 落地时必须按
> `CLAUDE.md` 的四处同步规则走生成器。
> 定位的进一步升级（资产内核、插件式扩展、四级偏好、五层 diff、AI 工作流）见
> [资产全生命周期架构规格](asset-lifecycle-architecture.md)；本文的事务层设计是其中的 Phase 3。

## 1. 定位

> **UEAssetLens 是 Epic 官方 MCP 的统一资产工具层与编辑事务层。**
> 它用确定性语义索引回答「该改哪个资产」，把实际写操作交给官方 Toolset，
> 并用事务层保证「改完可验证、可回滚、索引仍一致」；Zero-VLM 红线不变。

对外只暴露一个 MCP 入口（`http://localhost:8000/mcp`），用户/Agent 不需要知道某个能力
来自官方还是 UAL。能力分级见第 7 节。

## 2. 架构更正：按能力类型分层，不按实现归属分层

早期把「UAL 语义层」与「官方 Toolset 执行层」画成上下两层是**错误的分类**。
UAL 现有的语义检索工具（旧对外名 `ual_*`，见 §2.1；现共 10 个，见 §5.1）本身就是工具（query 类），与官方 `EditorToolset.FindAssets`、
`GetAssetTags` 等 Read 工具处于**同一层**；区别只在它们的语义深度（16 维指纹、
FTS BM25、共现图、TOON 压缩），而不在层级。

正确的切分依据直接采用官方 `EToolAccessMode`（`ToolsetDefinition.h`）：
`Read` / `Write` / `Execute`，并写进 `annotations.accessMode` 与 `readOnlyHint`。
**未标注 `AIAccessMode` 的工具默认按 `Execute` 处理**，因此新增工具必须显式标注。

| 类别 | accessMode | 官方已有（资产域实测） | UAL 需补充 |
|---|---|---|---|
| **Query** | `Read` | 54 个：`FindAssets`、`GetAssetTags`、`GetReferencers`、`DiffMaterial`、`ValidateAssets`… | 语义检索 / 指纹查重 / 共现装配 / TOON 批量召回 / 引用图谱语义查询 / 索引一致性检查 |
| **Edit** | `Write` | 57 个：`CreateMaterial`、`SetMaterial`、`Duplicate`、`RenameFolder`、`SaveAsset`、`SetActorTransform`… | 语义感知批量替换、按共现图批量装配、资产级 diff 预览、编辑影响面预演 |
| **Execute / 事务** | `Write` 或 `Execute` | 4 个**只读**历史查询：`GetRecentTransactions`、`GetTransactionDetails`、`SearchTransactions` 等；**无 dry-run / 审批 / 回滚** | `UEAssetLens.AssetEditTransactionToolset.PlanEdit` / `UEAssetLens.AssetEditTransactionToolset.ApplyPlan` / `UEAssetLens.AssetEditTransactionToolset.VerifyEdit` / `UEAssetLens.AssetEditTransactionToolset.RollbackEdit` |

**官方不全的实证**（对 878 个工具名做关键词扫描）：

| 能力 | 官方命中 |
|---|---|
| 语义相似度 / 指纹查重 / 共现图 | **0** |
| dry-run / 审批 / 回滚 | **0**（只有 4 个事务历史查询） |
| 批量上下文压缩 | 2（`GetEmitterSummary`、`GetSystemSummary`，均为 Niagara） |
| 资产索引刷新 | 0（2 个命中与资产索引无关） |

结论：**Query 类的深度语义能力与 Execute 类的事务能力，是 UAL 必须自建补上的部分**；
而 `CreateMaterial` / `Duplicate` / `SaveAsset` / `SetActorTransform` 这类通用写操作
官方已有，**禁止在 UAL 内重复实现**（第 6 节）。

## 2.1 命名规范（P0，命名对齐不推迟到阶段 2）

**官方有两层命名，UAL 当前一层都没对齐。**

| 层 | 官方程式 | 实测证据 |
|---|---|---|
| 源码层 UFUNCTION | PascalCase | 878 个工具中 877 个为 PascalCase，0 个 snake_case |
| MCP 对外工具名 | `<ToolsetName>.<Tool>`，完整形态 `{ModuleName}.{ClassName}.{FunctionName}` | `ToolsetRegistry/.../FakeToolset.cpp:49` 拼 `GetToolsetName() + "." + ToolName`；MCP 适配器 `ModelContextProtocolToolsetRegistryAdapter.cpp:588` 直接以该名 `AddTool`；官方测试注释 `ModelContextProtocolToolsetRegistryTests.cpp:29` |

`UE::ModelContextProtocol::ValidateToolName()` 允许 `A-Z a-z 0-9 _ - .`、长度 ≤128，
官方测试明确把 `getUser`、`my_tool`、`admin.tools.list` 判为 `Valid`。因此
**`ual_*` 是「合法但不规范」——CI 靠字符校验拦不住，必须靠约定**。三个不规范点：

1. snake_case + 手工扁平前缀 `ual_`，与官方 PascalCase 相悖；
2. 缺少结构化命名空间，工具一多只能靠前缀防撞，无法表达 Toolset 归属；
3. Python 实现函数名与 MCP 对外名强绑定（1:1 裸名契约），导致带点号的限定名无法使用。

**解法**：官方 MCP Python SDK 2.x 的 `@sdk.tool(name=...)` 支持显式工具名（已核对
`MCPServer.tool()` 签名）。因此 `Server/ual_mcp/tools.py::TOOL_DEFS` 增加 `mcp_name`
作为**对外名唯一事实源**，Python 内部实现函数保留 snake_case（PEP 8），
`gen_docs.py` 从 `mcp_name` 生成 `tool_schemas.yaml`，C++ `AddTool` 直接以限定名注册。
「Python 函数名 == 工具名」的旧 1:1 契约升级为「`mcp_name` 唯一决定、两条 server 共用」。

**目标命名表**（一次性硬切，不保留旧名别名）：

| 旧对外名 / Python 实现名 | 新 MCP 工具名 | 类别 |
|---|---|---|
| `ual_search_assets` | `UEAssetLens.AssetSemanticToolset.SearchAssets` | Query |
| `ual_inspect_asset` | `UEAssetLens.AssetSemanticToolset.InspectAsset` | Query |
| `ual_get_cooccurring_bundle` | `UEAssetLens.AssetSemanticToolset.GetCooccurringBundle` | Query |
| `ual_find_similar_assets` | `UEAssetLens.AssetSemanticToolset.FindSimilarAssets` | Query |
| `ual_plan_edit` | `UEAssetLens.AssetEditTransactionToolset.PlanEdit` | Execute |
| `ual_apply_plan` | `UEAssetLens.AssetEditTransactionToolset.ApplyPlan` | Execute |
| `ual_verify_edit` | `UEAssetLens.AssetEditTransactionToolset.VerifyEdit` | Execute |
| `ual_rollback_edit` | `UEAssetLens.AssetEditTransactionToolset.RollbackEdit` | Execute |

这是 **breaking change**：客户端调用名、脚本与文档示例全部变化。迁移清单：
`tools.py::TOOL_DEFS` → `server.py` 的 `@sdk.tool(name=)` → `Tools/gen_docs.py` →
`tool_schemas.yaml`（生成物）→ `engine_bridge.py` 分发映射 → `test_ual.py` 与夹具 →
`PROTOCOL.md` / `CLAUDE.md` / `docs/reference/*`（生成）/ `docs/*.md` / `README.md` →
`setup_mcp.ps1` 与客户端配置示例。全部改完必须过 §9 的门禁。

**落地状态（2026-11-23）**：上述硬切已执行完毕。`TOOL_DEFS` 现含 `mcp_name`，
`server.py` 用 `@sdk.tool(name=...)` 显式注册，`engine_bridge.py` 按限定名分发，
`gen_docs.py` 只发布限定名并校验其与 SDK 注册名一致；README、非生成页文档、
`PROTOCOL.md` 与生成页已全部同步。Python 内部实现函数名保留 snake_case，
`tools.TOOL_NAMES` 由 `TOOL_DEFS.mcp_name` 派生。生成器 6 页无漂移、44 项测试与
三个 self-test 全部通过，对外工具名残留为 0。

**运行时验证（2026-11-24）**：headless editor 下官方 MCP 握手成功，`tools/list` 返回
4 个限定名 UAL 工具（旧名 0 残留）与 4 个 tool-search 元工具；`tools/call` 对 4 个 UAL
工具全部返回真实 TOON / YAML 结果（PASS）。bridge 已改用引擎内嵌 Python 与官方
`PythonRequirements` 安装的依赖，不再依赖 PATH 上的系统解释器。

`Server/ual_mcp/tool_schemas.yaml` 的 `schema_version` 由 `2.0` bump 到 `3.0`，
标记对外工具名的 breaking change。它与 DuckDB `schema_meta.schema_version`
（见 `docs/reference/schema.md`，仍为 2.0）是两套独立契约，**不要混同或联动修改**。

## 3. 实现路径：`AddTool` 与 `UToolsetDefinition`

两条注册路径的硬事实：

| 项 | `IModelContextProtocolModule::AddTool` | `UToolsetDefinition` + `ToolsetRegistry` |
|---|---|---|
| 工具名 | 原样、全局唯一（`AddTool` 遇重名直接返回 false） | 被限定为 `<Toolset>.<Tool>`（`FakeToolset.cpp:49` 实证） |
| 权限标注 | 手写 `GetAnnotations()` | `meta=(AIAccessMode="Read"\|"Write"\|"Execute")` 自动生成 |
| Python 侧工具名契约 | 由 `TOOL_DEFS.mcp_name` 显式指定 | 由注册表自动生成限定名 |
| 进入 `list_toolsets` / 统一工具目录 | 否 | 是 |

`CLAUDE.md` 原先「禁止改用 `UToolsetDefinition`」的理由（命名空间与 Python 裸名冲突）
经核实成立，**但它是实现约束，不是概念约束**：概念上 UAL 工具归 Query 类工具集，
与是否注册进 ToolsetRegistry 是两件事。且该冲突已由 §2.1 的显式 `mcp_name` 契约消解，
不再构成迁移的阻塞项。

采用两阶段，避免一次性重写 44 个测试与 CI 依赖：

- **阶段 1（本规格覆盖范围）**：继续用 `AddTool`，但**必须先完成 §2.1 的命名对齐**；
  把 UAL 工具纳入统一工具目录；所有工具显式标注 `accessMode` / `readOnlyHint`；写操作一律经事务层。
  命名对齐不再推迟到阶段 2——SDK 2.x 支持 `@sdk.tool(name=...)`，无需迁移即可使用限定名。
- **阶段 2（可选，待决）**：一次性迁到 `UToolsetDefinition`，让注册表自动生成限定名并进入
  `list_toolsets` 统一目录；需同步 `TOOL_DEFS`、`server.py`、`tool_schemas.yaml`、
  `docs/reference/*.md` 与客户端调用。**命名在阶段 1 已对齐，此阶段只改变注册机制**，
  迁移前不引入双轨别名。

## 4. 事务层骨架

### 4.1 工具契约草案

> **以下为设计草案，不是事实源。** 真正落地时唯一事实源是 `Server/ual_mcp/tools.py::TOOL_DEFS`，
> 由 `Tools/gen_docs.py` 生成 `tool_schemas.yaml` 与 `docs/reference/mcp-tools.md`，禁止手改。

| 工具 | accessMode | 输入要点 | 输出要点 |
|---|---|---|---|
| `UEAssetLens.AssetEditTransactionToolset.PlanEdit` | `Read` | `intent`、`candidates[]`（来自 `UEAssetLens.AssetSemanticToolset.SearchAssets`）、`scope`（`/Game/...` 白名单）、`max_ops` | `plan_id`、`operations[]`、`index_revision`、`diff_preview`（TOON） |
| `UEAssetLens.AssetEditTransactionToolset.ApplyPlan` | `Write` | `plan_id`、`approved_by`、`dry_run`（默认 true） | `applied_ops[]`、`inverse_ops[]`、`index_revision_after` |
| `UEAssetLens.AssetEditTransactionToolset.VerifyEdit` | `Read` | `plan_id` | `per_op_result[]`、`index_consistent`、`residual_risks` |
| `UEAssetLens.AssetEditTransactionToolset.RollbackEdit` | `Write` | `plan_id` | `reverted_ops[]`、`index_consistent` |

`operations[]` 每项为 `{tool, arguments, accessMode, inverse, risk}`，其中 **`tool` 必须命中
官方工具白名单**（例如 `EditorToolset.SetMaterial`），禁止传入任意字符串执行。
`inverse` 在 plan 阶段生成，因为官方只有只读的事务历史查询，没有可用的 undo 工具。

### 4.2 状态机

```
planned ──审批──> approved ──apply(dry_run=false)──> applied ──verify──> committed
   │                  │                                  │                 │
   └── 拒绝 ──> discarded                                └── 失败 ──> rolled_back ──> 重扫索引
                                                          └── 索引不一致 ──> 强制增量重扫
```

- `plan_id` 必须是**确定性**值：`hash(intent + sorted(candidates) + index_revision)`。
  禁止随机 ID / 时间戳 / 本地绝对路径（`CLAUDE.md` §2 红线）。
- `dry_run=true` 时只产出 `diff_preview`，不调用任何 `Write` 工具。
- 审批依据：`operations[].accessMode`；`Write` / `Execute` 必须逐项确认，`Read` 可批量放行。
- 提交后对受影响资产调用 `UALIndexBuilder` 增量重扫；`verify` 回读 `UEAssetLens.AssetSemanticToolset.InspectAsset` 比对预期。

### 4.3 索引一致性（P0，当前缺失）

现仓库只有 Commandlet 全量/增量扫描，源码中 `OnAssetAdded` / `OnAssetUpdated` /
`OnAssetRemoved` 均无命中——**开放编辑后索引会过期**。二选一：

1. 在 `UEAssetLensEditor` 增加 AssetRegistry 事件监听，事件驱动增量重索引；或
2. 在事务 `commit` 阶段按 `operations[].arguments` 收集受影响资产路径，强制增量重扫。

推荐两者结合：事件监听保证人工编辑也同步，事务钩子保证 Agent 编辑在 `verify` 前已一致。
副作用：需要重新评估 `CLAUDE.md`「MCP Server 只读连接 DuckDB」的边界——
**写索引只能由 Commandlet / 事件监听侧完成，MCP 工具侧仍保持 `read_only=True`**，
事务层通过触发重扫而非直接写库来满足该红线。

## 5. 需补充的自建 Toolset 清单

| 优先级 | 类别 | 能力 | 理由 |
|---|---|---|---|
| P0 | Query | 语义检索、16 维指纹查重、共现装配、TOON 批量召回 | 官方 0 命中，UAL 护城河；已有 10 个工具需并入统一目录 |
| P0 | Execute | 计划 / 审批 / 执行 / 验证 / 回滚 | 官方 0 命中，编辑可信的前提 |
| P0 | — | 编辑后索引一致性 | 现无事件监听 |
| P1 | Query | 资产引用图谱语义查询、索引健康度检查 | 官方只有 `GetReferencers` 等点查询，无图谱/相似度 |
| P1 | Edit | 语义感知批量替换（查重→引用替换）、共现图批量装配、资产 diff 预览 | 官方有单次写，无"按语义结果批量改" |
| P2 | Edit | 跨资产引用安全的重命名 / 移动 | 官方有 `RenameFolder` 等，缺引用事务性保障 |

### 5.1 落地状态（2026-11-24）

本轮补齐了「特征收集 / 检索 / 依赖」三块，**编辑事务层仍待单独一轮**：

| 能力 | 状态 | 落点 |
|---|---|---|
| 全资产领域特征收集 | 已落地 | `FUALTypedFeatureExtractor`（Priority 100，24 族 profile），`feature.*` 写入 `domain_spec`，零加载、零 schema 变更 |
| 共现图生成 | 已落地 | `UALIndexBuilderCommandlet` 同目录 + 名称 token Jaccard，每资产上限 8 条 `CO_OCCURS`；`DEPENDS_ON` 过滤 `/Script` 类引用 |
| 引用图谱语义查询 | 已落地 | `UEAssetLens.AssetGraphToolset.GetDependencies` / `GetReferencers`（有界递归 BFS，环检测） |
| 索引健康度 | 已落地 | `UEAssetLens.AssetGraphToolset.GetIndexHealth`（断裂 / 孤立 / 外部引用 / 指纹缺失 / 族分布） |
| 全库指纹查重 | 已落地 | `UEAssetLens.AssetGraphToolset.FindDuplicates` |
| 特征检索与词表发现 | 已落地 | `UEAssetLens.AssetSemanticToolset.SearchByFeatures` / `ListFeatureKeys` |
| 计划 / 审批 / 执行 / 验证 / 回滚 | **未落地** | 规格见 §4，执行通道未定稿（§10.4），索引一致性仍缺重扫触发 |
| 语义感知批量替换 / 引用安全重命名 | 未落地 | 依赖事务层 |

现在共 **10 个只读工具**（`AssetSemanticToolset` 6 + `AssetGraphToolset` 4），全部 `accessMode=read`。`GetAnnotations()` 目前硬编码 read，新增 Write/Execute 工具前必须改为按 `AIAccessMode` 分派。

**真实 UE6 索引验证（2026-11-24）**：`ual-cli build --engine-cmd UnrealEditor.exe` 用模块限定名
`-run=UEAssetLensEditor.UALIndexBuilder` 扫描 `/Game`，产出 15 条 16 键记录、56 条边（其中 44 条
确定性 `CO_OCCURS`），staging YAML 可被 PyYAML 完整解析（0 非法控制字符），`feature.*` 10 个键
在全部 15 条记录上齐备。族分布为 Blueprint 13 / World 2，与工程当前 13 个骨架蓝图 + 2 张关卡一致。
过程中修掉两个真实缺陷：Blueprint `FiBData` 二进制 Tag 的 C1 控制字符（提取器跳过 + `UEYaml`
emitter 转义 `0x7F–0x9F`）以及 UE6 commandlet 成功仍返回非零退出码（`ual-cli` 改以 staging 产物为准）。

**headless 端到端（2026-11-24）**：编辑器内官方 MCP 注册 `10 UAL tools`，`tools/call` 对 10 个工具
全部 PASS（`isError=None`），数据来自上述真实索引：`SearchAssets` 3 条、`InspectAsset` 完整 AssetSpec、
`GetCooccurringBundle` 3 条（由新确定性共现边产出）、`FindSimilarAssets` 8 条、`GetDependencies` 29 条
两跳、`GetReferencers` 34 条影响面、`GetIndexHealth` 11 项指标、`FindDuplicates` 5 对、
`SearchByFeatures` 15 行、`ListFeatureKeys` 10 个键。注意官方 MCP 在编辑器执行慢任务期间会返回
`Not run: ... slow task`，这是 host 保护而非工具故障，客户端需等待后重试。

**禁止重复实现**（官方已具备，直接路由）：`FindAssets` / `GetAssetTags` / `CreateMaterial` /
`SetMaterial` / `Duplicate` / `Delete` / `SaveAsset` / `AddUserAssetTags` /
`SetActorTransform` / `CreateCollection` / `ValidateAssets` 等 111 个资产域工具。

## 6. 红线更新建议

保持不变：Zero-VLM / 100% 确定性离线、禁止整库原始数据进上下文、禁止手写 YAML/TOON
字符串拼接、禁止 `content_hash` 查重、禁止加载贴图/网格进显存、输出不含本地路径/时间戳/随机 ID、
MCP 侧 DuckDB 只读连接、SQL 参数化、工具返回 `str`。

需要补充：

1. 允许 `Write` 类工具，但**必须经事务层**（plan → 审批 → apply → verify），禁止裸写工具暴露给客户端。
2. 所有工具显式标注 `AIAccessMode`；未标注按 `Execute` 处理，CI 需拦截。
3. 官方工具白名单化：事务层只允许调用清单内的工具名，禁止任意路由。
4. Zero-VLM 扩展到编排层：事务层确定性，不引入视觉模型或云端推理。
5. 禁止重复实现官方已有工具；新增工具前先查 `toolset-inventory.yaml`。

## 7. 能力分级与降级

`UEAssetLens.uplugin` 中 `ModelContextProtocol` 是 `Enabled: true, Optional: true`，保持软依赖。

| 场景 | 可用能力 |
|---|---|
| UE6 + 官方 MCP 插件启用 | 全量：Query + Edit + 事务层，单一 HTTP 入口 |
| 官方插件缺失 / 非 UE6 | 降级为 Python stdio server 的 10 个只读工具 + `ual-cli`；编辑能力明确不可用 |
| 无 UE 引擎（CI / 离线） | Python server + DuckDB，44 项测试与生成门禁照常运行 |

官方 MCP / Toolset 均为 `Experimental + NoRedist`，**只能开发期启用，不可随发布包分发**；
README 与 `docs/index.md` 必须写明该分级，避免用户误以为装了 UAL 就能编辑。

## 8. 统一入口

现存在两条入口：C++ `UEAssetLensMCP` 用 `AddTool` 注册进官方 HTTP server（内部经 YAML
请求文件桥接 `engine_bridge.py`），同时 `Tools/setup_mcp.ps1` 让客户端直连独立 Python stdio server。
目标：

- 客户端只配置 `http://localhost:8000/mcp`（HTTP）；stdio 仅作为第 7 节的降级路径保留、不删。
- `setup_mcp.ps1` 增加 HTTP 配置模式；现有 stdio 模式保留但标注为降级。
- 所有工具在两条路径上保持**同一个限定名**（由 `TOOL_DEFS.mcp_name` 唯一决定）、同 schema、同输出；
  Python 内部实现函数名与对外工具名解耦，禁止再以裸名注册。

## 9. 验证与门禁

新增任何工具或改签名，必须按顺序完成：

1. 改 `Server/ual_mcp/tools.py::TOOL_DEFS`（含 `mcp_name`）与 `server.py` 的 `@sdk.tool(name=...)`；
2. 跑 `python Tools/gen_docs.py`（更新 `tool_schemas.yaml`、`docs/reference/mcp-tools.md`）；
3. 同步 `Server/schema/PROTOCOL.md` 与 `CLAUDE.md` 相关章节；
4. `python Tools/gen_docs.py --check`、`python Tools/gen_taxonomy.py --check`；
5. `python -m pytest Server/tests -q`、`python Server/ual_cli/ual_cli.py self-test`、
   `python Server/ual_mcp/engine_bridge.py --self-test`；
6. C++ 侧改动需在有合格 MSVC 工具链的环境编译 `AnnEditor Win64 Development`。

## 10. 待决事项

1. 阶段 2 是否迁移到 `UToolsetDefinition`（命名已对齐，此处只决定是否进入官方统一工具目录）。
2. Python stdio server 的长期定位：仅降级路径，还是与 HTTP 双轨并存。
3. 审批粒度：逐操作确认 vs 每个 plan 一次确认；默认建议逐 `Write` 操作确认。
4. 事务层调用官方工具的机制：经 `IModelContextProtocolModule` 查找已注册工具 `Run`，
   还是直接走 `UE::ToolsetRegistry::FToolsetRegistry`。**编译环境 blocker 已消除**
   （`AnnEditor Win64 Development` 全量 4450 actions `Result: Succeeded`），可实测后定稿。
   路径 A（Python 子进程 HTTP 回环 `call_tool`）与 `CLAUDE.md` 的「只用 duckdb / PyYAML /
   mcp 三依赖、禁止手写协议层」冲突，且引擎内嵌 Python 未装 `mcp`；路径 B（C++ 直调
   `ToolsetRegistry`）需在 `UEAssetLensMCP.Build.cs` 加模块依赖并触碰「工具调用委托
   `engine_bridge.py` 子进程」红线。折中建议：**`PlanEdit` / `VerifyEdit`（Read）留在 Python，
   `ApplyPlan` / `RollbackEdit`（Write）用薄 C++ 层经 `IModelContextProtocolModule` 查已注册
   工具 `Run`**，白名单以独立常量表维护并对齐父工程
   `Docs/ai-capabilities/toolset-inventory.yaml`。此决策需确认后实施。
