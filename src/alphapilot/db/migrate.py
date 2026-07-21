from __future__ import annotations

import re

from sqlalchemy import Engine, inspect, text

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
    ("securities", "snapshot_at", "TIMESTAMP"),
]

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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


def run_migrations(engine: Engine) -> list[str]:
    """Apply the centrally registered idempotent column migrations."""

    applied: list[str] = []
    for table, column, ddl_type in MIGRATIONS:
        if ensure_column(engine, table, column, ddl_type):
            applied.append(f"{table}.{column}")
    return applied
