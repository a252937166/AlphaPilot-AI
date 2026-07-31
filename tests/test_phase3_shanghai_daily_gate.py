from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, time
from pathlib import Path

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "run_shanghai_daily.py"
)
_SPEC = importlib.util.spec_from_file_location("run_shanghai_daily", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
run_shanghai_daily = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = run_shanghai_daily
_SPEC.loader.exec_module(run_shanghai_daily)


def test_gate_uses_shanghai_wall_time_and_one_success_per_day() -> None:
    before = datetime(2026, 7, 26, 20, 9, tzinfo=UTC)
    at_target = datetime(2026, 7, 26, 20, 10, tzinfo=UTC)

    assert not run_shanghai_daily._should_run(
        now=before,
        target=time(4, 10),
        last_success_date=None,
    )
    assert run_shanghai_daily._should_run(
        now=at_target,
        target=time(4, 10),
        last_success_date="2026-07-26",
    )
    assert not run_shanghai_daily._should_run(
        now=at_target,
        target=time(4, 10),
        last_success_date="2026-07-27",
    )


@pytest.mark.parametrize("value", ["4:10", "24:00", "04:60", "invalid"])
def test_gate_rejects_invalid_target_time(value: str) -> None:
    with pytest.raises(ValueError, match="invalid Shanghai target time"):
        run_shanghai_daily._parse_target(value)


def test_success_state_is_atomic_and_private(tmp_path: Path) -> None:
    state = tmp_path / "runtime" / "last-success-shanghai-date"

    run_shanghai_daily._write_success(state, "2026-07-27")

    assert state.read_text(encoding="utf-8") == "2026-07-27\n"
    assert state.stat().st_mode & 0o777 == 0o600
    assert run_shanghai_daily._read_last_success(state) == "2026-07-27"
