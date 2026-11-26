# UEAssetLens 定位与能力规格（唯一事实源）

<!-- 人工维护页；不由 Tools/gen_docs.py 生成。 -->

> **本文是 UEAssetLens「是什么 / 要成为什么 / 现在做到哪一步」的唯一事实源**，也是
> 2026-11-24 定位升级的规格摘要。README、[站点首页](index.md)、[MCP 规格](mcp-positioning.md)、
> [生命周期规格主文](asset-lifecycle-architecture.md) 与 `mkdocs.yml` 的站点描述只做
> **摘要 + 链接**，不再各自重写一句新的定位。状态口径以 **2026-11-27** 为准；
> 改定位时先改本页，再同步第 7 节列出的引用点。

## 1. 一句话定位（规格）

> 零 VLM 依赖的 Unreal Engine **资产内核（Asset Kernel）**——超大规模游戏开发中所有底层资源交互的唯一入口，覆盖资产的检索、复用、管理、开发、编辑、校验与演化全生命周期；向上服务 AI 工作流，向外提供可注册扩展与可覆盖策略。**当前已交付**只读语义检索中枢与 Phase 0 骨架，其余为**规格目标**。

这一句只允许有一份措辞。README 首段、[站点首页](index.md) 首屏与 `mkdocs.yml` 的
`site_description` 都从本页复制；渲染层可以去掉 Markdown 强调、把全角括号写成半角，
但**不得改写措辞或另加一句新定义**。该句是**正式规格原文**，其措辞不随离线实现增量改写；
Phase 1–5 的离线代码状态以 §3、§8、[离线内核实现](kernel-implementation.md) 与
[已知限制](known-limitations.md) 的逐项状态为准。

| 视角 | 陈述 | 状态 |
| --- | --- | --- |
| 规格定位 | **资产内核（Asset Kernel）**：所有底层资源交互的唯一入口，覆盖检索、复用、管理、开发、编辑、校验、演化全生命周期 | **规格**（Phase 0 与 Phase 1–5 离线内核代码已存在；真实 UE host 仍为规格） |
| 今天已实现 | 零 VLM 确定性索引、10 个只读工具 + 20 个 registry-driven runtime tools（共 30 个工具）、DuckDB v3.0 冻结 16 键 Core Record + 类型化 `extension_payload`、`tool_schemas.yaml` v4.0、四级偏好 / custom rules / allowlist、离线事务 / 网关 / 五层 diff / GameSpec / 生命周期审计 / 基准 | **已实现**（离线；由 CI / 自检门禁覆盖，真实 UE 写入未验证） |
| 在 UE MCP 生态里 | Epic 官方 MCP 的**统一资产工具层**与**离线 dry-run 事务层**；真实写操作需经已批准 transaction / gateway / 注入 host executor | 工具层与离线事务层 **已实现** / 真实 UE host executor **规格** |
| 明确未落地 | 真实 UE 写入 / 编辑器编译 / PIE 验证、UE 自动化用例、外部生成式服务、UE 原生二进制级 diff 解析 | **未落地 / 规格** |

!!! note "状态标记约定"
    全文统一使用三种状态：**已实现**（有离线代码且由自检 / CI 门禁覆盖）、**规格**（有设计文档、无真实 UE host 实现）、
    **未落地**（已知缺口或未验证项）。引用定位与能力时必须带状态，禁止把「规格」写成「已验证结论」——
    这条口径与 [已知限制](known-limitations.md) 开篇一致。

「资产内核」是本项目的**正式规格定位**，不是历史材料，也不是对当前已交付能力的描述。
两种误读都要避免：把它当成已经做完的能力，或把它降级成废弃草稿。UAL 今天把二进制资产
变成可检索、可比对、可校验、可 diff 的结构化索引，并有 Phase 1–5 的离线内核代码；规格要求它继续成为 AI 工作流与
人类美术共用的资源底座——全程零 VLM、零云端推理、零外部生成式服务、零外部 GPU。

!!! note "规格来源（2026-11-24 定位升级）"
    WorkBuddy 会话里的原始诉求：UAL 还要涵盖资产的**命名 / 目录 / 格式规范**，服务于
    超大规模游戏开发中「检索、复用、管理、开发、编辑」全链路全生命周期；
    **既要有框架支撑，又要给用户自由度调整偏好**；要非常好地服务于基于 AI 的工作流
    （「一句话做 FPS 3C」→ 自动找到合适的美术资产、判断需要新建哪些资产、管理最终产出）；
    「所有底层资源交互都应该由 Lens 来支持」；两个资产的 diff 要「准确、毫无保留」；
    整个架构应是**插件 / 注册式**，支持多方向拓展点，方便用户基于框架对各类资产做自定义拓展。
    本页与 [规格主文](asset-lifecycle-architecture.md) 共同把它落成带状态的 spec。

