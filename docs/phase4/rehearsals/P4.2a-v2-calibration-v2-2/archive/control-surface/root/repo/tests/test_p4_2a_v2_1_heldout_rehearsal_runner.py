from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import socket
import sqlite3
import subprocess
import textwrap
import threading
from collections import Counter
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from scripts import prepare_p4_2a_v2_heldout as prepare
from scripts import rehearse_p4_2a_v2_1_heldout_full_path as runner
from scripts import rehearse_p4_2a_v2_heldout_full_path as retired_v2


def test_registered_cli_exposes_only_the_execute_switch() -> None:
    parser = runner._parser()
    actions = {action.dest for action in parser._actions}
    assert actions == {"execute", "help"}
    assert set(inspect.signature(runner.run_rehearsal).parameters) == set()


def test_runner_bootstrap_precedes_every_shadowable_import() -> None:
    tree = ast.parse(Path(runner.__file__).read_bytes())
    shadowable_import_lines = [
        node.lineno
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and not (
            isinstance(node, ast.ImportFrom)
            and node.module == "__future__"
        )
        and not (
            isinstance(node, ast.Import)
            and {alias.name for alias in node.names}.issubset({"os", "sys"})
        )
    ]
    sys_path_lock_lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Attribute)
            and isinstance(target.value.value, ast.Name)
            and target.value.value.id == "sys"
            and target.value.attr == "path"
            for target in node.targets
        )
    ]
    assert shadowable_import_lines
    assert sys_path_lock_lines
    assert min(shadowable_import_lines) > min(sys_path_lock_lines)


def test_early_sha256_and_fixed_interpreter_binding() -> None:
    executable = runner._fixed_python_executable().resolve(strict=True)
    assert runner._early_sha256(executable.as_posix()) == hashlib.sha256(
        executable.read_bytes()
    ).hexdigest()
    assert runner._early_sha256(executable.as_posix()) == runner._EARLY_PYTHON_EXECUTABLE_SHA256
    original_argv_executable = Path(runner._early_orig_argv_executable())
    assert runner._early_sha256(original_argv_executable.as_posix()) == hashlib.sha256(
        original_argv_executable.read_bytes()
    ).hexdigest()
    assert (
        runner._early_sha256(original_argv_executable.as_posix())
        == runner._EARLY_ORIG_ARGV_EXECUTABLE_SHA256
    )


