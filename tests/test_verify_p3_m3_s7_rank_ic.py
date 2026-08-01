from __future__ import annotations

import ast
import math
import os
import sqlite3
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from scripts.verify_p3_m3_s7_rank_ic import (
    DEFAULT_TOLERANCE,
    PREDECLARED_CONTRACT,
    DailySnapshot,
    SecurityRecord,
    VerificationContract,
    VerificationError,
    _daily_snapshot,
    _eligible_symbols,
    _rank_ic,
    _summary,
    _validate_recomputed_contract,
    _verify_database_for_contract,
    _winsorized_zscores,
    _write_json,
    main,
    verify_database,
)

_ROOT = Path(__file__).resolve().parent.parent


def _weekdays(start: date, count: int) -> list[date]:
    result: list[date] = []
    cursor = start
    while len(result) < count:
        if cursor.weekday() < 5:
            result.append(cursor)
        cursor += timedelta(days=1)
    return result


def _schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE securities (
            symbol TEXT PRIMARY KEY,
            market TEXT NOT NULL,
            list_status TEXT NOT NULL,
            is_st INTEGER NOT NULL,
            snapshot_at TEXT,
            listed_date TEXT
        );
        CREATE TABLE daily_bars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            amount REAL,
            source TEXT NOT NULL,
            UNIQUE(symbol, trade_date)
        );
        CREATE TABLE adj_factors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            adj_factor REAL NOT NULL,
            UNIQUE(symbol, trade_date)
        );
        CREATE TABLE financial_indicators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            report_period TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL,
            available_time TEXT NOT NULL,
            UNIQUE(symbol, report_period, metric)
        );
        CREATE TABLE factor_ic_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            factor TEXT NOT NULL,
            sample_tag TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            ic_mean REAL,
            ic_ir REAL,
            t_stat REAL,
            ic_positive_ratio REAL,
            long_short REAL,
            n_periods INTEGER NOT NULL,
            UNIQUE(factor, sample_tag, start_date, end_date)
        );
        """
    )


def _insert_market(
    connection: sqlite3.Connection,
    *,
    dates: list[date],
    symbols: list[str],
    closes: dict[tuple[str, int], float],
    adjustments: dict[tuple[str, int], float] | None = None,
) -> None:
    adjustment_values = adjustments or {}
    for symbol in symbols:
        connection.execute(
            """
            INSERT INTO securities(
                symbol, market, list_status, is_st, snapshot_at, listed_date
            )
            VALUES (?, 'CN', 'listed', 0, NULL, '2010-01-01')
            """,
            (symbol,),
        )
        last_close = 100.0
        for index, trade_date in enumerate(dates):
            last_close = closes.get((symbol, index), last_close)
            connection.execute(
                """
                INSERT INTO daily_bars(
                    symbol, trade_date, close, volume, amount, source
                )
                VALUES (?, ?, ?, 100.0, 10000.0, 'baostock')
                """,
                (symbol, trade_date.isoformat(), last_close),
            )
            connection.execute(
                """
                INSERT INTO adj_factors(symbol, trade_date, adj_factor)
                VALUES (?, ?, ?)
                """,
                (
                    symbol,
                    trade_date.isoformat(),
                    adjustment_values.get((symbol, index), 1.0),
                ),
            )


def _insert_financial(
    connection: sqlite3.Connection,
    *,
    symbol: str,
    report_period: str,
    value: float,
    available_time: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO financial_indicators(
            symbol, report_period, metric, value, available_time
        )
        VALUES (?, ?, 'roe', ?, ?)
        """,
        (
            symbol,
            report_period,
            value,
            available_time.astimezone(UTC).replace(tzinfo=None).isoformat(" "),
        ),
    )


def _contract(dates: list[date]) -> VerificationContract:
    return VerificationContract(
        factor="roe",
        sample_tag="full",
        start_date=dates[0],
        end_date=dates[-1],
        horizon_sessions=20,
        rebalance_sessions=20,
    )


