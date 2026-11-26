# UEAssetLens 资产全生命周期架构规格 v0

> 状态：**本文是 2026-11-24 定位升级的规格主文（正式 spec，不是历史材料）**；
> Phase 0 骨架与 Phase 1–5 的**离线内核代码**已存在（2026-11-26），真实 UE host executor / compile / PIE 仍为**规格**。
> 本文是人工维护页，不由 `Tools/gen_docs.py` 生成。完整能力清单与逐项状态见
> **[项目定位与能力规格](positioning.md) §3**；一句话定位的对外措辞以该页 §1 为准。
> 文中「现状」均可按给出的路径复核，「设计 / 规格」不是已验证结论；本地 UE6 UBT Build 已通过，但 **CI 仍不编译 C++**，真实 UE 写入 / 编辑器编译 / PIE / UE 自动化用例仍为**规格 / 未落地**。

## 0. 定位升级

> 本节是**正式规格的论证与声明**。对外一句话措辞一律从
> [项目定位](positioning.md) §1 复制：UAL 的规格定位是资产内核（Asset Kernel），
> 该句作为正式规格原文保留；离线 Phase 1–5 实现增量按 [项目定位](positioning.md) §3 / §8 逐项带状态引用。
> 不得把本文降级为「历史材料」，也不得把标为规格的条目写成已验证能力。

**旧定位**：确定性资产语义索引、防重复检测与 AI Agent（MCP）检索中枢。
**正式规格定位**：**资产内核（Asset Kernel）**——超大规模游戏开发中所有底层资源交互的唯一入口，覆盖资产的检索、复用、管理、开发、编辑、校验与演化全生命周期；向上服务 AI 工作流，向外提供可注册扩展与可覆盖策略。
完整能力清单（七域 + 九个扩展命名空间 + 五层 diff + AI 工作流）见 [项目定位](positioning.md) §3。

一句话职责边界：

> Lens 不负责「决定做什么」，负责「准确地知道有什么、改了什么、该按什么规矩做、以及如何安全地做」。
> 自然语言 → GameSpec 由宿主模型负责；官方 Toolset 负责具体写操作；Lens 提供知识、策略、网关、离线事务底座与有界工件。真实 UE 写入 / compile / PIE 仍是**规格**。

三条不可让渡的原则：

1. **框架给能力，数据给自由**：所有业务规则（命名、目录、格式、族、特征键、模板、工具、diff 层）都是声明式数据，可分层覆盖；框架只提供加载、求值、注册与执行机制，不执行 YAML 中的任意代码。
2. **零重写官方能力**：UE6 已提供 878 个工具（Read 409 / Write 398 / Execute 28）、`AssetRegistry` 依赖图、`PPF_ForDiff` 属性文本化、6 个 Diff 工具。Lens 只做编排、白名单与确定性封装。
3. **确定性优先于丰富度**：同一输入 + 同一 `index_revision` 必须产出逐字节相同的 spec / plan / diff，否则无法进 Git、无法 review、无法 CI 重放。

## 1. 现状事实：三个硬边界已改变

