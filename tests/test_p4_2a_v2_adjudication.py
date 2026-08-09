from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from scripts import build_p4_2a_v2_adjudication_ui as ui
from scripts import finalize_p4_2a_v2_dev_adjudication as finalizer
from scripts import seal_p4_2a_v2_ai_draft as seal


def _sha(value: str) -> str:
    return seal.sha256_bytes(value.encode())


def _contract(tmp_path: Path) -> seal.V2AdjudicationContract:
    root = tmp_path.resolve()
    development = root / "docs/phase4/eval/v2-calibration/development"
    development.mkdir(parents=True)
    artifacts = {
        "development_private_selection_manifest": development / "selection.json",
        "development_owner_blind_jsonl": development / "blind.jsonl",
        "development_ai_draft_jsonl": development / "draft.jsonl",
        "development_adjudication_html": development / "adjudication.html",
        "development_owner_raw_export_jsonl": development / "owner-export.jsonl",
        "development_human_adjudicated_jsonl": development / "human.jsonl",
        "development_owner_completion_manifest": development / "completion.json",
    }
    return seal.V2AdjudicationContract(
        project_root=root,
        design_path=root / seal.DESIGN_RELATIVE_PATH,
        design_ref={"path": seal.DESIGN_RELATIVE_PATH, "sha256": _sha("design")},
        frame_id=seal.FRAME_ID,
        expected_count=45,
        taxonomy=frozenset({"other", "major_contract"}),
        artifacts=artifacts,
    )


def _body_evidence() -> dict[str, Any]:
    return {
        "required": False,
        "source": None,
        "url": None,
        "pdf_sha256": None,
        "full_text_sha256": None,
        "full_text_character_count": None,
        "annotation_text_character_count": None,
        "body_characters_in_original_text": None,
        "text_truncated": False,
        "pdf_persisted": False,
    }


def _blind_rows(contract: seal.V2AdjudicationContract) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(1, 46):
        original_text = f"公司 {index} 公告签署重大合同，金额以正式公告为准。"
        rows.append(
            {
                "schema_version": seal.BLIND_SCHEMA,
                "design": dict(contract.design_ref),
                "frame_id": contract.frame_id,
                "sample_index": index,
                "news_item_id": 10_000 + index,
                "source": "sina_company_news",
                "url": f"https://example.test/{index}",
                "title": f"公司 {index} 签署合同",
                "ingested_symbol": f"{index:06d}",
                "published_at": "2026-08-04T00:00:00Z",
                "available_time": "2026-08-04T00:01:00Z",
                "original_text": original_text,
                "input_sha256": _sha(f"input-{index}"),
                "text_sha256": _sha(original_text),
                "body_state": "title_only",
                "body_evidence": _body_evidence(),
                "gold": {},
            }
        )
    return rows


def _candidate_rows(blind: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "schema_version": seal.CANDIDATE_DRAFT_SCHEMA,
            "news_item_id": row["news_item_id"],
            "draft_label": {
                "symbols": [row["ingested_symbol"]],
                "event_type": "major_contract",
                "direction": 1,
                "materiality": 2,
                "evidence_span": "签署重大合同",
                "notes": None,
            },
        }
        for row in blind
    ]


def _selection_manifest(
    contract: seal.V2AdjudicationContract,
    blind: list[dict[str, Any]],
    blind_payload: bytes | None = None,
) -> dict[str, Any]:
    payload = blind_payload or seal.canonical_jsonl_bytes(blind)
    selected = []
    for index, row in enumerate(blind, 1):
        selected.append(
            {
                "sample_index": index,
                "sampling_stratum": (
                    "predicted_positive" if index <= 30 else "predicted_negative"
                ),
                "selection_rank_sha256": _sha(f"selection-{index}"),
                "owner_order_sha256": _sha(f"owner-order-{index}"),
                "news_item_id": row["news_item_id"],
                "source": row["source"],
                "input_sha256": row["input_sha256"],
                "declared_input_sha256": _sha(f"declared-{index}"),
                "text_sha256": row["text_sha256"],
                "contract_sha256": _sha("source-contract"),
                "model": "qwen3.7-flash",
            }
        )
    return {
        "schema_version": seal.SELECTION_MANIFEST_SCHEMA,
        "design": dict(contract.design_ref),
        "frame_id": contract.frame_id,
        "source_lineage": {},
        "audit": {},
        "selection": {
            "algorithm": "sha256_rank_without_replacement_per_stratum_v1",
            "seed": "synthetic-seed",
            "rank_preimage": "synthetic-rank-preimage",
            "owner_order_algorithm": "sha256_rank_without_sampling_stratum_v1",
            "owner_order_preimage": "synthetic-owner-order-preimage",
            "selected_counts": {
                "predicted_positive": 30,
                "predicted_negative": 15,
                "extract_failed": 0,
                "total": 45,
            },
            "without_replacement": True,
            "selected": selected,
        },
        "owner_delivery": {
            "path": str(
                contract.artifacts["development_owner_blind_jsonl"].relative_to(
                    contract.project_root
                )
            ),
            "sha256": seal.sha256_bytes(payload),
            "row_count": 45,
            "sampling_stratum_visible": False,
            "prediction_visible": False,
            "selection_rank_visible": False,
            "gold_state": "empty_object_pending_human_adjudication",
        },
        "production_writes": False,
    }