def test_direct_sqlite_recompute_honors_pit_and_exact_window(tmp_path: Path) -> None:
    database = tmp_path / "rank-ic.db"
    dates = _weekdays(date(2026, 1, 2), 41)
    symbols = ["600001", "600002", "600003"]
    first_returns = dict(zip(symbols, (0.01, 0.02, 0.03), strict=True))
    second_returns = dict(zip(symbols, (0.03, 0.02, 0.01), strict=True))
    closes: dict[tuple[str, int], float] = {}
    for symbol in symbols:
        midpoint = 100.0 * (1.0 + first_returns[symbol])
        closes[(symbol, 0)] = 100.0
        closes[(symbol, 20)] = midpoint
        closes[(symbol, 40)] = midpoint * (1.0 + second_returns[symbol])

    with sqlite3.connect(database) as connection:
        _schema(connection)
        _insert_market(
            connection,
            dates=dates,
            symbols=symbols,
            closes=closes,
        )
        before_first_decision = datetime.combine(
            dates[0] - timedelta(days=1),
            datetime.min.time(),
            tzinfo=UTC,
        )
        before_second_decision = datetime.combine(
            dates[20],
            datetime.min.time(),
            tzinfo=UTC,
        ).replace(hour=11, minute=30)
        after_second_decision = datetime.combine(
            dates[20],
            datetime.min.time(),
            tzinfo=UTC,
        ).replace(hour=11, minute=30, second=1)
        for rank, symbol in enumerate(symbols, start=1):
            _insert_financial(
                connection,
                symbol=symbol,
                report_period="2025Q3",
                value=float(rank),
                available_time=before_first_decision,
            )
            _insert_financial(
                connection,
                symbol=symbol,
                report_period="2025Q4",
                value=float(4 - rank),
                available_time=before_second_decision,
            )
            _insert_financial(
                connection,
                symbol=symbol,
                report_period="2026Q1",
                value=float(rank),
                available_time=after_second_decision,
            )
        contract = _contract(dates)
        connection.execute(
            """
            INSERT INTO factor_ic_stats(
                factor, sample_tag, start_date, end_date, ic_mean, ic_ir,
                t_stat, ic_positive_ratio, long_short, n_periods
            )
            VALUES ('roe', 'full', ?, ?, 1.0, NULL, NULL, 1.0, 0.02, 2)
            """,
            (contract.start_date.isoformat(), contract.end_date.isoformat()),
        )
        connection.execute(
            """
            INSERT INTO factor_ic_stats(
                factor, sample_tag, start_date, end_date, ic_mean, ic_ir,
                t_stat, ic_positive_ratio, long_short, n_periods
            )
            VALUES ('roe', 'full', '2025-01-01', '2025-12-31',
                    -1.0, NULL, NULL, 0.0, -0.02, 99)
            """
        )
        connection.commit()

    report = _verify_database_for_contract(database, contract=contract)

    assert report["status"] == "pass"
    assert report["database"]["query_only"] is True
    assert report["comparison"]["all_match"] is True
    assert report["persisted_exact_window"]["n_periods"] == 2
    independent = report["independent"]
    assert independent["summary"] == {
        "ic_mean": pytest.approx(1.0),
        "ic_std": pytest.approx(0.0),
        "ic_ir": None,
        "t_stat": None,
        "ic_positive_ratio": pytest.approx(1.0),
        "n_periods": 2,
    }
    assert [period["rank_ic"] for period in independent["periods"]] == pytest.approx(
        [1.0, 1.0]
    )


def test_adjusted_endpoint_return_drives_rank_ic(tmp_path: Path) -> None:
    database = tmp_path / "adjusted.db"
    dates = _weekdays(date(2026, 3, 2), 21)
    symbols = ["600001", "600002"]
    closes = {
        ("600001", 0): 100.0,
        ("600002", 0): 100.0,
        ("600001", 20): 120.0,
        ("600002", 20): 110.0,
    }
    adjustments = {("600002", 20): 2.0}

    with sqlite3.connect(database) as connection:
        _schema(connection)
        _insert_market(
            connection,
            dates=dates,
            symbols=symbols,
            closes=closes,
            adjustments=adjustments,
        )
        available = datetime.combine(
            dates[0] - timedelta(days=1),
            datetime.min.time(),
            tzinfo=UTC,
        )
        _insert_financial(
            connection,
            symbol="600001",
            report_period="2025Q4",
            value=1.0,
            available_time=available,
        )
        _insert_financial(
            connection,
            symbol="600002",
            report_period="2025Q4",
            value=2.0,
            available_time=available,
        )
        contract = _contract(dates)
        connection.execute(
            """
            INSERT INTO factor_ic_stats(
                factor, sample_tag, start_date, end_date, ic_mean, ic_ir,
                t_stat, ic_positive_ratio, long_short, n_periods
            )
            VALUES ('roe', 'full', ?, ?, 1.0, NULL, NULL, 1.0, 0.1, 1)
            """,
            (contract.start_date.isoformat(), contract.end_date.isoformat()),
        )
        connection.commit()

    report = _verify_database_for_contract(database, contract=contract)

    assert report["status"] == "pass"
    assert report["independent"]["periods"][0]["rank_ic"] == pytest.approx(1.0)