## 2. 它解决的问题与职责边界

- 二进制 `.uasset` 对人和模型都是黑盒：路径与命名不足以回答「这个动画是不是已经有了」。
- 把整库原始数据塞进上下文既撑爆窗口，又因为绝对路径 / 时间戳 / 随机 ID 让 CI diff 永远脏。
- 视觉多模态模型、云端推理和外部生成式服务在 CI 里不可复现、不可离线、成本不可控。
- 超大规模工程里，AI 工作流无法可靠回答「有哪些可复用资产、该新建什么、改了什么、
  按什么规矩改、改完如何验证与回滚」。
- UAL 的答案：**UE 反射属性 + 确定性数学 + 引用图谱 + 声明式规范** →
  DuckDB / YAML / TOON 三层有界输出 → 资源网关与事务底座。
  批量路径只走 `ual_common/toon.py`，单资产精读只走 `ual_common/yamlspec.py`，完整规范工件只走
  `ual_common/artifacts.py` canonical emitter。

> Lens 不负责「决定做什么」，负责「准确地知道有什么、改了什么、该按什么规矩做、
> 以及如何安全地做」。自然语言 → GameSpec 由宿主模型负责；官方 Toolset 负责具体写操作；
> Lens 提供知识、策略、网关、dry-run 事务底座与有界工件。真实 UE 写入 / compile / PIE 仍是**规格**。

三条不可让渡的原则（来自 [规格主文](asset-lifecycle-architecture.md) §0）：

1. **框架给能力，数据给自由**：命名 / 目录 / 格式 / 族 / 特征键 / 模板 / 工具 / diff 层都是声明式数据，
   可分层覆盖；框架只提供加载、求值、注册与执行机制，不执行 YAML 中的任意代码。
2. **零重写官方能力**：UE6 已提供 878 个唯一工具（Read 409 / Write 398 / Execute 28）、
   `AssetRegistry` 依赖图、`PPF_ForDiff` 属性文本化与 6 个 Diff 工具；Lens 只做编排、
   白名单与确定性封装。
3. **确定性优先于丰富度**：同一输入 + 同一 `index_revision` 必须产出逐字节相同的
   spec / plan / diff，否则无法进 Git、无法 review、无法 CI 重放。

## 3. 能力全景（完整清单，逐项带状态）

本表回答「UAL 能做什么、要做到什么」。按用户提出的全生命周期七域与横切框架能力列出；
每项的**状态**必须连同引用一起读。详细设计见 [规格主文](asset-lifecycle-architecture.md)，
离线实现见 [内核实现](kernel-implementation.md)，追踪矩阵见 [C1–C20](requirements-traceability.md)，
工具签名见 [MCP 工具参考](reference/mcp-tools.md)，命令见 [ual-cli 命令参考](reference/cli.md)。

### 3.1 检索 / 复用

| 域 | 能力 | 交付形态 | 状态 | 落点 |
| --- | --- | --- | --- | --- |
| 检索 | 确定性语义索引（冻结 16 键 Core Record、16 维指纹、FTS BM25、DuckDB v3.0） | DuckDB + 6 个语义 Query 工具 | **已实现** | [三层协议](reference/protocol.md)、[存储参考](reference/schema.md) |
| 检索 | 类型化自定义字段（`extension_payload` 表、字段注册表、有界查询） | `field` namespace + `fields.py` | **已实现** | [扩展体系](extensions.md)、[内核实现](kernel-implementation.md) |
| 检索 | 全类型特征收集、24 族 taxonomy、通用反射兜底、Python M2 reference + C++ source-level zero-load domain extractor | `UALDomainFeatureExtractor` / `UALDomainProfiles.h` + `domain.py` + `SearchByFeatures` / `ListFeatureKeys` | **已实现**（通用索引 / Python M2 / C++ 源码级 zero-load；本地 UE6 UBT Build 已通过、CI 仍不编译 C++）；真实 UE 端到端反射级深度规格 **规格** | [架构说明](architecture.md)、[已知限制](known-limitations.md) §3 / §14 |
| 检索 | 依赖与影响面图谱（有界多跳 BFS、反向引用、索引健康度） | 4 个图谱工具 | **已实现** | [MCP 工具参考](reference/mcp-tools.md) |
| 检索 | 共现装配与有界批量召回、上下文预算 | TOON / YAML / artifacts 唯一实现 | **已实现** | [三层协议](reference/protocol.md) |
| 复用 | 指纹防重复检测 + CI 阻断 + review-gated allowlist | `lint-duplicates` + `allowlist.py` | **已实现** | [CI 门禁](ci-gates.md) §1 / §5 |
| 复用 | 相似资产 / 配套资产包推荐（3C 工作流第 3 步） | `FindSimilarAssets` / `GetCooccurringBundle` | **已实现**；语义装配关系由声明式模板**离线实现** | [规格主文](asset-lifecycle-architecture.md) §6、[GameSpec 工作流](gamespec-workflow.md) |

