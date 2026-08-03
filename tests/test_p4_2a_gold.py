from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from scripts import build_p4_2a_gold_sample as builder
from scripts import evaluate_p4_2a_gold as evaluator

from alphapilot.llm.p4_news_event import EXPECTED_CONTRACT_SHA256

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config/p4_event_extract_eval_v1.yaml"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _row(
    news_item_id: int,
    *,
    source: str,
    bound: bool,
    available_time: datetime | None = None,
) -> builder.NewsRow:
    symbol = f"{news_item_id % 1_000_000:06d}" if bound else None
    title = f"测试资讯 {news_item_id}"
    raw_payload: dict[str, Any]
    if source == "akshare_ths":
        raw_payload = {
            "digest": f"摘要 {news_item_id}",
            "short": f"短讯 {news_item_id}",
        }
    else:
        raw_payload = {}
    return builder.NewsRow(
        news_item_id=news_item_id,
        source=source,
        ingested_symbol=symbol,
        title=title,
        url=(
            f"https://static.cninfo.com.cn/finalpage/2026-08-03/{news_item_id}.PDF"
            if source == "cninfo"
            else f"https://example.test/news/{news_item_id}"
        ),
        published_at=datetime(2026, 8, 3, tzinfo=UTC),
        available_time=available_time or datetime(2026, 8, 3, 2, tzinfo=UTC),
        content_hash=_hash(f"content-{news_item_id}"),
        raw_payload=raw_payload,
    )


def _inventory_rows() -> list[builder.NewsRow]:
    rows: list[builder.NewsRow] = []
    next_id = 1
    for source, bound, count in (
        ("cninfo", True, 24),
        ("akshare_ths", True, 9),
        ("akshare_ths", False, 9),
        ("sina_company_news", True, 9),
        ("sina_company_news", False, 9),
    ):
        for _ in range(count):
            rows.append(_row(next_id, source=source, bound=bound))
            next_id += 1
    return rows


def _inventory_strata(contract: builder.FrozenContract) -> list[builder.Stratum]:
    raw = contract.document["gold_sample"]["inventory_60"]["strata"]
    return builder._contract_strata(raw, label="test.inventory.strata")


def _future_strata(contract: builder.FrozenContract) -> list[builder.Stratum]:
    raw = contract.document["gold_sample"]["future_40"]["per_date_strata"]
    return builder._contract_strata(raw, label="test.future.strata")


def _blind_records(contract: builder.FrozenContract) -> list[dict[str, Any]]:
    selected = [
        builder.SelectedNews(
            row=_row(news_item_id, source="sina_company_news", bound=True),
            sample_group=(
                "inventory_60"
                if news_item_id <= 60
                else (
                    "future_40:2026-08-04"
                    if news_item_id <= 80
                    else "future_40:2026-08-05"
                )
            ),
            trading_date=(
                None
                if news_item_id <= 60
                else date(2026, 8, 4 if news_item_id <= 80 else 5)
            ),
            stratum=builder.Stratum("sina_company_news", "bound", 100, False),
            rank_sha256=_hash(f"rank-{news_item_id}"),
        )
        for news_item_id in range(1, 101)
    ]
    return builder.materialize_selected_rows(selected, contract, starting_index=1)


def _owner_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    completed = copy.deepcopy(records)
    for record in completed:
        record["annotation_status"] = "completed"
        record["annotation_owner"] = "owner"
        record["annotated_at"] = "2026-08-06T01:00:00+08:00"
        record["gold"] = {
            "symbols": [record["ingested_symbol"]],
            "event_type": "other",
            "direction": 0,
            "materiality": 0,
            "evidence_span": str(record["original_text"])[:2],
            "notes": None,
        }
    return completed


