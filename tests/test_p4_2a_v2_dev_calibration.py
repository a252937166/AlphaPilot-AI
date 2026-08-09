from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
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


def _round2_fixture_root(tmp_path: Path) -> Path:
    root = _fixture_root(tmp_path)
    extra_files = (
        "config/p4_event_extract_eval_v2-r2-qwen3.7-flash.yaml",
        "config/p4_event_extract_eval_v2-r2-qwen3.6-plus.yaml",
        "config/prompts/p4_news_event_extract_v2-r2.txt",
        "docs/phase4/reports/P4.2a-round1-adjudication-and-round2-conditions-20260809.json",
        "docs/phase4/eval/v2-calibration/development/calibration.state.jsonl",
        "docs/phase4/eval/v2-calibration/development/rounds/r1/round-outcome.json",
        "docs/phase4/eval/v2-calibration/development/rounds/r1/qwen3.7-flash/predictions.jsonl",
        "docs/phase4/eval/v2-calibration/development/rounds/r1/qwen3.7-flash/manifest.json",
        "docs/phase4/eval/v2-calibration/development/rounds/r1/qwen3.7-flash/report.json",
        "docs/phase4/eval/v2-calibration/development/rounds/r1/qwen3.7-flash/terminal-state.jsonl",
        "docs/phase4/eval/v2-calibration/development/rounds/r1/qwen3.6-plus/predictions.jsonl",
        "docs/phase4/eval/v2-calibration/development/rounds/r1/qwen3.6-plus/manifest.json",
        "docs/phase4/eval/v2-calibration/development/rounds/r1/qwen3.6-plus/report.json",
        "docs/phase4/eval/v2-calibration/development/rounds/r1/qwen3.6-plus/terminal-state.jsonl",
        "docs/phase4/eval/v2-calibration/development/rounds/r2/round-preregistration.json",
    )
    for relative in extra_files:
        _copy(root, relative)

    state_path = root / "docs/phase4/eval/v2-calibration/development/calibration.state.jsonl"
    round_one_prefix = b"".join(state_path.read_bytes().splitlines(keepends=True)[:2])
    state_path.write_bytes(round_one_prefix)
    assert runner._sha256_file(state_path) == runner.ROUND_1_STATE_PREFIX_SHA256
    return root


