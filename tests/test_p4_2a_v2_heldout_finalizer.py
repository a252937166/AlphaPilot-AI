from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from scripts import build_p4_2a_v2_heldout_adjudication_ui as heldout_ui
from scripts import finalize_p4_2a_v2_heldout_adjudication as finalizer
from scripts import prepare_p4_2a_v2_heldout as prepare
from scripts import seal_p4_2a_v2_ai_draft as base_seal
from scripts import seal_p4_2a_v2_heldout_draft as heldout


def _temporary_binding(tmp_path: Path) -> prepare.HeldoutBinding:
    source = prepare.load_binding()
    return replace(
        source,
        root=tmp_path,
        artifacts={
            name: tmp_path / path.relative_to(source.root)
            for name, path in source.artifacts.items()
        },
    )


def _temporary_contract(
    binding: prepare.HeldoutBinding,
) -> base_seal.V2AdjudicationContract:
    registered = heldout.load_registered_contract()
    aliases = {
        "development_private_selection_manifest": "private_selection",
        "development_owner_blind_jsonl": "owner_blind",
        "development_ai_draft_jsonl": "ai_draft",
        "development_adjudication_html": "adjudication_ui",
        "development_owner_raw_export_jsonl": "owner_export",
        "development_human_adjudicated_jsonl": "human_adjudicated",
        "development_owner_completion_manifest": "owner_completion",
    }
    return replace(
        registered,
        project_root=binding.root,
        artifacts={name: binding.artifacts[target] for name, target in aliases.items()},
    )


def _prepare_owner_bundle(
    binding: prepare.HeldoutBinding,
    contract: base_seal.V2AdjudicationContract,
) -> Path:
    prepare._write_synthetic_production_execution_fixture(
        binding,
        started_at_utc="2026-08-10T05:00:00Z",
        recorded_at_utc="2026-08-10T05:00:30Z",
        completed_at_utc="2026-08-10T05:01:00Z",
    )
    prepare.run_select_blind(binding)
    selected_manifest = prepare._load_json(
        binding.artifacts["private_selection"], "selection"
    )
    assert selected_manifest["source_lineage"]["binding_scope"] == (
        "registered_full_execution"
    )
    selected_blind = prepare._load_jsonl(binding.artifacts["owner_blind"], "blind")

    candidates_for_draft = [
        {
            "schema_version": base_seal.CANDIDATE_DRAFT_SCHEMA,
            "news_item_id": row["news_item_id"],
            "draft_label": {
                "symbols": [row["ingested_symbol"]],
                "event_type": "other",
                "direction": 0,
                "materiality": 2,
                "evidence_span": row["original_text"],
                "notes": None,
            },
        }
        for row in selected_blind
    ]
    sealed = heldout.seal_candidate_rows(
        selected_blind,
        candidates_for_draft,
        contract=contract,
        drafter_id=heldout.EXPECTED_DRAFTER_ID,
        drafted_at="2026-08-10T05:02:00Z",
    )
    draft_payload = base_seal.canonical_jsonl_bytes(sealed)
    binding.artifacts["ai_draft"].write_bytes(draft_payload)
    blind_payload = binding.artifacts["owner_blind"].read_bytes()
    selection_payload = binding.artifacts["private_selection"].read_bytes()
    ui_payload, _count = heldout_ui.render_registered_ui_payload(
        selected_blind,
        sealed,
        contract=contract,
        blind_payload=blind_payload,
        draft_payload=draft_payload,
        selection_payload=selection_payload,
    )
    binding.artifacts["adjudication_ui"].write_bytes(ui_payload)

    owner_rows: list[dict[str, Any]] = []
    for blind, draft in zip(selected_blind, sealed, strict=True):
        owner_rows.append(
            {
                "schema_version": "p4.2a-v2-owner-adjudication-export-item-v1",
                "design": dict(contract.design_ref),
                "frame_id": contract.frame_id,
                "sample_index": blind["sample_index"],
                "news_item_id": blind["news_item_id"],
                "input_sha256": blind["input_sha256"],
                "sealed_draft_item_sha256": base_seal.sha256_bytes(
                    base_seal.canonical_json_bytes(draft)
                ),
                "draft_label": draft["draft_label"],
                "human_label": draft["draft_label"],
                "annotation_status": "adjudicated",
                "adjudication": {
                    "method": "ai_drafted_human_adjudicated",
                    "drafter_id": heldout.EXPECTED_DRAFTER_ID,
                    "adjudicator_id": "ouyang",
                    "confirmed": True,
                    "changed": False,
                    "changed_fields": [],
                    "adjudicated_at": "2026-08-10T05:05:00Z",
                },
            }
        )
    export = binding.root / "owner-export-download.jsonl"
    export.write_bytes(base_seal.canonical_jsonl_bytes(owner_rows))
    return export


