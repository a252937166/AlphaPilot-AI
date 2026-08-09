from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from scripts import p4_2a_v2_dev_common as common
from scripts.p4_2a_v2_dev_common import (
    BLIND_FIELDS,
    DevelopmentFrameError,
    DevelopmentFrameSpec,
    FrozenFile,
    build_development_frame,
    candidate_identity_sha256,
    canonical_json_bytes,
    canonical_jsonl_bytes,
    forbidden_blind_paths,
    owner_order_rank,
    selection_rank,
    sha256_bytes,
)

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class SyntheticFrame:
    root: Path
    spec: DevelopmentFrameSpec
    retired_ids: frozenset[int]


def _sha(label: str) -> str:
    return sha256_bytes(label.encode())


def _write(path: Path, payload: bytes) -> FrozenFile:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return FrozenFile(path=path.as_posix(), sha256=sha256_bytes(payload))


def _synthetic_frame(tmp_path: Path) -> SyntheticFrame:
    source_design = _sha("synthetic-source-design")
    source_contract = _sha("synthetic-source-contract")
    model = "synthetic-drafter-baseline"
    inputs: list[JsonObject] = []
    predictions: list[JsonObject] = []
    for offset in range(14):
        identifier = 900_001 + offset
        active_input = _sha(f"active-{identifier}")
        declared_input = _sha(f"declared-{identifier}")
        text_hash = _sha(f"text-{identifier // 2}")
        source = "synthetic-a" if offset % 2 == 0 else "synthetic-b"
        candidate: JsonObject = {
            "available_time": f"2026-01-01T00:{offset:02d}:00Z",
            "body_evidence": {"kind": "synthetic", "present": True},
            "body_state": "synthetic_complete",
            "content_hash": _sha(f"content-{identifier}"),
            "contract_sha256": source_contract,
            "declared_input_sha256": declared_input,
            "design_sha256": source_design,
            "ingested_symbol": None if offset % 3 == 0 else "600000",
            "input_sha256": active_input,
            "model": model,
            "news_item_id": identifier,
            "original_text": f"synthetic original text {identifier}",
            "published_at": None if offset % 4 == 0 else "2026-01-01T00:00:00Z",
            "schema_version": "synthetic-candidate-v1",
            "source": source,
            "text_sha256": text_hash,
            "title": f"synthetic title {identifier}",
            "url": f"https://invalid.example/{identifier}",
        }
        if offset < 8:
            status = "ok"
            prediction: object = {"materiality": 2}
        elif offset < 12:
            status = "ok"
            prediction = {"materiality": 1}
        else:
            status = "extract_failed"
            prediction = None
        prediction_row: JsonObject = {
            "contract_sha256": source_contract,
            "declared_input_sha256": declared_input,
            "input_sha256": active_input,
            "latency_ms": 1,
            "llm_audit_latency_ms": 1,
            "model": model,
            "news_item_id": identifier,
            "prediction": prediction,
            "recorded_at_utc": "2026-01-01T00:00:00Z",
            "schema_version": "synthetic-prediction-v1",
            "security": {},
            "source": source,
            "status": status,
            "text_sha256": text_hash,
            "tokens": {},
        }
        inputs.append(candidate)
        predictions.append(prediction_row)

    input_rel = Path("fixtures/candidate-inputs.jsonl")
    prediction_rel = Path("fixtures/predictions.jsonl")
    materialization_rel = Path("fixtures/materialization-manifest.json")
    prediction_manifest_rel = Path("fixtures/prediction-manifest.json")
    retired_rel = Path("fixtures/retired.json")
    input_file = _write(tmp_path / input_rel, canonical_jsonl_bytes(inputs))
    input_file = replace(input_file, path=input_rel.as_posix())
    prediction_file = _write(tmp_path / prediction_rel, canonical_jsonl_bytes(predictions))
    prediction_file = replace(prediction_file, path=prediction_rel.as_posix())

    materialization: JsonObject = {
        "artifacts": {
            "eligible_inputs_jsonl": {
                "path": input_file.path,
                "sha256": input_file.sha256,
            }
        },
        "counts": {"eligible_candidates": len(inputs)},
        "lineage": {
            "evaluation_design": {"sha256": source_design},
            "prediction_contract": {"sha256": source_contract, "model": model},
            "freeze_receipt": {"sha256": _sha("synthetic-freeze")},
        },
    }
    materialization_file = _write(
        tmp_path / materialization_rel, canonical_json_bytes(materialization)
    )
    materialization_file = replace(materialization_file, path=materialization_rel.as_posix())
    identities = [
        {
            field: row[field]
            for field in (
                "news_item_id",
                "input_sha256",
                "declared_input_sha256",
                "text_sha256",
            )
        }
        for row in inputs
    ]
    prediction_manifest: JsonObject = {
        "candidate_count": len(inputs),
        "prediction_attempted_count": len(inputs),
        "prediction_success_count": 12,
        "prediction_failure_count": 2,
        "candidate_inputs_sha256": input_file.sha256,
        "candidate_predictions_sha256": prediction_file.sha256,
        "candidate_inputs": {
            "path": input_file.path,
            "sha256": input_file.sha256,
            "count": len(inputs),
            "identity_sha256": candidate_identity_sha256(inputs),
            "identities": identities,
        },
        "predictions": {
            "path": prediction_file.path,
            "sha256": prediction_file.sha256,
            "row_count": len(inputs),
            "attempted_count": len(inputs),
            "success_count": 12,
            "failure_count": 2,
        },
        "design": {"sha256": source_design},
        "prediction_contract": {"sha256": source_contract, "model": model},
        "materialization": {
            "manifest_path": materialization_file.path,
            "manifest_sha256": materialization_file.sha256,
        },
    }
    prediction_manifest_file = _write(
        tmp_path / prediction_manifest_rel, canonical_json_bytes(prediction_manifest)
    )
    prediction_manifest_file = replace(
        prediction_manifest_file, path=prediction_manifest_rel.as_posix()
    )

    retired_rows = inputs[:2]
    retired_ids = frozenset(int(row["news_item_id"]) for row in retired_rows)
    retired_manifest: JsonObject = {
        "candidate_inputs": {
            "path": input_file.path,
            "sha256": input_file.sha256,
            "count": len(inputs),
        },
        "candidate_predictions": {
            "path": prediction_file.path,
            "sha256": prediction_file.sha256,
            "manifest_path": prediction_manifest_file.path,
            "manifest_sha256": prediction_manifest_file.sha256,
        },
        "selection": {
            "selected_count": len(retired_rows),
            "selected": [
                {
                    "news_item_id": row["news_item_id"],
                    "input_sha256": row["input_sha256"],
                    "declared_input_sha256": row["declared_input_sha256"],
                    "text_sha256": row["text_sha256"],
                    "selection_rank_sha256": _sha(f"retired-{row['news_item_id']}"),
                }
                for row in retired_rows
            ],
        },
    }
    retired_file = _write(tmp_path / retired_rel, canonical_json_bytes(retired_manifest))
    retired_file = replace(retired_file, path=retired_rel.as_posix())
    retired_id_hash = sha256_bytes(
        json.dumps(sorted(retired_ids), separators=(",", ":")).encode("ascii")
    )
    spec = DevelopmentFrameSpec(
        design_path="config/synthetic-v2.yaml",
        design_sha256=_sha("synthetic-v2-design"),
        frame_id="synthetic-development-frame",
        sampling_seed="synthetic-development-seed",
        source_design_sha256=source_design,
        source_contract_sha256=source_contract,
        source_model=model,
        candidate_inputs=input_file,
        candidate_input_manifest=materialization_file,
        baseline_predictions=prediction_file,
        baseline_prediction_manifest=prediction_manifest_file,
        retired_selection=retired_file,
        retired_count=2,
        retired_sorted_ids_sha256=retired_id_hash,
        input_count=14,
        positive_count=8,
        negative_count=4,
        failed_count=2,
        positive_available_after_retirement=6,
        negative_available_after_retirement=4,
        positive_selected=3,
        negative_selected=2,
        selection_manifest_path="artifacts/development/private.json",
        owner_blind_path="artifacts/development/blind.jsonl",
        artifact_root="artifacts",
    )
    return SyntheticFrame(root=tmp_path, spec=spec, retired_ids=retired_ids)