| # | 原天花板 | 当前事实 | 状态 / 后果 |
| --- | --- | --- | --- |
| 1 | **16 键硬契约锁死自定义字段** | `Server/schema/PROTOCOL.md`、`Source/UEAssetLens/Public/UALTypes.h`、`Server/schema/init.sql`、`Server/ual_common/db.py` 四处同步；DuckDB v3.0 **有意冻结** 16 键 Core Record，类型化扩展进入独立 `extension_payload` 表，字段清单来自 `Config/UAL/fields/default.yaml` 与 `extensions/builtin.yaml` | **已实现**：不再往 staging 增加第 17 键；`schema_meta.schema_version` 已 bump 到 `3.0`；`fields.py` / `domain.py` 提供 Python M2 reference，`UALDomainFeatureExtractor` / `UALDomainProfiles.h` 提供 C++ source-level zero-load domain extractor 与第三个生成头。本地 UE6 UBT Build 已通过、CI 仍不编译 C++；未加载 mesh / animation，真实 UE 端到端反射级深度规格 **规格** |
| 2 | **生成器 AST 强校验使运行时注册不可能** | `Tools/gen_docs.py` 合并 `tools.py::TOOL_DEFS`（既有 10 个只读工具）与 `Config/UAL/extensions/tools.yaml` 的 `ExtensionRegistry.snapshot()`（20 个 runtime tools（共 30 个工具）），发布 `tool_schemas.yaml` v4.0；`server.py` / `engine_bridge.py` 按限定名注册 / 分派；`Tools/audit_tool_annotations.py --check` 与 `Tools/audit_engine_toolsets.py --check` 审计限定名、AIAccessMode / readOnlyHint、重复名与裸 `ual_*` | **已实现**：运行时工具是声明式数据，新增 runtime tool 不再要求改 `TOOL_DEFS` AST 字面量后重编译 C++；阶段 2 `UToolsetDefinition` 迁移 **DECIDED AGAINST for now**，保持 `AddTool` + 显式限定名 / annotations |
| 3 | **只读 + 无写路径** | MCP DuckDB 连接仍 `read_only=True`；离线写路径由 `transaction.py` / `gateway.py` / `artifacts.py` 提供 dry-run plan、审批、inverse、verify、rollback、rescan request、lifecycle events 与五层 diff 工件；真实写入只经注入 host executor | **已实现（离线 dry-run）**：工具不能直接裸写 `.uasset` 或索引；真实 UE host executor、真实写入、compile / PIE、AssetRegistry 事件重扫仍 **规格 / 未落地** |

**现有可插拔度**：`Server/ual_common/registry.py` 固定九个 namespace，`extensions.py` 从四级 `Config/UAL/extensions/*.yaml` 装载 manifest，`Config/UAL/fields/default.yaml` 定义类型化字段，`Config/UAL/allowlists/default.yaml` 提供 review-gated allowlist，`Config/UAL/diff/layers.yaml` 定义 L0–L4，`Config/UAL/templates/fps_3c.yaml` 定义 FPS 3C，`Config/UAL/gateway/official_tools.yaml` 定义官方工具白名单。YAML 只含数据，callable 固定解析到 `ual_common.extensions_builtins`，不存在任意代码执行。C++ 侧模块级注册表、类型专属 handler 与真实引擎生命周期钩子仍是源码级 / **规格**；本地 UE6 UBT Build 已通过，但真实引擎运行时钩子 / 事件重扫仍**规格 / 未落地**。

`Tools/gen_taxonomy.py` 现在生成 `UALTaxonomyRules.h`、`UALFeatureProfiles.h` 与 `UALDomainProfiles.h` 三个头（声明式来源：`Server/ual_common/taxonomy.yaml` / `Config/UAL/fields/default.yaml`），`--check` 阻止三个生成头漂移；`Tools/audit_tool_annotations.py` 与 `Tools/audit_engine_toolsets.py` 阻止工具标注 / inventory 漂移。

## 2. 五层架构

| 层 | 职责 | 关键组件 | 现状 |
| --- | --- | --- | --- |
| L4 AI 工作流 | 意图 → GameSpec → 计划 → 验证 | `gamespec.py`、`workflow.py`、FPS 3C 模板、离线验证循环 | **已实现（离线）** / 真实 compile / PIE **规格** |
| L3 策略与扩展 | 命名/目录/格式规范、四级偏好、九个扩展点、custom rules、allowlist | `PolicyEngine`、`SettingsProvider`、`ExtensionRegistry`、声明式 manifest | **已实现** |
| L2 资产内核 | 资源网关、知识图谱、Diff 工件、Transaction、typed payload | `gateway.py`、`transaction.py`、`asset_diff.py`、`revision.py`、`artifacts.py`、`fields.py` | **已实现（离线）** / 真实 UE host **规格** |
| L1 UE6 官方底座 | 具体读写、反射、依赖、文本导出 | `call_tool`、AssetRegistry、`PPF_ForDiff`、Graph DSL | **已启用**（宿主工程 5 条 Editor-only）；真实写入通道 **规格** |
| L0 插件文本资产 | 强类型规格 ↔ 资产、脚本交付 | UEAbilityKit YAML、UnLuaNova Lua、UEYaml | **已有**，但互不统一 |

