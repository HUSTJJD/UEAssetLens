# UEAssetLens 资产全生命周期架构规格 v0

> 状态：**架构规格，待评审**。本文是人工维护页，不由 `Tools/gen_docs.py` 生成。
> 文中「现状」均可按给出的路径与行号复核；「设计」是目标形态，不代表已实现。

## 0. 定位升级

**旧定位**：确定性资产语义索引、防重复检测与 AI Agent（MCP）检索中枢。
**新定位**：**资产内核（Asset Kernel）**——超大规模游戏开发中所有底层资源交互的唯一入口，覆盖资产的检索、复用、管理、开发、编辑、校验与演化全生命周期；向上服务 AI 工作流，向外提供可注册扩展与可覆盖策略。

一句话职责边界：

> Lens 不负责「决定做什么」，负责「准确地知道有什么、改了什么、该按什么规矩做、以及如何安全地做」。
> 自然语言 → GameSpec 由模型负责；官方 Toolset 负责具体写操作；Lens 提供知识、策略、网关与事务底座。

三条不可让渡的原则：

1. **框架给能力，数据给自由**：所有业务规则（命名、目录、格式、族、特征键、模板）都是声明式数据，可分层覆盖；框架只提供加载、求值、注册与执行机制。
2. **零重写官方能力**：UE6 已提供 878 个工具（Read 409 / Write 398 / Execute 28）、`AssetRegistry` 依赖图、`PPF_ForDiff` 属性文本化、6 个 Diff 工具。Lens 只做编排、白名单与确定性封装。
3. **确定性优先于丰富度**：同一输入 + 同一 `index_revision` 必须产出逐字节相同的 spec / plan / diff，否则无法进 Git、无法 review、无法 CI 重放。

## 1. 现状事实：三个硬天花板

| # | 天花板 | 证据 | 后果 |
|---|---|---|---|
| 1 | **16 键硬契约锁死自定义字段** | `Server/schema/PROTOCOL.md:23-52`、`Source/UEAssetLensEditor/Private/UALYamlIndexSink.cpp:82-155`、`Source/UEAssetLens/Public/UALTypes.h:53-114`、`Server/schema/init.sql:22-47`、`Server/ual_common/db.py:22-26,118-161` 五处逐字同步；`CLAUDE.md` 禁止新增字段 | 用户扩展只能往 `domain_spec MAP(VARCHAR,VARCHAR)` 塞字符串，数值类型被擦除、无嵌套 schema、无版本迁移、无按类型校验 |
| 2 | **生成器 AST 强校验使运行时注册不可能** | `Tools/gen_docs.py:194-200` 要求 `TOOL_DEFS` 是顶层 `ast.AnnAssign` 且 `literal_eval` 可求值；`:203-224` 要求 `@sdk.tool(name=...)` 是 `ast.Constant`；`:227-237` 要求工具名集合精确相等；`:410-436 --check` 逐字节比对 6 页 | 新增工具必须是「改源码字面量 → 重生成 → 提交 → 重编译 C++」的离线流程，与「插件式扩展」根本冲突 |
| 3 | **只读 + 无写路径** | `Server/ual_common/db.py:33-39` `read_only=True`；`Source/UEAssetLensMCP/Private/UALEngineMcpTool.cpp:42-50` 硬编码 read；`UEAssetLensEditorModule.cpp:36-67` 事件委托只计数不重扫；`TOOL_DEFS` 中无 `PlanEdit`/`ApplyPlan` | 校验、编辑、发布、审批、回滚、增量同步全无载体，目前只有「建索引 + 只读查询」 |

