# 扩展体系

> 本文只描述扩展框架与治理，不定义产品定位；「UAL 是什么 / 完整能力」唯一事实源是 **[项目定位与能力规格](positioning.md)**。状态使用 **已实现 / 规格 / 未落地**。

## 1. 九个 namespace

`Server/ual_common/registry.py::NAMESPACES` 固定九个扩展点，`Server/ual_common/extensions.py` 从 `Config/UAL/extensions/*.yaml` 装载声明式 manifest 并合并为 `ExtensionRegistry` 快照：**已实现**。

| namespace | 自定义什么 | 状态 |
| --- | --- | --- |
| `asset_type` | 资产角色、用途、装配关系、类型化描述 | **已实现（离线描述）** / **规格**（UE 深度反射） |
| `policy` | 命名 / 目录 / 格式 / 引用 / custom 规则 | **已实现** |
| `field` | 类型化自定义字段、默认值、类型投影 | **已实现** |
| `diff` | L0–L4 规范化层定义 | **已实现（离线投影）** / **规格**（UE 原生 diff） |
| `tool` | 运行时 MCP 工具 schema、限定名、accessMode / readOnlyHint | **已实现** |
| `lifecycle` | OnPreValidate / OnIndexed / OnPlanned / OnCommitted / OnPublished 审计钩子 | **已实现（离线事件）** / **规格**（真实引擎事件） |
| `sink` | canonical YAML artifact sink | **已实现（离线）** / **规格**（DuckDB C API Appender） |
| `template` | FPS 3C GameSpec 模板与十步链 | **已实现（离线）** |
| `gateway` | 官方 Toolset 白名单与注入 host executor | **已实现（离线校验）** / **规格**（真实 UE 写入） |

## 2. 四层偏好

`Server/ual_common/settings.py` 按 **builtin < plugin < project < user** 合并 `Config/UAL/` 与 `<Project>/Saved/Config/UAL/`：dict 递归深合并、规则按 `id` 覆盖并保持首次出现顺序、`enabled: false` 可禁用内置项。`provenance` 只用于诊断，不进入确定性产物。团队规范入 Git，个人覆盖不入库；所有规则都是数据，不是可执行代码。

`extensions.py` 只允许解析 `ual_common.extensions_builtins` 下的固定 callable；manifest 出现 `impl` / `handler` / `eval` / `exec` / `code` / `script` 之类的任意代码引用会被拒绝或忽略：**已实现**。因此扩展点是声明式配置覆盖，不引入用户代码执行路径。

## 3. custom policy rules

`Config/UAL/policies/default.yaml` 中的 `kind: custom` 规则通过 `extension` 指向 `Config/UAL/extensions/builtin.yaml` 注册的 builtin 谓词，`PolicyEngine` 从 `ExtensionRegistry` 的 `policy` namespace 解析：`lint-policy` 已装载声明式扩展清单，**已实现**。

| 约束 | 状态 |
| --- | --- |
| 自定义谓词来自内置 allowlist，不从 YAML 导入任意模块 | **已实现** |
| 默认 `enforce: false`，报告优先；`--strict` / `enforce: true` 才阻断 | **已实现** |
| 四级偏好覆盖与确定性的有界 TOON 报告 | **已实现** |
| 真实 UE 反射级校验 | **规格** |

详见 [CI 门禁](ci-gates.md) §3 与 [扩展体系需求追踪](requirements-traceability.md) C5/C6/C9。

## 4. allowlist review gating

`Config/UAL/allowlists/default.yaml` 与 `Server/ual_common/allowlist.py` 已实现版本化白名单：**已实现**。

- `duplicates` / `references` 条目必须带 `reason` / `owner` / `review_by`；过期或无效条目不豁免门禁。
- 调用侧必须提供 `approved_by` 与 `review_ref`；缺少审批引用时不抑制报告。
- `pair` 按无序对精确匹配；`rule` / `path` 必须精确，禁止通配符。
- 有界 `allowlist_issues` TOON 报告不输出本地路径、时间戳、随机 ID、reviewer 或原始 payload。
- 白名单只豁免报告，不改指纹、索引或 Core Record；`content_hash` 不参与查重。

状态边界：白名单机制、过期校验、review gate 为 **已实现**；把 white-list 审批接入真实 GitHub code review 自动化仍为 **规格 / 未落地**。详见 [CI 门禁](ci-gates.md) §5。

## 5. 关联

## 6. 命令入口与边界

扩展 / 字段 / 治理侧离线命令：`list-extensions`、`list-extension-fields`、`search-extension-fields`、
`get-assembly-graph`、`get-index-revision`、`list-official-tools`、`get-lifecycle-events`、
`allowlist-audit`。22 个 `ual-cli` 离线命令完整清单见 [快速开始](quickstart.md) §2 与
[离线内核实现](kernel-implementation.md) §5，状态为 **已实现（离线；2026-11-27 完整离线门禁已全部通过）**。

CI 还运行 `Tools/audit_tool_annotations.py --check` 与 `Tools/audit_engine_toolsets.py --check`：
前者校验 `tool_schemas.yaml` 的限定名 / annotations / meta，后者校验离线 `tools/list` inventory，均为只读门禁。
`Config/UAL/gateway/integrations.yaml` 中的 P1 / P2 外部服务保持 `disabled` / license-reviewed /
Zero-VLM boundary，**not executed**；YAML 不允许任意代码执行，也不接外部生成式服务。
本地 UE6 UBT Build 已通过（2026-11-27 用户明确授权，`Result: Succeeded`，见 [已知限制](known-limitations.md) §1），
此前非编译类 UBT 检查与 22 个翻译单元的 MSVC `/Zs` 仅语法检查仍只是源码级证据；**CI 仍不编译 C++**，
真实引擎事件 / 写入 / 编辑器编译 / PIE / UE 自动化用例仍 **规格 / 未落地**。

[离线内核实现](kernel-implementation.md) · [事务与五层差异工件](transaction-and-diff.md) · [GameSpec / FPS 3C 工作流](gamespec-workflow.md) · [已知限制](known-limitations.md) · [项目定位与能力规格](positioning.md)（唯一定义源）。