def _round3_fixture_root(tmp_path: Path) -> tuple[Path, str]:
    root = _round2_fixture_root(tmp_path)
    extra_files = (
        "docs/phase4/reports/P4.2a-round2-adjudication-20260810.json",
        "docs/phase4/eval/v2-calibration/development/rounds/r2/round-outcome.json",
        "docs/phase4/eval/v2-calibration/development/rounds/r2/"
        "qwen3.7-flash/predictions.jsonl",
        "docs/phase4/eval/v2-calibration/development/rounds/r2/"
        "qwen3.7-flash/manifest.json",
        "docs/phase4/eval/v2-calibration/development/rounds/r2/"
        "qwen3.7-flash/report.json",
        "docs/phase4/eval/v2-calibration/development/rounds/r2/"
        "qwen3.7-flash/terminal-state.jsonl",
        "docs/phase4/eval/v2-calibration/development/rounds/r2/"
        "qwen3.6-plus/predictions.jsonl",
        "docs/phase4/eval/v2-calibration/development/rounds/r2/"
        "qwen3.6-plus/manifest.json",
        "docs/phase4/eval/v2-calibration/development/rounds/r2/"
        "qwen3.6-plus/report.json",
        "docs/phase4/eval/v2-calibration/development/rounds/r2/"
        "qwen3.6-plus/terminal-state.jsonl",
    )
    for relative in extra_files:
        _copy(root, relative)
    state_relative = "docs/phase4/eval/v2-calibration/development/calibration.state.jsonl"
    _copy(root, state_relative)
    state_path = root / state_relative
    state_rows = runner._load_jsonl(state_path, "current calibration state fixture")
    assert len(state_rows) >= 4
    state_path.write_bytes(
        b"".join(runner._canonical_json_bytes(event) for event in state_rows[:4])
    )
    assert runner._sha256_file(state_path) == runner.ROUND_2_STATE_PREFIX_SHA256

    preregistered_at = "2026-08-09T00:00:00Z"
    prompt_relative = "config/prompts/p4_news_event_extract_v2-r3.txt"
    prompt_path = root / prompt_relative
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    round_two_prompt = (
        root / "config/prompts/p4_news_event_extract_v2-r2.txt"
    ).read_text(encoding="utf-8")
    prompt_path.write_text(
        round_two_prompt.replace(
            "[P4_NEWS_EVENT_EXTRACT v2-r2]",
            "[P4_NEWS_EVENT_EXTRACT v2-r3]",
            1,
        ),
        encoding="utf-8",
    )
    prompt_sha256 = runner._sha256_file(prompt_path)

    contract_refs: dict[str, dict[str, str]] = {}
    for model in runner.MODEL_ORDER:
        source = root / f"config/p4_event_extract_eval_v2-r2-{model}.yaml"
        wrapper = yaml.safe_load(source.read_text(encoding="utf-8"))
        assert isinstance(wrapper, dict)
        wrapper["schema_version"] = "p4.2a-development-event-extract-contract-v2-r3"
        wrapper["round_number"] = 3
        wrapper["pre_registered_at"] = preregistered_at
        wrapper["contract_files"]["prompt"] = {
            "path": prompt_relative,
            "sha256": prompt_sha256,
        }
        contract_relative = f"config/p4_event_extract_eval_v2-r3-{model}.yaml"
        contract_path = root / contract_relative
        contract_path.write_text(
            yaml.safe_dump(wrapper, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        contract_refs[model] = {
            "path": contract_relative,
            "sha256": runner._sha256_file(contract_path),
        }

    round_two_prereg = _read_json(
        root / runner.ROUND_2_PREREGISTRATION_PATH
    )
    prereg = dict(round_two_prereg)
    prereg["round_number"] = 3
    prereg["pre_registered_at"] = preregistered_at
    prereg["prior_round"] = {
        "round_number": 2,
        "preregistration": {
            "path": runner.ROUND_2_PREREGISTRATION_PATH.as_posix(),
            "sha256": runner.ROUND_2_PREREGISTRATION_SHA256,
        },
        "outcome": {
            "path": (
                "docs/phase4/eval/v2-calibration/development/rounds/"
                "r2/round-outcome.json"
            ),
            "sha256": runner.ROUND_2_OUTCOME_SHA256,
        },
    }
    prereg["round_authorization"] = {
        "path": runner.ROUND_3_AUTHORIZATION_PATH.as_posix(),
        "sha256": runner.ROUND_3_AUTHORIZATION_SHA256,
    }
    prereg["prompt"] = {"path": prompt_relative, "sha256": prompt_sha256}
    prereg["candidate_prompt_summaries"] = {
        model: {
            "prompt": {"path": prompt_relative, "sha256": prompt_sha256},
            "summary": "fixture-only byte-frozen Round 3 prompt summary",
        }
        for model in runner.MODEL_ORDER
    }
    prereg["models"] = [
        {"model_slug": model, "model": model, "contract": contract_refs[model]}
        for model in runner.MODEL_ORDER
    ]
    prereg["artifacts"] = {
        "round_directory": {
            "path": "docs/phase4/eval/v2-calibration/development/rounds/r3"
        },
        "round_outcome": {
            "path": (
                "docs/phase4/eval/v2-calibration/development/rounds/"
                "r3/round-outcome.json"
            ),
            "create_only": True,
        },
        "calibration_state": {
            "path": state_relative,
            "append_only": True,
        },
    }
    prereg["post_round_governance"] = {
        "o5_trigger": "round_3_point_estimate_passes_but_adverse_flip_margin_fails",
        "action": (
            "pause_round_consumption_and_return_frame_enlargement_decision_to_owner"
        ),
        "automatic_round_4_allowed": False,
    }
    prereg_path = root / runner.ROUND_3_PREREGISTRATION_PATH
    prereg_path.parent.mkdir(parents=True, exist_ok=True)
    prereg_path.write_bytes(runner._canonical_json_bytes(prereg))
    return root, runner._sha256_file(prereg_path)


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


def test_round2_appends_state_preserves_round1_and_applies_margin(tmp_path: Path) -> None:
    root = _round2_fixture_root(tmp_path)
    round_one = root / "docs/phase4/eval/v2-calibration/development/rounds/r1"
    round_one_hashes = {
        path.relative_to(root).as_posix(): runner._sha256_file(path)
        for path in round_one.rglob("*")
        if path.is_file()
    }
    calls: list[tuple[str, int]] = []

    result = runner.run_round(
        round_number=2,
        round_preregistration=runner.ROUND_2_PREREGISTRATION_PATH,
        round_preregistration_sha256=runner.ROUND_2_PREREGISTRATION_SHA256,
        project_root=root,
        settings=_settings(),
        chat_json_fn=_fake_chat(root, calls),
    )

    assert len(calls) == 90
    assert result.selected_model == "qwen3.7-flash"
    events = runner._load_jsonl(result.calibration_state_path, "calibration state")
    assert [(row["round_number"], row["event"]) for row in events] == [
        (1, "round_started"),
        (1, "round_completed"),
        (2, "round_started"),
        (2, "round_completed"),
    ]
    for report in result.reports.values():
        metrics = report["metrics"]
        assert metrics["sampling_frame"]["registered_strata"] == {
            "baseline_predicted_positive": 30,
            "baseline_predicted_negative": 15,
        }
        breakdown = metrics["baseline_stratum_breakdown"]
        assert set(breakdown) == {
            "baseline_predicted_positive",
            "baseline_predicted_negative",
        }
        for stratum, expected_size in (
            ("baseline_predicted_positive", 30),
            ("baseline_predicted_negative", 15),
        ):
            detail = breakdown[stratum]
            assert detail["sampling_frame"] == (
                "development_frame_v2_baseline_stratified_30_positive_15_negative"
            )
            assert detail["sampling_stratum"] == stratum
            assert detail["registered_stratum_size"] == expected_size
            assert detail["materiality_precision"]["formula"] == "tp / (tp + fp)"
            assert detail["materiality_precision"]["threshold"] == 0.8
            assert detail["materiality_precision"]["gate_or_diagnostic"] == (
                "sampling_stratum_diagnostic"
            )
            assert detail["materiality_false_omission_rate"]["formula"] == "fn / (fn + tn)"
            assert detail["materiality_false_omission_rate"]["threshold"] == 0.2
            assert detail["materiality_false_omission_rate"]["gate_or_diagnostic"] == (
                "sampling_stratum_diagnostic"
            )
            assert detail["materiality_recall"]["value"] is None
            assert detail["materiality_recall"]["formula"] == "not_estimable"
            stratum_margin = detail["adverse_single_item_margin"]
            assert stratum_margin["required_by_overall_gate"] is True
            assert stratum_margin["precision"]["transformation"] == (
                "one_true_positive_reclassified_as_false_positive"
            )
            assert stratum_margin["false_omission_rate"]["transformation"] == (
                "one_true_negative_reclassified_as_false_negative"
            )
            assert stratum_margin["gate_or_diagnostic"] == (
                "sampling_stratum_diagnostic"
            )
            assert stratum_margin["may_not_override_overall_development_gate"] is True
            assert detail["may_not_override_overall_development_gate"] is True
        assert metrics["materiality_recall"]["value"] is None
        assert metrics["adverse_single_item_margin"]["required"] is True
        assert metrics["adverse_single_item_margin"]["both_passed"] is True
        assert metrics["both_materiality_gates_passed"] is True
    assert {
        path.relative_to(root).as_posix(): runner._sha256_file(path)
        for path in round_one.rglob("*")
        if path.is_file()
    } == round_one_hashes
    with pytest.raises(runner.CalibrationRoundError, match="already exists"):
        runner.run_round(
            round_number=1,
            project_root=root,
            settings=_settings(),
            chat_json_fn=lambda *_args, **_kwargs: pytest.fail("must not rerun Round 1"),
        )


def test_round2_adverse_flip_margin_rejects_boundary_point_estimates() -> None:
    gold = [
        {
            "news_item_id": identifier,
            "gold": {
                "materiality": 2 if identifier <= 18 else 1,
                "symbols": [],
            },
        }
        for identifier in range(1, 46)
    ]
    predicted_positive = set(range(1, 13)) | {19, 20, 21}
    rows = [
        {
            "news_item_id": identifier,
            "status": "ok",
            "prediction": {
                "materiality": 2 if identifier in predicted_positive else 1,
                "symbols": [],
            },
        }
        for identifier in range(1, 46)
    ]
    strata = {
        identifier: "predicted_positive" if identifier <= 30 else "predicted_negative"
        for identifier in range(1, 46)
    }

    metrics = runner._score(
        rows,
        gold,
        strata,
        adverse_flip_margin_required=True,
    )

    assert metrics["confusion_matrix"] == {"tp": 12, "fp": 3, "fn": 6, "tn": 24}
    assert metrics["point_estimate_materiality_gates_passed"] is True
    margin = metrics["adverse_single_item_margin"]
    assert margin["precision"]["value"] == pytest.approx(11 / 15)
    assert margin["false_omission_rate"]["value"] == pytest.approx(7 / 30)
    assert margin["both_passed"] is False
    assert metrics["both_materiality_gates_passed"] is False


def test_round2_wrong_registered_hash_fails_before_state_or_model_call(tmp_path: Path) -> None:
    root = _round2_fixture_root(tmp_path)
    state = root / "docs/phase4/eval/v2-calibration/development/calibration.state.jsonl"
    before = state.read_bytes()
    calls: list[tuple[str, int]] = []

    with pytest.raises(runner.CalibrationRoundError, match="not registered"):
        runner.run_round(
            round_number=2,
            round_preregistration=runner.ROUND_2_PREREGISTRATION_PATH,
            round_preregistration_sha256="0" * 64,
            project_root=root,
            settings=_settings(),
            chat_json_fn=_fake_chat(root, calls),
        )

    assert state.read_bytes() == before
    assert calls == []
    assert not (
        root / "docs/phase4/eval/v2-calibration/development/rounds/r2/round-outcome.json"
    ).exists()


def test_round2_rejects_coherently_relinked_round1_prediction_tamper(tmp_path: Path) -> None:
    root = _round2_fixture_root(tmp_path)
    model_root = (
        root
        / "docs/phase4/eval/v2-calibration/development/rounds/r1/qwen3.7-flash"
    )
    predictions = model_root / "predictions.jsonl"
    rows = [json.loads(line) for line in predictions.read_text().splitlines()]
    rows[0]["prediction"]["summary"] += "tampered"
    predictions.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        )
    )
    manifest_path = model_root / "manifest.json"
    manifest = _read_json(manifest_path)
    manifest["predictions_sha256"] = runner._sha256_file(predictions)
    manifest_path.write_bytes(runner._canonical_json_bytes(manifest))
    terminal_path = model_root / "terminal-state.jsonl"
    terminal = runner._load_jsonl(terminal_path, "tampered terminal")
    terminal[1]["manifest_sha256"] = runner._sha256_file(manifest_path)
    terminal_path.write_bytes(b"".join(runner._canonical_json_bytes(row) for row in terminal))
    calls: list[tuple[str, int]] = []

    with pytest.raises(runner.CalibrationRoundError, match="artifact anchor drifted"):
        runner.run_round(
            round_number=2,
            round_preregistration=runner.ROUND_2_PREREGISTRATION_PATH,
            round_preregistration_sha256=runner.ROUND_2_PREREGISTRATION_SHA256,
            project_root=root,
            settings=_settings(),
            chat_json_fn=_fake_chat(root, calls),
        )

    assert calls == []