横切约束：**确定性**（无时间戳 / 绝对路径 / 随机 ID）、**上下文预算**（渐进披露、有界返回、完整工件落盘）、
**审批边界**（索引只读、Write / Execute 逐项确认、dry-run 默认）、**零外部生成式服务**（无 VLM / 云 / GPU / 网络推理）。

## 3. 扩展点注册表（插件式架构的核心）

所有扩展点共用 `Server/ual_common/registry.py` 的 `ExtensionRegistry`：`namespace / name / version / priority / applies_to / capabilities / schema / impl`。按 `namespace + name` 唯一，重复注册报错；`resolve(asset_class, capability)` 按 priority 降序返回，未命中回落 `*` 通配。C++ 侧对应 `Source/UEAssetLensCore/Public/Extension/` 仍是建议模块，**规格**。

| 扩展点 | namespace | 触发时机 | 自定义什么 | 状态 |
| --- | --- | --- | --- | --- |
| 资产类型扩展 | `asset_type` | 索引/校验/序列化/diff/新建时按 `UClass` 精确分派 | 每种资产类的特征提取、文本化、diff 层、创建模板、专属校验 | **已实现（离线描述）** / UE 深度反射 **规格** |
| 规则扩展 | `policy` | 校验阶段 | 命名/目录/格式/引用/生命周期规则，含 declarative custom 谓词 | **已实现** |
| 字段扩展 | `field` | 灌库与 schema 演进 | 类型化自定义字段（独立 `extension_payload` 表），带默认值与迁移 | **已实现** |
| Diff 层扩展 | `diff` | `DiffAssets` | L0–L4 各层的规范化导出器 | **已实现（离线投影）** / UE 原生导出 **规格** |
| 工具扩展 | `tool` | MCP 注册 | 新工具的 schema + 实现，替代 `TOOL_DEFS` 字面量 | **已实现** |
| 生命周期钩子 | `lifecycle` | `OnPreValidate / OnIndexed / OnPlanned / OnCommitted / OnPublished` | 自定义副作用、审计、外部系统同步 | **已实现（离线事件）** / 真实引擎事件 **规格** |
| 索引 Sink | `sink` | Commandlet commit | canonical YAML / Parquet / 用户自定义落盘 | **已实现（离线 YAML）** / DuckDB C API Appender **规格** |
| 玩法模板 | `template` | GameSpec 展开 | 玩法域 → 资产清单 + 装配关系 + 操作序列 | **已实现（离线）** |
| 资源通道 | `gateway` | 读写执行 | 官方 Toolset 白名单项、插件文本资产通道 | **已实现（离线校验）** / 真实 UE 写入 **规格** |

**注册方式**：Python 侧 `Config/UAL/extensions/*.yaml` 经 `settings.py` 四层合并后由 `extensions.py` 装载进 `ExtensionRegistry`；跨插件 manifest 只允许引用 `ual_common.extensions_builtins` 固定 callable，禁止任意 import。C++ 侧模块级单例 `FUALExtensionRegistry` 与 Editor / Commandlet 共用实例仍是**规格**。

> 关键设计：扩展点按 **能力 + 资产类** 分派，而不是靠一个 `CanExtract()` 恒 true 的高优先级提取器吞掉全部资产。

## 4. 偏好体系（用户自由度）

