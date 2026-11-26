# UEAssetLens MCP 定位与事务层规格

> 状态：**Query 工具层、命名对齐与离线 dry-run 事务层已落地；真实 UE host executor / 写入 / compile / PIE 仍是规格草案 v0，待评审**。
> 本文是人工维护页，不由 `Tools/gen_docs.py` 生成。
> **「UAL 是什么」的唯一事实源是 [项目定位与能力规格](positioning.md)**；本文只定义它在 Epic 官方 MCP
> 生态里的分工、命名规则与事务层契约骨架，不重写产品定位。
> 数据基准：UE 6.0 源码树；官方 `ToolsetRegistry` 历史实测 905 处 AI 标注 / 878 个唯一工具 /
> 40 个含 C++ 工具的 Toolset（见父工程 `Docs/ai-capabilities/toolset-inventory.yaml`）。
> 2026-11-24 定位升级的**正式规格主文**（资产内核、插件式扩展、四级偏好、五层 diff、AI 工作流）见
> [资产全生命周期架构规格](asset-lifecycle-architecture.md)，完整能力清单见
> [项目定位](positioning.md) §3；本文的事务层设计是其中的 Phase 3。标为**规格 / 未落地**的条目不是已验证结论。

## 1. 定位

在 Epic 官方 MCP 生态这一**局部视角**下，UAL 承担两层角色（整体定位见 [项目定位](positioning.md) §5）：

- **统一资产工具层（已实现）**：10 个既有 Query 类只读工具，用确定性语义索引回答「该改哪个资产」；另有 20 个由 `ExtensionRegistry.snapshot()` 发布的 runtime tools（共 30 个工具）。
- **离线 dry-run 事务层（已实现 / 离线）**：`PlanEdit` / `ApplyPlan` / `VerifyEdit` / `RollbackEdit` / `RequestRescan` 等运行时工具产出确定性 plan、审批记录、inverse、verify、rescan 与五层 diff 工件；默认 `dry_run=true`，不写 `.uasset`、不写 DuckDB 索引、不调用真实 UE。
- **真实 UE host executor（规格 / 未落地）**：官方 Toolset 经注入 executor 执行真实写入、compile / PIE、AssetRegistry 事件重扫与失败回滚；离线门禁与 CI 未执行。本地 UE6 UBT Build 已通过（`Result: Succeeded`），但 **CI 仍不编译 C++**，真实 UE 写入 / 编辑器编译 / PIE / UE 自动化用例仍**规格 / 未落地**。

> **读这一页请带状态**：工具契约中的 Write / Execute 标注表示能力类别和离线事务编排，**不表示当前可对真实 UE 资产执行写入**。官方 MCP / Toolset 为 `Experimental + NoRedist`，loopback-only、无认证、不可随发布包分发。

对外主入口是 `http://localhost:8000/mcp`，`Tools/setup_mcp.ps1` 的 HTTP DryRun 可验证客户端配置；Python stdio server 仅作降级路径。能力分级见第 7 节。

## 2. 架构更正：按能力类型分层，不按实现归属分层

早期把「UAL 语义层」与「官方 Toolset 执行层」画成上下两层是**错误的分类**。
UAL 现有的语义检索工具（旧对外名 `ual_*`，见 §2.1；现共 10 个，见 §5.1）本身就是工具（Query 类），与官方 `EditorToolset.FindAssets`、
`GetAssetTags` 等 Read 工具处于**同一层**；区别只在它们的语义深度（16 维指纹、
FTS BM25、共现图、TOON 压缩），而不在层级。

正确的切分依据直接采用官方 `EToolAccessMode`（`ToolsetDefinition.h`）：
`Read` / `Write` / `Execute`，并写进 `annotations.accessMode` 与 `readOnlyHint`。
**未标注 `AIAccessMode` 的工具默认按 `Execute` 处理**，因此新增工具必须显式标注。`Tools/audit_tool_annotations.py --check` 会拦截缺失 / 冲突标注。

