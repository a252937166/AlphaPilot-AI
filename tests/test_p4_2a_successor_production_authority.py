"""Production-authority checks using synthetic local Git history only.

No fixture is an owner/reviewer statement about this project. Test documents
live below pytest's temporary directory, never at a governed repository path.
No test starts materialization, inference, recovery, release consumption, or an
evaluation; only authority predicates are invoked.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
import pickle
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from scripts import p4_2a_successor_production_authority as authority
from scripts import prepare_p4_2a_v2_heldout as prepare

ROOT = Path(__file__).resolve().parents[1]
RELEASE_PATH = Path(
    "docs/phase4/reports/P4.2a-successor-production-integration-v1-production-release-20260907.json"
)
SCHEMA_PATH = Path(
    "config/schemas/p4_2a_successor_production_integration_v1_release_authorization.schema.json"
)
MODULE_PATH = Path("scripts/p4_2a_successor_production_authority.py")
PREPARE_PATH = Path("scripts/prepare_p4_2a_v2_heldout.py")
BASE_HEAD = "d7a1f3c001a6243150eb12e1c58efe473c238892"
IMPLEMENTATION_FILES = (
    PREPARE_PATH,
    MODULE_PATH,
    SCHEMA_PATH,
    Path("tests/test_p4_2a_successor_production_authority.py"),
    Path("tests/test_p4_2a_successor_preparation_integration.py"),
)
PREPARATION_STAGES = ("materialize", "infer", "select-blind", "seal-draft", "build-adjudication-ui")
FORBIDDEN_STAGES = (
    "finalize-owner-adjudication",
    "heldout-evaluation",
    "finalize",
    "evaluate",
    "p4.2a-done",
    "p4.2b",
    "p4.3",
    "trading",
    "infer ",
    "INFER",
    "",
)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical(document: object) -> bytes:
    return (
        json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def _git(root: Path, *arguments: str) -> str:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
    )
    return subprocess.run(
        [
            "/usr/bin/git",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "gc.auto=0",
            "-C",
            str(root),
            *arguments,
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _put(root: Path, relative: Path, payload: bytes) -> Path:
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return destination


def _commit(root: Path, relative: Path, payload: bytes) -> str:
    _put(root, relative, payload)
    _git(root, "add", "--", relative.as_posix())
    _git(
        root,
        "-c",
        "user.name=Synthetic Authority Fixture",
        "-c",
        "user.email=authority-fixture@example.invalid",
        "commit",
        "-q",
        "-m",
        f"Synthetic authority fixture: {relative.as_posix()}",
    )
    return _git(root, "rev-parse", "HEAD")


def test_new_authority_type_is_distinct_and_immutable() -> None:
    kind = authority.ProductionPreparationAuthorization
    assert kind is not prepare.V21ReleaseAuthorization
    assert not issubclass(kind, prepare._OfflineRehearsalCapability)
    assert not issubclass(kind, prepare.V21ReleaseAuthorization)
    assert dataclasses.is_dataclass(kind)
    assert kind.__dataclass_params__.frozen is True


def test_constructor_copy_and_pickle_cannot_mint_an_issued_authority(tmp_path: Path) -> None:
    facts = authority.ProductionPreparationAuthorization(
        project_root=tmp_path,
        receipt_path=tmp_path / RELEASE_PATH,
        receipt_sha256="1" * 64,
        receipt_creating_commit="2" * 40,
        preregistration_commit="3" * 40,
        implementation_commit="4" * 40,
        validated_stage="infer",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        facts.validated_stage = "heldout-evaluation"
    with pytest.raises(authority.ProductionAuthorityError, match="serialized"):
        pickle.dumps(facts)
    for forged in (
        facts,
        dataclasses.replace(facts),
        dataclasses.replace(facts, validated_stage=None),
    ):
        with pytest.raises(authority.ProductionAuthorityError, match="unissued"):
            authority.execution_authority_evidence(forged)
        with pytest.raises(authority.ProductionAuthorityError):
            authority.validate_registered_production_sources(tmp_path, forged, stage="infer")


@pytest.mark.parametrize(
    "payload",
    [
        b'{"key":1,"key":2}',
        b'{"n":NaN}',
        b'{"n":Infinity}',
        b"\xff",
        b'{"n":1e999}',
        b'{"nested":[{"n":-1e999}]}',
    ],
)
def test_authority_json_decoder_rejects_ambiguous_or_nonfinite_bytes(payload: bytes) -> None:
    with pytest.raises(authority.ProductionAuthorityError):
        authority._decode(payload)


def test_absent_receipt_is_not_a_candidate_and_cannot_authorize(tmp_path: Path) -> None:
    assert authority.has_registered_release_candidate(tmp_path) is False
    with pytest.raises(authority.ProductionAuthorityError):
        authority.validate_preparation_authorization(tmp_path, stage="infer")
    assert not (tmp_path / RELEASE_PATH).exists()


def test_actual_candidate_checkout_stops_at_the_missing_production_release_gate() -> None:
    assert not os.path.lexists(ROOT / RELEASE_PATH)
    with pytest.raises(
        authority.ProductionAuthorityError,
        match="BLOCKED_PENDING_SUCCESSOR_PRODUCTION_OWNER_RELEASE",
    ):
        authority.validate_preparation_authorization(ROOT, stage="infer")


@pytest.mark.parametrize("mode", ["broken-link", "directory", "garbage-file"])
def test_invalid_existing_candidate_cannot_be_treated_as_absent(
    tmp_path: Path,
    mode: str,
) -> None:
    path = tmp_path / RELEASE_PATH
    path.parent.mkdir(parents=True)
    if mode == "broken-link":
        path.symlink_to(tmp_path / "not-present.json")
    elif mode == "directory":
        path.mkdir()
    else:
        path.write_bytes(b"not a JSON document\n")
    assert authority.has_registered_release_candidate(tmp_path) is True
    with pytest.raises(authority.ProductionAuthorityError):
        authority.validate_preparation_authorization(tmp_path, stage="infer")


@pytest.mark.parametrize("stage", FORBIDDEN_STAGES)
def test_unregistered_stages_are_hard_rejected(tmp_path: Path, stage: str) -> None:
    with pytest.raises(authority.ProductionAuthorityError):
        authority.validate_preparation_authorization(tmp_path, stage=stage)


@pytest.mark.parametrize("forged", [object(), None, {}, {"stage": "infer"}])
def test_source_gate_and_evidence_reject_unissued_authority(
    tmp_path: Path,
    forged: object,
) -> None:
    with pytest.raises(authority.ProductionAuthorityError):
        authority.validate_registered_production_sources(tmp_path, forged, stage="infer")
    with pytest.raises(authority.ProductionAuthorityError):
        authority.execution_authority_evidence(forged)


def test_release_schema_is_closed_and_keeps_the_owner_day_as_a_pattern() -> None:
    schema = json.loads((ROOT / SCHEMA_PATH).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert schema["$id"] == (
        "https://alphapilot.local/schemas/"
        "p4_2a_successor_production_integration_v1_release_authorization.schema.json"
    )
    identity = schema["properties"]["authorization_id"]
    assert "pattern" in identity and "const" not in identity
    assert schema["properties"]["verdict"]["const"] == (
        "APPROVE_SUCCESSOR_PRODUCTION_INTEGRATION_V1_HELDOUT_PREPARATION_ONLY"
    )
    assert schema["properties"]["authorized_stages"]["const"] == list(PREPARATION_STAGES)


def _structural_release_document() -> dict[str, Any]:
    """An explicitly fictional schema example, not a valid authority fixture."""
    prefix = "docs/phase4/reports/P4.2a-successor-production-integration-v1-"
    prereg = json.loads((ROOT / (prefix + "preregistration-20260907.json")).read_text())
    sample_ref = {"path": "/synthetic-authority-test/evidence.json", "sha256": "1" * 64, "bytes": 1}
    sample_source = {
        "source_path": "/synthetic-authority-test/owner.txt",
        "source_sha256": "2" * 64,
        "source_bytes": 43,
        "verbatim_text": "SYNTHETIC TEST ONLY; NOT AN OWNER DECISION.",
        "relay_path": "/synthetic-authority-test/relay.json",
        "relay_sha256": "3" * 64,
        "observed_at_utc": "2026-09-07T01:00:00Z",
        "delivery_channel": "synthetic_test_only",
    }
    reviewer = {
        "identity": "SYNTHETIC TEST REVIEWER",
        "reviewer_type": "ai",
        "model": "synthetic-fixture",
        "method": "Schema example only",
        "independent_of_operator": True,
        "observed_at_utc": "2026-09-07T01:00:00Z",
        "evidence_refs": [sample_ref],
    }

    def authority_ref(path: str) -> dict[str, str]:
        return {"path": path, "sha256": "4" * 64, "creating_commit": "5" * 40}

    lineage: dict[str, Any] = {
        "preregistration": authority_ref(prefix + "preregistration-20260907.json"),
        "release_schema": {"path": SCHEMA_PATH.as_posix(), "sha256": "6" * 64, "bytes": 1},
        "owner_exception": authority_ref(prefix + "owner-exact-surface-exception-20260907.json"),
        "h0_evidence_acceptance": authority_ref(
            "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-2-release-authorization-20260811.json"
        ),
        "h0_completion": copy.deepcopy(prereg["H0_completion"]["completion_receipt"]),
        "recovered_bundle": {
            "path": "docs/phase4/rehearsals/P4.2a-v2-calibration-v2-2/bundle.json",
            "sha256": "7" * 64,
            "bytes": 1,
        },
        "paired_recovery_receipts": {"primary": sample_ref, "secondary": sample_ref},
    }
    for key, suffix in (
        ("q", "review-request"),
        ("r", "authorization"),
        ("b", "owner-confirmation-binding"),
    ):
        lineage[key] = authority_ref(
            "docs/phase4/reports/P4.2a-v2-2-series2-through-ordinal-000002-bundle-recovery-"
            + suffix
            + "-20260906.json"
        )
    limits = {
        "stage_start_limits": dict.fromkeys(PREPARATION_STAGES, 1),
        "maximum_model_calls": 4,
        "maximum_total_cost_cny": 1,
        "currency": "CNY",
        "automatic_retries": 0,
        "max_inference_attempts_per_item": 1,
    }
    return {
        "schema_version": "p4.2a-successor-production-integration-v1-production-release",
        "authorization_id": "P4.2A-SUCCESSOR-PRODUCTION-INTEGRATION-V1-PRODUCTION-RELEASE-20260907",
        "verdict": "APPROVE_SUCCESSOR_PRODUCTION_INTEGRATION_V1_HELDOUT_PREPARATION_ONLY",
        "created_at_utc": "2026-09-07T01:00:00Z",
        "created_at_shanghai": "2026-09-07T09:00:00+08:00",
        "reviewed_repository_head": "8" * 40,
        "owner_decision_source": sample_source,
        "owner_identity": "ouyang",
        "owner_decision_scope": {
            "scope": "HELDOUT_PREPARATION_ONLY",
            "authorized_stages": list(PREPARATION_STAGES),
            "still_gated": copy.deepcopy(prereg["still_gated"]),
        },
        "independent_implementation_review_ref": authority_ref(
            prefix + "independent-implementation-review-20260907.json"
        ),
        "reviewer": reviewer,
        "lineage": lineage,
        "production_implementation_binding": {
            "implementation_commit": "9" * 40,
            "build_manifest": sample_ref,
            "prepare_exception": {
                "path": PREPARE_PATH.as_posix(),
                "base_sha256": "ac2b3eb92ac9fadf823dfc081a1983af57132a77b36bb4f1419297c488eb9394",
                "target_sha256": "fdcb7fc9063b563ca02fcd630f36e450c1dd04d5b99aa9b2811c11c81eaba1d5",
                "patch_sha256": "aef8e0ce1ad8e4e31a091b7414dd9ef72fb6048b40113c0b1d11a8b568e43ca5",
            },
            "source_closure": [{"path": MODULE_PATH.as_posix(), "sha256": "a" * 64, "bytes": 1}],
            "active_validator": {
                "path": MODULE_PATH.as_posix(),
                "sha256": "a" * 64,
                "entrypoint": "validate_implementation_binding",
                "implementation_commit": "9" * 40,
            },
            "execution_environment": {
                "interpreter_realpath": "/synthetic-authority-test/python",
                "interpreter_sha256": "b" * 64,
                "python_version": "3.12.0",
                "packages": [{"name": "synthetic-package", "version": "1"}],
                "packages_sha256": "c" * 64,
            },
        },
        "historical_anchor_h0": copy.deepcopy(prereg["historical_anchor_h0"]),
        "registered_checks": [
            {
                "check_id": row["test_id"],
                "command_ref": sample_ref,
                "rc": 0,
                "stdout_sha256": "d" * 64,
                "stderr_sha256": "e" * 64,
                "status": "PASS",
                "evidence_refs": [sample_ref],
            }
            for row in prereg["registered_tests"]
        ],
        "authorized_stages": list(PREPARATION_STAGES),
        "still_gated": copy.deepcopy(prereg["still_gated"]),
        "runtime_start_policy": copy.deepcopy(prereg["runtime_start_policy"]),
        "locks": copy.deepcopy(prereg["locks"]),
        "execution_limits": limits,
        "external_cost_confirmation": {
            "owner_source": copy.deepcopy(sample_source),
            "budget_facts": {
                "estimated_model_calls": 4,
                "estimated_cost_cny": 1,
                "maximum_model_calls": 4,
                "maximum_total_cost_cny": 1,
                "currency": "CNY",
                "basis_refs": [sample_ref],
            },
        },
        "failure_policy": copy.deepcopy(prereg["failure_policy"]),
        "supporting_evidence_manifest": sample_ref,
    }


def _schema_validator() -> Draft202012Validator:
    return Draft202012Validator(
        json.loads((ROOT / SCHEMA_PATH).read_text(encoding="utf-8")),
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


def test_schema_example_is_not_execution_authority(tmp_path: Path) -> None:
    document = _structural_release_document()
    _schema_validator().validate(document)
    _put(tmp_path, RELEASE_PATH, _canonical(document))
    with pytest.raises(authority.ProductionAuthorityError):
        authority.validate_preparation_authorization(tmp_path, stage="materialize")


@pytest.mark.parametrize(
    "pointer,value",
    [
        ("verdict", "APPROVE_ALL_REAL_STAGES"),
        (
            "authorization_id",
            "P4.2A-SUCCESSOR-PRODUCTION-INTEGRATION-V1-PRODUCTION-RELEASE-2026-09-07",
        ),
        ("created_at_utc", "2026-09-07T09:00:00+08:00"),
        ("created_at_shanghai", "2026-09-07T01:00:00Z"),
        ("owner_identity", "operator"),
        ("authorized_stages", ["infer", "heldout-evaluation"]),
        ("still_gated", []),
        ("locks.heldout_evaluation_unlocked", True),
        ("locks.p4_2a_done", True),
        ("execution_limits.automatic_retries", 1),
        ("execution_limits.max_inference_attempts_per_item", 2),
        ("runtime_start_policy.live_probe_required", False),
        ("reviewer.independent_of_operator", False),
        ("production_implementation_binding.active_validator.entrypoint", "lambda_true"),
        ("production_implementation_binding.source_closure", []),
        ("lineage.preregistration.path", "../../some-other-repository/preregistration.json"),
        ("owner_decision_source.source_sha256", "not-a-hash"),
        ("failure_policy.repair_or_retry_in_same_consumed_stage", True),
        ("failure_policy.automatic_retries", 1),
    ],
)
def test_schema_rejects_expanded_scope_missing_guards_or_malformed_bindings(
    pointer: str,
    value: Any,
) -> None:
    document = _structural_release_document()
    target = document
    parts = pointer.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    assert list(_schema_validator().iter_errors(document))


def test_schema_rejects_extra_fields_and_duplicated_or_missing_registered_checks() -> None:
    document = _structural_release_document()
    document["operator_override"] = True
    assert list(_schema_validator().iter_errors(document))
    document = _structural_release_document()
    document["registered_checks"][1] = copy.deepcopy(document["registered_checks"][0])
    assert list(_schema_validator().iter_errors(document))
    document = _structural_release_document()
    document["registered_checks"].pop()
    assert list(_schema_validator().iter_errors(document))


def test_owner_issuance_day_is_not_backdated_by_a_schema_constant() -> None:
    document = _structural_release_document()
    document["authorization_id"] = (
        "P4.2A-SUCCESSOR-PRODUCTION-INTEGRATION-V1-PRODUCTION-RELEASE-20260908"
    )
    document["created_at_utc"] = "2026-09-08T01:00:00Z"
    document["created_at_shanghai"] = "2026-09-08T09:00:00+08:00"
    document["owner_decision_source"]["observed_at_utc"] = "2026-09-08T00:59:00Z"
    _schema_validator().validate(document)


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../outside.py",
        "scripts/../../outside.py",
        "/outside.py",
        "scripts//authority.py",
        "scripts/authority.py\x00trailer",
        "scripts\\authority.py",
    ],
)
def test_schema_does_not_allow_source_closure_to_escape_the_repository(unsafe_path: str) -> None:
    document = _structural_release_document()
    document["production_implementation_binding"]["source_closure"][0]["path"] = unsafe_path
    assert list(_schema_validator().iter_errors(document))


def test_schema_cannot_relabel_accepted_h0_as_current_implementation() -> None:
    document = _structural_release_document()
    document["historical_anchor_h0"]["selected_historical_anchor"]["require_current"] = True
    assert list(_schema_validator().iter_errors(document))
    document = _structural_release_document()
    document["historical_anchor_h0"]["selected_historical_anchor"]["implementation_commit"] = (
        "0" * 40
    )
    assert list(_schema_validator().iter_errors(document))


@pytest.fixture(scope="module")
def implementation_fixture(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    """Real local implementation commit; no predicate is replaced or skipped."""
    temporary = tmp_path_factory.mktemp("successor-authority-implementation").resolve()
    repository = temporary / "repository"
    _git(temporary, "clone", "--local", "--no-hardlinks", "--quiet", str(ROOT), str(repository))
    _git(repository, "checkout", "-q", "-b", "synthetic-authority-fixture", BASE_HEAD)
    for relative in IMPLEMENTATION_FILES:
        _put(repository, relative, (ROOT / relative).read_bytes())
    _git(repository, "add", "--", *(path.as_posix() for path in IMPLEMENTATION_FILES))
    _git(
        repository,
        "-c",
        "user.name=Synthetic Authority Fixture",
        "-c",
        "user.email=authority-fixture@example.invalid",
        "commit",
        "-q",
        "-m",
        "Synthetic fixture implementation; no real release or stages",
    )
    implementation_commit = _git(repository, "rev-parse", "HEAD")
    closure = authority.registered_source_closure(repository, implementation_commit)
    changes = [
        {"status": line.split("\t")[0], "path": line.split("\t")[1]}
        for line in _git(
            repository, "diff", "--name-status", BASE_HEAD, implementation_commit
        ).splitlines()
    ]
    changes.sort(key=lambda row: row["path"])
    manifest = {
        "schema_version": "p4.2a-successor-production-integration-v1-build-manifest",
        "implementation_commit": implementation_commit,
        "source_closure": closure,
        "changed_paths": changes,
    }
    manifest_path = temporary / "SYNTHETIC_BUILD_MANIFEST.json"
    manifest_bytes = _canonical(manifest)
    manifest_path.write_bytes(manifest_bytes)
    binding = copy.deepcopy(_structural_release_document()["production_implementation_binding"])
    binding.update(
        {
            "implementation_commit": implementation_commit,
            "source_closure": closure,
            "build_manifest": {
                "path": str(manifest_path),
                "sha256": _sha(manifest_bytes),
                "bytes": len(manifest_bytes),
            },
            "active_validator": {
                "path": MODULE_PATH.as_posix(),
                "sha256": _sha((repository / MODULE_PATH).read_bytes()),
                "entrypoint": "validate_implementation_binding",
                "implementation_commit": implementation_commit,
            },
            "execution_environment": authority.execution_environment(),
        }
    )
    return repository, binding


def test_active_validator_checks_real_implementation_commit_and_complete_closure(
    implementation_fixture: tuple[Path, dict[str, Any]],
) -> None:
    repository, binding = implementation_fixture
    result = authority.validate_implementation_binding(
        repository, binding, reviewed_commit=binding["implementation_commit"]
    )
    assert isinstance(result, dict)
    assert result["implementation_commit"] == binding["implementation_commit"]
    assert _sha((repository / PREPARE_PATH).read_bytes()) == (
        "fdcb7fc9063b563ca02fcd630f36e450c1dd04d5b99aa9b2811c11c81eaba1d5"
    )
    closure_paths = {row["path"] for row in binding["source_closure"]}
    assert {path.as_posix() for path in IMPLEMENTATION_FILES} <= closure_paths
    tracked = set(_git(repository, "ls-files").splitlines())
    independently_required = {
        path for path in tracked if path.endswith(".py") or path.startswith("config/")
    }
    assert closure_paths == independently_required


@pytest.mark.parametrize(
    "mutation",
    [
        "omit-authority",
        "omit-scientific-source",
        "duplicate-path",
        "wrong-source-sha",
        "wrong-source-size",
        "wrong-validator-sha",
        "wrong-validator-entrypoint",
        "wrong-validator-commit",
        "wrong-reviewed-commit",
        "wrong-interpreter-sha",
        "wrong-package-version",
        "wrong-packages-sha",
        "wrong-prepare-target",
        "wrong-manifest-sha",
    ],
)
def test_active_validator_refuses_unreviewed_or_drifted_implementation_facts(
    implementation_fixture: tuple[Path, dict[str, Any]],
    mutation: str,
) -> None:
    repository, original = implementation_fixture
    binding = copy.deepcopy(original)
    reviewed_commit = binding["implementation_commit"]
    if mutation == "omit-authority":
        binding["source_closure"] = [
            r for r in binding["source_closure"] if r["path"] != MODULE_PATH.as_posix()
        ]
    elif mutation == "omit-scientific-source":
        binding["source_closure"] = [
            r
            for r in binding["source_closure"]
            if r["path"] != "scripts/build_p4_2a_gold_sample.py"
        ]
    elif mutation == "duplicate-path":
        binding["source_closure"].append(copy.deepcopy(binding["source_closure"][0]))
    elif mutation == "wrong-source-sha":
        binding["source_closure"][0]["sha256"] = "0" * 64
    elif mutation == "wrong-source-size":
        binding["source_closure"][0]["bytes"] += 1
    elif mutation == "wrong-validator-sha":
        binding["active_validator"]["sha256"] = "0" * 64
    elif mutation == "wrong-validator-entrypoint":
        binding["active_validator"]["entrypoint"] = "always_pass"
    elif mutation == "wrong-validator-commit":
        binding["active_validator"]["implementation_commit"] = BASE_HEAD
    elif mutation == "wrong-reviewed-commit":
        reviewed_commit = BASE_HEAD
    elif mutation == "wrong-interpreter-sha":
        binding["execution_environment"]["interpreter_sha256"] = "0" * 64
    elif mutation == "wrong-package-version":
        binding["execution_environment"]["packages"][0]["version"] += ".unreviewed"
    elif mutation == "wrong-packages-sha":
        binding["execution_environment"]["packages_sha256"] = "0" * 64
    elif mutation == "wrong-prepare-target":
        binding["prepare_exception"]["target_sha256"] = binding["prepare_exception"]["base_sha256"]
    elif mutation == "wrong-manifest-sha":
        binding["build_manifest"]["sha256"] = "0" * 64
    else:
        raise AssertionError(mutation)
    with pytest.raises(authority.ProductionAuthorityError):
        authority.validate_implementation_binding(
            repository, binding, reviewed_commit=reviewed_commit
        )


def test_active_validator_rejects_current_worktree_bytes_that_differ_from_commit(
    implementation_fixture: tuple[Path, dict[str, Any]],
    tmp_path: Path,
) -> None:
    source, binding = implementation_fixture
    repository = tmp_path / "source-drift"
    _git(tmp_path, "clone", "--local", "--no-hardlinks", "--quiet", str(source), str(repository))
    path = repository / "scripts/build_p4_2a_gold_sample.py"
    path.write_bytes(path.read_bytes() + b"\n# Synthetic unreviewed source drift.\n")
    with pytest.raises(authority.ProductionAuthorityError):
        authority.validate_implementation_binding(
            repository, binding, reviewed_commit=binding["implementation_commit"]
        )


def test_standalone_active_validator_rejects_an_injected_inventory_callable(
    implementation_fixture: tuple[Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, binding = implementation_fixture

    # Adversarial callable replacement is the input under test, never a PASS stub.
    def injected(*_args: object, **_kwargs: object) -> list[dict[str, Any]]:
        raise AssertionError("an injected inventory function must never execute")

    monkeypatch.setattr(authority, "registered_source_closure", injected)
    with pytest.raises(authority.ProductionAuthorityError, match="injected"):
        authority.validate_implementation_binding(
            repository, binding, reviewed_commit=binding["implementation_commit"]
        )


_ORIGIN_PROBE = r"""
import hashlib, json, sys, types
from pathlib import Path
from scripts import p4_2a_successor_production_authority as authority

