from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from scripts import run_p4_2a_heldout_predictions as runner
from scripts.build_p4_2a_gold_sample import (
    AnnouncementBodyPolicy,
    ExtractedPdfText,
    extracted_pdf_text_fixture,
    validate_heldout_candidate_inputs,
)
from sqlalchemy.orm import Session

from alphapilot.core.config import Settings
from alphapilot.db.models import LLMCall
from alphapilot.llm.client import LLMUnavailable
from alphapilot.llm.p4_news_eval import (
    EventEvaluationDesign,
    load_event_evaluation_design,
)
from alphapilot.llm.p4_news_event import EventExtractContract

READY = datetime.fromisoformat("2026-08-06T00:11:00+08:00")
DEV_COMPLETED = "2026-08-03T00:00:00Z"
PDF_TEXT = "600519 公司公告披露重大事项，供独立盲标与模型使用。" * 8


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


def _copy_project_file(root: Path, relative: str) -> Path:
    source = runner.PROJECT_ROOT / relative
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    return target


def _copy_contract_files(root: Path, design: EventEvaluationDesign) -> None:
    base = design.document["base_annotation_contract"]
    for relative in (
        base["path"],
        base["prompt"]["path"],
        base["result_schema"]["path"],
        design.document["artifacts"]["dev_60_frozen_jsonl"]["path"],
        "docs/phase4/eval/P4.2a-gold-inventory60-v1.labels-ai-drafted.jsonl",
    ):
        _copy_project_file(root, cast(str, relative))


