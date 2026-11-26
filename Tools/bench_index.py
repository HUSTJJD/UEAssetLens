#!/usr/bin/env python3
"""Reproducible cold-build / unchanged-incremental benchmark for the UAL index.

Usage (engine Python with its PipInstall site-packages on PYTHONPATH):

    python Tools/bench_index.py --count 100000
    python Tools/bench_index.py --count 100000 --incremental
    python Tools/bench_index.py --count 10000 --incremental --enforce-targets

``--enforce-targets`` is CI opt-in: return 1 when either target flag in the
canonical TOON summary is false. Ad-hoc reporting stays report-only (exit 0).
The DuckDB file is created in a TemporaryDirectory and removed on exit. stdout
is a single TOON report rendered by ual_common.toon.render_toon. Synthetic
assets are generated inside DuckDB from ``range(?)``; no Python-side raw dump or
absolute local path enters the report. Runtime dependencies are unchanged:
duckdb / PyYAML / mcp.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import duckdb

SERVER_ROOT = Path(__file__).resolve().parents[1] / "Server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from ual_common import db  # noqa: E402
from ual_common.toon import Table, render_toon  # noqa: E402

COLD_TARGET_SECONDS = 30.0
INCREMENTAL_TARGET_SECONDS = 1.0
REPORT_FIELDS = ("scenario", "assets", "seconds", "assets_per_second")

# Static SQL identifiers only. The single ? is a bound row count, never a
# user-supplied identifier or expression. Column order is checked against the
# authoritative ASSET_COLUMNS tuple so a 16-key contract change fails loudly.
EXPECTED_COLUMNS = (
    "asset_path", "logical_id", "asset_class", "parent_class", "taxonomy",
    "tokens", "content_hash", "duration", "bounds_size", "skeleton_path",
    "dominant_limb", "fingerprint_vec", "interface_spec", "domain_spec",
    "yaml_cache",
)
if db.ASSET_COLUMNS != EXPECTED_COLUMNS:
    raise RuntimeError("bench_index: ASSET_COLUMNS drifted; update the static SQL contract")

COLD_INSERT_SQL = """
INSERT INTO assets (
  asset_path, logical_id, asset_class, parent_class, taxonomy, tokens,
  content_hash, duration, bounds_size, skeleton_path, dominant_limb,
  fingerprint_vec, interface_spec, domain_spec, yaml_cache
)
SELECT
  '/Game/Bench/' || chr(CAST(65 + (i % 26) AS INTEGER)) || '/AM_' || printf('%06d', i),
  'AnimMontage:AM_' || printf('%06d', i),
  'AnimMontage',
  'AnimSequenceBase',
  ['Asset', 'Animation', 'Montage'],
  'bench montage synthetic ' || chr(CAST(65 + (i % 26) AS INTEGER)) || ' ' || CAST(i % 100 AS VARCHAR),
  'sha1:bench-' || printf('%06d', i),
  CAST(i % 100 AS DOUBLE) / 10.0,
  CAST(NULL AS FLOAT[3]),
  CAST(NULL AS VARCHAR),
  CAST(NULL AS VARCHAR),
  CAST(NULL AS FLOAT[16]),
  MAP {'loop': 'false'},
  MAP {'forward_cm': '0.0', 'hint': 'bench'},
  CAST(NULL AS VARCHAR)
FROM range(?) AS t(i)
"""

CHANGED_COUNT_SQL = """
SELECT COUNT(*) FROM assets
WHERE content_hash <> 'sha1:bench-' || printf('%06d', CAST(substr(asset_path, -6) AS BIGINT))
"""


def bulk_ingest(con: Any, count: int) -> int:
    """Generate deterministic 16-key rows in DuckDB, then return row count."""
    con.execute(COLD_INSERT_SQL, [count])
    return int(con.execute("SELECT COUNT(*) FROM assets").fetchone()[0])


def run_cold(index: Path, count: int) -> float:
    """Schema + SQL bulk insert + FTS rebuild, all inside the measured region."""
    started = time.perf_counter()
    con = db.open_write(index)
    try:
        written = bulk_ingest(con, count)
        db.rebuild_fts(con)
    finally:
        con.close()
    elapsed = time.perf_counter() - started
    if written != count:
        raise RuntimeError(
            f"bench_index: cold build wrote {written} rows (expected {count})"
        )
    return elapsed


def run_incremental(index: Path, count: int) -> tuple[float, int, int]:
    """Re-scan the same fixed set and skip rows whose content_hash is unchanged.

    content_hash is used only for change detection here, never for dedup. The
    unchanged pass performs no writes, so the FTS snapshot stays valid.
    """
    started = time.perf_counter()
    probe = db.open_readonly(index)
    try:
        changed = int(probe.execute(CHANGED_COUNT_SQL).fetchone()[0])
    finally:
        probe.close()
    if changed:
        con = db.open_write(index)
        try:
            bulk_ingest(con, count)
            db.rebuild_fts(con)
        finally:
            con.close()
    elapsed = time.perf_counter() - started
    return elapsed, changed, count - changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100000,
                        help="number of deterministic synthetic assets (default: 100000)")
    parser.add_argument("--incremental", action="store_true",
                        help="also time an unchanged incremental pass")
    parser.add_argument("--enforce-targets", action="store_true", default=False,
                        help="return 1 when either benchmark target flag is false "
                             "(CI; use with --incremental; default: report only)")
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error("--count must be >= 1")

    with tempfile.TemporaryDirectory(prefix="ual-bench-") as tmp:
        index = Path(tmp) / "index.duckdb"
        cold_seconds = run_cold(index, args.count)
        rows: list[list[Any]] = [[
            "cold_build", args.count, cold_seconds,
            args.count / max(cold_seconds, 1e-9),
        ]]
        incremental_seconds = 0.0
        changed = 0
        skipped = 0
        if args.incremental:
            incremental_seconds, changed, skipped = run_incremental(index, args.count)
            rows.append([
                "incremental_unchanged", args.count, incremental_seconds,
                args.count / max(incremental_seconds, 1e-9),
            ])
        cold_target_met = cold_seconds <= COLD_TARGET_SECONDS
        incremental_target_met = bool(
            args.incremental and incremental_seconds <= INCREMENTAL_TARGET_SECONDS
        )
        summary = {
            "assets": args.count,
            "duckdb_version": duckdb.__version__,
            "content_hash_use": "change_detection_only",
            "cold_target_seconds": COLD_TARGET_SECONDS,
            "incremental_target_seconds": INCREMENTAL_TARGET_SECONDS,
            "cold_target_met": cold_target_met,
            "incremental_target_met": incremental_target_met,
            "incremental_measured": bool(args.incremental),
            "cold_fts_rebuilt": True,
            "incremental_changed_assets": changed,
            "incremental_skipped_unchanged": skipped,
            "incremental_fts_rebuilt": bool(args.incremental and changed > 0),
            "null_fingerprint_assets": args.count,
            "batch_size": "duckdb_range",
        }
    print(render_toon([Table("benchmark", REPORT_FIELDS, rows)], summary), end="")
    if args.enforce_targets and not (cold_target_met and incremental_target_met):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