def test_round2_rejects_non_key_round1_state_prefix_tamper(tmp_path: Path) -> None:
    root = _round2_fixture_root(tmp_path)
    state = root / "docs/phase4/eval/v2-calibration/development/calibration.state.jsonl"
    events = runner._load_jsonl(state, "calibration state")
    events[0]["production_writes"] = True
    state.write_bytes(b"".join(runner._canonical_json_bytes(row) for row in events))
    calls: list[tuple[str, int]] = []

    with pytest.raises(runner.CalibrationRoundError, match="state prefix drifted"):
        runner.run_round(
            round_number=2,
            round_preregistration=runner.ROUND_2_PREREGISTRATION_PATH,
            round_preregistration_sha256=runner.ROUND_2_PREREGISTRATION_SHA256,
            project_root=root,
            settings=_settings(),
            chat_json_fn=_fake_chat(root, calls),
        )

    assert calls == []


@pytest.mark.parametrize("use_valid_registration", [False, True])
def test_round2_preflight_failure_cannot_terminalize_another_active_invocation(
    tmp_path: Path,
    use_valid_registration: bool,
) -> None:
    root = _round2_fixture_root(tmp_path)
    state = root / "docs/phase4/eval/v2-calibration/development/calibration.state.jsonl"
    runner._append_calibration_event(
        state,
        {
            "schema_version": "p4.2a-v2-development-calibration-state-v1",
            "event": "round_started",
            "execution_id": "a" * 64,
            "round_number": 2,
            "at_utc": "2026-08-09T16:10:00Z",
            "design_sha256": runner.DESIGN_SHA256,
            "round_preregistration_sha256": runner.ROUND_2_PREREGISTRATION_SHA256,
            "heldout_touched": False,
            "production_writes": False,
        },
        expected_event_count=2,
        expected_last_event="round_completed",
    )
    before = state.read_bytes()
    supplied_sha = (
        runner.ROUND_2_PREREGISTRATION_SHA256 if use_valid_registration else "0" * 64
    )

    with pytest.raises(runner.CalibrationRoundError):
        runner.run_round(
            round_number=2,
            round_preregistration=runner.ROUND_2_PREREGISTRATION_PATH,
            round_preregistration_sha256=supplied_sha,
            project_root=root,
            settings=_settings(),
            chat_json_fn=lambda *_args, **_kwargs: pytest.fail("must not call model"),
        )

    assert state.read_bytes() == before
    assert not (
        root / "docs/phase4/eval/v2-calibration/development/rounds/r2/round-outcome.json"
    ).exists()