**现有可插拔度实测**：`IAssetExtractor`（`Source/UEAssetLensEditor/Public/IAssetExtractor.h:16-29`）是唯一真扩展点，但注册只有两处硬编码（`UEAssetLensEditorModule.cpp:17-22`、`UALIndexBuilderCommandlet.cpp:228-230`），且 Commandlet 用局部栈 `Registry`，外部插件无法影响无头构建。`GProfileRules`（`UALTypedFeatureExtractor.cpp:22-48`）、`GCapabilityRules`（`:51-66`）、`GPrefixes`（`FUniversalReflectionExtractor.cpp:21-28`）都是匿名 namespace 里的 `static const` 表。**policy 引擎、类型专属 handler、生命周期钩子、用户偏好载体目前为零。**

一个真实破口：`gen_taxonomy.py` 只生成 `UALTaxonomyRules.h`，不生成 `GProfileRules`。往 `taxonomy.yaml` 加新族后 `FamilyFor()` 会返回新族名，但 `ProfileKeysFor()` 静默落到硬编码默认串，且 `--check` 不报错。做字段注册表时必须纳入。

## 2. 五层架构

| 层 | 职责 | 关键组件 | 现状 |
|---|---|---|---|
| L4 AI 工作流 | 意图 → GameSpec → 计划 → 验证 | GameSpec schema、玩法模板库、编排循环 | **缺**（父仓库 `Docs/ai-capabilities/ludus-toolset-gap.md:218-224` 列为 P0/P1） |
| L3 策略与扩展 | 命名/目录/格式规范、四级偏好、扩展注册 | `PolicyEngine`、`SettingsProvider`、`ExtensionRegistry` | 本轮落地骨架 |
| L2 资产内核 | 资源网关、知识图谱、Diff 工件 | Gateway、Index、Graph、Diff、Transaction | 部分（10 个只读工具 + 图谱检索已可用） |
| L1 UE6 官方底座 | 具体读写、反射、依赖、文本导出 | `call_tool`、AssetRegistry、`PPF_ForDiff`、Graph DSL | 已启用（`Ann.uproject` 5 条 Editor-only） |
| L0 插件文本资产 | 强类型规格 ↔ 资产、脚本交付 | UEAbilityKit YAML、UnLuaNova Lua、UEYaml | 已有，但互不统一 |

横切约束：**确定性**（无时间戳/绝对路径/随机 ID）、**上下文预算**（渐进披露、有界返回）、**审批边界**（索引只读、Write/Execute 逐项确认）。

## 3. 扩展点注册表（插件式架构的核心）

所有扩展点共用 `Server/ual_common/registry.py` 的 `ExtensionRegistry`：`namespace / name / version / priority / applies_to / capabilities / schema / impl`。按 `namespace + name` 唯一，重复注册报错；`resolve(asset_class, capability)` 按 priority 降序返回，未命中回落 `*` 通配。C++ 侧对应 `Source/UEAssetLensCore/Public/Extension/`（建议新增模块，避免 Editor 依赖倒置）。

| 扩展点 | namespace | 触发时机 | 自定义什么 |
|---|---|---|---|
| 资产类型扩展 | `asset_type` | 索引/校验/序列化/diff/新建时按 `UClass` 精确分派 | 每种资产类的特征提取、文本化、diff 层、创建模板、专属校验 |
| 规则扩展 | `policy` | 校验阶段 | 命名/目录/格式/引用/生命周期规则，含自定义 Python 谓词 |
| 字段扩展 | `field` | 灌库与 schema 演进 | 类型化自定义字段（替代扁平 MAP），带默认值与迁移 |
| Diff 层扩展 | `diff` | `DiffAssets` | L0–L4 各层的规范化导出器 |
| 工具扩展 | `tool` | MCP 注册 | 新工具的 schema + 实现（目标：替代 `TOOL_DEFS` 字面量） |
| 生命周期钩子 | `lifecycle` | `OnPreValidate / OnIndexed / OnPlanned / OnCommitted / OnPublished` | 自定义副作用、审计、外部系统同步 |
| 索引 Sink | `sink` | Commandlet commit | DuckDB / Parquet / 用户自定义落盘 |
| 玩法模板 | `template` | GameSpec 展开 | 玩法域 → 资产清单 + 装配关系 + 操作序列 |
| 资源通道 | `gateway` | 读写执行 | 官方 Toolset 白名单项、插件文本资产通道 |

