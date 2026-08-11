from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from alphapilot.backtest import data_health
from alphapilot.backtest import s6_presign as presign
from alphapilot.backtest.external_pit_adjudication import (
    FROZEN_MANIFEST_SHA256,
    canonical_sha256,
)
from alphapilot.core.config import Settings


def test_frozen_baseline_must_resolve_to_exact_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        presign,
        "_git",
        lambda *_args, **_kwargs: f"{presign.FROZEN_BASELINE_COMMIT}\n".encode(),
    )
    assert (
        presign._resolve_frozen_baseline("frozen")
        == presign.FROZEN_BASELINE_COMMIT
    )

    monkeypatch.setattr(
        presign,
        "_git",
        lambda *_args, **_kwargs: b"0" * 40 + b"\n",
    )
    with pytest.raises(presign.PresignGateError, match="frozen S6 commit"):
        presign._resolve_frozen_baseline("moving")


def test_scope_attestation_reports_structured_add_modify_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "same.py").write_text("same\n", encoding="utf-8")
    (tmp_path / "changed.py").write_text("current\n", encoding="utf-8")
    (tmp_path / "added.py").write_text("added\n", encoding="utf-8")
    baseline = {
        "same.py": b"same\n",
        "changed.py": b"baseline\n",
        "deleted.py": b"deleted\n",
        "added.py": None,
    }
    monkeypatch.setattr(
        presign,
        "_baseline_bytes",
        lambda _commit, path, **_kwargs: baseline[path],
    )

    result = presign._scope_attestation(
        name="factor",
        paths=tuple(baseline),
        baseline_commit=presign.FROZEN_BASELINE_COMMIT,
        root=tmp_path,
    )

    assert result["diff_count"] == 3
    assert result["changed_paths"] == [
        "added.py",
        "changed.py",
        "deleted.py",
    ]
    statuses = {item["path"]: item["status"] for item in result["files"]}
    assert statuses == {
        "added.py": "added",
        "changed.py": "modified",
        "deleted.py": "deleted",
        "same.py": "unchanged",
    }
    assert len(result["baseline_manifest_sha256"]) == 64
    assert len(result["current_manifest_sha256"]) == 64


def test_scope_attestation_rejects_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target.py"
    target.write_text("value\n", encoding="utf-8")
    (tmp_path / "factor.py").symlink_to(target)
    monkeypatch.setattr(
        presign,
        "_baseline_bytes",
        lambda *_args, **_kwargs: b"value\n",
    )
    with pytest.raises(presign.PresignGateError, match="non-symlink"):
        presign._scope_attestation(
            name="factor",
            paths=("factor.py",),
            baseline_commit=presign.FROZEN_BASELINE_COMMIT,
            root=tmp_path,
        )


