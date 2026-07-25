from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from alphapilot.db.data_hygiene import (
    OrphanAdjFactorCleanupResult,
    cleanup_mock_daily_bars,
    cleanup_orphan_sina_adj_factors,
    repair_invalid_sina_daily_bars,
)


def _create_database(path: Path, *, mock_count: int) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE daily_bars (
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                source TEXT NOT NULL
            );
            CREATE TABLE trade_proposals (id INTEGER PRIMARY KEY);
            CREATE TABLE broker_orders (id INTEGER PRIMARY KEY);
            INSERT INTO trade_proposals (id) VALUES (1);
            INSERT INTO broker_orders (id) VALUES (1);
            INSERT INTO daily_bars (symbol, trade_date, source)
            VALUES ('600519', '2026-07-24', 'baostock');
            """
        )
        for index in range(mock_count):
            connection.execute(
                """
                INSERT INTO daily_bars (symbol, trade_date, source)
                VALUES (?, ?, 'mock')
                """,
                (f"{index:06d}", f"2026-07-{index + 1:02d}"),
            )
        connection.commit()
    finally:
        connection.close()


def test_cleanup_requires_apply_and_preserves_safety_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "test.db"
    _create_database(database_path, mock_count=2)

    dry_run = cleanup_mock_daily_bars(
        database_path=database_path,
        backup_directory=tmp_path / "backups",
        expected_count=2,
        apply=False,
    )
    assert dry_run.status == "dry_run"
    assert dry_run.before_count == 2
    assert dry_run.deleted_count == 0
    assert dry_run.backup_path is None

    applied = cleanup_mock_daily_bars(
        database_path=database_path,
        backup_directory=tmp_path / "backups",
        expected_count=2,
        apply=True,
    )
    assert applied.status == "applied"
    assert applied.deleted_count == 2
    assert applied.after_count == 0
    assert applied.database_quick_check == "ok"
    assert applied.trade_proposals_before == applied.trade_proposals_after == 1
    assert applied.broker_orders_before == applied.broker_orders_after == 1
    assert applied.backup_path is not None
    assert Path(applied.backup_path).is_file()
    assert applied.backup_sha256 is not None
    assert len(applied.backup_sha256) == 64

    second_run = cleanup_mock_daily_bars(
        database_path=database_path,
        backup_directory=tmp_path / "backups",
        expected_count=2,
        apply=True,
    )
    assert second_run.status == "already_clean"
    assert second_run.deleted_count == 0
    assert second_run.backup_path is None


def test_cleanup_fails_closed_when_count_changes(tmp_path: Path) -> None:
    database_path = tmp_path / "test.db"
    _create_database(database_path, mock_count=1)

    with pytest.raises(RuntimeError, match="refusing mutation"):
        cleanup_mock_daily_bars(
            database_path=database_path,
            backup_directory=tmp_path / "backups",
            expected_count=2,
            apply=True,
        )

    connection = sqlite3.connect(database_path)
    try:
        remaining = connection.execute(
            "SELECT COUNT(*) FROM daily_bars WHERE source = 'mock'"
        ).fetchone()
        assert remaining == (1,)
    finally:
        connection.close()


class _FakeSinaFetcher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, date, date]] = []

    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        self.calls.append((symbol, start, end))
        if symbol == "920001":
            return pd.DataFrame(
                [
                    {
                        "date": date(2020, 1, 2),
                        "open": 2.0,
                        "high": 2.2,
                        "low": 1.9,
                        "close": 2.1,
                        "volume": 100.0,
                        "amount": 205.0,
                    }
                ]
            )
        return pd.DataFrame(
            [
                {
                    "date": date(2020, 1, 3),
                    "open": 0.0,
                    "high": 0.0,
                    "low": 0.0,
                    "close": 0.0,
                    "volume": 200.0,
                    "amount": 400.0,
                }
            ]
        )


def _create_invalid_sina_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE daily_bars (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                amount REAL,
                source TEXT NOT NULL,
                ingested_at TEXT
            );
            CREATE TABLE adj_factors (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                adj_factor REAL NOT NULL,
                source TEXT NOT NULL,
                UNIQUE (symbol, trade_date)
            );
            CREATE TABLE trade_proposals (id INTEGER PRIMARY KEY);
            CREATE TABLE broker_orders (id INTEGER PRIMARY KEY);
            INSERT INTO trade_proposals (id) VALUES (1);
            INSERT INTO broker_orders (id) VALUES (1);
            INSERT INTO daily_bars
                (id, symbol, trade_date, open, high, low, close, volume, amount, source)
            VALUES
                (1, '920001', '2020-01-02', 0, 0, 0, 0, 100, 205, 'sina'),
                (2, '920002', '2020-01-03', 0, 0, 0, 0, 200, 400, 'sina');
            INSERT INTO adj_factors
                (id, symbol, trade_date, adj_factor, source)
            VALUES
                (1, '920001', '2020-01-02', 1.0, 'sina-hfq'),
                (2, '920002', '2020-01-03', 1.0, 'sina-hfq');
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_sina_repair_refetches_valid_row_and_deletes_invalid_source_row(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    _create_invalid_sina_database(database_path)
    fetcher = _FakeSinaFetcher()

    dry_run = repair_invalid_sina_daily_bars(
        database_path=database_path,
        backup_directory=tmp_path / "backups",
        expected_count=2,
        fetcher=fetcher,
        apply=False,
        cleared_proxy_keys=("HTTP_PROXY",),
    )
    assert dry_run.status == "dry_run"
    assert dry_run.repaired_count == 1
    assert dry_run.deleted_count == 1
    assert dry_run.deleted_adj_factor_count == 1
    assert dry_run.after_count == 2
    assert [row.classification for row in dry_run.rows] == [
        "sina_refetch_valid",
        "sina_zero_ohlc_with_trading_activity",
    ]

    applied = repair_invalid_sina_daily_bars(
        database_path=database_path,
        backup_directory=tmp_path / "backups",
        expected_count=2,
        fetcher=fetcher,
        apply=True,
        cleared_proxy_keys=(),
    )
    assert applied.status == "applied"
    assert applied.repaired_count == 1
    assert applied.deleted_count == 1
    assert applied.deleted_adj_factor_count == 1
    assert applied.after_count == 0
    assert applied.trade_proposals_before == applied.trade_proposals_after == 1
    assert applied.broker_orders_before == applied.broker_orders_after == 1
    assert applied.backup_path is not None

    connection = sqlite3.connect(database_path)
    try:
        remaining = connection.execute(
            """
            SELECT symbol, open, high, low, close, volume, amount
            FROM daily_bars
            ORDER BY symbol
            """
        ).fetchall()
        assert remaining == [("920001", 2.0, 2.2, 1.9, 2.1, 100.0, 205.0)]
        remaining_adj = connection.execute(
            "SELECT symbol, trade_date, source FROM adj_factors ORDER BY symbol"
        ).fetchall()
        assert remaining_adj == [("920001", "2020-01-02", "sina-hfq")]
    finally:
        connection.close()

    idempotent = repair_invalid_sina_daily_bars(
        database_path=database_path,
        backup_directory=tmp_path / "backups",
        expected_count=2,
        fetcher=fetcher,
        apply=True,
        cleared_proxy_keys=(),
    )
    assert idempotent.status == "already_clean"
    assert idempotent.deleted_adj_factor_count == 0


def _create_orphan_adj_database(path: Path, *, extra_orphan: bool = False) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE daily_bars (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                source TEXT NOT NULL,
                UNIQUE (symbol, trade_date)
            );
            CREATE TABLE adj_factors (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                adj_factor REAL NOT NULL,
                source TEXT NOT NULL,
                UNIQUE (symbol, trade_date)
            );
            CREATE TABLE trade_proposals (id INTEGER PRIMARY KEY);
            CREATE TABLE broker_orders (id INTEGER PRIMARY KEY);
            INSERT INTO trade_proposals (id) VALUES (1);
            INSERT INTO broker_orders (id) VALUES (1);
            INSERT INTO daily_bars (id, symbol, trade_date, source)
            VALUES (1, '600519', '2020-01-04', 'baostock');
            INSERT INTO adj_factors
                (id, symbol, trade_date, adj_factor, source)
            VALUES
                (1, '920001', '2020-01-02', 1.1, 'sina-hfq'),
                (2, '920002', '2020-01-03', 1.2, 'sina-hfq'),
                (3, '600519', '2020-01-04', 1.0, 'tushare');
            """
        )
        if extra_orphan:
            connection.execute(
                """
                INSERT INTO adj_factors
                    (id, symbol, trade_date, adj_factor, source)
                VALUES (4, '920003', '2020-01-05', 1.3, 'sina-hfq')
                """
            )
        connection.commit()
    finally:
        connection.close()