def test_round2_terminalizes_failure_after_round_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _round2_fixture_root(tmp_path)
    original_create = runner._create_only

    def fail_first_model_state(path: Path, payload: bytes, artifact_root: Path) -> None:
        if path.name == "terminal-state.jsonl" and "rounds/r2" in path.as_posix():
            raise RuntimeError("injected_pre_model_failure")
        original_create(path, payload, artifact_root)

    monkeypatch.setattr(runner, "_create_only", fail_first_model_state)

    with pytest.raises(RuntimeError, match="injected_pre_model_failure"):
        runner.run_round(
            round_number=2,
            round_preregistration=runner.ROUND_2_PREREGISTRATION_PATH,
            round_preregistration_sha256=runner.ROUND_2_PREREGISTRATION_SHA256,
            project_root=root,
            settings=_settings(),
            chat_json_fn=lambda *_args, **_kwargs: pytest.fail("must fail before model call"),
        )

    state_path = root / "docs/phase4/eval/v2-calibration/development/calibration.state.jsonl"
    events = runner._load_jsonl(state_path, "terminalized state")
    assert [(row["round_number"], row["event"]) for row in events[-2:]] == [
        (2, "round_started"),
        (2, "round_failed"),
    ]
    outcome = _read_json(
        root / "docs/phase4/eval/v2-calibration/development/rounds/r2/round-outcome.json"
    )
    assert outcome["status"] == "technical_failed"
    assert outcome["selected_model"] is None
    assert outcome["terminalization"] == "outer_fail_closed_guard"
    assert outcome["raw_exception_or_payload_persisted"] is False


