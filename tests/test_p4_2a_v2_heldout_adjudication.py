from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from scripts import build_p4_2a_v2_heldout_adjudication_ui as ui
from scripts import p4_2a_v2_dev_common as common
from scripts import prepare_p4_2a_v2_heldout as prepare
from scripts import seal_p4_2a_v2_ai_draft as base
from scripts import seal_p4_2a_v2_heldout_draft as heldout


def _sha(value: str) -> str:
    return base.sha256_bytes(value.encode())


def _contract(tmp_path: Path) -> base.V2AdjudicationContract:
    artifact_root = tmp_path / "docs/phase4/eval/v2-calibration/heldout"
    artifact_root.mkdir(parents=True)
    return base.V2AdjudicationContract(
        project_root=tmp_path.resolve(),
        design_path=tmp_path / heldout.DESIGN_RELATIVE_PATH,
        design_ref={"path": heldout.DESIGN_RELATIVE_PATH, "sha256": _sha("design")},
        frame_id=heldout.FRAME_ID,
        expected_count=heldout.EXPECTED_COUNT,
        taxonomy=frozenset({"other", "major_contract"}),
        artifacts={
            "development_private_selection_manifest": artifact_root / "selection.json",
            "development_owner_blind_jsonl": artifact_root / "blind.jsonl",
            "development_ai_draft_jsonl": artifact_root / "draft.jsonl",
            "development_adjudication_html": artifact_root / "adjudication.html",
            "development_owner_raw_export_jsonl": artifact_root / "owner-export.jsonl",
            "development_human_adjudicated_jsonl": artifact_root / "human.jsonl",
            "development_owner_completion_manifest": artifact_root / "completion.json",
        },
        blind_schema=heldout.BLIND_SCHEMA,
    )


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


def _registered_contract(
    binding: prepare.HeldoutBinding,
) -> base.V2AdjudicationContract:
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


def _write_full_execution_bundle(
    binding: prepare.HeldoutBinding,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _candidates, predictions, _execution_id = (
        prepare._write_synthetic_production_execution_fixture(binding)
    )
    prepare.run_select_blind(binding)
    return (
        prepare._load_jsonl(binding.artifacts["owner_blind"], "blind"),
        predictions,
    )


def _blind(contract: base.V2AdjudicationContract) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(1, heldout.EXPECTED_COUNT + 1):
        text = f"公司 {index} 公告签署重大合同，金额以正式公告为准。"
        rows.append(
            {
                "schema_version": heldout.BLIND_SCHEMA,
                "design": dict(contract.design_ref),
                "frame_id": heldout.FRAME_ID,
                "sample_index": index,
                "news_item_id": 20_000 + index,
                "source": "sina_company_news",
                "url": f"https://example.test/heldout/{index}",
                "title": f"公司 {index} 签署重大合同",
                "ingested_symbol": f"{index:06d}",
                "published_at": "2026-08-06T00:00:00Z",
                "available_time": "2026-08-06T00:01:00Z",
                "original_text": text,
                "input_sha256": _sha(f"input-{index}"),
                "text_sha256": _sha(text),
                "body_state": "title_only",
                "body_evidence": {"required": False, "source_lineage": "fixture"},
                "gold": {},
            }
        )
    return rows


def _candidate(blind: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "schema_version": base.CANDIDATE_DRAFT_SCHEMA,
            "news_item_id": row["news_item_id"],
            "draft_label": {
                "symbols": [row["ingested_symbol"]],
                "event_type": "major_contract",
                "direction": 1,
                "materiality": 2,
                "evidence_span": row["original_text"],
                "notes": None,
            },
        }
        for row in blind
    ]


