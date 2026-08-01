from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path("config/p4_source_spike_v2.yaml")
DEFAULT_REPORT = Path("docs/phase4/reports/P4.1-source-spike-20260802.json")
EXECUTION_INPUTS = (
    DEFAULT_CONFIG,
    Path("scripts/run_p4_source_spike.py"),
    Path("src/alphapilot/jobs/p4_source_spike.py"),
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the bounded P4.1 source-feasibility spike. This command only "
            "uses public news/quote reads and never calls a Futu trade method."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_DIR,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_blob(path: Path) -> bytes:
    return subprocess.run(
        ["git", "show", f"HEAD:{path.as_posix()}"],
        cwd=PROJECT_DIR,
        check=True,
        capture_output=True,
    ).stdout


def _verify_execution_inputs_committed() -> dict[str, str]:
    dirty = subprocess.run(
        [
            "git",
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            *(path.as_posix() for path in EXECUTION_INPUTS),
        ],
        cwd=PROJECT_DIR,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        raise RuntimeError(
            "P4.1 execution inputs must be committed and clean before source requests"
        )
    hashes: dict[str, str] = {}
    for relative_path in EXECUTION_INPUTS:
        working_hash = _sha256(PROJECT_DIR / relative_path)
        committed_hash = hashlib.sha256(_git_blob(relative_path)).hexdigest()
        if working_hash != committed_hash:
            raise RuntimeError(f"P4.1 execution input is not bound to HEAD: {relative_path}")
        hashes[relative_path.as_posix()] = working_hash
    return hashes


def _validate_fixed_paths(config_path: Path, report_path: Path) -> None:
    expected_config = (PROJECT_DIR / DEFAULT_CONFIG).absolute()
    expected_report = (PROJECT_DIR / DEFAULT_REPORT).absolute()
    if config_path != expected_config:
        raise ValueError(f"P4.1 config path is frozen to {expected_config}")
    if report_path != expected_report:
        raise ValueError(f"P4.1 report path is frozen to {expected_report}")
    if not config_path.is_file() or config_path.is_symlink():
        raise ValueError("P4.1 config must be one regular, non-symlink file")
    if report_path.exists() or report_path.is_symlink():
        raise FileExistsError(f"refusing to overwrite source-spike evidence: {report_path}")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if report_path.parent.is_symlink():
        raise ValueError("P4.1 report directory must not be a symlink")


def _mark_job_failed(run_id: int, message: str, extra_stats: dict[str, Any]) -> None:
    from alphapilot.db.engine import get_session
    from alphapilot.db.models import JobRun, utcnow

    with get_session() as session:
        record = session.get(JobRun, run_id)
        if record is None:
            raise RuntimeError(f"job audit row disappeared: {run_id}")
        stats = dict(record.stats or {})
        stats.update(extra_stats)
        record.status = "failed"
        record.error = message[:4000]
        record.finished_at = utcnow()
        record.stats = stats


def _source_statuses(stats: dict[str, Any]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    raw_sources = stats.get("sources", {})
    if isinstance(raw_sources, dict):
        rows = [
            (str(source_id), result)
            for source_id, result in raw_sources.items()
            if isinstance(result, dict)
        ]
    elif isinstance(raw_sources, list):
        rows = [
            (str(result.get("source_id") or f"source-{index}"), result)
            for index, result in enumerate(raw_sources)
            if isinstance(result, dict)
        ]
    else:
        rows = []
    for source_id, result in rows:
        status = str(result.get("status") or "unknown")
        grouped.setdefault(status, []).append(source_id)
    return {key: sorted(value) for key, value in sorted(grouped.items())}


def _write_new_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite source-spike evidence: {path}")
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    arguments = _arguments()
    os.chdir(PROJECT_DIR)
    config_path = (PROJECT_DIR / arguments.config).absolute()
    report_path = (PROJECT_DIR / arguments.report).absolute()
    _validate_fixed_paths(config_path, report_path)
    execution_input_hashes = _verify_execution_inputs_committed()
    config_sha256_before = _sha256(config_path)
    execution_commit_before = _git_head()

    from alphapilot.db.engine import init_db
    from alphapilot.jobs.p4_source_spike import register_p4_source_spike_job
    from alphapilot.jobs.registry import run_job

    init_db()
    register_p4_source_spike_job()
    record = run_job(
        "p4_source_spike",
        config_path=config_path,
        expected_config_sha256=config_sha256_before,
        execution_commit=execution_commit_before,
        planned_report_path=str(report_path.relative_to(PROJECT_DIR)),
    )
    stats = dict(record.stats or {})
    config_sha256_after = _sha256(config_path)
    execution_commit_after = _git_head()
    if (
        config_sha256_after != config_sha256_before
        or execution_commit_after != execution_commit_before
    ):
        _mark_job_failed(
            record.id,
            "EvidenceDriftError: config bytes or Git HEAD changed during P4.1 spike",
            {
                "evidence_drift": {
                    "config_sha256_before": config_sha256_before,
                    "config_sha256_after": config_sha256_after,
                    "execution_commit_before": execution_commit_before,
                    "execution_commit_after": execution_commit_after,
                }
            },
        )
        from alphapilot.db.engine import get_session
        from alphapilot.db.models import JobRun

        with get_session() as session:
            refreshed = session.get(JobRun, record.id)
            if refreshed is None:
                raise RuntimeError(f"job audit row disappeared: {record.id}")
            record = refreshed
            stats = dict(record.stats or {})
    terminal_at = datetime.now(UTC).isoformat()
    source_statuses = _source_statuses(stats)
    report: dict[str, Any] = {
        "schema_version": "p4.1-source-spike-report-v2",
        "generated_at": terminal_at,
        "phase_baseline_commit": stats.get(
            "phase_baseline_commit",
            "e288be683deef67891ebea0b37b508f4eb59b37c",
        ),
        "execution_commit": execution_commit_before,
        "scope": {
            "completed": "P4.1 source feasibility spike only",
            "excluded": [
                "news_items migration",
                "news_poll scheduling",
                "AUDITED_NEWS_SOURCES promotion",
                "P4.2 or later implementation",
                "trade proposal creation",
                "broker order creation",
            ],
            "stop_after_report": True,
        },
        "prior_invalid_evidence": stats.get("prior_invalid_evidence"),
        "pre_registration": {
            "config_path": str(config_path.relative_to(PROJECT_DIR)),
            "config_sha256": config_sha256_before,
            "config_sha256_after": config_sha256_after,
            "unchanged_during_execution": (
                config_sha256_after == config_sha256_before
                and execution_commit_after == execution_commit_before
            ),
            "execution_input_sha256": execution_input_hashes,
        },
        "job_run": {
            "id": record.id,
            "job_name": record.job_name,
            "status": record.status,
            "error": record.error,
            "started_at": record.started_at.isoformat(),
            "finished_at": (
                record.finished_at.isoformat() if record.finished_at is not None else None
            ),
        },
        "source_statuses": source_statuses,
        "stats": stats,
        "gate": {
            "spike_execution_completed": record.status == "ok",
            "report_written": True,
            "p4_1_full_implementation_done": False,
            "p4_2_unlocked": False,
            "requires_architecture_review": True,
        },
    }
    try:
        _write_new_json(report_path, report)
    except Exception as exc:
        _mark_job_failed(
            record.id,
            f"ReportPersistenceError: {type(exc).__name__}: {exc}",
            {
                "planned_report_path": str(report_path.relative_to(PROJECT_DIR)),
                "report_write_failed": True,
            },
        )
        raise
    print(
        json.dumps(
            {
                "job_run_id": record.id,
                "job_run_status": record.status,
                "job_run_error": record.error,
                "report": str(report_path.relative_to(PROJECT_DIR)),
                "report_sha256": _sha256(report_path),
                "source_statuses": source_statuses,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if record.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
