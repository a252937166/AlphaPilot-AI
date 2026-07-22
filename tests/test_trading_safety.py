from __future__ import annotations

import ast
import inspect
import textwrap
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from tests.test_futu_client import FakeFutuModule, FakeTradeContext
from tests.test_phase2_executor import (
    StubExecutionClient,
    _as_futu,
    _record,
    _settings,
)

from alphapilot.api.dependencies import db_session_dependency, futu_client_dependency
from alphapilot.api.routes import trades
from alphapilot.core.config import Settings
from alphapilot.db.models import (
    Base,
    BrokerOrder,
    RuntimeFlag,
    Security,
    TradeProposalRecord,
)
from alphapilot.futu.client import (
    PERMANENTLY_BLOCKED_METHODS,
    TRADE_MUTATION_METHODS,
    FutuClient,
    FutuFeatureDisabledError,
    FutuMethodNotAllowedError,
)
from alphapilot.services import executor
from alphapilot.services.runtime_flags import (
    TRADING_HALTED,
    initialize_runtime_flags,
    set_trading_halted,
)


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    database = create_engine(
        f"sqlite:///{tmp_path / 'trading-safety.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(database)
    with Session(database) as session:
        session.add(Security(symbol="600000", industry_csrc="银行"))
        session.commit()
    return database


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as database_session:
        yield database_session


def test_paper_switch_off_blocks_before_any_futu_call(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    monkeypatch.setattr(
        executor,
        "get_settings",
        lambda: _settings(paper_trading_enabled=False),
    )
    record = _record(session, proposal_id="safety-paper-off")
    client = StubExecutionClient()

    with pytest.raises(executor.ExecutionBlocked, match="模拟交易执行器未启用"):
        executor.execute_proposal(session, _as_futu(client), record)
    assert client.calls == []


def test_config_halt_is_initial_default_and_persisted_resume_overrides_it(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    settings = _settings(trading_halted=True)
    monkeypatch.setattr(executor, "get_settings", lambda: settings)
    blocked_record = _record(session, proposal_id="safety-config-halt")
    client = StubExecutionClient()

    with pytest.raises(executor.ExecutionBlocked) as caught:
        executor.execute_proposal(session, _as_futu(client), blocked_record)
    assert caught.value.halted is True
    assert client.calls == []

    set_trading_halted(session, False)
    session.commit()
    resumed_record = _record(session, proposal_id="safety-runtime-resume")
    order = executor.execute_proposal(session, _as_futu(client), resumed_record)
    assert order.environment == "SIMULATE"
    assert client.place_count == 1


def test_runtime_flag_initialization_never_overwrites_persisted_operator_state(
    session: Session,
) -> None:
    seeded = initialize_runtime_flags(session, _settings(trading_halted=True))
    session.commit()
    assert seeded.value is True

    preserved = initialize_runtime_flags(session, _settings(trading_halted=False))
    session.commit()
    assert preserved.value is True
    assert session.scalar(select(func.count()).select_from(RuntimeFlag)) == 1


def test_futu_real_order_still_requires_live_flag_and_unlock_is_permanent() -> None:
    disabled_trade = FutuClient(
        Settings(futu_enable_trade=False, live_trading_enabled=True),
        sdk_module=FakeFutuModule,
    )
    with pytest.raises(FutuFeatureDisabledError, match="mutation is disabled"):
        disabled_trade.trade_call(
            "security",
            "place_order",
            args=[100.0, 1, "HK.00700", "BUY"],
            environment="REAL",
            confirmation="SUBMIT_REAL_ORDER",
        )

    disabled_live = FutuClient(
        Settings(futu_enable_trade=True, live_trading_enabled=False),
        sdk_module=FakeFutuModule,
    )
    with pytest.raises(FutuFeatureDisabledError, match="Live trading is disabled"):
        disabled_live.trade_call(
            "security",
            "place_order",
            args=[100.0, 1, "HK.00700", "BUY"],
            environment="REAL",
            confirmation="SUBMIT_REAL_ORDER",
        )

    all_live_flags = FutuClient(
        Settings(futu_enable_trade=True, live_trading_enabled=True),
        sdk_module=FakeFutuModule,
    )
    with pytest.raises(FutuMethodNotAllowedError, match="never exposed"):
        all_live_flags.trade_call("security", "unlock_trade")
    for confirmation in (None, "WRONG_CONFIRMATION"):
        with pytest.raises(FutuFeatureDisabledError, match="confirmation"):
            all_live_flags.trade_call(
                "security",
                "place_order",
                args=[100.0, 1, "HK.00700", "BUY"],
                environment="REAL",
                confirmation=confirmation,
            )
    assert "unlock_trade" in PERMANENTLY_BLOCKED_METHODS
    assert PERMANENTLY_BLOCKED_METHODS.isdisjoint(TRADE_MUTATION_METHODS)


def test_executor_source_and_runtime_can_only_submit_simulate(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    source = textwrap.dedent(inspect.getsource(executor.execute_proposal))
    tree = ast.parse(source)
    environments: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "trade_call":
            continue
        for keyword in node.keywords:
            if keyword.arg == "environment" and isinstance(keyword.value, ast.Constant):
                environments.append(str(keyword.value.value))
    assert environments == ["SIMULATE"]
    assert '"REAL"' not in source and "'REAL'" not in source

    monkeypatch.setattr(
        executor,
        "get_settings",
        lambda: _settings(live_trading_enabled=True),
    )
    record = _record(session, proposal_id="safety-simulate-only")
    client = StubExecutionClient()
    order = executor.execute_proposal(session, _as_futu(client), record)
    place = next(call for call in client.calls if call["method"] == "place_order")
    assert order.environment == "SIMULATE"
    assert place["environment"] == "SIMULATE"
    assert place["confirmation"] is None


class ExecutorFakeTradeContext(FakeTradeContext):
    def get_acc_list(self) -> tuple[int, pd.DataFrame]:
        return 0, pd.DataFrame(
            [
                {
                    "acc_id": 101,
                    "trd_env": "SIMULATE",
                    "sim_acc_type": "STOCK",
                    "acc_status": "ACTIVE",
                    "trdmarket_auth": ["CN"],
                }
            ]
        )

    def accinfo_query(
        self,
        acc_id: int,
        refresh_cache: bool,
        trd_env: str = "REAL",
    ) -> tuple[int, pd.DataFrame]:
        del acc_id, refresh_cache
        self.last_environment = trd_env
        return 0, pd.DataFrame([{"total_assets": 1_000_000, "cash": 1_000_000, "market_val": 0}])

    def position_list_query(
        self,
        acc_id: int,
        refresh_cache: bool,
        trd_env: str = "REAL",
    ) -> tuple[int, pd.DataFrame]:
        del acc_id, refresh_cache
        self.last_environment = trd_env
        return 0, pd.DataFrame([])

    def order_list_query(
        self,
        acc_id: int,
        code: str,
        refresh_cache: bool,
        trd_env: str = "REAL",
    ) -> tuple[int, pd.DataFrame]:
        del acc_id, code, refresh_cache
        self.last_environment = trd_env
        return 0, pd.DataFrame([])

    def place_order(
        self,
        price: float,
        qty: float,
        code: str,
        trd_side: str,
        order_type: str,
        acc_id: int,
        remark: str,
        trd_env: str = "REAL",
    ) -> tuple[int, pd.DataFrame]:
        del price, qty, order_type, acc_id, remark
        self.last_environment = trd_env
        return 0, pd.DataFrame(
            [
                {
                    "order_id": "fake-sdk-paper-1",
                    "trd_env": trd_env,
                    "code": code,
                    "trd_side": trd_side,
                }
            ]
        )


class ExecutorFakeFutuModule(FakeFutuModule):
    OpenSecTradeContext = ExecutorFakeTradeContext


def test_executor_reaches_fake_futu_sdk_with_simulate_environment(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    settings = _settings(live_trading_enabled=True)
    monkeypatch.setattr(executor, "get_settings", lambda: settings)
    client = FutuClient(settings, sdk_module=ExecutorFakeFutuModule)
    record = _record(session, proposal_id="safety-real-futu-client")

    order = executor.execute_proposal(session, client, record)
    context = FakeTradeContext.last_instance
    assert order.environment == "SIMULATE"
    assert order.futu_order_id == "fake-sdk-paper-1"
    assert context is not None
    assert context.last_environment == "SIMULATE"
    assert context.closed is True


def test_same_proposal_never_places_a_second_order(
    monkeypatch: pytest.MonkeyPatch,
    session: Session,
) -> None:
    monkeypatch.setattr(executor, "get_settings", lambda: _settings())
    record = _record(session, proposal_id="safety-idempotent")
    client = StubExecutionClient()

    first = executor.execute_proposal(session, _as_futu(client), record)
    second = executor.execute_proposal(session, _as_futu(client), record)
    assert first.id == second.id
    assert client.place_count == 1
    assert session.scalar(select(func.count()).select_from(BrokerOrder)) == 1


def test_halt_and_resume_api_persist_and_halt_precedes_idempotent_replay(
    monkeypatch: pytest.MonkeyPatch,
    engine: Engine,
) -> None:
    settings = _settings()
    monkeypatch.setattr(executor, "get_settings", lambda: settings)
    monkeypatch.setattr(trades, "get_settings", lambda: settings)
    broker_client = StubExecutionClient()
    with Session(engine, expire_on_commit=False) as setup:
        record = _record(setup, proposal_id="safety-api")
        record_id = record.id

    app = FastAPI()
    app.include_router(trades.router)

    def override_session() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as database_session:
            try:
                yield database_session
                database_session.commit()
            except Exception:
                database_session.rollback()
                raise

    app.dependency_overrides[db_session_dependency] = override_session
    app.dependency_overrides[futu_client_dependency] = lambda: _as_futu(broker_client)

    with TestClient(app) as api:
        halted = api.post("/v1/trades/halt")
        assert halted.status_code == 200
        assert halted.json()["trading_halted"] is True
        assert halted.json()["source"] == "runtime_flags"
        assert halted.json()["updated_at"].endswith("+00:00")
        blocked = api.post(f"/v1/trades/proposals/{record_id}/execute")
        assert blocked.status_code == 423
        assert "Kill Switch" in blocked.json()["detail"]
        assert broker_client.calls == []

        resumed = api.post("/v1/trades/resume")
        assert resumed.status_code == 200
        assert resumed.json()["trading_halted"] is False
        submitted = api.post(f"/v1/trades/proposals/{record_id}/execute")
        assert submitted.status_code == 200
        order_id = submitted.json()["order"]["id"]
        assert broker_client.place_count == 1

        assert api.post("/v1/trades/halt").status_code == 200
        blocked_replay = api.post(f"/v1/trades/proposals/{record_id}/execute")
        assert blocked_replay.status_code == 423

    with Session(engine) as verify:
        flag = verify.get(RuntimeFlag, TRADING_HALTED)
        assert flag is not None and flag.value is True
        assert verify.scalar(select(func.count()).select_from(RuntimeFlag)) == 1
        order = verify.get(BrokerOrder, order_id)
        assert order is not None and order.environment == "SIMULATE"
    assert broker_client.place_count == 1


class BlockingPlaceClient(StubExecutionClient):
    def __init__(self) -> None:
        super().__init__()
        self.place_entered = Event()
        self.release_place = Event()

    def trade_call(self, *args: object, **kwargs: object) -> dict[str, object]:
        response = super().trade_call(*args, **kwargs)  # type: ignore[arg-type]
        if len(args) >= 2 and args[1] == "place_order":
            self.place_entered.set()
            if not self.release_place.wait(timeout=5):
                raise AssertionError("test did not release the simulated place_order")
        return response


def test_halt_is_linearized_with_an_inflight_submission(
    monkeypatch: pytest.MonkeyPatch,
    engine: Engine,
) -> None:
    settings = _settings()
    monkeypatch.setattr(executor, "get_settings", lambda: settings)
    client = BlockingPlaceClient()
    with Session(engine, expire_on_commit=False) as setup:
        first = _record(setup, proposal_id="safety-inflight-first")
        second = _record(setup, proposal_id="safety-inflight-second")
        first_id, second_id = first.id, second.id
    halt_started = Event()
    halt_finished = Event()

    def submit_first() -> int:
        with Session(engine, expire_on_commit=False) as database_session:
            record = database_session.get(TradeProposalRecord, first_id)
            assert record is not None
            return executor.execute_proposal(
                database_session,
                _as_futu(client),
                record,
            ).id

    def halt() -> None:
        halt_started.set()
        with Session(engine, expire_on_commit=False) as database_session:
            trades._set_halt_state(database_session, True)
        halt_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        submit_future = pool.submit(submit_first)
        assert client.place_entered.wait(timeout=5)
        halt_future = pool.submit(halt)
        assert halt_started.wait(timeout=5)
        assert not halt_finished.wait(timeout=0.1)
        client.release_place.set()
        assert submit_future.result(timeout=5) > 0
        halt_future.result(timeout=5)
        assert halt_finished.is_set()

    with Session(engine, expire_on_commit=False) as database_session:
        second_record = database_session.get(TradeProposalRecord, second_id)
        assert second_record is not None
        with pytest.raises(executor.ExecutionBlocked) as caught:
            executor.execute_proposal(
                database_session,
                _as_futu(client),
                second_record,
            )
        assert caught.value.halted is True
    assert client.place_count == 1
