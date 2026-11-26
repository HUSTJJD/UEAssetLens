# 需求追踪矩阵（C1–C20）

> 本页的 C1–C20 是文档追踪键，不是新的产品规格；「UAL 是什么 / 完整能力」的唯一事实源是 **[项目定位与能力规格](positioning.md)**。状态只使用 **已实现 / 规格 / 未落地**；离线代码存在不等于真实 UE 写入、编译或 PIE 已验证。

| ID | 追踪要求 | 主要文件 | 测试 / 门禁 | 状态 |
| --- | --- | --- | --- | --- |
| C1 | Zero-VLM / 离线 / 无云端 / 无 GPU / 无外部生成式服务；P1 / P2 外部服务 disabled / license-reviewed / Zero-VLM boundary，not executed | `CLAUDE.md`、`docs/positioning.md`、`Server/ual_common/*`、`Config/UAL/gateway/integrations.yaml`、`docs/known-limitations.md` | `.github/workflows/python-ci.yml`；`python -m pytest Server/tests -q` | **已实现**（红线与离线实现） |
| C2 | 不把整库 / 全量原始数据返回给 LLM；批量走 TOON、单资产走 YAML、完整结果落工件 + 分页 | `Server/ual_common/toon.py`、`yamlspec.py`、`artifacts.py`、`asset_diff.py` | `test_ual.py::test_mcp_tools_obey_protocol`、`test_toon_roundtrip_with_escaping`、`test_transaction.py::test_artifacts_have_no_timestamps_absolute_paths_or_random_keys` | **已实现** |
| C3 | 16 键 Core Record 有意冻结；类型化扩展走独立 `extension_payload` 表 | `Server/schema/PROTOCOL.md`、`init.sql`、`Source/UEAssetLens/Public/UALTypes.h`、`Server/ual_common/db.py`、`fields.py` | `test_ual.py::test_schema_is_idempotent`、`test_type_coverage.py` | **已实现**（DuckDB `schema_version` `3.0`） |
| C4 | 字段变更四处同步并 bump `schema_meta.schema_version` | `PROTOCOL.md`、`init.sql`、`UALTypes.h`、`db.py`、`CLAUDE.md` | `test_ual.py::test_schema_is_idempotent`；`Tools/gen_docs.py --check` | **已实现** |
| C5 | 九个 namespace 的声明式扩展清单与四级偏好覆盖 | `Config/UAL/extensions/*.yaml`、`settings.py`、`registry.py`、`extensions.py` | `test_policy.py::test_four_layer_override_keeps_first_seen_order`、`test_registry_resolution_is_deterministic_and_typed` | **已实现** |
| C6 | YAML 不执行任意代码；builtin callable 只允许 `ual_common.extensions_builtins` | `Server/ual_common/extensions.py`、`extensions_builtins.py`、`Config/UAL/policies/default.yaml` | `test_policy.py::test_custom_rule_goes_through_extension_registry`；`Tools/audit_tool_annotations.py --check` | **已实现** |
| C7 | 既有 10 个工具来自 `TOOL_DEFS`、20 个 runtime tools（共 30 个工具） 来自 registry snapshot；限定 `UEAssetLens.*` 名；无裸 `ual_*`；逐工具 AIAccessMode / readOnlyHint | `tools.py::TOOL_DEFS`、`Config/UAL/extensions/tools.yaml`、`extensions.py`、`Tools/gen_docs.py`、`Server/ual_mcp/tool_schemas.yaml` | `Tools/audit_tool_annotations.py --check`、`Tools/audit_engine_toolsets.py --check`、`Tools/gen_docs.py --check`、`test_ual.py::test_tool_schemas_contract_matches_python_server` | **已实现** |
| C8 | `tool_schemas.yaml` `4.0` 与 DuckDB `3.0` 是两套契约，不联动 | `PROTOCOL.md`、`init.sql`、`tool_schemas.yaml`、`docs/mcp-positioning.md` | `python-ci.yml`；`test_ual.py` schema / tool-schema contract checks | **已实现** |
| C9 | custom policy rules 从声明式 manifest 装载并经 `lint-policy` 求值 | `Config/UAL/policies/default.yaml`、`extensions/builtin.yaml`、`policy.py`、`policy_gate.py` | `test_policy.py::test_custom_rule_goes_through_extension_registry`、`test_policy_gate.py` | **已实现** |
| C10 | allowlist 具备 review gating、审批引用、有效期与失效不豁免 | `Config/UAL/allowlists/default.yaml`、`Server/ual_common/allowlist.py`、`docs/ci-gates.md` | `python-ci.yml`；`Tools/run_tests.ps1`；`lint-duplicates` / `lint-policy` 门禁路径 | **已实现** |
| C11 | 离线事务 / 网关：dry-run 默认、审批、白名单、inverse、verify、rollback | `transaction.py`、`gateway.py`、`Config/UAL/gateway/official_tools.yaml`、`artifacts.py` | `test_transaction.py`、`test_gateway.py` | **已实现（离线）** |
| C12 | 真实 UE host executor 调用官方 Toolset、真实写入、编译与 PIE；P1 / P2 外部服务保持 disabled / license-reviewed / Zero-VLM boundary，not executed | `docs/mcp-positioning.md`、`docs/known-limitations.md`、`Config/UAL/gateway/official_tools.yaml`、`Config/UAL/gateway/integrations.yaml` | 无 UE SDK / 无编辑器 CI；本地 UE6 UBT Build 已通过（`Result: Succeeded`）但不在 CI；真实写入 / 编辑器编译 / PIE / UE 自动化用例仍需宿主实机验证 | **规格**（真实写入、compile 与 PIE 未落地） |
| C13 | 五层 diff 工件 L0–L4、canonical YAML、摘要指针、分页、确定性复验 | `asset_diff.py`、`Config/UAL/diff/layers.yaml`、`artifacts.py` | `python-ci.yml` 全量 pytest；`test_transaction.py` 覆盖 canonical artifact 约束 | **已实现（离线投影）** / **规格**（UE 原生二进制 diff） |
| C14 | GameSpec / FPS 3C 模板展开、十步链与离线验证循环 | `gamespec.py`、`workflow.py`、`Config/UAL/templates/fps_3c.yaml` | `python-ci.yml`；`Tools/run_tests.ps1`；2026-11-27 完整离线门禁已通过（168 项 pytest 等） | **已实现（离线编排）** / **规格**（真实 compile / PIE） |
| C15 | 生命周期 hooks、审计、apply-events、rescan request、`index_revision` | `lifecycle.py`、`revision.py`、`transaction.py`、`workflow.py` | `test_transaction.py::test_request_rescan_stages_sorted_paths`、`test_rollback_uses_inverse_ops_and_stages_rescan` | **已实现（离线）** |
| C16 | Python M2 reference + C++ zero-load domain extractor implemented at source level；本地 UE6 UBT Build 已通过、CI 仍不编译 C++；未加载 mesh / animation | `domain.py`、`fields.py`、`Config/UAL/fields/default.yaml`、`UALDomainFeatureExtractor`、`UALDomainProfiles.h`、`Source/UEAssetLensEditor/**` | `test_type_coverage.py`、`test_taxonomy_generation.py`（168 项 pytest 已通过）；2026-11-27 本地 UE6 UBT Build `Result: Succeeded`（见 [已知限制](known-limitations.md) §1）；真实 UE 端到端反射级深度规格仍规格 | **已实现（Python / C++ 源码级）** / **规格**（真实 UE 端到端深度规格 / UE 自动化用例） |
| C17 | HTTP loopback 客户端配置；Experimental + NoRedist；无认证且不可分发 | `Tools/setup_mcp.ps1`、`docs/quickstart.md`、`docs/known-limitations.md` | `Tools/run_tests.ps1` 的 `setup_mcp.ps1 -DryRun -Transport Http` | **已实现**（客户端配置 DryRun） |
| C18 | 100000 资产冷建库与未变增量基准 | `Tools/bench_index.py`、`docs/benchmarks.md` | `python-ci.yml` 的 `--count 10000 --incremental` smoke；实测 2.727s / 0.0796s，<30s / <1s 两个目标均满足 | **已实现（离线测量）**；非 C++ 编译结论 |
| C19 | 生成门禁、Python CI、annotation / engine-toolset audits、22 个新增 `ual-cli` 离线命令、一键脚本；无 C++ compile / 无 auto push | `.github/workflows/docs.yml`、`.github/workflows/python-ci.yml`、`Tools/gen_docs.py`、`Tools/gen_taxonomy.py`、`Tools/audit_tool_annotations.py`、`Tools/audit_engine_toolsets.py`、`Tools/run_tests.ps1` | `gen_docs.py --check`、`gen_taxonomy.py --check`、`audit_tool_annotations.py --check`、`audit_engine_toolsets.py --check`、`mkdocs build --strict`、`pytest Server/tests -q`（168 项）、`ual-cli self-test`（17/17）、`server.py --self-test`、`engine_bridge.py --self-test`、`bench_index.py --count 10000 --incremental` smoke、HTTP DryRun；2026-11-27 全部通过 | **已实现** |
| C20 | 真实 UE 写入 / 编辑器编译 / PIE / UE 自动化用例未在 CI 或离线门禁验证；2026-11-27 用户明确授权后本地 UE6 UBT Build 已通过（`AnnEditor Win64 Development`，19 个 action、UHT 写入 4 个生成文件、UBA 97.24 秒、`Result: Succeeded`、总耗时 136.42 秒、输出 `UnrealEditor.exe`、仅 C4996 `UE_LOG` 弃用告警、无编译错误），此前非编译类 UBT 检查与 22 个翻译单元的 MSVC `/Zs` 仅语法检查仍只是源码级证据；**CI 仍不编译 C++**；P1 / P2 外部服务 disabled / license-reviewed / Zero-VLM boundary，not executed | `docs/known-limitations.md`、`CLAUDE.md`、`Source/**`、`Config/UAL/gateway/integrations.yaml` | CI 明确不编译 C++；本地宿主 UBT Build 通过不等于真实 UE 运行时验证；无云服务 / 密钥 / 外部生成式推理步骤；不自动 push | **规格 / 未落地** |

