from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import alphapilot.api.routes.backtest as backtest_routes
from alphapilot.db.engine import get_session
from alphapilot.db.models import (
    BacktestDaily,
    BacktestRun,
    DailyBar,
    Security,
)
from alphapilot.main import app

client = TestClient(app)
START = date(2026, 1, 5)
END = date(2026, 1, 6)


@pytest.fixture
def audited_range() -> Iterator[None]:
    with get_session() as session:
        for running in session.scalars(select(BacktestRun).where(BacktestRun.status == "running")):
            running.status = "failed"
            running.error = "test cleanup"
        security = session.get(Security, "699991")
        if security is None:
            session.add(
                Security(
                    symbol="699991",
                    market="CN",
                    name="回测接口样本",
                    board="主板",
                    is_st=False,
                    list_status="listed",
                    listed_date="2020-01-01",
                    snapshot_at=datetime(2026, 1, 5, 1, tzinfo=UTC),
                )
            )
        existing_dates = set(
            session.scalars(select(DailyBar.trade_date).where(DailyBar.symbol == "699991"))
        )
        for trade_date in (START, END):
            if trade_date not in existing_dates:
                session.add(
                    DailyBar(
                        symbol="699991",
                        trade_date=trade_date,
                        open=10.0,
                        high=10.1,
                        low=9.9,
                        close=10.0,
                        volume=1_000.0,
                        amount=10_000.0,
                        source="baostock",
                    )
                )
    yield
    with get_session() as session:
        for running in session.scalars(select(BacktestRun).where(BacktestRun.status == "running")):
            running.status = "failed"
            running.error = "test cleanup"