四级覆盖，后者优先：**builtin < plugin < project < user**。

```
<Plugin>/Config/UAL/                 # builtin：随插件分发的默认规则（只读基线）
<Plugin>/Plugins/*/Config/UAL/       # plugin：扩展插件自带默认
<Project>/Config/UAL/                # project：团队约定，进 Git
<Project>/Saved/Config/UAL/          # user：个人偏好，被 gitignore
```

每个 namespace 一个目录：`policies/`、`fields/`、`extensions/`、`allowlists/`、`diff/`、`templates/`、`gateway/`。合并语义：

- dict 递归深合并；标量后者覆盖；
- 规则列表按 `id` 合并（后层覆盖同 id 项），**保持首次出现顺序**以保证确定性；
- `enabled: false` 可禁用内置规则；`extends: <profile_id>` 可继承预设；
- 每次解析返回 `(merged, provenance)`，`provenance` 只用于诊断、不写进产物；
- 规则是数据不是代码：可 diff、可 CI、可版本化；**默认 `enforce: false` 只报告，`enforce: true` 才阻断门禁**。

规则 schema（`Config/UAL/policies/default.yaml` 为内置示例）：

```yaml
id: naming.blueprint.prefix
kind: naming                         # naming | directory | format | reference | custom
applies_to: [Blueprint, WidgetBlueprint]   # 或 "*"
target: asset_name                   # asset_name | package_path | suffix | tag | graph
pattern: '^(BP|WBP|ABP)_[A-Z][A-Za-z0-9_]*$'
severity: warning                    # info | warning | error
enforce: false
message: "蓝图命名需带类型前缀且首字母大写"
```

`custom` 规则通过 `policy` 命名空间引用声明式扩展清单中的 builtin 谓词：规则求值经 `ExtensionRegistry`，YAML 本身不执行任意 Python 代码。allowlist 另有 `reason` / `owner` / `review_by`、审批引用与有效期；无效、过期或未经 review 的条目不得豁免门禁。

## 5. 资产 diff：准确、毫无保留

### 5.1 五层模型与官方 API

| 层 | 内容 | 官方能力 | 加载 UObject | 现状 |
| --- | --- | --- | --- | --- |
| L0 元数据 | Tag / 类 / 路径 / taxonomy / content_hash | `AssetData.h:216-217`，UAL `FUniversalReflectionExtractor.cpp:293-345` | 否 | **已实现** |
| L1 反射属性 | `interface_spec` / `domain_spec` 的离线确定性文本投影 | `FProperty::ExportTextItem_Direct(..., PPF_ForDiff)`（`UnrealType.h:598`） | 否（离线）/ 是（官方） | **已实现（离线投影）** / UE 反射级 **规格** |
| L2 引用图 | 硬/软引用、依赖/被引用、深度 | `IAssetRegistry.h:517/580` | 否 | **已实现（索引边投影）** |
| L3 载荷文本 | `yaml_cache` / Blueprint Graph DSL、技能 YAML、Lua、t3d | `BlueprintDslToolset.h:32/115-116` 等官方 6 个 Diff 工具 | 否（离线）/ 部分是（官方） | **已实现（离线文本投影）** / 原生解析 **规格** |
| L4 二进制规范化 | L0–L3 canonical 摘要 / 剔除版本 / GUID / 序列化顺序的确定性导出 | `AssetTools.cpp:3643-3685` `DumpAssetToTempFile`、`LOAD_ForDiff` | 否（离线）/ 是（官方） | **已实现（离线摘要）** / UE 原始二进制导出 **规格** |

**UE6 官方已有 6 个 Diff 工具且全部 Read**。Lens 不自研 UObject / Blueprint 解析器；离线 `asset_diff.py` 只做分层编排 + 确定性规范化 + 白名单路由，真实 UE 原生 diff 仍**规格**。

### 5.2 「毫无保留」与上下文预算的解法

