# CI 门禁（M4 + 离线内核）

UAL 把资产质量问题与离线内核契约变成 CI 约束：重复资产、断裂引用、规范违背、生成漂移、运行时工具标注、引擎工具集 inventory、Python 契约、离线自检与基准。前两个门禁「发现问题即非零退出」；`lint-policy` 默认只报告，需 `enforce: true` 或 `--strict` 才阻断。「UAL 是什么」唯一事实源是 [项目定位与能力规格](positioning.md)；本页只描述门禁与状态，不定义产品。

**当前计数**：DuckDB `schema_meta.schema_version` `3.0`（冻结 16 键 Core Record + `extension_payload`）；
`Server/ual_mcp/tool_schemas.yaml` `schema_version` `4.0`；10 个既有只读工具 + 20 个 registry-driven runtime tools（共 30 个工具）；
`ual-cli` 既有 5 个命令 + `self-test` + 22 个离线内核命令：
`list-extensions`、`list-extension-fields`、`search-extension-fields`、`get-assembly-graph`、
`list-official-tools`、`get-index-revision`、`get-lifecycle-events`、`allowlist-audit`、
`diff-assets`、`read-diff-layer`、`verify-diff`、`plan-edit`、`apply-plan`、`verify-edit`、
`rollback-edit`、`expand-gamespec`、`run-validation-loop`、`run-workflow`、`request-rescan`、`apply-events`、
`read-artifact`、`import-gamespec`。
完整用法见 [快速开始](quickstart.md) §2；`docs/reference/cli.md` 由生成器写入，**禁止手工编辑**。
100000 资产实测冷建库 **2.727s**、未变增量 **0.0796s**，两个目标均满足（<30s / <1s），见 [性能基准](benchmarks.md)。
2026-11-27 完整离线门禁已全部通过：`pytest Server/tests -q` 168 项、`ual-cli self-test` 17/17、
`server.py --self-test`、`engine_bridge.py --self-test`、`gen_docs.py --check`、`gen_taxonomy.py --check`、
`audit_tool_annotations.py --check`、`audit_engine_toolsets.py --check`、`mkdocs build --strict`、
`bench_index.py --count 10000 --incremental` smoke、`setup_mcp.ps1 -DryRun -Transport Http`。
**本地 UE6 UBT Build 已通过**（2026-11-27 用户明确授权，`AnnEditor Win64 Development`，19 个 action、
UHT 写入 4 个生成文件、UBA 97.24 秒、`Result: Succeeded`、总耗时 136.42 秒、输出 `UnrealEditor.exe`，
仅 C4996 `UE_LOG` 弃用告警、无编译错误，详见 [已知限制](known-limitations.md) §1）；**CI 仍不编译 C++**。
此前非编译类 UBT 检查与 22 个翻译单元的 MSVC `/Zs` 仅语法检查仍只是源码级证据。
C++ 编译通过不代表所有规格已验证：真实 UE host executor / 写入 / 编辑器编译 / PIE / UE 自动化用例仍**规格 / 未落地**。

## 1. `lint-duplicates`：重复资产门禁

### 原理：`array_cosine_similarity` 自连接

查重基于 `assets.fingerprint_vec FLOAT[16]`。实现见 `db.duplicate_pairs()`，等价于：

```sql
SELECT * FROM (
    SELECT a.asset_path AS lhs, b.asset_path AS rhs,
           a.logical_id AS lhs_id, b.logical_id AS rhs_id,
           array_cosine_similarity(a.fingerprint_vec, b.fingerprint_vec) AS similarity
    FROM assets a
    JOIN assets b ON a.asset_path < b.asset_path   -- 避免自比与重复对
    WHERE a.fingerprint_vec IS NOT NULL AND b.fingerprint_vec IS NOT NULL
) q
WHERE q.similarity >= 0.85                         -- --threshold
ORDER BY q.similarity DESC, q.lhs;
```

- `array_cosine_similarity` 要求 DuckDB ≥ 1.1；已在 **2.0.0** 实测通过。旧版本可降级为 `list_cosine_similarity(fp::FLOAT[], target::FLOAT[])`。
- 缺失指纹写 `NULL` 并被 `WHERE` 排除；不写全零向量，避免余弦退化。
- 命中时输出 YAML 审计报告（含 `lhs` / `rhs` / `similarity` / `verdict`），供人工复核。
- `content_hash` 只用于增量变更检测，**不用于查重**。

`--diff-only` 把参与自连接的集合限制为**本次 Git 变更新增的 `Content/` 资产**（`git diff --diff-filter=A HEAD` + 未跟踪文件，映射为 `/Game/...`），使 PR 场景只检查增量。它**不**包含 `content_hash` 变化但路径未新增的资产。

```bash
python Server/ual_cli/ual_cli.py lint-duplicates --threshold 0.85
python Server/ual_cli/ual_cli.py lint-duplicates --threshold 0.85 --diff-only
```

### 退出码

