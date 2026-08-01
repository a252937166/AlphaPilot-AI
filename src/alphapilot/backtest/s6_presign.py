from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alphapilot.backtest.data_health import (
    _audit_frozen_pit_samples,
    _load_frozen_pit_samples,
    build_data_health_report,
    readonly_connection,
)
from alphapilot.backtest.external_pit_adjudication import (
    ADJUDICATION_CONTRACT_SHA256,
    FROZEN_PAIRING_V3_UNSIGNED_CANONICAL_SHA256,
    canonical_sha256,
    normalized_unsigned_candidate_sha256,
    validate_pairing_v3_candidate,
)
from alphapilot.core.config import Settings
from alphapilot.futu.client import PERMANENTLY_BLOCKED_METHODS

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_VERSION = "p3.3-s6-presign-gate-v1"
MACHINE_VALIDATION_SCHEMA_VERSION = "p3.3-s6-pairing-v3-machine-validation-v1"
FROZEN_BASELINE_COMMIT = "b9d2b19b1e701a31ecdcab3dca8ed86575f89b30"
# machine-validation.json carries validated_at, the test-window audit cutoff.
# Without this pin a sanctioned builder re-run could move the cutoff forward
# and launder post-remediation test-window access into "preexisting" rows.
FROZEN_MACHINE_VALIDATION_SHA256 = (
    "57f70704905ba84b371fc7f3432ce28967e9e67a5ae9b6517fc188da6109ef3d"
)

FACTOR_SCOPE = (
    "src/alphapilot/engines/factors.py",
    "src/alphapilot/backtest/pit.py",
    "src/alphapilot/backtest/factor_scope.py",
    "src/alphapilot/backtest/factor_research.py",
    "src/alphapilot/backtest/metrics.py",
    "src/alphapilot/jobs/factors.py",
    "src/alphapilot/engines/stock_score.py",
)
WEIGHT_SCOPE = (
    "config/factor_weights.yaml",
    "config/factor_weights_v2.yaml",
    "config/factor_weights_v3.yaml",
    "src/alphapilot/backtest/engine.py",
    "src/alphapilot/backtest/weights_rebuild.py",
)
SAFETY_GATE_SCOPE = (
    "src/alphapilot/api/routes/trades.py",
    "src/alphapilot/core/config.py",
    "src/alphapilot/futu/client.py",
    "src/alphapilot/jobs/paper_auto_trade.py",
    "src/alphapilot/risk/guardrails.py",
    "src/alphapilot/scheduler_main.py",
    "src/alphapilot/services/executor.py",
    "src/alphapilot/services/runtime_flags.py",
    "src/alphapilot/trade/futu_gateway.py",
)
TEST_WINDOW_GUARD_SCOPE = (
    "src/alphapilot/api/routes/backtest.py",
    "src/alphapilot/backtest/diagnosis.py",
    "src/alphapilot/backtest/factor_research.py",
    "src/alphapilot/jobs/factor_research_job.py",
)