request = json.load(sys.stdin)
root = Path(request["root"])
if request.get("injection") == "engine-function":
    from alphapilot.db import engine
closure = []
for relative in request["source_paths"]:
    payload = (root / relative).read_bytes()
    closure.append({"path": relative, "sha256": hashlib.sha256(payload).hexdigest(),
                    "bytes": len(payload)})
authority._loaded_origins(root, closure)
if request.get("injection") == "authority-helper":
    authority._read_json = lambda *_args: {}
elif request.get("injection") == "engine-function":
    engine._build_engine = lambda *_args: None
else:
    name = request["namespace"] + ".synthetic_injected_module"
    injected = types.ModuleType(name)
    if request["origin"] is not None:
        injected.__file__ = request["origin"]
    sys.modules[name] = injected
try:
    authority._loaded_origins(root, closure)
except authority.ProductionAuthorityError as error:
    print(json.dumps({"baseline_pass": True, "rejected": True, "message": str(error)}))
else:
    raise AssertionError("An injected repository module/callable was accepted")
"""


def _origin_probe(**values: object) -> dict[str, Any]:
    """Clean interpreter avoids pytest's intentional function/assert rewrites."""
    tracked = set(_git(ROOT, "ls-files").splitlines())
    source_paths = sorted(
        {name for name in tracked if name.endswith(".py") or name.startswith("config/")}
        | {path.as_posix() for path in IMPLEMENTATION_FILES}
    )
    request = {"root": str(ROOT), "source_paths": source_paths, **values}
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT)))
    result = subprocess.run(
        [sys.executable, "-B", "-c", _ORIGIN_PROBE],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=environment,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    return json.loads(result.stdout)


@pytest.mark.parametrize("namespace", ["scripts", "alphapilot"])
@pytest.mark.parametrize("origin", [None, "<injected>"])
def test_runtime_origin_gate_refuses_originless_repository_modules(
    namespace: str,
    origin: str | None,
) -> None:
    result = _origin_probe(namespace=namespace, origin=origin)
    assert result["baseline_pass"] is True and result["rejected"] is True
    assert "originless" in result["message"]


@pytest.mark.parametrize("injection", ["authority-helper", "engine-function"])
def test_runtime_origin_gate_checks_actual_loaded_callable_code(injection: str) -> None:
    result = _origin_probe(injection=injection)
    assert result["baseline_pass"] is True and result["rejected"] is True
    assert "injected" in result["message"] or "callable" in result["message"]


def test_design_registration_does_not_conflict_with_future_preparation_scope() -> None:
    receipt = _structural_release_document()
    policy = json.loads((ROOT / (authority.PREFIX + "preregistration-20260907.json")).read_text())
    assert policy["authorized_stages"] == []
    assert receipt["authorized_stages"] == list(PREPARATION_STAGES)
    assert authority._validate_policy_scope(receipt, policy) is None
    receipt["authorized_stages"].append("heldout-evaluation")
    with pytest.raises(authority.ProductionAuthorityError):
        authority._validate_policy_scope(receipt, policy)
    receipt = _structural_release_document()
    receipt["locks"]["p4_2a_done"] = True
    with pytest.raises(authority.ProductionAuthorityError):
        authority._validate_policy_scope(receipt, policy)


@pytest.mark.parametrize("mutation", ["anchor", "receipt-digest", "receipt-commit"])
def test_historical_adapter_refuses_substitutions_before_accessing_history(
    tmp_path: Path,
    mutation: str,
) -> None:
    policy = json.loads((ROOT / (authority.PREFIX + "preregistration-20260907.json")).read_text())
    receipt = {
        "historical_anchor_h0": copy.deepcopy(policy["historical_anchor_h0"]),
        "lineage": {
            "h0_evidence_acceptance": copy.deepcopy(
                policy["historical_anchor_h0"]["accepted_h0_receipt"]
            )
        },
    }
    if mutation == "anchor":
        receipt["historical_anchor_h0"]["selected_historical_anchor"]["implementation_commit"] = (
            "0" * 40
        )
    elif mutation == "receipt-digest":
        receipt["lineage"]["h0_evidence_acceptance"]["sha256"] = "0" * 64
    else:
        receipt["lineage"]["h0_evidence_acceptance"]["creating_commit"] = "0" * 40
    with pytest.raises(authority.ProductionAuthorityError, match=r"H0 anchor|H0 receipt reference"):
        authority._validate_historical_h0(tmp_path, policy, receipt)


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef", "$recursiveRef"])
@pytest.mark.parametrize(
    "destination",
    [
        "https://must-never-contact.invalid/schema.json",
        "file:///must-not-read.json",
        "other-schema.json",
        1,
    ],
)
def test_schema_reference_guard_blocks_every_external_resolution_form(
    keyword: str,
    destination: object,
) -> None:
    # Invoke the actual pre-validation guard; never invoke a URL resolver.
    schema = {"properties": {"nested": {"allOf": [{keyword: destination}]}}}
    with pytest.raises(authority.ProductionAuthorityError, match="non-local schema"):
        authority._validate_local_schema_refs(schema)


def test_schema_reference_guard_accepts_the_real_local_schema() -> None:
    schema = json.loads((ROOT / SCHEMA_PATH).read_text(encoding="utf-8"))
    assert authority._validate_local_schema_refs(schema) is None
    assert authority._validate_local_schema_refs({"$dynamicRef": "#local-anchor"}) is None


def _registered_budget_document() -> dict[str, Any]:
    relative = Path("docs/phase4/reports/P4.2a-v2-heldout-preregistration-20260810.json")
    plan_bytes = (ROOT / relative).read_bytes()
    plan = json.loads(plan_bytes)
    cost_ref = copy.deepcopy(plan["authorities"]["cost_correction_and_p4_2b_backlog"])
    cost_ref["bytes"] = len((ROOT / cost_ref["path"]).read_bytes())
    return {
        "external_cost_confirmation": {
            "budget_facts": {
                "estimated_model_calls": 4048,
                "estimated_cost_cny": 57,
                "maximum_model_calls": 4048,
                "maximum_total_cost_cny": 57,
                "currency": "CNY",
                "basis_refs": [
                    {
                        "path": relative.as_posix(),
                        "sha256": _sha(plan_bytes),
                        "bytes": len(plan_bytes),
                    },
                    cost_ref,
                ],
            }
        },
        "execution_limits": {"maximum_model_calls": 4048, "maximum_total_cost_cny": 57},
    }


def test_registered_budget_gate_reads_frozen_metadata_without_running_a_stage() -> None:
    result = authority._validate_registered_budget(ROOT, _registered_budget_document())
    assert result["model_calls_upper_bound"] == 4048
    assert 56 < result["historical_cost_estimate_cny"] < 57
    assert result["live_provider_charge_guaranteed"] is False
    assert result["billing_meter_performed"] is False


@pytest.mark.parametrize("mutation", ["one-call", "one-yuan", "missing-frozen-cost-basis"])
def test_underfunded_or_unbound_budget_cannot_pass_registered_plan_checks(mutation: str) -> None:
    document = _registered_budget_document()
    if mutation == "one-call":
        document["execution_limits"]["maximum_model_calls"] = 1
    elif mutation == "one-yuan":
        document["external_cost_confirmation"]["budget_facts"]["estimated_cost_cny"] = 1
        document["execution_limits"]["maximum_total_cost_cny"] = 1
    else:
        document["external_cost_confirmation"]["budget_facts"]["basis_refs"].pop()
    with pytest.raises(authority.ProductionAuthorityError, match=r"confirmed|budget lacks"):
        authority._validate_registered_budget(ROOT, document)