def _sample_manifest(
    contract: builder.FrozenContract,
    blind_records: list[dict[str, Any]],
) -> dict[str, Any]:
    inventory_records = blind_records[:60]
    inventory_bytes = builder._json_line_bytes(inventory_records)
    final_bytes = builder._json_line_bytes(blind_records)
    return builder._manifest(
        contract=contract,
        inventory_records=inventory_records,
        all_records=blind_records,
        inventory_bytes=inventory_bytes,
        final_bytes=final_bytes,
        inventory_snapshot={"fixture": "inventory"},
        final_snapshot={"fixture": "final"},
        generated_at=datetime(2026, 8, 6, tzinfo=UTC),
        database_path=PROJECT_DIR / "data/alphapilot.db",
    )


def test_contract_and_deterministic_rank_are_frozen() -> None:
    contract = builder.load_contract(CONFIG_PATH)

    assert contract.sha256 == EXPECTED_CONTRACT_SHA256
    assert (
        builder.deterministic_rank(
            seed="seed",
            group="group",
            source="cninfo",
            symbol_state="bound",
            content_hash="a" * 64,
            news_item_id=7,
        )
        == hashlib.sha256(f"seed|group|cninfo|bound|{'a' * 64}|7".encode()).hexdigest()
    )


def test_inventory_selection_has_exact_quotas_and_fails_short_stratum() -> None:
    contract = builder.load_contract(CONFIG_PATH)
    strata = _inventory_strata(contract)
    rows = _inventory_rows()

    selected = builder.select_stratified_rows(
        rows,
        strata=strata,
        seed="frozen-seed",
        group="inventory_60",
    )

    assert len(selected) == 60
    assert Counter((item.stratum.source, item.stratum.symbol_state) for item in selected) == {
        ("cninfo", "bound"): 24,
        ("akshare_ths", "bound"): 9,
        ("akshare_ths", "null"): 9,
        ("sina_company_news", "bound"): 9,
        ("sina_company_news", "null"): 9,
    }
    rows.remove(
        next(row for row in rows if row.source == "akshare_ths" and row.symbol_state == "bound")
    )
    with pytest.raises(builder.GoldSampleError, match="required=9, available=8"):
        builder.select_stratified_rows(
            rows,
            strata=strata,
            seed="frozen-seed",
            group="inventory_60",
        )


def test_future_selection_uses_each_shanghai_trading_date() -> None:
    contract = builder.load_contract(CONFIG_PATH)
    strata = _future_strata(contract)
    rows: list[builder.NewsRow] = []
    next_id = 424
    for trading_day in (date(2026, 8, 4), date(2026, 8, 5)):
        available = datetime.combine(
            trading_day, datetime.min.time(), tzinfo=builder.SHANGHAI
        ).astimezone(UTC) + timedelta(minutes=30)
        for source, bound, count in (
            ("cninfo", True, 10),
            ("akshare_ths", True, 2),
            ("akshare_ths", False, 3),
            ("sina_company_news", True, 2),
            ("sina_company_news", False, 3),
        ):
            for _ in range(count):
                rows.append(
                    _row(
                        next_id,
                        source=source,
                        bound=bound,
                        available_time=available,
                    )
                )
                next_id += 1

    first = builder.select_stratified_rows(
        rows,
        strata=strata,
        seed="seed",
        group="future_40:2026-08-04",
        trading_date=date(2026, 8, 4),
    )
    second = builder.select_stratified_rows(
        rows,
        strata=strata,
        seed="seed",
        group="future_40:2026-08-05",
        trading_date=date(2026, 8, 5),
    )

    assert len(first) == len(second) == 20
    assert all(
        item.row.available_time.astimezone(builder.SHANGHAI).date() == date(2026, 8, 4)
        for item in first
    )
    assert {item.row.news_item_id for item in first}.isdisjoint(
        item.row.news_item_id for item in second
    )


