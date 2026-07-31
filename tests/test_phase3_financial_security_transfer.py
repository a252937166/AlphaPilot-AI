from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from alphapilot.db.models import Base
from alphapilot.jobs.financial_security_transfer import (
    export_staging_security_metadata,
    import_missing_staging_securities,
)


def _database(
    path: Path,
    *,
    securities: list[tuple[object, ...]],
    with_protected_rows: bool = False,
) -> None:
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    now = datetime(2026, 7, 30, tzinfo=UTC).isoformat()
    with sqlite3.connect(path) as connection:
        connection.executemany(
            """
            INSERT INTO securities (
                symbol,
                market,
                name,
                org_id,
                industry,
                board,
                listed_date,
                status,
                profile,
                updated_at,
                industry_csrc,
                industry_futu,
                is_st,
                list_status,
                market_cap,
                float_cap,
                pe_ttm,
                pb,
                turnover_rate,
                snapshot_at,
                style_tag
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            securities,
        )
        if with_protected_rows:
            connection.execute(
                """
                INSERT INTO financial_indicators (
                    symbol, report_period, metric, value, source, available_time, payload
                )
                VALUES ('600519', '2026Q1', 'roe', 0.2, 'baostock', ?, '{}')
                """,
                (now,),
            )
            connection.execute(
                """
                INSERT INTO job_runs (
                    job_name, started_at, finished_at, status, stats, error
                )
                VALUES ('seed', ?, ?, 'ok', '{}', NULL)
                """,
                (now, now),
            )
            connection.execute(
                """
                INSERT INTO trade_proposals (
                    proposal_id, symbol, side, quantity, estimated_notional, confidence,
                    mode, status, proposal, risk_decision, created_at
                )
                VALUES (
                    'proposal-1', '600519', 'BUY', 1, 100, 0.8,
                    'SIMULATE', 'approved', '{}', '{}', ?
                )
                """,
                (now,),
            )
            connection.execute(
                """
                INSERT INTO broker_orders (
                    proposal_id, symbol, side, order_type, qty, status, filled_qty,
                    environment, created_at, updated_at
                )
                VALUES (
                    'proposal-1', '600519', 'BUY', 'MARKET', 1, 'submitted', 0,
                    'SIMULATE', ?, ?
                )
                """,
                (now, now),
            )


def _security(
    symbol: str,
    *,
    name: str,
    profile: str,
) -> tuple[object, ...]:
    now = datetime(2026, 7, 30, tzinfo=UTC).isoformat()
    return (
        symbol,
        "CN",
        name,
        f"org-{symbol}",
        "半导体",
        "科创板",
        "2026-07-30",
        "active",
        profile,
        now,
        "C39计算机、通信和其他电子设备制造业",
        "半导体",
        0,
        "listed",
        123_000_000.0,
        45_000_000.0,
        22.6,
        3.1,
        0.5,
        now,
        "growth",
    )


def _selected_row(path: Path, symbol: str) -> tuple[object, ...] | None:
    with sqlite3.connect(path) as connection:
        columns = [
            str(row[1])
            for row in connection.execute("PRAGMA table_info(securities)")
        ]
        quoted = ", ".join(f'"{column}"' for column in columns)
        row = connection.execute(
            f"SELECT {quoted} FROM securities WHERE symbol = ?",
            (symbol,),
        ).fetchone()
    return tuple(row) if row is not None else None


def test_import_missing_staging_security_is_full_field_safe_and_idempotent(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    requested = _security("688825", name="C长鑫", profile='{"origin":"production"}')
    _database(
        source,
        securities=[
            requested,
            _security("688999", name="not-allowed", profile="{}"),
        ],
    )
    _database(
        target,
        securities=[_security("600519", name="贵州茅台", profile="{}")],
        with_protected_rows=True,
    )

    first = import_missing_staging_securities(
        source,
        target,
        symbols=["688825"],
    )
    second = import_missing_staging_securities(
        source,
        target,
        symbols=["688825"],
    )

    assert first["source"]["mode"] == "ro"
    assert first["source"]["query_only"] is True
    assert first["target"]["busy_timeout_ms"] == 15_000
    assert first["target"]["security_columns_copied"] == 21
    assert first["inserted_symbols"] == ["688825"]
    assert first["already_present_symbols"] == []
    assert first["protected_counts_before"] == {
        "financial_indicators": 1,
        "job_runs": 1,
        "trade_proposals": 1,
        "broker_orders": 1,
    }
    assert first["protected_counts_after"] == first["protected_counts_before"]
    assert first["protected_counts_unchanged"] is True
    assert second["inserted_symbols"] == []
    assert second["already_present_symbols"] == ["688825"]
    assert second["protected_counts_after"] == first["protected_counts_before"]
    assert second["source"]["selected_rows_sha256"] == first["source"][
        "selected_rows_sha256"
    ]
    assert _selected_row(target, "688825") == _selected_row(source, "688825")
    assert _selected_row(target, "688999") is None


def test_export_staging_security_metadata_is_allowlisted_exact_and_read_only(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    output = tmp_path / "metadata.db"
    _database(
        source,
        securities=[
            _security("688825", name="C长鑫", profile='{"origin":"production"}'),
            _security("688999", name="not-allowed", profile="{}"),
        ],
        with_protected_rows=True,
    )
    source_before = source.read_bytes()
    with sqlite3.connect(source) as connection:
        source_columns = [
            str(row[1])
            for row in connection.execute("PRAGMA table_info(securities)")
        ]

    result = export_staging_security_metadata(
        source,
        output,
        symbols=["688825"],
    )

    assert source.read_bytes() == source_before
    assert result["source"]["mode"] == "ro"
    assert result["source"]["query_only"] is True
    assert result["source"]["selected_rows"] == 1
    assert result["output"]["tables"] == ["securities"]
    assert result["output"]["security_columns"] == len(source_columns) == 21
    assert result["output"]["security_rows"] == 1
    assert result["output"]["quick_check"] == "ok"
    assert result["output"]["selected_rows_sha256"] == result["source"][
        "selected_rows_sha256"
    ]
    with sqlite3.connect(output) as exported:
        output_tables = [
            str(row[0])
            for row in exported.execute(
                """
                SELECT name
                FROM sqlite_schema
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            )
        ]
        output_columns = [
            str(row[1]) for row in exported.execute("PRAGMA table_info(securities)")
        ]
        output_symbols = [
            str(row[0])
            for row in exported.execute("SELECT symbol FROM securities ORDER BY symbol")
        ]
    assert output_tables == ["securities"]
    assert output_columns == source_columns
    assert output_symbols == ["688825"]
    assert _selected_row(output, "688825") == _selected_row(source, "688825")
    assert _selected_row(output, "688999") is None