class PresignGateError(RuntimeError):
    """The pre-sign proof cannot be produced without weakening its contract."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_json_bytes(payload: bytes, *, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number is forbidden: {value}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key is forbidden: {key}")
            result[key] = value
        return result

    try:
        decoded = payload.decode("utf-8")
        value = json.loads(
            decoded,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PresignGateError(f"{label} must be strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PresignGateError(f"{label} root must be an object")
    return value


def _read_regular_file(path: Path, *, label: str) -> tuple[bytes, dict[str, Any]]:
    expanded = path.expanduser()
    absolute = expanded if expanded.is_absolute() else Path.cwd() / expanded
    before = absolute.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise PresignGateError(f"{label} must be a regular non-symlink file")
    resolved = absolute.resolve(strict=True)
    payload = resolved.read_bytes()
    after = resolved.stat(follow_symlinks=False)
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after or len(payload) != before.st_size:
        raise PresignGateError(f"{label} changed while being read")
    return payload, {
        "path": str(resolved),
        "bytes": len(payload),
        "sha256": _sha256_bytes(payload),
        "device": before.st_dev,
        "inode": before.st_ino,
        "mtime_ns": before.st_mtime_ns,
    }


def _aware_datetime(value: object, *, label: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PresignGateError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PresignGateError(f"{label} must include an explicit timezone")
    return parsed.astimezone(UTC)


def _git(*arguments: str, root: Path = ROOT) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise PresignGateError(
            f"git {' '.join(arguments)} failed: {detail or completed.returncode}"
        )
    return completed.stdout


def _resolve_frozen_baseline(ref: str, *, root: Path = ROOT) -> str:
    resolved = (
        _git("rev-parse", "--verify", f"{ref}^{{commit}}", root=root)
        .decode("ascii")
        .strip()
    )
    if resolved != FROZEN_BASELINE_COMMIT:
        raise PresignGateError(
            "baseline ref does not resolve to the frozen S6 commit "
            f"{FROZEN_BASELINE_COMMIT}"
        )
    return resolved


def _baseline_bytes(
    commit: str,
    relative_path: str,
    *,
    root: Path = ROOT,
) -> bytes | None:
    completed = subprocess.run(
        ["git", "show", f"{commit}:{relative_path}"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if completed.returncode == 0:
        return completed.stdout
    missing = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}:{relative_path}"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if missing.returncode != 0:
        return None
    detail = completed.stderr.decode("utf-8", errors="replace").strip()
    raise PresignGateError(
        f"could not read baseline path {relative_path}: {detail}"
    )


def _scope_attestation(
    *,
    name: str,
    paths: Sequence[str],
    baseline_commit: str,
    root: Path = ROOT,
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    changed_paths: list[str] = []
    for relative_path in sorted(set(paths)):
        baseline = _baseline_bytes(
            baseline_commit,
            relative_path,
            root=root,
        )
        current_path = root / relative_path
        current: bytes | None
        if current_path.exists() or current_path.is_symlink():
            current, _ = _read_regular_file(
                current_path,
                label=f"{name} current file {relative_path}",
            )
        else:
            current = None
        if baseline is None and current is None:
            status_text = "absent_unchanged"
        elif baseline is None:
            status_text = "added"
        elif current is None:
            status_text = "deleted"
        elif baseline == current:
            status_text = "unchanged"
        else:
            status_text = "modified"
        if status_text not in {"unchanged", "absent_unchanged"}:
            changed_paths.append(relative_path)
        files.append(
            {
                "path": relative_path,
                "status": status_text,
                "baseline_present": baseline is not None,
                "baseline_sha256": (
                    _sha256_bytes(baseline) if baseline is not None else None
                ),
                "current_present": current is not None,
                "current_sha256": (
                    _sha256_bytes(current) if current is not None else None
                ),
            }
        )
    baseline_manifest = [
        {
            "path": item["path"],
            "present": item["baseline_present"],
            "sha256": item["baseline_sha256"],
        }
        for item in files
    ]
    current_manifest = [
        {
            "path": item["path"],
            "present": item["current_present"],
            "sha256": item["current_sha256"],
        }
        for item in files
    ]
    return {
        "scope": name,
        "baseline_commit": baseline_commit,
        "baseline_manifest_sha256": canonical_sha256(baseline_manifest),
        "current_manifest_sha256": canonical_sha256(current_manifest),
        "diff_count": len(changed_paths),
        "changed_paths": changed_paths,
        "files": files,
    }


def _safety_effective_values(settings: Settings) -> dict[str, Any]:
    return {
        "research": settings.trading_mode.strip().lower() == "research",
        "paper_auto": bool(settings.paper_auto_trading_enabled),
        "live": bool(settings.live_trading_enabled),
        "paper_trading_enabled": bool(settings.paper_trading_enabled),
        "futu_enable_trade_query": bool(settings.futu_enable_trade_query),
        "futu_enable_account_mutation": bool(
            settings.futu_enable_account_mutation
        ),
        "futu_enable_trade": bool(settings.futu_enable_trade),
    }


def _query_only_scalar(
    connection: sqlite3.Connection,
    sql: str,
    parameters: Sequence[object] = (),
) -> int:
    row = connection.execute(sql, parameters).fetchone()
    if row is None:
        raise PresignGateError("invariant query unexpectedly returned no row")
    return int(row[0])


def _test_window_attestation(
    connection: sqlite3.Connection,
    *,
    baseline_cutoff: datetime,
) -> dict[str, Any]:
    cutoff = baseline_cutoff.replace(tzinfo=None).isoformat(sep=" ")
    probes = {
        "current_factor_ic_test_rows": _query_only_scalar(
            connection,
            "SELECT COUNT(*) FROM factor_ic_stats WHERE sample_tag='test'",
        ),
        "new_factor_ic_test_rows": _query_only_scalar(
            connection,
            """
            SELECT COUNT(*) FROM factor_ic_stats
            WHERE sample_tag='test' AND updated_at > ?
            """,
            (cutoff,),
        ),
        "current_factor_correlation_test_rows": _query_only_scalar(
            connection,
            """
            SELECT COUNT(*) FROM factor_correlation_stats
            WHERE sample_tag='test'
            """,
        ),
        "new_factor_correlation_test_rows": _query_only_scalar(
            connection,
            """
            SELECT COUNT(*) FROM factor_correlation_stats
            WHERE sample_tag='test' AND updated_at > ?
            """,
            (cutoff,),
        ),
        "current_formal_s7_job_runs": _query_only_scalar(
            connection,
            """
            SELECT COUNT(*) FROM job_runs
            WHERE job_name='research_factors_m3'
            """,
        ),
        "new_formal_s7_job_runs": _query_only_scalar(
            connection,
            """
            SELECT COUNT(*) FROM job_runs
            WHERE job_name='research_factors_m3' AND started_at > ?
            """,
            (cutoff,),
        ),
        "preexisting_backtest_runs": _query_only_scalar(
            connection,
            "SELECT COUNT(*) FROM backtest_runs WHERE created_at <= ?",
            (cutoff,),
        ),
        "new_backtest_runs": _query_only_scalar(
            connection,
            "SELECT COUNT(*) FROM backtest_runs WHERE created_at > ?",
            (cutoff,),
        ),
    }
    access_count = sum(
        probes[name]
        for name in (
            "current_factor_ic_test_rows",
            "current_factor_correlation_test_rows",
            "current_formal_s7_job_runs",
            "new_backtest_runs",
        )
    )
    evidence_rows = [
        {"name": name, "value": value}
        for name, value in sorted(probes.items())
    ]
    return {
        "baseline_cutoff_at": baseline_cutoff.isoformat(),
        "test_window_access_count": access_count,
        "evidence_scope": [
            "current persisted factor_ic_stats rows tagged test",
            "current persisted factor_correlation_stats rows tagged test",
            "current formal S7 research_factors_m3 JobRun rows",
            "backtest_runs created after the frozen candidate cutoff",
            "post-baseline deltas on the same machine-observable surfaces",
        ],
        "excluded_history": (
            "backtest_runs at or before the cutoff are prior M1/M2 evidence and "
            "are reported separately, not counted as P3.3-S6 test access"
        ),
        "caveat": (
            "SQLite does not retain arbitrary SELECT history. This proves zero "
            "machine-observable formal S7/test artifacts and formal JobRuns; it "
            "does not retroactively prove that no unaudited process ever issued "
            "a read-only SELECT."
        ),
        "query_results_sha256": canonical_sha256(evidence_rows),
        "probes": probes,
    }


def _candidate_binding(
    candidate_directory: Path,
    *,
    pit_samples: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    candidate_path = candidate_directory / "pairing-v3-candidate.json"
    validation_path = candidate_directory / "machine-validation.json"
    candidate_bytes, candidate_identity = _read_regular_file(
        candidate_path,
        label="pairing-v3 unsigned candidate",
    )
    validation_bytes, validation_identity = _read_regular_file(
        validation_path,
        label="pairing-v3 machine validation",
    )
    if validation_identity["sha256"] != FROZEN_MACHINE_VALIDATION_SHA256:
        raise PresignGateError("machine validation SHA-256 is not frozen")
    candidate = _strict_json_bytes(
        candidate_bytes,
        label="pairing-v3 unsigned candidate",
    )
    machine = _strict_json_bytes(
        validation_bytes,
        label="pairing-v3 machine validation",
    )
    if machine.get("schema_version") != MACHINE_VALIDATION_SCHEMA_VERSION:
        raise PresignGateError("machine validation schema version is not frozen")
    if candidate.get("approved") is not False:
        raise PresignGateError("pre-sign candidate must keep approved=false")
    if candidate.get("reviewer_role") != "pending":
        raise PresignGateError("pre-sign candidate must keep reviewer_role=pending")
    if candidate.get("reviewed_at") is not None:
        raise PresignGateError("pre-sign candidate must keep reviewed_at=null")
    candidate_sha256 = candidate_identity["sha256"]
    canonical = normalized_unsigned_candidate_sha256(candidate)
    if candidate_sha256 != machine.get("candidate_file_sha256"):
        raise PresignGateError("machine validation candidate file SHA-256 mismatch")
    if canonical != machine.get("candidate_canonical_sha256"):
        raise PresignGateError("machine validation candidate canonical SHA-256 mismatch")
    if canonical != FROZEN_PAIRING_V3_UNSIGNED_CANONICAL_SHA256:
        raise PresignGateError("candidate canonical SHA-256 is not frozen")
    machine_validated_at = _aware_datetime(
        machine.get("validated_at"),
        label="machine validation validated_at",
    )
    started_at = datetime.now(UTC)
    verified = validate_pairing_v3_candidate(
        candidate,
        evidence_path=candidate_path.resolve(),
        pit_samples=pit_samples,
    )
    completed_at = datetime.now(UTC)
    if verified.get("signature_status") != "unsigned_candidate":
        raise PresignGateError("candidate validator did not preserve unsigned status")
    keys = [
        {"table": sample["table"], "key": sample["key"]}
        for sample in candidate["samples"]
    ]
    binding = {
        "status": "validated_unsigned_candidate",
        "accepted_for_release": False,
        "candidate_file_sha256": candidate_sha256,
        "candidate_canonical_sha256": canonical,
        "machine_validation_sha256": validation_identity["sha256"],
        "machine_validated_at": machine_validated_at.isoformat(),
        "validation_started_at": started_at.isoformat(),
        "validation_completed_at": completed_at.isoformat(),
        "pit_manifest_schema_version": candidate.get(
            "pit_manifest_schema_version"
        ),
        "pit_manifest_sha256": candidate.get("pit_manifest_sha256"),
        "business_key_count": len(keys),
        "business_keys_sha256": canonical_sha256(keys),
        "validation": verified,
    }
    return binding, {
        "candidate": candidate_identity,
        "machine_validation": validation_identity,
    }


def _contract(
    contract_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload, identity = _read_regular_file(
        contract_path,
        label="S6 adjudication contract",
    )
    if identity["sha256"] != ADJUDICATION_CONTRACT_SHA256:
        raise PresignGateError("adjudication contract SHA-256 is not frozen")
    contract = _strict_json_bytes(payload, label="S6 adjudication contract")
    threshold = contract.get("acceptance_threshold")
    if not isinstance(threshold, dict):
        raise PresignGateError("contract acceptance_threshold must be an object")
    return contract, identity


def _frozen_pit_samples(
    preflight_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        return _load_frozen_pit_samples(preflight_path)
    except (FileNotFoundError, ValueError) as exc:
        raise PresignGateError(str(exc)) from exc


def _frozen_exact_key_audit(
    connection: sqlite3.Connection,
    *,
    frozen: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        return _audit_frozen_pit_samples(connection, frozen=frozen)
    except (KeyError, TypeError, ValueError) as exc:
        raise PresignGateError(str(exc)) from exc


def build_presign_gate(
    *,
    database_path: Path,
    candidate_directory: Path,
    contract_path: Path,
    frozen_preflight_path: Path,
    baseline_ref: str = FROZEN_BASELINE_COMMIT,
    settings: Settings | None = None,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Build a fail-closed candidate-bound S6 proof without accepting a signature."""

    started_at = datetime.now(UTC)
    contract, contract_identity = _contract(contract_path)
    frozen_samples, frozen_preflight_identity = _frozen_pit_samples(
        frozen_preflight_path
    )
    baseline_commit = _resolve_frozen_baseline(baseline_ref, root=root)
    factor_scope = _scope_attestation(
        name="factor",
        paths=FACTOR_SCOPE,
        baseline_commit=baseline_commit,
        root=root,
    )
    weight_scope = _scope_attestation(
        name="weight",
        paths=WEIGHT_SCOPE,
        baseline_commit=baseline_commit,
        root=root,
    )
    safety_scope = _scope_attestation(
        name="trading_safety_gate",
        paths=SAFETY_GATE_SCOPE,
        baseline_commit=baseline_commit,
        root=root,
    )
    test_guard_scope = _scope_attestation(
        name="test_window_guard",
        paths=TEST_WINDOW_GUARD_SCOPE,
        baseline_commit=baseline_commit,
        root=root,
    )

    resolved_db = database_path.expanduser().resolve(strict=True)
    with readonly_connection(resolved_db) as guard:
        query_only = bool(int(guard.execute("PRAGMA query_only").fetchone()[0]))
        if not query_only:
            raise PresignGateError("database did not enter query_only mode")
        running_before = _query_only_scalar(
            guard,
            "SELECT COUNT(*) FROM job_runs WHERE status='running'",
        )
        if running_before:
            raise PresignGateError(
                f"database has {running_before} running JobRun rows"
            )
        data_version_before = _query_only_scalar(guard, "PRAGMA data_version")
        exact_key_audit = _frozen_exact_key_audit(
            guard,
            frozen=frozen_samples,
        )

        candidate, candidate_identities = _candidate_binding(
            candidate_directory.expanduser().resolve(strict=True),
            pit_samples=frozen_samples,
        )
        if candidate["pit_manifest_sha256"] != exact_key_audit["manifest_sha256"]:
            raise PresignGateError("candidate and current PIT manifest differ")

        data_health = build_data_health_report(resolved_db)
        test_window = _test_window_attestation(
            guard,
            baseline_cutoff=_aware_datetime(
                candidate["machine_validated_at"],
                label="candidate machine_validated_at",
            ),
        )
        data_version_after = _query_only_scalar(guard, "PRAGMA data_version")
        running_after = _query_only_scalar(
            guard,
            "SELECT COUNT(*) FROM job_runs WHERE status='running'",
        )
        if data_version_after != data_version_before:
            raise PresignGateError("database changed during the pre-sign gate")
        if running_after:
            raise PresignGateError(
                f"database gained {running_after} running JobRun rows"
            )

    effective = _safety_effective_values(settings or Settings())
    effective["unlock_trade_permanently_blocked"] = (
        "unlock_trade" in PERMANENTLY_BLOCKED_METHODS
    )
    operational_safety_expected = {
        "unlock_trade_permanently_blocked": True,
    }
    operational_safety_differences = [
        key
        for key, expected in operational_safety_expected.items()
        if effective.get(key) != expected
    ]
    expected_safety = contract["acceptance_threshold"]["safety_invariants"]
    observed_safety = {
        "research": effective["research"],
        "paper_auto": effective["paper_auto"],
        "live": effective["live"],
        "trading_safety_gates_unchanged": (
            safety_scope["diff_count"] == 0
            and not operational_safety_differences
        ),
    }
    safety_differences = [
        key
        for key, expected in expected_safety.items()
        if observed_safety.get(key) != expected
    ]
    observed = {
        "machine_verifiable_passes": int(
            candidate["validation"]["sample_count"]
        ),
        "total_samples": int(candidate["validation"]["sample_count"]),
        "unresolved": 0,
        "generic_unavailable": 0,
        "ambiguous_mapping": 0,
        "schema_hash_integrity_errors": 0,
        "local_read_only_gate": (
            "pass"
            if data_health["gate"]["automated_checks_pass"] is True
            else "blocked"
        ),
        "test_window_access_count": test_window["test_window_access_count"],
        "factor_diff_count": factor_scope["diff_count"],
        "weight_diff_count": weight_scope["diff_count"],
        "safety_invariants": observed_safety,
    }
    acceptance_differences = [
        key
        for key, expected in contract["acceptance_threshold"].items()
        if observed.get(key) != expected
    ]
    machine_gate_pass = (
        not acceptance_differences
        and not safety_differences
        and test_guard_scope["diff_count"] == 0
        and data_health["gate"]["automated_checks_pass"] is True
        and data_health["external_pit_pairing"]["accepted"] is False
        and data_health["gate"]["ready_for_s7"] is False
    )

    machine_validated_at = _aware_datetime(
        candidate["machine_validated_at"],
        label="candidate machine_validated_at",
    )

    # Re-read every mutable input after the expensive gate. Any mutation makes
    # this run unusable rather than producing a mixed-state attestation.
    immutable_inputs = {
        **candidate_identities,
        "contract": contract_identity,
        "frozen_preflight": frozen_preflight_identity,
    }
    for label, identity in immutable_inputs.items():
        payload, current = _read_regular_file(
            Path(identity["path"]),
            label=f"{label} TOCTOU recheck",
        )
        del payload
        if current["sha256"] != identity["sha256"]:
            raise PresignGateError(f"{label} changed during the pre-sign gate")
    for scope in (factor_scope, weight_scope, safety_scope, test_guard_scope):
        repeated = _scope_attestation(
            name=str(scope["scope"]),
            paths=tuple(str(item["path"]) for item in scope["files"]),
            baseline_commit=baseline_commit,
            root=root,
        )
        if repeated["current_manifest_sha256"] != scope["current_manifest_sha256"]:
            raise PresignGateError(
                f"{scope['scope']} scope changed during the pre-sign gate"
            )
    completed_at = datetime.now(UTC)
    if completed_at <= machine_validated_at:
        raise PresignGateError(
            "pre-sign report time must be strictly later than machine validation"
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": completed_at.isoformat(),
        "execution_started_at": started_at.isoformat(),
        "database": {
            "path": str(resolved_db),
            "open_mode": "ro",
            "query_only": query_only,
            "data_version_before": data_version_before,
            "data_version_after": data_version_after,
            "running_job_runs_before": running_before,
            "running_job_runs_after": running_after,
        },
        "contract": {
            "path": contract_identity["path"],
            "sha256": contract_identity["sha256"],
            "acceptance_threshold": contract["acceptance_threshold"],
        },
        "frozen_preflight": {
            "path": frozen_preflight_identity["path"],
            "sha256": frozen_preflight_identity["sha256"],
        },
        "candidate_binding": candidate,
        "frozen_exact_key_audit": exact_key_audit,
        "fresh_diagnostic_pit_manifest": {
            "purpose": (
                "global data-health diagnostic only; never replaces the frozen "
                "15 candidate-bound business keys"
            ),
            "manifest_schema_version": data_health["pit_samples"][
                "manifest_schema_version"
            ],
            "manifest_sha256": data_health["pit_samples"]["manifest_sha256"],
        },
        "baseline": {
            "kind": "git_commit",
            "requested_ref": baseline_ref,
            "resolved_commit": baseline_commit,
        },
        "test_window": test_window,
        "factor_diff_count": factor_scope["diff_count"],
        "weight_diff_count": weight_scope["diff_count"],
        "test_window_access_count": test_window["test_window_access_count"],
        "safety_invariants": observed_safety,
        "invariant_evidence": {
            "factor": factor_scope,
            "weight": weight_scope,
            "trading_safety_gate": safety_scope,
            "test_window_guard": test_guard_scope,
            "effective_safety_config": {
                "read_mode": "allowlisted Settings values in presign process",
                "source": (
                    "ALPHAPILOT_* process environment plus repository .env "
                    "resolved by pydantic-settings; secrets are excluded"
                ),
                "values": effective,
                "sha256": canonical_sha256(effective),
                "baseline_contract_values": expected_safety,
                "baseline_contract_sha256": canonical_sha256(expected_safety),
                "current_contract_projection": observed_safety,
                "current_contract_projection_sha256": canonical_sha256(
                    observed_safety
                ),
                "expected_operational_gates": operational_safety_expected,
            },
            "operational_safety_differences": operational_safety_differences,
            "safety_differences": safety_differences,
        },
        "contract_observed": observed,
        "contract_difference_keys": acceptance_differences,
        "local_data_health": data_health,
        "presign_machine_gate_pass": machine_gate_pass,
        "machine_ready_for_human_review": machine_gate_pass,
        "ready_for_signature": machine_gate_pass,
        "ready_for_s7": False,
        "release_blockers": [
            "INDEPENDENT_HUMAN_ARCHITECT_SIGNATURE_REQUIRED",
            "PRODUCTION_SIGNED_EVIDENCE_TRUST_ANCHOR_PENDING",
        ],
        "release_sequence_state": "presign_machine_evidence_only",
    }


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a candidate-bound, read-only P3.3-S6 pre-sign gate. "
            "This never accepts the unsigned candidate for release."
        )
    )
    parser.add_argument("--db", type=Path, default=Path("data/alphapilot.db"))
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument(
        "--contract",
        type=Path,
        default=Path("docs/P3.3-S6-external-pit-adjudication-v1.contract.json"),
    )
    parser.add_argument(
        "--frozen-preflight",
        type=Path,
        default=Path(
            "docs/phase3/reports/P3.3-S6-final-preflight-20260731.json"
        ),
    )
    parser.add_argument("--baseline-ref", default=FROZEN_BASELINE_COMMIT)
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--sha256-out", type=Path)
    return parser.parse_args(argv)