**注册方式**：Python 侧 `registry.register(spec, impl=...)`（进程内）；跨插件用声明式清单 `<Project>/Config/UAL/extensions/*.yaml` + entry-point 发现；C++ 侧用模块级单例 `FUALExtensionRegistry`，Editor 与 Commandlet **共用同一实例**（修掉当前局部栈问题）。外部插件可 `GetEditorRegistry()->Register(...)` 注入。

> 关键设计：扩展点按 **能力 + 资产类** 分派，而不是靠一个 `CanExtract()` 恒 true 的高优先级提取器吞掉全部资产（现状 `UALTypedFeatureExtractor.cpp:183-188` 正是这个陷阱）。

## 4. 偏好体系（用户自由度）

四级覆盖，后者优先：**builtin < plugin < project < user**。

```
<Plugin>/Config/UAL/                 # builtin：随插件分发的默认规则（只读基线）
<Plugin>/Plugins/*/Config/UAL/       # plugin：扩展插件自带默认
<Project>/Config/UAL/                # project：团队约定，进 Git
<Project>/Saved/Config/UAL/          # user：个人偏好，被 gitignore
```

每个 namespace 一个目录：`policies/`、`taxonomy/`、`profiles/`、`extensions/`、`templates/`。合并语义：

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

`custom` 规则通过 `policy` 命名空间注册 Python 谓词，让用户可以「基于框架对任意资产做自定义拓展」而无需改框架源码。

## 5. 资产 diff：准确、毫无保留

### 5.1 五层模型与官方 API

| 层 | 内容 | 官方能力 | 加载 UObject | 现状 |
|---|---|---|---|---|
| L0 元数据 | Tag / 类 / 路径 / taxonomy / content_hash | `AssetData.h:216-217`，UAL `FUniversalReflectionExtractor.cpp:293-345` | 否 | 已具备 |
| L1 反射属性 | 每个 UPROPERTY 的规范文本 | `FProperty::ExportTextItem_Direct(..., PPF_ForDiff)`（`UnrealType.h:598`）、`KismetSystemLibrary.cpp:3785-3807`、`FJsonObjectConverter.h:155` | 是 | 缺 |
| L2 引用图 | 硬/软引用、依赖/被引用、深度 | `IAssetRegistry.h:517/580` | 否 | 部分（UAL 只有简化边 + 正则启发式） |
| L3 载荷文本 | Blueprint Graph DSL、技能 YAML、Lua、t3d | `BlueprintDslToolset.h:32/115-116`（`Diff` 直接给两蓝图图与类默认值行 diff）、`UAKAbilityYamlSerializer.cpp:430-448`、`Exporter.h:240`、`DiffAssetsCommandlet.cpp:183-245` | 部分是 | 分裂，未按类型路由 |
| L4 二进制规范化 | 剔除版本/GUID/序列化顺序的确定性导出与哈希 | `AssetTools.cpp:3643-3685` `DumpAssetToTempFile`、`LOAD_ForDiff` | 是 | 缺 |

**UE6 官方已有 6 个 Diff 工具且全部 Read**：`Diff`(ActorToolset)、`DiffFunction`/`DiffMaterial`(MaterialToolset)、`Diff`(BlueprintDslToolset)、`DiffGraphs`(BlueprintToolset)、`DiffPages`(MetaSoundInspectionToolset)。因此 Lens **不应自研资产解析器**，只做分层编排 + 确定性规范化 + 白名单路由。

### 5.2 「毫无保留」与上下文预算的解法

用户要「准确、毫无保留」，而 `CLAUDE.md:19` 禁止把全量原始数据返回 LLM。两者不矛盾，正确做法是：

