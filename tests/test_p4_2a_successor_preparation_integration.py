"""Synthetic prepare integration checks; these never authorize real execution.

The authority module's returned facts and source-validation boundary are mocked
only where a test isolates prepare dispatch. Its cryptographic/Git/mint checks
are covered in the separate authority suite. The prepare predicates exercised
here are real: runtime-observation validation, presence selection, exact types,
stage restrictions, delegation identity, manifest validation and no-injection.
Fixtures contain no owner/reviewer approval. All files are under pytest tmp_path.
"""

from __future__ import annotations

import copy
import hashlib
import json
import socket
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn, cast

import pytest
from scripts import build_p4_2a_v2_heldout_adjudication_ui as ui
from scripts import p4_2a_successor_production_authority as authority
from scripts import prepare_p4_2a_v2_heldout as prepare
from scripts import seal_p4_2a_v2_heldout_draft as seal

RELEASE_REL = Path(
    "docs/phase4/reports/P4.2a-successor-production-integration-v1-production-release-20260907.json"
)
PREPARATION_STAGES = ("materialize", "infer", "select-blind", "seal-draft", "build-adjudication-ui")
SYNTHETIC_EVIDENCE = {
    "schema_version": "synthetic-unit-evidence-not-a-registered-authority",
    "purpose": "prepare-branch-tests-only-no-owner-or-reviewer-approval",
}


def _sha(label: str) -> str:
    return hashlib.sha256(("synthetic-H2a-unit:" + label).encode()).hexdigest()


def _oid(label: str) -> str:
    return hashlib.sha1(("synthetic-H2a-unit:" + label).encode()).hexdigest()


def _never(*_args: Any, **_kwargs: Any) -> NoReturn:
    raise AssertionError("synthetic prepare test reached forbidden external/business IO")