| 情况 | 行为 | 退出码 |
| --- | --- | --- |
| 存在任一 `similarity >= threshold` 的资产对且未被有效 allowlist 豁免 | stdout 打印 YAML 审计，stderr 打印 `BLOCKED` | **1** |
| 无超阈值对，或 `--diff-only` 未发现新增资产 | 打印 `PASS` | 0 |
| 索引缺失 | stderr 打印引导信息（`--demo` / `--from-yaml`） | 2 |

门禁**不会自动删除或合并资产**，只阻断合入并给出候选清单。

## 2. `verify-references`：引用完整性门禁

扫描 `--scan-dirs` 下的 `.yaml` / `.yml` / `.luau` / `.lua`，做三类静态校验：

| 类别 | 实现方式 | 已知边界 |
| --- | --- | --- |
| 资产路径 | 正则提取 `/Game/...`，检查是否存在于 `assets` | 运行期动态生成的资产会被误报 |
| 骨架匹配 | 配置中 `skeleton: <path>` 与所引用动画资产的 `skeleton_path` 比对 | 跨骨架 retarget 的合法用例会被误报 |
| 动态材质参数 | `User.<Param>` 与 `SetScalar/VectorParameterValue("...")` 提取参数名，比对资产 `domain_spec['exposed_params']` 逗号分隔白名单 | 若库内无任何资产声明 `exposed_params`，该类校验整体跳过 |

输出 YAML 审计，包含 `files_scanned` / `known_assets` / `issues` / `findings`。任一 issue 即退出码 1。

```bash
python Server/ual_cli/ual_cli.py verify-references --scan-dirs Content/Abilities,Content/Script
```

> 这是**静态启发式**校验，不是 UE 反射级语义解析。它的价值是拦截 AI 幻觉提交，不能替代编辑器内的引用完整性检查。

## 3. `lint-policy`：声明式规范门禁与 custom rules

`lint-policy` 把 `ual_common.policy.PolicyEngine` 接到 CI：只读打开 `.ual/index.duckdb`，把 `assets` 的
`asset_path` / `logical_id` / `asset_class` / `domain_spec` / `interface_spec` 与 `DEPENDS_ON` 边投影成策略输入，
输出有界 TOON 报告（`violations` 表 + `summary`）。`CO_OCCURS` 是名称相似度提示，不参与引用规则。

规则来自四级偏好解析（`Server/ual_common/settings.py`），按 `id` 合并、后层覆盖前层并保持首次出现顺序：

| 层 | 路径 | 用途 |
| --- | --- | --- |
| builtin | `<Plugin>/Config/UAL/policies/` | 随插件发布的默认规则（`enforce: false`，只报告） |
| plugin | `<Project>/Plugins/*/Config/UAL/policies/` | 扩展插件默认值 |
| project | `<Project>/Config/UAL/policies/` | 团队规范，入 Git |
| user | `<Project>/Saved/Config/UAL/policies/` | 个人覆盖，不入库 |

```bash
python Server/ual_cli/ual_cli.py lint-policy --project <ProjectRoot>
python Server/ual_cli/ual_cli.py lint-policy --project <ProjectRoot> --strict --fail-on warning
python Server/ual_cli/ual_cli.py allowlist-audit
```

| 参数 | 作用 |
| --- | --- |
| `--project` | 指定 UE 工程根，启用 project / user 两层；省略时从 cwd 向上找 `*.uproject` |
| `--namespace` | 偏好命名空间，默认 `policies` |
| `--limit` | TOON 报告最多输出多少行违规（1–500），全量结果仍由 `validate_many()` 计算 |
| `--fail-on` | 可阻断的最低严重度：`info` < `warning` < `error` |
| `--strict` | 放宽 `enforce` 要求：仅按严重度阻断 |

### 退出码

| 情况 | 退出码 |
| --- | --- |
| 无违规，或只有报告型违规且未启用 `--strict` | 0 |
| `enforce: true` 且严重度达到 `--fail-on`，或 `--strict` 下达到阈值 | **1** |
| 索引缺失 | 2 |

stdout 始终只有 TOON 报告，便于管道消费；`PASS` / `BLOCKED` 状态写 stderr。`ual-cli self-test`
会在隔离的临时工程根上跑两遍：默认模式只报告（0），`--strict --fail-on warning` 命中内置命名规则（1），
因此退出码语义与四级偏好一起被离线自检覆盖。

> **custom rules：已实现**。`lint-policy` 现在通过 `Server/ual_common/extensions.py` 装载
> `Config/UAL/extensions/*.yaml` 声明式扩展清单，`kind: custom` 规则经 `ExtensionRegistry` 的 `policy`
> namespace 解析到 `ual_common.extensions_builtins` 固定 callable；YAML 不 import 任意模块、不执行任意代码。
> `Server/tests/test_policy.py::test_custom_rule_goes_through_extension_registry` 覆盖分派路径。
> 详见 [扩展体系](extensions.md) §3 与 [已知限制](known-limitations.md) §15。

## 4. 离线 CI / 本地一键检查

### GitHub Actions（片段）