def test_rank_and_summary_match_spearman_and_sample_statistics() -> None:
    rank_ic = _rank_ic(
        {"a": 1.0, "b": 2.0, "c": 2.0, "d": 4.0},
        {"a": 4.0, "b": 2.0, "c": 3.0, "d": 1.0},
    )
    assert rank_ic is not None
    assert rank_ic == pytest.approx(-0.9486832980505138)

    summary = _summary([1.0, 0.5, -0.5])
    expected_mean = 1.0 / 3.0
    expected_std = math.sqrt(7.0 / 12.0)
    assert summary["ic_mean"] == pytest.approx(expected_mean)
    assert summary["ic_std"] == pytest.approx(expected_std)
    assert summary["ic_ir"] == pytest.approx(expected_mean / expected_std)
    assert summary["t_stat"] == pytest.approx(
        expected_mean / (expected_std / math.sqrt(3))
    )
    assert summary["ic_positive_ratio"] == pytest.approx(2.0 / 3.0)
    assert summary["n_periods"] == 3


def test_eligibility_replays_listing_age_amount_and_visible_st_rules() -> None:
    decision_date = date(2026, 7, 31)
    old_listing = decision_date - timedelta(days=61)
    exactly_old_enough = decision_date - timedelta(days=60)
    too_new = decision_date - timedelta(days=59)
    same_day_visible = datetime(2026, 7, 31, 11, 29, tzinfo=UTC)
    prior_day_snapshot = datetime(2026, 7, 30, 11, 29, tzinfo=UTC)
    securities = {
        "old": SecurityRecord(old_listing, None, False, None),
        "boundary": SecurityRecord(exactly_old_enough, None, False, None),
        "new": SecurityRecord(too_new, None, False, None),
        "fallback-old": SecurityRecord(None, old_listing, False, None),
        "fallback-new": SecurityRecord(None, too_new, False, None),
        "visible-st": SecurityRecord(old_listing, None, True, same_day_visible),
        "historical-st-unknown": SecurityRecord(
            old_listing,
            None,
            True,
            prior_day_snapshot,
        ),
        "zero-amount": SecurityRecord(old_listing, None, False, None),
    }
    regular_bar = DailySnapshot(
        close=10.0,
        volume=100.0,
        amount=1000.0,
        adj_factor=1.0,
        adjustment_missing=False,
    )
    bars = dict.fromkeys(securities, regular_bar)
    bars["zero-amount"] = DailySnapshot(
        close=10.0,
        volume=100.0,
        amount=0.0,
        adj_factor=1.0,
        adjustment_missing=False,
    )

    eligible = _eligible_symbols(
        securities=securities,
        decision_bars=bars,
        decision_date=decision_date,
    )

    assert eligible == {
        "old",
        "boundary",
        "fallback-old",
        "historical-st-unknown",
    }


def test_winsorization_preserves_deterministic_average_rank_ties() -> None:
    values = {
        "a": -100.0,
        "b": -100.0,
        "c": 1.0,
        "d": 2.0,
        "e": 100.0,
        "f": 100.0,
    }

    winsorized = _winsorized_zscores(values)
    assert winsorized["a"] == pytest.approx(winsorized["b"])
    assert winsorized["e"] == pytest.approx(winsorized["f"])
    assert _rank_ic(
        winsorized,
        {"a": 0.0, "b": 0.0, "c": 1.0, "d": 2.0, "e": 3.0, "f": 3.0},
    ) == pytest.approx(1.0)


def test_tool_does_not_import_repository_or_dataframe_calculators() -> None:
    script = _ROOT / "scripts" / "verify_p3_m3_s7_rank_ic.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])

    assert "alphapilot" not in imported_roots
    assert "pandas" not in imported_roots
    assert "numpy" not in imported_roots
    assert "sqlalchemy" not in imported_roots


