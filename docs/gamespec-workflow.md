# GameSpec / FPS 3C 工作流

> 本文只描述离线编排，不定义产品定位；「UAL 是什么」唯一事实源是 **[项目定位与能力规格](positioning.md)**。状态使用 **已实现 / 规格 / 未落地**。

`Server/ual_common/workflow.py` 提供确定性的十步 FPS 3C 链：完整 GameSpec / 工件留在仓库相对路径下，模型侧只得到十行有界 TOON 与工件指针。`Config/UAL/templates/fps_3c.yaml` 是声明式模板数据，`Server/ual_common/gamespec.py` 负责域 / 实体 / 规则 / 输入 / UI / 验收断言的展开与离线验证。

## 1. 十步链

| 步 | `STEP_NAMES` | Lens 做什么 | 状态 |
| --- | --- | --- | --- |
| 1 | `intent_to_gamespec` | 匹配受支持的 FPS / 3C / shooter 关键词，确定 GameSpec id | **已实现（离线）** |
| 2 | `template_expansion` | 展开 18 个资产、装配关系、白名单操作与断言 | **已实现（离线）** |
| 3 | `retrieve_assets` | 复用只读索引 / 图谱 / 特征检索，有界返回 | **已实现** |
| 4 | `policy_gate` | 四级偏好、`custom` 规则、allowlist review gating | **已实现** |
| 5 | `plan_edit` | 生成 dry-run plan、inverse、diff preview、确定性 plan_id | **已实现（离线）** |
| 6 | `approval_dry_run` | 按 `accessMode` 逐项审批，默认不执行 Write | **已实现（离线）** |
| 7 | `apply_offline` | 仅在注入 host executor 时委托；否则 deferred offline | **已实现（离线）** / **规格**（真实 UE 写入） |
| 8 | `diff_assets` | 五层 canonical diff 工件 + 有界摘要，详见 [事务与五层差异工件](transaction-and-diff.md) | **已实现（离线投影）** |
| 9 | `verify_and_validate` | compile / PIE / snapshot / assert / repair / rollback 阶段的有界报告 | **已实现（离线报告）** / **规格**（真实 compile / PIE） |
| 10 | `rollback_fallback` | 使用 plan-time inverse 与 rescan 工件回退到一致状态 | **已实现（离线）** / **规格**（真实资产回滚） |

## 2. 离线 runner 边界

- **已实现**：模板只含数据；`gamespec.py` 拒绝 `impl` / `handler` / `eval` / `exec` / `code` / `script` 等可执行引用；操作名必须命中官方工具白名单；完整结果由 `artifacts.py` canonical emitter 写盘。
- **已实现（离线）**：`RunValidationLoop` 的默认 runner 是 `offline`，`dry_run=true`，验证阶段状态为 `deferred_offline`；断言只检查 schema、唯一 ID、装配解析、白名单、阶段列表、payload 噪声键等确定性条件。
- **规格**：真实 UE 编译、PIE、状态快照、引擎内断言、host 回编排层修正与失败回滚，需要宿主 runner；离线门禁未执行，CI 也不编译 C++。
- **未落地**：外部生成式服务、VLM、云端推理、GPU、视觉模型；自然语言 → GameSpec 由宿主模型负责，Lens 不生成美术、不决定玩法、不直接写资产。

## 3. 输出约束

- 批量输出只走 `ual_common/toon.py`；完整 GameSpec / validation 工件只走 `ual_common/artifacts.py` canonical YAML。
- 禁止手写 TOON / YAML 字符串拼接；禁止把整库原始索引或完整 payload 灌进 LLM 上下文。
- plan_id / GameSpec id / diff id 为确定性内容摘要；工件不含本地绝对路径、时间戳、随机 ID。
- `content_hash` 只用于增量变更检测；查重只用 `fingerprint_vec`。
- 官方 MCP / Toolset 为 `Experimental + NoRedist`，loopback-only、无认证、不可随发布包分发。

## 4. 命令入口与 CI 边界

GameSpec / workflow 侧离线命令：`expand-gamespec`、`run-validation-loop`、`run-workflow`，
配合 `plan-edit`、`apply-plan`、`verify-edit`、`rollback-edit`、`diff-assets`、`read-diff-layer`、
`verify-diff`、`request-rescan`、`apply-events`、`get-lifecycle-events`、`allowlist-audit`。
22 个 `ual-cli` 离线命令完整清单见 [快速开始](quickstart.md) §2 与
[离线内核实现](kernel-implementation.md) §5，状态为 **已实现（离线；2026-11-27 完整离线门禁已全部通过）**。

CI 还运行 `gen_docs.py --check`、`gen_taxonomy.py --check`、`audit_tool_annotations.py --check`、
`audit_engine_toolsets.py --check`、`server.py --self-test`、`engine_bridge.py --self-test`、
`bench_index.py --count 10000 --incremental` 与 HTTP setup DryRun。所有检查不编译 C++、不接外部生成式服务；
`Config/UAL/gateway/integrations.yaml` 中的 P1 / P2 外部服务保持 `disabled` / license-reviewed /
Zero-VLM boundary，**not executed**。本地 UE6 UBT Build 已通过（2026-11-27 用户明确授权，`Result: Succeeded`，见 [已知限制](known-limitations.md) §1），此前非编译类 UBT 检查与 22 个翻译单元的 MSVC `/Zs` 仅语法检查仍只是源码级证据；**CI 仍不编译 C++**，真实 compile / PIE / UE 自动化用例仍 **规格 / 未落地**。「UAL 是什么」唯一事实源仍是 [项目定位与能力规格](positioning.md)。

关联：[规格主文](asset-lifecycle-architecture.md) §6、[离线内核实现](kernel-implementation.md)、[需求追踪矩阵](requirements-traceability.md)、[已知限制](known-limitations.md)。
