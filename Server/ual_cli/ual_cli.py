#!/usr/bin/env python3
"""ual-cli — UEAssetLens headless CLI and CI gate.

Commands:
  build             Seed/update .ual/index.duckdb from UE Commandlet YAML doc-stream.
  lint-duplicates   Cosine self-join duplicate gate (non-zero exit blocks the commit).
  lint-policy       Declarative naming/directory/format/reference gate (four-layer UAL prefs).
  verify-references Validate asset paths / skeletons / material params in configs & scripts.
  export-parquet    COPY ... TO Parquet for cross-project catalog sharing.
  self-test         Offline end-to-end check of build, all gates and export.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import duckdb

SERVER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parents[0]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from ual_common import db, policy_gate  # noqa: E402
from ual_common.toon import Table, render_toon  # noqa: E402

SEED = SERVER_ROOT / "tests" / "seed.yaml"
ASSET_PATH_RE = re.compile(r"/Game/[A-Za-z0-9_/\-.]+")
USER_PARAM_RE = re.compile(r"User\.([A-Za-z_]\w*)")
SETSCALAR_RE = re.compile(r"(?:SetScalar|SetVector)ParameterValue\(\s*['\"]([A-Za-z_]\w*)['\"]")
SKELETON_RE = re.compile(r"(?im)^\s*skeleton\s*:\s*(/Game/[A-Za-z0-9_/\-.]+)\s*$")
SCAN_SUFFIXES = (".yaml", ".yml", ".luau", ".lua")


def _open(index: str | None) -> duckdb.DuckDBPyConnection:
    try:
        return db.open_readonly(index)
    except FileNotFoundError as exc:
        print(f"ual-cli: {exc}", file=sys.stderr)
        raise SystemExit(2)


# --------------------------------------------------------------------------- build

def _run_ue_commandlet(args: argparse.Namespace) -> list[Path]:
    engine = args.engine_cmd
    if not engine:
        raise SystemExit(
            "ual-cli: no --engine-cmd given and UE_EDITOR_CMD is unset.\n"
            "Either pass --from-yaml / --demo, or provide UnrealEditor.exe, e.g.\n"
            "  ual-cli build --project Ann --engine-cmd C:/UE/Engine/Binaries/Win64/UnrealEditor.exe"
        )
    project = Path(args.project or ".").resolve()
    uproject = project / (project.name + ".uproject")
    staging = project / ".ual" / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    # UE6 resolves -run= before PostEngineInit loads Editor modules. The
    # Module.Class form makes LaunchEngineLoop load UEAssetLensEditor first
    # (LaunchEngineLoop.cpp:4220-4225); a bare class name fails with
    # "could not find the class UALIndexBuilderCommandlet".
    cmd = [
        engine, str(uproject),
        "-nullrhi", "-Unattended", "-NoSplash", "-NoSound",
        "-stdout", "-FullStdOutLogOutput",
        "-run=UEAssetLensEditor.UALIndexBuilder",
        "-Out=index",
    ]
    if args.incremental:
        cmd.append("-Incremental")
    print(f"ual-cli: {' '.join(cmd)}")
    completed = subprocess.run(cmd, cwd=str(project), capture_output=True, text=True)
    sources = sorted(staging.glob("*.yaml"))
    # UE6 commandlets can report "finished execution (result 0)" and still make
    # the engine process exit non-zero (shutdown-time load errors). The staging
    # document stream is the real contract, so gate on the produced files.
    if not sources:
        detail = (completed.stderr or completed.stdout or "")[-2000:]
        raise SystemExit(
            f"ual-cli: Commandlet produced no staging YAML (exit={completed.returncode}).\n"
            f"{detail}"
        )
    if completed.returncode != 0:
        print(
            f"ual-cli: warning: commandlet exit={completed.returncode} but "
            f"{len(sources)} staging YAML file(s) produced; continuing.",
            file=sys.stderr,
        )
    return sources


def cmd_build(args: argparse.Namespace) -> int:
    if args.demo:
        sources = [SEED]
    elif args.from_yaml:
        sources = [Path(args.from_yaml)]
    else:
        sources = _run_ue_commandlet(args)
    if not sources:
        raise SystemExit("ual-cli: no YAML staging files produced by the Commandlet")

    existing: dict[str, str] = {}
    if args.incremental and db.resolve_index(args.index).exists():
        probe = db.open_readonly(args.index)
        existing = dict(probe.execute("SELECT asset_path, content_hash FROM assets").fetchall())
        probe.close()

    con = db.open_write(args.index)
    total = {"assets": 0, "edges": 0}
    skipped = 0
    for source in sources:
        records = db.load_yaml_docs(source)
        if args.incremental and existing:
            before = len(records)
            records = [r for r in records if existing.get(r.get("asset_path")) != r.get("content_hash")]
            skipped += before - len(records)
        stats = db.ingest_records(con, records, rebuild=False)
        total["assets"] += stats["assets"]
        total["edges"] += stats["edges"]
    db.rebuild_fts(con)
    count = con.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    con.close()
    fields = ["index", "assets_written", "edges_written", "skipped_unchanged", "assets_total", "fts_rebuilt"]
    row = [[str(db.resolve_index(args.index)), total["assets"], total["edges"],
            skipped, count, "true"]]
    print(render_toon([Table("build_result", fields, row)]), end="")
    return 0


# --------------------------------------------------------------------------- lint-duplicates

def _git_added_paths() -> list[str]:
    paths: list[str] = []
    for command in (
        ["git", "diff", "--name-only", "--diff-filter=A", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=15)
            if result.returncode == 0:
                paths += result.stdout.splitlines()
        except (OSError, subprocess.SubprocessError):
            continue
    out = []
    for raw in paths:
        text = raw.strip().replace("\\", "/")
        if "/Content/" in text or text.startswith("Content/"):
            rel = text.split("/Content/", 1)[-1] if "/Content/" in text else text[len("Content/"):]
            rel = re.sub(r"\.(uasset|umap)$", "", rel)
            out.append("/Game/" + rel.lstrip("/"))
    return sorted(set(out))


def _audit_yaml(pairs: list[dict[str, Any]], threshold: float) -> str:
    lines = [
        "# UEAssetLens Duplicate Audit (Auto-generated, Zero-VLM)",
        f"threshold: {threshold}",
        f"duplicate_pairs: {len(pairs)}",
        "pairs:",
    ]
    for pair in pairs:
        lines.append(f"  - lhs: {pair['lhs']} # {pair['lhs_id']}")
        lines.append(f"    rhs: {pair['rhs']} # {pair['rhs_id']}")
        lines.append(f"    similarity: {float(pair['similarity']):.4f}")
        lines.append("    verdict: deterministic_fingerprint_match")
    return "\n".join(lines) + "\n"


def cmd_lint_duplicates(args: argparse.Namespace) -> int:
    con = _open(args.index)
    scope = _git_added_paths() if args.diff_only else None
    if args.diff_only and not scope:
        print("ual-cli: --diff-only found no newly added Content assets; nothing to check")
        con.close()
        return 0
    pairs = db.duplicate_pairs(con, args.threshold, scope)
    con.close()
    if pairs:
        print(_audit_yaml(pairs, args.threshold), end="")
        print(f"ual-cli: BLOCKED - {len(pairs)} duplicate pair(s) >= {args.threshold}", file=sys.stderr)
        return 1
    print(f"ual-cli: PASS - no duplicate pair >= {args.threshold}")
    return 0


# --------------------------------------------------------------------------- lint-policy

def cmd_lint_policy(args: argparse.Namespace) -> int:
    con = _open(args.index)
    try:
        report, violations, resolved = policy_gate.run_policy_gate(
            con,
            project_root=Path(args.project).resolve() if args.project else None,
            namespace=args.namespace,
            limit=args.limit,
        )
    finally:
        con.close()
    print(report, end="")
    rc = policy_gate.decide_exit_code(
        violations, fail_on=args.fail_on, strict=args.strict
    )
    if rc:
        print(
            f"ual-cli: BLOCKED - {len(violations)} policy finding(s) "
            f"(fail_on={args.fail_on}, strict={str(args.strict).lower()})",
            file=sys.stderr,
        )
    else:
        print(
            f"ual-cli: PASS - {len(violations)} finding(s), "
            f"{len(resolved.active_rules())} active rule(s)",
            file=sys.stderr,
        )
    return rc


# --------------------------------------------------------------------------- verify-references

def _iter_scan_files(dirs: list[str], root: Path):
    for entry in dirs:
        base = Path(entry)
        path = base if base.is_absolute() else root / base
        if not path.exists():
            continue
        candidates = [path] if path.is_file() else path.rglob("*")
        for file in candidates:
            if file.is_file() and file.suffix.lower() in SCAN_SUFFIXES:
                yield file


def cmd_verify_references(args: argparse.Namespace) -> int:
    con = _open(args.index)
    known = {row["asset_path"] for row in db.all_assets(con)}
    montage_skeleton = dict(con.execute(
        "SELECT asset_path, skeleton_path FROM assets WHERE asset_class LIKE '%Anim%' AND skeleton_path IS NOT NULL"
    ).fetchall())
    exposed: set[str] = set()
    for (raw,) in con.execute(
        "SELECT domain_spec['exposed_params'] FROM assets WHERE domain_spec['exposed_params'] IS NOT NULL"
    ).fetchall():
        exposed.update(p.strip() for p in str(raw).split(",") if p.strip())
    con.close()

    root = Path(args.project or ".").resolve()
    issues: list[dict[str, str]] = []
    scanned = 0
    for file in _iter_scan_files(args.scan_dirs.split(","), root):
        scanned += 1
        text = file.read_text(encoding="utf-8", errors="ignore")
        try:
            rel = str(file.relative_to(root)).replace("\\", "/")
        except ValueError:
            import os
            rel = os.path.relpath(str(file), str(root)).replace("\\", "/")
        for match in ASSET_PATH_RE.finditer(text):
            path = match.group(0).rstrip(".,;\"'")
            if path not in known:
                issues.append({"file": rel, "kind": "missing_asset", "value": path})
        declared = SKELETON_RE.findall(text)
        if declared:
            skeleton = declared[0]
            if skeleton not in known:
                issues.append({"file": rel, "kind": "missing_skeleton", "value": skeleton})
            for match in ASSET_PATH_RE.finditer(text):
                path = match.group(0).rstrip(".,;\"'")
                if path in montage_skeleton and montage_skeleton[path] != skeleton:
                    issues.append({
                        "file": rel, "kind": "skeleton_mismatch",
                        "value": f"{path} expects {montage_skeleton[path]}, declared {skeleton}",
                    })
        for param in set(USER_PARAM_RE.findall(text) + SETSCALAR_RE.findall(text)):
            if exposed and param not in exposed:
                issues.append({"file": rel, "kind": "unknown_material_param", "value": param})

    lines = [
        "# UEAssetLens Reference Audit (Auto-generated, Zero-VLM)",
        f"files_scanned: {scanned}",
        f"known_assets: {len(known)}",
        f"issues: {len(issues)}",
        "findings:",
    ]
    for item in issues:
        lines.append(f"  - {{ file: {item['file']}, kind: {item['kind']}, value: \"{item['value']}\" }}")
    print("\n".join(lines) + "\n", end="")
    if issues:
        print(f"ual-cli: BLOCKED - {len(issues)} invalid reference(s)", file=sys.stderr)
        return 1
    print("ual-cli: PASS - all references resolve")
    return 0


# --------------------------------------------------------------------------- export-parquet

def cmd_export_parquet(args: argparse.Namespace) -> int:
    con = _open(args.index)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        con.execute("COPY (SELECT * FROM v_search) TO ? (FORMAT PARQUET)", [str(out)])
    except Exception:
        escaped = str(out).replace("'", "''")
        con.execute(f"COPY (SELECT * FROM v_search) TO '{escaped}' (FORMAT PARQUET)")
    rows = con.execute("SELECT COUNT(*) FROM v_search").fetchone()[0]
    con.close()
    print(f"ual-cli: exported {rows} rows -> {out}")
    return 0


# --------------------------------------------------------------------------- self-test

def cmd_self_test(args: argparse.Namespace) -> int:
    tmp = Path(tempfile.gettempdir()) / "ual_cli_self_test.duckdb"
    tmp.unlink(missing_ok=True)
    build = argparse.Namespace(demo=True, from_yaml=None, index=str(tmp), incremental=False,
                               project=None, engine_cmd=None)
    rc = cmd_build(build)
    if rc != 0:
        return rc

    dup = cmd_lint_duplicates(argparse.Namespace(index=str(tmp), threshold=0.85, diff_only=False))
    parquet_out = Path(tempfile.gettempdir()) / "ual_cli_self_test.parquet"
    parquet_out.unlink(missing_ok=True)
    cmd_export_parquet(argparse.Namespace(index=str(tmp), out=str(parquet_out)))

    # Policy gate: an isolated project root keeps the four-layer resolver on
    # builtin rules only, so self-test never picks up a developer's user layer.
    policy_root = Path(tempfile.gettempdir()) / "ual_cli_policy_root"
    policy_root.mkdir(exist_ok=True)
    policy_base = dict(index=str(tmp), project=str(policy_root),
                       namespace="policies", limit=20)
    policy_report = cmd_lint_policy(argparse.Namespace(
        **policy_base, fail_on="error", strict=False))
    policy_strict = cmd_lint_policy(argparse.Namespace(
        **policy_base, fail_on="warning", strict=True))

    sandbox = Path(tempfile.gettempdir()) / "ual_cli_scan"
    sandbox.mkdir(exist_ok=True)
    (sandbox / "ability.yaml").write_text(
        "skeleton: /Game/Characters/Knight/SK_Knight\n"
        "montage: /Game/Characters/Knight/Anim/AM_Knight_HeavySlash_02\n"
        "vfx_param: User.SlashColor\n",
        encoding="utf-8",
    )
    (sandbox / "bad.yaml").write_text(
        "montage: /Game/Characters/Knight/Anim/AM_Hallucinated_Move\n", encoding="utf-8",
    )
    verify = cmd_verify_references(argparse.Namespace(
        index=str(tmp), scan_dirs=str(sandbox), project=str(Path(tempfile.gettempdir()))))
    checks = {
        "build": rc == 0,
        "lint_duplicates_blocks": dup == 1,
        "verify_references_blocks": verify == 1,
        "parquet_written": parquet_out.exists() and parquet_out.stat().st_size > 0,
        "lint_policy_reports_only": policy_report == 0,
        "lint_policy_strict_blocks": policy_strict == 1,
    }
    print("\n===== self-test =====")
    for key, passed in checks.items():
        print(f"  {key}: {'PASS' if passed else 'FAIL'}")
    ok = all(checks.values())
    print(f"ual-cli self-test: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


# --------------------------------------------------------------------------- entry

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ual-cli", description="UEAssetLens headless CLI")
    parser.add_argument("--index", default=None, help="Path to .ual/index.duckdb")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="Build/update the DuckDB index")
    p.add_argument("--project", default=None, help="UE project root")
    p.add_argument("--engine-cmd", default=None, help="Path to UnrealEditor-Cmd.exe")
    p.add_argument("--from-yaml", default=None, help="Ingest a Commandlet YAML document stream")
    p.add_argument("--demo", action="store_true", help="Ingest bundled demo seed (no UE required)")
    p.add_argument("--incremental", action="store_true", help="Skip unchanged content_hash")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("lint-duplicates", help="Cosine self-join duplicate gate")
    p.add_argument("--threshold", type=float, default=0.85)
    p.add_argument("--diff-only", action="store_true", help="Only assets added in this Git change")
    p.set_defaults(func=cmd_lint_duplicates)

    p = sub.add_parser("lint-policy", help="Declarative asset policy gate (four-layer UAL preferences)")
    p.add_argument("--project", default=None, help="UE project root for team/user preference layers")
    p.add_argument("--namespace", default="policies", help="UAL preference namespace to load")
    p.add_argument("--limit", type=int, default=100, help="Max violation rows in the TOON report")
    p.add_argument("--fail-on", default="error", choices=["info", "warning", "error"],
                   help="Minimum severity that can block")
    p.add_argument("--strict", action="store_true",
                   help="Block on severity alone; rules are report-only by default")
    p.set_defaults(func=cmd_lint_policy)

    p = sub.add_parser("verify-references", help="Validate asset references in configs/scripts")
    p.add_argument("--scan-dirs", default="Content/Abilities,Content/Script")
    p.add_argument("--project", default=None, help="Root for relative scan dirs")
    p.set_defaults(func=cmd_verify_references)

    p = sub.add_parser("export-parquet", help="Export catalog as Parquet")
    p.add_argument("--out", default=".ual/shared_catalog.parquet")
    p.set_defaults(func=cmd_export_parquet)

    p = sub.add_parser("self-test", help="Offline end-to-end check")
    p.set_defaults(func=cmd_self_test)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