@pytest.fixture(autouse=True)
def _forbid_real_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every exercised path must finish before external commands or business IO."""
    monkeypatch.setattr(subprocess, "run", _never)
    monkeypatch.setattr(socket, "create_connection", _never)
    monkeypatch.setattr(socket.socket, "connect", _never)
    monkeypatch.setattr(sqlite3, "connect", _never)
    monkeypatch.setattr(prepare, "_git", _never)
    monkeypatch.setattr(prepare, "_window_rows", _never)
    monkeypatch.setattr(prepare, "_extract_record", _never)


def _binding(root: Path) -> prepare.HeldoutBinding:
    return prepare.HeldoutBinding(
        root=root.resolve(),
        preregistration={},
        design={},
        contract=cast(Any, None),
        artifacts={
            name: root / "synthetic-artifacts" / name
            for name in (
                "materialized_inputs",
                "materialization_manifest",
                "predictions",
                "inference_state",
                "inference_manifest",
                "blind",
                "private_selection",
                "sealed_draft",
                "adjudication_ui",
            )
        },
        retired_ids=frozenset(),
    )


def _synthetic_authorization(
    root: Path, stage: str | None = None
) -> authority.ProductionPreparationAuthorization:
    """Unminted data used only behind an explicitly mocked authority boundary."""
    return authority.ProductionPreparationAuthorization(
        project_root=root.resolve(),
        receipt_path=root / RELEASE_REL,
        receipt_sha256=_sha("not-an-owner-release"),
        receipt_creating_commit=_oid("not-a-real-release-commit"),
        preregistration_commit=_oid("not-a-real-preregistration-commit"),
        implementation_commit=_oid("not-a-real-implementation-commit"),
        validated_stage=stage,
    )


def _legacy_data(root: Path) -> prepare.V21ReleaseAuthorization:
    return prepare.V21ReleaseAuthorization(
        project_root=root,
        receipt_path=root / prepare.SUCCESSOR_V2_1_RELEASE_PATH,
        receipt_sha256=_sha("unvalidated-v21-data"),
        receipt_creating_commit=_oid("unvalidated-v21-receipt"),
        preregistration_commit=prepare.SUCCESSOR_V2_1_PREREGISTRATION_COMMIT,
        implementation_commit=_oid("unvalidated-v21-implementation"),
        rehearsal_evidence_commit=_oid("unvalidated-v21-evidence"),
        bundle_path=root / prepare.SUCCESSOR_V2_1_BUNDLE_PATH,
        bundle_sha256=_sha("unvalidated-v21-bundle"),
        bundle_root_sha256=_sha("unvalidated-v21-tree"),
    )


def _synthetic_candidate(root: Path) -> Path:
    path = root / RELEASE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"synthetic_unit_test":"not-a-release-and-not-owner-approval"}\n',
        encoding="utf-8",
    )
    return path


@pytest.fixture
def dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Validate real prepare runtime rules against clearly synthetic observations."""
    root = tmp_path.resolve()
    original_launcher = (prepare.PROJECT_ROOT / prepare._LOCKED_PYTHON_EXECUTABLE_RELATIVE).resolve(
        strict=True
    )
    launcher = root / prepare._LOCKED_PYTHON_EXECUTABLE_RELATIVE
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(original_launcher)
    for relative in set(prepare._REAL_STAGE_ENTRYPOINTS.values()):
        entry = root / relative
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text("# synthetic entrypoint identity; never executed\n", encoding="utf-8")

    binding = _binding(root)
    events: list[tuple[str, str | None]] = []

    def snapshot(stage: str) -> prepare._CanonicalRuntimeSnapshot:
        return prepare._CanonicalRuntimeSnapshot(
            environment=prepare._canonical_real_stage_environment(root),
            runtime_paths=prepare._canonical_real_stage_runtime_paths(root),
            executable=launcher.as_posix(),
            version=(3, 12),
            hash_randomization=0,
            no_site=1,
            no_user_site=1,
            safe_path=True,
            dont_write_bytecode=True,
            pycache_prefix="/dev/null",
            ignore_environment=0,
            isolated=0,
            optimize=0,
            main_file=(root / prepare._REAL_STAGE_ENTRYPOINTS[stage]).as_posix(),
            original_arguments=(
                "synthetic-observation-no-process-started",
                "-S",
                "-P",
                "-B",
                "-c",
                prepare._canonical_real_stage_bootstrap(root, stage),
                stage,
            ),
        )

    def validate(
        root_arg: Path, *, stage: str | None
    ) -> authority.ProductionPreparationAuthorization:
        assert root_arg.resolve() == root
        events.append(("validate", stage))
        return _synthetic_authorization(root, stage)

    def sources(
        root_arg: Path, facts: authority.ProductionPreparationAuthorization, *, stage: str
    ) -> None:
        assert root_arg == root
        assert type(facts) is authority.ProductionPreparationAuthorization
        assert facts.validated_stage == stage
        events.append(("sources", stage))
        return None

    monkeypatch.setattr(prepare, "PROJECT_ROOT", root)
    monkeypatch.setattr(prepare, "_capture_canonical_runtime_snapshot", snapshot)
    monkeypatch.setattr(authority, "validate_preparation_authorization", validate)
    monkeypatch.setattr(authority, "validate_registered_production_sources", sources)
    monkeypatch.setattr(prepare, "load_binding", lambda *_args, **_kwargs: binding)
    return SimpleNamespace(root=root, binding=binding, events=events, snapshot=snapshot)


def test_absent_fixed_new_path_does_not_select_another_date(tmp_path: Path) -> None:
    assert prepare._production_release_candidate(tmp_path) is False
    wrong_date = tmp_path / str(RELEASE_REL).replace("20260907", "20260906")
    wrong_date.parent.mkdir(parents=True)
    wrong_date.write_text('{"synthetic":true}', encoding="utf-8")
    assert prepare._production_release_candidate(tmp_path) is False


@pytest.mark.parametrize("kind", ("invalid-json", "directory", "dangling-symlink"))
def test_existing_new_candidate_never_becomes_absent_fallback(tmp_path: Path, kind: str) -> None:
    target = tmp_path / RELEASE_REL
    target.parent.mkdir(parents=True)
    if kind == "invalid-json":
        target.write_bytes(b"synthetic invalid JSON")
    elif kind == "directory":
        target.mkdir()
    else:
        target.symlink_to(tmp_path / "missing-synthetic-target")
    try:
        selected = prepare._production_release_candidate(tmp_path)
    except prepare.HeldoutPreparationError as error:
        assert "successor production release candidate inspection failed" in str(error)
    else:
        assert selected is True