def test_round2_terminalizes_keyboard_interrupt_during_model_call(tmp_path: Path) -> None:
    root = _round2_fixture_root(tmp_path)

    def interrupt(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        runner.run_round(
            round_number=2,
            round_preregistration=runner.ROUND_2_PREREGISTRATION_PATH,
            round_preregistration_sha256=runner.ROUND_2_PREREGISTRATION_SHA256,
            project_root=root,
            settings=_settings(),
            chat_json_fn=interrupt,
        )

    state = runner._load_jsonl(
        root / "docs/phase4/eval/v2-calibration/development/calibration.state.jsonl",
        "terminalized interrupt state",
    )
    assert state[-1]["event"] == "round_failed"
    assert state[-1]["technical_failure"] == "KeyboardInterrupt"


def test_round2_terminalizes_post_run_snapshot_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _round2_fixture_root(tmp_path)
    original_snapshot = runner._production_snapshot
    count = 0

    def fail_second_snapshot(project_root: Path) -> runner.ProductionSnapshot:
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("injected_post_run_snapshot_failure")
        return original_snapshot(project_root)

    monkeypatch.setattr(runner, "_production_snapshot", fail_second_snapshot)
    calls: list[tuple[str, int]] = []

    with pytest.raises(RuntimeError, match="injected_post_run_snapshot_failure"):
        runner.run_round(
            round_number=2,
            round_preregistration=runner.ROUND_2_PREREGISTRATION_PATH,
            round_preregistration_sha256=runner.ROUND_2_PREREGISTRATION_SHA256,
            project_root=root,
            settings=_settings(),
            chat_json_fn=_fake_chat(root, calls),
        )

    assert len(calls) == 90
    state = runner._load_jsonl(
        root / "docs/phase4/eval/v2-calibration/development/calibration.state.jsonl",
        "terminalized post-run state",
    )
    assert state[-1]["event"] == "round_failed"
    outcome = _read_json(
        root / "docs/phase4/eval/v2-calibration/development/rounds/r2/round-outcome.json"
    )
    assert outcome["status"] == "technical_failed"
    assert outcome["technical_failure"] == "RuntimeError"
    assert outcome["models_always_measured"] == list(runner.MODEL_ORDER)
    assert set(outcome["model_reports"]) == set(runner.MODEL_ORDER)
    assert outcome["partial_model_artifacts"] == {}
    for model in runner.MODEL_ORDER:
        assert outcome["model_reports"][model]["closure"] == "complete"


def test_round2_recovers_completed_terminal_when_recorded_outcome_already_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _round2_fixture_root(tmp_path)
    original_append = runner._append_calibration_event
    failed_once = False

    def fail_first_terminal_append(
        path: Path,
        event: dict[str, Any],
        *,
        expected_event_count: int,
        expected_last_event: str,
        **transition_guards: Any,
    ) -> None:
        nonlocal failed_once
        if event.get("event") == "round_completed" and not failed_once:
            failed_once = True
            raise RuntimeError("injected_terminal_append_failure")
        original_append(
            path,
            event,
            expected_event_count=expected_event_count,
            expected_last_event=expected_last_event,
            **transition_guards,
        )

    monkeypatch.setattr(runner, "_append_calibration_event", fail_first_terminal_append)
    calls: list[tuple[str, int]] = []

    with pytest.raises(RuntimeError, match="injected_terminal_append_failure"):
        runner.run_round(
            round_number=2,
            round_preregistration=runner.ROUND_2_PREREGISTRATION_PATH,
            round_preregistration_sha256=runner.ROUND_2_PREREGISTRATION_SHA256,
            project_root=root,
            settings=_settings(),
            chat_json_fn=_fake_chat(root, calls),
        )

    assert len(calls) == 90
    outcome = _read_json(
        root / "docs/phase4/eval/v2-calibration/development/rounds/r2/round-outcome.json"
    )
    assert outcome["status"] == "recorded"
    assert outcome["selected_model"] == "qwen3.7-flash"
    state = runner._load_jsonl(
        root / "docs/phase4/eval/v2-calibration/development/calibration.state.jsonl",
        "recovered completed state",
    )
    assert state[-1]["event"] == "round_completed"
    assert state[-1]["selected_model"] == "qwen3.7-flash"
    assert state[-1]["technical_failure"] is None
    assert state[-1]["terminalization"] == (
        "recovered_terminal_append_for_recorded_outcome"
    )


def test_round3_is_not_executable_until_exact_preregistration_sha_is_registered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, preregistration_sha256 = _round3_fixture_root(tmp_path)
    monkeypatch.setattr(runner, "ROUND_3_PREREGISTRATION_SHA256", None)
    state_path = (
        root / "docs/phase4/eval/v2-calibration/development/calibration.state.jsonl"
    )
    before = state_path.read_bytes()

    with pytest.raises(
        runner.CalibrationRoundError,
        match="no executable preregistration SHA-256",
    ):
        runner.run_round(
            round_number=3,
            round_preregistration=runner.ROUND_3_PREREGISTRATION_PATH,
            round_preregistration_sha256=preregistration_sha256,
            project_root=root,
            settings=_settings(),
            chat_json_fn=lambda *_args, **_kwargs: pytest.fail("must not call model"),
        )

    assert state_path.read_bytes() == before
    assert not (
        root / "docs/phase4/eval/v2-calibration/development/rounds/r3/round-outcome.json"
    ).exists()


def test_round3_appends_four_line_history_to_six_and_preserves_all_prior_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, preregistration_sha256 = _round3_fixture_root(tmp_path)
    monkeypatch.setattr(
        runner,
        "ROUND_3_PREREGISTRATION_SHA256",
        preregistration_sha256,
    )
    prior_root = root / "docs/phase4/eval/v2-calibration/development/rounds"
    prior_hashes = {
        path.relative_to(root).as_posix(): runner._sha256_file(path)
        for round_name in ("r1", "r2")
        for path in (prior_root / round_name).rglob("*")
        if path.is_file()
    }
    calls: list[tuple[str, int]] = []

    result = runner.run_round(
        round_number=3,
        round_preregistration=runner.ROUND_3_PREREGISTRATION_PATH,
        round_preregistration_sha256=preregistration_sha256,
        project_root=root,
        settings=_settings(),
        chat_json_fn=_fake_chat(root, calls),
    )

    assert len(calls) == 90
    assert [model for model, _ in calls[:45]] == ["qwen3.7-flash"] * 45
    assert [model for model, _ in calls[45:]] == ["qwen3.6-plus"] * 45
    assert result.selected_model == "qwen3.7-flash"
    state = runner._load_jsonl(result.calibration_state_path, "Round 3 state")
    assert [(row["round_number"], row["event"]) for row in state] == [
        (1, "round_started"),
        (1, "round_completed"),
        (2, "round_started"),
        (2, "round_completed"),
        (3, "round_started"),
        (3, "round_completed"),
    ]
    assert state[4]["execution_id"] == state[5]["execution_id"]
    outcome = _read_json(result.outcome_path)
    assert outcome["post_round_governance"] == {
        **runner.ROUND_3_POST_ROUND_GOVERNANCE,
        "triggered": False,
        "next_action": "await_independent_round_adjudication",
    }
    assert {
        path.relative_to(root).as_posix(): runner._sha256_file(path)
        for round_name in ("r1", "r2")
        for path in (prior_root / round_name).rglob("*")
        if path.is_file()
    } == prior_hashes
    assert not (root / "docs/phase4/eval/v2-calibration/heldout").exists()


@pytest.mark.parametrize("line_index", [0, 2])
def test_round3_rejects_round1_or_round2_state_prefix_drift_before_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    line_index: int,
) -> None:
    root, preregistration_sha256 = _round3_fixture_root(tmp_path)
    monkeypatch.setattr(
        runner,
        "ROUND_3_PREREGISTRATION_SHA256",
        preregistration_sha256,
    )
    state_path = (
        root / "docs/phase4/eval/v2-calibration/development/calibration.state.jsonl"
    )
    events = runner._load_jsonl(state_path, "tampered history")
    events[line_index]["production_writes"] = True
    state_path.write_bytes(
        b"".join(runner._canonical_json_bytes(event) for event in events)
    )
    calls: list[tuple[str, int]] = []

    with pytest.raises(runner.CalibrationRoundError, match="state prefix drifted"):
        runner.run_round(
            round_number=3,
            round_preregistration=runner.ROUND_3_PREREGISTRATION_PATH,
            round_preregistration_sha256=preregistration_sha256,
            project_root=root,
            settings=_settings(),
            chat_json_fn=_fake_chat(root, calls),
        )

    assert calls == []
    assert len(runner._load_jsonl(state_path, "unchanged tamper")) == 4