### 3.2 管理 / 开发 / 编辑 / 校验 / 演化

| 域 | 能力 | 交付形态 | 状态 | 落点 |
| --- | --- | --- | --- | --- |
| 管理 | 命名 / 目录 / 格式 / 引用规范、custom rules、有界报告 | `PolicyEngine` + `lint-policy` + 声明式扩展清单 | **已实现** | [CI 门禁](ci-gates.md) §3、[扩展体系](extensions.md) |
| 管理 | 团队 / 个人偏好覆盖（builtin < plugin < project < user） | `settings.py` 四级合并 | **已实现** | [规格主文](asset-lifecycle-architecture.md) §4 |
| 管理 | 生命周期钩子、审计、发布、apply-events | `lifecycle.py` + `lifecycle` namespace | **已实现（离线事件）** / 真实引擎发布 **规格** | [内核实现](kernel-implementation.md) |
| 管理 | 资产角色 / 用途 / 装配关系知识层 | `asset_type` 扩展 + `Config/UAL/templates/fps_3c.yaml` | **已实现（离线声明式）** / UE 反射级 **规格** | [GameSpec 工作流](gamespec-workflow.md) |
| 开发 | GameSpec（域 / 实体 / 规则 / 输入 / UI / 验收断言）+ FPS 3C 模板展开 | `gamespec.py` + `UEYaml` / `artifacts.py` | **已实现（离线展开）** / 自然语言生成 **规格** | [GameSpec 工作流](gamespec-workflow.md) |
| 开发 | 玩法模板库（FPS 3C → 资产清单 + 装配关系 + 操作序列） | `template` namespace | **已实现（离线）** | [规格主文](asset-lifecycle-architecture.md) §6 |
| 开发 | 新建 / 装配角色蓝图、动画蓝图、混合空间、Lua 脚本、AI | 官方 Toolset 白名单 + 资源网关 | **规格**（真实 UE host executor） | [事务与 diff](transaction-and-diff.md) |
| 编辑 | Plan / Apply / Verify / Rollback、审批、dry-run 默认、inverse | `AssetEditTransactionToolset` runtime tools | **已实现（离线 dry-run）** / 真实写入 **规格** | [MCP 规格](mcp-positioning.md)、[事务与 diff](transaction-and-diff.md) |
| 编辑 | 逐 op 权限标注（`annotations.accessMode`）+ 官方工具白名单 | 资源网关 + `tool_schemas.yaml` v4.0 | **已实现** | [MCP 规格](mcp-positioning.md) §5.1、[扩展体系](extensions.md) |
| 编辑 | 索引一致性（rescan request / apply-events / 增量重扫意图） | 离线工件 + host executor | 离线请求 **已实现** / 真实重扫 **规格** | [事务与 diff](transaction-and-diff.md) |
| 校验 | 引用完整性门禁（资产路径 / 骨架 / 材质参数） | `verify-references` | **已实现**（正则启发式） | [CI 门禁](ci-gates.md) §2 |
| 校验 | 改前改后五层 diff + 确定性复验 + 分页工件 | `asset_diff.py` + `Config/UAL/diff/layers.yaml` | **已实现（离线投影）** / UE 原生二进制 diff **规格** | [事务与 diff](transaction-and-diff.md) |
| 校验 | 验证闭环（compile / PIE / snapshot / assert / repair / rollback） | `RunValidationLoop` + 注入 host runner | 离线 runner **已实现** / 真实编译与 PIE **规格** | [GameSpec 工作流](gamespec-workflow.md) |
| 演化 | schema 演进 / 类型化自定义字段 | `field` 扩展 + `extension_payload` 表（v3.0） | **已实现** | [规格主文](asset-lifecycle-architecture.md) §7 |
| 演化 | 扩展清单与运行时工具注册（放宽生成器 AST 字面量强校验；既有 `TOOL_DEFS` + 20 个 runtime tools（共 30 个工具） 合并发布） | `tools.py::TOOL_DEFS` + `ExtensionRegistry.snapshot()` → `tool_schemas.yaml` v4.0 | **已实现** | [内核实现](kernel-implementation.md)、[扩展体系](extensions.md) |
| 演化 | `index_revision`、确定性产物、Git / CI 可重放 | `revision.py` + canonical artifacts | **已实现** | [规格主文](asset-lifecycle-architecture.md) §8 |

