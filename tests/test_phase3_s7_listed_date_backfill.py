from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pandas as pd
import pytest

from alphapilot.db import listed_date_backfill as backfill_module
from alphapilot.db.backup import create_database_backup
from alphapilot.db.listed_date_backfill import (
    ListedDateBackfillError,
    backfill_security_listed_dates,
    fetch_tushare_bse_stock_basic,
)


class _FutuStub:
    def __init__(
        self,
        *,
        sh_rows: list[dict[str, Any]] | None = None,
        sz_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.rows = {
            "SH": sh_rows
            or [
                {
                    "code": "SH.600519",
                    "listing_date": "2001-08-27",
                    "stock_type": "STOCK",
                }
            ],
            "SZ": sz_rows
            or [
                {
                    "code": "SZ.000001",
                    "listing_date": "1991-04-03",
                    "stock_type": "STOCK",
                }
            ],
        }
        self.calls: list[dict[str, Any]] = []

    def quote_call_raw(
        self,
        method: str,
        args: list[Any] | None = None,
        kwargs: Mapping[str, Any] | None = None,
    ) -> Any:
        assert method == "get_stock_basicinfo"
        assert args is None
        assert kwargs is not None
        market_constant = kwargs["market"]
        stock_type_constant = kwargs["stock_type"]
        assert isinstance(market_constant, Mapping)
        assert isinstance(stock_type_constant, Mapping)
        market = str(market_constant["__futu_constant__"]).removeprefix("Market.")
        assert stock_type_constant == {"__futu_constant__": "SecurityType.STOCK"}
        self.calls.append({"method": method, "market": market})
        return pd.DataFrame(self.rows[market])


class _ForbiddenFutu:
    def quote_call_raw(
        self,
        method: str,
        args: list[Any] | None = None,
        kwargs: Mapping[str, Any] | None = None,
    ) -> Any:
        raise AssertionError("running-job gate must block before opening Futu")


class _TushareStub:
    def __init__(
        self,
        *,
        rows: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.rows = (
            rows
            if rows is not None
            else [
                {
                    "ts_code": "920344.BJ",
                    "symbol": "920344",
                    "exchange": "BSE",
                    "list_status": "L",
                    "list_date": "20241115",
                }
            ]
        )
        self.error = error
        self.calls: list[str] = []

    def __call__(self, token: str) -> pd.DataFrame:
        assert token == "test-token"
        self.calls.append("stock_basic")
        if self.error is not None:
            raise self.error
        return pd.DataFrame(self.rows)


def _authority_arguments(
    tushare: _TushareStub | None = None,
) -> dict[str, Any]:
    stub = tushare or _TushareStub()
    return {
        "tushare_token": "test-token",
        "tushare_stock_basic_fetcher": stub,
    }


def _database(path: Path, *, running: bool = False) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE securities (
                symbol TEXT PRIMARY KEY,
                market TEXT NOT NULL,
                list_status TEXT,
                name TEXT,
                listed_date TEXT,
                profile TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE trade_proposals (id INTEGER PRIMARY KEY);
            CREATE TABLE broker_orders (id INTEGER PRIMARY KEY);
            CREATE TABLE runtime_flags (
                key TEXT PRIMARY KEY,
                value BOOLEAN NOT NULL
            );
            CREATE TABLE job_runs (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL
            );

            INSERT INTO securities
                (symbol, market, list_status, name, listed_date, profile, updated_at)
            VALUES
                ('600519', 'CN', 'listed', '贵州茅台', NULL,
                 '{"keep":"sh"}', '2026-07-31T00:00:00Z'),
                ('000001', 'CN', 'listed', '平安银行', '1991-04-03',
                 '{"keep":"sz"}', '2026-07-31T00:00:00Z'),
                ('920344', 'CN', 'listed', '北交所样本', NULL,
                 '{"keep":"bse"}', '2026-07-31T00:00:00Z');

            INSERT INTO trade_proposals (id) VALUES (1);
            INSERT INTO broker_orders (id) VALUES (1);
            INSERT INTO runtime_flags (key, value) VALUES ('trading_halted', 1);
            INSERT INTO job_runs (id, status) VALUES (1, 'ok');
            """
        )
        if running:
            connection.execute("INSERT INTO job_runs (id, status) VALUES (2, 'running')")
        connection.commit()
    finally:
        connection.close()


def _security_rows(path: Path) -> list[tuple[Any, ...]]:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            """
            SELECT symbol, market, name, listed_date, profile, updated_at
            FROM securities
            ORDER BY symbol
            """
        ).fetchall()


def test_listed_date_backfill_defaults_to_read_only_dry_run(tmp_path: Path) -> None:
    database = tmp_path / "alphapilot.db"
    evidence = tmp_path / "dry-run.json"
    _database(database)
    before = _security_rows(database)
    client = _FutuStub()
    tushare = _TushareStub()

    result = backfill_security_listed_dates(
        database_path=database,
        backup_directory=tmp_path / "backups",
        evidence_path=evidence,
        client=client,
        **_authority_arguments(tushare),
        as_of_date=date(2026, 8, 1),
    )

    assert result["status"] == "dry_run"
    assert result["updated_rows"] == 0
    assert result["checks"]["missing_values_to_update"] == 2
    assert result["checks"]["existing_values_checked"] == 1
    assert result["checks"]["current_existing_value_equality"] is True
    assert result["application_safety"]["trading_mode"] == "research"
    assert result["application_safety"]["live_trading_enabled"] is False
    assert result["application_safety"]["paper_auto_trading_enabled"] is False
    assert result["application_safety"]["unlock_trade_permanently_blocked"] is True
    assert result["candidates"] == [
        {
            "symbol": "600519",
            "authority": "futu",
            "authority_code": "SH.600519",
            "old_listed_date": None,
            "new_listed_date": "2001-08-27",
        },
        {
            "symbol": "920344",
            "authority": "tushare",
            "authority_code": "920344.BJ",
            "old_listed_date": None,
            "new_listed_date": "2024-11-15",
        },
    ]
    assert client.calls == [
        {"method": "get_stock_basicinfo", "market": "SH"},
        {"method": "get_stock_basicinfo", "market": "SZ"},
    ]
    assert tushare.calls == ["stock_basic"]
    assert result["provider"]["calls"] == {
        "futu_quote_calls": 2,
        "tushare_api_calls": 1,
        "total_read_only_calls": 3,
        "retry_attempts": 0,
    }
    assert result["provider"]["futu"]["quote_calls"] == 2
    assert result["provider"]["tushare"]["api_calls"] == 1
    assert len(result["provider"]["futu"]["normalized_sha256"]) == 64
    assert len(result["provider"]["tushare"]["normalized_sha256"]) == 64
    assert "test-token" not in evidence.read_text(encoding="utf-8")
    assert _security_rows(database) == before
    assert not (tmp_path / "backups").exists()
    assert json.loads(evidence.read_text(encoding="utf-8"))["status"] == "dry_run"


def test_listed_date_backfill_applies_after_verified_backup_and_preserves_rows(
    tmp_path: Path,
) -> None:
    database = tmp_path / "alphapilot.db"
    evidence = tmp_path / "applied.json"
    backup_directory = tmp_path / "backups"
    _database(database)
    before = _security_rows(database)

    result = backfill_security_listed_dates(
        database_path=database,
        backup_directory=backup_directory,
        evidence_path=evidence,
        client=_FutuStub(),
        **_authority_arguments(),
        apply=True,
        as_of_date=date(2026, 8, 1),
    )

    assert result["status"] == "applied"
    assert result["updated_rows"] == 2
    assert result["quick_check_after"] == "ok"
    assert (
        result["safety_before"]
        == result["safety_after"]
        == {
            "trade_proposals": 1,
            "broker_orders": 1,
            "running_job_runs": 0,
        }
    )
    assert result["backup"]["independent_verification"]["verified"] is True
    assert result["backup"]["securities_snapshot"]["matches_preflight"] is True
    assert result["backup"]["securities_snapshot"]["rows"] == 3
    assert len(result["backup"]["securities_snapshot"]["sha256"]) == 64
    assert result["mutation_committed"] is True
    assert result["stage"] == "committed"
    backup_path = Path(result["backup"]["backup_path"])
    manifest_path = Path(result["backup"]["manifest_path"])
    assert backup_path.is_file()
    assert manifest_path.is_file()

    after = _security_rows(database)
    expected = list(before)
    target = list(expected[1])
    assert target[0] == "600519"
    target[3] = "2001-08-27"
    expected[1] = tuple(target)
    bse_target = list(expected[2])
    assert bse_target[0] == "920344"
    bse_target[3] = "2024-11-15"
    expected[2] = tuple(bse_target)
    assert after == expected
    assert after[0][4:] == before[0][4:]
    assert after[1][4:] == before[1][4:]
    assert after[2][4:] == before[2][4:]

    second = backfill_security_listed_dates(
        database_path=database,
        backup_directory=backup_directory,
        evidence_path=tmp_path / "idempotent.json",
        client=_FutuStub(),
        **_authority_arguments(),
        apply=True,
        as_of_date=date(2026, 8, 1),
    )
    assert second["status"] == "already_complete"
    assert second["updated_rows"] == 0
    assert second["backup"] is None
    assert len(list(backup_directory.glob("alphapilot-full-*.db"))) == 1


def test_existing_value_mismatch_blocks_before_backup(tmp_path: Path) -> None:
    database = tmp_path / "alphapilot.db"
    evidence = tmp_path / "blocked.json"
    backup_directory = tmp_path / "backups"
    _database(database)
    before = _security_rows(database)
    client = _FutuStub(
        sz_rows=[
            {
                "code": "SZ.000001",
                "listing_date": "1991-04-04",
                "stock_type": "STOCK",
            }
        ]
    )

    with pytest.raises(ListedDateBackfillError, match="complete, equal"):
        backfill_security_listed_dates(
            database_path=database,
            backup_directory=backup_directory,
            evidence_path=evidence,
            client=client,
            **_authority_arguments(),
            apply=True,
            as_of_date=date(2026, 8, 1),
        )

    assert _security_rows(database) == before
    assert not backup_directory.exists()
    blocked = json.loads(evidence.read_text(encoding="utf-8"))
    assert blocked["status"] == "blocked"
    assert blocked["details"]["existing_value_mismatches"] == [
        {
            "symbol": "000001",
            "authority": "futu",
            "authority_code": "SZ.000001",
            "database_listed_date": "1991-04-03",
            "authority_listed_date": "1991-04-04",
        }
    ]


def test_non_target_futu_sentinel_date_does_not_block_target_validation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "alphapilot.db"
    _database(database)
    client = _FutuStub(
        sh_rows=[
            {
                "code": "SH.600519",
                "listing_date": "2001-08-27",
                "stock_type": "STOCK",
            },
            {
                "code": "SH.600349",
                "listing_date": "1970-01-01",
                "stock_type": "STOCK",
            },
        ]
    )

    result = backfill_security_listed_dates(
        database_path=database,
        backup_directory=tmp_path / "backups",
        evidence_path=tmp_path / "dry-run.json",
        client=client,
        **_authority_arguments(),
        as_of_date=date(2026, 8, 1),
    )

    assert result["status"] == "dry_run"
    assert result["provider"]["futu"]["target_symbols"] == 2
    assert result["provider"]["futu"]["target_rows_found"] == 2
    assert result["checks"]["missing_values_to_update"] == 2


def test_target_futu_sentinel_remains_an_explicit_unresolved_gap(
    tmp_path: Path,
) -> None:
    database = tmp_path / "alphapilot.db"
    _database(database)
    client = _FutuStub(
        sh_rows=[
            {
                "code": "SH.600519",
                "listing_date": "1970-01-01",
                "stock_type": "STOCK",
            }
        ]
    )

    result = backfill_security_listed_dates(
        database_path=database,
        backup_directory=tmp_path / "backups",
        evidence_path=tmp_path / "dry-run.json",
        client=client,
        **_authority_arguments(),
        as_of_date=date(2026, 8, 1),
    )

    assert result["status"] == "dry_run"
    assert result["updated_rows"] == 0
    assert result["checks"]["unresolved_symbols"] == ["600519"]
    assert result["provider"]["futu"]["unavailable_target_dates"] == [
        {
            "symbol": "600519",
            "futu_code": "SH.600519",
            "provider_value": "1970-01-01",
            "reason": "futu_sentinel_date",
        }
    ]
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT listed_date FROM securities WHERE symbol='600519'"
        ).fetchone() == (None,)


def test_futu_sentinel_cannot_bypass_existing_value_equality(
    tmp_path: Path,
) -> None:
    database = tmp_path / "alphapilot.db"
    _database(database)
    client = _FutuStub(
        sz_rows=[
            {
                "code": "SZ.000001",
                "listing_date": "1970-01-01",
                "stock_type": "STOCK",
            }
        ]
    )

    with pytest.raises(ListedDateBackfillError, match="complete, equal"):
        backfill_security_listed_dates(
            database_path=database,
            backup_directory=tmp_path / "backups",
            evidence_path=tmp_path / "blocked.json",
            client=client,
            **_authority_arguments(),
            as_of_date=date(2026, 8, 1),
        )

    blocked = json.loads((tmp_path / "blocked.json").read_text(encoding="utf-8"))
    assert blocked["details"]["existing_values_without_provider_authority"] == ["000001"]


def test_known_302132_futu_sentinel_is_explicitly_unresolved(
    tmp_path: Path,
) -> None:
    database = tmp_path / "alphapilot.db"
    _database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO securities
                (symbol, market, list_status, name, listed_date, profile, updated_at)
            VALUES
                ('302132', 'CN', 'listed', 'Futu 日期哨兵样本', NULL,
                 '{"keep":"sentinel"}', '2026-07-31T00:00:00Z')
            """
        )
        connection.commit()
    client = _FutuStub(
        sz_rows=[
            {
                "code": "SZ.000001",
                "listing_date": "1991-04-03",
                "stock_type": "STOCK",
            },
            {
                "code": "SZ.302132",
                "listing_date": "1970-01-01",
                "stock_type": "STOCK",
            },
        ]
    )

    result = backfill_security_listed_dates(
        database_path=database,
        backup_directory=tmp_path / "backups",
        evidence_path=tmp_path / "dry-run.json",
        client=client,
        **_authority_arguments(),
        as_of_date=date(2026, 8, 1),
    )

    assert result["checks"]["unresolved_symbols"] == ["302132"]
    assert result["provider"]["futu"]["unavailable_target_dates"] == [
        {
            "symbol": "302132",
            "futu_code": "SZ.302132",
            "provider_value": "1970-01-01",
            "reason": "futu_sentinel_date",
        }
    ]


def test_missing_bse_authority_is_allowed_only_for_missing_value(
    tmp_path: Path,
) -> None:
    database = tmp_path / "alphapilot.db"
    _database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE securities SET listed_date='2001-08-27' WHERE symbol='600519'")
        connection.commit()
    tushare = _TushareStub(
        rows=[
            {
                "ts_code": "430047.BJ",
                "symbol": "430047",
                "exchange": "BSE",
                "list_status": "L",
                "list_date": "20201103",
            }
        ]
    )

    result = backfill_security_listed_dates(
        database_path=database,
        backup_directory=tmp_path / "backups",
        evidence_path=tmp_path / "dry-run.json",
        client=_FutuStub(),
        **_authority_arguments(tushare),
        as_of_date=date(2026, 8, 1),
    )

    assert result["status"] == "complete_with_unresolved"
    assert result["checks"]["unresolved_symbols"] == ["920344"]
    assert result["provider"]["tushare"]["unresolved_target_symbols"] == ["920344"]


def test_existing_bse_value_must_match_tushare_authority(tmp_path: Path) -> None:
    database = tmp_path / "alphapilot.db"
    evidence = tmp_path / "blocked.json"
    _database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE securities SET listed_date='2024-11-14' WHERE symbol='920344'")
        connection.commit()

    with pytest.raises(ListedDateBackfillError, match="complete, equal"):
        backfill_security_listed_dates(
            database_path=database,
            backup_directory=tmp_path / "backups",
            evidence_path=evidence,
            client=_FutuStub(),
            **_authority_arguments(),
            as_of_date=date(2026, 8, 1),
        )

    details = json.loads(evidence.read_text(encoding="utf-8"))["details"]
    assert details["existing_value_mismatches"] == [
        {
            "symbol": "920344",
            "authority": "tushare",
            "authority_code": "920344.BJ",
            "database_listed_date": "2024-11-14",
            "authority_listed_date": "2024-11-15",
        }
    ]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("ts_code", "920344.SH", "six-digit .BJ"),
        ("symbol", "600519", "BSE A-share symbol"),
        ("exchange", "SSE", "exchange must be BSE"),
        ("list_status", "D", "list_status must be L"),
        ("list_date", "20240230", "valid calendar date"),
    ],
)
def test_malformed_tushare_bse_rows_fail_closed(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    database = tmp_path / "alphapilot.db"
    _database(database)
    row = {
        "ts_code": "920344.BJ",
        "symbol": "920344",
        "exchange": "BSE",
        "list_status": "L",
        "list_date": "20241115",
    }
    row[field] = value

    with pytest.raises(ListedDateBackfillError, match=message):
        backfill_security_listed_dates(
            database_path=database,
            backup_directory=tmp_path / "backups",
            evidence_path=tmp_path / "blocked.json",
            client=_FutuStub(),
            **_authority_arguments(_TushareStub(rows=[row])),
            as_of_date=date(2026, 8, 1),
        )


def test_tushare_token_and_business_errors_never_expose_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ListedDateBackfillError, match="token is required"):
        fetch_tushare_bse_stock_basic(" ")

    class _BusinessErrorClient:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False

        def __enter__(self) -> _BusinessErrorClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def post(self, url: str, **kwargs: Any) -> Any:
            assert url == "https://api.tushare.pro"
            assert kwargs["json"]["token"] == "super-secret-token"
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={"code": -2001, "msg": "permission denied"},
            )

    monkeypatch.setattr(
        "alphapilot.db.listed_date_backfill.httpx.Client",
        _BusinessErrorClient,
    )
    with pytest.raises(
        ListedDateBackfillError,
        match="business error",
    ) as caught:
        fetch_tushare_bse_stock_basic("super-secret-token")
    assert "super-secret-token" not in str(caught.value)
    assert "super-secret-token" not in json.dumps(caught.value.details)

    database = tmp_path / "alphapilot.db"
    evidence = tmp_path / "missing-token.json"
    _database(database)
    with pytest.raises(ListedDateBackfillError, match="token is required"):
        backfill_security_listed_dates(
            database_path=database,
            backup_directory=tmp_path / "backups",
            evidence_path=evidence,
            client=_ForbiddenFutu(),
            tushare_token="",
            tushare_stock_basic_fetcher=_TushareStub(),
            as_of_date=date(2026, 8, 1),
        )
    assert "super-secret-token" not in evidence.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("sh_rows", "message"),
    [
        (
            [
                {
                    "code": "SZ.600519",
                    "listing_date": "2001-08-27",
                    "stock_type": "STOCK",
                }
            ],
            "outside the requested",
        ),
        (
            [
                {
                    "code": "SH.600519",
                    "listing_date": "2001/08/27",
                    "stock_type": "STOCK",
                }
            ],
            "exact YYYY-MM-DD",
        ),
        (
            [
                {
                    "code": "SH.600519",
                    "listing_date": "2001-08-27",
                    "stock_type": "ETF",
                }
            ],
            "not SecurityType.STOCK",
        ),
        (
            [
                {
                    "code": "SH.600519",
                    "listing_date": "2001-08-27",
                    "stock_type": "STOCK",
                },
                {
                    "code": "SH.600519",
                    "listing_date": "2001-08-27",
                    "stock_type": "STOCK",
                },
            ],
            "duplicate",
        ),
    ],
)
def test_invalid_futu_rows_fail_closed(
    tmp_path: Path,
    sh_rows: list[dict[str, Any]],
    message: str,
) -> None:
    database = tmp_path / "alphapilot.db"
    _database(database)
    before = _security_rows(database)

    with pytest.raises(ListedDateBackfillError, match=message):
        backfill_security_listed_dates(
            database_path=database,
            backup_directory=tmp_path / "backups",
            evidence_path=tmp_path / "blocked.json",
            client=_FutuStub(sh_rows=sh_rows),
            **_authority_arguments(),
            apply=True,
            as_of_date=date(2026, 8, 1),
        )

    assert _security_rows(database) == before
    assert not (tmp_path / "backups").exists()