| 类别 | accessMode | 官方已有（资产域历史实测） | UAL 需补充 / 现状 |
| --- | --- | --- | --- |
| **Query** | `Read` | 54 个：`FindAssets`、`GetAssetTags`、`GetReferencers`、`DiffMaterial`、`ValidateAssets`… | 语义检索 / 指纹查重 / 共现装配 / TOON 批量召回 / 引用图谱语义查询 / 索引一致性检查：**已实现** |
| **Edit** | `Write` | 57 个：`CreateMaterial`、`SetMaterial`、`Duplicate`、`RenameFolder`、`SaveAsset`、`SetActorTransform`… | 离线 dry-run `ApplyPlan` / `RollbackEdit`、白名单 inverse、diff preview：**已实现（离线）**；真实写入：**规格** |
| **Execute / 事务** | `Write` 或 `Execute` | 4 个**只读**历史查询：`GetRecentTransactions`、`GetTransactionDetails`、`SearchTransactions` 等；**无 dry-run / 审批 / 回滚** | `UEAssetLens.AssetEditTransactionToolset.*`、`UEAssetLens.AssetKernelToolset.RequestRescan` 等：**已实现（离线 dry-run）**；真实 host executor：**规格** |

**官方不全的历史实证**（对 878 个工具名做关键词扫描）：

| 能力 | 官方命中 |
| --- | --- |
| 语义相似度 / 指纹查重 / 共现图 | **0** |
| dry-run / 审批 / 回滚 | **0**（只有 4 个事务历史查询） |
| 批量上下文压缩 | 2（`GetEmitterSummary`、`GetSystemSummary`，均为 Niagara） |
| 资产索引刷新 | 0（2 个命中与资产索引无关） |

结论：**Query 类的深度语义能力与 Execute 类的事务能力，是 UAL 必须自建补上的部分**；
而 `CreateMaterial` / `Duplicate` / `SaveAsset` / `SetActorTransform` 这类通用写操作
官方已有，**禁止在 UAL 内重复实现**（第 6 节）。

## 2.1 命名规范（P0，命名对齐不推迟到阶段 2）

**官方有两层命名，UAL 当前一层都没对齐的历史问题已修正。**

| 层 | 官方程式 | 历史实测证据 |
| --- | --- | --- |
| 源码层 UFUNCTION | PascalCase | 878 个工具中 877 个为 PascalCase，0 个 snake_case |
| MCP 对外工具名 | `<ToolsetName>.<Tool>`，完整形态 `{ModuleName}.{ClassName}.{FunctionName}` | `ToolsetRegistry/.../FakeToolset.cpp:49` 拼 `GetToolsetName() + "." + ToolName`；MCP 适配器 `ModelContextProtocolToolsetRegistryAdapter.cpp:588` 直接以该名 `AddTool` |

`UE::ModelContextProtocol::ValidateToolName()` 允许 `A-Z a-z 0-9 _ - .`、长度 ≤128，
因此**`ual_*` 是「合法但不规范」——CI 靠字符校验拦不住，必须靠约定**。三个不规范点：

1. snake_case + 手工扁平前缀 `ual_`，与官方 PascalCase 相悖；
2. 缺少结构化命名空间，工具一多只能靠前缀防撞，无法表达 Toolset 归属；
3. Python 实现函数名与 MCP 对外名强绑定（1:1 裸名契约），导致带点号的限定名无法使用。

**解法**：官方 MCP Python SDK 2.x 的 `@sdk.tool(name=...)` 支持显式工具名。既有 10 个只读工具的对外名由 `tools.py::TOOL_DEFS.mcp_name` 提供；20 个 runtime tools（共 30 个工具） 的**对外名唯一事实源**是 `Config/UAL/extensions/tools.yaml` 中的 `mcp_name`，经 `ExtensionRegistry.snapshot()` 与静态契约合并后由 `Tools/gen_docs.py` 发布到 `tool_schemas.yaml`。`server.py` 的静态装饰器与 `register_runtime_tools(sdk, con, RUNTIME_TOOL_DEFS)`、C++ `AddTool` 使用同一限定名，Python 内部实现函数名保留 snake_case，与对外名解耦。禁止新增裸 `ual_*` 线名，也不保留旧名别名。