def _create_database(root: Path, *, include_cninfo: bool = True) -> Path:
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
                url TEXT NOT NULL,
                published_at TEXT,
                available_time TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                raw_payload TEXT
            );
            CREATE TABLE securities (symbol TEXT PRIMARY KEY);
            CREATE TABLE trade_proposals (id INTEGER PRIMARY KEY);
            CREATE TABLE broker_orders (
                id INTEGER PRIMARY KEY,
                environment TEXT NOT NULL
            );
            INSERT INTO securities(symbol) VALUES ('600519');
            INSERT INTO trade_proposals(id) VALUES (1);
            INSERT INTO broker_orders(id, environment) VALUES (1, 'SIMULATE');
            """
        )
        rows: list[tuple[object, ...]] = []
        for offset, identifier in enumerate(range(424, 464)):
            source = "cninfo" if include_cninfo and offset == 0 else "akshare_ths"
            title = f"600519 第{identifier}号公告"
            url = (
                f"https://static.cninfo.com.cn/finalpage/2026-08-04/{identifier}.pdf"
                if source == "cninfo"
                else f"https://news.example.test/{identifier}"
            )
            rows.append(
                (
                    identifier,
                    source,
                    "600519",
                    title,
                    url,
                    "2026-08-04 00:59:00" if offset < 20 else "2026-08-05 00:59:00",
                    "2026-08-04 01:00:00" if offset < 20 else "2026-08-05 01:00:00",
                    hashlib.sha256(f"{identifier}:{url}".encode()).hexdigest(),
                    json.dumps(
                        {
                            "digest": f"{title} 摘要",
                            "short": f"{title} 摘要",
                        },
                        ensure_ascii=False,
                    ),
                )
            )
        connection.executemany(
            """
            INSERT INTO news_items(
                id, source, symbol, title, url, published_at, available_time,
                content_hash, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return database


def _create_dev_only_database(root: Path) -> Path:
    data = root / "data"
    data.mkdir(parents=True)
    database = data / "alphapilot.db"
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
        rows = [
            json.loads(line)
            for line in (
                root / "docs/phase4/eval/P4.2a-gold-inventory60-v1.jsonl"
            )
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        symbols = {"600519"}
        for row in rows:
            ingested = row.get("ingested_symbol")
            if isinstance(ingested, str):
                symbols.add(ingested)
            symbols.update(
                re.findall(r"(?<!\d)[0-9]{6}(?!\d)", cast(str, row["original_text"]))
            )
        connection.executemany(
            "INSERT INTO securities(symbol) VALUES (?)",
            [(symbol,) for symbol in sorted(symbols)],
        )
    return database


def _fixture_root(tmp_path: Path) -> EventEvaluationDesign:
    design = load_event_evaluation_design()
    _copy_contract_files(tmp_path, design)
    _create_database(tmp_path)
    return design


def _v14_fixture_root(tmp_path: Path) -> EventEvaluationDesign:
    design = load_event_evaluation_design(
        runner.PROJECT_ROOT / "config/p4_event_evaluation_v1_3.yaml",
        project_root=runner.PROJECT_ROOT,
    )
    _copy_contract_files(tmp_path, design)
    prediction_contract = design.prediction_contract
    _copy_project_file(
        tmp_path,
        prediction_contract.path.relative_to(runner.PROJECT_ROOT).as_posix(),
    )
    contract_files = cast(
        dict[str, dict[str, str]],
        prediction_contract.document["contract_files"],
    )
    _copy_project_file(tmp_path, contract_files["prompt"]["path"])
    _create_dev_only_database(tmp_path)
    return design


def _pdf_fetcher(url: str, contract: AnnouncementBodyPolicy) -> bytes:
    assert url.startswith("https://static.cninfo.com.cn/")
    assert contract.tls_verify is True
    return b"%PDF-fixture"


def _pdf_text_extractor(
    payload: bytes,
    _contract: AnnouncementBodyPolicy,
) -> ExtractedPdfText:
    assert payload == b"%PDF-fixture"
    return extracted_pdf_text_fixture(PDF_TEXT)


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
    calls: list[int],
    *,
    failed_id: int | None = None,
    before_call: Callable[[int], None] | None = None,
    prediction_symbols: tuple[str, ...] = ("600519",),
    predictions_by_id: Mapping[int, Mapping[str, Any]] | None = None,
) -> Callable[..., dict[str, Any]]:
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
        identifier = int(payload["news_item_id"])
        if before_call is not None:
            before_call(identifier)
        calls.append(identifier)
        if identifier == failed_id:
            _record_audit(session, ok=False, error="request_timeout")
            raise LLMUnavailable("provider secret and raw response must not persist")
        _record_audit(session, ok=True, error=None)
        if predictions_by_id is not None:
            prediction = dict(predictions_by_id[identifier])
            prediction["summary"] = "开发集预测与冻结的 AI 开发信号一致。"
            return prediction
        return {
            "symbols": list(prediction_symbols),
            "event_type": "other",
            "direction": 0,
            "materiality": 2 if identifier % 2 == 0 else 1,
            "summary": "公告内容已完成结构化抽取。",
            "confidence": 0.8,
            "evidence_span": str(payload["original_text"])[:8],
        }

    return fake


def _ai_dev_predictions(root: Path) -> dict[int, dict[str, Any]]:
    labels = {
        int(row["news_item_id"]): row
        for row in (
            json.loads(line)
            for line in (
                root
                / "docs/phase4/eval/P4.2a-gold-inventory60-v1.labels-ai-drafted.jsonl"
            )
            .read_text(encoding="utf-8")
            .splitlines()
        )
    }
    result: dict[int, dict[str, Any]] = {}
    for identifier, row in labels.items():
        gold = cast(dict[str, Any], row["gold"])
        original_text = cast(str, row["original_text"])
        ingested = row.get("ingested_symbol")
        allowed = set(re.findall(r"(?<!\d)[0-9]{6}(?!\d)", original_text))
        if isinstance(ingested, str):
            allowed.add(ingested)
        symbols = [
            symbol for symbol in cast(list[str], gold["symbols"]) if symbol in allowed
        ]
        result[identifier] = {
            "symbols": symbols,
            "event_type": gold["event_type"],
            "direction": gold["direction"],
            "materiality": gold["materiality"],
            "summary": "开发集预测与冻结的 AI 开发信号一致。",
            "confidence": 0.8,
            "evidence_span": gold["evidence_span"],
        }
    return result


def _artifact(
    root: Path,
    design: EventEvaluationDesign,
    name: str,
) -> Path:
    raw = cast(str, design.document["artifacts"][name]["path"])
    return root / raw


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )


