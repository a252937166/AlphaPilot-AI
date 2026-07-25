from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Protocol

import pandas as pd


@dataclass(frozen=True)
class DailyBarEvidenceRow:
    symbol: str
    trade_date: str
    source: str


@dataclass(frozen=True)
class MockCleanupResult:
    status: str
    expected_count: int
    deleted_count: int
    before_count: int
    after_count: int
    backup_path: str | None
    backup_sha256: str | None
    database_quick_check: str
    trade_proposals_before: int
    trade_proposals_after: int
    broker_orders_before: int
    broker_orders_after: int
    rows: tuple[DailyBarEvidenceRow, ...]
    completed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "rows": [asdict(row) for row in self.rows],
        }


@dataclass(frozen=True)
class InvalidDailyBarRow:
    row_id: int
    symbol: str
    trade_date: str
    source: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float | None


@dataclass(frozen=True)
class SinaRepairEvidenceRow:
    row_id: int
    symbol: str
    trade_date: str
    source: str
    stored_open: float
    stored_high: float
    stored_low: float
    stored_close: float
    stored_volume: float
    stored_amount: float | None
    fetched_open: float | None
    fetched_high: float | None
    fetched_low: float | None
    fetched_close: float | None
    fetched_volume: float | None
    fetched_amount: float | None
    action: str
    classification: str


@dataclass(frozen=True)
class SinaRepairResult:
    status: str
    expected_count: int
    before_count: int
    repaired_count: int
    deleted_count: int
    deleted_adj_factor_count: int
    after_count: int
    backup_path: str | None
    backup_sha256: str | None
    database_quick_check: str
    trade_proposals_before: int
    trade_proposals_after: int
    broker_orders_before: int
    broker_orders_after: int
    cleared_proxy_keys: tuple[str, ...]
    rows: tuple[SinaRepairEvidenceRow, ...]
    completed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "rows": [asdict(row) for row in self.rows],
        }


@dataclass(frozen=True)
class OrphanAdjFactorEvidenceRow:
    row_id: int
    symbol: str
    trade_date: str
    source: str
    adj_factor: float


@dataclass(frozen=True)
class OrphanAdjFactorCleanupResult:
    status: str
    expected_count: int
    expected_symbol_count: int
    expected_min_date: str
    expected_max_date: str
    authority_evidence_path: str
    authority_sha256: str
    before_count: int
    deleted_count: int
    after_count: int
    backup_path: str | None
    backup_sha256: str | None
    database_quick_check: str
    trade_proposals_before: int
    trade_proposals_after: int
    broker_orders_before: int
    broker_orders_after: int
    adj_duplicate_groups_before: int
    adj_duplicate_groups_after: int
    daily_bar_duplicate_groups_before: int
    daily_bar_duplicate_groups_after: int
    daily_bars_without_adj_before: int
    daily_bars_without_adj_after: int
    rows: tuple[OrphanAdjFactorEvidenceRow, ...]
    completed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "rows": [asdict(row) for row in self.rows],
        }


class DailyBarFetcher(Protocol):
    def get_daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...


_PROXY_ENVIRONMENT_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scalar_count(connection: sqlite3.Connection, table: str) -> int:
    if table not in {"trade_proposals", "broker_orders"}:
        raise ValueError(f"unsupported safety table: {table}")
    row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    if row is None:
        raise RuntimeError(f"failed to count {table}")
    return int(row[0])


