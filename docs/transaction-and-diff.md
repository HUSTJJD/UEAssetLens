# 事务与五层差异工件

> 本文只描述离线事务与 diff 工件，不定义产品定位；「UAL 是什么」唯一事实源是 **[项目定位与能力规格](positioning.md)**。状态使用 **已实现 / 规格 / 未落地**。

## 1. 离线事务与网关

`Server/ual_common/transaction.py` 与 `gateway.py` 已实现离线 dry-run 事务：**已实现（离线）**。它们不写 `.uasset`、不写 DuckDB 索引，也不调用真实 UE；完整 payload 由 `Server/ual_common/artifacts.py` 写成规范 YAML，模型侧只收到有界 TOON 摘要、仓库相对工件指针与 `canonical_sha256`。

| 运行时工具 | accessMode | 离线行为 | 状态 |
| --- | --- | --- | --- |
| `UEAssetLens.AssetEditTransactionToolset.PlanEdit` | Read | 确定性 `plan_id`、白名单 op、`inverse`、diff preview | **已实现（离线）** |
| `UEAssetLens.AssetEditTransactionToolset.ApplyPlan` | Write | `approved_by` + 显式 approval ids；`dry_run` 默认 true；缺席 executor 时 deferred offline | **已实现（离线 dry-run）** |
| `UEAssetLens.AssetEditTransactionToolset.VerifyEdit` | Read | 有界 per-op 结果、`index_consistent`、residual risks | **已实现（离线）** |
| `UEAssetLens.AssetEditTransactionToolset.RollbackEdit` | Write | 使用 plan 阶段生成的 `inverse_ops`，默认 dry-run | **已实现（离线 dry-run）** |
| `UEAssetLens.AssetKernelToolset.RequestRescan` | Execute | 排序后的 `/Game` 路径与 audit reason 写入 rescan 工件 | **已实现（离线）** |
| `UEAssetLens.AssetKernelToolset.GetLifecycleEvents` | Read | 有界 lifecycle / audit 事件报告 | **已实现（离线）** |
| 真实 UE host executor / PIE / 索引事件重扫 | Write / Execute | 需要宿主编辑器与官方 Toolset；离线门禁与 CI 不执行 | **规格** |

审批规则：`Write` / `Execute` 操作必须逐项确认；`operations[].tool` 必须命中 `Config/UAL/gateway/official_tools.yaml` 中的官方工具白名单；`inverse` 在 plan 阶段生成，不依赖运行期 best-effort 恢复。MCP 侧 DuckDB 连接始终 `read_only=True`，写索引只能由 Commandlet / host 事件侧完成；当前离线实现只 stage rescan 请求，真实重扫执行器仍是 **规格**。

所有 YAML / TOON 由 canonical emitter 产出，禁止手写字符串拼接；工件和报告不得包含本地绝对路径、时间戳、随机 ID 或全量原始索引。

## 2. 五层 diff 工件

`Server/ual_common/asset_diff.py` 与 `Config/UAL/diff/layers.yaml` 已实现 L0–L4 的离线规范化投影：**已实现（离线投影）**。

| 层 | 离线来源 | 状态 |
| --- | --- | --- |
| L0 metadata | 只读索引元数据字段 | **已实现** |
| L1 reflection maps | `interface_spec` / `domain_spec` 的确定性文本投影 | **已实现（离线投影）** |
| L2 graph | `asset_edges` 的稳定排序投影 | **已实现** |
| L3 payload | `yaml_cache` 规范化文本 | **已实现（离线投影）** |
| L4 digest | L0–L3 canonical 文本摘要 | **已实现（离线投影）** |

- **已实现**：`DiffAssets` 写完整五层 canonical YAML 工件，只返回有界 TOON 摘要、工件指针、`canonical_sha256` 与分页游标；`ReadDiffLayer` 按 offset / limit 分页；`VerifyDiffDeterminism` 逐字节复验。
- **规格**：UE 原生 `PPF_ForDiff` / Blueprint Graph DSL / 原始二进制规范化导出，需要真实引擎与官方 6 个 Diff 工具；Lens 不自研 UObject 解析器。
- **未落地**：真实 UE 写入 / 编辑器编译 / PIE / UE 自动化用例与真实二进制导出（本地 UE6 UBT Build 已通过、`Result: Succeeded`，但 CI 仍不编译 C++；见 [已知限制](known-limitations.md) §1）；离线 L1/L3/L4 不等价于 UE 反射级 / 二进制级 diff。
- **红线**：不加载 UObject、不导出整库原始数据、不调用 VLM / 云 / GPU / 外部生成式服务；`content_hash` 不做查重。

## 3. 关联

- [MCP 定位与事务层规格](mcp-positioning.md)：`AddTool` 决策、`tool_schemas.yaml` `4.0` 与真实 host executor 边界。
- [扩展体系](extensions.md)：九个 namespace、四级偏好、custom rules、allowlist。
- [已知限制](known-limitations.md)：C++ 编译与真实 UE 写入 / PIE 未验证项。
- [离线内核实现](kernel-implementation.md)：模块级状态总览。

## 4. `ual-cli` 命令入口与边界

事务 / diff / 审计侧的离线命令：`plan-edit`、`apply-plan`、`verify-edit`、`rollback-edit`、
`diff-assets`、`read-diff-layer`、`verify-diff`、`request-rescan`、`apply-events`、
`get-lifecycle-events`、`list-official-tools`、`get-index-revision`、`allowlist-audit`。
其余离线命令为 `list-extensions`、`list-extension-fields`、`search-extension-fields`、
`get-assembly-graph`、`expand-gamespec`、`run-validation-loop`、`run-workflow`、`read-artifact`、
`import-gamespec`；合计 22 个，
状态为 **已实现（离线；2026-11-27 完整离线门禁已全部通过）**。完整用法见 [快速开始](quickstart.md) §2。

CI 还运行 `Tools/audit_tool_annotations.py --check` 与 `Tools/audit_engine_toolsets.py --check`，
分别校验工具 annotations / meta 与离线 `tools/list` inventory；二者只读，不编辑生成物。
`Config/UAL/gateway/integrations.yaml` 中的 P1 / P2 外部服务保持 `disabled` / license-reviewed /
Zero-VLM boundary，**not executed**。本地 UE6 UBT Build 已通过（2026-11-27 用户明确授权，`Result: Succeeded`，见 [已知限制](known-limitations.md) §1），此前非编译类 UBT 检查与 22 个翻译单元的 MSVC `/Zs` 仅语法检查仍只是源码级证据；**CI 仍不编译 C++**，真实 UE 写入 / 编辑器编译 / PIE / UE 自动化用例仍 **规格 / 未落地**。「UAL 是什么」唯一事实源仍是
[项目定位与能力规格](positioning.md)。