def test_round3_rejects_coherently_relinked_round2_prediction_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, preregistration_sha256 = _round3_fixture_root(tmp_path)
    monkeypatch.setattr(
        runner,
        "ROUND_3_PREREGISTRATION_SHA256",
        preregistration_sha256,
    )
    model_root = (
        root
        / "docs/phase4/eval/v2-calibration/development/rounds/r2/qwen3.7-flash"
    )
    predictions = model_root / "predictions.jsonl"
    rows = [json.loads(line) for line in predictions.read_text().splitlines()]
    rows[0]["prediction"]["summary"] += "coherent-tamper"
    predictions.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in rows
        )
    )
    manifest_path = model_root / "manifest.json"
    manifest = _read_json(manifest_path)
    manifest["predictions_sha256"] = runner._sha256_file(predictions)
    manifest_path.write_bytes(runner._canonical_json_bytes(manifest))
    terminal_path = model_root / "terminal-state.jsonl"
    terminal = runner._load_jsonl(terminal_path, "tampered Round 2 terminal")
    terminal[1]["manifest_sha256"] = runner._sha256_file(manifest_path)
    terminal_path.write_bytes(
        b"".join(runner._canonical_json_bytes(event) for event in terminal)
    )
    calls: list[tuple[str, int]] = []

    with pytest.raises(runner.CalibrationRoundError, match="artifact anchor drifted"):
        runner.run_round(
            round_number=3,
            round_preregistration=runner.ROUND_3_PREREGISTRATION_PATH,
            round_preregistration_sha256=preregistration_sha256,
            project_root=root,
            settings=_settings(),
            chat_json_fn=_fake_chat(root, calls),
        )

    assert calls == []


