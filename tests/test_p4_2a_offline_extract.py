from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from scripts import run_p4_2a_offline_extract as runner
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.db.models import LLMCall
from alphapilot.llm.client import LLMUnavailable
from alphapilot.llm.p4_news_event import (
    EventExtractContract,
    build_event_extract_user_input,
    event_extract_input_sha256,
    load_event_extract_contract,
)

FROZEN_MAX_AVAILABLE_TIME = "2026-08-03 02:10:09.075785"


def _settings() -> Settings:
    return Settings(
        llm_base_url="https://llm.example.test/compatible-mode/v1",
        llm_api_key="test-only-key",
        llm_model="qwen3.6-flash",
    )


def _plus_settings(
    *,
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    model: str = "qwen3.6-plus",
    purpose_models: dict[str, str] | None = None,
) -> Settings:
    return Settings(
        llm_base_url=base_url,
        llm_api_key="test-only-key",
        llm_model=model,
        llm_purpose_models=purpose_models or {},
    )


def test_runtime_contract_uses_frozen_model_and_mainland_endpoint() -> None:
    contract = load_event_extract_contract(
        Path("config/p4_event_extract_eval_v1_3.yaml")
    )

    runner._validate_runtime_contract(contract, _plus_settings())

    with pytest.raises(runner.OfflineExtractError, match="purpose model differs"):
        runner._validate_runtime_contract(
            contract,
            _plus_settings(model="qwen3.6-flash"),
        )
    with pytest.raises(runner.OfflineExtractError, match="purpose model differs"):
        runner._validate_runtime_contract(
            contract,
            _plus_settings(
                purpose_models={"p4_news_event_extract": "qwen3.6-flash"}
            ),
        )
    with pytest.raises(runner.OfflineExtractError, match="endpoint differs"):
        runner._validate_runtime_contract(
            contract,
            _plus_settings(
                base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
            ),
        )


def _contract_for_root(root: Path) -> EventExtractContract:
    contract = load_event_extract_contract()
    path = root / "config" / "p4_event_extract_eval_v1.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contract.path.read_bytes())
    return replace(contract, path=path)