def test_locked_help_bootstrap_reaches_parser_without_registered_execution() -> None:
    completed = subprocess.run(
        [
            runner._fixed_python_executable().as_posix(),
            "-S",
            "-P",
            "-B",
            Path(runner.__file__).as_posix(),
            "--help",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=runner._EARLY_EXEC_ENVIRONMENT,
    )
    assert completed.returncode == 0
    assert "--execute" in completed.stdout
    assert "Traceback" not in completed.stderr


def test_ambient_registered_invocations_fail_before_claim_or_validation(
    tmp_path: Path,
) -> None:
    sitecustomize = tmp_path / "sitecustomize.py"
    marker = tmp_path / "ambient-sitecustomize-ran"
    sitecustomize.write_text(
        "from pathlib import Path\n"
        f"Path({marker.as_posix()!r}).write_text('ambient side effect\\n')\n"
    )
    target = runner.registered_rehearsal_directory(runner.PROJECT_ROOT)
    claim = runner.registered_execution_claim_directory(runner.PROJECT_ROOT, target)
    target_before = retired_v2._tree_fingerprint(target)
    claim_before = retired_v2._tree_fingerprint(claim)
    ambient = {**os.environ, "PYTHONPATH": tmp_path.as_posix()}

    runner_result = subprocess.run(
        [
            runner._fixed_python_executable().as_posix(),
            Path(runner.__file__).as_posix(),
            "--execute",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=ambient,
    )
    assert runner_result.returncode != 0
    assert "must start in the exact -S -P -B" in runner_result.stderr
    assert marker.is_file()
    assert retired_v2._tree_fingerprint(target) == target_before
    assert retired_v2._tree_fingerprint(claim) == claim_before

    validator_result = subprocess.run(
        [
            runner._fixed_python_executable().as_posix(),
            (runner.PROJECT_ROOT / "scripts/validate_p4_2a_v2_1_heldout_rehearsal_bundle.py")
            .as_posix(),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=ambient,
    )
    assert validator_result.returncode != 0
    assert "must start in the exact -S -P -B" in validator_result.stderr
    assert retired_v2._tree_fingerprint(target) == target_before
    assert retired_v2._tree_fingerprint(claim) == claim_before


def test_runpy_alternate_entries_cannot_impersonate_registered_commands(
    tmp_path: Path,
) -> None:
    runner_marker = tmp_path / "runner-pre-effect"
    validator_marker = tmp_path / "validator-pre-effect"
    target = runner.registered_rehearsal_directory(runner.PROJECT_ROOT)
    claim = runner.registered_execution_claim_directory(runner.PROJECT_ROOT, target)
    target_before = retired_v2._tree_fingerprint(target)
    claim_before = retired_v2._tree_fingerprint(claim)
    runner_script = Path(runner.__file__).resolve()
    validator_script = (
        runner.PROJECT_ROOT
        / "scripts/validate_p4_2a_v2_1_heldout_rehearsal_bundle.py"
    ).resolve()
    runner_code = (
        "import pathlib,runpy,sys;"
        f"pathlib.Path({runner_marker.as_posix()!r}).write_text('pre-effect\\n');"
        f"sys.argv=[{runner_script.as_posix()!r},'--execute'];"
        f"runpy.run_path({runner_script.as_posix()!r},run_name='__main__')"
    )
    runner_result = subprocess.run(
        [
            runner._fixed_python_executable().as_posix(),
            "-S",
            "-P",
            "-B",
            "-c",
            runner_code,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=runner._EARLY_EXEC_ENVIRONMENT,
    )
    assert runner_result.returncode != 0
    assert "must start in the exact -S -P -B" in runner_result.stderr
    assert runner_marker.is_file()
    assert retired_v2._tree_fingerprint(target) == target_before
    assert retired_v2._tree_fingerprint(claim) == claim_before

    validator_code = (
        "import pathlib,runpy,sys;"
        f"pathlib.Path({validator_marker.as_posix()!r}).write_text('pre-effect\\n');"
        f"sys.argv=[{validator_script.as_posix()!r}];"
        f"runpy.run_path({validator_script.as_posix()!r},run_name='__main__')"
    )
    validator_result = subprocess.run(
        [
            runner._fixed_python_executable().as_posix(),
            "-S",
            "-P",
            "-B",
            "-c",
            validator_code,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=runner._EARLY_EXEC_ENVIRONMENT,
    )
    assert validator_result.returncode != 0
    assert "must start in the exact -S -P -B" in validator_result.stderr
    assert validator_marker.is_file()
    assert retired_v2._tree_fingerprint(target) == target_before
    assert retired_v2._tree_fingerprint(claim) == claim_before


def test_fixed_git_prefix_disables_repository_fsmonitor(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    marker = tmp_path / "fsmonitor-executed"
    subprocess.run(
        ["/usr/bin/git", "-C", repository.as_posix(), "init", "--quiet"],
        check=True,
        capture_output=True,
        env=runner._sanitized_git_environment(),
    )
    subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            repository.as_posix(),
            "config",
            "core.fsmonitor",
            f"/usr/bin/touch {marker.as_posix()}",
        ],
        check=True,
        capture_output=True,
        env=runner._sanitized_git_environment(),
    )
    unsafe = subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            repository.as_posix(),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            "tracked.txt",
        ],
        check=False,
        capture_output=True,
        env=runner._sanitized_git_environment(),
    )
    assert unsafe.returncode == 0
    assert marker.is_file()
    marker.unlink()
    completed = runner._git_read(
        repository,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        "tracked.txt",
    )
    assert completed.returncode == 0
    assert not marker.exists()


def test_git_authority_ignores_replace_refs_and_rejects_legacy_grafts(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    setup_environment = runner._sanitized_git_environment(synthetic_identity=True)
    setup_environment.pop("GIT_NO_REPLACE_OBJECTS")
    subprocess.run(
        ["/usr/bin/git", "-C", repository.as_posix(), "init", "--quiet"],
        check=True,
        capture_output=True,
        env=setup_environment,
    )
    tracked = repository / "tracked.txt"
    tracked.write_text("original\n")
    subprocess.run(
        ["/usr/bin/git", "-C", repository.as_posix(), "add", "--", "tracked.txt"],
        check=True,
        capture_output=True,
        env=setup_environment,
    )
    subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            repository.as_posix(),
            "commit",
            "--quiet",
            "--no-gpg-sign",
            "-m",
            "original",
        ],
        check=True,
        capture_output=True,
        env=setup_environment,
    )
    original_commit = subprocess.run(
        ["/usr/bin/git", "-C", repository.as_posix(), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=setup_environment,
    ).stdout.strip()
    tracked.write_text("replacement\n")
    subprocess.run(
        ["/usr/bin/git", "-C", repository.as_posix(), "add", "--", "tracked.txt"],
        check=True,
        capture_output=True,
        env=setup_environment,
    )
    subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            repository.as_posix(),
            "commit",
            "--quiet",
            "--no-gpg-sign",
            "-m",
            "replacement",
        ],
        check=True,
        capture_output=True,
        env=setup_environment,
    )
    replacement_commit = subprocess.run(
        ["/usr/bin/git", "-C", repository.as_posix(), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=setup_environment,
    ).stdout.strip()
    subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            repository.as_posix(),
            "replace",
            original_commit,
            replacement_commit,
        ],
        check=True,
        capture_output=True,
        env=setup_environment,
    )
    unsafe_blob = subprocess.run(
        [
            "/usr/bin/git",
            *runner.GIT_CONFIG_PREFIX,
            "-C",
            repository.as_posix(),
            "show",
            f"{original_commit}:tracked.txt",
        ],
        check=True,
        capture_output=True,
        env=setup_environment,
    ).stdout
    assert unsafe_blob == b"replacement\n"
    assert runner._git_read(
        repository,
        "show",
        f"{original_commit}:tracked.txt",
    ).stdout == b"original\n"

    grafts = repository / ".git/info/grafts"
    grafts.write_text(f"{replacement_commit}\n")
    unsafe_parents = subprocess.run(
        [
            "/usr/bin/git",
            *runner.GIT_CONFIG_PREFIX,
            "-C",
            repository.as_posix(),
            "rev-list",
            "--parents",
            "-n",
            "1",
            replacement_commit,
            "--",
        ],
        check=True,
        capture_output=True,
        env=setup_environment,
    ).stdout.decode("ascii").strip().split()
    assert unsafe_parents == [replacement_commit]
    with pytest.raises(runner.RehearsalV21Error, match="graft"):
        runner._git_read(
            repository,
            "rev-list",
            "--parents",
            "-n",
            "1",
            replacement_commit,
            "--",
        )