def test_running_job_gate_blocks_before_futu_or_backup(tmp_path: Path) -> None:
    database = tmp_path / "alphapilot.db"
    evidence = tmp_path / "running.json"
    _database(database, running=True)

    with pytest.raises(ListedDateBackfillError, match="running JobRun"):
        backfill_security_listed_dates(
            database_path=database,
            backup_directory=tmp_path / "backups",
            evidence_path=evidence,
            client=_ForbiddenFutu(),
            **_authority_arguments(),
            apply=True,
            as_of_date=date(2026, 8, 1),
        )

    assert not (tmp_path / "backups").exists()
    blocked = json.loads(evidence.read_text(encoding="utf-8"))
    assert blocked["status"] == "blocked"
    assert blocked["details"]["safety"]["running_job_runs"] == 1


def test_research_safety_gate_blocks_before_futu_or_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "alphapilot.db"
    evidence = tmp_path / "unsafe.json"
    _database(database)
    monkeypatch.setattr(
        backfill_module,
        "get_settings",
        lambda: SimpleNamespace(
            trading_mode="research",
            live_trading_enabled=True,
            paper_trading_enabled=False,
            paper_auto_trading_enabled=False,
            futu_enable_account_mutation=False,
            futu_enable_trade=False,
        ),
    )

    with pytest.raises(ListedDateBackfillError, match="safety gate"):
        backfill_security_listed_dates(
            database_path=database,
            backup_directory=tmp_path / "backups",
            evidence_path=evidence,
            client=_ForbiddenFutu(),
            **_authority_arguments(),
            apply=True,
            as_of_date=date(2026, 8, 1),
        )

    assert not (tmp_path / "backups").exists()
    blocked = json.loads(evidence.read_text(encoding="utf-8"))
    assert blocked["status"] == "blocked"
    assert blocked["details"]["blockers"] == ["live_trading_enabled"]


