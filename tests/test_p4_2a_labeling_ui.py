from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from scripts import build_p4_2a_labeling_ui as labeling

from alphapilot.llm.p4_news_eval import EVALUATION_DESIGN_V2_PATH


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _blind_row(
    *,
    sample_index: int = 1,
    news_item_id: int = 268,
    title: str = "关于开展衍生品交易的可行性分析报告",
    original_text: str = "公司拟开展以套期保值为目的的衍生品交易业务。",
) -> dict[str, Any]:
    return {
        "schema_version": "p4.2a-gold-annotation-item-v1",
        "sample_version": "p4.2a-gold-v1",
        "contract_sha256": _sha("contract"),
        "sample_index": sample_index,
        "sample_group": "inventory_60",
        "trading_date": None,
        "stratum": {
            "source": "cninfo",
            "symbol_state": "bound",
            "require_announcement_body": True,
        },
        "rank_sha256": _sha(f"rank-{sample_index}"),
        "news_item_id": news_item_id,
        "source": "cninfo",
        "url": "https://static.cninfo.com.cn/finalpage/example.PDF",
        "title": title,
        "ingested_symbol": "001399",
        "published_at": "2026-08-02T16:00:00Z",
        "available_time": "2026-08-03T00:00:08.926021Z",
        "original_text": original_text,
        "body_state": "announcement_body",
        "content_hash": _sha("content"),
        "text_sha256": _sha(original_text),
        "body_evidence": {
            "annotation_text_character_count": len(original_text),
            "body_characters_in_original_text": len(original_text),
            "full_text_character_count": len(original_text),
            "full_text_sha256": _sha(original_text),
            "pdf_persisted": False,
            "pdf_sha256": _sha("pdf"),
            "required": True,
            "source": "cninfo_pdf",
            "text_truncated": False,
            "url": "https://static.cninfo.com.cn/finalpage/example.PDF",
        },
        "annotation_status": "pending",
        "annotation_owner": None,
        "annotated_at": None,
        "gold": {
            "symbols": None,
            "event_type": None,
            "direction": None,
            "materiality": None,
            "evidence_span": None,
            "notes": None,
        },
        "input_sha256": _sha("input"),
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _embedded_items(rendered: str) -> list[dict[str, Any]]:
    marker = '<script id="annotation-items" type="application/json">'
    start = rendered.index(marker) + len(marker)
    end = rendered.index("</script>", start)
    value = json.loads(rendered[start:end])
    assert isinstance(value, list)
    return value


def test_load_items_preserves_complete_frozen_rows(tmp_path: Path) -> None:
    sample = tmp_path / "sample.jsonl"
    row = _blind_row()
    _write_jsonl(sample, [row])

    items = labeling._load_items(sample)

    assert items == [row]
    assert set(items[0]) == labeling.ANNOTATION_ITEM_FIELDS
    assert items[0]["original_text"] == row["original_text"]
    assert items[0]["body_evidence"] == row["body_evidence"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda row: row.update({"unexpected": "not allowed"}),
        lambda row: row.update({"test_sampling_basis": "holdout"}),
        lambda row: row["body_evidence"].update({"prediction": {"materiality": 3}}),
        lambda row: row["body_evidence"].update({"predicted_score": 0.9}),
        lambda row: row["stratum"].update({"selection_rank_sha256": _sha("leak")}),
        lambda row: row["gold"].update({"event_type": "other"}),
    ],
)
def test_load_items_rejects_blindness_or_blank_gold_drift(
    tmp_path: Path,
    mutate: Any,
) -> None:
    sample = tmp_path / "sample.jsonl"
    row = _blind_row()
    mutate(row)
    _write_jsonl(sample, [row])

    with pytest.raises(labeling.LabelingUIError):
        labeling._load_items(sample)