def test_heldout_finalizer_freezes_raw_human_and_completion_without_scoring(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _temporary_binding(tmp_path)
    contract = _temporary_contract(binding)
    export = _prepare_owner_bundle(binding, contract)
    monkeypatch.setattr(prepare, "load_binding", lambda _root: binding)

    summary, completion, hashes = finalizer.finalize_owner_export(
        contract=contract,
        owner_export_path=export,
        completed_at="2026-08-10T05:06:00Z",
    )

    raw = contract.artifacts["development_owner_raw_export_jsonl"]
    human = contract.artifacts["development_human_adjudicated_jsonl"]
    manifest = contract.artifacts["development_owner_completion_manifest"]
    assert summary["row_count"] == 60
    assert set(hashes) == {raw, human, manifest}
    assert completion["heldout_touched"] is True
    assert completion["validation"]["blind_schema"] == heldout.BLIND_SCHEMA
    assert completion["model_execution"] == {
        "drafting_ai_inference_occurred": True,
        "drafting_ai": heldout.EXPECTED_DRAFTER_ID,
        "drafting_ai_is_evaluated_model": False,
        "selected_model": heldout.EVALUATED_MODEL,
        "selected_model_candidate_inference_count": len(
            prepare._load_jsonl(binding.artifacts["materialized_inputs"], "inputs")
        ),
        "selected_model_candidate_failure_count": 0,
        "final_one_shot_evaluation_calls": 0,
        "workflow_script_model_calls": 0,
    }
    assert completion["safety"]["one_shot_evaluation_consumed"] is False
    assert len(human.read_text(encoding="utf-8").splitlines()) == 60

    before = {path: path.read_bytes() for path in (raw, human, manifest)}
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        finalizer.finalize_owner_export(
            contract=contract,
            owner_export_path=export,
            completed_at="2026-08-10T05:06:00Z",
        )
    assert {path: path.read_bytes() for path in before} == before


def test_heldout_finalizer_recomputes_owner_delta_before_any_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _temporary_binding(tmp_path)
    contract = _temporary_contract(binding)
    export = _prepare_owner_bundle(binding, contract)
    monkeypatch.setattr(prepare, "load_binding", lambda _root: binding)
    rows = [json.loads(line) for line in export.read_text(encoding="utf-8").splitlines()]
    rows[0]["adjudication"]["changed"] = True
    export.write_bytes(base_seal.canonical_jsonl_bytes(rows))

    with pytest.raises(base_seal.V2AdjudicationError, match="claimed delta"):
        finalizer.finalize_owner_export(
            contract=contract,
            owner_export_path=export,
            completed_at="2026-08-10T05:06:00Z",
        )
    for key in ("owner_export", "human_adjudicated", "owner_completion"):
        assert binding.artifacts[key].exists() is False


def test_heldout_finalizer_rederives_ranks_before_any_owner_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _temporary_binding(tmp_path)
    contract = _temporary_contract(binding)
    export = _prepare_owner_bundle(binding, contract)
    monkeypatch.setattr(prepare, "load_binding", lambda _root: binding)
    selection_path = binding.artifacts["private_selection"]
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selection["selection"]["selected"][0]["owner_order_sha256"] = "e" * 64
    selection_path.write_bytes(base_seal.canonical_json_bytes(selection))

    with pytest.raises(base_seal.V2AdjudicationError, match="producer re-derivation"):
        finalizer.finalize_owner_export(
            contract=contract,
            owner_export_path=export,
            completed_at="2026-08-10T05:06:00Z",
        )
    for key in ("owner_export", "human_adjudicated", "owner_completion"):
        assert binding.artifacts[key].exists() is False


@pytest.mark.parametrize("adjudicator_id", [" Ouyang", "OUYANG", "ouyang "])
def test_heldout_finalizer_requires_exact_registered_owner_identity_before_output(
    tmp_path: Path,
    adjudicator_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _temporary_binding(tmp_path)
    contract = _temporary_contract(binding)
    export = _prepare_owner_bundle(binding, contract)
    monkeypatch.setattr(prepare, "load_binding", lambda _root: binding)
    rows = [json.loads(line) for line in export.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        row["adjudication"]["adjudicator_id"] = adjudicator_id
    export.write_bytes(base_seal.canonical_jsonl_bytes(rows))

    with pytest.raises(base_seal.V2AdjudicationError, match="actor identity"):
        finalizer.finalize_owner_export(
            contract=contract,
            owner_export_path=export,
            completed_at="2026-08-10T05:06:00Z",
        )
    for key in ("owner_export", "human_adjudicated", "owner_completion"):
        assert binding.artifacts[key].exists() is False


@pytest.mark.parametrize(
    ("adjudicated_at", "completed_at", "message"),
    [
        (
            "2026-08-10T05:01:59Z",
            "2026-08-10T05:06:00Z",
            "adjudicated_at precedes drafted_at",
        ),
        (
            "2026-08-10T05:05:00Z",
            "2026-08-10T05:04:59Z",
            "completion timestamp precedes owner adjudication",
        ),
    ],
)
def test_heldout_finalizer_rejects_cross_stage_timestamp_inversion_before_output(
    tmp_path: Path,
    adjudicated_at: str,
    completed_at: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _temporary_binding(tmp_path)
    contract = _temporary_contract(binding)
    export = _prepare_owner_bundle(binding, contract)
    monkeypatch.setattr(prepare, "load_binding", lambda _root: binding)
    rows = [json.loads(line) for line in export.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        row["adjudication"]["adjudicated_at"] = adjudicated_at
    export.write_bytes(base_seal.canonical_jsonl_bytes(rows))

    with pytest.raises(base_seal.V2AdjudicationError, match=message):
        finalizer.finalize_owner_export(
            contract=contract,
            owner_export_path=export,
            completed_at=completed_at,
        )
    for key in ("owner_export", "human_adjudicated", "owner_completion"):
        assert binding.artifacts[key].exists() is False
