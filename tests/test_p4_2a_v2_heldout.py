from __future__ import annotations

import copy
import inspect
import json
import os
import pickle
import shutil
import sqlite3
import stat
import subprocess
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn, TypeVar
from zoneinfo import ZoneInfo

import pytest
from scripts import build_p4_2a_gold_sample as gold_builder
from scripts import p4_2a_v2_dev_common as common
from scripts import prepare_p4_2a_v2_heldout as runner
from scripts import run_p4_2a_v2_dev_calibration as dev_runner
from scripts.run_p4_2a_offline_extract import (
    ChatJsonCallable,
    MonotonicNsClock,
    RecordedAtClock,
)

from alphapilot.core.config import Settings
from alphapilot.db import backup as database_backup

_TestCallable = TypeVar("_TestCallable", bound=Callable[..., object])
_parametrize: Callable[..., Callable[[_TestCallable], _TestCallable]] = (
    pytest.mark.parametrize
)


def _test_pdf_policy() -> gold_builder.AnnouncementBodyPolicy:
    return gold_builder.AnnouncementBodyPolicy(
        allowed_scheme="https",
        allowed_host="static.cninfo.com.cn",
        follow_redirects=False,
        tls_verify=True,
        connect_timeout_seconds=1.0,
        read_timeout_seconds=1.0,
        max_pdf_bytes=8,
        required_magic=b"%PDF-",
        extractor_command="pdftotext",
        extractor_timeout_seconds=1.0,
        max_annotation_text_characters=100,
        minimum_extracted_characters=80,
    )


def _unused_pdf_extractor(
    _payload: bytes,
    _policy: gold_builder.AnnouncementBodyPolicy,
) -> gold_builder.ExtractedPdfText:
    return gold_builder.ExtractedPdfText(
        text="offline",
        text_sha256=common.sha256_bytes(b"offline"),
        full_character_count=7,
    )


def _temporary_binding(tmp_path: Path) -> runner.HeldoutBinding:
    tmp_path = tmp_path.resolve()
    source = runner.load_binding()
    artifacts = {
        name: tmp_path / path.relative_to(source.root) for name, path in source.artifacts.items()
    }
    binding = replace(source, root=tmp_path, artifacts=artifacts)
    for relative in (
        runner.SUCCESSOR_V2_1_PREREGISTRATION_PATH,
        runner.SUCCESSOR_V2_1_BUNDLE_SCHEMA_PATH,
        runner.SUCCESSOR_V2_1_RELEASE_SCHEMA_PATH,
        runner.FRAME_AUTHORITY_PATH,
        runner.SUCCESSOR_CODE_GATE_AUTHORITY_PATH,
    ):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(runner.PROJECT_ROOT / relative, destination)
    for relative in runner._registered_successor_implementation_paths(
        runner.PROJECT_ROOT
    ):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(runner.PROJECT_ROOT / relative, destination)
    database = tmp_path / "data/alphapilot.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    database.touch()
    return binding


def _offline_context(
    binding: runner.HeldoutBinding,
    *,
    database: Path | None = None,
    pdf_fetcher: gold_builder.PdfFetcher | None = None,
    pdf_text_extractor: gold_builder.PdfTextExtractor | None = None,
    monotonic: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    inference_settings: Settings | None = None,
    chat_json_fn: ChatJsonCallable | None = None,
    snapshot_loader: runner.ProductionSnapshotLoader | None = None,
    wall_clock: runner.Clock | None = None,
    execution_id_factory: runner.ExecutionIdFactory | None = None,
    prediction_recorded_at_clock: RecordedAtClock | None = None,
    prediction_monotonic_ns_clock: MonotonicNsClock | None = None,
) -> runner._OfflineRehearsalCapability:
    def default_fetcher(
        _url: str,
        _policy: gold_builder.AnnouncementBodyPolicy,
    ) -> bytes:
        return b"%PDF-offline"

    def default_extractor(
        _payload: bytes,
        _policy: gold_builder.AnnouncementBodyPolicy,
    ) -> NoReturn:
        raise AssertionError("offline extractor is test-bound only")

    def default_monotonic() -> float:
        return 0.0

    def default_sleep(_seconds: float) -> None:
        return None

    def default_chat_json(
        _purpose: str,
        _system: str,
        _user: str,
        _schema: dict[str, Any],
        **_kwargs: Any,
    ) -> NoReturn:
        raise AssertionError("offline model is test-bound only")

    def default_snapshot_loader(_root: Path) -> dev_runner.ProductionSnapshot:
        return _safe_snapshot()

    def default_wall_clock() -> datetime:
        return datetime(2026, 8, 10, 12, 30, tzinfo=UTC)

    def default_execution_id_factory() -> str:
        return "00000000-0000-4000-8000-000000000001"

    def default_prediction_recorded_at_clock() -> str:
        return "2026-08-10T12:30:00Z"

    def default_prediction_monotonic_ns_clock() -> int:
        return 0

    bound_fetcher = pdf_fetcher or default_fetcher
    bound_extractor = pdf_text_extractor or default_extractor
    bound_monotonic = monotonic or default_monotonic
    bound_sleep = sleep or default_sleep

    implementation_commit = runner._git(
        runner.PROJECT_ROOT,
        "rev-parse",
        "HEAD",
    ).strip()
    return runner._mint_v2_1_offline_rehearsal_capability(
        binding,
        database=database or binding.root / "data/alphapilot.db",
        pdf_fetcher=bound_fetcher,
        pdf_text_extractor=bound_extractor,
        monotonic=bound_monotonic,
        sleep=bound_sleep,
        inference_settings=inference_settings or _safe_settings(),
        chat_json_fn=chat_json_fn or default_chat_json,
        snapshot_loader=snapshot_loader or default_snapshot_loader,
        wall_clock=wall_clock or default_wall_clock,
        execution_id_factory=execution_id_factory or default_execution_id_factory,
        prediction_recorded_at_clock=(
            prediction_recorded_at_clock or default_prediction_recorded_at_clock
        ),
        prediction_monotonic_ns_clock=(
            prediction_monotonic_ns_clock or default_prediction_monotonic_ns_clock
        ),
        implementation_commit=implementation_commit,
    )


def _install_v1_incident(binding: runner.HeldoutBinding) -> None:
    source = runner.PROJECT_ROOT / runner.REHEARSAL_V1_INCIDENT_PATH
    destination = binding.root / runner.REHEARSAL_V1_INCIDENT_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())


def test_strict_loader_binds_actual_preregistration_and_round3_contract() -> None:
    binding = runner.load_binding()

    assert binding.contract.model == "qwen3.6-plus"
    assert binding.contract.sha256 == runner.HELDOUT_CONTRACT_SHA256
    assert binding.contract.max_retries == 0
    assert len(binding.retired_ids) == 40
    assert binding.artifacts["owner_blind"].name.endswith(".blind.jsonl")
    lineage = runner._verified_source_lineage(binding)
    assert lineage["required_closed_dates_shanghai"] == [
        "2026-08-06",
        "2026-08-07",
        "2026-08-08",
    ]
    assert set(lineage["evidence"]) == {
        "round3_evidence",
        "round3_independent_review",
        "incremental_evidence",
        "incremental_independent_review",
    }


@_parametrize(
    ("suffix", "payload", "message"),
    [
        (".json", '{"outer":{"value":1,"value":2}}\n', "duplicate key"),
        (".json", '{"value":NaN}\n', "non-finite"),
        (".json", '{"value":1e9999}\n', "non-finite"),
        (".jsonl", '{"outer":{"value":1,"value":2}}\n', "duplicate key"),
        (".jsonl", '{"value":Infinity}\n', "non-finite"),
        (".jsonl", '{"value":-Infinity}\n', "non-finite"),
    ],
)
def test_json_loaders_reject_duplicate_keys_and_nonfinite_numbers(
    tmp_path: Path,
    suffix: str,
    payload: str,
    message: str,
) -> None:
    path = tmp_path / f"strict{suffix}"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(runner.HeldoutPreparationError, match=message):
        if suffix == ".json":
            runner._load_json(path, "strict fixture")
        else:
            runner._load_jsonl(path, "strict fixture")


def test_synthetic_rehearsal_is_create_only_and_never_reads_production(
    tmp_path: Path,
) -> None:
    binding = _temporary_binding(tmp_path)

    receipt_path = runner.run_synthetic_rehearsal(binding)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    assert receipt["status"] == "preparation_helper_passed_not_full_path"
    assert receipt["full_path_covered"] is False
    assert receipt["materialization_gate_unlock"] is False
    assert receipt["real_database_read"] is False
    assert receipt["real_model_calls"] == 0
    assert sorted(path.name for path in receipt_path.parent.iterdir()) == [
        "contract.json",
        "expected.json",
        "inputs.jsonl",
        "preparation-helper-receipt.json",
    ]
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        runner.run_synthetic_rehearsal(binding)

    _install_v1_incident(binding)
    with pytest.raises(runner.HeldoutPreparationError, match="synthetic successor receipt"):
        runner.run_materialize(
            binding,
            operator_timing_attestation=None,
            database=tmp_path / "must-not-open.db",
        )


def test_select_blind_uses_40_20_and_hides_sampling_metadata(tmp_path: Path) -> None:
    binding = _temporary_binding(tmp_path)
    candidates = [runner._synthetic_input(identifier) for identifier in range(1, 81)]
    predictions = [
        runner._synthetic_prediction(row, materiality=2 if index <= 50 else 1)
        for index, row in enumerate(candidates, start=1)
    ]

    result = runner.select_and_blind(binding, candidates, predictions)

    counts = result.manifest["selection"]["selected_counts"]
    assert counts == {
        "predicted_positive": 40,
        "predicted_negative": 20,
        "extract_failed": 0,
        "total": 60,
    }
    assert result.manifest["schema_version"] == ("p4.2a-v2-heldout-selection-manifest-v1")
    assert len(result.blind_rows) == 60
    assert all(row["gold"] == {} for row in result.blind_rows)
    assert all(not common.forbidden_blind_paths(row) for row in result.blind_rows)

    with pytest.raises(runner.HeldoutPreparationError, match="insufficient"):
        runner.select_and_blind(binding, candidates[:50], predictions[:50])

    failed = [dict(row) for row in predictions]
    failed[0] = {**failed[0], "status": "extract_failed", "prediction": None}
    with pytest.raises(runner.HeldoutPreparationError, match="sampling is forbidden"):
        runner.select_and_blind(binding, candidates, failed)