def test_independent_math_matches_production_on_synthetic_ties_and_outliers() -> None:
    from alphapilot.backtest.metrics import ic_summary, rank_ic
    from alphapilot.engines.factors import zscore_cross_section

    raw = {
        "a": -100.0,
        "b": -100.0,
        "c": 1.0,
        "d": 2.0,
        "e": 3.0,
        "f": 100.0,
        "g": 100.0,
    }
    realized = {
        "a": 0.03,
        "b": 0.03,
        "c": -0.01,
        "d": 0.02,
        "e": 0.01,
        "f": 0.04,
        "g": 0.04,
    }
    production_scores = zscore_cross_section(
        pd.DataFrame({"roe": pd.Series(raw, dtype=float)})
    )["roe"].to_dict()
    independent_scores = _winsorized_zscores(raw)
    assert production_scores == pytest.approx(independent_scores)

    production_ic = rank_ic(
        pd.Series(production_scores),
        pd.Series(realized),
    )
    independent_ic = _rank_ic(independent_scores, realized)
    assert independent_ic == pytest.approx(production_ic)

    values = [production_ic, -0.1, 0.2]
    production_summary = ic_summary(values)
    independent_summary = _summary(values)
    assert independent_summary["ic_mean"] == pytest.approx(
        production_summary["mean"]
    )
    assert independent_summary["ic_std"] == pytest.approx(
        production_summary["std"]
    )
    assert independent_summary["ic_ir"] == pytest.approx(
        production_summary["ic_ir"]
    )
    assert independent_summary["t_stat"] == pytest.approx(
        production_summary["t_stat"]
    )


def test_public_contract_rejects_window_or_tolerance_changes(tmp_path: Path) -> None:
    database = tmp_path / "immutable.db"
    database.touch()
    custom = VerificationContract(
        factor="roe",
        sample_tag="full",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        horizon_sessions=20,
        rebalance_sessions=20,
    )

    with pytest.raises(ValueError, match="contract is immutable"):
        verify_database(database, contract=custom)
    with pytest.raises(ValueError, match="tolerance is immutable"):
        verify_database(
            database,
            contract=PREDECLARED_CONTRACT,
            tolerance=DEFAULT_TOLERANCE * 10,
        )


def test_zero_period_recompute_is_rejected_instead_of_matching_nulls(
    tmp_path: Path,
) -> None:
    database = tmp_path / "zero-period.db"
    dates = _weekdays(date(2026, 1, 2), 21)
    contract = _contract(dates)
    with sqlite3.connect(database) as connection:
        _schema(connection)
        _insert_market(
            connection,
            dates=dates,
            symbols=["600001", "600002"],
            closes={},
        )
        connection.execute(
            """
            INSERT INTO factor_ic_stats(
                factor, sample_tag, start_date, end_date, ic_mean, ic_ir,
                t_stat, ic_positive_ratio, long_short, n_periods
            )
            VALUES ('roe', 'full', ?, ?, NULL, NULL, NULL, NULL, NULL, 0)
            """,
            (contract.start_date.isoformat(), contract.end_date.isoformat()),
        )
        connection.commit()

    with pytest.raises(VerificationError, match="zero valid periods"):
        _verify_database_for_contract(database, contract=contract)


def test_predeclared_contract_rejects_calendar_or_period_drift() -> None:
    valid_period = {
        "decision_date": "2019-01-02",
        "paired_symbols": 100,
        "rank_ic": 0.1,
    }
    base: dict[str, Any] = {
        "summary": {
            "n_periods": 91,
            "ic_mean": 0.01,
            "ic_ir": 0.1,
            "t_stat": 2.0,
            "ic_positive_ratio": 0.6,
        },
        "calendar": {
            "sessions": 1_837,
            "weekend_dates": 0,
            "minimum_symbols": 100,
        },
        "periods": [valid_period] * 91,
    }
    with pytest.raises(VerificationError, match="session count changed"):
        _validate_recomputed_contract(base, contract=PREDECLARED_CONTRACT)

    base["calendar"]["sessions"] = 1_838
    base["calendar"]["weekend_dates"] = 1
    with pytest.raises(VerificationError, match="weekend"):
        _validate_recomputed_contract(base, contract=PREDECLARED_CONTRACT)

    base["calendar"]["weekend_dates"] = 0
    base["periods"] = [valid_period] * 90
    with pytest.raises(VerificationError, match="decision-period count changed"):
        _validate_recomputed_contract(base, contract=PREDECLARED_CONTRACT)