def test_audit_git_allowlist_requires_exact_fixed_config_prefix() -> None:
    operation = [
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--",
        "scripts/prepare_p4_2a_v2_heldout.py",
    ]
    policy = runner._AuditPolicy(
        project_root=runner.PROJECT_ROOT,
        write_roots=(),
        sqlite_roots=(),
        subprocess_mode="canonical_git_reads",
    )
    exact_command = [
        "/usr/bin/git",
        *runner.GIT_CONFIG_PREFIX,
        "-C",
        runner.PROJECT_ROOT.as_posix(),
        *operation,
    ]
    assert runner._allowed_git_subprocess(
        exact_command,
        None,
        runner._sanitized_git_environment(),
        policy,
    )
    assert not runner._allowed_git_subprocess(
        [
            "/usr/bin/git",
            "-C",
            runner.PROJECT_ROOT.as_posix(),
            *operation,
        ],
        None,
        runner._sanitized_git_environment(),
        policy,
    )
    assert runner._read_only_git_operation(
        (
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "--no-renames",
            "1" * 40,
            "2" * 40,
            "--",
        )
    )
    assert "core.commitGraph=false" in runner.GIT_CONFIG_PREFIX
    assert runner._sanitized_git_environment()["GIT_NO_REPLACE_OBJECTS"] == "1"


def test_fixture_is_the_preregistered_available_time_frame() -> None:
    rows = list(runner._fixture_rows())
    assert len(rows) == runner.FIXTURE_RAW_COUNT == 4_048
    assert Counter(str(row[1]) for row in rows) == runner.FIXTURE_BY_SOURCE
    assert all(row[6] == "2026-08-06 00:01:00" for row in rows)
    assert all(isinstance(row[0], int) and not isinstance(row[0], bool) for row in rows)
    assert len({row[0] for row in rows}) == 4_048