### 3.3 框架与扩展（插件 / 注册式）

九个扩展命名空间共用 `ExtensionRegistry`，按 `namespace + name` 唯一注册，
按 **能力 + 资产类** priority 分派（规格见 [规格主文](asset-lifecycle-architecture.md) §3，落地见 [扩展体系](extensions.md)）：

| namespace | 自定义什么 | 状态 |
| --- | --- | --- |
| `asset_type` | 每类资产的特征提取、文本化、diff 层、创建模板、专属校验 | **已实现（离线描述）** / UE 深度反射 **规格** |
| `policy` | 命名 / 目录 / 格式 / 引用 / 生命周期规则，含 custom 谓词 | **已实现** |
| `field` | 类型化自定义字段、默认值与迁移 | **已实现** |
| `diff` | L0–L4 各层规范化导出器 | **已实现（离线投影）** / UE 原生导出 **规格** |
| `tool` | 新工具的 schema + 实现，替代 `TOOL_DEFS` 字面量 | **已实现** |
| `lifecycle` | 钩子、审计、外部系统同步 | **已实现（离线事件）** / 真实引擎事件 **规格** |
| `sink` | canonical YAML artifact sink | **已实现（离线）** / DuckDB C API Appender **规格** |
| `template` | 玩法域 → 资产清单 + 装配关系 + 操作序列 | **已实现（离线）** |
| `gateway` | 官方 Toolset 白名单项、插件文本资产通道 | **已实现（离线校验）** / 真实 UE 写入 **规格** |

四级偏好（builtin < plugin < project < user）与声明式规则引擎是**已实现**的框架：
团队规范入 Git、个人覆盖不入库，规则是数据不是代码，默认 `enforce: false` 只报告。
custom rules 从声明式 manifest 装载；allowlist 具备 review 元数据、审批引用、有效期与失效不豁免。
YAML 不解析任意代码引用，builtin callable 只来自 `ual_common.extensions_builtins`。

### 3.4 资产 diff：准确、毫无保留

「毫无保留」与「禁止全量原始数据进上下文」同时成立的解法（[规格主文](asset-lifecycle-architecture.md) §5）：

> **完整结果落盘为可 diff 工件，MCP 只返回摘要 + 工件指针 + 分页游标 + 内容哈希。**

| 层 | 内容 | 状态 |
| --- | --- | --- |
| L0 元数据 | Tag / 类 / 路径 / taxonomy / content_hash | **已实现**（索引内已有） |
| L1 反射属性 | `interface_spec` / `domain_spec` 的确定性文本投影 | **已实现（离线投影）** / UE `PPF_ForDiff` **规格** |
| L2 引用图 | 硬 / 软引用、依赖 / 被引用、深度（索引边投影） | **已实现（离线投影）** |
| L3 载荷文本 | `yaml_cache` / 文本工件规范化 | **已实现（离线投影）** / Blueprint Graph DSL 原生解析 **规格** |
| L4 二进制规范化 | L0–L3 canonical 摘要 | **已实现（离线投影）** / UE 原始二进制导出 **规格** |

`diff_id` 由 lhs / rhs / layers / engine_version / `index_revision` 确定；五层写入
`.ual/diffs/<diff_id>/L0..L4.yaml`，Agent 用 `ReadDiffLayer(diff_id, layer, offset, limit)` 分页取全文，
`VerifyDiffDeterminism` 逐字节复验。以上离线工件链路为**已实现**；不加载 UObject、不导出整库、
不调用 VLM / 云 / GPU / 外部生成式服务。UE 原生 `PPF_ForDiff` / Graph DSL / 二进制规范化仍为**规格**，
详见 [事务与五层差异工件](transaction-and-diff.md)。

### 3.5 AI 工作流：一句话做 FPS 3C