def _write_orphan_authority(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "status": "applied",
                "rows": [
                    {
                        "symbol": "920001",
                        "trade_date": "2020-01-02",
                        "source": "sina",
                        "action": "delete",
                    },
                    {
                        "symbol": "920002",
                        "trade_date": "2020-01-03",
                        "source": "sina",
                        "action": "delete",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def _run_orphan_cleanup(
    database_path: Path,
    authority_path: Path,
    backup_directory: Path,
    *,
    apply: bool,
) -> OrphanAdjFactorCleanupResult:
    return cleanup_orphan_sina_adj_factors(
        database_path=database_path,
        backup_directory=backup_directory,
        authority_evidence_path=authority_path,
        expected_count=2,
        expected_symbol_count=2,
        expected_min_date="2020-01-02",
        expected_max_date="2020-01-03",
        apply=apply,
    )


def test_orphan_adj_cleanup_is_evidence_scoped_and_idempotent(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    authority_path = tmp_path / "authority.json"
    _create_orphan_adj_database(database_path)
    _write_orphan_authority(authority_path)

    dry_run = _run_orphan_cleanup(
        database_path,
        authority_path,
        tmp_path / "backups",
        apply=False,
    )
    assert dry_run.status == "dry_run"
    assert dry_run.before_count == 2
    assert dry_run.deleted_count == 0
    assert dry_run.backup_path is None
    assert dry_run.adj_duplicate_groups_before == 0
    assert dry_run.daily_bar_duplicate_groups_before == 0

    applied = _run_orphan_cleanup(
        database_path,
        authority_path,
        tmp_path / "backups",
        apply=True,
    )
    assert applied.status == "applied"
    assert applied.deleted_count == 2
    assert applied.after_count == 0
    assert applied.trade_proposals_before == applied.trade_proposals_after == 1
    assert applied.broker_orders_before == applied.broker_orders_after == 1
    assert applied.daily_bars_without_adj_before == applied.daily_bars_without_adj_after
    assert applied.backup_path is not None
    assert Path(applied.backup_path).is_file()
    assert applied.backup_sha256 is not None
    assert len(applied.backup_sha256) == 64

    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT symbol, trade_date, source FROM adj_factors ORDER BY symbol"
        ).fetchall()
        assert rows == [("600519", "2020-01-04", "tushare")]
    finally:
        connection.close()

    idempotent = _run_orphan_cleanup(
        database_path,
        authority_path,
        tmp_path / "backups",
        apply=True,
    )
    assert idempotent.status == "already_clean"
    assert idempotent.deleted_count == 0
    assert idempotent.backup_path is None


def test_orphan_adj_cleanup_fails_closed_on_unexpected_orphan(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    authority_path = tmp_path / "authority.json"
    _create_orphan_adj_database(database_path, extra_orphan=True)
    _write_orphan_authority(authority_path)

    with pytest.raises(RuntimeError, match="do not exactly equal"):
        cleanup_orphan_sina_adj_factors(
            database_path=database_path,
            backup_directory=tmp_path / "backups",
            authority_evidence_path=authority_path,
            expected_count=2,
            expected_symbol_count=2,
            expected_min_date="2020-01-02",
            expected_max_date="2020-01-03",
            apply=True,
        )
    assert not (tmp_path / "backups").exists()


def test_orphan_adj_cleanup_requires_exact_safety_table_counts(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "test.db"
    authority_path = tmp_path / "authority.json"
    _create_orphan_adj_database(database_path)
    _write_orphan_authority(authority_path)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("INSERT INTO trade_proposals (id) VALUES (2)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="exactly 1/1"):
        _run_orphan_cleanup(
            database_path,
            authority_path,
            tmp_path / "backups",
            apply=True,
        )
    assert not (tmp_path / "backups").exists()
