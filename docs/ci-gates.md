# CI 门禁（M4）

UAL 把两类资产质量问题变成 CI 中的强制约束：重复资产、断裂引用。两个门禁都是「发现问题即非零退出」。当前通过 `python Server/ual_cli/ual_cli.py <command>` 调用；仓库未提供 `ual-cli` console script 入口。

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

`--diff-only` 把参与自连接的集合限制为**本次 Git 变更新增的 `Content/` 资产**（`git diff --diff-filter=A HEAD` + 未跟踪文件，映射为 `/Game/...`），使 PR 场景只检查增量。它**不**包含 `content_hash` 变化但路径未新增的资产。

```bash
python Server/ual_cli/ual_cli.py lint-duplicates --threshold 0.85
python Server/ual_cli/ual_cli.py lint-duplicates --threshold 0.85 --diff-only
```

### 退出码

| 情况 | 行为 | 退出码 |
| --- | --- | --- |
| 存在任一 `similarity >= threshold` 的资产对 | stdout 打印 YAML 审计，stderr 打印 `BLOCKED` | **1** |
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

> 这是**静态启发式**校验，不是 UE 反射级语义解析。它的价值是拦截 AI 编造的资产路径与参数名，不能替代编辑器内的引用完整性检查。

## 3. 接入示例

### GitHub Actions（片段）

```yaml
name: ual-ci-gates
on:
  pull_request:
    paths: ['Content/**', '**/*.yaml', '**/*.luau', 'Server/**']
jobs:
  gates:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install UAL dependencies
        run: python -m pip install -r requirements.txt
      - name: Build demo index (no UE SDK on CI)
        run: python Server/ual_cli/ual_cli.py build --demo
      - name: Python contract tests
        run: python -m pytest Server/tests -q
      - name: Gate 1 - duplicate detection
        run: python Server/ual_cli/ual_cli.py lint-duplicates --threshold 0.85
      - name: Gate 2 - reference integrity
        run: python Server/ual_cli/ual_cli.py verify-references --scan-dirs Content/Abilities,Content/Script
```

> 有 UE SDK 的自托管 runner 可用 `build --project <root> --engine-cmd <UnrealEditor-Cmd.exe>` 走真实 Commandlet。本仓库是 `Ann/Plugins/UEAssetLens` 下的独立 git 仓库，路径需按 CI 检出布局调整。

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
```

## 4. 误报处理与白名单约定

常见误报：同网格的合法材质/LOD 变体、跨骨架 retarget、运行期动态参数、阈值边界抖动、指纹方向余弦主导导致的假阳性（见 [架构说明](architecture.md) §3）。

处理流程：

1. 对 `lint-duplicates` 给出的每一对用 `UEAssetLens.AssetSemanticToolset.InspectAsset` 精读 YAML，判断真冗余还是合法变体；
2. 真冗余 → 合并/删除资产、修正引用、重跑门禁；
3. 确属误报 → 记录白名单，注明理由、责任人、复查日期，并经过 code review；
4. 禁止用白名单掩盖真实重复。

**白名单机制目前是约定，尚未实现**（当前里程碑只做到「报告 + 非零退出」）。落地时建议路径使用版本化的 `config/ual_allowlist.yaml`——注意 `.ual/` 已在 `.gitignore` 中被忽略，不应把白名单放进去。建议条目结构：

```yaml
version: 1
duplicates:
  - pair: [/Game/Props/SM_Chair_A, /Game/Props/SM_Chair_A_Painted]
    reason: "同一网格的合法材质变体"
    owner: art-team
    review_by: 2026-12-31
references:
  - path: /Game/UI/DebugOnly/WBP_Probe
    rule: missing_asset
    reason: "仅编辑器调试用，运行期按需生成"
    owner: tools-team
    review_by: 2026-12-31
```

约定：`pair` 按无序对匹配；`rule` 限定生效的校验类别，避免一条白名单屏蔽所有规则；缺 `reason` / `owner` / `review_by` 的条目视为无效并告警；白名单只豁免报告，不修改指纹或索引数据。