def test_legacy_and_new_candidate_is_ambiguous(tmp_path: Path) -> None:
    _synthetic_candidate(tmp_path)
    legacy = tmp_path / prepare.SUCCESSOR_V2_1_RELEASE_PATH
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('{"synthetic":true}', encoding="utf-8")
    with pytest.raises(prepare.HeldoutPreparationError, match="ambiguous legacy"):
        prepare._production_release_candidate(tmp_path)


@pytest.mark.parametrize("invalid_presence", (None, 0, 1, {}, "present"))
def test_presence_contract_requires_an_actual_boolean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_presence: Any
) -> None:
    monkeypatch.setattr(
        authority, "has_registered_release_candidate", lambda _root: invalid_presence
    )
    with pytest.raises(prepare.HeldoutPreparationError, match="not boolean"):
        prepare._production_release_candidate(tmp_path)


@pytest.mark.parametrize("stage", PREPARATION_STAGES)
def test_five_stage_dispatch_revalidates_new_facts_and_sources(
    dispatch: SimpleNamespace, stage: str
) -> None:
    _synthetic_candidate(dispatch.root)
    observed = prepare.validate_v2_1_stage_authorization(dispatch.binding, stage=stage)
    assert type(observed) is authority.ProductionPreparationAuthorization
    assert not isinstance(
        observed, (prepare.V21ReleaseAuthorization, prepare._OfflineRehearsalCapability)
    )
    assert observed == _synthetic_authorization(dispatch.root, stage)
    assert dispatch.events == [("validate", stage), ("sources", stage)]


