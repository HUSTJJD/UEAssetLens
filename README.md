# UEAssetLens (UAL)

[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-3F51B5?style=for-the-badge)](https://hustjjd.github.io/UEAssetLens/)
[![Docs Build](https://github.com/HUSTJJD/UEAssetLens/actions/workflows/docs.yml/badge.svg)](https://github.com/HUSTJJD/UEAssetLens/actions/workflows/docs.yml)

> 零多模态大模型依赖的 Unreal Engine 确定性资产语义索引、防重复检测与 AI Agent（MCP）检索中枢。

**文档站**：<https://hustjjd.github.io/UEAssetLens/>（由 GitHub Actions 自动构建发布）

二进制资产黑盒 → 确定性数学/拓扑提取 → **DuckDB + YAML + TOON** 三层数据流转 → 双向服务于人类美术防重入库与 AI Agent 零幻觉装配。

## 工程红线

1. **Zero-VLM / 100% 确定性离线**：不调用视觉多模态模型，不依赖云端 GPU。索引完全由 UE 序列化反射属性、几何/运动学数学特征与引用图谱构成。
2. **无头优先、增量构建**：`UALIndexBuilder` Commandlet 不加载贴图/网格进显存；仅用 `FAssetData` Tag-Value 做兜底提取。
3. **三层格式铁律**：底层 DuckDB；单资产精读与 Git Diff 用 YAML；MCP 批量召回用 TOON，**禁止整库 JSON 进上下文**。
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

# 1) 从 UE 无头 Commandlet 产出的 JSONL 灌库（无 UE 环境时可用 --demo 生成样例）
python Server/ual_cli/ual_cli.py build --project /path/to/Ann --from-jsonl Server/tests/seed.jsonl
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

UE 侧无头扫描：

```bash
UnrealEditor-Cmd.exe Ann.uproject -run=UALIndexBuilder -Project=Ann -Out=.ual/staging -Incremental -unattended -nosplash
```

## MCP 工具

| 工具 | 输入 | 输出 |
|---|---|---|
| `ual_search_assets` | `query`, `asset_class`, `filters`, `domain_scope`, `limit` | TOON：`candidates` + `co_occurring_bundle` |
| `ual_inspect_asset` | `logical_id` 或 `asset_path` | YAML：完整资产契约 |
| `ual_get_cooccurring_bundle` | `asset_path`, `top_k` | TOON：配套资产包 |
| `ual_find_similar_assets` | `asset_path`, `threshold` | TOON：余弦相似度 ≥ 阈值的已有资产 |

`filters` 支持的硬约束：`skeleton`、`duration_min`、`duration_max`、`min_forward_cm`、`max_forward_cm`、`dominant_limb`、`asset_class`。

## 引擎内官方 MCP 接入（UE6 Experimental）

UE6 源码树随附 Epic 第一方 `ModelContextProtocol` 插件（`FriendlyName: Unreal MCP`，Experimental，默认不启用）。UAL 通过 `Source/UEAssetLensMCP/` 把同一批 4 个工具注册进这个官方 server，形成**双通道、单一索引**：

| 通道 | 场景 | 传输 |
|---|---|---|
| 引擎内官方 MCP | 编辑器开着，Agent 直接问资产 | HTTP `http://127.0.0.1:8000/mcp` |
| 独立 Python MCP / `ual-cli` | CI、无编辑器、无头构建机 | stdio / CLI |

**选择 `AddTool` 而非 `UToolsetDefinition`**：ToolsetRegistry 会把工具名限定为 `<ToolsetName>.<ToolName>`，破坏与 Python 侧及 `tool_schemas.json` 共享的 1:1 工具名契约；且 4 个工具全部只读，不需要 Toolset 的权限/发现机制。`IModelContextProtocolModule::AddTool` 只需依赖 `ModelContextProtocol` 一个模块。

调用链：

```text
MCP client → Epic Unreal MCP (HTTP) → FUALEngineMcpTool::RunAsync (线程池)
           → FPlatformProcess::ExecProcess → engine_bridge.py (UTF-8 请求/响应文件)
           → ual_mcp/tools.py → 只读 .ual/index.duckdb → TOON / YAML 文本
```

**唯一事实源**：`Server/ual_mcp/tool_schemas.json` 由 `Tools/gen_docs.py` 从 `tools.py` 的 `TOOL_DEFS` 生成，Python MCP Server、生成文档、C++ 引擎内工具三处共用；`--check` 会同时校验 `TOOL_DEFS` 与 `server.py` 的注册函数一致，拦截任何一侧漂移。

启用步骤：

1. 在工程中启用 `ModelContextProtocol` 插件（`UEAssetLens.uplugin` 已声明为 `Optional` 依赖），并确认 `AllToolsets` 按需启用。
2. 先建索引：`python Server/ual_cli/ual_cli.py build --project <root>`。
3. 启动编辑器；`UEAssetLensMCP` 在 `PostEngineInit` 自动 `AddTool`。控制台执行 `ModelContextProtocol.RefreshTools` 可热重载。
4. 客户端连接 `http://127.0.0.1:8000/mcp`；`tools/list` 应出现 `ual_search_assets` 等 4 个工具。

配置（`DefaultEditorPerProject.ini`，路径为空时用默认值）：

```ini
[/Script/UEAssetLensMCP.UALEngineMcpSettings]
PythonExecutable=D:/learning/AnnBuildTools/Ann/Plugins/UEAssetLens/.venv/Scripts/python.exe
IndexPath=D:/learning/AnnBuildTools/Ann/.ual/index.duckdb
```

约束与边界见 `docs/known-limitations.md`：每次工具调用启动一个 Python 子进程（百毫秒级，已用 `RunAsync` 避免阻塞 game thread）；官方 server 仅 loopback、无认证、实验性；离线 CI 门禁仍走 `ual-cli`，不依赖编辑器。

## 当前实现状态（2026-11-15）

| 里程碑 | 状态 | 内容 |
|---|---|---|
| M1 存储底座与通用提取器 | **已实现** | DuckDB DDL（实测 DuckDB 2.0.0）、UE 插件三模块骨架、16 维指纹数学、`IAssetExtractor`、`FUniversalReflectionExtractor`、AssetRegistry 增量监听、无头 Commandlet、JSONL 落盘 |
| M2 四大核心提取器 | **部分** | 指纹算法与 YAML 生成器已就绪；StaticMesh / AnimMontage / Material / Blueprint 的 UE 领域反射代码尚未全量落地 |
| M3 图谱与 MCP Server | **已实现** | 4 个工具、TOON/YAML 唯一序列化实现、`asset_edges` 共现查询；基于官方 MCP SDK 2.x（`MCPServer`），无手写协议降级 |
| M4 CI 门禁 CLI | **已实现** | 4 个命令 + `self-test` + pytest 端到端测试；UE 侧端到端联调待本地 UE SDK |
| M5 引擎内官方 MCP | **已实现** | `UEAssetLensMCP` 用 `AddTool` 注册 4 个工具、`engine_bridge.py` 子进程桥、`tool_schemas.json` 三端共享契约；实机联调状态见 `docs/known-limitations.md` |

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
| `Server/ual_mcp/tool_schemas.json` | `tools.py` 的 `TOOL_DEFS`（供 C++ 引擎内 MCP 工具读取） |

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
