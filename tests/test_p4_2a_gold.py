from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest
from scripts import build_p4_2a_gold_sample as builder
from scripts import evaluate_p4_2a_gold as evaluator

from alphapilot.llm.p4_news_eval import (
    EVALUATION_DESIGN_V1_2_PATH,
    EVALUATION_DESIGN_V1_7_PATH,
    EVALUATION_DESIGN_V1_8_PATH,
    EVALUATION_DESIGN_V2_PATH,
    EvaluationDesignAncestor,
    load_event_evaluation_design,
)
from alphapilot.llm.p4_news_event import (
    EXPECTED_CONTRACT_SHA256,
    WHITESPACE_NORMALIZED_EVIDENCE_SPAN_MATCH_MODE,
    load_event_extract_contract,
)

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config/p4_event_extract_eval_v1.yaml"
EVALUATION_DESIGN_V1_3_PATH = PROJECT_DIR / "config/p4_event_evaluation_v1_3.yaml"
EVALUATION_DESIGN_V1_4_PATH = PROJECT_DIR / "config/p4_event_evaluation_v1_4.yaml"


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
            "symbols": (
                [record["ingested_symbol"]]
                if record["ingested_symbol"] is not None
                else []
            ),
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


def test_base_and_active_contract_yaml_reject_conflicting_duplicate_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    duplicate_contract = CONFIG_PATH.read_text(encoding="utf-8").replace(
        "production_writes_allowed: false",
        "production_writes_allowed: false\nproduction_writes_allowed: true",
        1,
    )
    duplicate_path = tmp_path / "duplicate-contract.yaml"
    duplicate_path.write_text(duplicate_contract, encoding="utf-8")

    with pytest.raises(builder.GoldSampleError, match="duplicate keys"):
        builder.load_contract(duplicate_path)

    design = builder.load_evaluation_design()
    duplicate_sha256 = hashlib.sha256(duplicate_path.read_bytes()).hexdigest()
    monkeypatch.setattr(
        builder,
        "load_prediction_contract_freeze_receipt",
        lambda _design: (
            {
                "contract_path": str(duplicate_path),
                "contract_sha256": duplicate_sha256,
            },
            "f" * 64,
        ),
    )
    with pytest.raises(builder.GoldSampleError, match="invalid YAML"):
        builder.load_active_prediction_contract(design)


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


def test_heldout_owner_and_evaluation_entries_enforce_unlock_before_file_access(
    tmp_path: Path,
) -> None:
    locked = datetime.fromisoformat("2026-08-06T00:09:59.999999+08:00")
    ready = datetime.fromisoformat("2026-08-06T00:10:00+08:00")
    missing = tmp_path / "missing.jsonl"

    with pytest.raises(builder.GoldSampleNotReady):
        builder.combine_owner_annotations(
            dev_owner_export=missing,
            heldout_owner_export=missing,
            now=locked,
        )
    with pytest.raises(FileNotFoundError):
        builder.combine_owner_annotations(
            dev_owner_export=missing,
            heldout_owner_export=missing,
            now=ready,
        )

    with pytest.raises(builder.GoldSampleNotReady):
        evaluator.evaluate_gold_sample_v1_1(
            missing,
            tmp_path / "report.json",
            now=locked,
        )
    with pytest.raises(evaluator.GoldEvaluationError, match="artifact must stay"):
        evaluator.evaluate_gold_sample_v1_1(
            missing,
            tmp_path / "report.json",
            now=ready,
        )


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


@pytest.mark.parametrize("mode", ["heldout", "combine-owner"])
def test_active_cli_modes_reject_database_override_before_any_build(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    mode: str,
) -> None:
    result = builder.main(
        [
            "--mode",
            mode,
            "--database",
            str(tmp_path / "untrusted.db"),
        ]
    )

    assert result == 1
    assert f"--database override is forbidden for active {mode} mode" in capsys.readouterr().err


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


def test_negative_pdf_content_length_is_transport_failure_not_ineligibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = builder.announcement_body_policy(builder.load_contract(CONFIG_PATH))

    class FakeResponse:
        status_code = 200
        headers: ClassVar[dict[str, str]] = {"content-length": "-1"}

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def iter_bytes(self) -> list[bytes]:
            raise AssertionError("invalid transport metadata must fail before streaming")

    class FakeClient:
        def __init__(self, **_options: object) -> None:
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def stream(
            self,
            _method: str,
            _url: str,
            *,
            headers: Mapping[str, str],
        ) -> FakeResponse:
            assert headers == {"Accept": "application/pdf"}
            return FakeResponse()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    url = "https://static.cninfo.com.cn/finalpage/2026-08-03/fixture.PDF"

    with pytest.raises(
        builder.GoldSampleError,
        match="Content-Length must be non-negative",
    ) as caught:
        builder.download_cninfo_pdf(url, policy)
    assert not isinstance(caught.value, builder.CandidateDocumentIneligible)


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


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_all_gold_json_loaders_reject_non_finite_numbers(
    tmp_path: Path,
    constant: str,
) -> None:
    jsonl_path = tmp_path / "malicious.jsonl"
    json_path = tmp_path / "malicious.json"
    payload = f'{{"prediction":{{"confidence":{constant}}}}}\n'
    jsonl_path.write_text(payload, encoding="utf-8")
    json_path.write_text(payload, encoding="utf-8")

    with pytest.raises(builder.GoldSampleError, match="invalid"):
        builder._load_jsonl(jsonl_path)
    with pytest.raises(builder.GoldSampleError, match="invalid"):
        builder._load_json_with_sha256(json_path, label="prediction manifest")
    with pytest.raises(evaluator.GoldEvaluationError, match="invalid JSON"):
        evaluator._read_jsonl(jsonl_path, label="owner annotations")
    with pytest.raises(evaluator.GoldEvaluationError, match="invalid UTF-8 JSON"):
        evaluator._read_json(json_path, label="owner completion manifest")
    with pytest.raises(builder.GoldSampleError, match="invalid JSON"):
        builder._json_object(
            f'{{"confidence":{constant}}}',
            label="news_items.raw_payload",
        )
    with pytest.raises(FileExistsError, match="invalid JSON"):
        builder._read_manifest_for_recovery(json_path)


def test_owner_annotations_reject_unknown_model_hint_fields() -> None:
    contract = builder.load_contract(CONFIG_PATH)
    records = _owner_records(_blind_records(contract))

    validated = evaluator.validate_owner_annotations(records, contract)
    assert len(validated) == 100

    records[0]["llm_hint"] = {"materiality": 3}
    with pytest.raises(evaluator.GoldEvaluationError, match="fields drifted"):
        evaluator.validate_owner_annotations(records, contract)


def test_owner_annotations_require_timezone_aware_completion_time() -> None:
    contract = builder.load_contract(CONFIG_PATH)
    records = _owner_records(_blind_records(contract))
    records[0]["annotated_at"] = "2026-08-06T01:00:00"

    with pytest.raises(evaluator.GoldEvaluationError, match="must include a timezone"):
        evaluator.validate_owner_annotations(records, contract)


def test_v1_2_heldout_annotations_require_human_adjudication_provenance() -> None:
    design = load_event_evaluation_design(EVALUATION_DESIGN_V1_2_PATH)
    records = _owner_records(_blind_records(design.base_contract)[:40])
    for record in records:
        record["sample_group"] = "heldout40"
        record["annotation_type"] = "ai_drafted_human_adjudicated"
        record["drafter_id"] = "ChatGPT GPT-5.6 Pro"
        record["adjudicator_id"] = "owner-ouyang"
        record["annotation_owner"] = "owner-ouyang"

    validated = evaluator.validate_owner_annotations(
        records,
        design.base_contract,
        expected_count=40,
        expected_sample_group="heldout40",
        design=design,
    )
    assert len(validated) == 40

    records[0]["adjudicator_id"] = records[0]["drafter_id"]
    records[0]["annotation_owner"] = records[0]["drafter_id"]
    with pytest.raises(
        evaluator.GoldEvaluationError,
        match="human adjudicator must differ",
    ):
        evaluator.validate_owner_annotations(
            records,
            design.base_contract,
            expected_count=40,
            expected_sample_group="heldout40",
            design=design,
        )


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
                "model": "qwen3.6-flash",
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
                "--scope",
                "legacy-v1",
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


def test_legacy_evaluator_rejects_v2_before_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "must-not-exist.json"

    result = evaluator.main(
        [
            "--scope",
            "legacy-v1",
            "--evaluation-design",
            str(EVALUATION_DESIGN_V2_PATH),
            "--output",
            str(output),
        ]
    )

    assert result == 1
    assert not output.exists()
    assert "dedicated dev45/heldout60 scorer" in capsys.readouterr().err


def test_legacy_inventory_builder_rejects_v2_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        builder,
        "build_inventory_sample",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy inventory builder must not run")
        ),
    )

    result = builder.main(
        [
            "--mode",
            "inventory",
            "--evaluation-design",
            str(EVALUATION_DESIGN_V2_PATH),
        ]
    )

    assert result == 1
    assert "dedicated two-stratum builder" in capsys.readouterr().err


def test_preflight_runner_aggregates_independent_failures_and_blocks_dependents() -> None:
    runner = evaluator._PreflightRunner(collect_all=True)

    def fail_first() -> None:
        raise evaluator.GoldEvaluationError("first-safe-failure")

    def fail_second() -> None:
        raise ValueError("second-safe-failure")

    runner.run("first", (), fail_first)
    runner.run("second", (), fail_second)
    runner.run("dependent", ("first",), lambda: True)

    assert runner.stages == [
        {
            "name": "first",
            "status": "failed",
            "error_type": "GoldEvaluationError",
            "safe_message": "first-safe-failure",
        },
        {
            "name": "second",
            "status": "failed",
            "error_type": "ValueError",
            "safe_message": "second-safe-failure",
        },
        {"name": "dependent", "status": "blocked", "blocked_by": ["first"]},
    ]