> **完整结果落盘为可 diff 工件，MCP 只返回摘要 + 工件指针 + 分页游标 + 内容哈希。**

- 每次 diff 生成 `diff_id = sha256(lhs + rhs + layers + engine_version + index_revision)`；
- 五层各自产出规范文本，写入 `.ual/diffs/<diff_id>/L0..L4.yaml`（UTF-8、LF、无时间戳、无本机绝对路径、稳定键序，复用 `UEYaml`）；
- MCP 返回：逐层统计、变更摘要、`canonical_sha256`、`artifact` 相对路径、`next_cursor`；
- Agent 需要全文时用 `ReadDiffLayer(diff_id, layer, offset, limit)` 分页取回；
- 提供 `VerifyDiffDeterminism(diff_id)`：同输入重跑两次，逐字节比对工件。

这样既不截断、不省略任何一层，也不把几十 MB 文本灌进上下文。规范化必须剔除引擎导出噪声（对象路径/GUID/序列化顺序、`ActorMetaData` base64、`FiBData` 控制字符、`updated_at`、Blueprint DSL 的 `revision` 会话态），否则「可 diff」不成立。

## 6. AI 工作流：一句话做 FPS 3C

用户示例：「我要做 FPS 射击游戏，帮我完成整个 3C 系统设计」，涉及角色蓝图、动画蓝图、Lua 脚本、混合空间、AI 等。

| 步 | 承担者 | Lens 提供什么 | 现状 |
|---|---|---|---|
| 1 | 模型：自然语言 → GameSpec（域/实体/规则/输入/UI/验收断言，复用 `UEYaml`） | — | 缺 |
| 2 | 玩法模板：展开为资产清单 + 装配关系 | 24 族词表、`ListFeatureKeys`、类型扩展的「应如何新建」schema | 缺模板；词表已有 |
| 3 | **Lens：检索可复用美术资产、去重、影响面、共现装配** | 10 个只读工具 + 图谱 BFS + 特征检索，TOON 有界返回 | **已可用** |
| 4 | **Lens：规范校验**（命名/目录/格式/引用） | `PolicyEngine` + 四级偏好 + `ual-cli lint-policy` | 已接入（报告优先） |
| 5 | 事务层：PlanEdit（白名单 op / inverse / diff_preview / 确定性 plan_id） | `index_revision`、受影响资产集 | 缺 |
| 6 | host：按 `annotations.accessMode` 逐项审批，dry-run 默认 true | 逐 op 权限标注 | 缺 |
| 7 | 执行：官方 Toolset / UAK commandlet / Lua ScriptDelivery | 资源网关白名单 + 统一调用通道 | 部分（通道有、未串联） |
| 8 | **Lens：改前改后 Diff + 索引一致性校验** | 五层 diff 工件、commit 后增量重扫 | 缺 |
| 9 | 验证闭环：编译 → PIE → 状态快照 → 断言 → 回编排层修正 | 改前后索引对比 | 缺 |
| 10 | 失败回滚 | `inverse_ops` 在 plan 阶段生成 | 缺 |

**3C 资产域的真实缺口**：13 类资产大体能被 `taxonomy.yaml:6-54` 的 24 族「装进去」，但做 3C 真正需要的是**装配关系与内部规格**（谁绑定谁、ABP 状态机转移、输入映射、相机组件、GameplayTag ini），24 族只是「类名 → 单族」类型标签。当前两条边（`DEPENDS_ON` 包级依赖、`CO_OCCURS` 名称相似度）都无法表达语义装配。因此 Lens 的知识层需要新增「资产角色 / 用途 / 装配」维度，由 `asset_type` 扩展按类提供。

明确边界：**Lens 不生成美术、不决定玩法、不直接写资产**；它保证每一步的资源交互是准确、可验证、可回滚、可 diff 的。

## 7. 分阶段迁移路线