**目标命名表**（一次性硬切，不保留旧名别名）：

| 旧对外名 / Python 实现名 | 新 MCP 工具名 | 类别 |
| --- | --- | --- |
| `ual_search_assets` | `UEAssetLens.AssetSemanticToolset.SearchAssets` | Query |
| `ual_inspect_asset` | `UEAssetLens.AssetSemanticToolset.InspectAsset` | Query |
| `ual_get_cooccurring_bundle` | `UEAssetLens.AssetSemanticToolset.GetCooccurringBundle` | Query |
| `ual_find_similar_assets` | `UEAssetLens.AssetSemanticToolset.FindSimilarAssets` | Query |
| `ual_plan_edit` | `UEAssetLens.AssetEditTransactionToolset.PlanEdit` | Execute / 离线 dry-run |
| `ual_apply_plan` | `UEAssetLens.AssetEditTransactionToolset.ApplyPlan` | Execute / 离线 dry-run |
| `ual_verify_edit` | `UEAssetLens.AssetEditTransactionToolset.VerifyEdit` | Execute / 离线 dry-run |
| `ual_rollback_edit` | `UEAssetLens.AssetEditTransactionToolset.RollbackEdit` | Execute / 离线 dry-run |

这是 **breaking change**：客户端调用名、脚本与文档示例全部变化。迁移清单：
`Config/UAL/extensions/tools.yaml` → `ExtensionRegistry.snapshot()` → `Tools/gen_docs.py` →
`tool_schemas.yaml`（生成物）→ `server.py` / `engine_bridge.py` 分发映射 → `test_ual.py` 与夹具 →
`PROTOCOL.md` / `CLAUDE.md` / `docs/reference/*`（生成）/ `docs/*.md` / `README.md` →
`setup_mcp.ps1` 与客户端配置示例。全部改完必须过 §9 的门禁。

**落地状态（2026-11-23 命名硬切）**：上述硬切已执行完毕。Python 内部实现函数名保留 snake_case，
`Tools/audit_tool_annotations.py --check` 校验对外限定名与 annotations；2026-11-27 已重跑生成器
（`Tools/gen_docs.py` + `--check`）并通过 168 项 pytest、`mkdocs build --strict` 等完整离线门禁。

**历史运行时验证（2026-11-24，不在本轮离线门禁复核）**：headless editor 下官方 MCP 握手成功，`tools/list` 返回
限定名 UAL 工具（旧名 0 残留）与 tool-search 元工具；`tools/call` 对 UAL 工具返回真实 TOON / YAML 结果。
该记录不代表本轮重新编译 C++、重新执行 UE SDK 编译或真实 UE 写入 / PIE。

`Server/ual_mcp/tool_schemas.yaml` 的 `schema_version` 为 **`4.0`**：运行时工具来自 registry snapshot，逐工具有
`accessMode` / `readOnlyHint`。它与 DuckDB `schema_meta.schema_version` **`3.0`**（见 [存储参考](reference/schema.md)）
是两套独立契约，**不要混同或联动修改**。

## 3. 实现路径：`AddTool` 与 `UToolsetDefinition`

两条注册路径的硬事实：

| 项 | `IModelContextProtocolModule::AddTool` | `UToolsetDefinition` + `ToolsetRegistry` |
| --- | --- | --- |
| 工具名 | 原样、全局唯一（`AddTool` 遇重名直接返回 false） | 被限定为 `<Toolset>.<Tool>`（`FakeToolset.cpp:49` 实证） |
| 权限标注 | 显式 `GetAnnotations()` / generated annotations | `meta=(AIAccessMode="Read"\|"Write"\|"Execute")` 自动生成 |
| Python 侧工具名契约 | 由 `Config/UAL/extensions/tools.yaml` 的 `mcp_name` 显式指定 | 由注册表自动生成限定名 |
| 进入 `list_toolsets` / 统一工具目录 | 否 | 是 |