def test_future_ready_gate_is_timezone_aware_and_not_early() -> None:
    contract = builder.load_contract(CONFIG_PATH)
    boundary = datetime(2026, 8, 6, 0, 10, tzinfo=builder.SHANGHAI)

    with pytest.raises(builder.GoldSampleNotReady):
        builder.require_future_ready(contract, boundary - timedelta(microseconds=1))
    builder.require_future_ready(contract, boundary)
    with pytest.raises(ValueError, match="timezone-aware"):
        builder.require_future_ready(contract, datetime(2026, 8, 6, 0, 10))


def test_cli_does_not_expose_a_future_clock_override() -> None:
    with pytest.raises(SystemExit):
        builder._arguments(
            [
                "--mode",
                "future",
                "--now",
                "2099-01-01T00:00:00+08:00",
            ]
        )


def test_cninfo_body_is_mocked_blind_truncated_and_hash_bound() -> None:
    contract = builder.load_contract(CONFIG_PATH)
    row = _row(245, source="cninfo", bound=True)
    selected = builder.SelectedNews(
        row=row,
        sample_group="inventory_60",
        trading_date=None,
        stratum=builder.Stratum("cninfo", "bound", 1, True),
        rank_sha256="a" * 64,
    )
    extracted = "正文证据" * 4_000
    fetch_calls: list[str] = []

    def fake_fetch(url: str, policy: builder.AnnouncementBodyPolicy) -> bytes:
        fetch_calls.append(url)
        assert policy.tls_verify is True
        assert policy.follow_redirects is False
        return b"%PDF-mocked"

    def fake_extract(
        payload: bytes, policy: builder.AnnouncementBodyPolicy
    ) -> builder.ExtractedPdfText:
        assert payload == b"%PDF-mocked"
        assert policy.max_annotation_text_characters == 14_000
        return builder.extracted_pdf_text_fixture(extracted)

    records = builder.materialize_selected_rows(
        [selected],
        contract,
        starting_index=1,
        pdf_fetcher=fake_fetch,
        pdf_text_extractor=fake_extract,
    )
    record = records[0]
    body = record["body_evidence"]

    assert fetch_calls == [row.url]
    assert record["original_text"] == extracted[:14_000]
    assert record["body_state"] == "announcement_body"
    assert body["full_text_character_count"] == len(extracted)
    assert body["full_text_sha256"] == _hash(extracted)
    assert body["text_truncated"] is True
    assert record["text_sha256"] == _hash(extracted[:14_000])
    assert record["input_sha256"] == builder.compute_input_sha256(record, CONFIG_PATH)
    assert record["annotation_status"] == "pending"
    assert record["annotation_owner"] is None
    assert all(value is None for value in record["gold"].values())
    assert not builder.MODEL_PREDICTION_KEYS.intersection(record)
    record["prediction"] = {"materiality": 3}
    with pytest.raises(builder.GoldSampleError, match="contains model predictions"):
        builder.validate_blind_record(record, contract)
    del record["prediction"]
    record["gold"]["symbols"] = []
    with pytest.raises(builder.GoldSampleError, match="only null gold labels"):
        builder.validate_blind_record(record, contract)


def test_cninfo_failure_blocks_frozen_id_without_replacement() -> None:
    contract = builder.load_contract(CONFIG_PATH)
    selected = [
        builder.SelectedNews(
            row=_row(news_id, source="cninfo", bound=True),
            sample_group="inventory_60",
            trading_date=None,
            stratum=builder.Stratum("cninfo", "bound", 2, True),
            rank_sha256=f"{news_id:064x}",
        )
        for news_id in (245, 246)
    ]
    fetched: list[int] = []

    def fake_fetch(url: str, _policy: builder.AnnouncementBodyPolicy) -> bytes:
        fetched.append(int(Path(url).stem))
        if url.endswith("246.PDF"):
            raise builder.GoldSampleError("mock body failure")
        return b"%PDF-first"

    def fake_extract(
        _payload: bytes, _policy: builder.AnnouncementBodyPolicy
    ) -> builder.ExtractedPdfText:
        return builder.extracted_pdf_text_fixture("足够长的公告正文" * 20)

    with pytest.raises(
        builder.GoldSampleError,
        match="246 body extraction failed; sample blocked without replacement",
    ):
        builder.materialize_selected_rows(
            selected,
            contract,
            starting_index=1,
            pdf_fetcher=fake_fetch,
            pdf_text_extractor=fake_extract,
        )
    assert fetched == [245, 246]


