#!/usr/bin/env python3
"""ual-cli — UEAssetLens headless CLI, CI gate and offline kernel writer.

The MCP surface stays read-only. This CLI owns DuckDB writes (build and
apply-events), but never writes a UObject or calls a real UE editor executor.
Every stdout report is bounded TOON or canonical YAML emitted by ual_common.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import duckdb
import yaml

SERVER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parents[0]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from ual_common import allowlist, artifacts, db, fields, lifecycle, policy_gate, revision  # noqa: E402
from ual_common.toon import HEADER as TOON_HEADER, Table, parse_toon, render_toon  # noqa: E402
from ual_mcp import tools  # noqa: E402

SEED = SERVER_ROOT / "tests" / "seed.yaml"
BENCH_SCRIPT = REPO_ROOT / "Tools" / "bench_index.py"
DEFAULT_EVENTS = ".ual/staging/events.yaml"
DEFAULT_REMOVED = ".ual/staging/removed.yaml"
ASSET_PATH_RE = re.compile(r"/Game/[A-Za-z0-9_/\-.]+")
USER_PARAM_RE = re.compile(r"User\.([A-Za-z_]\w*)")
SETSCALAR_RE = re.compile(r"(?:SetScalar|SetVector)ParameterValue\(\s*['\"]([A-Za-z_]\w*)['\"]")
SKELETON_RE = re.compile(r"(?im)^\s*skeleton\s*:\s*(/Game/[A-Za-z0-9_/\-.]+)\s*$")
SCAN_SUFFIXES = (".yaml", ".yml", ".luau", ".lua")
MAX_AUDIT_ROWS = 100
MAX_BENCH_COUNT = 100000
STAGING_KEYS = frozenset(set(db.ASSET_COLUMNS) | {"edges"})
REMOVED_KEYS = frozenset({"asset_path", "change_kind"})
LOCAL_PATH_RE = re.compile(r"[A-Za-z]:[\\/]|(?<![\w/])/(?:home|Users|tmp|var|mnt|private)/")
TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*|\b\d{2}:\d{2}:\d{2}\b")
RANDOM_ID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b|\b[0-9a-fA-F]{32}\b"
)
RUNTIME_TOOL_NAMES: dict[str, str] = {
    str(item["handler"]): str(item["mcp_name"])
    for item in tools.RUNTIME_TOOL_DEFS
}


def _open(index: str | None) -> duckdb.DuckDBPyConnection:
    try:
        return db.open_readonly(index)
    except FileNotFoundError:
        print("ual-cli: UAL index not found; run `ual-cli build --demo` first.", file=sys.stderr)
        raise SystemExit(2)


def _bounded_limit(value: Any, default: int, maximum: int) -> int:
    try:
        size = int(value or default)
    except (TypeError, ValueError):
        size = default
    return max(1, min(size, maximum))


def _repo_relative_label(value: str | Path, root: Path | None = None) -> str:
    """Return a stable repository-relative label; never emit a local root."""
    base = Path(root).resolve() if root else REPO_ROOT.resolve()
    raw = Path(str(value))
    try:
        candidate = raw.resolve() if raw.is_absolute() else (base / raw).resolve()
        relative = candidate.relative_to(base)
        return artifacts.safe_relative(str(relative).replace("\\", "/"))
    except (ValueError, OSError):
        name = raw.name or "artifact"
        try:
            return artifacts.safe_relative(name)
        except ValueError:
            return "artifact"


@contextlib.contextmanager
def _runtime_artifact_root(args: argparse.Namespace):
    previous = tools.RUNTIME_ARTIFACT_ROOT
    project = getattr(args, "project", None)
    if project:
        tools.RUNTIME_ARTIFACT_ROOT = Path(project).resolve()
    try:
        yield
    finally:
        tools.RUNTIME_ARTIFACT_ROOT = previous


def _runtime_error(code: str, detail: str) -> int:
    print(render_toon(
        [Table("runtime_error", ["code", "detail"], [[code, detail]])],
        summary={"status": "error"},
    ), end="")
    return 2


def _runtime_tool(args: argparse.Namespace, handler: str, arguments: dict[str, Any]) -> int:
    name = RUNTIME_TOOL_NAMES.get(handler)
    if not name:
        return _runtime_error("unknown_runtime_tool", "qualified tool is unavailable")
    con = _open(args.index)
    try:
        try:
            with _runtime_artifact_root(args):
                report = str(tools.dispatch_runtime_tool(con, name, arguments))
        except Exception as exc:
            return _runtime_error(type(exc).__name__, "runtime dispatch failed")
    finally:
        con.close()
    if not report.startswith(TOON_HEADER):
        return _runtime_error("invalid_runtime_report", "bounded TOON expected")
    print(report, end="" if report.endswith("\n") else "\n")
    return 0


# --------------------------------------------------------------------------- build

def _run_ue_commandlet(args: argparse.Namespace) -> list[Path]:
    engine = args.engine_cmd
    if not engine:
        raise SystemExit(
            "ual-cli: no --engine-cmd given and UE_EDITOR_CMD is unset.\n"
            "Either pass --from-yaml / --demo, or provide UnrealEditor.exe."
        )
    project = Path(args.project or ".").resolve()
    uproject = project / (project.name + ".uproject")
    staging = project / ".ual" / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    cmd = [
        engine, str(uproject),
        "-nullrhi", "-Unattended", "-NoSplash", "-NoSound",
        "-stdout", "-FullStdOutLogOutput",
        "-run=UEAssetLensEditor.UALIndexBuilder",
        "-Out=index",
    ]
    if args.incremental:
        cmd.append("-Incremental")
    print("ual-cli: launching UE commandlet", file=sys.stderr)
    completed = subprocess.run(cmd, cwd=str(project), capture_output=True, text=True)
    sources = sorted(staging.glob("*.yaml"))
    if not sources:
        detail = artifacts.canonicalize_text((completed.stderr or completed.stdout or "")[-2000:])
        raise SystemExit(
            f"ual-cli: commandlet produced no staging YAML (exit={completed.returncode}).\n"
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
        raise SystemExit("ual-cli: no YAML staging files produced by the commandlet")

    existing: dict[str, str] = {}
    if args.incremental and db.resolve_index(args.index).exists():
        probe = db.open_readonly(args.index)
        try:
            existing = dict(probe.execute("SELECT asset_path, content_hash FROM assets").fetchall())
        finally:
            probe.close()

    con = db.open_write(args.index)
    total = {"assets": 0, "edges": 0}
    skipped = 0
    try:
        for source in sources:
            records = db.load_yaml_docs(source)
            if args.incremental and existing:
                before = len(records)
                records = [r for r in records if existing.get(r.get("asset_path")) != r.get("content_hash")]
                skipped += before - len(records)
            stats = db.ingest_records(con, records, rebuild=False)
            total["assets"] += stats["assets"]
            total["edges"] += stats["edges"]
        field_stats = fields.materialize(
            con, project_root=Path(args.project) if args.project else None
        )
        db.rebuild_fts(con)
        count = int(con.execute("SELECT COUNT(*) FROM assets").fetchone()[0])
        payload_count = int(con.execute("SELECT COUNT(*) FROM extension_payload").fetchone()[0])
    finally:
        con.close()
    report_fields = [
        "index", "assets_written", "edges_written", "skipped_unchanged",
        "assets_total", "extension_payload", "fts_rebuilt",
    ]
    row = [[
        _repo_relative_label(db.resolve_index(args.index)), total["assets"],
        total["edges"], skipped, count,
        max(payload_count, int(field_stats.get("extension_payload", 0))), "true",
    ]]
    print(render_toon([Table("build_result", report_fields, row)], summary={
        "status": "ok", "content_hash_use": "change_detection_only",
    }), end="")
    return 0


# --------------------------------------------------------------------------- allowlist gate

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


def _allowlist_root(args: argparse.Namespace) -> Path | None:
    raw = getattr(args, "allowlist_project", None) or getattr(args, "project", None)
    return Path(raw).resolve() if raw else None


def _load_gate_allowlist(args: argparse.Namespace):
    return allowlist.load_allowlist(
        project_root=_allowlist_root(args), as_of=getattr(args, "as_of", None)
    )


def _review_values(args: argparse.Namespace) -> tuple[str, str]:
    return (
        str(getattr(args, "approved_by", "") or ""),
        str(getattr(args, "review_ref", "") or ""),
    )


def _prime_review_gate(entry: allowlist.Allowlist, args: argparse.Namespace) -> None:
    approved_by, review_ref = _review_values(args)
    if not (approved_by and review_ref) and (entry.duplicates or entry.references or entry.issues()):
        entry.suppress_duplicate("", "", approved_by=approved_by, review_ref=review_ref)


def _allowlist_report(
    entry: allowlist.Allowlist, args: argparse.Namespace, *, always: bool = False
) -> tuple[int, bool]:
    issues = entry.issues()
    if issues or always:
        print(allowlist.render_report(
            issues, limit=_bounded_limit(getattr(args, "limit", 100), 100, allowlist.MAX_REPORT_LIMIT)
        ), end="")
    strict = bool(getattr(args, "strict_allowlist", False))
    return (1 if strict and issues else 0, bool(issues))


def _reference_suppressed(
    entry: allowlist.Allowlist, issue: dict[str, str], args: argparse.Namespace
) -> bool:
    approved_by, review_ref = _review_values(args)
    kind, value, path = issue["kind"], issue["value"], issue["file"]
    candidates = (
        (value, path), (path, value), (value, ""), (path, ""), (value, value),
    )
    for candidate_path, candidate_value in candidates:
        if entry.suppress_reference(
            kind, candidate_value, candidate_path,
            approved_by=approved_by, review_ref=review_ref,
        ):
            return True
    return False


# --------------------------------------------------------------------------- lint-duplicates

def _duplicate_audit(pairs: list[dict[str, Any]], threshold: float, limit: int) -> str:
    size = _bounded_limit(limit, MAX_AUDIT_ROWS, MAX_AUDIT_ROWS)
    shown = list(pairs)[:size]
    payload = {
        "kind": "duplicate_audit",
        "status": "blocked" if pairs else "pass",
        "threshold": float(threshold),
        "duplicate_pairs": len(pairs),
        "returned": len(shown),
        "truncated": len(pairs) > len(shown),
        "pairs": [{
            "lhs": str(item["lhs"]), "lhs_id": str(item["lhs_id"]),
            "rhs": str(item["rhs"]), "rhs_id": str(item["rhs_id"]),
            "similarity": round(float(item["similarity"]), 4),
            "verdict": "deterministic_fingerprint_match",
        } for item in shown],
    }
    return artifacts.canonical_yaml(payload)


def cmd_lint_duplicates(args: argparse.Namespace) -> int:
    con = _open(args.index)
    scope = _git_added_paths() if args.diff_only else None
    try:
        pairs = [] if (args.diff_only and not scope) else db.duplicate_pairs(con, args.threshold, scope)
    finally:
        con.close()

    gate = _load_gate_allowlist(args)
    _prime_review_gate(gate, args)
    approved_by, review_ref = _review_values(args)
    active = [
        pair for pair in pairs
        if not gate.suppress_duplicate(
            str(pair["lhs"]), str(pair["rhs"]),
            approved_by=approved_by, review_ref=review_ref,
        )
    ]
    issues_rc, _ = _allowlist_report(gate, args)
    print(_duplicate_audit(active, args.threshold, getattr(args, "limit", MAX_AUDIT_ROWS)), end="")
    if active:
        print(f"ual-cli: BLOCKED - {len(active)} duplicate pair(s) >= {args.threshold}", file=sys.stderr)
        return 1
    if args.diff_only and not scope:
        print("ual-cli: PASS - --diff-only found no newly added Content assets", file=sys.stderr)
    else:
        print(f"ual-cli: PASS - no unsuppressed duplicate pair >= {args.threshold}", file=sys.stderr)
    return issues_rc


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


def _reference_audit(
    issues: list[dict[str, str]], scanned: int, known: int, limit: int
) -> str:
    size = _bounded_limit(limit, MAX_AUDIT_ROWS, MAX_AUDIT_ROWS)
    shown = list(issues)[:size]
    payload = {
        "kind": "reference_audit",
        "status": "blocked" if issues else "pass",
        "files_scanned": scanned,
        "known_assets": known,
        "issues": len(issues),
        "returned": len(shown),
        "truncated": len(issues) > len(shown),
        "findings": shown,
    }
    return artifacts.canonical_yaml(payload)


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
        rel = _repo_relative_label(file, root)
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

    gate = _load_gate_allowlist(args)
    _prime_review_gate(gate, args)
    active = [item for item in issues if not _reference_suppressed(gate, item, args)]
    active.sort(key=lambda item: (item["file"], item["kind"], item["value"]))
    issues_rc, _ = _allowlist_report(gate, args)
    print(_reference_audit(active, scanned, len(known), getattr(args, "limit", MAX_AUDIT_ROWS)), end="")
    if active:
        print(f"ual-cli: BLOCKED - {len(active)} invalid reference(s)", file=sys.stderr)
        return 1
    print("ual-cli: PASS - all unsuppressed references resolve", file=sys.stderr)
    return issues_rc


# --------------------------------------------------------------------------- export-parquet

def cmd_export_parquet(args: argparse.Namespace) -> int:
    con = _open(args.index)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    con.execute("COPY (SELECT * FROM v_search) TO ? (FORMAT PARQUET)", [str(out)])
    rows = int(con.execute("SELECT COUNT(*) FROM v_search").fetchone()[0])
    con.close()
    label = _repo_relative_label(out, Path(args.project).resolve() if getattr(args, "project", None) else REPO_ROOT)
    print(render_toon(
        [Table("export_result", ["artifact", "format", "rows"], [[label, "parquet", rows]])],
        summary={"status": "ok"},
    ), end="")
    return 0


# --------------------------------------------------------------------------- runtime tool commands

def cmd_list_extensions(args: argparse.Namespace) -> int:
    return _runtime_tool(args, "ual_list_extensions", {
        "limit": _bounded_limit(getattr(args, "limit", 100), 100, 500),
    })


def cmd_list_extension_fields(args: argparse.Namespace) -> int:
    return _runtime_tool(args, "ual_list_extension_fields", {
        "limit": _bounded_limit(getattr(args, "limit", 50), 50, 200),
    })


def cmd_search_extension_fields(args: argparse.Namespace) -> int:
    return _runtime_tool(args, "ual_search_extension_fields", {
        "namespace": getattr(args, "namespace", "") or "",
        "field_name": getattr(args, "field_name", "") or "",
        "value_contains": getattr(args, "value_contains", "") or "",
        "limit": _bounded_limit(getattr(args, "limit", 20), 20, 100),
    })


def cmd_get_assembly_graph(args: argparse.Namespace) -> int:
    return _runtime_tool(args, "ual_get_assembly_graph", {
        "asset_path": getattr(args, "asset_path", "") or "",
        "limit": _bounded_limit(getattr(args, "limit", 20), 20, 100),
    })


def cmd_list_official_tools(args: argparse.Namespace) -> int:
    return _runtime_tool(args, "ual_list_official_tools", {
        "limit": _bounded_limit(getattr(args, "limit", 100), 100, 500),
    })


def cmd_get_index_revision(args: argparse.Namespace) -> int:
    return _runtime_tool(args, "ual_get_index_revision", {})


def cmd_get_lifecycle_events(args: argparse.Namespace) -> int:
    return _runtime_tool(args, "ual_get_lifecycle_events", {
        "limit": _bounded_limit(getattr(args, "limit", 50), 50, 200),
    })


def cmd_allowlist_audit(args: argparse.Namespace) -> int:
    gate = _load_gate_allowlist(args)
    _prime_review_gate(gate, args)
    rc, _ = _allowlist_report(gate, args, always=True)
    return rc


def cmd_diff_assets(args: argparse.Namespace) -> int:
    return _runtime_tool(args, "ual_diff_assets", {
        "lhs": getattr(args, "lhs", "") or "",
        "rhs": getattr(args, "rhs", "") or "",
        "layers": getattr(args, "layers", "") or "L0,L1,L2,L3,L4",
    })


def cmd_read_diff_layer(args: argparse.Namespace) -> int:
    return _runtime_tool(args, "ual_read_diff_layer", {
        "diff_id": getattr(args, "diff_id", "") or "",
        "layer": getattr(args, "layer", "") or "",
        "offset": max(0, int(getattr(args, "offset", 0) or 0)),
        "limit": _bounded_limit(getattr(args, "limit", 80), 80, 200),
    })


def cmd_verify_diff(args: argparse.Namespace) -> int:
    return _runtime_tool(args, "ual_verify_diff_determinism", {
        "diff_id": getattr(args, "diff_id", "") or "",
    })


def cmd_plan_edit(args: argparse.Namespace) -> int:
    operations = ""
    filename = getattr(args, "operations_file", None)
    if filename:
        try:
            operations = Path(filename).read_text(encoding="utf-8")
        except OSError:
            return _runtime_error("operations_file_unavailable", "cannot read operations YAML")
    return _runtime_tool(args, "ual_plan_edit", {
        "intent": getattr(args, "intent", "") or "",
        "candidates": getattr(args, "candidates", "") or "",
        "scope": getattr(args, "scope", "") or "/Game",
        "max_ops": _bounded_limit(getattr(args, "max_ops", 16), 16, 64),
        "operations": operations,
    })


def cmd_apply_plan(args: argparse.Namespace) -> int:
    return _runtime_tool(args, "ual_apply_plan", {
        "plan_id": getattr(args, "plan_id", "") or "",
        "approved_by": getattr(args, "approved_by", "") or "",
        "approvals": getattr(args, "approvals", "") or "",
        "dry_run": bool(getattr(args, "dry_run", True)),
    })


def cmd_verify_edit(args: argparse.Namespace) -> int:
    return _runtime_tool(args, "ual_verify_edit", {
        "plan_id": getattr(args, "plan_id", "") or "",
    })


def cmd_rollback_edit(args: argparse.Namespace) -> int:
    return _runtime_tool(args, "ual_rollback_edit", {
        "plan_id": getattr(args, "plan_id", "") or "",
        "approved_by": getattr(args, "approved_by", "") or "",
        "approvals": getattr(args, "approvals", "") or "",
        "dry_run": bool(getattr(args, "dry_run", True)),
    })


def cmd_expand_gamespec(args: argparse.Namespace) -> int:
    return _runtime_tool(args, "ual_expand_gamespec", {
        "intent": getattr(args, "intent", "") or "",
        "template": getattr(args, "template", "") or "fps_3c",
    })


def cmd_run_validation_loop(args: argparse.Namespace) -> int:
    return _runtime_tool(args, "ual_run_validation_loop", {
        "game_spec_id": getattr(args, "game_spec_id", "") or "",
        "runner": getattr(args, "runner", "") or "offline",
        "dry_run": bool(getattr(args, "dry_run", True)),
        "approved_by": getattr(args, "approved_by", "") or "",
        "approvals": getattr(args, "approvals", "") or "",
    })


def cmd_run_workflow(args: argparse.Namespace) -> int:
    return _runtime_tool(args, "ual_run_fps_3c_workflow", {
        "intent": getattr(args, "intent", "") or "",
        "dry_run": bool(getattr(args, "dry_run", True)),
        "approved_by": getattr(args, "approved_by", "") or "",
        "approvals": getattr(args, "approvals", "") or "",
    })


def cmd_request_rescan(args: argparse.Namespace) -> int:
    return _runtime_tool(args, "ual_request_rescan", {
        "asset_paths": getattr(args, "asset_paths", "") or "",
        "reason": getattr(args, "reason", "") or "index consistency",
        "dry_run": bool(getattr(args, "dry_run", True)),
        "approved_by": getattr(args, "approved_by", "") or "",
        "approvals": getattr(args, "approvals", "") or "",
    })


def cmd_read_artifact(args: argparse.Namespace) -> int:
    try:
        relative = artifacts.safe_relative(getattr(args, "relative", "") or "")
    except ValueError:
        return _runtime_error("invalid_artifact_relative", "repository-relative artifact path required")
    return _runtime_tool(args, "ual_read_artifact", {
        "relative": relative,
        "offset": max(0, int(getattr(args, "offset", 0) or 0)),
        "limit": _bounded_limit(getattr(args, "limit", 80), 80, 200),
    })


def cmd_import_gamespec(args: argparse.Namespace) -> int:
    payload_file = getattr(args, "payload_file", None)
    raw_payload = getattr(args, "payload", "") or ""
    if payload_file:
        try:
            raw_payload = Path(payload_file).read_text(encoding="utf-8")
        except OSError:
            return _runtime_error("payload_file_unavailable", "cannot read structured GameSpec payload")
    try:
        payload = yaml.safe_load(raw_payload) if str(raw_payload).strip() else {}
    except yaml.YAMLError:
        return _runtime_error("payload_yaml_invalid", "structured GameSpec payload is invalid YAML")
    if not isinstance(payload, dict):
        return _runtime_error("payload_shape_invalid", "structured GameSpec payload must be a mapping")
    return _runtime_tool(args, "ual_import_game_spec", {
        "payload": payload,
        "dry_run": bool(getattr(args, "dry_run", True)),
        "approved_by": getattr(args, "approved_by", "") or "",
        "approvals": getattr(args, "approvals", "") or "",
    })


# --------------------------------------------------------------------------- apply-events (CLI writer)

def _staging_path(args: argparse.Namespace, value: Any, default: str) -> Path:
    raw = str(value or default)
    path = Path(raw)
    if path.is_absolute():
        return path
    project = getattr(args, "project", None)
    base = Path(project).resolve() if project else Path.cwd()
    return base / path


def cmd_apply_events(args: argparse.Namespace) -> int:
    project_root = Path(args.project).resolve() if getattr(args, "project", None) else Path.cwd()
    events_path = _staging_path(args, getattr(args, "events", None), DEFAULT_EVENTS)
    removed_path = _staging_path(args, getattr(args, "removed", None), DEFAULT_REMOVED)
    event_docs = db.load_yaml_docs(events_path) if events_path.is_file() else []
    removed_docs = db.load_yaml_docs(removed_path) if removed_path.is_file() else []
    allowed = STAGING_KEYS
    for index, doc in enumerate(event_docs, 1):
        if set(doc) - allowed or not str(doc.get("asset_path", "")).startswith("/Game/"):
            return _runtime_error("invalid_event_document", f"events document {index} is invalid")
    removed_paths: list[str] = []
    for index, doc in enumerate(removed_docs, 1):
        if set(doc) - REMOVED_KEYS or not str(doc.get("asset_path", "")).startswith("/Game/"):
            return _runtime_error("invalid_removed_document", f"removed document {index} is invalid")
        removed_paths.append(str(doc["asset_path"]))

    con = db.open_write(args.index)
    try:
        ingest = db.ingest_records(con, event_docs, rebuild=False)
        removed = revision.remove_assets(con, removed_paths)
        db.rebuild_fts(con)
        field_stats = fields.materialize(con, project_root=project_root)
        assets_total = int(con.execute("SELECT COUNT(*) FROM assets").fetchone()[0])
        payload_total = int(con.execute("SELECT COUNT(*) FROM extension_payload").fetchone()[0])
    finally:
        con.close()

    event_label = _repo_relative_label(events_path, project_root)
    removed_label = _repo_relative_label(removed_path, project_root)
    audit_label = lifecycle.ARTIFACT_POINTER
    emitted = lifecycle.emit(
        "OnIndexed",
        {
            "events_artifact": event_label,
            "removed_artifact": removed_label,
            "events_ingested": ingest["assets"],
            "assets_removed": removed,
        },
        project_root=project_root,
        root=project_root / ".ual",
    )
    rows = [[
        event_label, removed_label, ingest["assets"], ingest["edges"], removed,
        max(payload_total, int(field_stats.get("extension_payload", 0))),
        assets_total, "true", emitted["event"], audit_label, emitted["artifact"],
    ]]
    print(render_toon(
        [Table("apply_events", [
            "events_artifact", "removed_artifact", "events_ingested", "edges_ingested",
            "assets_removed", "extension_payload", "assets_total", "fts_rebuilt",
            "lifecycle_event", "lifecycle_artifact", "staging_artifact",
        ], rows)],
        summary={"status": "ok", "writer": "ual-cli", "mcp_connection": "read_only"},
    ), end="")
    return 0


# --------------------------------------------------------------------------- benchmark

def cmd_benchmark(args: argparse.Namespace) -> int:
    count = _bounded_limit(getattr(args, "count", 10000), 10000, MAX_BENCH_COUNT)
    command = [sys.executable, str(BENCH_SCRIPT), "--count", str(count)]
    if bool(getattr(args, "incremental", False)):
        command.append("--incremental")
    completed = subprocess.run(command, cwd=str(REPO_ROOT), capture_output=True, text=True)
    if completed.returncode != 0 or not completed.stdout.startswith(TOON_HEADER):
        return _runtime_error("benchmark_failed", "bounded benchmark report unavailable")
    print(completed.stdout, end="")
    return 0


# --------------------------------------------------------------------------- self-test

def _draft_record() -> dict[str, Any]:
    return {
        "asset_path": "/Game/Temp/BP_Draft",
        "logical_id": "Blueprint:BP_Draft",
        "asset_class": "Blueprint",
        "parent_class": "UObject",
        "taxonomy": ["Asset", "Gameplay", "Blueprint"],
        "tokens": "temporary draft blueprint",
        "content_hash": "sha1:BP_Draft",
        "duration": 0.0,
        "bounds_size": None,
        "skeleton_path": None,
        "dominant_limb": None,
        "fingerprint_vec": None,
        "interface_spec": {},
        "domain_spec": {"feature.role": "unclassified"},
        "yaml_cache": "# UEAssetLens AssetSpec v2.0 (Auto-generated, Zero-VLM)\n",
        "edges": [],
    }


def _write_yaml(path: Path, payload: Any, *, stream: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = artifacts.canonical_yaml_stream(payload) if stream else artifacts.canonical_yaml(payload)
    path.write_text(text, encoding="utf-8", newline="\n")


def _capture_call(func: Any, args: argparse.Namespace) -> tuple[int, str]:
    buffer = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = int(func(args) or 0)
    except SystemExit as exc:
        code = int(exc.code or 0)
    except Exception:
        code = 2
    return code, buffer.getvalue()


def _forbidden_output(text: str) -> bool:
    return bool(
        LOCAL_PATH_RE.search(text)
        or TIMESTAMP_RE.search(text)
        or RANDOM_ID_RE.search(text)
        or "updated_at:" in text
    )


def cmd_self_test(args: argparse.Namespace) -> int:
    checks: dict[str, bool] = {}
    all_output: list[str] = []

    def run(func: Any, ns: argparse.Namespace) -> tuple[int, str]:
        code, text = _capture_call(func, ns)
        all_output.append(text)
        return code, text

    with tempfile.TemporaryDirectory(prefix="ual-cli-self-test-") as raw_tmp:
        tmp = Path(raw_tmp)
        project_root = tmp / "project"
        project_root.mkdir()
        index = tmp / "index.duckdb"
        runtime_root = tmp / "runtime"
        previous_runtime_root = tools.RUNTIME_ARTIFACT_ROOT
        tools.RUNTIME_ARTIFACT_ROOT = runtime_root
        try:
            build_args = argparse.Namespace(
                demo=True, from_yaml=None, index=str(index), incremental=False,
                project=str(project_root), engine_cmd=None,
            )
            build_rc, build_out = run(cmd_build, build_args)
            checks["build"] = build_rc == 0 and "extension_payload" in build_out

            con = db.open_readonly(index)
            try:
                typed_rows = int(con.execute(
                    "SELECT COUNT(*) FROM extension_payload "
                    "WHERE value_type IN ('string', 'integer', 'number', 'boolean', 'yaml') "
                    "AND namespace <> ''"
                ).fetchone()[0])
                fts_rows = len(db.search_assets(con, query="heavy slash", limit=1))
            finally:
                con.close()
            checks["typed_extension_payload_rows"] = typed_rows > 0 and fts_rows > 0

            gate_defaults = {
                "index": str(index), "project": str(project_root),
                "allowlist_project": None, "as_of": "2026-11-26",
                "approved_by": "self-test", "review_ref": "SELFFTEST-1",
                "strict_allowlist": False, "limit": 20,
            }
            dup_rc, _ = run(cmd_lint_duplicates, argparse.Namespace(
                **gate_defaults, threshold=0.85, diff_only=False))
            checks["lint_duplicates_blocks"] = dup_rc == 1

            parquet_out = tmp / "shared_catalog.parquet"
            parquet_rc, _ = run(cmd_export_parquet, argparse.Namespace(
                index=str(index), out=str(parquet_out), project=str(project_root)))
            checks["parquet_written"] = parquet_rc == 0 and parquet_out.exists() and parquet_out.stat().st_size > 0

            policy_root = tmp / "policy_root"
            policy_root.mkdir()
            policy_base = dict(index=str(index), project=str(policy_root),
                               namespace="policies", limit=20)
            policy_report_rc, _ = run(cmd_lint_policy, argparse.Namespace(
                **policy_base, fail_on="error", strict=False))
            policy_strict_rc, _ = run(cmd_lint_policy, argparse.Namespace(
                **policy_base, fail_on="warning", strict=True))
            checks["lint_policy_reports_only"] = policy_report_rc == 0
            checks["lint_policy_strict_blocks"] = policy_strict_rc == 1

            sandbox = tmp / "scan"
            sandbox.mkdir()
            (sandbox / "ability.yaml").write_text(
                "skeleton: /Game/Characters/Knight/SK_Knight\n"
                "montage: /Game/Characters/Knight/Anim/AM_Knight_HeavySlash_02\n"
                "vfx_param: User.SlashColor\n",
                encoding="utf-8",
            )
            (sandbox / "bad.yaml").write_text(
                "montage: /Game/Characters/Knight/Anim/AM_Hallucinated_Move\n",
                encoding="utf-8",
            )
            verify_args = dict(gate_defaults)
            verify_args.update({"scan_dirs": str(sandbox), "project": str(tmp)})
            verify_rc, _ = run(cmd_verify_references, argparse.Namespace(**verify_args))
            checks["verify_references_blocks"] = verify_rc == 1

            # Project override enables the builtin declarative custom predicate.
            custom_root = tmp / "custom_root"
            _write_yaml(custom_root / "Config" / "UAL" / "policies" / "team.yaml", {
                "rules": [{
                    "id": "custom.no_draft_names", "kind": "custom",
                    "applies_to": ["*"], "extension": "no_draft_names",
                    "severity": "error", "enforce": False, "enabled": True,
                    "message": "draft/WIP assets must be reviewed before publishing",
                }],
            })
            draft_stream = tmp / "draft-events.yaml"
            _write_yaml(draft_stream, [_draft_record()], stream=True)
            custom_build_rc, custom_build_out = run(cmd_build, argparse.Namespace(
                demo=False, from_yaml=str(draft_stream), index=str(index),
                incremental=False, project=str(custom_root), engine_cmd=None))
            custom_rc, custom_out = run(cmd_lint_policy, argparse.Namespace(
                index=str(index), project=str(custom_root), namespace="policies",
                limit=100, fail_on="error", strict=True))
            checks["custom_policy_rule"] = (
                custom_build_rc == 0 and custom_rc == 1
                and "custom.no_draft_names" in custom_out
                and "BP_Draft" in custom_out and "extension_payload" in custom_build_out
            )

            pair_lhs = "/Game/Characters/Knight/Anim/AM_Knight_HeavySlash_02"
            pair_rhs = "/Game/Characters/Knight/Anim/AM_Knight_HeavySlash_03"
            valid_root = tmp / "allowlist_valid"
            expired_root = tmp / "allowlist_bad"
            _write_yaml(valid_root / "Config" / "UAL" / "allowlists" / "team.yaml", {
                "version": 1,
                "duplicates": [{
                    "pair": [pair_lhs, pair_rhs], "reason": "reviewed variant",
                    "owner": "self-test", "review_by": "2999-12-31",
                }],
                "references": [],
            })
            _write_yaml(expired_root / "Config" / "UAL" / "allowlists" / "team.yaml", {
                "version": 1,
                "duplicates": [
                    {"pair": [pair_lhs, pair_rhs], "reason": "missing owner", "review_by": "2999-12-31"},
                    {"pair": [pair_lhs, pair_rhs], "reason": "expired", "owner": "self-test", "review_by": "2000-01-01"},
                ],
                "references": [],
            })
            audit_rc, audit_out = run(cmd_allowlist_audit, argparse.Namespace(
                index=str(index), project=str(expired_root), allowlist_project=str(expired_root),
                as_of="2026-11-26", approved_by="self-test", review_ref="SELFFTEST-1",
                strict_allowlist=True, limit=20))
            audit_rows = parse_toon(audit_out).get("tables", {}).get("allowlist_issues", [])
            bad_rc, _ = run(cmd_lint_duplicates, argparse.Namespace(
                index=str(index), project=str(project_root), allowlist_project=str(expired_root),
                as_of="2026-11-26", approved_by="self-test", review_ref="SELFFTEST-1",
                strict_allowlist=False, limit=20, threshold=0.95, diff_only=False))
            unreviewed_rc, unreviewed_out = run(cmd_lint_duplicates, argparse.Namespace(
                index=str(index), project=str(project_root), allowlist_project=str(valid_root),
                as_of="2026-11-26", approved_by="self-test", review_ref="",
                strict_allowlist=False, limit=20, threshold=0.95, diff_only=False))
            reviewed_rc, reviewed_out = run(cmd_lint_duplicates, argparse.Namespace(
                index=str(index), project=str(project_root), allowlist_project=str(valid_root),
                as_of="2026-11-26", approved_by="self-test", review_ref="SELFFTEST-1",
                strict_allowlist=False, limit=20, threshold=0.95, diff_only=False))
            kinds = {row.get("kind", "") for row in audit_rows}
            checks["allowlist_review_gate"] = (
                audit_rc == 1 and {"invalid", "expired"} <= kinds
                and bad_rc == 1 and unreviewed_rc == 1
                and "review" in unreviewed_out and reviewed_rc == 0
                and "duplicate_pairs: 0" in reviewed_out
            )

            # Every runtime tool dispatches through its qualified MCP name.
            from ual_mcp import engine_bridge

            runtime_ok = True
            fallback_relative = "plans/" + "0" * 64 + ".yaml"
            identifiers = {
                "diff_id": "0" * 64, "plan_id": "0" * 64,
                "game_spec_id": "0" * 64, "workflow_id": "0" * 64,
                "plan_relative": fallback_relative,
                "gamespec_relative": "gamespecs/" + "0" * 64 + ".yaml",
                "validation_relative": "validation/" + "0" * 64 + ".yaml",
                "workflow_relative": "workflow/" + "0" * 64 + ".yaml",
            }
            runtime_con = db.open_readonly(index)
            try:
                runtime_cases = engine_bridge.runtime_self_test_cases()
                runtime_ok &= len(runtime_cases) == len(tools.RUNTIME_TOOL_DEFS)
                for name, arguments in runtime_cases:
                    try:
                        text = tools.dispatch_runtime_tool(
                            runtime_con, name,
                            engine_bridge.resolve_case_arguments(arguments, identifiers),
                        )
                        runtime_ok &= text.startswith(TOON_HEADER)
                        engine_bridge.capture_case_identifiers(text, identifiers)
                        all_output.append(text)
                    except Exception:
                        runtime_ok = False
            finally:
                runtime_con.close()
            checks["all_runtime_tools_dispatch"] = runtime_ok

            hero = "/Game/Characters/Knight/Anim/AM_Knight_HeavySlash_02"
            diff_tool = RUNTIME_TOOL_NAMES["ual_diff_assets"]
            verify_diff_tool = RUNTIME_TOOL_NAMES["ual_verify_diff_determinism"]
            plan_tool = RUNTIME_TOOL_NAMES["ual_plan_edit"]
            apply_tool = RUNTIME_TOOL_NAMES["ual_apply_plan"]
            verify_edit_tool = RUNTIME_TOOL_NAMES["ual_verify_edit"]
            rollback_tool = RUNTIME_TOOL_NAMES["ual_rollback_edit"]
            expand_tool = RUNTIME_TOOL_NAMES["ual_expand_gamespec"]
            workflow_tool = RUNTIME_TOOL_NAMES["ual_run_fps_3c_workflow"]
            lifecycle_tool = RUNTIME_TOOL_NAMES["ual_get_lifecycle_events"]
            ro = db.open_readonly(index)
            try:
                diff_text = tools.dispatch_runtime_tool(
                    ro, diff_tool, {"lhs": hero, "rhs": hero, "layers": "L0,L1,L2,L3,L4"})
                parsed_diff = parse_toon(diff_text)
                diff_id = parsed_diff["summary"].get("diff_id", "")
                layer_ids = {row["layer"] for row in parsed_diff["tables"].get("layer_stats", [])}
                verify_text = tools.dispatch_runtime_tool(
                    ro, verify_diff_tool, {"diff_id": diff_id})
                deterministic = parse_toon(verify_text)["summary"].get("deterministic")
                plan_text = tools.dispatch_runtime_tool(ro, plan_tool, {
                    "intent": "tag", "candidates": hero, "scope": "/Game", "max_ops": 1})
                plan_id = parse_toon(plan_text)["summary"].get("plan_id", "")
                apply_text = tools.dispatch_runtime_tool(ro, apply_tool, {
                    "plan_id": plan_id, "approved_by": "self-test",
                    "approvals": "op-001", "dry_run": True})
                verify_edit_text = tools.dispatch_runtime_tool(
                    ro, verify_edit_tool, {"plan_id": plan_id})
                rollback_text = tools.dispatch_runtime_tool(ro, rollback_tool, {
                    "plan_id": plan_id, "approved_by": "self-test",
                    "approvals": "op-001.inverse", "dry_run": True})
                expansion_text = tools.dispatch_runtime_tool(ro, expand_tool, {
                    "intent": "fps 3c shooter", "template": "fps_3c"})
                game_spec_id = parse_toon(expansion_text)["summary"].get("game_spec_id", "")
                workflow_text = tools.dispatch_runtime_tool(ro, workflow_tool, {
                    "intent": "fps 3c shooter", "dry_run": True})
                all_output.extend([
                    diff_text, verify_text, plan_text, apply_text, verify_edit_text,
                    rollback_text, expansion_text, workflow_text,
                ])
                runtime_artifact_dir = runtime_root / tools.RUNTIME_ARTIFACT_SUBDIR
                plan_artifact = parse_toon(plan_text)["summary"].get("artifact", "")
                read_rc, read_out = run(cmd_read_artifact, argparse.Namespace(
                    index=str(index), project=None, relative=plan_artifact,
                    offset=0, limit=20))
                import_payload = engine_bridge._self_test_import_payload()
                import_file = tmp / "import-gamespec.yaml"
                _write_yaml(import_file, import_payload)
                import_rc, import_out = run(cmd_import_gamespec, argparse.Namespace(
                    index=str(index), project=None, payload="",
                    payload_file=str(import_file), dry_run=True,
                    approved_by="", approvals=""))
                all_output.extend([read_out, import_out])
                diff_files = [
                    runtime_artifact_dir / "diffs" / diff_id / f"{layer}.yaml"
                    for layer in ("L0", "L1", "L2", "L3", "L4")
                ]
                checks["five_layer_diff_and_determinism"] = (
                    layer_ids == {"L0", "L1", "L2", "L3", "L4"}
                    and all(path.is_file() for path in diff_files)
                    and deterministic == "true"
                )
                checks["transaction_dry_run_cycle"] = (
                    parse_toon(apply_text)["summary"].get("dry_run") == "true"
                    and parse_toon(rollback_text)["summary"].get("dry_run") == "true"
                    and plan_id == parse_toon(verify_edit_text)["summary"].get("plan_id", "")
                    and len(parse_toon(plan_text)["tables"].get("operations", [])) == 1
                )
                checks["gamespec_and_workflow"] = (
                    bool(game_spec_id)
                    and parse_toon(expansion_text)["summary"].get("status") == "expanded"
                    and len(parse_toon(workflow_text)["tables"].get("steps", [])) == 10
                    and (runtime_artifact_dir / "gamespecs" / f"{game_spec_id}.yaml").is_file()
                )
                checks["artifact_and_import_commands"] = (
                    read_rc == 0 and read_out.startswith(TOON_HEADER)
                    and "artifact_page" in parse_toon(read_out)["tables"]
                    and import_rc == 0 and import_out.startswith(TOON_HEADER)
                    and parse_toon(import_out)["summary"].get("status") == "imported"
                )
                lifecycle_text = tools.dispatch_runtime_tool(ro, lifecycle_tool, {"limit": 20})
                all_output.append(lifecycle_text)
                checks["lifecycle_events"] = (
                    (runtime_artifact_dir / "audit" / "events.yaml").is_file()
                    and len(parse_toon(lifecycle_text)["tables"].get("lifecycle_events", [])) >= 1
                )
            finally:
                ro.close()

            # Canonical YAML streams are the only apply-events input format.
            staging = project_root / ".ual" / "staging"
            staging.mkdir(parents=True, exist_ok=True)
            event_path = staging / "events.yaml"
            removed_path = staging / "removed.yaml"
            event_record = _draft_record()
            event_record["asset_path"] = "/Game/Events/BP_Indexed"
            event_record["logical_id"] = "Blueprint:BP_Indexed"
            event_record["content_hash"] = "sha1:BP_Indexed"
            _write_yaml(event_path, [event_record], stream=True)
            _write_yaml(removed_path, [{
                "asset_path": "/Game/Temp/BP_Draft", "change_kind": "removed",
            }], stream=True)
            apply_rc, apply_out = run(cmd_apply_events, argparse.Namespace(
                index=str(index), project=str(project_root),
                events=str(event_path), removed=str(removed_path)))
            check_con = db.open_readonly(index)
            try:
                paths = {row[0] for row in check_con.execute("SELECT asset_path FROM assets").fetchall()}
                payload_after = int(check_con.execute("SELECT COUNT(*) FROM extension_payload").fetchone()[0])
            finally:
                check_con.close()
            checks["apply_events_canonical_stream"] = (
                apply_rc == 0 and "/Game/Events/BP_Indexed" in paths
                and "/Game/Temp/BP_Draft" not in paths and payload_after > 0
                and ".ual/staging/events.yaml" in apply_out
                and ".ual/audit/events.yaml" in apply_out
                and "OnIndexed" in apply_out
            )

            benchmark_rc, benchmark_out = run(cmd_benchmark, argparse.Namespace(
                project=str(project_root), count=10000, incremental=True, dry_run=True))
            benchmark_summary = parse_toon(benchmark_out).get("summary", {})
            checks["benchmark_10000_incremental"] = (
                benchmark_rc == 0 and benchmark_out.startswith(TOON_HEADER)
                and benchmark_summary.get("assets") == "10000"
                and benchmark_summary.get("incremental_measured") == "true"
            )
            combined = "".join(all_output)
            checks["bounded_outputs_without_volatile_data"] = (
                not _forbidden_output(combined)
                and "SELECT * FROM v_search" not in combined
                and len(combined) < 400000
            )
        finally:
            tools.RUNTIME_ARTIFACT_ROOT = previous_runtime_root

    rows = [[name, "PASS" if passed else "FAIL"] for name, passed in checks.items()]
    ok = all(checks.values())
    print(render_toon(
        [Table("self_test", ["check", "status"], rows)],
        summary={"result": "PASS" if ok else "FAIL", "total": len(checks),
                 "passed": sum(1 for value in checks.values() if value)},
    ), end="")
    print(f"ual-cli self-test: {'PASS' if ok else 'FAIL'}", file=sys.stderr)
    return 0 if ok else 1


# --------------------------------------------------------------------------- entry

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ual-cli", description="UEAssetLens headless CLI")
    parser.add_argument("--index", default=None, help="Path to .ual/index.duckdb")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="Build/update the DuckDB index")
    p.add_argument("--project", default=None, help="UE project root")
    p.add_argument("--engine-cmd", default=None, help="Path to UnrealEditor.exe")
    p.add_argument("--from-yaml", default=None, help="Ingest a Commandlet YAML document stream")
    p.add_argument("--demo", action="store_true", help="Ingest bundled demo seed (no UE required)")
    p.add_argument("--incremental", action="store_true", help="Skip unchanged content_hash")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("lint-duplicates", help="Cosine self-join duplicate gate")
    p.add_argument("--project", default=None, help="UE project root for team/user preference layers")
    p.add_argument("--threshold", type=float, default=0.85)
    p.add_argument("--diff-only", action="store_true", help="Only assets added in this Git change")
    p.add_argument("--limit", type=int, default=100, help="Max audit rows and allowlist issues")
    p.add_argument("--allowlist-project", default=None, help="Project root for allowlist preference layers")
    p.add_argument("--as-of", default=None, help="ISO review date for allowlist expiry")
    p.add_argument("--approved-by", default="", help="Review-gated allowlist approver")
    p.add_argument("--review-ref", default="", help="Review-gated allowlist reference")
    p.add_argument("--strict-allowlist", action="store_true", help="Block on invalid, expired or unreviewed allowlist entries")
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
    p.add_argument("--project", default=None, help="Root for relative scan dirs")
    p.add_argument("--scan-dirs", default="Content/Abilities,Content/Script")
    p.add_argument("--limit", type=int, default=100, help="Max findings and allowlist issues")
    p.add_argument("--allowlist-project", default=None, help="Project root for allowlist preference layers")
    p.add_argument("--as-of", default=None, help="ISO review date for allowlist expiry")
    p.add_argument("--approved-by", default="", help="Review-gated allowlist approver")
    p.add_argument("--review-ref", default="", help="Review-gated allowlist reference")
    p.add_argument("--strict-allowlist", action="store_true", help="Block on invalid, expired or unreviewed allowlist entries")
    p.set_defaults(func=cmd_verify_references)

    p = sub.add_parser("export-parquet", help="Export catalog as Parquet")
    p.add_argument("--project", default=None, help="Root used to label the output artifact")
    p.add_argument("--out", default=".ual/shared_catalog.parquet")
    p.set_defaults(func=cmd_export_parquet)

    p = sub.add_parser("list-extensions", help="Bounded extension registry snapshot")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.add_argument("--limit", type=int, default=100, help="Max rows (1..500)")
    p.set_defaults(func=cmd_list_extensions)

    p = sub.add_parser("list-extension-fields", help="Typed extension field catalog")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.add_argument("--limit", type=int, default=50, help="Max rows (1..200)")
    p.set_defaults(func=cmd_list_extension_fields)

    p = sub.add_parser("search-extension-fields", help="Search typed extension_payload rows")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.add_argument("--namespace", default="", help="Extension namespace filter")
    p.add_argument("--field-name", default="", help="Field name filter")
    p.add_argument("--value-contains", default="", help="Case-insensitive value substring")
    p.add_argument("--limit", type=int, default=20, help="Max rows (1..100)")
    p.set_defaults(func=cmd_search_extension_fields)

    p = sub.add_parser("get-assembly-graph", help="Resolve declared asset roles and assembly relations")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.add_argument("--asset-path", default="", help="/Game asset path")
    p.add_argument("--limit", type=int, default=20, help="Max rows (1..100)")
    p.set_defaults(func=cmd_get_assembly_graph)

    p = sub.add_parser("list-official-tools", help="Bounded official tool whitelist")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.add_argument("--limit", type=int, default=100, help="Max rows (1..500)")
    p.set_defaults(func=cmd_list_official_tools)

    p = sub.add_parser("get-index-revision", help="Deterministic read-only index revision")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.set_defaults(func=cmd_get_index_revision)

    p = sub.add_parser("get-lifecycle-events", help="Bounded lifecycle audit events")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.add_argument("--limit", type=int, default=50, help="Max rows (1..200)")
    p.set_defaults(func=cmd_get_lifecycle_events)

    p = sub.add_parser("allowlist-audit", help="Bounded allowlist validation and review report")
    p.add_argument("--project", default=None, help="UE project root")
    p.add_argument("--allowlist-project", default=None, help="Override project root for allowlist layers")
    p.add_argument("--as-of", default=None, help="ISO review date for allowlist expiry")
    p.add_argument("--approved-by", default="", help="Review-gated allowlist approver")
    p.add_argument("--review-ref", default="", help="Review-gated allowlist reference")
    p.add_argument("--strict-allowlist", action="store_true", help="Return nonzero when allowlist issues exist")
    p.add_argument("--limit", type=int, default=100, help="Max issue rows (1..200)")
    p.set_defaults(func=cmd_allowlist_audit)

    p = sub.add_parser("diff-assets", help="Build five-layer canonical diff artifacts")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.add_argument("--lhs", default="", help="Left /Game asset path")
    p.add_argument("--rhs", default="", help="Right /Game asset path")
    p.add_argument("--layers", default="L0,L1,L2,L3,L4", help="Comma-separated layer ids")
    p.set_defaults(func=cmd_diff_assets)

    p = sub.add_parser("read-diff-layer", help="Page one canonical diff layer")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.add_argument("--diff-id", default="", help="Deterministic diff id")
    p.add_argument("--layer", default="", help="L0..L4")
    p.add_argument("--offset", type=int, default=0, help="Line offset")
    p.add_argument("--limit", type=int, default=80, help="Max lines (1..200)")
    p.set_defaults(func=cmd_read_diff_layer)

    p = sub.add_parser("verify-diff", help="Verify canonical diff determinism")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.add_argument("--diff-id", default="", help="Deterministic diff id")
    p.set_defaults(func=cmd_verify_diff)

    p = sub.add_parser("plan-edit", help="Build a deterministic dry-run edit plan")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.add_argument("--intent", default="", help="Natural-language change intent")
    p.add_argument("--candidates", default="", help="Comma-separated candidate /Game paths")
    p.add_argument("--scope", default="/Game", help="Allowed scope prefix")
    p.add_argument("--max-ops", type=int, default=16, help="Max operations (1..64)")
    p.add_argument("--operations-file", default=None, help="Optional YAML operation list")
    p.set_defaults(func=cmd_plan_edit)

    p = sub.add_parser("apply-plan", help="Approve and dry-run an edit plan")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.add_argument("--plan-id", default="", help="Deterministic plan id")
    p.add_argument("--approved-by", default="", help="Human approver id")
    p.add_argument("--approvals", default="", help="Comma-separated approved operation ids")
    p.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True,
                   help="Do not execute Write ops when enabled")
    p.set_defaults(func=cmd_apply_plan)

    p = sub.add_parser("verify-edit", help="Verify per-op plan results and index consistency")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.add_argument("--plan-id", default="", help="Deterministic plan id")
    p.set_defaults(func=cmd_verify_edit)

    p = sub.add_parser("rollback-edit", help="Dry-run the plan's inverse operations")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.add_argument("--plan-id", default="", help="Deterministic plan id")
    p.add_argument("--approved-by", default="", help="Human approver id")
    p.add_argument("--approvals", default="", help="Comma-separated approved inverse operation ids")
    p.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True,
                   help="Do not execute Write ops when enabled")
    p.set_defaults(func=cmd_rollback_edit)

    p = sub.add_parser("expand-gamespec", help="Expand a declarative GameSpec template")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.add_argument("--intent", default="", help="Supported FPS 3C intent")
    p.add_argument("--template", default="fps_3c", help="Template id")
    p.set_defaults(func=cmd_expand_gamespec)

    p = sub.add_parser("run-validation-loop", help="Run bounded offline validation stages")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.add_argument("--game-spec-id", default="", help="Deterministic GameSpec id")
    p.add_argument("--runner", default="offline", help="Host runner id")
    p.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True,
                   help="Skip real engine side effects")
    p.add_argument("--approved-by", default="", help="Human approver for non-dry-run")
    p.add_argument("--approvals", default="", help="Explicit per-op approval id RunValidationLoop")
    p.set_defaults(func=cmd_run_validation_loop)

    p = sub.add_parser("run-workflow", help="Run the ten-step offline FPS 3C workflow")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.add_argument("--intent", default="", help="Supported FPS 3C intent")
    p.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True,
                   help="Skip real engine side effects")
    p.add_argument("--approved-by", default="", help="Human approver for non-dry-run")
    p.add_argument("--approvals", default="", help="Explicit per-op approval ids for all workflow operations")
    p.set_defaults(func=cmd_run_workflow)

    p = sub.add_parser("request-rescan", help="Stage a deterministic incremental rescan request")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.add_argument("--asset-paths", default="", help="Comma-separated /Game paths")
    p.add_argument("--reason", default="index consistency", help="Audit reason")
    p.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True,
                   help="Only stage the rescan request when enabled")
    p.add_argument("--approved-by", default="", help="Human approver for non-dry-run")
    p.add_argument("--approvals", default="", help="Explicit per-op approval id RequestRescan")
    p.set_defaults(func=cmd_request_rescan)

    p = sub.add_parser("read-artifact", help="Page one canonical .ual artifact as bounded TOON")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.add_argument("--relative", default="", help="Repository-relative .ual artifact path")
    p.add_argument("--offset", type=int, default=0, help="Canonical artifact line offset")
    p.add_argument("--limit", type=int, default=80, help="Max lines (1..200)")
    p.set_defaults(func=cmd_read_artifact)

    p = sub.add_parser("import-gamespec", help="Validate a structured host GameSpec payload")
    p.add_argument("--project", default=None, help="Artifact root for runtime tools")
    p.add_argument("--payload", default="", help="Inline structured GameSpec YAML")
    p.add_argument("--payload-file", default=None, help="Repository-relative structured GameSpec YAML file")
    p.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True,
                   help="Skip host side effects when enabled")
    p.add_argument("--approved-by", default="", help="Human approver for non-dry-run")
    p.add_argument("--approvals", default="", help="Explicit per-op approval id ImportGameSpec")
    p.set_defaults(func=cmd_import_gamespec)

    p = sub.add_parser("apply-events", help="Apply canonical staging event streams to DuckDB")
    p.add_argument("--project", default=None, help="UE project root containing .ual/staging")
    p.add_argument("--events", default=DEFAULT_EVENTS, help="Repository-relative 16-key event stream")
    p.add_argument("--removed", default=DEFAULT_REMOVED, help="Repository-relative removed stream")
    p.set_defaults(func=cmd_apply_events)

    p = sub.add_parser("benchmark", help="Run the offline DuckDB index benchmark")
    p.add_argument("--project", default=None, help="UE project root")
    p.add_argument("--count", type=int, default=10000, help="Synthetic asset count (1..100000)")
    p.add_argument("--incremental", action="store_true", help="Measure an unchanged incremental pass")
    p.add_argument("--dry-run", action="store_true", default=True, help="Offline benchmark has no real writes")
    p.set_defaults(func=cmd_benchmark)

    p = sub.add_parser("self-test", help="Offline end-to-end check")
    p.set_defaults(func=cmd_self_test)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