> **完整结果落盘为可 diff 工件，MCP 只返回摘要 + 工件指针 + 分页游标 + 内容哈希。**

- 每次 diff 生成确定性 `diff_id`；
- 五层各自产出规范文本，写入 `.ual/diffs/<diff_id>/L0..L4.yaml`（UTF-8、LF、无时间戳、无本机绝对路径、稳定键序，复用 `artifacts.py`）；
- MCP 返回：逐层统计、变更摘要、`canonical_sha256`、`artifact` 相对路径、`next_cursor`；
- Agent 需要全文时用 `ReadDiffLayer(diff_id, layer, offset, limit)` 分页取回；
- `VerifyDiffDeterminism(diff_id)`：同输入重跑两次，逐字节比对工件。

离线规范化剔除引擎导出噪声（对象路径 / GUID / 时间戳 / 序列化顺序、`ActorMetaData` / `FiBData` 控制字符、session 态键）。
不把全量原始数据灌进上下文，不调用 VLM / 云 / GPU / 外部生成式服务。详见 [事务与五层差异工件](transaction-and-diff.md)。

## 6. AI 工作流：一句话做 FPS 3C

用户示例：「我要做 FPS 射击游戏，帮我完成整个 3C 系统设计」，涉及角色蓝图、动画蓝图、Lua 脚本、混合空间、AI 等。

| 步 | 承担者 | Lens 提供什么 | 现状 |
| --- | --- | --- | --- |
| 1 | 宿主模型：自然语言 → GameSpec | 受支持关键词匹配与确定性 GameSpec id | **已实现（离线展开）** / 自然语言生成 **规格** |
| 2 | 玩法模板：展开为资产清单 + 装配关系 | `Config/UAL/templates/fps_3c.yaml`、18 个资产、装配关系、白名单操作 | **已实现** |
| 3 | **Lens：检索可复用美术资产、去重、影响面、共现装配** | 10 个只读工具 + 图谱 BFS + 特征检索，TOON 有界返回 | **已实现** |
| 4 | **Lens：规范校验**（命名/目录/格式/引用） | `PolicyEngine` + 四级偏好 + custom rules + allowlist + `ual-cli lint-policy` | **已实现** |
| 5 | 事务层：PlanEdit（白名单 op / inverse / diff preview / 确定性 plan_id） | `index_revision`、受影响资产集 | **已实现（离线）** |
| 6 | host：按 `annotations.accessMode` 逐项审批，dry-run 默认 true | 逐 op 权限标注 | **已实现（离线）** / 真实 host **规格** |
| 7 | 执行：官方 Toolset / UAK commandlet / Lua ScriptDelivery | 资源网关白名单 + 注入 host executor | **已实现（离线 deferral）** / 真实 UE 写入 **规格** |
| 8 | **Lens：改前改后 Diff + 索引一致性意图** | 五层 diff 工件、rescan request / apply-events | **已实现（离线投影）** / 真实重扫 **规格** |
| 9 | 验证闭环：compile / PIE / snapshot / assert / repair / rollback | 有界验证报告 | **已实现（离线报告）** / 真实 compile / PIE **规格** |
| 10 | 失败回滚 | `inverse_ops` 在 plan 阶段生成 | **已实现（离线）** / 真实资产回滚 **规格** |

**3C 资产域**：`Config/UAL/templates/fps_3c.yaml` 用 18 项资产、装配关系、操作序列与验收断言补足 24 族只是「类名 → 单族」类型标签的不足；它仍是声明式数据，不是 UE 资产本体。真实新建 / 装配 / PIE 未在离线门禁中验证，仍为**规格**。

明确边界：**Lens 不生成美术、不决定玩法、不直接写资产、不调用外部生成式服务**；它保证每一步的资源交互是准确、可验证、可回滚、可 diff 的。详见 [GameSpec / FPS 3C 工作流](gamespec-workflow.md)。

## 7. 分阶段迁移路线