def test_pdf_url_and_artifact_path_fail_closed(tmp_path: Path) -> None:
    policy = builder.announcement_body_policy(builder.load_contract(CONFIG_PATH))
    with pytest.raises(builder.GoldSampleError, match=r"static\.cninfo\.com\.cn"):
        builder.download_cninfo_pdf("https://evil.example/announcement.pdf", policy)

    artifact_root = tmp_path / "eval"
    outside = tmp_path / "outside.jsonl"
    with pytest.raises(ValueError, match="escapes"):
        builder._new_artifact_path(outside, artifact_root)
    output = builder._new_artifact_path(artifact_root / "new.jsonl", artifact_root)
    builder._write_new_bytes(output, b"{}\n")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        builder._new_artifact_path(output, artifact_root)


def test_final_manifest_pair_rolls_back_and_hash_recovers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    final_path = tmp_path / "final.jsonl"
    manifest_path = tmp_path / "manifest.json"
    final_payload = b'{"news_item_id":1}\n'
    final_sha256 = hashlib.sha256(final_payload).hexdigest()

    def manifest(generated_at: str) -> dict[str, Any]:
        return {
            "schema_version": "p4.2a-gold-annotation-manifest-v1",
            "sample_version": "p4.2a-gold-v1",
            "generated_at_utc": generated_at,
            "contract": {"sha256": "a" * 64},
            "artifacts": {"final_jsonl": {"sha256": final_sha256}},
            "frozen_news_item_ids": [1],
            "frozen_items": [{"news_item_id": 1}],
            "strata": [],
            "announcement_body": {},
            "blind_at_creation": {},
            "no_substitution_after_id_freeze": True,
        }

    expected_manifest = manifest("2026-08-06T00:10:00Z")
    manifest_payload = (
        json.dumps(expected_manifest, sort_keys=True).encode("utf-8") + b"\n"
    )
    real_write = builder._write_new_bytes
    real_link = os.link

    def fail_manifest_link(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == manifest_path:
            raise OSError("simulated manifest write failure")
        real_link(source, destination)

    monkeypatch.setattr(os, "link", fail_manifest_link)
    with pytest.raises(OSError, match="simulated"):
        builder._write_new_final_manifest_pair(
            final_path=final_path,
            final_payload=final_payload,
            manifest_path=manifest_path,
            manifest_payload=manifest_payload,
            expected_manifest=expected_manifest,
        )
    assert not final_path.exists()
    assert not manifest_path.exists()

    monkeypatch.setattr(os, "link", real_link)
    real_write(final_path, final_payload)
    observed_final_sha256, observed_manifest_sha256 = (
        builder._write_new_final_manifest_pair(
            final_path=final_path,
            final_payload=final_payload,
            manifest_path=manifest_path,
            manifest_payload=manifest_payload,
            expected_manifest=expected_manifest,
        )
    )
    assert observed_final_sha256 == final_sha256
    assert observed_manifest_sha256 == hashlib.sha256(manifest_payload).hexdigest()

    later_manifest = manifest("2026-08-06T00:20:00Z")
    later_payload = json.dumps(later_manifest, sort_keys=True).encode("utf-8") + b"\n"
    _, recovered_manifest_sha256 = builder._write_new_final_manifest_pair(
        final_path=final_path,
        final_payload=final_payload,
        manifest_path=manifest_path,
        manifest_payload=later_payload,
        expected_manifest=later_manifest,
    )
    assert recovered_manifest_sha256 == hashlib.sha256(manifest_payload).hexdigest()
    assert manifest_path.read_bytes() == manifest_payload


def test_pdf_download_uses_tls_no_redirect_and_bounded_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = builder.announcement_body_policy(builder.load_contract(CONFIG_PATH))
    client_options: dict[str, Any] = {}
    requests: list[tuple[str, str]] = []

    class FakeResponse:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers = {"content-length": "13"}

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def iter_bytes(self) -> list[bytes]:
            return [b"%PDF-", b"mocked!!"]

    class FakeClient:
        def __init__(self, **options: object) -> None:
            client_options.update(options)

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def stream(self, method: str, url: str, *, headers: Mapping[str, str]) -> FakeResponse:
            assert headers == {"Accept": "application/pdf"}
            requests.append((method, url))
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    url = "https://static.cninfo.com.cn/finalpage/2026-08-03/fixture.PDF"

    assert builder.download_cninfo_pdf(url, policy) == b"%PDF-mocked!!"
    assert client_options["verify"] is True
    assert client_options["follow_redirects"] is False
    assert requests == [("GET", url)]


def test_database_open_is_uri_read_only_and_query_only(tmp_path: Path) -> None:
    database = tmp_path / "fixture.db"
    with sqlite3.connect(database) as connection:
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

    with builder.open_read_only_database(database) as connection:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute(
                """
                INSERT INTO news_items (
                    id, source, title, url, available_time, content_hash, raw_payload
                ) VALUES (1, 'fixture', 'title', 'url', '2026-08-03', ?, '{}')
                """,
                ("0" * 64,),
            )


def test_owner_annotations_reject_unknown_model_hint_fields() -> None:
    contract = builder.load_contract(CONFIG_PATH)
    records = _owner_records(_blind_records(contract))

    validated = evaluator.validate_owner_annotations(records, contract)
    assert len(validated) == 100

    records[0]["llm_hint"] = {"materiality": 3}
    with pytest.raises(evaluator.GoldEvaluationError, match="fields drifted"):
        evaluator.validate_owner_annotations(records, contract)


def test_manifest_binds_all_100_unique_ordered_items() -> None:
    contract = builder.load_contract(CONFIG_PATH)
    blind_records = _blind_records(contract)
    owner_records = _owner_records(blind_records)
    annotations = evaluator.validate_owner_annotations(owner_records, contract)
    manifest = _sample_manifest(contract, blind_records)

    evaluator._validate_manifest(manifest, annotations, contract)

    manifest["frozen_items"] = [copy.deepcopy(manifest["frozen_items"][0]) for _ in range(100)]
    with pytest.raises(
        evaluator.GoldEvaluationError,
        match="100 unique ordered frozen_news_item_ids",
    ):
        evaluator._validate_manifest(manifest, annotations, contract)


def test_evaluation_report_path_is_create_only(tmp_path: Path) -> None:
    artifact_root = tmp_path / "eval"
    output = evaluator._new_report_path(artifact_root / "round-1.json", artifact_root)
    evaluator._write_new_json(output, {"passed": False})

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        evaluator._new_report_path(output, artifact_root)


def _metric_records() -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any] | None]]:
    annotations: dict[int, dict[str, Any]] = {}
    predictions: dict[int, dict[str, Any] | None] = {}
    for news_item_id in range(1, 101):
        materiality = 2 if news_item_id <= 10 else 0
        symbols = [f"{news_item_id:06d}"]
        annotations[news_item_id] = {
            "gold": {
                "symbols": symbols,
                "event_type": "other",
                "direction": 0,
                "materiality": materiality,
                "evidence_span": "证据",
                "notes": None,
            }
        }
        predicted_materiality = 2 if news_item_id <= 8 or news_item_id in {11, 12} else 0
        predictions[news_item_id] = {
            "symbols": symbols if news_item_id > 5 else ["999999"],
            "event_type": "other",
            "direction": 0,
            "materiality": predicted_materiality,
            "summary": "摘要",
            "confidence": 0.8,
            "evidence_span": "证据",
        }
    return annotations, predictions


