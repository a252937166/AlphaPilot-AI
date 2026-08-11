from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from scripts import rehearse_p4_2a_v2_heldout_full_path as runner
from scripts import validate_p4_2a_v2_heldout_rehearsal_bundle as validator

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _fingerprint(directory: Path) -> dict[str, str]:
    if not directory.exists():
        return {}
    return {
        path.relative_to(directory).as_posix(): _sha256(path.read_bytes())
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _fake_git_binding() -> runner.GitBinding:
    def blob_reader(relative: str) -> bytes:
        path = PROJECT_ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise runner.RehearsalError(f"missing fake commit blob: {relative}")
        return path.read_bytes()

    return runner.GitBinding(
        implementation_commit="a" * 40,
        blob_reader=blob_reader,
        commit_exists=lambda: True,
        required_ancestor_present=lambda: True,
    )


def _minimal_preflight(publication_parent: Path) -> runner.SuccessorPreflight:
    return runner.SuccessorPreflight(
        repository_payloads={},
        repository_sha256={},
        ast_closure_paths=(),
        python_inventory_sha256="b" * 64,
        package_inventory_sha256="c" * 64,
        publication_device=publication_parent.stat().st_dev,
    )


def _run(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    destination = tmp_path / "published"
    bundle_path = runner.run_successor_rehearsal(
        project_root=PROJECT_ROOT,
        publish_directory=destination,
        workspace_parent=tmp_path / "workspaces",
        git_binding=_fake_git_binding(),
        validate_before_publish=False,
    )
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert isinstance(bundle, dict)
    return destination, bundle


def _artifact_payloads(directory: Path, run_record: dict[str, Any]) -> dict[str, bytes]:
    root = run_record["archive_root"]
    return {
        artifact["source_relative_path"]: (
            directory / root / artifact["source_relative_path"]
        ).read_bytes()
        for artifact in run_record["artifacts"]
    }


def test_successor_runner_dual_run_archive_is_deterministic_and_atomic(
    tmp_path: Path,
) -> None:
    retired_before = {
        relative: _sha256((PROJECT_ROOT / relative).read_bytes())
        for relative, _expected in runner.RETIRED_V1_REFERENCES
    }
    heldout_root = PROJECT_ROOT / "docs/phase4/eval/v2-calibration/heldout"
    heldout_before = _fingerprint(heldout_root)

    destination, bundle = _run(tmp_path)

    assert bundle["lineage"]["implementation_commit"] == "a" * 40
    assert bundle["remaining_blockers"] == {
        "cost_and_strata_authority_conflict": "BLOCKED_PENDING_OWNER_CLARIFICATION",
        "real_heldout_materialization_unlocked": False,
        "real_heldout_inference_unlocked": False,
        "heldout_metric_evaluation_unlocked": False,
    }
    run_a, run_b = bundle["archive"]["runs"]
    assert run_a["artifact_count"] == run_b["artifact_count"] == 14
    assert _artifact_payloads(destination, run_a) == _artifact_payloads(destination, run_b)
    assert run_a["artifact_merkle_root_sha256"] == runner._merkle_root(
        _artifact_payloads(destination, run_a)
    )
    assert run_b["artifact_merkle_root_sha256"] == runner._merkle_root(
        _artifact_payloads(destination, run_b)
    )
    inference_rows = [
        json.loads(line)
        for line in (
            destination / run_a["archive_root"] / "docs/phase4/eval/v2-calibration/heldout/"
            "P4.2a-heldout-v2-inference.state.jsonl"
        )
        .read_text()
        .splitlines()
    ]
    assert [row["status"] for row in inference_rows] == [
        "inference_started",
        "completed_all_eligible_candidates_once",
    ]
    assert inference_rows[0]["execution_id"] == str(
        uuid.uuid5(
            runner.UUID_NAMESPACE,
            "inference_execution\0p4.2a-heldout-frame-v2-synthetic",
        )
    )
    assert inference_rows[0]["started_at_utc"] == runner.FIXED_WALL_CLOCK_TEXT
    assert inference_rows[1]["completed_at_utc"] == runner.FIXED_WALL_CLOCK_TEXT

    control = bundle["archive"]["control_surface"]
    manifest_path = destination / control["manifest"]["bundle_relative_path"]
    manifest = json.loads(manifest_path.read_text())
    assert manifest == {
        "schema_version": runner.CONTROL_MANIFEST_SCHEMA,
        "files": control["files"],
    }
    assert control["tree_member_count"] == control["file_count"] + 1
    control_payloads = {
        record["bundle_relative_path"]: (destination / record["bundle_relative_path"]).read_bytes()
        for record in control["files"]
    }
    control_payloads[control["manifest"]["bundle_relative_path"]] = manifest_path.read_bytes()
    assert control["merkle_root_sha256"] == runner._merkle_root(control_payloads)
    for record in control["files"]:
        path = destination / record["bundle_relative_path"]
        assert path.stat().st_nlink == 1
        assert len(path.read_bytes()) == record["bytes"]
        assert _sha256(path.read_bytes()) == record["sha256"]

    assert _fingerprint(heldout_root) == heldout_before
    assert {
        relative: _sha256((PROJECT_ROOT / relative).read_bytes()) for relative in retired_before
    } == retired_before
    before_second = _fingerprint(destination)
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        runner.run_successor_rehearsal(
            project_root=PROJECT_ROOT,
            publish_directory=destination,
            workspace_parent=tmp_path / "workspaces",
            git_binding=_fake_git_binding(),
            validate_before_publish=False,
        )
    assert _fingerprint(destination) == before_second


def test_registered_schema_accepts_complete_commit_bound_control_closure(
    tmp_path: Path,
) -> None:
    _destination, bundle = _run(tmp_path)
    zero_byte_controls = [
        (record["logical_name"], record["source_kind"])
        for record in bundle["archive"]["control_surface"]["files"]
        if record["bytes"] == 0
    ]
    assert zero_byte_controls == []
    schema = json.loads((PROJECT_ROOT / runner.SUCCESSOR_SCHEMA_RELATIVE).read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(bundle)


def test_successor_bundle_passes_independent_full_validator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination, _bundle = _run(tmp_path)
    blob_reader = _fake_git_binding().blob_reader
    monkeypatch.setattr(validator, "_validate_locked_runtime_environment", lambda: None)
    monkeypatch.setattr(
        validator,
        "_validate_commit",
        lambda _root, _commit: "a" * 40,
    )
    monkeypatch.setattr(
        validator,
        "_commit_blob",
        lambda _root, _commit, relative: blob_reader(relative),
    )
    monkeypatch.setattr(
        validator,
        "_resolve_commit_module",
        lambda _root, _commit, module: runner._module_file_from_reader(
            module, blob_reader
        ),
    )
    monkeypatch.setattr(
        validator,
        "_commit_ancestor_initializers",
        lambda _root, _commit, relative: runner._ancestor_initializers(
            relative, blob_reader
        ),
    )

    result = validator.validate_rehearsal_bundle(
        destination,
        project_root=PROJECT_ROOT,
    )

    assert result["status"] == "PASS_REHEARSAL_V2_ONLY_REAL_HELDOUT_REMAINS_BLOCKED"
    assert result["run_artifact_count"] == 14
    assert result["byte_identical_artifact_count"] == 14
    assert result["v1_receipt_or_gate_accepted"] is False


def test_successor_runner_failure_before_publish_leaves_destination_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "must-remain-absent"

    def fail_bundle(*_args: object, **_kwargs: object) -> tuple[dict[str, Any], dict[str, bytes]]:
        raise runner.RehearsalError("injected successor bundle failure")

    monkeypatch.setattr(runner, "_bundle_payloads", fail_bundle)
    with pytest.raises(runner.RehearsalError, match="injected successor bundle failure"):
        runner.run_successor_rehearsal(
            project_root=PROJECT_ROOT,
            publish_directory=destination,
            workspace_parent=tmp_path / "workspaces",
            git_binding=_fake_git_binding(),
            validate_before_publish=False,
        )
    assert not destination.exists()


def test_database_guard_blocks_both_sqlite_entrypoints_and_allows_memory() -> None:
    with runner._forbid_real_database():
        for connect in (sqlite3.connect, sqlite3.dbapi2.connect):
            with pytest.raises(runner.RehearsalError, match="real database open"):
                connect("production.sqlite3")
        connection = sqlite3.dbapi2.connect(":memory:")
        connection.close()


def test_network_guard_blocks_dns_and_socket_connect() -> None:
    with runner._forbid_network():
        with pytest.raises(runner.RehearsalError, match="real network call"):
            socket.getaddrinfo("example.invalid", 443)
        with pytest.raises(runner.RehearsalError, match="real network call"):
            socket.socket()


def test_subprocess_guard_blocks_process_creation() -> None:
    with runner._forbid_subprocess(), pytest.raises(
        runner.RehearsalError,
        match="start a subprocess",
    ):
        subprocess.run([sys.executable, "-c", "pass"], check=True)


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        (b"import alphapilot.this_module_does_not_exist\n", "unresolved local import"),
        (
            b"from importlib import import_module\nname = 'json'\nimport_module(name)\n",
            "non-literal dynamic import",
        ),
        (
            b"from importlib import import_module\nimport_module('alphapilot.core')\n",
            "runtime dynamic import is forbidden",
        ),
    ),
)
def test_ast_closure_rejects_unresolved_or_unprovable_local_imports(
    payload: bytes,
    message: str,
) -> None:
    def blob_reader(relative: str) -> bytes:
        if relative == "scripts/synthetic_entrypoint.py":
            return payload
        raise runner.RehearsalError(f"missing synthetic commit blob: {relative}")

    with pytest.raises(runner.RehearsalError, match=message):
        runner._local_import_closure(
            entrypoint="scripts/synthetic_entrypoint.py",
            blob_reader=blob_reader,
        )


def test_ast_closure_allows_literal_third_party_dynamic_import() -> None:
    payload = b"from importlib import import_module\nimport_module('json')\n"

    def blob_reader(relative: str) -> bytes:
        if relative == "scripts/synthetic_entrypoint.py":
            return payload
        raise runner.RehearsalError(f"missing synthetic commit blob: {relative}")

    assert runner._local_import_closure(
        entrypoint="scripts/synthetic_entrypoint.py",
        blob_reader=blob_reader,
    ) == {"scripts/synthetic_entrypoint.py": payload}


def test_repository_trace_records_commit_bound_digest_and_blocks_escapes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    workspace = tmp_path / "workspace"
    external = tmp_path / "external.txt"
    root.mkdir()
    workspace.mkdir()
    external.write_bytes(b"external")
    control = root / "control.txt"
    control.write_bytes(b"commit-bound")

    def blob_reader(relative: str) -> bytes:
        if relative == "control.txt":
            return b"commit-bound"
        raise runner.RehearsalError(f"unpreflighted: {relative}")

    with runner._trace_repository_reads(
        root,
        blob_reader=blob_reader,
        allowed_write_root=workspace,
    ) as reads:
        assert control.read_bytes() == b"commit-bound"
        (workspace / "allowed.txt").write_bytes(b"allowed")
        with pytest.raises(runner.RehearsalError, match="external read"):
            external.read_bytes()
        with pytest.raises(runner.RehearsalError, match="outside its temp root"):
            external.write_bytes(b"forbidden")
    assert reads == {"control.txt": _sha256(b"commit-bound")}
    assert external.read_bytes() == b"external"


def test_repository_trace_rejects_symlink_escape_and_custom_read_opener(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    workspace = tmp_path / "workspace"
    external = tmp_path / "external.txt"
    root.mkdir()
    workspace.mkdir()
    external.write_bytes(b"external")
    escape = root / "escape.txt"
    escape.symlink_to(external)

    with runner._trace_repository_reads(
        root,
        blob_reader=lambda _relative: b"external",
        allowed_write_root=workspace,
    ):
        with pytest.raises(runner.RehearsalError, match="symlink"):
            escape.read_bytes()
        with pytest.raises(runner.RehearsalError, match="custom read opener"):
            open(external, opener=lambda path, flags: os.open(path, flags)).close()


def test_registered_claim_is_atomic_under_parallel_contenders(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    destination = root / "published" / "bundle"
    root.mkdir()
    destination.parent.mkdir()

    def attempt() -> str:
        try:
            runner._claim_registered_execution(root=root, destination=destination)
        except FileExistsError:
            return "blocked"
        return "winner"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: attempt(), range(2)))
    assert sorted(outcomes) == ["blocked", "winner"]
    claim = runner.registered_execution_claim_directory(root, destination)
    assert claim.is_dir() and not any(claim.iterdir())


@pytest.mark.parametrize("existing_kind", ("file", "directory", "symlink", "broken_symlink"))
def test_registered_claim_rejects_every_existing_filesystem_object(
    tmp_path: Path,
    existing_kind: str,
) -> None:
    root = tmp_path / "repo"
    destination = root / "published" / "bundle"
    root.mkdir()
    destination.parent.mkdir()
    claim = runner.registered_execution_claim_directory(root, destination)
    if existing_kind == "file":
        claim.write_bytes(b"occupied")
    elif existing_kind == "directory":
        claim.mkdir()
    elif existing_kind == "symlink":
        target = tmp_path / "target"
        target.mkdir()
        claim.symlink_to(target, target_is_directory=True)
    else:
        claim.symlink_to(tmp_path / "missing", target_is_directory=True)

    with pytest.raises(FileExistsError, match="already claimed"):
        runner._claim_registered_execution(root=root, destination=destination)


def test_registered_failure_retains_claim_and_blocks_all_second_run_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(runner, "PROJECT_ROOT", root)
    monkeypatch.setattr(runner, "RETIRED_V1_REFERENCES", ())
    destination = runner.registered_successor_directory(root)
    destination.parent.mkdir(parents=True)
    preflight = _minimal_preflight(root.parent)
    binding = runner.GitBinding("a" * 40, lambda _path: b"", lambda: True, lambda: True)
    monkeypatch.setattr(
        runner,
        "_preflight_successor_execution",
        lambda **_kwargs: preflight,
    )
    execution_count = 0

    def crash_after_claim(**_kwargs: object) -> runner.SuccessorRun:
        nonlocal execution_count
        execution_count += 1
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "_execute_successor_run", crash_after_claim)
    bootstrap = runner.RegisteredBootstrap(binding=binding, preflight=preflight)
    monkeypatch.setattr(
        runner,
        "_registered_bootstrap_state",
        (runner._REGISTERED_BOOTSTRAP_TOKEN, bootstrap),
    )
    with pytest.raises(KeyboardInterrupt):
        runner.run_successor_rehearsal(project_root=root)
    claim = runner.registered_execution_claim_directory(root, destination)
    assert claim.is_dir()
    assert execution_count == 1

    monkeypatch.setattr(
        runner,
        "_registered_bootstrap_state",
        (runner._REGISTERED_BOOTSTRAP_TOKEN, bootstrap),
    )
    with pytest.raises(FileExistsError, match="already claimed"):
        runner.run_successor_rehearsal(project_root=root)
    assert execution_count == 1


