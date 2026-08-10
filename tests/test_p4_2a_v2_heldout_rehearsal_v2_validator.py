from __future__ import annotations

import builtins
import fcntl
import hashlib
import inspect
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from scripts import validate_p4_2a_v2_heldout_rehearsal_bundle as validator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NON_DESCENDANT_COMMIT = "bb5ced1adc83bfe88fa4c86f9b70513a3de97503"
ZERO_BYTE_INITIALIZER = "src/alphapilot/core/__init__.py"


def test_control_manifest_single_byte_mutation_is_rejected_by_recorded_root() -> None:
    original = {
        "archive/control-surface/manifest.json": b'{"version":1}\n',
        "archive/control-surface/root/repo/control.json": b"{}\n",
    }
    recorded_root = validator._merkle_root(original)
    mutated = dict(original)
    mutated["archive/control-surface/manifest.json"] = b'{"version":2}\n'

    assert validator._merkle_root(mutated) != recorded_root
    with pytest.raises(
        validator.RehearsalV2ValidationError,
        match="control Merkle root drifted",
    ):
        validator._require_merkle_root(mutated, recorded_root, "control")


def test_repository_origin_single_byte_mutation_fails_commit_blob_binding() -> None:
    relative = "pyproject.toml"
    committed = validator._commit_blob(PROJECT_ROOT, validator.V1_FAIL_CLOSE_COMMIT, relative)
    assert committed
    mutated = bytes([committed[0] ^ 1]) + committed[1:]

    with pytest.raises(
        validator.RehearsalV2ValidationError,
        match="differs from implementation commit",
    ):
        validator._require_commit_blob_equality(
            PROJECT_ROOT,
            validator.V1_FAIL_CLOSE_COMMIT,
            relative,
            mutated,
        )


def test_second_pep503_equivalent_package_name_fails_before_inventory_hash() -> None:
    with pytest.raises(
        validator.RehearsalV2ValidationError,
        match="duplicate normalized names",
    ):
        validator._normalize_distribution_rows([("Example_Pkg", "1.0"), ("example-pkg", "2.0")])


def test_duplicate_package_negative_probe_is_actively_executed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator._require_duplicate_package_negative_probe()

    monkeypatch.setattr(
        validator,
        "_normalize_distribution_rows",
        lambda _rows: [{"name": "validator-probe-pkg", "version": "1"}],
    )
    with pytest.raises(
        validator.RehearsalV2ValidationError,
        match="negative probe was accepted",
    ):
        validator._require_duplicate_package_negative_probe()


def test_runtime_inventory_matches_registered_84_row_fingerprint() -> None:
    python_payload, package_payload, roots, raw_count = validator._runtime_inventory(PROJECT_ROOT)
    rows = validator.strict_json_loads(package_payload, source="runtime inventory test")

    assert hashlib.sha256(python_payload).hexdigest() == (
        "ab3e067417027bb98ea4335e9086d2046ac9dfd4eaf857acc8622dc8f0a13a31"
    )
    assert hashlib.sha256(package_payload).hexdigest() == (
        "c3c7792eb31679c0eb7d3140e067d691df330cd3af302d2350bf15b74ac8ec42"
    )
    assert roots == [".venv/lib/python3.12/site-packages"]
    assert hashlib.sha256(validator._canonical_json_bytes(roots)).hexdigest() == (
        "fae235892c0988d4093d1ad12b034a6126d116e436393e837a8b2f71601fbd12"
    )
    assert raw_count == 84
    assert isinstance(rows, list) and len(rows) == 84


def test_every_registered_frozen_authority_has_current_exact_bytes() -> None:
    for relative, expected in validator.FROZEN_AUTHORITY_SHA256.items():
        payload = (PROJECT_ROOT / relative).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected, relative


