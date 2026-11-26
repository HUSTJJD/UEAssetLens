# 离线内核实现

> 本文只描述 Phase 1–5 的离线实现结构与边界，不定义产品定位。「UAL 是什么 / 完整能力 / 现在做到哪一步」的唯一事实源是 **[项目定位与能力规格](positioning.md)**；本文所有结论均使用 **已实现 / 规格 / 未落地** 状态标记，规格不写成已验证结论。

## 1. 状态总览

| 层 | 主要文件 / 数据 | 状态 | 边界 |
| --- | --- | --- | --- |
| 数据契约 | `Server/schema/PROTOCOL.md`、`init.sql`、`Source/UEAssetLens/Public/UALTypes.h`、`Server/ual_common/db.py`、`schema_meta` | **已实现** | DuckDB `schema_version` `3.0`；16 键 Core Record 有意冻结，扩展走独立 `extension_payload` 类型化表；四处同步并 bump `schema_meta` |
| 声明式配置 | `Config/UAL/extensions/*.yaml`、`fields/default.yaml`、`allowlists/default.yaml`、`diff/layers.yaml`、`templates/fps_3c.yaml`、`gateway/official_tools.yaml` | **已实现** | YAML 只作为数据；不解析任意代码引用 |
| 偏好与注册 | `Server/ual_common/settings.py`、`registry.py`、`extensions.py` | **已实现** | builtin < plugin < project < user 四层合并；`ExtensionRegistry` 按 namespace + name 唯一注册 |
| 运行时工具 | `Config/UAL/extensions/tools.yaml` → `ExtensionRegistry.snapshot()` → `Tools/gen_docs.py` → `Server/ual_mcp/tool_schemas.yaml` | **已实现** | `tool_schemas.yaml` `schema_version` `4.0`，与 DuckDB `3.0` 独立；无裸 `ual_*` 线名；逐工具有 `accessMode` / `readOnlyHint` |
| 类型化字段 / M2 参考提取 | `Server/ual_common/fields.py`、`domain.py`、`Config/UAL/fields/default.yaml`、`UALDomainFeatureExtractor`、`UALDomainProfiles.h` | **已实现（Python 离线参考 + C++ 源码级 zero-load）** | 从既有 16 键 / MAP / `FAssetData` 投影，不加载 UObject / mesh / animation；本地 UE6 UBT Build 已通过、CI 仍不编译 C++，真实 UE 端到端深度规格 **规格** |
| 规范工件 | `Server/ual_common/artifacts.py`、`toon.py`、`yamlspec.py` | **已实现** | 计划 / diff / GameSpec / 审计 / rescan 一律走 canonical emitter；禁止手写 YAML / TOON 字符串拼接；输出不含本地绝对路径、时间戳、随机 ID |
| 策略 / 自定义规则 / 白名单 | `policy.py`、`policy_gate.py`、`allowlist.py`、`extensions_builtins.py` | **已实现** | 自定义谓词只来自声明式扩展清单中的 builtin allowlist；白名单需 review 元数据、审批引用与有效期 |
| 事务 / 网关 | `transaction.py`、`gateway.py`、`Config/UAL/gateway/official_tools.yaml` | **已实现（离线 dry-run）** | MCP 侧 DuckDB 仍只读；真实 UE host executor、真实写入与索引重扫执行器仍 **规格** |
| 生命周期 / 审计 / 重扫 / revision | `lifecycle.py`、`revision.py`、`transaction.py` | **已实现（离线）** | 事件 ID 为内容摘要；apply-events 与 rescan request 只写有界规范工件；真实引擎事件触发仍 **规格** |
| 五层 diff | `asset_diff.py`、`Config/UAL/diff/layers.yaml`、`artifacts.py` | **已实现（离线投影）** | L0–L4 是只读索引 / 文本投影与规范化摘要；不加载 UObject、不导出原始二进制；UE 原生二进制 diff 仍 **规格** |
| GameSpec / FPS 3C | `gamespec.py`、`workflow.py`、`Config/UAL/templates/fps_3c.yaml` | **已实现（离线编排）** | 十步链与验证循环只产出有界 TOON + 工件指针；真实编译 / PIE / 外部生成式服务均 **规格 / 未落地** |
| 客户端 / 基准 | `Tools/setup_mcp.ps1`、`Tools/bench_index.py` | **已实现** | HTTP 仅 loopback、无认证、Experimental + NoRedist、绝不随发布包分发；[性能基准](benchmarks.md)记录离线测量值 |

## 2. 数据流

```text
Config/UAL/** (四级偏好)
  -> settings.py / extensions.py
  -> ExtensionRegistry snapshot
  -> tool_schemas.yaml (4.0) + server.py / engine_bridge.py
  -> 有界 TOON / YAML，绝不把整库原始数据返回给 LLM

16 键 Core Record (冻结)
  -> fields.py / domain.py 投影
  -> extension_payload (类型化)
  -> revision.py / asset_diff.py / runtime tools

transaction.py + gateway.py
  -> dry-run plan / approval / inverse / rescan artifact
  -> 注入的 host executor 才可能进入真实 UE 写入（规格）
```