用户原始示例：「我要做 FPS 射击游戏，帮我完成整个 3C 系统设计」——涉及角色蓝图、
动画蓝图、Lua 脚本、混合空间、AI 等的设计与制作，自动找到合适的美术资产、判断要新建什么、
管理最终产出。十步链路逐项状态见 [规格主文](asset-lifecycle-architecture.md) §6 与
[GameSpec 工作流](gamespec-workflow.md)，压缩为五个阶段：

| 阶段 | 承担者 | Lens 提供什么 | 状态 |
| --- | --- | --- | --- |
| 意图 → GameSpec + 模板展开 | 宿主模型 / `template` | 确定性 FPS 3C 模板、18 个资产、装配关系、操作序列、验收断言 | **已实现（离线展开）** / 自然语言生成 **规格** |
| 检索可复用资产、去重、影响面、规范校验 | **Lens** | 10 个只读工具 + 图谱 BFS + `lint-policy` + allowlist | **已实现** |
| 事务计划 → 审批 → dry-run apply | 离线事务层 / 官方 Toolset 白名单 | 确定性 `plan_id`、inverse、diff preview、逐项审批 | **已实现（离线）** / 真实写入 **规格** |
| 改前改后 diff + 索引一致性意图 | **Lens** | 五层 diff 工件、rescan request / apply-events | **已实现（离线）** / 真实重扫 **规格** |
| 验证闭环 → 失败回滚 | offline runner / host runner | compile / PIE / snapshot / assert 阶段的有界报告、inverse_ops | **已实现（离线报告）** / 真实 compile / PIE **规格** |

明确边界：**Lens 不生成美术、不决定玩法、不直接写资产、不调用外部生成式服务**；它保证每一步的资源交互
准确、可验证、可回滚、可 diff。当前 Lens 离线承担第 2–5 阶段的编排与工件，真实 UE host executor / PIE 仍**未落地**。

## 4. 五层架构与交付状态

层号沿用 [规格主文](asset-lifecycle-architecture.md) §2；与 [站点首页](index.md)
「三层数据流」的 L1/L2/L3（DuckDB / YAML / TOON 格式层）编号互不适用。

| 层 | 职责 | 关键组件 | 状态 |
| --- | --- | --- | --- |
| L4 AI 工作流 | 意图 → GameSpec → 计划 → 验证 | GameSpec schema、FPS 3C 模板、十步编排循环 | **已实现（离线）** / 真实 compile / PIE **规格** |
| L3 策略与扩展 | 命名 / 目录 / 格式规范、四级偏好、九个 namespace、custom rules、allowlist | `PolicyEngine`、`SettingsProvider`、`ExtensionRegistry`、声明式 manifest | **已实现** |
| L2 资产内核 | 资源网关、知识图谱、Diff 工件、Transaction、typed payload | Gateway、Index、Graph、Diff、Transaction、`artifacts.py` | **已实现（离线）** / 真实 UE host **规格** |
| L1 UE6 官方底座 | 具体读写、反射、依赖、文本导出 | `call_tool`、AssetRegistry、`PPF_ForDiff`、Graph DSL | **已启用**（宿主工程 5 条 Editor-only 插件）；真实写入通道 **规格** |
| L0 插件文本资产 | 强类型规格 ↔ 资产、脚本交付 | UEAbilityKit YAML、UnLuaNova Lua、UEYaml | **已有**，但互不统一 |

横切约束：**确定性**（无时间戳 / 绝对路径 / 随机 ID）、**上下文预算**（渐进披露、有界返回）、
**审批边界**（索引只读、Write / Execute 逐项确认、dry-run 默认）、**零外部生成式服务**。
Phase 0–5 的离线内核代码已存在，逐项状态见 §3 与 [内核实现](kernel-implementation.md)；
阶段 2 `UToolsetDefinition` 迁移目前 **DECIDED AGAINST**，保持 `AddTool` + 显式限定名 / annotations。

## 5. 与 Epic 官方 MCP 的关系

UAL 不是另一个 MCP Server 竞品，而是官方 MCP 在资产域的能力补齐层与编排层。
官方实测 905 处 AI 标注 / 878 个唯一工具 / 40 个含 C++ 工具的 Toolset；
语义查重、dry-run / 审批 / 回滚命中为 0，这正是 UAL 自建的范围。