def test_evaluation_metrics_enforce_all_three_thresholds() -> None:
    contract = builder.load_contract(CONFIG_PATH)
    annotations, predictions = _metric_records()

    result = evaluator.evaluate_records(annotations, predictions, contract)

    materiality = result["gates"]["materiality_gte_2_precision"]
    all_symbols = result["gates"]["symbol_all_exact_set_accuracy"]
    bearing = result["gates"]["symbol_bearing_exact_set_accuracy"]
    assert materiality["value"] == 0.8
    assert materiality["passed"] is True
    assert all_symbols["value"] == 0.95
    assert all_symbols["passed"] is True
    assert bearing["value"] == 0.95
    assert bearing["passed"] is True
    assert result["passed"] is True

    predictions[6]["symbols"] = ["999999"]  # type: ignore[index]
    failed = evaluator.evaluate_records(annotations, predictions, contract)
    assert failed["gates"]["symbol_all_exact_set_accuracy"]["value"] == 0.94
    assert failed["passed"] is False


def test_materiality_zero_predicted_positive_is_a_failed_gate() -> None:
    contract = builder.load_contract(CONFIG_PATH)
    annotations, predictions = _metric_records()
    for prediction in predictions.values():
        assert prediction is not None
        prediction["materiality"] = 0

    result = evaluator.evaluate_records(annotations, predictions, contract)

    gate = result["gates"]["materiality_gte_2_precision"]
    assert gate["predicted_positive_count"] == 0
    assert gate["value"] is None
    assert gate["passed"] is False
    assert result["passed"] is False