def test_load_items_rejects_duplicate_ids_and_order_drift(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.jsonl"
    _write_jsonl(
        duplicate,
        [
            _blind_row(sample_index=1, news_item_id=268),
            _blind_row(sample_index=2, news_item_id=268),
        ],
    )
    with pytest.raises(labeling.LabelingUIError, match="duplicates news_item_id"):
        labeling._load_items(duplicate)

    reordered = tmp_path / "reordered.jsonl"
    _write_jsonl(
        reordered,
        [_blind_row(sample_index=2, news_item_id=269)],
    )
    with pytest.raises(labeling.LabelingUIError, match="order or sample_index"):
        labeling._load_items(reordered)


def test_render_structurally_escapes_html_and_embeds_full_rows(tmp_path: Path) -> None:
    attack = '</script><img src=x onerror="globalThis.PWNED=1">'
    row = _blind_row(title=attack, original_text=f"正文{attack}结尾")
    sample = tmp_path / "owner-sample.jsonl"
    _write_jsonl(sample, [row])
    items = labeling._load_items(sample)

    rendered = labeling._render(
        items,
        sample_path=sample,
        title=f"P4.2a 盲标 · {attack}",
    )

    assert attack not in rendered
    assert "\\u003c/script\\u003e\\u003cimg" in rendered
    assert "&lt;/script&gt;&lt;img" in rendered
    assert ".innerHTML" not in rendered
    assert '$("meta").replaceChildren(...tags)' in rendered
    assert _embedded_items(rendered) == [row]


def test_render_keeps_storage_key_and_exports_only_mutable_overrides(
    tmp_path: Path,
) -> None:
    sample = tmp_path / "P4.2a-gold-inventory60-v1.jsonl"
    row = _blind_row()
    _write_jsonl(sample, [row])

    rendered = labeling._render(
        labeling._load_items(sample),
        sample_path=sample,
        title="P4.2a 盲标",
    )

    assert 'const KEY = "p4.2a-labels:" + "P4.2a-gold-inventory60-v1.jsonl"' in rendered
    assert "return {...entry.item," in rendered
    assert 'annotation_status: "completed"' in rendered
    assert 'id="annotator"' in rendered
    assert "const annotator = ($(\"annotator\").value || \"\").trim()" in rendered
    assert "if (!annotator)" in rendered
    assert "annotation_owner: annotator" in rendered
    assert 'annotation_owner: "owner"' not in rendered
    assert "annotated_at: annotatedAt" in rendered
    assert "gold: entry.gold" in rendered
    assert "news_item_id: it.news_item_id" not in rendered
    assert 'a.download = "P4.2a-gold-inventory60-v1.labels.jsonl"' in rendered


def test_render_v1_2_exports_dual_heldout_provenance() -> None:
    row = _blind_row()
    rendered = labeling._render(
        [row],
        sample_path=Path("heldout-v1.2.jsonl"),
        title="P4.2a heldout 人工裁定",
        annotation_type="ai_drafted_human_adjudicated",
        drafter_id="ChatGPT GPT-5.6 Pro",
    )

    assert (
        'const ANNOTATION_TYPE = "ai_drafted_human_adjudicated"'
        in rendered
    )
    assert 'const DRAFTER_ID = "ChatGPT GPT-5.6 Pro"' in rendered
    assert "annotation_type: ANNOTATION_TYPE" in rendered
    assert "drafter_id: DRAFTER_ID" in rendered
    assert "adjudicator_id: annotator" in rendered
    assert "annotation_owner: annotator" in rendered


def test_render_can_import_legacy_progress_without_importing_predictions(
    tmp_path: Path,
) -> None:
    sample = tmp_path / "sample.jsonl"
    _write_jsonl(sample, [_blind_row()])

    rendered = labeling._render(
        labeling._load_items(sample),
        sample_path=sample,
        title="P4.2a 盲标",
    )

    assert 'id="importBtn"' in rendered
    assert 'id="importInput"' in rendered
    assert "containsForbiddenImportKey(row)" in rendered
    assert '"selection_basis"' in rendered
    assert '"selection_rank"' in rendered
    assert '"eligible_pool_size"' in rendered
    assert '"prediction_status"' in rendered
    assert "imported[row.news_item_id] = importedLabel(row)" in rendered
    assert "labels = {...labels, ...imported}" in rendered
    assert "仅接受本盲标样本导出的无预测 JSONL" in rendered


def test_render_completion_gate_enforces_gold_schema_and_grounding(
    tmp_path: Path,
) -> None:
    sample = tmp_path / "sample.jsonl"
    _write_jsonl(sample, [_blind_row()])

    rendered = labeling._render(
        labeling._load_items(sample),
        sample_path=sample,
        title="P4.2a 盲标",
    )

    assert "/^[0-9]{6}$/.test(s)" in rendered
    assert "EVENT_TYPES.includes(gold.event_type)" in rendered
    assert "[-1, 0, 1].includes(gold.direction)" in rendered
    assert "[0, 1, 2, 3].includes(gold.materiality)" in rendered
    assert "item.original_text.includes(gold.evidence_span)" in rendered
    assert "const firstInvalid = prepared.find" in rendered
    assert "if (firstInvalid)" in rendered


def test_cli_is_create_only_and_does_not_overwrite(tmp_path: Path) -> None:
    sample = tmp_path / "sample.jsonl"
    output = tmp_path / "labeling.html"
    _write_jsonl(sample, [_blind_row()])

    assert labeling.main(["--sample", str(sample), "--output", str(output)]) == 0
    first_payload = output.read_bytes()
    assert first_payload

    assert labeling.main(["--sample", str(sample), "--output", str(output)]) == 2
    assert output.read_bytes() == first_payload


def test_legacy_labeling_ui_rejects_v2_before_render_or_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sample = tmp_path / "missing-sample.jsonl"
    output = tmp_path / "must-not-exist.html"

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("legacy labeling output path must not run")

    monkeypatch.setattr(labeling, "_load_items", forbidden)
    monkeypatch.setattr(labeling, "_render", forbidden)
    monkeypatch.setattr(labeling, "_write_create_only", forbidden)

    result = labeling.main(
        [
            "--sample",
            str(sample),
            "--output",
            str(output),
            "--evaluation-design",
            str(EVALUATION_DESIGN_V2_PATH),
        ]
    )

    assert result == 2
    assert not output.exists()
    assert "labeling_ui_safety_gate_failed" in capsys.readouterr().err


def test_strict_json_rejects_duplicate_keys(tmp_path: Path) -> None:
    sample = tmp_path / "sample.jsonl"
    row = json.dumps(_blind_row(), ensure_ascii=False)
    duplicated = row[:-1] + ',"news_item_id":999}'
    sample.write_text(duplicated + "\n", encoding="utf-8")

    with pytest.raises(labeling.LabelingUIError, match="duplicate JSON key"):
        labeling._load_items(sample)