def test_registered_preflight_failure_creates_no_claim_or_pipeline_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(runner, "PROJECT_ROOT", root)
    monkeypatch.setattr(runner, "RETIRED_V1_REFERENCES", ())
    destination = runner.registered_successor_directory(root)
    destination.parent.mkdir(parents=True)
    preflight = _minimal_preflight(root.parent)
    binding = runner.GitBinding("a" * 40, lambda _path: b"", lambda: True, lambda: True)
    monkeypatch.setattr(
        runner,
        "_registered_bootstrap_state",
        (
            runner._REGISTERED_BOOTSTRAP_TOKEN,
            runner.RegisteredBootstrap(binding=binding, preflight=preflight),
        ),
    )
    monkeypatch.setattr(
        runner,
        "_preflight_successor_execution",
        lambda **_kwargs: (_ for _ in ()).throw(runner.RehearsalError("preflight failed")),
    )
    pipeline_count = 0

    def pipeline(**_kwargs: object) -> runner.SuccessorRun:
        nonlocal pipeline_count
        pipeline_count += 1
        raise AssertionError("pipeline must not start")

    monkeypatch.setattr(runner, "_execute_successor_run", pipeline)
    with pytest.raises(runner.RehearsalError, match="preflight failed"):
        runner.run_successor_rehearsal(project_root=root)
    assert pipeline_count == 0
    assert not runner.registered_execution_claim_directory(root, destination).exists()


