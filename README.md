# UEAssetLens (UAL)

[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-3F51B5?style=for-the-badge)](https://hustjjd.github.io/UEAssetLens/)
[![Docs Build](https://github.com/HUSTJJD/UEAssetLens/actions/workflows/docs.yml/badge.svg)](https://github.com/HUSTJJD/UEAssetLens/actions/workflows/docs.yml)

> 零 VLM 依赖的 Unreal Engine **资产内核（Asset Kernel）**——超大规模游戏开发中所有底层资源交互的唯一入口，覆盖资产的检索、复用、管理、开发、编辑、校验与演化全生命周期；向上服务 AI 工作流，向外提供可注册扩展与可覆盖策略。**当前已交付**只读语义检索中枢与 Phase 0 骨架，其余为**规格目标**。

**项目定位**：[`docs/positioning.md`](docs/positioning.md) 是「UAL 是什么 / 完整能力 / 现在做到哪一步」的唯一事实源——一句话定位、**能力全景（逐项带状态）**、五层架构、与 Epic 官方 MCP 的关系、工程红线与文档地图都在那一页；[规格主文](docs/asset-lifecycle-architecture.md) 给出定位升级论证、九个扩展方向、五层资产 diff、FPS 3C 十步链路与 Phase 0–5 路线。README 与文档站首页只做摘要 + 链接，不再各自重写定义。2026-11-24 的定位升级是**正式规格**，不是历史材料。首段一句话是正式规格原文，措辞不随离线实现增量改写；逐项状态以 `docs/positioning.md` §3 / §8 为准。

- **规格定位（正式 spec）**：首段一句话定位即是资产内核规格；完整能力清单与逐项状态见 [项目定位](docs/positioning.md) §3，升级论证、五层架构、九个扩展点、四级偏好、五层 diff、AI 工作流与 Phase 0–5 路线见 [规格主文](docs/asset-lifecycle-architecture.md)。
- **已实现（离线）**：确定性语义索引、防重复检测、10 个既有只读 MCP 工具 + 20 个 registry-driven runtime tools（共 30 个工具）、DuckDB `schema_meta.schema_version` `3.0`（冻结 16 键 Core Record + 独立 `extension_payload`）、`tool_schemas.yaml` `4.0`、三条 CI 门禁、四级偏好 / custom rules / review-gated allowlist、离线 transaction / gateway / 五层 diff / GameSpec / FPS 3C / lifecycle / audit / rescan / apply-events、Python M2 reference + C++ source-level zero-load domain extractor（`UALDomainFeatureExtractor` / `UALDomainProfiles.h`，本地 UE6 UBT Build 已通过但真实 UE 端到端反射级深度规格仍为**规格**）、HTTP setup、`Tools/audit_tool_annotations.py --check`、`Tools/audit_engine_toolsets.py --check` 与基准。
- **本地 UE6 UBT Build 已通过（宿主工具链）**：2026-11-27 用户明确授权后，`AnnEditor Win64 Development`
  `-Compiler=VisualStudio2022 -NoHotReload -WaitMutex` 通过（19 个 action、UHT processed AnnEditor 写入
  4 个生成文件、UBA local executor 97.24 秒、`Result: Succeeded`、`Total execution time: 136.42 seconds`，
  输出 `UnrealEditor.exe`，仅 C4996 `UE_LOG` 弃用告警、无编译错误）。**CI 仍不编译 C++**。
- **规格 / 未落地**：真实 UE host executor 写入 / 编辑器编译 / PIE、UE 自动化用例、AssetRegistry 事件重扫、
  UE 原生二进制级 diff、外部生成式服务。`Config/UAL/gateway/integrations.yaml` 中的 P1 / P2 外部服务保持
  `disabled` / license-reviewed / Zero-VLM boundary，**not executed**。C++ 编译通过不代表上述运行时能力已验证。

**文档站**：<https://hustjjd.github.io/UEAssetLens/>（由 GitHub Actions 自动构建发布） · [项目定位与能力规格](docs/positioning.md) · [规格主文](docs/asset-lifecycle-architecture.md) · [快速开始](docs/quickstart.md) · [架构说明](docs/architecture.md) · [离线内核实现](docs/kernel-implementation.md) · [需求追踪](docs/requirements-traceability.md) · [性能基准](docs/benchmarks.md) · [事务与 diff](docs/transaction-and-diff.md) · [GameSpec 工作流](docs/gamespec-workflow.md) · [扩展体系](docs/extensions.md) · [CI 门禁](docs/ci-gates.md) · [已知限制](docs/known-limitations.md)

二进制资产黑盒 → 确定性数学 / 拓扑提取 → **DuckDB + YAML + TOON** 三层数据流转 → 双向服务于人类美术防重入库与 AI Agent 零幻觉装配。完整结果落 canonical 工件，禁止把整库原始数据返回给 LLM。

## 工程红线

1. **Zero-VLM / 100% 确定性离线**：不调用视觉 / 多模态模型、外部生成式服务、云端推理或外部 GPU。索引完全由 UE 序列化反射属性、几何 / 运动学数学特征、引用图谱与声明式数据构成。
2. **无头优先、增量构建**：`UALIndexBuilder` Commandlet 不加载贴图 / 网格进显存；仅用 `FAssetData` Tag-Value 做兜底提取。
3. **三层格式铁律**：底层 DuckDB；单资产精读、Git Diff 与插件自有数据 / 交换格式统一用 YAML；MCP 批量召回用 TOON，**禁止整库原始数据进上下文**。所有 plan / diff / GameSpec / audit 工件走 `ual_common/artifacts.py` canonical emitter，禁止手写 YAML / TOON 字符串拼接。
4. **可插拔全资产覆盖**：`IAssetExtractor` 优先级 0 的通用反射底座覆盖 100% UObject，领域提取器优先级 100 可插拔；`Server/ual_common/domain.py` 是 Python M2 reference，`UALDomainFeatureExtractor` + `UALDomainProfiles.h` 是 C++ source-level zero-load domain extractor / third generated header，本地 UE6 UBT Build 已通过、CI 仍不编译 C++，不加载 mesh / animation；真实 UE 端到端反射级深度规格仍**规格**。
5. **确定性**：YAML / TOON 输出不含本地绝对路径、时间戳、随机 ID；`content_hash` 只用于增量变更检测，查重只用 `fingerprint_vec` 余弦相似度。
6. **只读边界**：MCP DuckDB 连接 `read_only=True`；真实写入只经已批准 transaction / gateway / 注入 host executor，当前为**规格**。官方 MCP / Toolset 为 `Experimental + NoRedist`，loopback-only、无认证、不可随发布包分发。
7. **CI 边界**：不编译 C++、不接 secrets / 外部服务、不自动 `git push`；父仓库 `Ann/README.md` 中 `git add Plugins/UEAssetLens` 的指令已过期，MUST NOT be followed。

## 目录结构

仓库根同时是 UE 插件根（物理路径 `Ann/Plugins/UEAssetLens`），因此 `Source/` 与 `.uplugin` 位于根级。

```
UEAssetLens/
├─ UEAssetLens.uplugin
├─ Source/
│  ├─ UEAssetLens/          # Runtime：UALTypes、16 维指纹数学
│  ├─ UEAssetLensEditor/    # Editor：AssetRegistry 监听、Commandlet、IAssetExtractor、Sink、生成头
│  ├─ UEAssetLensMCP/       # Editor：把限定名工具注册进 Epic 官方 Unreal MCP（AddTool）
│  └─ UEAssetLensTests/     # C++ 自动化单元测试（CI 不编译）
├─ Config/UAL/              # extensions / fields / allowlists / diff / templates / gateway / policies
├─ Server/
│  ├─ schema/               # init.sql、rebuild_fts.sql、PROTOCOL.md v3.0（唯一数据契约）
│  ├─ ual_common/           # db / toon / yamlspec / fields / domain / artifacts / transaction / gateway / asset_diff / gamespec / workflow / lifecycle / revision / allowlist / registry / extensions
│  ├─ ual_mcp/              # MCP Server、engine_bridge、tool_schemas.yaml v4.0
│  ├─ ual_cli/              # ual-cli：既有命令 + self-test + 22 个离线内核命令
│  └─ tests/                # pytest 契约测试与种子数据
├─ Tools/                   # run_tests.ps1、setup_mcp.ps1、gen_docs.py、gen_taxonomy.py、bench_index.py、audit_tool_annotations.py、audit_engine_toolsets.py
├─ docs/                    # MkDocs 文档站（含六个人工新增页）
├─ CLAUDE.md                # AI 编程代理开发与自检规范
└─ README.md
```

## 快速开始

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-docs.txt

# 1) 从 UE 无头 Commandlet 产出的冻结 16 键 YAML 文档流灌库（无 UE 环境时可用 --demo）
python Server/ual_cli/ual_cli.py build --project <ProjectRoot> --from-yaml Server/tests/seed.yaml
python Server/ual_cli/ual_cli.py build --project <ProjectRoot> --demo

# 2) 自检：建库 → 三门禁 → Parquet 导出 → 策略报告/严格模式
python Server/ual_cli/ual_cli.py self-test

# 3) CI 门禁
python Server/ual_cli/ual_cli.py lint-duplicates --threshold 0.85
python Server/ual_cli/ual_cli.py lint-policy --project <ProjectRoot>
python Server/ual_cli/ual_cli.py lint-policy --project <ProjectRoot> --strict --fail-on warning
python Server/ual_cli/ual_cli.py verify-references --scan-dirs Content/Abilities,Content/Script
python Server/ual_cli/ual_cli.py export-parquet --out .ual/shared_catalog.parquet
python Server/ual_cli/ual_cli.py allowlist-audit

# 4) 生成漂移 / annotations / engine toolset 审计
python Tools/gen_docs.py --check
python Tools/gen_taxonomy.py --check
python Tools/audit_tool_annotations.py --check
python Tools/audit_engine_toolsets.py --check

# 5) 启动 MCP Server（官方 SDK 2.x，stdio transport）
python Server/ual_mcp/server.py --index .ual/index.duckdb

# 6) 离线 self-test / benchmark / HTTP setup DryRun
python Server/ual_mcp/server.py --self-test
python Server/ual_mcp/engine_bridge.py --self-test
python Tools/bench_index.py --count 10000 --incremental
pwsh -File Tools/setup_mcp.ps1 -DryRun -Transport Http -ConfigRoot <TempConfigRoot>
```

PowerShell 一键测试：`pwsh Tools/run_tests.ps1`（1/13 … 13/13）；`-SkipDocs` 只跳过 MkDocs 依赖安装、参考页生成与严格站点构建。
22 个离线内核命令为：`list-extensions`、`list-extension-fields`、`search-extension-fields`、
`get-assembly-graph`、`list-official-tools`、`get-index-revision`、`get-lifecycle-events`、`allowlist-audit`、
`diff-assets`、`read-diff-layer`、`verify-diff`、`plan-edit`、`apply-plan`、`verify-edit`、`rollback-edit`、
`expand-gamespec`、`run-validation-loop`、`run-workflow`、`request-rescan`、`apply-events`、
`read-artifact`、`import-gamespec`。
完整用法见 [快速开始](docs/quickstart.md) §2；`docs/reference/cli.md` 由生成器写入，禁止手工编辑。

UE 侧无头扫描（默认只扫 `/Game`，加 `-IncludePlugins` 才会把插件内容纳入索引）。推荐让 `ual-cli` 代跑：`build --project <root> --engine-cmd UnrealEditor.exe`；直接调用时 `-run=` 必须写模块限定名：

```bash
UnrealEditor.exe Ann.uproject -run=UEAssetLensEditor.UALIndexBuilder -Project=Ann -Out=.ual/staging -Incremental -IncludePlugins -unattended -nosplash

# 或显式指定挂载根（逗号分隔）
UnrealEditor.exe Ann.uproject -run=UEAssetLensEditor.UALIndexBuilder -Roots=/Game,/UEAbilityKit,/UnLuaNova -unattended -nosplash
```

> UE6 在 `PostEngineInit` 之前解析 `-run=`，裸 `-run=UALIndexBuilder` 会报 `could not find the class UALIndexBuilderCommandlet`；本机 UE6 源码树的 `UnrealEditor-Cmd.exe` 存在引擎模块 BuildId 不匹配，默认请用 `UnrealEditor.exe`。详见 [快速开始](docs/quickstart.md) §1。

> 类型覆盖说明：扫描与提取不设 `AssetClass` 白名单，任何 UObject 派生类都会由通用兜底提取器入索引；`taxonomy` / M2 profile 规则集中在 `Server/ual_common/taxonomy.yaml` 与 `Config/UAL/fields/default.yaml`，改后需运行 `python Tools/gen_taxonomy.py`（生成 taxonomy / feature-profile / domain-profile 三个头）。Python M2 reference 与 C++ source-level zero-load `UALDomainFeatureExtractor` / `UALDomainProfiles.h` 已实现；该 C++ 路径不加载 mesh / animation，本地 UE6 UBT Build 已通过、CI 仍不编译 C++，真实 UE 端到端反射级深度规格仍**规格**，见 `docs/known-limitations.md` §3 / §14 / §17。

## MCP 工具

既有 10 个只读工具：

| 工具 | 输入 | 输出 |
|---|---|---|
| `UEAssetLens.AssetSemanticToolset.SearchAssets` | `query`, `asset_class`, `filters`, `domain_scope`, `limit` | TOON：`candidates` + `co_occurring_bundle` |
| `UEAssetLens.AssetSemanticToolset.InspectAsset` | `logical_id` 或 `asset_path` | YAML：完整资产契约 |
| `UEAssetLens.AssetSemanticToolset.GetCooccurringBundle` | `asset_path`, `top_k` | TOON：配套资产包 |
| `UEAssetLens.AssetSemanticToolset.FindSimilarAssets` | `asset_path`, `threshold` | TOON：余弦相似度 ≥ 阈值的已有资产 |
| `UEAssetLens.AssetSemanticToolset.SearchByFeatures` | `feature_key`, `value_contains`, `asset_class`, `family`, `limit` | TOON：按 `feature.*` 特征键检索 |
| `UEAssetLens.AssetSemanticToolset.ListFeatureKeys` | `limit` | TOON：索引中实际存在的特征词表 |
| `UEAssetLens.AssetGraphToolset.GetDependencies` | `asset_path`, `max_depth`, `edge_type`, `limit` | TOON：有界多跳依赖 BFS |
| `UEAssetLens.AssetGraphToolset.GetReferencers` | 同上 | TOON：反向影响面 BFS |
| `UEAssetLens.AssetGraphToolset.GetIndexHealth` | `top_n` | TOON：断裂 / 孤立 / 外部引用 / 热点 / 族分布 |
| `UEAssetLens.AssetGraphToolset.FindDuplicates` | `threshold`, `limit` | TOON：全库 16 维指纹查重对 |

`filters` 支持的硬约束：`skeleton`、`duration_min`、`duration_max`、`min_forward_cm`、`max_forward_cm`、`dominant_limb`、`asset_class`。
另有 20 个 runtime tools（共 30 个工具）（扩展 / 字段 / 装配 / 白名单 / diff / 离线事务 / GameSpec / workflow / rescan / lifecycle / 工件分页 / GameSpec 导入），每个都有 `UEAssetLens.*` 限定名、显式 `accessMode`（Read / Write / Execute）与 `readOnlyHint`；`Write` / `Execute` 仅离线 dry-run。完整签名见生成的 [MCP 工具参考](docs/reference/mcp-tools.md) 与 [事务与 diff](docs/transaction-and-diff.md)。

## 引擎内官方 MCP 接入（UE6 Experimental）

UE6 源码树随附 Epic 第一方 `ModelContextProtocol` 插件（`FriendlyName: Unreal MCP`，Experimental，默认不启用）。UAL 通过 `Source/UEAssetLensMCP/` 把同一批限定名工具注册进这个官方 server，形成**双通道、单一索引**：

| 通道 | 场景 | 传输 |
| --- | --- | --- |
| 引擎内官方 MCP | 编辑器开着，Agent 直接问资产 | HTTP `http://localhost:8000/mcp` |
| 独立 Python MCP / `ual-cli` | CI、无编辑器、无头构建机 | stdio / CLI |

**选择 `AddTool` 而非 `UToolsetDefinition`**：阶段 2 `UToolsetDefinition` 迁移 **DECIDED AGAINST for now**。ToolsetRegistry 会把工具名限定为 `<ToolsetName>.<ToolName>`，可能破坏与 `Config/UAL/extensions/tools.yaml` / Python 侧 / `tool_schemas.yaml` 共享的 1:1 限定名契约，并引入双轨别名。当前保持 `AddTool` + 显式限定名 / annotations；`IModelContextProtocolModule::AddTool` 只需依赖 `ModelContextProtocol` 一个模块。

调用链：

```text
MCP client → Epic Unreal MCP (HTTP loopback) → FUALEngineMcpTool::RunAsync (线程池)
           → FPlatformProcess::ExecProcess → engine_bridge.py (UTF-8 请求/响应文件)
           → ual_mcp/tools.py / runtime tool implementations → 只读 .ual/index.duckdb → TOON / YAML 文本
```

**唯一事实源**：既有 10 个只读工具的 `mcp_name` 来自 `Server/ual_mcp/tools.py::TOOL_DEFS`；20 个 runtime tools（共 30 个工具） 的 `mcp_name` 来自 `Config/UAL/extensions/tools.yaml` 并经 `ExtensionRegistry.snapshot()` 合并。`Tools/gen_docs.py` 由两者生成 `tool_schemas.yaml` v4.0 与 `docs/reference/mcp-tools.md`；Python MCP Server、生成文档、C++ 引擎内工具三处共用；禁止裸 `ual_*` 线名。`gen_docs.py --check` 校验生成物，`audit_tool_annotations.py --check` 校验限定名 / annotations，`audit_engine_toolsets.py --check` 校验离线 `tools/list` inventory。DuckDB `schema_meta.schema_version` `3.0` 与 tool schema `4.0` 互不联动。

启用步骤：

1. 在工程中启用 `ModelContextProtocol` 插件（`UEAssetLens.uplugin` 已声明为 `Optional` 依赖），并确认 `AllToolsets` 按需启用。
2. 先建索引：`python Server/ual_cli/ual_cli.py build --project <root>`。
3. 启动编辑器；`UEAssetLensMCP` 在 `PostEngineInit` 自动 `AddTool`。控制台执行 `ModelContextProtocol.RefreshTools` 可热重载。
4. 客户端连接 `http://localhost:8000/mcp`；`tools/list` 应出现限定名 UAL 工具。`Tools/setup_mcp.ps1 -DryRun -Transport Http -ConfigRoot <TempConfigRoot>` 可预览客户端配置；stdio 仅降级路径。

**Python 依赖走 UE 官方途径**：`UEAssetLens.uplugin` 的 `PythonRequirements` 声明
`duckdb==2.0.0` 与 `PyYAML==6.0.3`（带官方 wheel hash），由 `PythonScriptPlugin` 在
编辑器启动或 `UnrealBuildTool -Mode=PipInstall` 时安装到
`<Project>/Intermediate/PipInstall/Lib/site-packages`。`engine_bridge.py` 由
`UALEngineMcpBridge` 用**引擎内嵌 Python**启动，并在导入第三方包前把该 site-packages 加入 `sys.path`，
因此无需配置 venv 或绝对路径。`requirements.txt` 仅用于独立 Python MCP server 与 CI。

配置（`DefaultEditorPerProject.ini`，一般无需设置；留空即用默认值）：

```ini
[/Script/UEAssetLensMCP.UALEngineMcpSettings]
; 可选：覆盖默认的引擎内嵌解释器
; PythonExecutable=<path to python>
; 可选：覆盖默认索引 <Project>/.ual/index.duckdb
; IndexPath=<path to index.duckdb>
```

约束与边界见 `docs/known-limitations.md`：每次工具调用启动一个 Python 子进程（百毫秒级，已用 `RunAsync` 避免阻塞 game thread）；官方 server 仅 loopback、无认证、实验性、NoRedist，不可随发布包分发；离线 CI 门禁仍走 `ual-cli`，不依赖编辑器；本地 UE6 UBT Build 已通过但 CI 仍不编译 C++，真实 UE 写入 / 编辑器编译 / PIE / UE 自动化用例仍**规格 / 未落地**。

## 当前实现状态（2026-11-27）

状态口径沿用 [项目定位](docs/positioning.md) 的 **已实现 / 规格 / 未落地** 三级标记；量化事实与未验证项见 [已知限制](docs/known-limitations.md)。

| 里程碑 | 状态 | 内容 |
|---|---|---|
| M1 存储底座与通用提取器 | **已实现** | DuckDB v3.0 DDL（DuckDB 2.0.0 实测）、UE 插件三模块骨架、16 维指纹数学、`IAssetExtractor`、AssetRegistry 增量监听、无头 Commandlet、冻结 16 键 YAML staging 文档流 |
| M1+ 全类型索引覆盖 | **已实现** | 多挂载根扫描（`-Roots=` / `-IncludePlugins`）、taxonomy / feature / domain profiles 声明式单一事实源（24 族、三个生成头）、`logical_id` 碰撞消歧；C++ source-level zero-load extractor 未加载 mesh / animation，本地 UE6 UBT Build 已通过、CI 仍不编译 C++，真实 UE 端到端反射级深度规格仍**规格** |
| M2 四大核心提取器 | **已实现（Python 参考）** | `fingerprint.py` / `yamlspec.py` / `domain.py` / `fields.py` 离线投影；C++ 深度反射仍是源码级 / **规格**，UE SDK 编译未复核 |
| M3 图谱与 MCP Server | **已实现** | 10 个只读工具、TOON / YAML 唯一序列化实现、`asset_edges` 共现查询；基于官方 MCP SDK 2.x（`MCPServer`），无手写协议降级 |
| M4 CI 门禁 CLI | **已实现** | 5 个既有命令 + `self-test` + 22 个离线内核命令；2026-11-27 完整离线门禁已全部通过；`lint-policy` 默认报告优先；custom rules / allowlist 已实现 |
| M5 引擎内官方 MCP | **已实现（历史实机）** | `UEAssetLensMCP` 用 `AddTool` 注册限定名工具、`engine_bridge.py` 子进程桥、`tool_schemas.yaml` v4.0 三端共享契约；2026-11-24 headless 联调为历史记录，本轮离线门禁未复核 |
| Phase 0 资产内核骨架 | **已实现** | 四级偏好、`PolicyEngine`、`ExtensionRegistry`、内置 YAML 规则与 `ual-cli lint-policy` |
| Phase 1–5 离线内核 | **已实现（离线）** | 类型化 `extension_payload`、registry-driven runtime tools、custom rules、allowlist、transaction / gateway dry-run、五层 diff 工件、GameSpec / FPS 3C 十步链与 validation loop、lifecycle / audit、rescan request / apply-events、HTTP setup、基准 |

> 已知边界与未验证项见 `docs/known-limitations.md`。`Tools/bench_index.py --count 100000 --incremental` 实测冷建库
> **2.727s**（<30s）、未变增量 **0.0796s**（<1s），两个目标均满足；方法见 [性能基准](docs/benchmarks.md)。该数字不是 C++ 编译 / PIE 验证。

## 文档站与自动生成

文档站使用 **MkDocs Material**，配置见 `mkdocs.yml`，源文件在 `docs/`。导航按「[项目定位](docs/positioning.md) → [快速开始](docs/quickstart.md) → 架构与治理 → 自动生成的参考页 → 开发规范」组织；六个新增人工页已挂进 `mkdocs.yml` 的 `nav`，且相对链接需能通过 `--strict`。推送到 `main` 且命中 `docs/**`、`mkdocs.yml`、`Config/UAL/**`、`Server/ual_common/**`、`Server/schema/**`、`Server/ual_mcp/**`、`Server/ual_cli/**`、`Tools/gen_docs.py`、`Tools/gen_taxonomy.py`、`Tools/audit_tool_annotations.py`、`Tools/audit_engine_toolsets.py`、`requirements*.txt` 或 `CLAUDE.md` 时，`.github/workflows/docs.yml` 自动构建并通过 GitHub Actions 官方 Pages 流程发布；`.github/workflows/python-ci.yml` 负责 Python 契约检查，两工作流都不添加 C++ 代码检查 job。

参考页由 `Tools/gen_docs.py` **从源码 / registry snapshot 生成**，不要手工编辑：

| 生成页 | 来源 |
| --- | --- |
| `docs/reference/schema.md` | `Server/schema/init.sql`、`rebuild_fts.sql` |
| `docs/reference/mcp-tools.md` | `tools.py::TOOL_DEFS` + `Config/UAL/extensions/tools.yaml` 的 `ExtensionRegistry.snapshot()` |
| `docs/reference/cli.md` | `ual_cli.py` 的 argparse 定义（含新增离线命令） |
| `docs/reference/protocol.md` | `Server/schema/PROTOCOL.md` v3.0 |
| `docs/agent-guide.md` | `CLAUDE.md` |
| `Server/ual_mcp/tool_schemas.yaml` | `tools.py::TOOL_DEFS` + registry snapshot，`schema_version` `4.0` |
| `Source/UEAssetLensEditor/Public/UALTaxonomyRules.h` / `UALFeatureProfiles.h` / `UALDomainProfiles.h` | `Tools/gen_taxonomy.py`（声明式来源含 `Config/UAL/fields/default.yaml`） |

生成器依赖 Python 标准库 + PyYAML。CI 用 `--check` 做**文档 / 工具契约漂移门禁**：源改了但生成页（或工具 schema / 生成头）没同步就会失败。`Tools/audit_tool_annotations.py` 与 `Tools/audit_engine_toolsets.py` 只读校验，不编辑任何文件。

```bash
python -m pip install -r requirements-docs.txt
python Tools/gen_docs.py                       # 重新生成（2026-11-27 已重跑）
python Tools/gen_docs.py --check              # 校验是否过期
python Tools/gen_taxonomy.py --check
python Tools/audit_tool_annotations.py --check
python Tools/audit_engine_toolsets.py --check
python -m mkdocs build --strict --site-dir _site
pwsh -File Tools/run_tests.ps1                # 一键：生成 + 严格构建 + 审计 + pytest + 自检 + benchmark + HTTP DryRun
```

> 首次使用需在仓库 Settings → Pages 把 **Source** 设为 `GitHub Actions`；工作流本身不需要 `gh-pages` 分支。`-SkipDocs` 可在未安装文档工具链时只跳过 MkDocs 相关步骤；2026-11-27 已重跑生成器、168 项 pytest、`mkdocs build --strict` 等完整离线门禁并全部通过，本地 UE6 UBT Build 亦已通过，但 CI 仍不编译 C++；真实 UE 写入 / 编辑器编译 / PIE / UE 自动化用例仍**规格 / 未落地**。

## 数据契约

`Server/schema/PROTOCOL.md` v3.0 是唯一权威。字段变更必须同步 `init.sql`、`Source/UEAssetLens/Public/UALTypes.h`、`Server/ual_common/db.py` 并递增 `schema_meta.schema_version`；Core Record 的 16 键有意冻结，类型化扩展只进 `extension_payload`，不新增第 17 个 staging 键。随后必须重跑 `python Tools/gen_docs.py` 同步生成页（本轮已重跑并通过 `--check`）；`tool_schemas.yaml` 的 `4.0` 是独立工具契约，不与 DuckDB `3.0` 联动。

## 许可

见仓库根 [`LICENSE`](LICENSE) 与 [`NOTICE`](NOTICE)（均随仓分发）。

## 作为独立插件安装 / 发布

仓库根目录本身就是插件根（`UEAssetLens.uplugin` 在此），可直接复制，也可用发布包：

```bash
# 产出 dist/UEAssetLens-<VersionName>-src.zip 及 .sha256
python Tools/package_plugin.py
python Tools/package_plugin.py --verify-only dist/UEAssetLens-2.0.0-src.zip

# 安装：把 zip 内 UEAssetLens/ 解压到目标工程的 Plugins/UEAssetLens/
```

`Config/FilterPlugin.ini` 为 UAT `BuildPlugin` 声明额外随包文件。打包脚本只产出**源码包**：
不含 `EngineVersion` 锁定、不含 `Binaries/` / `Intermediate/`，因此可放进任意兼容引擎版本的工程重新编译。
Python 服务端依赖见 `requirements.txt`，引擎侧仅需 `AssetRegistry`；`ModelContextProtocol` 为可选依赖。
官方 MCP / Toolset 为 `Experimental + NoRedist`，loopback-only、无认证，绝不随发布包分发。

**引擎兼容矩阵**

| 引擎 | 状态 |
| --- | --- |
| UE 6.0（源码构建，宿主 Ann 工程） | **本地 UE6 UBT Build 已通过**（2026-11-27 用户明确授权：`-Mode=Build AnnEditor Win64 Development -Compiler=VisualStudio2022 -NoHotReload -WaitMutex`；19 个 action、UHT 写入 4 个生成文件、UBA 97.24 秒、`Result: Succeeded`、总耗时 136.42 秒、输出 `UnrealEditor.exe`、仅 C4996 `UE_LOG` 弃用告警、无编译错误）；CI 仍不编译 C++；真实 UE 写入 / 编辑器编译 / PIE / UE 自动化用例仍 **规格 / 未落地**。2026-11-24 实机编译另为历史记录 |
| UE 5.8 | 未在当前环境验证；源码包可直接编译尝试 |

推送 `v*` tag 会触发 `.github/workflows/release.yml`，自动构建并上传 zip 与 SHA256 到 GitHub Release。
也可在 Actions 里手动 `workflow_dispatch` 触发；不会自动 `git push`。
