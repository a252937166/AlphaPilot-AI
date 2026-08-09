from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from scripts import build_p4_2a_adjudication_ui as ui
from scripts import build_p4_2a_gold_sample as builder
from scripts import evaluate_p4_2a_gold as evaluator

from alphapilot.llm.p4_news_eval import (
    EVALUATION_DESIGN_V1_2_PATH,
    EVALUATION_DESIGN_V1_7_PATH,
    load_event_evaluation_design,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _blind_and_draft() -> tuple[Any, dict[str, Any], dict[str, Any]]:
    design = load_event_evaluation_design(EVALUATION_DESIGN_V1_2_PATH)
    row = builder.NewsRow(
        news_item_id=700,
        source="sina_company_news",
        ingested_symbol="600519",
        title="600519 公司披露重大事项",
        url="https://example.test/news/700",
        published_at=datetime(2026, 8, 4, tzinfo=UTC),
        available_time=datetime(2026, 8, 4, 1, tzinfo=UTC),
        content_hash=_hash("content-700"),
        raw_payload={},
    )
    blind = builder.materialize_selected_rows(
        [
            builder.SelectedNews(
                row=row,
                sample_group="heldout40",
                trading_date=date(2026, 8, 4),
                stratum=builder.Stratum(
                    "sina_company_news",
                    "bound",
                    1,
                    False,
                ),
                rank_sha256=_hash("rank-700"),
            )
        ],
        design.base_contract,
        starting_index=1,
    )[0]
    draft = copy.deepcopy(blind)
    draft.update(
        {
            "annotation_status": "completed",
            "annotation_owner": "ChatGPT GPT-5.6 Pro",
            "annotated_at": "2026-08-06T00:20:00+08:00",
            "gold": {
                "symbols": ["600519"],
                "event_type": "other",
                "direction": 0,
                "materiality": 1,
                "evidence_span": str(blind["original_text"])[:6],
                "notes": None,
            },
        }
    )
    return design, blind, draft


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_build_binds_full_blind_and_prefills_validated_draft(tmp_path: Path) -> None:
    design, blind, draft = _blind_and_draft()
    sample = tmp_path / "blind.jsonl"
    draft_path = tmp_path / "draft.jsonl"
    _write_jsonl(sample, [blind])
    _write_jsonl(draft_path, [draft])

    items, annotator = ui._build(
        sample,
        draft_path,
        design=design,
        expected_count=1,
    )

    assert annotator == "ChatGPT GPT-5.6 Pro"
    assert items[0]["draft"] == draft["gold"]

    duplicate = json.dumps(blind, ensure_ascii=False)[:-1] + ',"news_item_id":701}'
    sample.write_text(duplicate + "\n", encoding="utf-8")
    with pytest.raises(ui.AdjudicationUIError, match="duplicate JSON key"):
        ui._rows(sample, label="sample")


def test_build_rejects_nonempty_ai_draft_notes(tmp_path: Path) -> None:
    design, blind, draft = _blind_and_draft()
    draft["gold"]["notes"] = "AI rationale must not pre-anchor the human adjudicator"
    sample = tmp_path / "blind.jsonl"
    draft_path = tmp_path / "draft.jsonl"
    _write_jsonl(sample, [blind])
    _write_jsonl(draft_path, [draft])

    with pytest.raises(ui.AdjudicationUIError, match="must keep notes empty"):
        ui._build(
            sample,
            draft_path,
            design=design,
            expected_count=1,
        )


def test_render_escapes_news_text_and_exports_auditable_delta() -> None:
    attack = '</script><img src=x onerror="globalThis.PWNED=1">'
    rendered = ui._render(
        [
            {
                "news_item_id": 700,
                "sample_index": 1,
                "source": "sina_company_news",
                "ingested_symbol": "600519",
                "title": attack,
                "text": f"正文{attack}结尾",
                "body_state": "title_only",
                "available_time": "2026-08-04T01:00:00Z",
                "draft": {
                    "symbols": [],
                    "event_type": "other",
                    "direction": 0,
                    "materiality": 0,
                    "evidence_span": "正文",
                    "notes": None,
                },
            }
        ],
        draft_annotator="ChatGPT GPT-5.6 Pro",
        title="P4.2a 人工裁定",
        storage_key="bound-key",
        download_name="heldout.adjudicated.jsonl",
    )

    assert attack not in rendered
    assert "\\u003c/script\\u003e\\u003cimg" in rendered
    assert 'schema_version: "p4.2a-heldout-adjudication-export-v1"' in rendered
    assert "adjudicated_changed: adjudicatedChanged" in rendered
    assert 'rec().values.symbols = raw ? raw.split(/[,，\\s]+/).filter(Boolean) : []' in rendered
    assert ".innerHTML" not in rendered
    assert "item.text.includes(gold.evidence_span)" in rendered
    assert "adjudicator.toLocaleLowerCase() === DRAFT_ANNOTATOR.toLocaleLowerCase()" in rendered


def test_normalize_adjudication_recomputes_provenance_and_changed_fields() -> None:
    design, blind, draft = _blind_and_draft()
    final_gold = copy.deepcopy(draft["gold"])
    final_gold["materiality"] = 2
    adjudicated = {
        "schema_version": "p4.2a-heldout-adjudication-export-v1",
        "news_item_id": blind["news_item_id"],
        "gold": final_gold,
        "draft_gold": copy.deepcopy(draft["gold"]),
        "annotation_status": "adjudicated",
        "adjudication": {
            "method": "ai_drafted_human_adjudicated",
            "draft_annotator": "ChatGPT GPT-5.6 Pro",
            "adjudicator": "owner-ouyang",
            "adjudicated_changed": True,
            "changed_fields": ["materiality"],
            "adjudicated_at": "2026-08-06T00:30:00+08:00",
        },
    }

    records = builder._normalize_heldout_adjudication_export(
        blind_records=[blind],
        draft_records=[draft],
        adjudicated_records=[adjudicated],
        design=design,
    )

    assert records[0]["annotation_status"] == "completed"
    assert records[0]["annotation_owner"] == "owner-ouyang"
    assert records[0]["annotation_type"] == "ai_drafted_human_adjudicated"
    assert records[0]["drafter_id"] == "ChatGPT GPT-5.6 Pro"
    assert records[0]["adjudicator_id"] == "owner-ouyang"
    assert records[0]["gold"]["materiality"] == 2

    adjudicated["adjudication"]["changed_fields"] = []
    with pytest.raises(builder.GoldSampleError, match="changed_fields drifted"):
        builder._normalize_heldout_adjudication_export(
            blind_records=[blind],
            draft_records=[draft],
            adjudicated_records=[adjudicated],
            design=design,
        )

    draft_with_notes = copy.deepcopy(draft)
    draft_with_notes["gold"]["notes"] = "AI rationale"
    with pytest.raises(builder.GoldSampleError, match="must keep notes empty"):
        builder._normalize_heldout_adjudication_export(
            blind_records=[blind],
            draft_records=[draft_with_notes],
            adjudicated_records=[adjudicated],
            design=design,
        )


def test_combine_owner_consumes_adjudicated_export_with_explicit_draft(
    tmp_path: Path,
) -> None:
    design = load_event_evaluation_design(EVALUATION_DESIGN_V1_7_PATH)
    eval_root = tmp_path / "docs/phase4/eval"
    eval_root.mkdir(parents=True)
    artifacts = design.document["artifacts"]
    dev_blind = eval_root / Path(artifacts["dev_60_frozen_jsonl"]["path"]).name
    dev_labels = eval_root / Path(
        artifacts["dev_60_owner_annotations_jsonl"]["path"]
    ).name
    dev_blind.write_bytes(
        (ui.PROJECT_ROOT / artifacts["dev_60_frozen_jsonl"]["path"]).read_bytes()
    )
    frozen_dev_bytes = (
        ui.PROJECT_ROOT / artifacts["dev_60_owner_annotations_jsonl"]["path"]
    ).read_bytes()
    dev_labels.write_bytes(frozen_dev_bytes)

    heldout: list[dict[str, Any]] = []
    for index in range(40):
        news_item_id = 700 + index
        row = builder.NewsRow(
            news_item_id=news_item_id,
            source="sina_company_news",
            ingested_symbol="600519",
            title=f"600519 公司披露第 {index} 项重大事项",
            url=f"https://example.test/news/{news_item_id}",
            published_at=datetime(2026, 8, 4, tzinfo=UTC),
            available_time=datetime(2026, 8, 4, 1, tzinfo=UTC),
            content_hash=_hash(f"content-{news_item_id}"),
            raw_payload={},
        )
        heldout.extend(
            builder.materialize_selected_rows(
                [
                    builder.SelectedNews(
                        row=row,
                        sample_group="heldout40",
                        trading_date=date(2026, 8, 4),
                        stratum=builder.Stratum(
                            "sina_company_news",
                            "bound",
                            40,
                            False,
                        ),
                        rank_sha256=_hash(f"rank-{news_item_id}"),
                    )
                ],
                design.base_contract,
                starting_index=index + 1,
            )
        )
    heldout_path = eval_root / Path(
        artifacts["heldout_40_blind_sample_jsonl"]["path"]
    ).name
    heldout_payload = builder._json_line_bytes(heldout)
    heldout_path.write_bytes(heldout_payload)
    selection_path = eval_root / Path(
        artifacts["heldout_selection_manifest_json"]["path"]
    ).name
    selection_path.write_text(
        json.dumps(
            {
                "design": {"sha256": design.sha256},
                "owner_delivery": {
                    "heldout_blind_sample_path": str(heldout_path.relative_to(tmp_path)),
                    "heldout_blind_sample_sha256": hashlib.sha256(
                        heldout_payload
                    ).hexdigest(),
                    "heldout_blind_sample_count": 40,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    drafts: list[dict[str, Any]] = []
    exports: list[dict[str, Any]] = []
    for blind in heldout:
        draft = copy.deepcopy(blind)
        draft_gold = {
            "symbols": ["600519"],
            "event_type": "other",
            "direction": 0,
            "materiality": 1,
            "evidence_span": str(blind["original_text"])[:6],
            "notes": None,
        }
        draft.update(
            {
                "annotation_status": "completed",
                "annotation_owner": "ChatGPT GPT-5.6 Pro",
                "annotated_at": "2026-08-06T00:20:00+08:00",
                "gold": draft_gold,
            }
        )
        drafts.append(draft)
        exports.append(
            {
                "schema_version": "p4.2a-heldout-adjudication-export-v1",
                "news_item_id": blind["news_item_id"],
                "gold": copy.deepcopy(draft_gold),
                "draft_gold": copy.deepcopy(draft_gold),
                "annotation_status": "adjudicated",
                "adjudication": {
                    "method": "ai_drafted_human_adjudicated",
                    "draft_annotator": "ChatGPT GPT-5.6 Pro",
                    "adjudicator": "owner-ouyang",
                    "adjudicated_changed": False,
                    "changed_fields": [],
                    "adjudicated_at": "2026-08-06T00:30:00+08:00",
                },
            }
        )
    draft_path = eval_root / "heldout.ai-draft.jsonl"
    export_path = eval_root / "heldout.adjudicated.jsonl"
    draft_path.write_bytes(builder._json_line_bytes(drafts))
    export_path.write_bytes(builder._json_line_bytes(exports))

    registered_outputs = {
        eval_root / Path(artifacts[name]["path"]).name
        for name in builder.COMBINE_OWNER_OUTPUT_ARTIFACTS
    }
    unregistered_dev_copy = tmp_path / "dev-ai-export-copy.jsonl"
    unregistered_dev_copy.write_bytes(frozen_dev_bytes)
    with pytest.raises(
        builder.GoldSampleError,
        match="dev60 frozen input must use the design-registered path",
    ):
        builder.combine_owner_annotations(
            dev_owner_export=unregistered_dev_copy,
            heldout_owner_export=export_path,
            heldout_ai_draft=draft_path,
            design_path=EVALUATION_DESIGN_V1_7_PATH,
            now=datetime.fromisoformat("2026-08-06T00:30:00+08:00"),
            project_root=tmp_path,
        )
    assert not any(path.exists() for path in registered_outputs)

    assert frozen_dev_bytes.endswith(b"\n")
    dev_labels.write_bytes(frozen_dev_bytes[:-1] + b" \n")
    with pytest.raises(
        builder.GoldSampleError,
        match="dev60 AI-drafted annotation bytes differ from the frozen design",
    ):
        builder.combine_owner_annotations(
            dev_owner_export=dev_labels,
            heldout_owner_export=export_path,
            heldout_ai_draft=draft_path,
            design_path=EVALUATION_DESIGN_V1_7_PATH,
            now=datetime.fromisoformat("2026-08-06T00:30:00+08:00"),
            project_root=tmp_path,
        )
    assert not any(path.exists() for path in registered_outputs)
    dev_labels.write_bytes(frozen_dev_bytes)
    dev_stat_before = dev_labels.stat()
    files_before = {path for path in eval_root.rglob("*") if path.is_file()}

    evidence = builder.combine_owner_annotations(
        dev_owner_export=dev_labels,
        heldout_owner_export=export_path,
        heldout_ai_draft=draft_path,
        design_path=EVALUATION_DESIGN_V1_7_PATH,
        now=datetime.fromisoformat("2026-08-06T00:30:00+08:00"),
        project_root=tmp_path,
    )

    canonical_path = eval_root / Path(
        artifacts["heldout_40_owner_annotations_jsonl"]["path"]
    ).name
    canonical = builder._load_jsonl(canonical_path)
    combined_path = eval_root / Path(
        artifacts["combined_100_annotations_jsonl"]["path"]
    ).name
    completion_path = eval_root / Path(
        artifacts["owner_completion_manifest_json"]["path"]
    ).name
    combined = builder._load_jsonl(combined_path)
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    dev_stat_after = dev_labels.stat()
    files_after = {path for path in eval_root.rglob("*") if path.is_file()}
    assert evidence["heldout_completed_count"] == 40
    assert files_after - files_before == registered_outputs
    assert dev_labels.read_bytes() == frozen_dev_bytes
    assert dev_stat_after.st_ino == dev_stat_before.st_ino
    assert dev_stat_after.st_mtime_ns == dev_stat_before.st_mtime_ns
    assert evidence["dev_owner_annotations"] == str(dev_labels.relative_to(tmp_path))
    assert evidence["dev_owner_annotations_sha256"] == artifacts[
        "dev_60_owner_annotations_jsonl"
    ]["sha256"]
    assert completion["dev_owner_annotations_path"] == str(
        dev_labels.relative_to(tmp_path)
    )
    assert completion["dev_owner_annotations_sha256"] == artifacts[
        "dev_60_owner_annotations_jsonl"
    ]["sha256"]
    assert combined[:60] == builder._normalized_completed_records(
        builder._load_jsonl(dev_labels)
    )
    assert evidence["heldout_adjudication_export"] == str(
        export_path.relative_to(tmp_path)
    )
    assert evidence["heldout_ai_draft_sha256"] == hashlib.sha256(
        draft_path.read_bytes()
    ).hexdigest()
    assert canonical[0]["annotation_owner"] == "owner-ouyang"
    assert canonical[0]["drafter_id"] == "ChatGPT GPT-5.6 Pro"
    assert "adjudication" not in canonical[0]
    assert "draft_gold" not in canonical[0]

    adjudication_evidence = evaluator.validate_heldout_adjudication_evidence(
        design,
        adjudicated_path=export_path,
        ai_draft_path=draft_path,
        project_root=tmp_path,
    )
    assert adjudication_evidence["row_count"] == 40
    assert adjudication_evidence["changed_count"] == 0
    assert adjudication_evidence["confirmed_unchanged_count"] == 40
    assert adjudication_evidence["canonical_reconstruction_passed"] is True
    assert adjudication_evidence["adjudication_export_sha256"] == hashlib.sha256(
        export_path.read_bytes()
    ).hexdigest()


def test_combine_owner_requires_declared_create_only_outputs() -> None:
    design = builder.load_evaluation_design(EVALUATION_DESIGN_V1_7_PATH)
    document = copy.deepcopy(design.document)
    document["artifacts"]["combined_100_annotations_jsonl"].pop("create_only")
    malformed = replace(design, document=document)

    with pytest.raises(
        builder.GoldSampleError,
        match="combined_100_annotations_jsonl must be create-only",
    ):
        builder._combine_owner_output_paths(
            malformed,
            project_root=ui.PROJECT_ROOT,
        )