def test_import_never_overwrites_existing_security(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    _database(
        source,
        securities=[_security("688825", name="source-name", profile='{"side":"source"}')],
    )
    _database(
        target,
        securities=[_security("688825", name="target-name", profile='{"side":"target"}')],
    )
    before = _selected_row(target, "688825")

    result = import_missing_staging_securities(
        source,
        target,
        symbols=["688825"],
    )

    assert result["inserted_symbols"] == []
    assert result["already_present_symbols"] == ["688825"]
    assert result["existing_row_mismatches"] == ["688825"]
    assert _selected_row(target, "688825") == before


def test_import_maps_columns_by_name_when_physical_order_differs(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    requested = _security("688825", name="C长鑫", profile='{"origin":"production"}')
    _database(source, securities=[requested])
    _database(target, securities=[])

    with sqlite3.connect(target) as connection:
        columns = [
            str(row[1])
            for row in connection.execute("PRAGMA table_info(securities)")
        ]
        reordered = [columns[0], *reversed(columns[1:])]
        quoted = ", ".join(f'"{column}"' for column in reordered)
        connection.execute("ALTER TABLE securities RENAME TO securities_original")
        connection.execute(
            f"CREATE TABLE securities AS "
            f"SELECT {quoted} FROM securities_original WHERE 0"
        )

    result = import_missing_staging_securities(
        source,
        target,
        symbols=["688825"],
    )

    assert result["inserted_symbols"] == ["688825"]
    assert result["target"]["security_columns_copied"] == 21
    with sqlite3.connect(source) as source_connection, sqlite3.connect(
        target
    ) as target_connection:
        source_columns = [
            str(row[1])
            for row in source_connection.execute("PRAGMA table_info(securities)")
        ]
        quoted_source_order = ", ".join(
            f'"{column}"' for column in source_columns
        )
        source_row = source_connection.execute(
            f"SELECT {quoted_source_order} FROM securities WHERE symbol = '688825'"
        ).fetchone()
        target_row = target_connection.execute(
            f"SELECT {quoted_source_order} FROM securities WHERE symbol = '688825'"
        ).fetchone()
    assert target_row == source_row


@pytest.mark.parametrize(
    ("symbols", "message"),
    [
        ([], "at least one explicit"),
        (["68882"], "invalid six-digit"),
        (["688825", "688825"], "duplicate"),
    ],
)
def test_import_rejects_unsafe_allowlists(
    tmp_path: Path,
    symbols: list[str],
    message: str,
) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    _database(source, securities=[])
    _database(target, securities=[])

    with pytest.raises(ValueError, match=message):
        import_missing_staging_securities(source, target, symbols=symbols)


def test_import_rejects_symbol_absent_from_source_without_target_change(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    _database(source, securities=[])
    _database(
        target,
        securities=[_security("600519", name="贵州茅台", profile="{}")],
        with_protected_rows=True,
    )
    before = target.read_bytes()

    with pytest.raises(ValueError, match="absent from source"):
        import_missing_staging_securities(
            source,
            target,
            symbols=["688825"],
        )

    assert target.read_bytes() == before


@pytest.mark.parametrize(
    ("symbols", "message"),
    [
        ([], "at least one explicit"),
        (["68882"], "invalid six-digit"),
        (["688825", "688825"], "duplicate"),
        (["688999"], "absent from source"),
    ],
)
def test_export_rejects_invalid_or_missing_allowlist_without_output(
    tmp_path: Path,
    symbols: list[str],
    message: str,
) -> None:
    source = tmp_path / "source.db"
    output = tmp_path / "metadata.db"
    _database(
        source,
        securities=[_security("688825", name="C长鑫", profile="{}")],
    )
    source_before = source.read_bytes()

    with pytest.raises(ValueError, match=message):
        export_staging_security_metadata(source, output, symbols=symbols)

    assert source.read_bytes() == source_before
    assert not output.exists()


def test_export_refuses_to_overwrite_existing_output(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    output = tmp_path / "metadata.db"
    _database(
        source,
        securities=[_security("688825", name="C长鑫", profile="{}")],
    )
    output.write_bytes(b"keep-me")

    with pytest.raises(FileExistsError, match="already exists"):
        export_staging_security_metadata(
            source,
            output,
            symbols=["688825"],
        )

    assert output.read_bytes() == b"keep-me"
