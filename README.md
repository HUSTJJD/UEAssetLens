# UEAssetLens (UAL)

> 零多模态大模型依赖的 Unreal Engine 确定性资产语义索引、防重复检测与 AI Agent（MCP）检索中枢。

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
│  └─ UEAssetLensTests/     # C++ 自动化单元测试
├─ Server/
│  ├─ schema/               # init.sql、rebuild_fts.sql、PROTOCOL.md（唯一数据契约）
│  ├─ ual_common/           # db.py / toon.py / yamlspec.py / fingerprint.py
│  ├─ ual_mcp/              # MCP Server：4 个工具
│  ├─ ual_cli/              # ual-cli：4 个命令 + self-test
│  └─ tests/                # pytest 端到端测试与种子数据
├─ Tools/                   # run_tests.ps1、setup_mcp.ps1
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

# 4) 启动 MCP Server（stdio）
python Server/ual_mcp/server.py --index .ual/index.duckdb
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

## 当前实现状态（2026-11-15）

| 里程碑 | 状态 | 内容 |
|---|---|---|
| M1 存储底座与通用提取器 | **已实现** | DuckDB DDL（实测 DuckDB 2.0.0）、UE 插件三模块骨架、16 维指纹数学、`IAssetExtractor`、`FUniversalReflectionExtractor`、AssetRegistry 增量监听、无头 Commandlet、JSONL 落盘 |
| M2 四大核心提取器 | **部分** | 指纹算法与 YAML 生成器已就绪；StaticMesh / AnimMontage / Material / Blueprint 的 UE 领域反射代码尚未全量落地 |
| M3 图谱与 MCP Server | **已实现** | 4 个工具、TOON/YAML 唯一序列化实现、`asset_edges` 共现查询；MCP SDK 1.x/2.x 双版本兼容 |
| M4 CI 门禁 CLI | **已实现** | 4 个命令 + `self-test` + pytest 端到端测试；UE 侧端到端联调待本地 UE5 SDK |

> 已知边界与未验证项见 `docs/known-limitations.md`。10 万级资产冷建库 <30s、增量 <1s 是设计目标，本仓库尚未提供实测基准。

## 数据契约

`Server/schema/PROTOCOL.md` 是唯一权威。字段变更必须同步 `init.sql`、`Source/UEAssetLens/Public/UALTypes.h`、`Server/ual_common/db.py` 并递增协议版本。

## 许可

见仓库根 LICENSE（如未附带，沿用宿主工程 `Ann` 的许可约定）。