def test_deterministic_sleeper_advances_only_by_requested_duration() -> None:
    clock = runner.DeterministicClock()
    assert clock.monotonic() == runner.MONOTONIC_INITIAL_SECONDS
    clock.sleep(1.0)
    assert clock.monotonic() == runner.MONOTONIC_INITIAL_SECONDS + 1.0
    with pytest.raises(runner.RehearsalV21Error):
        clock.sleep(-0.001)


def test_v2_1_merkle_domain_does_not_reuse_retired_v2() -> None:
    payloads = {"a": b"one", "b": b"two"}
    assert runner._merkle_root(payloads) != retired_v2._merkle_root(payloads)
    assert runner._bundle_root(
        runner._merkle_root({"a": b"one"}),
        runner._merkle_root({"a": b"one"}),
        runner._merkle_root({"a": b"one"}),
    ) != retired_v2._merkle_root({"a": b"one"})


def test_registered_destination_rejects_symlink_alias(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "docs").symlink_to(tmp_path)
    with pytest.raises(runner.RehearsalV21Error, match="symlink"):
        runner.registered_rehearsal_directory(root)


def test_control_file_rejects_a_symlink_ancestor(tmp_path: Path) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "control.json").write_text("{}\n")
    (root / "alias").symlink_to(outside, target_is_directory=True)
    with pytest.raises(runner.RehearsalV21Error, match=r"alias|regular"):
        runner._safe_repository_file(root, "alias/control.json", "test control")


def test_all_preregistered_path_hash_pairs_are_bound() -> None:
    declared = runner._declared_preregistration_hashes(runner.PROJECT_ROOT)
    assert len(declared) == 16
    assert (
        declared[
            "docs/phase4/reports/P4.2a-heldout-full-pool-inference-cost-acceptance-20260810.json"
        ]
        == "7555b4e7ade255d0d947f2e9005246a490be92db71c8db1e559e616572e033e7"
    )
    assert (
        declared["docs/phase4/rehearsals/P4.2a-v2-calibration-v2/bundle.json"]
        == "0f3cbb3fe0251994da457e1a8d36a09b06ba127a8f0b584a2d924eb02e47b01f"
    )


def test_prediction_timing_preregistration_and_targets_are_explicit_controls() -> None:
    assert (
        runner.FROZEN_FILE_SHA256[runner.PREDICTION_TIMING_PREREG_RELATIVE.as_posix()]
        == runner.PREDICTION_TIMING_PREREG_SHA256
    )
    assert runner.PREDICTION_TIMING_IMPLEMENTATION_PATHS == (
        "scripts/run_p4_2a_offline_extract.py",
        "tests/test_p4_2a_offline_extract.py",
    )
    assert (
        prepare._unique_added_path_commit(
            runner.PROJECT_ROOT,
            runner.PREDICTION_TIMING_PREREG_RELATIVE,
        )
        == runner.PREDICTION_TIMING_PREREG_COMMIT
    )
    document = runner._prediction_timing_preregistration_document(runner.PROJECT_ROOT)
    records = document["prospective_scope_extension"]["paths"]
    for record in records:
        completed = runner._git_read(
            runner.PROJECT_ROOT,
            "show",
            f"{runner.PREDICTION_TIMING_PREREG_COMMIT}:{record['path']}",
        )
        assert completed.returncode == 0
        assert runner._sha256(completed.stdout) == record["current_sha256"]


def test_implementation_surface_rejects_extra_or_forbidden_path_operations() -> None:
    expected = runner._registered_implementation_statuses(runner.PROJECT_ROOT)
    assert len(expected) == 14
    valid = "".join(f"{status}\t{relative}\n" for relative, status in expected.items()).encode()
    runner._require_exact_implementation_name_status(valid, expected)
    with pytest.raises(runner.RehearsalV21Error, match="surface drifted"):
        runner._require_exact_implementation_name_status(
            valid + b"M\tscripts/unregistered.py\n",
            expected,
        )
    with pytest.raises(runner.RehearsalV21Error, match="forbidden"):
        runner._require_exact_implementation_name_status(
            valid.replace(b"M\t", b"D\t", 1),
            expected,
        )