| Phase | 目标 | 主要改动 | 可验证产物 / 状态 | 回滚点 |
| --- | --- | --- | --- | --- |
| **0（已落地，2026-11-26）** | 规格 + 偏好/策略/注册骨架 + CLI 入口 + 文档定位 | `docs/positioning.md`、`docs/quickstart.md`、`docs/asset-lifecycle-architecture.md`、`Server/ual_common/{settings,policy,registry,policy_gate}.py`、`Config/UAL/policies/default.yaml`、`Server/tests/{test_policy,test_policy_gate}.py` 与 `ual-cli lint-policy` | **已实现**：既有 pytest / CLI 自检 / 门禁覆盖，10 工具链路不变 | 独立新文件，删除即回滚 |
| 1 | 解耦 16 键 | Core Record 冻结 16 键；新增类型化 `extension_payload` 表、`fields.py` / `domain.py` Python M2 reference、`UALDomainFeatureExtractor` / `UALDomainProfiles.h` C++ source-level zero-load domain extractor / third generated header；`schema_version` `2.0 → 3.0` | **已实现（离线 / C++ 源码级）**：旧索引重建 + 新字段可查询；本地 UE6 UBT Build 已通过、CI 仍不编译 C++，未加载 mesh / animation，真实 UE 端到端深度规格 **规格** | 需重建 `.ual/index.duckdb` |
| 2 | 生成器改为注册表快照 | `Config/UAL/extensions/tools.yaml` → `ExtensionRegistry.snapshot()` → `tool_schemas.yaml` v4.0；`audit_tool_annotations.py --check` 校验限定名 / annotations，`audit_engine_toolsets.py --check` 校验离线 `tools/list` inventory；阶段 2 `UToolsetDefinition` 迁移 **DECIDED AGAINST** | **已实现**：20 个 runtime tools（共 30 个工具） 与既有 10 个只读工具共用限定名契约，`--check` 拦截漂移 | 保留旧生成器一个版本 |
| 3 | 资源网关 + 事务层 | `gateway.py`、`transaction.py`、官方工具白名单、dry-run Plan / Apply / Verify / Rollback、审批、inverse、rescan request / apply-events | **已实现（离线 dry-run）**：canonical plan / audit / rescan 工件；真实 UE host executor **规格** | 事务默认 `dry_run=true` |
| 4 | 五层 Diff | `asset_diff.py`、`Config/UAL/diff/layers.yaml`、L0–L4 canonical 工件、分页、逐字节确定性复验 | **已实现（离线投影）**：UE 原生反射 / 二进制 diff **规格** | 只读，无副作用 |
| 5 | GameSpec + 3C 模板 + 验证闭环 | `gamespec.py`、`workflow.py`、`Config/UAL/templates/fps_3c.yaml`、十步链、offline validation loop | **已实现（离线）**：一句话 → 有界 GameSpec / plan / diff / validation 工件；真实可编译灰盒小游戏 **规格** | 模板限定域 |

依赖关系：1 → 2 → 3 → 4 → 5；Phase 0 已落地且与其余解耦，可独立评审。Phase 1–5 的离线内核代码均已存在，本地 UE6 UBT Build 已通过且 CI 仍不编译 C++，但真实 UE 写入 / 编辑器编译 / PIE / UE 自动化用例仍**规格 / 未落地**；完整能力清单见 [项目定位](positioning.md) §3，现状口径见 §8 与 [已知限制](known-limitations.md)。

## 8. 红线与边界