`CLAUDE.md` 原先「禁止改用 `UToolsetDefinition`」的理由（命名空间与 Python 裸名冲突）
经核实成立，**但它是实现约束，不是概念约束**：概念上 UAL 工具归 Query / Write / Execute 类工具集，
与是否注册进 ToolsetRegistry 是两件事。该冲突已由 §2.1 的显式 `mcp_name` 契约消解，
但当前仍选择不迁移。

- **阶段 1（当前，已实现）**：继续用 `AddTool`，保持显式限定名、逐工具 `accessMode` / `readOnlyHint`；写 / 执行能力只经离线 transaction / gateway，禁止裸写工具。
- **阶段 2（`UToolsetDefinition`）**：**DECIDED AGAINST for now**。不迁移到 `UToolsetDefinition`，不引入双轨别名；若未来重新评估，必须一次性同步 `Config/UAL/extensions/tools.yaml`、`server.py`、`tool_schemas.yaml`、`docs/reference/*.md` 与所有客户端调用，并通过 §9 门禁。当前保持 `AddTool` + 显式 qualified names / annotations。

## 4. 离线事务层骨架

### 4.1 工具契约

> **真实落地时唯一事实源是 `Config/UAL/extensions/tools.yaml` 经 `ExtensionRegistry.snapshot()` 生成的 `Server/ual_mcp/tool_schemas.yaml`**，由 `Tools/gen_docs.py` 生成 `tool_schemas.yaml` 与 `docs/reference/mcp-tools.md`，禁止手改。下表给出离线实现的状态，不代表真实 UE 写入已验证。

| 工具 | accessMode | 输入要点 | 输出要点 / 状态 |
| --- | --- | --- | --- |
| `UEAssetLens.AssetEditTransactionToolset.PlanEdit` | `Read` | `intent`、`candidates`、`scope`、`max_ops`、可选 `operations` | `plan_id`、`operations[]`、`index_revision`、`diff_preview`、inverse：**已实现（离线）** |
| `UEAssetLens.AssetEditTransactionToolset.ApplyPlan` | `Write` | `plan_id`、`approved_by`、`dry_run`（默认 true）、`approvals` | `applied_ops[]`、`inverse_ops[]`、rescan 工件；缺席 executor 时 deferred offline：**已实现（离线 dry-run）** |
| `UEAssetLens.AssetEditTransactionToolset.VerifyEdit` | `Read` | `plan_id` | `per_op_result[]`、`index_consistent`、`residual_risks`：**已实现（离线）** |
| `UEAssetLens.AssetEditTransactionToolset.RollbackEdit` | `Write` | `plan_id`、`approved_by`、`dry_run` | `reverted_ops[]`、inverse、rescan：**已实现（离线 dry-run）** |
| `UEAssetLens.AssetKernelToolset.RequestRescan` | `Execute` | `asset_paths`、`reason`、`dry_run` | 排序后的 rescan request canonical 工件：**已实现（离线）** |
| 真实 UE host executor / official Toolset `Run` | `Write` / `Execute` | 注入 executor + 官方工具白名单 | 真实 `.uasset` 写入、compile / PIE：**规格 / 未落地** |

`operations[]` 每项为 `{id, tool, arguments, accessMode, inverse, risk}`，其中 **`tool` 必须命中
`Config/UAL/gateway/official_tools.yaml` 白名单**（例如 `EditorToolset.SetMaterial` 的 bare 名 `SetMaterial`），禁止传入任意字符串执行。
`inverse` 在 plan 阶段生成，因为官方只有只读的事务历史查询，没有可用的 undo 工具。所有 plan / audit / rescan 工件由 `ual_common/artifacts.py` canonical emitter 写出，不手写 YAML / TOON，不输出本地绝对路径 / 时间戳 / 随机 ID。

### 4.2 状态机

```text
planned ──审批──> approved ──apply(dry_run=false, host executor)──> applied ──verify──> committed
   │                  │                                  │                 │
   └── 拒绝 ──> discarded                                └── 失败 ──> rolled_back ──> rescan 工件
                                                          └── 索引不一致 ──> 强制增量重扫意图
```