def _load_jsonl(path: Path) -> list[JsonObject]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_builds_deterministic_blind_create_only_pair_from_synthetic_pool(
    tmp_path: Path,
) -> None:
    fixture = _synthetic_frame(tmp_path)

    result = build_development_frame(fixture.root, fixture.spec)

    manifest = json.loads(result.selection_manifest_path.read_text())
    blind = _load_jsonl(result.owner_blind_path)
    assert result.selected_count == 5
    assert manifest["audit"]["partition_before_retirement"] == {
        "predicted_positive": 8,
        "predicted_negative": 4,
        "extract_failed": 2,
    }
    assert manifest["audit"]["available_after_retirement"] == {
        "predicted_positive": 6,
        "predicted_negative": 4,
        "extract_failed": 2,
    }
    selected = manifest["selection"]["selected"]
    assert all("sampling_stratum" in row for row in selected)
    assert all("selection_rank_sha256" in row for row in selected)
    assert fixture.retired_ids.isdisjoint(row["news_item_id"] for row in selected)
    assert [row["sample_index"] for row in blind] == list(range(1, 6))
    assert all(set(row) == BLIND_FIELDS and row["gold"] == {} for row in blind)
    assert all(forbidden_blind_paths(row) == [] for row in blind)
    assert result.owner_blind_sha256 == common.sha256_file(result.owner_blind_path)
    assert result.selection_manifest_sha256 == common.sha256_file(result.selection_manifest_path)
    for private, owner in zip(selected, blind, strict=True):
        assert private["news_item_id"] == owner["news_item_id"]
        assert private["owner_order_sha256"] == owner_order_rank(
            design_sha256=fixture.spec.design_sha256,
            news_item_id=owner["news_item_id"],
            input_sha256=owner["input_sha256"],
        )
        assert private["selection_rank_sha256"] == selection_rank(
            seed=fixture.spec.sampling_seed,
            sampling_stratum=private["sampling_stratum"],
            news_item_id=owner["news_item_id"],
            input_sha256=owner["input_sha256"],
        )

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        build_development_frame(fixture.root, fixture.spec)