def test_runtime_drift_rejects_before_new_authority(
    dispatch: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _synthetic_candidate(dispatch.root)
    valid = dispatch.snapshot("materialize")
    monkeypatch.setattr(
        prepare,
        "_capture_canonical_runtime_snapshot",
        lambda _stage: replace(valid, hash_randomization=1),
    )
    with pytest.raises(prepare.HeldoutPreparationError, match="interpreter flags drifted"):
        prepare.validate_v2_1_stage_authorization(dispatch.binding, stage="materialize")
    assert dispatch.events == []


@pytest.mark.parametrize("error_type", (authority.ProductionAuthorityError, OSError))
def test_source_validator_failure_cannot_admit_a_stage(
    dispatch: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    _synthetic_candidate(dispatch.root)

    def refuse(*_args: Any, **_kwargs: Any) -> NoReturn:
        raise error_type("synthetic source closure mismatch")

    monkeypatch.setattr(authority, "validate_registered_production_sources", refuse)
    with pytest.raises(
        prepare.HeldoutPreparationError, match="source or runtime verification failed"
    ):
        prepare.validate_v2_1_stage_authorization(dispatch.binding, stage="infer")
    assert dispatch.events == [("validate", "infer")]


@pytest.mark.parametrize("stage", PREPARATION_STAGES)
def test_new_rejection_precedes_all_five_preparation_entry_business_reads(
    dispatch: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stage: str,
) -> None:
    _synthetic_candidate(dispatch.root)
    seen: list[str | None] = []

    def invalid(_root: Path, *, stage: str | None) -> NoReturn:
        seen.append(stage)
        raise authority.ProductionAuthorityError("synthetic invalid new authorization")

    monkeypatch.setattr(authority, "validate_preparation_authorization", invalid)
    monkeypatch.setattr(prepare, "validate_v2_1_release_authorization", _never)
    monkeypatch.setattr(seal, "PROJECT_ROOT", dispatch.root)
    monkeypatch.setattr(ui, "PROJECT_ROOT", dispatch.root)
    monkeypatch.setattr(seal, "load_registered_contract", _never)
    if stage in ("materialize", "infer", "select-blind"):
        with pytest.raises(
            prepare.HeldoutPreparationError, match="successor production authorization failed"
        ):
            if stage == "materialize":
                prepare.run_materialize(dispatch.binding, operator_timing_attestation=None)
            elif stage == "infer":
                prepare.run_infer(dispatch.binding)
            else:
                prepare.run_select_blind(dispatch.binding)
    elif stage == "seal-draft":
        assert (
            seal.main(
                [
                    "--candidate-draft",
                    str(dispatch.root / "never-read.jsonl"),
                    "--drafted-at",
                    "2026-09-07T15:10:00Z",
                ]
            )
            == 2
        )
        assert "successor production authorization failed" in capsys.readouterr().err
    else:
        assert ui.main([]) == 2
        assert "successor production authorization failed" in capsys.readouterr().err
    assert seen == [stage]
    assert dispatch.events == []
    assert all(not path.exists() for path in dispatch.binding.artifacts.values())


@pytest.mark.parametrize(
    "stage,reason",
    (
        ("finalize-owner-adjudication", "REJECTED_PENDING_OWNER_ADJUDICATION_AUTHORITY"),
        ("evaluation", "held-out evaluation remains locked"),
        ("unregistered-stage", "unknown successor stage"),
        (None, "unknown successor stage"),
    ),
)
def test_diagnostic_none_and_later_stages_cannot_enter_preparation_dispatch(
    dispatch: SimpleNamespace, stage: Any, reason: str
) -> None:
    _synthetic_candidate(dispatch.root)
    with pytest.raises(prepare.HeldoutPreparationError, match=reason):
        prepare.validate_v2_1_stage_authorization(
            dispatch.binding, stage=stage, execution_context=_synthetic_authorization(dispatch.root)
        )
    assert dispatch.events == []


def test_missing_new_path_retains_real_legacy_missing_release_rejection(
    dispatch: SimpleNamespace,
) -> None:
    # The unchanged legacy validator verifies these frozen documents before it
    # reaches the missing release receipt. Supply their original clone bytes,
    # leaving the release absent and every external/business IO guard active.
    source_root = Path(__file__).resolve().parents[1]
    for relative, expected_sha256 in (
        (
            prepare.SUCCESSOR_V2_1_PREREGISTRATION_PATH,
            prepare.SUCCESSOR_V2_1_PREREGISTRATION_SHA256,
        ),
        (
            prepare.SUCCESSOR_V2_1_TIMING_PREREGISTRATION_PATH,
            prepare.SUCCESSOR_V2_1_TIMING_PREREGISTRATION_SHA256,
        ),
        (prepare.SUCCESSOR_V2_1_BUNDLE_SCHEMA_PATH, prepare.SUCCESSOR_V2_1_BUNDLE_SCHEMA_SHA256),
        (prepare.SUCCESSOR_V2_1_RELEASE_SCHEMA_PATH, prepare.SUCCESSOR_V2_1_RELEASE_SCHEMA_SHA256),
        (prepare.FRAME_AUTHORITY_PATH, prepare.FRAME_AUTHORITY_SHA256),
        (prepare.SUCCESSOR_CODE_GATE_AUTHORITY_PATH, prepare.SUCCESSOR_CODE_GATE_AUTHORITY_SHA256),
    ):
        raw = (source_root / relative).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected_sha256
        target = dispatch.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    assert not (dispatch.root / prepare.SUCCESSOR_V2_1_RELEASE_PATH).exists()
    with pytest.raises(
        prepare.HeldoutPreparationError, match="BLOCKED_PENDING_SUCCESSOR_V2_1_OWNER_RELEASE"
    ):
        prepare.validate_v2_1_stage_authorization(dispatch.binding, stage="materialize")
    assert dispatch.events == []


def test_production_context_without_candidate_does_not_fall_back(
    dispatch: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(prepare, "validate_v2_1_release_authorization", _never)
    with pytest.raises(
        prepare.HeldoutPreparationError, match="production context has no registered release"
    ):
        prepare.validate_v2_1_stage_authorization(
            dispatch.binding,
            stage="infer",
            execution_context=_synthetic_authorization(dispatch.root, "infer"),
        )


def test_old_context_cannot_impersonate_new_authority(dispatch: SimpleNamespace) -> None:
    _synthetic_candidate(dispatch.root)
    with pytest.raises(
        prepare.HeldoutPreparationError, match="production execution context is forged"
    ):
        prepare.validate_v2_1_stage_authorization(
            dispatch.binding, stage="infer", execution_context=_legacy_data(dispatch.root)
        )
    assert dispatch.events == []


@pytest.mark.parametrize("wrong", ("type", "root"))
def test_facts_boundary_rejects_wrong_type_or_root(
    dispatch: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, wrong: str
) -> None:
    _synthetic_candidate(dispatch.root)
    facts: Any = (
        SimpleNamespace(project_root=dispatch.root)
        if wrong == "type"
        else _synthetic_authorization(dispatch.root / "other")
    )
    monkeypatch.setattr(
        authority, "validate_preparation_authorization", lambda *_args, **_kwargs: facts
    )
    with pytest.raises(prepare.HeldoutPreparationError, match="type or root drifted"):
        prepare._validate_production_release_facts(dispatch.root, stage=None)


def test_production_context_equality_is_revalidated(dispatch: SimpleNamespace) -> None:
    _synthetic_candidate(dispatch.root)
    changed = replace(
        _synthetic_authorization(dispatch.root, "infer"), receipt_sha256=_sha("drift")
    )
    with pytest.raises(prepare.HeldoutPreparationError, match="production release context drifted"):
        prepare.validate_v2_1_stage_authorization(
            dispatch.binding, stage="infer", execution_context=changed
        )


def test_new_delegation_identity_stage_and_facts_are_rechecked(
    dispatch: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _synthetic_candidate(dispatch.root)
    delegated = prepare._prevalidate_v2_1_stage_authorization(dispatch.binding, stage="seal-draft")
    assert prepare._consume_prevalidated_v2_1_stage_authorization(
        dispatch.binding, delegated, "seal-draft"
    ) == _synthetic_authorization(dispatch.root, "seal-draft")
    assert dispatch.events == [
        ("validate", "seal-draft"),
        ("sources", "seal-draft"),
        ("validate", "seal-draft"),
        ("sources", "seal-draft"),
    ]
    for fake, stage in ((replace(delegated), "seal-draft"), (delegated, "build-adjudication-ui")):
        with pytest.raises(
            prepare.HeldoutPreparationError, match="forged, cross-stage, or drifted"
        ):
            prepare._consume_prevalidated_v2_1_stage_authorization(dispatch.binding, fake, stage)
    with pytest.raises(prepare.HeldoutPreparationError, match="forged, cross-stage, or drifted"):
        prepare._consume_prevalidated_v2_1_stage_authorization(
            replace(dispatch.binding, root=dispatch.root / "other-root"), delegated, "seal-draft"
        )
    monkeypatch.setattr(
        authority,
        "validate_preparation_authorization",
        lambda _root, *, stage: replace(
            _synthetic_authorization(dispatch.root, stage),
            receipt_sha256=_sha("changed-after-delegation"),
        ),
    )
    with pytest.raises(prepare.HeldoutPreparationError, match="production release context drifted"):
        prepare._consume_prevalidated_v2_1_stage_authorization(
            dispatch.binding, delegated, "seal-draft"
        )


def test_cli_validate_returns_diagnostic_facts_without_stage_admission(
    dispatch: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    _synthetic_candidate(dispatch.root)
    with pytest.raises(SystemExit) as result:
        prepare.main(["validate"])
    assert result.value.code == 0
    value = json.loads(capsys.readouterr().out)
    assert value["status"] == "valid_successor_production_preparation_authority"
    assert value["read_only_authority_validation"] is True
    assert value["stage_started"] is False
    assert value["runtime_start_preflight_performed"] is False
    assert dispatch.events == [("validate", None)]


@pytest.mark.parametrize("context_kind", ("production", "legacy-v21"))
@pytest.mark.parametrize(
    "injection",
    (
        "settings",
        "chat_json_fn",
        "snapshot_loader",
        "clock",
        "execution_id_factory",
        "prediction_recorded_at_clock",
        "prediction_monotonic_ns_clock",
    ),
)
def test_both_real_context_types_reject_every_injection_seam(
    tmp_path: Path, context_kind: str, injection: str
) -> None:
    context = (
        _synthetic_authorization(tmp_path, "infer")
        if context_kind == "production"
        else _legacy_data(tmp_path)
    )
    arguments: dict[str, Any] = {
        "settings": None,
        "chat_json_fn": None,
        "snapshot_loader": prepare.dev_runner._production_snapshot,
        "clock": prepare._system_clock,
        "execution_id_factory": prepare._random_execution_id,
        "prediction_recorded_at_clock": None,
        "prediction_monotonic_ns_clock": None,
    }
    assert prepare._validate_v2_1_inference_seams(context, **arguments) is None
    arguments[injection] = object() if injection == "settings" else _never
    with pytest.raises(prepare.HeldoutPreparationError, match="forbids injected"):
        prepare._validate_v2_1_inference_seams(context, **arguments)


def test_forged_offline_capability_still_fails_the_real_old_identity_gate(tmp_path: Path) -> None:
    forged = prepare._OfflineRehearsalCapability(
        _nonce=object(),
        project_root=tmp_path.resolve(),
        database=tmp_path / "never-opened.db",
        artifact_paths=(),
        pdf_fetcher=_never,
        pdf_text_extractor=_never,
        monotonic=lambda: 0.0,
        sleep=_never,
        inference_settings=cast(Any, None),
        chat_json_fn=_never,
        snapshot_loader=_never,
        wall_clock=_never,
        execution_id_factory=_never,
        prediction_recorded_at_clock=_never,
        prediction_monotonic_ns_clock=_never,
        preregistration_commit=_oid("unminted-offline-prereg"),
        implementation_commit=_oid("unminted-offline-implementation"),
    )
    with pytest.raises(prepare.HeldoutPreparationError, match="forged or drifted"):
        prepare.validate_v2_1_stage_authorization(
            _binding(tmp_path), stage="materialize", execution_context=forged
        )


def _runtime_record(root: Path) -> dict[str, Any]:
    """Synthetic record shape only; no backup command, DB check or real attestation."""
    directory = root / "data/backups"
    directory.mkdir(parents=True)
    backup = directory / "alphapilot-full-synthetic-unit.db"
    backup.write_bytes(b"synthetic record fixture; intentionally not a production database")
    manifest = prepare.database_backup.manifest_path_for(backup)
    created_utc, created_local = "2026-09-07T14:30:00Z", "2026-09-07T22:30:00+08:00"
    backup_sha = prepare.common.sha256_file(backup)
    manifest.write_bytes(
        prepare.common.canonical_json_bytes(
            {
                "format_version": prepare.database_backup.BACKUP_FORMAT_VERSION,
                "managed_by": prepare.database_backup.BACKUP_MANAGED_BY,
                "created_at": created_utc,
                "backup": {"filename": backup.name, "sha256": backup_sha},
            }
        )
    )
    observed_utc, observed_local = "2026-09-07T15:10:00Z", "2026-09-07T23:10:00+08:00"
    runtime = prepare._database_backup_runtime_directory()
    return {
        "mode": "real",
        "observed_at_utc": observed_utc,
        "observed_at_shanghai": observed_local,
        "backup_stamp": {
            "path": str(runtime / "last-success-shanghai-date"),
            "expected_shanghai_date": "2026-09-07",
            "observed_value": "2026-09-07",
            "regular_file": True,
            "symlink": False,
            "mode": "0600",
        },
        "database_backup_launchagent": {
            "label": "com.alphapilot.database-backup",
            "target": f"gui/{prepare.os.getuid()}/com.alphapilot.database-backup",
            "loaded": True,
            "state": "not running",
            "last_exit_code": 0,
        },
        "database_backup_lock": {
            "path": str(runtime / ".daily-backup.lock"),
            "nonblocking_exclusive_flock_acquired": True,
            "held": False,
        },
        "verified_backup": {
            "manifest_path": str(manifest.resolve()),
            "manifest_sha256": prepare.common.sha256_file(manifest),
            "backup_path": str(backup.resolve()),
            "backup_sha256": backup_sha,
            "created_at_utc": created_utc,
            "created_at_shanghai": created_local,
            "quick_check": "ok",
            "verify_database_backup_passed": True,
        },
        "operator_timing_attestation": {
            "observed_start_cst": observed_local,
            "attester_identity": "SYNTHETIC_UNIT_OBSERVER_NOT_OWNER",
            "explicitly_supplied": True,
            "input_channel": (
                "required_real_CLI_flags_or_required_typed_run_materialize_argument_no_default"
            ),
            "cninfo_midnight_batch_assessment": "clear_for_start",
            "p4_1_dense_poll_slot_assessment": "clear_for_start",
            "decision": "launched_outside_owner_identified_CNInfo_midnight_and_dense_P4_1_slots",
            "automatic_blackout_verification": False,
            "authority_path": prepare.SUCCESSOR_CODE_GATE_AUTHORITY_PATH.as_posix(),
            "authority_sha256": prepare.SUCCESSOR_CODE_GATE_AUTHORITY_SHA256,
        },
    }


@pytest.fixture
def materialization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Build data synthetically, then test every manifest predicate without stubs."""
    source = prepare.load_binding()
    root = tmp_path.resolve()
    binding = replace(
        source,
        root=root,
        artifacts={
            name: root / path.relative_to(source.root) for name, path in source.artifacts.items()
        },
    )
    facts = _synthetic_authorization(root, "materialize")
    monkeypatch.setattr(
        authority,
        "execution_authority_evidence",
        lambda _facts: copy.deepcopy(SYNTHETIC_EVIDENCE),
    )
    # Only the synthetic producer's admission is replaced while constructing data.
    # The checks under test below are restored/real; no real stage is invoked.
    with monkeypatch.context() as construction:
        construction.setattr(
            prepare, "validate_v2_1_stage_authorization", lambda *_args, **_kwargs: facts
        )
        candidates, manifest = prepare._synthetic_production_materialization_fixture(binding)
    manifest["schema_version"] = authority.MATERIALIZATION_MANIFEST_SCHEMA
    manifest["runtime_start_preflight"] = _runtime_record(root)
    return SimpleNamespace(
        binding=binding,
        facts=facts,
        candidates=candidates,
        manifest=manifest,
        inputs_sha=prepare.common.sha256_bytes(prepare.common.canonical_jsonl_bytes(candidates)),
    )


def _validate_manifest(data: SimpleNamespace, manifest: dict[str, Any]) -> None:
    prepare._validate_production_materialization_manifest(
        data.binding,
        manifest,
        data.candidates,
        inputs_sha256=data.inputs_sha,
        authorization=data.facts,
    )


def test_new_manifest_runs_runtime_pacing_and_complete_existing_scientific_projection(
    materialization: SimpleNamespace,
) -> None:
    before = copy.deepcopy(materialization.manifest)
    assert _validate_manifest(materialization, materialization.manifest) is None
    assert materialization.manifest == before


@pytest.mark.parametrize(
    "legacy_schema",
    (
        "p4.2a-v2-heldout-materialization-manifest-v1",
        "p4.2a-v2-heldout-materialization-manifest-v2",
    ),
)
def test_new_authority_does_not_accept_either_old_manifest_version(
    materialization: SimpleNamespace, legacy_schema: str
) -> None:
    manifest = copy.deepcopy(materialization.manifest)
    manifest["schema_version"] = legacy_schema
    with pytest.raises(
        prepare.HeldoutPreparationError, match="production materialization manifest schema"
    ):
        _validate_manifest(materialization, manifest)


@pytest.mark.parametrize("mutation", ("authority", "retry", "runtime", "counts", "eligible-layer"))
def test_new_manifest_cannot_skip_existing_deep_predicates(
    materialization: SimpleNamespace, mutation: str
) -> None:
    manifest = copy.deepcopy(materialization.manifest)
    if mutation == "authority":
        manifest["execution_authority"] = {"synthetic": "different-facts"}
    elif mutation == "retry":
        manifest["request_pacing"]["cninfo_pdf"]["retry_count"] = 1
    elif mutation == "runtime":
        manifest["runtime_start_preflight"]["mode"] = "offline"
    elif mutation == "counts":
        manifest["counts"]["raw_source_window"] += 1
    else:
        manifest["layers"]["eligible_candidates"].pop()
    with pytest.raises(prepare.HeldoutPreparationError):
        _validate_manifest(materialization, manifest)