def test_round3_preflight_cannot_claim_or_terminalize_concurrent_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, preregistration_sha256 = _round3_fixture_root(tmp_path)
    monkeypatch.setattr(
        runner,
        "ROUND_3_PREREGISTRATION_SHA256",
        preregistration_sha256,
    )
    state_path = (
        root / "docs/phase4/eval/v2-calibration/development/calibration.state.jsonl"
    )
    runner._append_calibration_event(
        state_path,
        {
            "schema_version": "p4.2a-v2-development-calibration-state-v1",
            "event": "round_started",
            "execution_id": "a" * 64,
            "round_number": 3,
            "at_utc": "2026-08-09T18:30:00Z",
            "design_sha256": runner.DESIGN_SHA256,
            "round_preregistration_sha256": preregistration_sha256,
            "heldout_touched": False,
            "production_writes": False,
        },
        expected_event_count=4,
        expected_last_event="round_completed",
        expected_prefix_sha256=runner.ROUND_2_STATE_PREFIX_SHA256,
        expected_last_round_number=2,
    )
    before = state_path.read_bytes()

    with pytest.raises(runner.CalibrationRoundError):
        runner.run_round(
            round_number=3,
            round_preregistration=runner.ROUND_3_PREREGISTRATION_PATH,
            round_preregistration_sha256=preregistration_sha256,
            project_root=root,
            settings=_settings(),
            chat_json_fn=lambda *_args, **_kwargs: pytest.fail("must not call model"),
        )

    assert state_path.read_bytes() == before
    assert not (
        root / "docs/phase4/eval/v2-calibration/development/rounds/r3/round-outcome.json"
    ).exists()