def _reject_evidence_overwrite(
    arguments: argparse.Namespace,
    *,
    output: Path,
    sidecar: Path | None,
) -> None:
    """The gate's own outputs must never replace the evidence it attests."""

    if sidecar == output:
        raise PresignGateError("--sha256-out must not equal --json-out")
    candidate_directory = arguments.candidate_dir.expanduser().resolve()
    protected = (
        arguments.db.expanduser().resolve(),
        candidate_directory / "pairing-v3-candidate.json",
        candidate_directory / "machine-validation.json",
        arguments.contract.expanduser().resolve(),
        arguments.frozen_preflight.expanduser().resolve(),
    )
    protected_identities = {
        (item.stat().st_dev, item.stat().st_ino)
        for item in protected
        if item.exists()
    }
    for target in (output, sidecar):
        if target is None:
            continue
        if target in protected or (
            target.exists()
            and (target.stat().st_dev, target.stat().st_ino)
            in protected_identities
        ):
            raise PresignGateError(
                "gate outputs must not overwrite gate evidence inputs"
            )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        output = arguments.json_out.expanduser().resolve()
        sidecar = (
            arguments.sha256_out.expanduser().resolve()
            if arguments.sha256_out is not None
            else None
        )
        _reject_evidence_overwrite(arguments, output=output, sidecar=sidecar)
        report = build_presign_gate(
            database_path=arguments.db,
            candidate_directory=arguments.candidate_dir,
            contract_path=arguments.contract,
            frozen_preflight_path=arguments.frozen_preflight,
            baseline_ref=str(arguments.baseline_ref),
        )
        if output.exists() and output.is_dir():
            raise PresignGateError("--json-out must be a file")
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, output)
        digest = _sha256_bytes(payload)
        if sidecar is not None:
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(f"{digest}  {output.name}\n", encoding="ascii")
        print(
            json.dumps(
                {
                    "path": str(output),
                    "sha256": digest,
                    "presign_machine_gate_pass": report[
                        "presign_machine_gate_pass"
                    ],
                    "ready_for_signature": report["ready_for_signature"],
                    "ready_for_s7": False,
                    "release_blockers": report["release_blockers"],
                },
                sort_keys=True,
            )
        )
        return 0 if report["presign_machine_gate_pass"] else 2
    except (
        OSError,
        PresignGateError,
        sqlite3.Error,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        print(
            f"S6 pre-sign gate failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