def _dev_identity(rows: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(
            (f"{row['news_item_id']}\0{row['input_sha256']}\0{row['text_sha256']}\n").encode(
                "ascii"
            )
        )
    return digest.hexdigest()


def _create_dev_final_artifacts(
    root: Path,
    design: EventEvaluationDesign,
    active_contract: EventExtractContract,
) -> tuple[Path, Path]:
    dev_rows = [
        json.loads(line)
        for line in _artifact(root, design, "dev_60_frozen_jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    dev_rows.sort(key=lambda row: int(row["news_item_id"]))
    predictions: list[dict[str, Any]] = []
    by_id = _ai_dev_predictions(root)
    for row in dev_rows:
        prediction = by_id[cast(int, row["news_item_id"])]
        predictions.append(
            {
                "news_item_id": row["news_item_id"],
                "input_sha256": row["input_sha256"],
                "text_sha256": row["text_sha256"],
                "contract_sha256": active_contract.sha256,
                "model": active_contract.model,
                "status": "ok",
                "prediction": prediction,
            }
        )
    predictions_path = _artifact(root, design, "dev_final_predictions_jsonl")
    predictions_path.parent.mkdir(parents=True, exist_ok=True)
    predictions_payload = b"".join(_canonical_json(row) for row in predictions)
    predictions_path.write_bytes(predictions_payload)
    manifest_path = _artifact(
        root,
        design,
        "dev_final_predictions_manifest_json",
    )
    manifest_path.write_bytes(
        _canonical_json(
            {
                "design_sha256": design.sha256,
                "contract_sha256": active_contract.sha256,
                "predictions_path": predictions_path.relative_to(root).as_posix(),
                "predictions_sha256": hashlib.sha256(predictions_payload).hexdigest(),
                "row_count": 60,
                "success_count": 60,
                "failure_count": 0,
                "ordered_identity_sha256": _dev_identity(predictions),
                "completed_at_utc": DEV_COMPLETED,
            }
        )
    )
    return predictions_path, manifest_path


def _freeze(
    root: Path,
    design: EventEvaluationDesign,
    *,
    active_contract_path: Path | None = None,
) -> tuple[Path, Path, Path]:
    contract_path = (
        root / "config/p4_event_extract_eval_v1.yaml"
        if active_contract_path is None
        else active_contract_path
    )
    active_contract = runner._load_active_contract(design, root, contract_path)
    predictions_path, manifest_path = _create_dev_final_artifacts(
        root,
        design,
        active_contract,
    )
    receipt_path = runner.freeze_prediction_contract(
        contract_path,
        predictions_path,
        manifest_path,
        project_root=root,
        design=design,
        now=datetime.fromisoformat("2026-08-04T08:00:00+08:00"),
    )
    return receipt_path, predictions_path, manifest_path


def _versioned_active_contract(
    root: Path,
    *,
    forbidden_temperature_drift: bool = False,
) -> Path:
    base_path = root / "config/p4_event_extract_eval_v1.yaml"
    document = cast(dict[str, Any], yaml.safe_load(base_path.read_bytes()))
    prompt_path = root / "config/prompts/p4_news_event_extract_v1_1.txt"
    prompt = (
        "[P4_NEWS_EVENT_EXTRACT v1.1]\n"
        "仅依据输入原文抽取；无法证实时降低 materiality，不得猜测证券代码。\n"
    )
    prompt_path.write_text(prompt, encoding="utf-8")
    document["schema_version"] = "p4.2a-event-extract-eval-v1.1"
    document["owner_spec_commit"] = "abcdef1"
    document["pre_registered_at"] = "2026-08-03T23:00:00Z"
    document["contract_files"]["prompt"] = {
        "path": prompt_path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(prompt.encode()).hexdigest(),
    }
    if forbidden_temperature_drift:
        document["llm"]["temperature"] = 0.3
    active_path = root / "config/p4_event_extract_eval_v1_1.yaml"
    active_path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return active_path


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_strict_artifact_loaders_reject_nonfinite_json_constants(
    tmp_path: Path,
    constant: str,
) -> None:
    json_path = tmp_path / f"{constant}.json"
    jsonl_path = tmp_path / f"{constant}.jsonl"
    payload = f'{{"unsafe":{constant}}}\n'
    json_path.write_text(payload, encoding="utf-8")
    jsonl_path.write_text(payload, encoding="utf-8")

    with pytest.raises(runner.HeldoutPredictionError, match="non-finite JSON"):
        runner._load_json(json_path, "strict fixture JSON")
    with pytest.raises(runner.HeldoutPredictionError, match="non-finite JSON"):
        runner._load_jsonl(jsonl_path, "strict fixture JSONL")


def test_active_contract_rejects_conflicting_duplicate_yaml_key(
    tmp_path: Path,
) -> None:
    design = load_event_evaluation_design()
    _copy_contract_files(tmp_path, design)
    contract_path = tmp_path / "config/p4_event_extract_eval_v1.yaml"
    contract_path.write_bytes(contract_path.read_bytes() + b"\nproduction_writes_allowed: true\n")

    with pytest.raises(runner.HeldoutPredictionError, match=r"duplicate key"):
        runner._load_active_contract(design, tmp_path, contract_path)


def test_active_contract_rejects_merge_key_security_conflict(
    tmp_path: Path,
) -> None:
    design = load_event_evaluation_design()
    _copy_contract_files(tmp_path, design)
    contract_path = tmp_path / "config/p4_event_extract_eval_v1.yaml"
    document = contract_path.read_text(encoding="utf-8")
    document = document.replace(
        "isolation:\n",
        (
            "isolation:\n"
            "  <<: &unsafe_defaults\n"
            "    production_writes_allowed: true\n"
            "  production_writes_allowed: false\n"
        ),
        1,
    )
    contract_path.write_text(document, encoding="utf-8")

    with pytest.raises(runner.HeldoutPredictionError, match=r"duplicate key"):
        runner._load_active_contract(design, tmp_path, contract_path)


def test_dev_final_mode_closes_run_to_freeze_chain_without_heldout_read(
    tmp_path: Path,
) -> None:
    design = load_event_evaluation_design()
    _copy_contract_files(tmp_path, design)
    database = _create_dev_only_database(tmp_path)
    contract_path = tmp_path / "config/p4_event_extract_eval_v1.yaml"
    calls: list[int] = []
    timeline: list[str] = []
    completed_clock = datetime.fromisoformat("2026-08-04T08:30:00+08:00")

    def completion_clock() -> datetime:
        timeline.append("completion_clock")
        return completed_clock

    result = runner.run_dev_final_predictions(
        contract_path,
        project_root=tmp_path,
        design=design,
        settings=_settings(),
        now=datetime.fromisoformat("2026-08-04T08:00:00+08:00"),
        clock=completion_clock,
        chat_json_fn=_fake_chat(
            calls,
            predictions_by_id=_ai_dev_predictions(tmp_path),
            before_call=lambda identifier: timeline.append(f"model:{identifier}"),
        ),
    )

    frozen_rows = [
        json.loads(line)
        for line in _artifact(tmp_path, design, "dev_60_frozen_jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert calls == sorted(int(row["news_item_id"]) for row in frozen_rows)
    assert result.summary.expected_count == 60
    assert result.summary.success_count == 60
    assert result.summary.failure_count == 0
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert set(manifest) == {
        "design_sha256",
        "contract_sha256",
        "predictions_path",
        "predictions_sha256",
        "row_count",
        "success_count",
        "failure_count",
        "ordered_identity_sha256",
        "completed_at_utc",
    }
    assert manifest["row_count"] == 60
    assert manifest["completed_at_utc"] == "2026-08-04T00:30:00Z"
    assert timeline[-1] == "completion_clock"
    assert timeline[-2].startswith("model:")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='news_items'"
        ).fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM trade_proposals").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM broker_orders").fetchone() == (1,)
    assert not _artifact(
        tmp_path,
        design,
        "heldout_candidate_inputs_jsonl",
    ).exists()
    assert not _artifact(
        tmp_path,
        design,
        "heldout_inference_state_jsonl",
    ).exists()

    receipt_path = runner.freeze_prediction_contract(
        contract_path,
        result.predictions_path,
        result.manifest_path,
        project_root=tmp_path,
        design=design,
        now=datetime.fromisoformat("2026-08-04T09:00:00+08:00"),
    )
    receipt, _, active_contract = runner.validate_prediction_contract_freeze(
        design,
        tmp_path,
    )
    assert receipt["dev_final_predictions_contract_sha256"] == active_contract.sha256
    assert datetime.fromisoformat(
        cast(str, receipt["frozen_at_utc"]).replace("Z", "+00:00")
    ) >= datetime.fromisoformat(cast(str, manifest["completed_at_utc"]).replace("Z", "+00:00"))
    assert receipt_path.is_file()

    def must_not_call(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("completed dev-final mode must not call the model again")

    with pytest.raises(
        runner.HeldoutPredictionError,
        match="locked after heldout artifact creation",
    ):
        runner.run_dev_final_predictions(
            contract_path,
            project_root=tmp_path,
            design=design,
            settings=_settings(),
            now=datetime.fromisoformat("2026-08-04T10:00:00+08:00"),
            chat_json_fn=must_not_call,
        )
    assert len(calls) == 60


def test_freeze_receipt_binds_dev_final_and_revalidates_bytes(tmp_path: Path) -> None:
    design = load_event_evaluation_design()
    _copy_contract_files(tmp_path, design)
    receipt_path, predictions_path, _ = _freeze(tmp_path, design)

    receipt, receipt_sha256, active_contract = runner.validate_prediction_contract_freeze(
        design, tmp_path
    )

    assert receipt["design_sha256"] == design.sha256
    assert receipt["contract_sha256"] == design.base_contract.sha256
    assert receipt["dev_final_predictions_contract_sha256"] == active_contract.sha256
    assert receipt["dev_final_predictions_row_count"] == 60
    assert receipt["model"] == "qwen3.6-flash"
    assert len(receipt_sha256) == 64
    with pytest.raises(FileExistsError, match="create-only"):
        runner.freeze_prediction_contract(
            tmp_path / cast(str, receipt["contract_path"]),
            predictions_path,
            tmp_path / cast(str, receipt["dev_final_predictions_manifest_path"]),
            project_root=tmp_path,
            design=design,
        )

    predictions_path.write_bytes(predictions_path.read_bytes() + b"\n")
    with pytest.raises(runner.HeldoutPredictionError, match="dev-final"):
        runner.validate_prediction_contract_freeze(design, tmp_path)
    assert receipt_path.is_file()


def test_freeze_rejects_dev_final_that_did_not_pass_development_gate(
    tmp_path: Path,
) -> None:
    design = load_event_evaluation_design()
    _copy_contract_files(tmp_path, design)
    active_contract = runner._load_active_contract(
        design,
        tmp_path,
        tmp_path / "config/p4_event_extract_eval_v1.yaml",
    )
    predictions_path, manifest_path = _create_dev_final_artifacts(
        tmp_path,
        design,
        active_contract,
    )
    predictions = [
        json.loads(line)
        for line in predictions_path.read_text(encoding="utf-8").splitlines()
    ]
    for row in predictions:
        row["prediction"]["materiality"] = 1
    payload = b"".join(_canonical_json(row) for row in predictions)
    predictions_path.write_bytes(payload)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["predictions_sha256"] = hashlib.sha256(payload).hexdigest()
    manifest_path.write_bytes(_canonical_json(manifest))

    with pytest.raises(
        runner.HeldoutPredictionError,
        match="model interagreement development gate",
    ):
        runner.freeze_prediction_contract(
            active_contract.path,
            predictions_path,
            manifest_path,
            project_root=tmp_path,
            design=design,
            now=datetime.fromisoformat("2026-08-04T08:00:00+08:00"),
        )


def test_versioned_prompt_contract_is_explicit_and_non_prompt_drift_fails(
    tmp_path: Path,
) -> None:
    design = load_event_evaluation_design()
    _copy_contract_files(tmp_path, design)
    active_path = _versioned_active_contract(tmp_path)
    _, _, _ = _freeze(tmp_path, design, active_contract_path=active_path)

    receipt, _, active_contract = runner.validate_prediction_contract_freeze(
        design,
        tmp_path,
    )
    assert active_contract.sha256 != design.base_contract.sha256
    assert receipt["contract_path"] == "config/p4_event_extract_eval_v1_1.yaml"
    assert receipt["prompt_path"] == "config/prompts/p4_news_event_extract_v1_1.txt"
    assert receipt["dev_final_predictions_contract_sha256"] == active_contract.sha256

    drifted = cast(dict[str, Any], yaml.safe_load(active_path.read_bytes()))
    drifted["llm"]["temperature"] = 0.3
    active_path.write_text(
        yaml.safe_dump(drifted, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(
        runner.HeldoutPredictionError,
        match="active LLM controls are invalid",
    ):
        runner.validate_prediction_contract_freeze(design, tmp_path)


def test_v14_contract_and_freeze_receipt_bind_registered_evidence_mode(
    tmp_path: Path,
) -> None:
    design = _v14_fixture_root(tmp_path)
    active_path = (
        tmp_path
        / design.prediction_contract.path.relative_to(runner.PROJECT_ROOT)
    )
    active_contract = runner._load_active_contract(
        design,
        tmp_path,
        active_path,
    )
    assert (
        active_contract.evidence_span_match_mode
        == "unicode_whitespace_elided_contiguous_substring_v1"
    )
    assert (
        design.document["artifacts"]["dev_final_predictions_jsonl"]["path"]
        == "docs/phase4/eval/P4.2a-dev60-final-predictions-v1.3.jsonl"
    )
    assert (
        design.document["artifacts"]["prediction_contract_freeze_receipt_json"]["path"]
        == "docs/phase4/eval/P4.2a-heldout-prediction-contract-freeze-v1.3.json"
    )
    predictions_path, manifest_path = _create_dev_final_artifacts(
        tmp_path,
        design,
        active_contract,
    )
    receipt_path = runner.freeze_prediction_contract(
        active_path,
        predictions_path,
        manifest_path,
        project_root=tmp_path,
        design=design,
        now=datetime.fromisoformat("2026-08-04T20:00:00+08:00"),
    )
    receipt, _, validated = runner.validate_prediction_contract_freeze(
        design,
        tmp_path,
    )

    assert receipt_path.name.endswith("-v1.3.json")
    assert (
        receipt["evidence_span_match_mode"]
        == "unicode_whitespace_elided_contiguous_substring_v1"
    )
    assert (
        validated.evidence_span_match_mode
        == receipt["evidence_span_match_mode"]
    )

    receipt["evidence_span_match_mode"] = "exact_contiguous_substring_v1"
    receipt_path.write_bytes(_canonical_json(receipt))
    with pytest.raises(
        runner.HeldoutPredictionError,
        match="evidence_span_match_mode",
    ):
        runner.validate_prediction_contract_freeze(design, tmp_path)


def test_v14_contract_rejects_unregistered_input_drift(tmp_path: Path) -> None:
    design = _v14_fixture_root(tmp_path)
    active_path = (
        tmp_path
        / design.prediction_contract.path.relative_to(runner.PROJECT_ROOT)
    )
    document = cast(dict[str, Any], yaml.safe_load(active_path.read_bytes()))
    document["input"]["max_original_text_characters"] = 9_999
    active_path.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    altered_design = EventEvaluationDesign(
        path=design.path,
        sha256=design.sha256,
        document=design.document,
        base_contract=design.base_contract,
        prediction_contract=replace(
            design.prediction_contract,
            sha256=hashlib.sha256(active_path.read_bytes()).hexdigest(),
            document=document,
            path=active_path,
        ),
    )

    with pytest.raises(
        runner.HeldoutPredictionError,
        match="outside evidence-span match mode",
    ):
        runner._load_active_contract(altered_design, tmp_path, active_path)


def test_dev_final_global_seal_detects_historical_namespace(
    tmp_path: Path,
) -> None:
    design = _v14_fixture_root(tmp_path)
    historical = (
        tmp_path
        / "docs/phase4/eval/P4.2a-heldout-candidate-inputs-v1.2.jsonl"
    )
    historical.parent.mkdir(parents=True, exist_ok=True)
    historical.write_text("{}\n", encoding="utf-8")

    with pytest.raises(
        runner.HeldoutPredictionError,
        match=r"heldout_candidate_inputs_jsonl@v1\.2",
    ):
        runner._ensure_dev_final_precedes_heldout(design, tmp_path)


def test_dev_final_global_seal_uses_authoritative_v1_1_owner_label_path(
    tmp_path: Path,
) -> None:
    design = _v14_fixture_root(tmp_path)
    legacy_owner_labels = (
        tmp_path
        / "docs/phase4/eval/P4.2a-gold-heldout40-owner-labels-v1.1.jsonl"
    )
    legacy_owner_labels.parent.mkdir(parents=True, exist_ok=True)
    legacy_owner_labels.write_text("{}\n", encoding="utf-8")

    with pytest.raises(
        runner.HeldoutPredictionError,
        match=r"heldout_40_owner_annotations_jsonl@v1\.1",
    ):
        runner._ensure_dev_final_precedes_heldout(design, tmp_path)


def test_time_gate_rejects_before_any_candidate_or_model_access(tmp_path: Path) -> None:
    design = _fixture_root(tmp_path)
    calls: list[int] = []

    with pytest.raises(runner.HeldoutPredictionNotReady):
        runner.run_heldout_predictions(
            project_root=tmp_path,
            design=design,
            settings=_settings(),
            now=datetime.fromisoformat("2026-08-05T23:59:59+08:00"),
            pdf_fetcher=_pdf_fetcher,
            pdf_text_extractor=_pdf_text_extractor,
            chat_json_fn=_fake_chat(calls),
        )

    assert calls == []
    assert not _artifact(tmp_path, design, "heldout_candidate_inputs_jsonl").exists()
    assert not _artifact(tmp_path, design, "heldout_inference_state_jsonl").exists()


def test_whole_candidate_batch_runs_once_with_blind_frozen_inputs(
    tmp_path: Path,
) -> None:
    design = _fixture_root(tmp_path)
    _freeze(tmp_path, design)
    calls: list[int] = []
    state_path = _artifact(tmp_path, design, "heldout_inference_state_jsonl")

    def assert_started_is_durable(identifier: int) -> None:
        if identifier == 424:
            events = [
                json.loads(line) for line in state_path.read_text(encoding="utf-8").splitlines()
            ]
            assert [event["event"] for event in events] == ["inference_started"]

    result = runner.run_heldout_predictions(
        project_root=tmp_path,
        design=design,
        settings=_settings(),
        now=READY,
        pdf_fetcher=_pdf_fetcher,
        pdf_text_extractor=_pdf_text_extractor,
        chat_json_fn=_fake_chat(calls, before_call=assert_started_is_durable),
    )

    assert calls == list(range(424, 464))
    assert result.summary.expected_count == 40
    assert result.summary.success_count == 40
    assert result.summary.failure_count == 0
    assert result.positive_count == 20
    assert result.positive_rate == 0.5

    candidate_rows = [
        json.loads(line)
        for line in result.candidate_inputs_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(candidate_rows) == 40
    assert candidate_rows[0]["news_item_id"] == 424
    assert candidate_rows[0]["body_state"] == "announcement_body"
    assert candidate_rows[0]["body_evidence"]["required"] is True
    assert candidate_rows[0]["content_hash"]
    assert "prediction" not in candidate_rows[0]
    assert "selection_reason" not in candidate_rows[0]
    database_rows, _, _, _ = runner._load_candidate_rows(
        design,
        design.base_contract,
        tmp_path,
    )
    validated = validate_heldout_candidate_inputs(
        candidate_rows,
        rows=database_rows,
        design=runner._frozen_design(design),
        active_contract=design.base_contract,
    )
    assert list(validated) == list(range(424, 464))

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["design"]["sha256"] == design.sha256
    assert manifest["candidate_inputs"]["count"] == 40
    assert len(manifest["candidate_inputs"]["identities"]) == 40
    assert manifest["predictions"]["attempted_count"] == 40
    assert manifest["predictions"]["positive_rate_denominator"] == ("successful_predictions")
    assert manifest["predictions"]["predicted_materiality_gte_2_rate"] == 0.5
    assert "inference_state_sha256" not in manifest
    state = [
        json.loads(line) for line in result.state_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [item["event"] for item in state] == [
        "inference_started",
        "inference_completed",
    ]
    assert state[0]["model_calls_started"] == 0
    assert (
        state[1]["prediction_manifest_sha256"]
        == hashlib.sha256(result.manifest_path.read_bytes()).hexdigest()
    )

    def must_not_call(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("a second held-out model call is forbidden")

    with pytest.raises(runner.HeldoutPredictionError, match="permanently blocks"):
        runner.run_heldout_predictions(
            project_root=tmp_path,
            design=design,
            settings=_settings(),
            now=READY,
            pdf_fetcher=_pdf_fetcher,
            pdf_text_extractor=_pdf_text_extractor,
            chat_json_fn=must_not_call,
        )
    with sqlite3.connect(tmp_path / "data/alphapilot.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM news_items").fetchone() == (40,)
        assert connection.execute("SELECT COUNT(*) FROM trade_proposals").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM broker_orders").fetchone() == (1,)


def test_existing_authoritative_candidate_inputs_are_reused_without_refetch(
    tmp_path: Path,
) -> None:
    design = _fixture_root(tmp_path)
    _freeze(tmp_path, design)
    _, _, active_contract = runner.validate_prediction_contract_freeze(
        design,
        tmp_path,
    )
    fetched: list[str] = []

    def counted_fetcher(url: str, policy: AnnouncementBodyPolicy) -> bytes:
        fetched.append(url)
        return _pdf_fetcher(url, policy)

    runner._prepare_or_validate_candidate_artifact(
        design,
        active_contract,
        tmp_path,
        pdf_fetcher=counted_fetcher,
        pdf_text_extractor=_pdf_text_extractor,
    )
    assert len(fetched) == 1

    def forbidden_refetch(
        _url: str,
        _policy: AnnouncementBodyPolicy,
    ) -> bytes:
        raise AssertionError("frozen CNInfo body must not be fetched a second time")

    calls: list[int] = []
    result = runner.run_heldout_predictions(
        project_root=tmp_path,
        design=design,
        settings=_settings(),
        now=READY,
        pdf_fetcher=forbidden_refetch,
        pdf_text_extractor=_pdf_text_extractor,
        chat_json_fn=_fake_chat(calls),
    )
    assert result.summary.success_count == 40
    assert len(fetched) == 1


def test_failed_prediction_is_safe_and_never_retried(tmp_path: Path) -> None:
    design = _fixture_root(tmp_path)
    _freeze(tmp_path, design)
    calls: list[int] = []

    result = runner.run_heldout_predictions(
        project_root=tmp_path,
        design=design,
        settings=_settings(),
        now=READY,
        pdf_fetcher=_pdf_fetcher,
        pdf_text_extractor=_pdf_text_extractor,
        chat_json_fn=_fake_chat(calls, failed_id=424),
    )

    assert len(calls) == 40
    assert result.summary.success_count == 39
    assert result.summary.failure_count == 1
    failed = json.loads(result.predictions_path.read_text(encoding="utf-8").splitlines()[0])
    assert failed["status"] == "extract_failed"
    assert failed["prediction"] is None
    assert failed["error"] == "request_timeout"
    assert failed["security"]["raw_transport_response_persisted"] is False
    assert "provider secret" not in json.dumps(failed)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["predictions"]["failure_count"] == 1
    assert manifest["predictions"]["failures_by_safe_reason"] == {"request_timeout": 1}

    with pytest.raises(runner.HeldoutPredictionError, match="permanently blocks"):
        runner.run_heldout_predictions(
            project_root=tmp_path,
            design=design,
            settings=_settings(),
            now=READY,
            pdf_fetcher=_pdf_fetcher,
            pdf_text_extractor=_pdf_text_extractor,
            chat_json_fn=_fake_chat(calls),
        )
    assert len(calls) == 40


def test_recovery_can_create_missing_manifest_then_terminal_without_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    design = _fixture_root(tmp_path)
    _freeze(tmp_path, design)
    calls: list[int] = []

    def crash_before_manifest(
        _design: EventEvaluationDesign,
        _project_root: Path,
        _payload: Mapping[str, Any],
    ) -> tuple[Path, str]:
        raise KeyboardInterrupt

    with monkeypatch.context() as context:
        context.setattr(
            runner,
            "_write_or_validate_manifest",
            crash_before_manifest,
        )
        with pytest.raises(KeyboardInterrupt):
            runner.run_heldout_predictions(
                project_root=tmp_path,
                design=design,
                settings=_settings(),
                now=READY,
                pdf_fetcher=_pdf_fetcher,
                pdf_text_extractor=_pdf_text_extractor,
                chat_json_fn=_fake_chat(calls),
            )

    manifest_path = _artifact(
        tmp_path,
        design,
        "heldout_candidate_predictions_manifest_json",
    )
    state_path = _artifact(tmp_path, design, "heldout_inference_state_jsonl")
    assert not manifest_path.exists()
    assert len(state_path.read_text(encoding="utf-8").splitlines()) == 1

    result = runner.finalize_existing_heldout_run(
        project_root=tmp_path,
        design=design,
        now=READY,
    )
    assert result.summary.newly_attempted_count == 0
    assert manifest_path.is_file()
    assert calls == list(range(424, 464))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_safety = runner._settings_safety(_settings())
    assert manifest["trading_safety"]["settings"] == expected_safety
    events = [json.loads(line) for line in state_path.read_text(encoding="utf-8").splitlines()]
    assert events[0]["settings_safety"] == expected_safety
    assert events[-1]["event"] == "inference_completed"
    assert events[-1]["terminal_recovery_without_model_calls"] is True
    assert events[-1]["model_calls"] == 0


def test_terminal_recovery_requires_complete_output_and_calls_no_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    design = _fixture_root(tmp_path)
    _freeze(tmp_path, design)
    calls: list[int] = []

    real_append = runner._append_terminal_state

    def simulate_process_crash(path: Path, event: Mapping[str, Any]) -> None:
        if event.get("event") == "inference_completed":
            raise KeyboardInterrupt
        real_append(path, event)

    with monkeypatch.context() as context:
        context.setattr(runner, "_append_terminal_state", simulate_process_crash)
        with pytest.raises(KeyboardInterrupt):
            runner.run_heldout_predictions(
                project_root=tmp_path,
                design=design,
                settings=_settings(),
                now=READY,
                pdf_fetcher=_pdf_fetcher,
                pdf_text_extractor=_pdf_text_extractor,
                chat_json_fn=_fake_chat(calls),
            )

    state_path = _artifact(tmp_path, design, "heldout_inference_state_jsonl")
    assert len(state_path.read_text(encoding="utf-8").splitlines()) == 1
    assert calls == list(range(424, 464))
    manifest_path = _artifact(
        tmp_path,
        design,
        "heldout_candidate_predictions_manifest_json",
    )
    manifest_before = manifest_path.read_bytes()

    result = runner.finalize_existing_heldout_run(
        project_root=tmp_path,
        design=design,
        now=READY,
    )

    assert result.summary.expected_count == 40
    assert result.summary.newly_attempted_count == 0
    assert calls == list(range(424, 464))
    state = [json.loads(line) for line in state_path.read_text(encoding="utf-8").splitlines()]
    assert state[-1]["event"] == "inference_completed"
    assert state[-1]["terminal_recovery_without_model_calls"] is True
    assert state[-1]["model_calls"] == 0
    assert state[0]["settings_safety"] == runner._settings_safety(_settings())
    assert state[-1]["prediction_manifest_sha256"] == hashlib.sha256(manifest_before).hexdigest()
    assert manifest_path.read_bytes() == manifest_before
    assert not hasattr(runner.finalize_existing_heldout_run, "chat_json_fn")