def test_nonfinite_adjustment_is_not_silently_replaced(tmp_path: Path) -> None:
    database = tmp_path / "infinite-adjustment.db"
    trade_date = date(2026, 1, 2)
    with sqlite3.connect(database) as connection:
        _schema(connection)
        _insert_market(
            connection,
            dates=[trade_date],
            symbols=["600001"],
            closes={},
        )
        connection.execute(
            """
            UPDATE adj_factors
            SET adj_factor = ?
            WHERE symbol = '600001' AND trade_date = ?
            """,
            (float("inf"), trade_date.isoformat()),
        )
        connection.commit()
        connection.row_factory = sqlite3.Row
        with pytest.raises(VerificationError, match="infinite adj_factor"):
            _daily_snapshot(connection, trade_date=trade_date)


def test_nonfinite_persisted_stat_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "infinite-stat.db"
    dates = _weekdays(date(2026, 3, 2), 21)
    symbols = ["600001", "600002"]
    contract = _contract(dates)
    with sqlite3.connect(database) as connection:
        _schema(connection)
        _insert_market(
            connection,
            dates=dates,
            symbols=symbols,
            closes={
                ("600001", 0): 100.0,
                ("600002", 0): 100.0,
                ("600001", 20): 101.0,
                ("600002", 20): 102.0,
            },
        )
        available = datetime.combine(
            dates[0] - timedelta(days=1),
            datetime.min.time(),
            tzinfo=UTC,
        )
        for rank, symbol in enumerate(symbols, start=1):
            _insert_financial(
                connection,
                symbol=symbol,
                report_period="2025Q4",
                value=float(rank),
                available_time=available,
            )
        connection.execute(
            """
            INSERT INTO factor_ic_stats(
                factor, sample_tag, start_date, end_date, ic_mean, ic_ir,
                t_stat, ic_positive_ratio, long_short, n_periods
            )
            VALUES ('roe', 'full', ?, ?, 1.0, ?, NULL, 1.0, 0.01, 1)
            """,
            (
                contract.start_date.isoformat(),
                contract.end_date.isoformat(),
                float("inf"),
            ),
        )
        connection.commit()

    with pytest.raises(VerificationError, match="non-finite ic_ir"):
        _verify_database_for_contract(database, contract=contract)


def test_cli_and_temporary_output_cannot_overwrite_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "production.db"
    sentinel = b"sqlite-sentinel"
    database.write_bytes(sentinel)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_p3_m3_s7_rank_ic.py",
            "--db",
            str(database),
            "--output",
            str(database),
        ],
    )
    assert main() == 1
    assert database.read_bytes() == sentinel

    hardlink = tmp_path / "hardlink.json"
    os.link(database, hardlink)
    with pytest.raises(ValueError, match="must not alias"):
        _write_json(hardlink, {"status": "pass"}, database=database)
    assert database.read_bytes() == sentinel

    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{database}{suffix}")
        sidecar.write_bytes(sentinel)
        destination_alias = tmp_path / f"sidecar-{suffix[1:]}.json"
        os.link(sidecar, destination_alias)
        with pytest.raises(ValueError, match="must not alias"):
            _write_json(
                destination_alias,
                {"status": "pass"},
                database=database,
            )
        assert sidecar.read_bytes() == sentinel

        ordinary_output = tmp_path / f"sidecar-temp-{suffix[1:]}.json"
        temporary_alias = ordinary_output.with_name(
            f".{ordinary_output.name}.tmp"
        )
        os.link(sidecar, temporary_alias)
        with pytest.raises(ValueError, match="temporary output"):
            _write_json(
                ordinary_output,
                {"status": "pass"},
                database=database,
            )
        assert sidecar.read_bytes() == sentinel

    temporary_database = tmp_path / ".review.json.tmp"
    temporary_database.write_bytes(sentinel)
    with pytest.raises(ValueError, match="temporary output"):
        _write_json(
            tmp_path / "review.json",
            {"status": "pass"},
            database=temporary_database,
        )
    assert temporary_database.read_bytes() == sentinel
