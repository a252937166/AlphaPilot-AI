from __future__ import annotations

import copy
import json
import re
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from scripts import run_p4_2a_dev_iteration as dev_runner
from scripts.run_p4_2a_offline_extract import ExtractionSummary
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.db.models import LLMCall
from alphapilot.llm.p4_news_eval import (
    EVALUATION_DESIGN_V2_PATH,
    load_event_evaluation_design,
)
from alphapilot.llm.p4_news_event import load_event_extract_contract

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _copy(root: Path, relative: str) -> None:
    source = PROJECT_ROOT / relative
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())


def _fixture_root(tmp_path: Path) -> None:
    for relative in (
        "config/p4_event_extract_eval_v1.yaml",
        "config/p4_event_extract_eval_v1_1.yaml",
        "config/p4_event_extract_eval_v1_2.yaml",
        "config/p4_event_extract_eval_v1_3.yaml",
        "config/p4_event_extract_eval_v1_4.yaml",
        "config/p4_event_extract_eval_v1_5.yaml",
        "config/p4_event_extract_eval_v1_6.yaml",
        "config/p4_event_evaluation_v1_1.yaml",
        "config/p4_event_evaluation_v1_2.yaml",
        "config/p4_event_evaluation_v1_3.yaml",
        "config/p4_event_evaluation_v1_4.yaml",
        "config/p4_event_evaluation_v1_5.yaml",
        "config/prompts/p4_news_event_extract_v1.txt",
        "config/prompts/p4_news_event_extract_v1_1.txt",
        "config/prompts/p4_news_event_extract_v1_2.txt",
        "config/prompts/p4_news_event_extract_v1_3.txt",
        "config/prompts/p4_news_event_extract_v1_4.txt",
        "config/prompts/p4_news_event_extract_v1_5.txt",
        "config/schemas/p4_news_event_v1.schema.json",
        "config/schemas/p4_news_event_candidate_v1.schema.json",
        "config/p4_news_poll_v1.yaml",
        "docs/phase4/eval/P4.2a-gold-inventory60-v1.jsonl",
        "docs/phase4/eval/P4.2a-gold-inventory60-v1.labels-ai-drafted.jsonl",
    ):
        _copy(tmp_path, relative)

    symbols: set[str] = set()
    inventory = (
        tmp_path / "docs/phase4/eval/P4.2a-gold-inventory60-v1.jsonl"
    )
    for line in inventory.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if isinstance(row.get("ingested_symbol"), str):
            symbols.add(row["ingested_symbol"])
        symbols.update(re.findall(r"(?<!\d)[0-9]{6}(?!\d)", row["original_text"]))

    database = tmp_path / "data/alphapilot.db"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE securities (symbol TEXT PRIMARY KEY);
            CREATE TABLE trade_proposals (id INTEGER PRIMARY KEY);
            CREATE TABLE broker_orders (
                id INTEGER PRIMARY KEY,
                environment TEXT NOT NULL
            );
            INSERT INTO trade_proposals(id) VALUES (1);
            INSERT INTO broker_orders(id, environment) VALUES (1, 'SIMULATE');
            """
        )
        connection.executemany(
            "INSERT INTO securities(symbol) VALUES (?)",
            [(symbol,) for symbol in sorted(symbols)],
        )


def _settings(
    *,
    model: str = "qwen3.6-flash",
    endpoint: str = "https://llm.example.test/compatible-mode/v1",
) -> Settings:
    return Settings(
        trading_mode="research",
        live_trading_enabled=False,
        paper_auto_trading_enabled=False,
        futu_enable_account_mutation=False,
        futu_enable_trade=False,
        llm_base_url=endpoint,
        llm_api_key="test-only-key",
        llm_model=model,
    )


def _fake_chat(
    purpose: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    *,
    timeout: float | None = None,
    max_tokens: int | None = None,
    max_retries: int = 1,
    settings: Settings | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    assert purpose == "p4_news_event_extract"
    assert system
    assert schema
    assert timeout == 20.0
    assert max_tokens == 2_000
    assert max_retries == 0
    assert settings is not None
    assert session is not None
    payload = json.loads(user)
    original_text = str(payload["original_text"])
    ingested_symbol = payload.get("ingested_symbol")
    symbols = [ingested_symbol] if isinstance(ingested_symbol, str) else []
    evidence = next(
        (line.strip() for line in original_text.splitlines() if line.strip()),
        original_text[:20],
    )
    session.add(
        LLMCall(
            purpose="p4_news_event_extract",
            model=settings.llm_model or "missing-test-model",
            latency_ms=1,
            ok=True,
            prompt_tokens=12,
            completion_tokens=8,
            error=None,
        )
    )
    session.flush()
    return {
        "symbols": symbols,
        "event_type": "other",
        "direction": 0,
        "materiality": 1,
        "summary": evidence[:100],
        "confidence": 0.8,
        "evidence_span": evidence[:500],
    }


def _fake_candidate_chat(
    purpose: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    *,
    timeout: float | None = None,
    max_tokens: int | None = None,
    max_retries: int = 1,
    settings: Settings | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    assert purpose == "p4_news_event_extract"
    assert system
    assert schema
    assert timeout == 20.0
    assert max_tokens == 2_000
    assert max_retries == 0
    assert settings is not None
    assert session is not None
    payload = json.loads(user)
    assert "original_text" not in payload
    candidates = payload["evidence_candidates"]
    assert isinstance(candidates, list) and candidates
    first = candidates[0]
    assert isinstance(first, list)
    ingested_symbol = payload.get("ingested_symbol")
    symbols = [ingested_symbol] if isinstance(ingested_symbol, str) else []
    session.add(
        LLMCall(
            purpose="p4_news_event_extract",
            model=settings.llm_model or "missing-test-model",
            latency_ms=1,
            ok=True,
            prompt_tokens=12,
            completion_tokens=8,
            error=None,
        )
    )
    session.flush()
    return {
        "symbols": symbols,
        "event_type": "other",
        "direction": 0,
        "materiality": 1,
        "summary": str(first[3])[:100],
        "confidence": 0.8,
        "evidence_candidate_id": first[0],
    }


def test_legacy_dev_runner_rejects_v2_before_contract_or_artifact_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    design = load_event_evaluation_design(EVALUATION_DESIGN_V2_PATH)
    monkeypatch.setattr(
        dev_runner.heldout,
        "_load_active_contract",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy active contract must not load")
        ),
    )

    with pytest.raises(
        dev_runner.DevIterationError,
        match="dedicated dev45 dual-model runner",
    ):
        dev_runner.run_dev_iteration(
            Path("config/must-not-load.yaml"),
            "v2-must-not-run",
            project_root=tmp_path,
            design=design,
        )

    assert not any(tmp_path.iterdir())


def test_dev_iteration_is_create_only_and_does_not_need_news_items(
    tmp_path: Path,
) -> None:
    _fixture_root(tmp_path)
    design = load_event_evaluation_design()
    result = dev_runner.run_dev_iteration(
        Path("config/p4_event_extract_eval_v1_1.yaml"),
        "v1.1-r1",
        project_root=tmp_path,
        design=design,
        settings=_settings(),
        clock=lambda: datetime(2026, 8, 4, 7, 0, tzinfo=UTC),
        chat_json_fn=_fake_chat,
    )

    assert result.summary.expected_count == 60
    assert result.summary.success_count == 60
    assert result.predictions_path.is_file()
    assert result.manifest_path.is_file()
    assert result.report_path.is_file()
    metrics = result.report["metrics"]
    assert metrics["metric_semantics"] == "model_interagreement"
    assert metrics["not_phase_gate"] is True
    assert metrics["development_ready_to_freeze"] is False
    assert result.report["formal_dev_round_valid"] is True
    comparison = result.report["flash_baseline_comparison"]
    assert comparison["baseline"]["materiality_positive"] == {
        "matches": 7,
        "denominator": 14,
        "agreement": 0.5,
    }
    assert comparison["baseline"]["symbol_exact_set"]["matches"] == 58
    assert comparison["baseline"]["symbol_exact_set"]["denominator"] == 59
    assert comparison["candidate"]["success_count"] == 60
    assert comparison["candidate"]["failure_count"] == 0
    assert comparison["interpretation"] == (
        "indicative_comparison_not_single_variable_causality"
    )
    assert result.report["runtime_evidence"]["successful_rows"] == 60
    assert result.report["heldout_accessed"] is False
    serialized = result.report_path.read_text(encoding="utf-8")
    assert "precision" not in serialized
    assert "human_gold" not in serialized
    assert "phase_pass" not in serialized

    with sqlite3.connect(tmp_path / "data/alphapilot.db") as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "news_items" not in tables

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        dev_runner.run_dev_iteration(
            Path("config/p4_event_extract_eval_v1_1.yaml"),
            "v1.1-r1",
            project_root=tmp_path,
            design=design,
            settings=_settings(),
            clock=lambda: datetime(2026, 8, 4, 7, 1, tzinfo=UTC),
            chat_json_fn=_fake_chat,
        )


def test_v1_4_dev_iteration_reports_preregistered_three_layer_evidence(
    tmp_path: Path,
) -> None:
    _fixture_root(tmp_path)
    design = load_event_evaluation_design(
        PROJECT_ROOT / "config/p4_event_evaluation_v1_3.yaml",
    )

    result = dev_runner.run_dev_iteration(
        Path("config/p4_event_extract_eval_v1_4.yaml"),
        "v1.4-r1",
        project_root=tmp_path,
        design=design,
        settings=_settings(
            model="qwen3.6-plus",
            endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        clock=lambda: datetime(2026, 8, 4, 11, 0, tzinfo=UTC),
        chat_json_fn=_fake_chat,
    )

    assert result.summary.success_count == 60
    assert result.summary.failure_count == 0
    assert result.report["formal_dev_round_valid"] is True
    assert result.report["prediction_contract"]["evidence_span_match_mode"] == (
        "unicode_whitespace_elided_contiguous_substring_v1"
    )
    evidence = result.report["evidence_validation"]
    assert evidence["v1_3_actual"]["failure_count"] == 7
    assert evidence["whitespace_normalized_counterfactual"]["failure_count"] == 2
    assert evidence["v1_4_actual"]["success_count"] == 60
    assert evidence["v1_4_actual"]["failure_count"] == 0
    assert evidence["v1_4_actual"][
        "failures_by_validation_field_and_constraint"
    ] == {}
    assert result.report["symbol_diagnostics"]["raw_gate"] == (
        result.report["metrics"]["symbol_exact_set"]
    )
    assert result.report["symbol_diagnostics"]["ai_label_defect_ids"] == [44]
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["request_contract"]["evidence_span_match_mode"] == (
        "unicode_whitespace_elided_contiguous_substring_v1"
    )
    assert result.report["heldout_accessed"] is False
    assert result.report["heldout_phase_unlocked"] is False


def test_v1_5_dev_iteration_reports_failed_round_and_fresh_layers(
    tmp_path: Path,
) -> None:
    _fixture_root(tmp_path)
    design = load_event_evaluation_design(
        PROJECT_ROOT / "config/p4_event_evaluation_v1_4.yaml",
    )

    result = dev_runner.run_dev_iteration(
        Path("config/p4_event_extract_eval_v1_5.yaml"),
        "v1.5-r1",
        project_root=tmp_path,
        design=design,
        settings=_settings(
            model="qwen3.6-plus",
            endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        clock=lambda: datetime(2026, 8, 4, 11, 0, tzinfo=UTC),
        chat_json_fn=_fake_chat,
    )

    evidence = result.report["evidence_validation"]
    assert evidence["v1_4_actual"]["historical_round_immutable"] is True
    assert evidence["v1_4_r1_actual"]["extraction"]["failure_ids"] == [
        253,
        258,
        280,
        304,
        336,
        340,
    ]
    assert evidence["v1_5_actual"]["success_count"] == 60
    assert evidence["v1_5_actual"]["failure_count"] == 0
    assert evidence["v1_5_legacy_exact_shadow"]["mismatch_count"] == 0
    diagnostics = result.report["symbol_diagnostics"]
    assert diagnostics["v1_4_r1_actual"][
        "current_model_under_attribution_ids"
    ] == [28, 67, 71, 96]
    assert "model_over_attribution_ids" not in diagnostics
    changed = result.report["flash_baseline_comparison"]["changed_dimensions"]
    assert "evidence_span_match_mode" in changed
    assert "validation_contract" in changed


def test_v1_5_required_report_field_drift_fails_before_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixture_root(tmp_path)
    design = load_event_evaluation_design(
        PROJECT_ROOT / "config/p4_event_evaluation_v1_4.yaml",
    )
    document = copy.deepcopy(design.document)
    required = document["evaluation"]["required_report_fields"]
    required.remove("evidence_validation.v1_5_actual")
    drifted = replace(design, document=document)
    calls: list[str] = []

    def forbidden_extract(*_args: object, **_kwargs: object) -> None:
        calls.append("called")

    monkeypatch.setattr(dev_runner, "extract_records", forbidden_extract)
    with pytest.raises(
        dev_runner.DevIterationError,
        match="required report fields are unrecognized",
    ):
        dev_runner.run_dev_iteration(
            Path("config/p4_event_extract_eval_v1_5.yaml"),
            "v1.5-preflight",
            project_root=tmp_path,
            design=drifted,
            settings=_settings(
                model="qwen3.6-plus",
                endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            clock=lambda: datetime(2026, 8, 4, 11, 0, tzinfo=UTC),
            chat_json_fn=_fake_chat,
        )
    assert calls == []


def test_v1_5_rejects_wrong_round_namespace_before_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixture_root(tmp_path)
    design = load_event_evaluation_design(
        PROJECT_ROOT / "config/p4_event_evaluation_v1_4.yaml",
    )
    calls: list[str] = []

    def forbidden_extract(*_args: object, **_kwargs: object) -> None:
        calls.append("called")

    monkeypatch.setattr(dev_runner, "extract_records", forbidden_extract)
    with pytest.raises(
        dev_runner.DevIterationError,
        match=r"requires a v1\.5-\* round_id",
    ):
        dev_runner.run_dev_iteration(
            Path("config/p4_event_extract_eval_v1_5.yaml"),
            "v1.4-r2",
            project_root=tmp_path,
            design=design,
            settings=_settings(
                model="qwen3.6-plus",
                endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            clock=lambda: datetime(2026, 8, 4, 11, 0, tzinfo=UTC),
            chat_json_fn=_fake_chat,
        )
    assert calls == []


def test_v1_6_dev_iteration_reports_candidate_materialization_and_dual_identity(
    tmp_path: Path,
) -> None:
    _fixture_root(tmp_path)
    loaded = load_event_evaluation_design(
        PROJECT_ROOT / "config/p4_event_evaluation_v1_5.yaml",
    )
    design = replace(
        loaded,
        prediction_contract=replace(
            loaded.prediction_contract,
            path=tmp_path / "config/p4_event_extract_eval_v1_6.yaml",
        ),
    )

    result = dev_runner.run_dev_iteration(
        Path("config/p4_event_extract_eval_v1_6.yaml"),
        "v1.6-r1",
        project_root=tmp_path,
        design=design,
        settings=_settings(
            model="qwen3.6-plus",
            endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
        chat_json_fn=_fake_candidate_chat,
    )

    assert result.summary.success_count == 60
    assert result.summary.failure_count == 0
    report = result.report
    contract = report["prediction_contract"]
    assert contract["input_representation"]["name"] == (
        "ordered_evidence_candidates_v1"
    )
    assert contract["model_result_schema"]["sha256"] == (
        "c106cd15bd974de19ecc01d6e99e8f39c39fbf14df3a3b4dc74ee9b08ff6dd66"
    )
    assert contract["materialized_result_schema"]["sha256"] == (
        "0ac68654ce23ecd4e537d849d695e092c76dcb9de0fb03793e65ae62b181947f"
    )
    assert contract["candidate_materialization"]["materialization"] == (
        "exact_raw_slice_from_registered_start_end"
    )
    identity = report["input_identity"]
    assert identity["declared_frozen_input_sha256"]["row_count"] == 60
    assert identity["active_model_input_sha256"]["row_count"] == 60
    assert identity["dual_hash_identity"]["rows_with_both"] == 60
    assert identity["dual_hash_identity"]["distinct_hash_pair_count"] == 60
    evidence = report["evidence_validation"]
    assert evidence["v1_4_r1_actual"]["historical_round_immutable"] is True
    assert evidence["v1_5_actual"]["failure_count"] == 10
    assert evidence["v1_5_legacy_exact_shadow"]["mismatch_count"] == 12
    assert evidence["v1_5_r1_actual"]["extraction"]["gold_intersection_failure_ids"] == [
        272,
        304,
        306,
        336,
    ]
    assert evidence["v1_6_actual"]["success_count"] == 60
    assert report["symbol_diagnostics"]["v1_4_r1_actual"][
        "ai_label_defect_ids"
    ] == [44]
    assert report["symbol_diagnostics"]["v1_5_r1_actual"][
        "current_model_over_attribution_ids"
    ] == [393]
    disclosure = report["comparison_disclosure"]
    assert disclosure["causal_reading_forbidden"] is True
    assert {
        "model_input_representation",
        "model_result_schema",
        "candidate_materialization",
        "input_identity_contract",
    }.issubset(disclosure["changed_dimensions"])

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["model_result_schema"] == contract["model_result_schema"]
    assert manifest["materialized_result_schema"] == (
        contract["materialized_result_schema"]
    )
    assert manifest["input_identity"] == identity


def test_v1_6_rejects_wrong_round_namespace_before_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixture_root(tmp_path)
    design = load_event_evaluation_design(
        PROJECT_ROOT / "config/p4_event_evaluation_v1_5.yaml",
    )
    calls: list[str] = []

    def forbidden_extract(*_args: object, **_kwargs: object) -> None:
        calls.append("called")

    monkeypatch.setattr(dev_runner, "extract_records", forbidden_extract)
    with pytest.raises(
        dev_runner.DevIterationError,
        match=r"requires a v1\.6-\* round_id",
    ):
        dev_runner.run_dev_iteration(
            Path("config/p4_event_extract_eval_v1_6.yaml"),
            "v1.5-r2",
            project_root=PROJECT_ROOT,
            design=design,
            settings=_settings(
                model="qwen3.6-plus",
                endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
            clock=lambda: datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
            chat_json_fn=_fake_candidate_chat,
        )
    assert calls == []


def test_v1_7_accepts_only_the_single_preregistered_round() -> None:
    design = load_event_evaluation_design(
        PROJECT_ROOT / "config/p4_event_evaluation_v1_6.yaml"
    )
    contract = load_event_extract_contract(
        PROJECT_ROOT / "config/p4_event_extract_eval_v1_7.yaml"
    )

    dev_runner._validate_versioned_dev_contract_preflight(
        design,
        contract,
        "v1.7-r1",
    )
    for disallowed in ("v1.7-r2", "v1.7-retry", "v1.8-r1"):
        with pytest.raises(
            dev_runner.DevIterationError,
            match=r"one official v1\.7-r1 round",
        ):
            dev_runner._validate_versioned_dev_contract_preflight(
                design,
                contract,
                disallowed,
            )


@pytest.mark.parametrize(
    "design_path",
    [
        PROJECT_ROOT / "config/p4_event_evaluation_v1_7.yaml",
        PROJECT_ROOT / "config/p4_event_evaluation_v1_8.yaml",
    ],
)
def test_report_contract_binding_is_declared_by_v1_7_or_v1_8_design(
    design_path: Path,
) -> None:
    design = load_event_evaluation_design(design_path)

    dev_runner._require_declared_active_contract(
        design,
        design.prediction_contract,
    )
    summary = ExtractionSummary(
        expected_count=1,
        success_count=1,
        failure_count=0,
        newly_attempted_count=1,
        retried_failure_count=0,
        skipped_exact_success_count=0,
        skipped_failure_count=0,
        output_line_count=1,
        failures_by_reason={},
        failures_by_validation_field_and_constraint={},
        isolated_audit_tables=("llm_calls",),
        isolated_audit_row_count=1,
        checkpoint_audited_success_count=1,
    )

    evidence = dev_runner._evidence_validation(
        design=design,
        active_contract=design.prediction_contract,
        summary=summary,
        prediction_rows=[
            {
                "news_item_id": 999,
                "status": "ok",
                "prediction": {"evidence_span": "连续逐字证据"},
            }
        ],
        labels={999: {"original_text": "连续逐字证据"}},
    )

    assert evidence["v1_7_actual"]["success_count"] == 1
    assert evidence["v1_7_actual"]["failure_count"] == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("path", PROJECT_ROOT / "config/p4_event_extract_eval_v1_6.yaml"),
        ("sha256", "0" * 64),
        ("schema_version", "p4.2a-event-extract-eval-v9.9"),
        ("model", "qwen3.6-plus"),
        ("endpoint", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
    ],
)
@pytest.mark.parametrize(
    "design_path",
    [
        PROJECT_ROOT / "config/p4_event_evaluation_v1_7.yaml",
        PROJECT_ROOT / "config/p4_event_evaluation_v1_8.yaml",
    ],
)
def test_report_contract_binding_rejects_identity_drift(
    design_path: Path,
    field: str,
    value: object,
) -> None:
    design = load_event_evaluation_design(design_path)
    contract = design.prediction_contract
    if field == "schema_version":
        document = copy.deepcopy(contract.document)
        document["schema_version"] = value
        drifted = replace(contract, document=document)
    else:
        drifted = replace(contract, **{field: value})

    with pytest.raises(
        dev_runner.DevIterationError,
        match="design/contract binding drifted",
    ):
        dev_runner._require_declared_active_contract(design, drifted)


def test_dev_iteration_rejects_ai_label_byte_drift(tmp_path: Path) -> None:
    _fixture_root(tmp_path)
    labels = (
        tmp_path
        / "docs/phase4/eval/P4.2a-gold-inventory60-v1.labels-ai-drafted.jsonl"
    )
    labels.write_bytes(labels.read_bytes() + b"\n")

    with pytest.raises(dev_runner.DevIterationError, match="frozen SHA-256"):
        dev_runner.run_dev_iteration(
            Path("config/p4_event_extract_eval_v1_1.yaml"),
            "v1.1-r1",
            project_root=tmp_path,
            design=load_event_evaluation_design(),
            settings=_settings(),
            clock=lambda: datetime(2026, 8, 4, 7, 0, tzinfo=UTC),
            chat_json_fn=_fake_chat,
        )


def test_dev_iteration_is_locked_after_any_heldout_artifact(tmp_path: Path) -> None:
    _fixture_root(tmp_path)
    heldout = (
        tmp_path
        / "docs/phase4/eval/P4.2a-heldout-candidate-inputs-v1.1.jsonl"
    )
    heldout.write_text("{}\n", encoding="utf-8")

    with pytest.raises(Exception, match="locked after heldout artifact creation"):
        dev_runner.run_dev_iteration(
            Path("config/p4_event_extract_eval_v1_1.yaml"),
            "v1.1-r1",
            project_root=tmp_path,
            design=load_event_evaluation_design(),
            settings=_settings(),
            clock=lambda: datetime(2026, 8, 4, 7, 0, tzinfo=UTC),
            chat_json_fn=_fake_chat,
        )


def test_failed_reference_positive_is_not_hidden_by_comparable_metrics() -> None:
    labels: dict[int, dict[str, Any]] = {
        1: {"gold": {"materiality": 3, "symbols": ["600519"]}},
        2: {"gold": {"materiality": 1, "symbols": []}},
    }
    predictions = [
        {"news_item_id": 1, "source": "cninfo", "status": "extract_failed"},
        {
            "news_item_id": 2,
            "source": "akshare_ths",
            "status": "ok",
            "prediction": {"materiality": 1, "symbols": []},
        },
    ]

    metrics = dev_runner._score_predictions(predictions, labels)

    assert metrics["failed_reference_positive_count"] == 1
    assert metrics["failed_reference_positive_ids"] == [1]
    assert metrics["development_ready_to_freeze"] is False
    assert "active_failures_present" in metrics["development_blockers"]
    assert "failed_reference_positive_items" in metrics["development_blockers"]
    assert (
        metrics["materiality_positive"]["comparable_positive_capture"]
        is None
    )


def test_v1_4_report_keeps_three_evidence_layers_and_exact_shadow() -> None:
    design = load_event_evaluation_design(
        PROJECT_ROOT / "config/p4_event_evaluation_v1_3.yaml"
    )
    labels = {
        250: {"original_text": "公司公告：本次\n回购股份。"},
        999: {"original_text": "连续逐字证据"},
    }
    predictions = [
        {
            "news_item_id": 250,
            "status": "ok",
            "prediction": {"evidence_span": "本次回购"},
        },
        {
            "news_item_id": 999,
            "status": "ok",
            "prediction": {"evidence_span": "连续逐字证据"},
        },
    ]
    summary = ExtractionSummary(
        expected_count=2,
        success_count=2,
        failure_count=0,
        newly_attempted_count=2,
        retried_failure_count=0,
        skipped_exact_success_count=0,
        skipped_failure_count=0,
        output_line_count=2,
        failures_by_reason={},
        failures_by_validation_field_and_constraint={},
        isolated_audit_tables=("llm_calls",),
        isolated_audit_row_count=2,
        checkpoint_audited_success_count=2,
    )

    evidence = dev_runner._evidence_validation(
        design=design,
        active_contract=design.prediction_contract,
        summary=summary,
        prediction_rows=predictions,
        labels=labels,
    )

    assert evidence["v1_3_actual"]["success_count"] == 53
    assert evidence["v1_3_actual"]["failure_count"] == 7
    assert evidence["v1_3_actual"]["historical_round_immutable"] is True
    assert evidence["v1_3_actual"]["persisted_failure_detail"] == {
        "reason": "post_validation_failed",
        "field": None,
        "constraint": None,
        "count": 7,
    }
    counterfactual = evidence["whitespace_normalized_counterfactual"]
    assert counterfactual["success_count"] == 58
    assert counterfactual["normalization_recovered_ids"] == [
        250,
        258,
        287,
        306,
        358,
    ]
    assert counterfactual["true_synthesis_failure_ids"] == [304, 336]
    assert counterfactual["not_a_rewrite_of_v1_3"] is True
    assert counterfactual["reviewer_adjudicated_root_cause"] == {
        "evidence_source": "independent_reviewer_external_reproduction",
        "field": "evidence_span",
        "prior_constraint": "exact_contiguous_substring",
        "affected_count": 7,
    }
    assert evidence["v1_4_actual"]["evidence_span_match_mode"] == (
        "unicode_whitespace_elided_contiguous_substring_v1"
    )
    shadow = evidence["v1_4_legacy_exact_shadow"]
    assert shadow["mismatch_ids"] == [250]
    assert shadow["whitespace_matcher_recovered_ids"] == [250]


def test_v1_4_symbol_diagnostic_keeps_raw_gate_and_excludes_only_id44() -> None:
    design = load_event_evaluation_design(
        PROJECT_ROOT / "config/p4_event_evaluation_v1_3.yaml"
    )
    labels: dict[int, dict[str, Any]] = {
        44: {"gold": {"symbols": ["000044"]}},
        75: {"gold": {"symbols": []}},
        100: {"gold": {"symbols": ["000100"]}},
    }
    predictions = [
        {
            "news_item_id": 44,
            "status": "ok",
            "prediction": {"symbols": []},
        },
        {
            "news_item_id": 75,
            "status": "ok",
            "prediction": {"symbols": ["000075"]},
        },
        {
            "news_item_id": 100,
            "status": "ok",
            "prediction": {"symbols": ["000100"]},
        },
    ]
    raw = {
        "matches": 1,
        "denominator": 3,
        "agreement": 1 / 3,
        "mismatch_ids": [44, 75],
    }

    diagnostics = dev_runner._symbol_diagnostics(
        design=design,
        metrics={"symbol_exact_set": raw},
        prediction_rows=predictions,
        labels=labels,
    )

    assert diagnostics["raw_gate"] == raw
    assert diagnostics["raw_gate_uses_frozen_ai_labels_unchanged"] is True
    assert diagnostics["ai_label_defect_ids"] == [44]
    assert diagnostics["model_over_attribution_ids"] == [75, 210, 232, 393]
    assert diagnostics["adjusted_exact_set"] == {
        "diagnostic_only": True,
        "not_a_gate": True,
        "excluded_ai_label_defect_ids": [44],
        "matches": 1,
        "denominator": 2,
        "agreement": 0.5,
        "mismatch_ids": [75],
    }
