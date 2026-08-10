from __future__ import annotations

import copy
import json
import os
import sqlite3
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from scripts import p4_2a_v2_dev_common as common
from scripts import prepare_p4_2a_v2_heldout as runner
from scripts import run_p4_2a_v2_dev_calibration as dev_runner

from alphapilot.core.config import Settings


def _temporary_binding(tmp_path: Path) -> runner.HeldoutBinding:
    source = runner.load_binding()
    artifacts = {
        name: tmp_path / path.relative_to(source.root) for name, path in source.artifacts.items()
    }
    return replace(source, root=tmp_path, artifacts=artifacts)


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


@pytest.mark.parametrize(
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
    directory = binding.artifacts["synthetic_rehearsal"]
    directory.mkdir(parents=True)
    (directory / "contract.json").write_text("{}\n", encoding="utf-8")
    (directory / "inputs.jsonl").write_text("{}\n", encoding="utf-8")
    (directory / "expected.json").write_text("{}\n", encoding="utf-8")
    (directory / "pass-receipt.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "full_path_covered": True,
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
    with pytest.raises(runner.HeldoutPreparationError, match="schema drifted"):
        runner.run_materialize(binding, database=tmp_path / "must-not-open.db")
    assert database_read is False


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
    failed_status: bool = False,
    terminal_predictions_sha256: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    return runner._write_synthetic_production_execution_fixture(
        binding,
        failed_status=failed_status,
        terminal_predictions_sha256=terminal_predictions_sha256,
    )


def test_infer_rejects_unsafe_settings_before_state_or_snapshot(
    tmp_path: Path,
) -> None:
    binding = _temporary_binding(tmp_path)
    _write_inference_fixture(binding, 1)
    snapshot_calls = 0

    def forbidden_snapshot(_root: Path) -> dev_runner.ProductionSnapshot:
        nonlocal snapshot_calls
        snapshot_calls += 1
        raise AssertionError("unsafe settings must fail before a production snapshot")

    with pytest.raises(runner.HeldoutPreparationError, match="pre-state safety gate"):
        runner.run_infer(
            binding,
            settings=_safe_settings(trading_mode="live"),
            snapshot_loader=forbidden_snapshot,
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
    settings = _safe_settings()
    snapshot = _safe_snapshot()
    snapshot_calls: list[Path] = []

    def snapshot_loader(root: Path) -> dev_runner.ProductionSnapshot:
        if not snapshot_calls:
            assert not binding.artifacts["inference_state"].exists()
        snapshot_calls.append(root)
        return snapshot

    runner.run_infer(binding, settings=settings, snapshot_loader=snapshot_loader)

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
    with pytest.raises(runner.HeldoutPreparationError, match="candidate 1 failed"):
        runner.run_infer(
            binding,
            settings=_safe_settings(),
            snapshot_loader=lambda _root: _safe_snapshot(),
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
        runner.run_select_blind(binding)


def test_run_select_blind_closes_full_hash_and_execution_lineage(tmp_path: Path) -> None:
    binding = _temporary_binding(tmp_path)
    _candidates, predictions, execution_id = _write_completed_selection_fixture(binding)

    selection_path, blind_path = runner.run_select_blind(binding)

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


@pytest.mark.parametrize(
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
    _write_completed_selection_fixture(
        binding,
        failed_status=failed_status,
        terminal_predictions_sha256=terminal_sha,
    )

    with pytest.raises(runner.HeldoutPreparationError, match=message):
        runner.run_select_blind(binding)
    assert not binding.artifacts["private_selection"].exists()
    assert not binding.artifacts["owner_blind"].exists()


def test_production_materialization_deep_layers_and_pdf_source_fail_closed(
    tmp_path: Path,
) -> None:
    binding = _temporary_binding(tmp_path)
    candidates, manifest = runner._synthetic_production_materialization_fixture(binding)
    inputs_sha = common.sha256_bytes(common.canonical_jsonl_bytes(candidates))

    missing_layer = copy.deepcopy(manifest)
    del missing_layer["layers"]["all_candidates"]
    with pytest.raises(runner.HeldoutPreparationError, match="layer schema"):
        runner._validate_materialization_for_selection(
            binding,
            missing_layer,
            candidates,
            inputs_sha256=inputs_sha,
        )

    non_cninfo_pdf = copy.deepcopy(manifest)
    ineligible = non_cninfo_pdf["layers"]["ineligible_candidates"][0]
    ineligible["news_item_id"] = candidates[0]["news_item_id"]
    ineligible["url"] = candidates[0]["url"]
    with pytest.raises(runner.HeldoutPreparationError, match="ineligible evidence"):
        runner._validate_materialization_for_selection(
            binding,
            non_cninfo_pdf,
            candidates,
            inputs_sha256=inputs_sha,
        )


def test_candidate_body_and_frozen_contract_input_identities_fail_closed(
    tmp_path: Path,
) -> None:
    binding = _temporary_binding(tmp_path)
    candidates, _manifest = runner._synthetic_production_materialization_fixture(binding)
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