def test_select_blind_requires_unique_complete_prediction_ids(tmp_path: Path) -> None:
    binding = _temporary_binding(tmp_path)
    candidates = [runner._synthetic_input(identifier) for identifier in range(1, 81)]
    predictions = [
        runner._synthetic_prediction(row, materiality=2 if index <= 50 else 1)
        for index, row in enumerate(candidates, start=1)
    ]
    duplicate_with_same_count = [*predictions[:-1], predictions[0]]

    with pytest.raises(runner.HeldoutPreparationError, match="prediction ids are not unique"):
        runner.select_and_blind(binding, candidates, duplicate_with_same_count)
    with pytest.raises(
        runner.HeldoutPreparationError,
        match="do not exactly match eligible candidate ids",
    ):
        runner.select_and_blind(binding, candidates, predictions[:-1])


def test_materialization_rejects_minimal_fake_pass_receipt_before_database_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _temporary_binding(tmp_path)
    _install_v1_incident(binding)
    directory = binding.artifacts["synthetic_rehearsal"]
    directory.mkdir(parents=True)
    (directory / "contract.json").write_text("{}\n", encoding="utf-8")
    (directory / "inputs.jsonl").write_text("{}\n", encoding="utf-8")
    (directory / "expected.json").write_text("{}\n", encoding="utf-8")
    (directory / "pass-receipt.json").write_text(
        json.dumps(
            {
                "schema_version": runner.FULL_REHEARSAL_RECEIPT_SCHEMA,
                "status": "passed",
                "full_path_covered": True,
                "materialization_gate_unlock": True,
                "preregistration_sha256": runner.PREREGISTRATION_SHA256,
            }
        ),
        encoding="utf-8",
    )
    database_read = False

    def forbidden_window(*_args: object, **_kwargs: object) -> list[object]:
        nonlocal database_read
        database_read = True
        raise AssertionError("database must not be read for a fake receipt")

    monkeypatch.setattr(runner, "_window_rows", forbidden_window)
    with pytest.raises(runner.HeldoutPreparationError, match="synthetic successor receipt"):
        runner.run_materialize(
            binding,
            operator_timing_attestation=None,
            database=tmp_path / "must-not-open.db",
        )
    assert database_read is False


def test_unlocked_runtime_is_rejected_before_v1_or_database_read_and_v1_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = runner.load_binding()
    directory = binding.artifacts["synthetic_rehearsal"]
    expected_hashes = {
        "contract.json": "61ff631b0dc025bf7441fd3bd04636d0d6d34e5085b0ca570e6f8e2fcfd298ac",
        "inputs.jsonl": "a6c5e6e0da6341cecf01df038b36964af6d8bb433b72babf953b385addc87707",
        "expected.json": "557433d6ebdd6e3585aad18c6204e28c1cae6417ab3b1b3591474c5f0189b9ac",
        "pass-receipt.json": "2610a8b3885426e44a7f32c1d964defcaa46a118bb531a2ecc9b0f3aefa1e0f5",
    }
    assert {
        name: common.sha256_file(directory / name) for name in expected_hashes
    } == expected_hashes
    database_reads = 0
    network_calls = 0

    def forbidden_window(*_args: object, **_kwargs: object) -> list[object]:
        nonlocal database_reads
        database_reads += 1
        raise AssertionError("database must not be read for the retired v1 receipt")

    def forbidden_pdf_fetch(*_args: object, **_kwargs: object) -> bytes:
        nonlocal network_calls
        network_calls += 1
        raise AssertionError("network must not be used for the retired v1 receipt")

    def forbidden_pdf_extract(*_args: object, **_kwargs: object) -> NoReturn:
        nonlocal network_calls
        network_calls += 1
        raise AssertionError("PDF extraction must not run for the retired v1 receipt")

    monkeypatch.setattr(runner, "_window_rows", forbidden_window)
    with pytest.raises(
        runner.HeldoutPreparationError,
        match="exact first-exec runtime: environment drifted",
    ):
        runner.run_materialize(
            binding,
            operator_timing_attestation=None,
            database=Path("does-not-exist.db"),
            pdf_fetcher=forbidden_pdf_fetch,
            pdf_text_extractor=forbidden_pdf_extract,
        )

    assert database_reads == 0
    assert network_calls == 0
    assert {
        name: common.sha256_file(directory / name) for name in expected_hashes
    } == expected_hashes


def _create_news_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE news_items (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                symbol TEXT,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                published_at TEXT,
                available_time TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                raw_payload TEXT NOT NULL
            )
            """
        )
        rows = [
            (1, "cninfo", "2026-08-05 15:59:59"),
            (2, "cninfo", "2026-08-05 16:00:00"),
            (3, "akshare_ths", "2026-08-08 15:59:59"),
            (4, "sina_company_news", "2026-08-08 16:00:00"),
        ]
        connection.executemany(
            """
            INSERT INTO news_items
              (id, source, symbol, title, url, published_at, available_time,
               content_hash, raw_payload)
            VALUES (?, ?, '600519', ?, ?, NULL, ?, ?, '{}')
            """,
            [
                (
                    identifier,
                    source,
                    f"title-{identifier}",
                    f"https://example.invalid/{identifier}",
                    available,
                    f"{identifier:064x}",
                )
                for identifier, source, available in rows
            ],
        )
        connection.commit()
    finally:
        connection.close()


def test_window_query_uses_sqlite_utc_storage_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "fixture.db"
    _create_news_database(database)
    binding = _temporary_binding(tmp_path)
    monkeypatch.setattr(runner, "EXPECTED_RAW_COUNT", 2)
    monkeypatch.setattr(
        runner,
        "EXPECTED_BY_SOURCE",
        {"akshare_ths": 1, "cninfo": 1},
    )

    rows = runner._window_rows(binding, database)

    assert [row.news_item_id for row in rows] == [2, 3]


def test_materialization_design_reuses_the_loaded_v17_base_contract() -> None:
    binding = runner.load_binding()
    materialization_design = runner._materialization_design(binding)
    v17_design = gold_builder.load_evaluation_design(
        binding.root / "config/p4_event_evaluation_v1_7.yaml"
    )

    assert materialization_design.base_contract == v17_design.base_contract
    assert materialization_design.document["candidate_eligibility"] == (
        v17_design.document["candidate_eligibility"]
    )


def _write_inference_fixture(
    binding: runner.HeldoutBinding,
    count: int,
) -> list[dict[str, Any]]:
    candidates = [runner._synthetic_input(identifier) for identifier in range(1, count + 1)]
    path = binding.artifacts["materialized_inputs"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(common.canonical_jsonl_bytes(candidates))
    binding.artifacts["materialization_manifest"].write_bytes(
        common.canonical_json_bytes(
            {
                "artifacts": {
                    "eligible_inputs_jsonl": {
                        "sha256": common.sha256_file(path),
                    }
                }
            }
        )
    )
    return candidates


def _safe_settings(*, trading_mode: str = "research") -> Settings:
    return Settings(
        trading_mode=trading_mode,
        live_trading_enabled=False,
        paper_trading_enabled=False,
        paper_auto_trading_enabled=False,
        futu_enable_account_mutation=False,
        futu_enable_trade=False,
        llm_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        llm_api_key="test-only-key",
        llm_model="qwen3.6-plus",
    )


def _safe_snapshot() -> dev_runner.ProductionSnapshot:
    return dev_runner.ProductionSnapshot(
        sqlite_uri_mode="ro",
        pragma_query_only=1,
        connection_total_changes=0,
        llm_call_count=1,
        llm_call_max_id=1,
        trade_proposal_count=1,
        broker_order_count=1,
        non_simulate_order_count=0,
        news_events_table_exists=False,
        universe_symbols=frozenset({"600519"}),
    )


def test_state_append_retries_partial_os_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state.jsonl"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    original_write = os.write
    write_calls = 0

    def partial_write(target: int, payload: bytes | bytearray | memoryview) -> int:
        nonlocal write_calls
        write_calls += 1
        chunk = payload[: max(1, len(payload) // 2)]
        return original_write(target, chunk)

    monkeypatch.setattr(os, "write", partial_write)
    try:
        runner._append_state_descriptor(descriptor, {"status": "complete", "count": 80})
    finally:
        os.close(descriptor)

    assert write_calls > 1
    assert runner._load_jsonl(path, "state") == [{"count": 80, "status": "complete"}]


def _write_completed_selection_fixture(
    binding: runner.HeldoutBinding,
    *,
    execution_context: runner.ExecutionContext,
    failed_status: bool = False,
    terminal_predictions_sha256: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    return runner._write_synthetic_production_execution_fixture(
        binding,
        execution_context=execution_context,
        failed_status=failed_status,
        terminal_predictions_sha256=terminal_predictions_sha256,
    )


def _dummy_real_authorization(root: Path) -> runner.V21ReleaseAuthorization:
    return runner.V21ReleaseAuthorization(
        project_root=root,
        receipt_path=root / runner.SUCCESSOR_V2_1_RELEASE_PATH,
        receipt_sha256="1" * 64,
        receipt_creating_commit="1" * 40,
        preregistration_commit=runner.SUCCESSOR_V2_1_PREREGISTRATION_COMMIT,
        implementation_commit="2" * 40,
        rehearsal_evidence_commit="3" * 40,
        bundle_path=root / runner.SUCCESSOR_V2_1_BUNDLE_PATH,
        bundle_sha256="4" * 64,
        bundle_root_sha256="5" * 64,
    )


def test_real_inference_boundary_rejects_custom_model_and_snapshot_seams(
    tmp_path: Path,
) -> None:
    authorization = _dummy_real_authorization(tmp_path)

    def fake_chat_json(
        purpose: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        timeout: float | None = None,
        max_tokens: int | None = None,
        max_retries: int = 1,
        settings: Settings | None = None,
        session: Any = None,
    ) -> dict[str, Any]:
        del (
            purpose,
            system,
            user,
            schema,
            timeout,
            max_tokens,
            max_retries,
            settings,
            session,
        )
        return {}

    def fake_snapshot(_root: Path) -> dev_runner.ProductionSnapshot:
        return _safe_snapshot()

    with pytest.raises(runner.HeldoutPreparationError, match="forbids injected"):
        runner._validate_v2_1_inference_seams(
            authorization,
            settings=None,
            chat_json_fn=fake_chat_json,
            snapshot_loader=dev_runner._production_snapshot,
            clock=runner._system_clock,
            execution_id_factory=runner._random_execution_id,
            prediction_recorded_at_clock=None,
            prediction_monotonic_ns_clock=None,
        )
    with pytest.raises(runner.HeldoutPreparationError, match="forbids injected"):
        runner._validate_v2_1_inference_seams(
            authorization,
            settings=None,
            chat_json_fn=None,
            snapshot_loader=fake_snapshot,
            clock=runner._system_clock,
            execution_id_factory=runner._random_execution_id,
            prediction_recorded_at_clock=None,
            prediction_monotonic_ns_clock=None,
        )
    with pytest.raises(runner.HeldoutPreparationError, match="forbids injected"):
        runner._validate_v2_1_inference_seams(
            authorization,
            settings=None,
            chat_json_fn=None,
            snapshot_loader=dev_runner._production_snapshot,
            clock=runner._system_clock,
            execution_id_factory=runner._random_execution_id,
            prediction_recorded_at_clock=lambda: "2026-08-10T12:30:00Z",
            prediction_monotonic_ns_clock=None,
        )


def test_offline_inference_boundary_rejects_unminted_seam_identity(
    tmp_path: Path,
) -> None:
    binding = _temporary_binding(tmp_path)
    capability = _offline_context(binding)

    def wrong_snapshot(_root: Path) -> dev_runner.ProductionSnapshot:
        return _safe_snapshot()

    with pytest.raises(runner.HeldoutPreparationError, match="minted capability"):
        runner._validate_v2_1_inference_seams(
            capability,
            settings=capability.inference_settings,
            chat_json_fn=capability.chat_json_fn,
            snapshot_loader=wrong_snapshot,
            clock=capability.wall_clock,
            execution_id_factory=capability.execution_id_factory,
            prediction_recorded_at_clock=capability.prediction_recorded_at_clock,
            prediction_monotonic_ns_clock=capability.prediction_monotonic_ns_clock,
        )
    with pytest.raises(runner.HeldoutPreparationError, match="minted capability"):
        runner._validate_v2_1_inference_seams(
            capability,
            settings=capability.inference_settings,
            chat_json_fn=capability.chat_json_fn,
            snapshot_loader=capability.snapshot_loader,
            clock=capability.wall_clock,
            execution_id_factory=capability.execution_id_factory,
            prediction_recorded_at_clock=lambda: "2026-08-10T12:30:00Z",
            prediction_monotonic_ns_clock=capability.prediction_monotonic_ns_clock,
        )


def test_run_infer_rejects_real_timing_injection_before_candidate_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Isolate seam ordering only; this is not release-gate acceptance evidence."""

    binding = _temporary_binding(tmp_path)
    authorization = _dummy_real_authorization(tmp_path)
    input_reads = 0
    observed_contexts: list[runner.ExecutionContext] = []

    def isolated_stage_authorization(
        _binding: runner.HeldoutBinding,
        *,
        stage: str,
        execution_context: runner.ExecutionContext = None,
    ) -> runner.V21ReleaseAuthorization:
        assert stage == "infer"
        assert execution_context is None or execution_context == authorization
        observed_contexts.append(execution_context)
        return authorization

    def forbidden_input_reader(_path: Path, _label: str) -> NoReturn:
        nonlocal input_reads
        input_reads += 1
        raise AssertionError("timing injection must reject before candidate input read")

    monkeypatch.setattr(
        runner,
        "validate_v2_1_stage_authorization",
        isolated_stage_authorization,
    )
    monkeypatch.setattr(runner, "_load_jsonl", forbidden_input_reader)
    with pytest.raises(runner.HeldoutPreparationError, match="forbids injected"):
        runner.run_infer(
            binding,
            prediction_recorded_at_clock=lambda: "2026-08-10T12:30:00Z",
        )
    assert input_reads == 0
    assert observed_contexts == [None, authorization]
    assert not binding.artifacts["inference_state"].exists()
    assert not binding.artifacts["predictions"].exists()


