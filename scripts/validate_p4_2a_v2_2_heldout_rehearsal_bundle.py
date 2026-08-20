#!/usr/bin/env python3
"""Independently validate P4.2a successor-v2.2 rehearsal evidence.

This module is deliberately not a producer shim.  It imports the one package
implementation object, consumes that object's private authority delegation,
and independently replays schemas, live ledger bytes, archive bytes, Merkle
roots, attempt-history chaining, implementation epochs, and release-receipt
cross bindings.  It never imports ``rehearse_p4_2a_v2_2_heldout_full_path``.
"""

# ruff: noqa: E402

from __future__ import annotations

import os as _validator_os
import sys as _validator_sys

_VALIDATOR_ENVIRONMENT_MARKER = "ALPHAPILOT_P42A_REHEARSAL_V2_2_ENV_LOCKED"
_VALIDATOR_PROJECT_ROOT_TEXT = _validator_os.path.dirname(
    _validator_os.path.dirname(_validator_os.path.realpath(__file__))
)
_VALIDATOR_REGISTERED_ROOT_TEXT = (
    "/Users/ouyangduning/Documents/project/interesting/AlphaPilot-AI"
)
_VALIDATOR_FIXED_PYTHON = (
    "/Users/ouyangduning/Documents/project/interesting/AlphaPilot-AI/.venv/bin/python"
)
_VALIDATOR_FIXED_ORIG_PYTHON = (
    "/Library/Frameworks/Python.framework/Versions/3.12/Resources/"
    "Python.app/Contents/MacOS/Python"
)
_VALIDATOR_REGISTERED_PATH_TEXT = (
    _VALIDATOR_REGISTERED_ROOT_TEXT
    + "/scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py"
)
_VALIDATOR_LOCKED_ENVIRONMENT = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "OPENBLAS_MAIN_FREE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
    "PYTHONNOUSERSITE": "1",
    "PYTHONPYCACHEPREFIX": "/dev/null",
    "PYTHONSAFEPATH": "1",
    "TZ": "UTC",
    "__CF_USER_TEXT_ENCODING": "0x1F5:0x0:0x0",
    "PATH": "/usr/bin:/bin",
    _VALIDATOR_ENVIRONMENT_MARKER: "1",
}


def _validator_locked_runtime() -> bool:
    return (
        dict(_validator_os.environ) == _VALIDATOR_LOCKED_ENVIRONMENT
        and _validator_sys.flags.hash_randomization == 0
        and _validator_sys.flags.no_site == 1
        and _validator_sys.flags.no_user_site == 1
        and bool(_validator_sys.flags.safe_path)
        and bool(_validator_sys.dont_write_bytecode)
        and _validator_sys.pycache_prefix == "/dev/null"
    )


def _validator_direct_entry() -> bool:
    main_module = _validator_sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", None)
    return (
        isinstance(main_file, str)
        and _validator_os.path.realpath(main_file) == _VALIDATOR_REGISTERED_PATH_TEXT
        and _validator_os.path.realpath(__file__) == _VALIDATOR_REGISTERED_PATH_TEXT
        and _validator_os.path.abspath(_validator_sys.executable)
        == _VALIDATOR_FIXED_PYTHON
        and tuple(_validator_sys.orig_argv)
        == (
            _VALIDATOR_FIXED_ORIG_PYTHON,
            "-S",
            "-P",
            "-B",
            _VALIDATOR_REGISTERED_PATH_TEXT,
        )
        and tuple(_validator_sys.argv) == (_VALIDATOR_REGISTERED_PATH_TEXT,)
    )


if __name__ == "__main__" and not (
    _validator_locked_runtime() and _validator_direct_entry()
):
    raise RuntimeError(
        "registered v2.2 validation must start in the exact locked -S -P -B "
        "interpreter environment"
    )

_VALIDATOR_REGISTERED_BOOTSTRAP = __name__ == "__main__"

if _VALIDATOR_REGISTERED_BOOTSTRAP:
    _validator_stdlib = _validator_os.path.join(
        _validator_sys.base_prefix,
        "lib",
        f"python{_validator_sys.version_info.major}.{_validator_sys.version_info.minor}",
    )
    _validator_candidates = (
        _validator_stdlib,
        _validator_os.path.join(_validator_stdlib, "lib-dynload"),
        _validator_os.path.join(
            _VALIDATOR_REGISTERED_ROOT_TEXT,
            ".venv/lib/python3.12/site-packages",
        ),
        _VALIDATOR_PROJECT_ROOT_TEXT,
        _validator_os.path.join(_VALIDATOR_PROJECT_ROOT_TEXT, "src"),
    )
    _validator_runtime_paths: list[str] = []
    for _validator_candidate in _validator_candidates:
        _validator_absolute = _validator_os.path.abspath(_validator_candidate)
        if _validator_absolute not in _validator_runtime_paths:
            _validator_runtime_paths.append(_validator_absolute)
    _validator_sys.path[:] = _validator_runtime_paths

# The sole authority-owning implementation must install the process audit hook
# before this validator imports any other standard-library, third-party, or
# repository module.  Its closure-private import guard remains active until the
# exact standalone validator calls the one-shot finalizer below.
# isort: off
import scripts.p4_2a_v2_2_heldout_rehearsal as implementation
# isort: on

import argparse
import ast
import copy
import hashlib
import importlib.metadata
import json
import platform
import re
import stat
import subprocess
import sys
import sysconfig
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn, cast

from jsonschema import Draft202012Validator, FormatChecker

if _VALIDATOR_REGISTERED_BOOTSTRAP:
    implementation._finish_validator_import_guard(_validator_sys.modules[__name__])

_implementation_module = implementation
_AUDIT_POLICY = implementation._AUDIT_POLICY
_TEMP_AUTHORITY = implementation._TEMP_AUTHORITY

JsonObject = dict[str, Any]
PROJECT_ROOT = Path(_VALIDATOR_PROJECT_ROOT_TEXT).resolve()
REGISTERED_PROJECT_ROOT = Path(_VALIDATOR_REGISTERED_ROOT_TEXT)
PREREGISTRATION_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-heldout-rehearsal-v2-2-preregistration-20260811.json"
)
PREREGISTRATION_SHA256 = (
    "8f52a9e24df11e23a900b5cb79720f3b4aae999c6ab770a9038ebe2617e8d8d5"
)
BUNDLE_SCHEMA_RELATIVE = Path(
    "config/schemas/p4_2a_v2_2_heldout_rehearsal_bundle.schema.json"
)
BUNDLE_SCHEMA_SHA256 = (
    "19903ac94d4d7ced81c7f18e7b8880bd1dbb68fd3ededf3f0b91f89d034aa5db"
)
RELEASE_SCHEMA_RELATIVE = Path(
    "config/schemas/p4_2a_v2_2_heldout_release_authorization.schema.json"
)
RELEASE_SCHEMA_SHA256 = (
    "098d213f510718aab0d9c6bfc950a30bb1c4841ca151631bea78c1bf0238e7ea"
)
INDEPENDENT_REVIEW_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-preregistration-independent-review-20260811.json"
)
INDEPENDENT_REVIEW_SHA256 = (
    "6707e2b3c0b2ba87712e88b59ceaed17524be2de947b764a94c8b170b2a30bb6"
)
INDEPENDENT_REVIEW_COMMIT = "b21e1bdbf865dfd9c7605ecc7794fc3f8701ed1f"
INITIAL_REVIEWED_COMMIT = "be6423506f598c290db7ad944b002763fdf806ab"
INITIAL_IMPLEMENTATION_PARENT = INITIAL_REVIEWED_COMMIT
VOID_EPOCH_ONE_IMPLEMENTATION_COMMIT = "fae44154d3504017742934ff3b3961642c35eb65"
VOID_EPOCH_ONE_IMPLEMENTATION_PARENT = "cf10ef8d636049b0fc206c8698a809be3090e1d7"
VOID_EPOCH_ONE_LANDING_COMMIT = "be7a2cedff1ad4bf523d88d83fa333126d502720"
VOID_EPOCH_ONE_REVIEW_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-remediation-independent-review-20260812.json"
)
VOID_EPOCH_ONE_REVIEW_SHA256 = (
    "e348bbc6c2976d473bf2b8e5b280784fd45ff7ae1ba7d7a4119309eb178b16cf"
)
VOID_EPOCH_ONE_REVIEW_COMMIT = "16f3e700c2ca9da997c8c0180e8b780aeae93346"
VOID_EPOCH_ONE_ADJUDICATION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-attempt1-adjudication-and-epoch3-companion-20260813.json"
)
VOID_EPOCH_ONE_ADJUDICATION_SHA256 = (
    "aef641f0624ffa5ec8f722356b10c9e3fe0424edd93969f3251eecffac176521"
)
VOID_EPOCH_ONE_ADJUDICATION_COMMIT = "7fc122f575801ff43d2446a2c59491a086735e93"
VOID_EPOCH_ONE_RULING = (
    "Epoch 1 is recorded as ASSIGNED_AND_STRUCTURALLY_UNCONSUMABLE: its bytes "
    "were approved and carried forward into epoch 2 unchanged; it executed "
    "nothing and appears in no ledger record.",
    "The immutable history legitimately begins at epoch 2. The history is never "
    "rewritten or renumbered.",
    "Bundle construction and validation must accept a declared epoch origin and "
    "must disclose the void epoch 1 with its commit and reason in the final bundle, "
    "satisfying the preregistration's requirement that the bundle lists every epoch "
    "and the release acknowledges each.",
    "The numbering contract's intent, no gaps and no reuse among EXECUTED epochs, "
    "is preserved; only the assumption that execution starts at 1 is corrected.",
)
VOID_EPOCH_THREE_IMPLEMENTATION_COMMIT = (
    "d4fb0d8c763b9fa104949ea2ac58bc921d9e8889"
)
VOID_EPOCH_THREE_IMPLEMENTATION_PARENT = (
    "d6c9c353217e00730457bf6b944ff26a32b8cf85"
)
VOID_EPOCH_THREE_CONTROL_ROOT_SHA256 = (
    "5ba2a3dc7f1512efd52e2654b4ac4a491c13218cfe6f92fda8db885be1d9ebbf"
)
VOID_EPOCH_THREE_CONTROL_RECORD_COUNT = 75
VOID_EPOCH_THREE_AUTHORITY_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-epoch3-surface-authority-20260813.json"
)
VOID_EPOCH_THREE_AUTHORITY_SHA256 = (
    "44c2ab4e310da3f4b4a11efef8b9c20f73d231dc2b8f44dc535616cf18c646b3"
)
VOID_EPOCH_THREE_REVIEW_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-2-epoch3-r2-implementation-independent-review-20260814.json"
)
VOID_EPOCH_THREE_REVIEW_SHA256 = (
    "590d6b6b24bb6672956ea320a21458aa10523514ec384586029f09ef2cf757ef"
)
VOID_EPOCH_THREE_REVIEW_COMMIT = "bf9f610bda54523d69ecda0a72bf8fe89eaef78c"
VOID_EPOCH_THREE_LANDING_COMMIT = "006927080312b0e563e4ec3058b706455b33b70d"
VOID_EPOCH_THREE_LANDING_PARENTS = (
    "5791041e7eb48ffc6977752e381b10333bf53358",
    VOID_EPOCH_THREE_REVIEW_COMMIT,
)
VOID_EPOCH_THREE_GATE_ADJUDICATION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-epoch3-gate-failure-adjudication-20260814.json"
)
VOID_EPOCH_THREE_GATE_ADJUDICATION_SHA256 = (
    "29e476f1b2817bf8c6bb711230ba8ecb74f27d08648526f12073de3c8a1d067f"
)
VOID_EPOCH_THREE_GATE_ADJUDICATION_COMMIT = (
    "578a62551729e3bdc37ef6f2d2a9851fdf785dbd"
)
VOID_EPOCH_THREE_REANCHOR_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-epoch4-reanchor-companion-20260818.json"
)
VOID_EPOCH_THREE_REANCHOR_SHA256 = (
    "14a4eb277b3e3fb8ac181d432bdf4ee7821c339a25990991408b1759711fc546"
)
VOID_EPOCH_THREE_REANCHOR_COMMIT = "8b25d36033791efb4e800182150f4e7cae9ff597"
VOID_EPOCH_THREE_REASON_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-attempt2-adjudication-and-epoch5-direction-20260819.json"
)
VOID_EPOCH_THREE_REASON_SHA256 = (
    "e7e7615a404c216cfaf6ccba9a523cb46de48d82cd8a178c19612a1a38795180"
)
VOID_EPOCH_THREE_REASON_COMMIT = "0548692480ff8325b69be92f01e0d42e11ad4eb0"
VOID_EPOCH_THREE_GATE_SUPERSESSION_RULING = (
    "a corrected epoch-3 implementation candidate is authorized as a NEW direct "
    "single-parent child of the SAME six-field authority d6c9c353 with the SAME "
    "exact four-M surface, on a NEW branch. No new owner authorization is required: "
    "the owner approved the surface and content scope, not a byte-pinned diff, and "
    "both fixes fall inside that scope, the stale-absence repair being part of "
    "accepting the real bent history and the test relocation being part of the F-11 "
    "coverage. The epoch is unconsumed: no ledger record references any epoch-3 commit."
)
VOID_EPOCH_THREE_REANCHOR_TRIGGER = (
    "the owner's sector-flow and baostock workstream landed as 851a4bf, which modifies "
    "src/alphapilot/core/config.py"
)
VOID_EPOCH_THREE_REASON_FINDING = (
    "bundle construction after sealing: _implementation_epochs accepts used-epoch "
    "sequences [1..n] or [2..n+1] only; the real history is [2,4] because epoch 3 "
    "landed on main, passed its gate and review, and was then superseded by epoch 4 "
    "before any attempt bound it. The gap is lawful history; the packer cannot express it."
)
_V2_2_REMEDIATION_AUTHORITY = (
    "docs/phase4/reports/"
    "P4.2a-v2-heldout-rehearsal-v2-1-failure-remediation-review-request-20260811.json",
    "820ed6c62a2e04a051d530bee7c33f5cfff21fd3fee25afd7587e18a407ce29f",
    "530f2dc9f89360ad7c12776d85c3bf369f209214",
)
_V2_2_SCOPE_AUTHORITY = (
    "docs/phase4/reports/P4.2a-v2-2-preregistration-scope-authorization-20260811.json",
    "7cef82e5e4b2fcce349cbc25672705ea75795b0b07865970c415945747aa3296",
    "5fe756401f20e67ff5c868bf29f099c1bfe5b4d3",
)
IMPLEMENTATION_PATHS = (
    "scripts/rehearse_p4_2a_v2_2_heldout_full_path.py",
    "scripts/p4_2a_v2_2_heldout_rehearsal.py",
    "scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py",
    "tests/test_p4_2a_v2_2_heldout_rehearsal_runner.py",
    "tests/test_p4_2a_v2_2_heldout_rehearsal_validator.py",
)
REHEARSAL_ID = "P4.2A-V2-HELDOUT-REHEARSAL-V2-2-DETERMINISTIC-20260811"
SERIES_POLICY = "DISCLOSED_REPEATABLE_SERIES_V1"
REGISTERED_DESTINATION_RELATIVE = Path(
    "docs/phase4/rehearsals/P4.2a-v2-calibration-v2-2"
)
REGISTERED_SERIES_TOKEN = (
    "35ba1b83a9b187817d7a591758e1c131e867fcd37917cba0ab196799fff832ef"
)
INCIDENT_SHA256 = (
    "d658336f61cdca0239584b696043fe4abc5ede1ef7aff76a4fe514b7b5d0735c"
)
BUNDLE_FILENAME = "bundle.json"
RELEASE_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-heldout-rehearsal-v2-2-release-authorization-20260811.json"
)
VALIDATOR_RESULT_SCHEMA = "p4.2a-v2-heldout-validator-result-v2.2"
CONTROL_MANIFEST_SCHEMA = "p4.2a-v2-heldout-rehearsal-control-manifest-v2.2"

_CARRY_FORWARD_AUTHORITIES = {
    "v2_1_preregistration": (
        "docs/phase4/reports/"
        "P4.2a-v2-heldout-rehearsal-v2-1-preregistration-20260810.json",
        "c303cfb13a42ecbb7e0acaec04de12a9e9169b89cf9e93ea79d0f120d1439d3e",
        "b302d5889f01296568340bcc15041cc554ceb2c7",
    ),
    "v2_1_prediction_timing_preregistration": (
        "docs/phase4/reports/"
        "P4.2a-v2-heldout-rehearsal-v2-1-prediction-timing-seam-"
        "preregistration-20260810.json",
        "1052c7a33268572fc794517844dae4b6c1ea504121712ad2f55ec814a7446f9a",
        "b3c2d2216c1feffd9949f181fa6766f8357ff683",
    ),
    "v2_1_frame_authority_ruling": (
        "docs/phase4/reports/"
        "P4.2a-rehearsal-v2-approval-and-frame-authority-ruling-20260810.json",
        "8605dd30dd6bffa9621b6efe5e01c4c9cead615c9639259c2e79e14fcbbc3421",
        "da374342781d6fde2f2c6d87d23582050bc8edaa",
    ),
    "v2_1_code_gate_authorization": (
        "docs/phase4/reports/P4.2a-successor-v2-1-code-gate-authorization-"
        "20260810.json",
        "e28db692dc150983f86f6760fb1a95584d8607658e8a78a0de35cf3fc81940cd",
        "aa082578aa48296f1dd394a380775a5a4546ca65",
    ),
    "v2_1_scope_correction_owner_ruling": (
        "docs/phase4/reports/"
        "P4.2a-v2-heldout-rehearsal-v2-1-scope-correction-owner-ruling-"
        "20260810.json",
        "36a3baea9ce5e4c28c7e6aff9e77c09691024a870513f49f2094b07963f3582e",
        "88690ef488925f9de922569f961ec4ff1a23bb78",
    ),
    "v2_1_registry_expansion_authorization": (
        "docs/phase4/reports/"
        "P4.2a-v2-1-control-plane-registry-expansion-authorization-20260811.json",
        "ab85a0ddd90728c7d41051e640b59f7dc777f2f2aec3c8290286206979251796",
        "d37040be87644977ddaad60b2590ac2e62b2aeed",
    ),
    "v2_1_independent_implementation_review": (
        "docs/phase4/reports/"
        "P4.2a-v2-1-implementation-independent-review-20260811.json",
        "d144f77d4e7a2946f00e618fb768960b0abdd6e40caf5831f4f198700762d276",
        "ed59a0ce6057145068b7c87fc681dd0aeea47270",
    ),
    "v2_1_consumed_attempt_incident": (
        "docs/phase4/reports/"
        "P4.2a-v2-heldout-rehearsal-v2-1-one-shot-consumed-incident-20260811.json",
        "d658336f61cdca0239584b696043fe4abc5ede1ef7aff76a4fe514b7b5d0735c",
        "7a6e8be39f9a0702bf8fb4a22c669dc7331b0d95",
    ),
}
_V2_1_IMPLEMENTATION_PARENT = "d37040be87644977ddaad60b2590ac2e62b2aeed"
_V2_1_IMPLEMENTATION_COMMIT = "4fce89e89fe2dba656694a7cffdc0ee1af0305c0"
_V2_1_IMPLEMENTATION_SURFACE = (
    ("M", "scripts/build_p4_2a_v2_heldout_adjudication_ui.py"),
    ("M", "scripts/evaluate_p4_2a_v2_heldout.py"),
    ("M", "scripts/finalize_p4_2a_v2_heldout_adjudication.py"),
    ("M", "scripts/prepare_p4_2a_v2_heldout.py"),
    ("A", "scripts/rehearse_p4_2a_v2_1_heldout_full_path.py"),
    ("M", "scripts/run_p4_2a_offline_extract.py"),
    ("M", "scripts/seal_p4_2a_v2_heldout_draft.py"),
    ("A", "scripts/validate_p4_2a_v2_1_heldout_rehearsal_bundle.py"),
    ("M", "tests/test_p4_2a_offline_extract.py"),
    ("A", "tests/test_p4_2a_v2_1_heldout_rehearsal_runner.py"),
    ("A", "tests/test_p4_2a_v2_1_heldout_rehearsal_validator.py"),
    ("M", "tests/test_p4_2a_v2_heldout.py"),
    ("M", "tests/test_p4_2a_v2_heldout_adjudication.py"),
    ("M", "tests/test_p4_2a_v2_heldout_evaluator.py"),
    ("M", "tests/test_p4_2a_v2_heldout_finalizer.py"),
)
_INERT_HISTORICAL_AUDIT_HOOK_SOURCES: tuple[tuple[str, str, int], ...] = (
    (
        "scripts/rehearse_p4_2a_v2_1_heldout_full_path.py",
        "scripts.rehearse_p4_2a_v2_1_heldout_full_path",
        2,
    ),
    (
        "scripts/validate_p4_2a_v2_1_heldout_rehearsal_bundle.py",
        "scripts.validate_p4_2a_v2_1_heldout_rehearsal_bundle",
        1,
    ),
)
_CONTROL_GOVERNANCE_AUTHORITIES = {
    **{
        path: (digest, creating_commit, True)
        for path, digest, creating_commit in _CARRY_FORWARD_AUTHORITIES.values()
    },
    INDEPENDENT_REVIEW_RELATIVE.as_posix(): (
        INDEPENDENT_REVIEW_SHA256,
        INDEPENDENT_REVIEW_COMMIT,
        False,
    ),
}
_V2_1_BUNDLE_SCHEMA_RELATIVE = Path(
    "config/schemas/p4_2a_v2_1_heldout_rehearsal_bundle.schema.json"
)
_V2_1_BUNDLE_SCHEMA_SHA256 = (
    "ed827e29ce853f07a9110d44c98793a4cc3ef0634a12fe7e8bc64c7290d7d716"
)
_V2_1_RELEASE_SCHEMA_RELATIVE = Path(
    "config/schemas/p4_2a_v2_1_heldout_release_authorization.schema.json"
)
_V2_1_RELEASE_SCHEMA_SHA256 = (
    "c5a4ecfe8c5bf3e3ebea2d4470337a67dde3a8e9dbe6fc3df68b1c4e16241c51"
)
_INHERITANCE_SNAPSHOT_SHA256 = (
    "f3d74f06c9b114ce85768f647252db76edadc42a95ab6a6f29c05d69f39bea0e"
)
_PROJECTION_TARGETS = {
    "/frozen_inputs": "frozen_inputs",
    "/request_interval_contract": "request_interval_contract",
    "/materialization_manifest_amendment": "materialization_manifest_amendment",
    "/runtime_start_policy": "runtime_start_policy",
    "/rehearsal_contract": "rehearsal_contract_non_delta",
    "/implementation_contract": "implementation_contract_historical_v2_1",
    "/bundle_and_release_effects": "bundle_and_release_effects_base_guarantees",
    "/execution_safety": "execution_safety",
    "/locks": "locks",
}
_PROJECTION_EXCLUDED_REHEARSAL_KEYS = (
    "registered_runner",
    "registered_validator",
    "official_execution_count",
    "domain_separated_merkle",
)
_ALLOWED_V2_2_DELTA_POINTERS = (
    "/schema_version",
    "/preregistration_id",
    "/created_at_utc",
    "/created_at_shanghai",
    "/status",
    "/purpose",
    "/ordering_discipline",
    "/registered_schemas",
    "/authorities",
    "/identity",
    "/rehearsal_attempt_policy",
    "/threat_model",
    "/series_ledger_contract",
    "/attempt_record_contract",
    "/action_time_authorization_contract",
    "/harness_identity_contract",
    "/exact_os_bootstrap_contract",
    "/synthetic_rebase_contract",
    "/implementation_epoch_contract",
    "/prospective_implementation_contract",
    "/contract_inheritance",
    "/additional_frozen_authorities",
    "/pipeline_contract",
    "/runtime_and_control_inheritance",
    "/bundle_contract",
    "/release_contract",
    "/required_positive_tests",
    "/required_negative_tests",
    "/test_execution_contract",
    "/future_owner_gates_required",
    "/execution_safety",
    "/locks",
    "/authorization",
)
_BUNDLE_SCHEMA_DELTA_POINTERS = (
    "/$id",
    "/title",
    "/required",
    "/properties/schema_version",
    "/properties/rehearsal_id",
    "/properties/status",
    "/properties/execution_binding",
    "/properties/rehearsal_attempt_policy",
    "/properties/harness_identity",
    "/properties/implementation_epochs",
    "/properties/attempt_history",
    "/properties/evaluation_one_shot",
    "/$defs/lineage",
    "/$defs/publication/properties/directory",
    "/$defs/realEntryGateValidation",
    "/$defs/archive",
    "/$defs/merkle",
    "/$defs/semanticValidation",
    "/$defs/remainingBlockers",
    "/$defs/absolutePath",
    "/$defs/authorityRef",
    "/$defs/executionBinding",
    "/$defs/rehearsalAttemptPolicy",
    "/$defs/harnessIdentity",
    "/$defs/implementationEpoch",
    "/$defs/attemptFileEvidence",
    "/$defs/archivedAuthorityEvidence",
    "/$defs/attemptArtifactEvidence",
    "/$defs/attemptError",
    "/$defs/attemptRecord",
    "/$defs/attemptHistory",
    "/$defs/attemptHistoryArchive",
    "/$defs/evaluationOneShot",
)
_RELEASE_SCHEMA_DELTA_POINTERS = (
    "/$id",
    "/title",
    "/required",
    "/properties/schema_version",
    "/properties/authorization_id",
    "/properties/verdict",
    "/properties/owner_authorization",
    "/properties/lineage",
    "/properties/execution_binding",
    "/properties/series_identity",
    "/properties/attempt_history_acceptance",
    "/properties/implementation_epochs",
    "/properties/independent_checks",
    "/properties/authorized_stages",
    "/properties/still_gated",
    "/properties/runtime_start_policy",
    "/properties/production_integration_gate",
    "/properties/evaluation_one_shot",
    "/properties/locks",
    "/$defs/absolutePath",
    "/$defs/authorityRef",
    "/$defs/executionBinding",
    "/$defs/attemptOutcomeAcknowledgement",
    "/$defs/implementationEpoch",
)

_GIT_CONFIG_PREFIX = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.commitGraph=false",
    "-c",
    "gc.auto=0",
)
_GIT_ENVIRONMENT = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TZ": "UTC",
    "GIT_ATTR_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "PAGER": "cat",
}

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_RELATIVE_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
_UTC_SECONDS_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_SHANGHAI_SECONDS_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\+08:00$"
)
_ACTION_ID_PATTERN = re.compile(
    r"^P4\.2A-V2-2-REHEARSAL-ATTEMPT-([0-9]{6})-EXECUTION-AUTHORIZATION-([0-9]{8})$"
)
_ACTION_PATH_PATTERN = re.compile(
    r"^docs/phase4/reports/P4\.2a-v2-2-rehearsal-attempt-([0-9]{6})-execution-authorization-([0-9]{8})\.json$"
)
_EVIDENCE_RELATIVE_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
_ZERO32 = bytes(32)

_EXACT_ENVIRONMENT = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "OPENBLAS_MAIN_FREE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONHASHSEED": "0",
    "PYTHONNOUSERSITE": "1",
    "PYTHONPYCACHEPREFIX": "/dev/null",
    "PYTHONSAFEPATH": "1",
    "TZ": "UTC",
    "__CF_USER_TEXT_ENCODING": "0x1F5:0x0:0x0",
    "PATH": "/usr/bin:/bin",
    _VALIDATOR_ENVIRONMENT_MARKER: "1",
}

_STARTED_FIELDS = frozenset(
    {
        "schema_version",
        "series_id",
        "series_token_sha256",
        "ordinal",
        "attempt_token_sha256",
        "previous_history_root_sha256",
        "implementation_epoch",
        "implementation_commit",
        "owner_action_time_authorization",
        "control_merkle_root_sha256",
        "command",
        "command_sha256",
        "environment",
        "environment_sha256",
        "interpreter_path",
        "interpreter_sha256",
        "created_at_utc",
    }
)
_CANDIDATE_FIELDS = frozenset(
    {
        "schema_version",
        "series_id",
        "ordinal",
        "attempt_token_sha256",
        "implementation_epoch",
        "implementation_commit",
        "run_a_root_sha256",
        "run_b_root_sha256",
        "control_surface_root_sha256",
        "evidence_tree_root_sha256",
        "candidate_content_root_sha256",
        "validated_at_utc",
    }
)
_TERMINAL_FIELDS = frozenset(
    {
        "schema_version",
        "series_id",
        "ordinal",
        "attempt_token_sha256",
        "outcome",
        "reached_stage",
        "implementation_epoch",
        "implementation_commit",
        "automatic_retry_count",
        "artifact_inventory",
        "error",
        "evidence_tree_root_sha256",
        "completed_at_utc",
    }
)
_ACTION_FIELDS = frozenset(
    {
        "schema_version",
        "authorization_id",
        "created_at_utc",
        "created_at_shanghai",
        "verdict",
        "owner",
        "series_id",
        "series_token_sha256",
        "ledger_root",
        "ordinal",
        "previous_history_root_sha256",
        "implementation_epoch",
        "implementation_commit",
        "owner_exact_surface_authorization",
        "independent_implementation_review",
        "control_merkle_root_sha256",
        "exact_argv",
        "command_sha256",
        "exact_environment",
        "environment_sha256",
        "authorized_pipeline_starts",
        "automatic_retry_count",
        "heldout_evaluation_authorized",
        "locks",
    }
)
_SERIES_FIELDS = frozenset(
    {
        "schema_version",
        "series_id",
        "series_token_sha256",
        "policy",
        "ledger_root",
        "attempt_limit",
        "per_attempt_action_time_owner_authorization_required",
        "automatic_retry_count",
        "first_validated_candidate_closes_series",
        "preregistration",
        "bundle_schema",
        "release_schema",
        "created_at_utc",
    }
)
_REAL_STAGES = (
    "materialize",
    "infer",
    "select-blind",
    "blind-draft",
    "owner-adjudication-ui",
    "finalize-owner-adjudication",
    "heldout-evaluation",
    "p4.2b",
    "p4.3",
)


class RehearsalV22ValidationError(RuntimeError):
    """Fail-closed v2.2 bundle or evidence-acceptance validation error."""


def _reject_constant(value: str, *, label: str) -> NoReturn:
    raise RehearsalV22ValidationError(
        f"{label} contains forbidden numeric constant {value!r}"
    )


