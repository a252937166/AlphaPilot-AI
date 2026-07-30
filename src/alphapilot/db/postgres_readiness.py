"""Offline PostgreSQL migration-readiness inventory.

This module deliberately compiles SQL and inspects repository text only. It
does not create an engine, connect to PostgreSQL, or read the production
SQLite database.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from alphapilot.db.models import Base

CheckStatus = Literal["pass", "blocker"]
REPORT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    check_id: str
    status: CheckStatus
    summary: str
    evidence: list[str]
    remediation: str | None = None


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _source_matches(
    root: Path,
    paths: list[Path],
    pattern: str,
) -> list[str]:
    expression = re.compile(pattern)
    evidence: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if expression.search(line):
                evidence.append(f"{_relative(root, path)}:{line_number}")
    return evidence


def _python_files(root: Path, *directories: str) -> list[Path]:
    files: list[Path] = []
    for directory in directories:
        candidate = root / directory
        if candidate.is_dir():
            files.extend(candidate.rglob("*.py"))
    return sorted(
        path
        for path in files
        if path.name != "postgres_readiness.py" and path.name != "test_postgres_readiness.py"
    )


def _compile_schema() -> ReadinessCheck:
    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    failures: list[str] = []
    index_count = 0
    tables = sorted(Base.metadata.tables.values(), key=lambda table: table.name)
    for table in tables:
        try:
            str(CreateTable(table).compile(dialect=dialect))
            for index in sorted(table.indexes, key=lambda item: item.name or ""):
                str(CreateIndex(index).compile(dialect=dialect))
                index_count += 1
        except Exception as exc:
            failures.append(f"{table.name}: {type(exc).__name__}: {exc}")
    if failures:
        return ReadinessCheck(
            check_id="PG_SCHEMA_COMPILES",
            status="blocker",
            summary="ORM schema cannot be compiled for PostgreSQL.",
            evidence=failures,
            remediation="Make every ORM table and index compile with the PostgreSQL dialect.",
        )
    return ReadinessCheck(
        check_id="PG_SCHEMA_COMPILES",
        status="pass",
        summary="All ORM tables and indexes compile with the PostgreSQL dialect.",
        evidence=[f"tables={len(tables)}", f"indexes={index_count}"],
    )


def _absence_blocker(
    *,
    check_id: str,
    summary: str,
    evidence: list[str],
    remediation: str,
) -> ReadinessCheck:
    return ReadinessCheck(
        check_id=check_id,
        status="blocker",
        summary=summary,
        evidence=evidence,
        remediation=remediation,
    )


def _repository_checks(root: Path) -> list[ReadinessCheck]:
    source_files = _python_files(root, "src/alphapilot")
    runtime_and_scripts = _python_files(root, "src/alphapilot", "scripts")
    test_files = _python_files(root, "tests")
    engine_path = root / "src/alphapilot/db/engine.py"
    model_path = root / "src/alphapilot/db/models.py"

    alembic_evidence = [
        path
        for path in (root / "alembic.ini", root / "alembic", root / "migrations")
        if path.exists()
    ]
    versioned_migrations = (
        ReadinessCheck(
            check_id="PG_VERSIONED_MIGRATIONS",
            status="pass",
            summary="A versioned schema migration tree exists.",
            evidence=[_relative(root, path) for path in alembic_evidence],
        )
        if alembic_evidence
        else _absence_blocker(
            check_id="PG_VERSIONED_MIGRATIONS",
            summary="No versioned PostgreSQL migration history exists.",
            evidence=["alembic.ini=missing", "alembic/=missing", "migrations/=missing"],
            remediation="Create a reviewed Alembic baseline and forward-only revisions.",
        )
    )

    startup_ddl = _source_matches(
        root,
        [engine_path],
        r"Base\.metadata\.create_all|run_migrations\(",
    )
    startup_check = (
        _absence_blocker(
            check_id="PG_STARTUP_DDL_OWNERSHIP",
            summary="Application startup still owns schema creation or raw migrations.",
            evidence=startup_ddl,
            remediation=(
                "Make PostgreSQL startup validate a schema version only; run migrations "
                "as a separately approved operation."
            ),
        )
        if startup_ddl
        else ReadinessCheck(
            check_id="PG_STARTUP_DDL_OWNERSHIP",
            status="pass",
            summary="Application startup does not own schema DDL.",
            evidence=["src/alphapilot/db/engine.py"],
        )
    )

    sqlite_dml = _source_matches(
        root,
        source_files,
        r"sqlalchemy\.dialects\.sqlite|sqlite_insert\(",
    )
    upsert_check = (
        _absence_blocker(
            check_id="PG_DIALECT_NEUTRAL_UPSERTS",
            summary="Runtime write paths contain SQLite-specific insert/upsert statements.",
            evidence=sqlite_dml,
            remediation=(
                "Introduce dialect adapters and PostgreSQL ON CONFLICT integration tests "
                "for every affected write path."
            ),
        )
        if sqlite_dml
        else ReadinessCheck(
            check_id="PG_DIALECT_NEUTRAL_UPSERTS",
            status="pass",
            summary="No SQLite-specific runtime upsert marker was found.",
            evidence=[],
        )
    )

    sqlite_only = _source_matches(
        root,
        runtime_and_scripts,
        r"\bsqlite3\.connect\(|\bPRAGMA\b|\bsqlite_master\b|\bjulianday\(",
    )
    sqlite_tools_check = (
        _absence_blocker(
            check_id="PG_SQLITE_ONLY_TOOLS",
            summary="Operational and research tools still require SQLite APIs or SQL.",
            evidence=sqlite_only[:40],
            remediation=(
                "Classify each tool as SQLite-only maintenance or add a PostgreSQL "
                "implementation before claiming feature parity."
            ),
        )
        if sqlite_only
        else ReadinessCheck(
            check_id="PG_SQLITE_ONLY_TOOLS",
            status="pass",
            summary="No SQLite-only operational or research SQL marker was found.",
            evidence=[],
        )
    )

    generic_json = _source_matches(root, [model_path], r"\bmapped_column\(JSON|\b: JSON\b")
    json_check = (
        _absence_blocker(
            check_id="PG_JSON_POLICY",
            summary="PostgreSQL JSON versus JSONB and indexing policy is not defined.",
            evidence=generic_json[:20],
            remediation="Choose JSONB/index policy and encode it in versioned migrations.",
        )
        if generic_json
        else ReadinessCheck(
            check_id="PG_JSON_POLICY",
            status="pass",
            summary="No unresolved generic JSON mapping marker was found.",
            evidence=[],
        )
    )

    engine_text = engine_path.read_text(encoding="utf-8") if engine_path.is_file() else ""
    utc_markers = ("timezone=UTC", "SET TIME ZONE", "options=-c timezone")
    utc_check = _absence_blocker(
        check_id="PG_UTC_SESSION_POLICY",
        summary="PostgreSQL session UTC enforcement is not configured.",
        evidence=["src/alphapilot/db/engine.py"],
        remediation="Pin server/session timezone to UTC and add round-trip tests.",
    )
    if any(marker in engine_text for marker in utc_markers):
        utc_check = ReadinessCheck(
            check_id="PG_UTC_SESSION_POLICY",
            status="pass",
            summary="PostgreSQL session UTC enforcement marker exists.",
            evidence=["src/alphapilot/db/engine.py"],
        )

    pool_markers = ("pool_size", "max_overflow", "pool_timeout", "connect_timeout")
    pool_check = _absence_blocker(
        check_id="PG_POOL_TIMEOUT_POLICY",
        summary="PostgreSQL pool and timeout policy is not explicit.",
        evidence=["src/alphapilot/db/engine.py"],
        remediation=(
            "Configure pool size/overflow/timeout and connection or statement timeout, "
            "then soak API plus scheduler writers."
        ),
    )
    if all(marker in engine_text for marker in pool_markers):
        pool_check = ReadinessCheck(
            check_id="PG_POOL_TIMEOUT_POLICY",
            status="pass",
            summary="PostgreSQL pool and connection timeout markers exist.",
            evidence=["src/alphapilot/db/engine.py"],
        )

    sequence_evidence = _source_matches(root, runtime_and_scripts, r"\bsetval\s*\(")
    sequence_check = (
        ReadinessCheck(
            check_id="PG_SEQUENCE_RESEED",
            status="pass",
            summary="A PostgreSQL sequence reseed implementation exists.",
            evidence=sequence_evidence,
        )
        if sequence_evidence
        else _absence_blocker(
            check_id="PG_SEQUENCE_RESEED",
            summary="No PostgreSQL identity sequence reseed step exists.",
            evidence=["setval implementation=missing"],
            remediation="Reseed every imported integer identity after bulk load.",
        )
    )

    pg_tests = _source_matches(
        root,
        test_files,
        r"pytest\.mark\.postgres|POSTGRES_TEST_DATABASE_URL|test_postgres_integration",
    )
    integration_check = (
        ReadinessCheck(
            check_id="PG_INTEGRATION_TESTS",
            status="pass",
            summary="PostgreSQL integration-test markers exist.",
            evidence=pg_tests,
        )
        if pg_tests
        else _absence_blocker(
            check_id="PG_INTEGRATION_TESTS",
            summary="No PostgreSQL integration or concurrent-writer test suite exists.",
            evidence=["tests/ PostgreSQL marker=missing"],
            remediation=(
                "Run schema, CRUD, PIT, API+scheduler concurrency, restart, and rollback "
                "tests against disposable PostgreSQL."
            ),
        )
    )

    return [
        versioned_migrations,
        startup_check,
        upsert_check,
        sqlite_tools_check,
        json_check,
        utc_check,
        pool_check,
        sequence_check,
        integration_check,
        _absence_blocker(
            check_id="PG_DATA_PARITY_SIGNOFF",
            summary="Offline source inspection cannot authorize production data cutover.",
            evidence=[
                "P3.3-S2 architect signoff=required",
                "row-count/hash/PIT/sequence parity=not executed",
            ],
            remediation=(
                "After S2 signoff, run an approved snapshot export/import and independent "
                "row, digest, PIT, safety-count, and rollback verification."
            ),
        ),
    ]


def build_postgres_readiness_report(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    checks = [_compile_schema(), *_repository_checks(root)]
    blockers = [check.check_id for check in checks if check.status == "blocker"]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "offline_static",
        "ready": not blockers,
        "summary": {
            "passes": sum(check.status == "pass" for check in checks),
            "blockers": len(blockers),
            "blocker_ids": blockers,
        },
        "guardrails": {
            "postgres_connection_attempted": False,
            "production_database_read": False,
            "production_database_written": False,
        },
        "checks": [asdict(check) for check in checks],
    }
