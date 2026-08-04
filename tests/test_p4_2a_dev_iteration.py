from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from scripts import run_p4_2a_dev_iteration as dev_runner
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.db.models import LLMCall
from alphapilot.llm.p4_news_eval import load_event_evaluation_design

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
        "config/prompts/p4_news_event_extract_v1.txt",
        "config/prompts/p4_news_event_extract_v1_1.txt",
        "config/schemas/p4_news_event_v1.schema.json",
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


def _settings() -> Settings:
    return Settings(
        trading_mode="research",
        live_trading_enabled=False,
        paper_auto_trading_enabled=False,
        futu_enable_account_mutation=False,
        futu_enable_trade=False,
        llm_base_url="https://llm.example.test/compatible-mode/v1",
        llm_api_key="test-only-key",
        llm_model="qwen3.6-flash",
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
            model="qwen3.6-flash",
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
    labels = {
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
