from __future__ import annotations

import json
import re

from sqlalchemy import Engine, Index, MetaData, Table, inspect, text

MIGRATIONS: list[tuple[str, str, str]] = [
    ("securities", "industry_csrc", "TEXT"),
    ("securities", "industry_futu", "TEXT"),
    ("securities", "is_st", "BOOLEAN DEFAULT FALSE"),
    ("securities", "list_status", "TEXT"),
    ("securities", "market_cap", "FLOAT"),
    ("securities", "float_cap", "FLOAT"),
    ("securities", "pe_ttm", "FLOAT"),
    ("securities", "pb", "FLOAT"),
    ("securities", "turnover_rate", "FLOAT"),
    ("securities", "style_tag", "TEXT"),
    ("securities", "snapshot_at", "TIMESTAMP"),
    ("screening_runs", "universe", "TEXT DEFAULT 'custom'"),
    ("screening_runs", "filters", "JSON DEFAULT '{}'"),
    ("style_daily", "source_fingerprint", "TEXT DEFAULT ''"),
    ("alerts", "target_low", "FLOAT"),
    ("alerts", "target_high", "FLOAT"),
    ("alerts", "suggested_notional", "FLOAT"),
]

INDEX_MIGRATIONS: list[tuple[str, str, tuple[str, ...]]] = [
    ("daily_bars", "ix_daily_bars_trade_date_symbol", ("trade_date", "symbol")),
    ("adj_factors", "ix_adj_trade_date_symbol", ("trade_date", "symbol")),
    ("valuation_daily", "ix_valuation_trade_date_symbol", ("trade_date", "symbol")),
]

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TABLE_MIGRATIONS = ("sector_constituent_snapshots",)


def _validated_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid SQL identifier: {value!r}")
    return value


def _validated_ddl_type(value: str) -> str:
    normalized = value.strip()
    if not normalized or any(token in normalized for token in (";", "--", "/*", "*/", "\x00")):
        raise ValueError("unsafe or empty DDL type")
    return normalized


def _sqlite_columns(engine: Engine, table: str) -> set[str]:
    quoted_table = engine.dialect.identifier_preparer.quote(table)
    with engine.connect() as connection:
        rows = connection.execute(text(f"PRAGMA table_info({quoted_table})")).mappings()
        return {str(row["name"]) for row in rows}


def ensure_column(engine: Engine, table: str, column: str, ddl_type: str) -> bool:
    """Add a missing column and report whether the schema changed.

    SQLite needs an explicit PRAGMA lookup because ``create_all`` never alters an
    existing table. Other SQLAlchemy dialects use their native inspector.
    """

    table = _validated_identifier(table)
    column = _validated_identifier(column)
    ddl_type = _validated_ddl_type(ddl_type)

    inspector = inspect(engine)
    if not inspector.has_table(table):
        raise ValueError(f"table does not exist: {table}")

    if engine.dialect.name == "sqlite":
        columns = _sqlite_columns(engine, table)
    else:
        columns = {str(item["name"]) for item in inspector.get_columns(table)}
    if column in columns:
        return False

    preparer = engine.dialect.identifier_preparer
    quoted_table = preparer.quote(table)
    quoted_column = preparer.quote(column)
    with engine.begin() as connection:
        connection.execute(
            text(f"ALTER TABLE {quoted_table} ADD COLUMN {quoted_column} {ddl_type}")
        )
    return True


def ensure_model_table(engine: Engine, table_name: str) -> bool:
    """Create one centrally modelled table for an existing database."""

    table_name = _validated_identifier(table_name)
    if inspect(engine).has_table(table_name):
        return False
    from alphapilot.db.models import Base

    table = Base.metadata.tables.get(table_name)
    if table is None:
        raise ValueError(f"model table does not exist: {table_name}")
    table.create(bind=engine, checkfirst=True)
    return True


