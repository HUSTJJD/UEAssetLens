# 快速开始

> 本页只讲「怎么跑」。UAL 是什么、完整能力与边界见 [项目定位与能力规格](positioning.md)（唯一事实源，§3 为能力全景）；
> 升级规格主文见 [资产全生命周期架构规格](asset-lifecycle-architecture.md)；
> 字段契约见 [三层数据协议](reference/protocol.md)；`docs/reference/cli.md` 由生成器写入，**禁止手工编辑**。
> 状态使用 **已实现 / 规格 / 未落地**；2026-11-27 完整离线门禁（168 项 pytest、`ual-cli self-test` 17/17、
> `server.py --self-test`、`engine_bridge.py --self-test`、`gen_docs.py --check`、`gen_taxonomy.py --check`、
> `audit_tool_annotations.py --check`、`audit_engine_toolsets.py --check`、`mkdocs build --strict`、
> `bench_index.py --count 10000 --incremental` smoke、`setup_mcp.ps1 -DryRun -Transport Http`）已全部通过。
> 本地 UE6 UBT Build 已通过（`Result: Succeeded`，见 [已知限制](known-limitations.md) §1），**CI 仍不编译 C++**；
> 真实 UE 写入 / 编辑器编译 / PIE / UE 自动化用例仍 **规格 / 未落地**。

## 0. 前置条件与计数

| 场景 | 需要 | 不需要 |
| --- | --- | --- |
| 离线 / CI 自检 | Python ≥ 3.10（CI 固定 3.11）、`requirements.txt` | UE SDK、MCP 客户端 |
| 索引真实工程 | 上述 + UE6 编辑器（`UnrealEditor.exe`） | 独立 Python venv（引擎侧走 `PythonRequirements`） |
| 构建文档站 | `requirements-docs.txt`（MkDocs Material + PyYAML） | `duckdb` / `mcp` 运行时 |

当前契约：DuckDB `schema_meta.schema_version` `3.0`（冻结 16 键 Core Record + 独立 `extension_payload`）；
`Server/ual_mcp/tool_schemas.yaml` `schema_version` `4.0`；10 个既有只读工具 + 20 个 registry-driven runtime tools（共 30 个工具）；
100000 资产实测冷建库 **2.727s**、未变增量 **0.0796s**，两个目标均满足，见 [性能基准](benchmarks.md)。
2026-11-27 用户明确授权后**本地 UE6 UBT Build 已通过**：`dotnet <Engine>/Engine/Binaries/DotNET/UnrealBuildTool/UnrealBuildTool.dll -Mode=Build AnnEditor Win64 Development -Project=<Project>.uproject -Compiler=VisualStudio2022 -NoHotReload -WaitMutex`，
19 个 action、UHT processed AnnEditor（4 个生成文件）、UBA local executor 97.24 秒、`Result: Succeeded`、
`Total execution time: 136.42 seconds`、输出 `UnrealEditor.exe`，仅 C4996 `UE_LOG` 弃用告警、无编译错误
（完整命令见 [已知限制](known-limitations.md) §1）。此前非编译类 UBT 检查（`-Mode=QueryTargets`、
`-Mode=Query -Query=Capabilities` / `AvailableTargets` / `EnabledPlugins` / `TargetDetails -NoIntellisense` /
`CompileDetails`（`-Compiler=VisualStudio2022 -SyntaxCheckOnly`）、
`-Mode=GenerateClangDatabase -NoExecCodeGenActions -Compiler=VisualStudio2022`）仍只是源码级证据。
**CI 仍不编译 C++**；真实 UE 写入 / 编辑器编译 / PIE / UE 自动化用例仍**规格 / 未落地**。

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-docs.txt
```

`<UALRoot>` 指本仓库根（同时也是 UE 插件根），`<ProjectRoot>` 指宿主 UE 工程根，
`<ProjectName>` 指 `.uproject` 文件名（不含扩展名），`<TempConfigRoot>` 指临时配置目录。
以下命令均在 `<UALRoot>` 下执行；示例刻意不写本机绝对路径，也不把它们写进任何 YAML / TOON 产物。

## 1. 建索引（三条路径，任选其一）

### A. 无 UE：随仓种子数据

```bash
python Server/ual_cli/ual_cli.py build --demo
```

适合 CI、离线验证与文档演示。`--demo` 不启动编辑器、不写 staging，直接灌 `.ual/index.duckdb`。

### B. 已有 staging YAML

```bash
python Server/ual_cli/ual_cli.py build --project <ProjectRoot> --from-yaml .ual/staging/ual.yaml --incremental
```

`--from-yaml` 读取 Commandlet 产出的**冻结 16 键 YAML 文档流**（不是 JSONL）；类型化扩展字段由 `fields.py`
写入独立 `extension_payload` 表，不新增第 17 个 staging 键。`--incremental` 按 `content_hash` 跳过未变记录；
`content_hash` 只用于变更检测，不用于查重。批量灌库后会自动重建 FTS 索引（`rebuild_fts.sql`）。

!!! note "干净的纯真实工程索引（已验证）"
    本轮已用 `--index .ual/real_project.duckdb build --project ../.. --from-yaml ../../.ual/staging/index.yaml`
    从 staging（15 资产 / 56 边）重建 `.ual/real_project.duckdb`：15 资产 / 56 边 / 75 行 `extension_payload`，
    `schema_version` `3.0`、`engine_version` `UE6`，FTS `ann character` 返回 3 条有界命中，`index_revision`
    两次读取稳定，位于被忽略 `Intermediate/` 下的只读验证脚本 verdict **PASS**。现有 `.ual/index.duckdb`
    是 seed + 真实数据的混合产物（24 资产 / 61 边），**禁止**引用为干净的纯真实工程全量索引。
    该验证只覆盖离线灌库 / 查询，不代表真实 UE host executor 或 `.uasset` 写入。

### C. 真实 UE6 工程：无头 Commandlet

推荐让 `ual-cli` 代跑——它使用模块限定名，并在引擎异常返回非零时以 staging 产物为准：

```bash
python Server/ual_cli/ual_cli.py build --project <ProjectRoot> --engine-cmd UnrealEditor.exe
```

直接调用时**必须**写 `Module.Class` 限定名：

```bash
UnrealEditor.exe <ProjectRoot>/<ProjectName>.uproject \
  -run=UEAssetLensEditor.UALIndexBuilder \
  -Project=<ProjectName> -Out=.ual/staging -Incremental -IncludePlugins \
  -unattended -nosplash