def test_prediction_join_rejects_input_or_text_hash_mismatch() -> None:
    contract = builder.load_contract(CONFIG_PATH)
    annotations: dict[int, dict[str, Any]] = {}
    predictions: list[dict[str, Any]] = []
    for news_item_id in range(1, 101):
        record = {
            "news_item_id": news_item_id,
            "contract_sha256": contract.sha256,
            "input_sha256": _hash(f"input-{news_item_id}"),
            "text_sha256": _hash(f"text-{news_item_id}"),
            "original_text": "证据正文",
        }
        annotations[news_item_id] = {"record": record}
        predictions.append(
            {
                "news_item_id": news_item_id,
                "contract_sha256": contract.sha256,
                "input_sha256": record["input_sha256"],
                "text_sha256": record["text_sha256"],
                "status": "ok",
                "prediction": {
                    "symbols": [],
                    "event_type": "other",
                    "direction": 0,
                    "materiality": 0,
                    "summary": "摘要",
                    "confidence": 0.8,
                    "evidence_span": "证据",
                },
            }
        )

    joined, extras = evaluator.join_predictions(predictions, annotations, contract)
    assert len(joined) == 100
    assert extras == 0

    predictions[0]["input_sha256"] = "0" * 64
    with pytest.raises(evaluator.GoldEvaluationError, match="input hash differs"):
        evaluator.join_predictions(predictions, annotations, contract)


def test_cli_returns_exit_2_for_threshold_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    contract = builder.load_contract(CONFIG_PATH)

    def fake_evaluate(
        _annotations: Path,
        _predictions: Path,
        _contract: Path,
        _output: Path,
    ) -> dict[str, Any]:
        return {"passed": False}

    monkeypatch.setattr(builder, "load_contract", lambda _path: contract)
    monkeypatch.setattr(evaluator, "evaluate_gold_sample", fake_evaluate)

    assert (
        evaluator.main(
            [
                "--config",
                str(CONFIG_PATH),
                "--annotations",
                str(tmp_path / "annotations.jsonl"),
                "--predictions",
                str(tmp_path / "predictions.jsonl"),
                "--output",
                str(tmp_path / "report.json"),
            ]
        )
        == 2
    )