def test_registered_environment_requires_the_exact_minimal_mapping() -> None:
    exact = dict(runner._EARLY_EXEC_ENVIRONMENT)
    assert exact["OPENBLAS_MAIN_FREE"] == "1"
    assert exact["PYTHONPYCACHEPREFIX"] == "/dev/null"
    assert exact["__CF_USER_TEXT_ENCODING"] == f"0x{os.getuid():X}:0x0:0x0"
    assert runner._registered_environment_is_exact(exact)
    for name in ("DYLD_INSERT_LIBRARIES", "GIT_DIR", "TMPDIR", "HOME"):
        assert not runner._registered_environment_is_exact({**exact, name: "/tmp/ambient"})

    runtime_paths = runner._fixed_runtime_paths()
    site = (runner.PROJECT_ROOT / runner.PACKAGE_ROOT_RELATIVE).as_posix()
    repository = runner.PROJECT_ROOT.as_posix()
    source = (runner.PROJECT_ROOT / "src").as_posix()
    lib_dynload = next(path for path in runtime_paths if path.endswith("/lib-dynload"))
    assert runtime_paths.index(lib_dynload) < runtime_paths.index(site)
    assert runtime_paths.index(site) < runtime_paths.index(repository)
    assert runtime_paths.index(repository) < runtime_paths.index(source)