| 通道 | 场景 | 传输 | 状态 |
| --- | --- | --- | --- |
| 引擎内官方 MCP | 编辑器开着，Agent 直接问资产 | HTTP `http://localhost:8000/mcp` | 只读工具 **已实现**；真实 UE 写入 **规格** |
| 独立 Python MCP / `ual-cli` | CI、无编辑器、无头构建机 | stdio / CLI | **已实现** |
| HTTP setup DryRun | 客户端预览 loopback 配置 | `Tools/setup_mcp.ps1 -DryRun -Transport Http` | **已实现** |

- 工具按官方 `AIAccessMode` 分为 Read / Write / Execute 三类。10 个既有语义 / 图谱工具全部 `Read`；
  20 个 runtime tools（共 30 个工具） 逐项显式标注 `accessMode` / `readOnlyHint`，`tool_schemas.yaml` 为 `4.0`，
  与 DuckDB `schema_meta.schema_version` `3.0` 互不联动。
- **禁止重复实现官方已有工具**；UAL 只补语义查重、批量召回、计划 / 审批 / dry-run 回滚与有界 diff。
  工具名必须对齐官方限定名，禁止新增 `ual_*` 裸名工具。
- 官方 MCP / Toolset 为 `Experimental + NoRedist`，只能开发期启用，**不可随发布包分发**；
  server 仅 loopback 且无认证，禁止暴露到非 loopback 地址。
- 客户端统一走 HTTP；Python stdio server 仅作降级路径保留，不删。

命名决策、事务层契约与分级规则见 [MCP 定位与事务层规格](mcp-positioning.md)，
本页只确立它在整体定位中的位置。

## 6. 非目标（工程红线）

下列约束属于定位本身，不是实现细节；完整条款见 [开发规范](agent-guide.md) 与
[已知限制](known-limitations.md)。

- **禁止引入 VLM / 云端推理 / 外部生成式服务 / 外部 GPU**；所有语义只来自 UE 反射属性、确定性数学、引用图谱或声明式数据。
- **禁止把整库或全量原始数据返回给 LLM**；批量走 TOON，单资产走 YAML，完整 diff / plan / GameSpec 落规范工件 + 分页取回。
- **禁止手写 TOON / YAML 字符串拼接**，必须调用唯一实现；规范工件统一走 `artifacts.py`；C++ 侧 staging 走 UEYaml emitter。
- **禁止用 `content_hash` 查重**（只用于增量变更检测）；查重只用 `fingerprint_vec` 余弦相似度。
- **禁止加载贴图 / 网格进显存**；UE 侧优先 `FAssetData` Tag-Value。
- **禁止在 YAML / TOON 输出本地绝对路径、时间戳、随机 ID**，否则 CI diff 永远脏。
- **禁止 YAML 任意代码执行**；扩展清单只能引用 `ual_common.extensions_builtins` 固定 callable。
- MCP Server 只读连接 DuckDB（`read_only=True`），绝不写索引；新增 Write / Execute 类工具必须经已批准 transaction / gateway，
  Write / Execute 逐项审批、dry-run 默认 true、官方工具白名单。真实 UE host executor 仍**规格**。
- **零重写官方能力**：通用写操作交给官方 Toolset；Lens 只做编排、白名单与确定性封装。
- CI 不编译 C++，不执行真实 UE 写入 / compile / PIE，不自动 `git push`；本地 UE6 UBT Build 已通过（`Result: Succeeded`，见 §8），但 CI 仍不编译 C++，真实 UE 写入 / 编辑器编译 / PIE / UE 自动化用例仍为**规格 / 未落地**。
- `Config/UAL/gateway/integrations.yaml` 中的 P1 / P2 外部服务保持 `disabled` / license-reviewed / Zero-VLM boundary，**not executed**；不得写成已启用集成或外部生成式服务调用。

## 7. 文档地图与同步约定