```

!!! warning "两个常见坑"
    - 裸 `-run=UALIndexBuilder` 在 UE6 会报 `could not find the class UALIndexBuilderCommandlet`：
      `UEAssetLensEditor` 是 `PostEngineInit` 加载的 Editor 模块，而 commandlet 在这之前被解析。
    - 本机 UE6 源码树的 `UnrealEditor-Cmd.exe` 存在引擎模块 BuildId 不匹配，默认请用 `UnrealEditor.exe`。
    - 默认只扫 `/Game`；插件内容需显式 `-IncludePlugins` 或 `-Roots=/Game,/PluginA`。
    - 本地 UE6 UBT Build 已通过（`Result: Succeeded`），但离线门禁与 CI 仍不编译 C++，也不执行真实写入 / PIE；
      后续新增 C++ source-level hooks 仍需宿主工具链单独复验。

## 2. 自检、生成门禁与离线内核命令

```bash
python -m pytest Server/tests -q
python Server/ual_cli/ual_cli.py self-test
python Server/ual_mcp/server.py --self-test
python Server/ual_mcp/engine_bridge.py --self-test
python Tools/gen_taxonomy.py --check
python Tools/gen_docs.py
python Tools/gen_docs.py --check
python Tools/audit_tool_annotations.py --check
python Tools/audit_engine_toolsets.py --check
python Tools/bench_index.py --count 10000 --incremental
python -m mkdocs build --strict --site-dir _site
pwsh -File Tools/run_tests.ps1
```

Windows 上若 pytest 清理临时目录报 `PermissionError: [WinError 5]`，加
`--basetemp=Intermediate\pytest_run -p no:cacheprovider`。`Tools/run_tests.ps1` 的步骤编号固定为 1/13 … 13/13；
`-SkipDocs` 只跳过 MkDocs 依赖安装、参考页生成与严格站点构建，其它审计 / 测试 / benchmark / HTTP DryRun 仍执行。

22 个离线内核命令（由 `python-ci.yml` / `run_tests.ps1` 与 2026-11-27 完整离线门禁校验；`docs/reference/cli.md` 查看完整参数）：

```bash
python Server/ual_cli/ual_cli.py list-extensions
python Server/ual_cli/ual_cli.py list-extension-fields
python Server/ual_cli/ual_cli.py search-extension-fields
python Server/ual_cli/ual_cli.py get-assembly-graph
python Server/ual_cli/ual_cli.py list-official-tools
python Server/ual_cli/ual_cli.py get-index-revision
python Server/ual_cli/ual_cli.py get-lifecycle-events
python Server/ual_cli/ual_cli.py allowlist-audit
python Server/ual_cli/ual_cli.py diff-assets
python Server/ual_cli/ual_cli.py read-diff-layer
python Server/ual_cli/ual_cli.py verify-diff
python Server/ual_cli/ual_cli.py plan-edit
python Server/ual_cli/ual_cli.py apply-plan
python Server/ual_cli/ual_cli.py verify-edit
python Server/ual_cli/ual_cli.py rollback-edit
python Server/ual_cli/ual_cli.py expand-gamespec
python Server/ual_cli/ual_cli.py run-validation-loop
python Server/ual_cli/ual_cli.py run-workflow
python Server/ual_cli/ual_cli.py request-rescan
python Server/ual_cli/ual_cli.py apply-events
python Server/ual_cli/ual_cli.py read-artifact
python Server/ual_cli/ual_cli.py import-gamespec
```

这些命令只产出有界 TOON / canonical YAML 工件，**已实现（离线）**；真实 UE 写入、compile / PIE、host executor
仍为**规格 / 未落地**。`Config/UAL/gateway/integrations.yaml` 中的 P1 / P2 外部服务保持
`disabled` / license-reviewed / Zero-VLM boundary，**not executed**；不接外部生成式服务、不调用 VLM / 云 / GPU，
也不把整库原始索引塞进 LLM。

## 3. 三条 CI 门禁与 allowlist

```bash
python Server/ual_cli/ual_cli.py lint-duplicates --threshold 0.85
python Server/ual_cli/ual_cli.py lint-policy --project <ProjectRoot>
python Server/ual_cli/ual_cli.py lint-policy --project <ProjectRoot> --strict --fail-on warning
python Server/ual_cli/ual_cli.py verify-references --scan-dirs Content/Abilities,Content/Script
python Server/ual_cli/ual_cli.py export-parquet --out .ual/shared_catalog.parquet
```

`lint-policy` 默认只报告（内置规则 `enforce: false`），`--strict` 或 `enforce: true` 才阻断；
stdout 始终是纯 TOON，`PASS` / `BLOCKED` 写 stderr。custom rules 与 review-gated allowlist **已实现**：
自定义谓词来自声明式扩展清单的 builtin allowlist，白名单需要 `reason` / `owner` / `review_by`
与调用侧 `approved_by` / `review_ref`，过期 / 无效条目不豁免门禁。四级偏好覆盖、退出码语义与
GitHub Actions / pre-commit 接入示例见 [CI 门禁](ci-gates.md)。

## 4. 接 MCP 客户端

主通道是编辑器内官方 MCP 的 HTTP loopback（`http://localhost:8000/mcp`），
Python stdio server 只作降级路径：