def test_loaded_module_origin_classifier_rejects_repository_shadowing(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    scripts = root / "scripts"
    alphapilot = root / "src/alphapilot"
    site = root / ".venv/lib/python3.12/site-packages"
    stdlib = tmp_path / "stdlib"
    for directory in (scripts, alphapilot, site, stdlib):
        directory.mkdir(parents=True)
    runner_path = scripts / "rehearse_p4_2a_v2_1_heldout_full_path.py"
    script_path = scripts / "registered.py"
    alphapilot_path = alphapilot / "__init__.py"
    site_path = site / "jsonschema.py"
    stdlib_path = stdlib / "json.py"
    for path in (runner_path, script_path, alphapilot_path, site_path, stdlib_path):
        path.write_text("# bound source\n")

    def file_module(name: str, path: Path) -> ModuleType:
        module = ModuleType(name)
        module.__file__ = path.as_posix()
        return module

    scripts_namespace = ModuleType("scripts")
    scripts_namespace.__path__ = [scripts.as_posix()]
    alphapilot_package = file_module("alphapilot", alphapilot_path)
    alphapilot_package.__path__ = [alphapilot.as_posix()]
    builtin = ModuleType("built_in")
    builtin.__spec__ = ModuleSpec("built_in", loader=None, origin="built-in")
    frozen = ModuleType("frozen")
    frozen.__spec__ = ModuleSpec("frozen", loader=None, origin="frozen")
    originless = ModuleType("_cython_3_2_5")
    six_path = site / "six.py"
    six = file_module("six", six_path)
    six_path.write_text("# site module with an empty package path\n")
    six.__path__ = []
    six_moves = ModuleType("six.moves")
    six_moves.__path__ = []
    six.__dict__["moves"] = six_moves
    modules: dict[str, object] = {
        "__main__": file_module("__main__", runner_path),
        "scripts": scripts_namespace,
        "scripts.registered": file_module("scripts.registered", script_path),
        "alphapilot": alphapilot_package,
        "jsonschema": file_module("jsonschema", site_path),
        "json": file_module("json", stdlib_path),
        "built_in": builtin,
        "frozen": frozen,
        "_cython_3_2_5": originless,
        "six": six,
        "six.moves": six_moves,
    }
    assert runner._classify_loaded_module_origins(
        modules=modules,
        repository_root=root,
        runner_path=runner_path,
        site_root=site,
        stdlib_roots=(stdlib,),
    ) == frozenset(
        {
            "scripts/rehearse_p4_2a_v2_1_heldout_full_path.py",
            "scripts/registered.py",
            "src/alphapilot/__init__.py",
        }
    )

    shadow_path = root / "yaml.py"
    shadow_path.write_text("# unregistered shadow\n")
    with pytest.raises(RuntimeError, match="not registered"):
        runner._classify_loaded_module_origins(
            modules={**modules, "yaml": file_module("yaml", shadow_path)},
            repository_root=root,
            runner_path=runner_path,
            site_root=site,
            stdlib_roots=(stdlib,),
        )

    unknown_empty_package = ModuleType("unknown.empty")
    unknown_empty_package.__path__ = []
    with pytest.raises(RuntimeError, match="empty"):
        runner._classify_loaded_module_origins(
            modules={**modules, "unknown.empty": unknown_empty_package},
            repository_root=root,
            runner_path=runner_path,
            site_root=site,
            stdlib_roots=(stdlib,),
        )
    shadow_package = root / "jsonschema"
    shadow_package.mkdir()
    bad_namespace = ModuleType("jsonschema")
    bad_namespace.__path__ = [shadow_package.as_posix()]
    with pytest.raises(RuntimeError, match="not registered"):
        runner._classify_loaded_module_origins(
            modules={**modules, "jsonschema": bad_namespace},
            repository_root=root,
            runner_path=runner_path,
            site_root=site,
            stdlib_roots=(stdlib,),
        )

    site_shadow_path = site / "alphapilot.py"
    site_shadow_path.write_text("# third-party namespace shadow\n")
    with pytest.raises(RuntimeError, match="not registered"):
        runner._classify_loaded_module_origins(
            modules={
                **modules,
                "alphapilot": file_module("alphapilot", site_shadow_path),
            },
            repository_root=root,
            runner_path=runner_path,
            site_root=site,
            stdlib_roots=(stdlib,),
        )


def test_loaded_repository_sources_must_be_in_commit_ast_closure() -> None:
    runner._require_loaded_repository_sources_in_closure(
        ("scripts/registered.py",),
        {"scripts/registered.py", "scripts/lazy.py"},
    )
    with pytest.raises(runner.RehearsalV21Error, match="absent"):
        runner._require_loaded_repository_sources_in_closure(
            ("scripts/shadow.py",),
            {"scripts/registered.py"},
        )


def test_temp_authority_ignores_ambient_tmpdir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    hostile = tmp_path / "project/tmp"
    hostile.mkdir(parents=True)
    monkeypatch.setenv("TMPDIR", str(hostile))
    authority = runner._create_temp_authority(
        project_root=tmp_path / "project",
        forbidden_paths=(hostile,),
    )
    try:
        assert authority.parent == Path("/private/tmp")
        assert not authority.is_relative_to(tmp_path)
        assert not any(hostile.iterdir())
    finally:
        runner._remove_temp_authority(authority)


def test_noop_sleeper_probe_contract_is_exact() -> None:
    assert runner.CNINFO_REQUEST_COUNT == 2_824
    assert runner.CNINFO_GAP_COUNT == 2_823
    assert runner.GateProbeEvidence.__dataclass_fields__[
        "noop_sleeper_rejected_before_second_fetch"
    ]


def test_noop_sleeper_is_rejected_before_second_start() -> None:
    pacer = prepare._CninfoStartPacer(lambda: 1_000.0, lambda _duration: None)
    pacer.before_fetch()
    with pytest.raises(prepare.HeldoutPreparationError, match="did not advance"):
        pacer.before_fetch()
    assert pacer.evidence()["request_start_count"] == 1


def test_runtime_start_negative_probes_use_only_temp_inputs(tmp_path: Path) -> None:
    binding = cast(prepare.HeldoutBinding, SimpleNamespace(root=tmp_path))
    assert runner._run_runtime_start_negative_probes(binding) is True


def test_generated_synthetic_release_receipt_matches_frozen_schema() -> None:
    bundle = {"merkle": {"bundle_root_sha256": "3" * 64}}
    payload = runner._synthetic_release_payload(
        bundle=bundle,
        bundle_sha256="4" * 64,
        implementation_commit="5" * 40,
        evidence_commit="6" * 40,
        reviewed_head="6" * 40,
        review_sha256="7" * 64,
    )
    receipt = json.loads(payload)
    schema = json.loads((runner.PROJECT_ROOT / runner.RELEASE_SCHEMA_RELATIVE).read_bytes())
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(receipt))
    assert errors == []
    assert receipt["still_gated"][0] == "finalize-owner-adjudication"
    review = json.loads(runner._synthetic_review_request_payload(bundle))
    assert review["prediction_timing_preregistration"] == {
        "path": runner.PREDICTION_TIMING_PREREG_RELATIVE.as_posix(),
        "sha256": runner.PREDICTION_TIMING_PREREG_SHA256,
        "creation_commit": runner.PREDICTION_TIMING_PREREG_COMMIT,
    }