def ensure_index(
    engine: Engine,
    table: str,
    index_name: str,
    columns: tuple[str, ...],
) -> bool:
    """Create one named index safely and report whether the schema changed."""

    table = _validated_identifier(table)
    index_name = _validated_identifier(index_name)
    columns = tuple(_validated_identifier(column) for column in columns)
    if not columns or len(set(columns)) != len(columns):
        raise ValueError("index columns must be non-empty and unique")

    inspector = inspect(engine)
    if not inspector.has_table(table):
        raise ValueError(f"table does not exist: {table}")
    available_columns = {str(item["name"]) for item in inspector.get_columns(table)}
    missing = [column for column in columns if column not in available_columns]
    if missing:
        raise ValueError(f"index columns do not exist on {table}: {missing}")
    existing_indexes = inspector.get_indexes(table)
    for item in existing_indexes:
        if item.get("name") != index_name:
            continue
        existing_columns = tuple(str(column) for column in item.get("column_names") or ())
        if existing_columns != columns:
            raise ValueError(
                f"index {index_name} already exists with columns {existing_columns}, "
                f"expected {columns}"
            )
        return False
    if any(
        tuple(str(column) for column in item.get("column_names") or ()) == columns
        and not bool(item.get("dialect_options"))
        for item in existing_indexes
    ):
        return False

    metadata = MetaData()
    reflected = Table(table, metadata, autoload_with=engine)
    index = Index(index_name, *(reflected.c[column] for column in columns))
    with engine.begin() as connection:
        index.create(bind=connection, checkfirst=True)
    return True


def run_migrations(engine: Engine) -> list[str]:
    """Apply the centrally registered idempotent column and index migrations."""

    applied: list[str] = []
    for table_name in TABLE_MIGRATIONS:
        if ensure_model_table(engine, table_name):
            applied.append(table_name)
    for table, column, ddl_type in MIGRATIONS:
        if ensure_column(engine, table, column, ddl_type):
            applied.append(f"{table}.{column}")
    for table, index_name, columns in INDEX_MIGRATIONS:
        if inspect(engine).has_table(table) and ensure_index(
            engine, table, index_name, columns
        ):
            applied.append(f"{table}.{index_name}")
    if inspect(engine).has_table("trade_proposals"):
        if ensure_column(engine, "trade_proposals", "idempotency_key", "TEXT"):
            applied.append("trade_proposals.idempotency_key")
        if _ensure_trade_proposal_idempotency(engine):
            applied.append("trade_proposals.idempotency_unique")
    return applied


def _proposal_key(raw: object, proposal_id: str) -> str:
    payload: object = raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
    if isinstance(payload, dict):
        value = payload.get("idempotency_key")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return proposal_id


def _ensure_trade_proposal_idempotency(engine: Engine) -> bool:
    """Backfill legacy keys and create the DB-level concurrency boundary."""

    changed = False
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                "SELECT id, proposal_id, proposal, idempotency_key "
                "FROM trade_proposals ORDER BY id"
            )
        ).mappings()
        used: set[str] = set()
        for row in rows:
            proposal_id = str(row["proposal_id"])
            candidate = _proposal_key(row["proposal"], proposal_id)
            if candidate in used:
                candidate = proposal_id
            if candidate in used:
                candidate = f"legacy-{row['id']}-{proposal_id}"
            used.add(candidate)
            if row["idempotency_key"] != candidate:
                connection.execute(
                    text(
                        "UPDATE trade_proposals SET idempotency_key = :key WHERE id = :id"
                    ),
                    {"key": candidate, "id": row["id"]},
                )
                changed = True
        inspector = inspect(connection)
        index_exists = any(
            item.get("name") == "uq_trade_proposals_idempotency_key"
            for item in inspector.get_indexes("trade_proposals")
        )
        constraint_exists = any(
            item.get("column_names") == ["idempotency_key"]
            for item in inspector.get_unique_constraints("trade_proposals")
        )
        if not index_exists and not constraint_exists:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_trade_proposals_idempotency_key "
                    "ON trade_proposals (idempotency_key)"
                )
            )
            changed = True
    return changed
