from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
P4_2A_DIRECT_ENTRYPOINTS = (
    "scripts/run_p4_2a_dev_iteration.py",
    "scripts/run_p4_2a_heldout_predictions.py",
    "scripts/run_p4_2a_v1_7_selection.py",
    "scripts/run_p4_2a_v2_dev_calibration.py",
    "scripts/evaluate_p4_2a_gold.py",
)


def _isolated_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


@pytest.mark.parametrize("relative_script", P4_2A_DIRECT_ENTRYPOINTS)
def test_direct_cli_help_bootstraps_checkout_without_pythonpath(
    tmp_path: Path,
    relative_script: str,
) -> None:
    sentinel = tmp_path / "state-must-not-change.jsonl"
    sentinel.write_bytes(b'{"event":"untouched"}\n')

    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(PROJECT_ROOT / relative_script),
            "--help",
        ],
        cwd=tmp_path,
        env=_isolated_environment(),
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("usage:")
    assert "Traceback" not in completed.stderr
    assert sentinel.read_bytes() == b'{"event":"untouched"}\n'
    assert sorted(path.name for path in tmp_path.iterdir()) == [sentinel.name]


@pytest.mark.parametrize("relative_script", P4_2A_DIRECT_ENTRYPOINTS)
def test_direct_cli_rejects_unknown_argument_before_any_state_write(
    tmp_path: Path,
    relative_script: str,
) -> None:
    sentinel = tmp_path / "state-must-not-change.jsonl"
    sentinel.write_bytes(b'{"event":"untouched"}\n')

    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            str(PROJECT_ROOT / relative_script),
            "--o4-invalid-entrypoint-probe",
        ],
        cwd=tmp_path,
        env=_isolated_environment(),
        capture_output=True,
        check=False,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 2
    assert "error:" in completed.stderr
    assert (
        "unrecognized arguments" in completed.stderr
        or "the following arguments are required" in completed.stderr
    )
    assert "Traceback" not in completed.stderr
    assert sentinel.read_bytes() == b'{"event":"untouched"}\n'
    assert sorted(path.name for path in tmp_path.iterdir()) == [sentinel.name]