| Phase | 目标 | 主要改动 | 可验证产物 | 回滚点 |
|---|---|---|---|---|
| **0（本轮）** | 规格 + 偏好/策略/注册骨架 + CLI 入口 | 新增 `docs/asset-lifecycle-architecture.md`、`Server/ual_common/{settings,policy,registry,policy_gate}.py`、`Config/UAL/policies/default.yaml`、`Server/tests/{test_policy,test_policy_gate}.py` 与 `ual-cli lint-policy` | 单元测试 + 全套门禁仍绿，10 工具链路不变 | 独立新文件，删除即回滚 |
| 1 | 解耦 16 键 | Core Record 保留 16 键作兼容基线；新增类型化 `extension_payload` 表 + `FUALFieldRegistry`；`schema_version` 2.0 → 3.0 | 旧索引重建 + 新字段可查重/可过滤 | 需重建 `.ual/index.duckdb` |
| 2 | 生成器改为注册表快照 | `gen_docs.py` 从 `ExtensionRegistry.snapshot()` 生成，放宽 AST 字面量强校验；保留 `--check` | 运行时注册的工具能进 `tool_schemas.yaml` 且门禁不红 | 保留旧生成器一个版本 |
| 3 | 资源网关 + 事务层 | 官方 `call_tool` 白名单、`PlanEdit/ApplyPlan/VerifyEdit/RollbackEdit`、审批、事件重扫 | dry-run 计划 + 索引一致性断言 | 事务默认 `dry_run=true` |
| 4 | 五层 Diff | L0/L2 先落地（零加载），L1/L3/L4 按需加载开关 | `diff_id` 工件 + `VerifyDiffDeterminism` | 只读，无副作用 |
| 5 | GameSpec + 3C 模板 + 验证闭环 | 复用 `UEYaml`，先做 1 个 FPS 3C 域 | 一句话 → 可编译灰盒小游戏 | 模板限定域 |

依赖关系：1 → 2 → 3 → 4 → 5；Phase 0 与其余解耦，可独立评审。

## 8. 红线与边界

- **Zero-VLM**：禁 VLM / embedding / 云推理 / GPU；所有知识来自反射、确定性数学、引用图谱与用户输入。
- **上下文预算**：批量走 TOON、单资产走 YAML；清单渐进披露；完整 diff 落工件 + 分页取回。
- **确定性**：无时间戳、无本机绝对路径、无随机 ID；稳定键序；同输入逐字节相同。
- **审批**：索引侧 MCP 永远 `read_only=True`；Write/Execute 逐项确认、dry-run 默认 true、官方工具白名单；回滚靠 plan 阶段生成的 `inverse`，不依赖 best-effort 热更恢复。
- **分发**：官方 MCP / Toolset 为 `Experimental + NoRedist`，仅开发期启用，不可随发布包分发。
- **许可**：UEAbilityKit 为 PolyForm 商业许可；生成物分发需合规审查。

## 9. 待决事项

1. **Phase 1 的 16 键解耦策略**：Core Record 冻结 + 独立扩展表（推荐，兼容旧索引）vs 直接改为主表宽列（破坏 5 处同步但更简单）。
2. **C++ 扩展注册表放哪**：新增 `UEAssetLensCore` 模块（干净但需改 uplugin + 编译）vs 放进现有 `UEAssetLensEditor`（快但 Editor-only，Commandlet 与编辑器共享问题仍在）。
3. **Diff 的加载策略**：默认只开 L0/L2 零加载层，L1/L3/L4 按显式参数开启并缓存 revision；是否允许在无头模式下加载。
4. **审批粒度**：逐 `Write` 操作确认 vs 每个 plan 一次确认（规格 §10 现有建议为逐项）。
5. **事务执行通道**：沿用上轮 §10.4 的折中方案（Read 留 Python、Write 用薄 C++ 层经 `IModelContextProtocolModule::FindTool` → `Run`），需实测 `FindTool` 对 toolset 内工具的可见性。
