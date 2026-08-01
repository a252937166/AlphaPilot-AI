from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
from scripts import run_factor_research

from alphapilot.backtest.factor_scope import HISTORICAL_FACTOR_CANDIDATES
from alphapilot.jobs import factor_research_job
from alphapilot.jobs.registry import JobExecutionError


def _ic_table(offset: float = 0.0) -> pd.DataFrame:
    return pd.DataFrame.from_records(
        [
            {
                "factor": factor,
                "ic_mean": 0.01 + offset,
                "ic_ir": 0.1 + offset,
                "t_stat": 2.0 + offset,
                "n_periods": 42,
                "long_short": 0.02 + offset,
            }
            for factor in HISTORICAL_FACTOR_CANDIDATES
        ]
    )


@contextmanager
def _fake_session() -> Any:
    yield object()


def test_formal_runtime_uses_explicit_full_train_test_and_lineage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, date, date]] = []
    calendar = [date(2019, 1, 2) + timedelta(days=index) for index in range(10)]

    monkeypatch.setattr(
        factor_research_job,
        "_require_research_safety",
        lambda: {"trading_mode": "research"},
    )
    monkeypatch.setattr(
        factor_research_job,
        "_require_s6_gate",
        lambda: {
            "ready_for_s7": True,
            "report_version": "fixture",
            "generated_at": "2026-07-26T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(factor_research_job, "get_session", _fake_session)
    monkeypatch.setattr(
        factor_research_job,
        "_calendar",
        lambda _session, start, end: (
            calendar
            if (start, end) == (date(2019, 1, 1), date(2019, 12, 31))
            else pytest.fail("calendar must receive the explicit caller window")
        ),
    )

    def fake_ic(
        _session: object,
        start: date,
        end: date,
        *,
        sample_tag: str,
        persist: bool,
    ) -> pd.DataFrame:
        assert persist is False
        calls.append((sample_tag, start, end))
        return _ic_table({"full": 0.0, "train": 0.1, "test": 0.2}[sample_tag])

    correlation = pd.DataFrame(
        1.0,
        index=HISTORICAL_FACTOR_CANDIDATES,
        columns=HISTORICAL_FACTOR_CANDIDATES,
    )
    correlation.attrs = {
        "method": "fixture",
        "minimum_pair_periods": 3,
        "decision_dates": ["2019-01-02"],
    }
    monkeypatch.setattr(factor_research_job, "all_factors_ic", fake_ic)
    monkeypatch.setattr(
        factor_research_job,
        "factor_correlation",
        lambda _session, start, end: (
            correlation
            if (start, end) == (calendar[0], calendar[6])
            else pytest.fail("correlation must use the explicit train window")
        ),
    )
    monkeypatch.setattr(
        factor_research_job,
        "persist_factors_ic",
        lambda _session, table, **kwargs: (
            None
            if table is not None
            and kwargs["sample_tag"] in {"full", "train", "test"}
            else pytest.fail("unexpected IC persistence payload")
        ),
    )
    monkeypatch.setattr(
        factor_research_job,
        "persist_factor_correlation",
        lambda _session, corr, **kwargs: (
            66
            if corr is correlation
            and kwargs
            == {
                "sample_tag": "train",
                "start": calendar[0],
                "end": calendar[6],
            }
            else pytest.fail("correlation lineage/window mismatch")
        ),
    )
    monkeypatch.setattr(
        factor_research_job,
        "rebuild_weights",
        lambda *_args, **_kwargs: pytest.fail("S7 must not rebuild weights"),
    )

    result = factor_research_job.run_factor_research(
        start_date=date(2019, 1, 1),
        end_date=date(2019, 12, 31),
        train_ratio=0.7,
        do_rebuild=False,
        output_path=tmp_path / "unused.yaml",
    )

    assert calls == [
        ("full", calendar[0], calendar[-1]),
        ("train", calendar[0], calendar[6]),
        ("test", calendar[7], calendar[-1]),
    ]
    assert result["status"] == "formal_factor_research"
    assert set(result["samples"]) == {"full", "train", "test"}
    assert all(
        set(sample["n_periods"]) == set(HISTORICAL_FACTOR_CANDIDATES)
        for sample in result["samples"].values()
    )
    assert result["correlation"]["stored_cells"] == 66
    assert result["correlation"]["lineage"] == {
        "job_name": "research_factors_m3",
        "sample_tag": "train",
        "start_date": calendar[0].isoformat(),
        "end_date": calendar[6].isoformat(),
    }
    assert result["excluded_factors"] == {
        "net_inflow_5d": "history_excluded_pit_gap",
        "sector_strength": "live_only",
    }
    assert result["weights_written"] is False


def test_s6_gate_blocks_before_any_database_or_research_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def blocked() -> dict[str, Any]:
        raise JobExecutionError(
            "S6 blocked",
            stats={"status": "blocked_s6", "research_started": False},
        )

    monkeypatch.setattr(factor_research_job, "_require_s6_gate", blocked)
    monkeypatch.setattr(
        factor_research_job,
        "_require_research_safety",
        lambda: {"trading_mode": "research"},
    )
    monkeypatch.setattr(
        factor_research_job,
        "get_session",
        lambda: pytest.fail("S6 must run before opening a research Session"),
    )
    monkeypatch.setattr(
        factor_research_job,
        "_calendar",
        lambda *_args: pytest.fail("S6 must run before reading the calendar"),
    )
    monkeypatch.setattr(
        factor_research_job,
        "all_factors_ic",
        lambda *_args, **_kwargs: pytest.fail("S6 must run before factor outcomes"),
    )

    with pytest.raises(JobExecutionError, match="S6 blocked") as captured:
        factor_research_job.run_factor_research(
            start_date=date(2019, 1, 1),
            end_date=date(2026, 7, 23),
        )
    assert captured.value.stats["research_started"] is False


def test_safety_gate_blocks_before_s6_or_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocked = JobExecutionError(
        "unsafe",
        stats={"status": "blocked_safety", "research_started": False},
    )
    monkeypatch.setattr(
        factor_research_job,
        "_require_research_safety",
        lambda: (_ for _ in ()).throw(blocked),
    )
    monkeypatch.setattr(
        factor_research_job,
        "_require_s6_gate",
        lambda: pytest.fail("S6 must not run when safety is open"),
    )
    monkeypatch.setattr(
        factor_research_job,
        "get_session",
        lambda: pytest.fail("database must remain untouched"),
    )

    with pytest.raises(JobExecutionError, match="unsafe"):
        factor_research_job.run_factor_research(
            start_date=date(2019, 1, 1),
            end_date=date(2026, 7, 23),
        )


def test_formal_runtime_never_calls_fixed_301_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import alphapilot.backtest.weights_rebuild as weights_rebuild

    monkeypatch.setattr(
        weights_rebuild,
        "train_test_split",
        lambda *_args, **_kwargs: pytest.fail("fixed 301 split is forbidden"),
    )
    # The formal module deliberately does not import the fixed helper.
    assert "train_test_split" not in factor_research_job.__dict__


def test_s9_rebuild_rejects_non_v3_target_before_gates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        factor_research_job,
        "_require_research_safety",
        lambda: pytest.fail("invalid target must fail before safety/database"),
    )
    with pytest.raises(ValueError, match=r"factor_weights_v3\.yaml"):
        factor_research_job.run_factor_research(
            start_date=date(2019, 1, 2),
            end_date=date(2026, 7, 23),
            do_rebuild=True,
            output_path=tmp_path / "factor_weights_v2.yaml",
        )


def test_foreground_runner_registers_job_and_forces_s7_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    allowance_calls: list[tuple[str, str]] = []
    for key in (
        *run_factor_research._RESEARCH_ENV,
        "ALPHAPILOT_S6_EXTERNAL_PIT_EVIDENCE",
    ):
        monkeypatch.delenv(key, raising=False)
    def fake_register() -> None:
        monkeypatch.setitem(
            run_factor_research.JOBS,
            run_factor_research.FORMAL_RESEARCH_JOB_NAME,
            object(),
        )

    monkeypatch.setattr(
        run_factor_research,
        "register_factor_research_job",
        fake_register,
    )
    monkeypatch.setattr(
        run_factor_research,
        "_research_host_lock",
        _fake_session,
    )

    @contextmanager
    def fake_allowance(*, job_name: str) -> Iterator[None]:
        allowance_calls.append(("enter", job_name))
        try:
            yield
        finally:
            allowance_calls.append(("exit", job_name))

    monkeypatch.setattr(
        run_factor_research,
        "allow_s6_release_for_current_job",
        fake_allowance,
    )
    monkeypatch.setattr(
        run_factor_research,
        "run_job",
        lambda name, **kwargs: (
            calls.append((name, kwargs))
            or SimpleNamespace(id=7, status="ok", error=None, stats={})
        ),
    )
    evidence = tmp_path / "signed.json"

    result = run_factor_research._foreground(
        SimpleNamespace(
            start_date=date(2019, 1, 2),
            end_date=date(2026, 7, 23),
            train_ratio=0.7,
            external_pit_evidence=evidence,
        )
    )

    assert result == 0
    assert all(
        run_factor_research.os.environ[key] == value
        for key, value in run_factor_research._RESEARCH_ENV.items()
    )
    assert calls == [
        (
            "research_factors_m3",
            {
                "start_date": date(2019, 1, 2),
                "end_date": date(2026, 7, 23),
                "train_ratio": 0.7,
                "do_rebuild": False,
                "output_path": None,
            },
        )
    ]
    assert allowance_calls == [
        ("enter", "research_factors_m3"),
        ("exit", "research_factors_m3"),
    ]


def test_detached_child_uses_resolved_evidence_and_research_safety_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    class _Process:
        pid = 321

    def fake_popen(command: list[str], **kwargs: Any) -> _Process:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _Process()

    monkeypatch.setattr(run_factor_research.subprocess, "Popen", fake_popen)
    evidence = tmp_path / "signed.json"
    result = run_factor_research._detach(
        SimpleNamespace(
            start_date=date(2019, 1, 2),
            end_date=date(2026, 7, 23),
            train_ratio=0.7,
            external_pit_evidence=evidence,
            log_path=tmp_path / "research.log",
        )
    )

    assert result == 0
    command = captured["command"]
    assert str(evidence.resolve()) in command
    child_env = captured["kwargs"]["env"]
    assert all(
        child_env[key] == value
        for key, value in run_factor_research._RESEARCH_ENV.items()
    )


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm", "-journal"])
def test_detached_log_rejects_database_and_sidecar_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    suffix: str,
) -> None:
    database = tmp_path / "alphapilot.db"
    protected = Path(f"{database}{suffix}")
    protected.write_bytes(b"protected")
    evidence = tmp_path / "signed.json"
    monkeypatch.setattr(
        run_factor_research,
        "get_settings",
        lambda: SimpleNamespace(database_url=f"sqlite:///{database}"),
    )

    with pytest.raises(ValueError, match="must not alias"):
        run_factor_research._validated_log_path(
            protected,
            evidence_path=evidence,
        )

    assert protected.read_bytes() == b"protected"


def test_detached_log_rejects_hardlink_and_signed_evidence_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = tmp_path / "alphapilot.db"
    database.write_bytes(b"database")
    hardlink = tmp_path / "research.log"
    os.link(database, hardlink)
    evidence = tmp_path / "signed.json"
    evidence.write_text("signed", encoding="utf-8")
    monkeypatch.setattr(
        run_factor_research,
        "get_settings",
        lambda: SimpleNamespace(database_url=f"sqlite:///{database}"),
    )

    with pytest.raises(ValueError, match="must not alias"):
        run_factor_research._validated_log_path(
            hardlink,
            evidence_path=evidence,
        )
    with pytest.raises(ValueError, match="must not alias"):
        run_factor_research._validated_log_path(
            evidence,
            evidence_path=evidence,
        )

    assert database.read_bytes() == b"database"
    assert evidence.read_text(encoding="utf-8") == "signed"
