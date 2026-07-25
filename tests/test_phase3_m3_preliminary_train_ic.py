from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from inspect import signature
from typing import Any

import pandas as pd
import pytest

from alphapilot.backtest import factor_research
from alphapilot.jobs import factor_research_job


def _dates(start: date, count: int) -> list[date]:
    return [start + timedelta(days=index) for index in range(count)]


def _result(factors: object) -> pd.DataFrame:
    assert isinstance(factors, tuple)
    return pd.DataFrame(
        [
            {
                "factor": factor,
                "ic_mean": 0.01,
                "ic_ir": 0.1,
                "t_stat": 1.0,
                "n_periods": 3,
                "long_short": 0.02,
            }
            for factor in factors
        ]
    )


def test_preliminary_research_splits_two_train_only_cohorts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object()
    multi_year = _dates(date(2019, 1, 2), 100)
    one_year_flow = _dates(date(2025, 7, 25), 20)
    calls: list[dict[str, Any]] = []
    persisted: list[dict[str, Any]] = []

    @contextmanager
    def fake_session() -> Iterator[object]:
        yield session

    def fake_calendar(
        received_session: object,
        start: date,
        end: date,
    ) -> list[date]:
        assert received_session is session
        if start == multi_year[0] and end == multi_year[-1]:
            return multi_year
        assert start == one_year_flow[0]
        assert end == one_year_flow[-1]
        return one_year_flow

    def fake_research_factors_ic(
        received_session: object,
        factors: object,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        assert received_session is session
        calls.append(
            {
                "factors": factors,
                "start": start,
                "end": end,
            }
        )
        return _result(factors)

    def fake_persist_factors_ic(
        received_session: object,
        table: pd.DataFrame,
        *,
        sample_tag: str,
        start: date,
        end: date,
    ) -> None:
        assert received_session is session
        persisted.append(
            {
                "factors": table["factor"].tolist(),
                "sample_tag": sample_tag,
                "start": start,
                "end": end,
            }
        )

    monkeypatch.setattr(factor_research_job, "get_session", fake_session)
    monkeypatch.setattr(factor_research_job, "_calendar", fake_calendar)
    monkeypatch.setattr(
        factor_research_job,
        "_sector_flow_bounds",
        lambda _session, *, end_date: (one_year_flow[0], one_year_flow[-1]),
    )
    monkeypatch.setattr(
        factor_research_job,
        "research_factors_ic",
        fake_research_factors_ic,
    )
    monkeypatch.setattr(
        factor_research_job,
        "persist_factors_ic",
        fake_persist_factors_ic,
    )

    stats = factor_research_job.run_preliminary_train_ic(
        start_date=multi_year[0],
        end_date=multi_year[-1],
    )

    assert calls == [
        {
            "factors": factor_research_job.MULTI_YEAR_TRAIN_FACTORS,
            "start": multi_year[0],
            "end": multi_year[69],
        },
        {
            "factors": factor_research_job.SECTOR_FLOW_TRAIN_FACTORS,
            "start": one_year_flow[0],
            "end": one_year_flow[13],
        },
    ]
    assert persisted == [
        {
            "factors": list(factor_research_job.MULTI_YEAR_TRAIN_FACTORS),
            "sample_tag": "train",
            "start": multi_year[0],
            "end": multi_year[69],
        },
        {
            "factors": list(factor_research_job.SECTOR_FLOW_TRAIN_FACTORS),
            "sample_tag": "train",
            "start": one_year_flow[0],
            "end": one_year_flow[13],
        },
    ]
    assert stats["sample_tag"] == "train"
    assert stats["test_window_used"] is False
    assert stats["weights_written"] is False
    assert stats["cohorts"]["multi_year_price_valuation"]["sealed_test_window"] == {
        "start": multi_year[70].isoformat(),
        "end": multi_year[-1].isoformat(),
        "sessions": 30,
        "read_factor_outcomes": False,
    }
    assert stats["cohorts"]["one_year_sector_flow"]["sealed_test_window"] == {
        "start": one_year_flow[14].isoformat(),
        "end": one_year_flow[-1].isoformat(),
        "sessions": 6,
        "read_factor_outcomes": False,
    }


@pytest.mark.parametrize("ratio", [True, 0.0, 1.0, float("nan")])
def test_preliminary_research_rejects_invalid_train_ratio(ratio: object) -> None:
    with pytest.raises(ValueError, match="train_ratio"):
        factor_research_job._validated_ratio(ratio)  # type: ignore[arg-type]


def test_preliminary_research_failure_persists_neither_cohort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object()
    multi_year = _dates(date(2019, 1, 2), 10)
    one_year_flow = _dates(date(2025, 7, 25), 10)
    research_calls = 0

    @contextmanager
    def fake_session() -> Iterator[object]:
        yield session

    def fake_calendar(
        _session: object,
        start: date,
        _end: date,
    ) -> list[date]:
        return multi_year if start == multi_year[0] else one_year_flow

    def fail_on_flow(
        _session: object,
        factors: object,
        _start: date,
        _end: date,
    ) -> pd.DataFrame:
        nonlocal research_calls
        research_calls += 1
        if factors == factor_research_job.SECTOR_FLOW_TRAIN_FACTORS:
            raise RuntimeError("flow cohort failed")
        return _result(factors)

    monkeypatch.setattr(factor_research_job, "get_session", fake_session)
    monkeypatch.setattr(factor_research_job, "_calendar", fake_calendar)
    monkeypatch.setattr(
        factor_research_job,
        "_sector_flow_bounds",
        lambda _session, *, end_date: (one_year_flow[0], one_year_flow[-1]),
    )
    monkeypatch.setattr(
        factor_research_job,
        "research_factors_ic",
        fail_on_flow,
    )
    monkeypatch.setattr(
        factor_research_job,
        "persist_factors_ic",
        pytest.fail,
    )

    with pytest.raises(RuntimeError, match="flow cohort failed"):
        factor_research_job.run_preliminary_train_ic(
            start_date=multi_year[0],
            end_date=multi_year[-1],
        )

    assert research_calls == 2


def test_preliminary_runner_does_not_expose_test_or_rebuild_controls() -> None:
    parameters = signature(
        factor_research_job.run_preliminary_train_ic
    ).parameters
    assert set(parameters) == {"start_date", "end_date", "train_ratio"}
    assert "sample_tag" not in parameters
    assert "do_rebuild" not in parameters
    assert "output_path" not in parameters


def test_factors_ic_persists_only_the_requested_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = ("momentum_20d", "pe_percentile")
    start = date(2019, 1, 2)
    end = date(2024, 4, 16)
    persisted: dict[str, object] = {}
    records = [
        {
            "factor": factor,
            "ic_mean": 0.01,
            "ic_std": 0.1,
            "ic_ir": 0.1,
            "t_stat": 1.0,
            "ic_positive_ratio": 0.5,
            "n_periods": 10,
            "layered_returns": [None] * 10,
            "long_short": 0.02,
            "decay": {},
        }
        for factor in selected
    ]

    monkeypatch.setattr(factor_research, "_research", lambda *_args, **_kwargs: records)

    def capture(
        _session: object,
        table: pd.DataFrame,
        *,
        sample_tag: str,
        start: date,
        end: date,
    ) -> None:
        persisted.update(
            {
                "factors": table["factor"].tolist(),
                "sample_tag": sample_tag,
                "start": start,
                "end": end,
            }
        )

    monkeypatch.setattr(factor_research, "_persist_stats", capture)
    table = factor_research.factors_ic(
        object(),  # type: ignore[arg-type]
        selected,
        start,
        end,
        sample_tag="train",
    )

    assert table["factor"].tolist() == list(selected)
    assert persisted == {
        "factors": list(selected),
        "sample_tag": "train",
        "start": start,
        "end": end,
    }