def test_run_infer_rejects_offline_timing_identity_before_candidate_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _temporary_binding(tmp_path)
    capability = _offline_context(binding)
    input_reads = 0

    def forbidden_input_reader(_path: Path, _label: str) -> NoReturn:
        nonlocal input_reads
        input_reads += 1
        raise AssertionError("timing identity must reject before candidate input read")

    monkeypatch.setattr(runner, "_load_jsonl", forbidden_input_reader)
    with pytest.raises(runner.HeldoutPreparationError, match="minted capability"):
        runner.run_infer(
            binding,
            execution_context=capability,
            settings=capability.inference_settings,
            chat_json_fn=capability.chat_json_fn,
            snapshot_loader=capability.snapshot_loader,
            clock=capability.wall_clock,
            execution_id_factory=capability.execution_id_factory,
            prediction_recorded_at_clock=lambda: "2026-08-10T12:30:00Z",
            prediction_monotonic_ns_clock=capability.prediction_monotonic_ns_clock,
        )
    assert input_reads == 0
    assert not binding.artifacts["inference_state"].exists()
    assert not binding.artifacts["predictions"].exists()


def test_infer_rejects_unsafe_settings_before_state_or_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _temporary_binding(tmp_path)
    _write_inference_fixture(binding, 1)
    snapshot_calls = 0

    def forbidden_snapshot(_root: Path) -> dev_runner.ProductionSnapshot:
        nonlocal snapshot_calls
        snapshot_calls += 1
        raise AssertionError("unsafe settings must fail before a production snapshot")

    monkeypatch.setattr(
        runner,
        "validate_v2_1_materialization_manifest",
        lambda *_args, **_kwargs: None,
    )

    unsafe_settings = _safe_settings(trading_mode="live")
    context = _offline_context(
        binding,
        inference_settings=unsafe_settings,
        snapshot_loader=forbidden_snapshot,
    )
    with pytest.raises(runner.HeldoutPreparationError, match="pre-state safety gate"):
        runner.run_infer(
            binding,
            execution_context=context,
            settings=unsafe_settings,
            chat_json_fn=context.chat_json_fn,
            snapshot_loader=forbidden_snapshot,
            clock=context.wall_clock,
            execution_id_factory=context.execution_id_factory,
            prediction_recorded_at_clock=context.prediction_recorded_at_clock,
            prediction_monotonic_ns_clock=context.prediction_monotonic_ns_clock,
        )
    assert snapshot_calls == 0
    assert not binding.artifacts["inference_state"].exists()
    assert not binding.artifacts["predictions"].exists()


def test_infer_calls_each_candidate_once_and_records_terminal_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = _temporary_binding(tmp_path)
    candidates = _write_inference_fixture(binding, 3)
    calls: list[int] = []

    def fake_extract(
        _contract: object,
        records: list[object],
        *,
        output_path: Path,
        retry_failures: bool,
        **_kwargs: object,
    ) -> SimpleNamespace:
        assert len(records) == 1
        assert retry_failures is False
        record = records[0]
        identifier = int(record.news_item_id)  # type: ignore[attr-defined]
        calls.append(identifier)
        candidate = candidates[identifier - 1]
        prediction = runner._synthetic_prediction(candidate, materiality=2)
        with output_path.open("ab") as stream:
            stream.write(common.canonical_json_bytes(prediction))
        return SimpleNamespace(
            newly_attempted_count=1,
            success_count=1,
            failure_count=0,
            retried_failure_count=0,
            output_line_count=len(calls),
        )

    monkeypatch.setattr(runner, "extract_records", fake_extract)
    monkeypatch.setattr(
        runner,
        "validate_v2_1_materialization_manifest",
        lambda *_args, **_kwargs: None,
    )
    settings = _safe_settings()
    snapshot = _safe_snapshot()
    snapshot_calls: list[Path] = []

    def snapshot_loader(root: Path) -> dev_runner.ProductionSnapshot:
        if not snapshot_calls:
            assert not binding.artifacts["inference_state"].exists()
        snapshot_calls.append(root)
        return snapshot

    context = _offline_context(
        binding,
        inference_settings=settings,
        snapshot_loader=snapshot_loader,
    )
    runner.run_infer(
        binding,
        execution_context=context,
        settings=settings,
        chat_json_fn=context.chat_json_fn,
        snapshot_loader=snapshot_loader,
        clock=context.wall_clock,
        execution_id_factory=context.execution_id_factory,
        prediction_recorded_at_clock=context.prediction_recorded_at_clock,
        prediction_monotonic_ns_clock=context.prediction_monotonic_ns_clock,
    )

    assert calls == [1, 2, 3]
    assert snapshot_calls == [tmp_path, tmp_path]
    states = runner._load_jsonl(binding.artifacts["inference_state"], "state")
    assert [state["status"] for state in states] == [
        "inference_started",
        "completed_all_eligible_candidates_once",
    ]
    manifest = json.loads(binding.artifacts["prediction_manifest"].read_text(encoding="utf-8"))
    assert manifest["one_news_item_per_request"] is True
    assert manifest["automatic_retries"] == 0
    assert manifest["production_snapshot_unchanged"] is True
    assert manifest["execution_id"] == states[0]["execution_id"] == states[1]["execution_id"]