| 通道 | 何时用 | 启动方 |
| --- | --- | --- |
| 引擎内官方 MCP (HTTP) | 编辑器开着，Agent 直接问资产 | `UEAssetLensMCP` 在 `PostEngineInit` 自动 `AddTool` |
| Python stdio server | CI / 无编辑器 / 官方插件缺失 | 客户端按配置拉起 `Server/ual_mcp/server.py` |

```bash
python Server/ual_mcp/server.py --index .ual/index.duckdb
pwsh -File Tools/setup_mcp.ps1 -DryRun -Transport Http -ConfigRoot <TempConfigRoot>
pwsh -File Tools/setup_mcp.ps1
```

10 个既有工具全部**只读**（6 语义 + 4 图谱），20 个 runtime tools（共 30 个工具） 逐项标注 `accessMode` / `readOnlyHint`；
`Write` / `Execute` 类运行时工具只做离线 dry-run，不是真实 UE 写入。签名与 TOON 字段顺序见
[MCP 工具参考](reference/mcp-tools.md)。阶段 2 `UToolsetDefinition` 迁移 **DECIDED AGAINST for now**，
保持 `AddTool` + 显式限定名 / annotations。官方 MCP / Toolset 为 `Experimental + NoRedist`，
只能开发期启用，不可随发布包分发；server 仅 loopback 且无认证，禁止暴露到非 loopback 地址。