| 想回答的问题 | 去哪一页 |
| --- | --- |
| UAL 是什么、边界在哪、完整能力清单 | **本文**（§1 定位、§3 能力全景） |
| 规格主文（升级论证、九扩展点、五层 diff、3C 十步、Phase 0–5） | [资产全生命周期架构规格](asset-lifecycle-architecture.md) |
| 官方 MCP 分工、命名与事务层契约 | [MCP 定位与事务层规格](mcp-positioning.md) |
| 离线内核模块与状态 | [离线内核实现](kernel-implementation.md) |
| C1–C20 需求 → 文件 / 测试 / 状态 | [需求追踪矩阵](requirements-traceability.md) |
| 100000 资产基准与方法 | [性能基准](benchmarks.md) |
| dry-run / 审批 / inverse / 五层 diff 工件 | [事务与五层差异工件](transaction-and-diff.md) |
| FPS 3C 十步链与离线 runner 边界 | [GameSpec / FPS 3C 工作流](gamespec-workflow.md) |
| 九个 namespace / 四级偏好 / custom rules / allowlist | [扩展体系](extensions.md) |
| 怎么跑起来 | [快速开始](quickstart.md)、[站点首页](index.md)、[ual-cli 命令参考](reference/cli.md) |
| 数据怎么流转、字段契约 | [架构说明](architecture.md)、[三层协议](reference/protocol.md) |
| 工具签名与输出格式 | [MCP 工具参考](reference/mcp-tools.md) |
| 怎么接 CI、退出码怎么算 | [CI 门禁](ci-gates.md) |
| 哪些还没做、哪些没验证 | [已知限制](known-limitations.md) |
| 改代码要遵守什么 | [开发规范](agent-guide.md)（由 `CLAUDE.md` 生成） |

**改定位时必须同步的引用点**（避免再次出现四份互相矛盾的定位）：

1. `README.md` 首段与「**已实现 / 规格 / 未落地**」要点列表 → 保持摘要 + 链接到本文，不重写定义。
2. `docs/index.md` 首屏、[快速开始](quickstart.md) 与站点导航。
3. `mkdocs.yml` 的 `site_description` 与 `nav`（新增页面必须挂进 nav，`--strict` 会拦断链）。
4. `docs/mcp-positioning.md` §1、`docs/asset-lifecycle-architecture.md` §0、
   `docs/architecture.md` 概述 → 保留各自的领域论证，但把「UAL 是什么」指向本文；
   **规格主文是正式 spec，不是历史材料，不得降级**。
5. `CLAUDE.md` §4.1 / §6 与六个人工新增页 → 必须指向本文 §3 与规格主文；改 `CLAUDE.md` 后必须重跑 `Tools/gen_docs.py` 同步生成页（本轮已重跑并通过 `--check`）。
6. `.github/workflows/docs.yml` / `.github/workflows/python-ci.yml` / `Tools/run_tests.ps1` /
   `Tools/audit_tool_annotations.py` / `Tools/audit_engine_toolsets.py` → 路径触发、生成门禁、annotations /
   engine toolset inventory 审计、22 个新增 `ual-cli` 离线命令与离线自检命令同步。

`docs/reference/*.md`、`docs/agent-guide.md` 与 `Server/ual_mcp/tool_schemas.yaml` 由生成器写入，**禁止手工编辑**；
它们不承载定位表述。`Tools/audit_tool_annotations.py` 只读校验工具 annotations / meta。

## 8. 现状口径与未验证项

截至 2026-11-27，可直接引用的量化事实：

- DuckDB `schema_meta.schema_version` `3.0`：冻结 16 键 Core Record + 独立类型化 `extension_payload` 表；
  `Server/ual_mcp/tool_schemas.yaml` `schema_version` `4.0`：10 个既有只读工具 + 20 个 registry-driven runtime tools（共 30 个工具），
  逐工具 `accessMode` / `readOnlyHint`；九个 namespace 与四级偏好。
- `ual-cli` 既有 5 个命令 + `self-test` + 22 个离线内核命令：`list-extensions`、
  `list-extension-fields`、`search-extension-fields`、`get-assembly-graph`、`list-official-tools`、
  `get-index-revision`、`get-lifecycle-events`、`allowlist-audit`、`diff-assets`、`read-diff-layer`、
  `verify-diff`、`plan-edit`、`apply-plan`、`verify-edit`、`rollback-edit`、`expand-gamespec`、
  `run-validation-loop`、`run-workflow`、`request-rescan`、`apply-events`、`read-artifact`、`import-gamespec`。新增离线自检命令覆盖
  `gen_taxonomy.py --check`、`audit_tool_annotations.py --check`、`audit_engine_toolsets.py --check`、
  `server.py --self-test`、`bench_index.py --count 10000 --incremental`、
  `setup_mcp.ps1 -DryRun -Transport Http`。
- `Tools/bench_index.py --count 100000 --incremental` 实测冷建库 **2.727s**、未变增量 **0.0796s**
  （<30s / <1s 目标，**两个目标均满足**），方法见 [性能基准](benchmarks.md)。
