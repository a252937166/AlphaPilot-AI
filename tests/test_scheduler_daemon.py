from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from alphapilot import scheduler_main
from alphapilot.core.config import Settings


def _safe_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "scheduler_enabled": True,
        "trading_mode": "research",
        "live_trading_enabled": False,
        "paper_auto_trading_enabled": False,
        "futu_enable_account_mutation": False,
        "baostock_financial_sync_enabled": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("scheduler_enabled", False, "scheduler_enabled"),
        ("trading_mode", "paper", "trading_mode"),
        ("live_trading_enabled", True, "live_trading_enabled"),
        (
            "paper_auto_trading_enabled",
            True,
            "paper_auto_trading_enabled",
        ),
        (
            "futu_enable_account_mutation",
            True,
            "futu_enable_account_mutation",
        ),
        (
            "baostock_financial_sync_enabled",
            True,
            "baostock_financial_sync_enabled",
        ),
    ],
)
def test_scheduler_settings_fail_closed(
    field: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(
        scheduler_main.SchedulerConfigurationError,
        match=message,
    ):
        scheduler_main.validate_scheduler_settings(_safe_settings(**{field: value}))


def test_scheduler_daemon_owns_lifecycle_and_graceful_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []
    stop_event = Mock()
    fake_client = Mock()
    fake_scheduler = SimpleNamespace(
        get_jobs=lambda: [SimpleNamespace(id="sync_orders"), SimpleNamespace(id="poll")]
    )

    @contextmanager
    def lock(*_args: object, **_kwargs: object) -> Iterator[None]:
        calls.append(("lock", "nonblocking"))
        yield

    monkeypatch.setattr(scheduler_main, "assert_database_ready", lambda: None)
    monkeypatch.setattr(
        scheduler_main,
        "register_builtin_jobs",
        lambda: calls.append(("register", True)),
    )
    monkeypatch.setattr(scheduler_main, "scheduler_process_lock", lock)
    monkeypatch.setattr(
        scheduler_main,
        "start_scheduler",
        lambda _settings: fake_scheduler,
    )
    monkeypatch.setattr(
        scheduler_main,
        "shutdown_scheduler",
        lambda *, wait: calls.append(("shutdown_wait", wait)),
    )
    monkeypatch.setattr(
        scheduler_main,
        "get_futu_client",
        lambda: fake_client,
    )

    scheduler_main.run_scheduler_daemon(
        settings=_safe_settings(),
        stop_event=stop_event,
    )

    stop_event.wait.assert_called_once_with()
    fake_client.close.assert_called_once_with()
    assert ("register", True) in calls
    assert ("lock", "nonblocking") in calls
    assert ("shutdown_wait", True) in calls
