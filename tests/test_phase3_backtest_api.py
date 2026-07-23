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
        for running in session.scalars(
            select(BacktestRun).where(BacktestRun.status == "running")
        ):
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
            session.scalars(
                select(DailyBar.trade_date).where(DailyBar.symbol == "699991")
            )
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
        for running in session.scalars(
            select(BacktestRun).where(BacktestRun.status == "running")
        ):
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
                    group_returns=[
                        -0.005 + group_index * 0.001
                        for group_index in range(10)
                    ],
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