def test_dry_run_report_path_validation_is_read_only_and_fail_closed(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "eval"
    artifact_root.mkdir()
    prospective = artifact_root / "not-created" / "report.json"

    assert (
        evaluator._validate_report_path_readonly(prospective, artifact_root)
        == prospective
    )
    assert not prospective.parent.exists()
    assert not prospective.exists()

    existing = artifact_root / "existing.json"
    existing.write_text("{}\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        evaluator._validate_report_path_readonly(existing, artifact_root)
    with pytest.raises(evaluator.GoldEvaluationError, match="must stay"):
        evaluator._validate_report_path_readonly(
            tmp_path / "outside.json",
            artifact_root,
        )

    symlink = artifact_root / "linked"
    symlink.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(evaluator.GoldEvaluationError, match="symlink"):
        evaluator._validate_report_path_readonly(
            symlink / "report.json",
            artifact_root,
        )

    root_symlink = tmp_path / "eval-link"
    root_symlink.symlink_to(artifact_root, target_is_directory=True)
    with pytest.raises(evaluator.GoldEvaluationError, match="non-symlink"):
        evaluator._validate_report_path_readonly(
            root_symlink / "report.json",
            root_symlink,
        )


def test_heldout_dry_run_scores_only_synthetic_inputs_and_never_mutates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    design = builder.load_evaluation_design(EVALUATION_DESIGN_V1_8_PATH)
    real_annotations: dict[int, dict[str, Any]] = {}
    real_predictions: dict[int, dict[str, Any]] = {}
    for news_item_id in range(1, 101):
        record = {
            "news_item_id": news_item_id,
            "sample_index": news_item_id,
            "sample_group": (
                "inventory_60" if news_item_id <= 60 else "heldout40"
            ),
        }
        real_annotations[news_item_id] = {
            "record": record,
            "gold": {
                "symbols": [f"{news_item_id:06d}"],
                "materiality": 0,
            },
        }
        real_predictions[news_item_id] = {
            "symbols": [f"{news_item_id:06d}"],
            "materiality": 0,
        }
    fake_preflight = SimpleNamespace(
        design=design,
        annotations=real_annotations,
        predictions=real_predictions,
        active_contract=object(),
        dev_annotations={
            news_item_id: real_annotations[news_item_id]
            for news_item_id in range(1, 61)
        },
        dev_prediction_records=[
            {"news_item_id": news_item_id} for news_item_id in range(1, 61)
        ],
    )
    stages = [{"name": "prediction_join", "status": "passed"}]
    monkeypatch.setattr(
        evaluator,
        "_run_heldout_preflight",
        lambda *_args, **_kwargs: (fake_preflight, copy.deepcopy(stages)),
    )

    observed: dict[str, object] = {}
    actual_evaluate_split_records = evaluator.evaluate_split_records

    def score_synthetic_only(
        annotations: Mapping[int, dict[str, Any]],
        predictions: Mapping[int, dict[str, Any] | None],
        loaded_design: builder.FrozenEvaluationDesign,
    ) -> dict[str, Any]:
        assert annotations is not real_annotations
        assert predictions is not real_predictions
        assert loaded_design is design
        assert all(
            annotation.get("synthetic_metric_fixture") is True
            for annotation in annotations.values()
        )
        assert all(
            annotation["gold"] == predictions[news_item_id]
            for news_item_id, annotation in annotations.items()
        )
        observed["scoring_annotations"] = annotations
        observed["scoring_predictions"] = predictions
        return actual_evaluate_split_records(annotations, predictions, loaded_design)

    def report_extensions(**kwargs: object) -> dict[str, object]:
        assert kwargs["annotations"] is observed["scoring_annotations"]
        assert kwargs["predictions"] is observed["scoring_predictions"]
        observed["extensions"] = True
        return {}

    def offline_diagnostics(
        loaded_design: builder.FrozenEvaluationDesign,
        gold_ids: set[int],
    ) -> dict[str, object]:
        assert loaded_design is design
        assert gold_ids == set(range(1, 101))
        observed["diagnostics"] = True
        return {"synthetic": True}

    def assemble_report(
        preflight: object,
        result: Mapping[str, object],
        **kwargs: object,
    ) -> dict[str, object]:
        assert preflight is fake_preflight
        assert isinstance(result["passed"], bool)
        assert kwargs["annotations"] is observed["scoring_annotations"]
        assert kwargs["predictions"] is observed["scoring_predictions"]
        assert kwargs["versioned_extensions"] == {}
        assert kwargs["offline_diagnostics"] == {"synthetic": True}
        observed["assembly"] = True
        return {"synthetic_report": True}

    def required_fields(
        report: Mapping[str, object],
        loaded_design: builder.FrozenEvaluationDesign,
    ) -> None:
        assert report == {"synthetic_report": True}
        assert loaded_design is design
        observed["required_fields"] = True

    monkeypatch.setattr(evaluator, "evaluate_split_records", score_synthetic_only)
    monkeypatch.setattr(evaluator, "_v1_3_report_extensions", report_extensions)
    monkeypatch.setattr(evaluator, "_offline_trial_diagnostics", offline_diagnostics)
    monkeypatch.setattr(evaluator, "_assemble_heldout_report", assemble_report)
    monkeypatch.setattr(evaluator, "validate_required_report_fields", required_fields)

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dry-run performed a filesystem mutation")

    for name in (
        "_new_report_path",
        "_claim_evaluation_one_shot",
        "_append_evaluation_terminal",
        "_write_new_json",
    ):
        monkeypatch.setattr(evaluator, name, unexpected)

    output = tmp_path / "never-created" / "report.json"
    result = evaluator.dry_run_heldout_evaluation(
        tmp_path / "annotations.jsonl",
        output,
        EVALUATION_DESIGN_V1_7_PATH,
    )

    assert result["status"] == "passed"
    assert result["one_shot_consumed"] is False
    assert result["metrics_computed"] is False
    assert result["evaluation_passed"] is None
    assert result["synthetic_metric_assembly"] is True
    assert result["report_created"] is False
    assert result["filesystem_mutations"] == 0
    assert result["validated_through"] == "report_serialization_in_memory"
    assert {"metrics", "gates", "passed"}.isdisjoint(result)
    assert observed == {
        "scoring_annotations": observed["scoring_annotations"],
        "scoring_predictions": observed["scoring_predictions"],
        "extensions": True,
        "diagnostics": True,
        "assembly": True,
        "required_fields": True,
    }
    stage_statuses = {stage["name"]: stage["status"] for stage in result["stages"]}
    assert stage_statuses == {
        "prediction_join": "passed",
        "synthetic_metric_inputs": "passed",
        "synthetic_metric_assembly": "passed",
        "report_extensions_dev_only": "passed",
        "offline_diagnostics": "passed",
        "report_payload_assembly": "passed",
        "required_report_fields": "passed",
        "canonical_serialization_in_memory": "passed",
        "real_heldout_metrics": "not_run_one_shot_protected",
    }
    assert not output.parent.exists()


def _v1_8_frozen_selection_fixture() -> SimpleNamespace:
    design = builder.load_evaluation_design(EVALUATION_DESIGN_V1_8_PATH)
    active_contract, receipt, receipt_sha256 = (
        builder.load_active_prediction_contract(design)
    )
    inference, inference_state_sha256 = builder.load_completed_one_shot_state(
        design,
        scope="inference",
    )
    annotation_path = builder.evaluation_artifact_path(
        design,
        "combined_100_annotations_jsonl",
    )
    annotation_records, annotation_sha256 = evaluator._read_jsonl(
        annotation_path,
        label="v1.8 frozen combined annotations",
    )
    annotations = evaluator.validate_owner_annotations(
        annotation_records,
        design.base_contract,
        design=design,
    )
    selection_path = builder.evaluation_artifact_path(
        design,
        "heldout_selection_manifest_json",
    )
    selection_manifest, selection_manifest_sha256 = evaluator._read_json(
        selection_path,
        label="v1.8 inherited heldout selection manifest",
    )
    materialization = dict(
        evaluator._mapping(
            selection_manifest.get("materialization"),
            label="v1.8 inherited materialization binding",
        )
    )
    candidate_inputs_path = builder.evaluation_artifact_path(
        design,
        "heldout_candidate_inputs_jsonl",
    )
    candidate_predictions_path = builder.evaluation_artifact_path(
        design,
        "heldout_candidate_predictions_jsonl",
    )
    candidate_prediction_manifest_path = builder.evaluation_artifact_path(
        design,
        "heldout_candidate_predictions_manifest_json",
    )
    selection_evidence = evaluator._validate_selection_manifest(
        selection_manifest,
        manifest_sha256=selection_manifest_sha256,
        design=design,
        annotations=annotations,
        active_contract=active_contract,
        receipt_sha256=receipt_sha256,
        candidate_inputs_sha256=evaluator._sha256_file(candidate_inputs_path),
        candidate_predictions_sha256=evaluator._sha256_file(
            candidate_predictions_path
        ),
        candidate_prediction_manifest_sha256=evaluator._sha256_file(
            candidate_prediction_manifest_path
        ),
        inference_state_sha256=inference_state_sha256,
        materialization_binding=materialization,
    )
    return SimpleNamespace(
        design=design,
        active_contract=active_contract,
        receipt=receipt,
        receipt_sha256=receipt_sha256,
        inference=inference,
        inference_state_sha256=inference_state_sha256,
        annotation_path=annotation_path,
        annotation_records=annotation_records,
        annotation_sha256=annotation_sha256,
        annotations=annotations,
        selection_manifest=selection_manifest,
        selection_manifest_sha256=selection_manifest_sha256,
        selection_evidence=selection_evidence,
        materialization=materialization,
        candidate_inputs_sha256=evaluator._sha256_file(candidate_inputs_path),
        candidate_predictions_sha256=evaluator._sha256_file(
            candidate_predictions_path
        ),
        candidate_prediction_manifest_sha256=evaluator._sha256_file(
            candidate_prediction_manifest_path
        ),
    )


def test_v1_8_frozen_inference_and_selection_require_all_15_lineage_scopes() -> None:
    frozen = _v1_8_frozen_selection_fixture()
    design = frozen.design
    required_scopes = builder.HELDOUT_EVALUATION_INPUT_DESIGN_SCOPES

    assert len(required_scopes) == 15
    assert frozen.inference_state_sha256 == (
        "44253bfb643458ebf2b1e86ef5bddcdf4d01469ebf60ffcb02c2096ad54cfbe3"
    )
    assert frozen.selection_manifest_sha256 == (
        "9da50ea8720b01b58c6d19eb9d7b11705a0c561c61da432543e2ab5644b3abe1"
    )
    assert frozen.selection_evidence["selected_count"] == 40
    assert {
        event["design_sha256"] for event in frozen.inference["events"]
    } == {"4c7964ad547820f5672631939af93978f11cb9f91e5921087770ac7d0d79bec1"}

    direct_parent = design.ancestor_designs[0]
    assert direct_parent.sha256 == (
        "4c7964ad547820f5672631939af93978f11cb9f91e5921087770ac7d0d79bec1"
    )
    incomplete_design = replace(
        design,
        ancestor_designs=(
            replace(
                direct_parent,
                byte_frozen_scopes=direct_parent.byte_frozen_scopes
                - {next(iter(required_scopes))},
            ),
            *design.ancestor_designs[1:],
        ),
    )
    with pytest.raises(builder.GoldSampleError, match="design hash drifted"):
        builder.load_completed_one_shot_state(
            incomplete_design,
            scope="inference",
        )
    with pytest.raises(
        evaluator.GoldEvaluationError,
        match="artifact bindings drifted",
    ):
        evaluator._validate_selection_manifest(
            frozen.selection_manifest,
            manifest_sha256=frozen.selection_manifest_sha256,
            design=incomplete_design,
            annotations=frozen.annotations,
            active_contract=frozen.active_contract,
            receipt_sha256=frozen.receipt_sha256,
            candidate_inputs_sha256=frozen.candidate_inputs_sha256,
            candidate_predictions_sha256=frozen.candidate_predictions_sha256,
            candidate_prediction_manifest_sha256=(
                frozen.candidate_prediction_manifest_sha256
            ),
            inference_state_sha256=frozen.inference_state_sha256,
            materialization_binding=frozen.materialization,
        )

    unrelated_manifest = copy.deepcopy(frozen.selection_manifest)
    unrelated_manifest["design"]["sha256"] = "f" * 64
    with pytest.raises(
        evaluator.GoldEvaluationError,
        match="artifact bindings drifted",
    ):
        evaluator._validate_selection_manifest(
            unrelated_manifest,
            manifest_sha256=frozen.selection_manifest_sha256,
            design=design,
            annotations=frozen.annotations,
            active_contract=frozen.active_contract,
            receipt_sha256=frozen.receipt_sha256,
            candidate_inputs_sha256=frozen.candidate_inputs_sha256,
            candidate_predictions_sha256=frozen.candidate_predictions_sha256,
            candidate_prediction_manifest_sha256=(
                frozen.candidate_prediction_manifest_sha256
            ),
            inference_state_sha256=frozen.inference_state_sha256,
            materialization_binding=frozen.materialization,
        )


def test_v1_8_formal_path_rehearsal_uses_synthetic_scores_and_tmp_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = _v1_8_frozen_selection_fixture()
    design = frozen.design
    owner_completion = evaluator.validate_owner_completion_manifest(
        design,
        annotation_path=frozen.annotation_path,
        annotation_records=frozen.annotation_records,
        annotation_sha256=frozen.annotation_sha256,
    )
    dev_final = builder.validate_dev_final_prediction_freeze(
        design,
        active_contract=frozen.active_contract,
        receipt=frozen.receipt,
    )

    synthetic_annotations = copy.deepcopy(frozen.annotations)
    synthetic_predictions: dict[int, dict[str, Any] | None] = {}
    synthetic_by_id: dict[int, dict[str, Any]] = {}
    for news_item_id, annotation in synthetic_annotations.items():
        original_text = str(annotation["record"]["original_text"])
        evidence_span = original_text[: min(24, len(original_text))]
        assert evidence_span
        synthetic_gold = {
            "symbols": [],
            "event_type": "other",
            "direction": 0,
            "materiality": 2,
            "evidence_span": evidence_span,
            "notes": None,
        }
        synthetic_prediction = {
            "symbols": [],
            "event_type": "other",
            "direction": 0,
            "materiality": 2,
            "summary": "synthetic rehearsal only",
            "confidence": 0.5,
            "evidence_span": evidence_span,
        }
        annotation["gold"] = synthetic_gold
        annotation["synthetic_metric_fixture"] = True
        synthetic_predictions[news_item_id] = synthetic_prediction
        synthetic_by_id[news_item_id] = synthetic_prediction

    dev_prediction_path = builder.evaluation_artifact_path(
        design,
        "dev_final_predictions_jsonl",
    )
    dev_prediction_records, _dev_prediction_sha256 = evaluator._read_jsonl(
        dev_prediction_path,
        label="v1.8 inherited dev predictions for synthetic rehearsal",
    )
    synthetic_dev_prediction_records = copy.deepcopy(dev_prediction_records)
    for row in synthetic_dev_prediction_records:
        news_item_id = int(row["news_item_id"])
        row["status"] = "ok"
        row["prediction"] = copy.deepcopy(synthetic_by_id[news_item_id])

    dev_ids = list(synthetic_annotations)[:60]
    synthetic_dev_annotations = {
        news_item_id: synthetic_annotations[news_item_id]
        for news_item_id in dev_ids
    }
    failed_v1_7_state = (
        PROJECT_DIR
        / "docs/phase4/eval/P4.2a-heldout-evaluation-one-shot-v1.7.state.jsonl"
    )
    failed_v1_7_sha256 = evaluator._sha256_file(failed_v1_7_state)
    production_v1_8_state = builder.evaluation_artifact_path(
        design,
        "heldout_evaluation_state_jsonl",
    )
    production_v1_8_state_existed = production_v1_8_state.exists()
    production_v1_8_state_sha256 = (
        evaluator._sha256_file(production_v1_8_state)
        if production_v1_8_state_existed
        else None
    )

    with tempfile.TemporaryDirectory(
        prefix=".p4-2a-v18-rehearsal-",
        dir=PROJECT_DIR,
    ) as temporary_directory:
        rehearsal_root = Path(temporary_directory)
        assert not rehearsal_root.is_relative_to(
            (PROJECT_DIR / "docs/phase4/eval").resolve()
        )
        rehearsal_state = rehearsal_root / "replacement.state.jsonl"
        rehearsal_output = rehearsal_root / "reports/replacement.json"
        synthetic_preflight = evaluator.HeldoutEvaluationPreflight(
            design=design,
            artifact_root=rehearsal_root,
            annotation_resolved=frozen.annotation_path,
            annotation_sha256=frozen.annotation_sha256,
            annotations=synthetic_annotations,
            owner_completion=owner_completion,
            adjudication_evidence=None,
            active_contract=frozen.active_contract,
            receipt=frozen.receipt,
            receipt_sha256=frozen.receipt_sha256,
            dev_final=dev_final,
            inference=frozen.inference,
            selection_evidence=frozen.selection_evidence,
            materialization_binding=frozen.materialization,
            dev_annotations=synthetic_dev_annotations,
            dev_prediction_records=synthetic_dev_prediction_records,
            predictions=synthetic_predictions,
            state_path=rehearsal_state,
        )
        monkeypatch.setattr(
            evaluator,
            "_run_heldout_preflight",
            lambda *_args, **_kwargs: (synthetic_preflight, []),
        )

        result = evaluator.evaluate_gold_sample_v1_1(
            frozen.annotation_path,
            rehearsal_output,
            EVALUATION_DESIGN_V1_8_PATH,
            now=datetime.fromisoformat("2026-08-09T16:00:00+08:00"),
        )

        assert rehearsal_output.is_file()
        assert rehearsal_state.is_file()
        events = [
            json.loads(line)
            for line in rehearsal_state.read_text(encoding="utf-8").splitlines()
        ]
        assert [event["event"] for event in events] == [
            "evaluation_started",
            "evaluation_completed",
        ]
        assert all(event["design_sha256"] == design.sha256 for event in events)
        assert result["report_artifact"]["sha256"] == evaluator._sha256_file(
            rehearsal_output
        )
        assert result["phase_gate"] == {
            "p4_2a_evaluation_passed": result["passed"],
            "p4_2b_unlocked": False,
            "production_writes_performed": False,
            "proposals_or_orders_created": False,
        }
        assert result["input_identity"]["dual_hash_identity"][
            "distinct_hash_pair_count"
        ] == 60
        assert result["diagnostics"]["offline_trial"][
            "gold_intersection_failure_ids"
        ] == [190]
        assert all(
            annotation.get("synthetic_metric_fixture") is True
            for annotation in synthetic_preflight.annotations.values()
        )

    assert evaluator._sha256_file(failed_v1_7_state) == failed_v1_7_sha256
    assert production_v1_8_state.exists() is production_v1_8_state_existed
    if production_v1_8_state_sha256 is not None:
        assert (
            evaluator._sha256_file(production_v1_8_state)
            == production_v1_8_state_sha256
        )


def test_cli_dry_run_routes_around_formal_evaluation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    design = builder.load_evaluation_design(EVALUATION_DESIGN_V1_7_PATH)
    monkeypatch.setattr(builder, "load_evaluation_design", lambda _path: design)
    monkeypatch.setattr(
        evaluator,
        "dry_run_heldout_evaluation",
        lambda *_args, **_kwargs: {"status": "passed"},
    )
    monkeypatch.setattr(
        evaluator,
        "evaluate_gold_sample_v1_1",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("formal evaluation must not run")
        ),
    )

    result = evaluator.main(
        [
            "--scope",
            "heldout-final-v1.7",
            "--evaluation-design",
            str(EVALUATION_DESIGN_V1_7_PATH),
            "--dry-run",
            "--output",
            str(tmp_path / "report.json"),
        ]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["scope"] == "heldout-final-v1.7"


def test_v1_1_positive_pool_selection_is_exact_and_fails_short_pool() -> None:
    records: list[dict[str, Any]] = []
    for news_item_id in range(424, 474):
        ok = news_item_id != 430
        materiality = 2 if news_item_id < 470 else 1
        records.append(
            {
                "news_item_id": news_item_id,
                "status": "ok" if ok else "extract_failed",
                "input_sha256": _hash(f"input-{news_item_id}"),
                "text_sha256": _hash(f"text-{news_item_id}"),
                "prediction": (
                    {
                        "symbols": [],
                        "event_type": "other",
                        "direction": 0,
                        "materiality": materiality,
                        "summary": "摘要",
                        "confidence": 0.8,
                        "evidence_span": "证据",
                    }
                    if ok
                    else None
                ),
            }
        )

    selected, evidence = builder.select_heldout_positive_predictions(
        records,
        seed="registered-seed",
        count=40,
    )

    expected = sorted(
        (
            builder.heldout_prediction_rank(
                seed="registered-seed",
                news_item_id=int(record["news_item_id"]),
                input_sha256=str(record["input_sha256"]),
            ),
            int(record["news_item_id"]),
        )
        for record in records
        if record["status"] == "ok"
        and isinstance(record["prediction"], Mapping)
        and int(record["prediction"]["materiality"]) >= 2
    )[:40]
    assert [record["news_item_id"] for record in selected] == [
        news_item_id for _, news_item_id in expected
    ]
    assert [item["selection_rank_sha256"] for item in evidence] == [
        rank for rank, _ in expected
    ]
    assert all("text_sha256" in item for item in evidence)

    with pytest.raises(builder.GoldSampleError, match="insufficient"):
        builder.select_heldout_positive_predictions(
            records,
            seed="registered-seed",
            count=46,
        )


def test_v1_1_candidate_body_is_frozen_once_for_prediction_and_owner() -> None:
    design = builder.load_evaluation_design()
    active_contract = load_event_extract_contract(CONFIG_PATH)
    row = _row(
        500,
        source="cninfo",
        bound=True,
        available_time=datetime(2026, 8, 4, 2, tzinfo=UTC),
    )
    fetch_count = 0

    def fake_fetch(_url: str, _policy: builder.AnnouncementBodyPolicy) -> bytes:
        nonlocal fetch_count
        fetch_count += 1
        return b"%PDF-heldout"

    def fake_extract(
        _payload: bytes,
        _policy: builder.AnnouncementBodyPolicy,
    ) -> builder.ExtractedPdfText:
        return builder.extracted_pdf_text_fixture("同一份公告正文证据" * 20)

    candidates = builder.materialize_heldout_candidate_inputs(
        [row],
        design,
        active_contract,
        pdf_fetcher=fake_fetch,
        pdf_text_extractor=fake_extract,
    )
    assert candidates.reason_counts == {}
    assert len(candidates.all_candidates) == 1
    assert candidates.ineligible_candidates == ()
    candidate = candidates.eligible_records[0]
    owner = builder._heldout_owner_record(
        candidate=candidate,
        row=row,
        base_contract=design.base_contract,
        sample_index=1,
    )

    assert fetch_count == 1
    assert owner["original_text"] == candidate["original_text"]
    assert owner["text_sha256"] == candidate["text_sha256"]
    assert owner["input_sha256"] == candidate["input_sha256"]
    assert owner["body_evidence"] == candidate["body_evidence"]
    assert not builder.owner_forbidden_field_paths(
        owner,
        frozenset(design.document["owner_delivery"]["forbidden_fields"]),
    )
    assert set(owner["stratum"]) == builder.STRATUM_FIELDS
    assert set(owner["body_evidence"]) == builder.BODY_EVIDENCE_FIELDS

    leaked = copy.deepcopy(owner)
    leaked["body_evidence"]["selection_reason"] = "model_positive"
    with pytest.raises(builder.GoldSampleError, match="body_evidence fields drifted"):
        builder.validate_blind_record(leaked, design.base_contract)


def _eligibility_fixture_design() -> builder.FrozenEvaluationDesign:
    design = builder.load_evaluation_design()
    document = copy.deepcopy(design.document)
    document["candidate_eligibility"] = {
        "schema_version": "p4.2a-heldout-candidate-eligibility-v1",
        "deterministic_document_ineligible_reasons": [
            "pdf_text_below_min_char_gate",
            "pdf_exceeds_size_bound",
        ],
        "minimum_extracted_characters": 80,
        "max_pdf_bytes": 8 * 1024 * 1024,
        "transient_download_failures_fail_closed": True,
        "sample_only_from_eligible_pool": True,
        "insufficient_stratum_policy": "fail_without_substitution",
    }
    return builder.FrozenEvaluationDesign(
        path=design.path,
        sha256="1" * 64,
        document=document,
        base_contract=design.base_contract,
    )


def test_heldout_materialization_excludes_only_deterministic_pdf_properties() -> None:
    design = _eligibility_fixture_design()
    active_contract = load_event_extract_contract(CONFIG_PATH)
    rows = [
        _row(500, source="cninfo", bound=True),
        _row(501, source="cninfo", bound=True),
        _row(502, source="akshare_ths", bound=True),
    ]
    short_pdf = b"%PDF-short-scan"
    oversized_pdf = b"%PDF-" + (b"x" * (8 * 1024 * 1024))

    def fake_fetch(url: str, _policy: builder.AnnouncementBodyPolicy) -> bytes:
        if url.endswith("/500.PDF"):
            return short_pdf
        if url.endswith("/501.PDF"):
            return oversized_pdf
        raise AssertionError(f"unexpected PDF URL {url}")

    def fake_extract(
        payload: bytes,
        _policy: builder.AnnouncementBodyPolicy,
    ) -> builder.ExtractedPdfText:
        assert payload == short_pdf
        return builder.extracted_pdf_text_fixture("扫描件")

    result = builder.materialize_heldout_candidate_inputs(
        rows,
        design,
        active_contract,
        pdf_fetcher=fake_fetch,
        pdf_text_extractor=fake_extract,
    )

    assert [item["news_item_id"] for item in result.all_candidates] == [500, 501, 502]
    assert [item["news_item_id"] for item in result.eligible_records] == [502]
    assert result.reason_counts == {
        "pdf_exceeds_size_bound": 1,
        "pdf_text_below_min_char_gate": 1,
    }
    excluded = {int(item["news_item_id"]): item for item in result.ineligible_candidates}
    assert excluded[500] == {
        "news_item_id": 500,
        "url": rows[0].url,
        "reason": "pdf_text_below_min_char_gate",
        "measured_value": 3,
        "gate_value": 80,
        "pdf_sha256": hashlib.sha256(short_pdf).hexdigest(),
    }
    assert excluded[501] == {
        "news_item_id": 501,
        "url": rows[1].url,
        "reason": "pdf_exceeds_size_bound",
        "measured_value": len(oversized_pdf),
        "gate_value": 8 * 1024 * 1024,
        "pdf_sha256": hashlib.sha256(oversized_pdf).hexdigest(),
    }


def test_heldout_materialization_keeps_transient_pdf_failure_batch_fatal() -> None:
    design = _eligibility_fixture_design()
    active_contract = load_event_extract_contract(CONFIG_PATH)
    row = _row(503, source="cninfo", bound=True)

    def transient_fetch(
        _url: str,
        _policy: builder.AnnouncementBodyPolicy,
    ) -> bytes:
        raise builder.GoldSampleError("CNInfo PDF download failed: ConnectError")

    with pytest.raises(builder.GoldSampleError, match="ConnectError"):
        builder.materialize_heldout_candidate_inputs(
            [row],
            design,
            active_contract,
            pdf_fetcher=transient_fetch,
            pdf_text_extractor=lambda *_args: pytest.fail("extractor must not run"),
        )


def test_v1_6_materialization_remains_batch_fatal_without_new_design_semantics() -> None:
    design = builder.load_evaluation_design(
        PROJECT_DIR / "config/p4_event_evaluation_v1_6.yaml"
    )
    active_contract = load_event_extract_contract(
        PROJECT_DIR / "config/p4_event_extract_eval_v1_7.yaml"
    )
    row = _row(504, source="cninfo", bound=True)

    with pytest.raises(builder.GoldSampleError, match="minimum extracted-character"):
        builder.materialize_heldout_candidate_inputs(
            [row],
            design,
            active_contract,
            pdf_fetcher=lambda *_args: b"%PDF-scan",
            pdf_text_extractor=lambda *_args: builder.extracted_pdf_text_fixture("扫描件"),
        )


def test_v1_1_combine_owner_is_create_only_and_binds_completion_manifest(
    tmp_path: Path,
) -> None:
    design = builder.load_evaluation_design()
    eval_root = tmp_path / "docs/phase4/eval"
    eval_root.mkdir(parents=True)
    dev_blind_path = builder.evaluation_artifact_path(
        design,
        "dev_60_frozen_jsonl",
        project_root=tmp_path,
    )
    dev_blind_path.write_bytes(
        (PROJECT_DIR / design.document["artifacts"]["dev_60_frozen_jsonl"]["path"]).read_bytes()
    )
    dev_blind = builder._load_jsonl(dev_blind_path)

    heldout_selected = [
        builder.SelectedNews(
            row=_row(
                news_item_id,
                source="sina_company_news",
                bound=True,
                available_time=datetime(2026, 8, 4, 2, tzinfo=UTC),
            ),
            sample_group="heldout40",
            trading_date=date(2026, 8, 4),
            stratum=builder.Stratum("sina_company_news", "bound", 40, False),
            rank_sha256=_hash(f"blind-rank-{news_item_id}"),
        )
        for news_item_id in range(424, 464)
    ]
    heldout_blind = builder.materialize_selected_rows(
        heldout_selected,
        design.base_contract,
        starting_index=1,
    )
    heldout_blind_path = builder.evaluation_artifact_path(
        design,
        "heldout_40_blind_sample_jsonl",
        project_root=tmp_path,
    )
    heldout_payload = builder._json_line_bytes(heldout_blind)
    heldout_blind_path.write_bytes(heldout_payload)
    selection_manifest_path = builder.evaluation_artifact_path(
        design,
        "heldout_selection_manifest_json",
        project_root=tmp_path,
    )
    selection_manifest_path.write_text(
        json.dumps(
            {
                "design": {"sha256": design.sha256},
                "owner_delivery": {
                    "heldout_blind_sample_path": str(
                        heldout_blind_path.relative_to(tmp_path)
                    ),
                    "heldout_blind_sample_sha256": hashlib.sha256(
                        heldout_payload
                    ).hexdigest(),
                    "heldout_blind_sample_count": 40,
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    dev_export = tmp_path / "dev-owner-export.jsonl"
    heldout_export = tmp_path / "heldout-owner-export.jsonl"
    dev_export.write_bytes(builder._json_line_bytes(_owner_records(dev_blind)))
    heldout_export.write_bytes(
        builder._json_line_bytes(_owner_records(heldout_blind))
    )

    evidence = builder.combine_owner_annotations(
        dev_owner_export=dev_export,
        heldout_owner_export=heldout_export,
        now=datetime.fromisoformat("2026-08-06T00:30:00+08:00"),
        project_root=tmp_path,
    )

    combined_path = builder.evaluation_artifact_path(
        design,
        "combined_100_annotations_jsonl",
        project_root=tmp_path,
    )
    completion_path = builder.evaluation_artifact_path(
        design,
        "owner_completion_manifest_json",
        project_root=tmp_path,
    )
    combined, combined_sha256 = evaluator._read_jsonl(
        combined_path,
        label="combined fixture",
    )
    assert [record["sample_index"] for record in combined] == list(range(1, 101))
    assert [record["news_item_id"] for record in combined[60:]] == list(range(424, 464))
    assert evidence["combined_annotations_sha256"] == combined_sha256
    owner_completion = evaluator.validate_owner_completion_manifest(
        design,
        annotation_path=combined_path,
        annotation_records=combined,
        annotation_sha256=combined_sha256,
        project_root=tmp_path,
    )
    assert owner_completion["combined_row_count"] == 100
    assert owner_completion["identity_validation_passed"] is True
    required_owner_fields = {
        field.removeprefix("owner_completion.")
        for field in design.document["evaluation"]["required_report_fields"]
        if field.startswith("owner_completion.")
    }
    assert set(owner_completion) == required_owner_fields
    assert completion_path.is_file()

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        builder.combine_owner_annotations(
            dev_owner_export=dev_export,
            heldout_owner_export=heldout_export,
            now=datetime.fromisoformat("2026-08-06T00:30:00+08:00"),
            project_root=tmp_path,
        )


def test_v1_1_split_gates_do_not_apply_dev_precision_to_heldout_gate() -> None:
    design = builder.load_evaluation_design()
    annotations: dict[int, dict[str, Any]] = {}
    predictions: dict[int, dict[str, Any] | None] = {}
    for news_item_id in range(1, 101):
        heldout = news_item_id > 60
        gold_materiality = 2 if (heldout and news_item_id <= 70) else 0
        if not heldout and news_item_id <= 5:
            gold_materiality = 2
        predicted_materiality = 0
        if not heldout and news_item_id in range(6, 11):
            predicted_materiality = 2  # dev precision 0/5, diagnostic only
        if heldout and (news_item_id <= 68 or news_item_id in {71, 72}):
            predicted_materiality = 2  # heldout precision 8/10
        symbols = [f"{news_item_id:06d}"]
        annotations[news_item_id] = {
            "record": {
                "sample_index": news_item_id,
                "sample_group": "heldout40" if heldout else "inventory_60",
            },
            "gold": {
                "symbols": symbols,
                "event_type": "other",
                "direction": 0,
                "materiality": gold_materiality,
                "evidence_span": "证据",
                "notes": None,
            },
        }
        predictions[news_item_id] = {
            "symbols": ["999999"] if news_item_id <= 5 else symbols,
            "event_type": "other",
            "direction": 0,
            "materiality": predicted_materiality,
            "summary": "摘要",
            "confidence": 0.8,
            "evidence_span": "证据",
        }

    result = evaluator.evaluate_split_records(annotations, predictions, design)

    assert result["metrics"]["materiality_precision"]["dev60"]["value"] == 0
    assert result["metrics"]["materiality_precision"]["heldout40"]["value"] == 0.8
    assert result["metrics"]["materiality_precision"]["heldout40"]["threshold"] == 0.8
    assert result["metrics"]["materiality_precision"]["heldout40"]["passed"] is True
    assert result["gates"]["materiality_precision_heldout40"] is True
    assert result["metrics"]["symbol_exact_set"]["all100"]["value"] == 0.95
    assert result["passed"] is True

    human_design = builder.load_evaluation_design(EVALUATION_DESIGN_V1_2_PATH)
    human_result = evaluator.evaluate_split_records(
        annotations,
        predictions,
        human_design,
    )
    assert "dev60" not in human_result["metrics"]["materiality_precision"]
    assert (
        human_result["metrics"]["materiality_model_interagreement"]["dev60"][
            "value"
        ]
        == 0
    )
    assert human_result["metrics"]["annotation_semantics"]["dev60"] == {
        "annotation_type": "ai_drafted_dev_signal",
        "metric_semantics": "model_interagreement",
        "human_ground_truth": False,
    }
    assert human_result["metrics"]["annotation_semantics"]["heldout40"][
        "human_ground_truth"
    ] is True


def test_v1_1_prediction_contract_may_differ_from_annotation_contract() -> None:
    contract = builder.load_contract(CONFIG_PATH)
    active = replace(load_event_extract_contract(CONFIG_PATH), sha256="a" * 64)
    annotation = {
        "news_item_id": 1,
        "contract_sha256": contract.sha256,
        "input_sha256": _hash("input"),
        "text_sha256": _hash("text"),
        "original_text": "证据正文",
    }
    annotations = {1: {"record": annotation}}
    prediction = {
        "news_item_id": 1,
        "contract_sha256": active.sha256,
        "model": active.model,
        "input_sha256": annotation["input_sha256"],
        "text_sha256": annotation["text_sha256"],
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

    joined, extras = evaluator.join_predictions(
        [prediction],
        annotations,
        contract,
        active_contract=active,
    )

    assert joined[1] is not None
    assert extras == 0


def test_prediction_join_uses_materialized_schema_for_candidate_contract() -> None:
    contract = builder.load_contract(CONFIG_PATH)
    active = load_event_evaluation_design(
        EVALUATION_DESIGN_V1_7_PATH
    ).prediction_contract
    annotation = {
        "news_item_id": 1,
        "input_sha256": _hash("declared-input"),
        "text_sha256": _hash("text"),
        "original_text": "连续证据正文",
    }
    annotations = {1: {"record": annotation}}
    prediction = {
        "news_item_id": 1,
        "contract_sha256": active.sha256,
        "model": active.model,
        "input_sha256": _hash("candidate-input"),
        "declared_input_sha256": annotation["input_sha256"],
        "text_sha256": annotation["text_sha256"],
        "status": "ok",
        "prediction": {
            "symbols": [],
            "event_type": "other",
            "direction": 0,
            "materiality": 0,
            "summary": "摘要",
            "confidence": 0.8,
            "evidence_span": "连续证据",
        },
    }

    joined, extras = evaluator.join_predictions(
        [prediction],
        annotations,
        contract,
        active_contract=active,
    )

    assert joined[1] is not None
    assert "evidence_candidate_id" not in prediction["prediction"]
    assert extras == 0

    missing_span = copy.deepcopy(prediction)
    del missing_span["prediction"]["evidence_span"]
    with pytest.raises(evaluator.GoldEvaluationError, match=r"evidence_span.*required"):
        evaluator.join_predictions(
            [missing_span],
            annotations,
            contract,
            active_contract=active,
        )

    with pytest.raises(
        evaluator.GoldEvaluationError,
        match="lacks materialized persisted schema",
    ):
        evaluator.join_predictions(
            [prediction],
            annotations,
            contract,
            active_contract=replace(active, materialized_schema=None),
        )


def test_legacy_prediction_join_still_requires_raw_evidence_span() -> None:
    contract = builder.load_contract(CONFIG_PATH)
    active = load_event_extract_contract(CONFIG_PATH)
    annotation = {
        "news_item_id": 1,
        "input_sha256": _hash("input"),
        "text_sha256": _hash("text"),
        "original_text": "连续证据正文",
    }
    prediction = {
        "news_item_id": 1,
        "contract_sha256": active.sha256,
        "model": active.model,
        "input_sha256": annotation["input_sha256"],
        "text_sha256": annotation["text_sha256"],
        "status": "ok",
        "prediction": {
            "symbols": [],
            "event_type": "other",
            "direction": 0,
            "materiality": 0,
            "summary": "摘要",
            "confidence": 0.8,
        },
    }

    with pytest.raises(evaluator.GoldEvaluationError, match=r"evidence_span.*required"):
        evaluator.join_predictions(
            [prediction],
            {1: {"record": annotation}},
            contract,
            active_contract=active,
        )


def test_prediction_join_uses_active_contract_evidence_span_match_mode() -> None:
    contract = builder.load_contract(CONFIG_PATH)
    exact = load_event_extract_contract(CONFIG_PATH)
    normalized = replace(
        exact,
        sha256="b" * 64,
        evidence_span_match_mode=WHITESPACE_NORMALIZED_EVIDENCE_SPAN_MATCH_MODE,
    )
    annotation = {
        "news_item_id": 1,
        "contract_sha256": contract.sha256,
        "input_sha256": _hash("input"),
        "text_sha256": _hash("text"),
        "original_text": "公司公告：本次\n\t回购股份。",
    }
    annotations = {1: {"record": annotation}}
    prediction = {
        "news_item_id": 1,
        "contract_sha256": normalized.sha256,
        "model": normalized.model,
        "input_sha256": annotation["input_sha256"],
        "text_sha256": annotation["text_sha256"],
        "status": "ok",
        "prediction": {
            "symbols": [],
            "event_type": "other",
            "direction": 0,
            "materiality": 0,
            "summary": "摘要",
            "confidence": 0.8,
            "evidence_span": "本次回购",
        },
    }

    joined, extras = evaluator.join_predictions(
        [prediction],
        annotations,
        contract,
        active_contract=normalized,
    )

    assert joined[1] is not None
    assert extras == 0

    prediction["contract_sha256"] = exact.sha256
    with pytest.raises(evaluator.GoldEvaluationError, match="not in frozen text"):
        evaluator.join_predictions(
            [prediction],
            annotations,
            contract,
            active_contract=exact,
        )


def test_v1_3_heldout_report_extensions_bind_v1_4_evidence_and_id44() -> None:
    design = load_event_evaluation_design(
        PROJECT_DIR / "config/p4_event_evaluation_v1_3.yaml"
    )
    active = design.prediction_contract
    annotations: dict[int, dict[str, Any]] = {}
    predictions: dict[int, dict[str, Any] | None] = {}
    dev_records: list[dict[str, Any]] = []
    for news_item_id in range(1, 101):
        original_text = f"连续证据 {news_item_id}"
        gold_symbols = ["000044"] if news_item_id == 44 else []
        annotation = {
            "record": {
                "news_item_id": news_item_id,
                "original_text": original_text,
            },
            "gold": {"symbols": gold_symbols},
        }
        prediction = {
            "symbols": [],
            "event_type": "other",
            "direction": 0,
            "materiality": 0,
            "summary": "摘要",
            "confidence": 0.8,
            "evidence_span": original_text,
        }
        annotations[news_item_id] = annotation
        predictions[news_item_id] = prediction
        if news_item_id <= 60:
            dev_records.append(
                {
                    "news_item_id": news_item_id,
                    "status": "ok",
                    "prediction": prediction,
                }
            )

    extensions = evaluator._v1_3_report_extensions(
        design=design,
        active_contract=active,
        dev_annotations={
            news_item_id: annotations[news_item_id]
            for news_item_id in range(1, 61)
        },
        dev_prediction_records=dev_records,
        annotations=annotations,
        predictions=predictions,
        result={
            "metrics": {
                "symbol_exact_set": {
                    "all100": {
                        "matches": 99,
                        "denominator": 100,
                        "value": 0.99,
                    }
                }
            }
        },
    )

    evidence = extensions["evidence_validation"]
    assert evidence["v1_3_actual"]["failure_count"] == 7
    assert evidence["whitespace_normalized_counterfactual"]["failure_count"] == 2
    assert evidence["v1_4_actual"]["success_count"] == 60
    assert evidence["v1_4_legacy_exact_shadow"]["mismatch_count"] == 0
    symbols = extensions["symbol_diagnostics"]
    assert symbols["raw_gate"]["denominator"] == 100
    assert symbols["ai_label_defect_ids"] == [44]
    assert symbols["adjusted_exact_set"]["matches"] == 99
    assert symbols["adjusted_exact_set"]["denominator"] == 99
    assert symbols["adjusted_exact_set"]["agreement"] == 1.0


def test_cli_accepts_heldout_final_v1_3_scope() -> None:
    arguments = evaluator._arguments(
        ["--scope", "heldout-final-v1.3", "--output", "report.json"]
    )

    assert arguments.scope == "heldout-final-v1.3"


def test_v1_4_heldout_report_extensions_preserve_failed_round_anchor() -> None:
    design = load_event_evaluation_design(EVALUATION_DESIGN_V1_4_PATH)
    active = design.prediction_contract
    annotations: dict[int, dict[str, Any]] = {}
    predictions: dict[int, dict[str, Any] | None] = {}
    dev_records: list[dict[str, Any]] = []
    for news_item_id in range(1, 101):
        original_text = f"连续证据 {news_item_id}"
        annotation = {
            "record": {
                "news_item_id": news_item_id,
                "original_text": original_text,
            },
            "gold": {"symbols": ["000044"] if news_item_id == 44 else []},
        }
        prediction = {
            "symbols": [],
            "event_type": "other",
            "direction": 0,
            "materiality": 0,
            "summary": "摘要",
            "confidence": 0.8,
            "evidence_span": original_text,
        }
        annotations[news_item_id] = annotation
        predictions[news_item_id] = prediction
        if news_item_id <= 60:
            dev_records.append(
                {
                    "news_item_id": news_item_id,
                    "status": "ok",
                    "prediction": prediction,
                }
            )

    extensions = evaluator._v1_3_report_extensions(
        design=builder.load_evaluation_design(EVALUATION_DESIGN_V1_4_PATH),
        active_contract=active,
        dev_annotations={
            news_item_id: annotations[news_item_id]
            for news_item_id in range(1, 61)
        },
        dev_prediction_records=dev_records,
        annotations=annotations,
        predictions=predictions,
        result={
            "metrics": {
                "symbol_exact_set": {
                    "all100": {
                        "matches": 99,
                        "denominator": 100,
                        "value": 0.99,
                    }
                }
            }
        },
    )

    evidence = extensions["evidence_validation"]
    assert evidence["v1_4_r1_actual"]["extraction"]["failure_count"] == 6
    assert evidence["v1_4_actual"]["historical_round_immutable"] is True
    assert evidence["v1_5_actual"]["success_count"] == 60
    assert evidence["v1_5_legacy_exact_shadow"]["mismatch_count"] == 0
    symbols = extensions["symbol_diagnostics"]
    assert symbols["v1_4_r1_actual"]["current_model_under_attribution_ids"] == [
        28,
        67,
        71,
        96,
    ]
    assert "model_over_attribution_ids" not in symbols


def test_cli_accepts_heldout_final_v1_4_scope() -> None:
    arguments = evaluator._arguments(
        ["--scope", "heldout-final-v1.4", "--output", "report.json"]
    )

    assert arguments.scope == "heldout-final-v1.4"


def test_v1_1_active_contract_allows_prompt_version_only() -> None:
    base = builder.load_contract(CONFIG_PATH).document
    active = copy.deepcopy(base)
    active["schema_version"] = "p4.2a-event-extract-eval-v1.1"
    active["owner_spec_commit"] = "f" * 40
    active["pre_registered_at"] = "2026-08-05T16:00:00Z"
    active["contract_files"]["prompt"] = {
        "path": "config/prompts/p4_news_event_extract_v1_1.txt",
        "sha256": "a" * 64,
    }

    assert builder.prediction_contract_changes_are_prompt_only(base, active)

    active["input"]["open_mode"] = "read_write"
    assert not builder.prediction_contract_changes_are_prompt_only(base, active)
    active["input"]["open_mode"] = base["input"]["open_mode"]
    active["isolation"]["p4_2b_unlocked"] = True
    assert not builder.prediction_contract_changes_are_prompt_only(base, active)


def test_v1_1_frozen_offline_trial_diagnostic_keeps_failure_190() -> None:
    design = builder.load_evaluation_design()
    diagnostics = evaluator._offline_trial_diagnostics(design, {190, 268, 500})

    assert diagnostics["successful_prediction_count"] == 406
    assert diagnostics["predicted_materiality_gte_2_count"] == 81
    assert diagnostics["predicted_materiality_gte_2_rate"] == pytest.approx(81 / 406)
    assert diagnostics["gold_intersection_failure_ids"] == [190]


def _inference_state_events(
    design: builder.FrozenEvaluationDesign,
    *,
    started_at: str = "2026-08-06T00:20:00Z",
    terminal_at: str = "2026-08-06T00:21:00Z",
) -> list[dict[str, Any]]:
    return [
        {
            "schema_version": "p4.2a-heldout-inference-state-v1.1",
            "event": "inference_started",
            "at_utc": started_at,
            "design_sha256": design.sha256,
        },
        {
            "schema_version": "p4.2a-heldout-inference-state-v1.1",
            "event": "inference_completed",
            "at_utc": terminal_at,
            "design_sha256": design.sha256,
        },
    ]


def _write_inference_state(
    tmp_path: Path,
    design: builder.FrozenEvaluationDesign,
    events: list[dict[str, Any]],
) -> Path:
    state_path = builder.evaluation_artifact_path(
        design,
        "heldout_inference_state_jsonl",
        project_root=tmp_path,
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_bytes(builder._json_line_bytes(events))
    return state_path


def test_v1_1_inference_state_rejects_unknown_third_event(tmp_path: Path) -> None:
    design = builder.load_evaluation_design()
    events = _inference_state_events(design)
    events.append(
        {
            "event": "inference_audit",
            "at_utc": "2026-08-06T00:22:00Z",
            "design_sha256": design.sha256,
        }
    )
    _write_inference_state(tmp_path, design, events)

    with pytest.raises(builder.GoldSampleError, match="must be exactly"):
        builder.load_completed_one_shot_state(
            design,
            scope="inference",
            project_root=tmp_path,
        )


def test_v1_1_inference_state_rejects_backdated_terminal(tmp_path: Path) -> None:
    design = builder.load_evaluation_design()
    events = _inference_state_events(
        design,
        started_at="2026-08-06T00:21:00Z",
        terminal_at="2026-08-06T00:20:59Z",
    )
    _write_inference_state(tmp_path, design, events)

    with pytest.raises(builder.GoldSampleError, match="earlier than started"):
        builder.load_completed_one_shot_state(
            design,
            scope="inference",
            project_root=tmp_path,
        )


def test_v1_1_inference_state_rejects_terminal_manifest_drift(
    tmp_path: Path,
) -> None:
    design = builder.load_evaluation_design()
    active_contract = load_event_extract_contract(CONFIG_PATH)
    candidate_records = [
        {
            "news_item_id": news_item_id,
            "input_sha256": _hash(f"candidate-input-{news_item_id}"),
            "text_sha256": _hash(f"candidate-text-{news_item_id}"),
        }
        for news_item_id in (424, 425)
    ]
    receipt_sha256 = "a" * 64
    candidate_inputs_sha256 = "b" * 64
    prediction_manifest_sha256 = "c" * 64
    events = _inference_state_events(design)
    events[0].update(
        {
            "contract_sha256": active_contract.sha256,
            "freeze_receipt_sha256": receipt_sha256,
            "candidate_inputs_sha256": candidate_inputs_sha256,
            "candidate_identity_sha256": (
                builder._ordered_candidate_identity_sha256(candidate_records)
            ),
            "candidate_count": 2,
        }
    )
    events[1].update(
        {
            "contract_sha256": active_contract.sha256,
            "candidate_count": 2,
            "attempted_count": 2,
            "success_count": 1,
            "failure_count": 1,
            "prediction_manifest_sha256": "d" * 64,
        }
    )
    _write_inference_state(tmp_path, design, events)
    inference, _state_sha256 = builder.load_completed_one_shot_state(
        design,
        scope="inference",
        project_root=tmp_path,
    )

    with pytest.raises(builder.GoldSampleError, match="terminal manifest/count"):
        builder.validate_inference_completion_bindings(
            inference,
            design=design,
            active_contract=active_contract,
            receipt_sha256=receipt_sha256,
            candidate_records=candidate_records,
            candidate_inputs_sha256=candidate_inputs_sha256,
            prediction_manifest_sha256=prediction_manifest_sha256,
            attempted_count=2,
            success_count=1,
            failure_count=1,
        )
    inference["events"][1]["prediction_manifest_sha256"] = prediction_manifest_sha256
    builder.validate_inference_completion_bindings(
        inference,
        design=design,
        active_contract=active_contract,
        receipt_sha256=receipt_sha256,
        candidate_records=candidate_records,
        candidate_inputs_sha256=candidate_inputs_sha256,
        prediction_manifest_sha256=prediction_manifest_sha256,
        attempted_count=2,
        success_count=1,
        failure_count=1,
    )


def test_v1_1_evaluation_one_shot_blocks_second_started_event(tmp_path: Path) -> None:
    state = tmp_path / "evaluation.state.jsonl"
    evaluator._claim_evaluation_one_shot(
        state,
        design_sha256="a" * 64,
        started_at_utc="2026-08-06T00:20:00Z",
    )
    evaluator._append_evaluation_terminal(
        state,
        design_sha256="a" * 64,
        event="evaluation_completed",
        at_utc="2026-08-06T00:21:00Z",
    )

    with pytest.raises(evaluator.GoldEvaluationError, match="reevaluation is forbidden"):
        evaluator._claim_evaluation_one_shot(
            state,
            design_sha256="a" * 64,
            started_at_utc="2026-08-06T00:22:00Z",
        )
    events = [json.loads(line) for line in state.read_text().splitlines()]
    assert [event["event"] for event in events] == [
        "evaluation_started",
        "evaluation_completed",
    ]


def test_v1_1_dev_final_predictions_are_receipt_bound(
    tmp_path: Path,
) -> None:
    actual_design = builder.load_evaluation_design()
    document = copy.deepcopy(actual_design.document)
    design = builder.FrozenEvaluationDesign(
        path=tmp_path / "config/p4_event_evaluation_v1_1.yaml",
        sha256=actual_design.sha256,
        document=document,
        base_contract=actual_design.base_contract,
    )
    active = load_event_extract_contract(CONFIG_PATH)
    dev_records = _blind_records(actual_design.base_contract)[:60]
    dev_path = builder.evaluation_artifact_path(
        design,
        "dev_60_frozen_jsonl",
        project_root=tmp_path,
    )
    predictions_path = builder.evaluation_artifact_path(
        design,
        "dev_final_predictions_jsonl",
        project_root=tmp_path,
    )
    manifest_path = builder.evaluation_artifact_path(
        design,
        "dev_final_predictions_manifest_json",
        project_root=tmp_path,
    )
    for path in (dev_path, predictions_path, manifest_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    dev_path.write_bytes(builder._json_line_bytes(dev_records))
    prediction_records: list[dict[str, Any]] = []
    for record in sorted(dev_records, key=lambda item: int(item["news_item_id"])):
        prediction_records.append(
            {
                "news_item_id": record["news_item_id"],
                "contract_sha256": active.sha256,
                "model": active.model,
                "input_sha256": record["input_sha256"],
                "text_sha256": record["text_sha256"],
                "status": "ok",
                "prediction": {
                    "symbols": [record["ingested_symbol"]],
                    "event_type": "other",
                    "direction": 0,
                    "materiality": 0,
                    "summary": "测试摘要",
                    "confidence": 0.8,
                    "evidence_span": str(record["original_text"])[:2],
                },
            }
        )
    predictions_payload = builder._json_line_bytes(prediction_records)
    predictions_path.write_bytes(predictions_payload)
    identity_sha256 = builder._ordered_prediction_identity_sha256(prediction_records)
    manifest = {
        "design_sha256": design.sha256,
        "contract_sha256": active.sha256,
        "predictions_path": str(predictions_path.relative_to(tmp_path)),
        "predictions_sha256": hashlib.sha256(predictions_payload).hexdigest(),
        "row_count": 60,
        "success_count": 60,
        "failure_count": 0,
        "ordered_identity_sha256": identity_sha256,
        "completed_at_utc": "2026-08-05T16:01:00Z",
    }
    manifest_payload = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )
    manifest_path.write_bytes(manifest_payload)
    receipt = {
        "frozen_at_utc": "2026-08-05T16:02:00Z",
        "dev_final_predictions_path": str(predictions_path.relative_to(tmp_path)),
        "dev_final_predictions_sha256": hashlib.sha256(predictions_payload).hexdigest(),
        "dev_final_predictions_manifest_path": str(manifest_path.relative_to(tmp_path)),
        "dev_final_predictions_manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
        "dev_final_predictions_row_count": 60,
        "dev_final_predictions_success_count": 60,
        "dev_final_predictions_failure_count": 0,
        "dev_final_predictions_identity_sha256": identity_sha256,
        "dev_final_predictions_contract_sha256": active.sha256,
    }

    evidence = builder.validate_dev_final_prediction_freeze(
        design,
        active_contract=active,
        receipt=receipt,
        project_root=tmp_path,
    )

    assert evidence["row_count"] == 60
    assert evidence["failure_ids"] == []

    ancestor_sha256 = "a" * 64
    lineage_design = replace(
        design,
        ancestor_designs=(
            EvaluationDesignAncestor(
                path=tmp_path / "config/p4_event_evaluation_parent.yaml",
                sha256=ancestor_sha256,
                schema_version="p4.2a-evaluation-design-parent",
                byte_frozen_scopes=builder.PREDICTION_FREEZE_DESIGN_SCOPES,
            ),
        ),
    )
    manifest["design_sha256"] = ancestor_sha256
    manifest_payload = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )
    manifest_path.write_bytes(manifest_payload)
    receipt["dev_final_predictions_manifest_sha256"] = hashlib.sha256(
        manifest_payload
    ).hexdigest()
    inherited = builder.validate_dev_final_prediction_freeze(
        lineage_design,
        active_contract=active,
        receipt=receipt,
        project_root=tmp_path,
    )
    assert inherited["manifest_sha256"] == hashlib.sha256(
        manifest_payload
    ).hexdigest()

    manifest["design_sha256"] = "b" * 64
    unrelated_payload = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )
    manifest_path.write_bytes(unrelated_payload)
    receipt["dev_final_predictions_manifest_sha256"] = hashlib.sha256(
        unrelated_payload
    ).hexdigest()
    with pytest.raises(builder.GoldSampleError, match="design_sha256 drifted"):
        builder.validate_dev_final_prediction_freeze(
            lineage_design,
            active_contract=active,
            receipt=receipt,
            project_root=tmp_path,
        )

    manifest["design_sha256"] = ancestor_sha256
    manifest_path.write_bytes(manifest_payload)
    receipt["dev_final_predictions_manifest_sha256"] = hashlib.sha256(
        manifest_payload
    ).hexdigest()
    incomplete_scope_design = replace(
        lineage_design,
        ancestor_designs=(
            replace(
                lineage_design.ancestor_designs[0],
                byte_frozen_scopes=frozenset({"active_prediction_contract"}),
            ),
        ),
    )
    with pytest.raises(builder.GoldSampleError, match="design_sha256 drifted"):
        builder.validate_dev_final_prediction_freeze(
            incomplete_scope_design,
            active_contract=active,
            receipt=receipt,
            project_root=tmp_path,
        )

    manifest["design_sha256"] = design.sha256
    manifest_payload = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )
    manifest_path.write_bytes(manifest_payload)
    receipt["dev_final_predictions_manifest_sha256"] = hashlib.sha256(
        manifest_payload
    ).hexdigest()
    receipt["dev_final_predictions_contract_sha256"] = "f" * 64
    with pytest.raises(builder.GoldSampleError, match="receipt"):
        builder.validate_dev_final_prediction_freeze(
            design,
            active_contract=active,
            receipt=receipt,
            project_root=tmp_path,
        )


def test_v1_7_dev_final_freeze_accepts_only_declared_v1_6_lineage() -> None:
    design = builder.load_evaluation_design(EVALUATION_DESIGN_V1_7_PATH)
    accepted = builder.byte_frozen_design_identities(
        design,
        required_scopes=builder.PREDICTION_FREEZE_DESIGN_SCOPES,
    )

    assert accepted == {
        (design.sha256, str(design.document["schema_version"])),
        (
            design.ancestor_designs[0].sha256,
            design.ancestor_designs[0].schema_version,
        ),
    }
    assert all(
        ancestor.sha256 not in {identity[0] for identity in accepted}
        for ancestor in design.ancestor_designs[1:]
    )

    active_contract, receipt, _receipt_sha256 = (
        builder.load_active_prediction_contract(design)
    )
    evidence = builder.validate_dev_final_prediction_freeze(
        design,
        active_contract=active_contract,
        receipt=receipt,
    )

    assert evidence["row_count"] == 60
    assert evidence["success_count"] == 60
    assert evidence["failure_count"] == 0


def _v14_freeze_receipt(
    design: builder.FrozenEvaluationDesign,
) -> dict[str, object]:
    registered = load_event_evaluation_design(EVALUATION_DESIGN_V1_3_PATH)
    contract = registered.prediction_contract
    files = contract.document["contract_files"]
    required = design.document["prediction_contract_freeze"][
        "required_receipt_fields"
    ]
    receipt: dict[str, object] = {field: "f" * 64 for field in required}
    receipt.update(
        {
            "design_schema_version": design.document["schema_version"],
            "design_sha256": design.sha256,
            "frozen_at_utc": "2026-08-04T12:00:00Z",
            "contract_path": str(contract.path.relative_to(PROJECT_DIR)),
            "contract_sha256": contract.sha256,
            "contract_schema_version": contract.document["schema_version"],
            "model": contract.model,
            "endpoint": contract.endpoint,
            "explicit_cache_enabled": False,
            "evidence_span_match_mode": contract.evidence_span_match_mode,
            "prompt_path": files["prompt"]["path"],
            "prompt_sha256": files["prompt"]["sha256"],
            "result_schema_path": files["schema"]["path"],
            "result_schema_sha256": files["schema"]["sha256"],
            "taxonomy_version": contract.document["taxonomy"]["version"],
            "dev_final_predictions_path": design.document["artifacts"][
                "dev_final_predictions_jsonl"
            ]["path"],
            "dev_final_predictions_row_count": 60,
            "dev_final_predictions_success_count": 60,
            "dev_final_predictions_failure_count": 0,
            "dev_final_predictions_contract_sha256": contract.sha256,
        }
    )
    assert set(receipt) == set(required)
    return receipt


def test_v14_freeze_receipt_and_active_loader_bind_evidence_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    design = builder.load_evaluation_design(EVALUATION_DESIGN_V1_3_PATH)
    receipt = _v14_freeze_receipt(design)
    receipt_path = tmp_path / "freeze.json"
    receipt_path.write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        builder,
        "evaluation_artifact_path",
        lambda _design, _name: receipt_path,
    )
    loaded, _ = builder.load_prediction_contract_freeze_receipt(design)
    assert (
        loaded["evidence_span_match_mode"]
        == WHITESPACE_NORMALIZED_EVIDENCE_SPAN_MATCH_MODE
    )

    monkeypatch.setattr(
        builder,
        "load_prediction_contract_freeze_receipt",
        lambda _design: (receipt, "a" * 64),
    )
    monkeypatch.setattr(
        builder,
        "validate_dev_final_prediction_freeze",
        lambda *_args, **_kwargs: {},
    )
    active, _, _ = builder.load_active_prediction_contract(design)
    assert (
        active.evidence_span_match_mode
        == WHITESPACE_NORMALIZED_EVIDENCE_SPAN_MATCH_MODE
    )

    receipt["evidence_span_match_mode"] = "exact_contiguous_substring_v1"
    monkeypatch.undo()
    receipt_path.write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        builder,
        "evaluation_artifact_path",
        lambda _design, _name: receipt_path,
    )
    with pytest.raises(builder.GoldSampleError, match="evidence-span match mode"):
        builder.load_prediction_contract_freeze_receipt(design)


def test_prediction_freeze_receipt_binds_sha_and_schema_to_same_lineage_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent = builder.load_evaluation_design(EVALUATION_DESIGN_V1_3_PATH)
    receipt = _v14_freeze_receipt(parent)
    receipt_path = tmp_path / "freeze.json"
    receipt_path.write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        builder,
        "evaluation_artifact_path",
        lambda _design, _name: receipt_path,
    )
    successor_document = copy.deepcopy(parent.document)
    successor_document["schema_version"] = "p4.2a-evaluation-design-successor"
    successor = replace(
        parent,
        sha256="c" * 64,
        document=successor_document,
        ancestor_designs=(
            EvaluationDesignAncestor(
                path=parent.path,
                sha256=parent.sha256,
                schema_version=str(parent.document["schema_version"]),
                byte_frozen_scopes=builder.PREDICTION_FREEZE_DESIGN_SCOPES,
            ),
        ),
    )

    loaded, _receipt_sha256 = builder.load_prediction_contract_freeze_receipt(
        successor
    )
    assert loaded["design_sha256"] == parent.sha256

    receipt["design_schema_version"] = successor_document["schema_version"]
    receipt_path.write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(builder.GoldSampleError, match="contract values drifted"):
        builder.load_prediction_contract_freeze_receipt(successor)

    receipt["design_schema_version"] = parent.document["schema_version"]
    receipt["design_sha256"] = "d" * 64
    receipt_path.write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(builder.GoldSampleError, match="contract values drifted"):
        builder.load_prediction_contract_freeze_receipt(successor)


def test_v1_1_malformed_preflight_does_not_consume_evaluation_one_shot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    actual = builder.load_evaluation_design()
    eval_root = tmp_path / "eval"
    document = copy.deepcopy(actual.document)
    document["artifact_root"] = str(eval_root)
    design = builder.FrozenEvaluationDesign(
        path=actual.path,
        sha256=actual.sha256,
        document=document,
        base_contract=actual.base_contract,
    )
    annotation_path = eval_root / "malformed.jsonl"
    annotation_path.parent.mkdir(parents=True)
    annotation_path.write_text("\n", encoding="utf-8")
    state_path = eval_root / "evaluation.state.jsonl"
    monkeypatch.setattr(builder, "load_evaluation_design", lambda _path: design)
    monkeypatch.setattr(
        builder,
        "evaluation_artifact_path",
        lambda _design, _name: state_path,
    )

    with pytest.raises(evaluator.GoldEvaluationError, match="blank line"):
        evaluator.evaluate_gold_sample_v1_1(
            annotation_path,
            eval_root / "reports/round.json",
            now=datetime.fromisoformat("2026-08-06T00:30:00+08:00"),
        )

    assert not state_path.exists()
    assert not (eval_root / "reports").exists()


def test_v1_1_cli_rejects_superseded_future_mode() -> None:
    with pytest.raises(SystemExit):
        builder._arguments(["--mode", "future"])
