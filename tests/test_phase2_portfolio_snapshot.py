from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alphapilot.api.dependencies import db_session_dependency, futu_client_dependency
from alphapilot.api.routes import portfolio as portfolio_routes
from alphapilot.db.models import Base, DailyBar, PortfolioSnapshot, Security
from alphapilot.futu.client import FutuClient, FutuUnavailableError
from alphapilot.jobs import portfolio_snapshot as portfolio_job
from alphapilot.jobs.registry import JOBS, JobExecutionError
from alphapilot.services.portfolio import (
    BENCHMARK_SYMBOL,
    PortfolioServiceError,
    get_portfolio_attribution,
    get_portfolio_overview,
    recompute_portfolio_metrics,
    upsert_benchmark_close_bar,
    upsert_portfolio_snapshot,
)

MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
FIRST_DAY = date(2026, 7, 20)
SECOND_DAY = date(2026, 7, 21)
THIRD_DAY = date(2026, 7, 22)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    database = create_engine(
        f"sqlite:///{tmp_path / 'portfolio.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(database)
    return database


def _bar(trade_date: date, close: float, *, source: str = "fixture") -> DailyBar:
    return DailyBar(
        symbol=BENCHMARK_SYMBOL,
        trade_date=trade_date,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000.0,
        amount=10_000.0,
        source=source,
    )


def _funds(total: float, cash: float, market_value: float) -> dict[str, Any]:
    return {
        "total_assets": total,
        "cash": cash,
        "market_val": market_value,
    }


def _positions(
    bank_value: float = 80.0,
    unknown_value: float = 20.0,
) -> list[dict[str, Any]]:
    return [
        {"symbol": "600000", "qty": 100.0, "market_val": bank_value},
        {"symbol": "000001", "qty": 100.0, "market_val": unknown_value},
    ]


def _seed_security_and_bars(session: Session) -> None:
    session.add_all(
        [
            Security(symbol="600000", industry_csrc="J66货币金融服务"),
            Security(symbol="000001", industry_csrc=None),
            _bar(FIRST_DAY, 100.0),
            _bar(SECOND_DAY, 101.0),
            _bar(THIRD_DAY, 99.0),
        ]
    )
    session.commit()


def test_portfolio_snapshot_model_defaults_constraints_and_one_row_per_day(
    engine: Engine,
) -> None:
    with Session(engine) as session:
        snapshot = PortfolioSnapshot(
            trade_date=FIRST_DAY,
            total_value=1_000.0,
            cash=900.0,
            positions=[],
        )
        session.add(snapshot)
        session.commit()
        assert snapshot.source == "futu-sim"
        assert PortfolioSnapshot.__table__.primary_key.columns.keys() == ["trade_date"]

    with Session(engine) as session:
        session.add(
            PortfolioSnapshot(
                trade_date=SECOND_DAY,
                total_value=0.0,
                cash=0.0,
                positions=[],
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_snapshot_metrics_overview_and_attribution_use_only_real_aligned_rows(
    engine: Engine,
) -> None:
    with Session(engine, expire_on_commit=False) as session:
        _seed_security_and_bars(session)
        first, first_stats = upsert_portfolio_snapshot(
            session,
            FIRST_DAY,
            _funds(1_000.0, 900.0, 100.0),
            _positions(),
        )
        second, _ = upsert_portfolio_snapshot(
            session,
            SECOND_DAY,
            _funds(1_020.0, 920.0, 100.0),
            _positions(),
        )
        third, _ = upsert_portfolio_snapshot(
            session,
            THIRD_DAY,
            _funds(990.0, 890.0, 100.0),
            _positions(),
        )
        session.commit()

        assert first_stats["missing_industry_count"] == 1
        assert (first.daily_return, first.benchmark_return, first.excess_return) == (
            None,
            None,
            None,
        )
        assert first.drawdown == pytest.approx(0.0)
        assert second.daily_return == pytest.approx(0.02)
        assert second.benchmark_return == pytest.approx(0.01)
        assert second.excess_return == pytest.approx(0.01)
        assert second.drawdown == pytest.approx(0.0)
        assert third.daily_return == pytest.approx(990.0 / 1_020.0 - 1.0)
        assert third.benchmark_return == pytest.approx(99.0 / 101.0 - 1.0)
        assert third.excess_return == pytest.approx((990.0 / 1_020.0 - 1.0) - (99.0 / 101.0 - 1.0))
        assert third.drawdown == pytest.approx(990.0 / 1_020.0 - 1.0)

        overview = get_portfolio_overview(session)
        assert overview["available"] is True
        assert overview["market_value"] == pytest.approx(100.0)
        assert overview["missing_industry_count"] == 1
        assert overview["industry_distribution"] == [
            {
                "industry": "J66货币金融服务",
                "market_value": 80.0,
                "weight": 0.8,
            },
            {
                "industry": "未分类",
                "market_value": 20.0,
                "weight": 0.2,
            },
        ]

        attribution = get_portfolio_attribution(session, 60)
        assert attribution["available"] is True
        assert attribution["available_days"] == 3
        assert attribution["dates"] == [
            FIRST_DAY.isoformat(),
            SECOND_DAY.isoformat(),
            THIRD_DAY.isoformat(),
        ]
        assert attribution["nav"] == pytest.approx([1.0, 1.02, 0.99])
        assert attribution["benchmark_nav"] == pytest.approx([1.0, 1.01, 0.99])
        assert attribution["excess_cum"] == pytest.approx(0.0)
        assert attribution["max_drawdown"] == pytest.approx(990.0 / 1_020.0 - 1.0)
        assert attribution["benchmark_drawdown"] == pytest.approx(99.0 / 101.0 - 1.0)
        assert "仅累积 3" in str(attribution["warning"])


def test_same_day_upsert_cascades_metrics_without_duplicating_snapshot(
    engine: Engine,
) -> None:
    with Session(engine) as session:
        _seed_security_and_bars(session)
        upsert_portfolio_snapshot(
            session,
            FIRST_DAY,
            _funds(1_000.0, 900.0, 100.0),
            _positions(),
        )
        upsert_portfolio_snapshot(
            session,
            SECOND_DAY,
            _funds(1_020.0, 920.0, 100.0),
            _positions(),
        )
        upsert_portfolio_snapshot(
            session,
            THIRD_DAY,
            _funds(990.0, 890.0, 100.0),
            _positions(),
        )
        _, rerun = upsert_portfolio_snapshot(
            session,
            SECOND_DAY,
            _funds(1_030.0, 930.0, 100.0),
            _positions(),
        )
        session.commit()

        assert rerun["inserted"] == 0
        assert rerun["updated"] == 1
        assert session.scalar(select(func.count()).select_from(PortfolioSnapshot)) == 3
        third = session.get(PortfolioSnapshot, THIRD_DAY)
        assert third is not None
        assert third.daily_return == pytest.approx(990.0 / 1_030.0 - 1.0)
        assert third.drawdown == pytest.approx(990.0 / 1_030.0 - 1.0)


def test_inconsistent_account_or_position_values_fail_closed(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(Security(symbol="600000", industry_csrc="银行"))
        with pytest.raises(PortfolioServiceError, match="总资产"):
            upsert_portfolio_snapshot(
                session,
                FIRST_DAY,
                _funds(1_000.0, 800.0, 100.0),
                [{"symbol": "600000", "qty": 100.0, "market_val": 100.0}],
            )
        session.rollback()
        with pytest.raises(PortfolioServiceError, match="持仓明细"):
            upsert_portfolio_snapshot(
                session,
                FIRST_DAY,
                _funds(1_000.0, 900.0, 100.0),
                [{"symbol": "600000", "qty": 100.0, "market_val": 80.0}],
            )
        assert not session.new
        assert session.get(PortfolioSnapshot, FIRST_DAY) is None


def test_benchmark_close_upsert_corrects_existing_intraday_row(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(_bar(THIRD_DAY, 95.0, source="futu-intraday"))
        session.commit()
        action = upsert_benchmark_close_bar(
            session,
            THIRD_DAY,
            {
                "date": pd.Timestamp(THIRD_DAY),
                "open": 98.0,
                "high": 101.0,
                "low": 97.0,
                "close": 100.0,
                "volume": 2_000.0,
                "amount": 20_000.0,
            },
            source="futu-close",
        )
        session.commit()

        row = session.scalar(
            select(DailyBar).where(
                DailyBar.symbol == BENCHMARK_SYMBOL,
                DailyBar.trade_date == THIRD_DAY,
            )
        )
        assert row is not None
        assert action == "updated"
        assert (row.close, row.source, row.volume) == (100.0, "futu-close", 2_000.0)


def test_benchmark_gap_stays_null_and_never_uses_another_date(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(Security(symbol="600000", industry_csrc="银行"))
        session.add_all([_bar(FIRST_DAY, 100.0), _bar(THIRD_DAY, 99.0)])
        upsert_portfolio_snapshot(
            session,
            FIRST_DAY,
            _funds(1_000.0, 900.0, 100.0),
            [{"symbol": "600000", "qty": 100.0, "market_val": 100.0}],
        )
        second, _ = upsert_portfolio_snapshot(
            session,
            SECOND_DAY,
            _funds(1_010.0, 910.0, 100.0),
            [{"symbol": "600000", "qty": 100.0, "market_val": 100.0}],
        )
        session.commit()

        assert second.daily_return == pytest.approx(0.01)
        assert second.benchmark_return is None
        assert second.excess_return is None
        attribution = get_portfolio_attribution(session, 2)
        assert attribution["benchmark_nav"] == [1.0, None]
        assert attribution["excess_cum"] is None
        assert attribution["benchmark_drawdown"] is None
        assert SECOND_DAY.isoformat() in str(attribution["warning"])


def test_invalid_cached_benchmark_never_erases_existing_metrics(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(Security(symbol="600000", industry_csrc="银行"))
        session.add_all([_bar(FIRST_DAY, 100.0), _bar(SECOND_DAY, 101.0)])
        upsert_portfolio_snapshot(
            session,
            FIRST_DAY,
            _funds(1_000.0, 900.0, 100.0),
            [{"symbol": "600000", "qty": 100.0, "market_val": 100.0}],
        )
        second, _ = upsert_portfolio_snapshot(
            session,
            SECOND_DAY,
            _funds(1_020.0, 920.0, 100.0),
            [{"symbol": "600000", "qty": 100.0, "market_val": 100.0}],
        )
        session.commit()
        expected = second.benchmark_return
        damaged = session.scalar(
            select(DailyBar).where(
                DailyBar.symbol == BENCHMARK_SYMBOL,
                DailyBar.trade_date == SECOND_DAY,
            )
        )
        assert damaged is not None
        damaged.close = float("inf")
        session.commit()

        with pytest.raises(PortfolioServiceError, match="benchmark_close"):
            recompute_portfolio_metrics(session)
        session.rollback()
        session.refresh(second)
        assert second.benchmark_return == expected


def test_attribution_returns_at_most_requested_sixty_real_rows(engine: Engine) -> None:
    start = date(2026, 1, 1)
    with Session(engine) as session:
        for offset in range(61):
            trade_day = start + timedelta(days=offset)
            session.add(_bar(trade_day, 100.0 + offset))
            session.add(
                PortfolioSnapshot(
                    trade_date=trade_day,
                    total_value=1_000.0 + offset,
                    cash=900.0,
                    positions=[],
                )
            )
        recompute_portfolio_metrics(session)
        session.commit()

        payload = get_portfolio_attribution(session, 60)
        assert payload["available_days"] == 60
        assert payload["dates"][0] == (start + timedelta(days=1)).isoformat()
        assert payload["dates"][-1] == (start + timedelta(days=60)).isoformat()
        assert len(payload["dates"]) == len(payload["nav"]) == len(payload["benchmark_nav"]) == 60


class TrackedSessionFactory:
    def __init__(self, path: Path) -> None:
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(self.engine)
        self.active = 0

    @contextmanager
    def __call__(self) -> Iterator[Session]:
        with Session(self.engine, expire_on_commit=False) as session:
            self.active += 1
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                self.active -= 1


class StubPortfolioClient:
    def __init__(
        self,
        sessions: TrackedSessionFactory,
        *,
        target: date = THIRD_DAY,
        trading_day: bool = True,
        benchmark_available: bool = True,
        funds_error: bool = False,
    ) -> None:
        self.sessions = sessions
        self.target = target
        self.trading_day = trading_day
        self.benchmark_available = benchmark_available
        self.funds_error = funds_error
        self.total_assets = 1_000.0
        self.calls: list[str] = []

    def quote_call_raw(
        self,
        method: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        assert self.sessions.active == 0, "network call must not hold a DB session"
        self.calls.append(method)
        if method == "request_trading_days":
            assert args == ["CN", self.target.isoformat(), self.target.isoformat()]
            return [{"time": self.target.isoformat()}] if self.trading_day else []
        if method == "request_history_kline":
            assert kwargs is not None
            rows = []
            if self.benchmark_available:
                rows = [
                    {
                        "time_key": f"{self.target.isoformat()} 00:00:00",
                        "open": 99.0,
                        "high": 101.0,
                        "low": 98.0,
                        "close": 100.0,
                        "volume": 1_000.0,
                        "turnover": 10_000.0,
                    }
                ]
            return pd.DataFrame(rows), None
        raise AssertionError(f"unexpected quote method: {method}")

    def trade_call(
        self,
        context_kind: str,
        method: str,
        *,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        market: str = "HK",
        environment: str = "SIMULATE",
        confirmation: str | None = None,
    ) -> dict[str, Any]:
        del args, confirmation
        assert self.sessions.active == 0, "network call must not hold a DB session"
        assert context_kind == "security"
        assert market == "CN"
        assert environment == "SIMULATE"
        self.calls.append(method)
        if method == "get_acc_list":
            records = [
                {
                    "acc_id": 101,
                    "trd_env": "SIMULATE",
                    "sim_acc_type": "STOCK",
                    "acc_status": "ACTIVE",
                    "trdmarket_auth": ["CN"],
                }
            ]
        elif method == "accinfo_query":
            if self.funds_error:
                raise FutuUnavailableError("fixture unavailable")
            assert kwargs == {"acc_id": 101, "refresh_cache": True}
            records = [
                {
                    "total_assets": self.total_assets,
                    "cash": self.total_assets - 100.0,
                    "market_val": 100.0,
                }
            ]
        elif method == "position_list_query":
            assert kwargs == {"acc_id": 101, "refresh_cache": True}
            records = [
                {
                    "code": "SH.600000",
                    "qty": 100.0,
                    "cost_price": 1.0,
                    "cost_price_valid": True,
                    "market_val": 100.0,
                    "pl_ratio": 0.0,
                    "pl_ratio_valid": True,
                }
            ]
        else:
            raise AssertionError(f"unexpected trade method: {method}")
        return {"ok": True, "data": {"records": records}}


def _as_futu(client: StubPortfolioClient) -> FutuClient:
    return cast(FutuClient, client)


def _seed_job_security(sessions: TrackedSessionFactory) -> None:
    with sessions() as session:
        session.add(Security(symbol="600000", industry_csrc="银行"))


def test_snapshot_job_is_network_outside_db_idempotent_and_simulate_read_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sessions = TrackedSessionFactory(tmp_path / "job.db")
    _seed_job_security(sessions)
    client = StubPortfolioClient(sessions)
    monkeypatch.setattr(portfolio_job, "get_session", sessions)
    monkeypatch.setattr(
        portfolio_job,
        "_market_now",
        lambda: datetime(2026, 7, 22, 15, 10, tzinfo=MARKET_TIMEZONE),
    )

    first = portfolio_job.snapshot_portfolio(_as_futu(client))
    client.total_assets = 1_010.0
    second = portfolio_job.snapshot_portfolio(_as_futu(client))

    assert first["inserted"] == 1
    assert first["benchmark_bar"] == "inserted"
    assert second["updated"] == 1
    assert second["benchmark_bar"] == "updated"
    assert first["warning_count"] == second["warning_count"] == 0
    assert not {"place_order", "modify_order", "change_order", "cancel_all_order"} & set(
        client.calls
    )
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(PortfolioSnapshot)) == 1
        snapshot = session.get(PortfolioSnapshot, THIRD_DAY)
        assert snapshot is not None
        assert snapshot.total_value == pytest.approx(1_010.0)
        assert snapshot.positions == [
            {"symbol": "600000", "qty": 100.0, "mv": 100.0, "industry": "银行"}
        ]


def test_snapshot_job_before_close_and_non_trading_day_never_open_account_or_db(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sessions = TrackedSessionFactory(tmp_path / "skip.db")
    before_close = StubPortfolioClient(sessions)
    monkeypatch.setattr(portfolio_job, "get_session", sessions)
    monkeypatch.setattr(
        portfolio_job,
        "_market_now",
        lambda: datetime(2026, 7, 22, 15, 9, 59, tzinfo=MARKET_TIMEZONE),
    )

    early = portfolio_job.snapshot_portfolio(_as_futu(before_close))
    assert early["skipped"] == "before_close"
    assert before_close.calls == []

    monkeypatch.setattr(
        portfolio_job,
        "_market_now",
        lambda: datetime(2026, 7, 22, 15, 10, tzinfo=MARKET_TIMEZONE),
    )
    holiday = StubPortfolioClient(sessions, trading_day=False)
    closed = portfolio_job.snapshot_portfolio(_as_futu(holiday))
    assert closed["skipped"] == "non_trading_day"
    assert holiday.calls == ["request_trading_days"]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(PortfolioSnapshot)) == 0


def test_snapshot_job_keeps_missing_benchmark_null_for_later_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sessions = TrackedSessionFactory(tmp_path / "degraded.db")
    _seed_job_security(sessions)
    client = StubPortfolioClient(sessions, benchmark_available=False)
    monkeypatch.setattr(portfolio_job, "get_session", sessions)
    monkeypatch.setattr(
        portfolio_job,
        "_market_now",
        lambda: datetime(2026, 7, 22, 15, 10, tzinfo=MARKET_TIMEZONE),
    )

    stats = portfolio_job.snapshot_portfolio(_as_futu(client))

    assert stats["inserted"] == 1
    assert stats["benchmark_bar"] is None
    assert stats["warning_count"] == 1
    with sessions() as session:
        snapshot = session.get(PortfolioSnapshot, THIRD_DAY)
        assert snapshot is not None
        assert snapshot.benchmark_return is None
        assert session.scalar(select(func.count()).select_from(DailyBar)) == 0


def test_snapshot_job_broker_failure_writes_no_partial_database_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sessions = TrackedSessionFactory(tmp_path / "failure.db")
    client = StubPortfolioClient(sessions, funds_error=True)
    monkeypatch.setattr(portfolio_job, "get_session", sessions)
    monkeypatch.setattr(
        portfolio_job,
        "_market_now",
        lambda: datetime(2026, 7, 22, 15, 10, tzinfo=MARKET_TIMEZONE),
    )

    with pytest.raises(JobExecutionError) as captured:
        portfolio_job.snapshot_portfolio(_as_futu(client))

    assert captured.value.stats["failure_type"] == "FutuUnavailableError"
    assert "acc_id" not in repr(captured.value.stats)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(PortfolioSnapshot)) == 0
        assert session.scalar(select(func.count()).select_from(DailyBar)) == 0


def test_close_reconciliation_backfills_missing_benchmark_without_opening_account(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    sessions = TrackedSessionFactory(tmp_path / "reconcile.db")
    with sessions() as session:
        session.add(_bar(FIRST_DAY, 99.0))
        session.add_all(
            [
                PortfolioSnapshot(
                    trade_date=FIRST_DAY,
                    total_value=1_000.0,
                    cash=1_000.0,
                    positions=[],
                ),
                PortfolioSnapshot(
                    trade_date=SECOND_DAY,
                    total_value=1_010.0,
                    cash=1_010.0,
                    positions=[],
                ),
            ]
        )
    client = StubPortfolioClient(sessions, target=SECOND_DAY)
    monkeypatch.setattr(portfolio_job, "get_session", sessions)
    monkeypatch.setattr(
        portfolio_job,
        "_market_now",
        lambda: datetime(2026, 7, 22, 19, 0, tzinfo=MARKET_TIMEZONE),
    )

    stats = portfolio_job.reconcile_portfolio_benchmark(
        _as_futu(client),
        trade_date=SECOND_DAY,
    )

    assert stats["benchmark_bar"] == "inserted"
    assert stats["snapshot_exists"] is True
    assert client.calls == ["request_trading_days", "request_history_kline"]
    with sessions() as session:
        second = session.get(PortfolioSnapshot, SECOND_DAY)
        assert second is not None
        assert second.daily_return == pytest.approx(0.01)
        assert second.benchmark_return == pytest.approx(100.0 / 99.0 - 1.0)


def test_portfolio_jobs_are_registered_at_close_and_reconciliation_times() -> None:
    portfolio_job.register_portfolio_jobs()
    snapshot = JOBS["snapshot_portfolio"]
    reconciliation = JOBS["reconcile_portfolio_benchmark"]
    assert snapshot.enabled_key == reconciliation.enabled_key == "paper_trading_enabled"
    assert "day_of_week='mon-fri'" in str(snapshot.trigger)
    assert "hour='15'" in str(snapshot.trigger)
    assert "minute='10'" in str(snapshot.trigger)
    assert "hour='19'" in str(reconciliation.trigger)
    assert "minute='0'" in str(reconciliation.trigger)


def _api(engine: Engine) -> TestClient:
    app = FastAPI()
    app.include_router(portfolio_routes.router)

    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            yield session

    def forbidden_futu() -> FutuClient:
        raise AssertionError("persisted portfolio endpoints must not open Futu")

    app.dependency_overrides[db_session_dependency] = override_session
    app.dependency_overrides[futu_client_dependency] = forbidden_futu
    return TestClient(app)


def test_portfolio_api_empty_state_and_persisted_views_never_depend_on_opend(
    engine: Engine,
) -> None:
    with _api(engine) as api:
        empty_overview = api.get("/v1/portfolio/overview")
        empty_attribution = api.get("/v1/portfolio/attribution?days=60")
        invalid_days = api.get("/v1/portfolio/attribution?days=0")
    assert empty_overview.status_code == 200
    assert empty_overview.json()["available"] is False
    assert empty_overview.json()["snapshot"] is None
    assert empty_attribution.status_code == 200
    assert empty_attribution.json()["dates"] == []
    assert invalid_days.status_code == 422

    with Session(engine) as session:
        _seed_security_and_bars(session)
        for trade_day, total in (
            (FIRST_DAY, 1_000.0),
            (SECOND_DAY, 1_020.0),
            (THIRD_DAY, 990.0),
        ):
            upsert_portfolio_snapshot(
                session,
                trade_day,
                _funds(total, total - 100.0, 100.0),
                _positions(),
            )
        session.commit()

    with _api(engine) as api:
        overview = api.get("/v1/portfolio/overview")
        attribution = api.get("/v1/portfolio/attribution?days=2")
    assert overview.status_code == 200
    assert overview.json()["snapshot"]["trade_date"] == THIRD_DAY.isoformat()
    assert attribution.status_code == 200
    body = attribution.json()
    assert body["available_days"] == 2
    assert len(body["dates"]) == len(body["nav"]) == len(body["benchmark_nav"]) == 2


def test_corrupted_snapshot_is_a_chinese_503_not_a_json_nan(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(
            PortfolioSnapshot(
                trade_date=FIRST_DAY,
                total_value=1_000.0,
                cash=900.0,
                positions=[{"symbol": "bad", "qty": 1.0, "mv": 1.0}],
            )
        )
        session.commit()

    with _api(engine) as api:
        response = api.get("/v1/portfolio/overview")
    assert response.status_code == 503
    assert "无效股票代码" in response.json()["detail"]


def test_non_finite_snapshot_metric_is_a_chinese_503(engine: Engine) -> None:
    with Session(engine) as session:
        session.add(
            PortfolioSnapshot(
                trade_date=FIRST_DAY,
                total_value=1_000.0,
                cash=1_000.0,
                positions=[],
                excess_return=float("inf"),
            )
        )
        session.commit()

    with _api(engine) as api:
        response = api.get("/v1/portfolio/overview")
    assert response.status_code == 503
    assert "excess_return" in response.json()["detail"]


def test_invalid_benchmark_ohlc_fails_closed(engine: Engine) -> None:
    with Session(engine) as session, pytest.raises(PortfolioServiceError, match="OHLC"):
        upsert_benchmark_close_bar(
            session,
            THIRD_DAY,
            {
                "date": THIRD_DAY,
                "open": 100.0,
                "high": 99.0,
                "low": 98.0,
                "close": 100.0,
                "volume": 1.0,
                "amount": 1.0,
            },
            source="futu-close",
        )