- **完整离线门禁已全部通过**（2026-11-27）：`pytest Server/tests -q` 168 项；`ual-cli self-test` 17/17；
  `server.py --self-test`、`engine_bridge.py --self-test`；`gen_docs.py --check`、`gen_taxonomy.py --check`、
  `audit_tool_annotations.py --check`、`audit_engine_toolsets.py --check`；`mkdocs build --strict`；
  `bench_index.py --count 10000 --incremental` smoke；`setup_mcp.ps1 -DryRun -Transport Http`。
- **干净的纯真实工程索引已重建并验证**：从 staging 用 `--index .ual/real_project.duckdb build --project ../.. --from-yaml ../../.ual/staging/index.yaml`
  重建到 `.ual/real_project.duckdb`；staging 15 资产 / 56 边 → 干净索引 15 资产 / 56 边 / 75 行 `extension_payload`，
  `schema_version` `3.0`、`engine_version` `UE6`，FTS `ann character` 返回 3 条有界命中，`index_revision` 两次读取稳定，
  位于被忽略 `Intermediate/` 下的只读验证脚本 verdict **PASS**。现有 `.ual/index.duckdb` 是 seed + 真实数据的混合产物
  （24 资产 / 61 边），**禁止**引用为干净的纯真实工程全量索引。该结论只覆盖离线索引灌库 / 查询，
  不覆盖真实 UE host executor 或 `.uasset` 写入。
- **本地 UE6 UBT Build 已通过（2026-11-27，用户明确授权）**：在宿主工程执行
  `dotnet D:/learning/AnnBuildTools/AnnUnrealEngine/Engine/Binaries/DotNET/UnrealBuildTool/UnrealBuildTool.dll -Mode=Build AnnEditor Win64 Development -Project=D:/learning/AnnBuildTools/Ann/Ann.uproject -Compiler=VisualStudio2022 -NoHotReload -WaitMutex`；
  UBT 运行 19 个 action，UHT processed AnnEditor（4 个 generated files written），Unreal Build Accelerator
  local executor 97.24 秒，`Result: Succeeded`，`Total execution time: 136.42 seconds`，输出
  `D:/learning/AnnBuildTools/AnnUnrealEngine/Engine/Binaries/Win64/UnrealEditor.exe`，仅 C4996 `UE_LOG`
  弃用告警、无编译错误。此前通过的非编译类 UBT 检查（`-Mode=QueryTargets`、`-Mode=Query -Query=Capabilities` /
  `AvailableTargets` / `EnabledPlugins` / `TargetDetails -NoIntellisense` / `CompileDetails`
  （配 `-Compiler=VisualStudio2022 -SyntaxCheckOnly`）、
  `-Mode=GenerateClangDatabase -NoExecCodeGenActions -Compiler=VisualStudio2022`）与 22 个 UAL 翻译单元的
  MSVC `/Zs` 仅语法检查仍只是源码级证据。**CI 仍不编译 C++**。
- 五层 diff、离线 transaction / gateway、custom rules、review-gated allowlist、lifecycle / audit、
  rescan request / apply-events、GameSpec / FPS 3C 模板与离线验证循环、Python M2 reference + C++ source-level zero-load domain extractor、HTTP setup 均为
  **已实现（离线）**；逐项状态见 §3、[内核实现](kernel-implementation.md) 与 [C1–C20](requirements-traceability.md)。
- 官方底座盘点：878 个唯一工具（Read 409 / Write 398 / Execute 28 / 未标注 43）、
  6 个 Diff 工具；宿主工程 2026-11-24 `AnnEditor` 全量编译记录为历史记录，本轮另有 2026-11-27 UBT Build 通过结论。

**不能**当作已验证结论引用：真实 UE host executor 写入 / 编辑器编译 / PIE、UE 自动化用例、
UE 原生二进制级 diff、外部生成式服务、以及第 3 节中所有标为**规格 / 未落地**的能力。
上述离线门禁、干净索引验证与本地 UE6 UBT Build 的 C++ 编译通过**不代表**真实 UE 运行时能力已验证；
**CI 仍不编译 C++**；没有启动真实 UE 写入 / PIE，不接云端推理 / GPU。
`Config/UAL/gateway/integrations.yaml` 中的 P1 / P2 外部服务保持 `disabled` / license-reviewed / Zero-VLM boundary，
**not executed**。引擎内官方 MCP 仅 loopback、无认证，禁止暴露到非 loopback 地址。
历史实机编译与运行时联调记录见 [已知限制](known-limitations.md) §11；所有规格条目必须由 [项目定位](positioning.md)
与 [规格主文](asset-lifecycle-architecture.md) 带状态引用，禁止写成已验证能力。