def test_candidate_failure_terminalizes_without_manifest_or_sampling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _temporary_binding(tmp_path)
    candidates = _write_inference_fixture(binding, 2)
    calls: list[int] = []

    def failing_extract(
        _contract: object,
        records: list[object],
        *,
        output_path: Path,
        retry_failures: bool,
        **_kwargs: object,
    ) -> SimpleNamespace:
        assert retry_failures is False
        identifier = int(records[0].news_item_id)  # type: ignore[attr-defined]
        calls.append(identifier)
        failed = {
            **runner._synthetic_prediction(candidates[identifier - 1], materiality=2),
            "status": "extract_failed",
            "prediction": None,
        }
        with output_path.open("ab") as stream:
            stream.write(common.canonical_json_bytes(failed))
        return SimpleNamespace(
            newly_attempted_count=1,
            success_count=0,
            failure_count=1,
            retried_failure_count=0,
            output_line_count=1,
        )

    monkeypatch.setattr(runner, "extract_records", failing_extract)
    monkeypatch.setattr(
        runner,
        "validate_v2_1_materialization_manifest",
        lambda *_args, **_kwargs: None,
    )
    settings = _safe_settings()

    def snapshot_loader(_root: Path) -> dev_runner.ProductionSnapshot:
        return _safe_snapshot()

    context = _offline_context(
        binding,
        inference_settings=settings,
        snapshot_loader=snapshot_loader,
    )
    with pytest.raises(runner.HeldoutPreparationError, match="candidate 1 failed"):
        runner.run_infer(
            binding,
            execution_context=context,
            settings=settings,
            chat_json_fn=context.chat_json_fn,
            snapshot_loader=snapshot_loader,
            clock=context.wall_clock,
            execution_id_factory=context.execution_id_factory,
            prediction_recorded_at_clock=context.prediction_recorded_at_clock,
            prediction_monotonic_ns_clock=context.prediction_monotonic_ns_clock,
        )
    assert calls == [1]
    states = runner._load_jsonl(binding.artifacts["inference_state"], "state")
    assert [row["status"] for row in states] == [
        "inference_started",
        "terminal_failed_no_sampling_no_retry",
    ]
    assert states[-1]["failed_candidate_index"] == 1
    assert states[-1]["failed_news_item_id"] == 1
    assert states[-1]["automatic_retries"] == 0
    assert states[-1]["failed_candidate_retries"] == 0
    assert states[-1]["sampling_allowed"] is False
    assert not binding.artifacts["prediction_manifest"].exists()
    with pytest.raises(runner.HeldoutPreparationError, match="completed terminal state"):
        runner.run_select_blind(binding, execution_context=context)


def test_run_select_blind_closes_full_hash_and_execution_lineage(tmp_path: Path) -> None:
    binding = _temporary_binding(tmp_path)
    context = _offline_context(binding)
    _candidates, predictions, execution_id = _write_completed_selection_fixture(
        binding,
        execution_context=context,
    )

    selection_path, blind_path = runner.run_select_blind(
        binding,
        execution_context=context,
    )

    selection = runner._load_json(selection_path, "selection")
    lineage = selection["source_lineage"]
    assert lineage["binding_scope"] == "registered_full_execution"
    assert lineage["execution"] == {
        "execution_id": execution_id,
        "eligible_candidate_count": len(predictions),
        "prediction_count": len(predictions),
        "status_ok_count": len(predictions),
        "status_failed_count": 0,
        "automatic_retries": 0,
        "failed_candidate_retries": 0,
        "terminal_status": "completed_all_eligible_candidates_once",
    }
    assert lineage["materialization_manifest"]["sha256"] == common.sha256_file(
        binding.artifacts["materialization_manifest"]
    )
    assert lineage["inference_state"]["sha256"] == common.sha256_file(
        binding.artifacts["inference_state"]
    )
    assert lineage["prediction_manifest"]["sha256"] == common.sha256_file(
        binding.artifacts["prediction_manifest"]
    )
    assert lineage["predictions"]["sha256"] == common.sha256_file(
        binding.artifacts["predictions"]
    )
    selected_prediction_bindings = lineage["selected_predictions"]["bindings"]
    assert len(selected_prediction_bindings) == 60
    predictions_by_id = {row["news_item_id"]: row for row in predictions}
    assert all(
        item["prediction_row_sha256"]
        == common.sha256_bytes(
            common.canonical_json_bytes(predictions_by_id[item["news_item_id"]])
        )
        for item in selected_prediction_bindings
    )
    assert len(runner._load_jsonl(blind_path, "blind")) == 60


@_parametrize(
    ("failed_status", "terminal_sha", "message"),
    [
        (True, None, "not every eligible prediction has status ok"),
        (False, "f" * 64, "completed inference hash/count lineage drifted"),
    ],
)
def test_run_select_blind_rejects_status_or_terminal_hash_drift(
    tmp_path: Path,
    failed_status: bool,
    terminal_sha: str | None,
    message: str,
) -> None:
    binding = _temporary_binding(tmp_path)
    context = _offline_context(binding)
    _write_completed_selection_fixture(
        binding,
        execution_context=context,
        failed_status=failed_status,
        terminal_predictions_sha256=terminal_sha,
    )

    with pytest.raises(runner.HeldoutPreparationError, match=message):
        runner.run_select_blind(binding, execution_context=context)
    assert not binding.artifacts["private_selection"].exists()
    assert not binding.artifacts["owner_blind"].exists()


def test_production_materialization_deep_layers_and_pdf_source_fail_closed(
    tmp_path: Path,
) -> None:
    binding = _temporary_binding(tmp_path)
    context = _offline_context(binding)
    candidates, manifest = runner._synthetic_production_materialization_fixture(
        binding,
        execution_context=context,
    )
    inputs_sha = common.sha256_bytes(common.canonical_jsonl_bytes(candidates))

    missing_layer = copy.deepcopy(manifest)
    del missing_layer["layers"]["all_candidates"]
    with pytest.raises(runner.HeldoutPreparationError, match="layer"):
        runner.validate_v2_1_materialization_manifest(
            binding,
            missing_layer,
            candidates,
            inputs_sha256=inputs_sha,
            validated_stage="select-blind",
            execution_context=context,
        )

    non_cninfo_pdf = copy.deepcopy(manifest)
    ineligible = non_cninfo_pdf["layers"]["ineligible_candidates"][0]
    ineligible["news_item_id"] = candidates[0]["news_item_id"]
    ineligible["url"] = candidates[0]["url"]
    with pytest.raises(runner.HeldoutPreparationError, match="ineligible evidence"):
        runner.validate_v2_1_materialization_manifest(
            binding,
            non_cninfo_pdf,
            candidates,
            inputs_sha256=inputs_sha,
            validated_stage="select-blind",
            execution_context=context,
        )


def test_candidate_body_and_frozen_contract_input_identities_fail_closed(
    tmp_path: Path,
) -> None:
    binding = _temporary_binding(tmp_path)
    candidates, _manifest = runner._synthetic_production_materialization_fixture(
        binding,
        execution_context=_offline_context(binding),
    )
    cninfo_index = next(
        index for index, row in enumerate(candidates) if row["source"] == "cninfo"
    )

    title_only_cninfo = copy.deepcopy(candidates)
    cninfo = title_only_cninfo[cninfo_index]
    cninfo["body_state"] = "title_only"
    cninfo["body_evidence"] = {
        "required": False,
        "source": None,
        "url": None,
        "pdf_sha256": None,
        "full_text_sha256": None,
        "full_text_character_count": None,
        "annotation_text_character_count": None,
        "body_characters_in_original_text": None,
        "text_truncated": False,
        "pdf_persisted": False,
    }
    active, declared = runner._recomputed_candidate_input_hashes(cninfo, binding.contract)
    cninfo["input_sha256"] = active
    cninfo["declared_input_sha256"] = declared
    with pytest.raises(runner.HeldoutPreparationError, match="CNInfo announcement body"):
        runner._validate_candidate_input_hashes(title_only_cninfo, binding.contract)

    stale_title = copy.deepcopy(candidates)
    stale_title[0]["title"] = "coherently relinked title without frozen serializer digest"
    with pytest.raises(runner.HeldoutPreparationError, match="input identities drifted"):
        runner._validate_candidate_input_hashes(stale_title, binding.contract)

    predictions = [
        runner._synthetic_prediction(row, materiality=2 if index <= 50 else 1)
        for index, row in enumerate(candidates, 1)
    ]
    extra_field = copy.deepcopy(predictions)
    extra_field[-1]["attacker_field"] = True
    with pytest.raises(runner.HeldoutPreparationError, match=r"prediction row .* schema"):
        runner._validate_candidate_prediction_rows(binding, candidates, extra_field)
    wrong_model = copy.deepcopy(predictions)
    wrong_model[-1]["model"] = "attacker-model"
    with pytest.raises(runner.HeldoutPreparationError, match="contract/security/result"):
        runner._validate_candidate_prediction_rows(binding, candidates, wrong_model)


def test_create_only_bundle_and_inference_claim_fsync_parent_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory_fsyncs = 0
    original_fsync = os.fsync

    def spy_fsync(descriptor: int) -> None:
        nonlocal directory_fsyncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsyncs += 1
        original_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    target = tmp_path / "bundle" / "artifact.json"
    runner._publish_create_only(((target, b"{}\n"),))
    state = tmp_path / "state" / "inference.jsonl"
    with runner._exclusive_inference_state(state) as descriptor:
        runner._append_state_descriptor(descriptor, {"status": "inference_started"})

    assert target.read_bytes() == b"{}\n"
    assert state.is_file()
    assert directory_fsyncs >= 2


def test_successor_release_path_requires_one_status_a_touch_and_no_later_commit(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "release-git"
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.name", "Offline Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "config", "user.email", "offline@test.invalid"],
        check=True,
    )
    relative = runner.SUCCESSOR_V2_1_RELEASE_PATH
    receipt = repository / relative
    receipt.parent.mkdir(parents=True)
    receipt.write_text('{"version":1}\n', encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "--", relative.as_posix()],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-q", "-m", "create receipt"],
        check=True,
    )
    creation = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert runner._unique_added_path_commit(repository, relative) == creation

    receipt.write_text('{"version":2}\n', encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "--", relative.as_posix()],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repository), "commit", "-q", "-m", "modify receipt"],
        check=True,
    )
    with pytest.raises(runner.HeldoutPreparationError, match="exactly one"):
        runner._unique_added_path_commit(repository, relative)


