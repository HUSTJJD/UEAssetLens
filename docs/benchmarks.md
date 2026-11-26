# 性能基准

> 本文只记录离线索引基准，不定义产品定位；「UAL 是什么」唯一事实源是 **[项目定位与能力规格](positioning.md)**。状态使用 **已实现 / 规格 / 未落地**。

## 1. 实测结果

| 场景 | 资产数 | 实测耗时 | 目标 | 状态 |
| --- | ---: | ---: | --- | --- |
| 冷建库 | 100000 | **2.727s** | < 30s | **已实现（离线测量）** |
| 未变增量 | 100000 | **0.0796s** | < 1s | **已实现（离线测量）** |

这两个数字来自 `Tools/bench_index.py --count 100000 --incremental` 的 TOON 报告；<30s / <1s **两个目标均满足**。它们是 Python / DuckDB 离线索引基准，**不是** UE 编辑器编译、PIE、C++ 提取器编译或真实 `.uasset` 写入的验证结论。2026-11-27 完整离线门禁（含 `--count 10000 --incremental` smoke）已全部通过；本地 UE6 UBT Build 亦已通过（`Result: Succeeded`），但 **CI 也不编译 C++**；真实 UE 写入 / 编辑器编译 / PIE / UE 自动化用例仍**规格 / 未落地**。

## 2. 方法

- 资产行由 SQL `range(?)` 在 DuckDB 内确定性生成，避免 Python 侧拼接十万条原始记录；行数由绑定参数控制。
- 冷建库计时包含 schema 初始化、批量 `INSERT` 与 FTS 重建；目标阈值为 `COLD_TARGET_SECONDS = 30.0`。
- 未变增量只做只读 `content_hash` 变更探测；全部未变时无写入、无 FTS 重建；目标阈值为 `INCREMENTAL_TARGET_SECONDS = 1.0`。
- `content_hash` 只用于增量变更检测，**不用于查重**；查重仍走 `fingerprint_vec` 余弦相似度。
- DuckDB 文件位于进程临时目录并在结束时删除；报告由 `ual_common/toon.py` 唯一 emitter 输出，不写本地绝对路径、时间戳、随机 ID，也不把整库原始数据塞进 LLM 上下文。
- `python-ci.yml` 与 `Tools/run_tests.ps1` 使用 `--count 10000 --incremental` 作为较小的 CI smoke，不覆盖上表 100000 资产的实测数字。

## 3. 边界与状态

| 项 | 状态 | 说明 |
| --- | --- | --- |
| DuckDB 冷建库 / 未变增量基准脚本 | **已实现** | `Tools/bench_index.py`，零 VLM、零云端、零 GPU、无网络 |
| 100000 资产 2.727s / 0.0796s | **已实现（离线测量）** | 仅描述本次基准方法下的 Python / DuckDB 数值 |
| 真实 UE 无头 Commandlet 全工程扫描耗时 | **规格** | 未在离线门禁中重新测量，依赖本机 UE6 SDK |
| C++ 源码编译 | **已实现（宿主编译验证）** | 2026-11-27 本地 UE6 UBT Build `Result: Succeeded`（见 [已知限制](known-limitations.md) §1）；**CI 仍不编译 C++** |
| UE 自动化用例 / PIE / 真实 `.uasset` 写入 | **规格 / 未落地** | `UEAssetLensTests` 自动化用例、headless 编辑器联调、真实 UE 写入 / 编辑器编译 / PIE 未执行；C++ 编译通过不改变此状态 |
| 外部生成式服务 / VLM / GPU | **未落地** | 红线禁止，基准路径不调用 |

## 4. 相关离线命令与集成边界

基准与离线内核共用同一组无网络、无 VLM、无 GPU 的自检命令。22 个 `ual-cli` 离线命令为
`list-extensions`、`list-extension-fields`、`search-extension-fields`、`get-assembly-graph`、
`list-official-tools`、`get-index-revision`、`get-lifecycle-events`、`allowlist-audit`、
`diff-assets`、`read-diff-layer`、`verify-diff`、`plan-edit`、`apply-plan`、`verify-edit`、
`rollback-edit`、`expand-gamespec`、`run-validation-loop`、`run-workflow`、`request-rescan`、`apply-events`、
`read-artifact`、`import-gamespec`；
它们由 `python-ci.yml` / `Tools/run_tests.ps1` 校验，2026-11-27 完整离线门禁已全部通过，状态为 **已实现（离线）**。
CI 还运行 `gen_taxonomy.py --check`、`audit_tool_annotations.py --check`、
`audit_engine_toolsets.py --check`、`server.py --self-test` 与 HTTP setup DryRun。

`Config/UAL/gateway/integrations.yaml` 中的 P1 / P2 外部服务保持 `disabled` / license-reviewed /
Zero-VLM boundary，**not executed**；不接外部生成式服务。真实 UE 写入 / compile / PIE 与 C++ 源码编译复核均为
**规格 / 未落地**。完整实现边界见 [已知限制](known-limitations.md)；内核结构见 [离线内核实现](kernel-implementation.md)；
「UAL 是什么」唯一事实源仍是 [项目定位与能力规格](positioning.md)。