- **Zero-VLM**：禁 VLM / embedding / 云推理 / 外部生成式服务 / GPU；所有知识来自反射、确定性数学、引用图谱、声明式数据与用户输入。
- **上下文预算**：批量走 TOON、单资产走 YAML；完整 plan / diff / GameSpec / audit 走 `artifacts.py` canonical 工件 + 分页取回；禁止整库原始数据进 LLM。
- **确定性**：无时间戳、无本机绝对路径、无随机 ID；稳定键序；同输入逐字节相同；禁止手写 YAML / TOON 字符串拼接。
- **审批**：索引侧 MCP 永远 `read_only=True`；Write / Execute 逐项确认、dry-run 默认 true、官方工具白名单；离线事务不裸写索引或 `.uasset`，真实写入只经已批准 transaction / gateway / 注入 host executor。
- **C++ 编译**：CI 不编译 C++；2026-11-27 用户明确授权后本地 UE6 UBT Build 已通过（`AnnEditor Win64 Development`，`Result: Succeeded`，见 [已知限制](known-limitations.md) §1）；此前非编译类 UBT 检查与 22 个翻译单元的 MSVC `/Zs` 仅语法检查仍只是源码级证据。C++ 编译通过**不是**真实引擎写入 / 编辑器编译 / PIE / UE 自动化用例已验证的结论，这些仍为**规格 / 未落地**；源码级 hooks 的运行时能力不作为已验证能力引用。
- **外部集成边界**：`Config/UAL/gateway/integrations.yaml` 中的 P1 / P2 外部服务保持 `disabled` / license-reviewed / Zero-VLM boundary，**not executed**；不接外部生成式服务，不把 disabled 声明写成已启用集成。
- **分发**：官方 MCP / Toolset 为 `Experimental + NoRedist`，仅开发期启用，loopback-only、无认证，不可随发布包分发。
- **许可**：UEAbilityKit 为 PolyForm 商业许可；生成物分发需合规审查。

量化事实：DuckDB `3.0` / `tool_schemas.yaml` `4.0`；10 个既有只读工具 + 20 个 runtime tools（共 30 个工具）；
`ual-cli` 既有 5 个命令 + `self-test` + 22 个离线内核命令：`list-extensions`、
`list-extension-fields`、`search-extension-fields`、`get-assembly-graph`、`list-official-tools`、
`get-index-revision`、`get-lifecycle-events`、`allowlist-audit`、`diff-assets`、`read-diff-layer`、
`verify-diff`、`plan-edit`、`apply-plan`、`verify-edit`、`rollback-edit`、`expand-gamespec`、
`run-validation-loop`、`run-workflow`、`request-rescan`、`apply-events`、`read-artifact`、`import-gamespec`。离线门禁包含
`gen_taxonomy.py --check`、`audit_tool_annotations.py --check`、`audit_engine_toolsets.py --check`。
`Tools/bench_index.py --count 100000 --incremental` 实测冷建库 **2.727s**、未变增量 **0.0796s**
（<30s / <1s 目标，**两个目标均满足**；见 [性能基准](benchmarks.md)）。

## 9. 待决事项（已决项保留为记录）

1. **Phase 1 的 16 键解耦策略**：**已决** —— Core Record 冻结 + 独立 `extension_payload` 表；不改成主表宽列。
2. **C++ 扩展注册表放哪**：仍为**规格**。新增 `UEAssetLensCore` 模块或放进现有 `UEAssetLensEditor` 需 UE SDK 编译验证，离线门禁不做。
3. **Diff 的加载策略**：离线 L0–L4 只投影索引 / 文本；UE 原生 `PPF_ForDiff` / 二进制导出按显式 host runner 开关，仍为**规格**。
4. **审批粒度**：当前按逐 `Write` / `Execute` 操作确认；每个 plan 一次确认不是默认。
5. **事务执行通道**：**规格**。离线路径保留 Python transaction / gateway + 注入 executor；真实官方 Toolset 调用 / `FindTool` 可见性 / C++ 薄层均需宿主实机验证，离线门禁未编译或联调。
6. **阶段 2 `UToolsetDefinition` 迁移**：**DECIDED AGAINST for now**。保持 `AddTool` + 显式限定名 / annotations，避免双轨别名与自动拼名破坏 1:1 契约。