def test_authority_git_proofs_ignore_all_ambient_git_redirection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in {
        "GIT_DIR": str(tmp_path / "attacker.git"),
        "GIT_WORK_TREE": str(tmp_path / "attacker-worktree"),
        "GIT_INDEX_FILE": str(tmp_path / "attacker-index"),
        "GIT_OBJECT_DIRECTORY": str(tmp_path / "attacker-objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(tmp_path / "alternate-objects"),
        "GIT_CONFIG_PARAMETERS": "'alias.status=!false'",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": str(tmp_path / "hooks"),
    }.items():
        monkeypatch.setenv(name, value)

    environment = runner._git_environment()
    assert not any(
        name in environment
        for name in {
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_CONFIG_PARAMETERS",
            "GIT_CONFIG_COUNT",
            "GIT_CONFIG_KEY_0",
            "GIT_CONFIG_VALUE_0",
        }
    )
    head = runner._git(runner.PROJECT_ROOT, "rev-parse", "HEAD").strip()
    runner._require_git_ancestor(
        runner.PROJECT_ROOT,
        runner.SUCCESSOR_V2_1_PREREGISTRATION_COMMIT,
        head,
        "test authority ancestry",
    )
    assert runner._git_blob(
        runner.PROJECT_ROOT,
        head,
        runner.SUCCESSOR_V2_1_PREREGISTRATION_PATH,
        "test preregistration",
    ) == (runner.PROJECT_ROOT / runner.SUCCESSOR_V2_1_PREREGISTRATION_PATH).read_bytes()


def test_authority_git_proofs_disable_repository_fsmonitor(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    marker = tmp_path / "fsmonitor-executed"
    subprocess.run(["/usr/bin/git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        [
            "/usr/bin/git",
            "-C",
            str(repository),
            "config",
            "core.fsmonitor",
            f"/usr/bin/touch {marker}",
        ],
        check=True,
    )

    assert runner._git(repository, "status", "--porcelain=v1") == ""
    assert not marker.exists()


def test_authority_git_proofs_ignore_repository_replace_refs(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    relative = Path("authority.txt")
    subprocess.run(["/usr/bin/git", "init", "-q", str(repository)], check=True)
    for key, value in (("user.name", "Test"), ("user.email", "test@example.com")):
        subprocess.run(
            ["/usr/bin/git", "-C", str(repository), "config", key, value],
            check=True,
        )
    path = repository / relative
    path.write_bytes(b"original\n")
    subprocess.run(
        ["/usr/bin/git", "-C", str(repository), "add", relative.as_posix()],
        check=True,
    )
    subprocess.run(
        ["/usr/bin/git", "-C", str(repository), "commit", "-q", "-m", "original"],
        check=True,
    )
    original = subprocess.run(
        ["/usr/bin/git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    path.write_bytes(b"replacement\n")
    subprocess.run(
        ["/usr/bin/git", "-C", str(repository), "commit", "-qam", "replacement"],
        check=True,
    )
    replacement = subprocess.run(
        ["/usr/bin/git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["/usr/bin/git", "-C", str(repository), "replace", original, replacement],
        check=True,
    )

    assert runner._git_blob(repository, original, relative, "replace-ref probe") == b"original\n"


def test_authority_git_proofs_reject_repository_grafts(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    subprocess.run(["/usr/bin/git", "init", "-q", str(repository)], check=True)
    grafts = repository / ".git/info/grafts"
    grafts.parent.mkdir(parents=True, exist_ok=True)
    grafts.write_text(f"{'1' * 40} {'2' * 40}\n", encoding="utf-8")

    with pytest.raises(runner.HeldoutPreparationError, match="Git grafts are forbidden"):
        runner._git(repository, "rev-parse", "HEAD", check=False)


def test_locked_validator_machine_result_is_exact() -> None:
    expected = {
        "schema_version": runner.SUCCESSOR_V2_1_VALIDATOR_RESULT_SCHEMA,
        "status": runner.SUCCESSOR_V2_1_VALIDATOR_RESULT_STATUS,
        "bundle_path": runner.SUCCESSOR_V2_1_BUNDLE_PATH.as_posix(),
        "bundle_sha256": "1" * 64,
        "bundle_root_sha256": "2" * 64,
        "implementation_commit": "3" * 40,
        "real_heldout_materialization_unlocked": False,
        "heldout_metric_evaluation_unlocked": False,
    }
    valid = subprocess.CompletedProcess(
        args=("registered-validator",),
        returncode=0,
        stdout=common.canonical_json_bytes(expected),
        stderr=b"",
    )
    runner._validate_locked_validator_result(valid, expected)

    for invalid in (
        subprocess.CompletedProcess(valid.args, 2, valid.stdout, valid.stderr),
        subprocess.CompletedProcess(valid.args, 0, valid.stdout, b"unexpected\n"),
        subprocess.CompletedProcess(valid.args, 0, valid.stdout + b"\n", valid.stderr),
        subprocess.CompletedProcess(
            valid.args,
            0,
            common.canonical_json_bytes(
                {**expected, "real_heldout_materialization_unlocked": 0}
            ),
            valid.stderr,
        ),
    ):
        with pytest.raises(
            runner.HeldoutPreparationError,
            match="failed locked independent validation",
        ):
            runner._validate_locked_validator_result(invalid, expected)


def test_canonical_real_stage_runtime_snapshot_is_exact_and_fail_closed() -> None:
    root = runner.PROJECT_ROOT.resolve()
    entrypoint = runner._REAL_STAGE_ENTRYPOINTS["materialize"]
    valid = runner._CanonicalRuntimeSnapshot(
        environment=runner._canonical_real_stage_environment(root),
        runtime_paths=runner._canonical_real_stage_runtime_paths(root),
        executable=(root / runner._LOCKED_PYTHON_EXECUTABLE_RELATIVE).as_posix(),
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
        main_file=(root / entrypoint).as_posix(),
        original_arguments=(
            "registered-python",
            "-S",
            "-P",
            "-B",
            "-c",
            runner._canonical_real_stage_bootstrap(root, "materialize"),
            "materialize",
        ),
    )
    assert (
        runner._canonical_runtime_snapshot_drift(
            root,
            stage="materialize",
            snapshot=valid,
        )
        is None
    )
    invalid_snapshots = (
        (replace(valid, environment={}), "environment"),
        (replace(valid, runtime_paths=()), "sys.path"),
        (replace(valid, hash_randomization=1), "interpreter flags"),
        (replace(valid, original_arguments=("python", "-c", "pass")), "first-exec command"),
    )
    for snapshot, reason in invalid_snapshots:
        assert (
            runner._canonical_runtime_snapshot_drift(
                root,
                stage="materialize",
                snapshot=snapshot,
            )
            == reason
        )


def test_exact_first_exec_runtime_reaches_only_missing_release_gate() -> None:
    """Pure pre-release probe; skip forever once an owner receipt exists."""

    root = runner.PROJECT_ROOT.resolve()
    receipt = root / runner.SUCCESSOR_V2_1_RELEASE_PATH
    if receipt.exists() or receipt.is_symlink():
        pytest.skip("owner receipt exists; never exercise a real stage from this test")
    binding = runner.load_binding()
    before = {
        name: (path.exists(), path.is_symlink())
        for name, path in binding.artifacts.items()
    }
    completed = subprocess.run(
        [
            str(root / runner._LOCKED_PYTHON_EXECUTABLE_RELATIVE),
            "-S",
            "-P",
            "-B",
            "-c",
            runner._canonical_real_stage_bootstrap(root, "materialize"),
            "materialize",
            "--attester-identity",
            "pure-pre-release-runtime-probe",
            "--cninfo-midnight-batch-assessment",
            "clear_for_start",
            "--p4-1-dense-poll-slot-assessment",
            "clear_for_start",
        ],
        cwd=root,
        env=runner._canonical_real_stage_environment(root),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "exact first-exec runtime" not in completed.stderr
    assert "BLOCKED_PENDING_SUCCESSOR_V2_1_OWNER_RELEASE" in completed.stderr
    assert {
        name: (path.exists(), path.is_symlink())
        for name, path in binding.artifacts.items()
    } == before


def test_real_stage_runtime_rejects_before_release_or_business_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = runner.load_binding()
    snapshot = runner._capture_canonical_runtime_snapshot("materialize")
    monkeypatch.setattr(
        runner,
        "_capture_canonical_runtime_snapshot",
        lambda _stage: replace(snapshot, environment={}),
    )

    def forbidden_release(_root: Path) -> NoReturn:
        raise AssertionError("runtime drift must reject before release or business input")

    monkeypatch.setattr(runner, "validate_v2_1_release_authorization", forbidden_release)
    with pytest.raises(
        runner.HeldoutPreparationError,
        match="exact first-exec runtime: environment drifted",
    ):
        runner.validate_v2_1_stage_authorization(binding, stage="materialize")


def test_real_stage_validates_release_before_importing_origin_classifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unit ordering isolation only; this is not release-gate PASS evidence."""

    binding = runner.load_binding()
    events: list[str] = []
    authorization = runner.V21ReleaseAuthorization(
        project_root=runner.PROJECT_ROOT,
        receipt_path=runner.PROJECT_ROOT / runner.SUCCESSOR_V2_1_RELEASE_PATH,
        receipt_sha256="1" * 64,
        receipt_creating_commit="2" * 40,
        preregistration_commit=runner.SUCCESSOR_V2_1_PREREGISTRATION_COMMIT,
        implementation_commit="3" * 40,
        rehearsal_evidence_commit="4" * 40,
        bundle_path=runner.PROJECT_ROOT / runner.SUCCESSOR_V2_1_BUNDLE_PATH,
        bundle_sha256="5" * 64,
        bundle_root_sha256="6" * 64,
    )

    def runtime_environment(
        _binding: runner.HeldoutBinding,
        *,
        stage: str,
    ) -> None:
        assert stage == "materialize"
        events.append("runtime")

    def release(_root: Path) -> runner.V21ReleaseAuthorization:
        events.append("locked-release-child")
        return authorization

    def origins(
        _binding: runner.HeldoutBinding,
        *,
        stage: str,
        authorization: runner.V21ReleaseAuthorization,
    ) -> None:
        assert stage == "materialize"
        assert authorization is not None
        events.append("origins")

    monkeypatch.setattr(runner, "_validate_canonical_runtime_environment", runtime_environment)
    monkeypatch.setattr(runner, "validate_v2_1_release_authorization", release)
    monkeypatch.setattr(runner, "_validate_canonical_runtime_module_origins", origins)

    assert runner.validate_v2_1_stage_authorization(binding, stage="materialize") is authorization
    assert events == ["runtime", "locked-release-child", "origins"]


def test_offline_capability_is_identity_bound_nonserializable_and_stage_scoped(
    tmp_path: Path,
) -> None:
    binding = _temporary_binding(tmp_path)
    capability = _offline_context(binding)
    assert not hasattr(runner, "_MINTED_OFFLINE_CAPABILITIES")

    assert (
        runner.validate_v2_1_stage_authorization(
            binding,
            stage="finalize-owner-adjudication",
            execution_context=capability,
        )
        is capability
    )
    with pytest.raises(TypeError, match="not serializable"):
        pickle.dumps(capability)
    forged = replace(capability, _nonce=object())
    with pytest.raises(runner.HeldoutPreparationError, match="forged or drifted"):
        runner.validate_v2_1_stage_authorization(
            binding,
            stage="materialize",
            execution_context=forged,
        )
    directly_constructed = runner._OfflineRehearsalCapability(
        _nonce=runner._OFFLINE_CAPABILITY_NONCE,
        project_root=capability.project_root,
        database=capability.database,
        artifact_paths=capability.artifact_paths,
        pdf_fetcher=capability.pdf_fetcher,
        pdf_text_extractor=capability.pdf_text_extractor,
        monotonic=capability.monotonic,
        sleep=capability.sleep,
        inference_settings=capability.inference_settings,
        chat_json_fn=capability.chat_json_fn,
        snapshot_loader=capability.snapshot_loader,
        wall_clock=capability.wall_clock,
        execution_id_factory=capability.execution_id_factory,
        prediction_recorded_at_clock=capability.prediction_recorded_at_clock,
        prediction_monotonic_ns_clock=capability.prediction_monotonic_ns_clock,
        preregistration_commit=capability.preregistration_commit,
        implementation_commit=capability.implementation_commit,
    )
    with pytest.raises(runner.HeldoutPreparationError, match="forged or drifted"):
        runner.validate_v2_1_stage_authorization(
            binding,
            stage="materialize",
            execution_context=directly_constructed,
        )
    with pytest.raises(runner.HeldoutPreparationError, match="synthetic successor receipt"):
        runner.validate_v2_1_stage_authorization(
            binding,
            stage="materialize",
            execution_context=None,
        )
    with pytest.raises(
        runner.HeldoutPreparationError,
        match="REJECTED_PENDING_OWNER_ADJUDICATION_AUTHORITY",
    ):
        runner.validate_v2_1_stage_authorization(
            runner.load_binding(),
            stage="finalize-owner-adjudication",
        )


def test_offline_capability_rejects_a_root_that_contains_canonical_repository() -> None:
    binding = replace(runner.load_binding(), root=runner.PROJECT_ROOT.parent)
    with pytest.raises(
        runner.HeldoutPreparationError,
        match="distinct and outside the canonical repository",
    ):
        _offline_context(binding)


def test_prevalidated_stage_authority_is_identity_and_stage_bound(
    tmp_path: Path,
) -> None:
    binding = _temporary_binding(tmp_path)
    try:
        authorization = _offline_context(binding)
    except runner.HeldoutPreparationError as exc:
        head = runner._git(runner.PROJECT_ROOT, "rev-parse", "HEAD").strip()
        if (
            head == runner.SUCCESSOR_V2_1_TIMING_PREREGISTRATION_COMMIT
            and str(exc).startswith(
                "timing target was not changed by the implementation:"
            )
        ):
            pytest.skip("successor implementation commit is not available yet")
        raise
    delegated = runner._prevalidate_v2_1_stage_authorization(
        binding,
        stage="seal-draft",
        execution_context=authorization,
    )
    assert (
        runner._consume_prevalidated_v2_1_stage_authorization(
            binding,
            delegated,
            "seal-draft",
        )
        is authorization
    )
    pure_authorization, same_delegated = runner._pure_revalidation_authority(
        binding,
        execution_context=None,
        prevalidated_authority=delegated,
        validated_stage="seal-draft",
    )
    assert pure_authorization is authorization
    assert same_delegated is delegated

    with pytest.raises(runner.HeldoutPreparationError, match="cross-stage"):
        runner._consume_prevalidated_v2_1_stage_authorization(
            binding,
            delegated,
            "build-adjudication-ui",
        )
    forged = replace(delegated, _nonce=runner._PREVALIDATED_STAGE_AUTHORITY_NONCE)
    with pytest.raises(runner.HeldoutPreparationError, match="forged"):
        runner._consume_prevalidated_v2_1_stage_authorization(
            binding,
            forged,
            "seal-draft",
        )
    with pytest.raises(runner.HeldoutPreparationError, match="one explicit stage"):
        runner._pure_revalidation_authority(
            binding,
            execution_context=authorization,
            prevalidated_authority=delegated,
            validated_stage="seal-draft",
        )
    other_binding = replace(binding, root=tmp_path / "different-root")
    with pytest.raises(runner.HeldoutPreparationError, match="drifted"):
        runner._consume_prevalidated_v2_1_stage_authorization(
            other_binding,
            delegated,
            "seal-draft",
        )
    with pytest.raises(TypeError, match="not serializable"):
        pickle.dumps(delegated)


def test_stolen_prevalidated_registry_cannot_unlock_canonical_stage() -> None:
    binding = runner.load_binding()
    forged_authorization = runner.V21ReleaseAuthorization(
        project_root=binding.root,
        receipt_path=binding.root / runner.SUCCESSOR_V2_1_RELEASE_PATH,
        receipt_sha256="1" * 64,
        receipt_creating_commit="2" * 40,
        preregistration_commit=runner.SUCCESSOR_V2_1_PREREGISTRATION_COMMIT,
        implementation_commit="3" * 40,
        rehearsal_evidence_commit="4" * 40,
        bundle_path=binding.root / runner.SUCCESSOR_V2_1_BUNDLE_PATH,
        bundle_sha256="5" * 64,
        bundle_root_sha256="6" * 64,
    )
    forged = runner._PrevalidatedStageAuthority(
        _nonce=runner._PREVALIDATED_STAGE_AUTHORITY_NONCE,
        project_root=binding.root,
        validated_stage="seal-draft",
        authorization=forged_authorization,
    )
    closure = inspect.getclosurevars(
        runner._consume_prevalidated_v2_1_stage_authorization
    ).nonlocals
    registry = closure["minted"]
    assert isinstance(registry, dict)
    registry[id(forged)] = forged
    try:
        with pytest.raises(runner.HeldoutPreparationError):
            runner._consume_prevalidated_v2_1_stage_authorization(
                binding,
                forged,
                "seal-draft",
            )
    finally:
        registry.pop(id(forged), None)


def test_cninfo_start_pacer_has_no_first_delay_and_exact_monotonic_evidence() -> None:
    now = 1_000.0
    sleeps: list[float] = []

    def monotonic() -> float:
        return now

    def sleep(duration: float) -> None:
        nonlocal now
        sleeps.append(duration)
        now += duration

    pacer = runner._CninfoStartPacer(monotonic, sleep)
    pacer.before_fetch()
    assert sleeps == []
    pacer.before_fetch()
    pacer.before_fetch()

    assert sleeps == [1.0, 1.0]
    assert pacer.evidence() == {
        "host": "static.cninfo.com.cn",
        "policy": "minimum_start_to_start",
        "configured_min_start_to_start_seconds": 1.0,
        "clock": "monotonic",
        "first_request_delayed": False,
        "request_start_count": 3,
        "observed_gap_count": 2,
        "minimum_observed_start_to_start_seconds": 1.0,
        "median_observed_start_to_start_seconds": 1.0,
        "violation_count": 0,
        "retry_count": 0,
    }


def test_cninfo_noop_sleeper_rejects_before_second_fetch_and_never_retries() -> None:
    calls = 0

    def fetch(
        _url: str,
        _policy: gold_builder.AnnouncementBodyPolicy,
    ) -> bytes:
        nonlocal calls
        calls += 1
        return b"%PDF-offline"

    paced, _extract = runner._paced_pdf_boundaries(
        runner._CninfoStartPacer(lambda: 100.0, lambda _duration: None),
        fetch,
        _unused_pdf_extractor,
    )
    policy = _test_pdf_policy()
    paced("https://static.cninfo.com.cn/first.pdf", policy)
    with pytest.raises(runner.HeldoutPreparationError, match="did not advance"):
        paced("https://static.cninfo.com.cn/second.pdf", policy)
    assert calls == 1

    reversing = iter((10.0, 9.0))
    reversed_pacer = runner._CninfoStartPacer(lambda: next(reversing), lambda _value: None)
    reversed_pacer.before_fetch()
    with pytest.raises(runner.HeldoutPreparationError, match="reversed"):
        reversed_pacer.before_fetch()
    with pytest.raises(runner.HeldoutPreparationError, match="non-finite"):
        runner._CninfoStartPacer(
            lambda: float("nan"), lambda _value: None
        ).before_fetch()


def test_paced_pdf_boundaries_preserve_only_registered_candidate_reasons() -> None:
    policy = _test_pdf_policy()

    def allowed_size(
        _url: str,
        _policy: gold_builder.AnnouncementBodyPolicy,
    ) -> bytes:
        raise gold_builder.CandidateDocumentIneligible(
            reason="pdf_exceeds_size_bound",
            measured_value=2,
            gate_value=1,
            pdf_sha256=None,
        )

    paced, _extract = runner._paced_pdf_boundaries(
        runner._CninfoStartPacer(lambda: 100.0, lambda _value: None),
        allowed_size,
        _unused_pdf_extractor,
    )
    with pytest.raises(
        gold_builder.CandidateDocumentIneligible,
        match="pdf_exceeds_size_bound",
    ):
        paced("https://static.cninfo.com.cn/allowed.pdf", policy)

    def unknown(
        _url: str,
        _policy: gold_builder.AnnouncementBodyPolicy,
    ) -> bytes:
        raise gold_builder.CandidateDocumentIneligible(
            reason="transient_download_failure",
            measured_value=0,
            gate_value=1,
            pdf_sha256=None,
        )

    unknown_paced, _extract = runner._paced_pdf_boundaries(
        runner._CninfoStartPacer(lambda: 100.0, lambda _value: None),
        unknown,
        _unused_pdf_extractor,
    )
    with pytest.raises(runner.HeldoutPreparationError, match="unknown deterministic"):
        unknown_paced("https://static.cninfo.com.cn/unknown.pdf", policy)

    calls = 0

    def unexpected(
        _url: str,
        _policy: gold_builder.AnnouncementBodyPolicy,
    ) -> bytes:
        nonlocal calls
        calls += 1
        raise RuntimeError("transport failed")

    unexpected_paced, _extract = runner._paced_pdf_boundaries(
        runner._CninfoStartPacer(lambda: 100.0, lambda _value: None),
        unexpected,
        _unused_pdf_extractor,
    )
    with pytest.raises(RuntimeError, match="transport failed"):
        unexpected_paced("https://static.cninfo.com.cn/failure.pdf", policy)
    assert calls == 1


def test_run_materialize_paces_cninfo_and_continues_two_deterministic_reasons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _temporary_binding(tmp_path)
    available = datetime(2026, 8, 6, tzinfo=UTC)
    rows = [
        gold_builder.NewsRow(
            news_item_id=900_000 + index,
            source="cninfo" if index <= 3 else "akshare_ths",
            ingested_symbol="000001",
            title=f"fixture {index}",
            url=(
                f"https://static.cninfo.com.cn/finalpage/2026-08-06/{index}.PDF"
                if index <= 3
                else "https://news.10jqka.com.cn/fixture"
            ),
            published_at=available,
            available_time=available,
            content_hash=f"{index:064x}",
            raw_payload={"summary": f"fixture {index}"},
        )
        for index in range(1, 5)
    ]
    now = 1_000.0
    sleeps: list[float] = []
    fetch_calls: list[str] = []
    extract_calls = 0

    def monotonic() -> float:
        return now

    def sleep(duration: float) -> None:
        nonlocal now
        sleeps.append(duration)
        now += duration

    def fetch(
        url: str,
        _policy: gold_builder.AnnouncementBodyPolicy,
    ) -> bytes:
        fetch_calls.append(url)
        if url.endswith("/1.PDF"):
            raise gold_builder.CandidateDocumentIneligible(
                reason="pdf_exceeds_size_bound",
                measured_value=9,
                gate_value=8,
                pdf_sha256=None,
            )
        return b"%PDF-short" if url.endswith("/2.PDF") else b"%PDF-valid"

    def extract(
        payload: bytes,
        _policy: gold_builder.AnnouncementBodyPolicy,
    ) -> gold_builder.ExtractedPdfText:
        nonlocal extract_calls
        extract_calls += 1
        if payload == b"%PDF-short":
            raise gold_builder.CandidateDocumentIneligible(
                reason="pdf_text_below_min_char_gate",
                measured_value=1,
                gate_value=80,
                pdf_sha256=common.sha256_bytes(payload),
            )
        text = "有效公告正文" * 20
        return gold_builder.ExtractedPdfText(
            text=text,
            text_sha256=common.sha256_bytes(text.encode()),
            full_character_count=len(text),
        )

    def fake_materialize(
        source_rows: list[gold_builder.NewsRow],
        _design: object,
        _contract: object,
        *,
        pdf_fetcher: gold_builder.PdfFetcher,
        pdf_text_extractor: gold_builder.PdfTextExtractor,
    ) -> gold_builder.HeldoutCandidateMaterialization:
        all_candidates: list[dict[str, Any]] = []
        eligible: list[dict[str, Any]] = []
        ineligible: list[dict[str, Any]] = []
        reasons: dict[str, int] = {}
        for row in source_rows:
            all_candidates.append(
                {
                    "news_item_id": row.news_item_id,
                    "source": row.source,
                    "url": row.url,
                    "content_hash": row.content_hash,
                }
            )
            try:
                if row.source == "cninfo":
                    payload = pdf_fetcher(row.url, _test_pdf_policy())
                    pdf_text_extractor(payload, _test_pdf_policy())
            except gold_builder.CandidateDocumentIneligible as exc:
                ineligible.append(
                    {
                        "news_item_id": row.news_item_id,
                        "url": row.url,
                        "reason": exc.reason,
                        "measured_value": exc.measured_value,
                        "gate_value": exc.gate_value,
                        "pdf_sha256": exc.pdf_sha256,
                    }
                )
                reasons[exc.reason] = reasons.get(exc.reason, 0) + 1
                continue
            digest = f"{row.news_item_id:064x}"[-64:]
            eligible.append(
                {
                    "news_item_id": row.news_item_id,
                    "source": row.source,
                    "input_sha256": digest,
                    "declared_input_sha256": digest,
                    "text_sha256": digest,
                }
            )
        return gold_builder.HeldoutCandidateMaterialization(
            all_candidates=tuple(all_candidates),
            eligible_records=tuple(eligible),
            ineligible_candidates=tuple(ineligible),
            reason_counts=reasons,
        )

    monkeypatch.setattr(runner, "EXPECTED_RAW_COUNT", len(rows))
    monkeypatch.setattr(runner, "_window_rows", lambda _binding, _database: rows)
    monkeypatch.setattr(runner, "_materialization_design", lambda _binding: object())
    monkeypatch.setattr(
        runner,
        "_verified_source_lineage",
        lambda _binding: {"offline_test": True},
    )
    monkeypatch.setattr(
        gold_builder,
        "materialize_heldout_candidate_inputs",
        fake_materialize,
    )
    database = binding.root / "data/alphapilot.db"
    capability = _offline_context(
        binding,
        database=database,
        pdf_fetcher=fetch,
        pdf_text_extractor=extract,
        monotonic=monotonic,
        sleep=sleep,
    )

    runner.run_materialize(
        binding,
        operator_timing_attestation=None,
        database=database,
        pdf_fetcher=fetch,
        pdf_text_extractor=extract,
        execution_context=capability,
        monotonic=monotonic,
        sleep=sleep,
    )
    manifest = json.loads(
        binding.artifacts["materialization_manifest"].read_text(encoding="utf-8")
    )

    assert len(fetch_calls) == 3
    assert extract_calls == 2
    assert sleeps == [1.0, 1.0]
    assert manifest["counts"]["eligible_candidates"] == 2
    assert manifest["counts"]["ineligible_by_reason"] == {
        "pdf_exceeds_size_bound": 1,
        "pdf_text_below_min_char_gate": 1,
    }
    assert manifest["request_pacing"]["cninfo_pdf"]["request_start_count"] == 3
    assert manifest["request_pacing"]["cninfo_pdf"]["retry_count"] == 0
    assert manifest["execution_authority"]["mode"] == "offline_rehearsal"
    assert manifest["execution_authority"]["rehearsal_bundle"] is None
    assert manifest["execution_authority"]["release_authorization"] is None


def test_run_materialize_unexpected_fetch_aborts_without_retry_or_partial_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _temporary_binding(tmp_path)
    available = datetime(2026, 8, 6, tzinfo=UTC)
    row = gold_builder.NewsRow(
        news_item_id=910_001,
        source="cninfo",
        ingested_symbol="000001",
        title="failure fixture",
        url="https://static.cninfo.com.cn/finalpage/2026-08-06/failure.PDF",
        published_at=available,
        available_time=available,
        content_hash="1" * 64,
        raw_payload={"summary": "failure fixture"},
    )
    calls = 0

    def fetch(
        _url: str,
        _policy: gold_builder.AnnouncementBodyPolicy,
    ) -> bytes:
        nonlocal calls
        calls += 1
        raise RuntimeError("single transport failure")

    def fake_materialize(
        _rows: object,
        _design: object,
        _contract: object,
        *,
        pdf_fetcher: gold_builder.PdfFetcher,
        pdf_text_extractor: object,
    ) -> NoReturn:
        del pdf_text_extractor
        pdf_fetcher(row.url, _test_pdf_policy())
        raise AssertionError("unreachable")

    def monotonic() -> float:
        return 100.0

    def sleep(_duration: float) -> None:
        return None

    def extractor(
        _payload: bytes,
        _policy: gold_builder.AnnouncementBodyPolicy,
    ) -> gold_builder.ExtractedPdfText:
        return _unused_pdf_extractor(_payload, _policy)

    monkeypatch.setattr(runner, "_window_rows", lambda _binding, _database: [row])
    monkeypatch.setattr(runner, "_materialization_design", lambda _binding: object())
    monkeypatch.setattr(
        gold_builder,
        "materialize_heldout_candidate_inputs",
        fake_materialize,
    )
    database = binding.root / "data/alphapilot.db"
    capability = _offline_context(
        binding,
        database=database,
        pdf_fetcher=fetch,
        pdf_text_extractor=extractor,
        monotonic=monotonic,
        sleep=sleep,
    )

    with pytest.raises(RuntimeError, match="single transport failure"):
        runner.run_materialize(
            binding,
            operator_timing_attestation=None,
            database=database,
            pdf_fetcher=fetch,
            pdf_text_extractor=extractor,
            execution_context=capability,
            monotonic=monotonic,
            sleep=sleep,
        )
    assert calls == 1
    assert not binding.artifacts["materialized_inputs"].exists()
    assert not binding.artifacts["materialization_manifest"].exists()


def test_run_materialize_rejects_existing_target_before_database_or_fetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _temporary_binding(tmp_path)
    existing = binding.artifacts["materialized_inputs"]
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"preexisting\n")
    fetch_calls = 0
    window_calls = 0

    def forbidden_window_rows(
        _binding: runner.HeldoutBinding,
        _database: Path,
    ) -> NoReturn:
        nonlocal window_calls
        window_calls += 1
        raise AssertionError("preexisting output must reject before a database read")

    monkeypatch.setattr(runner, "_window_rows", forbidden_window_rows)

    def fetch(
        _url: str,
        _policy: gold_builder.AnnouncementBodyPolicy,
    ) -> bytes:
        nonlocal fetch_calls
        fetch_calls += 1
        raise AssertionError("preexisting output must reject before a fetch")

    def monotonic() -> float:
        return 0.0

    def sleep(_duration: float) -> None:
        return None

    context = _offline_context(
        binding,
        pdf_fetcher=fetch,
        pdf_text_extractor=_unused_pdf_extractor,
        monotonic=monotonic,
        sleep=sleep,
    )
    with pytest.raises(FileExistsError, match="refusing to reuse"):
        runner.run_materialize(
            binding,
            operator_timing_attestation=None,
            database=binding.root / "data/alphapilot.db",
            pdf_fetcher=fetch,
            pdf_text_extractor=_unused_pdf_extractor,
            execution_context=context,
            monotonic=monotonic,
            sleep=sleep,
        )
    assert fetch_calls == 0
    assert window_calls == 0


def test_launchagent_parser_is_pure_and_fail_closed() -> None:
    label = "com.alphapilot.database-backup"
    target = f"gui/{os.getuid()}/{label}"
    evidence = runner._parse_launchagent_evidence(
        label=label,
        target=target,
        output="state = not running\nlast exit code = 0\n",
    )
    assert evidence == {
        "label": label,
        "target": target,
        "loaded": True,
        "state": "not running",
        "last_exit_code": 0,
    }
    for output in (
        "state = running\nlast exit code = 0\n",
        "state = not running\nlast exit code = 1\n",
        "state = not running\n",
        "state = not running\nstate = running\nlast exit code = 0\n",
    ):
        with pytest.raises(runner.HeldoutPreparationError, match="LaunchAgent"):
            runner._parse_launchagent_evidence(
                label=label,
                target=target,
                output=output,
            )


def test_runtime_preflight_evidence_is_bound_to_exact_paths_and_current_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _temporary_binding(tmp_path)
    runtime_directory = tmp_path / "runtime"
    runtime_directory.mkdir()
    monkeypatch.setattr(
        runner,
        "_database_backup_runtime_directory",
        lambda: runtime_directory,
    )
    backup_directory = binding.root / "data/backups"
    backup_directory.mkdir(parents=True)
    backup_path = backup_directory / "alphapilot-full-20260810T141000000000Z.db"
    backup_path.write_bytes(b"offline backup fixture")
    backup_sha = common.sha256_file(backup_path)
    manifest_path = database_backup.manifest_path_for(backup_path)
    backup_manifest = {
        "format_version": database_backup.BACKUP_FORMAT_VERSION,
        "managed_by": database_backup.BACKUP_MANAGED_BY,
        "created_at": "2026-08-10T14:10:00Z",
        "source": {"query_only": True},
        "backup": {"filename": backup_path.name, "sha256": backup_sha},
    }
    manifest_bytes = common.canonical_json_bytes(backup_manifest)
    manifest_path.write_bytes(manifest_bytes)
    observed_utc = "2026-08-10T14:30:00Z"
    observed_shanghai = "2026-08-10T22:30:00+08:00"
    evidence: dict[str, Any] = {
        "mode": "real",
        "observed_at_utc": observed_utc,
        "observed_at_shanghai": observed_shanghai,
        "backup_stamp": {
            "path": str(runtime_directory / "last-success-shanghai-date"),
            "expected_shanghai_date": "2026-08-10",
            "observed_value": "2026-08-10",
            "regular_file": True,
            "symlink": False,
            "mode": "0600",
        },
        "database_backup_launchagent": {
            "label": "com.alphapilot.database-backup",
            "target": f"gui/{os.getuid()}/com.alphapilot.database-backup",
            "loaded": True,
            "state": "not running",
            "last_exit_code": 0,
        },
        "database_backup_lock": {
            "path": str(runtime_directory / ".daily-backup.lock"),
            "nonblocking_exclusive_flock_acquired": True,
            "held": False,
        },
        "verified_backup": {
            "manifest_path": str(manifest_path.resolve()),
            "manifest_sha256": common.sha256_file(manifest_path),
            "backup_path": str(backup_path.resolve()),
            "backup_sha256": backup_sha,
            "created_at_utc": "2026-08-10T14:10:00Z",
            "created_at_shanghai": "2026-08-10T22:10:00+08:00",
            "quick_check": "ok",
            "verify_database_backup_passed": True,
        },
        "operator_timing_attestation": {
            "observed_start_cst": observed_shanghai,
            "attester_identity": "owner-ouyang",
            "explicitly_supplied": True,
            "input_channel": (
                "required_real_CLI_flags_or_required_typed_run_materialize_argument_no_default"
            ),
            "cninfo_midnight_batch_assessment": "clear_for_start",
            "p4_1_dense_poll_slot_assessment": "clear_for_start",
            "decision": (
                "launched_outside_owner_identified_CNInfo_midnight_and_dense_P4_1_slots"
            ),
            "automatic_blackout_verification": False,
            "authority_path": runner.SUCCESSOR_CODE_GATE_AUTHORITY_PATH.as_posix(),
            "authority_sha256": runner.SUCCESSOR_CODE_GATE_AUTHORITY_SHA256,
        },
    }
    context = runner.V21ReleaseAuthorization(
        project_root=binding.root,
        receipt_path=binding.root / runner.SUCCESSOR_V2_1_RELEASE_PATH,
        receipt_sha256="1" * 64,
        receipt_creating_commit="1" * 40,
        preregistration_commit=runner.SUCCESSOR_V2_1_PREREGISTRATION_COMMIT,
        implementation_commit="2" * 40,
        rehearsal_evidence_commit="3" * 40,
        bundle_path=binding.root / runner.SUCCESSOR_V2_1_BUNDLE_PATH,
        bundle_sha256="4" * 64,
        bundle_root_sha256="5" * 64,
    )

    runner._validate_runtime_preflight_evidence(binding, evidence, context)

    wrong_stamp = copy.deepcopy(evidence)
    wrong_stamp["backup_stamp"]["path"] = str(tmp_path / "attacker-stamp")
    with pytest.raises(runner.HeldoutPreparationError, match="stamp evidence"):
        runner._validate_runtime_preflight_evidence(binding, wrong_stamp, context)

    utc_operator = copy.deepcopy(evidence)
    utc_operator["operator_timing_attestation"]["observed_start_cst"] = observed_utc
    with pytest.raises(runner.HeldoutPreparationError, match="operator timing"):
        runner._validate_runtime_preflight_evidence(binding, utc_operator, context)

    escaped_manifest = copy.deepcopy(evidence)
    escaped_manifest["verified_backup"]["manifest_path"] = str(
        tmp_path / "outside.manifest.json"
    )
    with pytest.raises(runner.HeldoutPreparationError, match="escaped or drifted"):
        runner._validate_runtime_preflight_evidence(binding, escaped_manifest, context)

    manifest_path.write_bytes(manifest_bytes + b" ")
    with pytest.raises(runner.HeldoutPreparationError, match="manifest bytes drifted"):
        runner._validate_runtime_preflight_evidence(binding, evidence, context)


def test_runtime_preflight_never_falls_back_from_newer_invalid_backup_manifest(
    tmp_path: Path,
) -> None:
    binding = _temporary_binding(tmp_path)
    backup_directory = binding.root / "data/backups"
    backup_directory.mkdir(parents=True)
    old_backup = backup_directory / "alphapilot-full-old.db"
    new_backup = backup_directory / "alphapilot-full-new.db"
    old_backup.write_bytes(b"old")
    new_backup.write_bytes(b"new")
    for backup, created_at, format_version in (
        (old_backup, "2026-08-10T14:05:00Z", database_backup.BACKUP_FORMAT_VERSION),
        (new_backup, "2026-08-10T14:20:00Z", 999),
    ):
        database_backup.manifest_path_for(backup).write_bytes(
            common.canonical_json_bytes(
                {
                    "format_version": format_version,
                    "managed_by": database_backup.BACKUP_MANAGED_BY,
                    "created_at": created_at,
                    "backup": {
                        "filename": backup.name,
                        "sha256": common.sha256_file(backup),
                    },
                }
            )
        )

    with pytest.raises(
        runner.HeldoutPreparationError,
        match="latest database backup manifest format or authority drifted",
    ):
        runner._verified_backup_evidence(
            binding,
            datetime(2026, 8, 10, 15, 0, tzinfo=UTC),
        )

    new_manifest = database_backup.manifest_path_for(new_backup)
    new_manifest.write_bytes(
        common.canonical_json_bytes(
            {
                "format_version": database_backup.BACKUP_FORMAT_VERSION,
                "managed_by": database_backup.BACKUP_MANAGED_BY,
                "created_at": "2026-08-10T15:20:00Z",
                "backup": {
                    "filename": new_backup.name,
                    "sha256": common.sha256_file(new_backup),
                },
            }
        )
    )
    with pytest.raises(runner.HeldoutPreparationError, match="future-dated"):
        runner._verified_backup_evidence(
            binding,
            datetime(2026, 8, 10, 15, 0, tzinfo=UTC),
        )

    new_manifest.write_bytes(
        common.canonical_json_bytes(
            {
                "format_version": database_backup.BACKUP_FORMAT_VERSION,
                "managed_by": database_backup.BACKUP_MANAGED_BY,
                "created_at": "2026-08-10T14:20:00Z",
                "backup": {
                    "filename": new_backup.name,
                    "sha256": common.sha256_file(new_backup),
                },
            }
        )
    )
    mismatched_manifest = backup_directory / "alphapilot-full-latest.manifest.json"
    mismatched_manifest.write_bytes(
        common.canonical_json_bytes(
            {
                "format_version": database_backup.BACKUP_FORMAT_VERSION,
                "managed_by": database_backup.BACKUP_MANAGED_BY,
                "created_at": "2026-08-10T14:25:00Z",
                "backup": {
                    "filename": new_backup.name,
                    "sha256": common.sha256_file(new_backup),
                },
            }
        )
    )
    with pytest.raises(
        runner.HeldoutPreparationError,
        match="verified database backup file is unavailable",
    ):
        runner._verified_backup_evidence(
            binding,
            datetime(2026, 8, 10, 15, 0, tzinfo=UTC),
        )


def test_operator_attestation_requires_explicit_nonblank_clear_decision() -> None:
    observed = datetime(2026, 8, 10, 22, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    valid = runner.OperatorTimingAttestation(
        "owner-ouyang", "clear_for_start", "clear_for_start"
    )
    evidence = runner._operator_attestation_evidence(
        valid,
        observed_start_shanghai=observed,
    )
    assert evidence["observed_start_cst"] == "2026-08-10T22:30:00+08:00"
    assert evidence["explicitly_supplied"] is True

    for invalid in (
        None,
        runner.OperatorTimingAttestation(" ", "clear_for_start", "clear_for_start"),
        runner.OperatorTimingAttestation("owner", "blocked", "clear_for_start"),
        runner.OperatorTimingAttestation("owner", "clear_for_start", "blocked"),
    ):
        with pytest.raises(runner.HeldoutPreparationError):
            runner._operator_attestation_evidence(
                invalid,
                observed_start_shanghai=observed,
            )
