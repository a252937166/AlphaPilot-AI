from __future__ import annotations

import hashlib
import inspect
import sqlite3
import subprocess
from pathlib import Path

import pytest
from scripts import rehearse_p4_2a_v2_1_heldout_full_path as runner
from scripts import validate_p4_2a_v2_1_heldout_rehearsal_bundle as validator


def test_validator_api_is_fixed_read_only_mapping_contract() -> None:
    signature = inspect.signature(validator.validate_bundle)
    assert tuple(signature.parameters) == ("project_root", "bundle_path")
    assert signature.parameters["project_root"].default is inspect.Parameter.empty
    assert signature.parameters["bundle_path"].default is inspect.Parameter.empty


def test_validator_preexec_sha_and_standalone_bootstrap_reach_parser() -> None:
    executable = Path(validator._validator_fixed_python_executable()).resolve(strict=True)
    assert validator._validator_early_sha256(executable.as_posix()) == hashlib.sha256(
        executable.read_bytes()
    ).hexdigest()
    original_argv_executable = Path(validator._validator_orig_argv_executable())
    assert validator._validator_early_sha256(
        original_argv_executable.as_posix()
    ) == hashlib.sha256(original_argv_executable.read_bytes()).hexdigest()
    assert (
        validator._validator_early_sha256(original_argv_executable.as_posix())
        == validator._VALIDATOR_ORIG_ARGV_EXECUTABLE_SHA256
    )
    completed = subprocess.run(
        [
            executable.as_posix(),
            "-S",
            "-P",
            "-B",
            Path(validator.__file__).as_posix(),
            "--definitely-invalid",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=validator._VALIDATOR_EXEC_ENVIRONMENT,
    )
    assert completed.returncode == 2
    assert "unrecognized arguments: --definitely-invalid" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_v2_1_ast_closure_is_anchored_to_new_entrypoint_and_archive_only() -> None:
    entrypoint = "scripts/rehearse_p4_2a_v2_1_heldout_full_path.py"
    validator_path = "scripts/validate_p4_2a_v2_1_heldout_rehearsal_bundle.py"
    common_path = "scripts/p4_2a_v2_dev_common.py"
    blobs = {
        entrypoint: (
            b"from scripts import p4_2a_v2_dev_common\n"
            b"from scripts import validate_p4_2a_v2_1_heldout_rehearsal_bundle\n"
        ),
        validator_path: b"VALUE = 1\n",
        common_path: b"VALUE = 2\n",
    }
    assert validator._v2_1_ast_local_import_closure(blobs) == set(blobs)
    with pytest.raises(validator.RehearsalV21ValidationError, match=r"v2\.1 AST entrypoint"):
        validator._v2_1_ast_local_import_closure(
            {"scripts/rehearse_p4_2a_v2_heldout_full_path.py": b"VALUE = 1\n"}
        )


def test_locked_validator_machine_result_has_exact_fail_closed_shape() -> None:
    result = validator._validator_result(
        {
            "lineage": {"implementation_commit": "1" * 40},
            "merkle": {"bundle_root_sha256": "2" * 64},
        },
        bundle_sha256="3" * 64,
    )
    assert result == {
        "schema_version": "p4.2a-v2-heldout-validator-result-v2.1",
        "status": "PASS_REHEARSAL_V2_1_AWAITING_OWNER_REVIEW",
        "bundle_path": (
            "docs/phase4/rehearsals/P4.2a-v2-calibration-v2-1/bundle.json"
        ),
        "bundle_sha256": "3" * 64,
        "bundle_root_sha256": "2" * 64,
        "implementation_commit": "1" * 40,
        "real_heldout_materialization_unlocked": False,
        "heldout_metric_evaluation_unlocked": False,
    }


def test_strict_json_rejects_duplicate_and_nonfinite() -> None:
    for payload, message in (
        (b'{"a":1,"a":2}', "duplicate key"),
        (b'{"a":NaN}', "invalid numeric constant"),
        (b'{"a":Infinity}', "invalid numeric constant"),
    ):
        with pytest.raises(validator.RehearsalV21ValidationError, match=message):
            validator.strict_json_loads(payload, source="test")


def test_validator_registered_path_is_v2_1_literal(tmp_path: Path) -> None:
    expected = (tmp_path.resolve() / runner.SUCCESSOR_DIRECTORY_RELATIVE).absolute()
    assert validator.registered_rehearsal_directory(tmp_path) == expected


def test_bundle_path_must_be_bundle_json(tmp_path: Path) -> None:
    directory = tmp_path / "candidate"
    directory.mkdir()
    other = directory / "other.json"
    other.write_text("{}\n")
    with pytest.raises(validator.RehearsalV21ValidationError, match=r"bundle\.json"):
        validator.validate_bundle(tmp_path, other)


def test_canonical_in_process_validation_rejects_ambient_caller_before_read() -> None:
    with pytest.raises(validator.RehearsalV21ValidationError, match="locked registered"):
        validator.validate_bundle(
            runner.PROJECT_ROOT,
            runner.PROJECT_ROOT / runner.SUCCESSOR_DIRECTORY_RELATIVE / "absent-bundle.json",
        )


def test_drafter_identity_is_independent_of_qwen_model() -> None:
    assert validator.heldout_drafter_id() == "OpenAI Codex GPT-5"
    assert validator.heldout_drafter_id() != "qwen3.6-plus"


def test_expected_artifact_inventory_is_exact_14() -> None:
    assert len(runner.ARTIFACT_INVENTORY) == 14
    assert len({name for name, _path in runner.ARTIFACT_INVENTORY}) == 14
    assert len({path for _name, path in runner.ARTIFACT_INVENTORY}) == 14


def test_stolen_recursion_authority_cannot_skip_canonical_replay(tmp_path: Path) -> None:
    assert not hasattr(validator, "_VALIDATION_DEPTH")
    closure = inspect.getclosurevars(validator.validate_bundle)
    assert set(closure.nonlocals) == {"active_recursion", "recursion_authority"}
    assert all(
        value is not closure.nonlocals["active_recursion"]
        for value in vars(validator).values()
    )
    active_recursion = closure.nonlocals["active_recursion"]
    recursion_authority = closure.nonlocals["recursion_authority"]
    token = active_recursion.set((recursion_authority, runner.PROJECT_ROOT.resolve()))
    try:
        policy = runner._AuditPolicy(
            project_root=runner.PROJECT_ROOT.resolve(),
            write_roots=(tmp_path,),
            sqlite_roots=(tmp_path,),
            subprocess_mode="synthetic_git",
            synthetic_git_root=tmp_path,
        )
        assert not validator._nested_synthetic_validation_allowed(
            state_matches=True,
            root=runner.PROJECT_ROOT.resolve(),
            bundle_path=(
                runner.PROJECT_ROOT / runner.SUCCESSOR_DIRECTORY_RELATIVE / runner.BUNDLE_FILENAME
            ),
            outer_root=runner.PROJECT_ROOT.resolve(),
            policy=policy,
        )
    finally:
        active_recursion.reset(token)
    source = inspect.getsource(validator._nested_synthetic_validation_allowed)
    assert "root != runner.PROJECT_ROOT.resolve()" in source


def test_prediction_timing_tamper_is_rejected() -> None:
    state = b"".join(
        validator._canonical_json_bytes(row)
        for row in (
            {
                "status": "inference_started",
                "execution_id": "synthetic-execution",
                "started_at_utc": runner.FIXED_WALL_CLOCK_TEXT,
            },
            {
                "status": "completed_all_eligible_candidates_once",
                "execution_id": "synthetic-execution",
                "completed_at_utc": runner.FIXED_WALL_CLOCK_TEXT,
            },
        )
    )
    prediction = {
        "recorded_at_utc": runner.FIXED_WALL_CLOCK_TEXT,
        "latency_ms": 0,
    }
    validator._validate_prediction_timing(state, [prediction])
    with pytest.raises(validator.RehearsalV21ValidationError, match="outside"):
        validator._validate_prediction_timing(
            state,
            [{**prediction, "recorded_at_utc": "2026-08-10T12:29:59Z"}],
        )
    with pytest.raises(validator.RehearsalV21ValidationError, match="nonzero latency"):
        validator._validate_prediction_timing(state, [{**prediction, "latency_ms": 1}])
    drifted_state = state.replace(
        runner.FIXED_WALL_CLOCK_TEXT.encode(),
        b"2026-08-10T12:31:00Z",
        1,
    )
    with pytest.raises(validator.RehearsalV21ValidationError, match="fixed rehearsal"):
        validator._validate_prediction_timing(drifted_state, [prediction])


def test_v2_1_merkle_rejects_single_byte_artifact_or_control_mutation() -> None:
    original = {"a": b"one", "b": b"two"}
    artifact_mutation = {**original, "a": b"One"}
    control_mutation = {**original, "b": b"twO"}
    assert runner._merkle_root(original) != runner._merkle_root(artifact_mutation)
    assert runner._merkle_root(original) != runner._merkle_root(control_mutation)


def test_validator_database_guard_blocks_repository_sqlite(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    with (
        pytest.raises(
            validator.RehearsalV21ValidationError,
            match="repository database",
        ),
        validator._forbid_repository_database(project),
    ):
        sqlite3.connect(project / "production.db")


def test_active_validator_replays_real_entry_and_receipt_probe() -> None:
    source = inspect.getsource(validator._active_replay)
    assert source.count("runner._execute_temp_pipeline(") == 2
    assert "runner._synthetic_release_receipt_probe(" in source