def test_security_drift_after_backup_blocks_missing_only_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "alphapilot.db"
    backup_directory = tmp_path / "backups"
    _database(database)

    def create_then_drift(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = create_database_backup(*args, **kwargs)
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE securities SET name = '并发漂移' WHERE symbol = '600519'")
            connection.commit()
        return result

    monkeypatch.setattr(
        backfill_module,
        "create_database_backup",
        create_then_drift,
    )
    with pytest.raises(
        ListedDateBackfillError,
        match="changed after authority validation",
    ):
        backfill_security_listed_dates(
            database_path=database,
            backup_directory=backup_directory,
            evidence_path=tmp_path / "drift.json",
            client=_FutuStub(),
            **_authority_arguments(),
            apply=True,
            as_of_date=date(2026, 8, 1),
        )

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name, listed_date FROM securities WHERE symbol = '600519'"
        ).fetchone() == ("并发漂移", None)
    assert len(list(backup_directory.glob("alphapilot-full-*.db"))) == 1
    blocked = json.loads((tmp_path / "drift.json").read_text(encoding="utf-8"))
    assert blocked["status"] == "blocked"
    assert blocked["stage"] == "prepared"
    assert blocked["mutation_committed"] is False
    assert blocked["backup"]["securities_snapshot"]["matches_preflight"] is True
    assert len(blocked["candidates"]) == 2