## 命令入口（2026-11-27 已由完整离线门禁校验）

22 个新增 `ual-cli` 离线命令：`list-extensions`、`list-extension-fields`、`search-extension-fields`、
`get-assembly-graph`、`list-official-tools`、`get-index-revision`、`get-lifecycle-events`、`allowlist-audit`、
`diff-assets`、`read-diff-layer`、`verify-diff`、`plan-edit`、`apply-plan`、`verify-edit`、`rollback-edit`、
`expand-gamespec`、`run-validation-loop`、`run-workflow`、`request-rescan`、`apply-events`、
`read-artifact`、`import-gamespec`。
状态为 **已实现（离线；2026-11-27 已由完整离线门禁校验）**；真实 UE host executor / PIE
仍 **规格 / 未落地**。CI 还运行 `Tools/audit_engine_toolsets.py --check` 审计离线 `tools/list` inventory，
与 `Tools/audit_tool_annotations.py --check` 一起作为只读漂移门禁。

`Config/UAL/gateway/integrations.yaml` 中的 P1 / P2 外部服务保持 `disabled` / license-reviewed /
Zero-VLM boundary，**not executed**。本地 UE6 UBT Build 已通过（`Result: Succeeded`，见 [已知限制](known-limitations.md) §1），
但 **CI 仍不编译 C++**；真实 UE 写入 / 编辑器编译 / PIE / UE 自动化用例仍**规格 / 未落地**。