def _sealed(
    contract: seal.V2AdjudicationContract,
    blind: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return seal.seal_candidate_rows(
        blind,
        _candidate_rows(blind),
        contract=contract,
        drafter_id="Codex GPT-5.6",
        drafted_at="2026-08-09T13:00:00Z",
    )


def _owner_export(
    contract: seal.V2AdjudicationContract,
    blind: list[dict[str, Any]],
    draft: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, (blind_row, draft_row) in enumerate(zip(blind, draft, strict=True), 1):
        human = copy.deepcopy(draft_row["draft_label"])
        if index == 1:
            human["materiality"] = 1
            changed_fields = ["materiality"]
        else:
            changed_fields = []
        rows.append(
            {
                "schema_version": ui.EXPORT_SCHEMA,
                "design": dict(contract.design_ref),
                "frame_id": contract.frame_id,
                "sample_index": index,
                "news_item_id": blind_row["news_item_id"],
                "input_sha256": blind_row["input_sha256"],
                "sealed_draft_item_sha256": seal.sha256_bytes(seal.canonical_json_bytes(draft_row)),
                "draft_label": copy.deepcopy(draft_row["draft_label"]),
                "human_label": human,
                "annotation_status": "adjudicated",
                "adjudication": {
                    "method": "ai_drafted_human_adjudicated",
                    "drafter_id": "Codex GPT-5.6",
                    "adjudicator_id": "owner-ouyang",
                    "confirmed": True,
                    "changed": bool(changed_fields),
                    "changed_fields": changed_fields,
                    "adjudicated_at": f"2026-08-09T13:{index:02d}:00Z",
                },
            }
        )
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> bytes:
    payload = seal.canonical_jsonl_bytes(rows)
    path.write_bytes(payload)
    return payload


def _write_json(path: Path, value: dict[str, Any]) -> bytes:
    payload = seal.canonical_json_bytes(value)
    path.write_bytes(payload)
    return payload


def _write_bound_blind(
    contract: seal.V2AdjudicationContract,
    blind: list[dict[str, Any]],
) -> tuple[bytes, bytes]:
    blind_payload = _write_jsonl(
        contract.artifacts["development_owner_blind_jsonl"], blind
    )
    manifest_payload = _write_json(
        contract.artifacts["development_private_selection_manifest"],
        _selection_manifest(contract, blind, blind_payload),
    )
    return blind_payload, manifest_payload


def test_seal_dev45_binds_order_and_keeps_draft_out_of_gold(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    blind = _blind_rows(contract)
    sealed = _sealed(contract, blind)

    assert len(sealed) == 45
    assert [row["news_item_id"] for row in sealed] == [row["news_item_id"] for row in blind]
    assert all(set(row) == seal.SEALED_FIELDS for row in sealed)
    assert all("gold" not in row and row["draft_label"]["notes"] is None for row in sealed)
    assert all(row["design"] == contract.design_ref for row in sealed)

    candidates = _candidate_rows(blind)
    candidates[0], candidates[1] = candidates[1], candidates[0]
    with pytest.raises(seal.V2AdjudicationError, match="IDs/order"):
        seal.seal_candidate_rows(
            blind,
            candidates,
            contract=contract,
            drafter_id="Codex GPT-5.6",
            drafted_at="2026-08-09T13:00:00Z",
        )


def test_private_selection_binds_exact_blind_bytes_and_order(tmp_path: Path) -> None:
    contract = _contract(tmp_path)
    blind = _blind_rows(contract)
    blind_payload = seal.canonical_jsonl_bytes(blind)
    manifest = _selection_manifest(contract, blind, blind_payload)

    validated = seal.validate_selection_manifest_binding(
        manifest,
        blind,
        blind_payload,
        contract=contract,
    )
    assert validated["owner_delivery"]["sha256"] == seal.sha256_bytes(blind_payload)

    changed_text = copy.deepcopy(blind)
    changed_text[0]["original_text"] = "替换后的非冻结文本"
    changed_text[0]["text_sha256"] = _sha(changed_text[0]["original_text"])
    changed_payload = seal.canonical_jsonl_bytes(changed_text)
    with pytest.raises(seal.V2AdjudicationError, match="exact blind delivery"):
        seal.validate_selection_manifest_binding(
            manifest,
            changed_text,
            changed_payload,
            contract=contract,
        )

    changed_manifest = copy.deepcopy(manifest)
    changed_manifest["owner_delivery"]["sha256"] = seal.sha256_bytes(changed_payload)
    with pytest.raises(seal.V2AdjudicationError, match="IDs/order/input bindings"):
        seal.validate_selection_manifest_binding(
            changed_manifest,
            changed_text,
            changed_payload,
            contract=contract,
        )


@pytest.mark.parametrize(
    "drafter",
    [
        "",
        "qwen3.7-flash",
        "QWEN3.6-PLUS",
        "qwen3.7-flash@dashscope",
        "Qwen 3_6 Plus service",
        "ＱＷＥＮ３．７－ＦＬＡＳＨ",
    ],
)
def test_seal_rejects_missing_or_evaluated_drafter(tmp_path: Path, drafter: str) -> None:
    contract = _contract(tmp_path)
    blind = _blind_rows(contract)
    with pytest.raises(seal.V2AdjudicationError, match=r"drafter|drafting AI"):
        seal.seal_candidate_rows(
            blind,
            _candidate_rows(blind),
            contract=contract,
            drafter_id=drafter,
            drafted_at="2026-08-09T13:00:00Z",
        )


def test_seal_rejects_leaks_nonempty_gold_notes_and_ungrounded_evidence(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    blind = _blind_rows(contract)
    leaked = copy.deepcopy(blind)
    leaked[0]["body_evidence"]["prediction"] = {"materiality": 3}
    with pytest.raises(seal.V2AdjudicationError, match="leaks metadata"):
        seal.validate_blind_rows(leaked, contract=contract)

    opaque = copy.deepcopy(blind)
    opaque[0]["body_evidence"] = {
        "source_lineage": {"artifact_sha256": _sha("already-materialized-body")},
        "refetched": False,
    }
    assert (
        seal.validate_blind_rows(opaque, contract=contract)[0]["body_evidence"]
        == opaque[0]["body_evidence"]
    )

    nonempty_gold = copy.deepcopy(blind)
    nonempty_gold[0]["gold"] = {"materiality": 2}
    with pytest.raises(seal.V2AdjudicationError, match="identity/gold"):
        seal.validate_blind_rows(nonempty_gold, contract=contract)

    candidates = _candidate_rows(blind)
    candidates[0]["draft_label"]["notes"] = "AI rationale"
    with pytest.raises(seal.V2AdjudicationError, match="notes must be null"):
        seal.seal_candidate_rows(
            blind,
            candidates,
            contract=contract,
            drafter_id="Codex GPT-5.6",
            drafted_at="2026-08-09T13:00:00Z",
        )
    candidates[0]["draft_label"]["notes"] = None
    candidates[0]["draft_label"]["evidence_span"] = "not in source"
    with pytest.raises(seal.V2AdjudicationError, match="contiguous quote"):
        seal.seal_candidate_rows(
            blind,
            candidates,
            contract=contract,
            drafter_id="Codex GPT-5.6",
            drafted_at="2026-08-09T13:00:00Z",
        )


def test_offline_ui_escapes_content_and_requires_distinct_per_item_confirmation(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    blind = _blind_rows(contract)
    attack = '</script><img src=x onerror="globalThis.PWNED=1">'
    blind[0]["title"] = attack
    draft = _sealed(contract, blind)
    items, drafter_id = ui.build_ui_items(blind, draft, contract=contract)
    rendered = ui.render_ui(
        items,
        contract=contract,
        drafter_id=drafter_id,
        blind_sha256=_sha("blind"),
        draft_sha256=_sha("draft"),
        download_name="owner-export.jsonl",
    )

    assert attack not in rendered
    assert "\\u003c/script\\u003e\\u003cimg" in rendered
    assert "<script src=" not in rendered
    assert "fetch(" not in rendered
    assert "XMLHttpRequest" not in rendered
    assert ".innerHTML" not in rendered
    assert "sampling_stratum" not in rendered
    assert "selection_rank" not in rendered
    assert "prediction" not in rendered.casefold()
    assert "body_evidence" in rendered
    assert "adjudicator.toLocaleLowerCase()===DRAFTER_ID.toLocaleLowerCase()" in rendered
    assert "!value?.confirmed || value.adjudicator_id!==adjudicator" in rendered
    assert ui.EXPORT_SCHEMA in rendered
    assert "changed_fields:changed_fields" in rendered


def test_finalizer_recomputes_delta_provenance_and_preserves_body_evidence(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    blind = _blind_rows(contract)
    draft = _sealed(contract, blind)
    exported = _owner_export(contract, blind, draft)

    human, summary = finalizer.normalize_owner_export(blind, draft, exported, contract=contract)

    assert summary["changed_item_count"] == 1
    assert summary["unchanged_item_count"] == 44
    assert summary["changed_field_counts"]["materiality"] == 1
    assert human[0]["gold"]["materiality"] == 1
    assert human[0]["draft_label"]["materiality"] == 2
    assert human[0]["provenance"]["changed_fields"] == ["materiality"]
    assert human[0]["body_evidence"] == blind[0]["body_evidence"]

    tampered = copy.deepcopy(exported)
    tampered[0]["adjudication"]["changed_fields"] = []
    with pytest.raises(seal.V2AdjudicationError, match="delta/provenance"):
        finalizer.normalize_owner_export(blind, draft, tampered, contract=contract)
    same_identity = copy.deepcopy(exported)
    same_identity[0]["adjudication"]["adjudicator_id"] = "Codex GPT 5.6"
    with pytest.raises(seal.V2AdjudicationError, match="must differ"):
        finalizer.normalize_owner_export(blind, draft, same_identity, contract=contract)


def test_all_three_clis_reject_a_blind_not_bound_by_private_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    contract = _contract(tmp_path)
    blind = _blind_rows(contract)
    blind_payload = _write_jsonl(
        contract.artifacts["development_owner_blind_jsonl"], blind
    )
    manifest = _selection_manifest(contract, blind, blind_payload)
    manifest["owner_delivery"]["sha256"] = _sha("wrong-blind")
    _write_json(contract.artifacts["development_private_selection_manifest"], manifest)
    candidate_path = tmp_path / "candidate.jsonl"
    _write_jsonl(candidate_path, _candidate_rows(blind))
    monkeypatch.setattr(seal, "load_registered_contract", lambda _path: contract)

    assert (
        seal.main(
            [
                "--candidate-draft",
                str(candidate_path),
                "--drafter-id",
                "Codex GPT-5.6",
                "--drafted-at",
                "2026-08-09T13:00:00Z",
            ]
        )
        == 2
    )
    assert ui.main([]) == 2
    assert (
        finalizer.main(
            [
                "--owner-export",
                str(tmp_path / "not-reached.jsonl"),
                "--completed-at",
                "2026-08-09T14:00:00Z",
            ]
        )
        == 2
    )
    assert capsys.readouterr().err.count("exact blind delivery") == 3


def test_finalizer_rejects_ui_bytes_not_rendered_from_bound_blind_and_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    contract = _contract(tmp_path)
    blind = _blind_rows(contract)
    draft = _sealed(contract, blind)
    exported = _owner_export(contract, blind, draft)
    _write_bound_blind(contract, blind)
    _write_jsonl(contract.artifacts["development_ai_draft_jsonl"], draft)
    contract.artifacts["development_adjudication_html"].write_text(
        "<!doctype html><script>const sampling_stratum='leaked'</script>",
        encoding="utf-8",
    )
    downloaded = tmp_path / "downloaded-owner-export.jsonl"
    _write_jsonl(downloaded, exported)
    monkeypatch.setattr(seal, "load_registered_contract", lambda _path: contract)

    result = finalizer.main(
        [
            "--owner-export",
            str(downloaded),
            "--completed-at",
            "2026-08-09T14:00:00Z",
        ]
    )

    assert result == 2
    assert "differs from its deterministic" in capsys.readouterr().err
    assert not contract.artifacts["development_owner_raw_export_jsonl"].exists()
    assert not contract.artifacts["development_human_adjudicated_jsonl"].exists()
    assert not contract.artifacts["development_owner_completion_manifest"].exists()


def test_timestamp_causality_and_single_draft_timestamp_are_fail_closed(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    blind = _blind_rows(contract)
    draft = _sealed(contract, blind)

    inconsistent_draft = copy.deepcopy(draft)
    inconsistent_draft[1]["drafted_at"] = "2026-08-09T13:00:01Z"
    with pytest.raises(seal.V2AdjudicationError, match="consistent drafted_at"):
        seal.validate_sealed_draft_rows(blind, inconsistent_draft, contract=contract)

    before_draft = _owner_export(contract, blind, draft)
    before_draft[0]["adjudication"]["adjudicated_at"] = "2026-08-09T12:59:59Z"
    with pytest.raises(seal.V2AdjudicationError, match="precedes drafted_at"):
        finalizer.normalize_owner_export(blind, draft, before_draft, contract=contract)

    exported = _owner_export(contract, blind, draft)
    human, summary = finalizer.normalize_owner_export(
        blind, draft, exported, contract=contract
    )
    with pytest.raises(seal.V2AdjudicationError, match="completed_at precedes"):
        finalizer.completion_manifest(
            contract=contract,
            completed_at="2026-08-09T13:44:59Z",
            selection_manifest_payload=b"selection",
            blind_payload=b"blind",
            draft_payload=b"draft",
            ui_payload=b"ui",
            raw_export_payload=b"raw",
            human_payload=seal.canonical_jsonl_bytes(human),
            summary=summary,
        )


def test_finalizer_main_retains_raw_export_and_is_create_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    contract = _contract(tmp_path)
    blind = _blind_rows(contract)
    draft = _sealed(contract, blind)
    exported = _owner_export(contract, blind, draft)
    blind_payload, selection_payload = _write_bound_blind(contract, blind)
    draft_payload = _write_jsonl(
        contract.artifacts["development_ai_draft_jsonl"], draft
    )
    rendered_ui, _ = ui.render_registered_ui_payload(
        blind,
        draft,
        contract=contract,
        blind_payload=blind_payload,
        draft_payload=draft_payload,
    )
    contract.artifacts["development_adjudication_html"].write_bytes(rendered_ui)
    downloaded = tmp_path / "downloaded-owner-export.jsonl"
    raw_payload = _write_jsonl(downloaded, exported)
    monkeypatch.setattr(finalizer.seal, "load_registered_contract", lambda _path: contract)

    result = finalizer.main(
        [
            "--owner-export",
            str(downloaded),
            "--completed-at",
            "2026-08-09T14:00:00Z",
        ]
    )

    assert result == 0
    assert contract.artifacts["development_owner_raw_export_jsonl"].read_bytes() == raw_payload
    human = contract.artifacts["development_human_adjudicated_jsonl"].read_bytes()
    manifest = json.loads(contract.artifacts["development_owner_completion_manifest"].read_text())
    assert manifest["schema_version"] == finalizer.COMPLETION_SCHEMA
    assert manifest["artifacts"]["private_selection"]["sha256"] == seal.sha256_bytes(
        selection_payload
    )
    assert manifest["artifacts"]["owner_raw_export"]["sha256"] == _sha(raw_payload.decode())
    assert manifest["artifacts"]["human_adjudicated"]["sha256"] == seal.sha256_bytes(human)
    assert manifest["validation"]["raw_owner_export_retained"] is True
    assert manifest["validation"]["ui_byte_reconstruction_check"] is True
    assert manifest["validation"]["timestamp_order_check"] is True
    assert manifest["model_calls"] == 0
    assert manifest["heldout_touched"] is False

    second = finalizer.main(
        [
            "--owner-export",
            str(downloaded),
            "--completed-at",
            "2026-08-09T14:00:00Z",
        ]
    )
    assert second == 2
    assert contract.artifacts["development_human_adjudicated_jsonl"].read_bytes() == human
    assert "refusing to overwrite" in capsys.readouterr().err