- `plan_id` 必须是**确定性**值：`hash(intent + sorted(candidates) + index_revision)`。
  禁止随机 ID / 时间戳 / 本地绝对路径（`CLAUDE.md` §2 红线）。
- `dry_run=true` 时只产出 canonical plan / diff_preview / audit 工件，不调用任何 `Write` 工具。
- 审批依据：`operations[].accessMode`；`Write` / `Execute` 必须逐项确认，`Read` 可批量放行。
- 离线 `commit` / `rollback` 收集受影响资产路径并 stage rescan request / apply-events；真实增量重扫、AssetRegistry 事件触发、编译与 PIE 仍**规格**。

### 4.3 索引一致性（P0，真实重扫仍缺）

现状（`CLAUDE.md` §4.2）：`UEAssetLensEditorModule.cpp` **已挂** `OnAssetAdded` / `OnAssetRemoved` /
`OnAssetUpdated` 委托，但历史实现只做计数标记、尚未触发真实重扫。离线事务已补 rescan request 与 lifecycle events，
可由 `revision.py` 产出确定性 `index_revision`；真实 UE 侧仍需：

1. 让已挂的 AssetRegistry 委托真正触发增量重扫（事件驱动重索引）；**规格**。
2. 在真实事务 `commit` 阶段按 `operations[].arguments` 收集受影响资产路径，强制增量重扫；**规格**。
3. 用注入 host executor 调官方 Toolset 并核验 `verify` 回读；**规格**。

推荐三者结合：事件监听保证人工编辑也同步，事务钩子保证 Agent 编辑在 `verify` 前已一致，host executor 保证真实写入。
副作用：需要重新评估 `CLAUDE.md`「MCP Server 只读连接 DuckDB」的边界——
**写索引只能由 Commandlet / host 事件侧完成，MCP 工具侧仍保持 `read_only=True`**，
离线事务通过触发重扫意图而非直接写库来满足该红线。

## 5. 需补充的自建 Toolset 清单

| 优先级 | 类别 | 能力 | 理由 | 状态 |
| --- | --- | --- | --- | --- |
| P0 | Query | 语义检索、16 维指纹查重、共现装配、TOON 批量召回 | 官方 0 命中，UAL 护城河 | **已实现** |
| P0 | Execute | 离线 dry-run 计划 / 审批 / 执行 / 验证 / 回滚 | 官方 0 命中，编辑可信的前提 | **已实现（离线）** / 真实 UE host **规格** |
| P0 | — | 索引一致性 | rescan request / apply-events 已落地，真实重扫未落地 | **已实现（离线）** / **规格（真实重扫）** |
| P1 | Query | 资产引用图谱语义查询、索引健康度检查 | 官方只有点查询，无图谱 / 相似度 | **已实现** |
| P1 | Edit | 语义感知批量替换、共现图批量装配、资产 diff 预览 | 官方有单次写，无「按语义结果批量改」 | **已实现（离线计划 / diff 预览）** / 真实写入 **规格** |
| P2 | Edit | 跨资产引用安全的重命名 / 移动 | 官方有 `RenameFolder` 等，缺引用事务性保障 | **规格**（真实 host executor） |

### 5.1 落地状态（2026-11-27）

本轮补齐了「特征收集 / 检索 / 依赖 / 离线事务 / diff / GameSpec」链路，**真实 UE host executor 仍待单独一轮**：

