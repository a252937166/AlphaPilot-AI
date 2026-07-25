from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from alphapilot.db.data_hygiene import cleanup_mock_daily_bars


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