def test_rejects_synthetic_input_prediction_identity_drift(tmp_path: Path) -> None:
    fixture = _synthetic_frame(tmp_path)
    path = tmp_path / fixture.spec.baseline_predictions.path
    rows = _load_jsonl(path)
    rows[0]["source"] = "synthetic-tamper"
    payload = canonical_jsonl_bytes(rows)
    path.write_bytes(payload)
    spec = replace(
        fixture.spec,
        baseline_predictions=replace(
            fixture.spec.baseline_predictions, sha256=sha256_bytes(payload)
        ),
    )

    with pytest.raises(DevelopmentFrameError, match="identity join"):
        build_development_frame(tmp_path, spec, publish=False)


def test_rejects_nested_blind_selection_leakage() -> None:
    assert forbidden_blind_paths(
        {"safe": {"nested": [{"selection_rank_sha256": _sha("leak")}]}}
    ) == ["$.safe.nested[0].selection_rank_sha256"]


def test_atomic_pair_rolls_back_first_link_when_second_link_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _synthetic_frame(tmp_path)
    real_link = common.os.link
    calls = 0

    def fail_second_link(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic second-link failure")
        real_link(source, target)

    monkeypatch.setattr(common.os, "link", fail_second_link)
    with pytest.raises(OSError, match="second-link"):
        build_development_frame(tmp_path, fixture.spec)

    assert not (tmp_path / fixture.spec.selection_manifest_path).exists()
    assert not (tmp_path / fixture.spec.owner_blind_path).exists()