def test_atomic_publication_fsyncs_both_parents_and_preserves_exact_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_parent = tmp_path / "stage"
    destination_parent = tmp_path / "published"
    source_parent.mkdir()
    destination_parent.mkdir()
    source = source_parent / "claim"
    source.mkdir(mode=0o700)
    (source / "only.txt").write_bytes(b"payload")
    destination = destination_parent / "bundle"
    original_fsync = runner._fsync_directory
    fsynced: list[Path] = []

    def recording_fsync(path: Path) -> None:
        fsynced.append(path)
        original_fsync(path)

    monkeypatch.setattr(runner, "_fsync_directory", recording_fsync)
    runner._atomic_directory_create_only(source, destination)
    assert not source.exists()
    assert _fingerprint(destination) == {"only.txt": _sha256(b"payload")}
    assert fsynced[-2:] == sorted(
        [source_parent, destination_parent],
        key=lambda path: os.fsencode(path),
    )


def test_atomic_preflight_rejects_symlink_parent_and_cross_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_stage = tmp_path / "real-stage"
    destination_parent = tmp_path / "published"
    real_stage.mkdir()
    destination_parent.mkdir()
    stage_link = tmp_path / "stage-link"
    stage_link.symlink_to(real_stage, target_is_directory=True)
    destination = destination_parent / "bundle"
    with pytest.raises(runner.RehearsalError, match="not one regular directory"):
        runner._preflight_atomic_publication(
            publication_parent=stage_link,
            destination=destination,
        )

    monkeypatch.setattr(
        runner,
        "_device_id",
        lambda path: 1 if path == real_stage else 2,
    )
    with pytest.raises(runner.RehearsalError, match="different filesystems"):
        runner._preflight_atomic_publication(
            publication_parent=real_stage,
            destination=destination,
        )