def _manifest(
    contract: base.V2AdjudicationContract,
    blind: list[dict[str, Any]],
    payload: bytes,
) -> dict[str, Any]:
    selected = []
    for index, row in enumerate(blind, 1):
        selected.append(
            {
                "sample_index": index,
                "sampling_stratum": (
                    "predicted_positive" if index <= 40 else "predicted_negative"
                ),
                "selection_rank_sha256": _sha(f"rank-{index}"),
                "owner_order_sha256": _sha(f"owner-{index}"),
                "news_item_id": row["news_item_id"],
                "source": row["source"],
                "input_sha256": row["input_sha256"],
                "declared_input_sha256": _sha(f"declared-{index}"),
                "text_sha256": row["text_sha256"],
                "contract_sha256": heldout.EXPECTED_HELDOUT_CONTRACT_SHA256,
                "model": heldout.EVALUATED_MODEL,
            }
        )
    return {
        "schema_version": heldout.SELECTION_MANIFEST_SCHEMA,
        "design": dict(contract.design_ref),
        "frame_id": heldout.FRAME_ID,
        "source_lineage": {},
        "audit": {},
        "selection": {
            "algorithm": "sha256_rank_without_replacement_per_stratum_v1",
            "seed": "frozen-heldout-seed",
            "without_replacement": True,
            "selected_counts": dict(heldout.EXPECTED_COUNTS),
            "selected": selected,
        },
        "owner_delivery": {
            "path": str(
                contract.artifacts["development_owner_blind_jsonl"].relative_to(
                    contract.project_root
                )
            ),
            "sha256": base.sha256_bytes(payload),
            "row_count": heldout.EXPECTED_COUNT,
            "sampling_stratum_visible": False,
            "prediction_visible": False,
            "selection_rank_visible": False,
            "gold_state": "empty_object_pending_ai_draft_and_human_adjudication",
        },
        "production_writes": False,
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> bytes:
    payload = base.canonical_jsonl_bytes(rows)
    path.write_bytes(payload)
    return payload


def _write_bundle(
    contract: base.V2AdjudicationContract,
    blind: list[dict[str, Any]],
) -> None:
    payload = _write_jsonl(contract.artifacts["development_owner_blind_jsonl"], blind)
    contract.artifacts["development_private_selection_manifest"].write_bytes(
        base.canonical_json_bytes(_manifest(contract, blind, payload))
    )


def test_heldout60_seal_binds_frame_drafter_and_verbatim_evidence(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    blind = _blind(contract)
    sealed = heldout.seal_candidate_rows(
        blind,
        _candidate(blind),
        contract=contract,
        drafter_id="OpenAI Codex GPT-5",
        drafted_at="2026-08-10T04:02:00Z",
    )

    assert len(sealed) == 60
    assert all(row["frame_id"] == heldout.FRAME_ID for row in sealed)
    assert all(row["drafter_id"] == "OpenAI Codex GPT-5" for row in sealed)
    assert all(
        row["draft_label"]["evidence_span"] in blind[index]["original_text"]
        for index, row in enumerate(sealed)
    )

    invalid = _candidate(blind)
    invalid[0]["draft_label"]["evidence_span"] = "并非原文的合成句"
    with pytest.raises(base.V2AdjudicationError, match="contiguous quote"):
        heldout.seal_candidate_rows(
            blind,
            invalid,
            contract=contract,
            drafter_id="OpenAI Codex GPT-5",
            drafted_at="2026-08-10T04:02:00Z",
        )
    with pytest.raises(base.V2AdjudicationError, match="registered drafter_id"):
        heldout.seal_candidate_rows(
            blind,
            _candidate(blind),
            contract=contract,
            drafter_id="qwen3.6-plus",
            drafted_at="2026-08-10T04:02:00Z",
        )


def test_heldout_selection_requires_40_20_and_hides_recursive_metadata(
    tmp_path: Path,
) -> None:
    contract = _contract(tmp_path)
    blind = _blind(contract)
    payload = base.canonical_jsonl_bytes(blind)
    manifest = _manifest(contract, blind, payload)
    heldout.validate_selection_manifest_binding(manifest, blind, payload, contract=contract)

    wrong = copy.deepcopy(manifest)
    wrong["selection"]["selected_counts"]["predicted_positive"] = 39
    with pytest.raises(base.V2AdjudicationError, match="counts/order"):
        heldout.validate_selection_manifest_binding(wrong, blind, payload, contract=contract)

    leaked = copy.deepcopy(blind)
    leaked[0]["body_evidence"]["selection_rank"] = "secret"
    with pytest.raises(base.V2AdjudicationError, match="leaks metadata"):
        base.validate_blind_rows(leaked, contract=contract)

    scored = copy.deepcopy(blind)
    scored[0]["body_evidence"]["score"] = 0.999
    with pytest.raises(base.V2AdjudicationError, match=r"body_evidence\.score"):
        base.validate_blind_rows(scored, contract=contract)
    with pytest.raises(common.DevelopmentFrameError, match=r"body_evidence\.score"):
        common.validate_blind_row(scored[0])


@pytest.mark.parametrize(
    "drafter_id",
    ["openai-codex gpt5", " OpenAI Codex GPT-5", "OpenAI Codex GPT-5 "],
)
def test_heldout_drafter_identity_must_match_registered_bytes(
    tmp_path: Path,
    drafter_id: str,
) -> None:
    contract = _contract(tmp_path)
    blind = _blind(contract)
    with pytest.raises(base.V2AdjudicationError, match="registered drafter_id"):
        heldout.seal_candidate_rows(
            blind,
            _candidate(blind),
            contract=contract,
            drafter_id=drafter_id,
            drafted_at="2026-08-10T04:02:00Z",
        )


@pytest.mark.parametrize("tamper", ["seed", "source_lineage", "rank"])
def test_draft_gate_rederives_full_producer_chain_before_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    binding = _temporary_binding(tmp_path)
    contract = _registered_contract(binding)
    blind, _predictions = _write_full_execution_bundle(binding)
    selection_path = binding.artifacts["private_selection"]
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    if tamper == "seed":
        selection["selection"]["seed"] = "attacker-controlled-seed"
    elif tamper == "source_lineage":
        selection["source_lineage"]["execution"]["execution_id"] = "attacker"
    else:
        selection["selection"]["selected"][0]["selection_rank_sha256"] = "f" * 64
    selection_path.write_bytes(base.canonical_json_bytes(selection))
    candidate_path = tmp_path / "candidate.jsonl"
    candidate_path.write_bytes(base.canonical_jsonl_bytes(_candidate(blind)))
    monkeypatch.setattr(heldout, "load_registered_contract", lambda _path: contract)
    monkeypatch.setattr(prepare, "load_binding", lambda _root: binding)

    assert (
        heldout.main(
            [
                "--candidate-draft",
                str(candidate_path),
                "--drafted-at",
                "2026-08-10T04:02:00Z",
            ]
        )
        == 2
    )
    assert binding.artifacts["ai_draft"].exists() is False


def test_ui_gate_rederives_full_producer_chain_before_owner_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _temporary_binding(tmp_path)
    contract = _registered_contract(binding)
    blind, _predictions = _write_full_execution_bundle(binding)
    sealed = heldout.seal_candidate_rows(
        blind,
        _candidate(blind),
        contract=contract,
        drafter_id=heldout.EXPECTED_DRAFTER_ID,
        drafted_at="2026-08-10T04:02:00Z",
    )
    binding.artifacts["ai_draft"].write_bytes(base.canonical_jsonl_bytes(sealed))
    selection_path = binding.artifacts["private_selection"]
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selection["source_lineage"]["binding_scope"] = "payload_only_synthetic_or_unit_helper"
    selection_path.write_bytes(base.canonical_json_bytes(selection))
    monkeypatch.setattr(heldout, "load_registered_contract", lambda _path: contract)
    monkeypatch.setattr(prepare, "load_binding", lambda _root: binding)

    assert ui.main([]) == 2
    assert binding.artifacts["adjudication_ui"].exists() is False


def test_draft_gate_rejects_incomplete_materialization_before_owner_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _temporary_binding(tmp_path)
    contract = _registered_contract(binding)
    blind, _predictions = _write_full_execution_bundle(binding)
    manifest_path = binding.artifacts["materialization_manifest"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["layers"]["all_candidates"]
    manifest_path.write_bytes(base.canonical_json_bytes(manifest))
    candidate_path = tmp_path / "candidate.jsonl"
    candidate_path.write_bytes(base.canonical_jsonl_bytes(_candidate(blind)))
    monkeypatch.setattr(heldout, "load_registered_contract", lambda _path: contract)
    monkeypatch.setattr(prepare, "load_binding", lambda _root: binding)

    assert (
        heldout.main(
            [
                "--candidate-draft",
                str(candidate_path),
                "--drafted-at",
                "2026-08-10T04:02:00Z",
            ]
        )
        == 2
    )
    assert binding.artifacts["ai_draft"].exists() is False


def test_heldout_seal_and_ui_clis_are_create_only_and_stop_before_owner_gold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    binding = _temporary_binding(tmp_path)
    contract = _registered_contract(binding)
    blind, _predictions = _write_full_execution_bundle(binding)
    candidate_path = tmp_path / "candidate.jsonl"
    _write_jsonl(candidate_path, _candidate(blind))
    monkeypatch.setattr(heldout, "load_registered_contract", lambda _path: contract)
    monkeypatch.setattr(prepare, "load_binding", lambda _root: binding)

    assert (
        heldout.main(
            [
                "--candidate-draft",
                str(candidate_path),
                "--drafted-at",
                "2026-08-10T04:02:00Z",
            ]
        )
        == 0
    )
    assert ui.main([]) == 0
    rendered = contract.artifacts["development_adjudication_html"].read_text()
    assert "Held-out 60 人工裁定" in rendered
    assert "sampling_stratum" not in rendered
    assert "selection_rank" not in rendered
    assert "prediction" not in rendered.casefold()
    assert 'value="ouyang" readonly' in rendered
    assert "EXPECTED_ADJUDICATOR_ID" in rendered
    assert base.sha256_bytes(binding.artifacts["private_selection"].read_bytes()) in rendered
    assert "fetch(" not in rendered
    assert "XMLHttpRequest" not in rendered
    assert contract.artifacts["development_owner_raw_export_jsonl"].exists() is False
    assert contract.artifacts["development_human_adjudicated_jsonl"].exists() is False
    assert contract.artifacts["development_owner_completion_manifest"].exists() is False

    sealed_bytes = contract.artifacts["development_ai_draft_jsonl"].read_bytes()
    ui_bytes = contract.artifacts["development_adjudication_html"].read_bytes()
    assert heldout.main(
        [
            "--candidate-draft",
            str(candidate_path),
            "--drafted-at",
            "2026-08-10T04:02:00Z",
        ]
    ) == 2
    assert ui.main([]) == 2
    assert contract.artifacts["development_ai_draft_jsonl"].read_bytes() == sealed_bytes
    assert contract.artifacts["development_adjudication_html"].read_bytes() == ui_bytes
    assert capsys.readouterr().err.count("refusing to overwrite") == 2


def test_registered_heldout_contract_is_60_and_dev45_contract_stays_unchanged() -> None:
    registered = heldout.load_registered_contract()
    development = base.load_registered_contract()

    assert registered.frame_id == heldout.FRAME_ID
    assert registered.expected_count == 60
    assert registered.blind_schema == heldout.BLIND_SCHEMA
    assert registered.artifacts["development_ai_draft_jsonl"].name.endswith(
        "heldout-frame-v2.labels-ai-drafted.jsonl"
    )
    assert development.frame_id == base.FRAME_ID
    assert development.expected_count == 45
    assert development.blind_schema == base.BLIND_SCHEMA