| 能力 | 状态 | 落点 |
| --- | --- | --- |
| 全资产领域特征收集 | **已实现**（通用反射 + Python M2 reference + C++ source-level zero-load domain extractor）；本地 UE6 UBT Build 已通过、CI 仍不编译 C++；真实 UE 端到端反射级深度规格 **规格** | `FUALTypedFeatureExtractor` / `UALDomainFeatureExtractor`（源码级）、`UALDomainProfiles.h`、`Server/ual_common/domain.py`、`Config/UAL/fields/default.yaml` |
| 共现图生成 | **已实现** | `UALIndexBuilderCommandlet` 同目录 + 名称 token Jaccard，每资产上限 8 条 `CO_OCCURS`；`DEPENDS_ON` 过滤 `/Script` 类引用 |
| 引用图谱语义查询 | **已实现** | `UEAssetLens.AssetGraphToolset.GetDependencies` / `GetReferencers`（有界递归 BFS，环检测） |
| 索引健康度 / 指纹查重 / 特征检索 | **已实现** | `GetIndexHealth` / `FindDuplicates` / `SearchByFeatures` / `ListFeatureKeys` |
| Runtime tool registry | **已实现** | `Config/UAL/extensions/tools.yaml` → `ExtensionRegistry.snapshot()` → `tool_schemas.yaml` `4.0`；10 个既有只读工具 + 20 个 runtime tools（共 30 个工具） |
| 逐工具 annotations | **已实现** | `accessMode` ∈ Read / Write / Execute；`readOnlyHint` 仅 Read 为 true；`Tools/audit_tool_annotations.py --check` 拦截漂移 |
| 计划 / 审批 / dry-run apply / 验证 / 回滚 / rescan / 五层 diff / GameSpec 验证循环 | **已实现（离线 dry-run）** | [事务与五层差异工件](transaction-and-diff.md)、[GameSpec 工作流](gamespec-workflow.md) |
| 真实 UE 写入、compile / PIE、AssetRegistry 事件重扫、UE 自动化用例 | **规格 / 未落地** | 离线门禁与 CI 均未执行；本地 UE6 UBT Build 已通过（`Result: Succeeded`）但 C++ 编译不在 CI，C++ 编译通过不改变此状态 |

阶段 2 `UToolsetDefinition` 迁移 **DECIDED AGAINST for now**：保持 `AddTool` + 显式限定名 / annotations，不引入双轨别名。
`Server/ual_mcp/tool_schemas.yaml` `schema_version` 为 **`4.0`**，与 DuckDB `schema_meta.schema_version` **`3.0`** 互不联动。
`GetAnnotations()` / generated annotations 必须按 `AIAccessMode` 分派，禁止硬编码 read。

**历史真实 UE6 索引验证（2026-11-24，不在本轮离线门禁复核）**：`ual-cli build --engine-cmd UnrealEditor.exe` 用模块限定名
`-run=UEAssetLensEditor.UALIndexBuilder` 扫描 `/Game`，产出 15 条 16 键记录、56 条边（其中 44 条
确定性 `CO_OCCURS`），staging YAML 可被 PyYAML 完整解析（0 非法控制字符），`feature.*` 10 个键
在全部 15 条记录上齐备。族分布为 Blueprint 13 / World 2，与工程当前 13 个骨架蓝图 + 2 张关卡一致。
过程中修掉两个真实缺陷：Blueprint `FiBData` 二进制 Tag 的 C1 控制字符以及 UE6 commandlet 成功仍返回非零退出码。
该历史记录不代表本轮重新编译 C++ 或重新执行 UE SDK 编译。

**历史 headless 端到端（2026-11-24，不在本轮离线门禁复核）**：编辑器内官方 MCP 注册 UAL 工具，`tools/call` 返回真实 TOON / YAML 结果。
注意官方 MCP 在编辑器执行慢任务期间会返回 `Not run: ... slow task`，这是 host 保护而非工具故障。

**禁止重复实现**（官方已具备，直接路由）：`FindAssets` / `GetAssetTags` / `CreateMaterial` /
`SetMaterial` / `Duplicate` / `Delete` / `SaveAsset` / `AddUserAssetTags` /
`SetActorTransform` / `CreateCollection` / `ValidateAssets` 等资产域工具。

## 6. 红线更新建议