def test_post_run_queues_pollable_async_backtest(
    audited_range: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del audited_range
    monkeypatch.setattr(
        backtest_routes,
        "_run_queued_backtest",
        lambda _run_id, _cfg: None,
    )

    response = client.post(
        "/v1/backtest/run",
        json={
            "signal_id": "composite-v1",
            "start_date": START.isoformat(),
            "end_date": END.isoformat(),
            "rebalance_freq": "5d",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["run"]["status"] == "running"
    assert body["run"]["params"]["execution"] == "decision T close; fill T+1 open"
    run_id = body["run"]["id"]
    detail = client.get(f"/v1/backtest/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["run"]["status"] == "running"
    daily = client.get(f"/v1/backtest/{run_id}/daily")
    assert daily.status_code == 200
    assert daily.json()["dates"] == []
    report = client.get(f"/v1/backtest/{run_id}/report")
    assert report.status_code == 409
    assert "仍在运行" in report.json()["detail"]
    duplicate = client.post(
        "/v1/backtest/run",
        json={
            "signal_id": "composite-v1",
            "start_date": START.isoformat(),
            "end_date": END.isoformat(),
            "rebalance_freq": "5d",
        },
    )
    assert duplicate.status_code == 409
    assert "已有回测正在运行" in duplicate.json()["detail"]


def test_post_v2_test_window_uses_frozen_split_and_weights(
    audited_range: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del audited_range
    monkeypatch.setattr(
        backtest_routes,
        "_run_queued_backtest",
        lambda _run_id, _cfg: None,
    )
    test_start = END
    test_end = END
    monkeypatch.setattr(
        backtest_routes,
        "train_test_split",
        lambda _session: (START, START, test_start, test_end),
    )

    response = client.post(
        "/v1/backtest/run",
        json={
            "signal_id": "composite-v2",
            "window": "test",
            "rebalance_freq": "20d",
        },
    )

    assert response.status_code == 202
    run = response.json()["run"]
    assert run["name"] == "composite-v2 回测"
    assert run["signal_id"] == "composite-v2"
    assert run["start_date"] == test_start.isoformat()
    assert run["end_date"] == test_end.isoformat()
    assert run["rebalance_freq"] == "20d"
    assert run["params"]["weight_version"] == "v2.0.0"
    assert sum(abs(value) for value in run["params"]["weights"].values()) == (pytest.approx(1.0))


def test_v3_explicit_dates_bypass_fixed_301_window(
    audited_range: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del audited_range
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        backtest_routes,
        "train_test_split",
        lambda *_args, **_kwargs: pytest.fail("explicit S9 dates must bypass 301 split"),
    )
    monkeypatch.setattr(
        backtest_routes,
        "create_backtest_run",
        lambda _session, cfg: captured.update(cfg=cfg) or 9001,
    )
    monkeypatch.setattr(
        backtest_routes,
        "_run_queued_backtest",
        lambda _run_id, _cfg: None,
    )
    monkeypatch.setattr(
        backtest_routes,
        "_serialize_run",
        lambda _run: {
            "id": 9001,
            "signal_id": "composite-v3",
            "status": "running",
        },
    )
    monkeypatch.setattr(
        backtest_routes.Session,
        "get",
        lambda *_args, **_kwargs: object(),
    )

    response = client.post(
        "/v1/backtest/run",
        json={
            "signal_id": "composite-v3",
            "start_date": START.isoformat(),
            "end_date": END.isoformat(),
            "rebalance_freq": "20d",
        },
    )

    assert response.status_code == 202
    cfg = captured["cfg"]
    assert cfg.signal_id == "composite-v3"
    assert cfg.start_date == START
    assert cfg.end_date == END


def test_v3_missing_weight_file_returns_conflict_without_queue(
    audited_range: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del audited_range
    monkeypatch.setattr(
        backtest_routes,
        "create_backtest_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("composite-v3 权重配置尚未生成：fixture")
        ),
    )
    queued: list[object] = []
    monkeypatch.setattr(
        backtest_routes,
        "_run_queued_backtest",
        lambda *_args, **_kwargs: queued.append(object()),
    )

    response = client.post(
        "/v1/backtest/run",
        json={
            "signal_id": "composite-v3",
            "start_date": START.isoformat(),
            "end_date": END.isoformat(),
            "rebalance_freq": "20d",
        },
    )

    assert response.status_code == 409
    assert "尚未生成" in response.json()["detail"]
    assert queued == []


def test_factor_diagnosis_static_routes_are_not_shadowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        backtest_routes,
        "factor_ic_report",
        lambda _session, sample_tag, **_kwargs: {
            "available": False,
            "sample_tag": sample_tag,
            "factors": [],
        },
    )
    monkeypatch.setattr(
        backtest_routes,
        "factor_diagnosis_report",
        lambda _session, sample_tag, **_kwargs: {
            "available": False,
            "sample": {"tag": sample_tag},
        },
    )
    monkeypatch.setattr(
        backtest_routes,
        "factor_ic_windows",
        lambda _session, sample_tag: {
            "sample_tag": sample_tag,
            "windows": [],
        },
    )
    monkeypatch.setattr(
        backtest_routes,
        "compare_backtests",
        lambda _session, v1_id, v2_id: {
            "v1": v1_id,
            "v2": v2_id,
        },
    )

    ic = client.get("/v1/backtest/factors/ic")
    windows = client.get("/v1/backtest/factors/windows")
    diagnosis = client.get("/v1/backtest/factors/diagnosis")
    comparison = client.get("/v1/backtest/compare", params={"v1": 9, "v2": 8})

    assert ic.status_code == 200
    assert ic.json()["sample_tag"] == "train"
    assert windows.status_code == 200
    assert windows.json()["sample_tag"] == "train"
    assert diagnosis.status_code == 200
    assert diagnosis.json()["sample"]["tag"] == "train"
    assert comparison.status_code == 200
    assert comparison.json() == {"v1": 9, "v2": 8}


def test_factor_routes_forward_exact_window_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, date | None, date | None]] = []

    def fake_ic(
        _session: object,
        sample_tag: str,
        *,
        start_date: date | None,
        end_date: date | None,
    ) -> dict[str, object]:
        calls.append(("ic", sample_tag, start_date, end_date))
        return {
            "sample_tag": sample_tag,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        }

    def fake_diagnosis(
        _session: object,
        sample_tag: str,
        *,
        start_date: date | None,
        end_date: date | None,
    ) -> dict[str, object]:
        calls.append(("diagnosis", sample_tag, start_date, end_date))
        return {
            "sample": {
                "tag": sample_tag,
                "start_date": start_date.isoformat() if start_date else None,
                "end_date": end_date.isoformat() if end_date else None,
                "research_run_id": 15004,
            }
        }

    monkeypatch.setattr(backtest_routes, "factor_ic_report", fake_ic)
    monkeypatch.setattr(backtest_routes, "factor_diagnosis_report", fake_diagnosis)
    params = {
        "sample_tag": "train",
        "start_date": "2019-01-02",
        "end_date": "2024-04-16",
    }

    ic = client.get("/v1/backtest/factors/ic", params=params)
    diagnosis = client.get("/v1/backtest/factors/diagnosis", params=params)

    assert ic.status_code == 200
    assert diagnosis.status_code == 200
    assert diagnosis.json()["sample"]["research_run_id"] == 15004
    assert calls == [
        ("ic", "train", date(2019, 1, 2), date(2024, 4, 16)),
        ("diagnosis", "train", date(2019, 1, 2), date(2024, 4, 16)),
    ]


@pytest.mark.parametrize("route", ["/factors/ic", "/factors/diagnosis"])
@pytest.mark.parametrize(
    "params",
    [
        {"sample_tag": "train", "start_date": "2019-01-02"},
        {
            "sample_tag": "train",
            "start_date": "2024-04-16",
            "end_date": "2019-01-02",
        },
    ],
)
def test_factor_routes_reject_unpaired_or_reversed_dates(
    route: str,
    params: dict[str, str],
) -> None:
    response = client.get(f"/v1/backtest{route}", params=params)

    assert response.status_code == 422


def test_orphaned_background_run_expires_instead_of_blocking_queue(
    audited_range: None,
) -> None:
    del audited_range
    with get_session() as session:
        stale = BacktestRun(
            name="失联异步任务",
            signal_id="composite-v1",
            start_date=START,
            end_date=END,
            rebalance_freq="5d",
            top_pct=0.1,
            params={},
            status="running",
            summary={},
            created_at=datetime.now(UTC) - timedelta(hours=2),
        )
        session.add(stale)
        session.flush()
        run_id = int(stale.id)

    response = client.get(f"/v1/backtest/{run_id}")

    assert response.status_code == 200
    run = response.json()["run"]
    assert run["status"] == "failed"
    assert "超过 1 小时" in run["error"]
    assert run["summary"]["failure_stage"] == "background_lease"


def _completed_run() -> int:
    with get_session() as session:
        run = BacktestRun(
            name="API 完成样本",
            signal_id="composite-v1",
            start_date=START,
            end_date=END,
            rebalance_freq="5d",
            top_pct=0.1,
            params={
                "initial_capital": 1_000_000.0,
                "execution": "decision T close; fill T+1 open",
            },
            status="completed",
            summary={
                "total_cost": 100.0,
                "total_traded": 100_000.0,
                "day_errors": [],
                "missing_benchmark_days": 0,
            },
        )
        session.add(run)
        session.flush()
        for index, trade_date in enumerate((START, END)):
            session.add(
                BacktestDaily(
                    run_id=run.id,
                    trade_date=trade_date,
                    rank_ic=0.2 + index * 0.1,
                    long_ret=0.0 if index == 0 else 0.01,
                    ls_ret=0.002,
                    turnover=None if index == 0 else 0.5,
                    nav=1.0 + index * 0.01,
                    benchmark_nav=1.0 + index * 0.005,
                    market_nav=1.0 + index * 0.003,
                    n_eligible=100,
                    group_returns=[-0.005 + group_index * 0.001 for group_index in range(10)],
                )
            )
        session.flush()
        return int(run.id)


def test_list_detail_daily_and_report_endpoints(audited_range: None) -> None:
    del audited_range
    run_id = _completed_run()

    listed = client.get("/v1/backtest", params={"limit": 10})
    detail = client.get(f"/v1/backtest/{run_id}")
    daily = client.get(f"/v1/backtest/{run_id}/daily")
    report = client.get(f"/v1/backtest/{run_id}/report")

    assert listed.status_code == 200
    assert any(item["id"] == run_id for item in listed.json()["runs"])
    assert detail.status_code == 200
    assert detail.json()["run"]["report_available"] is True
    assert daily.status_code == 200
    assert daily.json()["dates"] == [START.isoformat(), END.isoformat()]
    assert daily.json()["market_nav"][-1] == pytest.approx(1.003)
    assert report.status_code == 200
    assert report.json()["run"]["id"] == run_id
    assert report.json()["probability_calibration"]["available"] is False
    assert client.get("/v1/backtest/999999").status_code == 404