def strict_json_loads(payload: bytes | str, *, label: str = "JSON") -> Any:
    """Parse JSON while rejecting duplicate keys and non-finite constants."""

    try:
        text = payload.decode("utf-8", errors="strict") if isinstance(payload, bytes) else payload
    except UnicodeDecodeError as exc:
        raise RehearsalV22ValidationError(f"{label} is not UTF-8") from exc

    def object_pairs(pairs: list[tuple[str, Any]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in pairs:
            if key in result:
                raise RehearsalV22ValidationError(f"{label} contains duplicate key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_constant=lambda value: _reject_constant(value, label=label),
        )
    except RehearsalV22ValidationError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RehearsalV22ValidationError(f"{label} is not strict JSON") from exc


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError) as exc:
        raise RehearsalV22ValidationError("value cannot be canonical JSON") from exc


def _strict_canonical_json_loads(payload: bytes, *, label: str) -> JsonObject:
    value = _object(strict_json_loads(payload, label=label), label)
    if _canonical_json_bytes(value) != payload:
        raise RehearsalV22ValidationError(f"{label} is not canonical JSON bytes")
    return value


def _object(value: object, label: str) -> JsonObject:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise RehearsalV22ValidationError(f"{label} must be one JSON object")
    return cast(JsonObject, value)


def _array(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RehearsalV22ValidationError(f"{label} must be one JSON array")
    return value


def _string(value: object, label: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        raise RehearsalV22ValidationError(f"{label} must be a string")
    return value


def _integer(value: object, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RehearsalV22ValidationError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise RehearsalV22ValidationError(f"{label} is below {minimum}")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise RehearsalV22ValidationError(f"{label} must be a boolean")
    return value


def _sha(value: object, label: str) -> str:
    text = _string(value, label)
    if _SHA256_PATTERN.fullmatch(text) is None:
        raise RehearsalV22ValidationError(f"{label} is not lowercase SHA-256")
    return text


def _commit(value: object, label: str) -> str:
    text = _string(value, label)
    if _COMMIT_PATTERN.fullmatch(text) is None:
        raise RehearsalV22ValidationError(f"{label} is not a lowercase Git commit")
    return text


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_exact_keys(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise RehearsalV22ValidationError(
            f"{label} fields drifted: missing={missing!r} extra={extra!r}"
        )


def _require_equal(actual: object, expected: object, label: str) -> None:
    if type(actual) is not type(expected) or actual != expected:
        raise RehearsalV22ValidationError(f"{label} drifted")


def _rfc3339_utc(value: object, label: str) -> str:
    text = _string(value, label)
    if _UTC_SECONDS_PATTERN.fullmatch(text) is None:
        raise RehearsalV22ValidationError(f"{label} must be RFC3339 UTC seconds")
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RehearsalV22ValidationError(f"{label} is not a real timestamp") from exc
    return text


def _rfc3339_shanghai(value: object, label: str) -> str:
    text = _string(value, label)
    if _SHANGHAI_SECONDS_PATTERN.fullmatch(text) is None:
        raise RehearsalV22ValidationError(f"{label} must have exact +08:00 offset")
    try:
        datetime.fromisoformat(text)
    except ValueError as exc:
        raise RehearsalV22ValidationError(f"{label} is not a real timestamp") from exc
    return text


def _relative(value: object, label: str) -> str:
    text = _string(value, label)
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or _RELATIVE_PATTERN.fullmatch(text) is None
        or "//" in text
        or text.endswith("/")
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise RehearsalV22ValidationError(f"{label} is not a safe relative POSIX path")
    return text


def _git_safe_relative(value: object, label: str) -> str:
    """Accept any strict UTF-8 Git path that is safe as relative POSIX text."""

    text = _string(value, label)
    segments = text.split("/")
    if (
        PurePosixPath(text).is_absolute()
        or "\x00" in text
        or "\\" in text
        or "//" in text
        or text.endswith("/")
        or any(segment in {"", ".", ".."} for segment in segments)
        or any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in text
        )
    ):
        raise RehearsalV22ValidationError(
            f"{label} is not a safe relative POSIX path"
        )
    return text


def _evidence_relative(value: object, label: str) -> str:
    text = _relative(value, label)
    if not text.isascii() or _EVIDENCE_RELATIVE_PATTERN.fullmatch(text) is None:
        raise RehearsalV22ValidationError(f"{label} is not normalized ASCII")
    return text


def _safe_path(root: Path, relative: object, label: str) -> Path:
    text = _relative(relative, label)
    candidate = root.joinpath(*PurePosixPath(text).parts)
    current = root
    for part in PurePosixPath(text).parts:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise RehearsalV22ValidationError(f"{label} is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise RehearsalV22ValidationError(f"{label} traverses a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise RehearsalV22ValidationError(f"{label} is unavailable") from exc
    if resolved == root or not resolved.is_relative_to(root):
        raise RehearsalV22ValidationError(f"{label} escapes its root")
    return resolved


def _regular_bytes(path: Path, label: str, *, allow_empty: bool = False) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RehearsalV22ValidationError(f"{label} is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise RehearsalV22ValidationError(f"{label} is not one unaliased regular file")
    payload = path.read_bytes()
    if not payload and not allow_empty:
        raise RehearsalV22ValidationError(f"{label} is empty")
    return payload


def _fixed_launcher_bytes() -> bytes:
    launcher = Path(_VALIDATOR_FIXED_PYTHON)
    try:
        launcher_metadata = launcher.lstat()
        resolved = launcher.resolve(strict=True)
        resolved_metadata = resolved.lstat()
    except OSError as exc:
        raise RehearsalV22ValidationError("fixed Python launcher is unavailable") from exc
    if (
        not (stat.S_ISLNK(launcher_metadata.st_mode) or stat.S_ISREG(launcher_metadata.st_mode))
        or resolved.is_symlink()
        or not stat.S_ISREG(resolved_metadata.st_mode)
        or resolved_metadata.st_nlink != 1
    ):
        raise RehearsalV22ValidationError(
            "fixed Python launcher chain is not one regular executable"
        )
    payload = resolved.read_bytes()
    if not payload:
        raise RehearsalV22ValidationError("fixed Python launcher is empty")
    return payload


def _directory(path: Path, label: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RehearsalV22ValidationError(f"{label} is unavailable") from exc
    resolved = path.resolve(strict=True)
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or path.absolute() != resolved
    ):
        raise RehearsalV22ValidationError(f"{label} is not one regular directory")
    return resolved


def _bound_control(root: Path, relative: Path, digest: str, label: str) -> bytes:
    payload = _regular_bytes(_safe_path(root, relative.as_posix(), label), label)
    if _sha256(payload) != digest:
        raise RehearsalV22ValidationError(f"{label} digest drifted")
    return payload


def _typed_json_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        if not isinstance(right, dict) or set(left) != set(right):
            return False
        return all(_typed_json_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return isinstance(right, list) and len(left) == len(right) and all(
            _typed_json_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


def _pointer_tokens(pointer: str, label: str) -> tuple[str, ...]:
    if not pointer.startswith("/") or pointer == "/":
        raise RehearsalV22ValidationError(f"{label} is not a registered JSON Pointer")
    result: list[str] = []
    for raw in pointer[1:].split("/"):
        if re.search(r"~(?![01])", raw):
            raise RehearsalV22ValidationError(f"{label} has invalid JSON Pointer escape")
        result.append(raw.replace("~1", "/").replace("~0", "~"))
    return tuple(result)


def _pointer_get(document: object, pointer: str, label: str) -> object:
    current = document
    for token in _pointer_tokens(pointer, label):
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise RehearsalV22ValidationError(f"{label} does not resolve")
    return current


def _pointer_delete(document: object, pointer: str, *, required: bool, label: str) -> bool:
    tokens = _pointer_tokens(pointer, label)
    current = document
    for token in tokens[:-1]:
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            if required:
                raise RehearsalV22ValidationError(f"{label} does not resolve")
            return False
    final = tokens[-1]
    if isinstance(current, dict) and final in current:
        del current[final]
        return True
    if isinstance(current, list) and final.isdigit() and int(final) < len(current):
        del current[int(final)]
        return True
    if required:
        raise RehearsalV22ValidationError(f"{label} does not resolve")
    return False


def _snapshot_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RehearsalV22ValidationError(
            "inheritance snapshot cannot be encoded with frozen CPython JSON semantics"
        ) from exc


def _schemas_match_after_registered_delta_strip(
    *,
    base: object,
    successor: object,
    pointers: tuple[str, ...],
    label: str,
) -> None:
    base_copy = copy.deepcopy(base)
    successor_copy = copy.deepcopy(successor)
    for pointer in pointers:
        _pointer_delete(
            successor_copy,
            pointer,
            required=True,
            label=f"{label} successor delta {pointer}",
        )
        _pointer_delete(
            base_copy,
            pointer,
            required=False,
            label=f"{label} base delta {pointer}",
        )
    if not _typed_json_equal(base_copy, successor_copy):
        raise RehearsalV22ValidationError(
            f"{label} retained v2.1 schema changed outside registered delta domains"
        )


def _validate_contract_inheritance_payloads(
    *,
    preregistration_payload: bytes,
    bundle_schema_payload: bytes,
    release_schema_payload: bytes,
    base_preregistration_payload: bytes,
    base_bundle_schema_payload: bytes,
    base_release_schema_payload: bytes,
) -> None:
    """Rebuild the typed v2.1 projection and both zero-diff schema projections."""

    preregistration = _object(
        strict_json_loads(preregistration_payload, label="v2.2 preregistration"),
        "v2.2 preregistration",
    )
    inheritance = _object(
        preregistration.get("contract_inheritance"),
        "v2.2 contract inheritance",
    )
    base_path, base_digest, base_commit = _CARRY_FORWARD_AUTHORITIES[
        "v2_1_preregistration"
    ]
    _require_equal(
        inheritance.get("base_preregistration"),
        {"path": base_path, "sha256": base_digest, "creating_commit": base_commit},
        "inheritance base preregistration",
    )
    base = _object(
        strict_json_loads(
            base_preregistration_payload,
            label="v2.1 base preregistration",
        ),
        "v2.1 base preregistration",
    )
    projection_contract = _object(
        inheritance.get("source_projection"), "inheritance source projection"
    )
    _require_equal(
        projection_contract.get("source_file"),
        base_path,
        "inheritance source file",
    )
    _require_equal(
        projection_contract.get("exact_sections"),
        [pointer for pointer in _PROJECTION_TARGETS if pointer != "/rehearsal_contract"],
        "inheritance exact sections",
    )
    _require_equal(
        projection_contract.get("rehearsal_contract_source"),
        "/rehearsal_contract",
        "inheritance rehearsal source",
    )
    _require_equal(
        projection_contract.get("rehearsal_contract_excluded_keys"),
        list(_PROJECTION_EXCLUDED_REHEARSAL_KEYS),
        "inheritance rehearsal exclusions",
    )
    _require_equal(
        projection_contract.get("target_key_map"),
        _PROJECTION_TARGETS,
        "inheritance target map",
    )
    snapshot: JsonObject = {}
    for pointer, target in _PROJECTION_TARGETS.items():
        value = copy.deepcopy(
            _pointer_get(base, pointer, f"inheritance source {pointer}")
        )
        if pointer == "/rehearsal_contract":
            projected = _object(value, "projected rehearsal contract")
            for excluded in _PROJECTION_EXCLUDED_REHEARSAL_KEYS:
                if excluded not in projected:
                    raise RehearsalV22ValidationError(
                        f"base rehearsal contract lacks excluded key {excluded}"
                    )
                del projected[excluded]
        snapshot[target] = value
    recorded_snapshot = inheritance.get("strict_inheritance_snapshot")
    if not _typed_json_equal(snapshot, recorded_snapshot):
        raise RehearsalV22ValidationError(
            "typed strict-inheritance source projection differs from preregistration"
        )
    if (
        inheritance.get("strict_inheritance_snapshot_sha256")
        != _INHERITANCE_SNAPSHOT_SHA256
        or _sha256(_snapshot_bytes(snapshot)) != _INHERITANCE_SNAPSHOT_SHA256
    ):
        raise RehearsalV22ValidationError("strict-inheritance snapshot digest drifted")
    _require_equal(
        inheritance.get("allowed_v2_2_delta_json_pointers"),
        list(_ALLOWED_V2_2_DELTA_POINTERS),
        "allowed v2.2 delta pointers",
    )
    for pointer in _ALLOWED_V2_2_DELTA_POINTERS:
        _pointer_get(preregistration, pointer, f"allowed v2.2 delta {pointer}")
    _require_equal(
        inheritance.get("bundle_schema_delta_domains"),
        list(_BUNDLE_SCHEMA_DELTA_POINTERS),
        "bundle schema delta domains",
    )
    _require_equal(
        inheritance.get("release_schema_delta_domains"),
        list(_RELEASE_SCHEMA_DELTA_POINTERS),
        "release schema delta domains",
    )
    _schemas_match_after_registered_delta_strip(
        base=strict_json_loads(
            base_bundle_schema_payload,
            label="v2.1 bundle schema",
        ),
        successor=strict_json_loads(bundle_schema_payload, label="v2.2 bundle schema"),
        pointers=_BUNDLE_SCHEMA_DELTA_POINTERS,
        label="bundle schema inheritance",
    )
    _schemas_match_after_registered_delta_strip(
        base=strict_json_loads(
            base_release_schema_payload,
            label="v2.1 release schema",
        ),
        successor=strict_json_loads(release_schema_payload, label="v2.2 release schema"),
        pointers=_RELEASE_SCHEMA_DELTA_POINTERS,
        label="release schema inheritance",
    )


def _validate_contract_inheritance(
    *,
    project_root: Path,
    preregistration_payload: bytes,
    bundle_schema_payload: bytes,
    release_schema_payload: bytes,
) -> None:
    """Validate active inheritance exclusively against current control bytes."""

    base_path, base_digest, _base_commit = _CARRY_FORWARD_AUTHORITIES[
        "v2_1_preregistration"
    ]
    _validate_contract_inheritance_payloads(
        preregistration_payload=preregistration_payload,
        bundle_schema_payload=bundle_schema_payload,
        release_schema_payload=release_schema_payload,
        base_preregistration_payload=_bound_control(
            project_root,
            Path(base_path),
            base_digest,
            "v2.1 base preregistration",
        ),
        base_bundle_schema_payload=_bound_control(
            project_root,
            _V2_1_BUNDLE_SCHEMA_RELATIVE,
            _V2_1_BUNDLE_SCHEMA_SHA256,
            "v2.1 bundle schema",
        ),
        base_release_schema_payload=_bound_control(
            project_root,
            _V2_1_RELEASE_SCHEMA_RELATIVE,
            _V2_1_RELEASE_SCHEMA_SHA256,
            "v2.1 release schema",
        ),
    )


def _validated_implementation_blob(
    *,
    project_root: Path,
    historical_anchor: implementation.HistoricalSelectedAnchor,
    relative_path: str,
    expected_sha256: str,
) -> bytes:
    """Validate one selected-history blob without consulting current bytes."""

    payload = _git_blob(project_root, historical_anchor.selected_commit, relative_path)
    recorded = historical_anchor.selected_git_blob_sha256.get(relative_path)
    if (
        _sha256(payload) != expected_sha256
        or recorded != expected_sha256
    ):
        raise RehearsalV22ValidationError(
            f"historical selected implementation blob drifted: {relative_path}"
        )
    return payload


def _validate_historical_contract_inheritance(
    *,
    project_root: Path,
    historical_anchor: implementation.HistoricalSelectedAnchor,
    preregistration_payload: bytes,
    bundle_schema_payload: bytes,
    release_schema_payload: bytes,
) -> None:
    """Validate passive inheritance only from the selected commit's Git blobs."""

    base_path, base_digest, _base_commit = _CARRY_FORWARD_AUTHORITIES[
        "v2_1_preregistration"
    ]
    _validate_contract_inheritance_payloads(
        preregistration_payload=preregistration_payload,
        bundle_schema_payload=bundle_schema_payload,
        release_schema_payload=release_schema_payload,
        base_preregistration_payload=_validated_implementation_blob(
            project_root=project_root,
            historical_anchor=historical_anchor,
            relative_path=base_path,
            expected_sha256=base_digest,
        ),
        base_bundle_schema_payload=_validated_implementation_blob(
            project_root=project_root,
            historical_anchor=historical_anchor,
            relative_path=_V2_1_BUNDLE_SCHEMA_RELATIVE.as_posix(),
            expected_sha256=_V2_1_BUNDLE_SCHEMA_SHA256,
        ),
        base_release_schema_payload=_validated_implementation_blob(
            project_root=project_root,
            historical_anchor=historical_anchor,
            relative_path=_V2_1_RELEASE_SCHEMA_RELATIVE.as_posix(),
            expected_sha256=_V2_1_RELEASE_SCHEMA_SHA256,
        ),
    )


def _validated_live_implementation_blob(
    *,
    project_root: Path,
    live_anchor: implementation.LiveExecutionAnchor,
    relative_path: str,
    expected_sha256: str,
) -> bytes:
    """Validate one currently executing blob against the reviewed live epoch."""

    payload = _git_blob(project_root, live_anchor.implementation_commit, relative_path)
    current = _regular_bytes(
        _safe_path(
            project_root,
            relative_path,
            f"current implementation {relative_path}",
        ),
        f"current implementation {relative_path}",
    )
    if (
        _sha256(payload) != expected_sha256
        or current != payload
        or implementation.validate_implementation_blob(
            project_root,
            live_anchor.implementation_commit,
            relative_path,
            require_current=True,
        )
        != payload
    ):
        raise RehearsalV22ValidationError(
            f"live execution implementation blob drifted: {relative_path}"
        )
    return payload


def _raw_hardened_git(root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        [
            "/usr/bin/git",
            *_GIT_CONFIG_PREFIX,
            "-C",
            root.as_posix(),
            *arguments,
        ],
        check=False,
        capture_output=True,
        env=dict(_GIT_ENVIRONMENT),
    )
    if completed.returncode != 0 or completed.stderr:
        raise RehearsalV22ValidationError(
            f"hardened Git {' '.join(arguments[:3])} failed: "
            f"{completed.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return completed.stdout


def _git_metadata_roots(root: Path) -> tuple[Path, Path]:
    observed_git_dir = _raw_hardened_git(
        root, "rev-parse", "--path-format=absolute", "--git-dir"
    ).decode("utf-8", errors="strict").strip()
    observed_common_dir = _raw_hardened_git(
        root, "rev-parse", "--path-format=absolute", "--git-common-dir"
    ).decode("utf-8", errors="strict").strip()
    if not observed_git_dir or not observed_common_dir:
        raise RehearsalV22ValidationError("Git metadata authority path is empty")
    git_dir = Path(observed_git_dir)
    common_dir = Path(observed_common_dir)
    if not git_dir.is_absolute() or not common_dir.is_absolute():
        raise RehearsalV22ValidationError("Git metadata authority is not absolute")
    dotgit = root / ".git"
    try:
        metadata = dotgit.lstat()
    except OSError as exc:
        raise RehearsalV22ValidationError("Git metadata authority is unavailable") from exc
    if dotgit.is_symlink():
        raise RehearsalV22ValidationError("Git metadata authority is symlinked")
    if stat.S_ISDIR(metadata.st_mode):
        pointer_git_dir = dotgit.resolve(strict=True)
    elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
        text = dotgit.read_text(encoding="utf-8", errors="strict").strip()
        if not text.startswith("gitdir: "):
            raise RehearsalV22ValidationError("worktree Git pointer is malformed")
        raw = Path(text.removeprefix("gitdir: "))
        pointer_git_dir = (raw if raw.is_absolute() else root / raw).resolve(strict=True)
    else:
        raise RehearsalV22ValidationError("Git metadata authority is not regular")
    try:
        git_dir = git_dir.resolve(strict=True)
        common_dir = common_dir.resolve(strict=True)
    except OSError as exc:
        raise RehearsalV22ValidationError("Git metadata authority is unavailable") from exc
    if pointer_git_dir != git_dir:
        raise RehearsalV22ValidationError("Git metadata pointer differs from Git authority")
    common_file = git_dir / "commondir"
    if common_file.is_symlink():
        raise RehearsalV22ValidationError("Git common-dir pointer is symlinked")
    if common_file.is_file():
        raw_common = Path(common_file.read_text(encoding="utf-8", errors="strict").strip())
        pointer_common_dir = (
            raw_common if raw_common.is_absolute() else git_dir / raw_common
        ).resolve(strict=True)
    else:
        pointer_common_dir = git_dir
    if pointer_common_dir != common_dir:
        raise RehearsalV22ValidationError("Git common-dir pointer differs from Git authority")
    for directory, label in ((git_dir, "Git dir"), (common_dir, "Git common dir")):
        if directory.is_symlink() or not directory.is_dir():
            raise RehearsalV22ValidationError(f"{label} is aliased")
    return git_dir, common_dir


def _validate_git_metadata_authority(root: Path) -> None:
    git_dir, common_dir = _git_metadata_roots(root)
    forbidden = {
        git_dir / "shallow",
        git_dir / "info/grafts",
        git_dir / "objects/info/alternates",
        git_dir / "refs/replace",
        common_dir / "shallow",
        common_dir / "info/grafts",
        common_dir / "objects/info/alternates",
        common_dir / "refs/replace",
    }
    if any(path.exists() or path.is_symlink() for path in forbidden):
        raise RehearsalV22ValidationError("mutable Git graft/alternate/replace authority exists")
    for packed in {git_dir / "packed-refs", common_dir / "packed-refs"}:
        if packed.is_file() and b"refs/replace/" in packed.read_bytes():
            raise RehearsalV22ValidationError("packed Git replace authority exists")


def _git_bytes(root: Path, *arguments: str) -> bytes:
    """Run one independently selected, hardened, read-only Git operation."""

    _validate_git_metadata_authority(root)
    if (
        tuple(implementation.GIT_CONFIG_PREFIX) != _GIT_CONFIG_PREFIX
        or implementation._git_environment() != _GIT_ENVIRONMENT
    ):
        raise RehearsalV22ValidationError("producer and validator Git policy drifted")
    return _raw_hardened_git(root, *arguments)


def _git_commit(root: Path, value: object, label: str) -> str:
    commit = _commit(value, label)
    observed = _git_bytes(root, "rev-parse", "--verify", f"{commit}^{{commit}}").decode(
        "ascii", errors="strict"
    ).strip()
    if observed != commit:
        raise RehearsalV22ValidationError(f"{label} object identity drifted")
    return commit


def _git_parents(root: Path, commit: str) -> tuple[str, ...]:
    line = _git_bytes(root, "rev-list", "--parents", "-n", "1", commit).decode(
        "ascii", errors="strict"
    ).strip()
    fields = tuple(line.split())
    if not fields or fields[0] != commit:
        raise RehearsalV22ValidationError("Git parent record drifted")
    return tuple(_commit(value, "Git parent") for value in fields[1:])


def _git_is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    # merge-base --is-ancestor uses exit 1 for a clean negative answer.  Use a
    # raw subprocess here so a false relation is not conflated with corruption.
    _validate_git_metadata_authority(root)
    completed = subprocess.run(
        [
            "/usr/bin/git",
            *_GIT_CONFIG_PREFIX,
            "-C",
            root.as_posix(),
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        ],
        check=False,
        capture_output=True,
        env=dict(_GIT_ENVIRONMENT),
    )
    if completed.stderr or completed.returncode not in {0, 1}:
        raise RehearsalV22ValidationError("Git ancestry proof failed")
    return completed.returncode == 0


def _git_blob(root: Path, commit: str, relative: str) -> bytes:
    _relative(relative, "Git blob path")
    target = f"{commit}:{relative}"
    if _git_bytes(root, "cat-file", "-t", target).strip() != b"blob":
        raise RehearsalV22ValidationError("Git path does not identify one blob")
    return _git_bytes(root, "show", target)


def _git_optional_blob(root: Path, commit: str, relative: str) -> bytes | None:
    """Read an optional commit blob without translating other Git failures."""

    path = _relative(relative, "optional Git blob path")
    observed = _git_bytes(root, "ls-tree", "-z", "--full-tree", commit, "--", path)
    if observed == b"":
        return None
    records = observed.split(b"\0")
    if len(records) != 2 or records[1] != b"":
        raise RehearsalV22ValidationError(
            "optional local-import candidate has multiple Git tree records"
        )
    identity, separator, observed_path = records[0].partition(b"\t")
    fields = identity.split(b" ")
    if (
        separator != b"\t"
        or observed_path != path.encode("ascii")
        or len(fields) != 3
        or fields[0] != b"100644"
        or fields[1] != b"blob"
        or re.fullmatch(rb"[0-9a-f]{40}", fields[2]) is None
    ):
        raise RehearsalV22ValidationError(
            "optional local-import candidate is not one exact regular Git blob"
        )
    target = f"{commit}:{path}"
    if _git_bytes(root, "rev-parse", "--verify", target).strip() != fields[2]:
        raise RehearsalV22ValidationError(
            "optional local-import candidate object identity drifted"
        )
    return _git_blob(root, commit, path)


def _local_module_name(relative: str) -> tuple[str, str]:
    path = PurePosixPath(_relative(relative, "local Python source"))
    if path.parts[0] == "scripts":
        components = list(path.with_suffix("").parts)
    elif path.parts[:2] == ("src", "alphapilot"):
        components = list(path.with_suffix("").parts[1:])
    else:
        raise RehearsalV22ValidationError(
            "local Python source is outside registered namespaces"
        )
    package_source = components[-1] == "__init__"
    if package_source:
        components.pop()
    module = ".".join(components)
    return module, module if package_source else module.rpartition(".")[0]


def _local_module_file(
    root: Path,
    commit: str,
    module_name: str,
) -> str | None:
    if module_name == "scripts":
        return None
    if module_name.startswith("scripts."):
        stem = "scripts/" + module_name.removeprefix("scripts.").replace(".", "/")
    elif module_name == "alphapilot":
        stem = "src/alphapilot"
    elif module_name.startswith("alphapilot."):
        stem = "src/alphapilot/" + module_name.removeprefix("alphapilot.").replace(
            ".", "/"
        )
    else:
        return None
    found = [
        candidate
        for candidate in (f"{stem}.py", f"{stem}/__init__.py")
        if _git_optional_blob(root, commit, candidate) is not None
    ]
    if len(found) > 1:
        raise RehearsalV22ValidationError(
            f"ambiguous local import in implementation commit: {module_name}"
        )
    return found[0] if found else None


def _local_ancestor_initializers(
    root: Path,
    commit: str,
    relative: str,
) -> set[str]:
    path = PurePosixPath(relative)
    if path.parts[0] == "scripts":
        start = 1
    elif path.parts[:2] == ("src", "alphapilot"):
        start = 2
    else:
        return set()
    result: set[str] = set()
    parent_parts = path.parent.parts
    for length in range(start, len(parent_parts) + 1):
        candidate = (PurePosixPath(*parent_parts[:length]) / "__init__.py").as_posix()
        if _git_optional_blob(root, commit, candidate) is not None:
            result.add(candidate)
    return result


def _resolve_local_import_from(package: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    components = package.split(".") if package else []
    remove = node.level - 1
    if remove > len(components):
        raise RehearsalV22ValidationError("relative local import escapes its package")
    prefix = components[: len(components) - remove]
    if node.module:
        prefix.extend(node.module.split("."))
    return ".".join(prefix)


def _resolved_local_module_paths(
    root: Path,
    commit: str,
    module_name: str,
    *,
    unresolved_is_error: bool,
) -> set[str]:
    if module_name not in {"scripts", "alphapilot"} and not module_name.startswith(
        ("scripts.", "alphapilot.")
    ):
        return set()
    candidate = _local_module_file(root, commit, module_name)
    if candidate is None:
        if unresolved_is_error and module_name != "scripts":
            raise RehearsalV22ValidationError(
                f"unresolved local import in implementation commit: {module_name}"
            )
        return set()
    return {
        candidate,
        *_local_ancestor_initializers(root, commit, candidate),
    }


def _independent_local_import_closure(
    *,
    project_root: Path,
    implementation_commit: str,
) -> dict[str, bytes]:
    """Re-derive the local AST closure without calling the producer walker."""

    pending = list(IMPLEMENTATION_PATHS[:3])
    payloads: dict[str, bytes] = {}
    while pending:
        relative = pending.pop(0)
        if relative in payloads:
            continue
        payload = _git_blob(project_root, implementation_commit, relative)
        try:
            tree = ast.parse(payload, filename=relative)
        except (SyntaxError, UnicodeDecodeError) as exc:
            raise RehearsalV22ValidationError(
                f"cannot parse commit-bound local Python source: {relative}"
            ) from exc
        payloads[relative] = payload
        _module, package = _local_module_name(relative)
        discovered: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    discovered.update(
                        _resolved_local_module_paths(
                            project_root,
                            implementation_commit,
                            alias.name,
                            unresolved_is_error=True,
                        )
                    )
                continue
            if isinstance(node, ast.ImportFrom):
                base = _resolve_local_import_from(package, node)
                if base:
                    discovered.update(
                        _resolved_local_module_paths(
                            project_root,
                            implementation_commit,
                            base,
                            unresolved_is_error=True,
                        )
                    )
                for alias in node.names:
                    if alias.name != "*":
                        discovered.update(
                            _resolved_local_module_paths(
                                project_root,
                                implementation_commit,
                                f"{base}.{alias.name}" if base else alias.name,
                                unresolved_is_error=False,
                            )
                        )
                continue
            if isinstance(node, ast.Call):
                name = ""
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                target = (
                    node.args[0].value
                    if node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    else None
                )
                if name in {"__import__", "import_module"}:
                    if not isinstance(target, str):
                        raise RehearsalV22ValidationError(
                            "nonliteral dynamic import cannot be proven non-local: "
                            f"{relative}"
                        )
                    if target in {"scripts", "alphapilot"} or target.startswith(
                        ("scripts.", "alphapilot.")
                    ):
                        raise RehearsalV22ValidationError(
                            f"runtime local dynamic import is forbidden: {relative}"
                        )
        pending.extend(
            sorted(
                discovered - payloads.keys(),
                key=lambda value: value.encode("utf-8"),
            )
        )
    return dict(
        sorted(payloads.items(), key=lambda item: item[0].encode("utf-8"))
    )


def _unique_a_authority(
    root: Path,
    reference: Mapping[str, Any],
    *,
    require_worktree: bool,
) -> bytes:
    path = _relative(reference.get("path"), "authority path")
    creating_commit = _git_commit(root, reference.get("creating_commit"), "authority commit")
    expected_sha = _sha(reference.get("sha256"), "authority SHA")
    history = _git_bytes(
        root,
        "log",
        "--all",
        "--diff-merges=first-parent",
        "--format=@@%H",
        "--name-status",
        "--find-renames",
        "--find-copies",
        "--",
        path,
    ).decode("utf-8", errors="strict")
    touches: list[tuple[str, str, tuple[str, ...]]] = []
    active: str | None = None
    for raw in history.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("@@"):
            active = _commit(line[2:], "authority history commit")
            continue
        if active is None:
            raise RehearsalV22ValidationError("authority Git history is malformed")
        fields = tuple(line.split("\t"))
        if len(fields) < 2:
            raise RehearsalV22ValidationError("authority Git status is malformed")
        touches.append((active, fields[0], fields[1:]))
    if touches != [(creating_commit, "A", (path,))]:
        raise RehearsalV22ValidationError("authority is not one unique status-A Git touch")
    payload = _git_blob(root, creating_commit, path)
    if _sha256(payload) != expected_sha:
        raise RehearsalV22ValidationError("authority creation blob SHA drifted")
    if require_worktree:
        current = _regular_bytes(_safe_path(root, path, "authority worktree file"), "authority")
        if current != payload:
            raise RehearsalV22ValidationError("authority worktree bytes differ from creation blob")
    return payload


def _validate_implementation_review_authority(
    root: Path,
    reference: Mapping[str, Any],
    *,
    implementation_commit: str,
    execution_head: str,
    require_worktree: bool,
) -> bytes:
    """Validate a direct or merge-projected post-implementation review."""

    path = _relative(reference.get("path"), "implementation review path")
    creating_commit = _git_commit(
        root,
        reference.get("creating_commit"),
        "implementation review creating commit",
    )
    expected_sha = _sha(reference.get("sha256"), "implementation review SHA")
    reviewed_implementation = _git_commit(
        root,
        implementation_commit,
        "reviewed implementation commit",
    )
    head = _git_commit(root, execution_head, "implementation review execution HEAD")

    def parse_touches(history: str) -> list[tuple[str, str, tuple[str, ...]]]:
        touches: list[tuple[str, str, tuple[str, ...]]] = []
        active: str | None = None
        for raw in history.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("@@"):
                active = _commit(line[2:], "implementation review history commit")
                continue
            if active is None:
                raise RehearsalV22ValidationError(
                    "implementation review Git history is malformed"
                )
            fields = tuple(line.split("\t"))
            if len(fields) < 2:
                raise RehearsalV22ValidationError(
                    "implementation review Git status is malformed"
                )
            touches.append((active, fields[0], fields[1:]))
        return touches

    def first_parent_touches(start: str) -> list[tuple[str, str, tuple[str, ...]]]:
        return parse_touches(
            _git_bytes(
                root,
                "log",
                "--first-parent",
                "--diff-merges=first-parent",
                "--format=@@%H",
                "--name-status",
                "--find-renames",
                "--find-copies",
                start,
                "--",
                path,
            ).decode("utf-8", errors="strict")
        )

    def all_touches() -> list[tuple[str, str, tuple[str, ...]]]:
        return parse_touches(
            _git_bytes(
                root,
                "log",
                "--all",
                "--diff-merges=first-parent",
                "--format=@@%H",
                "--name-status",
                "--find-renames",
                "--find-copies",
                "--",
                path,
            ).decode("utf-8", errors="strict")
        )

    def path_diff(base: str, commit: str) -> tuple[str, ...]:
        return tuple(
            _git_bytes(
                root,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-status",
                "--no-renames",
                base,
                commit,
                "--",
                path,
            )
            .decode("utf-8", errors="strict")
            .splitlines()
        )

    if first_parent_touches(head) != [(creating_commit, "A", (path,))]:
        raise RehearsalV22ValidationError(
            "implementation review is not one first-parent status-A projection"
        )
    if not _git_is_ancestor(root, reviewed_implementation, creating_commit) or not (
        _git_is_ancestor(root, creating_commit, head)
    ):
        raise RehearsalV22ValidationError(
            "implementation review projection escaped implementation or execution lineage"
        )
    payload = _git_blob(root, creating_commit, path)
    if _sha256(payload) != expected_sha or _git_optional_blob(root, head, path) != payload:
        raise RehearsalV22ValidationError(
            "implementation review projection bytes or SHA drifted"
        )

    parents = _git_parents(root, creating_commit)
    if len(parents) == 1:
        source_review = creating_commit
        if _diff_name_status(root, parents[0], source_review) != (("A", path),):
            raise RehearsalV22ValidationError(
                "direct implementation review creation surface drifted"
            )
    elif len(parents) == 2:
        main_parent, review_parent = parents
        if (
            path_diff(main_parent, creating_commit) != (f"A\t{path}",)
            or path_diff(review_parent, creating_commit) != ()
            or _git_optional_blob(root, review_parent, path) != payload
        ):
            raise RehearsalV22ValidationError(
                "implementation review merge projection topology drifted"
            )
        branch_touches = first_parent_touches(review_parent)
        if len(branch_touches) != 1 or branch_touches[0][1:] != ("A", (path,)):
            raise RehearsalV22ValidationError(
                "implementation review source branch is not one status-A history"
            )
        source_review = _git_commit(
            root,
            branch_touches[0][0],
            "source implementation review commit",
        )
        source_parents = _git_parents(root, source_review)
        if (
            len(source_parents) != 1
            or _diff_name_status(root, source_parents[0], source_review)
            != (("A", path),)
            or _git_blob(root, source_review, path) != payload
            or not _git_is_ancestor(root, reviewed_implementation, source_review)
            or not _git_is_ancestor(root, source_review, review_parent)
        ):
            raise RehearsalV22ValidationError(
                "implementation review source commit or preserved bytes drifted"
            )
    else:
        raise RehearsalV22ValidationError(
            "implementation review projection is neither direct nor two-parent landing"
        )
    if not _git_is_ancestor(root, reviewed_implementation, source_review):
        raise RehearsalV22ValidationError(
            "implementation review predates its reviewed implementation"
        )
    expected_global_touches = {(creating_commit, "A", (path,))}
    if source_review != creating_commit:
        expected_global_touches.add((source_review, "A", (path,)))
    global_touches = all_touches()
    if len(global_touches) != len(expected_global_touches) or set(global_touches) != (
        expected_global_touches
    ):
        raise RehearsalV22ValidationError(
            "implementation review has a non-projection global Git touch"
        )
    if require_worktree:
        current = _regular_bytes(
            _safe_path(root, path, "implementation review worktree file"),
            "implementation review worktree file",
        )
        if current != payload:
            raise RehearsalV22ValidationError(
                "implementation review worktree bytes differ from projection"
            )
    return payload


def _validate_initial_sibling_authority(
    root: Path,
    reference: Mapping[str, Any],
    *,
    execution_head: str,
    require_worktree: bool,
) -> bytes:
    """Validate the fixed b21 sibling without counting its merge projection twice."""

    if not isinstance(require_worktree, bool):
        raise RehearsalV22ValidationError(
            "initial sibling worktree requirement is not one exact boolean"
        )
    path = INDEPENDENT_REVIEW_RELATIVE.as_posix()
    expected_reference = {
        "path": path,
        "sha256": INDEPENDENT_REVIEW_SHA256,
        "creating_commit": INDEPENDENT_REVIEW_COMMIT,
        "unique_a_history_verified": True,
    }
    _require_equal(reference, expected_reference, "initial sibling authority reference")
    head = _git_commit(root, execution_head, "initial sibling execution HEAD")
    if _git_parents(root, INDEPENDENT_REVIEW_COMMIT) != (INITIAL_REVIEWED_COMMIT,):
        raise RehearsalV22ValidationError("initial sibling authority parent drifted")
    if _diff_name_status(
        root,
        INITIAL_REVIEWED_COMMIT,
        INDEPENDENT_REVIEW_COMMIT,
    ) != (("A", path),):
        raise RehearsalV22ValidationError("initial sibling authority creation diff drifted")
    payload = _git_blob(root, INDEPENDENT_REVIEW_COMMIT, path)
    if _sha256(payload) != INDEPENDENT_REVIEW_SHA256:
        raise RehearsalV22ValidationError("initial sibling authority creation SHA drifted")

    graph: dict[str, tuple[str, ...]] = {}
    rows = _git_bytes(root, "rev-list", "--all", "--children").decode(
        "ascii", errors="strict"
    )
    for raw in rows.splitlines():
        fields = tuple(raw.split())
        if not fields:
            continue
        commit = _commit(fields[0], "initial sibling graph commit")
        children = tuple(
            _commit(value, "initial sibling graph child") for value in fields[1:]
        )
        if commit in graph or len(set(children)) != len(children):
            raise RehearsalV22ValidationError("initial sibling authority graph is malformed")
        graph[commit] = children
    if INDEPENDENT_REVIEW_COMMIT not in graph or head not in graph:
        raise RehearsalV22ValidationError("initial sibling authority graph is incomplete")
    if any(child not in graph for children in graph.values() for child in children):
        raise RehearsalV22ValidationError("initial sibling authority graph is incomplete")

    descendants = {INDEPENDENT_REVIEW_COMMIT}
    pending = [INDEPENDENT_REVIEW_COMMIT]
    while pending:
        commit = pending.pop()
        for child in graph[commit]:
            if child not in descendants:
                descendants.add(child)
                pending.append(child)
    if head not in descendants:
        raise RehearsalV22ValidationError(
            "initial sibling authority is outside the execution-head lineage"
        )
    for commit in sorted(graph):
        observed = _git_optional_blob(root, commit, path)
        if commit in descendants:
            if observed != payload:
                raise RehearsalV22ValidationError(
                    "initial sibling authority bytes drifted in its descendant lineage"
                )
        elif observed is not None:
            raise RehearsalV22ValidationError(
                "initial sibling authority path exists outside its descendant lineage"
            )
    if _git_optional_blob(root, head, path) != payload:
        raise RehearsalV22ValidationError("initial sibling execution-head bytes drifted")
    worktree_path = root.joinpath(*PurePosixPath(path).parts)
    if require_worktree and _validator_os.path.lexists(worktree_path):
        current = _regular_bytes(
            _safe_path(root, path, "initial sibling authority worktree file"),
            "initial sibling authority worktree file",
        )
        if current != payload:
            raise RehearsalV22ValidationError(
                "initial sibling authority worktree bytes drifted"
            )
    return payload


def _unique_a_unserialized(
    root: Path,
    *,
    path: str,
    execution_head: str,
) -> tuple[str, bytes]:
    """Derive one globally unique create-only authority and bind it to HEAD."""

    relative = _relative(path, "first-parent authority path")
    head = _git_commit(root, execution_head, "first-parent execution HEAD")
    history = _git_bytes(
        root,
        "log",
        "--all",
        "--diff-merges=first-parent",
        "--format=@@%H",
        "--name-status",
        "--find-renames",
        "--find-copies",
        "--",
        relative,
    ).decode("utf-8", errors="strict")
    touches: list[tuple[str, str, tuple[str, ...]]] = []
    active: str | None = None
    for raw in history.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("@@"):
            active = _commit(line[2:], "first-parent authority commit")
            continue
        if active is None:
            raise RehearsalV22ValidationError("first-parent authority history is malformed")
        fields = tuple(line.split("\t"))
        if len(fields) < 2:
            raise RehearsalV22ValidationError("first-parent authority status is malformed")
        touches.append((active, fields[0], fields[1:]))
    if len(touches) != 1 or touches[0][1:] != ("A", (relative,)):
        raise RehearsalV22ValidationError(
            "authority is not one globally unique status-A Git touch"
        )
    creating_commit = _git_commit(root, touches[0][0], "authority creating commit")
    if not _git_is_ancestor(root, creating_commit, head):
        raise RehearsalV22ValidationError(
            "authority creation commit is outside the execution-head lineage"
        )
    payload = _git_blob(root, creating_commit, relative)
    current = _regular_bytes(
        _safe_path(root, relative, "first-parent authority worktree file"),
        "first-parent authority worktree file",
    )
    if current != payload:
        raise RehearsalV22ValidationError(
            "first-parent authority current bytes differ from creation blob"
        )
    return creating_commit, payload


def _parse_git_name_status_z(output: bytes) -> tuple[tuple[str, str], ...]:
    """Parse raw Git name-status pairs without imposing evidence-path grammar."""

    if not output:
        return ()
    fields = output.split(b"\x00")
    if fields[-1] != b"" or len(fields[:-1]) % 2 != 0:
        raise RehearsalV22ValidationError("implementation Git surface is malformed")
    result: list[tuple[str, str]] = []
    payload_fields = fields[:-1]
    for index in range(0, len(payload_fields), 2):
        raw_status, raw_path = payload_fields[index : index + 2]
        try:
            status_value = raw_status.decode("ascii", errors="strict")
            relative = raw_path.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise RehearsalV22ValidationError(
                "implementation Git surface is malformed"
            ) from None
        if status_value not in {"A", "M", "D", "T", "U", "X", "B"}:
            raise RehearsalV22ValidationError("implementation Git surface is malformed")
        safe_relative = _git_safe_relative(relative, "implementation surface path")
        result.append((status_value, safe_relative))
    return tuple(result)


def _diff_name_status(root: Path, base: str, commit: str) -> tuple[tuple[str, str], ...]:
    return _parse_git_name_status_z(
        _git_bytes(
            root,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "--no-renames",
            "-z",
            base,
            commit,
            "--",
        )
    )


def _root_diff_name_status(root: Path, commit: str) -> tuple[tuple[str, str], ...]:
    return _parse_git_name_status_z(
        _git_bytes(
            root,
            "diff-tree",
            "--root",
            "--no-commit-id",
            "-r",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "--no-renames",
            "-z",
            commit,
            "--",
        )
    )


def _path_history_touches(
    root: Path,
    *,
    relative: str,
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    path = _relative(relative, "historical path touch")
    history = _git_bytes(
        root,
        "log",
        "--all",
        "--diff-merges=first-parent",
        "--format=@@%H",
        "--name-status",
        "--find-renames",
        "--find-copies",
        "--",
        path,
    ).decode("utf-8", errors="strict")
    touches: list[tuple[str, str, tuple[str, ...]]] = []
    active: str | None = None
    for raw in history.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("@@"):
            active = _commit(line[2:], "historical path touch commit")
            continue
        if active is None:
            raise RehearsalV22ValidationError("historical path touch record is malformed")
        fields = tuple(line.split("\t"))
        if len(fields) < 2:
            raise RehearsalV22ValidationError("historical path touch status is malformed")
        touches.append((active, fields[0], fields[1:]))
    return tuple(touches)


def _path_diff_name_status(
    root: Path,
    *,
    base: str,
    commit: str,
    relative: str,
) -> tuple[tuple[str, str], ...]:
    path = _relative(relative, "historical projected path")
    output = _git_bytes(
        root,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--name-status",
        "--no-renames",
        base,
        commit,
        "--",
        path,
    ).decode("utf-8", errors="strict")
    rows: list[tuple[str, str]] = []
    for raw in output.splitlines():
        fields = raw.split("\t")
        if len(fields) != 2:
            raise RehearsalV22ValidationError("historical projected path diff is malformed")
        rows.append((fields[0], _relative(fields[1], "historical projected path diff")))
    return tuple(rows)


_EPOCH_SURFACE_PATH = re.compile(
    r"^docs/phase4/reports/P4\.2a-v2-2-epoch([0-9]+)-surface-authority-"
    r"[0-9]{8}\.json$"
)
_EPOCH_REVIEW_PATH = re.compile(
    r"^docs/phase4/reports/P4\.2a-v2-2-epoch([0-9]+)(?:-[A-Za-z0-9-]+)?-"
    r"implementation-independent-review-[0-9]{8}\.json$"
)
_EPOCH_LANDING_PATH = re.compile(
    r"^docs/phase4/reports/P4\.2a-v2-2-epoch([0-9]+)-.*landing-report-"
    r"[0-9]{8}\.json$"
)


def _first_parent_chain(root: Path, head: str) -> tuple[str, ...]:
    rows = _git_bytes(root, "rev-list", "--first-parent", "--reverse", head).decode(
        "ascii", errors="strict"
    )
    chain = tuple(_commit(row, "first-parent commit") for row in rows.splitlines())
    if not chain or chain[-1] != head or len(set(chain)) != len(chain):
        raise RehearsalV22ValidationError("execution first-parent chain is malformed")
    return chain


def _unique_a_review_commit_all_history(root: Path, relative: str) -> str:
    """Resolve one review's source creation without counting merge projections."""

    path = _relative(relative, "landed epoch review path")
    history = _git_bytes(
        root,
        "log",
        "--all",
        "--format=@@%H",
        "--name-status",
        "--find-renames",
        "--find-copies",
        "--",
        path,
    ).decode("utf-8", errors="strict")
    touches: list[tuple[str, str, tuple[str, ...]]] = []
    active: str | None = None
    for raw in history.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("@@"):
            active = _commit(line[2:], "landed epoch review history commit")
            continue
        if active is None:
            raise RehearsalV22ValidationError(
                "landed epoch review history is malformed"
            )
        fields = tuple(line.split("\t"))
        if len(fields) < 2:
            raise RehearsalV22ValidationError(
                "landed epoch review history is malformed"
            )
        touches.append((active, fields[0], fields[1:]))
    if len(touches) != 1 or touches[0][1:] != ("A", (path,)):
        raise RehearsalV22ValidationError(
            "landed epoch review is not unique-A across all refs"
        )
    return touches[0][0]


def _validate_first_parent_epoch_governance(
    *,
    root: Path,
    chain: tuple[str, ...],
) -> tuple[
    tuple[tuple[int, str, str], ...],
    tuple[tuple[int, str, str, str], ...],
    tuple[tuple[int, str, str, str, str], ...],
]:
    """Strictly validate every matching authority, review and landing document."""

    authorities: list[tuple[int, str, str]] = []
    reviews: list[tuple[int, str, str, str]] = []
    landings: list[tuple[int, str, str, str, str]] = []
    observed_paths: set[str] = set()
    for commit in chain:
        parents = _git_parents(root, commit)
        changes = (
            _diff_name_status(root, parents[0], commit)
            if parents
            else _root_diff_name_status(root, commit)
        )
        for status_value, relative in changes:
            matches = tuple(
                (kind, match)
                for kind, pattern in (
                    ("authority", _EPOCH_SURFACE_PATH),
                    ("review", _EPOCH_REVIEW_PATH),
                    ("landing", _EPOCH_LANDING_PATH),
                )
                if (match := pattern.fullmatch(relative)) is not None
            )
            if not matches:
                continue
            if len(matches) != 1 or status_value != "A" or relative in observed_paths:
                raise RehearsalV22ValidationError(
                    "epoch governance artifact is not one unique first-parent add"
                )
            observed_paths.add(relative)
            kind, match = matches[0]
            epoch_number = int(match.group(1))
            payload = _git_blob(root, commit, relative)
            document = _object(
                strict_json_loads(payload, label=f"landed epoch {kind} {relative}"),
                f"landed epoch {kind} {relative}",
            )
            if kind == "authority":
                label = f"landed epoch authority {relative}"
                _require_exact_keys(
                    document,
                    frozenset(
                        {
                            "schema_version",
                            "verdict",
                            "owner",
                            "implementation_epoch",
                            "base_commit",
                            "exact_surface",
                        }
                    ),
                    label,
                )
                _require_equal(
                    document.get("schema_version"),
                    "p4.2a-v2-2-implementation-epoch-surface-authorization-v1",
                    f"{label} schema",
                )
                _require_equal(
                    document.get("verdict"),
                    "APPROVE_EXACT_V2_2_IMPLEMENTATION_EPOCH_SURFACE",
                    f"{label} verdict",
                )
                _require_equal(
                    document.get("owner"),
                    {"identity": "ouyang", "approved": True},
                    f"{label} owner",
                )
                _require_equal(
                    _integer(document.get("implementation_epoch"), f"{label} epoch"),
                    epoch_number,
                    f"{label} filename/document epoch",
                )
                _commit(document.get("base_commit"), f"{label} base commit")
                rows = _array(document.get("exact_surface"), f"{label} exact surface")
                if not rows:
                    raise RehearsalV22ValidationError(
                        "landed epoch authority exact surface is empty"
                    )
                observed_surface: dict[str, str] = {}
                ordered_paths: list[str] = []
                for index, raw_row in enumerate(rows):
                    row_label = f"{label} exact surface row {index}"
                    row = _object(raw_row, row_label)
                    _require_exact_keys(
                        row,
                        frozenset({"path", "status"}),
                        row_label,
                    )
                    path = _relative(row.get("path"), f"{row_label} path")
                    row_status = _string(row.get("status"), f"{row_label} status")
                    if (
                        path not in IMPLEMENTATION_PATHS
                        or row_status not in {"A", "M"}
                        or path in observed_surface
                    ):
                        raise RehearsalV22ValidationError(
                            "landed epoch authority exact surface escaped allowlist"
                        )
                    observed_surface[path] = row_status
                    ordered_paths.append(path)
                if ordered_paths != sorted(
                    ordered_paths, key=lambda value: value.encode("utf-8")
                ):
                    raise RehearsalV22ValidationError(
                        "landed epoch authority exact surface is not byte-sorted"
                    )
                authorities.append((epoch_number, commit, relative))
                continue
            if kind == "review":
                label = f"landed epoch review {relative}"
                reviewed_commit = _commit(
                    document.get("reviewed_commit"), f"{label} reviewed commit"
                )
                creation_commit = _unique_a_review_commit_all_history(root, relative)
                if (
                    document.get("schema_version") != "p4.2a-independent-review-v1"
                    or document.get("verdict")
                    != f"APPROVE_EPOCH{epoch_number}_IMPLEMENTATION"
                    or document.get("blockers") != []
                    or not _git_is_ancestor(root, creation_commit, chain[-1])
                    or _git_blob(root, creation_commit, relative) != payload
                ):
                    raise RehearsalV22ValidationError(
                        "landed epoch review shape or creation binding drifted"
                    )
                reviews.append(
                    (epoch_number, creation_commit, relative, reviewed_commit)
                )
                continue

            label = f"landed epoch landing {relative}"
            schema = _string(document.get("schema_version"), f"{label} schema")
            lineage = _object(
                document.get("candidate_lineage"), f"{label} candidate lineage"
            )
            planned = _object(document.get("planned_landing"), f"{label} plan")
            implementation_commit = _commit(
                lineage.get("implementation_commit"),
                f"{label} implementation commit",
            )
            review_row = _object(
                lineage.get("independent_review"), f"{label} review"
            )
            review_path = _relative(review_row.get("path"), f"{label} review path")
            review_match = _EPOCH_REVIEW_PATH.fullmatch(review_path)
            review_commit_keys = tuple(
                key for key in ("creating_commit", "commit") if key in review_row
            )
            if len(review_commit_keys) != 1:
                raise RehearsalV22ValidationError(
                    "landed epoch landing review commit alias is ambiguous"
                )
            review_commit = _commit(
                review_row[review_commit_keys[0]],
                f"{label} review commit",
            )
            second_parent = _commit(
                planned.get("second_parent"), f"{label} second parent"
            )
            review_creation_commit = _unique_a_review_commit_all_history(
                root, review_path
            )
            review_creation_payload = _git_blob(
                root, review_creation_commit, review_path
            )
            claimed_review_sha = review_row.get("sha256")
            if claimed_review_sha is not None and _sha(
                claimed_review_sha, f"{label} review SHA"
            ) != _sha256(review_creation_payload):
                raise RehearsalV22ValidationError(
                    "landed epoch landing review digest drifted"
                )
            if (
                re.fullmatch(
                    rf"p4\.2a-v2-2-epoch{epoch_number}(?:-[A-Za-z0-9-]+)?-"
                    r"registered-gate-landing-report-v[0-9]+",
                    schema,
                )
                is None
                or document.get("status")
                != "PASS_REGISTERED_GATE_LANDING_REPORT_READY_BEFORE_MERGE"
                or review_match is None
                or int(review_match.group(1)) != epoch_number
                or second_parent != review_commit
                or review_creation_commit != review_commit
            ):
                raise RehearsalV22ValidationError(
                    "landed epoch landing shape or review binding drifted"
                )
            landings.append(
                (
                    epoch_number,
                    commit,
                    relative,
                    implementation_commit,
                    review_commit,
                )
            )
    review_bindings = {
        (epoch, review_commit, reviewed_commit)
        for epoch, review_commit, _path, reviewed_commit in reviews
    }
    for epoch, _commit_value, _path, implementation_commit, review_commit in landings:
        if (epoch, review_commit, implementation_commit) not in review_bindings:
            raise RehearsalV22ValidationError(
                "landed epoch landing review target binding drifted"
            )
    return tuple(authorities), tuple(reviews), tuple(landings)


def _validate_latest_landed_epoch(
    *,
    project_root: Path,
    live_anchor: implementation.LiveExecutionAnchor,
) -> None:
    """Independently prove that the live anchor is the latest landed epoch chain."""

    root = project_root.resolve(strict=True)
    execution_head = _git_commit(root, live_anchor.execution_head, "live execution HEAD")
    observed_head = _git_bytes(root, "rev-parse", "HEAD").decode(
        "ascii", errors="strict"
    ).strip()
    _require_equal(execution_head, observed_head, "live execution HEAD")
    owner = _authority_reference_json(
        live_anchor.owner_surface_authorization,
        "latest epoch surface authority",
    )
    review = _authority_reference_json(
        live_anchor.independent_implementation_review,
        "latest epoch independent review",
    )
    landing_report = _authority_reference_json(
        live_anchor.landing_report,
        "latest epoch landing report",
    )
    owner_payload = _unique_a_authority(root, owner, require_worktree=True)
    owner_document = _object(
        strict_json_loads(owner_payload, label="latest epoch surface authority"),
        "latest epoch surface authority",
    )
    _require_exact_keys(
        owner_document,
        frozenset(
            {
                "schema_version",
                "verdict",
                "owner",
                "implementation_epoch",
                "base_commit",
                "exact_surface",
            }
        ),
        "latest epoch surface authority",
    )
    _require_equal(
        owner_document.get("schema_version"),
        "p4.2a-v2-2-implementation-epoch-surface-authorization-v1",
        "latest epoch surface authority schema",
    )
    _require_equal(
        owner_document.get("verdict"),
        "APPROVE_EXACT_V2_2_IMPLEMENTATION_EPOCH_SURFACE",
        "latest epoch surface authority verdict",
    )
    _require_equal(
        owner_document.get("owner"),
        {"identity": "ouyang", "approved": True},
        "latest epoch surface authority owner",
    )
    _require_equal(
        owner_document.get("implementation_epoch"),
        live_anchor.execution_epoch,
        "latest epoch number",
    )
    implementation_commit = _git_commit(
        root,
        live_anchor.implementation_commit,
        "latest epoch implementation commit",
    )
    if _git_parents(root, implementation_commit) != (cast(str, owner["creating_commit"]),):
        raise RehearsalV22ValidationError(
            "latest epoch implementation is not the authority's direct child"
        )
    base_commit = _git_commit(
        root,
        owner_document.get("base_commit"),
        "latest epoch surface-authority base",
    )
    if (
        _git_parents(root, cast(str, owner["creating_commit"])) != (base_commit,)
        or _diff_name_status(root, base_commit, cast(str, owner["creating_commit"]))
        != (("A", cast(str, owner["path"])),)
    ):
        raise RehearsalV22ValidationError(
            "latest epoch surface authority is not one exact unique-A child"
        )
    rows = _array(owner_document.get("exact_surface"), "latest epoch exact surface")
    expected_surface: dict[str, str] = {}
    ordered_paths: list[str] = []
    for index, raw in enumerate(rows):
        row = _object(raw, f"latest epoch surface row {index}")
        _require_exact_keys(
            row,
            frozenset({"path", "status"}),
            f"latest epoch surface row {index}",
        )
        relative = _relative(row.get("path"), f"latest epoch surface row {index} path")
        status_value = _string(
            row.get("status"), f"latest epoch surface row {index} status"
        )
        if (
            relative not in IMPLEMENTATION_PATHS
            or status_value not in {"A", "M"}
            or relative in expected_surface
        ):
            raise RehearsalV22ValidationError("latest epoch exact surface escaped allowlist")
        expected_surface[relative] = status_value
        ordered_paths.append(relative)
    if (
        not expected_surface
        or ordered_paths
        != sorted(ordered_paths, key=lambda value: value.encode("utf-8"))
        or {
            relative: status_value
            for status_value, relative in _diff_name_status(
                root,
                cast(str, owner["creating_commit"]),
                implementation_commit,
            )
        }
        != expected_surface
    ):
        raise RehearsalV22ValidationError("latest epoch exact implementation surface drifted")
    review_commit = _git_commit(
        root,
        review["creating_commit"],
        "latest epoch source independent review",
    )
    review_path = cast(str, review["path"])
    if (
        _git_parents(root, review_commit) != (implementation_commit,)
        or _diff_name_status(root, implementation_commit, review_commit)
        != (("A", review_path),)
    ):
        raise RehearsalV22ValidationError("latest epoch source review topology drifted")
    review_payload = _git_blob(root, review_commit, review_path)
    if (
        _sha256(review_payload) != review["sha256"]
        or _regular_bytes(
            _safe_path(root, review_path, "latest epoch review worktree file"),
            "latest epoch review worktree file",
        )
        != review_payload
    ):
        raise RehearsalV22ValidationError("latest epoch source review bytes drifted")
    _validate_implementation_review_document(
        document=_object(
            strict_json_loads(review_payload, label="latest epoch independent review"),
            "latest epoch independent review",
        ),
        implementation_commit=implementation_commit,
        label="latest epoch independent review",
    )
    merge_commit = _git_commit(root, live_anchor.merge_commit, "latest epoch landing merge")
    merge_parents = _git_parents(root, merge_commit)
    if (
        len(merge_parents) != 2
        or merge_parents[1] != review_commit
        or not _git_is_ancestor(root, implementation_commit, merge_commit)
    ):
        raise RehearsalV22ValidationError("latest epoch merge topology drifted")
    landing_payload = _unique_a_authority(root, landing_report, require_worktree=True)
    landing_commit = cast(str, landing_report["creating_commit"])
    if (
        _git_parents(root, landing_commit) != (merge_commit,)
        or _diff_name_status(root, merge_commit, landing_commit)
        != (("A", cast(str, landing_report["path"])),)
    ):
        raise RehearsalV22ValidationError(
            "latest epoch landing report is not one exact unique-A merge child"
        )
    _object(
        strict_json_loads(landing_payload, label="latest epoch landing report"),
        "latest epoch landing report",
    )
    chain = _first_parent_chain(root, execution_head)
    if landing_commit not in chain or merge_commit not in chain:
        raise RehearsalV22ValidationError(
            "latest epoch landing chain is outside first-parent execution history"
        )

    authorities, matching_reviews, matching_landings = (
        _validate_first_parent_epoch_governance(root=root, chain=chain)
    )
    surface_authorities: dict[int, tuple[str, str]] = {}
    for epoch_number, commit, relative in authorities:
        if epoch_number in surface_authorities:
            raise RehearsalV22ValidationError("duplicate landed surface-authority epoch")
        surface_authorities[epoch_number] = (commit, relative)
    if (
        not surface_authorities
        or max(surface_authorities) != live_anchor.execution_epoch
        or surface_authorities.get(live_anchor.execution_epoch)
        != (cast(str, owner["creating_commit"]), cast(str, owner["path"]))
    ):
        raise RehearsalV22ValidationError("live execution epoch is not latest landed authority")
    if (
        any(
            epoch_number > live_anchor.execution_epoch
            for epoch_number, *_rest in matching_reviews
        )
        or any(
            epoch_number > live_anchor.execution_epoch
            for epoch_number, *_rest in matching_landings
        )
        or tuple(
            row
            for row in matching_reviews
            if row[0] == live_anchor.execution_epoch
        )
        != (
            (
                live_anchor.execution_epoch,
                review_commit,
                cast(str, review["path"]),
                implementation_commit,
            ),
        )
        or tuple(
            row
            for row in matching_landings
            if row[0] == live_anchor.execution_epoch
        )
        != (
            (
                live_anchor.execution_epoch,
                landing_commit,
                cast(str, landing_report["path"]),
                implementation_commit,
                review_commit,
            ),
        )
    ):
        raise RehearsalV22ValidationError(
            "live execution epoch is not the unique latest complete governance chain"
        )

    control_paths = {
        cast(str, record["repository_path"])
        for record in live_anchor.control_surface.records
        if record.get("repository_path") is not None
    }
    start = chain.index(merge_commit) + 1
    for commit in chain[start:]:
        parent = _git_parents(root, commit)[0]
        changed = {relative for _status, relative in _diff_name_status(root, parent, commit)}
        overlap = sorted(changed & control_paths, key=lambda value: value.encode("utf-8"))
        if overlap:
            raise RehearsalV22ValidationError(
                "post-landing control member changed: " + ", ".join(overlap)
            )


def _validate_live_execution_anchor(
    *,
    project_root: Path,
    live_anchor: implementation.LiveExecutionAnchor,
) -> None:
    anchor = _live_execution_anchor(live_anchor)
    _validate_latest_landed_epoch(project_root=project_root, live_anchor=anchor)
    _validate_current_control_and_modules(project_root=project_root, live_anchor=anchor)


def _validate_current_execution_anchor(
    *,
    project_root: Path,
    live_anchor: implementation.LiveExecutionAnchor,
) -> None:
    """Validate active code bytes without asserting post-selection landing state."""

    anchor = _live_execution_anchor(live_anchor)
    _validate_current_control_and_modules(project_root=project_root, live_anchor=anchor)
    implementation.validate_implementation_epoch(
        project_root,
        epoch=anchor.execution_epoch,
        implementation_commit=anchor.implementation_commit,
        owner_surface_authorization=anchor.owner_surface_authorization,
        independent_review=anchor.independent_implementation_review,
        control_merkle_root_sha256=anchor.control_surface.merkle_root_sha256,
        execution_head=anchor.execution_head,
        require_current_bytes=True,
    )


def _validate_current_control_and_modules(
    *,
    project_root: Path,
    live_anchor: implementation.LiveExecutionAnchor,
) -> None:
    """Common current-byte half of both ordinary and latest-landed anchors."""

    anchor = _live_execution_anchor(live_anchor)
    observed = implementation.build_control_surface(
        project_root,
        anchor.implementation_commit,
        require_current=True,
    )
    if (
        observed != anchor.control_surface
        or observed.implementation_commit != anchor.implementation_commit
    ):
        raise RehearsalV22ValidationError("live execution control surface drifted")
    _validate_module_identity(project_root, anchor)


def _schema_validate(document: JsonObject, schema: JsonObject, label: str) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise RehearsalV22ValidationError(f"{label} schema is not Draft 2020-12") from exc
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        first = errors[0]
        pointer = "/" + "/".join(str(part) for part in first.absolute_path)
        raise RehearsalV22ValidationError(
            f"{label} failed JSON Schema at {pointer}: {first.message}"
        )


def _digest_bytes(value: object, label: str) -> bytes:
    return bytes.fromhex(_sha(value, label))


def _u64(value: int, label: str) -> bytes:
    if value < 0 or value >= 1 << 64:
        raise RehearsalV22ValidationError(f"{label} is outside u64")
    return value.to_bytes(8, "big")


def _merkle_from_digests(digests: Sequence[bytes], *, node_domain: bytes) -> str:
    if not digests:
        raise RehearsalV22ValidationError("non-evidence Merkle tree is empty")
    current = list(digests)
    while len(current) > 1:
        next_level: list[bytes] = []
        for offset in range(0, len(current), 2):
            left = current[offset]
            right = current[offset + 1] if offset + 1 < len(current) else left
            next_level.append(hashlib.sha256(node_domain + left + right).digest())
        current = next_level
    return current[0].hex()


def _path_merkle(
    payloads: Mapping[str, bytes],
    *,
    leaf_domain: bytes,
    node_domain: bytes = b"p4.2a-rehearsal-node-v2.2\0",
) -> str:
    ordered = sorted(payloads.items(), key=lambda item: item[0].encode("utf-8"))
    leaves = [
        hashlib.sha256(
            leaf_domain
            + relative.encode("utf-8")
            + b"\0"
            + hashlib.sha256(payload).digest()
        ).digest()
        for relative, payload in ordered
    ]
    return _merkle_from_digests(leaves, node_domain=node_domain)


def _generic_merkle_root(payloads: Mapping[str, bytes]) -> str:
    """Independently rebuild the registered run/control Merkle root."""

    return _path_merkle(
        payloads,
        leaf_domain=b"p4.2a-rehearsal-leaf-v2.2\0",
        node_domain=b"p4.2a-rehearsal-node-v2.2\0",
    )


def _evidence_root(payloads: Mapping[str, bytes]) -> str:
    if not payloads:
        return hashlib.sha256(b"p4.2a-rehearsal-v2.2-evidence-empty-v1\0").hexdigest()
    ordered = sorted(payloads.items(), key=lambda item: item[0].encode("utf-8"))
    leaves = [
        hashlib.sha256(
            b"p4.2a-rehearsal-v2.2-evidence-leaf-v1\0"
            + relative.encode("utf-8")
            + b"\0"
            + hashlib.sha256(payload).digest()
        ).digest()
        for relative, payload in ordered
    ]
    return _merkle_from_digests(
        leaves,
        node_domain=b"p4.2a-rehearsal-v2.2-evidence-node-v1\0",
    )


def _history_empty_root() -> str:
    return hashlib.sha256(b"p4.2a-rehearsal-v2.2-history-empty-v1\0").hexdigest()


def _attempt_token(
    *,
    series_token: str,
    ordinal: int,
    implementation_commit: str,
    previous_history_root: str,
) -> str:
    return hashlib.sha256(
        b"p4.2a-rehearsal-v2.2-attempt-v1\0"
        + bytes.fromhex(series_token)
        + _u64(ordinal, "attempt ordinal")
        + bytes.fromhex(implementation_commit)
        + bytes.fromhex(previous_history_root)
    ).hexdigest()


def _attempt_record_root(
    *,
    ordinal: int,
    attempt_token: str,
    started_sha256: str,
    candidate_sha256: str | None,
    terminal_sha256: str | None,
    evidence_tree_root: str,
) -> str:
    return hashlib.sha256(
        b"p4.2a-rehearsal-v2.2-attempt-record-v1\0"
        + _u64(ordinal, "record ordinal")
        + bytes.fromhex(attempt_token)
        + bytes.fromhex(started_sha256)
        + (bytes.fromhex(candidate_sha256) if candidate_sha256 is not None else _ZERO32)
        + (bytes.fromhex(terminal_sha256) if terminal_sha256 is not None else _ZERO32)
        + bytes.fromhex(evidence_tree_root)
    ).hexdigest()


def _history_step(previous_root: str, record_root: str) -> str:
    return hashlib.sha256(
        b"p4.2a-rehearsal-v2.2-history-step-v1\0"
        + bytes.fromhex(previous_root)
        + bytes.fromhex(record_root)
    ).hexdigest()


def _candidate_content_root(
    *,
    previous_history_root: str,
    run_a_root: str,
    run_b_root: str,
    control_root: str,
    evidence_root: str,
) -> str:
    return hashlib.sha256(
        b"p4.2a-rehearsal-v2.2-candidate-content-v1\0"
        + bytes.fromhex(previous_history_root)
        + bytes.fromhex(run_a_root)
        + bytes.fromhex(run_b_root)
        + bytes.fromhex(control_root)
        + bytes.fromhex(evidence_root)
    ).hexdigest()


def _command_sha256(argv: Sequence[str]) -> str:
    if not argv or any(not isinstance(value, str) or not value for value in argv):
        raise RehearsalV22ValidationError("exact argv must contain nonempty strings")
    return hashlib.sha256(
        b"p4.2a-v2.2-argv-v1\0" + b"\0".join(value.encode("utf-8") for value in argv)
    ).hexdigest()


def _environment_sha256(environment: Mapping[str, str]) -> str:
    payload = bytearray(b"p4.2a-v2.2-env-v1\0")
    for key in sorted(environment, key=lambda value: value.encode("utf-8")):
        value = environment[key]
        payload.extend(key.encode("utf-8"))
        payload.append(0)
        payload.extend(value.encode("utf-8"))
        payload.append(0)
    return hashlib.sha256(bytes(payload)).hexdigest()


def registered_rehearsal_directory(project_root: Path = PROJECT_ROOT) -> Path:
    return project_root.resolve() / REGISTERED_DESTINATION_RELATIVE


def registered_series_ledger(project_root: Path = PROJECT_ROOT) -> Path:
    root = project_root.resolve()
    destination = registered_rehearsal_directory(root).absolute()
    token = hashlib.sha256(
        (INCIDENT_SHA256 + "\0" + REHEARSAL_ID + "\0" + destination.as_posix()).encode(
            "utf-8"
        )
    ).hexdigest()
    return root.parent / f".alphapilot-p4-2a-v2-2-execution-claim-{token}"


@dataclass(frozen=True)
class BindingView:
    mode: str
    project_root: Path
    absolute_destination: Path
    series_token_sha256: str
    ledger_root: Path


@dataclass(frozen=True)
class ResolvedExecution:
    view: BindingView
    raw: implementation.ExecutionBinding


@dataclass(frozen=True)
class HistoryReplay:
    records: tuple[JsonObject, ...]
    source_records: tuple[tuple[JsonObject, JsonObject | None, JsonObject | None], ...]
    started_count: int
    failed_count: int
    incomplete_count: int
    selected_attempt_ordinal: int
    selected_implementation_epoch: int
    selected_implementation_commit: str
    history_root_sha256: str
    live_ledger_root_sha256: str
    archive_merkle_root_sha256: str
    live_payloads: Mapping[str, bytes]
    archive_payloads: Mapping[str, bytes]


class ValidationMode(Enum):
    """Exact validation capability; passive modes can never request replay."""

    ORDINARY_ACTIVE = "ordinary-active"
    RECOVERY_PASSIVE = "recovery-passive"
    RECOVERED_RELEASE_PASSIVE = "recovered-release-passive"


def _binding_view(value: object) -> BindingView:
    if not isinstance(value, implementation.ExecutionBinding):
        raise RehearsalV22ValidationError("implementation execution binding is malformed")
    try:
        mode = _string(value.mode, "execution binding mode")
        project_root = value.project_root.resolve(strict=True)
        destination = value.destination.absolute()
        token = _sha(value.series_token_sha256, "execution series token")
        ledger = value.ledger_root.absolute()
    except OSError as exc:
        raise RehearsalV22ValidationError("implementation execution binding is malformed") from exc
    if mode not in {"REGISTERED_OFFICIAL", "DISPOSABLE_FULL_SHAPE_TEST"}:
        raise RehearsalV22ValidationError("execution binding mode is unknown")
    expected_destination = project_root / REGISTERED_DESTINATION_RELATIVE
    expected_token = hashlib.sha256(
        (
            INCIDENT_SHA256
            + "\0"
            + REHEARSAL_ID
            + "\0"
            + expected_destination.absolute().as_posix()
        ).encode("utf-8")
    ).hexdigest()
    expected_ledger = (
        project_root.parent
        / f".alphapilot-p4-2a-v2-2-execution-claim-{expected_token}"
    )
    if destination != expected_destination or token != expected_token or ledger != expected_ledger:
        raise RehearsalV22ValidationError("implementation execution binding derivation drifted")
    registered = REGISTERED_PROJECT_ROOT.absolute()
    if mode == "REGISTERED_OFFICIAL":
        if project_root != registered or token != REGISTERED_SERIES_TOKEN:
            raise RehearsalV22ValidationError("official execution binding is not canonical")
    elif (
        project_root == registered
        or project_root.is_relative_to(registered)
        or registered.is_relative_to(project_root)
    ):
        raise RehearsalV22ValidationError("disposable project root overlaps registered root")
    return BindingView(
        mode=mode,
        project_root=project_root,
        absolute_destination=destination,
        series_token_sha256=token,
        ledger_root=ledger,
    )


def _assert_official_runtime_before_read(root: Path) -> None:
    if _VALIDATOR_REGISTERED_BOOTSTRAP:
        _assert_registered_validator_environment()
        return
    main_module = sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", None)
    shim = root / "scripts/rehearse_p4_2a_v2_2_heldout_full_path.py"
    orig = tuple(sys.orig_argv)
    argv = tuple(sys.argv)
    if (
        dict(_validator_os.environ) != _EXACT_ENVIRONMENT
        or sys.flags.hash_randomization != 0
        or sys.flags.no_site != 1
        or sys.flags.no_user_site != 1
        or not sys.flags.safe_path
        or not sys.dont_write_bytecode
        or sys.pycache_prefix != "/dev/null"
        or not isinstance(main_file, str)
        or Path(main_file).resolve(strict=True) != shim
        or Path(sys.executable).absolute() != Path(_VALIDATOR_FIXED_PYTHON)
        or _sha256(_fixed_launcher_bytes())
        != "f4cd716d4b54f205398bec6932cc59361b087494ca2ddb157a5e8631d4d6f863"
        or _sha256(
            _regular_bytes(
                Path(_VALIDATOR_FIXED_ORIG_PYTHON),
                "fixed Python orig-argv executable",
            )
        )
        != "89c717ced41f6a395612366e5b038226d0d8fca36bbddd9321d385f5f370ebbe"
        or len(orig) != 10
        or orig[:7]
        != (
            _VALIDATOR_FIXED_ORIG_PYTHON,
            "-S",
            "-P",
            "-B",
            shim.as_posix(),
            "--execute",
            "--attempt-authorization",
        )
        or argv != orig[4:]
        or orig[8] != "--expected-ordinal"
        or not orig[9].isdigit()
        or int(orig[9]) < 1
    ):
        raise RehearsalV22ValidationError(
            "official validation lacks the exact locked runner bootstrap"
        )
    try:
        action_path = Path(orig[7]).resolve(strict=True)
        action_relative = action_path.relative_to(root).as_posix()
    except (OSError, ValueError) as exc:
        raise RehearsalV22ValidationError(
            "official validation action authorization path escaped"
        ) from exc
    match = _ACTION_PATH_PATTERN.fullmatch(action_relative)
    if match is None or int(match.group(1)) != int(orig[9]):
        raise RehearsalV22ValidationError(
            "official validation action ordinal binding drifted"
        )
    policy = implementation._AUDIT_POLICY.get()
    authority = implementation._TEMP_AUTHORITY.get()
    if (
        policy is None
        or authority is None
        or getattr(policy, "project_root", None) != root
        or not isinstance(authority, Path)
        or not any(
            authority == candidate or authority.is_relative_to(candidate)
            for candidate in getattr(policy, "write_roots", ())
            if isinstance(candidate, Path)
        )
    ):
        raise RehearsalV22ValidationError(
            "official validation lacks the active core audit authority"
        )


def _resolve_execution_binding(
    *,
    project_root: Path,
    execution_context: object | None,
    validator_delegation: object | None,
) -> ResolvedExecution:
    requested_root = project_root.absolute()
    if execution_context is None:
        if validator_delegation is not None:
            raise RehearsalV22ValidationError(
                "validator delegation without execution context is forbidden"
            )
        registered = REGISTERED_PROJECT_ROOT.absolute()
        if requested_root != registered:
            raise RehearsalV22ValidationError(
                "noncanonical project root requires private disposable authority"
            )
        _assert_official_runtime_before_read(registered)
        root = registered.resolve(strict=True)
        if root != registered:
            raise RehearsalV22ValidationError(
                "canonical project root is aliased"
            )
        raw_binding = implementation.derive_execution_binding(
            project_root=root,
            execution_context=None,
        )
        binding = _binding_view(raw_binding)
        if binding.mode != "REGISTERED_OFFICIAL":
            raise RehearsalV22ValidationError("official validation selected test mode")
        return ResolvedExecution(view=binding, raw=raw_binding)

    if validator_delegation is None:
        raise RehearsalV22ValidationError(
            "private disposable context requires borrowed validator authority"
        )
    raw_binding = implementation._validate_execution_capability(
        execution_context,
        project_root=requested_root,
    )
    delegated_binding = implementation._validate_validator_delegation(
        validator_delegation,
        execution_context=execution_context,
        validator_module=sys.modules[__name__],
        project_root=requested_root,
    )
    binding = _binding_view(raw_binding)
    delegated = _binding_view(delegated_binding)
    registered = REGISTERED_PROJECT_ROOT.absolute()
    expected_mode = (
        "REGISTERED_OFFICIAL"
        if binding.project_root == registered
        else "DISPOSABLE_FULL_SHAPE_TEST"
    )
    if (
        binding.project_root != requested_root
        or delegated != binding
        or binding.mode != expected_mode
    ):
        raise RehearsalV22ValidationError("borrowed validator authority binding drifted")
    return ResolvedExecution(view=binding, raw=raw_binding)


def _validate_binding_document(value: object, binding: BindingView, label: str) -> JsonObject:
    document = _object(value, label)
    expected_common: JsonObject = {
        "mode": binding.mode,
        "project_root": binding.project_root.as_posix(),
        "absolute_destination": binding.absolute_destination.as_posix(),
        "series_token_sha256": binding.series_token_sha256,
        "ledger_root": binding.ledger_root.as_posix(),
        "derivation_recomputed": True,
        "private_rebase_capability_validated": (
            binding.mode == "DISPOSABLE_FULL_SHAPE_TEST"
        ),
    }
    if binding.mode == "REGISTERED_OFFICIAL":
        expected_common[
            "registered_rehearsal_paths_created_as_expected"
            if label.startswith("bundle")
            else "registered_rehearsal_paths_rehashed_as_expected"
        ] = True
    else:
        expected_common["real_registered_paths_untouched"] = True
    _require_equal(document, expected_common, label)
    return document


def _authorized_bundle_directory(
    *,
    binding: BindingView,
    raw_binding: implementation.ExecutionBinding,
    bundle_path: Path,
    published_release_revalidation: bool,
) -> Path:
    try:
        candidate = bundle_path.absolute()
    except OSError as exc:
        raise RehearsalV22ValidationError("bundle path is invalid") from exc
    validator_module = sys.modules.get(__name__)
    if validator_module is None:
        raise RehearsalV22ValidationError("validator module identity is absent")
    if published_release_revalidation:
        evidence_root = implementation._validate_published_validator_bundle(
            binding=raw_binding,
            validator_module=validator_module,
            bundle_path=candidate,
        )
    else:
        evidence_root = implementation._validate_official_validator_candidate(
            binding=raw_binding,
            validator_module=validator_module,
            bundle_path=candidate,
        )
    authorized = _directory(evidence_root, "closure-authorized evidence root")
    if candidate.parent != authorized:
        raise RehearsalV22ValidationError(
            "bundle is outside its closure-authorized evidence root"
        )
    return authorized


def _audit_hook_source_map(sources: Mapping[str, bytes]) -> dict[str, int]:
    """Count syntactic audit-hook installers in commit-bound Python sources."""

    result: dict[str, int] = {}
    for relative, payload in sorted(
        sources.items(), key=lambda item: item[0].encode("utf-8")
    ):
        try:
            tree = ast.parse(payload, filename=relative)
        except (SyntaxError, ValueError) as exc:
            raise RehearsalV22ValidationError(
                f"implementation surface is not parseable: {relative}"
            ) from exc
        count = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if (
                isinstance(function, ast.Attribute)
                and function.attr == "addaudithook"
            ) or (isinstance(function, ast.Name) and function.id == "addaudithook"):
                count += 1
        if count:
            result[relative] = count
    return result


def _validate_module_identity(
    project_root: Path,
    live_anchor: implementation.LiveExecutionAnchor,
) -> None:
    implementation_commit = _commit(
        live_anchor.implementation_commit,
        "live execution implementation commit",
    )
    authority_surface = _independent_local_import_closure(
        project_root=project_root,
        implementation_commit=implementation_commit,
    )
    for relative in IMPLEMENTATION_PATHS[3:]:
        authority_surface[relative] = _git_blob(
            project_root,
            implementation_commit,
            relative,
        )
    audit_hook_sources = _audit_hook_source_map(authority_surface)
    historical_paths = frozenset(
        relative for relative, _module_name, _count in _INERT_HISTORICAL_AUDIT_HOOK_SOURCES
    )
    expected_historical = {
        relative: count
        for relative, _module_name, count in _INERT_HISTORICAL_AUDIT_HOOK_SOURCES
    }
    observed_historical = {
        relative: audit_hook_sources.get(relative, 0) for relative in historical_paths
    }
    for relative in historical_paths:
        if authority_surface.get(relative) != _git_blob(
            project_root,
            _V2_1_IMPLEMENTATION_COMMIT,
            relative,
        ):
            raise RehearsalV22ValidationError(
                f"inert historical authority bytes drifted: {relative}"
            )
    active_hook_sources = {
        relative: count
        for relative, count in audit_hook_sources.items()
        if relative not in historical_paths
    }
    if (
        observed_historical != expected_historical
        or active_hook_sources
        != {"scripts/p4_2a_v2_2_heldout_rehearsal.py": 1}
    ):
        raise RehearsalV22ValidationError(
            "implementation is not the sole process audit-hook installer"
        )

    base_runner_module = sys.modules.get(
        "scripts.rehearse_p4_2a_v2_heldout_full_path"
    )
    base_runner_payload = _regular_bytes(
        project_root / "scripts/rehearse_p4_2a_v2_heldout_full_path.py",
        "v2 pure control helper",
    )
    if (
        sys.modules.get("scripts.p4_2a_v2_2_heldout_rehearsal")
        is not _implementation_module
        or any(
            module_name in sys.modules
            for _relative, module_name, _count in _INERT_HISTORICAL_AUDIT_HOOK_SOURCES
        )
        or (
            base_runner_module is not None
            and any(
                hasattr(base_runner_module, name)
                for name in ("_AUDIT_POLICY", "_TEMP_AUTHORITY", "_build_authority_state")
            )
        )
        or any(
            marker in base_runner_payload
            for marker in (
                b"ContextVar",
                b"addaudithook",
                b"_AUDIT_POLICY",
                b"_TEMP_AUTHORITY",
            )
        )
        or _AUDIT_POLICY is not implementation._AUDIT_POLICY
        or _TEMP_AUTHORITY is not implementation._TEMP_AUTHORITY
        or getattr(implementation._process_audit_hook, "__module__", None)
        != implementation.MODULE_NAME
    ):
        raise RehearsalV22ValidationError(
            "implementation module, authority owner, or ContextVar identity split"
        )
    observation = implementation._module_identity_observation()
    try:
        module_object_id = _integer(observation.module_object_id, "module object id", minimum=1)
        audit_policy_id = _integer(
            observation.audit_policy_object_id, "audit policy id", minimum=1
        )
        temp_authority_id = _integer(
            observation.temp_authority_object_id, "temp authority id", minimum=1
        )
        origin = observation.module_origin.resolve(strict=True)
        digest = _sha256(_regular_bytes(origin, "implementation module"))
    except OSError as exc:
        raise RehearsalV22ValidationError("module identity observation is malformed") from exc
    expected_origin = project_root / "scripts/p4_2a_v2_2_heldout_rehearsal.py"
    if (
        module_object_id != id(_implementation_module)
        or audit_policy_id != id(_AUDIT_POLICY)
        or temp_authority_id != id(_TEMP_AUTHORITY)
        or origin != expected_origin
        or digest != _sha256(_regular_bytes(origin, "implementation module"))
    ):
        raise RehearsalV22ValidationError("module identity observation drifted")
    validator_origin = Path(__file__).resolve(strict=True)
    expected_validator_origin = (
        project_root / "scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py"
    ).resolve(strict=True)
    if validator_origin != expected_validator_origin:
        raise RehearsalV22ValidationError("live validator module origin drifted")
    expected_loaded = {
        "scripts/p4_2a_v2_2_heldout_rehearsal.py": digest,
        "scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py": _sha256(
            _regular_bytes(validator_origin, "validator module")
        ),
    }
    if dict(live_anchor.loaded_module_sha256) != expected_loaded:
        raise RehearsalV22ValidationError("live loaded-module digest binding drifted")
    _validated_live_implementation_blob(
        project_root=project_root,
        live_anchor=live_anchor,
        relative_path="scripts/p4_2a_v2_2_heldout_rehearsal.py",
        expected_sha256=digest,
    )
    _validated_live_implementation_blob(
        project_root=project_root,
        live_anchor=live_anchor,
        relative_path="scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py",
        expected_sha256=expected_loaded[
            "scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py"
        ],
    )


def _validate_authority_ref(value: object, label: str) -> JsonObject:
    reference = _object(value, label)
    _require_exact_keys(
        reference,
        frozenset({"path", "sha256", "creating_commit", "unique_a_history_verified"}),
        label,
    )
    _relative(reference["path"], f"{label}.path")
    _sha(reference["sha256"], f"{label}.sha256")
    _commit(reference["creating_commit"], f"{label}.creating_commit")
    if reference["unique_a_history_verified"] is not True:
        raise RehearsalV22ValidationError(f"{label} is not unique-A verified")
    return reference


def _core_authority(reference: Mapping[str, Any]) -> implementation.AuthorityReference:
    return implementation.AuthorityReference(
        path=cast(str, reference["path"]),
        sha256=cast(str, reference["sha256"]),
        creating_commit=cast(str, reference["creating_commit"]),
        unique_a_history_verified=True,
    )


def _authority_reference_json(value: object, label: str) -> JsonObject:
    if not isinstance(value, implementation.AuthorityReference):
        raise RehearsalV22ValidationError(f"{label} is not an authority reference")
    return _validate_authority_ref(value.as_json(), label)


def _historical_selected_anchor(
    value: object,
) -> implementation.HistoricalSelectedAnchor:
    if not isinstance(value, implementation.HistoricalSelectedAnchor):
        raise RehearsalV22ValidationError(
            "historical selected anchor is absent, forged, or cross-typed"
        )
    _integer(value.selected_epoch, "historical selected epoch", minimum=1)
    _commit(value.selected_commit, "historical selected commit")
    _authority_reference_json(
        value.owner_action_time_authorization,
        "historical selected action authorization",
    )
    _authority_reference_json(
        value.owner_surface_authorization,
        "historical selected surface authorization",
    )
    _authority_reference_json(
        value.independent_implementation_review,
        "historical selected independent review",
    )
    if not isinstance(value.control_surface, implementation.ControlSurface):
        raise RehearsalV22ValidationError("historical selected control surface is malformed")
    _require_equal(
        value.control_surface.implementation_commit,
        value.selected_commit,
        "historical selected control commit",
    )
    for label, digest in (
        ("historical selected control root", value.control_surface.merkle_root_sha256),
        ("historical selected history root", value.history_root_sha256),
        ("historical selected live-ledger root", value.live_ledger_root_sha256),
        ("historical selected evidence root", value.evidence_tree_root_sha256),
        ("historical selected candidate root", value.candidate_content_root_sha256),
        ("historical selected run-a root", value.run_a_root_sha256),
        ("historical selected run-b root", value.run_b_root_sha256),
    ):
        _sha(digest, label)
    blob_map = dict(value.selected_git_blob_sha256)
    if not blob_map:
        raise RehearsalV22ValidationError("historical selected Git-blob map is empty")
    for relative, digest in blob_map.items():
        _relative(relative, "historical selected Git-blob path")
        _sha(digest, f"historical selected Git-blob SHA {relative}")
    return value


def _live_execution_anchor(value: object) -> implementation.LiveExecutionAnchor:
    if not isinstance(value, implementation.LiveExecutionAnchor):
        raise RehearsalV22ValidationError(
            "live execution anchor is absent, forged, or cross-typed"
        )
    _integer(value.execution_epoch, "live execution epoch", minimum=1)
    _commit(value.implementation_commit, "live execution implementation commit")
    _authority_reference_json(
        value.owner_surface_authorization,
        "live execution surface authorization",
    )
    _authority_reference_json(
        value.independent_implementation_review,
        "live execution independent review",
    )
    _commit(value.merge_commit, "live execution merge commit")
    _authority_reference_json(value.landing_report, "live execution landing report")
    _commit(value.execution_head, "live execution HEAD")
    if not isinstance(value.control_surface, implementation.ControlSurface):
        raise RehearsalV22ValidationError("live execution control surface is malformed")
    _require_equal(
        value.control_surface.implementation_commit,
        value.implementation_commit,
        "live execution control commit",
    )
    _sha(value.control_surface.merkle_root_sha256, "live execution control root")
    loaded = dict(value.loaded_module_sha256)
    if set(loaded) != {
        "scripts/p4_2a_v2_2_heldout_rehearsal.py",
        "scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py",
    }:
        raise RehearsalV22ValidationError("live loaded-module inventory drifted")
    for relative, digest in loaded.items():
        _sha(digest, f"live loaded-module SHA {relative}")
    return value


def _validate_file_ref(value: object, label: str) -> JsonObject:
    reference = _object(value, label)
    _require_exact_keys(reference, frozenset({"path", "sha256"}), label)
    _relative(reference["path"], f"{label}.path")
    _sha(reference["sha256"], f"{label}.sha256")
    return reference


def _validate_series_json(
    payload: bytes,
    *,
    binding: BindingView,
    preregistration_commit: str,
) -> JsonObject:
    series = _strict_canonical_json_loads(payload, label="live series.json")
    _require_exact_keys(series, _SERIES_FIELDS, "live series.json")
    _require_equal(
        series["schema_version"], "p4.2a-v2-2-rehearsal-series-v1", "series schema"
    )
    _require_equal(series["series_id"], REHEARSAL_ID, "series id")
    _require_equal(series["series_token_sha256"], binding.series_token_sha256, "series token")
    _require_equal(series["policy"], SERIES_POLICY, "series policy")
    _require_equal(series["ledger_root"], binding.ledger_root.as_posix(), "series ledger root")
    _require_equal(
        series["attempt_limit"],
        "unbounded_until_first_validated_success_or_owner_abandonment",
        "series attempt limit",
    )
    _require_equal(
        series["per_attempt_action_time_owner_authorization_required"],
        True,
        "series per-attempt authorization",
    )
    _require_equal(series["automatic_retry_count"], 0, "series automatic retry count")
    _require_equal(
        series["first_validated_candidate_closes_series"], True, "series close policy"
    )
    prereg = _validate_authority_ref(series["preregistration"], "series preregistration")
    _require_equal(prereg["path"], PREREGISTRATION_RELATIVE.as_posix(), "series prereg path")
    _require_equal(prereg["sha256"], PREREGISTRATION_SHA256, "series prereg SHA")
    _require_equal(prereg["creating_commit"], preregistration_commit, "series prereg commit")
    bundle_schema = _validate_file_ref(series["bundle_schema"], "series bundle schema")
    _require_equal(
        bundle_schema,
        {"path": BUNDLE_SCHEMA_RELATIVE.as_posix(), "sha256": BUNDLE_SCHEMA_SHA256},
        "series bundle schema",
    )
    release_schema = _validate_file_ref(series["release_schema"], "series release schema")
    _require_equal(
        release_schema,
        {"path": RELEASE_SCHEMA_RELATIVE.as_posix(), "sha256": RELEASE_SCHEMA_SHA256},
        "series release schema",
    )
    _rfc3339_utc(series["created_at_utc"], "series created_at_utc")
    return series


def _validate_epoch_shape(value: object, label: str) -> JsonObject:
    epoch = _object(value, label)
    expected = frozenset(
        {
            "epoch",
            "implementation_commit",
            "owner_exact_surface_authorization",
            "independent_implementation_review",
            "control_merkle_root_sha256",
            "first_attempt_ordinal",
            "last_attempt_ordinal",
            "all_attempts_authorized",
        }
    )
    _require_exact_keys(epoch, expected, label)
    _integer(epoch["epoch"], f"{label}.epoch", minimum=1)
    _commit(epoch["implementation_commit"], f"{label}.implementation_commit")
    _validate_authority_ref(
        epoch["owner_exact_surface_authorization"], f"{label}.owner authorization"
    )
    _validate_authority_ref(
        epoch["independent_implementation_review"], f"{label}.independent review"
    )
    _sha(epoch["control_merkle_root_sha256"], f"{label}.control root")
    first = _integer(epoch["first_attempt_ordinal"], f"{label}.first ordinal", minimum=1)
    last = _integer(epoch["last_attempt_ordinal"], f"{label}.last ordinal", minimum=1)
    if last < first:
        raise RehearsalV22ValidationError(f"{label} ordinal interval is reversed")
    _require_equal(epoch["all_attempts_authorized"], True, f"{label}.authorization")
    return epoch


def _is_void_epoch_one(epoch: Mapping[str, Any]) -> bool:
    return (
        epoch.get("epoch") == 1
        and epoch.get("implementation_commit") == VOID_EPOCH_ONE_IMPLEMENTATION_COMMIT
        and epoch.get("owner_exact_surface_authorization")
        == {
            "path": INDEPENDENT_REVIEW_RELATIVE.as_posix(),
            "sha256": INDEPENDENT_REVIEW_SHA256,
            "creating_commit": INDEPENDENT_REVIEW_COMMIT,
            "unique_a_history_verified": True,
        }
        and epoch.get("independent_implementation_review")
        == {
            "path": VOID_EPOCH_ONE_ADJUDICATION_RELATIVE.as_posix(),
            "sha256": VOID_EPOCH_ONE_ADJUDICATION_SHA256,
            "creating_commit": VOID_EPOCH_ONE_ADJUDICATION_COMMIT,
            "unique_a_history_verified": True,
        }
        and epoch.get("first_attempt_ordinal") == 1
        and epoch.get("last_attempt_ordinal") == 1
        and epoch.get("all_attempts_authorized") is True
    )


def _is_void_epoch_three(epoch: Mapping[str, Any]) -> bool:
    return epoch == {
        "epoch": 3,
        "implementation_commit": VOID_EPOCH_THREE_IMPLEMENTATION_COMMIT,
        "owner_exact_surface_authorization": {
            "path": VOID_EPOCH_THREE_AUTHORITY_RELATIVE.as_posix(),
            "sha256": VOID_EPOCH_THREE_AUTHORITY_SHA256,
            "creating_commit": VOID_EPOCH_THREE_IMPLEMENTATION_PARENT,
            "unique_a_history_verified": True,
        },
        "independent_implementation_review": {
            "path": VOID_EPOCH_THREE_REASON_RELATIVE.as_posix(),
            "sha256": VOID_EPOCH_THREE_REASON_SHA256,
            "creating_commit": VOID_EPOCH_THREE_REASON_COMMIT,
            "unique_a_history_verified": True,
        },
        "control_merkle_root_sha256": VOID_EPOCH_THREE_CONTROL_ROOT_SHA256,
        "first_attempt_ordinal": 2,
        "last_attempt_ordinal": 2,
        "all_attempts_authorized": True,
    }


def _epoch_map(bundle: Mapping[str, Any]) -> dict[int, JsonObject]:
    rows = _array(bundle.get("implementation_epochs"), "bundle implementation epochs")
    result: dict[int, JsonObject] = {}
    prior_last = 0
    for index, raw in enumerate(rows, 1):
        epoch = _validate_epoch_shape(raw, f"implementation epoch {index}")
        number = cast(int, epoch["epoch"])
        if number != index or number in result:
            raise RehearsalV22ValidationError("implementation epochs are not contiguous")
        if (index == 1 and _is_void_epoch_one(epoch)) or (
            index == 3 and _is_void_epoch_three(epoch)
        ):
            result[number] = epoch
            continue
        if cast(int, epoch["first_attempt_ordinal"]) != prior_last + 1:
            raise RehearsalV22ValidationError("implementation epoch attempt intervals have a gap")
        prior_last = cast(int, epoch["last_attempt_ordinal"])
        result[number] = epoch
    if not result:
        raise RehearsalV22ValidationError("bundle has no implementation epoch")
    if _is_void_epoch_one(result[1]) and len(result) < 2:
        raise RehearsalV22ValidationError("void epoch 1 lacks an executed epoch 2")
    if 3 in result and _is_void_epoch_three(result[3]):
        if len(result) != 4 or _is_void_epoch_three(result.get(4, {})):
            raise RehearsalV22ValidationError(
                "void epoch 3 requires one executed epoch 4 and no other epoch shape"
            )
        if (
            result[2].get("first_attempt_ordinal") != 1
            or result[2].get("last_attempt_ordinal") != 1
            or result[4].get("first_attempt_ordinal") != 2
            or result[4].get("last_attempt_ordinal") != 2
        ):
            raise RehearsalV22ValidationError(
                "void epoch 3 is accepted only for the exact [2,4] ordinal shape"
            )
    return result


def _validate_started(
    payload: bytes,
    *,
    binding: BindingView,
    ordinal: int,
    previous_history_root: str,
    epoch: Mapping[str, Any],
) -> JsonObject:
    started = _strict_canonical_json_loads(payload, label=f"attempt {ordinal} started.json")
    _require_exact_keys(started, _STARTED_FIELDS, f"attempt {ordinal} started.json")
    expected_commit = _commit(epoch["implementation_commit"], "epoch implementation commit")
    expected_token = _attempt_token(
        series_token=binding.series_token_sha256,
        ordinal=ordinal,
        implementation_commit=expected_commit,
        previous_history_root=previous_history_root,
    )
    scalar_expected: JsonObject = {
        "schema_version": "p4.2a-v2-2-rehearsal-attempt-started-v1",
        "series_id": REHEARSAL_ID,
        "series_token_sha256": binding.series_token_sha256,
        "ordinal": ordinal,
        "attempt_token_sha256": expected_token,
        "previous_history_root_sha256": previous_history_root,
        "implementation_epoch": epoch["epoch"],
        "implementation_commit": expected_commit,
        "control_merkle_root_sha256": epoch["control_merkle_root_sha256"],
        "environment": _EXACT_ENVIRONMENT,
        "interpreter_path": (
            "/Users/ouyangduning/Documents/project/interesting/AlphaPilot-AI/.venv/bin/python"
        ),
        "interpreter_sha256": (
            "f4cd716d4b54f205398bec6932cc59361b087494ca2ddb157a5e8631d4d6f863"
        ),
    }
    for key, expected in scalar_expected.items():
        _require_equal(started[key], expected, f"attempt {ordinal} started.{key}")
    command = _array(started["command"], f"attempt {ordinal} command")
    if any(not isinstance(item, str) or not item for item in command):
        raise RehearsalV22ValidationError(f"attempt {ordinal} command is malformed")
    if command[0] != (
        "/Library/Frameworks/Python.framework/Versions/3.12/Resources/"
        "Python.app/Contents/MacOS/Python"
    ):
        raise RehearsalV22ValidationError(f"attempt {ordinal} command executable drifted")
    _require_equal(
        started["command_sha256"],
        _command_sha256(cast(list[str], command)),
        f"attempt {ordinal} command SHA",
    )
    environment = _object(started["environment"], f"attempt {ordinal} environment")
    if any(not isinstance(value, str) for value in environment.values()):
        raise RehearsalV22ValidationError(f"attempt {ordinal} environment value is not text")
    _require_equal(
        started["environment_sha256"],
        _environment_sha256(cast(Mapping[str, str], environment)),
        f"attempt {ordinal} environment SHA",
    )
    _rfc3339_utc(started["created_at_utc"], f"attempt {ordinal} started timestamp")
    _validate_authority_ref(
        started["owner_action_time_authorization"],
        f"attempt {ordinal} owner action authorization",
    )
    return started


def _validate_candidate(
    payload: bytes,
    *,
    ordinal: int,
    started: Mapping[str, Any],
    epoch: Mapping[str, Any],
    evidence_root: str,
) -> JsonObject:
    candidate = _strict_canonical_json_loads(payload, label=f"attempt {ordinal} candidate.json")
    _require_exact_keys(candidate, _CANDIDATE_FIELDS, f"attempt {ordinal} candidate.json")
    expected = {
        "schema_version": "p4.2a-v2-2-rehearsal-attempt-candidate-v1",
        "series_id": REHEARSAL_ID,
        "ordinal": ordinal,
        "attempt_token_sha256": started["attempt_token_sha256"],
        "implementation_epoch": epoch["epoch"],
        "implementation_commit": epoch["implementation_commit"],
        "control_surface_root_sha256": epoch["control_merkle_root_sha256"],
        "evidence_tree_root_sha256": evidence_root,
    }
    for key, value in expected.items():
        _require_equal(candidate[key], value, f"attempt {ordinal} candidate.{key}")
    run_a = _sha(candidate["run_a_root_sha256"], f"attempt {ordinal} run-a root")
    run_b = _sha(candidate["run_b_root_sha256"], f"attempt {ordinal} run-b root")
    expected_content = _candidate_content_root(
        previous_history_root=cast(str, started["previous_history_root_sha256"]),
        run_a_root=run_a,
        run_b_root=run_b,
        control_root=cast(str, candidate["control_surface_root_sha256"]),
        evidence_root=evidence_root,
    )
    _require_equal(
        candidate["candidate_content_root_sha256"],
        expected_content,
        f"attempt {ordinal} candidate content root",
    )
    _rfc3339_utc(candidate["validated_at_utc"], f"attempt {ordinal} validated_at")
    return candidate


def _validate_terminal(
    payload: bytes,
    *,
    ordinal: int,
    started: Mapping[str, Any],
    epoch: Mapping[str, Any],
    candidate_present: bool,
    evidence_payloads: Mapping[str, bytes],
    evidence_root: str,
) -> JsonObject:
    terminal = _strict_canonical_json_loads(payload, label=f"attempt {ordinal} terminal.json")
    _require_exact_keys(terminal, _TERMINAL_FIELDS, f"attempt {ordinal} terminal.json")
    expected = {
        "schema_version": "p4.2a-v2-2-rehearsal-attempt-terminal-v1",
        "series_id": REHEARSAL_ID,
        "ordinal": ordinal,
        "attempt_token_sha256": started["attempt_token_sha256"],
        "implementation_epoch": epoch["epoch"],
        "implementation_commit": epoch["implementation_commit"],
        "automatic_retry_count": 0,
        "evidence_tree_root_sha256": evidence_root,
    }
    for key, value in expected.items():
        _require_equal(terminal[key], value, f"attempt {ordinal} terminal.{key}")
    outcome = _string(terminal["outcome"], f"attempt {ordinal} outcome")
    if outcome == "FAILED":
        if candidate_present:
            raise RehearsalV22ValidationError("failed attempt has candidate.json")
        error = _object(terminal["error"], f"attempt {ordinal} error")
        _require_exact_keys(
            error,
            frozenset({"exception_type", "message_sha256", "failing_stage"}),
            f"attempt {ordinal} error",
        )
        _string(error["exception_type"], f"attempt {ordinal} error type")
        _sha(error["message_sha256"], f"attempt {ordinal} error message SHA")
        _string(error["failing_stage"], f"attempt {ordinal} failing stage")
    elif outcome == "CANDIDATE_VALIDATED_AND_SELECTED":
        if not candidate_present or terminal["error"] is not None:
            raise RehearsalV22ValidationError("validated attempt terminal shape drifted")
    else:
        raise RehearsalV22ValidationError("terminal outcome is not registered")
    _string(terminal["reached_stage"], f"attempt {ordinal} reached stage")
    inventory = _array(terminal["artifact_inventory"], f"attempt {ordinal} inventory")
    observed: dict[str, JsonObject] = {}
    for index, raw in enumerate(inventory):
        item = _object(raw, f"attempt {ordinal} inventory item {index}")
        _require_exact_keys(
            item,
            frozenset({"logical_name", "relative_path", "bytes", "sha256", "durability"}),
            f"attempt {ordinal} inventory item {index}",
        )
        relative = _evidence_relative(
            item["relative_path"], f"attempt {ordinal} inventory relative path"
        )
        if relative in observed:
            raise RehearsalV22ValidationError("attempt evidence inventory has duplicate path")
        _string(item["logical_name"], f"attempt {ordinal} evidence logical name")
        _integer(item["bytes"], f"attempt {ordinal} evidence bytes", minimum=0)
        _sha(item["sha256"], f"attempt {ordinal} evidence SHA")
        _require_equal(item["durability"], "LEDGER_PERSISTED", "evidence durability")
        observed[relative] = item
    if list(observed) != sorted(observed, key=lambda value: value.encode("utf-8")):
        raise RehearsalV22ValidationError("attempt evidence inventory is not byte-sorted")
    if set(observed) != set(evidence_payloads):
        raise RehearsalV22ValidationError("attempt evidence inventory set drifted")
    for relative, payload_value in evidence_payloads.items():
        item = observed[relative]
        _require_equal(item["bytes"], len(payload_value), "attempt evidence byte count")
        _require_equal(item["sha256"], _sha256(payload_value), "attempt evidence SHA")
    _rfc3339_utc(terminal["completed_at_utc"], f"attempt {ordinal} completed_at")
    return terminal


def _walk_regular_tree(
    root: Path,
    *,
    label: str,
    required_directory_mode: int | None = None,
    required_file_mode: int | None = None,
) -> dict[str, bytes]:
    directory = _directory(root, label)
    if required_directory_mode is not None and stat.S_IMODE(directory.lstat().st_mode) != (
        required_directory_mode
    ):
        raise RehearsalV22ValidationError(f"{label} root mode drifted")
    result: dict[str, bytes] = {}
    stack = [directory]
    while stack:
        current = stack.pop()
        try:
            entries = list(_validator_os.scandir(current))
        except OSError as exc:
            raise RehearsalV22ValidationError(f"cannot enumerate {label}") from exc
        entries.sort(key=lambda entry: entry.name.encode("utf-8"), reverse=True)
        for entry in entries:
            path = Path(entry.path)
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise RehearsalV22ValidationError(f"cannot stat {label} entry") from exc
            if entry.is_symlink():
                raise RehearsalV22ValidationError(f"{label} contains a symlink")
            if stat.S_ISDIR(metadata.st_mode):
                if (
                    required_directory_mode is not None
                    and stat.S_IMODE(metadata.st_mode) != required_directory_mode
                ):
                    raise RehearsalV22ValidationError(
                        f"{label} contains a directory with the wrong mode"
                    )
                stack.append(path)
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise RehearsalV22ValidationError(
                    f"{label} contains a special or hardlinked entry"
                )
            if (
                required_file_mode is not None
                and stat.S_IMODE(metadata.st_mode) != required_file_mode
            ):
                raise RehearsalV22ValidationError(
                    f"{label} contains a file with the wrong mode"
                )
            relative = path.relative_to(directory).as_posix()
            if relative in result:
                raise RehearsalV22ValidationError(f"{label} contains duplicate path")
            result[relative] = path.read_bytes()
    return dict(sorted(result.items(), key=lambda item: item[0].encode("utf-8")))


def _tree_directory_relatives(root: Path, *, label: str) -> set[str]:
    directory = _directory(root, label)
    result: set[str] = set()
    for path in directory.rglob("*"):
        metadata = path.lstat()
        if path.is_symlink():
            raise RehearsalV22ValidationError(f"{label} contains a symlink")
        if stat.S_ISDIR(metadata.st_mode):
            result.add(path.relative_to(directory).as_posix())
        elif not stat.S_ISREG(metadata.st_mode):
            raise RehearsalV22ValidationError(f"{label} contains a special entry")
    return result


def _directories_implied_by_files(paths: Sequence[str]) -> set[str]:
    result: set[str] = set()
    for relative in paths:
        parent = PurePosixPath(relative).parent
        while parent != PurePosixPath("."):
            result.add(parent.as_posix())
            parent = parent.parent
    return result


def _validate_action_authorization(
    payload: bytes,
    *,
    source_relative: str,
    binding: BindingView,
    ordinal: int,
    started: Mapping[str, Any],
    epoch: Mapping[str, Any],
) -> JsonObject:
    label = f"attempt {ordinal} action authorization"
    receipt = _strict_canonical_json_loads(payload, label=label)
    _require_exact_keys(receipt, _ACTION_FIELDS, label)
    path_match = _ACTION_PATH_PATTERN.fullmatch(source_relative)
    id_match = _ACTION_ID_PATTERN.fullmatch(
        _string(receipt["authorization_id"], f"{label}.authorization_id")
    )
    if path_match is None or id_match is None:
        raise RehearsalV22ValidationError(f"{label} path or id is not registered")
    ordinal_text = f"{ordinal:06d}"
    if path_match.group(1) != ordinal_text or id_match.group(1) != ordinal_text:
        raise RehearsalV22ValidationError(f"{label} ordinal binding drifted")
    created_utc = _rfc3339_utc(receipt["created_at_utc"], f"{label}.created_at_utc")
    created_shanghai = _rfc3339_shanghai(
        receipt["created_at_shanghai"], f"{label}.created_at_shanghai"
    )
    utc_instant = datetime.fromisoformat(created_utc.replace("Z", "+00:00"))
    shanghai_instant = datetime.fromisoformat(created_shanghai)
    if utc_instant != shanghai_instant:
        raise RehearsalV22ValidationError(f"{label} timestamps are not the same instant")
    date_text = created_shanghai[0:10].replace("-", "")
    if path_match.group(2) != date_text or id_match.group(2) != date_text:
        raise RehearsalV22ValidationError(f"{label} date binding drifted")
    expected: JsonObject = {
        "schema_version": "p4.2a-v2-2-rehearsal-attempt-execution-authorization-v1",
        "verdict": (
            "APPROVE_EXACTLY_ONE_V2_2_REHEARSAL_ATTEMPT_ZERO_AUTOMATIC_RETRY"
        ),
        "owner": {
            "identity": "ouyang",
            "approved": True,
            "scope": "one_disclosed_v2_2_rehearsal_ordinal_only",
        },
        "series_id": REHEARSAL_ID,
        "series_token_sha256": binding.series_token_sha256,
        "ledger_root": binding.ledger_root.as_posix(),
        "ordinal": ordinal,
        "previous_history_root_sha256": started["previous_history_root_sha256"],
        "implementation_epoch": epoch["epoch"],
        "implementation_commit": epoch["implementation_commit"],
        "owner_exact_surface_authorization": epoch["owner_exact_surface_authorization"],
        "independent_implementation_review": epoch["independent_implementation_review"],
        "control_merkle_root_sha256": epoch["control_merkle_root_sha256"],
        "exact_argv": started["command"],
        "command_sha256": started["command_sha256"],
        "exact_environment": _EXACT_ENVIRONMENT,
        "environment_sha256": started["environment_sha256"],
        "authorized_pipeline_starts": 1,
        "automatic_retry_count": 0,
        "heldout_evaluation_authorized": False,
        "locks": {
            "real_heldout_materialization": False,
            "real_heldout_inference": False,
            "heldout_evaluation": False,
            "p4_2b": False,
            "p4_3": False,
            "trading": False,
        },
    }
    for key, value in expected.items():
        _require_equal(receipt[key], value, f"{label}.{key}")
    command = cast(list[str], receipt["exact_argv"])
    expected_command = [
        (
            "/Library/Frameworks/Python.framework/Versions/3.12/Resources/"
            "Python.app/Contents/MacOS/Python"
        ),
        "-S",
        "-P",
        "-B",
        (
            binding.project_root
            / "scripts/rehearse_p4_2a_v2_2_heldout_full_path.py"
        ).as_posix(),
        "--execute",
        "--attempt-authorization",
        (binding.project_root / source_relative).as_posix(),
        "--expected-ordinal",
        str(ordinal),
    ]
    _require_equal(command, expected_command, f"{label}.exact_argv")
    _require_equal(
        receipt["command_sha256"], _command_sha256(command), f"{label}.command_sha256"
    )
    _require_equal(
        receipt["environment_sha256"],
        _environment_sha256(_EXACT_ENVIRONMENT),
        f"{label}.environment_sha256",
    )
    return receipt


def _file_evidence(
    *,
    live_relative: str,
    archive_relative: str,
    payload: bytes,
) -> JsonObject:
    return {
        "live_relative_path": live_relative,
        "archive_relative_path": archive_relative,
        "bytes": len(payload),
        "sha256": _sha256(payload),
    }


def _authority_evidence(
    *,
    authority: Mapping[str, Any],
    archive_relative: str,
    payload: bytes,
) -> JsonObject:
    return {
        "authority": dict(authority),
        "archive_relative_path": archive_relative,
        "bytes": len(payload),
        "archive_sha256": _sha256(payload),
        "source_and_archive_bytes_equal": True,
    }


def _artifact_evidence(
    *,
    logical_name: str,
    relative: str,
    archive_relative: str,
    payload: bytes,
) -> JsonObject:
    return {
        "logical_name": logical_name,
        "relative_path": relative,
        "relative_path_basis": "attempt_evidence_root_excluded",
        "bytes": len(payload),
        "sha256": _sha256(payload),
        "durability": "ARCHIVED",
        "archive_relative_path": archive_relative,
        "source_and_archive_bytes_equal": True,
    }


def _bundle_attempt_record(
    bundle: Mapping[str, Any], ordinal: int,
) -> JsonObject:
    history = _object(bundle.get("attempt_history"), "bundle attempt_history")
    records = _array(history.get("records"), "bundle attempt_history.records")
    if ordinal > len(records):
        raise RehearsalV22ValidationError(f"bundle omits attempt {ordinal}")
    record = _object(records[ordinal - 1], f"bundle attempt record {ordinal}")
    if record.get("ordinal") != ordinal:
        raise RehearsalV22ValidationError("bundle attempt records are reordered")
    return record


def _validate_attempt_history_records(
    *,
    project_root: Path,
    bundle: Mapping[str, Any],
    ledger_root: Path,
    archive_root: Path,
    binding: object,
) -> HistoryReplay:
    """Purely replay live records and their archived twins without trusting booleans."""

    root = project_root.resolve(strict=True)
    binding_view = binding if isinstance(binding, BindingView) else _binding_view(binding)
    if binding_view.project_root != root or binding_view.ledger_root != ledger_root.absolute():
        raise RehearsalV22ValidationError("history replay binding does not match its roots")
    ledger = _directory(ledger_root, "series ledger")
    archive = _directory(archive_root, "attempt-history archive")
    bundle_directory = archive.parent.parent
    if archive != bundle_directory / "archive/attempt-history":
        raise RehearsalV22ValidationError("attempt-history archive path drifted")

    live_payloads = _walk_regular_tree(
        ledger,
        label="series ledger",
        required_directory_mode=0o700,
        required_file_mode=0o600,
    )
    archive_tree = _walk_regular_tree(archive, label="attempt-history archive")
    archive_payloads = {
        f"archive/attempt-history/{relative}": payload
        for relative, payload in archive_tree.items()
    }
    if "series.json" not in live_payloads or ".series.lock" not in live_payloads:
        raise RehearsalV22ValidationError("series ledger lacks its fixed root records")
    if live_payloads[".series.lock"] != b"":
        raise RehearsalV22ValidationError("series lock bytes are not empty")
    lineage = _object(bundle.get("lineage"), "bundle lineage")
    preregistration_commit = _commit(
        lineage.get("preregistration_commit"), "bundle preregistration commit"
    )
    _validate_series_json(
        live_payloads["series.json"],
        binding=binding_view,
        preregistration_commit=preregistration_commit,
    )
    for relative in ("series.json", ".series.lock"):
        archived = f"archive/attempt-history/{relative}"
        if archive_payloads.get(archived) != live_payloads[relative]:
            raise RehearsalV22ValidationError(f"live/archive {relative} bytes differ")

    attempt_names: set[str] = set()
    for relative in live_payloads:
        if relative in {"series.json", ".series.lock"}:
            continue
        parts = PurePosixPath(relative).parts
        if len(parts) < 3 or parts[0] != "attempts" or not re.fullmatch(r"[0-9]{6}", parts[1]):
            raise RehearsalV22ValidationError(f"unexpected live ledger member {relative}")
        attempt_names.add(parts[1])
        if parts[2] not in {"started.json", "candidate.json", "terminal.json", "evidence"}:
            raise RehearsalV22ValidationError(f"unexpected attempt member {relative}")
        if parts[2] != "evidence" and len(parts) != 3:
            raise RehearsalV22ValidationError(f"record path has descendants: {relative}")
        if parts[2] == "evidence":
            if len(parts) < 4:
                raise RehearsalV22ValidationError("evidence root contains non-file entry")
            _evidence_relative("/".join(parts[3:]), "live evidence relative path")
    if not attempt_names:
        raise RehearsalV22ValidationError("series contains no started attempt")
    ordinals = sorted(int(name) for name in attempt_names)
    if ordinals != list(range(1, len(ordinals) + 1)):
        raise RehearsalV22ValidationError("live attempt ordinals have a gap or reorder")
    expected_live_directories = _directories_implied_by_files(tuple(live_payloads)) | {
        f"attempts/{ordinal:06d}/evidence" for ordinal in ordinals
    }
    if _tree_directory_relatives(ledger, label="series ledger") != (
        expected_live_directories
    ):
        raise RehearsalV22ValidationError(
            "series ledger contains a missing or extra directory"
        )

    epochs = _epoch_map(bundle)
    prior_root = _history_empty_root()
    records: list[JsonObject] = []
    source_records: list[tuple[JsonObject, JsonObject | None, JsonObject | None]] = []
    outcomes: list[str] = []
    selected_ordinal: int | None = None
    selected_epoch: int | None = None
    selected_commit: str | None = None
    expected_archive_member_paths = {
        "archive/attempt-history/series.json",
        "archive/attempt-history/.series.lock",
    }
    execution_head = _git_commit(
        root,
        _git_bytes(root, "rev-parse", "HEAD").decode("ascii", errors="strict").strip(),
        "history execution HEAD",
    )

    for ordinal in ordinals:
        prefix = f"attempts/{ordinal:06d}"
        started_relative = f"{prefix}/started.json"
        if started_relative not in live_payloads:
            raise RehearsalV22ValidationError(f"attempt {ordinal} lacks started.json")
        candidate_relative = f"{prefix}/candidate.json"
        terminal_relative = f"{prefix}/terminal.json"
        candidate_payload = live_payloads.get(candidate_relative)
        terminal_payload = live_payloads.get(terminal_relative)
        evidence_prefix = f"{prefix}/evidence/"
        evidence_payloads = {
            relative.removeprefix(evidence_prefix): payload
            for relative, payload in live_payloads.items()
            if relative.startswith(evidence_prefix)
        }
        evidence_root = _evidence_root(evidence_payloads)

        bundle_record = _bundle_attempt_record(bundle, ordinal)
        epoch_number = _integer(
            bundle_record.get("implementation_epoch"),
            f"bundle attempt {ordinal} epoch",
            minimum=1,
        )
        epoch = epochs.get(epoch_number)
        if epoch is None or not (
            cast(int, epoch["first_attempt_ordinal"])
            <= ordinal
            <= cast(int, epoch["last_attempt_ordinal"])
        ):
            raise RehearsalV22ValidationError(f"attempt {ordinal} epoch interval drifted")
        started_payload = live_payloads[started_relative]
        started = _validate_started(
            started_payload,
            binding=binding_view,
            ordinal=ordinal,
            previous_history_root=prior_root,
            epoch=epoch,
        )
        candidate = (
            _validate_candidate(
                candidate_payload,
                ordinal=ordinal,
                started=started,
                epoch=epoch,
                evidence_root=evidence_root,
            )
            if candidate_payload is not None
            else None
        )
        terminal = (
            _validate_terminal(
                terminal_payload,
                ordinal=ordinal,
                started=started,
                epoch=epoch,
                candidate_present=candidate is not None,
                evidence_payloads=evidence_payloads,
                evidence_root=evidence_root,
            )
            if terminal_payload is not None
            else None
        )
        if candidate is not None and terminal is None:
            raise RehearsalV22ValidationError(
                "candidate exists without terminal; owner recovery ruling is required"
            )
        if terminal is None:
            outcome = "INCOMPLETE_UNTERMINALIZED"
            reached_stage = _string(
                bundle_record.get("reached_stage"), f"attempt {ordinal} reached stage"
            )
            error: object = None
        else:
            outcome = cast(str, terminal["outcome"])
            reached_stage = cast(str, terminal["reached_stage"])
            error = terminal["error"]
        if selected_ordinal is not None:
            raise RehearsalV22ValidationError("attempt exists after first validated candidate")
        if outcome == "CANDIDATE_VALIDATED_AND_SELECTED":
            selected_ordinal = ordinal
            selected_epoch = epoch_number
            selected_commit = cast(str, epoch["implementation_commit"])

        action_ref = _validate_authority_ref(
            started["owner_action_time_authorization"],
            f"attempt {ordinal} action authorization ref",
        )
        action_relative = cast(str, action_ref["path"])
        creation_payload = _unique_a_authority(
            root,
            action_ref,
            require_worktree=False,
        )
        action_payload = creation_payload
        action_commit = cast(str, action_ref["creating_commit"])
        epoch_review = _validate_authority_ref(
            epoch["independent_implementation_review"],
            f"attempt {ordinal} epoch review",
        )
        if not _git_is_ancestor(
            root, cast(str, epoch_review["creating_commit"]), action_commit
        ) or not _git_is_ancestor(root, action_commit, execution_head):
            raise RehearsalV22ValidationError(
                "action authorization Git topology escaped its reviewed epoch"
            )
        action = _validate_action_authorization(
            action_payload,
            source_relative=action_relative,
            binding=binding_view,
            ordinal=ordinal,
            started=started,
            epoch=epoch,
        )
        del action

        archive_prefix = f"archive/attempt-history/{prefix}"
        record_pairs = {
            started_relative: f"{archive_prefix}/started.json",
        }
        if candidate_payload is not None:
            record_pairs[candidate_relative] = f"{archive_prefix}/candidate.json"
        if terminal_payload is not None:
            record_pairs[terminal_relative] = f"{archive_prefix}/terminal.json"
        for live_relative, archived_relative in record_pairs.items():
            expected_archive_member_paths.add(archived_relative)
            if archive_payloads.get(archived_relative) != live_payloads[live_relative]:
                raise RehearsalV22ValidationError(
                    f"live/archive record bytes differ: {live_relative}"
                )
        action_archive_relative = f"{archive_prefix}/action-time-authorization.json"
        expected_archive_member_paths.add(action_archive_relative)
        if archive_payloads.get(action_archive_relative) != action_payload:
            raise RehearsalV22ValidationError(
                f"attempt {ordinal} action authorization archive differs"
            )
        for relative, payload_value in evidence_payloads.items():
            archived_relative = f"{archive_prefix}/evidence/{relative}"
            expected_archive_member_paths.add(archived_relative)
            if archive_payloads.get(archived_relative) != payload_value:
                raise RehearsalV22ValidationError(
                    f"attempt {ordinal} evidence archive differs: {relative}"
                )

        terminal_inventory: dict[str, str] = {}
        if terminal is not None:
            for item in cast(list[JsonObject], terminal["artifact_inventory"]):
                terminal_inventory[cast(str, item["relative_path"])] = cast(
                    str, item["logical_name"]
                )
        artifact_inventory: list[JsonObject] = []
        for relative, payload_value in evidence_payloads.items():
            logical_name = terminal_inventory.get(relative, relative)
            artifact_inventory.append(
                _artifact_evidence(
                    logical_name=logical_name,
                    relative=relative,
                    archive_relative=f"{archive_prefix}/evidence/{relative}",
                    payload=payload_value,
                )
            )

        started_sha = _sha256(started_payload)
        candidate_sha = _sha256(candidate_payload) if candidate_payload is not None else None
        terminal_sha = _sha256(terminal_payload) if terminal_payload is not None else None
        record_root = _attempt_record_root(
            ordinal=ordinal,
            attempt_token=cast(str, started["attempt_token_sha256"]),
            started_sha256=started_sha,
            candidate_sha256=candidate_sha,
            terminal_sha256=terminal_sha,
            evidence_tree_root=evidence_root,
        )
        expected_record: JsonObject = {
            "ordinal": ordinal,
            "attempt_token_sha256": started["attempt_token_sha256"],
            "previous_history_root_sha256": prior_root,
            "started": _file_evidence(
                live_relative=started_relative,
                archive_relative=f"{archive_prefix}/started.json",
                payload=started_payload,
            ),
            "candidate": (
                _file_evidence(
                    live_relative=candidate_relative,
                    archive_relative=f"{archive_prefix}/candidate.json",
                    payload=candidate_payload,
                )
                if candidate_payload is not None
                else None
            ),
            "terminal": (
                _file_evidence(
                    live_relative=terminal_relative,
                    archive_relative=f"{archive_prefix}/terminal.json",
                    payload=terminal_payload,
                )
                if terminal_payload is not None
                else None
            ),
            "outcome": outcome,
            "reached_stage": reached_stage,
            "implementation_epoch": epoch_number,
            "implementation_commit": epoch["implementation_commit"],
            "owner_action_time_authorization": _authority_evidence(
                authority=action_ref,
                archive_relative=action_archive_relative,
                payload=action_payload,
            ),
            "command_sha256": started["command_sha256"],
            "environment_sha256": started["environment_sha256"],
            "automatic_retry_count": 0,
            "artifact_inventory": artifact_inventory,
            "error": error,
            "evidence_tree_root_sha256": evidence_root,
            "record_root_sha256": record_root,
        }
        _require_equal(bundle_record, expected_record, f"bundle attempt record {ordinal}")
        records.append(expected_record)
        source_records.append((started, candidate, terminal))
        outcomes.append(outcome)
        prior_root = _history_step(prior_root, record_root)

    if selected_ordinal is None or selected_epoch is None or selected_commit is None:
        raise RehearsalV22ValidationError("series has no validated candidate")
    bundle_records = _array(
        _object(bundle.get("attempt_history"), "bundle attempt_history").get("records"),
        "bundle attempt_history.records",
    )
    if len(bundle_records) != len(records):
        raise RehearsalV22ValidationError("bundle omits or adds an attempt record")
    if max(cast(int, epoch["last_attempt_ordinal"]) for epoch in epochs.values()) != len(records):
        raise RehearsalV22ValidationError("implementation epoch intervals do not cover history")

    expected_archive_paths = expected_archive_member_paths
    if set(archive_payloads) != expected_archive_paths:
        raise RehearsalV22ValidationError(
            "attempt-history archive contains a missing or extra byte member"
        )
    if _tree_directory_relatives(archive, label="attempt-history archive") != (
        _directories_implied_by_files(tuple(archive_tree))
    ):
        raise RehearsalV22ValidationError(
            "attempt-history archive contains a missing or extra directory"
        )
    archive_manifest = _object(bundle.get("archive"), "bundle archive")
    attempt_archive = _object(
        archive_manifest.get("attempt_history"), "bundle archive.attempt_history"
    )
    _require_equal(
        attempt_archive.get("archive_root"),
        "archive/attempt-history",
        "attempt archive root",
    )
    archive_files = _array(attempt_archive.get("files"), "attempt archive files")
    manifest_paths: set[str] = set()
    for index, raw in enumerate(archive_files):
        reference = _validate_file_ref(raw, f"attempt archive file {index}")
        relative = cast(str, reference["path"])
        if relative in manifest_paths or relative not in archive_payloads:
            raise RehearsalV22ValidationError("attempt archive manifest path drifted")
        if reference["sha256"] != _sha256(archive_payloads[relative]):
            raise RehearsalV22ValidationError("attempt archive manifest SHA drifted")
        manifest_paths.add(relative)
    if manifest_paths != expected_archive_paths:
        raise RehearsalV22ValidationError("attempt archive manifest set is not exact")
    _require_equal(attempt_archive.get("file_count"), len(archive_payloads), "archive file count")
    if attempt_archive.get(
        "every_live_started_candidate_terminal_and_action_authorization_byte_archived"
    ) is not True or attempt_archive.get("every_attempt_evidence_byte_archived") is not True:
        raise RehearsalV22ValidationError("attempt archive completeness claims are false")
    archive_merkle = _path_merkle(
        archive_payloads,
        leaf_domain=b"p4.2a-rehearsal-leaf-v2.2\0",
    )
    _require_equal(
        attempt_archive.get("history_merkle_root_sha256"),
        archive_merkle,
        "attempt archive Merkle root",
    )
    live_merkle = _path_merkle(
        live_payloads,
        leaf_domain=b"p4.2a-rehearsal-v2.2-ledger-leaf-v1\0",
    )
    return HistoryReplay(
        records=tuple(records),
        source_records=tuple(source_records),
        started_count=len(records),
        failed_count=outcomes.count("FAILED"),
        incomplete_count=outcomes.count("INCOMPLETE_UNTERMINALIZED"),
        selected_attempt_ordinal=selected_ordinal,
        selected_implementation_epoch=selected_epoch,
        selected_implementation_commit=selected_commit,
        history_root_sha256=prior_root,
        live_ledger_root_sha256=live_merkle,
        archive_merkle_root_sha256=archive_merkle,
        live_payloads=live_payloads,
        archive_payloads=archive_payloads,
    )


@dataclass(frozen=True)
class ArchiveReplay:
    run_a: Mapping[str, bytes]
    run_b: Mapping[str, bytes]
    run_a_root_sha256: str
    run_b_root_sha256: str
    control_root_sha256: str
    control_repository_payloads: Mapping[str, bytes]
    all_payloads: Mapping[str, bytes]


@dataclass(frozen=True)
class ValidatedBundle:
    document: JsonObject
    payload: bytes
    path: Path
    project_root: Path
    bundle_directory: Path
    implementation_commit: str
    archives: ArchiveReplay
    history: HistoryReplay
    historical_anchor: implementation.HistoricalSelectedAnchor
    live_anchor: implementation.LiveExecutionAnchor
    validation_mode: ValidationMode


def _validate_materialization_manifest(
    payload: bytes,
    *,
    pipeline_implementation_commit: str,
) -> None:
    manifest = _object(strict_json_loads(payload, label="materialization manifest"), "manifest")
    if manifest.get("schema_version") != "p4.2a-v2-heldout-materialization-manifest-v2":
        raise RehearsalV22ValidationError("materialization manifest schema drifted")
    authority = _object(manifest.get("execution_authority"), "manifest execution authority")
    if authority.get("mode") != "offline_rehearsal":
        raise RehearsalV22ValidationError("rehearsal materialization used real authority mode")
    if authority.get("implementation_commit") != pipeline_implementation_commit:
        raise RehearsalV22ValidationError("materialization implementation commit drifted")
    if authority.get("rehearsal_bundle") is not None or authority.get(
        "release_authorization"
    ) is not None:
        raise RehearsalV22ValidationError("offline manifest recursively binds a release")
    pacing = _object(manifest.get("request_pacing"), "manifest request pacing")
    _require_exact_keys(
        pacing,
        frozenset({"cninfo_pdf", "akshare_ths", "sina_company_news"}),
        "manifest request pacing",
    )
    cninfo = _object(pacing["cninfo_pdf"], "manifest CNInfo pacing")
    expected_cninfo = {
        "host": "static.cninfo.com.cn",
        "policy": "minimum_start_to_start",
        "configured_min_start_to_start_seconds": 1.0,
        "clock": "monotonic",
        "first_request_delayed": False,
        "request_start_count": 2824,
        "observed_gap_count": 2823,
        "minimum_observed_start_to_start_seconds": 1.0,
        "median_observed_start_to_start_seconds": 1.0,
        "violation_count": 0,
        "retry_count": 0,
    }
    _require_equal(cninfo, expected_cninfo, "materialization CNInfo pacing")
    _require_equal(
        pacing["akshare_ths"],
        "not_applicable_no_external_document_fetch",
        "materialization THS pacing",
    )
    _require_equal(
        pacing["sina_company_news"],
        "not_applicable_no_external_document_fetch",
        "materialization Sina pacing",
    )
    preflight = _object(manifest.get("runtime_start_preflight"), "runtime preflight")
    _require_equal(
        preflight,
        {
            "mode": "offline_rehearsal",
            "host_probe_performed": False,
            "reason": "not_applicable_offline_rehearsal",
        },
        "offline runtime preflight",
    )


def _aware_utc_instant(value: object, label: str) -> datetime:
    text = _string(value, label)
    try:
        observed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RehearsalV22ValidationError(f"{label} is not ISO-8601") from exc
    if observed.tzinfo is None or observed.utcoffset() != UTC.utcoffset(observed):
        raise RehearsalV22ValidationError(f"{label} is not timezone-aware UTC")
    return observed.astimezone(UTC)


def heldout_drafter_id() -> str:
    return "OpenAI Codex GPT-5"


def _jsonl_objects(payload: bytes, label: str) -> list[JsonObject]:
    result: list[JsonObject] = []
    for index, line in enumerate(payload.splitlines(), 1):
        if not line:
            continue
        result.append(
            _object(strict_json_loads(line, label=f"{label} line {index}"), f"{label} row")
        )
    return result


def _validate_prediction_timing(
    inference_state_payload: bytes,
    predictions: Sequence[Mapping[str, Any]],
) -> None:
    states = _jsonl_objects(inference_state_payload, "inference state")
    if len(states) != 2:
        raise RehearsalV22ValidationError("inference state is not exactly two events")
    started, completed = states
    if (
        started.get("status") != "inference_started"
        or completed.get("status") != "completed_all_eligible_candidates_once"
        or started.get("execution_id") != completed.get("execution_id")
    ):
        raise RehearsalV22ValidationError("inference state event sequence drifted")
    started_at = _aware_utc_instant(started.get("started_at_utc"), "inference start")
    completed_at = _aware_utc_instant(completed.get("completed_at_utc"), "inference completion")
    fixed = _aware_utc_instant("2026-08-10T12:30:00Z", "fixed rehearsal clock")
    if started_at != fixed or completed_at != fixed or completed_at < started_at:
        raise RehearsalV22ValidationError("inference timing differs from fixed clock")
    prior = started_at
    for index, prediction in enumerate(predictions, 1):
        recorded = _aware_utc_instant(
            prediction.get("recorded_at_utc"), f"prediction {index} recorded_at"
        )
        latency = prediction.get("latency_ms")
        if (
            recorded != fixed
            or recorded < prior
            or isinstance(latency, bool)
            or latency != 0
        ):
            raise RehearsalV22ValidationError("prediction timing or latency drifted")
        prior = recorded


def _contains_blind_leak(value: object) -> bool:
    forbidden = ("prediction", "stratum", "rank", "score", "sampling", "selection")
    if isinstance(value, dict):
        return any(
            any(token in str(key).casefold().replace("-", "_") for token in forbidden)
            or _contains_blind_leak(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_blind_leak(item) for item in value)
    return False


def _validate_artifact_semantics(
    payloads: Mapping[str, bytes],
    *,
    pipeline_implementation_commit: str,
) -> None:
    _validate_materialization_manifest(
        payloads["materialization_manifest"],
        pipeline_implementation_commit=pipeline_implementation_commit,
    )
    inputs = _jsonl_objects(payloads["materialized_inputs"], "candidate input")
    predictions = _jsonl_objects(payloads["predictions"], "prediction")
    blind = _jsonl_objects(payloads["owner_blind"], "blind row")
    drafts = _jsonl_objects(payloads["ai_draft"], "draft row")
    selection = _object(
        strict_json_loads(payloads["private_selection"], label="selection"),
        "selection",
    )
    counts = _object(
        _object(selection.get("selection"), "selection.selection").get("selected_counts"),
        "selected counts",
    )
    if (
        len(inputs) != 4048
        or len(predictions) != 4048
        or len(blind) != 60
        or len(drafts) != 60
        or counts.get("predicted_positive") != 40
        or counts.get("predicted_negative") != 20
        or counts.get("extract_failed") not in {None, 0}
        or counts.get("total") != 60
    ):
        raise RehearsalV22ValidationError("full-pool inference or 40/20 selection drifted")
    _validate_prediction_timing(payloads["inference_state"], predictions)
    if any(_contains_blind_leak(row) or row.get("gold") not in ({}, None) for row in blind):
        raise RehearsalV22ValidationError("blind artifact leaks selection data")
    if any(row.get("drafter_id") != heldout_drafter_id() for row in drafts):
        raise RehearsalV22ValidationError("AI draft independence identity drifted")
    for name in (
        "prediction_manifest",
        "owner_completion",
        "synthetic_report",
    ):
        _object(strict_json_loads(payloads[name], label=name), name)
    for name in ("owner_export", "human_adjudicated", "evaluation_state"):
        if not _jsonl_objects(payloads[name], name):
            raise RehearsalV22ValidationError(f"{name} is empty")


def _validate_run_archive(
    *,
    bundle_directory: Path,
    row: Mapping[str, Any],
    expected_label: str,
    expected_root: str,
) -> tuple[dict[str, bytes], dict[str, bytes], str]:
    _require_equal(row.get("run_label"), expected_label, f"{expected_label} label")
    _require_equal(row.get("archive_root"), expected_root, f"{expected_label} archive root")
    _require_equal(row.get("artifact_count"), 14, f"{expected_label} artifact count")
    artifacts = _array(row.get("artifacts"), f"{expected_label} artifacts")
    if len(artifacts) != 14:
        raise RehearsalV22ValidationError(f"{expected_label} does not have 14 artifacts")
    logical_payloads: dict[str, bytes] = {}
    relative_payloads: dict[str, bytes] = {}
    bundle_payloads: dict[str, bytes] = {}
    for index, raw in enumerate(artifacts):
        record = _object(raw, f"{expected_label} artifact {index}")
        _require_exact_keys(
            record,
            frozenset({"logical_name", "source_relative_path", "bytes", "sha256"}),
            f"{expected_label} artifact {index}",
        )
        logical = _string(record["logical_name"], f"{expected_label} logical name")
        relative = _relative(
            record["source_relative_path"], f"{expected_label} source relative path"
        )
        if logical in logical_payloads or relative in relative_payloads:
            raise RehearsalV22ValidationError(f"{expected_label} artifact collision")
        archived_relative = f"{expected_root}/{relative}"
        payload = _regular_bytes(
            _safe_path(bundle_directory, archived_relative, f"{expected_label} artifact"),
            f"{expected_label} artifact {logical}",
        )
        _require_equal(record["bytes"], len(payload), f"{expected_label} artifact bytes")
        _require_equal(record["sha256"], _sha256(payload), f"{expected_label} artifact SHA")
        logical_payloads[logical] = payload
        relative_payloads[relative] = payload
        bundle_payloads[archived_relative] = payload
    root = _path_merkle(
        relative_payloads,
        leaf_domain=b"p4.2a-rehearsal-leaf-v2.2\0",
    )
    _require_equal(row.get("artifact_merkle_root_sha256"), root, f"{expected_label} root")
    return logical_payloads, bundle_payloads, root


def _normalize_distribution_rows(
    raw_rows: Sequence[tuple[str, str]],
) -> list[JsonObject]:
    rows: list[JsonObject] = []
    names: list[str] = []
    for raw_name, version in raw_rows:
        if not raw_name or not version:
            raise RehearsalV22ValidationError(
                "package inventory contains an unnamed or unversioned distribution"
            )
        name = re.sub(r"[-_.]+", "-", raw_name).lower()
        names.append(name)
        rows.append({"name": name, "version": version})
    if len(names) != len(set(names)):
        raise RehearsalV22ValidationError(
            "package inventory contains duplicate normalized names"
        )
    rows.sort(key=lambda row: (cast(str, row["name"]), cast(str, row["version"])))
    return rows


def _independent_runtime_inventory() -> tuple[bytes, bytes]:
    """Rebuild the fixed Python/package inventory without producer helpers."""

    python_payload = _canonical_json_bytes(
        {
            "abi_flags": sys.abiflags,
            "cache_tag": sys.implementation.cache_tag,
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        }
    )
    if _sha256(python_payload) != (
        "ab3e067417027bb98ea4335e9086d2046ac9dfd4eaf857acc8622dc8f0a13a31"
    ):
        raise RehearsalV22ValidationError("active Python runtime inventory drifted")

    registered = REGISTERED_PROJECT_ROOT.absolute()
    venv_root = registered / ".venv"
    scheme = sysconfig.get_preferred_scheme("prefix")
    variables = {"base": venv_root.as_posix(), "platbase": venv_root.as_posix()}
    selected: list[Path] = []
    for key in ("purelib", "platlib"):
        raw = sysconfig.get_path(key, scheme=scheme, vars=variables)
        if not isinstance(raw, str) or not raw:
            raise RehearsalV22ValidationError(
                f"explicit sysconfig package root is unavailable: {key}"
            )
        candidate = Path(raw).absolute()
        try:
            metadata = candidate.lstat()
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise RehearsalV22ValidationError(
                "fixed registered package metadata root is unavailable"
            ) from exc
        if (
            candidate.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or resolved != candidate
            or not resolved.is_relative_to(registered)
        ):
            raise RehearsalV22ValidationError(
                "fixed registered package metadata root is aliased"
            )
        if resolved not in selected:
            selected.append(resolved)
    projected: list[str] = []
    for package_root in selected:
        projected.append(package_root.relative_to(registered).as_posix())
    if projected != [".venv/lib/python3.12/site-packages"] or _sha256(
        _canonical_json_bytes(projected)
    ) != "fae235892c0988d4093d1ad12b034a6126d116e436393e837a8b2f71601fbd12":
        raise RehearsalV22ValidationError(
            "fixed package metadata root binding drifted"
        )

    try:
        _normalize_distribution_rows(
            [("validator_probe.pkg", "1"), ("validator-probe-pkg", "2")]
        )
    except RehearsalV22ValidationError:
        pass
    else:
        raise RehearsalV22ValidationError(
            "duplicate package-name negative probe did not reject"
        )
    distributions = list(
        importlib.metadata.distributions(path=[path.as_posix() for path in selected])
    )
    raw_rows: list[tuple[str, str]] = []
    for distribution in distributions:
        raw_name = distribution.metadata["Name"]
        version = distribution.version
        if (
            not isinstance(raw_name, str)
            or not raw_name
            or not isinstance(version, str)
            or not version
        ):
            raise RehearsalV22ValidationError(
                "active package inventory contains unnamed or unversioned metadata"
            )
        raw_rows.append((raw_name, version))
    rows = _normalize_distribution_rows(raw_rows)
    if len(distributions) != 84 or len(rows) != 84:
        raise RehearsalV22ValidationError("active package inventory count drifted")
    package_payload = _canonical_json_bytes(rows)
    if _sha256(package_payload) != (
        "c3c7792eb31679c0eb7d3140e067d691df330cd3af302d2350bf15b74ac8ec42"
    ):
        raise RehearsalV22ValidationError("active package inventory bytes drifted")
    return python_payload, package_payload


def _validate_control_archive(
    *,
    project_root: Path,
    bundle_directory: Path,
    value: object,
    historical_anchor: implementation.HistoricalSelectedAnchor,
) -> tuple[dict[str, bytes], dict[str, bytes], str]:
    control = _object(value, "control archive")
    _require_equal(control.get("archive_root"), "archive/control-surface/root", "control root")
    files = _array(control.get("files"), "control archive files")
    manifest_record = _object(control.get("manifest"), "control manifest record")
    repository_payloads: dict[str, bytes] = {}
    tree_payloads: dict[str, bytes] = {}
    bundle_payloads: dict[str, bytes] = {}
    record_paths: set[str] = set()
    for index, raw in enumerate(files):
        record = _object(raw, f"control file {index}")
        _require_exact_keys(
            record,
            frozenset(
                {
                    "logical_name",
                    "bundle_relative_path",
                    "source_kind",
                    "repository_path",
                    "bytes",
                    "sha256",
                }
            ),
            f"control file {index}",
        )
        bundle_relative = _relative(
            record["bundle_relative_path"], f"control file {index} archive path"
        )
        if not bundle_relative.startswith("archive/control-surface/root/"):
            raise RehearsalV22ValidationError("control file is outside control root")
        if bundle_relative in record_paths:
            raise RehearsalV22ValidationError("control archive contains duplicate path")
        record_paths.add(bundle_relative)
        payload = _regular_bytes(
            _safe_path(bundle_directory, bundle_relative, f"control file {index}"),
            f"control file {index}",
        )
        _require_equal(record["bytes"], len(payload), f"control file {index} bytes")
        _require_equal(record["sha256"], _sha256(payload), f"control file {index} SHA")
        source_kind = _string(record["source_kind"], f"control file {index} kind")
        repository_path = record["repository_path"]
        if source_kind in {
            "python_source",
            "package_initializer",
            "frozen_control",
            "project_manifest",
            "lockfile",
        }:
            relative = _relative(repository_path, f"control file {index} repository path")
            expected_archive = f"archive/control-surface/root/repo/{relative}"
            if bundle_relative != expected_archive:
                raise RehearsalV22ValidationError("control repository/archive mapping drifted")
            governance = _CONTROL_GOVERNANCE_AUTHORITIES.get(relative)
            if governance is None:
                _validated_implementation_blob(
                    project_root=project_root,
                    historical_anchor=historical_anchor,
                    relative_path=relative,
                    expected_sha256=_sha256(payload),
                )
            else:
                digest, creating_commit, _require_worktree = governance
                reference = {
                    "path": relative,
                    "sha256": digest,
                    "creating_commit": creating_commit,
                    "unique_a_history_verified": True,
                }
                if relative == INDEPENDENT_REVIEW_RELATIVE.as_posix():
                    execution_head = _git_bytes(
                        project_root,
                        "rev-parse",
                        "HEAD",
                    ).decode("ascii", errors="strict").strip()
                    creating_payload = _validate_initial_sibling_authority(
                        project_root,
                        reference,
                        execution_head=execution_head,
                        require_worktree=False,
                    )
                else:
                    creating_payload = _unique_a_authority(
                        project_root,
                        reference,
                        require_worktree=False,
                    )
                if payload != creating_payload:
                    raise RehearsalV22ValidationError(
                        f"governance control creation bytes drifted: {relative}"
                    )
            repository_payloads[relative] = payload
        elif source_kind in {"python_runtime", "package_inventory"}:
            if repository_path is not None:
                raise RehearsalV22ValidationError("runtime control has repository path")
        else:
            raise RehearsalV22ValidationError("control source kind is unregistered")
        tree_payloads[bundle_relative] = payload
        bundle_payloads[bundle_relative] = payload
    _require_equal(control.get("file_count"), len(files), "control file count")
    _require_equal(control.get("tree_member_count"), len(files) + 1, "control tree count")
    _require_equal(
        control.get("tree_member_count_rule"),
        "tree_member_count == file_count + 1",
        "control tree count rule",
    )
    _require_equal(control.get("manifest_included_in_merkle"), True, "control manifest Merkle")
    manifest_relative = "archive/control-surface/manifest.json"
    manifest_payload = _regular_bytes(
        _safe_path(bundle_directory, manifest_relative, "control manifest"),
        "control manifest",
    )
    _require_equal(
        manifest_record,
        {
            "logical_name": "control_surface_manifest",
            "bundle_relative_path": manifest_relative,
            "source_kind": "control_manifest",
            "repository_path": None,
            "bytes": len(manifest_payload),
            "sha256": _sha256(manifest_payload),
        },
        "control manifest record",
    )
    manifest_document = _strict_canonical_json_loads(
        manifest_payload, label="control surface manifest"
    )
    if set(manifest_document) != {"schema_version", "files"} or manifest_document["files"] != files:
        raise RehearsalV22ValidationError("control surface manifest content drifted")
    if manifest_document["schema_version"] != CONTROL_MANIFEST_SCHEMA:
        raise RehearsalV22ValidationError("control manifest schema version drifted")
    tree_payloads[manifest_relative] = manifest_payload
    bundle_payloads[manifest_relative] = manifest_payload
    control_root = _path_merkle(
        tree_payloads,
        leaf_domain=b"p4.2a-rehearsal-leaf-v2.2\0",
    )
    _require_equal(control.get("merkle_root_sha256"), control_root, "control Merkle root")
    referenced = _array(
        control.get("referenced_pass_test_paths"), "referenced PASS test paths"
    )
    if len(referenced) != _integer(
        control.get("referenced_pass_test_count"), "referenced PASS test count", minimum=1
    ):
        raise RehearsalV22ValidationError("referenced PASS test count drifted")
    seen_tests: set[str] = set()
    for raw in referenced:
        relative = _relative(raw, "referenced PASS test")
        if not relative.startswith("tests/") or relative not in repository_payloads:
            raise RehearsalV22ValidationError("referenced PASS test is not archived")
        seen_tests.add(relative)
    if len(seen_tests) != len(referenced):
        raise RehearsalV22ValidationError("referenced PASS tests collide")
    _require_equal(
        control.get("all_referenced_pass_tests_archived"),
        True,
        "referenced PASS test archive",
    )
    for relative, (digest, _creating_commit, _require_worktree) in (
        _CONTROL_GOVERNANCE_AUTHORITIES.items()
    ):
        governance_payload = repository_payloads.get(relative)
        if governance_payload is None or _sha256(governance_payload) != digest:
            raise RehearsalV22ValidationError(
                f"control archive omitted frozen governance bytes: {relative}"
            )
    for _status, relative in _V2_1_IMPLEMENTATION_SURFACE:
        implementation_payload = repository_payloads.get(relative)
        if implementation_payload is None or implementation_payload != _git_blob(
            project_root,
            _V2_1_IMPLEMENTATION_COMMIT,
            relative,
        ):
            raise RehearsalV22ValidationError(
                f"control archive omitted v2.1 implementation bytes: {relative}"
            )
    independent_closure = _independent_local_import_closure(
        project_root=project_root,
        implementation_commit=historical_anchor.selected_commit,
    )
    for relative, payload in independent_closure.items():
        if repository_payloads.get(relative) != payload:
            raise RehearsalV22ValidationError(
                f"control archive omitted independently derived AST closure: {relative}"
            )
    independent_python, independent_packages = _independent_runtime_inventory()
    if (
        tree_payloads.get("archive/control-surface/root/runtime/python.json")
        != independent_python
        or tree_payloads.get("archive/control-surface/root/runtime/packages.json")
        != independent_packages
    ):
        raise RehearsalV22ValidationError(
            "control archive differs from the independently derived runtime inventory"
        )
    implementation_surface = implementation.build_control_surface(
        project_root,
        historical_anchor.selected_commit,
        require_current=False,
    )
    if (
        implementation_surface != historical_anchor.control_surface
        or implementation_surface.implementation_commit
        != historical_anchor.selected_commit
        or list(implementation_surface.records) != files
        or dict(implementation_surface.payloads)
        != {
            relative: payload
            for relative, payload in tree_payloads.items()
            if relative != manifest_relative
        }
        or implementation_surface.manifest_payload != manifest_payload
        or implementation_surface.merkle_root_sha256 != control_root
        or implementation_surface.python_inventory
        != tree_payloads["archive/control-surface/root/runtime/python.json"]
        or implementation_surface.package_inventory
        != tree_payloads["archive/control-surface/root/runtime/packages.json"]
        or tuple(implementation_surface.ast_closure_paths)
        != tuple(independent_closure)
        or not set(implementation_surface.loaded_repository_sources).issubset(
            implementation_surface.ast_closure_paths
        )
        or not set(implementation_surface.ast_closure_paths).issubset(
            repository_payloads
        )
    ):
        raise RehearsalV22ValidationError(
            "independent control replay differs from the implementation control surface"
        )
    return repository_payloads, bundle_payloads, control_root


def _validate_archives(
    *,
    project_root: Path,
    bundle_directory: Path,
    bundle: Mapping[str, Any],
    historical_anchor: implementation.HistoricalSelectedAnchor,
) -> ArchiveReplay:
    archive = _object(bundle.get("archive"), "bundle archive")
    runs = _array(archive.get("runs"), "bundle run archives")
    if len(runs) != 2:
        raise RehearsalV22ValidationError("bundle run archive count is not two")
    run_a, run_a_files, run_a_root = _validate_run_archive(
        bundle_directory=bundle_directory,
        row=_object(runs[0], "run-a archive"),
        expected_label="run-a",
        expected_root="archive/run-a/root",
    )
    run_b, run_b_files, run_b_root = _validate_run_archive(
        bundle_directory=bundle_directory,
        row=_object(runs[1], "run-b archive"),
        expected_label="run-b",
        expected_root="archive/run-b/root",
    )
    if set(run_a) != set(run_b) or any(run_a[name] != run_b[name] for name in run_a):
        raise RehearsalV22ValidationError("two selected runs are not 14/14 byte-identical")
    if set(run_a) != {
        "materialized_inputs",
        "materialization_manifest",
        "inference_state",
        "predictions",
        "prediction_manifest",
        "private_selection",
        "owner_blind",
        "ai_draft",
        "adjudication_ui",
        "owner_export",
        "human_adjudicated",
        "owner_completion",
        "evaluation_state",
        "synthetic_report",
    }:
        raise RehearsalV22ValidationError("selected run artifact inventory drifted")
    _validate_artifact_semantics(
        run_a,
        pipeline_implementation_commit=_V2_1_IMPLEMENTATION_COMMIT,
    )
    controls, control_files, control_root = _validate_control_archive(
        project_root=project_root,
        bundle_directory=bundle_directory,
        value=archive.get("control_surface"),
        historical_anchor=historical_anchor,
    )
    return ArchiveReplay(
        run_a=run_a,
        run_b=run_b,
        run_a_root_sha256=run_a_root,
        run_b_root_sha256=run_b_root,
        control_root_sha256=control_root,
        control_repository_payloads=controls,
        all_payloads={**run_a_files, **run_b_files, **control_files},
    )


def _validate_lineage(
    *,
    project_root: Path,
    bundle: Mapping[str, Any],
    historical_anchor: implementation.HistoricalSelectedAnchor,
) -> str:
    lineage = _object(bundle.get("lineage"), "bundle lineage")
    selected_commit = historical_anchor.selected_commit
    expected_refs = {
        "preregistration": (PREREGISTRATION_RELATIVE.as_posix(), PREREGISTRATION_SHA256),
        "bundle_schema": (BUNDLE_SCHEMA_RELATIVE.as_posix(), BUNDLE_SCHEMA_SHA256),
        "release_authorization_schema": (
            RELEASE_SCHEMA_RELATIVE.as_posix(),
            RELEASE_SCHEMA_SHA256,
        ),
    }
    for key, (path, digest) in expected_refs.items():
        reference = _validate_file_ref(lineage.get(key), f"bundle lineage {key}")
        _require_equal(reference, {"path": path, "sha256": digest}, f"bundle lineage {key}")
        if _sha256(_git_blob(project_root, selected_commit, path)) != digest:
            raise RehearsalV22ValidationError(
                f"historical selected lineage bytes drifted: {key}"
            )
    preregistration_commit = _commit(
        lineage.get("preregistration_commit"), "bundle preregistration commit"
    )
    _require_equal(
        preregistration_commit,
        INITIAL_REVIEWED_COMMIT,
        "bundle preregistration commit",
    )
    _require_equal(
        lineage.get("implementation_commit"),
        historical_anchor.selected_commit,
        "bundle implementation commit",
    )
    for key in (
        "parent_heldout_preregistration",
        "parent_rehearsal_v2_preregistration",
        "parent_rehearsal_v2_bundle_schema",
        "parent_rehearsal_v2_bundle",
        "parent_rehearsal_v2_review_request",
        "parent_rehearsal_v2_approval",
        "frame_authority_ruling",
        "successor_v2_1_authorization",
        "full_pool_cost_acceptance",
        "same_publisher_interval_basis",
        "v1_incident",
        "design",
        "heldout_contract",
        "round3_prompt",
        "round3_plus_contract",
    ):
        reference = _validate_file_ref(lineage.get(key), f"bundle lineage {key}")
        payload = _git_blob(
            project_root,
            selected_commit,
            cast(str, reference["path"]),
        )
        if _sha256(payload) != reference["sha256"]:
            raise RehearsalV22ValidationError(f"bundle lineage {key} bytes drifted")
    retired = _array(lineage.get("retired_v1_artifacts"), "retired v1 artifacts")
    for index, raw in enumerate(retired):
        reference = _validate_file_ref(raw, f"retired v1 artifact {index}")
        payload = _git_blob(
            project_root,
            selected_commit,
            cast(str, reference["path"]),
        )
        if _sha256(payload) != reference["sha256"]:
            raise RehearsalV22ValidationError("retired v1 artifact drifted")
    for key, (path, digest, creating_commit) in _CARRY_FORWARD_AUTHORITIES.items():
        reference = _validate_authority_ref(lineage.get(key), f"bundle lineage {key}")
        _require_equal(
            reference,
            {
                "path": path,
                "sha256": digest,
                "creating_commit": creating_commit,
                "unique_a_history_verified": True,
            },
            f"bundle carry-forward lineage {key}",
        )
        _unique_a_authority(
            project_root,
            reference,
            require_worktree=False,
        )
    authority_chain = {
        "v2_2_remediation_request": _V2_2_REMEDIATION_AUTHORITY,
        "v2_2_preregistration_scope_authorization": _V2_2_SCOPE_AUTHORITY,
    }
    for key, (path, digest, creating_commit) in authority_chain.items():
        reference = _validate_authority_ref(lineage.get(key), f"bundle lineage {key}")
        _require_equal(
            reference,
            {
                "path": path,
                "sha256": digest,
                "creating_commit": creating_commit,
                "unique_a_history_verified": True,
            },
            f"bundle lineage {key}",
        )
        _unique_a_authority(project_root, reference, require_worktree=False)
    incident_commit = _CARRY_FORWARD_AUTHORITIES[
        "v2_1_consumed_attempt_incident"
    ][2]
    for child, parent, path in (
        (
            _V2_2_REMEDIATION_AUTHORITY[2],
            incident_commit,
            _V2_2_REMEDIATION_AUTHORITY[0],
        ),
        (
            _V2_2_SCOPE_AUTHORITY[2],
            _V2_2_REMEDIATION_AUTHORITY[2],
            _V2_2_SCOPE_AUTHORITY[0],
        ),
    ):
        if _git_parents(project_root, child) != (parent,) or set(
            _diff_name_status(project_root, parent, child)
        ) != {("A", path)}:
            raise RehearsalV22ValidationError(
                "v2.2 remediation/scope authority topology drifted"
            )
    _require_equal(
        lineage.get("v2_1_implementation_commit"),
        _V2_1_IMPLEMENTATION_COMMIT,
        "v2.1 implementation commit",
    )
    if (
        _git_parents(project_root, _V2_1_IMPLEMENTATION_COMMIT)
        != (_V2_1_IMPLEMENTATION_PARENT,)
        or tuple(
            sorted(
                _diff_name_status(
                    project_root,
                    _V2_1_IMPLEMENTATION_PARENT,
                    _V2_1_IMPLEMENTATION_COMMIT,
                ),
                key=lambda row: row[1].encode("utf-8"),
            )
        )
        != _V2_1_IMPLEMENTATION_SURFACE
    ):
        raise RehearsalV22ValidationError("v2.1 implementation exact surface drifted")
    prereg_reference = {
        "path": PREREGISTRATION_RELATIVE.as_posix(),
        "sha256": PREREGISTRATION_SHA256,
        "creating_commit": preregistration_commit,
        "unique_a_history_verified": True,
    }
    _unique_a_authority(project_root, prereg_reference, require_worktree=False)
    preregistration_parent = _V2_2_SCOPE_AUTHORITY[2]
    if _git_parents(project_root, preregistration_commit) != (
        preregistration_parent,
    ):
        raise RehearsalV22ValidationError("v2.2 preregistration parent drifted")
    prereg_surface = {
        ("A", PREREGISTRATION_RELATIVE.as_posix()),
        ("A", BUNDLE_SCHEMA_RELATIVE.as_posix()),
        ("A", RELEASE_SCHEMA_RELATIVE.as_posix()),
    }
    if set(
        _diff_name_status(
            project_root,
            preregistration_parent,
            preregistration_commit,
        )
    ) != prereg_surface:
        raise RehearsalV22ValidationError("v2.2 preregistration is not exact 3A")
    for relative, digest in (
        (BUNDLE_SCHEMA_RELATIVE.as_posix(), BUNDLE_SCHEMA_SHA256),
        (RELEASE_SCHEMA_RELATIVE.as_posix(), RELEASE_SCHEMA_SHA256),
    ):
        if _sha256(_git_blob(project_root, preregistration_commit, relative)) != digest:
            raise RehearsalV22ValidationError("v2.2 schema creation blob drifted")
    return preregistration_commit


def _validate_harness_identity(
    *,
    project_root: Path,
    bundle: Mapping[str, Any],
    historical_anchor: implementation.HistoricalSelectedAnchor,
) -> None:
    identity = _object(bundle.get("harness_identity"), "bundle harness identity")
    expected_paths = {
        "thin_main_shim": "scripts/rehearse_p4_2a_v2_2_heldout_full_path.py",
        "implementation_module": "scripts/p4_2a_v2_2_heldout_rehearsal.py",
        "validator_module": "scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py",
    }
    for key, expected_path in expected_paths.items():
        reference = _validate_file_ref(identity.get(key), f"harness {key}")
        _require_equal(reference["path"], expected_path, f"harness {key} path")
        payload = _git_blob(
            project_root,
            historical_anchor.selected_commit,
            expected_path,
        )
        _require_equal(reference["sha256"], _sha256(payload), f"harness {key} SHA")
        _validated_implementation_blob(
            project_root=project_root,
            historical_anchor=historical_anchor,
            relative_path=expected_path,
            expected_sha256=_sha256(payload),
        )
    expected_scalars = {
        "implementation_module_name": "scripts.p4_2a_v2_2_heldout_rehearsal",
        "validator_module_name": "scripts.validate_p4_2a_v2_2_heldout_rehearsal_bundle",
        "authority_owner_module": "scripts.p4_2a_v2_2_heldout_rehearsal",
        "shim_has_authority_state": False,
        "validator_import_target": "scripts.p4_2a_v2_2_heldout_rehearsal",
        "module_object_identity_equal": True,
        "exact_os_bootstrap_passed": True,
        "implementation_direct_execution_rejected": True,
        "second_authority_module_rejected": True,
        "delegation_binding_passed": "identity_root_creator_owner_and_lifetime_exact",
    }
    for key, expected in expected_scalars.items():
        _require_equal(identity.get(key), expected, f"harness identity {key}")


def _validate_history_summary(
    *,
    bundle: Mapping[str, Any],
    binding: BindingView,
    replay: HistoryReplay,
    archives: ArchiveReplay,
) -> None:
    history = _object(bundle.get("attempt_history"), "bundle attempt history")
    expected = {
        "series_id": REHEARSAL_ID,
        "series_token_sha256": binding.series_token_sha256,
        "ledger_root": binding.ledger_root.as_posix(),
        "policy": SERIES_POLICY,
        "attempt_limit": "unbounded_until_first_validated_success_or_owner_abandonment",
        "started_count": replay.started_count,
        "failed_count": replay.failed_count,
        "incomplete_count": replay.incomplete_count,
        "validated_candidate_count": 1,
        "selected_attempt_ordinal": replay.selected_attempt_ordinal,
        "series_closed": True,
        "records": list(replay.records),
        "history_root_sha256": replay.history_root_sha256,
        "live_ledger_root_sha256": replay.live_ledger_root_sha256,
        "ordinals_contiguous": True,
        "no_gap_duplicate_or_reorder": True,
        "no_unarchived_attempt": True,
        "no_attempt_after_selected_success": True,
        "first_validated_success_is_selected": True,
    }
    _require_equal(history, expected, "bundle attempt history")
    merkle = _object(bundle.get("merkle"), "bundle Merkle")
    root_expected = {
        "run_a_root_sha256": archives.run_a_root_sha256,
        "run_b_root_sha256": archives.run_b_root_sha256,
        "control_surface_root_sha256": archives.control_root_sha256,
        "attempt_history_root_sha256": replay.history_root_sha256,
        "live_ledger_root_sha256": replay.live_ledger_root_sha256,
    }
    for key, expected_value in root_expected.items():
        _require_equal(merkle.get(key), expected_value, f"bundle Merkle {key}")
    selected_candidate = replay.source_records[replay.selected_attempt_ordinal - 1][1]
    if selected_candidate is None:
        raise RehearsalV22ValidationError("selected attempt lacks candidate source")
    _require_equal(
        selected_candidate["run_a_root_sha256"],
        archives.run_a_root_sha256,
        "selected candidate run-a root",
    )
    _require_equal(
        selected_candidate["run_b_root_sha256"],
        archives.run_b_root_sha256,
        "selected candidate run-b root",
    )
    _require_equal(
        selected_candidate["control_surface_root_sha256"],
        archives.control_root_sha256,
        "selected candidate control root",
    )
    bundle_root = hashlib.sha256(
        b"p4.2a-rehearsal-bundle-v2.2\0"
        + bytes.fromhex(replay.history_root_sha256)
        + bytes.fromhex(archives.run_a_root_sha256)
        + bytes.fromhex(archives.run_b_root_sha256)
        + bytes.fromhex(archives.control_root_sha256)
    ).hexdigest()
    _require_equal(merkle.get("bundle_root_sha256"), bundle_root, "bundle root")


def _validate_historical_selected_anchor(
    *,
    project_root: Path,
    historical_anchor: implementation.HistoricalSelectedAnchor,
    replay: HistoryReplay,
    archives: ArchiveReplay,
) -> None:
    anchor = _historical_selected_anchor(historical_anchor)
    _require_equal(
        (anchor.selected_epoch, anchor.selected_commit),
        (replay.selected_implementation_epoch, replay.selected_implementation_commit),
        "historical selected epoch/commit",
    )
    _require_equal(
        anchor.history_root_sha256,
        replay.history_root_sha256,
        "historical selected history root",
    )
    _require_equal(
        anchor.live_ledger_root_sha256,
        replay.live_ledger_root_sha256,
        "historical selected live-ledger root",
    )
    _require_equal(
        anchor.control_surface.merkle_root_sha256,
        archives.control_root_sha256,
        "historical selected control root",
    )
    started, candidate, terminal = replay.source_records[
        replay.selected_attempt_ordinal - 1
    ]
    if candidate is None or terminal is None:
        raise RehearsalV22ValidationError(
            "historical selected anchor lacks sealed candidate/terminal"
        )
    expected_action = anchor.owner_action_time_authorization.as_json()
    _require_equal(
        started.get("owner_action_time_authorization"),
        expected_action,
        "historical selected action authorization",
    )
    for label, observed, expected in (
        (
            "historical selected evidence root",
            candidate.get("evidence_tree_root_sha256"),
            anchor.evidence_tree_root_sha256,
        ),
        (
            "historical selected terminal evidence root",
            terminal.get("evidence_tree_root_sha256"),
            anchor.evidence_tree_root_sha256,
        ),
        (
            "historical selected candidate content root",
            candidate.get("candidate_content_root_sha256"),
            anchor.candidate_content_root_sha256,
        ),
        (
            "historical selected run-a root",
            candidate.get("run_a_root_sha256"),
            anchor.run_a_root_sha256,
        ),
        (
            "historical selected run-b root",
            candidate.get("run_b_root_sha256"),
            anchor.run_b_root_sha256,
        ),
    ):
        _require_equal(observed, expected, label)
    observed_blob_map = {
        relative: _sha256(payload)
        for relative, payload in archives.control_repository_payloads.items()
    }
    expected_repository_paths = {
        cast(str, record["repository_path"])
        for record in anchor.control_surface.records
        if record.get("repository_path") is not None
    }
    if set(observed_blob_map) != expected_repository_paths:
        raise RehearsalV22ValidationError(
            "historical selected control repository inventory drifted"
        )
    _require_equal(
        dict(anchor.selected_git_blob_sha256),
        observed_blob_map,
        "historical selected Git-blob map",
    )


def _validate_implementation_review_document(
    *,
    document: Mapping[str, Any],
    implementation_commit: str,
    label: str,
) -> None:
    """Require an unambiguous post-implementation approval document."""

    commit = _commit(implementation_commit, f"{label} implementation commit")
    _require_equal(document.get("reviewed_commit"), commit, f"{label} target")
    verdict = _string(document.get("verdict"), f"{label} verdict")
    verdict_tokens = tuple(verdict.split("_"))
    negative_tokens = {
        "BLOCK",
        "BLOCKED",
        "CONDITIONAL",
        "DENIED",
        "DENY",
        "DISAPPROVE",
        "FAIL",
        "FAILED",
        "FAILURE",
        "INCOMPLETE",
        "NO",
        "NON",
        "NOT",
        "PARTIAL",
        "PENDING",
        "REJECT",
        "REJECTED",
    }
    if (
        re.fullmatch(r"[A-Z0-9]+(?:_[A-Z0-9]+)*", verdict) is None
        or not verdict_tokens
        or verdict_tokens[0] != "APPROVE"
        or "IMPLEMENTATION" not in verdict_tokens
        or not negative_tokens.isdisjoint(verdict_tokens)
        or document.get("blockers") not in (None, [])
    ):
        raise RehearsalV22ValidationError(
            f"{label} did not unambiguously approve implementation"
        )


def _validate_void_epoch_one(
    *,
    project_root: Path,
    epoch: Mapping[str, Any],
    execution_head: str,
) -> None:
    if not _is_void_epoch_one(epoch):
        raise RehearsalV22ValidationError("void epoch 1 marker drifted")
    implementation_commit = _git_commit(
        project_root,
        VOID_EPOCH_ONE_IMPLEMENTATION_COMMIT,
        "void epoch implementation commit",
    )
    if (
        _git_parents(project_root, implementation_commit)
        != (VOID_EPOCH_ONE_IMPLEMENTATION_PARENT,)
        or _diff_name_status(
            project_root,
            VOID_EPOCH_ONE_IMPLEMENTATION_PARENT,
            implementation_commit,
        )
        != (
            ("M", "scripts/p4_2a_v2_2_heldout_rehearsal.py"),
            ("M", "tests/test_p4_2a_v2_2_heldout_rehearsal_runner.py"),
        )
        or not _git_is_ancestor(project_root, implementation_commit, execution_head)
    ):
        raise RehearsalV22ValidationError("void epoch implementation topology drifted")
    owner = _validate_authority_ref(
        epoch["owner_exact_surface_authorization"],
        "void epoch owner authority",
    )
    _validate_initial_sibling_authority(
        project_root,
        owner,
        execution_head=execution_head,
        require_worktree=False,
    )
    adjudication = _validate_authority_ref(
        epoch["independent_implementation_review"],
        "void epoch adjudication",
    )
    adjudication_payload = _unique_a_authority(
        project_root,
        adjudication,
        require_worktree=False,
    )
    if not _git_is_ancestor(
        project_root,
        VOID_EPOCH_ONE_ADJUDICATION_COMMIT,
        execution_head,
    ):
        raise RehearsalV22ValidationError("void epoch adjudication is outside lineage")
    adjudication_document = _object(
        strict_json_loads(adjudication_payload, label="void epoch adjudication"),
        "void epoch adjudication",
    )
    correction = _object(
        adjudication_document.get("part_2_epoch_numbering_correction"),
        "void epoch numbering correction",
    )
    _require_equal(
        correction.get("ruling"),
        list(VOID_EPOCH_ONE_RULING),
        "void epoch structural-unconsumability ruling",
    )
    remediation_review = {
        "path": VOID_EPOCH_ONE_REVIEW_RELATIVE.as_posix(),
        "sha256": VOID_EPOCH_ONE_REVIEW_SHA256,
        "creating_commit": VOID_EPOCH_ONE_REVIEW_COMMIT,
        "unique_a_history_verified": True,
    }
    review_payload = _unique_a_authority(
        project_root,
        remediation_review,
        require_worktree=False,
    )
    review_document = _object(
        strict_json_loads(review_payload, label="void epoch implementation review"),
        "void epoch implementation review",
    )
    _validate_implementation_review_document(
        document=review_document,
        implementation_commit=implementation_commit,
        label="void epoch implementation review",
    )
    landing = _git_commit(
        project_root,
        VOID_EPOCH_ONE_LANDING_COMMIT,
        "void epoch merge-only landing",
    )
    if (
        _git_parents(project_root, landing)
        != (VOID_EPOCH_ONE_REVIEW_COMMIT, implementation_commit)
        or not _git_is_ancestor(project_root, landing, execution_head)
    ):
        raise RehearsalV22ValidationError("void epoch merge-only landing drifted")
    control = implementation.build_control_surface(
        project_root,
        implementation_commit,
        require_current=False,
    )
    if (
        control.implementation_commit != implementation_commit
        or control.merkle_root_sha256 != epoch["control_merkle_root_sha256"]
        or control.loaded_repository_sources
        or not set(control.ast_closure_paths).issubset(
            record["repository_path"]
            for record in control.records
            if record["repository_path"] is not None
        )
    ):
        raise RehearsalV22ValidationError("void epoch control surface replay drifted")


def _validate_void_epoch_three(
    *,
    project_root: Path,
    epoch: Mapping[str, Any],
    execution_head: str,
) -> None:
    """Re-prove the one exact structurally superseded epoch-3 sentinel."""

    if not _is_void_epoch_three(epoch):
        raise RehearsalV22ValidationError("void epoch 3 marker drifted")
    root = project_root.resolve(strict=True)
    head = _git_commit(root, execution_head, "void epoch 3 execution HEAD")
    implementation_commit = _git_commit(
        root,
        VOID_EPOCH_THREE_IMPLEMENTATION_COMMIT,
        "void epoch 3 implementation commit",
    )
    exact_surface = (
        ("M", "scripts/p4_2a_v2_2_heldout_rehearsal.py"),
        ("M", "scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py"),
        ("M", "tests/test_p4_2a_v2_2_heldout_rehearsal_runner.py"),
        ("M", "tests/test_p4_2a_v2_2_heldout_rehearsal_validator.py"),
    )
    if (
        _git_parents(root, implementation_commit)
        != (VOID_EPOCH_THREE_IMPLEMENTATION_PARENT,)
        or _diff_name_status(
            root,
            VOID_EPOCH_THREE_IMPLEMENTATION_PARENT,
            implementation_commit,
        )
        != exact_surface
        or not _git_is_ancestor(root, implementation_commit, head)
    ):
        raise RehearsalV22ValidationError("void epoch 3 implementation topology drifted")

    owner = _validate_authority_ref(
        epoch["owner_exact_surface_authorization"],
        "void epoch 3 owner authority",
    )
    owner_payload = _unique_a_authority(root, owner, require_worktree=False)
    owner_document = _object(
        strict_json_loads(owner_payload, label="void epoch 3 owner authority"),
        "void epoch 3 owner authority",
    )
    _require_equal(
        owner_document,
        {
            "schema_version": (
                "p4.2a-v2-2-implementation-epoch-surface-authorization-v1"
            ),
            "verdict": "APPROVE_EXACT_V2_2_IMPLEMENTATION_EPOCH_SURFACE",
            "owner": {"identity": "ouyang", "approved": True},
            "implementation_epoch": 3,
            "base_commit": _object(owner_document, "void epoch 3 owner authority")[
                "base_commit"
            ],
            "exact_surface": [
                {"path": path, "status": status_value}
                for status_value, path in exact_surface
            ],
        },
        "void epoch 3 owner authority document",
    )
    authority_base = _git_commit(
        root,
        owner_document["base_commit"],
        "void epoch 3 authority base",
    )
    if _git_parents(root, VOID_EPOCH_THREE_IMPLEMENTATION_PARENT) != (authority_base,):
        raise RehearsalV22ValidationError(
            "void epoch 3 authority is not the base's direct child"
        )

    review_reference = {
        "path": VOID_EPOCH_THREE_REVIEW_RELATIVE.as_posix(),
        "sha256": VOID_EPOCH_THREE_REVIEW_SHA256,
        "creating_commit": VOID_EPOCH_THREE_REVIEW_COMMIT,
        "unique_a_history_verified": True,
    }
    review_commit = _git_commit(
        root,
        VOID_EPOCH_THREE_REVIEW_COMMIT,
        "void epoch 3 real implementation review",
    )
    review_path = VOID_EPOCH_THREE_REVIEW_RELATIVE.as_posix()
    if (
        _git_parents(root, review_commit) != (implementation_commit,)
        or _diff_name_status(root, implementation_commit, review_commit)
        != (("A", review_path),)
    ):
        raise RehearsalV22ValidationError("void epoch 3 review topology drifted")
    review_payload = _git_blob(root, review_commit, review_path)
    if _sha256(review_payload) != VOID_EPOCH_THREE_REVIEW_SHA256:
        raise RehearsalV22ValidationError("void epoch 3 review SHA drifted")
    _validate_authority_ref(review_reference, "void epoch 3 real review reference")
    _validate_implementation_review_document(
        document=_object(
            strict_json_loads(review_payload, label="void epoch 3 real review"),
            "void epoch 3 real review",
        ),
        implementation_commit=implementation_commit,
        label="void epoch 3 real review",
    )
    landing = _git_commit(root, VOID_EPOCH_THREE_LANDING_COMMIT, "void epoch 3 landing")
    review_touches = _path_history_touches(root, relative=review_path)
    expected_review_touches = {
        (VOID_EPOCH_THREE_REVIEW_COMMIT, "A", (review_path,)),
        (VOID_EPOCH_THREE_LANDING_COMMIT, "A", (review_path,)),
    }
    if (
        _git_parents(root, landing) != VOID_EPOCH_THREE_LANDING_PARENTS
        or len(review_touches) != 2
        or set(review_touches) != expected_review_touches
        or _path_diff_name_status(
            root,
            base=VOID_EPOCH_THREE_LANDING_PARENTS[0],
            commit=landing,
            relative=review_path,
        )
        != (("A", review_path),)
        or _path_diff_name_status(
            root,
            base=VOID_EPOCH_THREE_REVIEW_COMMIT,
            commit=landing,
            relative=review_path,
        )
        != ()
        or _git_blob(root, landing, review_path) != review_payload
        or not _git_is_ancestor(root, landing, head)
    ):
        raise RehearsalV22ValidationError(
            "void epoch 3 source/projection review history drifted"
        )

    ruling_references = (
        (
            "void epoch 3 corrected-sibling adjudication",
            {
                "path": VOID_EPOCH_THREE_GATE_ADJUDICATION_RELATIVE.as_posix(),
                "sha256": VOID_EPOCH_THREE_GATE_ADJUDICATION_SHA256,
                "creating_commit": VOID_EPOCH_THREE_GATE_ADJUDICATION_COMMIT,
                "unique_a_history_verified": True,
            },
        ),
        (
            "void epoch 3 re-anchor companion",
            {
                "path": VOID_EPOCH_THREE_REANCHOR_RELATIVE.as_posix(),
                "sha256": VOID_EPOCH_THREE_REANCHOR_SHA256,
                "creating_commit": VOID_EPOCH_THREE_REANCHOR_COMMIT,
                "unique_a_history_verified": True,
            },
        ),
        (
            "void epoch 3 reason discriminator",
            {
                "path": VOID_EPOCH_THREE_REASON_RELATIVE.as_posix(),
                "sha256": VOID_EPOCH_THREE_REASON_SHA256,
                "creating_commit": VOID_EPOCH_THREE_REASON_COMMIT,
                "unique_a_history_verified": True,
            },
        ),
    )
    _require_equal(
        epoch["independent_implementation_review"],
        ruling_references[2][1],
        "void epoch 3 reason discriminator reference",
    )
    ruling_documents: list[JsonObject] = []
    for label, raw_reference in ruling_references:
        reference = _validate_authority_ref(raw_reference, label)
        payload = _unique_a_authority(root, reference, require_worktree=False)
        if not _git_is_ancestor(root, cast(str, reference["creating_commit"]), head):
            raise RehearsalV22ValidationError(f"{label} is outside execution lineage")
        ruling_documents.append(
            _object(strict_json_loads(payload, label=label), label)
        )
    gate_document, reanchor_document, reason_document = ruling_documents
    _require_equal(
        _object(gate_document.get("part_3_rulings"), "epoch 3 gate rulings").get(
            "supersession_authorized"
        ),
        VOID_EPOCH_THREE_GATE_SUPERSESSION_RULING,
        "void epoch 3 corrected sibling ruling",
    )
    _require_equal(
        _object(
            reanchor_document.get("part_1_why_epoch_4_is_necessary"),
            "epoch 4 re-anchor reason",
        ).get("trigger"),
        VOID_EPOCH_THREE_REANCHOR_TRIGGER,
        "void epoch 3 structural re-anchor trigger",
    )
    attempt_finding = _object(
        reason_document.get("part_1_attempt_2_adjudicated"),
        "attempt-2 epoch-gap finding",
    )
    _require_equal(
        attempt_finding.get("verdict"),
        "VALID_SERIES_CLOSING_SUCCESS_WITH_FAILED_BUNDLE_CONSTRUCTION",
        "void epoch 3 reason verdict",
    )
    _require_equal(
        attempt_finding.get("what_failed"),
        VOID_EPOCH_THREE_REASON_FINDING,
        "void epoch 3 exact [2,4] reason",
    )
    required_content = _array(
        _object(
            reason_document.get("part_5_epoch5_design_requirements"),
            "epoch-5 design requirements",
        ).get("required_content"),
        "epoch-5 required content",
    )
    if not any(
        isinstance(item, str)
        and "void-epoch-3 disclosure" in item
        and "exact-match sentinel" in item
        for item in required_content
    ):
        raise RehearsalV22ValidationError("void epoch 3 disclosure ruling drifted")

    control = implementation.build_control_surface(
        root,
        implementation_commit,
        require_current=False,
    )
    if (
        control.implementation_commit != implementation_commit
        or control.merkle_root_sha256 != VOID_EPOCH_THREE_CONTROL_ROOT_SHA256
        or control.merkle_root_sha256 != epoch["control_merkle_root_sha256"]
        or len(control.records) != VOID_EPOCH_THREE_CONTROL_RECORD_COUNT
        or control.loaded_repository_sources
        or not set(control.ast_closure_paths).issubset(
            record["repository_path"]
            for record in control.records
            if record["repository_path"] is not None
        )
    ):
        raise RehearsalV22ValidationError("void epoch 3 control surface replay drifted")


def _validate_implementation_epochs(
    *,
    project_root: Path,
    bundle: Mapping[str, Any],
    replay: HistoryReplay,
    archives: ArchiveReplay,
    historical_anchor: implementation.HistoricalSelectedAnchor,
    live_anchor: implementation.LiveExecutionAnchor,
) -> None:
    epochs = _array(bundle.get("implementation_epochs"), "implementation epochs")
    execution_head = live_anchor.execution_head
    _git_commit(project_root, execution_head, "execution HEAD")
    for index, raw in enumerate(epochs, 1):
        epoch = _validate_epoch_shape(raw, f"implementation epoch {index}")
        if index == 1 and _is_void_epoch_one(epoch):
            _validate_void_epoch_one(
                project_root=project_root,
                epoch=epoch,
                execution_head=execution_head,
            )
            continue
        if index == 3 and _is_void_epoch_three(epoch):
            _validate_void_epoch_three(
                project_root=project_root,
                epoch=epoch,
                execution_head=execution_head,
            )
            continue
        implementation_commit = _git_commit(
            project_root,
            epoch["implementation_commit"],
            f"implementation epoch {index} commit",
        )
        if not _git_is_ancestor(project_root, implementation_commit, execution_head):
            raise RehearsalV22ValidationError(
                f"implementation epoch {index} is not an execution-head ancestor"
            )
        owner = _validate_authority_ref(
            epoch["owner_exact_surface_authorization"],
            f"implementation epoch {index} owner authority",
        )
        review = _validate_authority_ref(
            epoch["independent_implementation_review"],
            f"implementation epoch {index} independent review",
        )
        if index == 1:
            expected_owner = {
                "path": INDEPENDENT_REVIEW_RELATIVE.as_posix(),
                "sha256": INDEPENDENT_REVIEW_SHA256,
                "creating_commit": INDEPENDENT_REVIEW_COMMIT,
                "unique_a_history_verified": True,
            }
            _require_equal(owner, expected_owner, "initial owner surface authority")
            owner_payload = _validate_initial_sibling_authority(
                project_root,
                owner,
                execution_head=execution_head,
                require_worktree=False,
            )
        else:
            owner_payload = _unique_a_authority(
                project_root,
                owner,
                require_worktree=False,
            )
        parents = _git_parents(project_root, implementation_commit)
        if len(parents) != 1:
            raise RehearsalV22ValidationError("implementation epoch commit is not single-parent")
        if index == 1:
            owner_document = _object(
                strict_json_loads(owner_payload, label="initial sibling authority"),
                "initial sibling authority",
            )
            _require_equal(
                owner_document.get("reviewed_commit"),
                INITIAL_REVIEWED_COMMIT,
                "initial sibling reviewed commit",
            )
            _require_equal(
                owner_document.get("verdict"),
                "APPROVE_V2_2_PREREGISTRATION_AND_AUTHORIZE_IMPLEMENTATION",
                "initial sibling authority verdict",
            )
            _require_equal(
                _object(
                    owner_document.get("what_this_authorizes"),
                    "initial sibling authorization scope",
                ).get("granted"),
                (
                    "implement the v2.2 harness exactly as preregistered: the shim, "
                    "the implementation module, the validator, the series ledger, "
                    "and the registered tests"
                ),
                "initial sibling authorization grant",
            )
            if parents != (INITIAL_IMPLEMENTATION_PARENT,):
                raise RehearsalV22ValidationError("initial implementation parent drifted")
            initial_expected_surface = {("A", path) for path in IMPLEMENTATION_PATHS}
            observed_surface = set(
                _diff_name_status(
                    project_root, INITIAL_IMPLEMENTATION_PARENT, implementation_commit
                )
            )
            if observed_surface != initial_expected_surface:
                raise RehearsalV22ValidationError("initial implementation is not exact 5A")
        else:
            if parents != (cast(str, owner["creating_commit"]),):
                raise RehearsalV22ValidationError(
                    "later implementation epoch is not direct child of its authority"
                )
            owner_document = _object(
                strict_json_loads(
                    owner_payload,
                    label=f"epoch {index} surface authorization",
                ),
                f"epoch {index} surface authorization",
            )
            _require_exact_keys(
                owner_document,
                frozenset(
                    {
                        "schema_version",
                        "verdict",
                        "owner",
                        "implementation_epoch",
                        "base_commit",
                        "exact_surface",
                    }
                ),
                f"epoch {index} surface authorization",
            )
            _require_equal(
                owner_document["schema_version"],
                "p4.2a-v2-2-implementation-epoch-surface-authorization-v1",
                f"epoch {index} surface authorization schema",
            )
            _require_equal(
                owner_document["verdict"],
                "APPROVE_EXACT_V2_2_IMPLEMENTATION_EPOCH_SURFACE",
                f"epoch {index} surface authorization verdict",
            )
            _require_equal(
                owner_document["owner"],
                {"identity": "ouyang", "approved": True},
                f"epoch {index} surface authorization owner",
            )
            _require_equal(
                owner_document["implementation_epoch"],
                index,
                f"epoch {index} surface authorization number",
            )
            base_commit = _git_commit(
                project_root,
                owner_document["base_commit"],
                f"epoch {index} surface base",
            )
            if _git_parents(
                project_root, cast(str, owner["creating_commit"])
            ) != (base_commit,):
                raise RehearsalV22ValidationError(
                    "later epoch authority is not the direct child of its base"
                )
            if not _git_is_ancestor(
                project_root,
                INITIAL_REVIEWED_COMMIT,
                base_commit,
            ):
                raise RehearsalV22ValidationError(
                    "later epoch authority base does not descend from preregistration"
                )
            rows = _array(
                owner_document["exact_surface"],
                f"epoch {index} exact surface",
            )
            if not rows:
                raise RehearsalV22ValidationError("later epoch exact surface is empty")
            later_expected_surface: dict[str, str] = {}
            ordered_surface_paths: list[str] = []
            for row_index, raw_row in enumerate(rows):
                row = _object(raw_row, f"epoch {index} surface row {row_index}")
                _require_exact_keys(
                    row,
                    frozenset({"path", "status"}),
                    f"epoch {index} surface row {row_index}",
                )
                relative = _relative(
                    row["path"], f"epoch {index} surface row path"
                )
                status_value = _string(
                    row["status"], f"epoch {index} surface row status"
                )
                if (
                    relative not in IMPLEMENTATION_PATHS
                    or status_value not in {"A", "M"}
                    or relative in later_expected_surface
                ):
                    raise RehearsalV22ValidationError(
                        "later epoch exact surface escaped its five-path A/M allowlist"
                    )
                later_expected_surface[relative] = status_value
                ordered_surface_paths.append(relative)
            if ordered_surface_paths != sorted(
                ordered_surface_paths, key=lambda value: value.encode("utf-8")
            ):
                raise RehearsalV22ValidationError(
                    "later epoch exact surface is not byte-sorted"
                )
            surface = _diff_name_status(project_root, parents[0], implementation_commit)
            if len(surface) != len(later_expected_surface) or dict(
                (path, status) for status, path in surface
            ) != later_expected_surface:
                raise RehearsalV22ValidationError(
                    "later epoch implementation differs from owner exact surface"
                )
        review_payload = (
            _unique_a_authority(
                project_root,
                review,
                require_worktree=False,
            )
            if index == 1
            else _validate_implementation_review_authority(
                project_root,
                review,
                implementation_commit=implementation_commit,
                execution_head=execution_head,
                require_worktree=False,
            )
        )
        review_document = _object(
            strict_json_loads(review_payload, label=f"epoch {index} independent review"),
            f"epoch {index} independent review",
        )
        _validate_implementation_review_document(
            document=review_document,
            implementation_commit=implementation_commit,
            label=f"epoch {index} independent review",
        )
        review_commit = cast(str, review["creating_commit"])
        if (
            not _git_is_ancestor(project_root, implementation_commit, review_commit)
            or not _git_is_ancestor(project_root, review_commit, execution_head)
        ):
            raise RehearsalV22ValidationError(
                f"epoch {index} independent review topology drifted"
            )
        for relative in IMPLEMENTATION_PATHS:
            blob = _git_blob(project_root, implementation_commit, relative)
            if index == replay.selected_implementation_epoch:
                _require_equal(
                    historical_anchor.selected_git_blob_sha256.get(relative),
                    _sha256(blob),
                    f"historical selected implementation SHA {relative}",
                )
        if index == replay.selected_implementation_epoch:
            epoch_control = historical_anchor.control_surface
        else:
            epoch_control = implementation.build_control_surface(
                project_root,
                implementation_commit,
                require_current=False,
            )
        if (
            epoch_control.implementation_commit != implementation_commit
            or epoch_control.merkle_root_sha256
            != epoch["control_merkle_root_sha256"]
            or epoch_control.loaded_repository_sources
            or not set(epoch_control.ast_closure_paths).issubset(
                record["repository_path"]
                for record in epoch_control.records
                if record["repository_path"] is not None
            )
        ):
            raise RehearsalV22ValidationError(
                f"implementation epoch {index} control surface replay drifted"
            )
        implementation.validate_implementation_epoch(
            project_root,
            epoch=index,
            implementation_commit=implementation_commit,
            owner_surface_authorization=_core_authority(owner),
            independent_review=_core_authority(review),
            control_merkle_root_sha256=cast(
                str, epoch["control_merkle_root_sha256"]
            ),
            execution_head=execution_head,
            require_current_bytes=False,
        )
    if _is_void_epoch_one(_object(epochs[0], "implementation epoch 1")):
        if any(record.get("implementation_epoch") == 1 for record in replay.records):
            raise RehearsalV22ValidationError(
                "void epoch 1 unexpectedly owns a ledger attempt"
            )
        if not replay.records or replay.records[0].get("implementation_epoch") != 2:
            raise RehearsalV22ValidationError(
                "executed implementation history does not begin at epoch 2"
            )
    has_void_epoch_three = any(
        index == 3 and _is_void_epoch_three(_object(raw, "implementation epoch 3"))
        for index, raw in enumerate(epochs, 1)
    )
    if has_void_epoch_three:
        if any(record.get("implementation_epoch") == 3 for record in replay.records):
            raise RehearsalV22ValidationError(
                "void epoch 3 unexpectedly owns a ledger attempt"
            )
        if (
            [record.get("implementation_epoch") for record in replay.records] != [2, 4]
            or replay.selected_attempt_ordinal != 2
            or replay.selected_implementation_epoch != 4
            or replay.selected_implementation_commit
            != historical_anchor.selected_commit
        ):
            raise RehearsalV22ValidationError(
                "void epoch 3 is restricted to the exact sealed [2,4] history"
            )
    selected = _object(
        epochs[replay.selected_implementation_epoch - 1], "selected implementation epoch"
    )
    _require_equal(
        selected["control_merkle_root_sha256"],
        archives.control_root_sha256,
        "selected epoch control root",
    )
    _require_equal(
        selected["implementation_commit"],
        historical_anchor.selected_commit,
        "historical selected epoch commit",
    )
    _require_equal(
        selected["owner_exact_surface_authorization"],
        historical_anchor.owner_surface_authorization.as_json(),
        "historical selected owner surface authority",
    )
    _require_equal(
        selected["independent_implementation_review"],
        historical_anchor.independent_implementation_review.as_json(),
        "historical selected independent review",
    )


def _bundle_filesystem_is_exact(
    *,
    bundle_directory: Path,
    bundle_payload: bytes,
    archives: ArchiveReplay,
    history: HistoryReplay,
) -> None:
    actual = _walk_regular_tree(bundle_directory, label="bundle directory")
    expected = {
        BUNDLE_FILENAME: bundle_payload,
        **archives.all_payloads,
        **history.archive_payloads,
    }
    if set(actual) != set(expected):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise RehearsalV22ValidationError(
            f"bundle filesystem inventory drifted: missing={missing!r} extra={extra!r}"
        )
    for relative, payload in expected.items():
        if actual[relative] != payload:
            raise RehearsalV22ValidationError(f"bundle file bytes drifted: {relative}")
    if _tree_directory_relatives(bundle_directory, label="bundle directory") != (
        _directories_implied_by_files(tuple(expected))
    ):
        raise RehearsalV22ValidationError(
            "bundle directory contains a missing or extra directory"
        )


def _validate_pipeline_replay_result(
    value: object,
    *,
    run_label: str,
    expected_artifacts: Mapping[str, bytes],
) -> None:
    if not isinstance(value, implementation.PipelineReplay):
        raise RehearsalV22ValidationError("active replay returned an unknown result type")
    _require_equal(value.run_label, run_label, f"{run_label} active replay label")
    if dict(value.artifacts) != dict(expected_artifacts):
        raise RehearsalV22ValidationError(
            f"{run_label} active replay artifacts differ from the archive"
        )
    if value.removed is not True or _validator_os.path.lexists(value.write_root):
        raise RehearsalV22ValidationError(
            f"{run_label} active replay temporary write root remains"
        )
    common = {
        "status": "PASS",
        "run_label": run_label,
        "real_database_reads": 0,
        "real_network_calls": 0,
        "real_model_calls": 0,
    }
    expected_probes: dict[str, JsonObject] = {
        "cninfo_one_second_pacing": {
            **common,
            "request_start_count": 2824,
            "observed_gap_count": 2823,
            "minimum_observed_gap_seconds": 1.0,
            "median_observed_gap_seconds": 1.0,
            "violation_count": 0,
        },
        "zero_retry_model_contract": {
            **common,
            "call_count": 4048,
            "max_retries": 0,
        },
        "deterministic_ineligible_zero_retry": {
            **common,
            "registered_reasons": [
                "pdf_text_below_min_char_gate",
                "pdf_exceeds_size_bound",
            ],
            "retry_count": 0,
            "return_to_pool_count": 0,
        },
        "unexpected_failure_aborts": {
            **common,
            "retry_count": 0,
            "partial_publish_count": 0,
        },
        "consumer_stage_gates": {
            **common,
            "seal_draft": "PRIVATE_OFFLINE_CAPABILITY_REVALIDATED",
            "build_adjudication_ui": "PRIVATE_OFFLINE_CAPABILITY_REVALIDATED",
            "finalize_owner_adjudication": "PRIVATE_OFFLINE_CAPABILITY_REVALIDATED",
            "evaluation": "SYNTHETIC_ONLY_PRIVATE_OFFLINE_CAPABILITY_REVALIDATED",
        },
    }
    if dict(value.probe_evidence) != expected_probes:
        raise RehearsalV22ValidationError(
            f"{run_label} active replay probe evidence drifted"
        )


def _active_replay_selected_pipeline(
    *,
    raw_binding: implementation.ExecutionBinding,
    bundle_path: Path,
    implementation_commit: str,
    execution_context: object | None,
    archives: ArchiveReplay,
) -> None:
    def replay(
        context: implementation.ExecutionCapability | implementation._ReplayCapability,
    ) -> None:
        for label, expected in (("run-a", archives.run_a), ("run-b", archives.run_b)):
            result = implementation.replay_selected_pipeline(
                binding=raw_binding,
                implementation_commit=implementation_commit,
                run_label=label,
                execution_context=context,
                validator_mode=True,
            )
            _validate_pipeline_replay_result(
                result,
                run_label=label,
                expected_artifacts=expected,
            )

    if execution_context is not None:
        replay(cast(implementation.ExecutionCapability, execution_context))
        return
    with implementation._official_validator_replay_scope(
        binding=raw_binding,
        validator_module=sys.modules[__name__],
        bundle_path=bundle_path,
        implementation_commit=implementation_commit,
    ) as replay_context:
        replay(replay_context)


def _selected_bundle_envelope(bundle_path: Path) -> tuple[int, str, str]:
    """Read only the three values needed to mint standalone ordinary anchors."""

    payload = _regular_bytes(bundle_path.absolute(), "ordinary bundle envelope")
    bundle = _strict_canonical_json_loads(payload, label="ordinary bundle envelope")
    history = _object(bundle.get("attempt_history"), "ordinary bundle history envelope")
    ordinal = _integer(
        history.get("selected_attempt_ordinal"),
        "ordinary bundle selected ordinal",
        minimum=1,
    )
    records = _array(history.get("records"), "ordinary bundle history records")
    if ordinal > len(records):
        raise RehearsalV22ValidationError(
            "ordinary bundle selected ordinal is outside its history"
        )
    selected_record = _object(records[ordinal - 1], "ordinary selected attempt")
    if selected_record.get("outcome") != "CANDIDATE_VALIDATED_AND_SELECTED":
        raise RehearsalV22ValidationError("ordinary selected attempt is not successful")
    epoch_number = _integer(
        selected_record.get("implementation_epoch"),
        "ordinary selected implementation epoch",
        minimum=1,
    )
    commit = _commit(
        selected_record.get("implementation_commit"),
        "ordinary selected implementation commit",
    )
    epoch = _epoch_map(bundle).get(epoch_number)
    if epoch is None or _is_void_epoch_one(epoch) or _is_void_epoch_three(epoch):
        raise RehearsalV22ValidationError(
            "ordinary selected implementation epoch is not executed"
        )
    _require_equal(epoch.get("implementation_commit"), commit, "ordinary selected commit")
    return (
        epoch_number,
        commit,
        _sha(epoch.get("control_merkle_root_sha256"), "ordinary selected control root"),
    )


def _ordinary_validation_anchors(
    *,
    project_root: Path,
    raw_binding: implementation.ExecutionBinding,
    bundle_path: Path,
    execution_context: object | None,
) -> tuple[
    implementation.HistoricalSelectedAnchor,
    implementation.LiveExecutionAnchor,
]:
    if execution_context is None:
        selected_epoch, selected_commit, selected_control_root = _selected_bundle_envelope(
            bundle_path
        )
        raw_historical, raw_live = implementation._standalone_active_validation_anchors(
            project_root=project_root,
            raw_binding=raw_binding,
            selected_epoch=selected_epoch,
            selected_commit=selected_commit,
            selected_control_root_sha256=selected_control_root,
        )
    else:
        raw_historical, raw_live = implementation._active_attempt_validation_anchors(
            execution_context,
            project_root=project_root,
            validator_module=sys.modules[__name__],
        )
    historical = _historical_selected_anchor(raw_historical)
    live = _live_execution_anchor(raw_live)
    if historical is live or type(historical) is type(live):
        raise RehearsalV22ValidationError("ordinary historical/live anchors crossed types")
    return historical, live


def _recovery_validation_context(
    *,
    project_root: Path,
    bundle_path: Path,
    recovery_context: object,
    recovery_validator_delegation: object,
) -> tuple[
    ResolvedExecution,
    implementation.HistoricalSelectedAnchor,
    implementation.LiveExecutionAnchor,
    Path,
]:
    validator_module = sys.modules.get(__name__)
    if validator_module is None:
        raise RehearsalV22ValidationError("recovery validator module identity is absent")
    raw_binding = implementation._validate_recovery_execution_capability(
        recovery_context,
        project_root=project_root.absolute(),
    )
    delegated_binding = implementation._validate_recovery_validator_delegation(
        recovery_validator_delegation,
        recovery_context=recovery_context,
        validator_module=validator_module,
        project_root=project_root.absolute(),
        bundle_path=bundle_path.absolute(),
    )
    binding = _binding_view(raw_binding)
    if _binding_view(delegated_binding) != binding:
        raise RehearsalV22ValidationError("recovery validator binding drifted")
    raw_historical, raw_live = implementation._recovery_validation_anchors(
        recovery_context,
        project_root=binding.project_root,
    )
    historical = _historical_selected_anchor(raw_historical)
    live = _live_execution_anchor(raw_live)
    if historical is live or type(historical) is type(live):
        raise RehearsalV22ValidationError("recovery historical/live anchors crossed types")
    candidate = bundle_path.absolute()
    authorized_directory = _directory(candidate.parent, "recovery delegated bundle directory")
    return (
        ResolvedExecution(view=binding, raw=raw_binding),
        historical,
        live,
        authorized_directory,
    )


def _recovered_release_validation_context(
    *,
    project_root: Path,
    bundle_path: Path,
    recovered_release_context: object,
    recovered_release_validator_delegation: object,
) -> tuple[
    ResolvedExecution,
    implementation.HistoricalSelectedAnchor,
    implementation.LiveExecutionAnchor,
    Path,
]:
    validator_module = sys.modules.get(__name__)
    if validator_module is None:
        raise RehearsalV22ValidationError(
            "recovered-release validator module identity is absent"
        )
    raw_binding = implementation._validate_recovered_release_capability(
        recovered_release_context,
        project_root=project_root.absolute(),
    )
    delegated_binding = implementation._validate_recovered_release_validator_delegation(
        recovered_release_validator_delegation,
        recovered_release_context=recovered_release_context,
        validator_module=validator_module,
        project_root=project_root.absolute(),
        bundle_path=bundle_path.absolute(),
    )
    binding = _binding_view(raw_binding)
    if _binding_view(delegated_binding) != binding:
        raise RehearsalV22ValidationError("recovered-release validator binding drifted")
    raw_historical, raw_live = implementation._recovered_release_validation_anchors(
        recovered_release_context,
        project_root=binding.project_root,
    )
    historical = _historical_selected_anchor(raw_historical)
    live = _live_execution_anchor(raw_live)
    if historical is live or type(historical) is type(live):
        raise RehearsalV22ValidationError(
            "recovered-release historical/live anchors crossed types"
        )
    candidate = bundle_path.absolute()
    authorized_directory = _directory(candidate.parent, "recovered-release bundle directory")
    if authorized_directory != binding.absolute_destination:
        raise RehearsalV22ValidationError(
            "recovered-release bundle is not the published destination"
        )
    return (
        ResolvedExecution(view=binding, raw=raw_binding),
        historical,
        live,
        authorized_directory,
    )


def _validate_bundle_once(
    *,
    project_root: Path,
    bundle_path: Path,
    binding: BindingView,
    bundle_directory: Path,
    historical_anchor: implementation.HistoricalSelectedAnchor,
    live_anchor: implementation.LiveExecutionAnchor,
    validation_mode: ValidationMode,
    expected_bundle_sha256: str | None,
) -> ValidatedBundle:
    root = project_root.resolve(strict=True)
    candidate = bundle_path.absolute()
    historical = _historical_selected_anchor(historical_anchor)
    live = _live_execution_anchor(live_anchor)
    if not isinstance(validation_mode, ValidationMode):
        raise RehearsalV22ValidationError("bundle validation mode is not exact")
    if validation_mode is ValidationMode.ORDINARY_ACTIVE:
        if (
            historical.selected_epoch != live.execution_epoch
            or historical.selected_commit != live.implementation_commit
        ):
            raise RehearsalV22ValidationError(
                "ordinary active replay requires distinct anchors for the same epoch"
            )
        _validate_current_execution_anchor(project_root=root, live_anchor=live)
    elif (
        historical.selected_epoch != 4
        or historical.selected_commit
        != "890e9002116c625d41f6aa037975df15d1546c56"
        or live.execution_epoch != 5
        or historical.selected_commit == live.implementation_commit
    ):
        raise RehearsalV22ValidationError(
            "recovered bundle requires historical epoch 4 and live epoch 5 anchors"
        )
    else:
        _validate_live_execution_anchor(project_root=root, live_anchor=live)
    if candidate.parent != bundle_directory:
        raise RehearsalV22ValidationError("bundle path traverses an aliased directory")
    bundle_payload = _regular_bytes(candidate, "v2.2 bundle")
    if expected_bundle_sha256 is not None:
        _require_equal(
            _sha256(bundle_payload),
            _sha(expected_bundle_sha256, "release-bound bundle SHA"),
            "release-bound bundle SHA",
        )
    bundle = _object(strict_json_loads(bundle_payload, label="v2.2 bundle"), "v2.2 bundle")
    if validation_mode is ValidationMode.ORDINARY_ACTIVE:
        schema_payload = _bound_control(
            root,
            BUNDLE_SCHEMA_RELATIVE,
            BUNDLE_SCHEMA_SHA256,
            "v2.2 bundle schema",
        )
        preregistration_payload = _bound_control(
            root,
            PREREGISTRATION_RELATIVE,
            PREREGISTRATION_SHA256,
            "v2.2 preregistration",
        )
        release_schema_payload = _bound_control(
            root,
            RELEASE_SCHEMA_RELATIVE,
            RELEASE_SCHEMA_SHA256,
            "v2.2 release schema",
        )
        _validate_contract_inheritance(
            project_root=root,
            preregistration_payload=preregistration_payload,
            bundle_schema_payload=schema_payload,
            release_schema_payload=release_schema_payload,
        )
    else:
        schema_payload = _validated_implementation_blob(
            project_root=root,
            historical_anchor=historical,
            relative_path=BUNDLE_SCHEMA_RELATIVE.as_posix(),
            expected_sha256=BUNDLE_SCHEMA_SHA256,
        )
        preregistration_payload = _validated_implementation_blob(
            project_root=root,
            historical_anchor=historical,
            relative_path=PREREGISTRATION_RELATIVE.as_posix(),
            expected_sha256=PREREGISTRATION_SHA256,
        )
        release_schema_payload = _validated_implementation_blob(
            project_root=root,
            historical_anchor=historical,
            relative_path=RELEASE_SCHEMA_RELATIVE.as_posix(),
            expected_sha256=RELEASE_SCHEMA_SHA256,
        )
        _validate_historical_contract_inheritance(
            project_root=root,
            historical_anchor=historical,
            preregistration_payload=preregistration_payload,
            bundle_schema_payload=schema_payload,
            release_schema_payload=release_schema_payload,
        )
    schema = _object(strict_json_loads(schema_payload, label="bundle schema"), "bundle schema")
    _schema_validate(bundle, schema, "v2.2 bundle")
    _validate_binding_document(bundle.get("execution_binding"), binding, "bundle execution binding")
    history_stub = _object(bundle.get("attempt_history"), "bundle attempt history")
    implementation_commit = _commit(
        _object(bundle.get("lineage"), "bundle lineage").get("implementation_commit"),
        "bundle implementation commit",
    )
    _require_equal(
        implementation_commit,
        historical.selected_commit,
        "bundle historical selected commit",
    )
    _validate_lineage(
        project_root=root,
        bundle=bundle,
        historical_anchor=historical,
    )
    _validate_harness_identity(
        project_root=root,
        bundle=bundle,
        historical_anchor=historical,
    )
    archives = _validate_archives(
        project_root=root,
        bundle_directory=bundle_directory,
        bundle=bundle,
        historical_anchor=historical,
    )
    replay = _validate_attempt_history_records(
        project_root=root,
        bundle=bundle,
        ledger_root=binding.ledger_root,
        archive_root=bundle_directory / "archive/attempt-history",
        binding=binding,
    )
    _require_equal(
        implementation_commit,
        replay.selected_implementation_commit,
        "bundle lineage selected implementation commit",
    )
    del history_stub
    _validate_history_summary(bundle=bundle, binding=binding, replay=replay, archives=archives)
    _validate_historical_selected_anchor(
        project_root=root,
        historical_anchor=historical,
        replay=replay,
        archives=archives,
    )
    _validate_implementation_epochs(
        project_root=root,
        bundle=bundle,
        replay=replay,
        archives=archives,
        historical_anchor=historical,
        live_anchor=live,
    )
    _bundle_filesystem_is_exact(
        bundle_directory=bundle_directory,
        bundle_payload=bundle_payload,
        archives=archives,
        history=replay,
    )
    _require_equal(
        _object(bundle.get("evaluation_one_shot"), "evaluation one-shot"),
        {
            "unchanged_and_unconsumed": True,
            "attempts_consumed_by_v2_2_rehearsal": 0,
            "rehearsal_repeatability_policy_applies_to_evaluation": False,
            "evaluation_claim_or_destination_touched": False,
        },
        "held-out evaluation one-shot evidence",
    )
    return ValidatedBundle(
        document=bundle,
        payload=bundle_payload,
        path=candidate,
        project_root=root,
        bundle_directory=bundle_directory,
        implementation_commit=implementation_commit,
        archives=archives,
        history=replay,
        historical_anchor=historical,
        live_anchor=live,
        validation_mode=validation_mode,
    )


def _active_replay_validated_bundle(
    *,
    validated: ValidatedBundle,
    binding: BindingView,
    raw_binding: implementation.ExecutionBinding,
    execution_context: object | None,
    published_release_revalidation: bool,
) -> None:
    if validated.validation_mode is not ValidationMode.ORDINARY_ACTIVE:
        raise RehearsalV22ValidationError("passive bundle cannot enter active replay")
    bundle_directory = _authorized_bundle_directory(
        binding=binding,
        raw_binding=raw_binding,
        bundle_path=validated.path,
        published_release_revalidation=published_release_revalidation,
    )
    current_payload = _regular_bytes(validated.path, "active-replay-bound bundle")
    if current_payload != validated.payload:
        raise RehearsalV22ValidationError("bundle bytes drifted before active replay")
    _bundle_filesystem_is_exact(
        bundle_directory=bundle_directory,
        bundle_payload=current_payload,
        archives=validated.archives,
        history=validated.history,
    )
    _active_replay_selected_pipeline(
        raw_binding=raw_binding,
        bundle_path=validated.path,
        implementation_commit=validated.live_anchor.implementation_commit,
        execution_context=execution_context,
        archives=validated.archives,
    )


def _passive_revalidate_validated_bundle(
    *,
    validated: ValidatedBundle,
) -> None:
    if validated.validation_mode not in {
        ValidationMode.RECOVERY_PASSIVE,
        ValidationMode.RECOVERED_RELEASE_PASSIVE,
    }:
        raise RehearsalV22ValidationError("active bundle cannot enter passive validation")
    current_payload = _regular_bytes(validated.path, "passive-recovery-bound bundle")
    if current_payload != validated.payload:
        raise RehearsalV22ValidationError("bundle bytes drifted before passive validation")
    _bundle_filesystem_is_exact(
        bundle_directory=validated.bundle_directory,
        bundle_payload=current_payload,
        archives=validated.archives,
        history=validated.history,
    )
    _validate_live_execution_anchor(
        project_root=validated.project_root,
        live_anchor=validated.live_anchor,
    )


def validate_bundle(
    *,
    project_root: Path,
    bundle_path: Path,
    execution_context: object | None = None,
    validator_delegation: object | None = None,
) -> JsonObject:
    """Actively validate one official or privately delegated disposable bundle."""

    resolved = _resolve_execution_binding(
        project_root=project_root,
        execution_context=execution_context,
        validator_delegation=validator_delegation,
    )
    # The context/delegation gate above deliberately precedes every bundle,
    # ledger, archive, database, network, model, or artifact read/write.
    bundle_directory = _authorized_bundle_directory(
        binding=resolved.view,
        raw_binding=resolved.raw,
        bundle_path=bundle_path.absolute(),
        published_release_revalidation=False,
    )
    historical_anchor, live_anchor = _ordinary_validation_anchors(
        project_root=resolved.view.project_root,
        raw_binding=resolved.raw,
        bundle_path=bundle_path,
        execution_context=execution_context,
    )
    validated = _validate_bundle_once(
        project_root=resolved.view.project_root,
        bundle_path=bundle_path,
        binding=resolved.view,
        bundle_directory=bundle_directory,
        historical_anchor=historical_anchor,
        live_anchor=live_anchor,
        validation_mode=ValidationMode.ORDINARY_ACTIVE,
        expected_bundle_sha256=None,
    )
    _active_replay_validated_bundle(
        validated=validated,
        binding=resolved.view,
        raw_binding=resolved.raw,
        execution_context=execution_context,
        published_release_revalidation=False,
    )
    return validated.document


def validate_recovered_bundle(
    *,
    project_root: Path,
    bundle_path: Path,
    recovery_context: object,
    recovery_validator_delegation: object,
) -> JsonObject:
    """Passively validate one sealed-ledger recovery with zero pipeline starts."""

    resolved, historical_anchor, live_anchor, bundle_directory = (
        _recovery_validation_context(
            project_root=project_root,
            bundle_path=bundle_path,
            recovery_context=recovery_context,
            recovery_validator_delegation=recovery_validator_delegation,
        )
    )
    validated = _validate_bundle_once(
        project_root=resolved.view.project_root,
        bundle_path=bundle_path,
        binding=resolved.view,
        bundle_directory=bundle_directory,
        historical_anchor=historical_anchor,
        live_anchor=live_anchor,
        validation_mode=ValidationMode.RECOVERY_PASSIVE,
        expected_bundle_sha256=None,
    )
    _passive_revalidate_validated_bundle(validated=validated)
    return validated.document


def _execution_binding_common(value: object, label: str) -> JsonObject:
    binding = _object(value, label)
    required = {
        "mode",
        "project_root",
        "absolute_destination",
        "series_token_sha256",
        "ledger_root",
        "derivation_recomputed",
        "private_rebase_capability_validated",
    }
    if not required.issubset(binding):
        raise RehearsalV22ValidationError(f"{label} lacks common execution fields")
    return {key: binding[key] for key in sorted(required)}


def _cross_validate_release(
    *,
    bundle: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> None:
    """Cross-equal every owner acknowledgement to recomputed bundle evidence."""

    bundle_binding = _object(bundle.get("execution_binding"), "bundle execution binding")
    receipt_binding = _object(receipt.get("execution_binding"), "receipt execution binding")
    _require_equal(
        _execution_binding_common(receipt_binding, "receipt execution binding"),
        _execution_binding_common(bundle_binding, "bundle execution binding"),
        "release/bundle execution binding",
    )
    history = _object(bundle.get("attempt_history"), "bundle attempt history")
    records = _array(history.get("records"), "bundle attempt records")
    selected_ordinal = _integer(
        history.get("selected_attempt_ordinal"), "bundle selected ordinal", minimum=1
    )
    if selected_ordinal > len(records):
        raise RehearsalV22ValidationError(
            "bundle selected ordinal is outside its attempt history"
        )
    selected = _object(records[selected_ordinal - 1], "selected attempt record")
    if selected.get("outcome") != "CANDIDATE_VALIDATED_AND_SELECTED":
        raise RehearsalV22ValidationError("bundle selected ordinal is not success")
    outcomes = [
        {
            "ordinal": record["ordinal"],
            "outcome": record["outcome"],
            "implementation_epoch": record["implementation_epoch"],
            "record_root_sha256": record["record_root_sha256"],
        }
        for record in (_object(raw, "bundle attempt record") for raw in records)
    ]
    failed = sum(record["outcome"] == "FAILED" for record in records)
    incomplete = sum(record["outcome"] == "INCOMPLETE_UNTERMINALIZED" for record in records)
    owner = _object(receipt.get("owner_authorization"), "release owner authorization")
    expected_owner = {
        "owner": "ouyang",
        "approved": True,
        "approval_scope": (
            "rehearsal_evidence_and_complete_attempt_history_only_not_real_stage_release"
        ),
        "accepts_disclosed_repeatability": True,
        "acknowledged_attempt_count": len(records),
        "acknowledged_failed_count": failed,
        "acknowledged_incomplete_count": incomplete,
        "acknowledged_outcomes": outcomes,
        "selected_attempt_ordinal": selected_ordinal,
        "attempt_history_root_sha256": history["history_root_sha256"],
        "all_attempt_outcomes_reviewed": True,
        "no_hidden_or_omitted_attempt_accepted": True,
        "acknowledged_outcomes_are_contiguous_and_ordered": True,
    }
    _require_equal(owner, expected_owner, "release owner acknowledgement")
    series = _object(receipt.get("series_identity"), "release series identity")
    _require_equal(
        series,
        {
            "series_id": REHEARSAL_ID,
            "policy": SERIES_POLICY,
            "series_token_sha256": history["series_token_sha256"],
            "ledger_root": history["ledger_root"],
            "series_closed": True,
        },
        "release series identity",
    )
    acceptance = _object(
        receipt.get("attempt_history_acceptance"), "release history acceptance"
    )
    expected_acceptance = {
        "policy": SERIES_POLICY,
        "series_closed": True,
        "attempt_count": len(records),
        "failed_count": failed,
        "incomplete_count": incomplete,
        "selected_attempt_ordinal": selected_ordinal,
        "validated_candidate_count": 1,
        "first_validated_success_is_selected": True,
        "no_attempt_after_selected_success": True,
        "ordinals_contiguous": True,
        (
            "all_started_candidate_terminal_action_authorization_"
            "and_actual_evidence_bytes_archived"
        ): True,
        "all_failure_and_incomplete_disclosures_archived": True,
        "history_merkle_recomputed": True,
        "live_ledger_matches_bundle_history": True,
        "history_unchanged_after_bundle_publication": True,
        "counts_equal_recomputed_records": True,
        "owner_acknowledged_outcomes_equal_ordered_bundle_records": True,
        "selected_ordinal_is_the_unique_validated_candidate": True,
        "selected_ordinal_and_epoch_match_lineage": True,
        "history_and_live_roots_match_lineage_and_bundle": True,
    }
    _require_equal(acceptance, expected_acceptance, "release history acceptance")
    bundle_epochs = _array(bundle.get("implementation_epochs"), "bundle epochs")
    release_epochs = _array(receipt.get("implementation_epochs"), "release epochs")
    expected_epochs = []
    for raw in bundle_epochs:
        epoch = _object(raw, "bundle epoch")
        expected_epochs.append(
            {
                "epoch": epoch["epoch"],
                "implementation_commit": epoch["implementation_commit"],
                "owner_surface_authorization": epoch["owner_exact_surface_authorization"],
                "independent_implementation_review": epoch["independent_implementation_review"],
                "control_merkle_root_sha256": epoch["control_merkle_root_sha256"],
                "first_attempt_ordinal": epoch["first_attempt_ordinal"],
                "last_attempt_ordinal": epoch["last_attempt_ordinal"],
            }
        )
    _require_equal(release_epochs, expected_epochs, "release implementation epochs")
    selected_epoch_number = _integer(
        selected.get("implementation_epoch"),
        "selected attempt implementation epoch",
        minimum=1,
    )
    if selected_epoch_number > len(expected_epochs):
        raise RehearsalV22ValidationError(
            "selected attempt implementation epoch is outside the epoch table"
        )
    selected_epoch = expected_epochs[selected_epoch_number - 1]
    lineage = _object(receipt.get("lineage"), "release lineage")
    bundle_lineage = _object(bundle.get("lineage"), "bundle lineage")
    bundle_merkle = _object(bundle.get("merkle"), "bundle Merkle")
    lineage_expected = {
        "preregistration": bundle_lineage["preregistration"],
        "bundle_schema": bundle_lineage["bundle_schema"],
        "release_schema": bundle_lineage["release_authorization_schema"],
        "bundle_root_sha256": bundle_merkle["bundle_root_sha256"],
        "attempt_history_root_sha256": history["history_root_sha256"],
        "live_ledger_root_sha256": history["live_ledger_root_sha256"],
        "preregistration_commit": bundle_lineage["preregistration_commit"],
        "selected_implementation_commit": selected_epoch["implementation_commit"],
    }
    for key, expected in lineage_expected.items():
        _require_equal(lineage.get(key), expected, f"release lineage {key}")
    for release_key, bundle_key in (
        ("v2_1_incident", "v2_1_consumed_attempt_incident"),
        ("remediation_request", "v2_2_remediation_request"),
        ("v2_2_scope_authorization", "v2_2_preregistration_scope_authorization"),
    ):
        _require_equal(
            lineage.get(release_key),
            bundle_lineage.get(bundle_key),
            f"release/bundle lineage {release_key}",
        )
    _require_equal(receipt.get("authorized_stages"), [], "release authorized stages")
    _require_equal(receipt.get("still_gated"), list(_REAL_STAGES), "release gated stages")
    production = _object(
        receipt.get("production_integration_gate"), "production integration gate"
    )
    if production.get("this_receipt_unlocks_real_stages") is not False:
        raise RehearsalV22ValidationError("evidence receipt unlocks a real stage")
    locks = _object(receipt.get("locks"), "release locks")
    if any(
        locks.get(key) is not False
        for key in (
            "p4_2a_done",
            "heldout_materialization_authorized_by_this_receipt",
            "heldout_inference_authorized_by_this_receipt",
            "heldout_evaluation_unlocked",
            "p4_2b_unlocked",
            "p4_3_unlocked",
            "non_simulate_orders_allowed",
        )
    ) or locks.get("trading_mode") != "research":
        raise RehearsalV22ValidationError("release locks are not fail-closed")


def _validate_release_once(
    *,
    project_root: Path,
    receipt_path: Path,
    binding: BindingView,
    raw_binding: implementation.ExecutionBinding,
    execution_context: object | None,
    bundle_directory: Path,
    historical_anchor: implementation.HistoricalSelectedAnchor,
    live_anchor: implementation.LiveExecutionAnchor,
    validation_mode: ValidationMode,
) -> JsonObject:
    root = project_root.resolve(strict=True)
    historical = _historical_selected_anchor(historical_anchor)
    if validation_mode is ValidationMode.ORDINARY_ACTIVE:
        schema_payload = _bound_control(
            root,
            RELEASE_SCHEMA_RELATIVE,
            RELEASE_SCHEMA_SHA256,
            "v2.2 evidence acceptance schema",
        )
    elif validation_mode is ValidationMode.RECOVERED_RELEASE_PASSIVE:
        schema_payload = _validated_implementation_blob(
            project_root=root,
            historical_anchor=historical,
            relative_path=RELEASE_SCHEMA_RELATIVE.as_posix(),
            expected_sha256=RELEASE_SCHEMA_SHA256,
        )
    else:
        raise RehearsalV22ValidationError(
            "release validation mode is not active or recovered-release passive"
        )
    expected_receipt_path = root / RELEASE_RELATIVE
    if receipt_path.absolute() != expected_receipt_path:
        raise RehearsalV22ValidationError("release receipt path is not mode-bound")
    expected_receipt = _safe_path(
        root,
        RELEASE_RELATIVE.as_posix(),
        "v2.2 evidence acceptance receipt",
    )
    receipt_payload = _regular_bytes(expected_receipt, "v2.2 evidence acceptance receipt")
    receipt = _object(
        strict_json_loads(receipt_payload, label="v2.2 evidence acceptance receipt"),
        "v2.2 evidence acceptance receipt",
    )
    schema = _object(strict_json_loads(schema_payload, label="release schema"), "release schema")
    _schema_validate(receipt, schema, "v2.2 evidence acceptance receipt")
    _validate_binding_document(
        receipt.get("execution_binding"), binding, "receipt execution binding"
    )
    lineage = _object(receipt.get("lineage"), "release lineage")
    bundle_ref = _validate_file_ref(lineage.get("bundle"), "release bundle reference")
    _require_equal(
        bundle_ref["path"],
        f"{REGISTERED_DESTINATION_RELATIVE.as_posix()}/{BUNDLE_FILENAME}",
        "release bundle path",
    )
    reviewed_head = _commit(receipt.get("reviewed_repository_head"), "release reviewed head")
    _require_equal(
        lineage.get("rehearsal_evidence_commit"),
        reviewed_head,
        "release reviewed/evidence head",
    )
    reviewed_head = _git_commit(root, reviewed_head, "release reviewed repository head")
    execution_head = _git_commit(
        root,
        _git_bytes(root, "rev-parse", "HEAD").decode("ascii", errors="strict").strip(),
        "release execution HEAD",
    )
    receipt_commit, receipt_creation_payload = _unique_a_unserialized(
        root,
        path=RELEASE_RELATIVE.as_posix(),
        execution_head=execution_head,
    )
    if receipt_creation_payload != receipt_payload:
        raise RehearsalV22ValidationError(
            "release receipt differs from its unique status-A creation blob"
        )
    if receipt_commit == reviewed_head or not _git_is_ancestor(
        root, reviewed_head, receipt_commit
    ):
        raise RehearsalV22ValidationError(
            "release receipt does not descend from the pre-receipt reviewed head"
        )
    selected_commit = _commit(
        lineage.get("selected_implementation_commit"),
        "release selected implementation commit",
    )
    preregistration_commit = _commit(
        lineage.get("preregistration_commit"), "release preregistration commit"
    )
    for ancestor, label in (
        (preregistration_commit, "preregistration"),
        (selected_commit, "selected implementation"),
    ):
        if not _git_is_ancestor(root, ancestor, reviewed_head):
            raise RehearsalV22ValidationError(
                f"release reviewed head does not descend from {label}"
            )
    release_authorities: dict[str, JsonObject] = {}
    for key in (
        "v2_1_incident",
        "remediation_request",
        "v2_2_scope_authorization",
        "review_request",
    ):
        authority = _validate_authority_ref(lineage.get(key), f"release lineage {key}")
        release_authorities[key] = authority
        if validation_mode is ValidationMode.ORDINARY_ACTIVE:
            _unique_a_authority(root, authority, require_worktree=True)
        else:
            _unique_a_authority(root, authority, require_worktree=False)
        if not _git_is_ancestor(
            root, cast(str, authority["creating_commit"]), reviewed_head
        ):
            raise RehearsalV22ValidationError(
                f"release lineage {key} is not contained by the reviewed head"
            )

    # Receipt shape, immutable Git identity, lineage, and every authority above
    # must fail closed before bundle replay is allowed to create even temporary
    # artifacts.  The bundle pass below is read-only until all receipt/bundle
    # cross equalities have also succeeded.
    bundle_path = binding.absolute_destination / BUNDLE_FILENAME
    validated = _validate_bundle_once(
        project_root=root,
        bundle_path=bundle_path,
        binding=binding,
        bundle_directory=bundle_directory,
        historical_anchor=historical,
        live_anchor=live_anchor,
        validation_mode=validation_mode,
        expected_bundle_sha256=cast(str, bundle_ref["sha256"]),
    )
    bundle = validated.document
    bundle_payload = validated.payload
    _cross_validate_release(bundle=bundle, receipt=receipt)
    if _git_blob(root, reviewed_head, cast(str, bundle_ref["path"])) != bundle_payload:
        raise RehearsalV22ValidationError(
            "release bundle bytes differ from the pre-receipt evidence head"
        )
    if binding.mode == "DISPOSABLE_FULL_SHAPE_TEST":
        review_request = release_authorities["review_request"]
        review_commit = cast(str, review_request["creating_commit"])
        _require_equal(
            review_commit,
            reviewed_head,
            "disposable review-request/evidence head",
        )
        review_parents = _git_parents(root, review_commit)
        review_path = cast(str, review_request["path"])
        if (
            len(review_parents) != 1
            or _diff_name_status(root, review_parents[0], review_commit)
            != (("A", review_path),)
            or _git_blob(root, review_parents[0], cast(str, bundle_ref["path"]))
            != bundle_payload
        ):
            raise RehearsalV22ValidationError(
                "disposable release did not commit the exact bundle before its "
                "unique-A review request"
            )
    if validation_mode is ValidationMode.ORDINARY_ACTIVE:
        _active_replay_validated_bundle(
            validated=validated,
            binding=binding,
            raw_binding=raw_binding,
            execution_context=execution_context,
            published_release_revalidation=True,
        )
    else:
        _passive_revalidate_validated_bundle(validated=validated)
    return receipt


def validate_release_authorization(
    *,
    project_root: Path,
    receipt_path: Path,
    execution_context: object | None = None,
    validator_delegation: object | None = None,
) -> JsonObject:
    """Validate evidence acceptance; this API never unlocks a real stage."""

    resolved = _resolve_execution_binding(
        project_root=project_root,
        execution_context=execution_context,
        validator_delegation=validator_delegation,
    )
    # The private/official authority gate above precedes receipt, bundle,
    # ledger, archive, database, network, model, or artifact access.
    bundle_path = resolved.view.absolute_destination / BUNDLE_FILENAME
    bundle_directory = _authorized_bundle_directory(
        binding=resolved.view,
        raw_binding=resolved.raw,
        bundle_path=bundle_path,
        published_release_revalidation=True,
    )
    historical_anchor, live_anchor = _ordinary_validation_anchors(
        project_root=resolved.view.project_root,
        raw_binding=resolved.raw,
        bundle_path=bundle_path,
        execution_context=execution_context,
    )
    return _validate_release_once(
        project_root=resolved.view.project_root,
        receipt_path=receipt_path,
        binding=resolved.view,
        raw_binding=resolved.raw,
        execution_context=execution_context,
        bundle_directory=bundle_directory,
        historical_anchor=historical_anchor,
        live_anchor=live_anchor,
        validation_mode=ValidationMode.ORDINARY_ACTIVE,
    )


def validate_recovered_release_authorization(
    *,
    project_root: Path,
    receipt_path: Path,
    recovery_context: object,
    recovery_validator_delegation: object,
) -> JsonObject:
    """Passively consume a recovered bundle under its exact recovery capability."""

    bundle_path = project_root.absolute() / REGISTERED_DESTINATION_RELATIVE / BUNDLE_FILENAME
    resolved, historical_anchor, live_anchor, bundle_directory = (
        _recovered_release_validation_context(
            project_root=project_root,
            bundle_path=bundle_path,
            recovered_release_context=recovery_context,
            recovered_release_validator_delegation=(
                recovery_validator_delegation
            ),
        )
    )
    return _validate_release_once(
        project_root=resolved.view.project_root,
        receipt_path=receipt_path,
        binding=resolved.view,
        raw_binding=resolved.raw,
        execution_context=None,
        bundle_directory=bundle_directory,
        historical_anchor=historical_anchor,
        live_anchor=live_anchor,
        validation_mode=ValidationMode.RECOVERED_RELEASE_PASSIVE,
    )


def _parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description=__doc__)


def _assert_registered_validator_environment() -> None:
    if not (
        _VALIDATOR_REGISTERED_BOOTSTRAP
        and _validator_locked_runtime()
        and _validator_direct_entry()
        and tuple(sys.path) == tuple(_validator_runtime_paths)
        and Path(sys.executable).absolute() == Path(_VALIDATOR_FIXED_PYTHON)
        and _sha256(_fixed_launcher_bytes())
        == "f4cd716d4b54f205398bec6932cc59361b087494ca2ddb157a5e8631d4d6f863"
        and _sha256(
            _regular_bytes(Path(sys.orig_argv[0]), "fixed Python orig-argv executable")
        )
        == "89c717ced41f6a395612366e5b038226d0d8fca36bbddd9321d385f5f370ebbe"
        and implementation._AUDIT_POLICY.get() is None
        and implementation._TEMP_AUTHORITY.get() is None
    ):
        raise RehearsalV22ValidationError(
            "registered v2.2 validator requires the exact locked interpreter"
        )


def _validator_result(bundle: Mapping[str, Any], *, bundle_sha256: str) -> JsonObject:
    history = _object(bundle.get("attempt_history"), "bundle attempt history")
    lineage = _object(bundle.get("lineage"), "bundle lineage")
    merkle = _object(bundle.get("merkle"), "bundle Merkle")
    return {
        "schema_version": VALIDATOR_RESULT_SCHEMA,
        "status": "PASS_REHEARSAL_V2_2_AWAITING_OWNER_EVIDENCE_ACCEPTANCE",
        "bundle_path": (
            REGISTERED_DESTINATION_RELATIVE / BUNDLE_FILENAME
        ).as_posix(),
        "bundle_sha256": bundle_sha256,
        "bundle_root_sha256": merkle["bundle_root_sha256"],
        "attempt_count": history["started_count"],
        "selected_attempt_ordinal": history["selected_attempt_ordinal"],
        "implementation_commit": lineage["implementation_commit"],
        "authorized_stages": [],
        "real_heldout_materialization_unlocked": False,
        "heldout_metric_evaluation_unlocked": False,
        "p4_2b_unlocked": False,
        "p4_3_unlocked": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    try:
        _assert_registered_validator_environment()
        _parser().parse_args(argv)
        bundle_path = registered_rehearsal_directory(REGISTERED_PROJECT_ROOT) / BUNDLE_FILENAME
        bundle = validate_bundle(
            project_root=REGISTERED_PROJECT_ROOT,
            bundle_path=bundle_path,
        )
        payload = _regular_bytes(bundle_path, "registered v2.2 bundle")
        sys.stdout.buffer.write(
            _canonical_json_bytes(
                _validator_result(bundle, bundle_sha256=_sha256(payload))
            )
        )
    except (OSError, RehearsalV22ValidationError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
