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
_VALIDATOR_REGISTERED_ROOT_TEXT = "/Users/ouyangduning/Documents/project/interesting/AlphaPilot-AI"
_VALIDATOR_FIXED_PYTHON = (
    "/Users/ouyangduning/Documents/project/interesting/AlphaPilot-AI/.venv/bin/python"
)
_VALIDATOR_FIXED_ORIG_PYTHON = (
    "/Library/Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python"
)
_VALIDATOR_REGISTERED_PATH_TEXT = (
    _VALIDATOR_REGISTERED_ROOT_TEXT + "/scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py"
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
        and _validator_os.path.abspath(_validator_sys.executable) == _VALIDATOR_FIXED_PYTHON
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


if __name__ == "__main__" and not (_validator_locked_runtime() and _validator_direct_entry()):
    raise RuntimeError(
        "registered v2.2 validation must start in the exact locked -S -P -B interpreter environment"
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
import contextlib
import copy
import fcntl
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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal, NoReturn, cast

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
    "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-2-preregistration-20260811.json"
)
PREREGISTRATION_SHA256 = "8f52a9e24df11e23a900b5cb79720f3b4aae999c6ab770a9038ebe2617e8d8d5"
BUNDLE_SCHEMA_RELATIVE = Path("config/schemas/p4_2a_v2_2_heldout_rehearsal_bundle.schema.json")
BUNDLE_SCHEMA_SHA256 = "19903ac94d4d7ced81c7f18e7b8880bd1dbb68fd3ededf3f0b91f89d034aa5db"
RELEASE_SCHEMA_RELATIVE = Path(
    "config/schemas/p4_2a_v2_2_heldout_release_authorization.schema.json"
)
RELEASE_SCHEMA_SHA256 = "098d213f510718aab0d9c6bfc950a30bb1c4841ca151631bea78c1bf0238e7ea"
SERIES_2_PREREGISTRATION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-series-2-preregistration-amendment-20260823.json"
)
SERIES_2_PREREGISTRATION_SHA256 = "be98803a6b6cbe25a79242c23ee728d0ed687ac70e3f6990230bb1710886e91c"
SERIES_2_PREREGISTRATION_COMMIT = "b6dfff08557fdbca1336f816b197cd6c8a0d5c41"
SERIES_2_PREREGISTRATION_PARENT = "f21fa10babd9b300fae03c751ba038c7ebc77392"
SERIES_2_TOKEN_SEED_SHA256 = "2deee3072c339d8e8993bbf8ca8ecbe9380576c1499835828064fb4aead43d30"
SERIES_2_BUNDLE_SCHEMA_RELATIVE = Path(
    "config/schemas/p4_2a_v2_2_series_2_heldout_rehearsal_bundle.schema.json"
)
SERIES_2_BUNDLE_SCHEMA_SHA256 = "252ad069ed300917989a97656b4c38e6ee2c74069b2bacd6c258b263b5684ec7"
SERIES_2_RELEASE_SCHEMA_RELATIVE = Path(
    "config/schemas/p4_2a_v2_2_series_2_heldout_release_authorization.schema.json"
)
SERIES_2_RELEASE_SCHEMA_SHA256 = "c7228bff2d4ec575bcdec024194ce2b53d37a27e05ba793bd4bdf145a97f63be"
SERIES_2_LOSS_INCIDENT_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-sealed-ledger-loss-incident-20260823.json"
)
SERIES_2_LOSS_INCIDENT_COMMIT = "a7cea63378a39702b1618895b3a7350febcb5da6"
SERIES_2_OWNER_DECISION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-owner-decision-rerun-rehearsal-series-2-20260823.json"
)
SERIES_2_OWNER_DECISION_SHA256 = "e0fc6a17c853be063632551b4b794091a6152324af7f7ec95262ed2af8538051"
SERIES_2_OWNER_DECISION_COMMIT = "9a028855c73c4feba36125ed30cf5a7d4db5fff4"
SERIES_2_EPOCH_ORIGIN = 5
SERIES_2_SERIES_SCHEMA_VERSION = "p4.2a-v2-2-rehearsal-series-2-v1"
EPOCH_7_ADJUDICATION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-series2-ordinal2-adjudication-and-epoch7-direction-20260827.json"
)
EPOCH_7_ADJUDICATION_SHA256 = "47d8a9bbd842b496352ba210952539cb8ad1e7ab36091ab0465b8bf4c0048119"
EPOCH_7_ADJUDICATION_COMMIT = "2dd5d60121dab100c3b2000ec73dbc5ce1cd4aa0"
EPOCH_7_COMPANION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-epoch7-design-review-r2-and-companion-20260827.json"
)
EPOCH_7_COMPANION_SHA256 = "43651a31b24088b0ec676bdf2fee3c0f54629471ab29d5e5164e2b2e308e7c9d"
EPOCH_7_COMPANION_COMMIT = "c2aee25cd96296245d21b776974193172578dae3"
EPOCH_7_CONTRACT_CANONICAL_SHA256 = (
    "32149311d2e92f7b677e9d2097053b69505c893e293623c8cb7037352535508f"
)
EPOCH_7_SURFACE_AUTHORITY_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-epoch7-surface-authority-20260827.json"
)
EPOCH_7_SURFACE_AUTHORITY_SHA256 = (
    "eb2eb477165af6eb4493f3892328b73ba373a7bc83d8857514eb328f52a0430e"
)
EPOCH_7_SURFACE_AUTHORITY_COMMIT = "06336a9f593ede4132be73a8c8a087df18db904b"
EPOCH_7_DESIGN_R1_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-epoch7-sealed-bundle-recovery-design-proposal-20260827.md"
)
EPOCH_7_DESIGN_R1_SHA256 = "c80f9219a1bd61aee0bbf143295b177926377dab570aae578785dec95f628e0f"
EPOCH_7_DESIGN_R1_BYTES = 53_608
EPOCH_7_DESIGN_R1_COMMIT = "9cd424c292d658c1ddb1092f618049e6283aabaf"
EPOCH_7_DESIGN_R2_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-epoch7-sealed-bundle-recovery-design-proposal-r2-20260827.md"
)
EPOCH_7_DESIGN_R2_SHA256 = "46ea89f8edf838edcca6b6f34996be273c7ea73e04ee7e2b998293c83984f3e1"
EPOCH_7_DESIGN_R2_BYTES = 27_880
EPOCH_7_DESIGN_R2_COMMIT = "1e2e23f8948aa88376dfba45b01b2666b5c9ddaf"
EPOCH_7_IMPLEMENTATION_EPOCH = 7
EPOCH_7_RECOVERY_STARTED_SCHEMA = "p4.2a-v2-2-series2-bundle-recovery-started-v1"
EPOCH_7_RECOVERY_TERMINAL_SCHEMA = "p4.2a-v2-2-series2-bundle-recovery-terminal-v1"
EPOCH_7_RECOVERY_MIRROR_RECEIPT_SCHEMA = "p4.2a-v2-2-series2-bundle-recovery-mirror-receipt-v1"
EPOCH_8_DESIGN_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-epoch8-preclaim-census-symmetry-design-proposal-20260901.md"
)
EPOCH_8_DESIGN_SHA256 = "0a0df03f853730e83b6963564035134538769c2e3db1ad07961379e3448a44b1"
EPOCH_8_DESIGN_BYTES = 30_343
EPOCH_8_DESIGN_COMMIT = "45f486cb72a08e3520d863c86218c44ad1d5ce90"
EPOCH_8_DESIGN_REVIEW_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-epoch8-preclaim-census-symmetry-independent-design-review-"
    "20260901.json"
)
EPOCH_8_DESIGN_REVIEW_SHA256 = (
    "e1b494d9ab76c704745cf7fbd00ec14269faf8f0a919343cc5244fd187a194a6"
)
EPOCH_8_DESIGN_REVIEW_BYTES = 9_610
EPOCH_8_DESIGN_REVIEW_COMMIT = "a1dff7a8b9d093404272e57fe30b6f1ddb575516"
EPOCH_8_ADJUDICATION_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-series2-epoch7-recovery-preclaim-refusal-and-epoch8-direction-20260901.json"
)
EPOCH_8_ADJUDICATION_SHA256 = (
    "673d74ac6229f891fa517ec6dadf4cdd2c2093edf110c7c4c8a277d1b425252c"
)
EPOCH_8_ADJUDICATION_BYTES = 10_520
EPOCH_8_ADJUDICATION_COMMIT = "87896e9b2c42d6110968876d21f3b0f3963d2ac7"
EPOCH_8_COMPANION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-series2-epoch8-companion-20260901.json"
)
EPOCH_8_COMPANION_SHA256 = "4d25ba645c81b3e0d6a3458a47d9e10c80b7cd61f9ad16a28404160af91226ed"
EPOCH_8_COMPANION_BYTES = 53_282
EPOCH_8_COMPANION_COMMIT = "a39c0263fefcfbdb1886100fec1b71ec374b43a4"
EPOCH_8_CONTRACT_CANONICAL_SHA256 = (
    "36b1ae714faf2746f677e3c5aa452d2dc1822234dd10d687aa11d804ac606dbf"
)
EPOCH_8_SURFACE_AUTHORITY_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-series2-epoch8-surface-authority-20260901.json"
)
EPOCH_8_SURFACE_AUTHORITY_SHA256 = (
    "4547a2231c23a0fff96dced033028c279c4247c76130e79360e2ec602f8dd016"
)
EPOCH_8_SURFACE_AUTHORITY_BYTES = 726
EPOCH_8_SURFACE_AUTHORITY_COMMIT = "73a703a422b5209115f5b244490db36e06b1f15d"
HISTORICAL_EPOCH_8_RECOVERY_GOVERNANCE_EPOCH = 8
LANDING_PREFLIGHT_ORIGIN_EPOCH = 8
LATEST_LANDED_EXECUTION_EPOCH = 9
EPOCH_8_RECOVERY_CONTRACT_SCHEMA = "p4.2a-v2-2-series2-epoch8-recovery-contract-v1"
EPOCH_8_RECOVERY_REVIEW_REQUEST_SCHEMA = (
    "p4.2a-v2-2-series2-sealed-bundle-recovery-review-request-v2"
)
EPOCH_8_RECOVERY_AUTHORIZATION_SCHEMA = (
    "p4.2a-v2-2-series2-sealed-bundle-recovery-authorization-v1"
)
EPOCH_8_RECOVERY_OWNER_BINDING_SCHEMA = (
    "p4.2a-v2-2-series2-sealed-bundle-recovery-owner-confirmation-binding-v1"
)
EPOCH_8_RECOVERY_STARTED_SCHEMA = "p4.2a-v2-2-series2-bundle-recovery-started-v1"
EPOCH_8_RECOVERY_TERMINAL_SCHEMA = "p4.2a-v2-2-series2-bundle-recovery-terminal-v1"
EPOCH_8_RECOVERY_MIRROR_RECEIPT_SCHEMA = (
    "p4.2a-v2-2-series2-bundle-recovery-mirror-receipt-v1"
)
EPOCH_8_READ_ONLY_PREFLIGHT_SCHEMA = "p4.2a-v2-2-read-only-implementation-preflight-v2"
SERIES_2_RECOVERY_REVIEW_REQUEST_SCHEMA = (
    "p4.2a-v2-2-series2-sealed-bundle-recovery-review-request-v2"
)
SERIES_2_RECOVERY_AUTHORIZATION_SCHEMA = (
    "p4.2a-v2-2-series2-sealed-bundle-recovery-authorization-v1"
)
SERIES_2_RECOVERY_OWNER_BINDING_SCHEMA = (
    "p4.2a-v2-2-series2-sealed-bundle-recovery-owner-confirmation-binding-v1"
)
SERIES_2_RECOVERY_STARTED_SCHEMA = "p4.2a-v2-2-series2-bundle-recovery-started-v1"
SERIES_2_RECOVERY_TERMINAL_SCHEMA = "p4.2a-v2-2-series2-bundle-recovery-terminal-v1"
SERIES_2_RECOVERY_MIRROR_RECEIPT_SCHEMA = (
    "p4.2a-v2-2-series2-bundle-recovery-mirror-receipt-v1"
)
SERIES_2_READ_ONLY_PREFLIGHT_SCHEMA = "p4.2a-v2-2-read-only-implementation-preflight-v2"
EPOCH_9_DESIGN_R1_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-epoch9-source-projection-identity-design-proposal-20260903.md"
)
EPOCH_9_DESIGN_R1_SHA256 = "2f479639273f374ccea36fa6c4dafa0aa2023817ad4fd377df7e78b02db9e23c"
EPOCH_9_DESIGN_R1_BYTES = 16_719
EPOCH_9_DESIGN_R1_COMMIT = "094816f11bdb26f9d4141db6c5ddf46de1f735cb"
EPOCH_9_DESIGN_R2_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-epoch9-source-projection-identity-design-proposal-r2-20260903.md"
)
EPOCH_9_DESIGN_R2_SHA256 = "afaf02b0cc2f53cd891e0ec65a8ad125ce8d7508cd7555b7d651274b8c6e541a"
EPOCH_9_DESIGN_R2_BYTES = 3_232
EPOCH_9_DESIGN_R2_COMMIT = "fce157432a505c7d0cd09263b0d7db8dd3f049da"
EPOCH_9_DESIGN_R3_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-epoch9-latest-landed-epoch-design-r3-20260903.md"
)
EPOCH_9_DESIGN_R3_SHA256 = "5f99c0ac8cf708467e6c37c4516a30941af313cebbcd9d3141bd8b048e1460d9"
EPOCH_9_DESIGN_R3_BYTES = 45_262
EPOCH_9_DESIGN_R3_COMMIT = "7bf6d7fd0b054d6be775c36583085b443e0769f2"
EPOCH_9_DESIGN_REVIEW_R3_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-epoch9-latest-landed-epoch-independent-design-review-r3-"
    "20260903.json"
)
EPOCH_9_DESIGN_REVIEW_R3_SHA256 = (
    "24c66b7027d30b3fc168ae3ffa9dff0a0fe707d61130c5102b67e3386c6425f9"
)
EPOCH_9_DESIGN_REVIEW_R3_BYTES = 13_519
EPOCH_9_DESIGN_REVIEW_R3_COMMIT = "dad5d0d332752bf99beeb9c5be55d1d2323bafab"
EPOCH_9_ADJUDICATION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-series2-epoch9-r3-design-conflict-adjudication-20260903.json"
)
EPOCH_9_ADJUDICATION_SHA256 = "204f0a4814d25f37390db154d6000b37fca36aa6e54ea35617c415308721cece"
EPOCH_9_ADJUDICATION_BYTES = 9_387
EPOCH_9_ADJUDICATION_COMMIT = "5c58141fd5c2622e147daa8f48f15c486515378d"
EPOCH_9_COMPANION_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-series2-epoch9-r3-companion-20260903.json"
)
EPOCH_9_COMPANION_SHA256 = "b3f5769a5ed9dd74d72e29dcad6612313e936d0f6c0537801bed82338ba74667"
EPOCH_9_COMPANION_BYTES = 26_612
EPOCH_9_COMPANION_COMMIT = "20ed6a747c66a775ad3793167fe3f511f1c16ab8"
EPOCH_9_LATEST_LANDED_CONTRACT_SCHEMA = (
    "p4.2a-v2-2-series2-epoch9-latest-landed-execution-contract-v1"
)
EPOCH_9_LATEST_LANDED_CONTRACT_CANONICAL_SHA256 = (
    "4d2c9d58c4e6b851de741d5f52861a4c18ff6b707280714debac829a1079272d"
)
EPOCH_9_SUPERSEDED_SURFACE_AUTHORITY_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-series2-epoch9-surface-authority-20260903.json"
)
EPOCH_9_SUPERSEDED_SURFACE_AUTHORITY_SHA256 = (
    "e5501e387d85be43fabe131b1e3d5067ad189db2e81a255744ad29b99446379e"
)
EPOCH_9_SUPERSEDED_SURFACE_AUTHORITY_BYTES = 517
EPOCH_9_SUPERSEDED_SURFACE_AUTHORITY_COMMIT = (
    "6936f4531eb52a6a343bf8a20e879dac1503bbdd"
)
EPOCH_9_SURFACE_AUTHORITY_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-series2-epoch9-r3-surface-authority-20260903.json"
)
EPOCH_9_SURFACE_AUTHORITY_SHA256 = (
    "ecbd909ba56158e644403a5a3f38f63e5714bee7b638fc9f72b9ba0541b97458"
)
EPOCH_9_SURFACE_AUTHORITY_BYTES = 726
EPOCH_9_SURFACE_AUTHORITY_COMMIT = "cc64e0b79fbb3a667eb8545c7617e7e8e59115e4"
EPOCH_7_LIVE_REVIEW_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-epoch7-r2-implementation-independent-review-20260831.json"
)
EPOCH_7_LIVE_REVIEW_SHA256 = "5712cc01f088ba96e9f199e60e327f171e24b23a4b6c1ca972d147bba75a208f"
EPOCH_7_LIVE_REVIEW_BYTES = 15_546
EPOCH_7_LIVE_REVIEW_LANDING_COMMIT = "6f150a31336fbb06cfbe0c42507806025b42daaa"
EPOCH_7_LIVE_LANDING_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-series2-epoch7-r2-merge-landing-record-20260901.json"
)
EPOCH_7_LIVE_LANDING_SHA256 = (
    "03ba42262592c67df605021ee4f2ec5dfc495301f28f7ceb2aa514697f010fb6"
)
EPOCH_7_LIVE_LANDING_BYTES = 6_625
EPOCH_7_LIVE_LANDING_COMMIT = "dcd749f4707f6b806249e842e1402d0c12df2fbf"
REFUSED_RECOVERY_Q_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-through-ordinal-000002-bundle-recovery-review-request-20260901.json"
)
REFUSED_RECOVERY_Q_SHA256 = "fbd9df2346090a4ac23a1957f7367103229316b38a3a3c76d2392657f0a2938f"
REFUSED_RECOVERY_Q_BYTES = 55_722
REFUSED_RECOVERY_Q_COMMIT = "f9743fdfce5d503c975a8fae3b32e95501b86db2"
REFUSED_RECOVERY_R_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-through-ordinal-000002-bundle-recovery-authorization-20260901.json"
)
REFUSED_RECOVERY_R_SHA256 = "0eba7d27441e83547a0052d1fc184e9bee3df03dda447942cf351e765121d890"
REFUSED_RECOVERY_R_BYTES = 9_272
REFUSED_RECOVERY_R_COMMIT = "88de28884f33ec4beba2dd4c42880fdb9c9ae9a8"
REFUSED_RECOVERY_B_RELATIVE = Path(
    "docs/phase4/reports/"
    "P4.2a-v2-2-series2-through-ordinal-000002-bundle-recovery-owner-confirmation-"
    "binding-20260901.json"
)
REFUSED_RECOVERY_B_SHA256 = "34b50becd377c65dc5ef17e83b7794be1c9800a0e263a653f30338dbdad29cc2"
REFUSED_RECOVERY_B_BYTES = 2_841
REFUSED_RECOVERY_B_COMMIT = "7b9aa18372410baa5d96bd9560fa10a2c6a3d8ac"
HISTORICAL_SELECTED_EPOCH = 6
HISTORICAL_SELECTED_IMPLEMENTATION_COMMIT = "e5aab9772793a7b0465f100cb48f99a1bc4e45dc"
HISTORICAL_SELECTED_CONTROL_ROOT_SHA256 = (
    "5948fd29a8c3f38399e6518699483f61094d577ae695bc7aa0b48c84e5b8829d"
)
SEALED_SERIES_HISTORY_ROOT_SHA256 = (
    "832559b59a8edc09c04b0f5a7c09cea71e5c3597c2bdc0831072a4122bf016e7"
)
SEALED_SERIES_LIVE_ROOT_SHA256 = "ab08612ba2e11e45c3a3415ca7079117f2a739bf470719abf4f204958272574d"
SEALED_SELECTED_CANDIDATE_SHA256 = (
    "e1d67123469ce63739936b7db8a520f4f0cc8dda969455a7117246cda4485086"
)
SEALED_SELECTED_TERMINAL_SHA256 = "3f41d80d63214af379bd8423ab7e0c61d6508ab19339f9bc7bf9a1d9ac4e0bf5"
SEALED_SELECTED_EVIDENCE_ROOT_SHA256 = (
    "eb44c7f3219e3f9ce92fbf17fd2da0e4b643ad0abc09f4008b2da1e35d426093"
)
SEALED_SELECTED_RUN_ROOT_SHA256 = "5fb8edf3aa65cdcd0f54b82bdf6f240104fa8537c1004e640671910115f8f314"
SEALED_SELECTED_RUN_A_PROBE_SHA256 = (
    "c53e94d513443399e2135c77fb6f556bc3359fb39f77ab0882755afe1a77628b"
)
SEALED_SELECTED_RUN_B_PROBE_SHA256 = (
    "7552c2a86515adae7206423429bf8fb61f5ca0a2038ceccf0734be447c5ded0b"
)
SEALED_SELECTED_TERMINAL_INVENTORY_COUNT = 36
SEALED_SELECTED_TERMINAL_INVENTORY_BYTES = 50_213_329
SEALED_MIRROR_RECEIPT_SHA256 = "b8da48fa759d7f5301dff63eed61c711d3fb01e2715fbc45ddd27a28545820f6"
SEALED_MIRROR_RECEIPT_BYTES = 1222
SERIES_2_EPOCH_5_LANDING_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-series2-epoch5-registered-gate-landing-report-20260826.json"
)
SERIES_2_EPOCH_5_LANDING_SHA256 = "cd3b0faf61d54824739f2f5263718aee455cd1ef59199ea8a7076ffe60f39ac9"
SERIES_2_EPOCH_5_LANDING_COMMIT = "9094039a09034e279fb26f97d2830aa227fdcdad"
SERIES_2_EPOCH_5_MERGE_COMMIT = "c41f333419a58731e85d23b74cffea0fca564c5d"
SERIES_2_EPOCH_6_LANDING_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-series2-epoch6-registered-gate-landing-report-20260827.json"
)
SERIES_2_EPOCH_6_LANDING_SHA256 = "ceffb325dc69f04a2158fe94bead7d841602613a1e2dc280d36bfced7e6ce6fc"
SERIES_2_EPOCH_6_LANDING_COMMIT = "ef21ffe14fb6bdd90346ec3694cc986e46212e1d"
SERIES_2_EPOCH_6_MERGE_COMMIT = "0961b1a781c5618a8623155b3ea911de7e9717da"
SERIES_2_ATTEMPT_1_AUTHORITY = (
    "docs/phase4/reports/P4.2a-v2-2-rehearsal-attempt-000001-execution-authorization-20260826.json",
    "4cf5d3936754fabb155880b9936198b389efb7e167f61d6c42d4dcf75ae8f05b",
    "911b6c5695a2e0014546edf9ce919d9d8922586e",
)
SERIES_2_ATTEMPT_2_AUTHORITY = (
    "docs/phase4/reports/P4.2a-v2-2-rehearsal-attempt-000002-execution-authorization-20260827.json",
    "371d92b946bf9f1f2e3ea67bf5cd8a47bc73190df33b89b8d404f92c12c97138",
    "2877f24843c69ec295f7fcb5ffe19ffd81371144",
)
SERIES_2_EPOCH_5_SURFACE_AUTHORITY = (
    "docs/phase4/reports/P4.2a-v2-2-series-2-epoch5-surface-authority-20260823.json",
    "b97dce798eed5be8450e462cfdfccde949677c823c867ed35b6738dc5f3f4270",
    "5bea28957e873857e7bca6dd30f7226d8b09bbf7",
)
SERIES_2_EPOCH_5_REVIEW = (
    "docs/phase4/reports/P4.2a-v2-2-series2-epoch5-implementation-independent-review-20260825.json",
    "cd220ea474e7e7f92e85b42411f03274352d4a6b7323a41d68eb8ca4626324f2",
    SERIES_2_EPOCH_5_MERGE_COMMIT,
)
SERIES_2_EPOCH_6_SURFACE_AUTHORITY = (
    "docs/phase4/reports/P4.2a-v2-2-series2-epoch6-surface-authority-20260826.json",
    "b2a0e1c3aae4b6b826b522aa74472415b1b782990326301aa68e467eadc45a92",
    "3ccc2f267a05137edf86c5eb72f82e0057d74f98",
)
SERIES_2_EPOCH_6_REVIEW = (
    "docs/phase4/reports/P4.2a-v2-2-series2-epoch6-implementation-independent-review-20260827.json",
    "84c6fab7ca36087b656cda17351da298a8bc4a7b4093a059f91a085b286d26e4",
    SERIES_2_EPOCH_6_MERGE_COMMIT,
)
INDEPENDENT_REVIEW_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-preregistration-independent-review-20260811.json"
)
INDEPENDENT_REVIEW_SHA256 = "6707e2b3c0b2ba87712e88b59ceaed17524be2de947b764a94c8b170b2a30bb6"
INDEPENDENT_REVIEW_COMMIT = "b21e1bdbf865dfd9c7605ecc7794fc3f8701ed1f"
INITIAL_REVIEWED_COMMIT = "be6423506f598c290db7ad944b002763fdf806ab"
INITIAL_IMPLEMENTATION_PARENT = INITIAL_REVIEWED_COMMIT
VOID_EPOCH_ONE_IMPLEMENTATION_COMMIT = "fae44154d3504017742934ff3b3961642c35eb65"
VOID_EPOCH_ONE_IMPLEMENTATION_PARENT = "cf10ef8d636049b0fc206c8698a809be3090e1d7"
VOID_EPOCH_ONE_LANDING_COMMIT = "be7a2cedff1ad4bf523d88d83fa333126d502720"
VOID_EPOCH_ONE_REVIEW_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-2-remediation-independent-review-20260812.json"
)
VOID_EPOCH_ONE_REVIEW_SHA256 = "e348bbc6c2976d473bf2b8e5b280784fd45ff7ae1ba7d7a4119309eb178b16cf"
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
REGISTERED_DESTINATION_RELATIVE = Path("docs/phase4/rehearsals/P4.2a-v2-calibration-v2-2")
REGISTERED_SERIES_TOKEN = "35ba1b83a9b187817d7a591758e1c131e867fcd37917cba0ab196799fff832ef"
INCIDENT_SHA256 = "d658336f61cdca0239584b696043fe4abc5ede1ef7aff76a4fe514b7b5d0735c"
SERIES_2_REGISTERED_SERIES_TOKEN = (
    "2543d679819f96958baf747ef61dda2044013a0b00a9cb824c0d7675640d9f93"
)
SERIES_2_PRIMARY_SERIES_CONTAINER = Path(
    "/Users/ouyangduning/AlphaPilot-EVIDENCE-DO-NOT-DELETE/P4.2a/v2.2/"
    f"SERIES-000002-{SERIES_2_REGISTERED_SERIES_TOKEN}"
)
SERIES_2_PRIMARY_LEDGER_ROOT = SERIES_2_PRIMARY_SERIES_CONTAINER / "PRIMARY-LEDGER-DO-NOT-DELETE"
SERIES_2_PRIMARY_RECEIPT_ROOT = SERIES_2_PRIMARY_SERIES_CONTAINER / "MIRROR-RECEIPTS-DO-NOT-DELETE"
SERIES_2_SECONDARY_SERIES_CONTAINER = Path(
    "/Users/ouyangduning/AlphaPilot-EVIDENCE-MIRROR-DO-NOT-DELETE/P4.2a/v2.2/"
    f"SERIES-000002-{SERIES_2_REGISTERED_SERIES_TOKEN}"
)
SERIES_2_SECONDARY_SNAPSHOT_ROOT = (
    SERIES_2_SECONDARY_SERIES_CONTAINER / "SEALED-LEDGER-SNAPSHOTS-DO-NOT-DELETE"
)
SERIES_2_SECONDARY_RECEIPT_ROOT = (
    SERIES_2_SECONDARY_SERIES_CONTAINER / "MIRROR-RECEIPTS-DO-NOT-DELETE"
)
SERIES_2_PRIMARY_RECOVERY_CONTAINER = Path(
    "/Users/ouyangduning/AlphaPilot-EVIDENCE-DO-NOT-DELETE/P4.2a/v2.2/"
    f"BUNDLE-RECOVERY-SERIES-000002-{SERIES_2_REGISTERED_SERIES_TOKEN}"
)
SERIES_2_SECONDARY_RECOVERY_CONTAINER = Path(
    "/Users/ouyangduning/AlphaPilot-EVIDENCE-MIRROR-DO-NOT-DELETE/P4.2a/v2.2/"
    f"BUNDLE-RECOVERY-SERIES-000002-{SERIES_2_REGISTERED_SERIES_TOKEN}"
)
SERIES_2_LEGACY_LEDGER_ROOT = REGISTERED_PROJECT_ROOT.parent / (
    ".alphapilot-p4-2a-v2-2-execution-claim-" + REGISTERED_SERIES_TOKEN
)
SERIES_2_RETIRED_V2_1_CLAIM = REGISTERED_PROJECT_ROOT.parent / (
    ".alphapilot-p4-2a-v2-1-execution-claim-"
    "52378ddcda558a8489795c62a5c4d290687700801320508c03c51589c202e962"
)
MIRROR_INVENTORY_PREFIX = b"p4.2a-rehearsal-v2.2-mirror-inventory-v1\0"
MIRROR_RECEIPT_SCHEMA = "p4.2a-v2-2-series-2-mirror-verification-v1"
FIXED_WALL_CLOCK_TEXT = "2026-08-10T12:30:00Z"
SERIES_2_LOST_HISTORY_SUMMARY: JsonObject = {
    "classification": "DIGEST_PROOF_CHAIN_NOT_RECONSTRUCTABLE_LEDGER_BYTES",
    "old_series_token_sha256": REGISTERED_SERIES_TOKEN,
    "old_ledger_root": SERIES_2_LEGACY_LEDGER_ROOT.as_posix(),
    "history_root_after_ordinal_1": (
        "076ae961fc149ae271bf5a3724c1677abccfea7139589909ea717a7f4a38083a"
    ),
    "history_root_after_ordinal_2": (
        "a466de7b349882f2bcd556a4b4d00bf38bace9adb593b0e3b6296c415a8c9ca1"
    ),
    "attempt_1": {
        "ordinal": 1,
        "outcome": "FAILED",
        "implementation_epoch": 2,
        "implementation_commit": "1b4e05c6acd513bb1bc11245911da97b6a128ca1",
        "evidence_tree_root_sha256": (
            "deea0e81e3fd8a5c886cc4c757fb5485cb7f750718462489dea48d3deed2691c"
        ),
    },
    "attempt_2": {
        "ordinal": 2,
        "outcome": "CANDIDATE_VALIDATED_AND_SELECTED",
        "implementation_epoch": 4,
        "implementation_commit": "890e9002116c625d41f6aa037975df15d1546c56",
        "started_sha256": ("75771a37572fb9191a9db26f986b1e9d89c26843556b502866322a8f4bdaf42d"),
        "candidate_sha256": ("92652f963b04b79e29580978cd6857c2154df0b429ac09502be6c0c0c5d84da5"),
        "terminal_sha256": ("7ba4ed1b5d7e7abc462b312f08b131ff438cc524cecbdeea6b43dc199292e3dc"),
        "evidence_tree_root_sha256": (
            "f38b18b972f14a170fc9bb4129f25ec77e8ad1c4e8a8f137b5853cc371b694c2"
        ),
        "run_a_and_run_b_root_sha256": (
            "5fb8edf3aa65cdcd0f54b82bdf6f240104fa8537c1004e640671910115f8f314"
        ),
        "candidate_content_root_sha256": (
            "5de4f74d1f73e5f90aa9c196c8fc6574bce2ecfa91abd750b22726c14c6a60b7"
        ),
        "selected_control_root_sha256": (
            "76076606d6e40cdd386b28cdd5bc40a8957693b8cfdc8b17a0a77410b4e082e8"
        ),
        "q_r_b_commits": [
            "f6f993d0e9f30b6f6c5250a94a4a49b179fc8ff1",
            "f004054c1797904206e5590f2b0f4751848665c1",
            "1832ed7c6130b71d5a99722721eaec83b2adabdd",
        ],
        "execution_authorization_sha256": (
            "8f5c0b31ef88922b8b44b202c729fad55a4d0cb172c000089991f1cd2c995461"
        ),
    },
    "contemporaneous_verification_commits": [
        "7fc122f575801ff43d2446a2c59491a086735e93",
        "0548692480ff8325b69be92f01e0d42e11ad4eb0",
        "7cf819d43d5b73f7b8f1469a556c79ece12587f0",
    ],
    "series_1_outcome_stands": True,
    "series_1_bundle_cannot_be_reconstructed_or_released": True,
    "series_1_digests_enter_series_2_amendment_lineage": True,
    "series_1_digests_enter_series_2_attempt_history_root": False,
    (
        "series_1_digests_enter_series_2_control_surface_and_bundle_lineage_through_the_amendment"
    ): True,
    "old_ledger_and_retired_v2_1_claim_must_remain_absent": True,
}
BUNDLE_FILENAME = "bundle.json"
RELEASE_RELATIVE = Path(
    "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-2-release-authorization-20260811.json"
)
VALIDATOR_RESULT_SCHEMA = "p4.2a-v2-heldout-validator-result-v2.2"
CONTROL_MANIFEST_SCHEMA = "p4.2a-v2-heldout-rehearsal-control-manifest-v2.2"

_CARRY_FORWARD_AUTHORITIES = {
    "v2_1_preregistration": (
        "docs/phase4/reports/P4.2a-v2-heldout-rehearsal-v2-1-preregistration-20260810.json",
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
        "docs/phase4/reports/P4.2a-rehearsal-v2-approval-and-frame-authority-ruling-20260810.json",
        "8605dd30dd6bffa9621b6efe5e01c4c9cead615c9639259c2e79e14fcbbc3421",
        "da374342781d6fde2f2c6d87d23582050bc8edaa",
    ),
    "v2_1_code_gate_authorization": (
        "docs/phase4/reports/P4.2a-successor-v2-1-code-gate-authorization-20260810.json",
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
        "docs/phase4/reports/P4.2a-v2-1-implementation-independent-review-20260811.json",
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
_V2_1_BUNDLE_SCHEMA_SHA256 = "ed827e29ce853f07a9110d44c98793a4cc3ef0634a12fe7e8bc64c7290d7d716"
_V2_1_RELEASE_SCHEMA_RELATIVE = Path(
    "config/schemas/p4_2a_v2_1_heldout_release_authorization.schema.json"
)
_V2_1_RELEASE_SCHEMA_SHA256 = "c5a4ecfe8c5bf3e3ebea2d4470337a67dde3a8e9dbe6fc3df68b1c4e16241c51"
_INHERITANCE_SNAPSHOT_SHA256 = "f3d74f06c9b114ce85768f647252db76edadc42a95ab6a6f29c05d69f39bea0e"
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
SERIES_2_BUNDLE_SCHEMA_DELTA_POINTERS = (
    "/$id",
    "/$defs/lineage/properties/preregistration/allOf/1/properties/path/const",
    "/$defs/lineage/properties/bundle_schema/allOf/1/properties/path/const",
    ("/$defs/lineage/properties/release_authorization_schema/allOf/1/properties/path/const"),
    ("/$defs/lineage/properties/release_authorization_schema/allOf/1/properties/sha256/const"),
    "/$defs/executionBinding/oneOf/0/properties/series_token_sha256/const",
    "/$defs/executionBinding/oneOf/0/properties/ledger_root/const",
)
SERIES_2_RELEASE_SCHEMA_DELTA_POINTERS = (
    "/$id",
    "/properties/lineage/properties/preregistration/allOf/1/properties/path/const",
    "/properties/lineage/properties/bundle_schema/allOf/1/properties/path/const",
    "/properties/lineage/properties/release_schema/allOf/1/properties/path/const",
    "/$defs/executionBinding/oneOf/0/properties/series_token_sha256/const",
    "/$defs/executionBinding/oneOf/0/properties/ledger_root/const",
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
_RECOVERY_Q_PATH_PATTERN = re.compile(
    r"^docs/phase4/reports/P4\.2a-v2-2-series2-through-ordinal-000002-"
    r"bundle-recovery-review-request-([0-9]{8})\.json$"
)
_RECOVERY_R_PATH_PATTERN = re.compile(
    r"^docs/phase4/reports/P4\.2a-v2-2-series2-through-ordinal-000002-"
    r"bundle-recovery-authorization-([0-9]{8})\.json$"
)
_RECOVERY_B_PATH_PATTERN = re.compile(
    r"^docs/phase4/reports/P4\.2a-v2-2-series2-through-ordinal-000002-"
    r"bundle-recovery-owner-confirmation-binding-([0-9]{8})\.json$"
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
_SERIES_2_FIELDS = _SERIES_FIELDS | frozenset({"implementation_epoch_origin"})
_MIRROR_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "series_token_sha256",
        "ordinal",
        "attempt_outcome",
        "attempt_sealed",
        "primary_ledger_root",
        "secondary_snapshot_root",
        "history_root_sha256",
        "live_ledger_root_sha256",
        "file_count",
        "total_bytes",
        "primary_inventory_sha256",
        "secondary_inventory_sha256",
        "second_copy_verified",
        "verified_at_utc",
    }
)
EPOCH_7_RECOVERY_CONTRACT_FIELDS = frozenset(
    {
        "schema_version",
        "governing_adjudication",
        "implementation_epoch",
        "recovery_review_request_contract",
        "recovery_authorization_contract",
        "recovery_owner_binding_contract",
        "recovery_claim_contract",
        "bundle_mirror_receipt_contract",
        "dual_byte_anchor_contract",
        "unique_a_and_lineage_census_contract",
        "protected_inputs_and_permitted_outputs",
        "legacy_absence_and_locks",
    }
)
EPOCH_8_RECOVERY_CONTRACT_FIELDS = frozenset(
    {
        "schema_version",
        "governing_adjudication",
        "implementation_epoch",
        "registered_preflight_contract",
        "recovery_review_request_contract",
        "recovery_authorization_contract",
        "recovery_owner_binding_contract",
        "recovery_claim_contract",
        "bundle_mirror_receipt_contract",
        "dual_byte_anchor_contract",
        "unique_a_and_lineage_census_contract",
        "protected_inputs_and_permitted_outputs",
        "legacy_absence_and_locks",
    }
)
EPOCH_9_LATEST_LANDED_CONTRACT_FIELDS = frozenset(
    {
        "schema_version",
        "implementation_epoch",
        "governing_adjudication",
        "superseded_two_file_authority",
        "historical_epoch_8_recovery_contract",
        "latest_landed_authority_contract",
        "registered_preflight_dispatch_contract",
        "dual_byte_anchor_transition_contract",
        "recovery_qrb_live_binding_contract",
        "recovery_claim_and_receipt_live_binding_contract",
        "recovered_publication_and_release_live_binding_contract",
        "authority_census_and_effect_lock_contract",
    }
)
EPOCH_9_LATEST_LANDED_NESTED_FIELDS: Mapping[str, frozenset[str]] = {
    "governing_adjudication": frozenset(
        {"path", "sha256", "bytes", "creating_commit", "unique_a_history_verified"}
    ),
    "superseded_two_file_authority": frozenset(
        {"path", "sha256", "bytes", "creating_commit", "disposition"}
    ),
    "historical_epoch_8_recovery_contract": frozenset(
        {
            "companion_path",
            "companion_sha256",
            "companion_bytes",
            "companion_creating_commit",
            "contract_schema_version",
            "contract_canonical_sha256",
            "preservation",
        }
    ),
    "latest_landed_authority_contract": frozenset(
        {
            "expected_implementation_epoch",
            "owner_identity_mode",
            "review_identity_mode",
            "landing_identity_mode",
            "landing_document_required_fields",
            "topology_requirements",
            "runtime_binding_chain",
            "unknown_values_policy",
        }
    ),
    "registered_preflight_dispatch_contract": frozenset(
        {
            "landing_preflight_origin_epoch",
            "latest_official_epoch",
            "historical_epoch_8_policy",
            "unknown_later_epoch_policy",
            "output_schema_version",
            "zero_effect_required",
            "runtime_binding_order",
        }
    ),
    "dual_byte_anchor_transition_contract": frozenset(
        {"historical_selected_anchor", "live_execution_anchor", "sealed_attempt_epoch_table"}
    ),
    "recovery_qrb_live_binding_contract": frozenset(
        {
            "q_schema",
            "q_top_level_fields",
            "r_schema",
            "r_top_level_fields",
            "b_schema",
            "b_top_level_fields",
            "runtime_live_binding_fields",
            "cross_epoch_policy",
            "counters",
        }
    ),
    "recovery_claim_and_receipt_live_binding_contract": frozenset(
        {
            "started_schema",
            "started_top_level_fields",
            "terminal_schema",
            "terminal_top_level_fields",
            "mirror_receipt_schema",
            "mirror_receipt_top_level_fields",
            "live_binding_rules",
        }
    ),
    "recovered_publication_and_release_live_binding_contract": frozenset(
        {
            "capability_top_level_fields",
            "historical_binding_rule",
            "live_binding_rule",
            "bundle_mode",
            "release_mode",
            "active_fallback_forbidden",
            "zero_effect_rule",
        }
    ),
    "authority_census_and_effect_lock_contract": frozenset(
        {
            "fixed_base_governance_rows",
            "dynamic_runtime_rows",
            "permanent_absence_fields",
            "sealed_attempt_epochs",
            "effect_limits",
            "lock_values",
        }
    ),
}
EPOCH_9_LANDING_DOCUMENT_REQUIRED_FIELDS = (
    "implementation_epoch",
    "owner_exact_surface_authorization",
    "implementation_commit",
    "independent_review_source_and_projection",
    "ordered_merge_parents",
    "merge_commit",
    "landing_commit_and_parent",
    "control_merkle_root_sha256",
    "control_record_count",
    "runner_validator_and_combined_test_counts",
    "gate_evidence_digests",
)
EPOCH_9_LATEST_TOPOLOGY_REQUIREMENTS = (
    "owner authority is one logical source with only lawful byte-identical first-parent "
    "projections",
    "implementation is the authority source commit direct single-parent exact-four-M child",
    "independent review is the implementation direct single-parent unique-A report",
    "no-ff merge ordered parents are fresh_main_base then review_commit and preserve reviewed "
    "implementation bytes",
    "landing report is the merge direct single-parent unique-A child",
    "every named commit is on execution lineage and every named document matches immutable Git "
    "and required current bytes",
)
EPOCH_9_RUNTIME_BINDING_CHAIN = (
    "landing_report",
    "registered_read_only_preflight",
    "recovery_review_request_Q",
    "recovery_authorization_R",
    "owner_confirmation_binding_B",
)
EPOCH_9_UNKNOWN_VALUES_POLICY = (
    "future implementation, review, merge, landing, control-root, and census values must be "
    "obtained from the actual validated runtime chain and may never be defaulted, guessed, "
    "left empty, or represented by a placeholder"
)
EPOCH_9_HISTORICAL_EPOCH_8_POLICY = (
    "validate the immutable epoch-8 recovery contract and historical or synthetic proof but "
    "reject epoch 8 as the latest official execution epoch after epoch 9 lands"
)
EPOCH_9_PREFLIGHT_RUNTIME_BINDING_ORDER = (
    "locked process interpreter environment and complete CLI",
    "immutable epoch-8 historical contract",
    "epoch-9 companion authority review landing and topology",
    "epoch-9 current control and loaded module bytes plus first real-lineage census",
    "series storage recovery containers sealed ledger and mirror",
    "second identical census complete before-after equality and one canonical stdout line",
)
EPOCH_9_REQUIRED_CURRENT_SURFACES = (
    "complete registered control-surface bytes",
    "loaded producer module origin and digest",
    "loaded validator module origin and digest",
)
EPOCH_9_REQUIRED_LINEAGE_OBJECTS = (
    "epoch-9 four-file owner surface authority",
    "epoch-9 implementation",
    "epoch-9 independent implementation review source and projection",
    "epoch-9 ordered-parent no-ff merge",
    "epoch-9 unique-A landing report",
)
EPOCH_9_LIVE_RUNTIME_VALUE_SOURCE = (
    "the validated epoch-9 landing report followed by the one real preflight and exact Q then "
    "R then B byte chain"
)
EPOCH_9_QRB_RUNTIME_LIVE_BINDING_FIELDS = (
    "execution epoch 9",
    "epoch-9 implementation authority review merge and landing",
    "epoch-9 control root count and real-lineage census",
    "selected sealed epoch 6 and attempts [5,6]",
    "exact argv and environment hashes",
    "owner identity topology destination storage roots and claim naming",
)
EPOCH_9_QRB_CROSS_EPOCH_POLICY = (
    "reject every epoch-8 and epoch-9 mixture across Q, R, B, embedded preflight, owner, "
    "review, merge, landing, control, and census before storage access"
)
EPOCH_9_CLAIM_LIVE_BINDING_RULES = (
    "started.execution_head and started.execution_epoch bind the epoch-9 live anchor",
    "mirror receipt execution_epoch execution_implementation_commit and execution_head equal "
    "the same epoch-9 live anchor and started record",
    "terminal has no execution epoch or head field and gains none",
    "terminal identity is proven through exact R and B references claim path recovery_id "
    "receipt references and the paired receipt live tuple",
    "producer and validator independently recompute the complete chain before accepting any "
    "recovered output",
)
EPOCH_9_PUBLICATION_HISTORICAL_BINDING_RULE = (
    "selected ordinal 2 epoch 6 implementation commit and sealed candidate terminal evidence "
    "history ledger and run roots are recomputed from immutable Git and sealed evidence with "
    "require_current false"
)
EPOCH_9_PUBLICATION_LIVE_BINDING_RULE = (
    "execution epoch 9 implementation owner review merge landing current control bytes loaded "
    "module bytes and final real-lineage census are independently recomputed with "
    "require_current true"
)
EPOCH_9_CENSUS_FIXED_BASE_ROWS = (
    (
        "immutable registered authority registry and epoch-8 governance chain",
        "preserve every pinned source path raw byte SHA byte count source parent role and "
        "lawful projection rule",
    ),
    (
        "epoch-9 r3 governing adjudication",
        "use the exact governing_adjudication tuple in this contract as a PINNED_SOURCE",
    ),
    (
        "this epoch-9 r3 companion",
        "derive its exact path SHA bytes and unique-A creating commit from the future authority "
        "base commit and verify its direct parent is the governing adjudication",
    ),
    (
        "future epoch-9 four-file surface authority",
        "derive its exact path SHA bytes and unique-A source commit from the caller-supplied "
        "authority and require its base_commit to equal this companion creating commit",
    ),
)
EPOCH_9_CENSUS_DYNAMIC_RUNTIME_ROWS = (
    "epoch-9 independent implementation review source and lawful first-parent projection",
    "epoch-9 no-ff merge",
    "epoch-9 unique-A landing report",
    "fresh Q then R then B exact linear ref delta at recovery start",
)
EPOCH_7_RECOVERY_REVIEW_REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "request_id",
        "created_at_utc",
        "created_at_shanghai",
        "status",
        "requester",
        "landed_epoch_7",
        "registered_read_only_recovery_preflight",
        "preflight_before_after_equality",
        "proposed_recovery_authorization",
        "requested_owner_action_time_confirmation",
        "post_confirmation_plan_not_yet_executed",
        "current_locks",
    }
)
RECOVERY_REVIEW_REQUEST_FIELDS = frozenset(
    {
        "schema_version",
        "request_id",
        "created_at_utc",
        "created_at_shanghai",
        "status",
        "requester",
        "landed_execution_epoch",
        "registered_read_only_recovery_preflight",
        "preflight_before_after_equality",
        "proposed_recovery_authorization",
        "requested_owner_action_time_confirmation",
        "post_confirmation_plan_not_yet_executed",
        "current_locks",
    }
)
EPOCH_8_READ_ONLY_PREFLIGHT_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "mode",
        "execution_head",
        "implementation_epoch",
        "implementation_commit",
        "owner_exact_surface_authorization",
        "independent_implementation_review",
        "control_merkle_root_sha256",
        "control_record_count",
        "registered_surface",
        "series_2_registered_storage",
        "real_lineage_census",
        "registered_recovery_storage",
        "sealed_recovery_inputs",
        "effect_summary",
    }
)
SERIES_2_READ_ONLY_PREFLIGHT_FIELDS = EPOCH_8_READ_ONLY_PREFLIGHT_FIELDS
RECOVERY_AUTHORIZATION_FIELDS = frozenset(
    {
        "schema_version",
        "authorization_id",
        "created_at_utc",
        "created_at_shanghai",
        "verdict",
        "owner",
        "sealed_series",
        "execution_epoch",
        "destination",
        "exact_argv",
        "command_sha256",
        "exact_environment",
        "environment_sha256",
        "authorized_bundle_recovery_starts",
        "authorized_pipeline_starts",
        "automatic_retry_count",
        "effect_authorization",
        "interpreter",
        "locks",
    }
)
RECOVERY_OWNER_BINDING_FIELDS = frozenset(
    {
        "schema_version",
        "binding_id",
        "created_at_utc",
        "created_at_shanghai",
        "status",
        "review_request",
        "recovery_authorization",
        "owner_confirmation",
        "authorized_scope",
        "explicit_exclusions",
        "registered_read_only_recovery_preflight",
        "machine_boundary",
    }
)
RECOVERY_STARTED_FIELDS = frozenset(
    {
        "schema_version",
        "recovery_id",
        "authorization",
        "owner_confirmation_binding",
        "created_at_utc",
        "created_at_shanghai",
        "execution_head",
        "execution_epoch",
        "sealed_history_root_sha256",
        "sealed_live_ledger_root_sha256",
        "sealed_mirror_receipt_sha256",
        "destination",
        "destination_stage",
        "secondary_snapshot_stage",
        "secondary_snapshot_target",
        "state",
        "authorized_bundle_recovery_starts",
        "authorized_pipeline_starts",
        "automatic_retry_count",
    }
)
RECOVERY_TERMINAL_FIELDS = frozenset(
    {
        "schema_version",
        "recovery_id",
        "authorization",
        "owner_confirmation_binding",
        "completed_at_utc",
        "completed_at_shanghai",
        "outcome",
        "reached_stage",
        "sealed_ledger_before_sha256",
        "sealed_ledger_after_sha256",
        "sealed_mirror_before_sha256",
        "sealed_mirror_after_sha256",
        "destination",
        "published_bundle_sha256",
        "published_tree_sha256",
        "secondary_snapshot",
        "secondary_snapshot_tree_sha256",
        "primary_receipt",
        "secondary_receipt",
        "paired_receipts_byte_identical",
        "destination_stage_absent",
        "secondary_snapshot_stage_absent",
        "pipeline_starts",
        "automatic_retry_count",
        "error",
    }
)
RECOVERY_MIRROR_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "recovery_authorization_sha256",
        "owner_confirmation_binding_sha256",
        "recovery_id",
        "series_id",
        "series_token_sha256",
        "sealed_history_root_sha256",
        "sealed_live_ledger_root_sha256",
        "selected_attempt_ordinal",
        "selected_implementation_epoch",
        "selected_implementation_commit",
        "execution_epoch",
        "execution_implementation_commit",
        "execution_head",
        "destination",
        "published_bundle_sha256",
        "published_tree_sha256",
        "secondary_snapshot",
        "secondary_snapshot_tree_sha256",
        "destination_and_snapshot_byte_identical",
        "pipeline_starts",
        "automatic_retry_count",
        "sealed_ledger_before_after_equal",
        "sealed_mirror_before_after_equal",
        "verified_at_utc",
    }
)
REAL_LINEAGE_CENSUS_FIELDS = frozenset(
    {
        "schema_version",
        "execution_head",
        "authority_registry_sha256",
        "ref_snapshot_before_sha256",
        "ref_snapshot_after_sha256",
        "reference_count",
        "row_count",
        "source_count",
        "projection_count",
        "invalid_count",
        "rows",
        "effects",
        "status",
    }
)
REAL_LINEAGE_ROW_FIELDS = frozenset(
    {
        "path",
        "pinned_sha256",
        "pinned_creating_commit",
        "mode",
        "logical_source_commit",
        "declared_landing_projection_commit",
        "raw_touch_count",
        "source_count",
        "projection_count",
        "touches",
        "execution_head_contains_source",
        "head_blob_sha256",
        "worktree_sha256",
        "verdict",
    }
)
REAL_LINEAGE_TOUCH_FIELDS = frozenset(
    {
        "commit",
        "parents",
        "first_parent_status",
        "classification",
        "blob_sha256",
        "raw_bytes_equal_pinned",
        "source_is_ancestor_of_second_parent",
        "second_parent_to_merge_path_diff_empty",
    }
)
LEGACY_AMENDMENT_ABSENCE_FIELDS = (
    "official_series_2_bundle_emits_void_epoch_1",
    "void_epoch_3_added",
    "two_four_exception_added",
    "sealed_bundle_recovery_added",
    "recover_sealed_bundle_cli_added",
    "consume_recovered_release_cli_added",
)
RECOVERED_PUBLICATION_CAPABILITY_FIELDS = (
    "recovery_authorization_path",
    "recovery_authorization_sha256",
    "recovery_authorization_creating_commit",
    "owner_binding_path",
    "owner_binding_sha256",
    "owner_binding_creating_commit",
    "claim_root",
    "claim_started_sha256",
    "claim_terminal_sha256",
    "series_token_sha256",
    "selected_attempt_ordinal",
    "selected_implementation_epoch",
    "selected_implementation_commit",
    "sealed_history_root_sha256",
    "sealed_live_ledger_root_sha256",
    "destination",
    "published_bundle_sha256",
    "published_tree_sha256",
    "secondary_snapshot",
    "secondary_snapshot_tree_sha256",
    "primary_receipt_path",
    "secondary_receipt_path",
    "paired_receipt_sha256",
    "paired_receipt_bytes",
    "execution_epoch",
    "execution_implementation_commit",
    "execution_control_merkle_root_sha256",
    "recovery_starts",
    "pipeline_starts",
    "automatic_retry_count",
    "sealed_ledger_before_after_equal",
    "sealed_mirror_before_after_equal",
    "selected_candidate_sha256",
    "selected_terminal_sha256",
    "selected_evidence_tree_root_sha256",
    "historical_run_a_root_sha256",
    "historical_run_b_root_sha256",
    "historical_run_a_probe_sha256",
    "historical_run_b_probe_sha256",
    "historical_full_downstream_replay_verified",
)
EPOCH_7_RECOVERY_REVIEW_REQUEST_FIELD_ORDER = (
    "schema_version",
    "request_id",
    "created_at_utc",
    "created_at_shanghai",
    "status",
    "requester",
    "landed_epoch_7",
    "registered_read_only_recovery_preflight",
    "preflight_before_after_equality",
    "proposed_recovery_authorization",
    "requested_owner_action_time_confirmation",
    "post_confirmation_plan_not_yet_executed",
    "current_locks",
)
RECOVERY_REVIEW_REQUEST_FIELD_ORDER = (
    "schema_version",
    "request_id",
    "created_at_utc",
    "created_at_shanghai",
    "status",
    "requester",
    "landed_execution_epoch",
    "registered_read_only_recovery_preflight",
    "preflight_before_after_equality",
    "proposed_recovery_authorization",
    "requested_owner_action_time_confirmation",
    "post_confirmation_plan_not_yet_executed",
    "current_locks",
)
EPOCH_8_READ_ONLY_PREFLIGHT_FIELD_ORDER = (
    "schema_version",
    "status",
    "mode",
    "execution_head",
    "implementation_epoch",
    "implementation_commit",
    "owner_exact_surface_authorization",
    "independent_implementation_review",
    "control_merkle_root_sha256",
    "control_record_count",
    "registered_surface",
    "series_2_registered_storage",
    "real_lineage_census",
    "registered_recovery_storage",
    "sealed_recovery_inputs",
    "effect_summary",
)
SERIES_2_READ_ONLY_PREFLIGHT_FIELD_ORDER = EPOCH_8_READ_ONLY_PREFLIGHT_FIELD_ORDER
RECOVERY_AUTHORIZATION_FIELD_ORDER = (
    "schema_version",
    "authorization_id",
    "created_at_utc",
    "created_at_shanghai",
    "verdict",
    "owner",
    "sealed_series",
    "execution_epoch",
    "destination",
    "exact_argv",
    "command_sha256",
    "exact_environment",
    "environment_sha256",
    "authorized_bundle_recovery_starts",
    "authorized_pipeline_starts",
    "automatic_retry_count",
    "effect_authorization",
    "interpreter",
    "locks",
)
RECOVERY_OWNER_BINDING_FIELD_ORDER = (
    "schema_version",
    "binding_id",
    "created_at_utc",
    "created_at_shanghai",
    "status",
    "review_request",
    "recovery_authorization",
    "owner_confirmation",
    "authorized_scope",
    "explicit_exclusions",
    "registered_read_only_recovery_preflight",
    "machine_boundary",
)
RECOVERY_STARTED_FIELD_ORDER = (
    "schema_version",
    "recovery_id",
    "authorization",
    "owner_confirmation_binding",
    "created_at_utc",
    "created_at_shanghai",
    "execution_head",
    "execution_epoch",
    "sealed_history_root_sha256",
    "sealed_live_ledger_root_sha256",
    "sealed_mirror_receipt_sha256",
    "destination",
    "destination_stage",
    "secondary_snapshot_stage",
    "secondary_snapshot_target",
    "state",
    "authorized_bundle_recovery_starts",
    "authorized_pipeline_starts",
    "automatic_retry_count",
)
RECOVERY_TERMINAL_FIELD_ORDER = (
    "schema_version",
    "recovery_id",
    "authorization",
    "owner_confirmation_binding",
    "completed_at_utc",
    "completed_at_shanghai",
    "outcome",
    "reached_stage",
    "sealed_ledger_before_sha256",
    "sealed_ledger_after_sha256",
    "sealed_mirror_before_sha256",
    "sealed_mirror_after_sha256",
    "destination",
    "published_bundle_sha256",
    "published_tree_sha256",
    "secondary_snapshot",
    "secondary_snapshot_tree_sha256",
    "primary_receipt",
    "secondary_receipt",
    "paired_receipts_byte_identical",
    "destination_stage_absent",
    "secondary_snapshot_stage_absent",
    "pipeline_starts",
    "automatic_retry_count",
    "error",
)
RECOVERY_MIRROR_RECEIPT_FIELD_ORDER = (
    "schema_version",
    "recovery_authorization_sha256",
    "owner_confirmation_binding_sha256",
    "recovery_id",
    "series_id",
    "series_token_sha256",
    "sealed_history_root_sha256",
    "sealed_live_ledger_root_sha256",
    "selected_attempt_ordinal",
    "selected_implementation_epoch",
    "selected_implementation_commit",
    "execution_epoch",
    "execution_implementation_commit",
    "execution_head",
    "destination",
    "published_bundle_sha256",
    "published_tree_sha256",
    "secondary_snapshot",
    "secondary_snapshot_tree_sha256",
    "destination_and_snapshot_byte_identical",
    "pipeline_starts",
    "automatic_retry_count",
    "sealed_ledger_before_after_equal",
    "sealed_mirror_before_after_equal",
    "verified_at_utc",
)
REAL_LINEAGE_CENSUS_FIELD_ORDER = (
    "schema_version",
    "execution_head",
    "authority_registry_sha256",
    "ref_snapshot_before_sha256",
    "ref_snapshot_after_sha256",
    "reference_count",
    "row_count",
    "source_count",
    "projection_count",
    "invalid_count",
    "rows",
    "effects",
    "status",
)
REAL_LINEAGE_ROW_FIELD_ORDER = (
    "path",
    "pinned_sha256",
    "pinned_creating_commit",
    "mode",
    "logical_source_commit",
    "declared_landing_projection_commit",
    "raw_touch_count",
    "source_count",
    "projection_count",
    "touches",
    "execution_head_contains_source",
    "head_blob_sha256",
    "worktree_sha256",
    "verdict",
)
REAL_LINEAGE_TOUCH_FIELD_ORDER = (
    "commit",
    "parents",
    "first_parent_status",
    "classification",
    "blob_sha256",
    "raw_bytes_equal_pinned",
    "source_is_ancestor_of_second_parent",
    "second_parent_to_merge_path_diff_empty",
)
RECOVERY_WORK_COUNTER_FIELDS = (
    "git_objects_read",
    "recursive_bytes_hashed",
    "sealed_snapshot_files_visited",
    "bundle_bytes_copied",
)
RECOVERY_WORK_LIMITS: Mapping[str, int] = {
    "git_objects_read": 20_000,
    "recursive_bytes_hashed": 768_000_000,
    "sealed_snapshot_files_visited": 2_000,
    "bundle_bytes_copied": 256_000_000,
}
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


class _RecoveryWorkBoundExceeded(RehearsalV22ValidationError):
    """A registered recovery work ceiling was reached before more work began."""


class BundleValidationMode(StrEnum):
    """Closed validation modes; recovered modes never fall back to active replay."""

    ACTIVE_ATTEMPT_BUNDLE = "ACTIVE_ATTEMPT_BUNDLE"
    PASSIVE_RECOVERED_BUNDLE = "PASSIVE_RECOVERED_BUNDLE"
    PASSIVE_RECOVERED_RELEASE = "PASSIVE_RECOVERED_RELEASE"


class AuthorityCensusRole(StrEnum):
    PINNED_SOURCE = "PINNED_SOURCE"
    PINNED_LANDING_PROJECTION = "PINNED_LANDING_PROJECTION"
    PINNED_SOURCE_WITH_DESCENDANT_GRAPH = "PINNED_SOURCE_WITH_DESCENDANT_GRAPH"
    DISCOVER_SOURCE_AFTER_PROJECTIONS = "DISCOVER_SOURCE_AFTER_PROJECTIONS"


@dataclass(frozen=True, slots=True)
class HistoricalSelectedAnchor:
    implementation_epoch: int
    implementation_commit: str
    control_merkle_root_sha256: str
    history_root_sha256: str
    live_ledger_root_sha256: str
    selected_attempt_ordinal: int
    require_current: Literal[False]


@dataclass(frozen=True, slots=True)
class LiveExecutionAnchor:
    implementation_epoch: int
    implementation_commit: str
    control_merkle_root_sha256: str
    control_record_count: int
    execution_head: str
    owner_surface_authorization: Mapping[str, Any]
    independent_implementation_review: Mapping[str, Any]
    landing_commit: str
    landing_report: Mapping[str, Any]
    real_lineage_census_sha256: str
    require_current: Literal[True]


@dataclass(frozen=True, slots=True)
class _FrozenControlRecord:
    logical_name: str
    bundle_relative_path: str
    source_kind: str
    repository_path: str | None
    byte_count: int
    sha256: str
    current_byte_required: bool


@dataclass(frozen=True, slots=True)
class _FrozenPayloadFact:
    bundle_relative_path: str
    byte_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _ControlSurfaceCacheEnvelope:
    """Independent deep-immutable cache scoped to one validation pass."""

    _nonce: object
    pass_kind: Literal["LIVE_CURRENT", "HISTORICAL_SELECTED_EPOCH_6"]
    selected_epoch: int | None
    resolved_project_root: str
    root_st_dev: int
    root_st_ino: int
    execution_head: str
    ref_snapshot_sha256: str | None
    lineage_census_sha256: str | None
    implementation_commit: str
    records: tuple[_FrozenControlRecord, ...]
    payload_facts: tuple[_FrozenPayloadFact, ...]
    manifest_payload: bytes
    manifest_sha256: str
    merkle_root_sha256: str
    ast_closure_paths: tuple[str, ...]
    loaded_repository_sources: tuple[str, ...]
    python_inventory_bytes: int
    python_inventory_sha256: str
    package_inventory_bytes: int
    package_inventory_sha256: str
    integrity_sha256: str


@dataclass(frozen=True, slots=True)
class ActiveBundleValidationContext:
    mode: Literal[BundleValidationMode.ACTIVE_ATTEMPT_BUNDLE]


@dataclass(frozen=True, slots=True)
class RecoveredBundleValidationContext:
    mode: Literal[
        BundleValidationMode.PASSIVE_RECOVERED_BUNDLE,
        BundleValidationMode.PASSIVE_RECOVERED_RELEASE,
    ]
    historical_anchor: HistoricalSelectedAnchor
    live_anchor: LiveExecutionAnchor


BundleValidationContext = ActiveBundleValidationContext | RecoveredBundleValidationContext


def _validation_context_requires_current(
    context: BundleValidationContext,
    *,
    implementation_commit: str,
) -> bool:
    """Close current-byte selection over the three registered modes.

    Recovered callers cannot choose a boolean. They must supply both typed
    anchors; the historical implementation determines selected bundle bytes,
    while the live anchor is validated as an independent current-byte wall.
    """

    if isinstance(context, ActiveBundleValidationContext):
        if context.mode is not BundleValidationMode.ACTIVE_ATTEMPT_BUNDLE:
            raise RehearsalV22ValidationError("active validation context mode drifted")
        return True
    if isinstance(context, RecoveredBundleValidationContext):
        if context.mode not in {
            BundleValidationMode.PASSIVE_RECOVERED_BUNDLE,
            BundleValidationMode.PASSIVE_RECOVERED_RELEASE,
        }:
            raise RehearsalV22ValidationError("recovered validation context mode drifted")
        if (
            context.historical_anchor.require_current is not False
            or context.live_anchor.require_current is not True
            or implementation_commit != context.historical_anchor.implementation_commit
        ):
            raise RehearsalV22ValidationError("recovered validation context anchor drifted")
        return False
    raise RehearsalV22ValidationError("bundle validation context type is unregistered")


@dataclass(frozen=True, slots=True)
class AuthorityCensusSpec:
    path: str
    pinned_sha256: str
    pinned_creating_commit: str
    role: AuthorityCensusRole
    declared_landing_projection_commit: str | None = None


@dataclass(frozen=True, slots=True)
class RecoveredPublicationCapability:
    recovery_authorization_path: str
    recovery_authorization_sha256: str
    recovery_authorization_creating_commit: str
    owner_binding_path: str
    owner_binding_sha256: str
    owner_binding_creating_commit: str
    claim_root: str
    claim_started_sha256: str
    claim_terminal_sha256: str
    series_token_sha256: str
    selected_attempt_ordinal: int
    selected_implementation_epoch: int
    selected_implementation_commit: str
    sealed_history_root_sha256: str
    sealed_live_ledger_root_sha256: str
    destination: str
    published_bundle_sha256: str
    published_tree_sha256: str
    secondary_snapshot: str
    secondary_snapshot_tree_sha256: str
    primary_receipt_path: str
    secondary_receipt_path: str
    paired_receipt_sha256: str
    paired_receipt_bytes: int
    execution_epoch: int
    execution_implementation_commit: str
    execution_control_merkle_root_sha256: str
    recovery_starts: int
    pipeline_starts: int
    automatic_retry_count: int
    sealed_ledger_before_after_equal: bool
    sealed_mirror_before_after_equal: bool
    selected_candidate_sha256: str
    selected_terminal_sha256: str
    selected_evidence_tree_root_sha256: str
    historical_run_a_root_sha256: str
    historical_run_b_root_sha256: str
    historical_run_a_probe_sha256: str
    historical_run_b_probe_sha256: str
    historical_full_downstream_replay_verified: bool


@dataclass(frozen=True, slots=True)
class _ValidatedRecoveryGovernance:
    contract: JsonObject
    latest_landed_contract: JsonObject
    preflight_document: JsonObject
    preflight_census: JsonObject
    landed_execution_epoch: JsonObject
    q_path: Path
    q_payload: bytes
    q_document: JsonObject
    q_commit: str
    r_path: Path
    r_payload: bytes
    r_document: JsonObject
    r_commit: str
    b_path: Path
    b_payload: bytes
    b_document: JsonObject
    b_commit: str
    authority_specs: tuple[AuthorityCensusSpec, ...]


def _build_recovered_publication_capability_issuer() -> tuple[Any, Any]:
    issued: dict[int, RecoveredPublicationCapability] = {}

    def issue(**values: object) -> RecoveredPublicationCapability:
        if tuple(values) != RECOVERED_PUBLICATION_CAPABILITY_FIELDS:
            raise RehearsalV22ValidationError(
                "recovered-publication capability field order or set drifted"
            )
        capability = RecoveredPublicationCapability(**values)  # type: ignore[arg-type]
        issued[id(capability)] = capability
        return capability

    def require(value: object) -> RecoveredPublicationCapability:
        if (
            not isinstance(value, RecoveredPublicationCapability)
            or issued.get(id(value)) is not value
        ):
            raise RehearsalV22ValidationError(
                "recovered-publication capability was not independently issued"
            )
        return value

    return issue, require


(
    _issue_recovered_publication_capability,
    _require_recovered_publication_capability,
) = _build_recovered_publication_capability_issuer()


def _reject_constant(value: str, *, label: str) -> NoReturn:
    raise RehearsalV22ValidationError(f"{label} contains forbidden numeric constant {value!r}")


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


def _assert_recovery_work_bound(counters: Mapping[str, int]) -> None:
    if tuple(counters) != RECOVERY_WORK_COUNTER_FIELDS:
        raise RehearsalV22ValidationError("recovery work counters are not exact and ordered")
    for field in RECOVERY_WORK_COUNTER_FIELDS:
        observed = counters[field]
        limit = RECOVERY_WORK_LIMITS[field]
        if (
            isinstance(observed, bool)
            or not isinstance(observed, int)
            or not 0 <= observed <= limit
        ):
            raise RehearsalV22ValidationError(f"recovery work bound exceeded: {field}")


class _IndependentRecoveryWorkTracker:
    """Validator-owned, incremental accounting of actual census Git work."""

    def __init__(self, initial: Mapping[str, int] | None = None) -> None:
        source = (
            {field: 0 for field in RECOVERY_WORK_COUNTER_FIELDS}
            if initial is None
            else dict(initial)
        )
        _assert_recovery_work_bound(source)
        self._counters = source
        self.git_subprocesses_started = 0
        self.git_object_read_occurrences = 0

    def snapshot(self) -> dict[str, int]:
        result = {
            field: self._counters[field] for field in RECOVERY_WORK_COUNTER_FIELDS
        }
        _assert_recovery_work_bound(result)
        return result

    def charge_git(self, *, subprocesses: int = 0, object_reads: int = 0) -> None:
        if (
            isinstance(subprocesses, bool)
            or not isinstance(subprocesses, int)
            or subprocesses < 0
            or isinstance(object_reads, bool)
            or not isinstance(object_reads, int)
            or object_reads < 0
        ):
            raise RehearsalV22ValidationError("validator Git work charge is invalid")
        prospective = self.snapshot()
        prospective["git_objects_read"] += subprocesses + object_reads
        try:
            _assert_recovery_work_bound(prospective)
        except RehearsalV22ValidationError as exc:
            raise _RecoveryWorkBoundExceeded(str(exc)) from exc
        self._counters = prospective
        self.git_subprocesses_started += subprocesses
        self.git_object_read_occurrences += object_reads


def _recovery_work_delta(
    before: Mapping[str, int],
    after: Mapping[str, int],
) -> dict[str, int]:
    _assert_recovery_work_bound(before)
    _assert_recovery_work_bound(after)
    result = {field: after[field] - before[field] for field in RECOVERY_WORK_COUNTER_FIELDS}
    if any(value < 0 for value in result.values()):
        raise RehearsalV22ValidationError("recovery work counters moved backwards")
    _assert_recovery_work_bound(result)
    return result


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
    if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode) or path.absolute() != resolved:
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
        return (
            isinstance(right, list)
            and len(left) == len(right)
            and all(
                _typed_json_equal(left_item, right_item)
                for left_item, right_item in zip(left, right, strict=True)
            )
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


def _validate_contract_inheritance(
    *,
    project_root: Path,
    preregistration_payload: bytes,
    bundle_schema_payload: bytes,
    release_schema_payload: bytes,
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
    base_path, base_digest, base_commit = _CARRY_FORWARD_AUTHORITIES["v2_1_preregistration"]
    _require_equal(
        inheritance.get("base_preregistration"),
        {"path": base_path, "sha256": base_digest, "creating_commit": base_commit},
        "inheritance base preregistration",
    )
    base_payload = _bound_control(
        project_root,
        Path(base_path),
        base_digest,
        "v2.1 base preregistration",
    )
    base = _object(
        strict_json_loads(base_payload, label="v2.1 base preregistration"),
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
        value = copy.deepcopy(_pointer_get(base, pointer, f"inheritance source {pointer}"))
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
        inheritance.get("strict_inheritance_snapshot_sha256") != _INHERITANCE_SNAPSHOT_SHA256
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
    base_bundle_payload = _bound_control(
        project_root,
        _V2_1_BUNDLE_SCHEMA_RELATIVE,
        _V2_1_BUNDLE_SCHEMA_SHA256,
        "v2.1 bundle schema",
    )
    base_release_payload = _bound_control(
        project_root,
        _V2_1_RELEASE_SCHEMA_RELATIVE,
        _V2_1_RELEASE_SCHEMA_SHA256,
        "v2.1 release schema",
    )
    _schemas_match_after_registered_delta_strip(
        base=strict_json_loads(base_bundle_payload, label="v2.1 bundle schema"),
        successor=strict_json_loads(bundle_schema_payload, label="v2.2 bundle schema"),
        pointers=_BUNDLE_SCHEMA_DELTA_POINTERS,
        label="bundle schema inheritance",
    )
    _schemas_match_after_registered_delta_strip(
        base=strict_json_loads(base_release_payload, label="v2.1 release schema"),
        successor=strict_json_loads(release_schema_payload, label="v2.2 release schema"),
        pointers=_RELEASE_SCHEMA_DELTA_POINTERS,
        label="release schema inheritance",
    )


def _validate_series_2_schema_profiles(
    *,
    project_root: Path,
    historical_bundle_payload: bytes,
    historical_release_payload: bytes,
    active_bundle_payload: bytes,
    active_release_payload: bytes,
) -> None:
    """Prove the active profiles differ only at the registered 7/6 bindings."""

    if (
        _sha256(historical_bundle_payload) != BUNDLE_SCHEMA_SHA256
        or _sha256(historical_release_payload) != RELEASE_SCHEMA_SHA256
        or _sha256(active_bundle_payload) != SERIES_2_BUNDLE_SCHEMA_SHA256
        or _sha256(active_release_payload) != SERIES_2_RELEASE_SCHEMA_SHA256
    ):
        raise RehearsalV22ValidationError("series-2 schema profile bytes drifted")
    _schemas_match_after_registered_delta_strip(
        base=strict_json_loads(
            historical_bundle_payload,
            label="historical v2.2 bundle schema",
        ),
        successor=strict_json_loads(
            active_bundle_payload,
            label="series-2 bundle schema",
        ),
        pointers=SERIES_2_BUNDLE_SCHEMA_DELTA_POINTERS,
        label="series-2 bundle schema profile",
    )
    _schemas_match_after_registered_delta_strip(
        base=strict_json_loads(
            historical_release_payload,
            label="historical v2.2 release schema",
        ),
        successor=strict_json_loads(
            active_release_payload,
            label="series-2 release schema",
        ),
        pointers=SERIES_2_RELEASE_SCHEMA_DELTA_POINTERS,
        label="series-2 release schema profile",
    )
    del project_root


def _validate_series_2_lost_history_summary(value: object) -> JsonObject:
    summary = _object(value, "series-2 complete lost-series digest history")
    if not _typed_json_equal(summary, SERIES_2_LOST_HISTORY_SUMMARY):
        raise RehearsalV22ValidationError("series-2 complete lost-series digest history drifted")
    return copy.deepcopy(summary)


def _validate_series_2_preregistration(
    *,
    project_root: Path,
    execution_head: str,
) -> str:
    """Independently bind the landed amendment, loss lineage, and schema profiles."""

    root = project_root.resolve(strict=True)
    head = _git_commit(root, execution_head, "series-2 execution HEAD")
    amendment_reference = {
        "path": SERIES_2_PREREGISTRATION_RELATIVE.as_posix(),
        "sha256": SERIES_2_PREREGISTRATION_SHA256,
        "creating_commit": SERIES_2_PREREGISTRATION_COMMIT,
        "unique_a_history_verified": True,
    }
    amendment_payload = _unique_a_authority(
        root,
        amendment_reference,
        require_worktree=True,
    )
    if (
        _git_parents(root, SERIES_2_PREREGISTRATION_COMMIT) != (SERIES_2_PREREGISTRATION_PARENT,)
        or set(
            _diff_name_status(
                root,
                SERIES_2_PREREGISTRATION_PARENT,
                SERIES_2_PREREGISTRATION_COMMIT,
            )
        )
        != {
            ("A", SERIES_2_PREREGISTRATION_RELATIVE.as_posix()),
            ("A", SERIES_2_BUNDLE_SCHEMA_RELATIVE.as_posix()),
            ("A", SERIES_2_RELEASE_SCHEMA_RELATIVE.as_posix()),
        }
        or not _git_is_ancestor(root, SERIES_2_PREREGISTRATION_COMMIT, head)
    ):
        raise RehearsalV22ValidationError("series-2 preregistration exact-three-A topology drifted")
    amendment = _object(
        strict_json_loads(amendment_payload, label="series-2 preregistration amendment"),
        "series-2 preregistration amendment",
    )
    authority_bindings = _object(
        amendment.get("part_1_authority_loss_and_owner_decision_bindings"),
        "series-2 amendment authority bindings",
    )
    loss = _object(
        authority_bindings.get("loss_incident"),
        "series-2 loss incident binding",
    )
    owner = _object(
        authority_bindings.get("owner_decision"),
        "series-2 owner decision binding",
    )
    _require_equal(
        loss,
        {
            "commit": SERIES_2_LOSS_INCIDENT_COMMIT,
            "path": SERIES_2_LOSS_INCIDENT_RELATIVE.as_posix(),
            "sha256": SERIES_2_TOKEN_SEED_SHA256,
            "bytes": 6422,
            "verdict": "SEALED_LEDGER_BYTES_LOST_SERIES_SUCCESS_STANDS_BY_DIGEST",
        },
        "series-2 loss incident binding",
    )
    _require_equal(
        owner,
        {
            "commit": SERIES_2_OWNER_DECISION_COMMIT,
            "path": SERIES_2_OWNER_DECISION_RELATIVE.as_posix(),
            "sha256": SERIES_2_OWNER_DECISION_SHA256,
            "bytes": 2347,
            "decision": "run it again",
        },
        "series-2 owner decision binding",
    )
    _validate_series_2_lost_history_summary(
        amendment.get("part_2_complete_lost_series_digest_history")
    )
    for reference, expected_bytes in (
        (
            {
                "path": SERIES_2_LOSS_INCIDENT_RELATIVE.as_posix(),
                "sha256": SERIES_2_TOKEN_SEED_SHA256,
                "creating_commit": SERIES_2_LOSS_INCIDENT_COMMIT,
            },
            6422,
        ),
        (
            {
                "path": SERIES_2_OWNER_DECISION_RELATIVE.as_posix(),
                "sha256": SERIES_2_OWNER_DECISION_SHA256,
                "creating_commit": SERIES_2_OWNER_DECISION_COMMIT,
            },
            2347,
        ),
    ):
        payload = _unique_a_authority(root, reference, require_worktree=True)
        if len(payload) != expected_bytes:
            raise RehearsalV22ValidationError("series-2 lineage authority byte count drifted")
    identity = _object(
        amendment.get("part_4_fresh_series_identity_and_visible_primary_mirror_paths"),
        "series-2 visible identity",
    )
    expected_identity = {
        "series_token_sha256": SERIES_2_REGISTERED_SERIES_TOKEN,
        "loss_incident_file_sha256": SERIES_2_TOKEN_SEED_SHA256,
        "primary_series_container": SERIES_2_PRIMARY_SERIES_CONTAINER.as_posix(),
        "primary_ledger_root": SERIES_2_PRIMARY_LEDGER_ROOT.as_posix(),
        "primary_receipt_root": SERIES_2_PRIMARY_RECEIPT_ROOT.as_posix(),
        "secondary_series_container": SERIES_2_SECONDARY_SERIES_CONTAINER.as_posix(),
        "secondary_snapshot_root": SERIES_2_SECONDARY_SNAPSHOT_ROOT.as_posix(),
        "secondary_receipt_root": SERIES_2_SECONDARY_RECEIPT_ROOT.as_posix(),
    }
    for key, expected in expected_identity.items():
        _require_equal(identity.get(key), expected, f"series-2 identity {key}")
    isolation = _object(identity.get("constant_isolation"), "series-2 constant isolation")
    _require_equal(
        isolation.get("new_token_seed_constant"),
        "SERIES_2_TOKEN_SEED_SHA256",
        "series-2 token seed constant",
    )
    _require_equal(
        isolation.get("new_token_seed_value"),
        SERIES_2_TOKEN_SEED_SHA256,
        "series-2 token seed value",
    )
    _require_equal(
        isolation.get("legacy_incident_sha256_constant_name"),
        "INCIDENT_SHA256",
        "historical incident constant name",
    )
    _require_equal(
        isolation.get("legacy_incident_sha256_value_unchanged"),
        INCIDENT_SHA256,
        "historical incident constant value",
    )
    reset = _object(
        amendment.get("part_3_non_reconstruction_and_independent_empty_history_reset"),
        "series-2 history reset",
    )
    if (
        reset.get("history_empty_root_sha256") != _history_empty_root()
        or reset.get("ordinal_1_previous_history_root_sha256") != _history_empty_root()
        or reset.get("old_history_root_must_not_seed_series_2") is not True
        or reset.get("old_ledger_must_remain_absent") is not True
    ):
        raise RehearsalV22ValidationError("series-2 independent history reset drifted")
    epoch_contract = _object(
        amendment.get("part_5_epoch_origin_5_and_explicit_epoch_key_rules"),
        "series-2 epoch origin contract",
    )
    _require_equal(
        epoch_contract.get("series_2_epoch_origin"),
        SERIES_2_EPOCH_ORIGIN,
        "series-2 epoch origin",
    )
    legacy = _object(
        epoch_contract.get("legacy_and_absence_rules"),
        "series-2 legacy absence rules",
    )
    for key in (
        "official_series_2_bundle_emits_void_epoch_1",
        "void_epoch_3_added",
        "two_four_exception_added",
        "sealed_bundle_recovery_added",
        "recover_sealed_bundle_cli_added",
        "consume_recovered_release_cli_added",
    ):
        _require_equal(legacy.get(key), False, f"series-2 absence rule {key}")
    historical_bundle = _bound_control(
        root,
        BUNDLE_SCHEMA_RELATIVE,
        BUNDLE_SCHEMA_SHA256,
        "historical v2.2 bundle schema",
    )
    historical_release = _bound_control(
        root,
        RELEASE_SCHEMA_RELATIVE,
        RELEASE_SCHEMA_SHA256,
        "historical v2.2 release schema",
    )
    active_bundle = _bound_control(
        root,
        SERIES_2_BUNDLE_SCHEMA_RELATIVE,
        SERIES_2_BUNDLE_SCHEMA_SHA256,
        "series-2 bundle schema",
    )
    active_release = _bound_control(
        root,
        SERIES_2_RELEASE_SCHEMA_RELATIVE,
        SERIES_2_RELEASE_SCHEMA_SHA256,
        "series-2 release schema",
    )
    _validate_series_2_schema_profiles(
        project_root=root,
        historical_bundle_payload=historical_bundle,
        historical_release_payload=historical_release,
        active_bundle_payload=active_bundle,
        active_release_payload=active_release,
    )
    return SERIES_2_PREREGISTRATION_COMMIT


def _validated_implementation_blob(
    *,
    project_root: Path,
    implementation_commit: str,
    relative_path: str,
    expected_sha256: str,
    require_current: bool,
) -> bytes:
    payload = _git_blob(project_root, implementation_commit, relative_path)
    current = (
        _regular_bytes(
            _safe_path(
                project_root,
                relative_path,
                f"current implementation {relative_path}",
            ),
            f"current implementation {relative_path}",
        )
        if require_current
        else None
    )
    if (
        _sha256(payload) != expected_sha256
        or (require_current and current != payload)
        or implementation.validate_implementation_blob(
            project_root,
            implementation_commit,
            relative_path,
            require_current=require_current,
        )
        != payload
    ):
        raise RehearsalV22ValidationError(f"implementation commit blob drifted: {relative_path}")
    return payload


def _raw_hardened_git(
    root: Path,
    *arguments: str,
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
    object_reads: int = 0,
) -> bytes:
    if work_tracker is not None:
        work_tracker.charge_git(subprocesses=1, object_reads=object_reads)
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


def _git_metadata_roots(
    root: Path,
    *,
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
) -> tuple[Path, Path]:
    observed_git_dir = (
        _raw_hardened_git(
            root,
            "rev-parse",
            "--path-format=absolute",
            "--git-dir",
            work_tracker=work_tracker,
        )
        .decode("utf-8", errors="strict")
        .strip()
    )
    observed_common_dir = (
        _raw_hardened_git(
            root,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
            work_tracker=work_tracker,
        )
        .decode("utf-8", errors="strict")
        .strip()
    )
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


def _validate_git_metadata_authority(
    root: Path,
    *,
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
) -> None:
    git_dir, common_dir = _git_metadata_roots(root, work_tracker=work_tracker)
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


def _git_bytes(
    root: Path,
    *arguments: str,
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
    object_reads: int = 0,
) -> bytes:
    """Run one independently selected, hardened, read-only Git operation."""

    _validate_git_metadata_authority(root, work_tracker=work_tracker)
    if (
        tuple(implementation.GIT_CONFIG_PREFIX) != _GIT_CONFIG_PREFIX
        or implementation._git_environment() != _GIT_ENVIRONMENT
    ):
        raise RehearsalV22ValidationError("producer and validator Git policy drifted")
    return _raw_hardened_git(
        root,
        *arguments,
        work_tracker=work_tracker,
        object_reads=object_reads,
    )


def _git_commit(
    root: Path,
    value: object,
    label: str,
    *,
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
) -> str:
    commit = _commit(value, label)
    observed = (
        _git_bytes(
            root,
            "rev-parse",
            "--verify",
            f"{commit}^{{commit}}",
            work_tracker=work_tracker,
            object_reads=1,
        )
        .decode("ascii", errors="strict")
        .strip()
    )
    if observed != commit:
        raise RehearsalV22ValidationError(f"{label} object identity drifted")
    return commit


def _git_parents(
    root: Path,
    commit: str,
    *,
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
) -> tuple[str, ...]:
    line = (
        _git_bytes(
            root,
            "rev-list",
            "--parents",
            "-n",
            "1",
            commit,
            work_tracker=work_tracker,
            object_reads=1,
        )
        .decode("ascii", errors="strict")
        .strip()
    )
    fields = tuple(line.split())
    if not fields or fields[0] != commit:
        raise RehearsalV22ValidationError("Git parent record drifted")
    return tuple(_commit(value, "Git parent") for value in fields[1:])


def _git_is_ancestor(
    root: Path,
    ancestor: str,
    descendant: str,
    *,
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
) -> bool:
    # merge-base --is-ancestor uses exit 1 for a clean negative answer.  Use a
    # raw subprocess here so a false relation is not conflated with corruption.
    _validate_git_metadata_authority(root, work_tracker=work_tracker)
    if work_tracker is not None:
        work_tracker.charge_git(subprocesses=1, object_reads=2)
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


def _git_blob(
    root: Path,
    commit: str,
    relative: str,
    *,
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
) -> bytes:
    _relative(relative, "Git blob path")
    target = f"{commit}:{relative}"
    if _git_bytes(
        root,
        "cat-file",
        "-t",
        target,
        work_tracker=work_tracker,
        object_reads=1,
    ).strip() != b"blob":
        raise RehearsalV22ValidationError("Git path does not identify one blob")
    return _git_bytes(
        root,
        "show",
        target,
        work_tracker=work_tracker,
        object_reads=1,
    )


def _git_optional_blob(
    root: Path,
    commit: str,
    relative: str,
    *,
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
) -> bytes | None:
    """Read an optional commit blob without translating other Git failures."""

    path = _relative(relative, "optional Git blob path")
    observed = _git_bytes(
        root,
        "ls-tree",
        "-z",
        "--full-tree",
        commit,
        "--",
        path,
        work_tracker=work_tracker,
        object_reads=1,
    )
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
    if _git_bytes(
        root,
        "rev-parse",
        "--verify",
        target,
        work_tracker=work_tracker,
        object_reads=1,
    ).strip() != fields[2]:
        raise RehearsalV22ValidationError("optional local-import candidate object identity drifted")
    return _git_blob(root, commit, path, work_tracker=work_tracker)


def _local_module_name(relative: str) -> tuple[str, str]:
    path = PurePosixPath(_relative(relative, "local Python source"))
    if path.parts[0] == "scripts":
        components = list(path.with_suffix("").parts)
    elif path.parts[:2] == ("src", "alphapilot"):
        components = list(path.with_suffix("").parts[1:])
    else:
        raise RehearsalV22ValidationError("local Python source is outside registered namespaces")
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
        stem = "src/alphapilot/" + module_name.removeprefix("alphapilot.").replace(".", "/")
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
                            f"nonliteral dynamic import cannot be proven non-local: {relative}"
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
    return dict(sorted(payloads.items(), key=lambda item: item[0].encode("utf-8")))


def _git_all_ref_commits(
    root: Path,
    *,
    work_tracker: _IndependentRecoveryWorkTracker,
) -> tuple[str, ...]:
    commits = tuple(
        line
        for line in _git_bytes(
            root,
            "rev-list",
            "--all",
            work_tracker=work_tracker,
        )
        .decode("ascii", errors="strict")
        .splitlines()
        if line
    )
    if (
        not commits
        or len(commits) != len(set(commits))
        or any(re.fullmatch(r"[0-9a-f]{40}", commit) is None for commit in commits)
    ):
        raise RehearsalV22ValidationError("validator all-ref commit snapshot is malformed")
    work_tracker.charge_git(object_reads=len(commits))
    return commits


def _all_ref_path_touches(
    root: Path,
    path: str,
    *,
    all_ref_commit_count: int | None = None,
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    tracker = _IndependentRecoveryWorkTracker() if work_tracker is None else work_tracker
    if all_ref_commit_count is None:
        all_ref_commit_count = len(_git_all_ref_commits(root, work_tracker=tracker))
    relative = _relative(path, "authority census path")
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
        work_tracker=tracker,
    ).decode("utf-8", errors="strict")
    touches: list[tuple[str, str, tuple[str, ...]]] = []
    active: str | None = None
    history_commit_count = 0
    for raw in history.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("@@"):
            history_commit_count += 1
            if history_commit_count > all_ref_commit_count:
                raise RehearsalV22ValidationError(
                    "authority census history exceeds its all-ref commit snapshot"
                )
            tracker.charge_git(object_reads=1)
            active = _commit(line[2:], "authority census history commit")
            continue
        if active is None:
            raise RehearsalV22ValidationError("authority census Git history is malformed")
        fields = tuple(line.split("\t"))
        if len(fields) < 2:
            raise RehearsalV22ValidationError("authority census Git status is malformed")
        touches.append((active, fields[0], fields[1:]))
    if len(set(touches)) != len(touches):
        raise RehearsalV22ValidationError("authority census contains duplicate touch rows")
    return tuple(touches)


def _path_status_diff(
    root: Path,
    base: str,
    commit: str,
    path: str,
    *,
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
) -> tuple[str, ...]:
    tracker = _IndependentRecoveryWorkTracker() if work_tracker is None else work_tracker
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
            work_tracker=tracker,
            object_reads=2,
        )
        .decode("utf-8", errors="strict")
        .splitlines()
    )


def _projection_touch_document(
    root: Path,
    *,
    path: str,
    pinned_payload: bytes,
    source_commit: str,
    touch: tuple[str, str, tuple[str, ...]],
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
) -> JsonObject | None:
    tracker = _IndependentRecoveryWorkTracker() if work_tracker is None else work_tracker
    commit, status, paths = touch
    if status != "A" or paths != (path,):
        return None
    parents = _git_parents(root, commit, work_tracker=tracker)
    if len(parents) != 2:
        return None
    first_parent, second_parent = parents
    source_in_second = _git_is_ancestor(
        root,
        source_commit,
        second_parent,
        work_tracker=tracker,
    )
    second_diff_empty = (
        _path_status_diff(
            root,
            second_parent,
            commit,
            path,
            work_tracker=tracker,
        )
        == ()
    )
    if not (
        _git_optional_blob(root, first_parent, path, work_tracker=tracker) is None
        and _git_optional_blob(root, second_parent, path, work_tracker=tracker)
        == pinned_payload
        and _git_optional_blob(root, commit, path, work_tracker=tracker) == pinned_payload
        and _path_status_diff(
            root,
            first_parent,
            commit,
            path,
            work_tracker=tracker,
        )
        == (f"A\t{path}",)
        and second_diff_empty
        and source_in_second
    ):
        return None
    return {
        "commit": commit,
        "parents": list(parents),
        "first_parent_status": "A",
        "classification": "FIRST_PARENT_MERGE_PROJECTION",
        "blob_sha256": _sha256(pinned_payload),
        "raw_bytes_equal_pinned": True,
        "source_is_ancestor_of_second_parent": True,
        "second_parent_to_merge_path_diff_empty": True,
    }


def _source_touch_document(
    root: Path,
    *,
    path: str,
    pinned_payload: bytes,
    source_commit: str,
    touch: tuple[str, str, tuple[str, ...]],
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
) -> JsonObject | None:
    tracker = _IndependentRecoveryWorkTracker() if work_tracker is None else work_tracker
    commit, status, paths = touch
    if commit != source_commit or status != "A" or paths != (path,):
        return None
    parents = _git_parents(root, commit, work_tracker=tracker)
    if len(parents) != 1 or _git_optional_blob(
        root,
        parents[0],
        path,
        work_tracker=tracker,
    ) is not None:
        return None
    blob = _git_optional_blob(root, commit, path, work_tracker=tracker)
    if blob != pinned_payload:
        return None
    return {
        "commit": commit,
        "parents": list(parents),
        "first_parent_status": "A",
        "classification": "PINNED_SOURCE",
        "blob_sha256": _sha256(blob),
        "raw_bytes_equal_pinned": True,
        "source_is_ancestor_of_second_parent": False,
        "second_parent_to_merge_path_diff_empty": False,
    }


def _classify_authority_touches(
    root: Path,
    *,
    path: str,
    pinned_payload: bytes,
    source_commit: str,
    touches: Sequence[tuple[str, str, tuple[str, ...]]],
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
) -> tuple[JsonObject, ...]:
    tracker = _IndependentRecoveryWorkTracker() if work_tracker is None else work_tracker
    classified: list[JsonObject] = []
    source_count = 0
    for touch in touches:
        source = _source_touch_document(
            root,
            path=path,
            pinned_payload=pinned_payload,
            source_commit=source_commit,
            touch=touch,
            work_tracker=tracker,
        )
        if source is not None:
            source_count += 1
            classified.append(source)
            continue
        projection = _projection_touch_document(
            root,
            path=path,
            pinned_payload=pinned_payload,
            source_commit=source_commit,
            touch=touch,
            work_tracker=tracker,
        )
        if projection is None:
            raise RehearsalV22ValidationError(
                f"authority has a non-source non-projection Git touch: {path}"
            )
        classified.append(projection)
    if source_count != 1:
        raise RehearsalV22ValidationError(
            f"authority does not have exactly one logical source: {path}"
        )
    classified.sort(key=lambda row: cast(str, row["commit"]).encode("ascii"))
    return tuple(classified)


def _discover_authority_source(
    root: Path,
    *,
    path: str,
    touches: Sequence[tuple[str, str, tuple[str, ...]]],
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
) -> tuple[str, bytes, tuple[JsonObject, ...]]:
    tracker = _IndependentRecoveryWorkTracker() if work_tracker is None else work_tracker
    candidates: list[tuple[str, bytes, tuple[JsonObject, ...]]] = []
    for commit, status, paths in touches:
        if status != "A" or paths != (path,) or len(
            _git_parents(root, commit, work_tracker=tracker)
        ) != 1:
            continue
        payload = _git_optional_blob(root, commit, path, work_tracker=tracker)
        if payload is None:
            continue
        try:
            classified = _classify_authority_touches(
                root,
                path=path,
                pinned_payload=payload,
                source_commit=commit,
                touches=touches,
                work_tracker=tracker,
            )
        except _RecoveryWorkBoundExceeded:
            raise
        except RehearsalV22ValidationError:
            continue
        candidates.append((commit, payload, classified))
    if len(candidates) != 1:
        raise RehearsalV22ValidationError(
            "authority source is ambiguous after removing lawful projections"
        )
    return candidates[0]


def _unique_a_authority(
    root: Path,
    reference: Mapping[str, Any],
    *,
    require_worktree: bool,
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
    all_ref_commits: Sequence[str] | None = None,
) -> bytes:
    tracker = _IndependentRecoveryWorkTracker() if work_tracker is None else work_tracker
    path = _relative(reference.get("path"), "authority path")
    creating_commit = _git_commit(
        root,
        reference.get("creating_commit"),
        "authority commit",
        work_tracker=tracker,
    )
    expected_sha = _sha(reference.get("sha256"), "authority SHA")
    payload = _git_blob(root, creating_commit, path, work_tracker=tracker)
    if _sha256(payload) != expected_sha:
        raise RehearsalV22ValidationError("authority creation blob SHA drifted")
    _classify_authority_touches(
        root,
        path=path,
        pinned_payload=payload,
        source_commit=creating_commit,
        touches=_all_ref_path_touches(
            root,
            path,
            all_ref_commit_count=(
                None if all_ref_commits is None else len(all_ref_commits)
            ),
            work_tracker=tracker,
        ),
        work_tracker=tracker,
    )
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
                raise RehearsalV22ValidationError("implementation review Git history is malformed")
            fields = tuple(line.split("\t"))
            if len(fields) < 2:
                raise RehearsalV22ValidationError("implementation review Git status is malformed")
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
        raise RehearsalV22ValidationError("implementation review projection bytes or SHA drifted")

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
            or _diff_name_status(root, source_parents[0], source_review) != (("A", path),)
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
    classified = _classify_authority_touches(
        root,
        path=path,
        pinned_payload=payload,
        source_commit=source_review,
        touches=_all_ref_path_touches(root, path),
    )
    if len(parents) == 2 and not any(
        row["commit"] == creating_commit
        and row["classification"] == "FIRST_PARENT_MERGE_PROJECTION"
        for row in classified
    ):
        raise RehearsalV22ValidationError(
            "implementation review landing is not a lawful first-parent projection"
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
    all_ref_commits: tuple[str, ...] | None = None,
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
) -> bytes:
    """Validate the fixed b21 sibling without counting its merge projection twice."""

    tracker = _IndependentRecoveryWorkTracker() if work_tracker is None else work_tracker
    commits = (
        _git_all_ref_commits(root, work_tracker=tracker)
        if all_ref_commits is None
        else all_ref_commits
    )
    path = INDEPENDENT_REVIEW_RELATIVE.as_posix()
    expected_reference = {
        "path": path,
        "sha256": INDEPENDENT_REVIEW_SHA256,
        "creating_commit": INDEPENDENT_REVIEW_COMMIT,
        "unique_a_history_verified": True,
    }
    _require_equal(reference, expected_reference, "initial sibling authority reference")
    head = _git_commit(
        root,
        execution_head,
        "initial sibling execution HEAD",
        work_tracker=tracker,
    )
    if _git_parents(
        root,
        INDEPENDENT_REVIEW_COMMIT,
        work_tracker=tracker,
    ) != (INITIAL_REVIEWED_COMMIT,):
        raise RehearsalV22ValidationError("initial sibling authority parent drifted")
    if _diff_name_status(
        root,
        INITIAL_REVIEWED_COMMIT,
        INDEPENDENT_REVIEW_COMMIT,
        work_tracker=tracker,
    ) != (("A", path),):
        raise RehearsalV22ValidationError("initial sibling authority creation diff drifted")
    payload = _git_blob(
        root,
        INDEPENDENT_REVIEW_COMMIT,
        path,
        work_tracker=tracker,
    )
    if _sha256(payload) != INDEPENDENT_REVIEW_SHA256:
        raise RehearsalV22ValidationError("initial sibling authority creation SHA drifted")
    _classify_authority_touches(
        root,
        path=path,
        pinned_payload=payload,
        source_commit=INDEPENDENT_REVIEW_COMMIT,
        touches=_all_ref_path_touches(
            root,
            path,
            all_ref_commit_count=len(commits),
            work_tracker=tracker,
        ),
        work_tracker=tracker,
    )

    graph: dict[str, tuple[str, ...]] = {}
    rows = _git_bytes(
        root,
        "rev-list",
        "--all",
        "--children",
        work_tracker=tracker,
        object_reads=len(commits),
    ).decode("ascii", errors="strict")
    for raw in rows.splitlines():
        fields = tuple(raw.split())
        if not fields:
            continue
        commit = _commit(fields[0], "initial sibling graph commit")
        children = tuple(_commit(value, "initial sibling graph child") for value in fields[1:])
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
        observed = _git_optional_blob(root, commit, path, work_tracker=tracker)
        if commit in descendants:
            if observed != payload:
                raise RehearsalV22ValidationError(
                    "initial sibling authority bytes drifted in its descendant lineage"
                )
        elif observed is not None:
            raise RehearsalV22ValidationError(
                "initial sibling authority path exists outside its descendant lineage"
            )
    if _git_optional_blob(root, head, path, work_tracker=tracker) != payload:
        raise RehearsalV22ValidationError("initial sibling execution-head bytes drifted")
    worktree_path = root.joinpath(*PurePosixPath(path).parts)
    if _validator_os.path.lexists(worktree_path):
        current = _regular_bytes(
            _safe_path(root, path, "initial sibling authority worktree file"),
            "initial sibling authority worktree file",
        )
        if current != payload:
            raise RehearsalV22ValidationError("initial sibling authority worktree bytes drifted")
    return payload


def _unique_a_unserialized(
    root: Path,
    *,
    path: str,
    execution_head: str,
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
) -> tuple[str, bytes]:
    """Derive one globally unique create-only authority and bind it to HEAD."""

    tracker = _IndependentRecoveryWorkTracker() if work_tracker is None else work_tracker
    relative = _relative(path, "first-parent authority path")
    head = _git_commit(
        root,
        execution_head,
        "first-parent execution HEAD",
        work_tracker=tracker,
    )
    creating_commit, payload, _classified = _discover_authority_source(
        root,
        path=relative,
        touches=_all_ref_path_touches(root, relative, work_tracker=tracker),
        work_tracker=tracker,
    )
    creating_commit = _git_commit(
        root,
        creating_commit,
        "authority creating commit",
        work_tracker=tracker,
    )
    if not _git_is_ancestor(root, creating_commit, head, work_tracker=tracker):
        raise RehearsalV22ValidationError(
            "authority creation commit is outside the execution-head lineage"
        )
    current = _regular_bytes(
        _safe_path(root, relative, "first-parent authority worktree file"),
        "first-parent authority worktree file",
    )
    if current != payload:
        raise RehearsalV22ValidationError(
            "first-parent authority current bytes differ from creation blob"
        )
    return creating_commit, payload


def _diff_name_status(
    root: Path,
    base: str,
    commit: str,
    *,
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
) -> tuple[tuple[str, str], ...]:
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
        work_tracker=work_tracker,
        object_reads=(2 if work_tracker is not None else 0),
    ).decode("utf-8", errors="strict")
    result: list[tuple[str, str]] = []
    for raw in output.splitlines():
        fields = raw.split("\t")
        if len(fields) != 2 or fields[0] not in {"A", "M", "D", "T", "U", "X", "B"}:
            raise RehearsalV22ValidationError("implementation Git surface is malformed")
        result.append((fields[0], _relative(fields[1], "implementation surface path")))
    return tuple(result)


def _git_ref_snapshot(
    root: Path,
    *,
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
) -> bytes:
    payload = _git_bytes(
        root,
        "for-each-ref",
        "--format=%(refname)%00%(objectname)",
        work_tracker=work_tracker,
    )
    rows = payload.splitlines(keepends=True)
    if rows != sorted(rows) or len(rows) != len(set(rows)):
        raise RehearsalV22ValidationError("Git ref snapshot is not canonical and unique")
    if work_tracker is not None:
        work_tracker.charge_git(object_reads=len(rows))
    return payload


def _assert_git_census_state_unchanged(
    project_root: Path,
    *,
    expected_refs: bytes,
    expected_head: str,
) -> None:
    observed_head = _git_commit(
        project_root,
        _git_bytes(project_root, "rev-parse", "HEAD").decode("ascii", errors="strict").strip(),
        "post-census execution HEAD",
    )
    if _git_ref_snapshot(project_root) != expected_refs or observed_head != expected_head:
        raise RehearsalV22ValidationError(
            "Git refs or HEAD changed between recovery census and live-anchor validation"
        )


def _base_authority_census_specs() -> tuple[AuthorityCensusSpec, ...]:
    specs: list[AuthorityCensusSpec] = []
    for path, (digest, creating_commit, _require_worktree) in sorted(
        _CONTROL_GOVERNANCE_AUTHORITIES.items(),
        key=lambda item: item[0].encode("utf-8"),
    ):
        role = (
            AuthorityCensusRole.PINNED_SOURCE_WITH_DESCENDANT_GRAPH
            if path == INDEPENDENT_REVIEW_RELATIVE.as_posix()
            else AuthorityCensusRole.PINNED_SOURCE
        )
        specs.append(
            AuthorityCensusSpec(
                path=path,
                pinned_sha256=digest,
                pinned_creating_commit=creating_commit,
                role=role,
            )
        )
    specs.extend(
        (
            AuthorityCensusSpec(
                path=PREREGISTRATION_RELATIVE.as_posix(),
                pinned_sha256=PREREGISTRATION_SHA256,
                pinned_creating_commit=INITIAL_REVIEWED_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=_V2_2_REMEDIATION_AUTHORITY[0],
                pinned_sha256=_V2_2_REMEDIATION_AUTHORITY[1],
                pinned_creating_commit=_V2_2_REMEDIATION_AUTHORITY[2],
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=_V2_2_SCOPE_AUTHORITY[0],
                pinned_sha256=_V2_2_SCOPE_AUTHORITY[1],
                pinned_creating_commit=_V2_2_SCOPE_AUTHORITY[2],
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=SERIES_2_PREREGISTRATION_RELATIVE.as_posix(),
                pinned_sha256=SERIES_2_PREREGISTRATION_SHA256,
                pinned_creating_commit=SERIES_2_PREREGISTRATION_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=SERIES_2_OWNER_DECISION_RELATIVE.as_posix(),
                pinned_sha256=SERIES_2_OWNER_DECISION_SHA256,
                pinned_creating_commit=SERIES_2_OWNER_DECISION_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=SERIES_2_LOSS_INCIDENT_RELATIVE.as_posix(),
                pinned_sha256=SERIES_2_TOKEN_SEED_SHA256,
                pinned_creating_commit=SERIES_2_LOSS_INCIDENT_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=VOID_EPOCH_ONE_ADJUDICATION_RELATIVE.as_posix(),
                pinned_sha256=VOID_EPOCH_ONE_ADJUDICATION_SHA256,
                pinned_creating_commit=VOID_EPOCH_ONE_ADJUDICATION_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=VOID_EPOCH_ONE_REVIEW_RELATIVE.as_posix(),
                pinned_sha256=VOID_EPOCH_ONE_REVIEW_SHA256,
                pinned_creating_commit=VOID_EPOCH_ONE_REVIEW_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=SERIES_2_EPOCH_5_LANDING_RELATIVE.as_posix(),
                pinned_sha256=SERIES_2_EPOCH_5_LANDING_SHA256,
                pinned_creating_commit=SERIES_2_EPOCH_5_LANDING_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            *(
                AuthorityCensusSpec(
                    path=path,
                    pinned_sha256=digest,
                    pinned_creating_commit=commit,
                    role=role,
                    declared_landing_projection_commit=(
                        commit if role is AuthorityCensusRole.PINNED_LANDING_PROJECTION else None
                    ),
                )
                for (path, digest, commit), role in (
                    (SERIES_2_ATTEMPT_1_AUTHORITY, AuthorityCensusRole.PINNED_SOURCE),
                    (SERIES_2_ATTEMPT_2_AUTHORITY, AuthorityCensusRole.PINNED_SOURCE),
                    (SERIES_2_EPOCH_5_SURFACE_AUTHORITY, AuthorityCensusRole.PINNED_SOURCE),
                    (SERIES_2_EPOCH_5_REVIEW, AuthorityCensusRole.PINNED_LANDING_PROJECTION),
                    (SERIES_2_EPOCH_6_SURFACE_AUTHORITY, AuthorityCensusRole.PINNED_SOURCE),
                    (SERIES_2_EPOCH_6_REVIEW, AuthorityCensusRole.PINNED_LANDING_PROJECTION),
                )
            ),
            AuthorityCensusSpec(
                path=SERIES_2_EPOCH_6_LANDING_RELATIVE.as_posix(),
                pinned_sha256=SERIES_2_EPOCH_6_LANDING_SHA256,
                pinned_creating_commit=SERIES_2_EPOCH_6_LANDING_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=EPOCH_7_ADJUDICATION_RELATIVE.as_posix(),
                pinned_sha256=EPOCH_7_ADJUDICATION_SHA256,
                pinned_creating_commit=EPOCH_7_ADJUDICATION_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=EPOCH_7_COMPANION_RELATIVE.as_posix(),
                pinned_sha256=EPOCH_7_COMPANION_SHA256,
                pinned_creating_commit=EPOCH_7_COMPANION_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=EPOCH_7_SURFACE_AUTHORITY_RELATIVE.as_posix(),
                pinned_sha256=EPOCH_7_SURFACE_AUTHORITY_SHA256,
                pinned_creating_commit=EPOCH_7_SURFACE_AUTHORITY_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=EPOCH_7_LIVE_REVIEW_RELATIVE.as_posix(),
                pinned_sha256=EPOCH_7_LIVE_REVIEW_SHA256,
                pinned_creating_commit=EPOCH_7_LIVE_REVIEW_LANDING_COMMIT,
                role=AuthorityCensusRole.PINNED_LANDING_PROJECTION,
                declared_landing_projection_commit=EPOCH_7_LIVE_REVIEW_LANDING_COMMIT,
            ),
            AuthorityCensusSpec(
                path=EPOCH_7_LIVE_LANDING_RELATIVE.as_posix(),
                pinned_sha256=EPOCH_7_LIVE_LANDING_SHA256,
                pinned_creating_commit=EPOCH_7_LIVE_LANDING_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=REFUSED_RECOVERY_Q_RELATIVE.as_posix(),
                pinned_sha256=REFUSED_RECOVERY_Q_SHA256,
                pinned_creating_commit=REFUSED_RECOVERY_Q_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=REFUSED_RECOVERY_R_RELATIVE.as_posix(),
                pinned_sha256=REFUSED_RECOVERY_R_SHA256,
                pinned_creating_commit=REFUSED_RECOVERY_R_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=REFUSED_RECOVERY_B_RELATIVE.as_posix(),
                pinned_sha256=REFUSED_RECOVERY_B_SHA256,
                pinned_creating_commit=REFUSED_RECOVERY_B_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=EPOCH_8_ADJUDICATION_RELATIVE.as_posix(),
                pinned_sha256=EPOCH_8_ADJUDICATION_SHA256,
                pinned_creating_commit=EPOCH_8_ADJUDICATION_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=EPOCH_8_COMPANION_RELATIVE.as_posix(),
                pinned_sha256=EPOCH_8_COMPANION_SHA256,
                pinned_creating_commit=EPOCH_8_COMPANION_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=EPOCH_8_SURFACE_AUTHORITY_RELATIVE.as_posix(),
                pinned_sha256=EPOCH_8_SURFACE_AUTHORITY_SHA256,
                pinned_creating_commit=EPOCH_8_SURFACE_AUTHORITY_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=EPOCH_9_ADJUDICATION_RELATIVE.as_posix(),
                pinned_sha256=EPOCH_9_ADJUDICATION_SHA256,
                pinned_creating_commit=EPOCH_9_ADJUDICATION_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=EPOCH_9_COMPANION_RELATIVE.as_posix(),
                pinned_sha256=EPOCH_9_COMPANION_SHA256,
                pinned_creating_commit=EPOCH_9_COMPANION_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
            AuthorityCensusSpec(
                path=EPOCH_9_SURFACE_AUTHORITY_RELATIVE.as_posix(),
                pinned_sha256=EPOCH_9_SURFACE_AUTHORITY_SHA256,
                pinned_creating_commit=EPOCH_9_SURFACE_AUTHORITY_COMMIT,
                role=AuthorityCensusRole.PINNED_SOURCE,
            ),
        )
    )
    return tuple(specs)


def _validate_epoch_8_fixed_carry_forward_registry(
    fixed_rows: object,
    *,
    base_specs: Sequence[AuthorityCensusSpec] | None = None,
) -> None:
    """Bind companion carry-forward rows to the independently built runtime registry."""

    expected_rows = (
        {
            "path": EPOCH_7_LIVE_REVIEW_RELATIVE.as_posix(),
            "sha256": EPOCH_7_LIVE_REVIEW_SHA256,
            "bytes": EPOCH_7_LIVE_REVIEW_BYTES,
            "creating_commit": EPOCH_7_LIVE_REVIEW_LANDING_COMMIT,
            "role": AuthorityCensusRole.PINNED_LANDING_PROJECTION.value,
            "declared_landing_projection_commit": EPOCH_7_LIVE_REVIEW_LANDING_COMMIT,
        },
        {
            "path": EPOCH_7_LIVE_LANDING_RELATIVE.as_posix(),
            "sha256": EPOCH_7_LIVE_LANDING_SHA256,
            "bytes": EPOCH_7_LIVE_LANDING_BYTES,
            "creating_commit": EPOCH_7_LIVE_LANDING_COMMIT,
            "role": AuthorityCensusRole.PINNED_SOURCE.value,
            "declared_landing_projection_commit": None,
        },
        {
            "path": REFUSED_RECOVERY_Q_RELATIVE.as_posix(),
            "sha256": REFUSED_RECOVERY_Q_SHA256,
            "bytes": REFUSED_RECOVERY_Q_BYTES,
            "creating_commit": REFUSED_RECOVERY_Q_COMMIT,
            "role": AuthorityCensusRole.PINNED_SOURCE.value,
            "declared_landing_projection_commit": None,
        },
        {
            "path": REFUSED_RECOVERY_R_RELATIVE.as_posix(),
            "sha256": REFUSED_RECOVERY_R_SHA256,
            "bytes": REFUSED_RECOVERY_R_BYTES,
            "creating_commit": REFUSED_RECOVERY_R_COMMIT,
            "role": AuthorityCensusRole.PINNED_SOURCE.value,
            "declared_landing_projection_commit": None,
        },
        {
            "path": REFUSED_RECOVERY_B_RELATIVE.as_posix(),
            "sha256": REFUSED_RECOVERY_B_SHA256,
            "bytes": REFUSED_RECOVERY_B_BYTES,
            "creating_commit": REFUSED_RECOVERY_B_COMMIT,
            "role": AuthorityCensusRole.PINNED_SOURCE.value,
            "declared_landing_projection_commit": None,
        },
        {
            "path": EPOCH_8_ADJUDICATION_RELATIVE.as_posix(),
            "sha256": EPOCH_8_ADJUDICATION_SHA256,
            "bytes": EPOCH_8_ADJUDICATION_BYTES,
            "creating_commit": EPOCH_8_ADJUDICATION_COMMIT,
            "role": AuthorityCensusRole.PINNED_SOURCE.value,
            "declared_landing_projection_commit": None,
        },
    )
    if fixed_rows != list(expected_rows):
        raise RehearsalV22ValidationError("epoch-8 fixed census carry-forward drifted")
    expected_specs = (
        *(
            AuthorityCensusSpec(
                path=cast(str, row["path"]),
                pinned_sha256=cast(str, row["sha256"]),
                pinned_creating_commit=cast(str, row["creating_commit"]),
                role=AuthorityCensusRole(cast(str, row["role"])),
                declared_landing_projection_commit=cast(
                    str | None,
                    row["declared_landing_projection_commit"],
                ),
            )
            for row in expected_rows
        ),
        AuthorityCensusSpec(
            path=EPOCH_8_COMPANION_RELATIVE.as_posix(),
            pinned_sha256=EPOCH_8_COMPANION_SHA256,
            pinned_creating_commit=EPOCH_8_COMPANION_COMMIT,
            role=AuthorityCensusRole.PINNED_SOURCE,
        ),
        AuthorityCensusSpec(
            path=EPOCH_8_SURFACE_AUTHORITY_RELATIVE.as_posix(),
            pinned_sha256=EPOCH_8_SURFACE_AUTHORITY_SHA256,
            pinned_creating_commit=EPOCH_8_SURFACE_AUTHORITY_COMMIT,
            role=AuthorityCensusRole.PINNED_SOURCE,
        ),
    )
    observed_by_path: dict[str, AuthorityCensusSpec] = {}
    for spec in _base_authority_census_specs() if base_specs is None else base_specs:
        prior = observed_by_path.get(spec.path)
        if prior is not None and prior != spec:
            raise RehearsalV22ValidationError(
                f"epoch-8 runtime carry-forward registry conflicts: {spec.path}"
            )
        observed_by_path[spec.path] = spec
    for expected in expected_specs:
        if observed_by_path.get(expected.path) != expected:
            raise RehearsalV22ValidationError(
                f"epoch-8 runtime carry-forward registry drifted: {expected.path}"
            )


def _canonical_authority_registry(
    additional_specs: Sequence[AuthorityCensusSpec],
) -> tuple[AuthorityCensusSpec, ...]:
    by_path: dict[str, AuthorityCensusSpec] = {}
    for spec in (*_base_authority_census_specs(), *additional_specs):
        path = _relative(spec.path, "authority census registry path")
        normalized = AuthorityCensusSpec(
            path=path,
            pinned_sha256=_sha(spec.pinned_sha256, "authority census registry SHA"),
            pinned_creating_commit=_commit(
                spec.pinned_creating_commit,
                "authority census registry commit",
            ),
            role=AuthorityCensusRole(spec.role),
            declared_landing_projection_commit=(
                _commit(
                    spec.declared_landing_projection_commit,
                    "authority census landing projection",
                )
                if spec.declared_landing_projection_commit is not None
                else None
            ),
        )
        prior = by_path.get(path)
        if prior is not None and prior != normalized:
            raise RehearsalV22ValidationError(
                f"authority census registry has conflicting specs: {path}"
            )
        by_path[path] = normalized
    return tuple(
        sorted(
            by_path.values(),
            key=lambda row: (
                row.path.encode("utf-8"),
                row.pinned_creating_commit.encode("ascii"),
                row.role.value.encode("ascii"),
            ),
        )
    )


def _authority_census_row(
    root: Path,
    *,
    execution_head: str,
    spec: AuthorityCensusSpec,
    all_ref_commits: tuple[str, ...],
    work_tracker: _IndependentRecoveryWorkTracker,
) -> JsonObject:
    touches = _all_ref_path_touches(
        root,
        spec.path,
        all_ref_commit_count=len(all_ref_commits),
        work_tracker=work_tracker,
    )
    source_commit = spec.pinned_creating_commit
    if spec.role is AuthorityCensusRole.DISCOVER_SOURCE_AFTER_PROJECTIONS:
        source_commit, payload, classified = _discover_authority_source(
            root,
            path=spec.path,
            touches=touches,
            work_tracker=work_tracker,
        )
        if _sha256(payload) != spec.pinned_sha256:
            raise RehearsalV22ValidationError(
                f"discovered authority payload differs from pinned SHA: {spec.path}"
            )
    else:
        payload = _git_blob(
            root,
            spec.pinned_creating_commit,
            spec.path,
            work_tracker=work_tracker,
        )
        if _sha256(payload) != spec.pinned_sha256:
            raise RehearsalV22ValidationError(f"authority census pinned bytes drifted: {spec.path}")
    if spec.role is AuthorityCensusRole.PINNED_LANDING_PROJECTION:
        landing = spec.declared_landing_projection_commit or spec.pinned_creating_commit
        landing_parents = _git_parents(root, landing, work_tracker=work_tracker)
        if len(landing_parents) != 2:
            raise RehearsalV22ValidationError(
                f"declared authority landing is not two-parent: {spec.path}"
            )
        candidates = [
            commit
            for commit, status, paths in touches
            if status == "A"
            and paths == (spec.path,)
            and len(_git_parents(root, commit, work_tracker=work_tracker)) == 1
            and _git_optional_blob(
                root,
                commit,
                spec.path,
                work_tracker=work_tracker,
            )
            == payload
            and _git_is_ancestor(
                root,
                commit,
                landing_parents[1],
                work_tracker=work_tracker,
            )
        ]
        if len(candidates) != 1:
            raise RehearsalV22ValidationError(
                f"declared authority landing source is ambiguous: {spec.path}"
            )
        source_commit = candidates[0]
        classified = _classify_authority_touches(
            root,
            path=spec.path,
            pinned_payload=payload,
            source_commit=source_commit,
            touches=touches,
            work_tracker=work_tracker,
        )
        if not any(
            row["commit"] == landing and row["classification"] == "FIRST_PARENT_MERGE_PROJECTION"
            for row in classified
        ):
            raise RehearsalV22ValidationError(
                f"declared authority landing is not the classified projection: {spec.path}"
            )
    elif spec.role is not AuthorityCensusRole.DISCOVER_SOURCE_AFTER_PROJECTIONS:
        classified = _classify_authority_touches(
            root,
            path=spec.path,
            pinned_payload=payload,
            source_commit=source_commit,
            touches=touches,
            work_tracker=work_tracker,
        )
    if spec.role is AuthorityCensusRole.PINNED_SOURCE_WITH_DESCENDANT_GRAPH:
        _validate_initial_sibling_authority(
            root,
            {
                "path": spec.path,
                "sha256": spec.pinned_sha256,
                "creating_commit": spec.pinned_creating_commit,
                "unique_a_history_verified": True,
            },
            execution_head=execution_head,
            all_ref_commits=all_ref_commits,
            work_tracker=work_tracker,
        )
    head_blob = _git_optional_blob(
        root,
        execution_head,
        spec.path,
        work_tracker=work_tracker,
    )
    if head_blob != payload or not _git_is_ancestor(
        root,
        source_commit,
        execution_head,
        work_tracker=work_tracker,
    ):
        raise RehearsalV22ValidationError(
            f"authority census source or HEAD bytes drifted: {spec.path}"
        )
    worktree_payload = _regular_bytes(
        _safe_path(root, spec.path, "authority census worktree file"),
        "authority census worktree file",
    )
    if worktree_payload != payload:
        raise RehearsalV22ValidationError(f"authority census worktree bytes drifted: {spec.path}")
    projection_count = sum(
        row["classification"] == "FIRST_PARENT_MERGE_PROJECTION" for row in classified
    )
    return {
        "path": spec.path,
        "pinned_sha256": spec.pinned_sha256,
        "pinned_creating_commit": spec.pinned_creating_commit,
        "mode": spec.role.value,
        "logical_source_commit": source_commit,
        "declared_landing_projection_commit": spec.declared_landing_projection_commit,
        "raw_touch_count": len(touches),
        "source_count": 1,
        "projection_count": projection_count,
        "touches": list(classified),
        "execution_head_contains_source": True,
        "head_blob_sha256": _sha256(head_blob),
        "worktree_sha256": _sha256(worktree_payload),
        "verdict": "PASS_ONE_LOGICAL_SOURCE_AND_ONLY_LAWFUL_PROJECTIONS",
    }


def _real_lineage_census(
    project_root: Path,
    *,
    execution_head: str,
    additional_specs: Sequence[AuthorityCensusSpec] = (),
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
) -> JsonObject:
    root = project_root.resolve(strict=True)
    tracker = _IndependentRecoveryWorkTracker() if work_tracker is None else work_tracker
    before = _git_ref_snapshot(root, work_tracker=tracker)
    all_ref_commits = _git_all_ref_commits(root, work_tracker=tracker)
    observed_head = _git_commit(
        root,
        _git_bytes(
            root,
            "rev-parse",
            "HEAD",
            work_tracker=tracker,
            object_reads=1,
        )
        .decode("ascii", errors="strict")
        .strip(),
        "real-lineage execution HEAD",
        work_tracker=tracker,
    )
    expected_head = _git_commit(
        root,
        execution_head,
        "real-lineage requested HEAD",
        work_tracker=tracker,
    )
    if observed_head != expected_head:
        raise RehearsalV22ValidationError("real-lineage census is not at execution HEAD")
    registry = _canonical_authority_registry(additional_specs)
    registry_document = [
        {
            "path": spec.path,
            "pinned_sha256": spec.pinned_sha256,
            "pinned_creating_commit": spec.pinned_creating_commit,
            "mode": spec.role.value,
            "declared_landing_projection_commit": spec.declared_landing_projection_commit,
        }
        for spec in registry
    ]
    rows: list[JsonObject] = []
    for spec in registry:
        rows.append(
            _authority_census_row(
                root,
                execution_head=observed_head,
                spec=spec,
                all_ref_commits=all_ref_commits,
                work_tracker=tracker,
            )
        )
        _assert_recovery_work_bound(tracker.snapshot())
    after = _git_ref_snapshot(root, work_tracker=tracker)
    if before != after:
        raise RehearsalV22ValidationError("Git refs changed during real-lineage census")
    projection_count = sum(cast(int, row["projection_count"]) for row in rows)
    result = {
        "schema_version": "p4.2a-v2-2-real-lineage-census-v1",
        "execution_head": observed_head,
        "authority_registry_sha256": _sha256(_canonical_json_bytes(registry_document)),
        "ref_snapshot_before_sha256": _sha256(before),
        "ref_snapshot_after_sha256": _sha256(after),
        "reference_count": len(registry),
        "row_count": len(rows),
        "source_count": len(rows),
        "projection_count": projection_count,
        "invalid_count": 0,
        "rows": rows,
        "effects": {
            "git_ref_write": False,
            "git_index_write": False,
            "git_worktree_write": False,
            "ledger_write": False,
            "mirror_write": False,
            "destination_write": False,
            "temporary_write": False,
            "network_access": False,
            "database_access": False,
            "pipeline_execution": False,
            "heldout_access": False,
        },
        "status": "PASS_REAL_LINEAGE_CENSUS",
    }
    _require_exact_keys(result, REAL_LINEAGE_CENSUS_FIELDS, "real-lineage census")
    for row in rows:
        _require_exact_keys(row, REAL_LINEAGE_ROW_FIELDS, "real-lineage census row")
        for touch in cast(list[JsonObject], row["touches"]):
            _require_exact_keys(
                touch,
                REAL_LINEAGE_TOUCH_FIELDS,
                "real-lineage census touch",
            )
    return result


def validate_epoch_7_recovery_contract(project_root: Path) -> JsonObject:
    """Independently validate the companion's byte-authoritative 12-field contract."""

    root = project_root.resolve(strict=True)
    for relative, digest, expected_bytes, commit in (
        (
            EPOCH_7_DESIGN_R1_RELATIVE,
            EPOCH_7_DESIGN_R1_SHA256,
            EPOCH_7_DESIGN_R1_BYTES,
            EPOCH_7_DESIGN_R1_COMMIT,
        ),
        (
            EPOCH_7_DESIGN_R2_RELATIVE,
            EPOCH_7_DESIGN_R2_SHA256,
            EPOCH_7_DESIGN_R2_BYTES,
            EPOCH_7_DESIGN_R2_COMMIT,
        ),
    ):
        design_payload = _git_blob(root, commit, relative.as_posix())
        if len(design_payload) != expected_bytes or _sha256(design_payload) != digest:
            raise RehearsalV22ValidationError("epoch-7 design authority bytes drifted")
    payload = _regular_bytes(
        root / EPOCH_7_COMPANION_RELATIVE,
        "epoch-7 recovery companion",
    )
    if (
        _sha256(payload) != EPOCH_7_COMPANION_SHA256
        or _git_blob(
            root,
            EPOCH_7_COMPANION_COMMIT,
            EPOCH_7_COMPANION_RELATIVE.as_posix(),
        )
        != payload
    ):
        raise RehearsalV22ValidationError("epoch-7 recovery companion bytes drifted")
    if (
        _unique_a_authority(
            root,
            {
                "path": EPOCH_7_COMPANION_RELATIVE.as_posix(),
                "sha256": EPOCH_7_COMPANION_SHA256,
                "creating_commit": EPOCH_7_COMPANION_COMMIT,
                "unique_a_history_verified": True,
            },
            require_worktree=True,
        )
        != payload
    ):
        raise RehearsalV22ValidationError("epoch-7 companion authority drifted")
    document = _object(
        strict_json_loads(payload, label="epoch-7 recovery companion"),
        "epoch-7 recovery companion",
    )
    contract = _object(document.get("epoch_7_recovery_contract"), "epoch-7 recovery contract")
    _require_exact_keys(contract, EPOCH_7_RECOVERY_CONTRACT_FIELDS, "epoch-7 recovery contract")
    if _sha256(_canonical_json_bytes(contract)) != EPOCH_7_CONTRACT_CANONICAL_SHA256:
        raise RehearsalV22ValidationError("epoch-7 recovery contract canonical bytes drifted")
    _require_equal(
        contract.get("schema_version"),
        "p4.2a-v2-2-series2-epoch7-recovery-contract-v1",
        "epoch-7 contract schema",
    )
    _require_equal(contract.get("implementation_epoch"), 7, "epoch-7 contract epoch")
    _require_equal(
        contract.get("governing_adjudication"),
        {
            "path": EPOCH_7_ADJUDICATION_RELATIVE.as_posix(),
            "sha256": EPOCH_7_ADJUDICATION_SHA256,
            "creating_commit": EPOCH_7_ADJUDICATION_COMMIT,
            "unique_a_history_verified": True,
        },
        "epoch-7 governing adjudication",
    )
    q = _object(contract.get("recovery_review_request_contract"), "epoch-7 Q contract")
    r = _object(contract.get("recovery_authorization_contract"), "epoch-7 R contract")
    b = _object(contract.get("recovery_owner_binding_contract"), "epoch-7 B contract")
    claim = _object(contract.get("recovery_claim_contract"), "epoch-7 claim contract")
    receipt = _object(
        contract.get("bundle_mirror_receipt_contract"),
        "epoch-7 mirror receipt contract",
    )
    anchors = _object(contract.get("dual_byte_anchor_contract"), "epoch-7 anchor contract")
    census = _object(
        contract.get("unique_a_and_lineage_census_contract"),
        "epoch-7 census contract",
    )
    protected = _object(
        contract.get("protected_inputs_and_permitted_outputs"),
        "epoch-7 effect contract",
    )
    legacy = _object(contract.get("legacy_absence_and_locks"), "epoch-7 legacy contract")
    for node, expected, label in (
        (
            q,
            frozenset(
                {
                    "exact_top_level_fields",
                    "nested_exact_field_sets",
                    "path_pattern",
                    "rules",
                    "schema_version",
                    "status_value",
                    "topology",
                }
            ),
            "Q contract",
        ),
        (
            r,
            frozenset(
                {
                    "counters",
                    "effect_authorization_exact",
                    "exact_top_level_fields",
                    "fixed_values",
                    "nested_exact_field_sets",
                    "path_pattern",
                    "schema_version",
                    "topology",
                    "verdict_value",
                }
            ),
            "R contract",
        ),
        (
            b,
            frozenset(
                {
                    "bootstrap_order",
                    "cli_operations",
                    "exact_top_level_fields",
                    "fixed_values",
                    "nested_exact_field_sets",
                    "path_pattern",
                    "schema_version",
                    "topology",
                }
            ),
            "B contract",
        ),
        (
            claim,
            frozenset(
                {
                    "claim_name",
                    "crash_states",
                    "linearization",
                    "outcomes",
                    "started_exact_fields",
                    "started_schema_version",
                    "terminal_exact_fields",
                    "terminal_schema_version",
                }
            ),
            "claim contract",
        ),
        (
            receipt,
            frozenset({"exact_fields", "filename_pattern", "rules", "schema_version"}),
            "mirror receipt contract",
        ),
        (
            anchors,
            frozenset(
                {
                    "capability_required_values",
                    "historical_selected_anchor",
                    "hook_disposition_authority",
                    "live_execution_anchor",
                    "mode_enum",
                    "no_fallback",
                    "recovered_publication_capability_exact_fields",
                    "release_truth_condition",
                }
            ),
            "dual-anchor contract",
        ),
        (
            census,
            frozenset(
                {
                    "census_exact_fields",
                    "census_schema_version",
                    "projection_criteria",
                    "roles",
                    "row_exact_fields",
                    "rules",
                    "scanner_role_map",
                    "timing",
                    "touch_exact_fields",
                }
            ),
            "census contract",
        ),
        (
            protected,
            frozenset(
                {
                    "consume_mode_effects",
                    "container_rules",
                    "forbidden_calls",
                    "permitted_recovery_writes",
                    "read_only_inputs",
                    "recovery_containers",
                    "sealed_input_invariance",
                }
            ),
            "effect contract",
        ),
        (
            legacy,
            frozenset(
                {
                    "amendment_time_facts_permanently_false",
                    "disclosure_rule",
                    "epoch_table",
                    "locks",
                }
            ),
            "legacy contract",
        ),
    ):
        _require_exact_keys(node, expected, label)
    for observed, expected_order, order_label in (
        (
            q.get("exact_top_level_fields"),
            EPOCH_7_RECOVERY_REVIEW_REQUEST_FIELD_ORDER,
            "Q exact fields",
        ),
        (r.get("exact_top_level_fields"), RECOVERY_AUTHORIZATION_FIELD_ORDER, "R exact fields"),
        (b.get("exact_top_level_fields"), RECOVERY_OWNER_BINDING_FIELD_ORDER, "B exact fields"),
        (claim.get("started_exact_fields"), RECOVERY_STARTED_FIELD_ORDER, "claim started fields"),
        (
            claim.get("terminal_exact_fields"),
            RECOVERY_TERMINAL_FIELD_ORDER,
            "claim terminal fields",
        ),
        (receipt.get("exact_fields"), RECOVERY_MIRROR_RECEIPT_FIELD_ORDER, "mirror receipt fields"),
        (census.get("census_exact_fields"), REAL_LINEAGE_CENSUS_FIELD_ORDER, "census fields"),
        (census.get("row_exact_fields"), REAL_LINEAGE_ROW_FIELD_ORDER, "census row fields"),
        (census.get("touch_exact_fields"), REAL_LINEAGE_TOUCH_FIELD_ORDER, "census touch fields"),
    ):
        values = tuple(cast(Sequence[object], observed))
        if any(not isinstance(value, str) for value in values) or len(set(values)) != len(values):
            raise RehearsalV22ValidationError(f"{order_label} are not unique strings")
        _require_equal(values, expected_order, order_label)
    _require_equal(
        tuple(anchors.get("mode_enum", ())),
        tuple(mode.value for mode in BundleValidationMode),
        "bundle validation modes",
    )
    _require_equal(
        tuple(anchors.get("recovered_publication_capability_exact_fields", ())),
        RECOVERED_PUBLICATION_CAPABILITY_FIELDS,
        "recovered-publication capability fields",
    )
    historical = _object(anchors.get("historical_selected_anchor"), "historical anchor")
    if (
        historical.get("implementation_epoch") != HISTORICAL_SELECTED_EPOCH
        or historical.get("implementation_commit") != HISTORICAL_SELECTED_IMPLEMENTATION_COMMIT
        or historical.get("selected_control_merkle_root_sha256")
        != HISTORICAL_SELECTED_CONTROL_ROOT_SHA256
        or historical.get("require_current") is not False
    ):
        raise RehearsalV22ValidationError("historical selected anchor contract drifted")
    _require_equal(
        tuple(census.get("roles", ())),
        tuple(role.value for role in AuthorityCensusRole),
        "authority census roles",
    )
    _require_equal(
        tuple(protected.get("recovery_containers", ())),
        (
            SERIES_2_PRIMARY_RECOVERY_CONTAINER.as_posix(),
            SERIES_2_SECONDARY_RECOVERY_CONTAINER.as_posix(),
        ),
        "registered recovery containers",
    )
    _require_equal(
        tuple(legacy.get("amendment_time_facts_permanently_false", ())),
        LEGACY_AMENDMENT_ABSENCE_FIELDS,
        "legacy amendment absence fields",
    )
    _require_equal(
        r.get("counters"),
        {
            "authorized_bundle_recovery_starts": 1,
            "authorized_pipeline_starts": 0,
            "automatic_retry_count": 0,
        },
        "recovery authorization counters",
    )
    _require_equal(
        r.get("effect_authorization_exact"),
        {
            "attempt_allocation": False,
            "candidate_or_terminal_rewrite": False,
            "destination_publish_once": True,
            "git_metadata_or_tracked_worktree_write": False,
            "git_object_read": True,
            "heldout_materialization_inference_or_evaluation": False,
            "ledger_read": True,
            "ledger_write": False,
            "model_access": False,
            "network_access": False,
            "paired_bundle_receipts_create_once": True,
            "pipeline_execution": False,
            "recovery_claim_create_once": True,
            "sealed_ledger_mirror_read": True,
            "sealed_ledger_mirror_write": False,
            "secondary_bundle_mirror_publish_once": True,
            "sqlite_or_production_database_access": False,
            "destination_stage_create_once": True,
            "secondary_snapshot_stage_create_once": True,
        },
        "recovery effect authorization",
    )
    _require_equal(
        b.get("bootstrap_order"),
        [
            "process/interpreter/environment and locked bootstrap",
            "R canonical identity, topology and scope",
            "B canonical identity, topology, owner text and R binding",
            "epoch-7 live byte anchor",
            "real-lineage census",
            "recovery-container validation",
            "sealed ledger and sealed-mirror validation",
            "destination/claim/temp absence",
            "any filesystem write",
        ],
        "recovery bootstrap order",
    )
    fixed_b = _object(b.get("fixed_values"), "recovery B fixed values")
    _require_equal(fixed_b.get("owner_identity"), "ouyang", "recovery B owner")
    _require_equal(
        fixed_b.get("source"),
        "业主向复核方当面确认，由复核方转达",
        "recovery B source",
    )
    _require_equal(
        fixed_b.get("machine_boundary_values"),
        [True, False, True, True, True],
        "recovery B machine boundary",
    )
    _require_equal(
        census.get("scanner_role_map"),
        {
            "_unique_a_authority": "PINNED_SOURCE",
            "_validate_implementation_review_authority.all_touches": ("PINNED_LANDING_PROJECTION"),
            "_validate_initial_sibling_authority": "PINNED_SOURCE_WITH_DESCENDANT_GRAPH",
            "_unique_a_unserialized": "DISCOVER_SOURCE_AFTER_PROJECTIONS",
        },
        "authority scanner role map",
    )
    _require_equal(
        anchors.get("capability_required_values"),
        {
            "selected_attempt_ordinal": 2,
            "selected_implementation_epoch": 6,
            "execution_epoch": 7,
            "recovery_starts": 1,
            "pipeline_starts": 0,
            "automatic_retry_count": 0,
            "sealed_ledger_before_after_equal": True,
            "sealed_mirror_before_after_equal": True,
            "historical_run_roots_equal": SEALED_SELECTED_RUN_ROOT_SHA256,
            "historical_full_downstream_replay_verified": True,
        },
        "recovered-publication capability fixed values",
    )
    _require_equal(
        legacy.get("epoch_table"),
        (
            "the bundle implementation-epoch table remains exactly [5,6]; epoch 7 is "
            "recovery execution provenance, never a synthetic attempt row; no ordinal 3 "
            "may be allocated"
        ),
        "epoch table contract",
    )
    return contract


def _epoch_8_governance_payload(
    root: Path,
    *,
    relative: Path,
    digest: str,
    expected_bytes: int,
    creating_commit: str,
    label: str,
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
    all_ref_commits: Sequence[str] | None = None,
) -> bytes:
    payload = _regular_bytes(root / relative, label)
    if (
        len(payload) != expected_bytes
        or _sha256(payload) != digest
        or _git_blob(
            root,
            creating_commit,
            relative.as_posix(),
            work_tracker=work_tracker,
        )
        != payload
        or _unique_a_authority(
            root,
            {
                "path": relative.as_posix(),
                "sha256": digest,
                "creating_commit": creating_commit,
                "unique_a_history_verified": True,
            },
            require_worktree=True,
            work_tracker=work_tracker,
            all_ref_commits=all_ref_commits,
        )
        != payload
    ):
        raise RehearsalV22ValidationError(f"{label} bytes or unique-A identity drifted")
    return payload


def validate_epoch_8_recovery_contract(
    project_root: Path,
    *,
    execution_head: str | None = None,
    work_tracker: _IndependentRecoveryWorkTracker | None = None,
    all_ref_commits: Sequence[str] | None = None,
) -> JsonObject:
    """Independently validate the epoch-8 companion's exact 13-field contract."""

    root = project_root.resolve(strict=True)
    if (work_tracker is None) != (all_ref_commits is None):
        raise RehearsalV22ValidationError(
            "epoch-8 contract work tracker and all-ref snapshot must be supplied together"
        )
    tracker = _IndependentRecoveryWorkTracker() if work_tracker is None else work_tracker
    commits = (
        _git_all_ref_commits(root, work_tracker=tracker)
        if all_ref_commits is None
        else tuple(all_ref_commits)
    )
    if (
        not commits
        or len(commits) != len(set(commits))
        or any(_COMMIT_PATTERN.fullmatch(commit) is None for commit in commits)
    ):
        raise RehearsalV22ValidationError("epoch-8 contract all-ref snapshot is malformed")
    if execution_head is not None:
        head = _git_commit(
            root,
            execution_head,
            "epoch-8 contract execution HEAD",
            work_tracker=tracker,
        )
        for commit in (
            EPOCH_8_DESIGN_COMMIT,
            EPOCH_8_DESIGN_REVIEW_COMMIT,
            EPOCH_8_ADJUDICATION_COMMIT,
            EPOCH_8_COMPANION_COMMIT,
            EPOCH_8_SURFACE_AUTHORITY_COMMIT,
        ):
            if not _git_is_ancestor(root, commit, head, work_tracker=tracker):
                raise RehearsalV22ValidationError(
                    "epoch-8 governance is outside the requested execution HEAD"
                )
    for relative, digest, expected_bytes, commit, label in (
        (
            EPOCH_8_DESIGN_RELATIVE,
            EPOCH_8_DESIGN_SHA256,
            EPOCH_8_DESIGN_BYTES,
            EPOCH_8_DESIGN_COMMIT,
            "epoch-8 design",
        ),
        (
            EPOCH_8_DESIGN_REVIEW_RELATIVE,
            EPOCH_8_DESIGN_REVIEW_SHA256,
            EPOCH_8_DESIGN_REVIEW_BYTES,
            EPOCH_8_DESIGN_REVIEW_COMMIT,
            "epoch-8 independent design review",
        ),
        (
            EPOCH_8_ADJUDICATION_RELATIVE,
            EPOCH_8_ADJUDICATION_SHA256,
            EPOCH_8_ADJUDICATION_BYTES,
            EPOCH_8_ADJUDICATION_COMMIT,
            "epoch-8 governing adjudication",
        ),
    ):
        _epoch_8_governance_payload(
            root,
            relative=relative,
            digest=digest,
            expected_bytes=expected_bytes,
            creating_commit=commit,
            label=label,
            work_tracker=tracker,
            all_ref_commits=commits,
        )
    payload = _epoch_8_governance_payload(
        root,
        relative=EPOCH_8_COMPANION_RELATIVE,
        digest=EPOCH_8_COMPANION_SHA256,
        expected_bytes=EPOCH_8_COMPANION_BYTES,
        creating_commit=EPOCH_8_COMPANION_COMMIT,
        label="epoch-8 recovery companion",
        work_tracker=tracker,
        all_ref_commits=commits,
    )
    if _git_parents(
        root,
        EPOCH_8_COMPANION_COMMIT,
        work_tracker=tracker,
    ) != (EPOCH_8_ADJUDICATION_COMMIT,):
        raise RehearsalV22ValidationError("epoch-8 companion parent drifted")
    document = _object(
        strict_json_loads(payload, label="epoch-8 recovery companion"),
        "epoch-8 recovery companion",
    )
    contract = _object(document.get("epoch_8_recovery_contract"), "epoch-8 recovery contract")
    _require_exact_keys(contract, EPOCH_8_RECOVERY_CONTRACT_FIELDS, "epoch-8 recovery contract")
    if _sha256(_canonical_json_bytes(contract)) != EPOCH_8_CONTRACT_CANONICAL_SHA256:
        raise RehearsalV22ValidationError("epoch-8 recovery contract canonical bytes drifted")
    _require_equal(
        contract.get("schema_version"),
        EPOCH_8_RECOVERY_CONTRACT_SCHEMA,
        "epoch-8 contract schema",
    )
    _require_equal(
        contract.get("implementation_epoch"),
        HISTORICAL_EPOCH_8_RECOVERY_GOVERNANCE_EPOCH,
        "epoch-8 contract epoch",
    )
    _require_equal(
        contract.get("governing_adjudication"),
        {
            "path": EPOCH_8_ADJUDICATION_RELATIVE.as_posix(),
            "sha256": EPOCH_8_ADJUDICATION_SHA256,
            "creating_commit": EPOCH_8_ADJUDICATION_COMMIT,
            "unique_a_history_verified": True,
        },
        "epoch-8 governing adjudication",
    )
    expected_surface = [
        {"path": "scripts/p4_2a_v2_2_heldout_rehearsal.py", "status": "M"},
        {"path": "scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py", "status": "M"},
        {"path": "tests/test_p4_2a_v2_2_heldout_rehearsal_runner.py", "status": "M"},
        {"path": "tests/test_p4_2a_v2_2_heldout_rehearsal_validator.py", "status": "M"},
    ]
    owner_payload = _epoch_8_governance_payload(
        root,
        relative=EPOCH_8_SURFACE_AUTHORITY_RELATIVE,
        digest=EPOCH_8_SURFACE_AUTHORITY_SHA256,
        expected_bytes=EPOCH_8_SURFACE_AUTHORITY_BYTES,
        creating_commit=EPOCH_8_SURFACE_AUTHORITY_COMMIT,
        label="epoch-8 surface authority",
        work_tracker=tracker,
        all_ref_commits=commits,
    )
    owner_document = _object(
        strict_json_loads(owner_payload, label="epoch-8 surface authority"),
        "epoch-8 surface authority",
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
        "epoch-8 surface authority",
    )
    _require_equal(
        owner_document,
        {
            "schema_version": "p4.2a-v2-2-implementation-epoch-surface-authorization-v1",
            "verdict": "APPROVE_EXACT_V2_2_IMPLEMENTATION_EPOCH_SURFACE",
            "owner": {"identity": "ouyang", "approved": True},
            "implementation_epoch": HISTORICAL_EPOCH_8_RECOVERY_GOVERNANCE_EPOCH,
            "base_commit": EPOCH_8_COMPANION_COMMIT,
            "exact_surface": expected_surface,
        },
        "epoch-8 surface authority",
    )
    if _git_parents(
        root,
        EPOCH_8_SURFACE_AUTHORITY_COMMIT,
        work_tracker=tracker,
    ) != (EPOCH_8_COMPANION_COMMIT,):
        raise RehearsalV22ValidationError("epoch-8 surface authority parent drifted")
    owner_approval = _object(document.get("part_2_owner_approval"), "epoch-8 owner approval")
    if (
        owner_approval.get("accepted_owner_decision_count") != 12
        or owner_approval.get("rejected_owner_decision_count") != 0
        or owner_approval.get("future_surface_limit") != expected_surface
        or owner_approval.get("implementation_authorized") is not False
        or owner_approval.get("gate_authorized") is not False
        or owner_approval.get("recovery_authorized") is not False
    ):
        raise RehearsalV22ValidationError("epoch-8 owner approval scope drifted")

    preflight = _object(contract.get("registered_preflight_contract"), "epoch-8 preflight contract")
    q = _object(contract.get("recovery_review_request_contract"), "epoch-8 Q contract")
    r = _object(contract.get("recovery_authorization_contract"), "epoch-8 R contract")
    b = _object(contract.get("recovery_owner_binding_contract"), "epoch-8 B contract")
    claim = _object(contract.get("recovery_claim_contract"), "epoch-8 claim contract")
    receipt = _object(
        contract.get("bundle_mirror_receipt_contract"),
        "epoch-8 mirror receipt contract",
    )
    anchors = _object(contract.get("dual_byte_anchor_contract"), "epoch-8 anchor contract")
    census = _object(
        contract.get("unique_a_and_lineage_census_contract"),
        "epoch-8 census contract",
    )
    protected = _object(
        contract.get("protected_inputs_and_permitted_outputs"),
        "epoch-8 effect contract",
    )
    legacy = _object(contract.get("legacy_absence_and_locks"), "epoch-8 legacy contract")
    for node, expected, label in (
        (
            preflight,
            frozenset(
                {
                    "baseline_contract",
                    "cli_contract",
                    "exact_top_level_fields",
                    "fixed_values",
                    "landing_authority_contract",
                    "nested_exact_field_sets",
                    "rules",
                    "schema_version",
                }
            ),
            "preflight contract",
        ),
        (
            q,
            frozenset(
                {
                    "exact_top_level_fields",
                    "nested_exact_field_sets",
                    "path_pattern",
                    "rules",
                    "schema_version",
                    "status_value",
                    "topology",
                }
            ),
            "Q contract",
        ),
        (
            r,
            frozenset(
                {
                    "counters",
                    "effect_authorization_exact",
                    "exact_top_level_fields",
                    "fixed_values",
                    "nested_exact_field_sets",
                    "path_pattern",
                    "schema_version",
                    "topology",
                    "verdict_value",
                }
            ),
            "R contract",
        ),
        (
            b,
            frozenset(
                {
                    "bootstrap_order",
                    "cli_operations",
                    "exact_top_level_fields",
                    "fixed_values",
                    "nested_exact_field_sets",
                    "path_pattern",
                    "schema_version",
                    "topology",
                }
            ),
            "B contract",
        ),
        (
            claim,
            frozenset(
                {
                    "claim_name",
                    "crash_states",
                    "linearization",
                    "outcomes",
                    "started_exact_fields",
                    "started_schema_version",
                    "terminal_exact_fields",
                    "terminal_schema_version",
                }
            ),
            "claim contract",
        ),
        (
            receipt,
            frozenset({"exact_fields", "filename_pattern", "rules", "schema_version"}),
            "mirror receipt contract",
        ),
        (
            anchors,
            frozenset(
                {
                    "capability_required_values",
                    "historical_selected_anchor",
                    "hook_disposition_authority",
                    "live_execution_anchor",
                    "mode_enum",
                    "no_fallback",
                    "recovered_publication_capability_exact_fields",
                    "release_truth_condition",
                }
            ),
            "dual-anchor contract",
        ),
        (
            census,
            frozenset(
                {
                    "baseline_and_start_contract",
                    "census_exact_fields",
                    "census_schema_version",
                    "fixed_carry_forward_row_fields",
                    "fixed_carry_forward_rows",
                    "projection_criteria",
                    "roles",
                    "row_exact_fields",
                    "rules",
                    "scanner_role_map",
                    "timing",
                    "touch_exact_fields",
                }
            ),
            "census contract",
        ),
        (
            protected,
            frozenset(
                {
                    "consume_mode_effects",
                    "container_rules",
                    "forbidden_calls",
                    "permitted_recovery_writes",
                    "read_only_inputs",
                    "recovery_containers",
                    "sealed_input_invariance",
                }
            ),
            "effect contract",
        ),
        (
            legacy,
            frozenset(
                {
                    "amendment_time_facts_permanently_false",
                    "disclosure_rule",
                    "epoch_table",
                    "locks",
                }
            ),
            "legacy contract",
        ),
    ):
        _require_exact_keys(node, expected, label)
    for observed, expected_order, label in (
        (
            preflight.get("exact_top_level_fields"),
            EPOCH_8_READ_ONLY_PREFLIGHT_FIELD_ORDER,
            "preflight exact fields",
        ),
        (q.get("exact_top_level_fields"), RECOVERY_REVIEW_REQUEST_FIELD_ORDER, "Q exact fields"),
        (r.get("exact_top_level_fields"), RECOVERY_AUTHORIZATION_FIELD_ORDER, "R exact fields"),
        (b.get("exact_top_level_fields"), RECOVERY_OWNER_BINDING_FIELD_ORDER, "B exact fields"),
        (claim.get("started_exact_fields"), RECOVERY_STARTED_FIELD_ORDER, "claim started fields"),
        (
            claim.get("terminal_exact_fields"),
            RECOVERY_TERMINAL_FIELD_ORDER,
            "claim terminal fields",
        ),
        (receipt.get("exact_fields"), RECOVERY_MIRROR_RECEIPT_FIELD_ORDER, "receipt fields"),
        (census.get("census_exact_fields"), REAL_LINEAGE_CENSUS_FIELD_ORDER, "census fields"),
        (census.get("row_exact_fields"), REAL_LINEAGE_ROW_FIELD_ORDER, "census row fields"),
        (census.get("touch_exact_fields"), REAL_LINEAGE_TOUCH_FIELD_ORDER, "census touch fields"),
    ):
        values = tuple(cast(Sequence[object], observed))
        if any(not isinstance(value, str) for value in values) or len(values) != len(set(values)):
            raise RehearsalV22ValidationError(f"{label} are not unique strings")
        _require_equal(values, expected_order, label)
    _require_equal(
        preflight.get("schema_version"),
        EPOCH_8_READ_ONLY_PREFLIGHT_SCHEMA,
        "preflight schema",
    )
    _require_equal(
        q.get("schema_version"),
        EPOCH_8_RECOVERY_REVIEW_REQUEST_SCHEMA,
        "Q schema",
    )
    if (
        r.get("schema_version") != EPOCH_8_RECOVERY_AUTHORIZATION_SCHEMA
        or b.get("schema_version") != EPOCH_8_RECOVERY_OWNER_BINDING_SCHEMA
        or claim.get("started_schema_version") != EPOCH_8_RECOVERY_STARTED_SCHEMA
        or claim.get("terminal_schema_version") != EPOCH_8_RECOVERY_TERMINAL_SCHEMA
        or receipt.get("schema_version") != EPOCH_8_RECOVERY_MIRROR_RECEIPT_SCHEMA
    ):
        raise RehearsalV22ValidationError("epoch-8 R/B/claim/receipt v1 schemas drifted")
    _require_equal(
        tuple(anchors.get("recovered_publication_capability_exact_fields", ())),
        RECOVERED_PUBLICATION_CAPABILITY_FIELDS,
        "recovered-publication capability fields",
    )
    _require_equal(
        tuple(anchors.get("mode_enum", ())),
        tuple(mode.value for mode in BundleValidationMode),
        "bundle validation modes",
    )
    historical = _object(anchors.get("historical_selected_anchor"), "historical anchor")
    live = _object(anchors.get("live_execution_anchor"), "live anchor")
    if (
        historical.get("implementation_epoch") != HISTORICAL_SELECTED_EPOCH
        or historical.get("implementation_commit") != HISTORICAL_SELECTED_IMPLEMENTATION_COMMIT
        or historical.get("selected_control_merkle_root_sha256")
        != HISTORICAL_SELECTED_CONTROL_ROOT_SHA256
        or historical.get("require_current") is not False
        or live.get("implementation_epoch")
        != HISTORICAL_EPOCH_8_RECOVERY_GOVERNANCE_EPOCH
        or live.get("require_current") is not True
    ):
        raise RehearsalV22ValidationError("epoch-8 dual-byte anchors drifted")
    _require_equal(
        anchors.get("capability_required_values"),
        {
            "selected_attempt_ordinal": 2,
            "selected_implementation_epoch": HISTORICAL_SELECTED_EPOCH,
            "execution_epoch": HISTORICAL_EPOCH_8_RECOVERY_GOVERNANCE_EPOCH,
            "recovery_starts": 1,
            "pipeline_starts": 0,
            "automatic_retry_count": 0,
            "sealed_ledger_before_after_equal": True,
            "sealed_mirror_before_after_equal": True,
            "historical_run_roots_equal": SEALED_SELECTED_RUN_ROOT_SHA256,
            "historical_full_downstream_replay_verified": True,
        },
        "epoch-8 recovered-publication capability values",
    )
    _require_equal(
        tuple(census.get("roles", ())),
        tuple(role.value for role in AuthorityCensusRole),
        "authority census roles",
    )
    _require_equal(
        tuple(legacy.get("amendment_time_facts_permanently_false", ())),
        LEGACY_AMENDMENT_ABSENCE_FIELDS,
        "legacy amendment absence fields",
    )
    _require_equal(
        tuple(protected.get("recovery_containers", ())),
        (
            SERIES_2_PRIMARY_RECOVERY_CONTAINER.as_posix(),
            SERIES_2_SECONDARY_RECOVERY_CONTAINER.as_posix(),
        ),
        "registered recovery containers",
    )
    _require_equal(
        r.get("counters"),
        {
            "authorized_bundle_recovery_starts": 1,
            "authorized_pipeline_starts": 0,
            "automatic_retry_count": 0,
        },
        "recovery authorization counters",
    )
    fixed_r = _object(r.get("fixed_values"), "epoch-8 R fixed values")
    if (
        fixed_r.get("execution_epoch")
        != HISTORICAL_EPOCH_8_RECOVERY_GOVERNANCE_EPOCH
        or fixed_r.get("selected_implementation_epoch") != HISTORICAL_SELECTED_EPOCH
        or fixed_r.get("selected_implementation_commit")
        != HISTORICAL_SELECTED_IMPLEMENTATION_COMMIT
        or fixed_r.get("history_root_sha256") != SEALED_SERIES_HISTORY_ROOT_SHA256
        or fixed_r.get("live_ledger_root_sha256") != SEALED_SERIES_LIVE_ROOT_SHA256
    ):
        raise RehearsalV22ValidationError("epoch-8 R fixed values drifted")
    expected_carry_forward = [
        {
            "path": EPOCH_7_LIVE_REVIEW_RELATIVE.as_posix(),
            "sha256": EPOCH_7_LIVE_REVIEW_SHA256,
            "bytes": EPOCH_7_LIVE_REVIEW_BYTES,
            "creating_commit": EPOCH_7_LIVE_REVIEW_LANDING_COMMIT,
            "role": AuthorityCensusRole.PINNED_LANDING_PROJECTION.value,
            "declared_landing_projection_commit": EPOCH_7_LIVE_REVIEW_LANDING_COMMIT,
        },
        {
            "path": EPOCH_7_LIVE_LANDING_RELATIVE.as_posix(),
            "sha256": EPOCH_7_LIVE_LANDING_SHA256,
            "bytes": EPOCH_7_LIVE_LANDING_BYTES,
            "creating_commit": EPOCH_7_LIVE_LANDING_COMMIT,
            "role": AuthorityCensusRole.PINNED_SOURCE.value,
            "declared_landing_projection_commit": None,
        },
        {
            "path": REFUSED_RECOVERY_Q_RELATIVE.as_posix(),
            "sha256": REFUSED_RECOVERY_Q_SHA256,
            "bytes": REFUSED_RECOVERY_Q_BYTES,
            "creating_commit": REFUSED_RECOVERY_Q_COMMIT,
            "role": AuthorityCensusRole.PINNED_SOURCE.value,
            "declared_landing_projection_commit": None,
        },
        {
            "path": REFUSED_RECOVERY_R_RELATIVE.as_posix(),
            "sha256": REFUSED_RECOVERY_R_SHA256,
            "bytes": REFUSED_RECOVERY_R_BYTES,
            "creating_commit": REFUSED_RECOVERY_R_COMMIT,
            "role": AuthorityCensusRole.PINNED_SOURCE.value,
            "declared_landing_projection_commit": None,
        },
        {
            "path": REFUSED_RECOVERY_B_RELATIVE.as_posix(),
            "sha256": REFUSED_RECOVERY_B_SHA256,
            "bytes": REFUSED_RECOVERY_B_BYTES,
            "creating_commit": REFUSED_RECOVERY_B_COMMIT,
            "role": AuthorityCensusRole.PINNED_SOURCE.value,
            "declared_landing_projection_commit": None,
        },
        {
            "path": EPOCH_8_ADJUDICATION_RELATIVE.as_posix(),
            "sha256": EPOCH_8_ADJUDICATION_SHA256,
            "bytes": EPOCH_8_ADJUDICATION_BYTES,
            "creating_commit": EPOCH_8_ADJUDICATION_COMMIT,
            "role": AuthorityCensusRole.PINNED_SOURCE.value,
            "declared_landing_projection_commit": None,
        },
    ]
    _require_equal(
        census.get("fixed_carry_forward_rows"),
        expected_carry_forward,
        "epoch-8 fixed census carry-forward",
    )
    _validate_epoch_8_fixed_carry_forward_registry(
        census.get("fixed_carry_forward_rows")
    )
    _require_equal(
        tuple(census.get("fixed_carry_forward_row_fields", ())),
        (
            "path",
            "sha256",
            "bytes",
            "creating_commit",
            "role",
            "declared_landing_projection_commit",
        ),
        "epoch-8 fixed census row fields",
    )
    if "fresh epoch-8 current census" not in _string(census.get("timing"), "census timing"):
        raise RehearsalV22ValidationError("recovered-release fresh census requirement drifted")
    tracker.snapshot()
    return contract


def _validate_epoch_9_latest_landed_contract_semantics(
    contract: Mapping[str, Any],
    authority: Mapping[str, Any],
) -> None:
    """Recompute every registered epoch-9 overlay predicate from raw fields."""

    _require_exact_keys(
        contract,
        EPOCH_9_LATEST_LANDED_CONTRACT_FIELDS,
        "epoch-9 latest-landed execution contract",
    )
    nodes: dict[str, JsonObject] = {}
    for name, exact_fields in EPOCH_9_LATEST_LANDED_NESTED_FIELDS.items():
        node = _object(contract.get(name), f"epoch-9 contract {name}")
        _require_exact_keys(node, exact_fields, f"epoch-9 contract {name}")
        nodes[name] = node

    governing = nodes["governing_adjudication"]
    superseded = nodes["superseded_two_file_authority"]
    historical_contract = nodes["historical_epoch_8_recovery_contract"]
    latest = nodes["latest_landed_authority_contract"]
    dispatch = nodes["registered_preflight_dispatch_contract"]
    anchors = nodes["dual_byte_anchor_transition_contract"]
    historical = _object(anchors.get("historical_selected_anchor"), "epoch-9 historical anchor")
    live = _object(anchors.get("live_execution_anchor"), "epoch-9 live anchor")
    _require_exact_keys(
        historical,
        frozenset(
            {
                "selected_attempt_ordinal",
                "implementation_epoch",
                "implementation_commit",
                "history_root_sha256",
                "live_ledger_root_sha256",
                "require_current",
            }
        ),
        "epoch-9 historical anchor",
    )
    _require_exact_keys(
        live,
        frozenset(
            {
                "implementation_epoch",
                "require_current",
                "required_current_surfaces",
                "required_lineage_objects",
                "runtime_value_source",
            }
        ),
        "epoch-9 live anchor",
    )
    _require_equal(
        governing,
        {
            "path": EPOCH_9_ADJUDICATION_RELATIVE.as_posix(),
            "sha256": EPOCH_9_ADJUDICATION_SHA256,
            "bytes": EPOCH_9_ADJUDICATION_BYTES,
            "creating_commit": EPOCH_9_ADJUDICATION_COMMIT,
            "unique_a_history_verified": True,
        },
        "epoch-9 governing adjudication",
    )
    _require_equal(
        superseded,
        {
            "path": EPOCH_9_SUPERSEDED_SURFACE_AUTHORITY_RELATIVE.as_posix(),
            "sha256": EPOCH_9_SUPERSEDED_SURFACE_AUTHORITY_SHA256,
            "bytes": EPOCH_9_SUPERSEDED_SURFACE_AUTHORITY_BYTES,
            "creating_commit": EPOCH_9_SUPERSEDED_SURFACE_AUTHORITY_COMMIT,
            "disposition": "SUPERSEDED_FOR_IMPLEMENTATION_ONLY",
        },
        "epoch-9 superseded two-file authority",
    )
    _require_equal(
        historical_contract,
        {
            "companion_path": EPOCH_8_COMPANION_RELATIVE.as_posix(),
            "companion_sha256": EPOCH_8_COMPANION_SHA256,
            "companion_bytes": EPOCH_8_COMPANION_BYTES,
            "companion_creating_commit": EPOCH_8_COMPANION_COMMIT,
            "contract_schema_version": EPOCH_8_RECOVERY_CONTRACT_SCHEMA,
            "contract_canonical_sha256": EPOCH_8_CONTRACT_CANONICAL_SHA256,
            "preservation": "IMMUTABLE_HISTORICAL_CONTRACT_BYTE_FOR_BYTE",
        },
        "epoch-9 historical epoch-8 contract reference",
    )
    _require_equal(
        tuple(_array(latest.get("landing_document_required_fields"), "landing fields")),
        EPOCH_9_LANDING_DOCUMENT_REQUIRED_FIELDS,
        "epoch-9 landing required fields",
    )
    _require_equal(
        tuple(_array(latest.get("topology_requirements"), "topology requirements")),
        EPOCH_9_LATEST_TOPOLOGY_REQUIREMENTS,
        "epoch-9 topology requirements",
    )
    _require_equal(
        tuple(_array(latest.get("runtime_binding_chain"), "runtime binding chain")),
        EPOCH_9_RUNTIME_BINDING_CHAIN,
        "epoch-9 runtime binding chain",
    )
    if (
        latest.get("expected_implementation_epoch") != LATEST_LANDED_EXECUTION_EPOCH
        or latest.get("owner_identity_mode")
        != "LOGICAL_SOURCE_WITH_LAWFUL_PROJECTIONS"
        or latest.get("review_identity_mode") != "FIRST_PARENT_VISIBLE_UNIQUE_A"
        or latest.get("landing_identity_mode") != "FIRST_PARENT_VISIBLE_UNIQUE_A"
        or latest.get("unknown_values_policy") != EPOCH_9_UNKNOWN_VALUES_POLICY
    ):
        raise RehearsalV22ValidationError("epoch-9 latest authority semantics drifted")
    _require_equal(
        tuple(_array(dispatch.get("runtime_binding_order"), "preflight runtime order")),
        EPOCH_9_PREFLIGHT_RUNTIME_BINDING_ORDER,
        "epoch-9 preflight runtime order",
    )
    if (
        dispatch.get("landing_preflight_origin_epoch") != LANDING_PREFLIGHT_ORIGIN_EPOCH
        or dispatch.get("latest_official_epoch") != LATEST_LANDED_EXECUTION_EPOCH
        or dispatch.get("historical_epoch_8_policy") != EPOCH_9_HISTORICAL_EPOCH_8_POLICY
        or dispatch.get("unknown_later_epoch_policy")
        != "FAIL_CLOSED_UNTIL_SEPARATELY_GOVERNED"
        or dispatch.get("output_schema_version") != SERIES_2_READ_ONLY_PREFLIGHT_SCHEMA
        or dispatch.get("zero_effect_required") is not True
    ):
        raise RehearsalV22ValidationError("epoch-9 preflight dispatch semantics drifted")
    _require_equal(
        historical,
        {
            "selected_attempt_ordinal": 2,
            "implementation_epoch": HISTORICAL_SELECTED_EPOCH,
            "implementation_commit": HISTORICAL_SELECTED_IMPLEMENTATION_COMMIT,
            "history_root_sha256": SEALED_SERIES_HISTORY_ROOT_SHA256,
            "live_ledger_root_sha256": SEALED_SERIES_LIVE_ROOT_SHA256,
            "require_current": False,
        },
        "epoch-9 historical selected anchor",
    )
    _require_equal(
        tuple(_array(live.get("required_current_surfaces"), "live current surfaces")),
        EPOCH_9_REQUIRED_CURRENT_SURFACES,
        "epoch-9 live current surfaces",
    )
    _require_equal(
        tuple(_array(live.get("required_lineage_objects"), "live lineage objects")),
        EPOCH_9_REQUIRED_LINEAGE_OBJECTS,
        "epoch-9 live lineage objects",
    )
    if (
        live.get("implementation_epoch") != LATEST_LANDED_EXECUTION_EPOCH
        or live.get("require_current") is not True
        or live.get("runtime_value_source") != EPOCH_9_LIVE_RUNTIME_VALUE_SOURCE
        or anchors.get("sealed_attempt_epoch_table") != [5, 6]
    ):
        raise RehearsalV22ValidationError("epoch-9 live or sealed anchor semantics drifted")

    qrb = nodes["recovery_qrb_live_binding_contract"]
    qrb_counters = _object(qrb.get("counters"), "epoch-9 Q/R/B counters")
    _require_exact_keys(
        qrb_counters,
        frozenset(
            {
                "authorized_bundle_recovery_starts_after_fresh_Q_R_B",
                "authorized_pipeline_starts",
                "automatic_retry_count",
            }
        ),
        "epoch-9 Q/R/B counters",
    )
    for observed, expected, label in (
        (qrb.get("q_top_level_fields"), RECOVERY_REVIEW_REQUEST_FIELD_ORDER, "Q fields"),
        (qrb.get("r_top_level_fields"), RECOVERY_AUTHORIZATION_FIELD_ORDER, "R fields"),
        (qrb.get("b_top_level_fields"), RECOVERY_OWNER_BINDING_FIELD_ORDER, "B fields"),
        (
            qrb.get("runtime_live_binding_fields"),
            EPOCH_9_QRB_RUNTIME_LIVE_BINDING_FIELDS,
            "Q/R/B live binding fields",
        ),
    ):
        _require_equal(tuple(_array(observed, label)), expected, f"epoch-9 {label}")
    if (
        qrb.get("q_schema") != SERIES_2_RECOVERY_REVIEW_REQUEST_SCHEMA
        or qrb.get("r_schema") != SERIES_2_RECOVERY_AUTHORIZATION_SCHEMA
        or qrb.get("b_schema") != SERIES_2_RECOVERY_OWNER_BINDING_SCHEMA
        or qrb.get("cross_epoch_policy") != EPOCH_9_QRB_CROSS_EPOCH_POLICY
        or qrb_counters
        != {
            "authorized_bundle_recovery_starts_after_fresh_Q_R_B": 1,
            "authorized_pipeline_starts": 0,
            "automatic_retry_count": 0,
        }
    ):
        raise RehearsalV22ValidationError("epoch-9 Q/R/B semantics drifted")

    claim = nodes["recovery_claim_and_receipt_live_binding_contract"]
    for observed, expected, label in (
        (claim.get("started_top_level_fields"), RECOVERY_STARTED_FIELD_ORDER, "started fields"),
        (claim.get("terminal_top_level_fields"), RECOVERY_TERMINAL_FIELD_ORDER, "terminal fields"),
        (
            claim.get("mirror_receipt_top_level_fields"),
            RECOVERY_MIRROR_RECEIPT_FIELD_ORDER,
            "mirror receipt fields",
        ),
        (claim.get("live_binding_rules"), EPOCH_9_CLAIM_LIVE_BINDING_RULES, "claim rules"),
    ):
        _require_equal(tuple(_array(observed, label)), expected, f"epoch-9 {label}")
    if (
        claim.get("started_schema") != SERIES_2_RECOVERY_STARTED_SCHEMA
        or claim.get("terminal_schema") != SERIES_2_RECOVERY_TERMINAL_SCHEMA
        or claim.get("mirror_receipt_schema") != SERIES_2_RECOVERY_MIRROR_RECEIPT_SCHEMA
    ):
        raise RehearsalV22ValidationError("epoch-9 claim or receipt schemas drifted")

    publication = nodes["recovered_publication_and_release_live_binding_contract"]
    zero_effects = _object(publication.get("zero_effect_rule"), "epoch-9 zero effects")
    _require_exact_keys(
        zero_effects,
        frozenset(
            {
                "filesystem_and_git_writes",
                "ledger_and_mirror_writes",
                "pipeline_starts",
                "automatic_retries",
                "model_network_database_and_heldout_accesses",
                "trading_effects",
            }
        ),
        "epoch-9 zero effects",
    )
    _require_equal(
        tuple(
            _array(
                publication.get("capability_top_level_fields"),
                "recovered-publication capability fields",
            )
        ),
        RECOVERED_PUBLICATION_CAPABILITY_FIELDS,
        "epoch-9 recovered-publication capability fields",
    )
    if (
        publication.get("historical_binding_rule")
        != EPOCH_9_PUBLICATION_HISTORICAL_BINDING_RULE
        or publication.get("live_binding_rule") != EPOCH_9_PUBLICATION_LIVE_BINDING_RULE
        or publication.get("bundle_mode") != BundleValidationMode.PASSIVE_RECOVERED_BUNDLE.value
        or publication.get("release_mode") != BundleValidationMode.PASSIVE_RECOVERED_RELEASE.value
        or publication.get("active_fallback_forbidden") is not True
        or any(value != 0 for value in zero_effects.values())
    ):
        raise RehearsalV22ValidationError("epoch-9 publication semantics drifted")

    census = nodes["authority_census_and_effect_lock_contract"]
    fixed_rows = _array(census.get("fixed_base_governance_rows"), "fixed census rows")
    observed_fixed_rows: list[tuple[str, str]] = []
    for row_value in fixed_rows:
        row = _object(row_value, "fixed census row")
        _require_exact_keys(row, frozenset({"identity", "binding_rule"}), "fixed census row")
        observed_fixed_rows.append(
            (
                _string(row.get("identity"), "fixed census identity"),
                _string(row.get("binding_rule"), "fixed census binding rule"),
            )
        )
    _require_equal(
        tuple(observed_fixed_rows),
        EPOCH_9_CENSUS_FIXED_BASE_ROWS,
        "epoch-9 fixed census rows",
    )
    _require_equal(
        tuple(_array(census.get("dynamic_runtime_rows"), "dynamic census rows")),
        EPOCH_9_CENSUS_DYNAMIC_RUNTIME_ROWS,
        "epoch-9 dynamic census rows",
    )
    permanent_absence = _object(
        census.get("permanent_absence_fields"),
        "epoch-9 permanent absence fields",
    )
    effect_limits = _object(census.get("effect_limits"), "epoch-9 effect limits")
    locks = _object(census.get("lock_values"), "epoch-9 lock values")
    _require_exact_keys(
        permanent_absence,
        frozenset(LEGACY_AMENDMENT_ABSENCE_FIELDS),
        "epoch-9 permanent absence fields",
    )
    _require_exact_keys(
        effect_limits,
        frozenset(
            {
                "future_recovery_starts_after_separately_authorized_fresh_Q_R_B",
                "pipeline_starts",
                "automatic_retries",
                "preflight_Q_drafting_validation_release_revalidation_and_review_writes",
            }
        ),
        "epoch-9 effect limits",
    )
    _require_exact_keys(
        locks,
        frozenset(
            {
                "ordinal_3_forbidden",
                "synthetic_attempt_rows_for_epochs_7_8_9_forbidden",
                "ledger_and_sealed_mirror_read_only",
                "p4_2a_done",
                "p4_2b_unlocked",
                "p4_3_unlocked",
                "heldout_materialization_inference_evaluation_locked",
                "non_simulate_trading_locked",
                "active_replay_fallback_forbidden",
            }
        ),
        "epoch-9 lock values",
    )
    _require_equal(
        permanent_absence,
        {name: False for name in LEGACY_AMENDMENT_ABSENCE_FIELDS},
        "epoch-9 permanent absence values",
    )
    _require_equal(
        effect_limits,
        {
            "future_recovery_starts_after_separately_authorized_fresh_Q_R_B": 1,
            "pipeline_starts": 0,
            "automatic_retries": 0,
            "preflight_Q_drafting_validation_release_revalidation_and_review_writes": 0,
        },
        "epoch-9 effect limits",
    )
    _require_equal(
        locks,
        {
            "ordinal_3_forbidden": True,
            "synthetic_attempt_rows_for_epochs_7_8_9_forbidden": True,
            "ledger_and_sealed_mirror_read_only": True,
            "p4_2a_done": False,
            "p4_2b_unlocked": False,
            "p4_3_unlocked": False,
            "heldout_materialization_inference_evaluation_locked": True,
            "non_simulate_trading_locked": True,
            "active_replay_fallback_forbidden": True,
        },
        "epoch-9 lock values",
    )
    if census.get("sealed_attempt_epochs") != [5, 6]:
        raise RehearsalV22ValidationError("epoch-9 sealed attempt epochs drifted")

    _require_exact_keys(
        authority,
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
        "epoch-9 four-file surface authority",
    )
    _require_equal(
        authority,
        {
            "schema_version": "p4.2a-v2-2-implementation-epoch-surface-authorization-v1",
            "verdict": "APPROVE_EXACT_V2_2_IMPLEMENTATION_EPOCH_SURFACE",
            "owner": {"identity": "ouyang", "approved": True},
            "implementation_epoch": LATEST_LANDED_EXECUTION_EPOCH,
            "base_commit": EPOCH_9_COMPANION_COMMIT,
            "exact_surface": [
                {"path": "scripts/p4_2a_v2_2_heldout_rehearsal.py", "status": "M"},
                {
                    "path": "scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py",
                    "status": "M",
                },
                {"path": "tests/test_p4_2a_v2_2_heldout_rehearsal_runner.py", "status": "M"},
                {
                    "path": "tests/test_p4_2a_v2_2_heldout_rehearsal_validator.py",
                    "status": "M",
                },
            ],
        },
        "epoch-9 four-file surface authority",
    )


def validate_epoch_9_latest_landed_execution_contract(
    project_root: Path,
    *,
    execution_head: str | None = None,
) -> JsonObject:
    """Independently validate the epoch-9 12-field live overlay.

    The epoch-8 contract remains the immutable recovery-history authority.  This
    overlay contributes only the latest-landed execution identity used by
    preflight, Q/R/B, recovery publication, and recovered-release validation.
    """

    root = project_root.resolve(strict=True)
    tracker = _IndependentRecoveryWorkTracker()
    all_ref_commits = _git_all_ref_commits(root, work_tracker=tracker)
    validate_epoch_8_recovery_contract(
        root,
        execution_head=execution_head,
        work_tracker=tracker,
        all_ref_commits=all_ref_commits,
    )
    if execution_head is not None:
        head = _git_commit(
            root,
            execution_head,
            "epoch-9 contract execution HEAD",
            work_tracker=tracker,
        )
        for commit in (
            EPOCH_9_DESIGN_R3_COMMIT,
            EPOCH_9_DESIGN_REVIEW_R3_COMMIT,
            EPOCH_9_ADJUDICATION_COMMIT,
            EPOCH_9_COMPANION_COMMIT,
            EPOCH_9_SURFACE_AUTHORITY_COMMIT,
        ):
            if not _git_is_ancestor(root, commit, head, work_tracker=tracker):
                raise RehearsalV22ValidationError(
                    "epoch-9 governance is outside the requested execution HEAD"
                )

    for relative, digest, expected_bytes, commit, label in (
        (
            EPOCH_9_DESIGN_R3_RELATIVE,
            EPOCH_9_DESIGN_R3_SHA256,
            EPOCH_9_DESIGN_R3_BYTES,
            EPOCH_9_DESIGN_R3_COMMIT,
            "epoch-9 design r3",
        ),
        (
            EPOCH_9_DESIGN_REVIEW_R3_RELATIVE,
            EPOCH_9_DESIGN_REVIEW_R3_SHA256,
            EPOCH_9_DESIGN_REVIEW_R3_BYTES,
            EPOCH_9_DESIGN_REVIEW_R3_COMMIT,
            "epoch-9 design review r3",
        ),
        (
            EPOCH_9_ADJUDICATION_RELATIVE,
            EPOCH_9_ADJUDICATION_SHA256,
            EPOCH_9_ADJUDICATION_BYTES,
            EPOCH_9_ADJUDICATION_COMMIT,
            "epoch-9 governing adjudication",
        ),
    ):
        _epoch_8_governance_payload(
            root,
            relative=relative,
            digest=digest,
            expected_bytes=expected_bytes,
            creating_commit=commit,
            label=label,
            work_tracker=tracker,
            all_ref_commits=all_ref_commits,
        )
    if (
        _git_parents(root, EPOCH_9_DESIGN_R1_COMMIT, work_tracker=tracker)
        != ("87c73ce601a84779295f4ee5e82b14822ea93f42",)
        or _git_parents(root, EPOCH_9_DESIGN_R2_COMMIT, work_tracker=tracker)
        != (EPOCH_9_DESIGN_R1_COMMIT,)
        or _git_parents(root, EPOCH_9_DESIGN_R3_COMMIT, work_tracker=tracker)
        != (EPOCH_9_SUPERSEDED_SURFACE_AUTHORITY_COMMIT,)
        or _git_parents(root, EPOCH_9_DESIGN_REVIEW_R3_COMMIT, work_tracker=tracker)
        != (EPOCH_9_DESIGN_R3_COMMIT,)
        or _git_parents(root, EPOCH_9_ADJUDICATION_COMMIT, work_tracker=tracker)
        != (EPOCH_9_DESIGN_REVIEW_R3_COMMIT,)
        or _git_parents(root, EPOCH_9_COMPANION_COMMIT, work_tracker=tracker)
        != (EPOCH_9_ADJUDICATION_COMMIT,)
        or _git_parents(root, EPOCH_9_SURFACE_AUTHORITY_COMMIT, work_tracker=tracker)
        != (EPOCH_9_COMPANION_COMMIT,)
    ):
        raise RehearsalV22ValidationError("epoch-9 governance parent chain drifted")

    companion_payload = _epoch_8_governance_payload(
        root,
        relative=EPOCH_9_COMPANION_RELATIVE,
        digest=EPOCH_9_COMPANION_SHA256,
        expected_bytes=EPOCH_9_COMPANION_BYTES,
        creating_commit=EPOCH_9_COMPANION_COMMIT,
        label="epoch-9 latest-landed companion",
        work_tracker=tracker,
        all_ref_commits=all_ref_commits,
    )
    companion = _object(
        strict_json_loads(companion_payload, label="epoch-9 latest-landed companion"),
        "epoch-9 latest-landed companion",
    )
    review_document = _object(
        strict_json_loads(
            _regular_bytes(
                root / EPOCH_9_DESIGN_REVIEW_R3_RELATIVE,
                "epoch-9 design review r3",
            ),
            label="epoch-9 design review r3",
        ),
        "epoch-9 design review r3",
    )
    adjudication_document = _object(
        strict_json_loads(
            _regular_bytes(
                root / EPOCH_9_ADJUDICATION_RELATIVE,
                "epoch-9 governing adjudication",
            ),
            label="epoch-9 governing adjudication",
        ),
        "epoch-9 governing adjudication",
    )
    contract = _object(
        companion.get("epoch_9_latest_landed_execution_contract"),
        "epoch-9 latest-landed execution contract",
    )
    _require_exact_keys(
        contract,
        EPOCH_9_LATEST_LANDED_CONTRACT_FIELDS,
        "epoch-9 latest-landed execution contract",
    )
    if (
        _sha256(_canonical_json_bytes(contract))
        != EPOCH_9_LATEST_LANDED_CONTRACT_CANONICAL_SHA256
        or review_document.get("verdict") != "PASS_INCREMENTAL_EPOCH9_R3_DESIGN_REVIEW"
        or adjudication_document.get("verdict")
        != "APPROVE_EPOCH9_R3_DESIGN_AND_GOVERNANCE_PREPARATION"
        or companion.get("verdict")
        != "EPOCH9_R3_COMPANION_READY_FOR_OWNER_APPROVED_FOUR_FILE_SURFACE_AUTHORITY"
        or contract.get("schema_version") != EPOCH_9_LATEST_LANDED_CONTRACT_SCHEMA
        or contract.get("implementation_epoch") != LATEST_LANDED_EXECUTION_EPOCH
    ):
        raise RehearsalV22ValidationError("epoch-9 latest-landed contract identity drifted")
    nodes: dict[str, JsonObject] = {}
    for name, exact_fields in EPOCH_9_LATEST_LANDED_NESTED_FIELDS.items():
        node = _object(contract.get(name), f"epoch-9 contract {name}")
        _require_exact_keys(node, exact_fields, f"epoch-9 contract {name}")
        nodes[name] = node

    _require_equal(
        nodes["governing_adjudication"],
        {
            "path": EPOCH_9_ADJUDICATION_RELATIVE.as_posix(),
            "sha256": EPOCH_9_ADJUDICATION_SHA256,
            "bytes": EPOCH_9_ADJUDICATION_BYTES,
            "creating_commit": EPOCH_9_ADJUDICATION_COMMIT,
            "unique_a_history_verified": True,
        },
        "epoch-9 governing adjudication",
    )
    _require_equal(
        nodes["superseded_two_file_authority"],
        {
            "path": EPOCH_9_SUPERSEDED_SURFACE_AUTHORITY_RELATIVE.as_posix(),
            "sha256": EPOCH_9_SUPERSEDED_SURFACE_AUTHORITY_SHA256,
            "bytes": EPOCH_9_SUPERSEDED_SURFACE_AUTHORITY_BYTES,
            "creating_commit": EPOCH_9_SUPERSEDED_SURFACE_AUTHORITY_COMMIT,
            "disposition": "SUPERSEDED_FOR_IMPLEMENTATION_ONLY",
        },
        "epoch-9 superseded two-file authority",
    )
    _require_equal(
        nodes["historical_epoch_8_recovery_contract"],
        {
            "companion_path": EPOCH_8_COMPANION_RELATIVE.as_posix(),
            "companion_sha256": EPOCH_8_COMPANION_SHA256,
            "companion_bytes": EPOCH_8_COMPANION_BYTES,
            "companion_creating_commit": EPOCH_8_COMPANION_COMMIT,
            "contract_schema_version": EPOCH_8_RECOVERY_CONTRACT_SCHEMA,
            "contract_canonical_sha256": EPOCH_8_CONTRACT_CANONICAL_SHA256,
            "preservation": "IMMUTABLE_HISTORICAL_CONTRACT_BYTE_FOR_BYTE",
        },
        "epoch-9 historical epoch-8 contract reference",
    )

    authority_payload = _epoch_8_governance_payload(
        root,
        relative=EPOCH_9_SURFACE_AUTHORITY_RELATIVE,
        digest=EPOCH_9_SURFACE_AUTHORITY_SHA256,
        expected_bytes=EPOCH_9_SURFACE_AUTHORITY_BYTES,
        creating_commit=EPOCH_9_SURFACE_AUTHORITY_COMMIT,
        label="epoch-9 four-file surface authority",
        work_tracker=tracker,
        all_ref_commits=all_ref_commits,
    )
    authority = _object(
        strict_json_loads(authority_payload, label="epoch-9 four-file surface authority"),
        "epoch-9 four-file surface authority",
    )
    _validate_epoch_9_latest_landed_contract_semantics(contract, authority)
    expected_surface = [
        {"path": "scripts/p4_2a_v2_2_heldout_rehearsal.py", "status": "M"},
        {"path": "scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py", "status": "M"},
        {"path": "tests/test_p4_2a_v2_2_heldout_rehearsal_runner.py", "status": "M"},
        {"path": "tests/test_p4_2a_v2_2_heldout_rehearsal_validator.py", "status": "M"},
    ]
    _require_equal(
        authority,
        {
            "schema_version": "p4.2a-v2-2-implementation-epoch-surface-authorization-v1",
            "verdict": "APPROVE_EXACT_V2_2_IMPLEMENTATION_EPOCH_SURFACE",
            "owner": {"identity": "ouyang", "approved": True},
            "implementation_epoch": LATEST_LANDED_EXECUTION_EPOCH,
            "base_commit": EPOCH_9_COMPANION_COMMIT,
            "exact_surface": expected_surface,
        },
        "epoch-9 four-file surface authority",
    )

    latest = nodes["latest_landed_authority_contract"]
    dispatch = nodes["registered_preflight_dispatch_contract"]
    anchors = nodes["dual_byte_anchor_transition_contract"]
    historical = _object(anchors.get("historical_selected_anchor"), "epoch-9 historical anchor")
    live = _object(anchors.get("live_execution_anchor"), "epoch-9 live anchor")
    _require_exact_keys(
        historical,
        frozenset(
            {
                "selected_attempt_ordinal",
                "implementation_epoch",
                "implementation_commit",
                "history_root_sha256",
                "live_ledger_root_sha256",
                "require_current",
            }
        ),
        "epoch-9 historical anchor",
    )
    _require_exact_keys(
        live,
        frozenset(
            {
                "implementation_epoch",
                "require_current",
                "required_current_surfaces",
                "required_lineage_objects",
                "runtime_value_source",
            }
        ),
        "epoch-9 live anchor",
    )
    if (
        latest.get("expected_implementation_epoch") != LATEST_LANDED_EXECUTION_EPOCH
        or latest.get("owner_identity_mode")
        != "LOGICAL_SOURCE_WITH_LAWFUL_PROJECTIONS"
        or latest.get("review_identity_mode") != "FIRST_PARENT_VISIBLE_UNIQUE_A"
        or latest.get("landing_identity_mode") != "FIRST_PARENT_VISIBLE_UNIQUE_A"
        or dispatch.get("landing_preflight_origin_epoch") != LANDING_PREFLIGHT_ORIGIN_EPOCH
        or dispatch.get("latest_official_epoch") != LATEST_LANDED_EXECUTION_EPOCH
        or dispatch.get("unknown_later_epoch_policy")
        != "FAIL_CLOSED_UNTIL_SEPARATELY_GOVERNED"
        or dispatch.get("output_schema_version") != SERIES_2_READ_ONLY_PREFLIGHT_SCHEMA
        or dispatch.get("zero_effect_required") is not True
        or historical
        != {
            "selected_attempt_ordinal": 2,
            "implementation_epoch": HISTORICAL_SELECTED_EPOCH,
            "implementation_commit": HISTORICAL_SELECTED_IMPLEMENTATION_COMMIT,
            "history_root_sha256": SEALED_SERIES_HISTORY_ROOT_SHA256,
            "live_ledger_root_sha256": SEALED_SERIES_LIVE_ROOT_SHA256,
            "require_current": False,
        }
        or live.get("implementation_epoch") != LATEST_LANDED_EXECUTION_EPOCH
        or live.get("require_current") is not True
        or anchors.get("sealed_attempt_epoch_table") != [5, 6]
    ):
        raise RehearsalV22ValidationError("epoch-9 dispatch or dual-anchor semantics drifted")

    qrb = nodes["recovery_qrb_live_binding_contract"]
    claim = nodes["recovery_claim_and_receipt_live_binding_contract"]
    publication = nodes["recovered_publication_and_release_live_binding_contract"]
    census_locks = nodes["authority_census_and_effect_lock_contract"]
    for observed, expected, label in (
        (qrb.get("q_top_level_fields"), RECOVERY_REVIEW_REQUEST_FIELD_ORDER, "epoch-9 Q fields"),
        (qrb.get("r_top_level_fields"), RECOVERY_AUTHORIZATION_FIELD_ORDER, "epoch-9 R fields"),
        (qrb.get("b_top_level_fields"), RECOVERY_OWNER_BINDING_FIELD_ORDER, "epoch-9 B fields"),
        (
            claim.get("started_top_level_fields"),
            RECOVERY_STARTED_FIELD_ORDER,
            "epoch-9 started fields",
        ),
        (
            claim.get("terminal_top_level_fields"),
            RECOVERY_TERMINAL_FIELD_ORDER,
            "epoch-9 terminal fields",
        ),
        (
            claim.get("mirror_receipt_top_level_fields"),
            RECOVERY_MIRROR_RECEIPT_FIELD_ORDER,
            "epoch-9 mirror receipt fields",
        ),
        (
            publication.get("capability_top_level_fields"),
            RECOVERED_PUBLICATION_CAPABILITY_FIELDS,
            "epoch-9 recovered-publication fields",
        ),
    ):
        _require_equal(tuple(_array(observed, label)), expected, label)
    if (
        qrb.get("q_schema") != SERIES_2_RECOVERY_REVIEW_REQUEST_SCHEMA
        or qrb.get("r_schema") != SERIES_2_RECOVERY_AUTHORIZATION_SCHEMA
        or qrb.get("b_schema") != SERIES_2_RECOVERY_OWNER_BINDING_SCHEMA
        or qrb.get("counters")
        != {
            "authorized_bundle_recovery_starts_after_fresh_Q_R_B": 1,
            "authorized_pipeline_starts": 0,
            "automatic_retry_count": 0,
        }
        or claim.get("started_schema") != SERIES_2_RECOVERY_STARTED_SCHEMA
        or claim.get("terminal_schema") != SERIES_2_RECOVERY_TERMINAL_SCHEMA
        or claim.get("mirror_receipt_schema") != SERIES_2_RECOVERY_MIRROR_RECEIPT_SCHEMA
        or publication.get("bundle_mode") != BundleValidationMode.PASSIVE_RECOVERED_BUNDLE.value
        or publication.get("release_mode") != BundleValidationMode.PASSIVE_RECOVERED_RELEASE.value
        or publication.get("active_fallback_forbidden") is not True
        or publication.get("zero_effect_rule")
        != {
            "filesystem_and_git_writes": 0,
            "ledger_and_mirror_writes": 0,
            "pipeline_starts": 0,
            "automatic_retries": 0,
            "model_network_database_and_heldout_accesses": 0,
            "trading_effects": 0,
        }
        or census_locks.get("sealed_attempt_epochs") != [5, 6]
        or census_locks.get("permanent_absence_fields")
        != {name: False for name in LEGACY_AMENDMENT_ABSENCE_FIELDS}
        or census_locks.get("effect_limits")
        != {
            "future_recovery_starts_after_separately_authorized_fresh_Q_R_B": 1,
            "pipeline_starts": 0,
            "automatic_retries": 0,
            "preflight_Q_drafting_validation_release_revalidation_and_review_writes": 0,
        }
        or census_locks.get("lock_values")
        != {
            "ordinal_3_forbidden": True,
            "synthetic_attempt_rows_for_epochs_7_8_9_forbidden": True,
            "ledger_and_sealed_mirror_read_only": True,
            "p4_2a_done": False,
            "p4_2b_unlocked": False,
            "p4_3_unlocked": False,
            "heldout_materialization_inference_evaluation_locked": True,
            "non_simulate_trading_locked": True,
            "active_replay_fallback_forbidden": True,
        }
    ):
        raise RehearsalV22ValidationError("epoch-9 recovery or effect-lock semantics drifted")
    tracker.snapshot()
    return contract


def _canonical_committed_report(
    root: Path,
    path: Path,
    *,
    pattern: re.Pattern[str],
    exact_fields: frozenset[str],
    label: str,
) -> tuple[JsonObject, bytes, str, str, str]:
    absolute = path.absolute()
    try:
        relative = absolute.relative_to(root).as_posix()
    except ValueError as exc:
        raise RehearsalV22ValidationError(f"{label} path escaped the repository") from exc
    if pattern.fullmatch(relative) is None:
        raise RehearsalV22ValidationError(f"{label} path is not registered")
    payload = _regular_bytes(absolute, label)
    document = _strict_canonical_json_loads(payload, label=label)
    _require_exact_keys(document, exact_fields, label)
    creating_commit, creation_payload = _unique_a_unserialized(
        root,
        path=relative,
        execution_head=_git_bytes(root, "rev-parse", "HEAD")
        .decode("ascii", errors="strict")
        .strip(),
    )
    if creation_payload != payload:
        raise RehearsalV22ValidationError(f"{label} differs from its logical source bytes")
    return document, payload, _sha256(payload), creating_commit, relative


def _validate_timestamp_pair(document: Mapping[str, Any], label: str) -> None:
    utc_text = _rfc3339_utc(document.get("created_at_utc"), f"{label} UTC timestamp")
    shanghai_text = _rfc3339_shanghai(
        document.get("created_at_shanghai"),
        f"{label} Shanghai timestamp",
    )
    utc = datetime.fromisoformat(utc_text.replace("Z", "+00:00"))
    shanghai = datetime.fromisoformat(shanghai_text)
    if utc != shanghai:
        raise RehearsalV22ValidationError(f"{label} timestamps disagree")


def _validated_census_document(value: object, label: str) -> JsonObject:
    census = _object(value, label)
    _require_exact_keys(census, REAL_LINEAGE_CENSUS_FIELDS, label)
    rows = _array(census.get("rows"), f"{label} rows")
    observed_paths: list[str] = []
    projection_count = 0
    registry_rows: list[JsonObject] = []
    for index, raw_row in enumerate(rows):
        row = _object(raw_row, f"{label} row {index}")
        _require_exact_keys(row, REAL_LINEAGE_ROW_FIELDS, f"{label} row {index}")
        path = _relative(row.get("path"), f"{label} row path")
        observed_paths.append(path)
        registry_rows.append(
            {
                "path": path,
                "pinned_sha256": row.get("pinned_sha256"),
                "pinned_creating_commit": row.get("pinned_creating_commit"),
                "mode": row.get("mode"),
                "declared_landing_projection_commit": row.get(
                    "declared_landing_projection_commit"
                ),
            }
        )
        projection_count += _integer(
            row.get("projection_count"),
            f"{label} row projection count",
            minimum=0,
        )
        touches = _array(row.get("touches"), f"{label} row touches")
        for touch_index, raw_touch in enumerate(touches):
            _require_exact_keys(
                _object(raw_touch, f"{label} touch {touch_index}"),
                REAL_LINEAGE_TOUCH_FIELDS,
                f"{label} touch {touch_index}",
            )
    if (
        observed_paths
        != sorted(observed_paths, key=lambda item: item.encode("utf-8"))
        or len(observed_paths) != len(set(observed_paths))
        or census.get("schema_version") != "p4.2a-v2-2-real-lineage-census-v1"
        or census.get("status") != "PASS_REAL_LINEAGE_CENSUS"
        or census.get("invalid_count") != 0
        or census.get("reference_count") != len(rows)
        or census.get("row_count") != len(rows)
        or census.get("source_count") != len(rows)
        or census.get("projection_count") != projection_count
        or census.get("authority_registry_sha256")
        != _sha256(_canonical_json_bytes(registry_rows))
        or census.get("ref_snapshot_before_sha256")
        != census.get("ref_snapshot_after_sha256")
        or census.get("effects")
        != {
            "git_ref_write": False,
            "git_index_write": False,
            "git_worktree_write": False,
            "ledger_write": False,
            "mirror_write": False,
            "destination_write": False,
            "temporary_write": False,
            "network_access": False,
            "database_access": False,
            "pipeline_execution": False,
            "heldout_access": False,
        }
    ):
        raise RehearsalV22ValidationError(f"{label} shape, counts, or effects drifted")
    return census


def _census_summary(census: Mapping[str, Any]) -> JsonObject:
    payload = _canonical_json_bytes(census)
    return {
        "schema_version": census["schema_version"],
        "execution_head": census["execution_head"],
        "reference_count": census["reference_count"],
        "row_count": census["row_count"],
        "projection_count": census["projection_count"],
        "invalid_count": census["invalid_count"],
        "canonical_json_sha256": _sha256(payload),
        "bytes": len(payload),
        "result": "PASS_REAL_LINEAGE_CENSUS",
        "all_references_revalidated_at_start": True,
    }


def _document_contains_exact_value(value: object, expected: object) -> bool:
    if value == expected:
        return True
    if isinstance(value, Mapping):
        return any(_document_contains_exact_value(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(_document_contains_exact_value(item, expected) for item in value)
    return False


def _validate_epoch_8_landing_document_bindings(
    document: Mapping[str, Any],
    *,
    expected_live_epoch: int,
    implementation_commit: str,
    owner: Mapping[str, Any],
    review: Mapping[str, Any],
    merge_commit: str,
    merge_parents: Sequence[str],
    control_root: object,
    control_count: object,
) -> None:
    """Independently reject named landing fields masked by unrelated aliases."""

    if type(expected_live_epoch) is not int:
        raise RehearsalV22ValidationError("landing expected live epoch is not an integer")

    topology_value = document.get("topology")
    if "topology" in document and not isinstance(topology_value, Mapping):
        raise RehearsalV22ValidationError("epoch-8 landing authority drifted")
    topology = cast(Mapping[str, Any] | None, topology_value)

    implementation_declarations: list[object] = []
    merge_declarations: list[object] = []
    parent_declarations: list[object] = []
    if "implementation_commit" in document:
        implementation_declarations.append(document["implementation_commit"])
    if "merge_commit" in document:
        merge_declarations.append(document["merge_commit"])
    if "merge_parents" in document:
        parent_declarations.append(document["merge_parents"])
    if topology is not None:
        if "candidate_commit" in topology:
            implementation_declarations.append(topology["candidate_commit"])
        if "merge_commit" in topology:
            merge_declarations.append(topology["merge_commit"])
        if "merge_ordered_parents" in topology:
            parent_declarations.append(topology["merge_ordered_parents"])

    expected_parents = list(merge_parents)
    if (
        len(expected_parents) != 2
        or not implementation_declarations
        or any(value != implementation_commit for value in implementation_declarations)
        or not merge_declarations
        or any(value != merge_commit for value in merge_declarations)
        or not parent_declarations
        or any(value != expected_parents for value in parent_declarations)
    ):
        raise RehearsalV22ValidationError("epoch-8 landing authority drifted")

    required_root_bindings: tuple[tuple[str, object], ...] = (
        ("owner_exact_surface_authorization", dict(owner)),
        ("independent_implementation_review", dict(review)),
        ("control_merkle_root_sha256", control_root),
    )
    if any(
        key not in document or document[key] != expected
        for key, expected in required_root_bindings
    ):
        raise RehearsalV22ValidationError("epoch-8 landing authority drifted")
    if (
        type(document.get("implementation_epoch")) is not int
        or document["implementation_epoch"] != expected_live_epoch
    ):
        raise RehearsalV22ValidationError("epoch-8 landing authority drifted")
    if (
        type(control_count) is not int
        or type(document.get("control_record_count")) is not int
        or document["control_record_count"] != control_count
    ):
        raise RehearsalV22ValidationError("epoch-8 landing authority drifted")
    if topology is not None and (
        (
            "independent_review_source_commit" in topology
            and topology["independent_review_source_commit"] != expected_parents[1]
        )
        or (
            "independent_review_source_parent" in topology
            and topology["independent_review_source_parent"] != implementation_commit
        )
    ):
        raise RehearsalV22ValidationError("epoch-8 landing authority drifted")
    if (
        "independent_review_projection_commit" in document
        and document["independent_review_projection_commit"] != merge_commit
    ):
        raise RehearsalV22ValidationError("epoch-8 landing authority drifted")


def _validate_epoch_8_landing_authority(
    root: Path,
    *,
    expected_live_epoch: int,
    expected_owner: Mapping[str, Any],
    execution_head: str,
    implementation_commit: str,
    owner: Mapping[str, Any],
    review: Mapping[str, Any],
    landing: Mapping[str, Any],
    declared_merge_commit: str,
    control_root: object,
    control_count: object,
) -> None:
    """Independently prove review projection, merge order, and landing bindings."""

    if type(expected_live_epoch) is not int or owner != expected_owner:
        raise RehearsalV22ValidationError("landing epoch or owner authority drifted")

    merge_commit = _git_commit(root, declared_merge_commit, "epoch-8 landing merge")
    landing_path = _relative(landing.get("path"), "epoch-8 landing report path")
    landing_commit = _git_commit(
        root,
        landing.get("creating_commit"),
        "epoch-8 landing report commit",
    )
    merge_parents = _git_parents(root, merge_commit)
    if (
        len(merge_parents) != 2
        or not landing_path.startswith("docs/phase4/reports/")
        or not landing_path.endswith(".json")
        or review.get("creating_commit") != merge_commit
        or _git_parents(root, landing_commit) != (merge_commit,)
        or _git_parents(root, merge_parents[1]) != (implementation_commit,)
        or not _git_is_ancestor(root, landing_commit, execution_head)
    ):
        raise RehearsalV22ValidationError("epoch-8 landing authority drifted")
    _validate_implementation_review_authority(
        root,
        review,
        implementation_commit=implementation_commit,
        execution_head=execution_head,
        require_worktree=True,
    )
    landing_payload = _unique_a_authority(root, landing, require_worktree=True)
    landing_document = _object(
        strict_json_loads(landing_payload, label="epoch-8 landing report"),
        "epoch-8 landing report",
    )
    _validate_epoch_8_landing_document_bindings(
        landing_document,
        expected_live_epoch=expected_live_epoch,
        implementation_commit=implementation_commit,
        owner=owner,
        review=review,
        merge_commit=merge_commit,
        merge_parents=merge_parents,
        control_root=control_root,
        control_count=control_count,
    )
    required_values = (
        implementation_commit,
        owner.get("creating_commit"),
        owner.get("sha256"),
        review.get("creating_commit"),
        review.get("sha256"),
        merge_commit,
        merge_parents[0],
        merge_parents[1],
        control_root,
        control_count,
    )
    if _canonical_json_bytes(landing_document) != landing_payload or any(
        value is None or not _document_contains_exact_value(landing_document, value)
        for value in required_values
    ):
        raise RehearsalV22ValidationError("epoch-8 landing authority drifted")


def _validate_epoch_8_preflight_storage_directory(
    value: object,
    *,
    expected_path: Path,
    label: str,
) -> JsonObject:
    evidence = _object(value, label)
    _require_exact_keys(
        evidence,
        frozenset(
            {
                "path",
                "owner_uid",
                "device",
                "inode",
                "mode_octal",
                "non_symlink",
                "canonical_unaliased",
            }
        ),
        label,
    )
    if (
        evidence.get("path") != expected_path.as_posix()
        or type(evidence.get("owner_uid")) is not int
        or evidence.get("owner_uid") != _validator_os.getuid()
        or type(evidence.get("device")) is not int
        or cast(int, evidence.get("device")) < 0
        or type(evidence.get("inode")) is not int
        or cast(int, evidence.get("inode")) <= 0
        or evidence.get("mode_octal") != "0700"
        or evidence.get("non_symlink") is not True
        or evidence.get("canonical_unaliased") is not True
    ):
        raise RehearsalV22ValidationError(f"{label} identity semantics drifted")
    return evidence


def _expected_epoch_8_preflight_effects() -> JsonObject:
    return {
        "action_receipt_required": False,
        "action_receipts_read": 0,
        "project_and_gate_state_writes_permitted": False,
        "temporary_authorities_created": 0,
        "ledgers_created": 0,
        "storage_containers_created": 0,
        "mirror_leaves_created": 0,
        "attempts_allocated": 0,
        "pipeline_starts": 0,
        "automatic_retries": 0,
        "heldout_evaluation_attempts_consumed": 0,
        "shallow_alternate_partial_and_included_git_config_rejected": True,
        "stdout_persistence_controlled_by_caller": True,
    }


def _validate_epoch_8_q_preflight(
    root: Path,
    *,
    contract: Mapping[str, Any],
    expected_live_epoch: int,
    expected_owner: Mapping[str, Any],
    q: Mapping[str, Any],
    q_commit: str,
) -> tuple[JsonObject, JsonObject, JsonObject]:
    if type(expected_live_epoch) is not int:
        raise RehearsalV22ValidationError("Q expected live epoch is not an integer")
    q_contract = _object(contract.get("recovery_review_request_contract"), "epoch-8 Q contract")
    q_nested = _object(q_contract.get("nested_exact_field_sets"), "epoch-8 Q nested fields")
    if (
        q.get("schema_version") != SERIES_2_RECOVERY_REVIEW_REQUEST_SCHEMA
        or q.get("status") != "AWAITING_INDEPENDENT_REVIEW_AND_OWNER_CONFIRMATION"
        or "landed_epoch_7" in q
    ):
        raise RehearsalV22ValidationError("epoch-8 recovery Q schema or status drifted")
    requester = _object(q.get("requester"), "Q requester")
    requested = _object(
        q.get("requested_owner_action_time_confirmation"),
        "Q requested owner confirmation",
    )
    post_plan = _object(
        q.get("post_confirmation_plan_not_yet_executed"),
        "Q post-confirmation plan",
    )
    current_locks = _object(q.get("current_locks"), "Q current locks")
    for value, key, label in (
        (requester, "requester", "Q requester"),
        (
            requested,
            "requested_owner_action_time_confirmation",
            "Q requested owner confirmation",
        ),
        (
            post_plan,
            "post_confirmation_plan_not_yet_executed",
            "Q post-confirmation plan",
        ),
        (current_locks, "current_locks", "Q current locks"),
    ):
        _require_exact_keys(
            value,
            frozenset(cast(Sequence[str], q_nested.get(key, ()))),
            label,
        )
    if (
        requester
        != {
            "identity": "codex",
            "role": "operator",
            "scope": "sealed_bundle_recovery_only",
        }
        or requested.get("required_owner_identity") != "ouyang"
        or requested.get("delivery_channel") != "in_person_via_independent_reviewer"
        or requested.get("confirmation_not_yet_received") is not True
        or not isinstance(requested.get("requested_exact_confirmation"), str)
        or not cast(str, requested.get("requested_exact_confirmation"))
        or post_plan
        != {
            "land_r": True,
            "land_b": True,
            "revalidate_start_census": True,
            "one_recovery_start": True,
            "zero_pipeline_start": True,
            "zero_automatic_retry": True,
        }
        or current_locks
        != {
            "series_closed": True,
            "attempts_allocated": 2,
            "selected_attempt_ordinal": 2,
            "ledger_and_sealed_mirror_read_only": True,
            "destination_created": False,
            "bundle_recovery_authorization_created": False,
            "owner_confirmation_binding_created": False,
            "bundle_recovery_starts": 0,
            "pipeline_starts_in_recovery": 0,
            "automatic_retries_in_recovery": 0,
            "recovery_claim_created": False,
            "recovered_bundle_mirror_created": False,
            "heldout_evaluation_attempts_consumed": 0,
            "p4_2a_done": False,
            "p4_2b_unlocked": False,
            "p4_3_unlocked": False,
            "trading_unlocked": False,
        }
    ):
        raise RehearsalV22ValidationError("recovery Q requester/confirmation/locks drifted")
    landed = _object(q.get("landed_execution_epoch"), "Q landed execution epoch")
    _require_exact_keys(
        landed,
        frozenset(cast(Sequence[str], q_nested.get("landed_execution_epoch", ()))),
        "Q landed execution epoch",
    )
    owner = _validate_authority_ref(
        landed.get("owner_exact_surface_authorization"),
        "Q landed owner authority",
    )
    review = _validate_authority_ref(
        landed.get("independent_implementation_review"),
        "Q landed independent review",
    )
    landing = _validate_authority_ref(landed.get("landing_report"), "Q landing report")
    merge_commit = _git_commit(root, landed.get("merge_commit"), "Q landed merge")
    implementation_commit = _git_commit(
        root,
        landed.get("implementation_commit"),
        "Q landed implementation",
    )
    if (
        landed.get("epoch") != expected_live_epoch
        or owner != expected_owner
        or not _git_is_ancestor(root, implementation_commit, merge_commit)
        or _git_parents(root, q_commit) != (cast(str, landing["creating_commit"]),)
        or _git_commit(root, landing["creating_commit"], "Q landing")
        != cast(str, landing["creating_commit"])
    ):
        raise RehearsalV22ValidationError("Q landed epoch identity or topology drifted")
    _unique_a_authority(root, owner, require_worktree=True)
    _validate_epoch_8_landing_authority(
        root,
        expected_live_epoch=expected_live_epoch,
        expected_owner=expected_owner,
        execution_head=q_commit,
        implementation_commit=implementation_commit,
        owner=owner,
        review=review,
        landing=landing,
        declared_merge_commit=merge_commit,
        control_root=landed.get("control_merkle_root_sha256"),
        control_count=landed.get("control_record_count"),
    )
    preflight_wrapper = _object(
        q.get("registered_read_only_recovery_preflight"),
        "Q registered preflight",
    )
    _require_exact_keys(
        preflight_wrapper,
        frozenset(cast(Sequence[str], q_nested.get("registered_read_only_recovery_preflight", ()))),
        "Q registered preflight",
    )
    stdout_text = _string(
        preflight_wrapper.get("stdout_canonical_json"),
        "Q preflight stdout",
    )
    stdout_payload = stdout_text.encode("utf-8")
    preflight = _strict_canonical_json_loads(stdout_payload, label="Q preflight stdout")
    _require_exact_keys(preflight, SERIES_2_READ_ONLY_PREFLIGHT_FIELDS, "epoch-8 preflight")
    if _canonical_json_bytes(preflight) != stdout_payload:
        raise RehearsalV22ValidationError("Q preflight stdout is not canonical JSON bytes")
    census = _validated_census_document(
        preflight.get("real_lineage_census"),
        "Q preflight real-lineage census",
    )
    summary = _census_summary(census)
    if (
        preflight.get("schema_version") != SERIES_2_READ_ONLY_PREFLIGHT_SCHEMA
        or preflight.get("status") != "PASS_READ_ONLY_IMPLEMENTATION_PREFLIGHT"
        or preflight.get("implementation_epoch") != expected_live_epoch
        or preflight.get("implementation_commit") != implementation_commit
        or preflight.get("owner_exact_surface_authorization") != owner
        or preflight.get("independent_implementation_review") != review
        or preflight.get("control_merkle_root_sha256")
        != landed.get("control_merkle_root_sha256")
        or preflight.get("control_record_count") != landed.get("control_record_count")
        or preflight.get("execution_head") != landing["creating_commit"]
        or census.get("execution_head") != preflight.get("execution_head")
        or preflight_wrapper.get("real_lineage_census") != summary
        or preflight_wrapper.get("stdout_sha256") != _sha256(stdout_payload)
        or preflight_wrapper.get("stdout_bytes") != len(stdout_payload)
        or preflight_wrapper.get("stderr_bytes") != 0
        or preflight_wrapper.get("returncode") != 0
        or preflight_wrapper.get("status") != "PASS_READ_ONLY_IMPLEMENTATION_PREFLIGHT"
    ):
        raise RehearsalV22ValidationError("Q registered preflight binding drifted")
    registered_surface = _array(
        preflight.get("registered_surface"),
        "epoch-8 preflight registered surface",
    )
    expected_surface = [
        {
            "path": relative,
            "sha256": _sha256(_git_blob(root, implementation_commit, relative)),
        }
        for relative in IMPLEMENTATION_PATHS
    ]
    if registered_surface != expected_surface or any(
        set(_object(row, "epoch-8 registered surface row")) != {"path", "sha256"}
        for row in registered_surface
    ):
        raise RehearsalV22ValidationError("epoch-8 preflight registered surface drifted")
    preflight_contract = _object(
        contract.get("registered_preflight_contract"),
        "epoch-8 preflight contract",
    )
    preflight_nested = _object(
        preflight_contract.get("nested_exact_field_sets"),
        "epoch-8 preflight nested fields",
    )
    preflight_mode = preflight.get("mode")
    expected_primary_series = registered_series_ledger(root).parent
    expected_secondary_series = (
        SERIES_2_SECONDARY_SERIES_CONTAINER
        if root == REGISTERED_PROJECT_ROOT.absolute()
        else root.parent
        / f"{root.name}-EVIDENCE-MIRROR-DO-NOT-DELETE"
        / "P4.2a/v2.2"
        / expected_primary_series.name
    )
    series_storage = _object(
        preflight.get("series_2_registered_storage"),
        "epoch-8 preflight series-2 registered storage",
    )
    _require_exact_keys(
        series_storage,
        frozenset(
            cast(Sequence[str], preflight_nested.get("series_2_registered_storage", ()))
        ),
        "epoch-8 preflight series-2 registered storage",
    )
    _validate_epoch_8_preflight_storage_directory(
        series_storage.get("primary_container"),
        expected_path=expected_primary_series,
        label="epoch-8 preflight primary series container",
    )
    _validate_epoch_8_preflight_storage_directory(
        series_storage.get("secondary_container"),
        expected_path=expected_secondary_series,
        label="epoch-8 preflight secondary series container",
    )
    leaf_state = _object(
        series_storage.get("registered_leaf_state"),
        "epoch-8 preflight registered leaf state",
    )
    _require_exact_keys(
        leaf_state,
        frozenset(cast(Sequence[str], preflight_nested.get("registered_leaf_state", ()))),
        "epoch-8 preflight registered leaf state",
    )
    mirrored_history = _object(
        series_storage.get("mirrored_history"),
        "epoch-8 preflight mirrored history",
    )
    _require_exact_keys(
        mirrored_history,
        frozenset(cast(Sequence[str], preflight_nested.get("mirrored_history", ()))),
        "epoch-8 preflight mirrored history",
    )
    if (
        series_storage.get("containers_non_overlapping") is not True
        or series_storage.get("storage_state") != "EXISTING_FULLY_MIRRORED"
        or any(value != "PRESENT_VERIFIED" for value in leaf_state.values())
        or mirrored_history
        != {
            "attempt_count": 2,
            "history_root_sha256": preflight.get("sealed_recovery_inputs", {}).get(
                "history_root_sha256"
            )
            if isinstance(preflight.get("sealed_recovery_inputs"), Mapping)
            else None,
            "live_ledger_root_sha256": preflight.get("sealed_recovery_inputs", {}).get(
                "live_ledger_root_sha256"
            )
            if isinstance(preflight.get("sealed_recovery_inputs"), Mapping)
            else None,
            "receipt_count": 2,
            "series_closed": True,
        }
        or series_storage.get("bundle_destination_absent") is not True
        or series_storage.get("lost_series_ledger_absent") is not True
        or series_storage.get("retired_v2_1_claim_absent") is not True
        or series_storage.get("paths_created") != 0
    ):
        raise RehearsalV22ValidationError("epoch-8 preflight series-2 storage drifted")
    if preflight_mode == "REGISTERED_OFFICIAL":
        if root != REGISTERED_PROJECT_ROOT.absolute():
            raise RehearsalV22ValidationError(
                "epoch-8 official/synthetic preflight root classification drifted"
            )
        recovery_storage = _object(
            preflight.get("registered_recovery_storage"),
            "epoch-8 registered recovery storage",
        )
        _require_exact_keys(
            recovery_storage,
            frozenset(
                cast(
                    Sequence[str],
                    preflight_nested.get("registered_recovery_storage", ()),
                )
            ),
            "epoch-8 registered recovery storage",
        )
        _validate_epoch_8_preflight_storage_directory(
            recovery_storage.get("primary_container"),
            expected_path=SERIES_2_PRIMARY_RECOVERY_CONTAINER,
            label="epoch-8 preflight primary recovery container",
        )
        _validate_epoch_8_preflight_storage_directory(
            recovery_storage.get("secondary_container"),
            expected_path=SERIES_2_SECONDARY_RECOVERY_CONTAINER,
            label="epoch-8 preflight secondary recovery container",
        )
        if (
            recovery_storage.get("both_owner_provisioned_empty") is not True
            or recovery_storage.get("leaf_paths_created") != 0
        ):
            raise RehearsalV22ValidationError(
                "epoch-8 recovery containers are not preflight-empty"
            )
    elif preflight_mode == "NONREGISTERED_READ_ONLY_TEST":
        if (
            root == REGISTERED_PROJECT_ROOT.absolute()
            or preflight.get("registered_recovery_storage") is not None
        ):
            raise RehearsalV22ValidationError(
                "epoch-8 official/synthetic preflight root classification drifted"
            )
    else:
        raise RehearsalV22ValidationError("epoch-8 preflight mode is not registered")
    sealed_inputs = _object(
        preflight.get("sealed_recovery_inputs"),
        "epoch-8 preflight sealed inputs",
    )
    _require_exact_keys(
        sealed_inputs,
        frozenset(cast(Sequence[str], preflight_nested.get("sealed_recovery_inputs", ()))),
        "epoch-8 preflight sealed inputs",
    )
    fingerprints = _object(
        sealed_inputs.get("sealed_input_fingerprints"),
        "epoch-8 preflight sealed input fingerprints",
    )
    _require_exact_keys(
        fingerprints,
        frozenset(cast(Sequence[str], preflight_nested.get("sealed_input_fingerprints", ()))),
        "epoch-8 preflight sealed input fingerprints",
    )
    counters = _object(
        sealed_inputs.get("work_counters"),
        "epoch-8 preflight work counters",
    )
    _require_exact_keys(
        counters,
        frozenset(cast(Sequence[str], preflight_nested.get("work_counters", ()))),
        "epoch-8 preflight work counters",
    )
    if any(
        _SHA256_PATTERN.fullmatch(_string(value, "sealed input fingerprint")) is None
        for value in fingerprints.values()
    ):
        raise RehearsalV22ValidationError("epoch-8 preflight sealed inputs drifted")
    for name in RECOVERY_WORK_COUNTER_FIELDS:
        counter_value = counters.get(name)
        if (
            type(counter_value) is not int
            or counter_value < 0
            or counter_value > RECOVERY_WORK_LIMITS[name]
        ):
            raise RehearsalV22ValidationError("epoch-8 preflight sealed inputs drifted")
    if (
        sealed_inputs.get("series_closed") is not True
        or sealed_inputs.get("record_count") != 2
        or sealed_inputs.get("selected_attempt_ordinal") != 2
        or sealed_inputs.get("selected_implementation_epoch") != HISTORICAL_SELECTED_EPOCH
        or sealed_inputs.get("mirror_receipt_count") != 2
        or sealed_inputs.get("ledger_and_mirror_read_only") is not True
        or counters.get("git_objects_read") != 0
        or counters.get("bundle_bytes_copied") != 0
        or _COMMIT_PATTERN.fullmatch(
            _string(
                sealed_inputs.get("selected_implementation_commit"),
                "preflight selected implementation",
            )
        )
        is None
        or _SHA256_PATTERN.fullmatch(
            _string(sealed_inputs.get("history_root_sha256"), "preflight history root")
        )
        is None
        or _SHA256_PATTERN.fullmatch(
            _string(sealed_inputs.get("live_ledger_root_sha256"), "preflight live root")
        )
        is None
    ):
        raise RehearsalV22ValidationError("epoch-8 preflight sealed inputs drifted")
    if preflight_mode == "REGISTERED_OFFICIAL" and (
        sealed_inputs.get("selected_implementation_commit")
        != HISTORICAL_SELECTED_IMPLEMENTATION_COMMIT
        or sealed_inputs.get("history_root_sha256") != SEALED_SERIES_HISTORY_ROOT_SHA256
        or sealed_inputs.get("live_ledger_root_sha256") != SEALED_SERIES_LIVE_ROOT_SHA256
    ):
        raise RehearsalV22ValidationError("official epoch-8 sealed inputs drifted")
    effect_summary = _object(preflight.get("effect_summary"), "epoch-8 preflight effects")
    _require_exact_keys(
        effect_summary,
        frozenset(cast(Sequence[str], preflight_nested.get("effect_summary", ()))),
        "epoch-8 preflight effects",
    )
    if effect_summary != _expected_epoch_8_preflight_effects():
        raise RehearsalV22ValidationError("epoch-8 preflight effects are not exactly zero")
    argv = _array(preflight_wrapper.get("exact_argv"), "Q preflight argv")
    expected_argv = [
        _VALIDATOR_FIXED_PYTHON,
        "-S",
        "-P",
        "-B",
        (root / "scripts/rehearse_p4_2a_v2_2_heldout_full_path.py").as_posix(),
        "--preflight-only",
        "--implementation-epoch",
        str(expected_live_epoch),
        "--implementation-commit",
        implementation_commit,
        "--owner-surface-authorization",
        (root / cast(str, owner["path"])).as_posix(),
        "--independent-implementation-review",
        (root / cast(str, review["path"])).as_posix(),
        "--landing-report",
        (root / cast(str, landing["path"])).as_posix(),
    ]
    if argv != expected_argv:
        raise RehearsalV22ValidationError("epoch-8 preflight argv order or values drifted")
    rows_by_path = {
        cast(str, _object(row, "Q baseline census row")["path"]): _object(
            row,
            "Q baseline census row",
        )
        for row in cast(list[JsonObject], census["rows"])
    }
    for reference, mode, declared_landing, label in (
        (owner, AuthorityCensusRole.PINNED_SOURCE.value, None, "owner"),
        (
            review,
            AuthorityCensusRole.PINNED_LANDING_PROJECTION.value,
            merge_commit,
            "review",
        ),
        (landing, AuthorityCensusRole.PINNED_SOURCE.value, None, "landing"),
    ):
        row = rows_by_path.get(cast(str, reference["path"]))
        if (
            row is None
            or row.get("pinned_sha256") != reference["sha256"]
            or row.get("pinned_creating_commit") != reference["creating_commit"]
            or row.get("mode") != mode
            or row.get("declared_landing_projection_commit") != declared_landing
        ):
            raise RehearsalV22ValidationError(f"Q preflight omitted or changed {label} row")
    equality = _object(q.get("preflight_before_after_equality"), "Q preflight equality")
    _require_exact_keys(
        equality,
        frozenset(cast(Sequence[str], q_nested.get("preflight_before_after_equality", ()))),
        "Q preflight equality",
    )
    if not equality or any(value is not True for value in equality.values()):
        raise RehearsalV22ValidationError("Q preflight before/after equality drifted")
    return preflight, census, landed


def _validate_epoch_8_census_delta(
    *,
    baseline: Mapping[str, Any],
    observed: Mapping[str, Any],
    additional_specs: Sequence[AuthorityCensusSpec],
) -> None:
    baseline_rows = {
        cast(str, _object(row, "baseline census row")["path"]): _object(
            row,
            "baseline census row",
        )
        for row in cast(list[JsonObject], baseline["rows"])
    }
    observed_rows = {
        cast(str, _object(row, "observed census row")["path"]): _object(
            row,
            "observed census row",
        )
        for row in cast(list[JsonObject], observed["rows"])
    }
    additional_by_path: dict[str, AuthorityCensusSpec] = {}
    for spec in additional_specs:
        prior = additional_by_path.get(spec.path)
        if prior is not None and prior != spec:
            raise RehearsalV22ValidationError(
                f"epoch-8 census delta has conflicting runtime spec: {spec.path}"
            )
        additional_by_path[spec.path] = spec
    dynamic_paths = set(additional_by_path) - set(baseline_rows)
    if set(observed_rows) != set(baseline_rows) | dynamic_paths:
        raise RehearsalV22ValidationError(
            "epoch-8 start/release census is not baseline plus exact runtime authorities"
        )
    for path, baseline_row in baseline_rows.items():
        if observed_rows.get(path) != baseline_row:
            raise RehearsalV22ValidationError(
                f"epoch-8 census changed a preflight baseline row: {path}"
            )
    for path in dynamic_paths:
        spec = additional_by_path[path]
        row = observed_rows[path]
        if (
            row.get("pinned_sha256") != spec.pinned_sha256
            or row.get("pinned_creating_commit") != spec.pinned_creating_commit
            or row.get("mode") != spec.role.value
            or row.get("declared_landing_projection_commit")
            != spec.declared_landing_projection_commit
        ):
            raise RehearsalV22ValidationError(
                f"epoch-8 census runtime authority row drifted: {path}"
            )
    if (
        observed.get("row_count") != len(observed_rows)
        or observed.get("reference_count") != len(observed_rows)
        or observed.get("source_count") != len(observed_rows)
    ):
        raise RehearsalV22ValidationError("epoch-8 census delta counts drifted")


def _validate_epoch_8_qrb_ref_delta(
    root: Path,
    *,
    governance: _ValidatedRecoveryGovernance,
    execution_head: str,
) -> None:
    if execution_head != governance.b_commit:
        raise RehearsalV22ValidationError("recovery start HEAD is not the exact fresh B commit")
    baseline_head = _git_commit(
        root,
        governance.preflight_census.get("execution_head"),
        "preflight baseline HEAD",
    )
    if (
        _git_parents(root, governance.q_commit) != (baseline_head,)
        or _git_parents(root, governance.r_commit) != (governance.q_commit,)
        or _git_parents(root, governance.b_commit) != (governance.r_commit,)
    ):
        raise RehearsalV22ValidationError("recovery Q/R/B are not the exact linear ref delta")
    current = _git_ref_snapshot(root)
    baseline_rows: list[bytes] = []
    changed_ref_names: set[bytes] = set()
    for raw_row in current.splitlines(keepends=True):
        body = raw_row[:-1] if raw_row.endswith(b"\n") else raw_row
        fields = body.split(b"\0")
        if len(fields) != 2:
            raise RehearsalV22ValidationError("Git ref snapshot row is malformed")
        name, oid = fields
        if name in {b"refs/heads/main", b"refs/remotes/origin/main"}:
            if oid != governance.b_commit.encode("ascii"):
                raise RehearsalV22ValidationError("main or origin/main is not the exact B commit")
            oid = baseline_head.encode("ascii")
            changed_ref_names.add(name)
        baseline_rows.append(name + b"\0" + oid + b"\n")
    if changed_ref_names != {b"refs/heads/main", b"refs/remotes/origin/main"} or _sha256(
        b"".join(baseline_rows)
    ) != governance.preflight_census.get("ref_snapshot_after_sha256"):
        raise RehearsalV22ValidationError(
            "recovery ref delta changed a ref other than exact main/origin-main Q/R/B"
        )


def _validate_recovery_governance(
    project_root: Path,
    *,
    recovery_authorization_path: Path,
    owner_binding_path: Path,
    expected_series_token_sha256: str,
) -> _ValidatedRecoveryGovernance:
    root = project_root.resolve(strict=True)
    execution_head = _git_bytes(root, "rev-parse", "HEAD").decode(
        "ascii", errors="strict"
    ).strip()
    latest_landed_contract = validate_epoch_9_latest_landed_execution_contract(
        root,
        execution_head=execution_head,
    )
    historical_companion = _object(
        strict_json_loads(
            _regular_bytes(root / EPOCH_8_COMPANION_RELATIVE, "epoch-8 companion"),
            label="epoch-8 companion",
        ),
        "epoch-8 companion",
    )
    contract = _object(
        historical_companion.get("epoch_8_recovery_contract"),
        "epoch-8 recovery contract",
    )
    _require_exact_keys(contract, EPOCH_8_RECOVERY_CONTRACT_FIELDS, "epoch-8 recovery contract")
    if _sha256(_canonical_json_bytes(contract)) != EPOCH_8_CONTRACT_CANONICAL_SHA256:
        raise RehearsalV22ValidationError("epoch-8 recovery contract drifted after validation")
    expected_owner = {
        "path": EPOCH_9_SURFACE_AUTHORITY_RELATIVE.as_posix(),
        "sha256": EPOCH_9_SURFACE_AUTHORITY_SHA256,
        "creating_commit": EPOCH_9_SURFACE_AUTHORITY_COMMIT,
        "unique_a_history_verified": True,
    }
    r, r_payload, r_sha, r_commit, r_relative = _canonical_committed_report(
        root,
        recovery_authorization_path,
        pattern=_RECOVERY_R_PATH_PATTERN,
        exact_fields=RECOVERY_AUTHORIZATION_FIELDS,
        label="bundle recovery authorization",
    )
    b, b_payload, b_sha, b_commit, b_relative = _canonical_committed_report(
        root,
        owner_binding_path,
        pattern=_RECOVERY_B_PATH_PATTERN,
        exact_fields=RECOVERY_OWNER_BINDING_FIELDS,
        label="bundle recovery owner binding",
    )
    _validate_timestamp_pair(r, "bundle recovery authorization")
    _validate_timestamp_pair(b, "bundle recovery owner binding")
    b_r = _object(b.get("recovery_authorization"), "owner binding R reference")
    b_q = _object(b.get("review_request"), "owner binding Q reference")
    for reference, expected_path, expected_sha, expected_bytes, expected_commit, label in (
        (b_r, r_relative, r_sha, len(r_payload), r_commit, "owner binding R"),
    ):
        _require_exact_keys(
            reference,
            frozenset({"path", "sha256", "bytes", "creating_commit"}),
            label,
        )
        _require_equal(
            reference,
            {
                "path": expected_path,
                "sha256": expected_sha,
                "bytes": expected_bytes,
                "creating_commit": expected_commit,
            },
            label,
        )
    q_path_text = _relative(b_q.get("path"), "owner binding Q path")
    q, q_payload, q_sha, q_commit, q_relative = _canonical_committed_report(
        root,
        root / q_path_text,
        pattern=_RECOVERY_Q_PATH_PATTERN,
        exact_fields=RECOVERY_REVIEW_REQUEST_FIELDS,
        label="bundle recovery review request",
    )
    _require_equal(
        b_q,
        {
            "path": q_relative,
            "sha256": q_sha,
            "bytes": len(q_payload),
            "creating_commit": q_commit,
        },
        "owner binding Q",
    )
    _validate_timestamp_pair(q, "bundle recovery review request")
    if _git_parents(root, r_commit) != (q_commit,) or _git_parents(root, b_commit) != (r_commit,):
        raise RehearsalV22ValidationError("recovery Q/R/B direct-parent order drifted")
    preflight, preflight_census, landed_execution = _validate_epoch_8_q_preflight(
        root,
        contract=contract,
        expected_live_epoch=LATEST_LANDED_EXECUTION_EPOCH,
        expected_owner=expected_owner,
        q=q,
        q_commit=q_commit,
    )
    execution = _object(r.get("execution_epoch"), "recovery execution epoch")
    r_contract = _object(contract.get("recovery_authorization_contract"), "contract R")
    r_nested = _object(r_contract.get("nested_exact_field_sets"), "contract R nested fields")
    owner = _object(r.get("owner"), "recovery R owner")
    sealed_series = _object(r.get("sealed_series"), "recovery R sealed series")
    destination = _object(r.get("destination"), "recovery R destination")
    recovery_storage = _object(
        destination.get("recovery_storage"),
        "recovery R storage",
    )
    interpreter = _object(r.get("interpreter"), "recovery R interpreter")
    locks = _object(r.get("locks"), "recovery R locks")
    selected_files = _object(
        sealed_series.get("selected_files"),
        "recovery R selected files",
    )
    sealed_mirror = _object(
        sealed_series.get("sealed_mirror"),
        "recovery R sealed mirror",
    )
    r_census_reference = _object(
        execution.get("real_lineage_census"),
        "recovery R real-lineage census",
    )
    for value, key, label in (
        (owner, "owner", "recovery R owner"),
        (sealed_series, "sealed_series", "recovery R sealed series"),
        (execution, "execution_epoch", "recovery execution epoch"),
        (destination, "destination", "recovery R destination"),
        (recovery_storage, "recovery_storage", "recovery R storage"),
        (interpreter, "interpreter", "recovery R interpreter"),
        (locks, "locks", "recovery R locks"),
        (selected_files, "selected_files", "recovery R selected files"),
        (sealed_mirror, "sealed_mirror", "recovery R sealed mirror"),
        (
            r_census_reference,
            "real_lineage_census",
            "recovery R real-lineage census",
        ),
    ):
        _require_exact_keys(
            value,
            frozenset(cast(Sequence[str], r_nested.get(key, ()))),
            label,
        )
    for name, value in selected_files.items():
        _require_exact_keys(
            _object(value, f"recovery R selected {name}"),
            frozenset(
                cast(Sequence[str], r_nested.get("selected_file_reference", ()))
            ),
            f"recovery R selected {name}",
        )
    _require_exact_keys(
        execution,
        frozenset(cast(Sequence[str], r_nested.get("execution_epoch", ()))),
        "recovery execution epoch",
    )
    for key, value in landed_execution.items():
        if execution.get(key) != value:
            raise RehearsalV22ValidationError(
                f"recovery R differs from Q landed execution epoch: {key}"
            )
    if (
        execution.get("real_lineage_census") != _census_summary(preflight_census)
        or execution.get("latest_complete_landed_epoch_required") is not True
        or execution.get("current_control_bytes_required") is not True
        or execution.get("loaded_module_bytes_required") is not True
    ):
        raise RehearsalV22ValidationError("recovery R preflight census or live policy drifted")
    preflight_sealed = _object(
        preflight.get("sealed_recovery_inputs"),
        "recovery Q preflight sealed inputs",
    )
    if (
        preflight_sealed.get("series_closed") != sealed_series.get("series_closed")
        or preflight_sealed.get("record_count") != sealed_series.get("started_count")
        or preflight_sealed.get("selected_attempt_ordinal")
        != sealed_series.get("selected_attempt_ordinal")
        or preflight_sealed.get("selected_implementation_epoch")
        != sealed_series.get("selected_implementation_epoch")
        or preflight_sealed.get("selected_implementation_commit")
        != sealed_series.get("selected_implementation_commit")
        or preflight_sealed.get("history_root_sha256")
        != sealed_series.get("history_root_sha256")
        or preflight_sealed.get("live_ledger_root_sha256")
        != sealed_series.get("live_ledger_root_sha256")
        or preflight_sealed.get("mirror_receipt_count")
        != _object(sealed_series.get("sealed_mirror"), "R sealed mirror").get(
            "receipt_count"
        )
    ):
        raise RehearsalV22ValidationError("recovery R sealed series differs from Q preflight")
    b_preflight = _object(
        b.get("registered_read_only_recovery_preflight"),
        "owner binding registered preflight",
    )
    _require_equal(
        b_preflight,
        {
            "path": q_relative,
            "stdout_sha256": _sha256(
                _string(
                    _object(
                        q.get("registered_read_only_recovery_preflight"),
                        "Q preflight",
                    ).get("stdout_canonical_json"),
                    "Q preflight stdout",
                ).encode("utf-8")
            ),
            "stdout_bytes": len(
                _string(
                    _object(
                        q.get("registered_read_only_recovery_preflight"),
                        "Q preflight",
                    ).get("stdout_canonical_json"),
                    "Q preflight stdout",
                ).encode("utf-8")
            ),
            "real_lineage_census_sha256": _sha256(_canonical_json_bytes(preflight_census)),
            "result": "PASS_READ_ONLY_IMPLEMENTATION_PREFLIGHT",
        },
        "owner binding registered preflight",
    )
    proposed = _object(q.get("proposed_recovery_authorization"), "Q proposed R")
    _require_exact_keys(
        proposed,
        frozenset({"path", "document", "canonical_json_sha256", "bytes", "currently_effective"}),
        "Q proposed R",
    )
    if (
        proposed.get("path") != r_relative
        or proposed.get("document") != r
        or proposed.get("canonical_json_sha256") != r_sha
        or proposed.get("bytes") != len(r_payload)
        or proposed.get("currently_effective") is not False
    ):
        raise RehearsalV22ValidationError("Q proposed R differs from landed authorization")
    exact_argv = _array(r.get("exact_argv"), "recovery R argv")
    exact_environment = _object(r.get("exact_environment"), "recovery R environment")
    expected_argv = [
        _VALIDATOR_FIXED_PYTHON,
        "-S",
        "-P",
        "-B",
        (root / "scripts/rehearse_p4_2a_v2_2_heldout_full_path.py").as_posix(),
        "--recover-sealed-bundle",
        "--bundle-recovery-authorization",
        (root / r_relative).as_posix(),
        "--bundle-recovery-owner-confirmation-binding",
        (root / b_relative).as_posix(),
    ]
    expected_storage_scalars = {
        "claim_name_derived_from_authorization_sha256": True,
        "destination_stage_name_derived_from_authorization_sha256": True,
        "secondary_snapshot_stage_name_derived_from_authorization_sha256": True,
        "secondary_snapshot_name_derived_from_authorization_sha256_and_tree_root": True,
        "receipt_name_derived_from_authorization_sha256_and_tree_root": True,
        "destination_publication_mode": "ATOMIC_DIRECTORY_NO_REPLACE",
        "secondary_snapshot_publication_mode": "ATOMIC_DIRECTORY_NO_REPLACE",
        "primary_receipt_publication_mode": "CREATE_ONLY",
        "secondary_receipt_publication_mode": "CREATE_ONLY",
        "paired_receipts_required": True,
    }
    if (
        owner
        != {
            "identity": "ouyang",
            "approved": True,
            "scope": "one_disclosed_sealed_bundle_recovery_only",
        }
        or destination.get("absolute_path")
        != (root / REGISTERED_DESTINATION_RELATIVE).as_posix()
        or destination.get("required_absent_before_start") is not True
        or destination.get("publication_mode") != "ATOMIC_DIRECTORY_NO_REPLACE"
        or destination.get("bundle_schema_version")
        != "p4.2a-v2-heldout-rehearsal-bundle-v2.2"
        or destination.get("expected_bundle_status")
        != "PASS_REHEARSAL_V2_2_AWAITING_OWNER_REVIEW"
        or any(
            recovery_storage.get(key) != value
            for key, value in expected_storage_scalars.items()
        )
        or not isinstance(recovery_storage.get("primary_recovery_container"), str)
        or not Path(cast(str, recovery_storage.get("primary_recovery_container"))).is_absolute()
        or not isinstance(recovery_storage.get("secondary_recovery_container"), str)
        or not Path(cast(str, recovery_storage.get("secondary_recovery_container"))).is_absolute()
        or (
            root == REGISTERED_PROJECT_ROOT.absolute()
            and (
                recovery_storage.get("primary_recovery_container")
                != SERIES_2_PRIMARY_RECOVERY_CONTAINER.as_posix()
                or recovery_storage.get("secondary_recovery_container")
                != SERIES_2_SECONDARY_RECOVERY_CONTAINER.as_posix()
            )
        )
        or recovery_storage.get("primary_recovery_container")
        == recovery_storage.get("secondary_recovery_container")
        or interpreter
        != {
            "launcher_path": _VALIDATOR_FIXED_PYTHON,
            "launcher_sha256": (
                "f4cd716d4b54f205398bec6932cc59361b087494ca2ddb157a5e8631d4d6f863"
            ),
            "orig_argv_executable": _VALIDATOR_FIXED_ORIG_PYTHON,
            "orig_argv_executable_sha256": (
                "89c717ced41f6a395612366e5b038226d0d8fca36bbddd9321d385f5f370ebbe"
            ),
            "version": platform.python_version(),
        }
        or any(value is not False for value in locks.values())
        or exact_argv != expected_argv
        or exact_environment != _EXACT_ENVIRONMENT
    ):
        raise RehearsalV22ValidationError(
            "recovery R nested identity or registered values drifted"
        )
    if (
        r.get("schema_version") != SERIES_2_RECOVERY_AUTHORIZATION_SCHEMA
        or r.get("verdict")
        != "APPROVE_EXACTLY_ONE_SEALED_BUNDLE_RECOVERY_ZERO_PIPELINE_START_ZERO_AUTOMATIC_RETRY"
        or r.get("authorized_bundle_recovery_starts") != 1
        or r.get("authorized_pipeline_starts") != 0
        or r.get("automatic_retry_count") != 0
        or r.get("effect_authorization")
        != _object(
            _object(
                contract.get("recovery_authorization_contract"),
                "contract R",
            ).get("effect_authorization_exact"),
            "contract R effects",
        )
    ):
        raise RehearsalV22ValidationError("recovery R scope or effects drifted")
    if _command_sha256([_string(value, "recovery argv member") for value in exact_argv]) != r.get(
        "command_sha256"
    ) or _environment_sha256(
        {
            _string(key, "environment key"): _string(value, "environment value")
            for key, value in exact_environment.items()
        }
    ) != r.get("environment_sha256"):
        raise RehearsalV22ValidationError("recovery R argv or environment hash drifted")
    owner_confirmation = _object(b.get("owner_confirmation"), "recovery owner confirmation")
    authorized_scope = _object(b.get("authorized_scope"), "recovery authorized scope")
    explicit_exclusions = _object(
        b.get("explicit_exclusions"),
        "recovery explicit exclusions",
    )
    machine = _object(b.get("machine_boundary"), "recovery machine boundary")
    b_contract = _object(contract.get("recovery_owner_binding_contract"), "contract B")
    b_nested = _object(b_contract.get("nested_exact_field_sets"), "contract B nested fields")
    for value, key, label in (
        (owner_confirmation, "owner_confirmation", "recovery owner confirmation"),
        (authorized_scope, "authorized_scope", "recovery authorized scope"),
        (explicit_exclusions, "explicit_exclusions", "recovery explicit exclusions"),
        (machine, "machine_boundary", "recovery machine boundary"),
        (b_preflight, "registered_read_only_recovery_preflight", "recovery B preflight"),
        (b_q, "review_request_and_recovery_authorization", "recovery B Q reference"),
        (b_r, "review_request_and_recovery_authorization", "recovery B R reference"),
    ):
        _require_exact_keys(
            value,
            frozenset(cast(Sequence[str], b_nested.get(key, ()))),
            label,
        )
    _validate_timestamp_pair(
        {
            "created_at_utc": owner_confirmation.get("observed_at_utc"),
            "created_at_shanghai": owner_confirmation.get("observed_at_shanghai"),
        },
        "recovery owner confirmation",
    )
    q_requested_confirmation = _object(
        q.get("requested_owner_action_time_confirmation"),
        "Q requested owner confirmation",
    )
    if (
        b.get("schema_version") != SERIES_2_RECOVERY_OWNER_BINDING_SCHEMA
        or b.get("status") != "OWNER_CONFIRMATION_BOUND"
        or owner_confirmation.get("identity") != "ouyang"
        or owner_confirmation.get("source") != "业主向复核方当面确认，由复核方转达"
        or owner_confirmation.get("authorization_sha256") != r_sha
        or owner_confirmation.get("confirmation_text")
        != q_requested_confirmation.get("requested_exact_confirmation")
        or q_requested_confirmation.get("requested_exact_confirmation")
        is None
        or r_sha
        not in _string(
            q_requested_confirmation.get("requested_exact_confirmation"),
            "Q requested exact confirmation",
        )
        or "pipeline start 0"
        not in cast(str, q_requested_confirmation.get("requested_exact_confirmation"))
        or "automatic retry 0"
        not in cast(str, q_requested_confirmation.get("requested_exact_confirmation"))
        or any(value is not True for value in explicit_exclusions.values())
        or authorized_scope
        != {
            "series_token_sha256": expected_series_token_sha256,
            "selected_attempt_ordinal": 2,
            "authorized_bundle_recovery_starts": 1,
            "authorized_pipeline_starts": 0,
            "automatic_retry_count": 0,
            "scope": "one_disclosed_sealed_bundle_recovery_only",
        }
        or machine
        != {
            "consumed_by_recovery_runner": True,
            "evidence_only": False,
            "passed_as_bundle_recovery_confirmation_binding": True,
            "machine_recovery_authorization_remains_exactly_19_fields": True,
            "this_document_adds_no_field_to_the_19_field_authorization": True,
        }
    ):
        raise RehearsalV22ValidationError("recovery B owner or machine scope drifted")
    specs = (
        AuthorityCensusSpec(q_relative, q_sha, q_commit, AuthorityCensusRole.PINNED_SOURCE),
        AuthorityCensusSpec(r_relative, r_sha, r_commit, AuthorityCensusRole.PINNED_SOURCE),
        AuthorityCensusSpec(b_relative, b_sha, b_commit, AuthorityCensusRole.PINNED_SOURCE),
    )
    return _ValidatedRecoveryGovernance(
        contract=contract,
        latest_landed_contract=latest_landed_contract,
        preflight_document=preflight,
        preflight_census=preflight_census,
        landed_execution_epoch=landed_execution,
        q_path=root / q_relative,
        q_payload=q_payload,
        q_document=q,
        q_commit=q_commit,
        r_path=root / r_relative,
        r_payload=r_payload,
        r_document=r,
        r_commit=r_commit,
        b_path=root / b_relative,
        b_payload=b_payload,
        b_document=b,
        b_commit=b_commit,
        authority_specs=specs,
    )


def _validate_delegated_recovery_governance(
    governance: _ValidatedRecoveryGovernance,
    authorization: implementation.BundleRecoveryAuthorization,
    owner_binding: implementation.RecoveryOwnerBinding,
) -> None:
    if (
        not isinstance(authorization, implementation.BundleRecoveryAuthorization)
        or not isinstance(owner_binding, implementation.RecoveryOwnerBinding)
        or authorization.path.absolute() != governance.r_path
        or authorization.payload != governance.r_payload
        or authorization.sha256 != _sha256(governance.r_payload)
        or authorization.creating_commit != governance.r_commit
        or owner_binding.path.absolute() != governance.b_path
        or owner_binding.payload != governance.b_payload
        or owner_binding.sha256 != _sha256(governance.b_payload)
        or owner_binding.creating_commit != governance.b_commit
    ):
        raise RehearsalV22ValidationError("delegated recovery governance identity drifted")


def _live_anchor_from_recovery_governance(
    project_root: Path,
    governance: _ValidatedRecoveryGovernance,
    *,
    additional_census_specs: Sequence[AuthorityCensusSpec] = (),
    control_pass_nonce: object,
    work_tracker: _IndependentRecoveryWorkTracker,
) -> tuple[
    LiveExecutionAnchor,
    JsonObject,
    tuple[AuthorityCensusSpec, ...],
    _ControlSurfaceCacheEnvelope,
]:
    root = project_root.resolve(strict=True)
    execution = _object(
        governance.r_document.get("execution_epoch"),
        "recovery execution epoch",
    )
    contract = governance.contract
    latest_landed_contract = validate_epoch_9_latest_landed_execution_contract(
        root,
        execution_head=_git_bytes(root, "rev-parse", "HEAD")
        .decode("ascii", errors="strict")
        .strip(),
    )
    if (
        contract != governance.contract
        or latest_landed_contract != governance.latest_landed_contract
    ):
        raise RehearsalV22ValidationError("recovery governance contracts drifted")
    expected_execution_fields = tuple(
        _object(
            _object(
                contract.get("recovery_authorization_contract"),
                "contract recovery authorization",
            ).get("nested_exact_field_sets"),
            "contract R nested fields",
        )["execution_epoch"]
    )
    _require_exact_keys(execution, frozenset(expected_execution_fields), "recovery execution epoch")
    if execution.get("epoch") != LATEST_LANDED_EXECUTION_EPOCH:
        raise RehearsalV22ValidationError("recovery execution epoch is not latest landed epoch 9")
    implementation_commit = _git_commit(
        root,
        execution.get("implementation_commit"),
        "recovery execution implementation",
    )
    owner = _validate_authority_ref(
        execution.get("owner_exact_surface_authorization"),
        "recovery execution owner authority",
    )
    review = _validate_authority_ref(
        execution.get("independent_implementation_review"),
        "recovery execution review",
    )
    landing_report = _validate_authority_ref(
        execution.get("landing_report"),
        "recovery execution landing report",
    )
    merge_commit = _git_commit(root, execution.get("merge_commit"), "recovery merge commit")
    execution_head = _git_commit(
        root,
        _git_bytes(root, "rev-parse", "HEAD").decode("ascii", errors="strict").strip(),
        "recovery execution HEAD",
    )
    additional = (
        *governance.authority_specs,
        AuthorityCensusSpec(
            path=cast(str, owner["path"]),
            pinned_sha256=cast(str, owner["sha256"]),
            pinned_creating_commit=cast(str, owner["creating_commit"]),
            role=AuthorityCensusRole.PINNED_SOURCE,
        ),
        AuthorityCensusSpec(
            path=cast(str, review["path"]),
            pinned_sha256=cast(str, review["sha256"]),
            pinned_creating_commit=cast(str, review["creating_commit"]),
            role=AuthorityCensusRole.PINNED_LANDING_PROJECTION,
            declared_landing_projection_commit=merge_commit,
        ),
        AuthorityCensusSpec(
            path=cast(str, landing_report["path"]),
            pinned_sha256=cast(str, landing_report["sha256"]),
            pinned_creating_commit=cast(str, landing_report["creating_commit"]),
            role=AuthorityCensusRole.PINNED_SOURCE,
        ),
        *additional_census_specs,
    )
    refs_before_census = _git_ref_snapshot(root)
    census = _real_lineage_census(
        root,
        execution_head=execution_head,
        additional_specs=additional,
        work_tracker=work_tracker,
    )
    _validate_epoch_8_census_delta(
        baseline=governance.preflight_census,
        observed=census,
        additional_specs=additional,
    )
    if not additional_census_specs:
        _validate_epoch_8_qrb_ref_delta(
            root,
            governance=governance,
            execution_head=execution_head,
        )
    census_sha = _sha256(_canonical_json_bytes(census))
    anchor = LiveExecutionAnchor(
        implementation_epoch=LATEST_LANDED_EXECUTION_EPOCH,
        implementation_commit=implementation_commit,
        control_merkle_root_sha256=_sha(
            execution.get("control_merkle_root_sha256"),
            "recovery execution control root",
        ),
        control_record_count=_integer(
            execution.get("control_record_count"),
            "recovery execution control record count",
            minimum=1,
        ),
        execution_head=execution_head,
        owner_surface_authorization=owner,
        independent_implementation_review=review,
        landing_commit=merge_commit,
        landing_report=landing_report,
        real_lineage_census_sha256=census_sha,
        require_current=True,
    )
    _root, _head, live_control = _validate_live_execution_anchor_identity(
        root,
        anchor,
        control_pass_nonce=control_pass_nonce,
        ref_snapshot_sha256=_sha256(refs_before_census),
        lineage_census_sha256=census_sha,
    )
    _assert_git_census_state_unchanged(
        root,
        expected_refs=refs_before_census,
        expected_head=execution_head,
    )
    return anchor, census, additional, live_control


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
            leaf_domain + relative.encode("utf-8") + b"\0" + hashlib.sha256(payload).digest()
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
        (SERIES_2_TOKEN_SEED_SHA256 + "\0" + REHEARSAL_ID + "\0" + destination.as_posix()).encode(
            "utf-8"
        )
    ).hexdigest()
    if root == REGISTERED_PROJECT_ROOT.resolve():
        if token != SERIES_2_REGISTERED_SERIES_TOKEN:
            raise RehearsalV22ValidationError("registered series-2 token drifted")
        return SERIES_2_PRIMARY_LEDGER_ROOT
    return (
        root.parent
        / f"{root.name}-EVIDENCE-DO-NOT-DELETE"
        / "P4.2a/v2.2"
        / f"SERIES-000002-{token}"
        / "PRIMARY-LEDGER-DO-NOT-DELETE"
    )


@dataclass(frozen=True)
class BindingView:
    mode: str
    project_root: Path
    absolute_destination: Path
    series_token_sha256: str
    ledger_root: Path
    primary_series_container: Path
    primary_receipt_root: Path
    secondary_series_container: Path
    secondary_snapshot_root: Path
    secondary_receipt_root: Path


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
    live_identities: Mapping[str, tuple[int, ...]]
    archive_payloads: Mapping[str, bytes]


def _historical_selected_anchor(
    replay: HistoryReplay,
    expected: HistoricalSelectedAnchor,
) -> HistoricalSelectedAnchor:
    selected_candidate = replay.source_records[1][1] if len(replay.source_records) == 2 else None
    if (
        replay.started_count != 2
        or replay.failed_count != 1
        or replay.incomplete_count != 0
        or replay.selected_attempt_ordinal != expected.selected_attempt_ordinal
        or replay.selected_implementation_epoch != expected.implementation_epoch
        or selected_candidate is None
    ):
        raise RehearsalV22ValidationError("sealed selected history does not match epoch-6 anchor")
    observed = HistoricalSelectedAnchor(
        implementation_epoch=replay.selected_implementation_epoch,
        implementation_commit=replay.selected_implementation_commit,
        control_merkle_root_sha256=_sha(
            selected_candidate.get("control_surface_root_sha256"),
            "selected candidate control root",
        ),
        history_root_sha256=replay.history_root_sha256,
        live_ledger_root_sha256=replay.live_ledger_root_sha256,
        selected_attempt_ordinal=expected.selected_attempt_ordinal,
        require_current=False,
    )
    if observed != expected:
        raise RehearsalV22ValidationError("sealed selected history does not match its bound anchor")
    return observed


_CONTROL_CACHE_RECORD_FIELDS = frozenset(
    {
        "logical_name",
        "bundle_relative_path",
        "source_kind",
        "repository_path",
        "bytes",
        "sha256",
    }
)
_CONTROL_CACHE_MANIFEST_RELATIVE = "archive/control-surface/manifest.json"
_CONTROL_CACHE_PYTHON_RUNTIME_RELATIVE = "archive/control-surface/root/runtime/python.json"
_CONTROL_CACHE_PACKAGE_RUNTIME_RELATIVE = "archive/control-surface/root/runtime/packages.json"


def _control_cache_root_identity(project_root: Path) -> tuple[Path, int, int]:
    absolute = project_root.absolute()
    resolved = project_root.resolve(strict=True)
    if absolute != resolved or project_root.is_symlink():
        raise RehearsalV22ValidationError(
            "control cache project root is not one resolved real directory"
        )
    status = resolved.stat()
    if not stat.S_ISDIR(status.st_mode):
        raise RehearsalV22ValidationError("control cache project root is not a directory")
    return resolved, status.st_dev, status.st_ino


def _frozen_control_record_document(record: _FrozenControlRecord) -> JsonObject:
    return {
        "logical_name": record.logical_name,
        "bundle_relative_path": record.bundle_relative_path,
        "source_kind": record.source_kind,
        "repository_path": record.repository_path,
        "bytes": record.byte_count,
        "sha256": record.sha256,
    }


def _control_cache_descriptor(cache: _ControlSurfaceCacheEnvelope) -> JsonObject:
    return {
        "pass_kind": cache.pass_kind,
        "selected_epoch": cache.selected_epoch,
        "resolved_project_root": cache.resolved_project_root,
        "root_st_dev": cache.root_st_dev,
        "root_st_ino": cache.root_st_ino,
        "execution_head": cache.execution_head,
        "ref_snapshot_sha256": cache.ref_snapshot_sha256,
        "lineage_census_sha256": cache.lineage_census_sha256,
        "implementation_commit": cache.implementation_commit,
        "records": [
            {
                **_frozen_control_record_document(record),
                "current_byte_required": record.current_byte_required,
            }
            for record in cache.records
        ],
        "payload_facts": [
            {
                "bundle_relative_path": fact.bundle_relative_path,
                "bytes": fact.byte_count,
                "sha256": fact.sha256,
            }
            for fact in cache.payload_facts
        ],
        "manifest_bytes": len(cache.manifest_payload),
        "manifest_sha256": cache.manifest_sha256,
        "merkle_root_sha256": cache.merkle_root_sha256,
        "ast_closure_paths": list(cache.ast_closure_paths),
        "loaded_repository_sources": list(cache.loaded_repository_sources),
        "python_inventory_bytes": cache.python_inventory_bytes,
        "python_inventory_sha256": cache.python_inventory_sha256,
        "package_inventory_bytes": cache.package_inventory_bytes,
        "package_inventory_sha256": cache.package_inventory_sha256,
    }


def _control_cache_merkle_root(
    payload_facts: Sequence[_FrozenPayloadFact],
    *,
    manifest_payload: bytes,
) -> str:
    facts = (
        *payload_facts,
        _FrozenPayloadFact(
            bundle_relative_path=_CONTROL_CACHE_MANIFEST_RELATIVE,
            byte_count=len(manifest_payload),
            sha256=_sha256(manifest_payload),
        ),
    )
    leaves = [
        hashlib.sha256(
            b"p4.2a-rehearsal-leaf-v2.2\0"
            + fact.bundle_relative_path.encode("utf-8")
            + b"\0"
            + bytes.fromhex(fact.sha256)
        ).digest()
        for fact in sorted(facts, key=lambda item: item.bundle_relative_path.encode("utf-8"))
    ]
    return _merkle_from_digests(
        leaves,
        node_domain=b"p4.2a-rehearsal-node-v2.2\0",
    )


def _validate_control_surface_cache_integrity(
    project_root: Path,
    *,
    implementation_commit: str,
    execution_head: str,
    pass_kind: Literal["LIVE_CURRENT", "HISTORICAL_SELECTED_EPOCH_6"],
    selected_epoch: int | None,
    ref_snapshot_sha256: str | None,
    lineage_census_sha256: str | None,
    pass_nonce: object,
    cache: _ControlSurfaceCacheEnvelope,
) -> None:
    root, root_st_dev, root_st_ino = _control_cache_root_identity(project_root)
    if (
        type(cache) is not _ControlSurfaceCacheEnvelope
        or cache._nonce is not pass_nonce
        or cache.pass_kind != pass_kind
        or cache.selected_epoch != selected_epoch
        or cache.resolved_project_root != root.as_posix()
        or cache.root_st_dev != root_st_dev
        or cache.root_st_ino != root_st_ino
        or cache.implementation_commit != implementation_commit
        or cache.execution_head != execution_head
        or _COMMIT_PATTERN.fullmatch(cache.execution_head) is None
        or cache.ref_snapshot_sha256 != ref_snapshot_sha256
        or cache.lineage_census_sha256 != lineage_census_sha256
        or (
            cache.pass_kind == "LIVE_CURRENT"
            and (
                cache.selected_epoch is not None
                or cache.ref_snapshot_sha256 is None
                or cache.lineage_census_sha256 is None
            )
        )
        or (
            cache.pass_kind == "HISTORICAL_SELECTED_EPOCH_6"
            and (
                cache.selected_epoch != 6
                or cache.ref_snapshot_sha256 is not None
                or cache.lineage_census_sha256 is not None
            )
        )
        or (
            cache.ref_snapshot_sha256 is not None
            and _SHA256_PATTERN.fullmatch(cache.ref_snapshot_sha256) is None
        )
        or (
            cache.lineage_census_sha256 is not None
            and _SHA256_PATTERN.fullmatch(cache.lineage_census_sha256) is None
        )
        or not cache.records
        or not _sha(cache.manifest_sha256, "cached control manifest SHA")
        or not _sha(cache.merkle_root_sha256, "cached control Merkle root")
        or cache.integrity_sha256
        != _sha256(_canonical_json_bytes(_control_cache_descriptor(cache)))
    ):
        raise RehearsalV22ValidationError("control cache identity or integrity drifted")
    manifest = _strict_canonical_json_loads(
        cache.manifest_payload,
        label="cached control manifest",
    )
    manifest_records = [_frozen_control_record_document(record) for record in cache.records]
    if (
        set(manifest) != {"schema_version", "files"}
        or manifest.get("schema_version") != CONTROL_MANIFEST_SCHEMA
        or manifest.get("files") != manifest_records
        or _sha256(cache.manifest_payload) != cache.manifest_sha256
    ):
        raise RehearsalV22ValidationError("control cache manifest drifted")
    record_paths = tuple(record.bundle_relative_path for record in cache.records)
    fact_paths = tuple(fact.bundle_relative_path for fact in cache.payload_facts)
    if (
        record_paths != tuple(sorted(record_paths, key=lambda value: value.encode("utf-8")))
        or len(set(record_paths)) != len(record_paths)
        or fact_paths != record_paths
        or len(set(fact_paths)) != len(fact_paths)
        or any(
            fact.byte_count != record.byte_count or fact.sha256 != record.sha256
            for record, fact in zip(cache.records, cache.payload_facts, strict=True)
        )
        or any(
            record.current_byte_required
            is not (
                record.repository_path is not None
                and (
                    _CONTROL_GOVERNANCE_AUTHORITIES.get(record.repository_path) is None
                    or _CONTROL_GOVERNANCE_AUTHORITIES[record.repository_path][2] is True
                )
            )
            for record in cache.records
        )
        or _control_cache_merkle_root(
            cache.payload_facts,
            manifest_payload=cache.manifest_payload,
        )
        != cache.merkle_root_sha256
    ):
        raise RehearsalV22ValidationError("control cache record, payload, or Merkle facts drifted")
    repository_paths = {
        record.repository_path for record in cache.records if record.repository_path is not None
    }
    if (
        cache.ast_closure_paths != tuple(sorted(set(cache.ast_closure_paths)))
        or cache.loaded_repository_sources != tuple(sorted(set(cache.loaded_repository_sources)))
        or not set(cache.loaded_repository_sources).issubset(cache.ast_closure_paths)
        or not set(cache.ast_closure_paths).issubset(repository_paths)
    ):
        raise RehearsalV22ValidationError("control cache closure facts drifted")
    fact_by_path = {fact.bundle_relative_path: fact for fact in cache.payload_facts}
    python_fact = fact_by_path.get(_CONTROL_CACHE_PYTHON_RUNTIME_RELATIVE)
    package_fact = fact_by_path.get(_CONTROL_CACHE_PACKAGE_RUNTIME_RELATIVE)
    if (
        python_fact is None
        or package_fact is None
        or python_fact.byte_count != cache.python_inventory_bytes
        or python_fact.sha256 != cache.python_inventory_sha256
        or package_fact.byte_count != cache.package_inventory_bytes
        or package_fact.sha256 != cache.package_inventory_sha256
    ):
        raise RehearsalV22ValidationError("control cache runtime facts drifted")


def _freeze_control_surface_cache(
    project_root: Path,
    *,
    implementation_commit: str,
    execution_head: str,
    pass_kind: Literal["LIVE_CURRENT", "HISTORICAL_SELECTED_EPOCH_6"],
    selected_epoch: int | None,
    ref_snapshot_sha256: str | None,
    lineage_census_sha256: str | None,
    pass_nonce: object,
    control: implementation.ControlSurface,
) -> _ControlSurfaceCacheEnvelope:
    if (
        type(control) is not implementation.ControlSurface
        or control.implementation_commit != implementation_commit
    ):
        raise RehearsalV22ValidationError("control cache source is not its implementation")
    if (pass_kind == "LIVE_CURRENT") != (selected_epoch is None):
        raise RehearsalV22ValidationError("control cache pass kind or epoch is inconsistent")
    if pass_kind == "HISTORICAL_SELECTED_EPOCH_6" and selected_epoch != 6:
        raise RehearsalV22ValidationError("historical control cache is not selected epoch 6")
    root, root_st_dev, root_st_ino = _control_cache_root_identity(project_root)
    frozen_records: list[_FrozenControlRecord] = []
    for raw_record in control.records:
        record = _object(raw_record, "control cache source record")
        if set(record) != _CONTROL_CACHE_RECORD_FIELDS:
            raise RehearsalV22ValidationError("control cache source record fields drifted")
        logical_name = record.get("logical_name")
        source_kind = record.get("source_kind")
        if not isinstance(logical_name, str) or not logical_name:
            raise RehearsalV22ValidationError("control cache logical name is malformed")
        if not isinstance(source_kind, str) or not source_kind:
            raise RehearsalV22ValidationError("control cache source kind is malformed")
        bundle_relative_path = _relative(
            record.get("bundle_relative_path"),
            "control cache bundle path",
        )
        repository_raw = record.get("repository_path")
        repository_path = (
            None
            if repository_raw is None
            else _relative(repository_raw, "control cache repository path")
        )
        byte_count = record.get("bytes")
        digest = record.get("sha256")
        if type(byte_count) is not int or byte_count < 0 or not isinstance(digest, str):
            raise RehearsalV22ValidationError("control cache byte or SHA fact is malformed")
        _sha(digest, "control cache source SHA")
        governance = (
            None
            if repository_path is None
            else _CONTROL_GOVERNANCE_AUTHORITIES.get(repository_path)
        )
        frozen_records.append(
            _FrozenControlRecord(
                logical_name=logical_name,
                bundle_relative_path=bundle_relative_path,
                source_kind=source_kind,
                repository_path=repository_path,
                byte_count=byte_count,
                sha256=digest,
                current_byte_required=(
                    repository_path is not None and (governance is None or governance[2] is True)
                ),
            )
        )
    payload_items = tuple(
        sorted(control.payloads.items(), key=lambda item: item[0].encode("utf-8"))
    )
    payload_facts: list[_FrozenPayloadFact] = []
    for relative, payload in payload_items:
        if not isinstance(relative, str) or type(payload) is not bytes:
            raise RehearsalV22ValidationError("control cache payload is malformed")
        payload_facts.append(
            _FrozenPayloadFact(
                bundle_relative_path=_relative(relative, "control cache payload path"),
                byte_count=len(payload),
                sha256=_sha256(payload),
            )
        )
    draft = _ControlSurfaceCacheEnvelope(
        _nonce=pass_nonce,
        pass_kind=pass_kind,
        selected_epoch=selected_epoch,
        resolved_project_root=root.as_posix(),
        root_st_dev=root_st_dev,
        root_st_ino=root_st_ino,
        execution_head=execution_head,
        ref_snapshot_sha256=ref_snapshot_sha256,
        lineage_census_sha256=lineage_census_sha256,
        implementation_commit=implementation_commit,
        records=tuple(frozen_records),
        payload_facts=tuple(payload_facts),
        manifest_payload=bytes(control.manifest_payload),
        manifest_sha256=_sha256(control.manifest_payload),
        merkle_root_sha256=control.merkle_root_sha256,
        ast_closure_paths=tuple(control.ast_closure_paths),
        loaded_repository_sources=tuple(control.loaded_repository_sources),
        python_inventory_bytes=len(control.python_inventory),
        python_inventory_sha256=_sha256(control.python_inventory),
        package_inventory_bytes=len(control.package_inventory),
        package_inventory_sha256=_sha256(control.package_inventory),
        integrity_sha256="",
    )
    cache = replace(
        draft,
        integrity_sha256=_sha256(_canonical_json_bytes(_control_cache_descriptor(draft))),
    )
    raw_payloads = dict(payload_items)
    if (
        tuple(fact.bundle_relative_path for fact in cache.payload_facts)
        != tuple(record.bundle_relative_path for record in cache.records)
        or any(
            len(raw_payloads[fact.bundle_relative_path]) != fact.byte_count
            or _sha256(raw_payloads[fact.bundle_relative_path]) != fact.sha256
            for fact in cache.payload_facts
        )
        or cache.merkle_root_sha256
        != _generic_merkle_root(
            {
                **raw_payloads,
                _CONTROL_CACHE_MANIFEST_RELATIVE: cache.manifest_payload,
            }
        )
    ):
        raise RehearsalV22ValidationError("control cache source payloads are inconsistent")
    _validate_control_surface_cache_integrity(
        root,
        implementation_commit=implementation_commit,
        execution_head=execution_head,
        pass_kind=pass_kind,
        selected_epoch=selected_epoch,
        ref_snapshot_sha256=ref_snapshot_sha256,
        lineage_census_sha256=lineage_census_sha256,
        pass_nonce=pass_nonce,
        cache=cache,
    )
    return cache


def _revalidate_cached_current_control_surface(
    project_root: Path,
    *,
    implementation_commit: str,
    execution_head: str,
    ref_snapshot_sha256: str,
    lineage_census_sha256: str,
    pass_nonce: object,
    cache: _ControlSurfaceCacheEnvelope,
) -> None:
    """Recheck mutable inputs from an independently frozen fact set."""

    root = project_root.resolve(strict=True)
    _validate_control_surface_cache_integrity(
        root,
        implementation_commit=implementation_commit,
        execution_head=execution_head,
        pass_kind="LIVE_CURRENT",
        selected_epoch=None,
        ref_snapshot_sha256=ref_snapshot_sha256,
        lineage_census_sha256=lineage_census_sha256,
        pass_nonce=pass_nonce,
        cache=cache,
    )
    for record in cache.records:
        if not record.current_byte_required:
            continue
        relative = cast(str, record.repository_path)
        payload = _regular_bytes(
            _safe_path(root, relative, f"cached current control {relative}"),
            f"cached current control {relative}",
        )
        if len(payload) != record.byte_count or _sha256(payload) != record.sha256:
            raise RehearsalV22ValidationError(f"cached current control bytes drifted: {relative}")
    observed_loaded_sources = tuple(sorted(implementation._classify_loaded_module_origins(root)))
    if observed_loaded_sources != cache.loaded_repository_sources:
        raise RehearsalV22ValidationError("cached live loaded-source inventory drifted")
    python_payload, package_payload = _independent_runtime_inventory()
    if (
        len(python_payload) != cache.python_inventory_bytes
        or _sha256(python_payload) != cache.python_inventory_sha256
        or len(package_payload) != cache.package_inventory_bytes
        or _sha256(package_payload) != cache.package_inventory_sha256
    ):
        raise RehearsalV22ValidationError("cached live runtime inventory drifted")


def _validate_live_execution_anchor_identity(
    project_root: Path,
    anchor: LiveExecutionAnchor,
    *,
    control_pass_nonce: object,
    ref_snapshot_sha256: str,
    lineage_census_sha256: str,
    cached_current_control: _ControlSurfaceCacheEnvelope | None = None,
) -> tuple[Path, str, _ControlSurfaceCacheEnvelope]:
    root = project_root.resolve(strict=True)
    if (
        anchor.implementation_epoch != LATEST_LANDED_EXECUTION_EPOCH
        or anchor.require_current is not True
    ):
        raise RehearsalV22ValidationError("live execution anchor epoch or current policy drifted")
    implementation_commit = _git_commit(
        root,
        anchor.implementation_commit,
        "live epoch-9 implementation commit",
    )
    if (
        _git_parents(root, implementation_commit) != (EPOCH_9_SURFACE_AUTHORITY_COMMIT,)
        or _diff_name_status(root, EPOCH_9_SURFACE_AUTHORITY_COMMIT, implementation_commit)
        != (
            ("M", "scripts/p4_2a_v2_2_heldout_rehearsal.py"),
            ("M", "scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py"),
            ("M", "tests/test_p4_2a_v2_2_heldout_rehearsal_runner.py"),
            ("M", "tests/test_p4_2a_v2_2_heldout_rehearsal_validator.py"),
        )
    ):
        raise RehearsalV22ValidationError("live epoch-9 implementation surface drifted")
    execution_head = _git_commit(root, anchor.execution_head, "live execution HEAD")
    if not _git_is_ancestor(root, implementation_commit, execution_head):
        raise RehearsalV22ValidationError("live implementation is outside execution HEAD")
    owner = _validate_authority_ref(
        anchor.owner_surface_authorization,
        "live epoch-9 owner surface authority",
    )
    _require_equal(
        owner,
        {
            "path": EPOCH_9_SURFACE_AUTHORITY_RELATIVE.as_posix(),
            "sha256": EPOCH_9_SURFACE_AUTHORITY_SHA256,
            "creating_commit": EPOCH_9_SURFACE_AUTHORITY_COMMIT,
            "unique_a_history_verified": True,
        },
        "live epoch-9 owner surface authority",
    )
    _unique_a_authority(root, owner, require_worktree=True)
    review = _validate_authority_ref(
        anchor.independent_implementation_review,
        "live epoch-9 implementation review",
    )
    _validate_implementation_review_authority(
        root,
        review,
        implementation_commit=implementation_commit,
        execution_head=execution_head,
        require_worktree=True,
    )
    landing = _git_commit(root, anchor.landing_commit, "live epoch-9 landing commit")
    if not _git_is_ancestor(root, implementation_commit, landing) or not _git_is_ancestor(
        root,
        landing,
        execution_head,
    ):
        raise RehearsalV22ValidationError("live epoch-9 landing topology drifted")
    landing_report = _validate_authority_ref(
        anchor.landing_report,
        "live epoch-9 landing report",
    )
    _unique_a_authority(root, landing_report, require_worktree=True)
    if cached_current_control is None:
        observed_control = implementation.build_control_surface(
            root,
            implementation_commit,
            require_current=True,
        )
        control = _freeze_control_surface_cache(
            root,
            implementation_commit=implementation_commit,
            execution_head=execution_head,
            pass_kind="LIVE_CURRENT",
            selected_epoch=None,
            ref_snapshot_sha256=ref_snapshot_sha256,
            lineage_census_sha256=lineage_census_sha256,
            pass_nonce=control_pass_nonce,
            control=observed_control,
        )
    else:
        _revalidate_cached_current_control_surface(
            root,
            implementation_commit=implementation_commit,
            execution_head=execution_head,
            ref_snapshot_sha256=ref_snapshot_sha256,
            lineage_census_sha256=lineage_census_sha256,
            pass_nonce=control_pass_nonce,
            cache=cached_current_control,
        )
        control = cached_current_control
    if (
        control.merkle_root_sha256 != anchor.control_merkle_root_sha256
        or len(control.records) != anchor.control_record_count
    ):
        raise RehearsalV22ValidationError("live epoch-9 control root or count drifted")
    _validate_live_module_identity(root, anchor)
    return root, execution_head, control


def _validate_live_execution_anchor(
    project_root: Path,
    anchor: LiveExecutionAnchor,
    *,
    additional_census_specs: Sequence[AuthorityCensusSpec] = (),
    control_pass_nonce: object,
    ref_snapshot_sha256: str,
    cached_current_control: _ControlSurfaceCacheEnvelope | None = None,
    work_tracker: _IndependentRecoveryWorkTracker,
) -> JsonObject:
    root, execution_head, _control = _validate_live_execution_anchor_identity(
        project_root,
        anchor,
        control_pass_nonce=control_pass_nonce,
        ref_snapshot_sha256=ref_snapshot_sha256,
        lineage_census_sha256=anchor.real_lineage_census_sha256,
        cached_current_control=cached_current_control,
    )
    census = _real_lineage_census(
        root,
        execution_head=execution_head,
        additional_specs=additional_census_specs,
        work_tracker=work_tracker,
    )
    if _sha256(_canonical_json_bytes(census)) != anchor.real_lineage_census_sha256:
        raise RehearsalV22ValidationError("live epoch-8 census binding drifted")
    return census


def _binding_view(value: object) -> BindingView:
    if not isinstance(value, implementation.ExecutionBinding):
        raise RehearsalV22ValidationError("implementation execution binding is malformed")
    try:
        mode = _string(value.mode, "execution binding mode")
        project_root = value.project_root.resolve(strict=True)
        destination = value.destination.absolute()
        token = _sha(value.series_token_sha256, "execution series token")
        ledger = value.ledger_root.absolute()
        primary_container = value.primary_series_container.absolute()
        primary_receipts = value.primary_receipt_root.absolute()
        secondary_container = value.secondary_series_container.absolute()
        secondary_snapshots = value.secondary_snapshot_root.absolute()
        secondary_receipts = value.secondary_receipt_root.absolute()
    except (AttributeError, OSError) as exc:
        raise RehearsalV22ValidationError("implementation execution binding is malformed") from exc
    if mode not in {"REGISTERED_OFFICIAL", "DISPOSABLE_FULL_SHAPE_TEST"}:
        raise RehearsalV22ValidationError("execution binding mode is unknown")
    expected_destination = project_root / REGISTERED_DESTINATION_RELATIVE
    expected_token = hashlib.sha256(
        (
            SERIES_2_TOKEN_SEED_SHA256
            + "\0"
            + REHEARSAL_ID
            + "\0"
            + expected_destination.absolute().as_posix()
        ).encode("utf-8")
    ).hexdigest()
    registered = REGISTERED_PROJECT_ROOT.absolute()
    if mode == "REGISTERED_OFFICIAL":
        expected_primary_container = SERIES_2_PRIMARY_SERIES_CONTAINER
        expected_secondary_container = SERIES_2_SECONDARY_SERIES_CONTAINER
    else:
        expected_primary_container = (
            project_root.parent
            / f"{project_root.name}-EVIDENCE-DO-NOT-DELETE"
            / "P4.2a/v2.2"
            / f"SERIES-000002-{expected_token}"
        )
        expected_secondary_container = (
            project_root.parent
            / f"{project_root.name}-EVIDENCE-MIRROR-DO-NOT-DELETE"
            / "P4.2a/v2.2"
            / f"SERIES-000002-{expected_token}"
        )
    expected_ledger = expected_primary_container / "PRIMARY-LEDGER-DO-NOT-DELETE"
    expected_primary_receipts = expected_primary_container / "MIRROR-RECEIPTS-DO-NOT-DELETE"
    expected_secondary_snapshots = (
        expected_secondary_container / "SEALED-LEDGER-SNAPSHOTS-DO-NOT-DELETE"
    )
    expected_secondary_receipts = expected_secondary_container / "MIRROR-RECEIPTS-DO-NOT-DELETE"
    if (
        destination != expected_destination
        or token != expected_token
        or ledger != expected_ledger
        or primary_container != expected_primary_container
        or primary_receipts != expected_primary_receipts
        or secondary_container != expected_secondary_container
        or secondary_snapshots != expected_secondary_snapshots
        or secondary_receipts != expected_secondary_receipts
    ):
        raise RehearsalV22ValidationError("implementation execution binding derivation drifted")
    if mode == "REGISTERED_OFFICIAL":
        if project_root != registered or token != SERIES_2_REGISTERED_SERIES_TOKEN:
            raise RehearsalV22ValidationError("official execution binding is not canonical")
        if _validator_os.path.lexists(SERIES_2_LEGACY_LEDGER_ROOT):
            raise RehearsalV22ValidationError(
                "official execution binding found the lost series ledger"
            )
        if _validator_os.path.lexists(SERIES_2_RETIRED_V2_1_CLAIM):
            raise RehearsalV22ValidationError(
                "official execution binding found the retired v2.1 claim"
            )
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
        primary_series_container=primary_container,
        primary_receipt_root=primary_receipts,
        secondary_series_container=secondary_container,
        secondary_snapshot_root=secondary_snapshots,
        secondary_receipt_root=secondary_receipts,
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
        raise RehearsalV22ValidationError("official validation action ordinal binding drifted")
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
            raise RehearsalV22ValidationError("canonical project root is aliased")
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
        "private_rebase_capability_validated": (binding.mode == "DISPOSABLE_FULL_SHAPE_TEST"),
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
        raise RehearsalV22ValidationError("bundle is outside its closure-authorized evidence root")
    return authorized


def _audit_hook_source_map(sources: Mapping[str, bytes]) -> dict[str, int]:
    """Count syntactic audit-hook installers in commit-bound Python sources."""

    result: dict[str, int] = {}
    for relative, payload in sorted(sources.items(), key=lambda item: item[0].encode("utf-8")):
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
            if (isinstance(function, ast.Attribute) and function.attr == "addaudithook") or (
                isinstance(function, ast.Name) and function.id == "addaudithook"
            ):
                count += 1
        if count:
            result[relative] = count
    return result


def _validate_module_identity(project_root: Path, implementation_commit: str) -> None:
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
        relative: count for relative, _module_name, count in _INERT_HISTORICAL_AUDIT_HOOK_SOURCES
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
    if observed_historical != expected_historical or active_hook_sources != {
        "scripts/p4_2a_v2_2_heldout_rehearsal.py": 1
    }:
        raise RehearsalV22ValidationError(
            "implementation is not the sole process audit-hook installer"
        )

    base_runner_module = sys.modules.get("scripts.rehearse_p4_2a_v2_heldout_full_path")
    base_runner_payload = _regular_bytes(
        project_root / "scripts/rehearse_p4_2a_v2_heldout_full_path.py",
        "v2 pure control helper",
    )
    if (
        sys.modules.get("scripts.p4_2a_v2_2_heldout_rehearsal") is not _implementation_module
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
        audit_policy_id = _integer(observation.audit_policy_object_id, "audit policy id", minimum=1)
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
    _validated_implementation_blob(
        project_root=project_root,
        implementation_commit=implementation_commit,
        relative_path="scripts/p4_2a_v2_2_heldout_rehearsal.py",
        expected_sha256=digest,
        require_current=True,
    )


def _validate_historical_module_identity(
    project_root: Path,
    anchor: HistoricalSelectedAnchor,
) -> None:
    if (
        anchor.implementation_epoch != HISTORICAL_SELECTED_EPOCH
        or anchor.implementation_commit != HISTORICAL_SELECTED_IMPLEMENTATION_COMMIT
        or anchor.require_current is not False
    ):
        raise RehearsalV22ValidationError("historical module anchor drifted")
    authority_surface = _independent_local_import_closure(
        project_root=project_root,
        implementation_commit=anchor.implementation_commit,
    )
    for relative in IMPLEMENTATION_PATHS[3:]:
        authority_surface[relative] = _git_blob(
            project_root,
            anchor.implementation_commit,
            relative,
        )
    audit_hook_sources = _audit_hook_source_map(authority_surface)
    historical_paths = frozenset(
        relative for relative, _module_name, _count in _INERT_HISTORICAL_AUDIT_HOOK_SOURCES
    )
    expected_historical = {
        relative: count for relative, _module_name, count in _INERT_HISTORICAL_AUDIT_HOOK_SOURCES
    }
    if {
        relative: audit_hook_sources.get(relative, 0) for relative in historical_paths
    } != expected_historical or {
        relative: count
        for relative, count in audit_hook_sources.items()
        if relative not in historical_paths
    } != {"scripts/p4_2a_v2_2_heldout_rehearsal.py": 1}:
        raise RehearsalV22ValidationError("historical module audit-hook identity drifted")
    for relative in historical_paths:
        if authority_surface.get(relative) != _git_blob(
            project_root,
            _V2_1_IMPLEMENTATION_COMMIT,
            relative,
        ):
            raise RehearsalV22ValidationError(
                f"historical inert authority bytes drifted: {relative}"
            )


def _validate_live_module_identity(
    project_root: Path,
    anchor: LiveExecutionAnchor,
) -> None:
    if (
        anchor.implementation_epoch != LATEST_LANDED_EXECUTION_EPOCH
        or anchor.require_current is not True
    ):
        raise RehearsalV22ValidationError("live module anchor drifted")
    _validate_module_identity(project_root, anchor.implementation_commit)


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
    _require_exact_keys(series, _SERIES_2_FIELDS, "live series.json")
    _require_equal(series["schema_version"], SERIES_2_SERIES_SCHEMA_VERSION, "series schema")
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
    _require_equal(series["first_validated_candidate_closes_series"], True, "series close policy")
    _require_equal(
        series["implementation_epoch_origin"],
        SERIES_2_EPOCH_ORIGIN,
        "series implementation epoch origin",
    )
    prereg = _validate_authority_ref(series["preregistration"], "series preregistration")
    _require_equal(
        prereg["path"],
        SERIES_2_PREREGISTRATION_RELATIVE.as_posix(),
        "series prereg path",
    )
    _require_equal(prereg["sha256"], SERIES_2_PREREGISTRATION_SHA256, "series prereg SHA")
    _require_equal(prereg["creating_commit"], preregistration_commit, "series prereg commit")
    bundle_schema = _validate_file_ref(series["bundle_schema"], "series bundle schema")
    _require_equal(
        bundle_schema,
        {
            "path": SERIES_2_BUNDLE_SCHEMA_RELATIVE.as_posix(),
            "sha256": SERIES_2_BUNDLE_SCHEMA_SHA256,
        },
        "series bundle schema",
    )
    release_schema = _validate_file_ref(series["release_schema"], "series release schema")
    _require_equal(
        release_schema,
        {
            "path": SERIES_2_RELEASE_SCHEMA_RELATIVE.as_posix(),
            "sha256": SERIES_2_RELEASE_SCHEMA_SHA256,
        },
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


def _epoch_map(
    bundle: Mapping[str, Any],
    *,
    epoch_origin: int = 1,
) -> dict[int, JsonObject]:
    rows = _array(bundle.get("implementation_epochs"), "bundle implementation epochs")
    result: dict[int, JsonObject] = {}
    prior_last = 0
    for row_index, raw in enumerate(rows):
        epoch = _validate_epoch_shape(raw, f"implementation epoch row {row_index}")
        number = cast(int, epoch["epoch"])
        expected_number = epoch_origin + row_index
        if number != expected_number or number in result:
            raise RehearsalV22ValidationError(
                "implementation epochs are not contiguous explicit keys"
            )
        if epoch_origin == 1 and number == 1 and _is_void_epoch_one(epoch):
            result[number] = epoch
            continue
        if cast(int, epoch["first_attempt_ordinal"]) != prior_last + 1:
            raise RehearsalV22ValidationError("implementation epoch attempt intervals have a gap")
        prior_last = cast(int, epoch["last_attempt_ordinal"])
        result[number] = epoch
    if not result:
        raise RehearsalV22ValidationError("bundle has no implementation epoch")
    if epoch_origin == 1 and _is_void_epoch_one(result[1]) and len(result) < 2:
        raise RehearsalV22ValidationError("void epoch 1 lacks an executed epoch 2")
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
        "interpreter_sha256": ("f4cd716d4b54f205398bec6932cc59361b087494ca2ddb157a5e8631d4d6f863"),
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
                raise RehearsalV22ValidationError(f"{label} contains a special or hardlinked entry")
            if (
                required_file_mode is not None
                and stat.S_IMODE(metadata.st_mode) != required_file_mode
            ):
                raise RehearsalV22ValidationError(f"{label} contains a file with the wrong mode")
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
        "verdict": ("APPROVE_EXACTLY_ONE_V2_2_REHEARSAL_ATTEMPT_ZERO_AUTOMATIC_RETRY"),
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
        (binding.project_root / "scripts/rehearse_p4_2a_v2_2_heldout_full_path.py").as_posix(),
        "--execute",
        "--attempt-authorization",
        (binding.project_root / source_relative).as_posix(),
        "--expected-ordinal",
        str(ordinal),
    ]
    _require_equal(command, expected_command, f"{label}.exact_argv")
    _require_equal(receipt["command_sha256"], _command_sha256(command), f"{label}.command_sha256")
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
    bundle: Mapping[str, Any],
    ordinal: int,
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

    live_inventory = _strict_private_tree_inventory(
        ledger,
        label="series ledger",
    )
    live_payloads = dict(live_inventory.payloads)
    archive_tree = _walk_regular_tree(archive, label="attempt-history archive")
    archive_payloads = {
        f"archive/attempt-history/{relative}": payload for relative, payload in archive_tree.items()
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
    observed_live_directories = {
        cast(str, row["relative_path"])
        for row in live_inventory.rows
        if row["kind"] == "directory" and row["relative_path"] != "."
    }
    if observed_live_directories != expected_live_directories:
        raise RehearsalV22ValidationError("series ledger contains a missing or extra directory")

    epochs = _epoch_map(bundle, epoch_origin=SERIES_2_EPOCH_ORIGIN)
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
        if terminal is None:
            outcome = "INCOMPLETE_UNTERMINALIZED"
            reached_stage = (
                "candidate_without_terminal"
                if candidate is not None
                else "started_without_terminal"
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
            require_worktree=True,
        )
        action_payload = _regular_bytes(
            _safe_path(root, action_relative, f"attempt {ordinal} action authorization"),
            f"attempt {ordinal} action authorization",
        )
        if action_payload != creation_payload:
            raise RehearsalV22ValidationError(
                "action authorization differs from its unique creation blob"
            )
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
                if candidate_payload is not None and outcome == "CANDIDATE_VALIDATED_AND_SELECTED"
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
    if (
        attempt_archive.get(
            "every_live_started_candidate_terminal_and_action_authorization_byte_archived"
        )
        is not True
        or attempt_archive.get("every_attempt_evidence_byte_archived") is not True
    ):
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
        live_identities=live_inventory.identities,
        archive_payloads=archive_payloads,
    )


@dataclass(frozen=True)
class _MirrorTreeInventory:
    rows: tuple[JsonObject, ...]
    payloads: Mapping[str, bytes]
    identities: Mapping[str, tuple[int, ...]]
    sha256: str
    file_count: int
    total_bytes: int


def _metadata_identity(metadata: _validator_os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _descriptor_path(descriptor: int, *, label: str) -> Path:
    try:
        raw = fcntl.fcntl(descriptor, fcntl.F_GETPATH, b"\0" * 1024)
    except (OSError, TypeError, ValueError) as exc:
        raise RehearsalV22ValidationError(f"{label} descriptor path is unavailable") from exc
    terminator = raw.find(b"\0")
    if terminator <= 0:
        raise RehearsalV22ValidationError(f"{label} descriptor path is unavailable")
    return Path(_validator_os.fsdecode(raw[:terminator])).absolute()


def _open_stable_read_descriptor(
    path: Path,
    *,
    label: str,
    directory: bool,
    parent_descriptor: int | None = None,
    entry_name: str | None = None,
) -> tuple[int, _validator_os.stat_result]:
    if (parent_descriptor is None) != (entry_name is None):
        raise RehearsalV22ValidationError(f"{label} parent descriptor binding is incomplete")
    try:
        before = path.lstat()
        flags = _validator_os.O_RDONLY | _validator_os.O_NOFOLLOW | _validator_os.O_CLOEXEC
        if directory:
            flags |= _validator_os.O_DIRECTORY
        target: str | Path = cast(str, entry_name) if parent_descriptor is not None else path
        descriptor = _validator_os.open(
            target,
            flags,
            dir_fd=parent_descriptor,
        )
    except OSError as exc:
        raise RehearsalV22ValidationError(f"{label} is unavailable or aliased") from exc
    try:
        descriptor_metadata = _validator_os.fstat(descriptor)
        after_open = path.lstat()
        descriptor_path = _descriptor_path(descriptor, label=label)
        descriptor_flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        descriptor_fd_flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
    except BaseException:
        _validator_os.close(descriptor)
        raise
    identities = {
        _metadata_identity(before),
        _metadata_identity(descriptor_metadata),
        _metadata_identity(after_open),
    }
    expected_kind = (
        stat.S_ISDIR(descriptor_metadata.st_mode)
        if directory
        else stat.S_ISREG(descriptor_metadata.st_mode)
    )
    if (
        len(identities) != 1
        or not expected_kind
        or descriptor_path != path.absolute()
        or descriptor_flags & _validator_os.O_ACCMODE != _validator_os.O_RDONLY
        or descriptor_fd_flags & fcntl.FD_CLOEXEC == 0
    ):
        _validator_os.close(descriptor)
        raise RehearsalV22ValidationError(f"{label} descriptor identity changed or escaped")
    return descriptor, before


def _verify_stable_directory_descriptor(
    descriptor: int,
    path: Path,
    before: _validator_os.stat_result,
    *,
    label: str,
) -> None:
    try:
        descriptor_after = _validator_os.fstat(descriptor)
        path_after = path.lstat()
        descriptor_path = _descriptor_path(descriptor, label=label)
    except BaseException as exc:
        raise RehearsalV22ValidationError(f"{label} directory identity recheck failed") from exc
    if (
        len(
            {
                _metadata_identity(before),
                _metadata_identity(descriptor_after),
                _metadata_identity(path_after),
            }
        )
        != 1
        or descriptor_path != path.absolute()
    ):
        raise RehearsalV22ValidationError(f"{label} directory changed during descriptor read")


def _close_stable_directory_descriptor(
    descriptor: int,
    path: Path,
    before: _validator_os.stat_result,
    *,
    label: str,
) -> None:
    try:
        _verify_stable_directory_descriptor(
            descriptor,
            path,
            before,
            label=label,
        )
    finally:
        _validator_os.close(descriptor)


def _read_stable_regular_descriptor(
    path: Path,
    *,
    label: str,
    parent_descriptor: int,
    entry_name: str,
) -> tuple[bytes, _validator_os.stat_result]:
    descriptor, before = _open_stable_read_descriptor(
        path,
        label=label,
        directory=False,
        parent_descriptor=parent_descriptor,
        entry_name=entry_name,
    )
    if (
        stat.S_IMODE(before.st_mode) != 0o600
        or before.st_uid != _validator_os.getuid()
        or before.st_nlink != 1
    ):
        _validator_os.close(descriptor)
        raise RehearsalV22ValidationError(f"{label} is hardlinked, wrong-mode, or wrong-owner")
    chunks: list[bytes] = []
    try:
        while True:
            chunk = _validator_os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        descriptor_after = _validator_os.fstat(descriptor)
        path_after = path.lstat()
        descriptor_path = _descriptor_path(descriptor, label=label)
    except BaseException:
        _validator_os.close(descriptor)
        raise
    _validator_os.close(descriptor)
    payload = b"".join(chunks)
    if (
        len(
            {
                _metadata_identity(before),
                _metadata_identity(descriptor_after),
                _metadata_identity(path_after),
            }
        )
        != 1
        or descriptor_path != path.absolute()
        or len(payload) != before.st_size
    ):
        raise RehearsalV22ValidationError(f"{label} changed during descriptor read")
    return payload, before


def _strict_owner_directory_identity(path: Path, *, label: str) -> Path:
    descriptor, before = _open_stable_read_descriptor(
        path,
        label=label,
        directory=True,
    )
    if stat.S_IMODE(before.st_mode) != 0o700 or before.st_uid != _validator_os.getuid():
        _validator_os.close(descriptor)
        raise RehearsalV22ValidationError(f"{label} identity, owner, or mode drifted")
    _close_stable_directory_descriptor(descriptor, path, before, label=label)
    return path.absolute()


def _strict_private_tree_inventory(
    root: Path,
    *,
    label: str,
    root_descriptor: int | None = None,
    root_before: _validator_os.stat_result | None = None,
    root_parent_descriptor: int | None = None,
    root_entry_name: str | None = None,
) -> _MirrorTreeInventory:
    """Descriptor-bind one private tree against aliases, links, and read races."""

    rows: list[JsonObject] = []
    payloads: dict[str, bytes] = {}
    identities: dict[str, tuple[int, ...]] = {}

    def walk_directory(
        path: Path,
        *,
        relative: str,
        parent_descriptor: int | None,
        entry_name: str | None,
        supplied_descriptor: int | None = None,
        supplied_before: _validator_os.stat_result | None = None,
    ) -> None:
        owns_descriptor = supplied_descriptor is None
        if supplied_descriptor is None:
            descriptor, before = _open_stable_read_descriptor(
                path,
                label=f"{label} directory {relative}",
                directory=True,
                parent_descriptor=parent_descriptor,
                entry_name=entry_name,
            )
        else:
            if supplied_before is None:
                raise RehearsalV22ValidationError(
                    f"{label} supplied root descriptor lacks identity"
                )
            descriptor, before = supplied_descriptor, supplied_before
            _verify_stable_directory_descriptor(
                descriptor,
                path,
                before,
                label=f"{label} directory {relative}",
            )
        if stat.S_IMODE(before.st_mode) != 0o700 or before.st_uid != _validator_os.getuid():
            if owns_descriptor:
                _validator_os.close(descriptor)
            raise RehearsalV22ValidationError(f"{label} directory mode or owner drifted")
        rows.append(
            {
                "relative_path": relative,
                "kind": "directory",
                "mode": 0o700,
                "bytes": 0,
                "sha256": None,
            }
        )
        identities[relative] = _metadata_identity(before)
        try:
            names = sorted(
                _validator_os.listdir(descriptor),
                key=lambda value: value.encode("utf-8"),
            )
            for name in names:
                if not isinstance(name, str) or name in {"", ".", ".."} or "/" in name:
                    raise RehearsalV22ValidationError(
                        f"{label} contains an invalid directory member"
                    )
                child = path / name
                child_relative = name if relative == "." else f"{relative}/{name}"
                _relative(child_relative, f"{label} member")
                try:
                    child_metadata = _validator_os.stat(
                        name,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise RehearsalV22ValidationError(f"{label} member is unavailable") from exc
                if stat.S_ISDIR(child_metadata.st_mode):
                    walk_directory(
                        child,
                        relative=child_relative,
                        parent_descriptor=descriptor,
                        entry_name=name,
                    )
                    continue
                if not stat.S_ISREG(child_metadata.st_mode):
                    raise RehearsalV22ValidationError(
                        f"{label} contains an alias or special member"
                    )
                payload, stable_metadata = _read_stable_regular_descriptor(
                    child,
                    label=f"{label} file {child_relative}",
                    parent_descriptor=descriptor,
                    entry_name=name,
                )
                payloads[child_relative] = payload
                identities[child_relative] = _metadata_identity(stable_metadata)
                rows.append(
                    {
                        "relative_path": child_relative,
                        "kind": "file",
                        "mode": stat.S_IMODE(stable_metadata.st_mode),
                        "bytes": len(payload),
                        "sha256": _sha256(payload),
                    }
                )
        except BaseException:
            if owns_descriptor:
                _validator_os.close(descriptor)
            raise
        if owns_descriptor:
            _close_stable_directory_descriptor(
                descriptor,
                path,
                before,
                label=f"{label} directory {relative}",
            )
        else:
            _verify_stable_directory_descriptor(
                descriptor,
                path,
                before,
                label=f"{label} directory {relative}",
            )

    walk_directory(
        root,
        relative=".",
        parent_descriptor=root_parent_descriptor,
        entry_name=root_entry_name,
        supplied_descriptor=root_descriptor,
        supplied_before=root_before,
    )
    root_row = [row for row in rows if row["relative_path"] == "."]
    if len(root_row) != 1:
        raise RehearsalV22ValidationError(f"{label} root inventory drifted")
    ordered_rows = tuple(
        root_row
        + sorted(
            (row for row in rows if row["relative_path"] != "."),
            key=lambda row: cast(str, row["relative_path"]).encode("utf-8"),
        )
    )
    ordered_payloads = dict(sorted(payloads.items(), key=lambda item: item[0].encode("utf-8")))
    ordered_identities = dict(sorted(identities.items(), key=lambda item: item[0].encode("utf-8")))
    return _MirrorTreeInventory(
        rows=ordered_rows,
        payloads=ordered_payloads,
        identities=ordered_identities,
        sha256=_sha256(MIRROR_INVENTORY_PREFIX + _canonical_json_bytes(list(ordered_rows))),
        file_count=len(ordered_payloads),
        total_bytes=sum(len(payload) for payload in ordered_payloads.values()),
    )


def _mirror_inventory_through_ordinal(
    inventory: _MirrorTreeInventory,
    *,
    ordinal: int,
) -> _MirrorTreeInventory:
    def included(relative: str) -> bool:
        if relative in {".", "series.json", ".series.lock", "attempts"}:
            return True
        parts = PurePosixPath(relative).parts
        return (
            len(parts) >= 2
            and parts[0] == "attempts"
            and re.fullmatch(r"[0-9]{6}", parts[1]) is not None
            and int(parts[1]) <= ordinal
        )

    rows = tuple(
        copy.deepcopy(row) for row in inventory.rows if included(cast(str, row["relative_path"]))
    )
    payloads = {
        relative: payload for relative, payload in inventory.payloads.items() if included(relative)
    }
    identities = {
        relative: identity
        for relative, identity in inventory.identities.items()
        if included(relative)
    }
    return _MirrorTreeInventory(
        rows=rows,
        payloads=payloads,
        identities=identities,
        sha256=_sha256(MIRROR_INVENTORY_PREFIX + _canonical_json_bytes(list(rows))),
        file_count=len(payloads),
        total_bytes=sum(len(payload) for payload in payloads.values()),
    )


def _strict_receipt_inventory(
    path: Path,
    *,
    label: str,
    root_descriptor: int | None = None,
    root_before: _validator_os.stat_result | None = None,
) -> _MirrorTreeInventory:
    inventory = _strict_private_tree_inventory(
        path,
        label=label,
        root_descriptor=root_descriptor,
        root_before=root_before,
    )
    if any(
        row["kind"] != "file" or len(PurePosixPath(cast(str, row["relative_path"])).parts) != 1
        for row in inventory.rows
        if row["relative_path"] != "."
    ):
        raise RehearsalV22ValidationError(f"{label} contains a noncanonical receipt member")
    return inventory


def _strict_receipt_root(
    path: Path,
    *,
    label: str,
    root_descriptor: int | None = None,
    root_before: _validator_os.stat_result | None = None,
) -> tuple[tuple[str, bytes], ...]:
    inventory = _strict_receipt_inventory(
        path,
        label=label,
        root_descriptor=root_descriptor,
        root_before=root_before,
    )
    return tuple(inventory.payloads.items())


def _mirror_receipt_filename(ordinal: int, live_root: str) -> str:
    _sha(live_root, "mirror live-ledger root")
    if ordinal < 1:
        raise RehearsalV22ValidationError("mirror receipt ordinal is invalid")
    return f"through-ordinal-{ordinal:06d}-{live_root}.mirror-verification.json"


def _mirror_snapshot_name(ordinal: int, live_root: str) -> str:
    _sha(live_root, "mirror snapshot live-ledger root")
    if ordinal < 1:
        raise RehearsalV22ValidationError("mirror snapshot ordinal is invalid")
    return f"through-ordinal-{ordinal:06d}-{live_root}"


def _validate_second_copy_history(
    *,
    binding: BindingView,
    replay: HistoryReplay,
) -> tuple[JsonObject, ...]:
    """Passively rebind every cumulative snapshot and paired receipt to primary."""

    if not replay.records:
        raise RehearsalV22ValidationError(
            "series-2 second-copy validation requires persisted history"
        )
    primary_container = _strict_owner_directory_identity(
        binding.primary_series_container,
        label="series-2 primary container",
    )
    secondary_container = _strict_owner_directory_identity(
        binding.secondary_series_container,
        label="series-2 secondary container",
    )
    if (
        binding.ledger_root != binding.primary_series_container / "PRIMARY-LEDGER-DO-NOT-DELETE"
        or binding.primary_receipt_root
        != binding.primary_series_container / "MIRROR-RECEIPTS-DO-NOT-DELETE"
        or binding.secondary_snapshot_root
        != binding.secondary_series_container / "SEALED-LEDGER-SNAPSHOTS-DO-NOT-DELETE"
        or binding.secondary_receipt_root
        != binding.secondary_series_container / "MIRROR-RECEIPTS-DO-NOT-DELETE"
    ):
        raise RehearsalV22ValidationError("series-2 mirror leaf binding drifted")
    protected = (binding.project_root, binding.absolute_destination)
    if (
        primary_container == secondary_container
        or primary_container.is_relative_to(secondary_container)
        or secondary_container.is_relative_to(primary_container)
        or any(
            primary_container == path
            or secondary_container == path
            or primary_container.is_relative_to(path)
            or secondary_container.is_relative_to(path)
            or path.is_relative_to(primary_container)
            or path.is_relative_to(secondary_container)
            for path in protected
        )
    ):
        raise RehearsalV22ValidationError(
            "series-2 evidence containers overlap each other or protected state"
        )
    leaves = (
        binding.secondary_snapshot_root,
        binding.primary_receipt_root,
        binding.secondary_receipt_root,
    )
    if tuple(_validator_os.path.lexists(path) for path in leaves) != (
        True,
        True,
        True,
    ):
        raise RehearsalV22ValidationError("series-2 mirror leaves are partial or absent")
    held_roots: list[tuple[int, Path, _validator_os.stat_result, str]] = []
    try:
        for path, label in (
            (binding.ledger_root, "series-2 primary live-ledger root"),
            (binding.secondary_snapshot_root, "series-2 snapshot root"),
            (binding.primary_receipt_root, "series-2 primary receipt root"),
            (binding.secondary_receipt_root, "series-2 secondary receipt root"),
        ):
            descriptor, before = _open_stable_read_descriptor(
                path,
                label=label,
                directory=True,
            )
            if stat.S_IMODE(before.st_mode) != 0o700 or before.st_uid != _validator_os.getuid():
                _validator_os.close(descriptor)
                raise RehearsalV22ValidationError(f"{label} owner or mode drifted")
            held_roots.append((descriptor, path, before, label))
    except BaseException:
        for descriptor, _path, _before, _label in held_roots:
            _validator_os.close(descriptor)
        raise
    (
        (primary_descriptor, _primary_path, primary_before, _primary_label),
        (snapshot_descriptor, snapshot_root, _snapshot_before, _snapshot_label),
        (
            primary_receipt_descriptor,
            _primary_receipt_path,
            primary_receipt_before,
            _primary_receipt_label,
        ),
        (
            secondary_receipt_descriptor,
            _secondary_receipt_path,
            secondary_receipt_before,
            _secondary_receipt_label,
        ),
    ) = held_roots
    result: tuple[JsonObject, ...] | None = None
    try:
        primary_receipt_inventory = _strict_receipt_inventory(
            binding.primary_receipt_root,
            label="series-2 primary receipt root",
            root_descriptor=primary_receipt_descriptor,
            root_before=primary_receipt_before,
        )
        secondary_receipt_inventory = _strict_receipt_inventory(
            binding.secondary_receipt_root,
            label="series-2 secondary receipt root",
            root_descriptor=secondary_receipt_descriptor,
            root_before=secondary_receipt_before,
        )
        primary_receipts = tuple(primary_receipt_inventory.payloads.items())
        secondary_receipts = tuple(secondary_receipt_inventory.payloads.items())
        if primary_receipts != secondary_receipts:
            raise RehearsalV22ValidationError("series-2 paired mirror receipt bytes differ")
        if len(primary_receipts) != len(replay.records):
            raise RehearsalV22ValidationError(
                "series-2 mirror receipt count blocks bundle or release"
            )
        snapshot_names = sorted(
            _validator_os.listdir(snapshot_descriptor),
            key=lambda value: value.encode("utf-8"),
        )
        if any(
            not isinstance(name, str) or name in {"", ".", ".."} or "/" in name
            for name in snapshot_names
        ) or len(snapshot_names) != len(replay.records):
            raise RehearsalV22ValidationError(
                "series-2 mirror snapshot count or name blocks bundle or release"
            )
        current_primary = _strict_private_tree_inventory(
            binding.ledger_root,
            label="series-2 current primary live ledger",
            root_descriptor=primary_descriptor,
            root_before=primary_before,
        )
        if current_primary.payloads != replay.live_payloads:
            raise RehearsalV22ValidationError(
                "primary ledger bytes changed after independent history replay"
            )
        if current_primary.identities != replay.live_identities:
            raise RehearsalV22ValidationError(
                "primary ledger identity changed after independent history replay"
            )
        current_live_root = _path_merkle(
            current_primary.payloads,
            leaf_domain=b"p4.2a-rehearsal-v2.2-ledger-leaf-v1\0",
        )
        if current_live_root != replay.live_ledger_root_sha256:
            raise RehearsalV22ValidationError(
                "primary live-ledger root changed after independent history replay"
            )
        history_roots: list[str] = []
        history_root = _history_empty_root()
        for ordinal, record in enumerate(replay.records, 1):
            _require_equal(
                record.get("ordinal"),
                ordinal,
                "mirror primary record ordinal",
            )
            history_root = _history_step(
                history_root,
                _sha(record.get("record_root_sha256"), "mirror primary record root"),
            )
            history_roots.append(history_root)
        _require_equal(
            history_root,
            replay.history_root_sha256,
            "mirror primary final history root",
        )
        observed_snapshot_names: set[str] = set()
        initial_snapshot_inventories: dict[str, _MirrorTreeInventory] = {}
        receipts: list[JsonObject] = []
        for ordinal, (filename, payload) in enumerate(primary_receipts, 1):
            receipt = _strict_canonical_json_loads(
                payload,
                label=f"series-2 mirror receipt {ordinal}",
            )
            _require_exact_keys(
                receipt,
                _MIRROR_RECEIPT_FIELDS,
                f"series-2 mirror receipt {ordinal}",
            )
            live_root = _sha(
                receipt.get("live_ledger_root_sha256"),
                f"series-2 mirror receipt {ordinal} live root",
            )
            _require_equal(
                filename,
                _mirror_receipt_filename(ordinal, live_root),
                f"series-2 mirror receipt {ordinal} filename",
            )
            record = replay.records[ordinal - 1]
            expected_outcome = _string(
                record.get("outcome"),
                f"series-2 primary record {ordinal} outcome",
            )
            snapshot_name = _mirror_snapshot_name(ordinal, live_root)
            snapshot = snapshot_root / snapshot_name
            expected_scalars: Mapping[str, object] = {
                "schema_version": MIRROR_RECEIPT_SCHEMA,
                "series_token_sha256": binding.series_token_sha256,
                "ordinal": ordinal,
                "attempt_outcome": expected_outcome,
                "attempt_sealed": expected_outcome != "INCOMPLETE_UNTERMINALIZED",
                "primary_ledger_root": binding.ledger_root.as_posix(),
                "secondary_snapshot_root": snapshot.as_posix(),
                "history_root_sha256": history_roots[ordinal - 1],
                "second_copy_verified": True,
            }
            for key, expected in expected_scalars.items():
                _require_equal(
                    receipt.get(key),
                    expected,
                    f"series-2 mirror receipt {ordinal} {key}",
                )
            _require_equal(
                receipt.get("verified_at_utc"),
                FIXED_WALL_CLOCK_TEXT,
                f"series-2 mirror receipt {ordinal} verified_at_utc",
            )
            snapshot_inventory = _strict_private_tree_inventory(
                snapshot,
                label=f"series-2 snapshot {ordinal}",
                root_parent_descriptor=snapshot_descriptor,
                root_entry_name=snapshot_name,
            )
            initial_snapshot_inventories[snapshot_name] = snapshot_inventory
            primary_prefix = _mirror_inventory_through_ordinal(
                current_primary,
                ordinal=ordinal,
            )
            if (
                snapshot_inventory.rows != primary_prefix.rows
                or snapshot_inventory.payloads != primary_prefix.payloads
            ):
                raise RehearsalV22ValidationError(
                    "mirror snapshot inventory differs from current primary prefix"
                )
            snapshot_live_root = _path_merkle(
                snapshot_inventory.payloads,
                leaf_domain=b"p4.2a-rehearsal-v2.2-ledger-leaf-v1\0",
            )
            inventory_expected: Mapping[str, object] = {
                "live_ledger_root_sha256": snapshot_live_root,
                "file_count": snapshot_inventory.file_count,
                "total_bytes": snapshot_inventory.total_bytes,
                "primary_inventory_sha256": primary_prefix.sha256,
                "secondary_inventory_sha256": snapshot_inventory.sha256,
            }
            for key, expected in inventory_expected.items():
                _require_equal(
                    receipt.get(key),
                    expected,
                    f"series-2 mirror receipt {ordinal} {key}",
                )
            observed_snapshot_names.add(snapshot_name)
            receipts.append(receipt)
        if set(snapshot_names) != observed_snapshot_names:
            raise RehearsalV22ValidationError(
                "series-2 snapshot root contains a staging or extra artifact"
            )
        final_primary_receipt_inventory = _strict_receipt_inventory(
            binding.primary_receipt_root,
            label="series-2 final primary receipt root recheck",
            root_descriptor=primary_receipt_descriptor,
            root_before=primary_receipt_before,
        )
        if (
            final_primary_receipt_inventory.rows != primary_receipt_inventory.rows
            or final_primary_receipt_inventory.payloads != primary_receipt_inventory.payloads
            or final_primary_receipt_inventory.identities != primary_receipt_inventory.identities
        ):
            raise RehearsalV22ValidationError(
                "primary receipt root changed during mirror validation"
            )
        final_secondary_receipt_inventory = _strict_receipt_inventory(
            binding.secondary_receipt_root,
            label="series-2 final secondary receipt root recheck",
            root_descriptor=secondary_receipt_descriptor,
            root_before=secondary_receipt_before,
        )
        if (
            final_secondary_receipt_inventory.rows != secondary_receipt_inventory.rows
            or final_secondary_receipt_inventory.payloads != secondary_receipt_inventory.payloads
            or final_secondary_receipt_inventory.identities
            != secondary_receipt_inventory.identities
        ):
            raise RehearsalV22ValidationError(
                "secondary receipt root changed during mirror validation"
            )
        final_snapshot_names = sorted(
            _validator_os.listdir(snapshot_descriptor),
            key=lambda value: value.encode("utf-8"),
        )
        if final_snapshot_names != snapshot_names:
            raise RehearsalV22ValidationError("snapshot root changed during mirror validation")
        for snapshot_name in snapshot_names:
            initial_snapshot_inventory = initial_snapshot_inventories[snapshot_name]
            final_snapshot_inventory = _strict_private_tree_inventory(
                snapshot_root / snapshot_name,
                label=f"series-2 final snapshot {snapshot_name} recheck",
                root_parent_descriptor=snapshot_descriptor,
                root_entry_name=snapshot_name,
            )
            if (
                final_snapshot_inventory.rows != initial_snapshot_inventory.rows
                or final_snapshot_inventory.payloads != initial_snapshot_inventory.payloads
                or final_snapshot_inventory.identities != initial_snapshot_inventory.identities
            ):
                raise RehearsalV22ValidationError(
                    "snapshot bytes or identity changed during mirror validation"
                )
        final_primary = _strict_private_tree_inventory(
            binding.ledger_root,
            label="series-2 final primary live-ledger recheck",
            root_descriptor=primary_descriptor,
            root_before=primary_before,
        )
        if (
            final_primary.rows != current_primary.rows
            or final_primary.payloads != current_primary.payloads
            or final_primary.identities != current_primary.identities
            or final_primary.sha256 != current_primary.sha256
        ):
            raise RehearsalV22ValidationError(
                "primary live ledger changed during mirror validation"
            )
        latest = receipts[-1]
        latest_expected: Mapping[str, object] = {
            "history_root_sha256": replay.history_root_sha256,
            "live_ledger_root_sha256": replay.live_ledger_root_sha256,
            "primary_inventory_sha256": final_primary.sha256,
            "file_count": final_primary.file_count,
            "total_bytes": final_primary.total_bytes,
        }
        for key, expected in latest_expected.items():
            _require_equal(
                latest.get(key),
                expected,
                f"series-2 latest mirror {key}",
            )
        result = tuple(receipts)
    except BaseException:
        for descriptor, _path, _before, _label in held_roots:
            with contextlib.suppress(OSError):
                _validator_os.close(descriptor)
        raise
    close_error: BaseException | None = None
    for descriptor, path, before, label in held_roots:
        try:
            _close_stable_directory_descriptor(
                descriptor,
                path,
                before,
                label=label,
            )
        except BaseException as exc:
            if close_error is None:
                close_error = exc
    if close_error is not None:
        raise close_error
    if result is None:
        raise RehearsalV22ValidationError("series-2 mirror validation produced no result")
    return result


@dataclass(frozen=True)
class ArchiveReplay:
    run_a: Mapping[str, bytes]
    run_b: Mapping[str, bytes]
    run_a_root_sha256: str
    run_b_root_sha256: str
    control_root_sha256: str
    control_repository_payloads: Mapping[str, bytes]
    selected_control_cache: _ControlSurfaceCacheEnvelope | None
    all_payloads: Mapping[str, bytes]


@dataclass(frozen=True)
class ValidatedBundle:
    document: JsonObject
    payload: bytes
    path: Path
    implementation_commit: str
    archives: ArchiveReplay
    history: HistoryReplay
    mirror_receipts: tuple[JsonObject, ...]


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
    if (
        authority.get("rehearsal_bundle") is not None
        or authority.get("release_authorization") is not None
    ):
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
        if recorded != fixed or recorded < prior or isinstance(latency, bool) or latency != 0:
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
) -> tuple[
    dict[str, bytes],
    dict[str, bytes],
    str,
]:
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
        raise RehearsalV22ValidationError("package inventory contains duplicate normalized names")
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
            raise RehearsalV22ValidationError("fixed registered package metadata root is aliased")
        if resolved not in selected:
            selected.append(resolved)
    projected: list[str] = []
    for package_root in selected:
        projected.append(package_root.relative_to(registered).as_posix())
    if (
        projected != [".venv/lib/python3.12/site-packages"]
        or _sha256(_canonical_json_bytes(projected))
        != "fae235892c0988d4093d1ad12b034a6126d116e436393e837a8b2f71601fbd12"
    ):
        raise RehearsalV22ValidationError("fixed package metadata root binding drifted")

    try:
        _normalize_distribution_rows([("validator_probe.pkg", "1"), ("validator-probe-pkg", "2")])
    except RehearsalV22ValidationError:
        pass
    else:
        raise RehearsalV22ValidationError("duplicate package-name negative probe did not reject")
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
    implementation_commit: str,
    validation_context: BundleValidationContext,
) -> tuple[
    dict[str, bytes],
    dict[str, bytes],
    str,
    implementation.ControlSurface,
]:
    require_current = _validation_context_requires_current(
        validation_context,
        implementation_commit=implementation_commit,
    )
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
                if require_current:
                    current = _regular_bytes(
                        _safe_path(project_root, relative, f"current control {relative}"),
                        f"current control {relative}",
                    )
                    if current != payload:
                        raise RehearsalV22ValidationError(
                            f"current control bytes drifted: {relative}"
                        )
                _validated_implementation_blob(
                    project_root=project_root,
                    implementation_commit=implementation_commit,
                    relative_path=relative,
                    expected_sha256=_sha256(payload),
                    require_current=require_current,
                )
            else:
                digest, creating_commit, require_worktree = governance
                reference = {
                    "path": relative,
                    "sha256": digest,
                    "creating_commit": creating_commit,
                    "unique_a_history_verified": True,
                }
                if relative == INDEPENDENT_REVIEW_RELATIVE.as_posix():
                    execution_head = (
                        _git_bytes(
                            project_root,
                            "rev-parse",
                            "HEAD",
                        )
                        .decode("ascii", errors="strict")
                        .strip()
                    )
                    creating_payload = _validate_initial_sibling_authority(
                        project_root,
                        reference,
                        execution_head=execution_head,
                    )
                else:
                    creating_payload = _unique_a_authority(
                        project_root,
                        reference,
                        require_worktree=require_worktree,
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
    referenced = _array(control.get("referenced_pass_test_paths"), "referenced PASS test paths")
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
    for relative, (
        digest,
        _creating_commit,
        _require_worktree,
    ) in _CONTROL_GOVERNANCE_AUTHORITIES.items():
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
        implementation_commit=implementation_commit,
    )
    for relative, payload in independent_closure.items():
        if repository_payloads.get(relative) != payload:
            raise RehearsalV22ValidationError(
                f"control archive omitted independently derived AST closure: {relative}"
            )
    independent_python, independent_packages = _independent_runtime_inventory()
    if (
        tree_payloads.get("archive/control-surface/root/runtime/python.json") != independent_python
        or tree_payloads.get("archive/control-surface/root/runtime/packages.json")
        != independent_packages
    ):
        raise RehearsalV22ValidationError(
            "control archive differs from the independently derived runtime inventory"
        )
    implementation_surface = implementation.build_control_surface(
        project_root,
        implementation_commit,
        require_current=require_current,
    )
    if (
        implementation_surface.implementation_commit != implementation_commit
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
        or tuple(implementation_surface.ast_closure_paths) != tuple(independent_closure)
        or not set(implementation_surface.loaded_repository_sources).issubset(
            implementation_surface.ast_closure_paths
        )
        or not set(implementation_surface.ast_closure_paths).issubset(repository_payloads)
    ):
        raise RehearsalV22ValidationError(
            "independent control replay differs from the implementation control surface"
        )
    return repository_payloads, bundle_payloads, control_root, implementation_surface


def _validate_archives(
    *,
    project_root: Path,
    bundle_directory: Path,
    bundle: Mapping[str, Any],
    implementation_commit: str,
    validation_context: BundleValidationContext,
    control_pass_nonce: object,
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
    controls, control_files, control_root, selected_control_surface = _validate_control_archive(
        project_root=project_root,
        bundle_directory=bundle_directory,
        value=archive.get("control_surface"),
        implementation_commit=implementation_commit,
        validation_context=validation_context,
    )
    selected_control_cache: _ControlSurfaceCacheEnvelope | None = None
    if isinstance(validation_context, RecoveredBundleValidationContext):
        if validation_context.historical_anchor.implementation_epoch != 6:
            raise RehearsalV22ValidationError(
                "passive recovery historical control cache is not selected epoch 6"
            )
        execution_head = _git_commit(
            project_root,
            _git_bytes(project_root, "rev-parse", "HEAD").decode("ascii", errors="strict").strip(),
            "historical control cache execution HEAD",
        )
        selected_control_cache = _freeze_control_surface_cache(
            project_root,
            implementation_commit=implementation_commit,
            execution_head=execution_head,
            pass_kind="HISTORICAL_SELECTED_EPOCH_6",
            selected_epoch=6,
            ref_snapshot_sha256=None,
            lineage_census_sha256=None,
            pass_nonce=control_pass_nonce,
            control=selected_control_surface,
        )
    return ArchiveReplay(
        run_a=run_a,
        run_b=run_b,
        run_a_root_sha256=run_a_root,
        run_b_root_sha256=run_b_root,
        control_root_sha256=control_root,
        control_repository_payloads=controls,
        selected_control_cache=selected_control_cache,
        all_payloads={**run_a_files, **run_b_files, **control_files},
    )


def _validate_lineage(
    *,
    project_root: Path,
    bundle: Mapping[str, Any],
    implementation_commit: str,
) -> str:
    lineage = _object(bundle.get("lineage"), "bundle lineage")
    expected_refs = {
        "preregistration": (
            SERIES_2_PREREGISTRATION_RELATIVE.as_posix(),
            SERIES_2_PREREGISTRATION_SHA256,
        ),
        "bundle_schema": (
            SERIES_2_BUNDLE_SCHEMA_RELATIVE.as_posix(),
            SERIES_2_BUNDLE_SCHEMA_SHA256,
        ),
        "release_authorization_schema": (
            SERIES_2_RELEASE_SCHEMA_RELATIVE.as_posix(),
            SERIES_2_RELEASE_SCHEMA_SHA256,
        ),
    }
    for key, (path, digest) in expected_refs.items():
        reference = _validate_file_ref(lineage.get(key), f"bundle lineage {key}")
        _require_equal(reference, {"path": path, "sha256": digest}, f"bundle lineage {key}")
    preregistration_commit = _commit(
        lineage.get("preregistration_commit"), "bundle preregistration commit"
    )
    _require_equal(
        preregistration_commit,
        SERIES_2_PREREGISTRATION_COMMIT,
        "bundle preregistration commit",
    )
    execution_head = (
        _git_bytes(project_root, "rev-parse", "HEAD").decode("ascii", errors="strict").strip()
    )
    _require_equal(
        _validate_series_2_preregistration(
            project_root=project_root,
            execution_head=execution_head,
        ),
        preregistration_commit,
        "series-2 preregistration lineage",
    )
    _require_equal(
        lineage.get("implementation_commit"),
        implementation_commit,
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
        payload = _regular_bytes(
            _safe_path(project_root, reference["path"], f"bundle lineage {key}"),
            f"bundle lineage {key}",
        )
        if _sha256(payload) != reference["sha256"]:
            raise RehearsalV22ValidationError(f"bundle lineage {key} bytes drifted")
    retired = _array(lineage.get("retired_v1_artifacts"), "retired v1 artifacts")
    for index, raw in enumerate(retired):
        reference = _validate_file_ref(raw, f"retired v1 artifact {index}")
        payload = _regular_bytes(
            _safe_path(project_root, reference["path"], f"retired v1 artifact {index}"),
            f"retired v1 artifact {index}",
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
            require_worktree=True,
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
        _unique_a_authority(project_root, reference, require_worktree=True)
    incident_commit = _CARRY_FORWARD_AUTHORITIES["v2_1_consumed_attempt_incident"][2]
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
            raise RehearsalV22ValidationError("v2.2 remediation/scope authority topology drifted")
    _require_equal(
        lineage.get("v2_1_implementation_commit"),
        _V2_1_IMPLEMENTATION_COMMIT,
        "v2.1 implementation commit",
    )
    if (
        _git_parents(project_root, _V2_1_IMPLEMENTATION_COMMIT) != (_V2_1_IMPLEMENTATION_PARENT,)
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
    historical_prereg_reference = {
        "path": PREREGISTRATION_RELATIVE.as_posix(),
        "sha256": PREREGISTRATION_SHA256,
        "creating_commit": INITIAL_REVIEWED_COMMIT,
        "unique_a_history_verified": True,
    }
    _unique_a_authority(
        project_root,
        historical_prereg_reference,
        require_worktree=True,
    )
    historical_preregistration_parent = _V2_2_SCOPE_AUTHORITY[2]
    if _git_parents(project_root, INITIAL_REVIEWED_COMMIT) != (historical_preregistration_parent,):
        raise RehearsalV22ValidationError("v2.2 preregistration parent drifted")
    prereg_surface = {
        ("A", PREREGISTRATION_RELATIVE.as_posix()),
        ("A", BUNDLE_SCHEMA_RELATIVE.as_posix()),
        ("A", RELEASE_SCHEMA_RELATIVE.as_posix()),
    }
    if (
        set(
            _diff_name_status(
                project_root,
                historical_preregistration_parent,
                INITIAL_REVIEWED_COMMIT,
            )
        )
        != prereg_surface
    ):
        raise RehearsalV22ValidationError("v2.2 preregistration is not exact 3A")
    for relative, digest in (
        (BUNDLE_SCHEMA_RELATIVE.as_posix(), BUNDLE_SCHEMA_SHA256),
        (RELEASE_SCHEMA_RELATIVE.as_posix(), RELEASE_SCHEMA_SHA256),
    ):
        if _sha256(_git_blob(project_root, INITIAL_REVIEWED_COMMIT, relative)) != digest:
            raise RehearsalV22ValidationError("v2.2 schema creation blob drifted")
    return preregistration_commit


def _validate_harness_identity(
    *,
    project_root: Path,
    bundle: Mapping[str, Any],
    implementation_commit: str,
    validation_context: BundleValidationContext,
) -> None:
    require_current = _validation_context_requires_current(
        validation_context,
        implementation_commit=implementation_commit,
    )
    identity = _object(bundle.get("harness_identity"), "bundle harness identity")
    expected_paths = {
        "thin_main_shim": "scripts/rehearse_p4_2a_v2_2_heldout_full_path.py",
        "implementation_module": "scripts/p4_2a_v2_2_heldout_rehearsal.py",
        "validator_module": "scripts/validate_p4_2a_v2_2_heldout_rehearsal_bundle.py",
    }
    for key, expected_path in expected_paths.items():
        reference = _validate_file_ref(identity.get(key), f"harness {key}")
        _require_equal(reference["path"], expected_path, f"harness {key} path")
        payload = _git_blob(project_root, implementation_commit, expected_path)
        if (
            require_current
            and _regular_bytes(
                _safe_path(project_root, expected_path, f"harness {key}"),
                f"harness {key}",
            )
            != payload
        ):
            raise RehearsalV22ValidationError(f"current harness bytes drifted: {expected_path}")
        _require_equal(reference["sha256"], _sha256(payload), f"harness {key} SHA")
        _validated_implementation_blob(
            project_root=project_root,
            implementation_commit=implementation_commit,
            relative_path=expected_path,
            expected_sha256=_sha256(payload),
            require_current=require_current,
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
    if require_current:
        _validate_module_identity(project_root, implementation_commit)
    else:
        if not isinstance(validation_context, RecoveredBundleValidationContext):
            raise RehearsalV22ValidationError("historical harness lacks recovered context")
        _validate_historical_module_identity(
            project_root,
            validation_context.historical_anchor,
        )


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
        raise RehearsalV22ValidationError(f"{label} did not unambiguously approve implementation")


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
        _git_parents(project_root, implementation_commit) != (VOID_EPOCH_ONE_IMPLEMENTATION_PARENT,)
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
    )
    adjudication = _validate_authority_ref(
        epoch["independent_implementation_review"],
        "void epoch adjudication",
    )
    adjudication_payload = _unique_a_authority(
        project_root,
        adjudication,
        require_worktree=True,
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
        require_worktree=True,
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
    if _git_parents(project_root, landing) != (
        VOID_EPOCH_ONE_REVIEW_COMMIT,
        implementation_commit,
    ) or not _git_is_ancestor(project_root, landing, execution_head):
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


def _historical_control_cache_for_epoch(
    *,
    project_root: Path,
    validation_context: BundleValidationContext,
    replay: HistoryReplay,
    epoch_number: int,
    implementation_commit: str,
    expected_merkle_root_sha256: str,
    execution_head: str,
    selected_require_current: bool,
    control_pass_nonce: object,
    cache: _ControlSurfaceCacheEnvelope | None,
) -> _ControlSurfaceCacheEnvelope | None:
    if not isinstance(validation_context, RecoveredBundleValidationContext):
        return None
    if (
        replay.selected_implementation_epoch != 6
        or validation_context.historical_anchor.implementation_epoch != 6
    ):
        raise RehearsalV22ValidationError(
            "passive recovery selected implementation epoch is not exactly 6"
        )
    if epoch_number != 6:
        return None
    if (
        selected_require_current
        or implementation_commit != replay.selected_implementation_commit
        or cache is None
        or cache.merkle_root_sha256 != expected_merkle_root_sha256
    ):
        raise RehearsalV22ValidationError("passive selected epoch-6 control cache binding drifted")
    _validate_control_surface_cache_integrity(
        project_root,
        implementation_commit=implementation_commit,
        execution_head=execution_head,
        pass_kind="HISTORICAL_SELECTED_EPOCH_6",
        selected_epoch=6,
        ref_snapshot_sha256=None,
        lineage_census_sha256=None,
        pass_nonce=control_pass_nonce,
        cache=cache,
    )
    return cache


def _validate_implementation_epochs(
    *,
    project_root: Path,
    bundle: Mapping[str, Any],
    replay: HistoryReplay,
    archives: ArchiveReplay,
    validation_context: BundleValidationContext,
    control_pass_nonce: object,
) -> None:
    selected_require_current = _validation_context_requires_current(
        validation_context,
        implementation_commit=replay.selected_implementation_commit,
    )
    epochs = _epoch_map(bundle, epoch_origin=SERIES_2_EPOCH_ORIGIN)
    execution_head = (
        _git_bytes(project_root, "rev-parse", "HEAD").decode("ascii", errors="strict").strip()
    )
    _git_commit(project_root, execution_head, "execution HEAD")
    for epoch_number, epoch in epochs.items():
        label = f"implementation epoch {epoch_number}"
        implementation_commit = _git_commit(
            project_root,
            epoch["implementation_commit"],
            f"{label} commit",
        )
        if not _git_is_ancestor(project_root, implementation_commit, execution_head):
            raise RehearsalV22ValidationError(f"{label} is not an execution-head ancestor")
        owner = _validate_authority_ref(
            epoch["owner_exact_surface_authorization"],
            f"{label} owner authority",
        )
        review = _validate_authority_ref(
            epoch["independent_implementation_review"],
            f"{label} independent review",
        )
        owner_payload = _unique_a_authority(
            project_root,
            owner,
            require_worktree=True,
        )
        parents = _git_parents(project_root, implementation_commit)
        if len(parents) != 1:
            raise RehearsalV22ValidationError("implementation epoch commit is not single-parent")
        if parents != (cast(str, owner["creating_commit"]),):
            raise RehearsalV22ValidationError(f"{label} is not the direct child of its authority")
        owner_document = _object(
            strict_json_loads(owner_payload, label=f"{label} surface authorization"),
            f"{label} surface authorization",
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
            f"{label} surface authorization",
        )
        _require_equal(
            owner_document["schema_version"],
            "p4.2a-v2-2-implementation-epoch-surface-authorization-v1",
            f"{label} surface authorization schema",
        )
        _require_equal(
            owner_document["verdict"],
            "APPROVE_EXACT_V2_2_IMPLEMENTATION_EPOCH_SURFACE",
            f"{label} surface authorization verdict",
        )
        _require_equal(
            owner_document["owner"],
            {"identity": "ouyang", "approved": True},
            f"{label} surface authorization owner",
        )
        _require_equal(
            owner_document["implementation_epoch"],
            epoch_number,
            f"{label} surface authorization number",
        )
        base_commit = _git_commit(
            project_root,
            owner_document["base_commit"],
            f"{label} surface base",
        )
        if _git_parents(project_root, cast(str, owner["creating_commit"])) != (base_commit,):
            raise RehearsalV22ValidationError(
                f"{label} authority is not the direct child of its base"
            )
        if not _git_is_ancestor(
            project_root,
            SERIES_2_PREREGISTRATION_COMMIT,
            base_commit,
        ):
            raise RehearsalV22ValidationError(
                f"{label} authority base lost series-2 preregistration lineage"
            )
        rows = _array(owner_document["exact_surface"], f"{label} exact surface")
        if not rows:
            raise RehearsalV22ValidationError("implementation epoch exact surface is empty")
        expected_surface: dict[str, str] = {}
        ordered_surface_paths: list[str] = []
        for row_index, raw_row in enumerate(rows):
            row = _object(raw_row, f"{label} surface row {row_index}")
            _require_exact_keys(
                row,
                frozenset({"path", "status"}),
                f"{label} surface row {row_index}",
            )
            relative = _relative(row["path"], f"{label} surface row path")
            status_value = _string(row["status"], f"{label} surface row status")
            if (
                relative not in IMPLEMENTATION_PATHS
                or status_value not in {"A", "M"}
                or relative in expected_surface
            ):
                raise RehearsalV22ValidationError(
                    "implementation epoch exact surface escaped its five-path A/M allowlist"
                )
            expected_surface[relative] = status_value
            ordered_surface_paths.append(relative)
        if ordered_surface_paths != sorted(
            ordered_surface_paths,
            key=lambda value: value.encode("utf-8"),
        ):
            raise RehearsalV22ValidationError(
                "implementation epoch exact surface is not byte-sorted"
            )
        surface = _diff_name_status(project_root, parents[0], implementation_commit)
        if (
            len(surface) != len(expected_surface)
            or {path: status for status, path in surface} != expected_surface
        ):
            raise RehearsalV22ValidationError(
                "implementation epoch differs from owner exact surface"
            )
        review_payload = _validate_implementation_review_authority(
            project_root,
            review,
            implementation_commit=implementation_commit,
            execution_head=execution_head,
            require_worktree=True,
        )
        review_document = _object(
            strict_json_loads(review_payload, label=f"{label} independent review"),
            f"{label} independent review",
        )
        _validate_implementation_review_document(
            document=review_document,
            implementation_commit=implementation_commit,
            label=f"{label} independent review",
        )
        review_commit = cast(str, review["creating_commit"])
        if not _git_is_ancestor(
            project_root, implementation_commit, review_commit
        ) or not _git_is_ancestor(project_root, review_commit, execution_head):
            raise RehearsalV22ValidationError(f"{label} independent review topology drifted")
        for relative in IMPLEMENTATION_PATHS:
            blob = _git_blob(project_root, implementation_commit, relative)
            if epoch_number == replay.selected_implementation_epoch and selected_require_current:
                current = _regular_bytes(
                    _safe_path(
                        project_root,
                        relative,
                        f"epoch {epoch_number} implementation path",
                    ),
                    f"epoch {epoch_number} implementation path",
                )
                if current != blob:
                    raise RehearsalV22ValidationError(
                        f"selected implementation bytes drifted: {relative}"
                    )
        historical_cache = _historical_control_cache_for_epoch(
            project_root=project_root,
            validation_context=validation_context,
            replay=replay,
            epoch_number=epoch_number,
            implementation_commit=implementation_commit,
            expected_merkle_root_sha256=cast(str, epoch["control_merkle_root_sha256"]),
            execution_head=execution_head,
            selected_require_current=selected_require_current,
            control_pass_nonce=control_pass_nonce,
            cache=archives.selected_control_cache,
        )
        if historical_cache is None:
            epoch_control = implementation.build_control_surface(
                project_root,
                implementation_commit,
                require_current=False,
            )
            observed_commit = epoch_control.implementation_commit
            observed_root = epoch_control.merkle_root_sha256
            observed_loaded_sources = epoch_control.loaded_repository_sources
            observed_closure = epoch_control.ast_closure_paths
            observed_repository_paths = {
                record["repository_path"]
                for record in epoch_control.records
                if record["repository_path"] is not None
            }
        else:
            observed_commit = historical_cache.implementation_commit
            observed_root = historical_cache.merkle_root_sha256
            observed_loaded_sources = historical_cache.loaded_repository_sources
            observed_closure = historical_cache.ast_closure_paths
            observed_repository_paths = {
                record.repository_path
                for record in historical_cache.records
                if record.repository_path is not None
            }
        if (
            observed_commit != implementation_commit
            or observed_root != epoch["control_merkle_root_sha256"]
            or observed_loaded_sources
            or not set(observed_closure).issubset(observed_repository_paths)
        ):
            raise RehearsalV22ValidationError(
                f"implementation epoch {epoch_number} control surface replay drifted"
            )
        implementation.validate_implementation_epoch(
            project_root,
            epoch=epoch_number,
            implementation_commit=implementation_commit,
            owner_surface_authorization=_core_authority(owner),
            independent_review=_core_authority(review),
            control_merkle_root_sha256=cast(str, epoch["control_merkle_root_sha256"]),
            execution_head=execution_head,
            require_current_bytes=(
                selected_require_current and epoch_number == replay.selected_implementation_epoch
            ),
        )
    if (
        not replay.records
        or replay.records[0].get("implementation_epoch") != SERIES_2_EPOCH_ORIGIN
        or any(
            record.get("implementation_epoch", 0) < SERIES_2_EPOCH_ORIGIN
            for record in replay.records
        )
    ):
        raise RehearsalV22ValidationError(
            "series-2 executed history does not begin at explicit epoch 5"
        )
    selected = epochs.get(replay.selected_implementation_epoch)
    if selected is None:
        raise RehearsalV22ValidationError(
            "selected implementation epoch is absent from explicit epoch map"
        )
    _require_equal(
        selected["control_merkle_root_sha256"],
        archives.control_root_sha256,
        "selected epoch control root",
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
        raise RehearsalV22ValidationError("bundle directory contains a missing or extra directory")


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
        raise RehearsalV22ValidationError(f"{run_label} active replay temporary write root remains")
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
        raise RehearsalV22ValidationError(f"{run_label} active replay probe evidence drifted")


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


def _validate_common_bundle_once(
    *,
    project_root: Path,
    bundle_path: Path,
    authorized_bundle_directory: Path,
    binding: BindingView,
    expected_bundle_sha256: str | None,
    validation_context: BundleValidationContext,
) -> ValidatedBundle:
    root = project_root.resolve(strict=True)
    candidate = bundle_path.absolute()
    bundle_directory = _directory(
        authorized_bundle_directory,
        "mode-authorized bundle directory",
    )
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
    historical_schema_payload = _bound_control(
        root,
        BUNDLE_SCHEMA_RELATIVE,
        BUNDLE_SCHEMA_SHA256,
        "historical v2.2 bundle schema",
    )
    schema_payload = _bound_control(
        root,
        SERIES_2_BUNDLE_SCHEMA_RELATIVE,
        SERIES_2_BUNDLE_SCHEMA_SHA256,
        "series-2 bundle schema",
    )
    schema = _object(
        strict_json_loads(schema_payload, label="series-2 bundle schema"),
        "series-2 bundle schema",
    )
    _schema_validate(bundle, schema, "v2.2 bundle")
    preregistration_payload = _bound_control(
        root,
        PREREGISTRATION_RELATIVE,
        PREREGISTRATION_SHA256,
        "historical v2.2 preregistration",
    )
    historical_release_schema_payload = _bound_control(
        root,
        RELEASE_SCHEMA_RELATIVE,
        RELEASE_SCHEMA_SHA256,
        "historical v2.2 release schema",
    )
    release_schema_payload = _bound_control(
        root,
        SERIES_2_RELEASE_SCHEMA_RELATIVE,
        SERIES_2_RELEASE_SCHEMA_SHA256,
        "series-2 release schema",
    )
    _validate_contract_inheritance(
        project_root=root,
        preregistration_payload=preregistration_payload,
        bundle_schema_payload=historical_schema_payload,
        release_schema_payload=historical_release_schema_payload,
    )
    _validate_series_2_schema_profiles(
        project_root=root,
        historical_bundle_payload=historical_schema_payload,
        historical_release_payload=historical_release_schema_payload,
        active_bundle_payload=schema_payload,
        active_release_payload=release_schema_payload,
    )
    _validate_binding_document(bundle.get("execution_binding"), binding, "bundle execution binding")
    history_stub = _object(bundle.get("attempt_history"), "bundle attempt history")
    implementation_commit = _commit(
        _object(bundle.get("lineage"), "bundle lineage").get("implementation_commit"),
        "bundle implementation commit",
    )
    _validate_lineage(
        project_root=root,
        bundle=bundle,
        implementation_commit=implementation_commit,
    )
    _validate_harness_identity(
        project_root=root,
        bundle=bundle,
        implementation_commit=implementation_commit,
        validation_context=validation_context,
    )
    archive_control_pass_nonce = object()
    archives = _validate_archives(
        project_root=root,
        bundle_directory=bundle_directory,
        bundle=bundle,
        implementation_commit=implementation_commit,
        validation_context=validation_context,
        control_pass_nonce=archive_control_pass_nonce,
    )
    replay = _validate_attempt_history_records(
        project_root=root,
        bundle=bundle,
        ledger_root=binding.ledger_root,
        archive_root=bundle_directory / "archive/attempt-history",
        binding=binding,
    )
    # This passive re-read is intentionally inside the common bundle pass, so
    # both bundle validation and release revalidation fail before any replay
    # capability can run when a snapshot or either receipt is missing/drifted.
    mirror_receipts = _validate_second_copy_history(binding=binding, replay=replay)
    _require_equal(
        implementation_commit,
        replay.selected_implementation_commit,
        "bundle lineage selected implementation commit",
    )
    del history_stub
    _validate_history_summary(bundle=bundle, binding=binding, replay=replay, archives=archives)
    _validate_implementation_epochs(
        project_root=root,
        bundle=bundle,
        replay=replay,
        archives=archives,
        validation_context=validation_context,
        control_pass_nonce=archive_control_pass_nonce,
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
        implementation_commit=implementation_commit,
        archives=archives,
        history=replay,
        mirror_receipts=mirror_receipts,
    )


def _validate_historical_full_downstream_replay_evidence(
    validated: ValidatedBundle,
    anchor: HistoricalSelectedAnchor,
    governance: _ValidatedRecoveryGovernance,
    binding: BindingView,
) -> Mapping[str, str]:
    replay = validated.history
    if _historical_selected_anchor(replay, anchor) != anchor:
        raise RehearsalV22ValidationError("historical replay anchor substitution detected")
    sealed = _object(governance.r_document.get("sealed_series"), "recovery sealed series")
    authorization_contract = _object(
        governance.contract.get("recovery_authorization_contract"),
        "recovery authorization contract",
    )
    nested_fields = _object(
        authorization_contract.get("nested_exact_field_sets"),
        "recovery authorization nested fields",
    )
    _require_exact_keys(
        sealed,
        frozenset(cast(Sequence[str], nested_fields.get("sealed_series"))),
        "recovery sealed series",
    )
    selected_files = _object(sealed.get("selected_files"), "recovery selected files")
    _require_exact_keys(
        selected_files,
        frozenset(cast(Sequence[str], nested_fields.get("selected_files"))),
        "recovery selected files",
    )
    started_reference = _object(
        selected_files.get("started"),
        "recovery selected started reference",
    )
    candidate_reference = _object(
        selected_files.get("candidate"),
        "recovery selected candidate reference",
    )
    terminal_reference = _object(
        selected_files.get("terminal"),
        "recovery selected terminal reference",
    )
    selected_reference_fields = frozenset(
        cast(Sequence[str], nested_fields.get("selected_file_reference"))
    )
    for label, reference in (
        ("started", started_reference),
        ("candidate", candidate_reference),
        ("terminal", terminal_reference),
    ):
        _require_exact_keys(
            reference,
            selected_reference_fields,
            f"recovery selected {label} reference",
        )
    sealed_mirror = _object(sealed.get("sealed_mirror"), "recovery sealed mirror")
    _require_exact_keys(
        sealed_mirror,
        frozenset(cast(Sequence[str], nested_fields.get("sealed_mirror"))),
        "recovery sealed mirror",
    )
    started_payload = replay.live_payloads.get("attempts/000002/started.json")
    candidate_payload = replay.live_payloads.get("attempts/000002/candidate.json")
    terminal_payload = replay.live_payloads.get("attempts/000002/terminal.json")
    if (
        started_payload is None
        or candidate_payload is None
        or terminal_payload is None
        or started_reference
        != {
            "relative_path": "attempts/000002/started.json",
            "sha256": _sha256(started_payload),
            "bytes": len(started_payload),
        }
        or candidate_reference
        != {
            "relative_path": "attempts/000002/candidate.json",
            "sha256": _sha256(candidate_payload),
            "bytes": len(candidate_payload),
        }
        or terminal_reference
        != {
            "relative_path": "attempts/000002/terminal.json",
            "sha256": _sha256(terminal_payload),
            "bytes": len(terminal_payload),
        }
    ):
        raise RehearsalV22ValidationError("sealed selected candidate or terminal bytes drifted")
    candidate = _object(
        strict_json_loads(candidate_payload, label="sealed selected candidate"),
        "sealed selected candidate",
    )
    terminal = _object(
        strict_json_loads(terminal_payload, label="sealed selected terminal"),
        "sealed selected terminal",
    )
    evidence_prefix = "attempts/000002/evidence/"
    evidence_payloads = {
        relative.removeprefix(evidence_prefix): payload
        for relative, payload in replay.live_payloads.items()
        if relative.startswith(evidence_prefix)
    }
    evidence_root = _evidence_root(evidence_payloads)
    previous_history_root = _sha(
        replay.records[1].get("previous_history_root_sha256"),
        "selected previous history root",
    )
    candidate_content_root = _candidate_content_root(
        previous_history_root=previous_history_root,
        run_a_root=_sha(candidate.get("run_a_root_sha256"), "selected run A root"),
        run_b_root=_sha(candidate.get("run_b_root_sha256"), "selected run B root"),
        control_root=_sha(
            candidate.get("control_surface_root_sha256"),
            "selected control root",
        ),
        evidence_root=evidence_root,
    )
    validated_candidate_count = sum(
        record.get("outcome") == "CANDIDATE_VALIDATED_AND_SELECTED" for record in replay.records
    )
    if not validated.mirror_receipts:
        raise RehearsalV22ValidationError("recovery sealed mirror receipt history is empty")
    latest_receipt = validated.mirror_receipts[-1]
    latest_receipt_payload = _canonical_json_bytes(latest_receipt)
    latest_ordinal = _integer(
        latest_receipt.get("ordinal"),
        "recovery latest sealed mirror ordinal",
        minimum=1,
    )
    latest_live_root = _sha(
        latest_receipt.get("live_ledger_root_sha256"),
        "recovery latest sealed mirror live root",
    )
    latest_receipt_name = _mirror_receipt_filename(latest_ordinal, latest_live_root)
    expected_selected_files = {
        "started": {
            "relative_path": "attempts/000002/started.json",
            "sha256": _sha256(started_payload),
            "bytes": len(started_payload),
        },
        "candidate": {
            "relative_path": "attempts/000002/candidate.json",
            "sha256": _sha256(candidate_payload),
            "bytes": len(candidate_payload),
        },
        "terminal": {
            "relative_path": "attempts/000002/terminal.json",
            "sha256": _sha256(terminal_payload),
            "bytes": len(terminal_payload),
        },
    }
    expected_sealed_mirror = {
        "snapshot_count": len(validated.mirror_receipts),
        "receipt_count": len(validated.mirror_receipts),
        "latest_ordinal": latest_ordinal,
        "latest_snapshot_path": latest_receipt.get("secondary_snapshot_root"),
        "primary_receipt_path": (binding.primary_receipt_root / latest_receipt_name).as_posix(),
        "secondary_receipt_path": (binding.secondary_receipt_root / latest_receipt_name).as_posix(),
        "receipt_sha256": _sha256(latest_receipt_payload),
        "receipt_bytes": len(latest_receipt_payload),
        "inventory_sha256": latest_receipt.get("primary_inventory_sha256"),
        "file_count": latest_receipt.get("file_count"),
        "total_bytes": latest_receipt.get("total_bytes"),
        "paired_receipts_byte_identical": True,
    }
    expected_sealed_series = {
        "series_id": REHEARSAL_ID,
        "series_token_sha256": binding.series_token_sha256,
        "ledger_root": binding.ledger_root.as_posix(),
        "history_root_sha256": replay.history_root_sha256,
        "live_ledger_root_sha256": replay.live_ledger_root_sha256,
        "series_closed": (
            validated_candidate_count == 1
            and replay.selected_attempt_ordinal == len(replay.records)
        ),
        "started_count": replay.started_count,
        "failed_count": replay.failed_count,
        "incomplete_count": replay.incomplete_count,
        "validated_candidate_count": validated_candidate_count,
        "selected_attempt_ordinal": replay.selected_attempt_ordinal,
        "selected_implementation_epoch": replay.selected_implementation_epoch,
        "selected_implementation_commit": replay.selected_implementation_commit,
        "selected_control_merkle_root_sha256": candidate.get("control_surface_root_sha256"),
        "selected_evidence_tree_root_sha256": evidence_root,
        "selected_candidate_content_root_sha256": candidate_content_root,
        "selected_run_a_root_sha256": validated.archives.run_a_root_sha256,
        "selected_run_b_root_sha256": validated.archives.run_b_root_sha256,
        "selected_terminal_outcome": terminal.get("outcome"),
        "selected_reached_stage": terminal.get("reached_stage"),
        "automatic_retry_count": terminal.get("automatic_retry_count"),
        "selected_files": expected_selected_files,
        "sealed_mirror": expected_sealed_mirror,
    }
    if latest_receipt.get("primary_inventory_sha256") != latest_receipt.get(
        "secondary_inventory_sha256"
    ):
        raise RehearsalV22ValidationError("recovery latest sealed mirror inventories differ")
    _require_equal(sealed, expected_sealed_series, "recovery sealed series binding")
    if (
        sealed.get("selected_implementation_epoch") != anchor.implementation_epoch
        or sealed.get("selected_implementation_commit") != anchor.implementation_commit
        or sealed.get("selected_control_merkle_root_sha256") != anchor.control_merkle_root_sha256
        or sealed.get("history_root_sha256") != anchor.history_root_sha256
        or sealed.get("live_ledger_root_sha256") != anchor.live_ledger_root_sha256
        or sealed.get("selected_attempt_ordinal") != anchor.selected_attempt_ordinal
        or candidate.get("evidence_tree_root_sha256") != evidence_root
        or terminal.get("evidence_tree_root_sha256") != evidence_root
        or candidate.get("candidate_content_root_sha256") != candidate_content_root
        or candidate.get("run_a_root_sha256") != sealed.get("selected_run_a_root_sha256")
        or candidate.get("run_b_root_sha256") != sealed.get("selected_run_b_root_sha256")
        or validated.archives.run_a_root_sha256 != sealed.get("selected_run_a_root_sha256")
        or validated.archives.run_b_root_sha256 != sealed.get("selected_run_b_root_sha256")
    ):
        raise RehearsalV22ValidationError("historical full-downstream replay roots drifted")
    probe_sha256: dict[str, str] = {}
    for run_label in ("run-a", "run-b"):
        relative = f"attempts/000002/evidence/probes/{run_label}.json"
        archived_relative = f"archive/attempt-history/{relative}"
        probe_payload = replay.live_payloads.get(relative)
        if probe_payload is None or replay.archive_payloads.get(archived_relative) != probe_payload:
            raise RehearsalV22ValidationError(f"historical {run_label} probe bytes drifted")
        probe_sha256[run_label] = _sha256(probe_payload)
        probe = _object(
            strict_json_loads(probe_payload, label=f"historical {run_label} probe"),
            f"historical {run_label} probe",
        )
        _require_exact_keys(
            probe,
            frozenset(
                {
                    "cninfo_one_second_pacing",
                    "consumer_stage_gates",
                    "deterministic_ineligible_zero_retry",
                    "unexpected_failure_aborts",
                    "zero_retry_model_contract",
                }
            ),
            f"historical {run_label} probe",
        )
        for name, raw_gate in probe.items():
            gate = _object(raw_gate, f"historical {run_label} probe {name}")
            if (
                gate.get("run_label") != run_label
                or gate.get("status") != "PASS"
                or gate.get("real_database_reads") != 0
                or gate.get("real_model_calls") != 0
                or gate.get("real_network_calls") != 0
                or gate.get("retry_count", 0) != 0
                or gate.get("max_retries", 0) != 0
            ):
                raise RehearsalV22ValidationError(
                    f"historical {run_label} probe gate drifted: {name}"
                )
    epochs = _epoch_map(validated.document, epoch_origin=SERIES_2_EPOCH_ORIGIN)
    if tuple(epochs) != (5, 6):
        raise RehearsalV22ValidationError("recovered bundle epoch table is not exactly [5,6]")
    return {
        "selected_candidate_sha256": _sha256(candidate_payload),
        "selected_terminal_sha256": _sha256(terminal_payload),
        "selected_evidence_tree_root_sha256": _sha(
            evidence_root,
            "recovery selected evidence root",
        ),
        "historical_run_a_root_sha256": validated.archives.run_a_root_sha256,
        "historical_run_b_root_sha256": validated.archives.run_b_root_sha256,
        "historical_run_a_probe_sha256": probe_sha256["run-a"],
        "historical_run_b_probe_sha256": probe_sha256["run-b"],
    }


def _independent_historical_anchor(
    value: implementation.HistoricalSelectedAnchor,
) -> HistoricalSelectedAnchor:
    if not isinstance(value, implementation.HistoricalSelectedAnchor):
        raise RehearsalV22ValidationError("producer historical anchor type drifted")
    return HistoricalSelectedAnchor(
        implementation_epoch=value.selected_epoch,
        implementation_commit=value.selected_commit,
        control_merkle_root_sha256=value.control_surface.merkle_root_sha256,
        history_root_sha256=value.history_root_sha256,
        live_ledger_root_sha256=value.live_ledger_root_sha256,
        selected_attempt_ordinal=2,
        require_current=False,
    )


def _independent_live_anchor(value: implementation.LiveExecutionAnchor) -> LiveExecutionAnchor:
    if not isinstance(value, implementation.LiveExecutionAnchor):
        raise RehearsalV22ValidationError("producer live anchor type drifted")
    return LiveExecutionAnchor(
        implementation_epoch=value.execution_epoch,
        implementation_commit=value.implementation_commit,
        control_merkle_root_sha256=value.control_surface.merkle_root_sha256,
        control_record_count=len(value.control_surface.records),
        execution_head=value.execution_head,
        owner_surface_authorization=value.owner_surface_authorization.as_json(),
        independent_implementation_review=value.independent_implementation_review.as_json(),
        landing_commit=value.merge_commit,
        landing_report=value.landing_report.as_json(),
        real_lineage_census_sha256=value.real_lineage_census_sha256,
        require_current=True,
    )


def _independent_recovery_work_counters(
    *,
    validated: ValidatedBundle,
    census: Mapping[str, Any],
    work_tracker: _IndependentRecoveryWorkTracker,
) -> Mapping[str, int]:
    """Bound this validator's actual read/hash surface from validated bytes.

    No producer-supplied counter participates.  Git work is the validator's
    incrementally measured census work; remaining fields are recomputed from
    independently validated bytes.  Validator recovery is read-only, so it
    copies zero bundle bytes.
    """

    measured_git_work = work_tracker.snapshot()["git_objects_read"]
    recursive_payloads = (
        validated.payload,
        *validated.archives.all_payloads.values(),
        *validated.history.live_payloads.values(),
        *validated.history.archive_payloads.values(),
        _canonical_json_bytes(census),
    )
    counters = {
        "git_objects_read": measured_git_work,
        "recursive_bytes_hashed": sum(len(payload) for payload in recursive_payloads),
        "sealed_snapshot_files_visited": (
            len(validated.archives.all_payloads)
            + len(validated.history.live_payloads)
            + len(validated.history.archive_payloads)
        ),
        "bundle_bytes_copied": 0,
    }
    _assert_recovery_work_bound(counters)
    return counters


def _validate_active_bundle_once(
    *,
    project_root: Path,
    bundle_path: Path,
    binding: BindingView,
    raw_binding: implementation.ExecutionBinding,
    published_release_revalidation: bool,
    expected_bundle_sha256: str | None,
) -> ValidatedBundle:
    authorized = _authorized_bundle_directory(
        binding=binding,
        raw_binding=raw_binding,
        bundle_path=bundle_path,
        published_release_revalidation=published_release_revalidation,
    )
    return _validate_common_bundle_once(
        project_root=project_root,
        bundle_path=bundle_path,
        authorized_bundle_directory=authorized,
        binding=binding,
        expected_bundle_sha256=expected_bundle_sha256,
        validation_context=ActiveBundleValidationContext(
            mode=BundleValidationMode.ACTIVE_ATTEMPT_BUNDLE,
        ),
    )


def validate_recovered_bundle(
    *,
    project_root: Path,
    bundle_path: Path,
    execution_context: implementation.RecoveryExecutionCapability,
    validator_delegation: implementation.RecoveryValidatorDelegation,
) -> JsonObject:
    """Passively validate one recovery-stage bundle with no replay capability."""

    requested_root = project_root.absolute()
    delegated = implementation._validate_recovery_validator_delegation(
        execution_context,
        validator_delegation,
        sys.modules[__name__],
        requested_root,
        bundle_path,
    )
    raw_binding, authorization, owner_binding, producer_historical, producer_live = delegated
    root = requested_root.resolve(strict=True)
    historical_anchor = _independent_historical_anchor(producer_historical)
    live_anchor = _independent_live_anchor(producer_live)
    governance = _validate_recovery_governance(
        root,
        recovery_authorization_path=authorization.path,
        owner_binding_path=owner_binding.path,
        expected_series_token_sha256=raw_binding.series_token_sha256,
    )
    _validate_delegated_recovery_governance(governance, authorization, owner_binding)
    live_control_pass_nonce = object()
    work_tracker = _IndependentRecoveryWorkTracker()
    first_census_before = work_tracker.snapshot()
    first_components_before = (
        work_tracker.git_subprocesses_started,
        work_tracker.git_object_read_occurrences,
    )
    observed_live, _census, additional_specs, live_control = _live_anchor_from_recovery_governance(
        root,
        governance,
        control_pass_nonce=live_control_pass_nonce,
        work_tracker=work_tracker,
    )
    first_census_after = work_tracker.snapshot()
    first_census_delta = _recovery_work_delta(first_census_before, first_census_after)
    first_component_delta = (
        work_tracker.git_subprocesses_started - first_components_before[0],
        work_tracker.git_object_read_occurrences - first_components_before[1],
    )
    if observed_live != live_anchor:
        raise RehearsalV22ValidationError("recovered-bundle live anchor substitution detected")
    binding = _binding_view(raw_binding)
    if binding.project_root != root:
        raise RehearsalV22ValidationError("recovered-bundle binding root drifted")
    candidate = bundle_path.absolute()
    authorized_directory = _directory(candidate.parent, "recovery-stage bundle directory")
    validation_context = RecoveredBundleValidationContext(
        mode=BundleValidationMode.PASSIVE_RECOVERED_BUNDLE,
        historical_anchor=historical_anchor,
        live_anchor=live_anchor,
    )
    validated = _validate_common_bundle_once(
        project_root=root,
        bundle_path=candidate,
        authorized_bundle_directory=authorized_directory,
        binding=binding,
        expected_bundle_sha256=None,
        validation_context=validation_context,
    )
    observed_historical = _historical_selected_anchor(validated.history, historical_anchor)
    if observed_historical != historical_anchor:
        raise RehearsalV22ValidationError(
            "recovered-bundle historical anchor substitution detected"
        )
    _validate_historical_full_downstream_replay_evidence(
        validated,
        historical_anchor,
        governance,
        binding,
    )
    final_census_before = work_tracker.snapshot()
    final_components_before = (
        work_tracker.git_subprocesses_started,
        work_tracker.git_object_read_occurrences,
    )
    final_census = _validate_live_execution_anchor(
        root,
        live_anchor,
        additional_census_specs=additional_specs,
        control_pass_nonce=live_control_pass_nonce,
        ref_snapshot_sha256=cast(str, _census.get("ref_snapshot_after_sha256")),
        cached_current_control=live_control,
        work_tracker=work_tracker,
    )
    final_census_delta = _recovery_work_delta(
        final_census_before,
        work_tracker.snapshot(),
    )
    final_component_delta = (
        work_tracker.git_subprocesses_started - final_components_before[0],
        work_tracker.git_object_read_occurrences - final_components_before[1],
    )
    if (
        final_census_delta != first_census_delta
        or final_component_delta != first_component_delta
    ):
        raise RehearsalV22ValidationError(
            "recovered-bundle census work changed between identical passes"
        )
    _independent_recovery_work_counters(
        validated=validated,
        census=final_census,
        work_tracker=work_tracker,
    )
    return validated.document


def _active_replay_validated_bundle(
    *,
    validated: ValidatedBundle,
    binding: BindingView,
    raw_binding: implementation.ExecutionBinding,
    execution_context: object | None,
    published_release_revalidation: bool,
) -> None:
    # Mirror state is mutable outside Git and can disappear after the common
    # bundle pass.  Re-read it immediately before borrowing any active replay
    # authority; failure here is still read-only and reaches no replay.
    _validate_second_copy_history(binding=binding, replay=validated.history)
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
        implementation_commit=validated.implementation_commit,
        execution_context=execution_context,
        archives=validated.archives,
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
    validated = _validate_active_bundle_once(
        project_root=resolved.view.project_root,
        bundle_path=bundle_path,
        binding=resolved.view,
        raw_binding=resolved.raw,
        published_release_revalidation=False,
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
        raise RehearsalV22ValidationError("bundle selected ordinal is outside its attempt history")
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
    acceptance = _object(receipt.get("attempt_history_acceptance"), "release history acceptance")
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
            "all_started_candidate_terminal_action_authorization_and_actual_evidence_bytes_archived"
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
    bundle_epoch_map = _epoch_map(bundle, epoch_origin=SERIES_2_EPOCH_ORIGIN)
    release_epochs = _array(receipt.get("implementation_epochs"), "release epochs")
    expected_epochs = []
    expected_epoch_map: dict[int, JsonObject] = {}
    for epoch_number, epoch in bundle_epoch_map.items():
        projected = {
            "epoch": epoch["epoch"],
            "implementation_commit": epoch["implementation_commit"],
            "owner_surface_authorization": epoch["owner_exact_surface_authorization"],
            "independent_implementation_review": epoch["independent_implementation_review"],
            "control_merkle_root_sha256": epoch["control_merkle_root_sha256"],
            "first_attempt_ordinal": epoch["first_attempt_ordinal"],
            "last_attempt_ordinal": epoch["last_attempt_ordinal"],
        }
        expected_epochs.append(projected)
        expected_epoch_map[epoch_number] = projected
    _require_equal(release_epochs, expected_epochs, "release implementation epochs")
    selected_epoch_number = _integer(
        selected.get("implementation_epoch"),
        "selected attempt implementation epoch",
        minimum=1,
    )
    selected_epoch = expected_epoch_map.get(selected_epoch_number)
    if selected_epoch is None:
        raise RehearsalV22ValidationError(
            "selected attempt implementation epoch is outside the epoch table"
        )
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
    production = _object(receipt.get("production_integration_gate"), "production integration gate")
    if production.get("this_receipt_unlocks_real_stages") is not False:
        raise RehearsalV22ValidationError("evidence receipt unlocks a real stage")
    locks = _object(receipt.get("locks"), "release locks")
    if (
        any(
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
        )
        or locks.get("trading_mode") != "research"
    ):
        raise RehearsalV22ValidationError("release locks are not fail-closed")


def _validate_passive_release_receipt(
    *,
    project_root: Path,
    receipt_path: Path,
    binding: BindingView,
    work_tracker: _IndependentRecoveryWorkTracker,
) -> tuple[
    JsonObject,
    bytes,
    JsonObject,
    str,
    str,
    tuple[AuthorityCensusSpec, ...],
]:
    root = project_root.resolve(strict=True)
    expected_receipt = root / RELEASE_RELATIVE
    if receipt_path.absolute() != expected_receipt:
        raise RehearsalV22ValidationError("recovered release receipt path is not fixed")
    receipt_payload = _regular_bytes(expected_receipt, "recovered release receipt")
    receipt = _object(
        strict_json_loads(receipt_payload, label="recovered release receipt"),
        "recovered release receipt",
    )
    schema_payload = _bound_control(
        root,
        SERIES_2_RELEASE_SCHEMA_RELATIVE,
        SERIES_2_RELEASE_SCHEMA_SHA256,
        "series-2 recovered release schema",
    )
    schema = _object(
        strict_json_loads(schema_payload, label="recovered release schema"),
        "recovered release schema",
    )
    _schema_validate(receipt, schema, "recovered release receipt")
    _validate_binding_document(
        receipt.get("execution_binding"),
        binding,
        "recovered release execution binding",
    )
    lineage = _object(receipt.get("lineage"), "recovered release lineage")
    bundle_ref = _validate_file_ref(lineage.get("bundle"), "recovered release bundle")
    _require_equal(
        bundle_ref["path"],
        f"{REGISTERED_DESTINATION_RELATIVE.as_posix()}/{BUNDLE_FILENAME}",
        "recovered release bundle path",
    )
    reviewed_head = _git_commit(
        root,
        receipt.get("reviewed_repository_head"),
        "recovered release reviewed HEAD",
        work_tracker=work_tracker,
    )
    _require_equal(
        lineage.get("rehearsal_evidence_commit"),
        reviewed_head,
        "recovered release reviewed/evidence HEAD",
    )
    receipt_commit, creation_payload = _unique_a_unserialized(
        root,
        path=RELEASE_RELATIVE.as_posix(),
        execution_head=_git_bytes(root, "rev-parse", "HEAD", work_tracker=work_tracker)
        .decode("ascii", errors="strict")
        .strip(),
        work_tracker=work_tracker,
    )
    if (
        creation_payload != receipt_payload
        or receipt_commit == reviewed_head
        or not _git_is_ancestor(
            root,
            reviewed_head,
            receipt_commit,
            work_tracker=work_tracker,
        )
    ):
        raise RehearsalV22ValidationError("recovered release receipt identity drifted")
    authority_specs: list[AuthorityCensusSpec] = []
    for key in (
        "v2_1_incident",
        "remediation_request",
        "v2_2_scope_authorization",
        "review_request",
    ):
        authority = _validate_authority_ref(lineage.get(key), f"recovered release {key}")
        _unique_a_authority(
            root,
            authority,
            require_worktree=True,
            work_tracker=work_tracker,
        )
        if not _git_is_ancestor(
            root,
            cast(str, authority["creating_commit"]),
            reviewed_head,
            work_tracker=work_tracker,
        ):
            raise RehearsalV22ValidationError(
                f"recovered release authority is outside reviewed HEAD: {key}"
            )
        authority_specs.append(
            AuthorityCensusSpec(
                path=cast(str, authority["path"]),
                pinned_sha256=cast(str, authority["sha256"]),
                pinned_creating_commit=cast(str, authority["creating_commit"]),
                role=AuthorityCensusRole.PINNED_SOURCE,
            )
        )
    release_specs = (
        AuthorityCensusSpec(
            path=RELEASE_RELATIVE.as_posix(),
            pinned_sha256=_sha256(receipt_payload),
            pinned_creating_commit=receipt_commit,
            role=AuthorityCensusRole.DISCOVER_SOURCE_AFTER_PROJECTIONS,
        ),
        *authority_specs,
    )
    return (
        receipt,
        receipt_payload,
        bundle_ref,
        reviewed_head,
        receipt_commit,
        release_specs,
    )


def _recovery_tree_fingerprint_with_work(
    root: Path,
) -> tuple[dict[str, str], int, int]:
    """Independently reproduce a tree commitment and its actual read work."""

    if not _validator_os.path.lexists(root):
        return {".": "absent"}, 0, 0
    metadata = root.lstat()
    if root.is_symlink():
        return {".": f"symlink:{_validator_os.readlink(root)}"}, 0, 0
    if root.is_file():
        payload = _regular_bytes(root, "recovery tree file", allow_empty=True)
        return {".": f"file:{_sha256(payload)}:{metadata.st_mode:o}"}, len(payload), 1
    if not root.is_dir():
        return {".": f"special:{metadata.st_mode:o}"}, 0, 0
    observed = {".": f"directory:{stat.S_IMODE(metadata.st_mode):04o}"}
    recursive_bytes = 0
    files_visited = 0
    for path in sorted(
        root.rglob("*"),
        key=lambda item: item.relative_to(root).as_posix().encode("utf-8"),
    ):
        relative = path.relative_to(root).as_posix()
        member = path.lstat()
        if path.is_symlink():
            observed[relative] = f"symlink:{_validator_os.readlink(path)}"
        elif path.is_file():
            payload = _regular_bytes(
                path,
                f"recovery tree file {relative}",
                allow_empty=True,
            )
            observed[relative] = (
                f"file:{_sha256(payload)}:{stat.S_IMODE(member.st_mode):04o}:{member.st_nlink}"
            )
            recursive_bytes += len(payload)
            files_visited += 1
        elif path.is_dir():
            observed[relative] = f"directory:{stat.S_IMODE(member.st_mode):04o}"
        else:
            observed[relative] = f"special:{member.st_mode:o}"
    return observed, recursive_bytes, files_visited


def _recovery_tree_fingerprint(root: Path) -> dict[str, str]:
    """Independently reproduce the registered recovery tree commitment."""

    return _recovery_tree_fingerprint_with_work(root)[0]


def _validate_recovery_timestamp_values(
    utc_value: object,
    shanghai_value: object,
    *,
    label: str,
) -> None:
    utc_text = _rfc3339_utc(utc_value, f"{label} UTC timestamp")
    shanghai_text = _rfc3339_shanghai(shanghai_value, f"{label} Shanghai timestamp")
    if datetime.fromisoformat(utc_text.replace("Z", "+00:00")) != datetime.fromisoformat(
        shanghai_text
    ):
        raise RehearsalV22ValidationError(f"{label} timestamps disagree")


def _validated_recovery_started_execution_head(
    project_root: Path,
    value: object,
    *,
    live_execution_head: str,
) -> str:
    started_head = _commit(value, "recovery claim started execution HEAD")
    if not _git_is_ancestor(project_root, started_head, live_execution_head):
        raise RehearsalV22ValidationError(
            "recovery claim started HEAD left the current live lineage"
        )
    return started_head


def _validate_epoch_8_preflight_recovery_storage_live(
    governance: _ValidatedRecoveryGovernance,
    *,
    binding: BindingView,
    primary_container: Path,
    secondary_container: Path,
) -> None:
    """Cross-bind Q's recorded storage and sealed inputs at the storage stage."""

    preflight = governance.preflight_document
    series_storage = _object(
        preflight.get("series_2_registered_storage"),
        "recorded preflight series-2 storage",
    )

    def actual_directory_evidence(path: Path, label: str) -> JsonObject:
        _strict_owner_directory_identity(path, label=label)
        metadata = path.lstat()
        return {
            "path": path.as_posix(),
            "owner_uid": metadata.st_uid,
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "mode_octal": "0700",
            "non_symlink": True,
            "canonical_unaliased": True,
        }

    if (
        series_storage.get("primary_container")
        != actual_directory_evidence(
            binding.primary_series_container,
            "series-2 primary container",
        )
        or series_storage.get("secondary_container")
        != actual_directory_evidence(
            binding.secondary_series_container,
            "series-2 secondary container",
        )
    ):
        raise RehearsalV22ValidationError(
            "series-2 container identity differs from the registered preflight"
        )
    for path, label in (
        (binding.ledger_root, "series-2 primary ledger"),
        (binding.primary_receipt_root, "series-2 primary receipts"),
        (binding.secondary_snapshot_root, "series-2 secondary snapshots"),
        (binding.secondary_receipt_root, "series-2 secondary receipts"),
    ):
        _strict_owner_directory_identity(path, label=label)
    if series_storage.get("registered_leaf_state") != {
        "primary_ledger": "PRESENT_VERIFIED",
        "primary_receipts": "PRESENT_VERIFIED",
        "secondary_receipts": "PRESENT_VERIFIED",
        "secondary_snapshots": "PRESENT_VERIFIED",
    }:
        raise RehearsalV22ValidationError(
            "series-2 leaf state differs from the registered preflight"
        )

    sealed_series = _object(
        governance.r_document.get("sealed_series"),
        "recovery sealed series",
    )
    sealed_mirror = _object(sealed_series.get("sealed_mirror"), "recovery sealed mirror")
    if series_storage.get("mirrored_history") != {
        "attempt_count": sealed_series.get("started_count"),
        "history_root_sha256": sealed_series.get("history_root_sha256"),
        "live_ledger_root_sha256": sealed_series.get("live_ledger_root_sha256"),
        "receipt_count": sealed_mirror.get("receipt_count"),
        "series_closed": sealed_series.get("series_closed"),
    }:
        raise RehearsalV22ValidationError(
            "series-2 mirrored history differs from the registered preflight"
        )
    roots = {
        "active_ledger": binding.ledger_root,
        "primary_seal_receipt": Path(
            _string(sealed_mirror.get("primary_receipt_path"), "primary seal receipt")
        ),
        "secondary_seal_receipt": Path(
            _string(sealed_mirror.get("secondary_receipt_path"), "secondary seal receipt")
        ),
        "through_ordinal_2_snapshot": Path(
            _string(sealed_mirror.get("latest_snapshot_path"), "sealed snapshot")
        ),
    }
    observed_fingerprints: dict[str, str] = {}
    recursive_bytes = 0
    files_visited = 0
    for name, path in roots.items():
        tree, observed_bytes, observed_files = _recovery_tree_fingerprint_with_work(path)
        observed_fingerprints[name] = _sha256(_canonical_json_bytes(tree))
        recursive_bytes += observed_bytes
        files_visited += observed_files
    observed_counters = {
        "git_objects_read": 0,
        "recursive_bytes_hashed": recursive_bytes,
        "sealed_snapshot_files_visited": files_visited,
        "bundle_bytes_copied": 0,
    }
    _assert_recovery_work_bound(observed_counters)
    recorded_sealed = _object(
        preflight.get("sealed_recovery_inputs"),
        "recorded preflight sealed inputs",
    )
    if (
        recorded_sealed.get("sealed_input_fingerprints") != observed_fingerprints
        or recorded_sealed.get("work_counters") != observed_counters
    ):
        raise RehearsalV22ValidationError(
            "sealed input fingerprints or work differ from the registered preflight"
        )

    recorded = preflight.get("registered_recovery_storage")
    if preflight.get("mode") == "NONREGISTERED_READ_ONLY_TEST":
        if recorded is not None:
            raise RehearsalV22ValidationError(
                "synthetic preflight unexpectedly recorded recovery storage"
            )
        return
    if preflight.get("mode") != "REGISTERED_OFFICIAL":
        raise RehearsalV22ValidationError("preflight recovery storage mode drifted")
    recorded_storage = _object(recorded, "recorded preflight recovery storage")
    for key, path, label in (
        ("primary_container", primary_container, "primary recovery container"),
        ("secondary_container", secondary_container, "secondary recovery container"),
    ):
        evidence = _object(recorded_storage.get(key), f"recorded {label}")
        metadata = path.lstat()
        expected = {
            "path": path.as_posix(),
            "owner_uid": metadata.st_uid,
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "mode_octal": "0700",
            "non_symlink": True,
            "canonical_unaliased": True,
        }
        if evidence != expected:
            raise RehearsalV22ValidationError(
                f"{label} identity differs from the registered preflight"
            )


def _validate_durable_recovery_evidence(
    *,
    root: Path,
    binding: BindingView,
    governance: _ValidatedRecoveryGovernance,
    capability: RecoveredPublicationCapability,
    live_anchor: LiveExecutionAnchor,
    bundle_payload: bytes,
) -> JsonObject:
    """Re-read claim, mirror and paired receipts without trusting capability fields."""

    destination_contract = _object(
        governance.r_document.get("destination"),
        "recovery durable destination contract",
    )
    storage_contract = _object(
        destination_contract.get("recovery_storage"),
        "recovery durable storage contract",
    )
    sealed_mirror = _object(
        _object(
            governance.r_document.get("sealed_series"),
            "recovery durable sealed series",
        ).get("sealed_mirror"),
        "recovery durable sealed mirror",
    )
    expected_sealed_receipt_sha256 = _sha(
        sealed_mirror.get("receipt_sha256"),
        "recovery durable sealed mirror receipt",
    )
    primary_container = Path(
        _string(
            storage_contract.get("primary_recovery_container"),
            "recovery primary container",
        )
    ).absolute()
    secondary_container = Path(
        _string(
            storage_contract.get("secondary_recovery_container"),
            "recovery secondary container",
        )
    ).absolute()
    _strict_owner_directory_identity(primary_container, label="recovery primary container")
    _strict_owner_directory_identity(secondary_container, label="recovery secondary container")
    _validate_epoch_8_preflight_recovery_storage_live(
        governance,
        binding=binding,
        primary_container=primary_container,
        secondary_container=secondary_container,
    )
    r_sha = _sha256(governance.r_payload)
    claim_root = primary_container / f"CLAIM-{r_sha}"
    _strict_owner_directory_identity(claim_root, label="recovery claim root")
    if claim_root.as_posix() != capability.claim_root or sorted(
        path.name for path in claim_root.iterdir()
    ) != ["started.json", "terminal.json"]:
        raise RehearsalV22ValidationError("recovery claim identity or inventory drifted")
    started_payload = _regular_bytes(claim_root / "started.json", "recovery claim started")
    terminal_payload = _regular_bytes(claim_root / "terminal.json", "recovery claim terminal")
    started = _strict_canonical_json_loads(started_payload, label="recovery claim started")
    terminal = _strict_canonical_json_loads(terminal_payload, label="recovery claim terminal")
    _require_exact_keys(started, RECOVERY_STARTED_FIELDS, "recovery claim started")
    _require_exact_keys(terminal, RECOVERY_TERMINAL_FIELDS, "recovery claim terminal")
    _validate_recovery_timestamp_values(
        started.get("created_at_utc"),
        started.get("created_at_shanghai"),
        label="recovery claim started",
    )
    _validate_recovery_timestamp_values(
        terminal.get("completed_at_utc"),
        terminal.get("completed_at_shanghai"),
        label="recovery claim terminal",
    )
    r_reference = {
        "path": governance.r_path.relative_to(root).as_posix(),
        "sha256": r_sha,
        "creating_commit": governance.r_commit,
        "unique_a_history_verified": True,
    }
    b_reference = {
        "path": governance.b_path.relative_to(root).as_posix(),
        "sha256": _sha256(governance.b_payload),
        "creating_commit": governance.b_commit,
        "unique_a_history_verified": True,
    }
    recovery_id = _string(
        governance.r_document.get("authorization_id"),
        "recovery authorization id",
    )
    started_execution_head = _validated_recovery_started_execution_head(
        root,
        started.get("execution_head"),
        live_execution_head=live_anchor.execution_head,
    )
    destination = binding.absolute_destination
    destination_stage = destination.parent / f".{destination.name}.recovery-stage-{r_sha}"
    secondary_stage = secondary_container / f".bundle-snapshot-stage-{r_sha}"
    if (
        started.get("schema_version") != SERIES_2_RECOVERY_STARTED_SCHEMA
        or started.get("recovery_id") != recovery_id
        or started.get("authorization") != r_reference
        or started.get("owner_confirmation_binding") != b_reference
        or started.get("execution_epoch") != capability.execution_epoch
        or started.get("sealed_history_root_sha256") != capability.sealed_history_root_sha256
        or started.get("sealed_live_ledger_root_sha256")
        != capability.sealed_live_ledger_root_sha256
        or started.get("sealed_mirror_receipt_sha256") != expected_sealed_receipt_sha256
        or started.get("destination") != destination.as_posix()
        or started.get("destination_stage") != destination_stage.as_posix()
        or started.get("secondary_snapshot_stage") != secondary_stage.as_posix()
        or started.get("secondary_snapshot_target")
        != (secondary_container / f"RECOVERED-BUNDLE-{r_sha}-<TREE_SHA256>").as_posix()
        or started.get("state") != "STARTED"
        or started.get("authorized_bundle_recovery_starts") != 1
        or started.get("authorized_pipeline_starts") != 0
        or started.get("automatic_retry_count") != 0
    ):
        raise RehearsalV22ValidationError("recovery claim started semantics drifted")
    primary_receipt = Path(
        _string(terminal.get("primary_receipt"), "recovery primary receipt")
    ).absolute()
    secondary_receipt = Path(
        _string(terminal.get("secondary_receipt"), "recovery secondary receipt")
    ).absolute()
    secondary_snapshot = Path(
        _string(terminal.get("secondary_snapshot"), "recovery secondary snapshot")
    ).absolute()
    published_tree_sha = _sha(
        terminal.get("published_tree_sha256"),
        "recovery published tree SHA",
    )
    receipt_name = f"recovery-{r_sha}-{published_tree_sha}.bundle-mirror-verification.json"
    snapshot_name = f"RECOVERED-BUNDLE-{r_sha}-{published_tree_sha}"
    if (
        primary_receipt != primary_container / receipt_name
        or secondary_receipt != secondary_container / receipt_name
        or secondary_snapshot != secondary_container / snapshot_name
        or capability.primary_receipt_path != primary_receipt.as_posix()
        or capability.secondary_receipt_path != secondary_receipt.as_posix()
        or capability.secondary_snapshot != secondary_snapshot.as_posix()
    ):
        raise RehearsalV22ValidationError("recovery durable output paths drifted")
    _strict_owner_directory_identity(destination, label="recovery published destination")
    _strict_owner_directory_identity(secondary_snapshot, label="recovery secondary snapshot")
    destination_fingerprint = _recovery_tree_fingerprint(destination)
    snapshot_fingerprint = _recovery_tree_fingerprint(secondary_snapshot)
    destination_tree_sha = _sha256(_canonical_json_bytes(destination_fingerprint))
    snapshot_tree_sha = _sha256(_canonical_json_bytes(snapshot_fingerprint))
    primary_receipt_payload = _regular_bytes(primary_receipt, "recovery primary receipt")
    secondary_receipt_payload = _regular_bytes(secondary_receipt, "recovery secondary receipt")
    receipt = _strict_canonical_json_loads(
        primary_receipt_payload,
        label="recovery paired receipt",
    )
    _require_exact_keys(receipt, RECOVERY_MIRROR_RECEIPT_FIELDS, "recovery paired receipt")
    _rfc3339_utc(receipt.get("verified_at_utc"), "recovery receipt verified timestamp")
    if (
        primary_receipt_payload != secondary_receipt_payload
        or destination_fingerprint != snapshot_fingerprint
        or destination_tree_sha != published_tree_sha
        or snapshot_tree_sha != published_tree_sha
        or capability.published_tree_sha256 != published_tree_sha
        or capability.secondary_snapshot_tree_sha256 != snapshot_tree_sha
        or _sha256(bundle_payload) != capability.published_bundle_sha256
        or _sha256(primary_receipt_payload) != capability.paired_receipt_sha256
        or len(primary_receipt_payload) != capability.paired_receipt_bytes
        or _sha256(started_payload) != capability.claim_started_sha256
        or _sha256(terminal_payload) != capability.claim_terminal_sha256
    ):
        raise RehearsalV22ValidationError("recovery durable bytes or tree roots drifted")
    expected_receipt = {
        "schema_version": SERIES_2_RECOVERY_MIRROR_RECEIPT_SCHEMA,
        "recovery_authorization_sha256": r_sha,
        "owner_confirmation_binding_sha256": _sha256(governance.b_payload),
        "recovery_id": recovery_id,
        "series_id": REHEARSAL_ID,
        "series_token_sha256": binding.series_token_sha256,
        "sealed_history_root_sha256": capability.sealed_history_root_sha256,
        "sealed_live_ledger_root_sha256": capability.sealed_live_ledger_root_sha256,
        "selected_attempt_ordinal": capability.selected_attempt_ordinal,
        "selected_implementation_epoch": capability.selected_implementation_epoch,
        "selected_implementation_commit": capability.selected_implementation_commit,
        "execution_epoch": capability.execution_epoch,
        "execution_implementation_commit": capability.execution_implementation_commit,
        "execution_head": started_execution_head,
        "destination": destination.as_posix(),
        "published_bundle_sha256": _sha256(bundle_payload),
        "published_tree_sha256": published_tree_sha,
        "secondary_snapshot": secondary_snapshot.as_posix(),
        "secondary_snapshot_tree_sha256": snapshot_tree_sha,
        "destination_and_snapshot_byte_identical": True,
        "pipeline_starts": 0,
        "automatic_retry_count": 0,
        "sealed_ledger_before_after_equal": True,
        "sealed_mirror_before_after_equal": True,
        "verified_at_utc": receipt.get("verified_at_utc"),
    }
    _require_equal(receipt, expected_receipt, "recovery paired receipt semantics")
    if (
        terminal.get("schema_version") != SERIES_2_RECOVERY_TERMINAL_SCHEMA
        or terminal.get("recovery_id") != recovery_id
        or terminal.get("authorization") != r_reference
        or terminal.get("owner_confirmation_binding") != b_reference
        or terminal.get("outcome") != "BUNDLE_RECOVERY_PUBLISHED_MIRRORED_AND_RECEIPTED"
        or terminal.get("reached_stage") != "paired_receipts_verified"
        or terminal.get("sealed_ledger_before_sha256") != terminal.get("sealed_ledger_after_sha256")
        or terminal.get("sealed_mirror_before_sha256") != terminal.get("sealed_mirror_after_sha256")
        or terminal.get("destination") != destination.as_posix()
        or terminal.get("published_bundle_sha256") != _sha256(bundle_payload)
        or terminal.get("published_tree_sha256") != published_tree_sha
        or terminal.get("secondary_snapshot") != secondary_snapshot.as_posix()
        or terminal.get("secondary_snapshot_tree_sha256") != snapshot_tree_sha
        or terminal.get("primary_receipt") != primary_receipt.as_posix()
        or terminal.get("secondary_receipt") != secondary_receipt.as_posix()
        or terminal.get("paired_receipts_byte_identical") is not True
        or terminal.get("destination_stage_absent") is not True
        or terminal.get("secondary_snapshot_stage_absent") is not True
        or terminal.get("pipeline_starts") != 0
        or terminal.get("automatic_retry_count") != 0
        or terminal.get("error") is not None
        or _validator_os.path.lexists(destination_stage)
        or _validator_os.path.lexists(secondary_stage)
    ):
        raise RehearsalV22ValidationError("recovery success terminal semantics drifted")
    if sorted(path.name for path in primary_container.iterdir()) != sorted(
        (claim_root.name, receipt_name)
    ) or sorted(path.name for path in secondary_container.iterdir()) != sorted(
        (receipt_name, snapshot_name)
    ):
        raise RehearsalV22ValidationError("recovery containers contain conflicting state")
    return {
        "claim_started_sha256": _sha256(started_payload),
        "claim_terminal_sha256": _sha256(terminal_payload),
        "published_tree_sha256": published_tree_sha,
        "secondary_snapshot_tree_sha256": snapshot_tree_sha,
        "paired_receipt_sha256": _sha256(primary_receipt_payload),
        "paired_receipt_bytes": len(primary_receipt_payload),
    }


def _independent_publication_capability(
    value: implementation.RecoveredPublicationCapability,
    *,
    root: Path,
    binding: BindingView,
    governance: _ValidatedRecoveryGovernance,
    historical_anchor: HistoricalSelectedAnchor,
    live_anchor: LiveExecutionAnchor,
    bundle_payload: bytes,
    historical_evidence: Mapping[str, str],
) -> tuple[RecoveredPublicationCapability, JsonObject]:
    if (
        not isinstance(value, implementation.RecoveredPublicationCapability)
        or tuple(value.__dataclass_fields__) != RECOVERED_PUBLICATION_CAPABILITY_FIELDS
    ):
        raise RehearsalV22ValidationError("producer recovered-publication capability drifted")
    values = {field: getattr(value, field) for field in RECOVERED_PUBLICATION_CAPABILITY_FIELDS}
    capability = _require_recovered_publication_capability(
        _issue_recovered_publication_capability(**values)
    )
    for field in (
        "recovery_authorization_sha256",
        "owner_binding_sha256",
        "claim_started_sha256",
        "claim_terminal_sha256",
        "series_token_sha256",
        "sealed_history_root_sha256",
        "sealed_live_ledger_root_sha256",
        "published_bundle_sha256",
        "published_tree_sha256",
        "secondary_snapshot_tree_sha256",
        "paired_receipt_sha256",
        "execution_control_merkle_root_sha256",
        "selected_candidate_sha256",
        "selected_terminal_sha256",
        "selected_evidence_tree_root_sha256",
        "historical_run_a_root_sha256",
        "historical_run_b_root_sha256",
        "historical_run_a_probe_sha256",
        "historical_run_b_probe_sha256",
    ):
        _sha(getattr(capability, field), f"recovered capability {field}")
    for field in (
        "recovery_authorization_path",
        "owner_binding_path",
        "claim_root",
        "destination",
        "secondary_snapshot",
        "primary_receipt_path",
        "secondary_receipt_path",
    ):
        _string(getattr(capability, field), f"recovered capability {field}")
    expected_r_path = governance.r_path.relative_to(root).as_posix()
    expected_b_path = governance.b_path.relative_to(root).as_posix()
    recovery_storage = _object(
        _object(
            governance.r_document.get("destination"),
            "recovery capability destination",
        ).get("recovery_storage"),
        "recovery capability storage",
    )
    primary_container = Path(
        _string(
            recovery_storage.get("primary_recovery_container"),
            "recovery primary container",
        )
    ).absolute()
    expected_claim_root = primary_container / f"CLAIM-{_sha256(governance.r_payload)}"
    expected = {
        "recovery_authorization_path": expected_r_path,
        "recovery_authorization_sha256": _sha256(governance.r_payload),
        "recovery_authorization_creating_commit": governance.r_commit,
        "owner_binding_path": expected_b_path,
        "owner_binding_sha256": _sha256(governance.b_payload),
        "owner_binding_creating_commit": governance.b_commit,
        "claim_root": expected_claim_root.as_posix(),
        "series_token_sha256": binding.series_token_sha256,
        "selected_attempt_ordinal": historical_anchor.selected_attempt_ordinal,
        "selected_implementation_epoch": historical_anchor.implementation_epoch,
        "selected_implementation_commit": historical_anchor.implementation_commit,
        "sealed_history_root_sha256": historical_anchor.history_root_sha256,
        "sealed_live_ledger_root_sha256": historical_anchor.live_ledger_root_sha256,
        "destination": binding.absolute_destination.as_posix(),
        "published_bundle_sha256": _sha256(bundle_payload),
        "execution_epoch": live_anchor.implementation_epoch,
        "execution_implementation_commit": live_anchor.implementation_commit,
        "execution_control_merkle_root_sha256": live_anchor.control_merkle_root_sha256,
        "recovery_starts": 1,
        "pipeline_starts": 0,
        "automatic_retry_count": 0,
        "sealed_ledger_before_after_equal": True,
        "sealed_mirror_before_after_equal": True,
        "selected_candidate_sha256": historical_evidence["selected_candidate_sha256"],
        "selected_terminal_sha256": historical_evidence["selected_terminal_sha256"],
        "selected_evidence_tree_root_sha256": historical_evidence[
            "selected_evidence_tree_root_sha256"
        ],
        "historical_run_a_root_sha256": historical_evidence["historical_run_a_root_sha256"],
        "historical_run_b_root_sha256": historical_evidence["historical_run_b_root_sha256"],
        "historical_run_a_probe_sha256": historical_evidence["historical_run_a_probe_sha256"],
        "historical_run_b_probe_sha256": historical_evidence["historical_run_b_probe_sha256"],
        "historical_full_downstream_replay_verified": True,
    }
    for field, expected_value in expected.items():
        _require_equal(
            getattr(capability, field),
            expected_value,
            f"recovered capability {field}",
        )
    if (
        capability.paired_receipt_bytes < 1
        or capability.secondary_snapshot == capability.destination
        or capability.primary_receipt_path == capability.secondary_receipt_path
    ):
        raise RehearsalV22ValidationError("recovered capability durable-copy binding drifted")
    durable = _validate_durable_recovery_evidence(
        root=root,
        binding=binding,
        governance=governance,
        capability=capability,
        live_anchor=live_anchor,
        bundle_payload=bundle_payload,
    )
    return capability, durable


def validate_recovered_release_authorization(
    *,
    project_root: Path,
    receipt_path: Path,
    bundle_path: Path,
    execution_context: implementation.RecoveredPublicationCapability,
    validator_delegation: implementation.RecoveredPublicationValidatorDelegation,
) -> JsonObject:
    """Revalidate one recovered release without minting replay authority."""

    requested_root = project_root.absolute()
    delegated = implementation._validate_recovered_publication_validator_delegation(
        execution_context,
        validator_delegation,
        sys.modules[__name__],
        requested_root,
        bundle_path,
        receipt_path,
    )
    raw_binding, authorization, owner_binding, producer_historical, producer_live = delegated
    if (
        validator_delegation.validator_module_id != id(sys.modules[__name__])
        or validator_delegation.bundle_path != bundle_path.absolute()
        or validator_delegation.release_path != receipt_path.absolute()
    ):
        raise RehearsalV22ValidationError("recovered publication delegation identity drifted")
    root = requested_root.resolve(strict=True)
    refs_before = _git_ref_snapshot(root)
    historical_anchor = _independent_historical_anchor(producer_historical)
    live_anchor = _independent_live_anchor(producer_live)
    governance = _validate_recovery_governance(
        root,
        recovery_authorization_path=authorization.path,
        owner_binding_path=owner_binding.path,
        expected_series_token_sha256=raw_binding.series_token_sha256,
    )
    _validate_delegated_recovery_governance(governance, authorization, owner_binding)
    binding = _binding_view(raw_binding)
    if (
        binding.project_root != root
        or bundle_path.absolute() != binding.absolute_destination / BUNDLE_FILENAME
    ):
        raise RehearsalV22ValidationError("recovered-release binding or bundle path drifted")
    work_tracker = _IndependentRecoveryWorkTracker()
    (
        receipt,
        receipt_payload,
        bundle_ref,
        reviewed_head,
        _receipt_commit,
        release_specs,
    ) = _validate_passive_release_receipt(
        project_root=root,
        receipt_path=receipt_path,
        binding=binding,
        work_tracker=work_tracker,
    )
    live_control_pass_nonce = object()
    first_census_before = work_tracker.snapshot()
    first_components_before = (
        work_tracker.git_subprocesses_started,
        work_tracker.git_object_read_occurrences,
    )
    observed_live, census, additional_specs, live_control = _live_anchor_from_recovery_governance(
        root,
        governance,
        additional_census_specs=release_specs,
        control_pass_nonce=live_control_pass_nonce,
        work_tracker=work_tracker,
    )
    first_census_after = work_tracker.snapshot()
    first_census_delta = _recovery_work_delta(first_census_before, first_census_after)
    first_component_delta = (
        work_tracker.git_subprocesses_started - first_components_before[0],
        work_tracker.git_object_read_occurrences - first_components_before[1],
    )
    if observed_live != live_anchor:
        raise RehearsalV22ValidationError("recovered-release live anchor substitution detected")
    validation_context = RecoveredBundleValidationContext(
        mode=BundleValidationMode.PASSIVE_RECOVERED_RELEASE,
        historical_anchor=historical_anchor,
        live_anchor=live_anchor,
    )
    validated = _validate_common_bundle_once(
        project_root=root,
        bundle_path=bundle_path,
        authorized_bundle_directory=_directory(
            binding.absolute_destination,
            "recovered published bundle directory",
        ),
        binding=binding,
        expected_bundle_sha256=cast(str, bundle_ref["sha256"]),
        validation_context=validation_context,
    )
    historical_evidence = _validate_historical_full_downstream_replay_evidence(
        validated,
        historical_anchor,
        governance,
        binding,
    )
    capability, durable = _independent_publication_capability(
        execution_context,
        root=root,
        binding=binding,
        governance=governance,
        historical_anchor=historical_anchor,
        live_anchor=live_anchor,
        bundle_payload=validated.payload,
        historical_evidence=historical_evidence,
    )
    _cross_validate_release(bundle=validated.document, receipt=receipt)
    if (
        _git_blob(root, reviewed_head, cast(str, bundle_ref["path"])) != validated.payload
        or _object(
            receipt.get("independent_checks"),
            "recovered release independent checks",
        ).get("full_downstream_replay_passed")
        is not True
    ):
        raise RehearsalV22ValidationError(
            "recovered release reviewed bundle or historical replay truth drifted"
        )
    final_census_before = work_tracker.snapshot()
    final_components_before = (
        work_tracker.git_subprocesses_started,
        work_tracker.git_object_read_occurrences,
    )
    final_census = _validate_live_execution_anchor(
        root,
        live_anchor,
        additional_census_specs=additional_specs,
        control_pass_nonce=live_control_pass_nonce,
        ref_snapshot_sha256=cast(str, census.get("ref_snapshot_after_sha256")),
        cached_current_control=live_control,
        work_tracker=work_tracker,
    )
    if _canonical_json_bytes(final_census) != _canonical_json_bytes(census):
        raise RehearsalV22ValidationError(
            "recovered release final census differs from its pre-storage census"
        )
    final_census_delta = _recovery_work_delta(
        final_census_before,
        work_tracker.snapshot(),
    )
    final_component_delta = (
        work_tracker.git_subprocesses_started - final_components_before[0],
        work_tracker.git_object_read_occurrences - final_components_before[1],
    )
    if (
        final_census_delta != first_census_delta
        or final_component_delta != first_component_delta
    ):
        raise RehearsalV22ValidationError(
            "recovered-release census work changed between identical passes"
        )
    _independent_recovery_work_counters(
        validated=validated,
        census=final_census,
        work_tracker=work_tracker,
    )
    if refs_before != _git_ref_snapshot(root):
        raise RehearsalV22ValidationError("Git refs changed during recovered release validation")
    effect_summary = {
        "filesystem_writes": 0,
        "git_writes": 0,
        "ledger_writes": 0,
        "sealed_mirror_writes": 0,
        "destination_writes": 0,
        "temporary_writes": 0,
        "pipeline_starts": 0,
        "automatic_retries": 0,
        "heldout_evaluation_attempts_consumed": 0,
        "model_calls": 0,
        "network_calls": 0,
        "database_accesses": 0,
        "before_after_equal": True,
    }
    return {
        "schema_version": ("p4.2a-v2-2-series2-read-only-recovered-release-revalidation-result-v1"),
        "status": "PASS_READ_ONLY_RECOVERED_RELEASE_REVALIDATION",
        "mode": BundleValidationMode.PASSIVE_RECOVERED_RELEASE.value,
        "release_path": receipt_path.absolute().as_posix(),
        "release_sha256": _sha256(receipt_payload),
        "bundle_path": bundle_path.absolute().as_posix(),
        "bundle_sha256": _sha256(validated.payload),
        "recovery_authorization_sha256": _sha256(governance.r_payload),
        "owner_binding_sha256": _sha256(governance.b_payload),
        "claim_terminal_sha256": cast(str, durable["claim_terminal_sha256"]),
        "paired_receipt_sha256": capability.paired_receipt_sha256,
        "real_lineage_census_sha256": _sha256(_canonical_json_bytes(final_census)),
        "historical_selected_anchor": {
            "implementation_epoch": historical_anchor.implementation_epoch,
            "implementation_commit": historical_anchor.implementation_commit,
            "control_merkle_root_sha256": historical_anchor.control_merkle_root_sha256,
            "history_root_sha256": historical_anchor.history_root_sha256,
            "live_ledger_root_sha256": historical_anchor.live_ledger_root_sha256,
            "require_current": False,
        },
        "live_execution_anchor": {
            "implementation_epoch": live_anchor.implementation_epoch,
            "implementation_commit": live_anchor.implementation_commit,
            "control_merkle_root_sha256": live_anchor.control_merkle_root_sha256,
            "real_lineage_census_sha256": live_anchor.real_lineage_census_sha256,
            "require_current": True,
        },
        "effect_summary": effect_summary,
    }


def _validate_release_once(
    *,
    project_root: Path,
    receipt_path: Path,
    binding: BindingView,
    raw_binding: implementation.ExecutionBinding,
    execution_context: object | None,
) -> JsonObject:
    root = project_root.resolve(strict=True)
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
    schema_payload = _bound_control(
        root,
        SERIES_2_RELEASE_SCHEMA_RELATIVE,
        SERIES_2_RELEASE_SCHEMA_SHA256,
        "series-2 evidence acceptance schema",
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
    if receipt_commit == reviewed_head or not _git_is_ancestor(root, reviewed_head, receipt_commit):
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
        _unique_a_authority(root, authority, require_worktree=True)
        if not _git_is_ancestor(root, cast(str, authority["creating_commit"]), reviewed_head):
            raise RehearsalV22ValidationError(
                f"release lineage {key} is not contained by the reviewed head"
            )

    if binding.mode == "REGISTERED_OFFICIAL":
        # This registered series is already closed on selected ordinal 2 and the
        # adjudication records that its attempt-built bundle validation failed.
        # Consequently its only lawful publication provenance is epoch-8
        # recovery.  Container presence is deliberately not consulted here:
        # those external directories can be deleted, emptied, or partially
        # damaged, and none of those states may downgrade the fixed series back
        # to active replay.  Epoch 8 is the only current recovery execution
        # authority; the selected attempt remains historically anchored at 6.
        raise RehearsalV22ValidationError(
            "registered closed series requires PASSIVE_RECOVERED_RELEASE"
        )

    # Receipt shape, immutable Git identity, lineage, and every authority above
    # must fail closed before bundle replay is allowed to create even temporary
    # artifacts.  The bundle pass below is read-only until all receipt/bundle
    # cross equalities have also succeeded.
    bundle_path = binding.absolute_destination / BUNDLE_FILENAME
    validated = _validate_active_bundle_once(
        project_root=root,
        bundle_path=bundle_path,
        binding=binding,
        raw_binding=raw_binding,
        published_release_revalidation=True,
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
            or _diff_name_status(root, review_parents[0], review_commit) != (("A", review_path),)
            or _git_blob(root, review_parents[0], cast(str, bundle_ref["path"])) != bundle_payload
        ):
            raise RehearsalV22ValidationError(
                "disposable release did not commit the exact bundle before its "
                "unique-A review request"
            )
    _active_replay_validated_bundle(
        validated=validated,
        binding=binding,
        raw_binding=raw_binding,
        execution_context=execution_context,
        published_release_revalidation=True,
    )
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
    return _validate_release_once(
        project_root=resolved.view.project_root,
        receipt_path=receipt_path,
        binding=resolved.view,
        raw_binding=resolved.raw,
        execution_context=execution_context,
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
        and _sha256(_regular_bytes(Path(sys.orig_argv[0]), "fixed Python orig-argv executable"))
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
        "bundle_path": (REGISTERED_DESTINATION_RELATIVE / BUNDLE_FILENAME).as_posix(),
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
            _canonical_json_bytes(_validator_result(bundle, bundle_sha256=_sha256(payload)))
        )
    except (OSError, RehearsalV22ValidationError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