def _python_child(source: str, *, startup_hash_seed: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.update(validator.LOCKED_EXECUTION_ENVIRONMENT)
    environment["PYTHONHASHSEED"] = startup_hash_seed
    return subprocess.run(
        (sys.executable, "-c", source),
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def test_locked_environment_rejects_env_only_hash_seed_spoof() -> None:
    result = _python_child(
        """
import os
from scripts import validate_p4_2a_v2_heldout_rehearsal_bundle as validator
os.environ.update(validator.LOCKED_EXECUTION_ENVIRONMENT)
try:
    validator._validate_locked_runtime_environment()
except validator.RehearsalV2ValidationError as exc:
    if "was not applied before interpreter startup" not in str(exc):
        raise
else:
    raise SystemExit("hash-randomized interpreter accepted an env-only seed spoof")
""",
        startup_hash_seed="1",
    )
    assert result.returncode == 0, result.stderr


def test_locked_environment_is_applied_and_observed_by_runtime() -> None:
    result = _python_child(
        """
from scripts import validate_p4_2a_v2_heldout_rehearsal_bundle as validator
validator._validate_locked_runtime_environment()
""",
        startup_hash_seed="0",
    )
    assert result.returncode == 0, result.stderr


def test_semantic_guard_allows_only_reconstructed_root_writes_and_traces_reads(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    replay_root = tmp_path / "replay"
    project_root.mkdir()
    replay_root.mkdir()
    repository_control = project_root / "control.json"
    reconstructed_control = replay_root / "control.json"
    repository_control.write_bytes(b'{"source":"repository"}\n')
    reconstructed_control.write_bytes(b'{"source":"archive"}\n')
    repository_reads: dict[str, str] = {}
    outside = tmp_path / "outside-sentinel.txt"

    with validator._semantic_replay_guards(
        project_root=project_root,
        replay_root=replay_root,
        tracked_control_paths={"control.json"},
        repository_read_hashes=repository_reads,
    ) as replay_reads:
        assert repository_control.read_bytes()
        assert reconstructed_control.read_bytes()
        (replay_root / "allowed.txt").write_text("allowed", encoding="utf-8")
        with pytest.raises(
            validator.RehearsalV2ValidationError,
            match="outside its reconstructed root",
        ):
            outside.write_text("forbidden", encoding="utf-8")
        with pytest.raises(
            validator.RehearsalV2ValidationError,
            match="outside its reconstructed root",
        ):
            (project_root / "forbidden.txt").write_text("forbidden", encoding="utf-8")
        with pytest.raises(
            validator.RehearsalV2ValidationError,
            match="network access",
        ):
            socket.getaddrinfo("example.invalid", 443)
        probe_socket = socket.socket()
        try:
            with pytest.raises(
                validator.RehearsalV2ValidationError,
                match="network access",
            ):
                probe_socket.bind(("127.0.0.1", 0))
        finally:
            probe_socket.close()
        with pytest.raises(
            validator.RehearsalV2ValidationError,
            match="non-memory SQLite",
        ):
            sqlite3.connect(outside)
        with pytest.raises(
            validator.RehearsalV2ValidationError,
            match="start a subprocess",
        ):
            subprocess.run(("true",), check=False)

    assert not outside.exists()
    assert not (project_root / "forbidden.txt").exists()
    assert replay_reads == {"control.json"}
    assert repository_reads == {
        "control.json": hashlib.sha256(repository_control.read_bytes()).hexdigest()
    }


def test_semantic_guard_rejects_custom_openers_and_read_dir_fds(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    replay_root = tmp_path / "replay"
    project_root.mkdir()
    replay_root.mkdir()
    (project_root / "control.json").write_text("{}\n", encoding="utf-8")
    allowed = replay_root / "allowed-custom-opener.txt"
    allowed_fdopen = replay_root / "allowed-fdopen.txt"
    allowed_dup2 = replay_root / "allowed-dup2.txt"
    outside = tmp_path / "outside-custom-opener-sentinel.txt"
    preopened_external = tmp_path / "preopened-external-fd.txt"
    directory_fd = os.open(project_root, os.O_RDONLY)
    external_fd = os.open(
        preopened_external,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with validator._semantic_replay_guards(
            project_root=project_root,
            replay_root=replay_root,
            tracked_control_paths=set(),
        ):
            with pytest.raises(
                validator.RehearsalV2ValidationError,
                match="custom read opener",
            ):
                builtins.open(  # noqa: SIM115 - the guard must raise before returning
                    project_root / "control.json",
                    encoding="utf-8",
                    opener=lambda _path, _flags: directory_fd,
                )
            with builtins.open(
                allowed,
                "w",
                encoding="utf-8",
                opener=lambda path, flags: os.open(path, flags, 0o600),
            ) as handle:
                handle.write("allowed\n")
            with tempfile.NamedTemporaryFile(dir=replay_root) as handle:
                handle.write(b"stdlib opener allowed\n")
                handle.flush()
            descriptor = os.open(
                allowed_fdopen,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            duplicated = os.dup(descriptor)
            os.close(descriptor)
            with os.fdopen(duplicated, "wb") as handle:
                handle.write(b"fdopen allowed\n")
            dup2_source = os.open(
                allowed_dup2,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            dup2_target = os.open(
                replay_root / "dup2-replaced.txt",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            os.dup2(dup2_source, dup2_target)
            os.close(dup2_source)
            with os.fdopen(dup2_target, "wb") as handle:
                handle.write(b"dup2 allowed\n")
            with pytest.raises(
                validator.RehearsalV2ValidationError,
                match="preopened external descriptor",
            ):
                os.fdopen(external_fd, "wb", closefd=False)
            with pytest.raises(
                validator.RehearsalV2ValidationError,
                match="preopened external descriptor",
            ):
                os.dup(external_fd)
            fcntl_source = os.open(allowed, os.O_RDONLY)
            try:
                with pytest.raises(
                    validator.RehearsalV2ValidationError,
                    match="forbids fcntl descriptor duplication",
                ):
                    fcntl.fcntl(
                        fcntl_source,
                        fcntl.F_DUPFD,
                        0,
                    )
            finally:
                os.close(fcntl_source)
            with pytest.raises(
                validator.RehearsalV2ValidationError,
                match="outside its reconstructed root",
            ):
                builtins.open(  # noqa: SIM115 - the guard must raise before returning
                    replay_root / "nominal-inside-path.txt",
                    "w",
                    encoding="utf-8",
                    opener=lambda _path, flags: os.open(outside, flags, 0o600),
                )
            with pytest.raises(
                validator.RehearsalV2ValidationError,
                match="preopened external descriptor",
            ):
                builtins.open(  # noqa: SIM115 - the guard must raise before returning
                    replay_root / "nominal-preopened-fd.txt",
                    "w",
                    encoding="utf-8",
                    opener=lambda _path, _flags: external_fd,
                )
            with pytest.raises(
                validator.RehearsalV2ValidationError,
                match="dir-fd filesystem read or write",
            ):
                os.open(
                    "control.json",
                    os.O_RDONLY,
                    dir_fd=directory_fd,
                )
    finally:
        os.close(directory_fd)
        os.close(external_fd)
    assert allowed.read_text(encoding="utf-8") == "allowed\n"
    assert allowed_fdopen.read_bytes() == b"fdopen allowed\n"
    assert allowed_dup2.read_bytes() == b"dup2 allowed\n"
    assert preopened_external.read_bytes() == b""
    assert not outside.exists()


def test_recomputed_read_evidence_rejects_a_self_declared_omission() -> None:
    with pytest.raises(
        validator.RehearsalV2ValidationError,
        match="independent open tracing",
    ):
        validator._require_recomputed_read_evidence(
            {"declared.json": "a" * 64},
            {"declared.json": "a" * 64, "omitted.json": "b" * 64},
            "run-a",
        )


def test_control_surface_rejects_any_extra_repository_record() -> None:
    closure = {"scripts/rehearse_p4_2a_v2_heldout_full_path.py"}
    replay_reads = {"dynamic-control.json": "a" * 64}
    exact = set(validator.REQUIRED_SEED_PATHS) | closure | set(replay_reads)
    validator._require_exact_repository_control_set(
        exact,
        closure=closure,
        replay_reads=replay_reads,
    )
    with pytest.raises(
        validator.RehearsalV2ValidationError,
        match="control exact set drifted",
    ):
        validator._require_exact_repository_control_set(
            exact | {"self-consistent-extra.json"},
            closure=closure,
            replay_reads=replay_reads,
        )


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("scripts/runner.py", "python_source"),
        ("src/alphapilot/core/__init__.py", "package_initializer"),
        ("pyproject.toml", "project_manifest"),
        ("uv.lock", "lockfile"),
        ("config/control.yaml", "frozen_control"),
    ],
)
def test_repository_source_kind_is_path_derived(relative: str, expected: str) -> None:
    assert validator._expected_repository_source_kind(relative) == expected


def test_ast_closure_rejects_unresolved_direct_local_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entrypoint = "scripts/rehearse_p4_2a_v2_heldout_full_path.py"
    monkeypatch.setattr(validator, "_resolve_commit_module", lambda *_args: None)

    with pytest.raises(
        validator.RehearsalV2ValidationError,
        match="unresolved direct local import",
    ):
        validator._ast_local_import_closure(
            {entrypoint: b"import alphapilot.missing\n"},
            project_root=PROJECT_ROOT,
            implementation_commit=validator.V1_FAIL_CLOSE_COMMIT,
        )


def test_ast_closure_rejects_unresolved_local_from_import_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entrypoint = "scripts/rehearse_p4_2a_v2_heldout_full_path.py"
    monkeypatch.setattr(validator, "_resolve_commit_module", lambda *_args: None)

    with pytest.raises(
        validator.RehearsalV2ValidationError,
        match="unresolved local from-import base",
    ):
        validator._ast_local_import_closure(
            {entrypoint: b"from alphapilot.missing import value\n"},
            project_root=PROJECT_ROOT,
            implementation_commit=validator.V1_FAIL_CLOSE_COMMIT,
        )


def test_ast_closure_allows_only_unresolved_from_import_attribute_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entrypoint = "scripts/rehearse_p4_2a_v2_heldout_full_path.py"
    dev_common = "scripts/p4_2a_v2_dev_common.py"
    config = "src/alphapilot/core/config.py"
    module_paths = {
        "scripts": None,
        "scripts.p4_2a_v2_dev_common": dev_common,
        "alphapilot.core.config": config,
        "alphapilot.core.config.settings": None,
    }
    monkeypatch.setattr(
        validator,
        "_resolve_commit_module",
        lambda _root, _commit, module: module_paths.get(module),
    )
    monkeypatch.setattr(
        validator,
        "_commit_ancestor_initializers",
        lambda *_args: set(),
    )

    closure = validator._ast_local_import_closure(
        {
            entrypoint: (
                b"from scripts import p4_2a_v2_dev_common\n"
                b"from alphapilot.core.config import settings\n"
            ),
            dev_common: b"",
            config: b"",
        },
        project_root=PROJECT_ROOT,
        implementation_commit=validator.V1_FAIL_CLOSE_COMMIT,
    )
    assert closure == {entrypoint, dev_common, config}


def test_ast_closure_rejects_nonliteral_dynamic_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entrypoint = "scripts/rehearse_p4_2a_v2_heldout_full_path.py"
    monkeypatch.setattr(validator, "_resolve_commit_module", lambda *_args: None)

    with pytest.raises(
        validator.RehearsalV2ValidationError,
        match="non-literal dynamic import",
    ):
        validator._ast_local_import_closure(
            {entrypoint: b"__import__(module_name)\n"},
            project_root=PROJECT_ROOT,
            implementation_commit=validator.V1_FAIL_CLOSE_COMMIT,
        )


def test_non_descendant_implementation_commit_fails_before_archive_semantics() -> None:
    with pytest.raises(
        validator.RehearsalV2ValidationError,
        match="does not descend from the v1 fail-close commit",
    ):
        validator._validate_commit(PROJECT_ROOT, NON_DESCENDANT_COMMIT)


def test_v1_receipt_directory_substitution_fails_without_fallback() -> None:
    result = _python_child(
        """
from scripts import validate_p4_2a_v2_heldout_rehearsal_bundle as validator
try:
    validator.validate_rehearsal_bundle(
        validator.PROJECT_ROOT / validator.V1_REHEARSAL_DIRECTORY,
        project_root=validator.PROJECT_ROOT,
    )
except validator.RehearsalV2ValidationError as exc:
    if "retired rehearsal v1 can never satisfy the v2 gate" not in str(exc):
        raise
else:
    raise SystemExit("retired v1 directory was accepted")
""",
        startup_hash_seed="0",
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "payload",
    [
        b'{"a":1,"a":2}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":1e9999}',
        b'{"a":-0}',
        b'{"a":-0.0}',
        b'"\xff"',
    ],
)
def test_strict_json_rejects_ambiguous_or_nonportable_payloads(payload: bytes) -> None:
    with pytest.raises(validator.RehearsalV2ValidationError):
        validator.strict_json_loads(payload, source="negative probe")


def test_registered_schema_rejects_required_zero_byte_package_initializer() -> None:
    schema = validator.strict_json_loads(
        (PROJECT_ROOT / validator.BUNDLE_SCHEMA_PATH).read_bytes(),
        source="registered bundle schema",
    )
    assert isinstance(schema, dict)
    control_schema = schema["$defs"]["controlFile"]
    empty_blob = validator._commit_blob(
        PROJECT_ROOT, validator.V1_FAIL_CLOSE_COMMIT, ZERO_BYTE_INITIALIZER
    )
    assert empty_blob == b""
    record = {
        "logical_name": ZERO_BYTE_INITIALIZER,
        "bundle_relative_path": ("archive/control-surface/root/repo/" + ZERO_BYTE_INITIALIZER),
        "source_kind": "package_initializer",
        "repository_path": ZERO_BYTE_INITIALIZER,
        "bytes": len(empty_blob),
        "sha256": hashlib.sha256(empty_blob).hexdigest(),
    }

    errors = list(Draft202012Validator(schema).descend(record, control_schema))
    assert any(
        list(error.path) == ["bytes"] and "less than the minimum of 1" in error.message
        for error in errors
    )

    current_blob = (PROJECT_ROOT / ZERO_BYTE_INITIALIZER).read_bytes()
    assert current_blob == b"\n"
    current_record = dict(record)
    current_record["bytes"] = len(current_blob)
    current_record["sha256"] = hashlib.sha256(current_blob).hexdigest()
    assert not list(Draft202012Validator(schema).descend(current_record, control_schema))


def test_validator_cli_has_no_bundle_path_override_or_v1_fallback() -> None:
    parser = validator._parser()
    help_text = parser.format_help()
    assert "--bundle" not in help_text
    assert "--path" not in help_text
    assert "v1 fallback" in help_text
    assert (
        "enforce_runtime_environment"
        not in inspect.signature(validator.validate_rehearsal_bundle).parameters
    )
    with pytest.raises(SystemExit):
        parser.parse_args(["/tmp/substituted-bundle"])


def test_registered_directory_authority_is_the_unresolved_literal_path(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    expected = (project_root / validator.REGISTERED_V2_REHEARSAL_DIRECTORY).absolute()

    assert validator.registered_rehearsal_directory(project_root) == expected
    assert expected.resolve(strict=False) == expected


@pytest.mark.parametrize("broken", [False, True])
def test_registered_directory_rejects_leaf_symlink(
    tmp_path: Path,
    broken: bool,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    literal = (project_root / validator.REGISTERED_V2_REHEARSAL_DIRECTORY).absolute()
    literal.parent.mkdir(parents=True)
    target = tmp_path / ("missing-target" if broken else "real-target")
    if not broken:
        target.mkdir()
    literal.symlink_to(target, target_is_directory=True)

    with pytest.raises(
        validator.RehearsalV2ValidationError,
        match="symlink",
    ):
        validator.registered_rehearsal_directory(project_root)


@pytest.mark.parametrize("broken", [False, True])
def test_registered_directory_rejects_ancestor_symlink(
    tmp_path: Path,
    broken: bool,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    target = tmp_path / ("missing-docs" if broken else "real-docs")
    if not broken:
        target.mkdir()
    (project_root / "docs").symlink_to(target, target_is_directory=True)

    with pytest.raises(
        validator.RehearsalV2ValidationError,
        match="symlink",
    ):
        validator.registered_rehearsal_directory(project_root)


def test_registered_directory_rejects_an_alias_before_runtime_validation(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    registered = (project_root / validator.REGISTERED_V2_REHEARSAL_DIRECTORY).absolute()
    registered.mkdir(parents=True)
    alias = tmp_path / "registered-alias"
    alias.symlink_to(registered, target_is_directory=True)

    with pytest.raises(
        validator.RehearsalV2ValidationError,
        match="exact literal path",
    ):
        validator.validate_rehearsal_bundle(alias, project_root=project_root)


def test_external_normal_staging_directory_remains_a_valid_parameter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = tmp_path / "external-staging"
    staging.mkdir()
    monkeypatch.setattr(validator, "_validate_locked_runtime_environment", lambda: None)

    with pytest.raises(
        validator.RehearsalV2ValidationError,
        match="bundle manifest is unavailable",
    ):
        validator.validate_rehearsal_bundle(staging, project_root=PROJECT_ROOT)


def test_validator_main_passes_only_the_registered_literal_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = validator.registered_rehearsal_directory(PROJECT_ROOT)
    observed: list[tuple[Path, Path]] = []

    def fake_validate(directory: Path, *, project_root: Path) -> dict[str, object]:
        observed.append((directory, project_root))
        return {}

    monkeypatch.setattr(validator, "validate_rehearsal_bundle", fake_validate)
    assert validator._main([]) == 0
    assert observed == [(expected, PROJECT_ROOT)]
