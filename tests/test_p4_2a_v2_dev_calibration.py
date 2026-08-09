from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from scripts import run_p4_2a_v2_dev_calibration as runner
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.db.models import LLMCall
from alphapilot.llm.client import LLMUnavailable

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _copy(root: Path, relative: str) -> None:
    source = PROJECT_ROOT / relative
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())


def _fixture_root(tmp_path: Path) -> Path:
    files = (
        "config/p4_event_evaluation_v2.yaml",
        "config/p4_event_extract_eval_v1_7.yaml",
        "config/p4_event_extract_eval_v2-r1-qwen3.7-flash.yaml",
        "config/p4_event_extract_eval_v2-r1-qwen3.6-plus.yaml",
        "config/prompts/p4_news_event_extract_v1_5.txt",
        "config/prompts/p4_news_event_extract_v2-r1.txt",
        "config/schemas/p4_news_event_candidate_v1.schema.json",
        "config/schemas/p4_news_event_v1.schema.json",
        "docs/phase4/reports/P4.2a-v2-calibration-round-preregistration-20260809.json",
        "docs/phase4/reports/P4.2a-v2-calibration-design-clarifications-20260809.json",
        "docs/phase4/reports/P4.2a-dev45-owner-adjudication-independent-review-20260809.json",
        "docs/phase4/eval/v2-calibration/development/P4.2a-development-frame-v2.owner-export.jsonl",
        "docs/phase4/eval/v2-calibration/development/"
        "P4.2a-development-frame-v2.human-adjudicated.jsonl",
        "docs/phase4/eval/v2-calibration/development/"
        "P4.2a-development-frame-v2.owner-completion.json",
        "docs/phase4/eval/v2-calibration/development/P4.2a-development-frame-v2.selection.json",
        "docs/phase4/eval/v2-calibration/development/rounds/r1/round-preregistration.json",
    )
    for relative in files:
        _copy(tmp_path, relative)

    gold_path = (
        tmp_path / "docs/phase4/eval/v2-calibration/development/"
        "P4.2a-development-frame-v2.human-adjudicated.jsonl"
    )
    symbols: set[str] = set()
    for line in gold_path.read_text(encoding="utf-8").splitlines():
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
            CREATE TABLE llm_calls (id INTEGER PRIMARY KEY);
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
    (tmp_path / ".env").write_text(
        "\n".join(
            (
                "ALPHAPILOT_TRADING_MODE=research",
                "ALPHAPILOT_LIVE_TRADING_ENABLED=false",
                "ALPHAPILOT_PAPER_TRADING_ENABLED=false",
                "ALPHAPILOT_PAPER_AUTO_TRADING_ENABLED=false",
                "ALPHAPILOT_FUTU_ENABLE_ACCOUNT_MUTATION=false",
                "ALPHAPILOT_FUTU_ENABLE_TRADE=false",
                "ALPHAPILOT_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1",
                "ALPHAPILOT_LLM_API_KEY=test-only-key",
                "ALPHAPILOT_LLM_MODEL=qwen3.6-plus",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


def _settings() -> Settings:
    return Settings(
        trading_mode="research",
        live_trading_enabled=False,
        paper_trading_enabled=False,
        paper_auto_trading_enabled=False,
        futu_enable_account_mutation=False,
        futu_enable_trade=False,
        llm_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        llm_api_key="test-only-key",
        llm_model="qwen3.6-plus",
    )


def _gold(root: Path) -> dict[int, dict[str, Any]]:
    path = (
        root / "docs/phase4/eval/v2-calibration/development/"
        "P4.2a-development-frame-v2.human-adjudicated.jsonl"
    )
    return {
        int(row["news_item_id"]): row["gold"]
        for row in (json.loads(line) for line in path.read_text().splitlines())
    }


def _record_audit(session: Session | None, *, model: str, ok: bool) -> None:
    assert session is not None
    session.add(
        LLMCall(
            purpose="p4_news_event_extract",
            model=model,
            latency_ms=1,
            ok=ok,
            prompt_tokens=12,
            completion_tokens=8,
            error=None if ok else "request_timeout",
        )
    )
    session.flush()


def _fake_chat(
    root: Path,
    calls: list[tuple[str, int]],
    *,
    fail_flash: bool = False,
) -> Callable[..., dict[str, Any]]:
    gold = _gold(root)

    def fake(
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
        assert system and schema
        assert timeout == 20.0
        assert max_tokens == 2000
        assert max_retries == 0
        assert settings is not None
        payload = json.loads(user)
        identifier = int(payload["news_item_id"])
        model = settings.llm_model
        calls.append((model, identifier))
        if fail_flash and model == "qwen3.7-flash":
            _record_audit(session, model=model, ok=False)
            raise LLMUnavailable("request_timeout")
        _record_audit(session, model=model, ok=True)
        label = gold[identifier]
        candidates = payload["evidence_candidates"]
        assert isinstance(candidates, list) and candidates
        first = candidates[0]
        return {
            "symbols": label["symbols"],
            "event_type": label["event_type"],
            "direction": label["direction"],
            "materiality": label["materiality"],
            "summary": str(first[3])[:100],
            "confidence": 0.8,
            "evidence_candidate_id": first[0],
        }

    return fake


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    assert isinstance(value, dict)
    return value


def test_round1_runs_both_models_in_fixed_order_and_selects_flash(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    calls: list[tuple[str, int]] = []

    result = runner.run_round(
        project_root=root,
        settings=_settings(),
        chat_json_fn=_fake_chat(root, calls),
    )

    assert len(calls) == 90
    assert [model for model, _ in calls[:45]] == ["qwen3.7-flash"] * 45
    assert [model for model, _ in calls[45:]] == ["qwen3.6-plus"] * 45
    assert result.selected_model == "qwen3.7-flash"
    outcome = _read_json(result.outcome_path)
    assert outcome["production_llm_calls_delta"] == 0
    assert outcome["heldout_touched"] is False
    assert outcome["p4_2b_unlocked"] is False
    assert outcome["p4_3_unlocked"] is False
    assert outcome["selected_model"] == "qwen3.7-flash"
    for model in runner.MODEL_ORDER:
        report = result.reports[model]
        assert report["gate_passed"] is True
        assert report["metrics"]["materiality_recall"]["value"] is None
        assert report["metrics"]["materiality_recall"]["formula"] == "not_estimable"
        assert report["extraction"]["isolated_audit_row_count"] == 45
        weighted = report["metrics"]["source_pool_weighted_diagnostics"]
        assert weighted["metric_partition"] == "candidate_round_predicted_output_partition"
        assert weighted["sampling_frame"] == "development_source_candidate_pool_after_retirement"
        assert weighted["sampling_strata"] == [
            "baseline_predicted_positive",
            "baseline_predicted_negative",
        ]
        assert weighted["denominator"] == "inverse_probability_weighted_partition_total"
        assert weighted["formula"] == {
            "materiality_precision": "weighted_tp / (weighted_tp + weighted_fp)",
            "materiality_false_omission_rate": "weighted_fn / (weighted_fn + weighted_tn)",
        }
        assert weighted["gate_or_diagnostic"] == "diagnostic"
        assert weighted["estimator"] == (
            "inverse_probability_weighted_by_baseline_sampling_stratum"
        )
    assert not (root / "docs/phase4/eval/v2-calibration/heldout").exists()
    with sqlite3.connect(root / "data/alphapilot.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0

    state = runner._load_jsonl(result.calibration_state_path, "state")
    assert [event["event"] for event in state] == ["round_started", "round_completed"]
    with pytest.raises(runner.CalibrationRoundError, match="already exists"):
        runner.run_round(
            project_root=root,
            settings=_settings(),
            chat_json_fn=lambda *_args, **_kwargs: pytest.fail("must not retry"),
        )


def test_flash_failures_are_recorded_without_retry_and_plus_still_runs(tmp_path: Path) -> None:
    root = _fixture_root(tmp_path)
    calls: list[tuple[str, int]] = []

    result = runner.run_round(
        project_root=root,
        settings=_settings(),
        chat_json_fn=_fake_chat(root, calls, fail_flash=True),
    )

    assert len(calls) == 90
    assert result.selected_model == "qwen3.6-plus"
    flash = result.reports["qwen3.7-flash"]
    plus = result.reports["qwen3.6-plus"]
    assert flash["gate_passed"] is False
    assert flash["extraction"]["failure_count"] == 45
    assert flash["extraction"]["retried_failure_count"] == 0
    assert plus["gate_passed"] is True
    flash_state = runner._load_jsonl(
        runner._artifact_paths(root, "qwen3.7-flash")["terminal_state"],
        "flash state",
    )
    assert [event["event"] for event in flash_state] == [
        "model_started",
        "model_completed",
    ]


def test_preregistration_or_prompt_drift_fails_before_state_or_model_call(
    tmp_path: Path,
) -> None:
    root = _fixture_root(tmp_path)
    prompt = root / "config/prompts/p4_news_event_extract_v2-r1.txt"
    prompt.write_text(prompt.read_text() + "drift\n")
    calls: list[tuple[str, int]] = []

    with pytest.raises(runner.CalibrationRoundError, match="SHA-256"):
        runner.run_round(
            project_root=root,
            settings=_settings(),
            chat_json_fn=_fake_chat(root, calls),
        )

    assert calls == []
    assert not (
        root / "docs/phase4/eval/v2-calibration/development/calibration.state.jsonl"
    ).exists()
    assert not (root / "docs/phase4/eval/v2-calibration/heldout").exists()


def test_score_fails_closed_on_missing_prediction_and_forbids_numeric_recall() -> None:
    gold = [
        {
            "news_item_id": identifier,
            "gold": {
                "materiality": 2 if identifier == 1 else 1,
                "symbols": [],
            },
        }
        for identifier in range(1, 46)
    ]
    rows = [
        {
            "news_item_id": identifier,
            "status": "ok",
            "prediction": {"materiality": 1, "symbols": []},
        }
        for identifier in range(1, 45)
    ]

    strata = {
        identifier: "predicted_positive" if identifier <= 30 else "predicted_negative"
        for identifier in range(1, 46)
    }
    metrics = runner._score(rows, gold, strata)

    assert metrics["technical_completion"]["passed"] is False
    assert metrics["materiality_precision"]["passed"] is False
    assert metrics["materiality_false_omission_rate"]["passed"] is False
    assert metrics["materiality_recall"]["value"] is None
    assert metrics["materiality_recall"]["formula"] == "not_estimable"
    assert metrics["both_materiality_gates_passed"] is False