def test_round3_terminalizes_failure_as_sixth_state_event_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, preregistration_sha256 = _round3_fixture_root(tmp_path)
    monkeypatch.setattr(
        runner,
        "ROUND_3_PREREGISTRATION_SHA256",
        preregistration_sha256,
    )
    original_create = runner._create_only

    def fail_first_model_state(path: Path, payload: bytes, artifact_root: Path) -> None:
        if path.name == "terminal-state.jsonl" and "rounds/r3" in path.as_posix():
            raise RuntimeError("injected_round3_pre_model_failure")
        original_create(path, payload, artifact_root)

    monkeypatch.setattr(runner, "_create_only", fail_first_model_state)

    with pytest.raises(RuntimeError, match="injected_round3_pre_model_failure"):
        runner.run_round(
            round_number=3,
            round_preregistration=runner.ROUND_3_PREREGISTRATION_PATH,
            round_preregistration_sha256=preregistration_sha256,
            project_root=root,
            settings=_settings(),
            chat_json_fn=lambda *_args, **_kwargs: pytest.fail("must not call model"),
        )

    state_path = (
        root / "docs/phase4/eval/v2-calibration/development/calibration.state.jsonl"
    )
    state = runner._load_jsonl(state_path, "terminalized Round 3 state")
    assert len(state) == 6
    assert [(row["round_number"], row["event"]) for row in state[-2:]] == [
        (3, "round_started"),
        (3, "round_failed"),
    ]
    assert state[-2]["execution_id"] == state[-1]["execution_id"]
    outcome = _read_json(
        root / "docs/phase4/eval/v2-calibration/development/rounds/r3/round-outcome.json"
    )
    assert outcome["selected_model"] is None
    assert outcome["post_round_governance"]["triggered"] is False
    assert outcome["post_round_governance"]["next_action"] == (
        "await_independent_round_adjudication"
    )
    assert not (root / "docs/phase4/eval/v2-calibration/heldout").exists()


def test_round3_recovers_completed_terminal_with_same_execution_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, preregistration_sha256 = _round3_fixture_root(tmp_path)
    monkeypatch.setattr(
        runner,
        "ROUND_3_PREREGISTRATION_SHA256",
        preregistration_sha256,
    )
    original_append = runner._append_calibration_event
    failed_once = False

    def fail_first_terminal_append(
        path: Path,
        event: dict[str, Any],
        *,
        expected_event_count: int,
        expected_last_event: str,
        **transition_guards: Any,
    ) -> None:
        nonlocal failed_once
        if event.get("event") == "round_completed" and not failed_once:
            failed_once = True
            raise RuntimeError("injected_round3_terminal_append_failure")
        original_append(
            path,
            event,
            expected_event_count=expected_event_count,
            expected_last_event=expected_last_event,
            **transition_guards,
        )

    monkeypatch.setattr(runner, "_append_calibration_event", fail_first_terminal_append)
    calls: list[tuple[str, int]] = []

    with pytest.raises(RuntimeError, match="injected_round3_terminal_append_failure"):
        runner.run_round(
            round_number=3,
            round_preregistration=runner.ROUND_3_PREREGISTRATION_PATH,
            round_preregistration_sha256=preregistration_sha256,
            project_root=root,
            settings=_settings(),
            chat_json_fn=_fake_chat(root, calls),
        )

    assert len(calls) == 90
    state = runner._load_jsonl(
        root / "docs/phase4/eval/v2-calibration/development/calibration.state.jsonl",
        "recovered Round 3 state",
    )
    assert len(state) == 6
    assert state[-1]["event"] == "round_completed"
    assert state[-1]["execution_id"] == state[-2]["execution_id"]
    assert state[-1]["terminalization"] == (
        "recovered_terminal_append_for_recorded_outcome"
    )
    assert not (root / "docs/phase4/eval/v2-calibration/heldout").exists()


def test_round3_o5_governance_triggers_only_for_point_pass_margin_fail() -> None:
    triggered = runner._round_three_governance_outcome(
        {
            "qwen3.6-plus": {
                "metrics": {
                    "point_estimate_materiality_gates_passed": True,
                    "adverse_single_item_margin": {"both_passed": False},
                }
            }
        }
    )
    not_triggered = runner._round_three_governance_outcome(
        {
            "qwen3.6-plus": {
                "metrics": {
                    "point_estimate_materiality_gates_passed": False,
                    "adverse_single_item_margin": {"both_passed": False},
                }
            }
        }
    )
    invalid_round = runner._round_three_governance_outcome(
        {
            "qwen3.6-plus": {
                "metrics": {
                    "point_estimate_materiality_gates_passed": True,
                    "adverse_single_item_margin": {"both_passed": False},
                }
            }
        },
        round_valid=False,
    )

    assert triggered["triggered"] is True
    assert triggered["next_action"] == "return_frame_enlargement_decision_to_owner"
    assert triggered["automatic_round_4_allowed"] is False
    assert not_triggered["triggered"] is False
    assert not_triggered["next_action"] == "await_independent_round_adjudication"
    assert invalid_round["triggered"] is False