def test_final_evidence_failure_records_that_database_commit_succeeded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "alphapilot.db"
    evidence = tmp_path / "evidence.json"
    _database(database)
    real_write = backfill_module._atomic_write_json
    writes = 0

    def fail_only_final_write(
        path: Path,
        document: Mapping[str, Any],
    ) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("simulated final evidence failure")
        real_write(path, document)

    monkeypatch.setattr(
        backfill_module,
        "_atomic_write_json",
        fail_only_final_write,
    )

    with pytest.raises(OSError, match="simulated final evidence failure"):
        backfill_security_listed_dates(
            database_path=database,
            backup_directory=tmp_path / "backups",
            evidence_path=evidence,
            client=_FutuStub(),
            **_authority_arguments(),
            apply=True,
            as_of_date=date(2026, 8, 1),
        )

    assert writes == 3
    blocked = json.loads(evidence.read_text(encoding="utf-8"))
    assert blocked["status"] == "blocked_after_commit"
    assert blocked["stage"] == "committed"
    assert blocked["mutation_committed"] is True
    assert blocked["backup"]["securities_snapshot"]["matches_preflight"] is True
    assert len(blocked["candidates"]) == 2
    with sqlite3.connect(database) as connection:
        values = dict(connection.execute("SELECT symbol, listed_date FROM securities").fetchall())
    assert values["600519"] == "2001-08-27"
    assert values["920344"] == "2024-11-15"


@pytest.mark.parametrize("alias_kind", ["database", "wal", "hardlink"])
def test_evidence_path_cannot_alias_live_sqlite_files(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    database = tmp_path / "alphapilot.db"
    _database(database)
    before = database.read_bytes()
    if alias_kind == "database":
        evidence = database
    elif alias_kind == "wal":
        evidence = database.with_name(f"{database.name}-wal")
    else:
        evidence = tmp_path / "database-hardlink"
        os.link(database, evidence)

    with pytest.raises(ListedDateBackfillError, match="alias"):
        backfill_security_listed_dates(
            database_path=database,
            backup_directory=tmp_path / "backups",
            evidence_path=evidence,
            client=_ForbiddenFutu(),
            **_authority_arguments(),
            as_of_date=date(2026, 8, 1),
        )

    assert database.read_bytes() == before