!!! note "真实写入边界"
    UAL 离线提供 dry-run 事务 / gateway / diff / GameSpec 编排，**已实现（离线）**；本地 UE6 UBT Build 已通过但 CI 仍不编译 C++，
    真实 `.uasset` 写入、编辑器编译、PIE、UE 自动化用例、AssetRegistry 事件重扫仍是**规格 / 未落地**。装了官方 MCP 插件也不等于
    「装了 UAL 就能编辑」。详见 [事务与五层差异工件](transaction-and-diff.md) 与 [已知限制](known-limitations.md)。

## 5. 本地构建文档站

```bash
python Tools/gen_docs.py                       # 生成 5 页文档 + tool_schemas.yaml
python Tools/gen_docs.py --check              # 文档漂移门禁
python Tools/gen_taxonomy.py --check          # taxonomy / feature-profile / domain-profile 三个生成头无漂移
python Tools/audit_tool_annotations.py --check
python Tools/audit_engine_toolsets.py --check
python -m mkdocs build --strict --site-dir _site
pwsh -File Tools/run_tests.ps1                # 一键：生成 + 严格构建 + 审计 + pytest + 自检 + benchmark + HTTP DryRun
```

`docs/reference/*.md`、`docs/agent-guide.md` 与 `Server/ual_mcp/tool_schemas.yaml` 由生成器写入，
**禁止手工编辑**。新增人工页已挂进 `mkdocs.yml` 的 `nav`，所有相对链接需通过 `--strict`
（断链会直接让发布失败）；`_site/` 与 `site/` 不入库。
2026-11-27 已重跑 `Tools/gen_docs.py` 并通过 `gen_docs.py --check`、`gen_taxonomy.py --check`、
`audit_tool_annotations.py --check`、`audit_engine_toolsets.py --check` 与 `mkdocs build --strict`（零漂移 / 零警告）。

## 6. 卡住了先看这里

| 症状 | 原因 / 下一步 |
| --- | --- |
| `index not found`，退出码 2 | 先跑 `build --demo` 或 `--from-yaml`；`.ual/` 已 gitignore |
| `No module named 'mcp'` | Python stdio server 需要官方 SDK 2.x（`mcp>=2,<3`）；CI 可只用 `ual-cli` |
| `No module named 'duckdb'` | 装 `requirements.txt`；引擎内桥走 `<Project>/Intermediate/PipInstall`，两套环境不要混用 |
| FTS 召回为空 / 无 BM25 排序 | 灌库后 `rebuild_fts.sql` 未跑，或 `fts` 扩展不可用而降级为 `ILIKE` |
| custom rule / allowlist 报错 | 检查声明式扩展清单、review 元数据与 `approved_by` / `review_ref`；见 [扩展体系](extensions.md) |
| YAML staging 解析失败 | Blueprint 的 `FiBData` / `Thumbnail` 二进制 Tag 含 C1 控制字符，见 [开发规范](agent-guide.md) §4.2 |
| C++ 编译或真实 UE 写入问题 | 本地 UE6 UBT Build 已通过（`Result: Succeeded`），但离线门禁与 CI 仍不编译 C++、不执行 UE 写入 / PIE；新的 C++ 改动需宿主工具链单独复验，见 [已知限制](known-limitations.md) |

完整实现边界与未验证项见 [已知限制](known-limitations.md)；量化事实与状态口径仍以 [项目定位](positioning.md) 为唯一事实源。
2026-11-27 完整离线门禁、干净索引重建与验证、非编译类 UBT 检查与本地 UE6 UBT Build 均已通过，但**不代表**所有规格已验证：
真实 UE host executor / 写入 / 编辑器编译 / PIE / UE 自动化用例仍**规格 / 未落地**。