> 本页不改变 [项目定位与能力规格](positioning.md) 的状态口径。2026-11-27 完整离线门禁已全部通过：168 项 pytest、
> `ual-cli self-test` 17/17、`server.py --self-test`、`engine_bridge.py --self-test`、`gen_docs.py --check`、
> `gen_taxonomy.py --check`、`audit_tool_annotations.py --check`、`audit_engine_toolsets.py --check`、
> `mkdocs build --strict`、`bench_index.py --count 10000 --incremental` smoke、`setup_mcp.ps1 -DryRun -Transport Http`；
> 干净的纯真实工程索引已从 staging 重建到 `.ual/real_project.duckdb` 并验证 PASS（15 资产 / 56 边 / 75 行 `extension_payload`），
> 现有 `.ual/index.duckdb` 为混合产物（24 资产 / 61 边），不得引用为干净的纯真实工程全量索引。
> 2026-11-27 用户明确授权后本地 UE6 UBT Build 亦已通过（`Result: Succeeded`，见 [已知限制](known-limitations.md) §1），
> 但 **CI 仍不编译 C++**；以上离线门禁与宿主 C++ 编译结果均**不代表**所有规格已验证：真实 UE host executor /
> 写入 / 编辑器编译 / PIE / UE 自动化用例仍 **规格 / 未落地**。
> 完整边界见 [已知限制](known-limitations.md) 与 [离线内核实现](kernel-implementation.md)。
