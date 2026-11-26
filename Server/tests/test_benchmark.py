"""Benchmark target enforcement tests using canonical TOON and stubbed timings."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SERVER_ROOT = PLUGIN_ROOT / "Server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from ual_common.toon import HEADER as TOON_HEADER, parse_toon  # noqa: E402

SCRIPT_PATH = PLUGIN_ROOT / "Tools" / "bench_index.py"
SPEC = importlib.util.spec_from_file_location("ual_bench_index_under_test", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
bench = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bench
SPEC.loader.exec_module(bench)


def _run_benchmark(
    monkeypatch: Any,
    capsys: Any,
    cold_seconds: float,
    incremental_seconds: float,
    *,
    incremental: bool = True,
    enforce: bool = False,
) -> tuple[int, dict[str, Any], str]:
    def fake_run_cold(_index: Path, _count: int) -> float:
        return cold_seconds

    def fake_run_incremental(_index: Path, count: int) -> tuple[float, int, int]:
        return incremental_seconds, 0, count

    monkeypatch.setattr(bench, "run_cold", fake_run_cold)
    monkeypatch.setattr(bench, "run_incremental", fake_run_incremental)

    argv = ["--count", "1"]
    if incremental:
        argv.append("--incremental")
    if enforce:
        argv.append("--enforce-targets")
    code = bench.main(argv)
    output = capsys.readouterr().out
    parsed = parse_toon(output)
    assert output.startswith(TOON_HEADER)
    assert parsed["header"] == TOON_HEADER
    assert parsed["summary"]["assets"] == "1"
    assert parsed["summary"]["incremental_measured"] == (
        "true" if incremental else "false"
    )
    assert len(parsed["tables"]["benchmark"]) == (2 if incremental else 1)
    return code, parsed, output


def test_benchmark_target_flags_are_canonical_and_default_is_report_only(
    monkeypatch: Any, capsys: Any
) -> None:
    code, parsed, _ = _run_benchmark(
        monkeypatch, capsys,
        bench.COLD_TARGET_SECONDS + 1.0,
        bench.INCREMENTAL_TARGET_SECONDS + 1.0,
    )
    assert parsed["summary"]["cold_target_met"] == "false"
    assert parsed["summary"]["incremental_target_met"] == "false"
    assert code == 0


@pytest.mark.parametrize(
    ("cold_seconds", "incremental_seconds", "cold_met", "incremental_met",
     "expected_code"),
    [
        (bench.COLD_TARGET_SECONDS + 1.0,
         bench.INCREMENTAL_TARGET_SECONDS + 1.0, "false", "false", 1),
        (bench.COLD_TARGET_SECONDS + 1.0,
         bench.INCREMENTAL_TARGET_SECONDS, "false", "true", 1),
        (bench.COLD_TARGET_SECONDS,
         bench.INCREMENTAL_TARGET_SECONDS + 1.0, "true", "false", 1),
        (bench.COLD_TARGET_SECONDS,
         bench.INCREMENTAL_TARGET_SECONDS, "true", "true", 0),
    ],
)
def test_enforce_targets_returns_nonzero_when_either_target_flag_is_false(
    monkeypatch: Any,
    capsys: Any,
    cold_seconds: float,
    incremental_seconds: float,
    cold_met: str,
    incremental_met: str,
    expected_code: int,
) -> None:
    code, parsed, _ = _run_benchmark(
        monkeypatch, capsys, cold_seconds, incremental_seconds, enforce=True
    )
    assert parsed["summary"]["cold_target_met"] == cold_met
    assert parsed["summary"]["incremental_target_met"] == incremental_met
    assert code == expected_code


def test_enforce_targets_requires_incremental_measurement(
    monkeypatch: Any, capsys: Any
) -> None:
    code, parsed, _ = _run_benchmark(
        monkeypatch, capsys,
        bench.COLD_TARGET_SECONDS,
        bench.INCREMENTAL_TARGET_SECONDS,
        incremental=False,
        enforce=True,
    )
    assert parsed["summary"]["cold_target_met"] == "true"
    assert parsed["summary"]["incremental_target_met"] == "false"
    assert code == 1


def test_enforce_targets_preserves_canonical_toon_output(
    monkeypatch: Any, capsys: Any
) -> None:
    default_code, default_parsed, default_output = _run_benchmark(
        monkeypatch, capsys,
        bench.COLD_TARGET_SECONDS + 1.0,
        bench.INCREMENTAL_TARGET_SECONDS + 1.0,
    )
    enforced_code, enforced_parsed, enforced_output = _run_benchmark(
        monkeypatch, capsys,
        bench.COLD_TARGET_SECONDS + 1.0,
        bench.INCREMENTAL_TARGET_SECONDS + 1.0,
        enforce=True,
    )
    assert default_code == 0
    assert enforced_code == 1
    assert default_output == enforced_output
    assert default_parsed == enforced_parsed
    assert enforced_parsed["summary"]["cold_target_met"] == "false"
    assert enforced_parsed["summary"]["incremental_target_met"] == "false"