生成器不再要求所有工具都是 `TOOL_DEFS` 的 AST 字面量：既有 10 个只读工具仍来自 `tools.py::TOOL_DEFS`，20 个 runtime tools（共 30 个工具） 来自 `Config/UAL/extensions/tools.yaml` 的 `ExtensionRegistry.snapshot()`，`Tools/gen_docs.py` 合并两者发布 `tool_schemas.yaml` v4.0。`Tools/audit_tool_annotations.py --check` 审计限定名、权限标注和重复名，`Tools/audit_engine_toolsets.py --check` 审计离线 `tools/list` inventory。阶段 2 `UToolsetDefinition` 迁移目前**明确决定不做**，保持 `AddTool` + 显式限定名与 annotations，详见 [MCP 定位与事务层规格](mcp-positioning.md)。

## 3. 写入边界

- **已实现**：离线事务可以产 plan、审批记录、`inverse_ops`、dry-run apply、verify、rollback、rescan request、lifecycle events，以及完整 diff / GameSpec 工件。
- **规格**：真实 UE host executor 调用官方 Toolset、真实 `.uasset` 写入、编辑器编译、PIE、AssetRegistry 事件重扫、C++ 侧源码级 hooks 的编译验证。
- **已通过（宿主工具链）**：2026-11-27 本地 UE6 UBT Build `Result: Succeeded`（见 [已知限制](known-limitations.md) §1）；此前非编译类 UBT 检查与 22 个翻译单元的 MSVC `/Zs` 仅语法检查仍只是源码级证据，**CI 仍不编译 C++**。
- **规格 / 未落地**：真实编辑器写入 / PIE / UE 自动化用例、AssetRegistry 事件重扫未执行；`Source/` 中新增 hooks 的运行时能力只按源码级看待。
- **零外部生成式服务**：不调用 VLM、云端推理、外部 GPU、模型服务；自然语言 → GameSpec 由宿主模型负责，Lens 只做确定性展开、校验与编排。

## 4. 关联页面

- [需求追踪矩阵](requirements-traceability.md)：C1–C20 → 文件 / 测试 / 状态。
- [事务与五层差异工件](transaction-and-diff.md)：dry-run、审批、inverse 与 L0–L4 工件。
- [GameSpec / FPS 3C 工作流](gamespec-workflow.md)：十步链与离线 runner 边界。
- [扩展体系](extensions.md)：九个 namespace、四级偏好、custom rules、allowlist。
- [性能基准](benchmarks.md)：100000 资产冷建库与未变增量的实测方法。
- [已知限制](known-limitations.md)：C++ 编译、真实 UE host executor / PIE 与未验证项。

## 5. `ual-cli` 命令入口与 CI 边界

22 个离线内核命令（**已实现（离线；2026-11-27 完整离线门禁已全部通过）**）：

| 组 | 命令 |
| --- | --- |
| 扩展 / 字段 / 图谱 | `list-extensions`、`list-extension-fields`、`search-extension-fields`、`get-assembly-graph`、`list-official-tools`、`get-index-revision`、`get-lifecycle-events` |
| 治理 | `allowlist-audit` |
| 五层 diff | `diff-assets`、`read-diff-layer`、`verify-diff` |
| 事务 / 重扫 | `plan-edit`、`apply-plan`、`verify-edit`、`rollback-edit`、`request-rescan`、`apply-events` |
| GameSpec / workflow | `expand-gamespec`、`run-validation-loop`、`run-workflow` |
| 工件 / 导入 | `read-artifact`、`import-gamespec` |

`python-ci.yml` 与 `Tools/run_tests.ps1` 另运行 `gen_docs.py --check`、`gen_taxonomy.py --check`、
`audit_tool_annotations.py --check`、`audit_engine_toolsets.py --check`、`server.py --self-test`、
`engine_bridge.py --self-test`、`bench_index.py --count 10000 --incremental` 与
`setup_mcp.ps1 -DryRun -Transport Http`。`audit_engine_toolsets.py --check` 只读审计离线 `tools/list` inventory；
`audit_tool_annotations.py --check` 只读审计限定名 / annotations / meta；二者都不得编辑生成物。

`Config/UAL/gateway/integrations.yaml` 中的 P1 / P2 外部服务保持 `disabled` / license-reviewed / Zero-VLM boundary，
**not executed**：不接外部生成式服务、不调用 VLM / 云 / GPU、不把整库原始索引塞进 LLM。非编译类 UBT 检查与
本地 UE6 UBT Build 已通过（2026-11-27 用户明确授权，`AnnEditor Win64 Development`，`Result: Succeeded`，总耗时 136.42 秒，见 [已知限制](known-limitations.md) §1）；此前 22 个翻译单元的 MSVC `/Zs` 仅语法检查仍只是源码级证据。
**CI 仍不编译 C++**，真实 UE 写入 / 编辑器编译 / PIE / UE 自动化用例仍 **规格 / 未落地**。
「UAL 是什么」唯一事实源仍是 [项目定位与能力规格](positioning.md)。