def _research_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE factor_ic_stats (
          sample_tag TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE factor_correlation_stats (
          sample_tag TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE job_runs (
          job_name TEXT NOT NULL,
          started_at TEXT NOT NULL,
          status TEXT NOT NULL
        );
        CREATE TABLE backtest_runs (
          created_at TEXT NOT NULL
        );
        """
    )


def test_test_window_attestation_ignores_old_m2_and_counts_formal_s7(
    tmp_path: Path,
) -> None:
    connection = sqlite3.connect(tmp_path / "audit.db")
    try:
        _research_tables(connection)
        cutoff = datetime(2026, 7, 31, 10, 0, tzinfo=UTC)

        def stored(moment: datetime) -> str:
            # Production rows are written by SQLAlchemy as naive-UTC text with
            # a space separator; the cutoff comparison must hold in that form.
            return moment.replace(tzinfo=None).isoformat(sep=" ")

        connection.execute(
            "INSERT INTO factor_ic_stats VALUES ('full', ?)",
            (stored(cutoff - timedelta(days=7)),),
        )
        connection.execute(
            "INSERT INTO factor_ic_stats VALUES ('test', ?)",
            (stored(cutoff + timedelta(minutes=1)),),
        )
        connection.execute(
            "INSERT INTO job_runs VALUES ('research_factors_m3', ?, 'ok')",
            (stored(cutoff + timedelta(minutes=2)),),
        )
        connection.execute(
            "INSERT INTO backtest_runs VALUES (?)",
            (stored(cutoff - timedelta(hours=1)),),
        )
        connection.execute(
            "INSERT INTO backtest_runs VALUES (?)",
            (stored(cutoff + timedelta(minutes=3)),),
        )
        connection.commit()

        result = presign._test_window_attestation(
            connection,
            baseline_cutoff=cutoff,
        )
    finally:
        connection.close()

    assert result["test_window_access_count"] == 3
    assert result["probes"]["new_factor_ic_test_rows"] == 1
    assert result["probes"]["current_formal_s7_job_runs"] == 1
    assert result["probes"]["preexisting_backtest_runs"] == 1
    assert result["probes"]["new_backtest_runs"] == 1
    assert "arbitrary SELECT history" in result["caveat"]


def test_test_window_attestation_zero_on_sealed_fixture(
    tmp_path: Path,
) -> None:
    connection = sqlite3.connect(tmp_path / "sealed.db")
    try:
        _research_tables(connection)
        connection.execute(
            "INSERT INTO factor_ic_stats VALUES ('train', '2026-07-25 12:00:00')"
        )
        connection.execute(
            """
            INSERT INTO job_runs
            VALUES ('research_preliminary_train_ic', '2026-07-25 12:00:00', 'ok')
            """
        )
        connection.execute(
            "INSERT INTO backtest_runs VALUES ('2026-07-23 12:00:00')"
        )
        connection.commit()
        result = presign._test_window_attestation(
            connection,
            baseline_cutoff=datetime(2026, 7, 31, tzinfo=UTC),
        )
    finally:
        connection.close()

    assert result["test_window_access_count"] == 0
    assert result["probes"]["preexisting_backtest_runs"] == 1
    assert all(
        value == 0
        for key, value in result["probes"].items()
        if key != "preexisting_backtest_runs"
    )


def test_frozen_exact_key_audit_rejects_local_value_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = sqlite3.connect(tmp_path / "pit.db")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE daily_bars (
          symbol TEXT, trade_date TEXT, close REAL, source TEXT
        );
        CREATE TABLE adj_factors (
          symbol TEXT, trade_date TEXT, adj_factor REAL, source TEXT
        );
        CREATE TABLE financial_indicators (
          symbol TEXT, report_period TEXT, metric TEXT, value REAL,
          source TEXT, available_time TEXT, payload TEXT
        );
        CREATE TABLE valuation_daily (
          symbol TEXT, trade_date TEXT, pe_ttm REAL, pb_mrq REAL, ps_ttm REAL,
          source TEXT, available_time TEXT
        );
        INSERT INTO daily_bars VALUES ('000001', '2025-01-02', 10.0, 'baostock');
        INSERT INTO daily_bars VALUES ('000001', '2025-01-03', 10.1, 'baostock');
        INSERT INTO adj_factors
        VALUES ('000001', '2025-01-02', 1.0, 'baostock-hfq');
        INSERT INTO adj_factors
        VALUES ('000001', '2025-01-03', 1.1, 'baostock-hfq');
        INSERT INTO financial_indicators
        VALUES ('000001', '2024Q4', 'roe', 0.1, 'baostock',
                '2025-03-31 16:00:00.000000', '{}');
        INSERT INTO valuation_daily
        VALUES ('000001', '2025-01-02', 10.0, 1.0, 2.0, 'em',
                '2025-01-02 07:00:00.000000');
        """
    )
    frozen = {
        "selection": "frozen fixture",
        "seed": 1,
        "sample_size_per_table": 1,
        "external_source_pairing": "fixture",
        "daily_bars_with_adj": [
            {
                "symbol": "000001",
                "trade_date": "2025-01-02",
                "close": 10.0,
                "source": "baostock",
                "adj_factor": 1.0,
                "adj_source": "baostock-hfq",
                "adj_anchor_date": "2025-01-03",
                "adj_anchor_factor": 1.1,
            }
        ],
        "financial_indicators": [
            {
                "symbol": "000001",
                "report_period": "2024Q4",
                "metric": "roe",
                "value": 0.1,
                "source": "baostock",
                "available_time": "2025-03-31 16:00:00.000000",
                "payload": "{}",
            }
        ],
        "valuation_daily": [
            {
                "symbol": "000001",
                "trade_date": "2025-01-02",
                "pe_ttm": 10.0,
                "pb_mrq": 1.0,
                "ps_ttm": 2.0,
                "source": "em",
                "available_time": "2025-01-02 07:00:00.000000",
            }
        ],
    }
    monkeypatch.setattr(data_health, "FROZEN_MANIFEST_SHA256", "fixture-hash")
    monkeypatch.setattr(
        data_health,
        "_pit_manifest_sha256",
        lambda _samples: "fixture-hash",
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        result = presign._frozen_exact_key_audit(connection, frozen=frozen)
        assert result["sample_count"] == 3
        assert result["difference_count"] == 0

        connection.execute("PRAGMA query_only=OFF")
        connection.execute(
            """
            UPDATE financial_indicators SET value=0.2
            WHERE symbol='000001' AND report_period='2024Q4' AND metric='roe'
            """
        )
        connection.commit()
        connection.execute("PRAGMA query_only=ON")
        with pytest.raises(
            presign.PresignGateError,
            match="no longer reproduces",
        ):
            presign._frozen_exact_key_audit(connection, frozen=frozen)
    finally:
        connection.close()


def _candidate_files(directory: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate = {
        "approved": False,
        "reviewer_role": "pending",
        "reviewed_at": None,
        "pit_manifest_schema_version": "p3.3-s6-local-pit-manifest-v1",
        "pit_manifest_sha256": FROZEN_MANIFEST_SHA256,
        "samples": [
            {"table": "daily_bars", "key": {"symbol": "000001"}}
        ],
    }
    candidate_path = directory / "pairing-v3-candidate.json"
    candidate_path.write_text(
        json.dumps(candidate, sort_keys=True),
        encoding="utf-8",
    )
    machine = {
        "schema_version": presign.MACHINE_VALIDATION_SCHEMA_VERSION,
        "validated_at": "2026-07-31T18:52:50+08:00",
        "candidate_file_sha256": presign._sha256_bytes(
            candidate_path.read_bytes()
        ),
        "candidate_canonical_sha256": canonical_sha256(candidate),
    }
    (directory / "machine-validation.json").write_text(
        json.dumps(machine, sort_keys=True),
        encoding="utf-8",
    )
    return candidate, machine


def _pin_machine_validation(
    monkeypatch: pytest.MonkeyPatch,
    directory: Path,
) -> None:
    monkeypatch.setattr(
        presign,
        "FROZEN_MACHINE_VALIDATION_SHA256",
        presign._sha256_bytes(
            (directory / "machine-validation.json").read_bytes()
        ),
    )


def test_candidate_binding_rejects_machine_hash_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, machine = _candidate_files(tmp_path)
    machine["candidate_file_sha256"] = "0" * 64
    (tmp_path / "machine-validation.json").write_text(
        json.dumps(machine),
        encoding="utf-8",
    )
    _pin_machine_validation(monkeypatch, tmp_path)
    monkeypatch.setattr(
        presign,
        "FROZEN_PAIRING_V3_UNSIGNED_CANONICAL_SHA256",
        canonical_sha256(candidate),
    )
    with pytest.raises(presign.PresignGateError, match="file SHA-256 mismatch"):
        presign._candidate_binding(
            tmp_path,
            pit_samples={"manifest_sha256": FROZEN_MANIFEST_SHA256},
        )


def test_candidate_binding_rejects_unfrozen_machine_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, _machine = _candidate_files(tmp_path)
    monkeypatch.setattr(
        presign,
        "FROZEN_PAIRING_V3_UNSIGNED_CANONICAL_SHA256",
        canonical_sha256(candidate),
    )
    with pytest.raises(
        presign.PresignGateError,
        match="machine validation SHA-256 is not frozen",
    ):
        presign._candidate_binding(
            tmp_path,
            pit_samples={"manifest_sha256": FROZEN_MANIFEST_SHA256},
        )


def test_candidate_binding_uses_unsigned_validator_and_binds_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, _machine = _candidate_files(tmp_path)
    _pin_machine_validation(monkeypatch, tmp_path)
    monkeypatch.setattr(
        presign,
        "FROZEN_PAIRING_V3_UNSIGNED_CANONICAL_SHA256",
        canonical_sha256(candidate),
    )
    calls: list[dict[str, Any]] = []

    def fake_validate(
        document: dict[str, Any],
        *,
        evidence_path: Path,
        pit_samples: Mapping[str, Any],
    ) -> dict[str, Any]:
        calls.append(
            {
                "document": document,
                "evidence_path": evidence_path,
                "pit_samples": pit_samples,
            }
        )
        return {
            "signature_status": "unsigned_candidate",
            "sample_count": 1,
        }

    monkeypatch.setattr(presign, "validate_pairing_v3_candidate", fake_validate)
    binding, identities = presign._candidate_binding(
        tmp_path,
        pit_samples={"manifest_sha256": FROZEN_MANIFEST_SHA256},
    )

    assert calls
    assert binding["accepted_for_release"] is False
    assert binding["business_key_count"] == 1
    assert len(binding["business_keys_sha256"]) == 64
    assert identities["candidate"]["sha256"] == binding["candidate_file_sha256"]


def test_candidate_binding_requires_unsigned_top_level_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, _machine = _candidate_files(tmp_path)
    candidate["approved"] = True
    candidate_path = tmp_path / "pairing-v3-candidate.json"
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    machine_path = tmp_path / "machine-validation.json"
    machine = json.loads(machine_path.read_text(encoding="utf-8"))
    machine["candidate_file_sha256"] = presign._sha256_bytes(
        candidate_path.read_bytes()
    )
    machine["candidate_canonical_sha256"] = canonical_sha256(candidate)
    machine_path.write_text(json.dumps(machine), encoding="utf-8")
    _pin_machine_validation(monkeypatch, tmp_path)
    monkeypatch.setattr(
        presign,
        "FROZEN_PAIRING_V3_UNSIGNED_CANONICAL_SHA256",
        canonical_sha256(candidate),
    )
    with pytest.raises(presign.PresignGateError, match="approved=false"):
        presign._candidate_binding(
            tmp_path,
            pit_samples={"manifest_sha256": FROZEN_MANIFEST_SHA256},
        )


def test_main_rejects_outputs_that_overwrite_evidence_inputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate_directory = tmp_path / "candidate"
    candidate_directory.mkdir()
    _candidate_files(candidate_directory)
    frozen_candidate = candidate_directory / "pairing-v3-candidate.json"
    before = frozen_candidate.read_bytes()

    exit_code = presign.main(
        [
            "--db",
            str(tmp_path / "alphapilot.db"),
            "--candidate-dir",
            str(candidate_directory),
            "--json-out",
            str(frozen_candidate),
        ]
    )

    assert exit_code == 2
    assert frozen_candidate.read_bytes() == before
    assert "must not overwrite" in capsys.readouterr().err

    exit_code = presign.main(
        [
            "--db",
            str(tmp_path / "alphapilot.db"),
            "--candidate-dir",
            str(candidate_directory),
            "--json-out",
            str(tmp_path / "gate.json"),
            "--sha256-out",
            str(tmp_path / "gate.json"),
        ]
    )

    assert exit_code == 2
    assert "must not equal" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, {"research": True, "paper_auto": False, "live": False}),
        (
            {
                "trading_mode": "paper_auto",
                "paper_auto_trading_enabled": True,
                "live_trading_enabled": True,
            },
            {"research": False, "paper_auto": True, "live": True},
        ),
    ],
)
def test_effective_safety_values(
    overrides: dict[str, Any],
    expected: dict[str, bool],
) -> None:
    settings = Settings(_env_file=None, **overrides)
    observed = presign._safety_effective_values(settings)
    for key, value in expected.items():
        assert observed[key] is value