```yaml
name: ual-python-ci
on: [push, pull_request]
jobs:
  checks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          python -m pip install -r requirements.txt
          python -m pip install -r requirements-docs.txt
      - name: Generate / tool drift gates
        run: |
          python Tools/gen_docs.py --check
          python Tools/gen_taxonomy.py --check
          python Tools/audit_tool_annotations.py --check
          python Tools/audit_engine_toolsets.py --check
      - name: Strict docs build
        run: python -m mkdocs build --strict
      - name: Python contract tests and self-tests
        run: |
          python -m pytest Server/tests -q
          python Server/ual_cli/ual_cli.py self-test
          python Server/ual_mcp/server.py --self-test
          python Server/ual_mcp/engine_bridge.py --self-test
      - name: Offline benchmark smoke
        run: python Tools/bench_index.py --count 10000 --incremental
```

完整工作流见 `.github/workflows/python-ci.yml`；文档发布工作流 `.github/workflows/docs.yml` 只生成 / 构建 / 发布站点，
**不添加 C++ 代码检查 job**。两条工作流都不使用 secrets、不接外部生成式服务、不自动 `git push`。
`Config/UAL/gateway/integrations.yaml` 中的 P1 / P2 外部服务保持 `disabled` / license-reviewed / Zero-VLM boundary，
**not executed**，不得写成已启用集成。本地 UE6 UBT Build 已通过（`Result: Succeeded`，见 [已知限制](known-limitations.md) §1），
但 **CI 仍不编译 C++**，真实 UE 写入 / 编辑器编译 / PIE / UE 自动化用例仍**规格 / 未落地**。

> 有 UE SDK 的自托管 runner 可用 `build --project <ProjectRoot> --engine-cmd UnrealEditor.exe` 走真实 Commandlet；
> `ual-cli` 内部已使用模块限定名 `-run=UEAssetLensEditor.UALIndexBuilder`（裸 `-run=UALIndexBuilder` 在 UE6 会找不到类）。
> 本仓库是 `Ann/Plugins/UEAssetLens` 下的独立 git 仓库，路径需按 CI 检出布局调整；不要在父仓库 `Ann` 中 `git add` 本目录。

### PowerShell 一键

`pwsh Tools/run_tests.ps1` 包含上述门禁、`setup_mcp.ps1 -DryRun -Transport Http` 与 10000 资产基准；
`-SkipDocs` 只跳过 MkDocs 依赖安装、参考页生成与严格站点构建，三个生成头 / annotations / engine toolset 审计、pytest、
self-tests、benchmark、HTTP DryRun 仍执行。步骤编号固定为 1/13 … 13/13。

### pre-commit（片段）

```yaml
repos:
  - repo: local
    hooks:
      - id: ual-lint-duplicates
        name: UAL duplicate gate
        entry: python Server/ual_cli/ual_cli.py lint-duplicates --threshold 0.85 --diff-only
        language: system
        pass_filenames: false
      - id: ual-verify-references
        name: UAL reference gate
        entry: python Server/ual_cli/ual_cli.py verify-references --scan-dirs Content/Abilities,Content/Script
        language: system
        pass_filenames: false
      - id: ual-lint-policy
        name: UAL declarative policy gate
        entry: python Server/ual_cli/ual_cli.py lint-policy --project . --strict --fail-on error
        language: system
        pass_filenames: false
```

## 5. 误报处理与 allowlist（已实现）

常见误报：同网格的合法材质 / LOD 变体、跨骨架 retarget、运行期动态参数、阈值边界抖动、指纹方向余弦主导导致的假阳性（见 [架构说明](architecture.md) §3）。

处理流程：

1. 对 `lint-duplicates` 给出的每一对用 `UEAssetLens.AssetSemanticToolset.InspectAsset` 精读 YAML，判断真冗余还是合法变体；
2. 真冗余 → 合并 / 删除资产、修正引用、重跑门禁；
3. 确属误报 → 记录白名单，注明理由、责任人、复查日期，并经过 code review；
4. 禁止用白名单掩盖真实重复。

**白名单机制：已实现**。`Config/UAL/allowlists/default.yaml` 与 `Server/ual_common/allowlist.py` 提供版本化 allowlist，
`allowlist-audit` 输出有界 `allowlist_issues` TOON 报告。示例条目结构：

```yaml
version: 1
duplicates:
  - pair: [/Game/Props/SM_Chair_A, /Game/Props/SM_Chair_A_Painted]
    reason: "同一网格的合法材质变体"
    owner: art-team
    review_by: 2026-12-31
    approved_by: reviewer-alias
    review_ref: PR-123
references:
  - path: /Game/UI/DebugOnly/WBP_Probe
    rule: missing_asset
    reason: "仅编辑器调试用，运行期按需生成"
    owner: tools-team
    review_by: 2026-12-31
    approved_by: reviewer-alias
    review_ref: PR-123
```

约定：**已实现** —— `pair` 按无序对精确匹配；`rule` / `path` 必须精确且禁止通配符；缺
`reason` / `owner` / `review_by` 或调用侧缺 `approved_by` / `review_ref` 的条目视为无效并告警；
过期条目不豁免门禁；白名单只豁免报告，不修改指纹、索引或 Core Record；报告不输出本地路径、时间戳、随机 ID 或 reviewer。
接入 GitHub code review 自动化仍为**规格 / 未落地**；当前由人工 review 记录满足审批引用。
