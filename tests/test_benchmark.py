"""Ensure the 20-case offline benchmark executes cleanly and scores 100%."""

import importlib.util
from pathlib import Path

_RUN_PATH = Path(__file__).resolve().parent.parent / "benchmarks" / "run.py"
_SPEC = importlib.util.spec_from_file_location("claimcite_benchmark_run", _RUN_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def test_offline_benchmark_passes_all_20_cases():
    summary = _MOD.evaluate()
    assert summary["total"] == 20
    assert summary["passed"] == 20, summary
    assert summary["failed"] == 0
    assert summary["false_errors"] == 0