def test_effective_safety_values_expose_trade_gate_overrides() -> None:
    settings = Settings(
        _env_file=None,
        futu_enable_trade=True,
        futu_enable_trade_query=True,
        futu_enable_account_mutation=True,
    )
    observed = presign._safety_effective_values(settings)
    assert observed["futu_enable_trade"] is True
    assert observed["futu_enable_trade_query"] is True
    assert observed["futu_enable_account_mutation"] is True


def test_actual_repo_scopes_match_frozen_baseline() -> None:
    resolved = subprocess.run(
        [
            "git",
            "rev-parse",
            "--verify",
            f"{presign.FROZEN_BASELINE_COMMIT}^{{commit}}",
        ],
        cwd=presign.ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert resolved == presign.FROZEN_BASELINE_COMMIT
    # Later accepted phases may strengthen a frozen safety scope, but the exact
    # bytes must then be pinned here rather than broadly allowing that path to
    # drift. S9 added the pre-registered v3 weights; P4.1-v2 strengthened the
    # scheduler fail-closed gate for paper_trading/futu_enable_trade.
    sanctioned = {
        "factor": [],
        "weight": ["config/factor_weights_v3.yaml"],
        "trading_safety_gate": ["src/alphapilot/scheduler_main.py"],
        "test_window_guard": [],
    }
    pinned_modified_sha256 = {
        "src/alphapilot/scheduler_main.py": (
            "efcedebe93fc548c96ed2907f3e036f4b18eb5b8c7b974c5214ae95d6ba6265f"
        ),
    }
    for name, paths in (
        ("factor", presign.FACTOR_SCOPE),
        ("weight", presign.WEIGHT_SCOPE),
        ("trading_safety_gate", presign.SAFETY_GATE_SCOPE),
        ("test_window_guard", presign.TEST_WINDOW_GUARD_SCOPE),
    ):
        attestation = presign._scope_attestation(
            name=name,
            paths=paths,
            baseline_commit=resolved,
        )
        assert attestation["changed_paths"] == sanctioned[name]
        assert attestation["diff_count"] == len(sanctioned[name])
        statuses = {item["path"]: item["status"] for item in attestation["files"]}
        for path in sanctioned[name]:
            expected_status = "modified" if path in pinned_modified_sha256 else "added"
            assert statuses[path] == expected_status
            if path in pinned_modified_sha256:
                assert (
                    presign._sha256_bytes((presign.ROOT / path).read_bytes())
                    == pinned_modified_sha256[path]
                )