保持不变：Zero-VLM / 100% 确定性离线、禁止外部生成式服务 / 云端推理 / GPU、禁止整库原始数据进上下文、禁止手写 YAML / TOON
字符串拼接、禁止 `content_hash` 查重、禁止加载贴图 / 网格进显存、输出不含本地路径 / 时间戳 / 随机 ID、
MCP 侧 DuckDB 只读连接、SQL 参数化、工具返回 `str`。

需要补充 / 当前已执行：

1. 允许 `Write` / `Execute` 运行时工具，但**必须经离线 transaction / gateway**（plan → 审批 → dry-run apply → verify），禁止裸写工具暴露给客户端；真实写入只经注入 host executor。
2. 所有工具显式标注 `AIAccessMode` / `readOnlyHint`；`readOnlyHint=true` 只允许 `Read`；`Tools/audit_tool_annotations.py --check` 拦截缺失 / 冲突标注。
3. 官方工具白名单化：事务层只允许调用 `Config/UAL/gateway/official_tools.yaml` 清单内的工具名，禁止任意路由。
4. Zero-VLM 扩展到编排层：离线事务确定性，不引入视觉模型或云端推理。
5. 禁止重复实现官方已有工具；新增工具前先查 `toolset-inventory.yaml`。
6. 官方 MCP / Toolset 为 `Experimental + NoRedist`，loopback-only、无认证、不可随发布包分发、禁止暴露到非 loopback 地址；本地 UE6 UBT Build 已通过（`Result: Succeeded`）但 **CI 仍不编译 C++**，真实 UE 写入 / 编辑器编译 / PIE / UE 自动化用例仍**规格 / 未落地**。
7. `Config/UAL/gateway/integrations.yaml` 中的 P1 / P2 外部服务保持 `disabled` / license-reviewed / Zero-VLM boundary，**not executed**；不得写成已启用集成或外部生成式服务调用。22 个新增 `ual-cli` 离线命令只产出有界 TOON / canonical YAML 工件，真实 UE host executor 仍**规格**。

## 7. 能力分级与降级

`UEAssetLens.uplugin` 中 `ModelContextProtocol` 是 `Enabled: true, Optional: true`，保持软依赖。

| 场景 | 可用能力 | 状态 |
| --- | --- | --- |
| UE6 + 官方 MCP 插件启用 | 10 个 Query 只读工具 + 20 个 runtime tools（共 30 个工具）；离线 dry-run 事务 / 五层 diff / GameSpec 工件 | **已实现（离线）**；真实 UE 写入 / compile / PIE **规格** |
| 官方插件缺失 / 非 UE6 | 降级为 Python stdio server 的只读工具 + `ual-cli`；真实编辑能力明确不可用 | **已实现**（降级路径） |
| 无 UE 引擎（CI / 离线） | Python server + DuckDB + 离线内核自检；pytest / generator / audit / benchmark | **已实现** |

官方 MCP / Toolset 均为 `Experimental + NoRedist`，**只能开发期启用，不可随发布包分发**；
server 仅 loopback 且无认证，禁止暴露到非 loopback 地址。README、[项目定位](positioning.md)、站点 [首页](index.md) 与 [快速开始](quickstart.md) 均已写明该分级，
避免用户误以为装了 UAL 就能编辑真实资产。

## 8. 统一入口

现存在两条入口：C++ `UEAssetLensMCP` 用 `AddTool` 注册进官方 HTTP server（内部经 YAML
请求文件桥接 `engine_bridge.py`），同时 `Tools/setup_mcp.ps1` 让客户端直连独立 Python stdio server 或预览 HTTP 配置。
目标：

- 客户端主配置 `http://localhost:8000/mcp`（HTTP）；stdio 仅作为第 7 节的降级路径保留、不删。
- `setup_mcp.ps1` 默认 HTTP 模式，支持 `-DryRun -Transport Http -ConfigRoot <TempConfigRoot>`；现有 stdio 模式保留但标注为降级。
- 所有工具在两条路径上保持**同一个限定名**（由 `Config/UAL/extensions/tools.yaml` 的 `mcp_name` 经 registry snapshot 唯一决定）、同 schema、同输出；禁止裸名注册。
- HTTP 仅 loopback、无认证、Experimental + NoRedist，绝不随发布包分发。

