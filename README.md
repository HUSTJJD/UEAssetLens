# UEAssetLens (UAL)

[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-3F51B5?style=for-the-badge)](https://hustjjd.github.io/UEAssetLens/)
[![Docs Build](https://github.com/HUSTJJD/UEAssetLens/actions/workflows/docs.yml/badge.svg)](https://github.com/HUSTJJD/UEAssetLens/actions/workflows/docs.yml)

> 零多模态大模型依赖的 Unreal Engine 确定性资产语义索引、防重复检测与 AI Agent（MCP）检索中枢。

**MCP 定位**：UEAssetLens 是 Epic 官方 MCP 的**统一资产工具层与编辑事务层**——用确定性语义索引回答「该改哪个资产」，把实际写操作交给官方 Toolset，并用事务层保证「改完可验证、可回滚、索引仍一致」。工具按 `AIAccessMode` 统一分为 Query / Edit / Execute 三类：UAL 的 4 个检索工具属于 Query 类，与官方 Read 工具同层；官方已有的通用写操作不重复实现，缺口部分（语义查重、批量召回、计划 / 审批 / 回滚）由 UAL 自建补齐。详见 [MCP 定位与事务层规格](docs/mcp-positioning.md)。

**文档站**：<https://hustjjd.github.io/UEAssetLens/>（由 GitHub Actions 自动构建发布）

二进制资产黑盒 → 确定性数学/拓扑提取 → **DuckDB + YAML + TOON** 三层数据流转 → 双向服务于人类美术防重入库与 AI Agent 零幻觉装配。

## 工程红线

1. **Zero-VLM / 100% 确定性离线**：不调用视觉多模态模型，不依赖云端 GPU。索引完全由 UE 序列化反射属性、几何/运动学数学特征与引用图谱构成。
2. **无头优先、增量构建**：`UALIndexBuilder` Commandlet 不加载贴图/网格进显存；仅用 `FAssetData` Tag-Value 做兜底提取。
3. **三层格式铁律**：底层 DuckDB；单资产精读、Git Diff 与插件自有数据/交换格式统一用 YAML；MCP 批量召回用 TOON，**禁止整库原始数据进上下文**。
4. **可插拔全资产覆盖**：`IAssetExtractor` 优先级 0 的通用反射底座覆盖 100% UObject，领域提取器优先级 100 可插拔。

## 目录结构

仓库根同时是 UE 插件根（物理路径 `Ann/Plugins/UEAssetLens`），因此 `Source/` 与 `.uplugin` 位于根级。

```
UEAssetLens/
├─ UEAssetLens.uplugin
├─ Source/
│  ├─ UEAssetLens/          # Runtime：UALTypes、16 维指纹数学
│  ├─ UEAssetLensEditor/    # Editor：AssetRegistry 监听、Commandlet、IAssetExtractor、Sink
│  ├─ UEAssetLensMCP/       # Editor：把 4 个工具注册进 Epic 官方 Unreal MCP（AddTool）
│  └─ UEAssetLensTests/     # C++ 自动化单元测试
├─ Server/
│  ├─ schema/               # init.sql、rebuild_fts.sql、PROTOCOL.md（唯一数据契约）
│  ├─ ual_common/           # db.py / toon.py / yamlspec.py / fingerprint.py
│  ├─ ual_mcp/              # MCP Server：4 个工具、stdio 服务、引擎内桥、工具 schema
│  ├─ ual_cli/              # ual-cli：4 个命令 + self-test
│  └─ tests/                # pytest 端到端测试与种子数据
├─ Tools/                   # run_tests.ps1、setup_mcp.ps1、gen_docs.py
├─ docs/                    # MkDocs 文档站
├─ CLAUDE.md                # AI 编程代理开发与自检规范
└─ README.md
```

## 快速开始

```bash
python -m pip install -r requirements.txt

# 1) 从 UE 无头 Commandlet 产出的 YAML 文档流灌库（无 UE 环境时可用 --demo 生成样例）
python Server/ual_cli/ual_cli.py build --project /path/to/Ann --from-yaml Server/tests/seed.yaml
python Server/ual_cli/ual_cli.py build --project /path/to/Ann --demo

# 2) 自检：DDL → 灌库 → 4 个 MCP 工具 → 4 个 CLI 命令
python Server/ual_cli/ual_cli.py self-test

# 3) CI 门禁
python Server/ual_cli/ual_cli.py lint-duplicates --threshold 0.85
python Server/ual_cli/ual_cli.py verify-references --scan-dirs Content/Abilities,Content/Script
python Server/ual_cli/ual_cli.py export-parquet --out .ual/shared_catalog.parquet

# 4) 启动 MCP Server（官方 SDK 2.x，stdio transport）
python Server/ual_mcp/server.py --index .ual/index.duckdb

# 5) 引擎内官方 MCP 的桥接自检（不需要 UE）
python Server/ual_mcp/engine_bridge.py --self-test
```

PowerShell 一键测试：`pwsh Tools/run_tests.ps1`；自动配置 Claude Code / Cursor：`pwsh Tools/setup_mcp.ps1 -DryRun`。

UE 侧无头扫描（默认只扫 `/Game`，加 `-IncludePlugins` 才会把插件内容纳入索引）：

```bash
UnrealEditor-Cmd.exe Ann.uproject -run=UALIndexBuilder -Project=Ann -Out=.ual/staging -Incremental -IncludePlugins -unattended -nosplash

# 或显式指定挂载根（逗号分隔）
UnrealEditor-Cmd.exe Ann.uproject -run=UALIndexBuilder -Roots=/Game,/UEAbilityKit,/UnLuaNova -unattended -nosplash
```

> 类型覆盖说明：扫描与提取不设 `AssetClass` 白名单，任何 UObject 派生类都会由通用兜底提取器入索引；`taxonomy` 规则集中在 `Server/ual_common/taxonomy.yaml`，改后需运行 `python Tools/gen_taxonomy.py`。领域深度字段仍受 M2 进度约束，见 `docs/known-limitations.md` §3 / §14。

## MCP 工具

| 工具 | 输入 | 输出 |
|---|---|---|
| `UEAssetLens.AssetSemanticToolset.SearchAssets` | `query`, `asset_class`, `filters`, `domain_scope`, `limit` | TOON：`candidates` + `co_occurring_bundle` |
| `UEAssetLens.AssetSemanticToolset.InspectAsset` | `logical_id` 或 `asset_path` | YAML：完整资产契约 |
| `UEAssetLens.AssetSemanticToolset.GetCooccurringBundle` | `asset_path`, `top_k` | TOON：配套资产包 |
| `UEAssetLens.AssetSemanticToolset.FindSimilarAssets` | `asset_path`, `threshold` | TOON：余弦相似度 ≥ 阈值的已有资产 |

`filters` 支持的硬约束：`skeleton`、`duration_min`、`duration_max`、`min_forward_cm`、`max_forward_cm`、`dominant_limb`、`asset_class`。

## 引擎内官方 MCP 接入（UE6 Experimental）

UE6 源码树随附 Epic 第一方 `ModelContextProtocol` 插件（`FriendlyName: Unreal MCP`，Experimental，默认不启用）。UAL 通过 `Source/UEAssetLensMCP/` 把同一批 4 个工具注册进这个官方 server，形成**双通道、单一索引**：

| 通道 | 场景 | 传输 |
|---|---|---|
| 引擎内官方 MCP | 编辑器开着，Agent 直接问资产 | HTTP `http://127.0.0.1:8000/mcp` |
| 独立 Python MCP / `ual-cli` | CI、无编辑器、无头构建机 | stdio / CLI |

**选择 `AddTool` 而非 `UToolsetDefinition`**：ToolsetRegistry 会把工具名限定为 `<ToolsetName>.<ToolName>`，破坏与 Python 侧及 `tool_schemas.yaml` 共享的 1:1 工具名契约；且 4 个工具全部只读，不需要 Toolset 的权限/发现机制。`IModelContextProtocolModule::AddTool` 只需依赖 `ModelContextProtocol` 一个模块。

调用链：

```text
MCP client → Epic Unreal MCP (HTTP) → FUALEngineMcpTool::RunAsync (线程池)
           → FPlatformProcess::ExecProcess → engine_bridge.py (UTF-8 请求/响应文件)
           → ual_mcp/tools.py → 只读 .ual/index.duckdb → TOON / YAML 文本
```

**唯一事实源**：`Server/ual_mcp/tool_schemas.yaml` 由 `Tools/gen_docs.py` 从 `tools.py` 的 `TOOL_DEFS` 生成，Python MCP Server、生成文档、C++ 引擎内工具三处共用；`--check` 会同时校验 `TOOL_DEFS` 与 `server.py` 的注册函数一致，拦截任何一侧漂移。

启用步骤：

1. 在工程中启用 `ModelContextProtocol` 插件（`UEAssetLens.uplugin` 已声明为 `Optional` 依赖），并确认 `AllToolsets` 按需启用。
2. 先建索引：`python Server/ual_cli/ual_cli.py build --project <root>`。
3. 启动编辑器；`UEAssetLensMCP` 在 `PostEngineInit` 自动 `AddTool`。控制台执行 `ModelContextProtocol.RefreshTools` 可热重载。
4. 客户端连接 `http://127.0.0.1:8000/mcp`；`tools/list` 应出现 `UEAssetLens.AssetSemanticToolset.SearchAssets` 等 4 个工具。

**Python 依赖走 UE 官方途径**：`UEAssetLens.uplugin` 的 `PythonRequirements` 声明
`duckdb==2.0.0` 与 `PyYAML==6.0.3`（带官方 wheel hash），由 `PythonScriptPlugin` 在
编辑器启动或 `UnrealBuildTool -Mode=PipInstall` 时安装到
`<Project>/Intermediate/PipInstall/Lib/site-packages`。`engine_bridge.py` 由
`UALEngineMcpBridge` 用**引擎内嵌 Python**（`Engine/Binaries/ThirdParty/Python3/<Platform>/python.exe`）
启动，并在导入第三方包前把该 site-packages 加入 `sys.path`，因此无需配置 venv 或绝对路径。
`Server/requirements.txt` 仅用于独立 Python MCP server 与 CI。

配置（`DefaultEditorPerProject.ini`，一般无需设置；留空即用默认值）：

```ini
[/Script/UEAssetLensMCP.UALEngineMcpSettings]
; 可选：覆盖默认的引擎内嵌解释器
; PythonExecutable=<path to python>
; 可选：覆盖默认索引 <Project>/.ual/index.duckdb
; IndexPath=<path to index.duckdb>
```

约束与边界见 `docs/known-limitations.md`：每次工具调用启动一个 Python 子进程（百毫秒级，已用 `RunAsync` 避免阻塞 game thread）；官方 server 仅 loopback、无认证、实验性；离线 CI 门禁仍走 `ual-cli`，不依赖编辑器。

## 当前实现状态（2026-11-15）

| 里程碑 | 状态 | 内容 |
|---|---|---|
| M1 存储底座与通用提取器 | **已实现** | DuckDB DDL（实测 DuckDB 2.0.0）、UE 插件三模块骨架、16 维指纹数学、`IAssetExtractor`、`FUniversalReflectionExtractor`、AssetRegistry 增量监听、无头 Commandlet、JSONL 落盘 |
| M1+ 全类型索引覆盖 | **部分（待 UE 编译）** | 多挂载根扫描（`-Roots=` / `-IncludePlugins`）、taxonomy 单一事实源（24 族）、`logical_id` 碰撞消歧；Python 侧 44 项 pytest 通过，C++ 侧需本地 UE6 SDK 编译验证 |
| M2 四大核心提取器 | **部分** | 指纹算法与 YAML 生成器已就绪；StaticMesh / AnimMontage / Material / Blueprint 的 UE 领域反射代码尚未全量落地 |
| M3 图谱与 MCP Server | **已实现** | 4 个工具、TOON/YAML 唯一序列化实现、`asset_edges` 共现查询；基于官方 MCP SDK 2.x（`MCPServer`），无手写协议降级 |
| M4 CI 门禁 CLI | **已实现** | 4 个命令 + `self-test` + pytest 端到端测试；UE 侧端到端联调待本地 UE SDK |
| M5 引擎内官方 MCP | **已实现** | `UEAssetLensMCP` 用 `AddTool` 注册 4 个工具、`engine_bridge.py` 子进程桥、`tool_schemas.yaml` 三端共享契约；实机联调状态见 `docs/known-limitations.md` |

> 已知边界与未验证项见 `docs/known-limitations.md`。10 万级资产冷建库 <30s、增量 <1s 是设计目标，本仓库尚未提供实测基准。

## 文档站与自动生成

文档站使用 **MkDocs Material**，配置见 `mkdocs.yml`，源文件在 `docs/`。推送到 `main` 且命中 `docs/**`、`mkdocs.yml`、`Tools/gen_docs.py` 或参考源（`Server/schema/`、`Server/ual_mcp/`、`Server/ual_cli/`、`CLAUDE.md`）时，`.github/workflows/docs.yml` 自动构建并通过 GitHub Actions 官方 Pages 流程发布。

参考页由 `Tools/gen_docs.py` **从源码生成**，不要手工编辑：

| 生成页 | 来源 |
|---|---|
| `docs/reference/schema.md` | `Server/schema/init.sql`、`rebuild_fts.sql` |
| `docs/reference/mcp-tools.md` | `tools.py` 的 `TOOL_DEFS` 与字段常量 |
| `docs/reference/cli.md` | `ual_cli.py` 的 argparse 定义 |
| `docs/reference/protocol.md` | `Server/schema/PROTOCOL.md` |
| `docs/agent-guide.md` | `CLAUDE.md` |
| `Server/ual_mcp/tool_schemas.yaml` | `tools.py` 的 `TOOL_DEFS`（供 C++ 引擎内 MCP 工具读取） |

生成器仅依赖 Python 标准库。CI 用 `--check` 做**文档漂移门禁**：源改了但生成页（或工具 schema）没同步就会失败。

```bash
python -m pip install -r requirements-docs.txt
python Tools/gen_docs.py                       # 重新生成
python Tools/gen_docs.py --check              # 校验是否过期
python -m mkdocs build --strict --site-dir _site   # 本地严格构建
pwsh -File Tools/run_tests.ps1                # 一键：生成 + 严格构建 + pytest + CLI 自检
```

> 首次使用需在仓库 Settings → Pages 把 **Source** 设为 `GitHub Actions`；工作流本身不需要 `gh-pages` 分支。`-SkipDocs` 可在未安装文档工具链时只跑 Python 测试。

## 数据契约

`Server/schema/PROTOCOL.md` 是唯一权威。字段变更必须同步 `init.sql`、`Source/UEAssetLens/Public/UALTypes.h`、`Server/ual_common/db.py` 并递增协议版本；随后运行 `python Tools/gen_docs.py` 同步生成页。

## 许可

见仓库根 LICENSE（如未附带，沿用宿主工程 `Ann` 的许可约定）。

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

**引擎兼容矩阵**

| 引擎 | 状态 |
|---|---|
| UE 6.0（源码构建，宿主 Ann 工程） | 已编译验证（`AnnEditor Win64 Development`） |
| UE 5.8 | 未在当前环境验证；源码包可直接编译尝试 |

推送 `v*` tag 会触发 `.github/workflows/release.yml`，自动构建并上传 zip 与 SHA256 到 GitHub Release。
也可在 Actions 里手动 `workflow_dispatch` 触发。