def _create_production_fixture(root: Path) -> Path:
    data = root / "data"
    data.mkdir(parents=True)
    database = data / "alphapilot.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE news_items (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                symbol TEXT,
                title TEXT NOT NULL,
                published_at TEXT,
                available_time TEXT NOT NULL,
                raw_payload TEXT
            );
            CREATE TABLE securities (
                symbol TEXT PRIMARY KEY
            );
            INSERT INTO securities(symbol) VALUES ('600519');
            """
        )
        connection.executemany(
            """
            INSERT INTO news_items(
                id, source, symbol, title, published_at, available_time, raw_payload
            ) VALUES (?, 'akshare_ths', '600519', ?, ?, ?, ?)
            """,
            [
                (
                    news_item_id,
                    f"第{news_item_id}号公告",
                    "2026-08-03 01:00:00",
                    FROZEN_MAX_AVAILABLE_TIME,
                    json.dumps(
                        {
                            "digest": f"第{news_item_id}号摘要",
                            "short": f"第{news_item_id}号摘要",
                        },
                        ensure_ascii=False,
                    ),
                )
                for news_item_id in range(1, 424)
            ],
        )
    return database


def _record_audit(
    session: Session | None,
    *,
    ok: bool,
    error: str | None,
) -> None:
    assert session is not None
    session.add(
        LLMCall(
            purpose="p4_news_event_extract",
            model="qwen3.6-flash",
            latency_ms=1,
            ok=ok,
            prompt_tokens=12,
            completion_tokens=8,
            error=error,
        )
    )
    session.flush()


def _fake_chat(
    *,
    failed_ids: set[int] | None = None,
    record_audit: bool = True,
) -> Callable[..., dict[str, Any]]:
    failures = failed_ids or set()

    def fake(
        _purpose: str,
        _system: str,
        user: str,
        _schema: dict[str, Any],
        *,
        timeout: float | None = None,
        max_tokens: int | None = None,
        max_retries: int = 1,
        settings: Settings | None = None,
        session: Session | None = None,
    ) -> dict[str, Any]:
        assert timeout == 20.0
        assert max_tokens == 2_000
        assert max_retries == 0
        assert settings is not None
        payload = json.loads(user)
        news_item_id = int(payload["news_item_id"])
        if news_item_id in failures:
            if record_audit:
                _record_audit(session, ok=False, error="request_timeout")
            raise LLMUnavailable("raw provider detail must not persist")
        if record_audit:
            _record_audit(session, ok=True, error=None)
        return {
            "symbols": ["600519"],
            "event_type": "other",
            "direction": 0,
            "materiality": 0,
            "summary": "公告内容已完成结构化抽取。",
            "confidence": 0.8,
            "evidence_span": payload["title"],
        }

    return fake


def _one_record() -> runner.ExtractRecord:
    return runner.ExtractRecord(
        news_item_id=1,
        source="cninfo",
        ingested_symbol="600519",
        title="公司公告",
        original_text="公司公告披露重大事项。",
        published_at="2026-08-03T01:00:00+00:00",
        available_time="2026-08-03T01:01:00+00:00",
        body_state="announcement_body",
    )


def test_production_database_is_read_only_and_frozen_max_time_is_enforced(
    tmp_path: Path,
) -> None:
    contract = _contract_for_root(tmp_path)
    database = _create_production_fixture(tmp_path)

    records, universe, evidence = runner._read_production_inputs(
        contract,
        tmp_path,
        include_inventory=True,
    )

    assert len(records) == 423
    assert universe == frozenset({"600519"})
    assert evidence.sqlite_uri_mode == "ro"
    assert evidence.pragma_query_only == 1
    assert evidence.total_changes == 0
    with (
        runner._open_production_database(database) as connection,
        pytest.raises(sqlite3.OperationalError),
    ):
        connection.execute("INSERT INTO securities(symbol) VALUES ('000001')")

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE news_items SET available_time=? WHERE id=423",
            ("2026-08-03 02:10:10.000000",),
        )
    with pytest.raises(runner.OfflineExtractError, match="max available_time drifted"):
        runner._read_production_inputs(
            contract,
            tmp_path,
            include_inventory=True,
        )


def test_ths_original_text_follows_contract_and_deduplicates_equal_fields() -> None:
    contract = load_event_extract_contract()

    duplicate_text, duplicate_state = runner._offline_original_text(
        contract,
        "akshare_ths",
        "标题",
        {"digest": "摘要", "short": "摘要"},
    )
    distinct_text, distinct_state = runner._offline_original_text(
        contract,
        "akshare_ths",
        "标题",
        {"digest": "摘要", "short": "短讯"},
    )

    assert duplicate_text == "标题\n摘要"
    assert duplicate_state == "title_digest"
    assert distinct_text == "标题\n摘要\n短讯"
    assert distinct_state == "title_digest_short"


def test_failed_first_run_keeps_report_open_for_explicit_retry(tmp_path: Path) -> None:
    contract = _contract_for_root(tmp_path)
    database = _create_production_fixture(tmp_path)
    paths = runner._artifact_paths(contract, tmp_path)

    first = runner.run_offline_extract(
        project_root=tmp_path,
        contract=contract,
        settings=_settings(),
        chat_json_fn=_fake_chat(failed_ids={1}),
    )

    assert first.success_count == 422
    assert first.failure_count == 1
    assert not paths.offline_report.exists()

    second = runner.run_offline_extract(
        project_root=tmp_path,
        contract=contract,
        settings=_settings(),
        retry_failures=True,
        chat_json_fn=_fake_chat(),
    )

    assert second.success_count == 423
    assert second.failure_count == 0
    assert second.retried_failure_count == 1
    assert second.skipped_exact_success_count == 422
    assert second.checkpoint_audited_success_count == 423
    assert paths.offline_report.is_file()
    report = json.loads(paths.offline_report.read_text(encoding="utf-8"))
    assert report["coverage"]["failure_count"] == 0
    assert report["isolated_llm_audit"]["current_process_llm_call_rows"] == 1
    assert (
        report["isolated_llm_audit"][
            "checkpoint_success_rows_with_recorded_audit"
        ]
        == 423
    )
    assert report["isolated_llm_audit"]["checkpoint_success_evidence_check"] == "423/423"

    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert tables == {"news_items", "securities"}
        assert connection.execute("SELECT COUNT(*) FROM news_items").fetchone() == (423,)


def test_non_retryable_failures_can_be_frozen_without_new_llm_calls(
    tmp_path: Path,
) -> None:
    contract = _contract_for_root(tmp_path)
    _create_production_fixture(tmp_path)
    paths = runner._artifact_paths(contract, tmp_path)

    first = runner.run_offline_extract(
        project_root=tmp_path,
        contract=contract,
        settings=_settings(),
        chat_json_fn=_fake_chat(failed_ids={1}),
    )
    assert first.failure_count == 1
    assert not paths.offline_report.exists()

    def must_not_call(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("terminal report finalization must not call the LLM")

    terminal = runner.run_offline_extract(
        project_root=tmp_path,
        contract=contract,
        settings=_settings(),
        finalize_report_with_failures=True,
        chat_json_fn=must_not_call,
    )

    assert terminal.success_count == 422
    assert terminal.failure_count == 1
    assert terminal.newly_attempted_count == 0
    assert terminal.skipped_exact_success_count == 422
    assert terminal.skipped_failure_count == 1
    report = json.loads(paths.offline_report.read_text(encoding="utf-8"))
    assert report["trial_outcome"] == "completed_with_failures"
    assert report["coverage"]["failure_count"] == 1
    assert report["isolated_llm_audit"]["current_process_llm_call_rows"] == 0
    assert report["isolated_llm_audit"]["table_check"] == "1/1"
    assert report["isolated_llm_audit"]["checkpoint_success_evidence_check"] == "422/422"


def test_total_deadline_is_fail_closed_after_model_returns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract_for_root(tmp_path)
    ticks = iter((0, 20_000_000_001, 20_000_000_002))
    monkeypatch.setattr(runner, "monotonic_ns", lambda: next(ticks))
    output = tmp_path / "eval" / "deadline.jsonl"

    summary = runner.extract_records(
        contract,
        [_one_record()],
        output_path=output,
        eval_root=tmp_path / "eval",
        universe_symbols={"600519"},
        settings=_settings(),
        retry_failures=False,
        chat_json_fn=_fake_chat(),
    )

    assert summary.success_count == 0
    assert summary.failure_count == 1
    row = json.loads(output.read_text(encoding="utf-8"))
    assert row["status"] == "extract_failed"
    assert row["error"] == "total_deadline_exceeded"
    assert row["prediction"] is None
    assert row["security"]["llm_audit_status"] == "recorded"


def test_post_validation_failure_persists_only_safe_field_and_constraint(
    tmp_path: Path,
) -> None:
    contract = load_event_extract_contract()
    output = tmp_path / "eval" / "post-validation.jsonl"

    def invalid_evidence_chat(
        _purpose: str,
        _system: str,
        _user: str,
        _schema: dict[str, Any],
        *,
        timeout: float | None = None,
        max_tokens: int | None = None,
        max_retries: int = 1,
        settings: Settings | None = None,
        session: Session | None = None,
    ) -> dict[str, Any]:
        assert timeout == 20.0
        assert max_tokens == 2_000
        assert max_retries == 0
        assert settings is not None
        _record_audit(session, ok=True, error=None)
        return {
            "symbols": ["600519"],
            "event_type": "other",
            "direction": 0,
            "materiality": 1,
            "summary": "公告完成结构化抽取。",
            "confidence": 0.8,
            "evidence_span": "raw model text must never persist",
        }

    summary = runner.extract_records(
        contract,
        [_one_record()],
        output_path=output,
        eval_root=tmp_path / "eval",
        universe_symbols={"600519"},
        settings=_settings(),
        retry_failures=False,
        chat_json_fn=invalid_evidence_chat,
    )

    assert summary.failure_count == 1
    assert summary.failures_by_validation_field_and_constraint == {
        "evidence_span": {"exact_contiguous_substring": 1}
    }
    row = json.loads(output.read_text(encoding="utf-8"))
    assert row["error"] == "post_validation_failed"
    assert row["extract_failed"] == {
        "reason": "post_validation_failed",
        "retryable": False,
        "field": "evidence_span",
        "constraint": "exact_contiguous_substring",
    }
    serialized = json.dumps(row, ensure_ascii=False)
    assert "raw model text must never persist" not in serialized
    assert row["security"]["exception_detail_persisted"] is False
    assert row["security"]["raw_transport_response_persisted"] is False


def test_success_without_isolated_audit_is_rejected(tmp_path: Path) -> None:
    contract = load_event_extract_contract()
    output = tmp_path / "eval" / "missing-audit.jsonl"

    summary = runner.extract_records(
        contract,
        [_one_record()],
        output_path=output,
        eval_root=tmp_path / "eval",
        universe_symbols={"600519"},
        settings=_settings(),
        retry_failures=False,
        chat_json_fn=_fake_chat(record_audit=False),
    )

    assert summary.success_count == 0
    row = json.loads(output.read_text(encoding="utf-8"))
    assert row["status"] == "extract_failed"
    assert row["error"] == "audit_evidence_missing"
    assert row["security"]["llm_audit_status"] == "not_recorded"


def test_resumed_success_requires_strict_security_and_audit_evidence(
    tmp_path: Path,
) -> None:
    contract = load_event_extract_contract()
    output = tmp_path / "eval" / "resume.jsonl"
    record = _one_record()
    runner.extract_records(
        contract,
        [record],
        output_path=output,
        eval_root=tmp_path / "eval",
        universe_symbols={"600519"},
        settings=_settings(),
        retry_failures=False,
        chat_json_fn=_fake_chat(),
    )
    row = json.loads(output.read_text(encoding="utf-8"))
    row["security"]["llm_audit_status"] = "not_recorded"
    output.write_text(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(runner.OfflineExtractError, match="strict in-memory audit"):
        runner.extract_records(
            contract,
            [record],
            output_path=output,
            eval_root=tmp_path / "eval",
            universe_symbols={"600519"},
            settings=_settings(),
            retry_failures=False,
            chat_json_fn=_fake_chat(),
        )


def test_report_exposes_checkpoint_audit_evidence_when_current_rows_are_zero(
    tmp_path: Path,
) -> None:
    contract = _contract_for_root(tmp_path)
    paths = runner._artifact_paths(contract, tmp_path)
    database = runner.ProductionDatabaseEvidence(
        relative_path="data/alphapilot.db",
        sqlite_uri_mode="ro",
        pragma_query_only=1,
        total_changes=0,
        required_tables_found=("news_items", "securities"),
    )
    summary = runner.ExtractionSummary(
        expected_count=1,
        success_count=1,
        failure_count=0,
        newly_attempted_count=0,
        retried_failure_count=0,
        skipped_exact_success_count=1,
        skipped_failure_count=0,
        output_line_count=1,
        failures_by_reason={},
        failures_by_validation_field_and_constraint={},
        isolated_audit_tables=("llm_calls",),
        isolated_audit_row_count=0,
        checkpoint_audited_success_count=1,
    )

    report = runner._offline_report(
        contract,
        paths,
        tmp_path,
        summary,
        database,
    )

    audit = report["isolated_llm_audit"]
    assert audit["current_process_llm_call_rows"] == 0
    assert audit["checkpoint_success_evidence_check"] == "1/1"
    assert audit["table_check"] == "1/1"


def test_gold_loader_does_not_send_owner_labels_to_model(tmp_path: Path) -> None:
    contract = load_event_extract_contract()
    original_text = "公司公告披露重大事项。"
    user_json = build_event_extract_user_input(
        contract,
        news_item_id=7,
        source="cninfo",
        ingested_symbol="600519",
        title="公司公告",
        original_text=original_text,
        published_at="2026-08-03T01:00:00+00:00",
        available_time="2026-08-03T01:01:00+00:00",
        body_state="announcement_body",
    )
    row = {
        "news_item_id": 7,
        "source": "cninfo",
        "ingested_symbol": "600519",
        "title": "公司公告",
        "original_text": original_text,
        "published_at": "2026-08-03T01:00:00+00:00",
        "available_time": "2026-08-03T01:01:00+00:00",
        "body_state": "announcement_body",
        "input_sha256": event_extract_input_sha256(user_json),
        "text_sha256": runner._sha256_text(original_text),
        "gold": {
            "event_type": "regulatory_action",
            "materiality": 3,
            "notes": "owner-only-secret-label",
        },
    }
    path = tmp_path / "gold.jsonl"
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    records = runner._load_gold_records(path, 1)
    prepared = runner._prepare_records(contract, records)
    model_input = json.loads(prepared[0].user_json)

    assert set(model_input) == {
        "available_time",
        "body_state",
        "ingested_symbol",
        "news_item_id",
        "original_text",
        "published_at",
        "source",
        "title",
    }
    assert "gold" not in model_input
    assert "owner-only-secret-label" not in prepared[0].user_json


def test_cli_redacts_fatal_errors_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(**_kwargs: object) -> runner.ExtractionSummary:
        raise runner.OfflineExtractError("credential-like-secret")

    monkeypatch.setattr(runner, "run_offline_extract", fail)

    assert runner.main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "offline_extract_safety_gate_failed" in captured.err
    assert "credential-like-secret" not in captured.err
    assert "Traceback" not in captured.err