def test_registered_preflight_precedes_claim_and_pipeline_in_control_flow() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(runner._run_rehearsal_to)))
    function = tree.body[0]
    assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    calls: dict[str, int] = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.setdefault(node.func.id, node.lineno)
    assert calls["_assert_registered_environment"] < calls["_git_binding"]
    assert calls["_git_binding"] < calls["_control_payloads"]
    assert calls["_control_payloads"] < calls["_create_temp_authority"]
    assert calls["_create_temp_authority"] < calls["_claim_registered_execution"]
    assert calls["_claim_registered_execution"] < calls["_execute_temp_pipeline"]


def test_minimal_registered_child_environment_and_flags_are_real() -> None:
    probe = subprocess.run(
        [
            runner._fixed_python_executable().as_posix(),
            "-S",
            "-P",
            "-B",
            "-c",
            (
                "import json,os,sys;"
                "print(json.dumps({'environment':dict(os.environ),"
                "'hash':sys.flags.hash_randomization,'no_site':sys.flags.no_site,"
                "'no_user_site':sys.flags.no_user_site,'safe_path':sys.flags.safe_path,"
                "'bytecode':sys.dont_write_bytecode,'pycache_prefix':sys.pycache_prefix,"
                "'executable':sys.executable},sort_keys=True))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=runner._EARLY_EXEC_ENVIRONMENT,
    )
    evidence = json.loads(probe.stdout)
    assert evidence == {
        "environment": runner._EARLY_EXEC_ENVIRONMENT,
        "hash": 0,
        "no_site": 1,
        "no_user_site": 1,
        "safe_path": True,
        "bytecode": True,
        "pycache_prefix": "/dev/null",
        "executable": runner._fixed_python_executable().as_posix(),
    }


def test_temp_execution_guard_rejects_external_effects(tmp_path: Path) -> None:
    project = tmp_path / "project"
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside.txt"
    project.mkdir()
    workspace.mkdir()

    with (
        pytest.raises(Exception, match=r"outside|external"),
        runner._guarded_temp_execution(project_root=project, workspace=workspace),
    ):
        outside.write_text("forbidden")
    with (
        pytest.raises(Exception, match="subprocess"),
        runner._guarded_temp_execution(project_root=project, workspace=workspace),
    ):
        subprocess.run(["/usr/bin/true"], check=True)
    malicious_output = tmp_path / "git-show-output"
    with (
        pytest.raises(Exception, match="subprocess"),
        runner._guarded_temp_execution(project_root=project, workspace=workspace),
    ):
        subprocess.run(
            [
                "git",
                "-C",
                str(runner.PROJECT_ROOT),
                "show",
                f"--output={malicious_output}",
                "HEAD",
            ],
            check=True,
        )
    assert not malicious_output.exists()
    with (
        pytest.raises(Exception, match="subprocess"),
        runner._guarded_temp_execution(project_root=project, workspace=workspace),
    ):
        subprocess.run(
            ["/usr/bin/git", "-C", str(runner.PROJECT_ROOT), "rev-parse", "HEAD"],
            check=True,
            env={**runner._sanitized_git_environment(), "HOME": str(tmp_path)},
        )
    with (
        pytest.raises(Exception, match="database"),
        runner._guarded_temp_execution(project_root=project, workspace=workspace),
    ):
        sqlite3.connect(project / "production.db")
    with (
        pytest.raises(Exception, match="network"),
        runner._guarded_temp_execution(project_root=project, workspace=workspace),
    ):
        socket.create_connection(("127.0.0.1", 1))
    with (
        pytest.raises(Exception, match="alternate process"),
        runner._guarded_temp_execution(project_root=project, workspace=workspace),
    ):
        os.system("/usr/bin/true")
    with (
        pytest.raises(Exception, match="thread"),
        runner._guarded_temp_execution(project_root=project, workspace=workspace),
    ):
        threading.Thread(target=lambda: None).start()


def test_temp_execution_guard_allows_only_required_canonical_git_reads(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    commit = subprocess.run(
        ["git", "-C", str(runner.PROJECT_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    with runner._guarded_temp_execution(
        project_root=runner.PROJECT_ROOT,
        workspace=workspace,
    ):
        assert prepare._require_git_commit(runner.PROJECT_ROOT, commit, "test") == commit
        prepare._require_git_ancestor(
            runner.PROJECT_ROOT,
            runner.PREREGISTRATION_COMMIT,
            commit,
            "test",
        )
        assert prepare._git_blob(
            runner.PROJECT_ROOT,
            commit,
            runner.PREREGISTRATION_RELATIVE,
            "test",
        )
        subprocess.run(
            [
                "/usr/bin/git",
                *runner.GIT_CONFIG_PREFIX,
                "-C",
                str(runner.PROJECT_ROOT),
                "rev-list",
                "--parents",
                "-n",
                "1",
                runner.PREDICTION_TIMING_PREREG_COMMIT,
            ],
            check=True,
            capture_output=True,
            env=runner._sanitized_git_environment(),
        )


def test_explicit_implementation_binding_accepts_a_descendant_evidence_head(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    environment = runner._sanitized_git_environment(synthetic_identity=True)
    subprocess.run(
        ["/usr/bin/git", "clone", "--quiet", "--local", str(runner.PROJECT_ROOT), str(repository)],
        check=True,
        env=environment,
    )
    subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(repository),
            "commit",
            "--quiet",
            "--allow-empty",
            "--no-gpg-sign",
            "-m",
            "synthetic post-implementation evidence",
        ],
        check=True,
        env=environment,
    )
    assert (
        subprocess.run(
            ["/usr/bin/git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        ).stdout.strip()
        != runner.PREDICTION_TIMING_PREREG_COMMIT
    )
    binding = runner._git_binding_for_commit(
        repository,
        runner.PREDICTION_TIMING_PREREG_COMMIT,
    )
    assert binding.implementation_commit == runner.PREDICTION_TIMING_PREREG_COMMIT


def test_run_materialize_control_flow_composes_gate_runtime_and_effect_order() -> None:
    tree = ast.parse(textwrap.dedent(inspect.getsource(prepare.run_materialize)))
    function = tree.body[0]
    assert isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
    first = function.body[0]
    assert isinstance(first, ast.Assign)
    assert isinstance(first.value, ast.Call)
    assert isinstance(first.value.func, ast.Name)
    assert first.value.func.id == "validate_v2_1_stage_authorization"

    calls = {
        node.func.id: node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert calls["validate_v2_1_stage_authorization"] < calls["_real_runtime_start_preflight"]
    assert calls["_real_runtime_start_preflight"] < calls["_window_rows"]
    assert calls["_window_rows"] < calls["_materialization_design"]
    assert calls["_materialization_design"] < calls["_publish_create_only"]


def test_single_temp_full_path_is_commit_bound_and_never_registered() -> None:
    git_binding = runner._git_binding(runner.PROJECT_ROOT)
    preregistration = runner._preregistration_document(runner.PROJECT_ROOT)
    implementation = preregistration["implementation_contract"]
    registered_paths = set(runner.PREDICTION_TIMING_IMPLEMENTATION_PATHS)
    for field in (
        "registered_modified_consumers",
        "registered_new_files",
        "registered_existing_test_updates",
    ):
        registered_paths.update(implementation[field])
    uncommitted: list[str] = []
    for relative in sorted(registered_paths):
        current = runner._safe_repository_file(
            runner.PROJECT_ROOT,
            relative,
            f"registered implementation {relative}",
        ).read_bytes()
        try:
            committed = git_binding.blob_reader(relative)
        except runner.RehearsalV21Error:
            uncommitted.append(relative)
            continue
        if current != committed:
            uncommitted.append(relative)
    if uncommitted:
        pytest.skip(
            "registered implementation bytes not yet committed: " + ", ".join(uncommitted)
        )
    runner._control_payloads(
        project_root=runner.PROJECT_ROOT,
        git_binding=git_binding,
    )
    with (
        runner._temporary_authority_scope(
            project_root=runner.PROJECT_ROOT,
            forbidden_paths=(
                runner.PROJECT_ROOT / "docs/phase4/eval/v2-calibration/heldout",
            ),
        ),
        runner._isolated_temp_directory(
            "alphapilot-p4-2a-v2-1-test-single-run-"
        ) as workspace,
    ):
        run = runner._execute_temp_pipeline(
            label="run-b",
            project_root=runner.PROJECT_ROOT,
            workspace=workspace,
            implementation_commit=git_binding.implementation_commit,
        )
    assert len(run.artifacts) == 14
    assert run.probes == {}