def _quick_check(connection: sqlite3.Connection) -> str:
    row = connection.execute("PRAGMA quick_check").fetchone()
    if row is None:
        raise RuntimeError("PRAGMA quick_check returned no result")
    return str(row[0])


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _duplicate_key_group_count(
    connection: sqlite3.Connection,
    table: str,
) -> int:
    if table not in {"daily_bars", "adj_factors"}:
        raise ValueError(f"unsupported key-gate table: {table}")
    row = connection.execute(
        f"""
        SELECT COUNT(*)
        FROM (
            SELECT symbol, trade_date
            FROM {table}
            GROUP BY symbol, trade_date
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()
    if row is None:
        raise RuntimeError(f"failed to count duplicate keys in {table}")
    return int(row[0])


def _daily_bars_without_adj_count(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        """
        SELECT COUNT(*)
        FROM daily_bars AS daily
        LEFT JOIN adj_factors AS adj
          ON adj.symbol = daily.symbol
         AND adj.trade_date = daily.trade_date
        WHERE adj.id IS NULL
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("failed to count daily bars without adjustment factors")
    return int(row[0])


def _load_orphan_adj_factors(
    connection: sqlite3.Connection,
) -> tuple[OrphanAdjFactorEvidenceRow, ...]:
    rows = connection.execute(
        """
        SELECT adj.id, adj.symbol, adj.trade_date, adj.source, adj.adj_factor
        FROM adj_factors AS adj
        LEFT JOIN daily_bars AS daily
          ON daily.symbol = adj.symbol
         AND daily.trade_date = adj.trade_date
        WHERE daily.id IS NULL
        ORDER BY adj.symbol, adj.trade_date, adj.id
        """
    ).fetchall()
    return tuple(
        OrphanAdjFactorEvidenceRow(
            row_id=int(row[0]),
            symbol=str(row[1]),
            trade_date=str(row[2]),
            source=str(row[3]),
            adj_factor=float(row[4]),
        )
        for row in rows
    )


def _load_adj_factors_for_keys(
    connection: sqlite3.Connection,
    keys: tuple[tuple[str, str], ...],
) -> tuple[OrphanAdjFactorEvidenceRow, ...]:
    rows: list[OrphanAdjFactorEvidenceRow] = []
    for symbol, trade_date in keys:
        matches = connection.execute(
            """
            SELECT id, symbol, trade_date, source, adj_factor
            FROM adj_factors
            WHERE symbol = ? AND trade_date = ?
            ORDER BY id
            """,
            (symbol, trade_date),
        ).fetchall()
        rows.extend(
            OrphanAdjFactorEvidenceRow(
                row_id=int(row[0]),
                symbol=str(row[1]),
                trade_date=str(row[2]),
                source=str(row[3]),
                adj_factor=float(row[4]),
            )
            for row in matches
        )
    return tuple(sorted(rows, key=lambda row: (row.symbol, row.trade_date, row.row_id)))


def _load_authority_delete_keys(
    evidence_path: Path,
    *,
    expected_count: int,
    expected_symbol_count: int,
    expected_min_date: str,
    expected_max_date: str,
) -> tuple[tuple[tuple[str, str], ...], str]:
    resolved_path = evidence_path.resolve()
    if not resolved_path.is_file():
        raise FileNotFoundError(resolved_path)
    try:
        document = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"invalid authority evidence JSON: {resolved_path}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("rows"), list):
        raise RuntimeError("authority evidence must contain a rows list")

    keys: list[tuple[str, str]] = []
    for item in document["rows"]:
        if not isinstance(item, dict) or item.get("action") != "delete":
            continue
        symbol = item.get("symbol")
        trade_date = item.get("trade_date")
        source = item.get("source")
        if (
            not isinstance(symbol, str)
            or not isinstance(trade_date, str)
            or source != "sina"
        ):
            raise RuntimeError(
                "authority delete row must have string symbol/trade_date and source=sina"
            )
        try:
            date.fromisoformat(trade_date)
        except ValueError as exc:
            raise RuntimeError(
                f"authority evidence contains invalid trade_date: {trade_date}"
            ) from exc
        keys.append((symbol, trade_date))

    unique_keys = set(keys)
    if len(keys) != expected_count or len(unique_keys) != expected_count:
        raise RuntimeError(
            "authority delete-key count changed; refusing mutation: "
            f"expected={expected_count}, rows={len(keys)}, unique={len(unique_keys)}"
        )
    symbol_count = len({symbol for symbol, _ in unique_keys})
    minimum = min(trade_date for _, trade_date in unique_keys)
    maximum = max(trade_date for _, trade_date in unique_keys)
    if (
        symbol_count != expected_symbol_count
        or minimum != expected_min_date
        or maximum != expected_max_date
    ):
        raise RuntimeError(
            "authority delete-key scope changed; refusing mutation: "
            f"symbols={symbol_count}/{expected_symbol_count}, "
            f"dates={minimum}..{maximum}/{expected_min_date}..{expected_max_date}"
        )
    return tuple(sorted(unique_keys)), _sha256(resolved_path)


def _load_mock_rows(connection: sqlite3.Connection) -> tuple[DailyBarEvidenceRow, ...]:
    rows = connection.execute(
        """
        SELECT symbol, trade_date, source
        FROM daily_bars
        WHERE source = 'mock'
        ORDER BY symbol, trade_date
        """
    ).fetchall()
    return tuple(
        DailyBarEvidenceRow(
            symbol=str(row[0]),
            trade_date=str(row[1]),
            source=str(row[2]),
        )
        for row in rows
    )


def _load_invalid_daily_bars(
    connection: sqlite3.Connection,
) -> tuple[InvalidDailyBarRow, ...]:
    rows = connection.execute(
        """
        SELECT id, symbol, trade_date, source, open, high, low, close, volume, amount
        FROM daily_bars
        WHERE open <= 0 OR high <= 0 OR low <= 0 OR close <= 0
        ORDER BY symbol, trade_date
        """
    ).fetchall()
    return tuple(
        InvalidDailyBarRow(
            row_id=int(row[0]),
            symbol=str(row[1]),
            trade_date=str(row[2]),
            source=str(row[3]),
            open=float(row[4]),
            high=float(row[5]),
            low=float(row[6]),
            close=float(row[7]),
            volume=float(row[8]),
            amount=float(row[9]) if row[9] is not None else None,
        )
        for row in rows
    )


def clear_proxy_environment() -> tuple[str, ...]:
    """Remove inherited proxy variables from this process without exposing values."""

    cleared = tuple(key for key in _PROXY_ENVIRONMENT_KEYS if key in os.environ)
    for key in cleared:
        del os.environ[key]
    return cleared


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not pd.notna(result):
        return None
    return result


def _valid_price_row(values: dict[str, float | None]) -> bool:
    open_value = values["open"]
    high_value = values["high"]
    low_value = values["low"]
    close_value = values["close"]
    if any(
        value is None or value <= 0
        for value in (open_value, high_value, low_value, close_value)
    ):
        return False
    assert open_value is not None
    assert high_value is not None
    assert low_value is not None
    assert close_value is not None
    return (
        high_value >= max(open_value, close_value)
        and low_value <= min(open_value, close_value)
        and high_value >= low_value
    )


def _fetch_sina_repair_rows(
    invalid_rows: tuple[InvalidDailyBarRow, ...],
    fetcher: DailyBarFetcher,
) -> tuple[SinaRepairEvidenceRow, ...]:
    by_symbol: dict[str, list[InvalidDailyBarRow]] = {}
    for row in invalid_rows:
        by_symbol.setdefault(row.symbol, []).append(row)

    evidence: list[SinaRepairEvidenceRow] = []
    for symbol in sorted(by_symbol):
        symbol_rows = by_symbol[symbol]
        parsed_dates = [date.fromisoformat(row.trade_date) for row in symbol_rows]
        frame = fetcher.get_daily_bars(symbol, min(parsed_dates), max(parsed_dates))
        if "date" not in frame.columns:
            raise RuntimeError(f"Sina refetch missing date column for {symbol}")
        normalized = frame.copy()
        normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce").dt.date
        normalized = normalized.dropna(subset=["date"]).drop_duplicates("date", keep="last")
        fetched_by_date = {
            item_date: record
            for item_date, record in normalized.set_index("date").iterrows()
            if isinstance(item_date, date)
        }

        for row, trade_date in zip(symbol_rows, parsed_dates, strict=True):
            fetched = fetched_by_date.get(trade_date)
            values = {
                field: _finite_float(fetched[field])
                if fetched is not None and field in fetched
                else None
                for field in ("open", "high", "low", "close", "volume", "amount")
            }
            if _valid_price_row(values):
                action = "repair"
                classification = "sina_refetch_valid"
            else:
                action = "delete"
                has_activity = row.volume > 0 or (row.amount is not None and row.amount > 0)
                classification = (
                    "sina_zero_ohlc_with_trading_activity"
                    if has_activity
                    else "sina_zero_ohlc_non_trading_placeholder"
                )
            evidence.append(
                SinaRepairEvidenceRow(
                    row_id=row.row_id,
                    symbol=row.symbol,
                    trade_date=row.trade_date,
                    source=row.source,
                    stored_open=row.open,
                    stored_high=row.high,
                    stored_low=row.low,
                    stored_close=row.close,
                    stored_volume=row.volume,
                    stored_amount=row.amount,
                    fetched_open=values["open"],
                    fetched_high=values["high"],
                    fetched_low=values["low"],
                    fetched_close=values["close"],
                    fetched_volume=values["volume"],
                    fetched_amount=values["amount"],
                    action=action,
                    classification=classification,
                )
            )
    return tuple(evidence)


def _online_backup(source: sqlite3.Connection, backup_path: Path) -> tuple[Path, str]:
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = backup_path.with_suffix(f"{backup_path.suffix}.partial")
    if temporary_path.exists():
        temporary_path.unlink()

    destination = sqlite3.connect(temporary_path)
    try:
        source.backup(destination, pages=2_048, sleep=0.05)
        if _quick_check(destination) != "ok":
            raise RuntimeError("backup PRAGMA quick_check failed")
    finally:
        destination.close()

    os.replace(temporary_path, backup_path)
    return backup_path, _sha256(backup_path)


def cleanup_mock_daily_bars(
    *,
    database_path: Path,
    backup_directory: Path,
    expected_count: int,
    apply: bool,
) -> MockCleanupResult:
    """Remove only bootstrap mock bars with a fail-closed count guard.

    A first successful mutation requires an online SQLite backup. Re-running the
    command against an already-clean database is an explicit idempotent no-op.
    """

    database_path = database_path.resolve()
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    if expected_count <= 0:
        raise ValueError("expected_count must be positive")

    connection = sqlite3.connect(database_path, timeout=15.0)
    connection.execute("PRAGMA busy_timeout=15000")
    try:
        before_rows = _load_mock_rows(connection)
        before_count = len(before_rows)
        proposals_before = _scalar_count(connection, "trade_proposals")
        orders_before = _scalar_count(connection, "broker_orders")
        quick_check_before = _quick_check(connection)
        if quick_check_before != "ok":
            raise RuntimeError(f"database PRAGMA quick_check failed: {quick_check_before}")

        if before_count == 0:
            completed_at = datetime.now(UTC).isoformat()
            return MockCleanupResult(
                status="already_clean",
                expected_count=expected_count,
                deleted_count=0,
                before_count=0,
                after_count=0,
                backup_path=None,
                backup_sha256=None,
                database_quick_check=quick_check_before,
                trade_proposals_before=proposals_before,
                trade_proposals_after=proposals_before,
                broker_orders_before=orders_before,
                broker_orders_after=orders_before,
                rows=(),
                completed_at=completed_at,
            )

        if before_count != expected_count:
            raise RuntimeError(
                "mock daily-bar count changed; refusing mutation: "
                f"expected={expected_count}, actual={before_count}"
            )

        if not apply:
            completed_at = datetime.now(UTC).isoformat()
            return MockCleanupResult(
                status="dry_run",
                expected_count=expected_count,
                deleted_count=0,
                before_count=before_count,
                after_count=before_count,
                backup_path=None,
                backup_sha256=None,
                database_quick_check=quick_check_before,
                trade_proposals_before=proposals_before,
                trade_proposals_after=proposals_before,
                broker_orders_before=orders_before,
                broker_orders_after=orders_before,
                rows=before_rows,
                completed_at=completed_at,
            )

        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_directory.resolve() / f"alphapilot-before-mock-cleanup-{timestamp}.db"
        completed_backup, backup_sha256 = _online_backup(connection, backup_path)

        connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = connection.execute("DELETE FROM daily_bars WHERE source = 'mock'")
            deleted_count = int(cursor.rowcount)
            after_count = len(_load_mock_rows(connection))
            proposals_after = _scalar_count(connection, "trade_proposals")
            orders_after = _scalar_count(connection, "broker_orders")
            if deleted_count != expected_count or after_count != 0:
                raise RuntimeError(
                    "unexpected cleanup result: "
                    f"deleted={deleted_count}, remaining={after_count}"
                )
            if proposals_after != proposals_before or orders_after != orders_before:
                raise RuntimeError("trading safety-table count changed; rolling back")
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()

        quick_check_after = _quick_check(connection)
        if quick_check_after != "ok":
            raise RuntimeError(f"post-cleanup PRAGMA quick_check failed: {quick_check_after}")
        return MockCleanupResult(
            status="applied",
            expected_count=expected_count,
            deleted_count=deleted_count,
            before_count=before_count,
            after_count=after_count,
            backup_path=str(completed_backup),
            backup_sha256=backup_sha256,
            database_quick_check=quick_check_after,
            trade_proposals_before=proposals_before,
            trade_proposals_after=proposals_after,
            broker_orders_before=orders_before,
            broker_orders_after=orders_after,
            rows=before_rows,
            completed_at=datetime.now(UTC).isoformat(),
        )
    finally:
        connection.close()


def repair_invalid_sina_daily_bars(
    *,
    database_path: Path,
    backup_directory: Path,
    expected_count: int,
    fetcher: DailyBarFetcher,
    apply: bool,
    cleared_proxy_keys: tuple[str, ...],
) -> SinaRepairResult:
    """Refetch invalid BSE bars from Sina, repairing only valid values.

    Rows that Sina still returns with invalid OHLC are deleted instead of being
    reconstructed from amount/volume. This avoids inventing historical prices.
    """

    database_path = database_path.resolve()
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    if expected_count <= 0:
        raise ValueError("expected_count must be positive")

    connection = sqlite3.connect(database_path, timeout=15.0)
    connection.execute("PRAGMA busy_timeout=15000")
    try:
        invalid_rows = _load_invalid_daily_bars(connection)
        before_count = len(invalid_rows)
        proposals_before = _scalar_count(connection, "trade_proposals")
        orders_before = _scalar_count(connection, "broker_orders")
        quick_check_before = _quick_check(connection)
        if quick_check_before != "ok":
            raise RuntimeError(f"database PRAGMA quick_check failed: {quick_check_before}")

        if before_count == 0:
            return SinaRepairResult(
                status="already_clean",
                expected_count=expected_count,
                before_count=0,
                repaired_count=0,
                deleted_count=0,
                deleted_adj_factor_count=0,
                after_count=0,
                backup_path=None,
                backup_sha256=None,
                database_quick_check=quick_check_before,
                trade_proposals_before=proposals_before,
                trade_proposals_after=proposals_before,
                broker_orders_before=orders_before,
                broker_orders_after=orders_before,
                cleared_proxy_keys=cleared_proxy_keys,
                rows=(),
                completed_at=datetime.now(UTC).isoformat(),
            )

        if before_count != expected_count:
            raise RuntimeError(
                "invalid daily-bar count changed; refusing mutation: "
                f"expected={expected_count}, actual={before_count}"
            )
        unexpected = [
            row
            for row in invalid_rows
            if row.source != "sina" or not row.symbol.startswith("92")
        ]
        if unexpected:
            raise RuntimeError(
                "invalid daily-bar scope contains non-Sina or non-BSE rows; refusing mutation"
            )

        evidence_rows = _fetch_sina_repair_rows(invalid_rows, fetcher)
        repaired_count = sum(row.action == "repair" for row in evidence_rows)
        deleted_count = sum(row.action == "delete" for row in evidence_rows)
        if repaired_count + deleted_count != expected_count:
            raise RuntimeError("repair plan does not cover every invalid daily-bar row")
        delete_keys = tuple(
            sorted(
                (row.symbol, row.trade_date)
                for row in evidence_rows
                if row.action == "delete"
            )
        )
        matching_adj_factors = (
            _load_adj_factors_for_keys(connection, delete_keys)
            if _table_exists(connection, "adj_factors")
            else ()
        )
        if any(row.source != "sina-hfq" for row in matching_adj_factors):
            raise RuntimeError(
                "invalid Sina rows have a non-sina-hfq adjustment factor; refusing mutation"
            )
        planned_adj_factor_deletes = len(matching_adj_factors)

        if not apply:
            return SinaRepairResult(
                status="dry_run",
                expected_count=expected_count,
                before_count=before_count,
                repaired_count=repaired_count,
                deleted_count=deleted_count,
                deleted_adj_factor_count=planned_adj_factor_deletes,
                after_count=before_count,
                backup_path=None,
                backup_sha256=None,
                database_quick_check=quick_check_before,
                trade_proposals_before=proposals_before,
                trade_proposals_after=proposals_before,
                broker_orders_before=orders_before,
                broker_orders_after=orders_before,
                cleared_proxy_keys=cleared_proxy_keys,
                rows=evidence_rows,
                completed_at=datetime.now(UTC).isoformat(),
            )

        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_directory.resolve() / f"alphapilot-before-sina-repair-{timestamp}.db"
        completed_backup, backup_sha256 = _online_backup(connection, backup_path)

        connection.execute("BEGIN IMMEDIATE")
        try:
            if _load_invalid_daily_bars(connection) != invalid_rows:
                raise RuntimeError("invalid daily-bar rows changed during refetch; rolling back")
            if (
                _table_exists(connection, "adj_factors")
                and _load_adj_factors_for_keys(connection, delete_keys)
                != matching_adj_factors
            ):
                raise RuntimeError(
                    "matching adjustment factors changed during refetch; rolling back"
                )
            applied_repairs = 0
            applied_deletes = 0
            applied_adj_factor_deletes = 0
            for row in evidence_rows:
                if row.action == "repair":
                    cursor = connection.execute(
                        """
                        UPDATE daily_bars
                        SET open = ?, high = ?, low = ?, close = ?,
                            volume = ?, amount = ?, ingested_at = ?
                        WHERE id = ? AND source = 'sina'
                          AND (open <= 0 OR high <= 0 OR low <= 0 OR close <= 0)
                        """,
                        (
                            row.fetched_open,
                            row.fetched_high,
                            row.fetched_low,
                            row.fetched_close,
                            row.fetched_volume,
                            row.fetched_amount,
                            datetime.now(UTC).isoformat(),
                            row.row_id,
                        ),
                    )
                    applied_repairs += int(cursor.rowcount)
                elif row.action == "delete":
                    if _table_exists(connection, "adj_factors"):
                        adj_cursor = connection.execute(
                            """
                            DELETE FROM adj_factors
                            WHERE symbol = ? AND trade_date = ? AND source = 'sina-hfq'
                            """,
                            (row.symbol, row.trade_date),
                        )
                        applied_adj_factor_deletes += int(adj_cursor.rowcount)
                    cursor = connection.execute(
                        """
                        DELETE FROM daily_bars
                        WHERE id = ? AND source = 'sina'
                          AND (open <= 0 OR high <= 0 OR low <= 0 OR close <= 0)
                        """,
                        (row.row_id,),
                    )
                    applied_deletes += int(cursor.rowcount)
                else:
                    raise RuntimeError(f"unsupported repair action: {row.action}")

            after_count = len(_load_invalid_daily_bars(connection))
            proposals_after = _scalar_count(connection, "trade_proposals")
            orders_after = _scalar_count(connection, "broker_orders")
            if (
                applied_repairs != repaired_count
                or applied_deletes != deleted_count
                or applied_adj_factor_deletes != planned_adj_factor_deletes
                or after_count != 0
            ):
                raise RuntimeError(
                    "unexpected Sina repair result: "
                    f"repairs={applied_repairs}/{repaired_count}, "
                    f"deletes={applied_deletes}/{deleted_count}, "
                    "adj_factor_deletes="
                    f"{applied_adj_factor_deletes}/{planned_adj_factor_deletes}, "
                    f"remaining={after_count}"
                )
            if proposals_after != proposals_before or orders_after != orders_before:
                raise RuntimeError("trading safety-table count changed; rolling back")
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()

        quick_check_after = _quick_check(connection)
        if quick_check_after != "ok":
            raise RuntimeError(f"post-repair PRAGMA quick_check failed: {quick_check_after}")
        return SinaRepairResult(
            status="applied",
            expected_count=expected_count,
            before_count=before_count,
            repaired_count=repaired_count,
            deleted_count=deleted_count,
            deleted_adj_factor_count=applied_adj_factor_deletes,
            after_count=after_count,
            backup_path=str(completed_backup),
            backup_sha256=backup_sha256,
            database_quick_check=quick_check_after,
            trade_proposals_before=proposals_before,
            trade_proposals_after=proposals_after,
            broker_orders_before=orders_before,
            broker_orders_after=orders_after,
            cleared_proxy_keys=cleared_proxy_keys,
            rows=evidence_rows,
            completed_at=datetime.now(UTC).isoformat(),
        )
    finally:
        connection.close()


def cleanup_orphan_sina_adj_factors(
    *,
    database_path: Path,
    backup_directory: Path,
    authority_evidence_path: Path,
    expected_count: int,
    expected_symbol_count: int,
    expected_min_date: str,
    expected_max_date: str,
    apply: bool,
) -> OrphanAdjFactorCleanupResult:
    """Delete only orphan Sina HFQ factors named by prior row-level evidence.

    The authoritative delete keys must exactly equal the database's complete
    orphan-adjustment set. Any count, key, source, symbol-range, date-range, or
    duplicate-key drift fails closed before a backup or mutation is attempted.
    """

    database_path = database_path.resolve()
    authority_evidence_path = authority_evidence_path.resolve()
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    if expected_count <= 0 or expected_symbol_count <= 0:
        raise ValueError("expected counts must be positive")
    authority_keys, authority_sha256 = _load_authority_delete_keys(
        authority_evidence_path,
        expected_count=expected_count,
        expected_symbol_count=expected_symbol_count,
        expected_min_date=expected_min_date,
        expected_max_date=expected_max_date,
    )
    authority_key_set = set(authority_keys)

    connection = sqlite3.connect(database_path, timeout=15.0)
    connection.execute("PRAGMA busy_timeout=15000")
    try:
        quick_check_before = _quick_check(connection)
        if quick_check_before != "ok":
            raise RuntimeError(f"database PRAGMA quick_check failed: {quick_check_before}")
        proposals_before = _scalar_count(connection, "trade_proposals")
        orders_before = _scalar_count(connection, "broker_orders")
        if proposals_before != 1 or orders_before != 1:
            raise RuntimeError(
                "trading safety-table counts must be exactly 1/1; "
                f"actual={proposals_before}/{orders_before}"
            )
        adj_duplicates_before = _duplicate_key_group_count(connection, "adj_factors")
        daily_duplicates_before = _duplicate_key_group_count(connection, "daily_bars")
        if adj_duplicates_before != 0 or daily_duplicates_before != 0:
            raise RuntimeError(
                "duplicate symbol/trade_date groups detected; refusing mutation: "
                f"adj_factors={adj_duplicates_before}, daily_bars={daily_duplicates_before}"
            )
        daily_without_adj_before = _daily_bars_without_adj_count(connection)
        orphan_rows = _load_orphan_adj_factors(connection)
        orphan_keys = {(row.symbol, row.trade_date) for row in orphan_rows}
        authority_rows = _load_adj_factors_for_keys(connection, authority_keys)

        if not orphan_rows:
            if authority_rows:
                raise RuntimeError(
                    "authority-key adjustment factors still exist but are not orphaned; "
                    "refusing idempotent no-op"
                )
            return OrphanAdjFactorCleanupResult(
                status="already_clean",
                expected_count=expected_count,
                expected_symbol_count=expected_symbol_count,
                expected_min_date=expected_min_date,
                expected_max_date=expected_max_date,
                authority_evidence_path=str(authority_evidence_path),
                authority_sha256=authority_sha256,
                before_count=0,
                deleted_count=0,
                after_count=0,
                backup_path=None,
                backup_sha256=None,
                database_quick_check=quick_check_before,
                trade_proposals_before=proposals_before,
                trade_proposals_after=proposals_before,
                broker_orders_before=orders_before,
                broker_orders_after=orders_before,
                adj_duplicate_groups_before=adj_duplicates_before,
                adj_duplicate_groups_after=adj_duplicates_before,
                daily_bar_duplicate_groups_before=daily_duplicates_before,
                daily_bar_duplicate_groups_after=daily_duplicates_before,
                daily_bars_without_adj_before=daily_without_adj_before,
                daily_bars_without_adj_after=daily_without_adj_before,
                rows=(),
                completed_at=datetime.now(UTC).isoformat(),
            )

        if orphan_keys != authority_key_set or len(orphan_rows) != expected_count:
            raise RuntimeError(
                "database orphan keys do not exactly equal authority delete keys; "
                "refusing mutation: "
                f"orphans={len(orphan_rows)}, authority={len(authority_keys)}, "
                f"missing={len(authority_key_set - orphan_keys)}, "
                f"unexpected={len(orphan_keys - authority_key_set)}"
            )
        if authority_rows != orphan_rows:
            raise RuntimeError(
                "authority-key adjustment rows are not exactly the orphan rows; "
                "refusing mutation"
            )
        if any(row.source != "sina-hfq" for row in orphan_rows):
            raise RuntimeError("orphan adjustment source drifted from sina-hfq")

        if not apply:
            return OrphanAdjFactorCleanupResult(
                status="dry_run",
                expected_count=expected_count,
                expected_symbol_count=expected_symbol_count,
                expected_min_date=expected_min_date,
                expected_max_date=expected_max_date,
                authority_evidence_path=str(authority_evidence_path),
                authority_sha256=authority_sha256,
                before_count=len(orphan_rows),
                deleted_count=0,
                after_count=len(orphan_rows),
                backup_path=None,
                backup_sha256=None,
                database_quick_check=quick_check_before,
                trade_proposals_before=proposals_before,
                trade_proposals_after=proposals_before,
                broker_orders_before=orders_before,
                broker_orders_after=orders_before,
                adj_duplicate_groups_before=adj_duplicates_before,
                adj_duplicate_groups_after=adj_duplicates_before,
                daily_bar_duplicate_groups_before=daily_duplicates_before,
                daily_bar_duplicate_groups_after=daily_duplicates_before,
                daily_bars_without_adj_before=daily_without_adj_before,
                daily_bars_without_adj_after=daily_without_adj_before,
                rows=orphan_rows,
                completed_at=datetime.now(UTC).isoformat(),
            )

        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = (
            backup_directory.resolve()
            / f"alphapilot-before-orphan-sina-adj-cleanup-{timestamp}.db"
        )
        completed_backup, backup_sha256 = _online_backup(connection, backup_path)

        connection.execute("BEGIN IMMEDIATE")
        try:
            if _load_orphan_adj_factors(connection) != orphan_rows:
                raise RuntimeError(
                    "orphan adjustment rows changed before mutation; rolling back"
                )
            if _scalar_count(connection, "trade_proposals") != proposals_before:
                raise RuntimeError("trade_proposals changed before mutation; rolling back")
            if _scalar_count(connection, "broker_orders") != orders_before:
                raise RuntimeError("broker_orders changed before mutation; rolling back")
            if (
                _duplicate_key_group_count(connection, "adj_factors") != 0
                or _duplicate_key_group_count(connection, "daily_bars") != 0
            ):
                raise RuntimeError("duplicate key gate changed before mutation; rolling back")

            deleted_count = 0
            for row in orphan_rows:
                cursor = connection.execute(
                    """
                    DELETE FROM adj_factors
                    WHERE id = ?
                      AND symbol = ?
                      AND trade_date = ?
                      AND source = 'sina-hfq'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM daily_bars
                          WHERE daily_bars.symbol = adj_factors.symbol
                            AND daily_bars.trade_date = adj_factors.trade_date
                      )
                    """,
                    (row.row_id, row.symbol, row.trade_date),
                )
                deleted_count += int(cursor.rowcount)

            after_rows = _load_orphan_adj_factors(connection)
            remaining_authority_rows = _load_adj_factors_for_keys(
                connection, authority_keys
            )
            proposals_after = _scalar_count(connection, "trade_proposals")
            orders_after = _scalar_count(connection, "broker_orders")
            adj_duplicates_after = _duplicate_key_group_count(
                connection, "adj_factors"
            )
            daily_duplicates_after = _duplicate_key_group_count(
                connection, "daily_bars"
            )
            daily_without_adj_after = _daily_bars_without_adj_count(connection)
            if (
                deleted_count != expected_count
                or after_rows
                or remaining_authority_rows
            ):
                raise RuntimeError(
                    "unexpected orphan adjustment cleanup result: "
                    f"deleted={deleted_count}/{expected_count}, "
                    f"orphans={len(after_rows)}, "
                    f"authority_rows={len(remaining_authority_rows)}"
                )
            if proposals_after != proposals_before or orders_after != orders_before:
                raise RuntimeError("trading safety-table count changed; rolling back")
            if adj_duplicates_after != 0 or daily_duplicates_after != 0:
                raise RuntimeError("duplicate key gate failed after cleanup; rolling back")
            if daily_without_adj_after != daily_without_adj_before:
                raise RuntimeError(
                    "daily-bar to adjustment-factor key gap changed; rolling back: "
                    f"before={daily_without_adj_before}, "
                    f"after={daily_without_adj_after}"
                )
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()

        quick_check_after = _quick_check(connection)
        if quick_check_after != "ok":
            raise RuntimeError(
                f"post-cleanup PRAGMA quick_check failed: {quick_check_after}"
            )
        return OrphanAdjFactorCleanupResult(
            status="applied",
            expected_count=expected_count,
            expected_symbol_count=expected_symbol_count,
            expected_min_date=expected_min_date,
            expected_max_date=expected_max_date,
            authority_evidence_path=str(authority_evidence_path),
            authority_sha256=authority_sha256,
            before_count=len(orphan_rows),
            deleted_count=deleted_count,
            after_count=len(after_rows),
            backup_path=str(completed_backup),
            backup_sha256=backup_sha256,
            database_quick_check=quick_check_after,
            trade_proposals_before=proposals_before,
            trade_proposals_after=proposals_after,
            broker_orders_before=orders_before,
            broker_orders_after=orders_after,
            adj_duplicate_groups_before=adj_duplicates_before,
            adj_duplicate_groups_after=adj_duplicates_after,
            daily_bar_duplicate_groups_before=daily_duplicates_before,
            daily_bar_duplicate_groups_after=daily_duplicates_after,
            daily_bars_without_adj_before=daily_without_adj_before,
            daily_bars_without_adj_after=daily_without_adj_after,
            rows=orphan_rows,
            completed_at=datetime.now(UTC).isoformat(),
        )
    finally:
        connection.close()


def write_cleanup_evidence(result: MockCleanupResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_sina_repair_evidence(result: SinaRepairResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_orphan_adj_factor_cleanup_evidence(
    result: OrphanAdjFactorCleanupResult,
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