def test_locked_execution_environment_is_applied_before_interpreter_start() -> None:
    environment = dict(os.environ)
    environment.update(runner.LOCKED_EXECUTION_ENVIRONMENT)
    environment[runner.ENVIRONMENT_REEXEC_MARKER] = "1"
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from scripts.rehearse_p4_2a_v2_heldout_full_path "
                "import _execution_environment_is_locked; "
                "raise SystemExit(0 if _execution_environment_is_locked() else 1)"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr


def test_successor_cli_exposes_no_path_git_or_validation_override() -> None:
    help_text = runner._parser().format_help()
    for forbidden in (
        "--output",
        "--publish-directory",
        "--workspace",
        "--implementation-commit",
        "--skip-validation",
    ):
        assert forbidden not in help_text
    assert runner.registered_successor_directory(PROJECT_ROOT) == (
        PROJECT_ROOT / runner.SUCCESSOR_DIRECTORY_RELATIVE
    )


@pytest.mark.parametrize("substitution", ("leaf", "broken_leaf", "ancestor"))
def test_registered_destination_rejects_symlink_substitution_before_all_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    substitution: str,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    literal_destination = (root / runner.SUCCESSOR_DIRECTORY_RELATIVE).absolute()
    external = tmp_path / f"external-{substitution}"
    if substitution == "ancestor":
        (root / "docs").mkdir()
        external.mkdir()
        (root / "docs" / "phase4").symlink_to(external, target_is_directory=True)
    else:
        literal_destination.parent.mkdir(parents=True)
        if substitution == "leaf":
            external.mkdir()
        literal_destination.symlink_to(external, target_is_directory=True)

    calls = {"preflight": 0, "claim": 0, "pipeline": 0}

    def forbidden_preflight(**_kwargs: object) -> runner.SuccessorPreflight:
        calls["preflight"] += 1
        raise AssertionError("preflight must not run for a substituted registered path")

    def forbidden_claim(*, root: Path, destination: Path) -> Path:
        del root, destination
        calls["claim"] += 1
        raise AssertionError("claim must not run for a substituted registered path")

    def forbidden_pipeline(**_kwargs: object) -> runner.SuccessorRun:
        calls["pipeline"] += 1
        raise AssertionError("pipeline must not run for a substituted registered path")

    monkeypatch.setattr(runner, "_preflight_successor_execution", forbidden_preflight)
    monkeypatch.setattr(runner, "_claim_registered_execution", forbidden_claim)
    monkeypatch.setattr(runner, "_execute_successor_run", forbidden_pipeline)
    with pytest.raises(runner.RehearsalError, match="symbolic link substitution"):
        runner.run_successor_rehearsal(project_root=root)
    assert calls == {"preflight": 0, "claim": 0, "pipeline": 0}


def test_registered_destination_normal_absent_literal_and_claim_hash_are_fixed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    expected = (root.resolve() / runner.SUCCESSOR_DIRECTORY_RELATIVE).absolute()

    observed = runner.registered_successor_directory(root)

    assert observed == expected
    assert not observed.exists() and not observed.is_symlink()
    claim_material = (
        runner.SUCCESSOR_PREREGISTRATION_SHA256
        + "\0"
        + runner.SUCCESSOR_REHEARSAL_ID
        + "\0"
        + expected.as_posix()
    ).encode("utf-8")
    expected_claim = root.resolve().parent / (
        ".alphapilot-p4-2a-v2-execution-claim-" + hashlib.sha256(claim_material).hexdigest()
    )
    assert runner.registered_execution_claim_directory(root, observed) == expected_claim
    assert not expected_claim.exists() and not expected_claim.is_symlink()


def test_registered_successor_rejects_python_api_dependency_injection() -> None:
    with pytest.raises(
        runner.RehearsalError,
        match="forbids every dependency-injection override",
    ):
        runner.run_successor_rehearsal(
            project_root=PROJECT_ROOT,
            git_binding=_fake_git_binding(),
        )