## 9. 验证与门禁

新增任何工具或改签名，必须按顺序完成：

1. 改 `tools.py::TOOL_DEFS`（既有静态工具）或 `Config/UAL/extensions/tools.yaml`（runtime tools，含 `mcp_name`、`accessMode`、`inputSchema`），并同步 `server.py` 的 `@sdk.tool(name=...)` / `register_runtime_tools(sdk, con, RUNTIME_TOOL_DEFS)`；阶段 2 不迁移到 `UToolsetDefinition`。
2. 跑 `python Tools/gen_docs.py`（更新 `tool_schemas.yaml`、`docs/reference/mcp-tools.md`）；2026-11-27 已重跑并经 `--check` 确认零漂移。
3. 同步 `Server/schema/PROTOCOL.md` 与 `CLAUDE.md` 相关章节；DuckDB `3.0` 与 tool schema `4.0` 不联动。
4. `python Tools/gen_docs.py --check`、`python Tools/gen_taxonomy.py --check`、`python Tools/audit_tool_annotations.py --check`、`python Tools/audit_engine_toolsets.py --check`；
5. `python -m pytest Server/tests -q`、`python Server/ual_cli/ual_cli.py self-test`、
   `python Server/ual_mcp/server.py --self-test`、`python Server/ual_mcp/engine_bridge.py --self-test`；
6. `python -m mkdocs build --strict`、`python Tools/bench_index.py --count 10000 --incremental`；
7. C++ 侧改动需在有合格 MSVC 工具链的环境单独编译 `AnnEditor Win64 Development`；2026-11-27 用户明确授权后本地 UE6 UBT Build 已通过（`Result: Succeeded`，19 个 action、UHT 写入 4 个生成文件、UBA 97.24 秒、总耗时 136.42 秒、输出 `UnrealEditor.exe`、仅 C4996 `UE_LOG` 弃用告警、无编译错误，见 [已知限制](known-limitations.md) §1），此前非编译类 UBT 检查与 22 个翻译单元的 MSVC `/Zs` 仅语法检查仍只是源码级证据；**CI 仍不编译 C++**。22 个新增 `ual-cli` 离线内核命令与 `audit_engine_toolsets.py --check` 由 `python-ci.yml` / `Tools/run_tests.ps1` 校验，2026-11-27 完整离线门禁已全部通过。

## 10. 待决事项（已决项保留为记录）

1. 阶段 2 是否迁移到 `UToolsetDefinition`：**DECIDED AGAINST for now**。保持 `AddTool` + 显式限定名 / annotations，不进入官方统一工具目录，不引入双轨别名。
2. Python stdio server 的长期定位：仅降级路径，还是与 HTTP 双轨并存；当前按第 7 节降级路径处理。
3. 审批粒度：逐操作确认 vs 每个 plan 一次确认；默认逐 `Write` / `Execute` 操作确认。
4. 事务层调用官方工具的机制：**规格**。本轮已有 2026-11-27 本地 UE6 UBT Build 通过（`Result: Succeeded`），但 C++ 编译不在 CI；真实官方 Toolset 调用、`compile` / PIE / 真实 UE 写入仍**规格 / 未落地**。离线路径保留 Python transaction / gateway + 注入 executor；真实官方 Toolset 调用、`FindTool` 可见性、C++ 薄层、`compile` / PIE 均需宿主实机验证。路径 A（Python 子进程 HTTP 回环 `call_tool`）与 `CLAUDE.md` 的「只用 duckdb / PyYAML / mcp 三依赖、禁止手写协议层」冲突；路径 B（C++ 直调 `ToolsetRegistry`）需在 `UEAssetLensMCP.Build.cs` 加模块依赖。折中建议仍是：**Read / dry-run 留 Python，真实 Write / Execute 只在注入 host executor 中经官方白名单执行**，白名单对齐父工程 `Docs/ai-capabilities/toolset-inventory.yaml`。此决策需宿主实机验证后实施，当前为**规格**。
