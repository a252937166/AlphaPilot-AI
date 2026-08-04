from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from scripts import run_p4_2a_dev_iteration as dev_runner
from scripts import run_p4_2a_heldout_predictions as heldout
from scripts import run_p4_2a_v1_7_selection as runner

from alphapilot.llm.p4_news_eval import (
    EventEvaluationDesign,
    load_event_evaluation_design,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy(root: Path, relative: str) -> None:
    source = PROJECT_ROOT / relative
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())


def _labels() -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    path = (
        PROJECT_ROOT
        / "docs/phase4/eval/P4.2a-gold-inventory60-v1.labels-ai-drafted.jsonl"
    )
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        result[int(row["news_item_id"])] = row
    assert len(result) == 60
    return result


def _fixture_design(tmp_path: Path) -> EventEvaluationDesign:
    source_design = load_event_evaluation_design(
        PROJECT_ROOT / "config/p4_event_evaluation_v1_6.yaml",
        project_root=PROJECT_ROOT,
    )
    _copy(tmp_path, "config/p4_event_evaluation_v1_6.yaml")
    _copy(tmp_path, "config/p4_event_extract_eval_v1_7.yaml")
    selection = source_design.document["model_selection"]
    incumbent = selection["incumbent"]
    for key in (
        "design",
        "contract",
        "dev_predictions",
        "dev_manifest",
        "dev_report",
        "dev_final_predictions",
        "dev_final_manifest",
        "freeze_receipt",
    ):
        _copy(tmp_path, incumbent[key]["path"])
    design_path = tmp_path / "config/p4_event_evaluation_v1_6.yaml"
    assert _sha256(design_path) == source_design.sha256
    return replace(
        source_design,
        path=design_path,
        document=copy.deepcopy(source_design.document),
    )


def _candidate_rows(
    design: EventEvaluationDesign,
    *,
    pass_gates: bool,
) -> list[dict[str, Any]]:
    incumbent_path = (
        PROJECT_ROOT
        / design.document["model_selection"]["incumbent"]["dev_predictions"]["path"]
    )
    contract_sha256 = design.document["model_selection"]["candidate"]["contract"][
        "sha256"
    ]
    rows: list[dict[str, Any]] = []
    for line in incumbent_path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        row["contract_sha256"] = contract_sha256
        row["model"] = "qwen3.7-flash"
        if not pass_gates:
            row["prediction"]["materiality"] = 0
        rows.append(row)
    assert len(rows) == 60
    return rows


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _fake_dev_call(
    tmp_path: Path,
    design: EventEvaluationDesign,
    rows: list[dict[str, Any]],
    calls: list[str],
) -> Any:
    contract = design.document["model_selection"]["candidate"]["contract"]
    design_sha256 = design.sha256

    def call(
        active_contract_path: Path,
        round_id: str,
        **kwargs: object,
    ) -> Any:
        calls.append(round_id)
        assert active_contract_path == runner.ACTIVE_CONTRACT_PATH
        assert round_id == runner.OFFICIAL_ROUND_ID
        assert kwargs["project_root"] == tmp_path.resolve()
        assert kwargs["design"] is design
        stem = (
            tmp_path
            / "docs/phase4/eval/dev-iterations/P4.2a-dev60-v1.7-r1"
        )
        predictions_path = Path(f"{stem}.predictions.jsonl")
        manifest_path = Path(f"{stem}.manifest.json")
        report_path = Path(f"{stem}.report.json")
        _write_jsonl(predictions_path, rows)
        _write_json(
            manifest_path,
            {
                "completed_at_utc": "2026-08-04T14:01:00Z",
                "input_identity": {"fixture": True},
                "round_id": "v1.7-r1",
                "design_sha256": design_sha256,
                "active_contract_path": contract["path"],
                "active_contract_sha256": contract["sha256"],
                "model": "qwen3.7-flash",
                "predictions_sha256": _sha256(predictions_path),
                "heldout_accessed": False,
                "production_writes": 0,
            },
        )
        _write_json(
            report_path,
            {
                "input_identity": {"fixture": True},
                "runtime_evidence": {"successful_rows": 60},
                "round_id": "v1.7-r1",
                "manifest_sha256": _sha256(manifest_path),
                "predictions_sha256": _sha256(predictions_path),
                "heldout_accessed": False,
                "heldout_phase_unlocked": False,
                "prediction_contract": {
                    "path": contract["path"],
                    "sha256": contract["sha256"],
                    "model": "qwen3.7-flash",
                },
            },
        )
        return SimpleNamespace(
            predictions_path=predictions_path,
            manifest_path=manifest_path,
            report_path=report_path,
            report={},
            summary=None,
        )

    return call


def _install_offline_fakes(
    monkeypatch: pytest.MonkeyPatch,
    labels: dict[int, dict[str, Any]],
    preflight_calls: list[str],
) -> None:
    def preflight(design: EventEvaluationDesign, root: Path) -> None:
        assert design.document["schema_version"] == runner.EXPECTED_DESIGN_SCHEMA
        assert root.is_absolute()
        preflight_calls.append("checked")

    monkeypatch.setattr(
        heldout,
        "_ensure_v1_7_model_selection_preparation_is_safe",
        preflight,
    )
    monkeypatch.setattr(
        heldout,
        "_load_active_contract",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        heldout,
        "_dev_final_inputs",
        lambda *args, **kwargs: (
            tuple({"news_item_id": identifier} for identifier in sorted(labels)),
            [],
        ),
    )
    monkeypatch.setattr(
        dev_runner,
        "_load_dev_labels",
        lambda *args, **kwargs: (labels, Path("fixture-labels.jsonl")),
    )


def test_v1_7_passes_once_promotes_locally_and_freezes_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    design = _fixture_design(tmp_path)
    labels = _labels()
    preflight_calls: list[str] = []
    _install_offline_fakes(monkeypatch, labels, preflight_calls)
    model_calls: list[str] = []
    rows = _candidate_rows(design, pass_gates=True)
    freeze_calls: list[tuple[Path, Path]] = []

    def freeze(
        contract_path: Path,
        predictions_path: Path,
        manifest_path: Path,
        **kwargs: object,
    ) -> Path:
        freeze_calls.append((predictions_path, manifest_path))
        assert contract_path == runner.ACTIVE_CONTRACT_PATH
        assert kwargs["design"] is design
        assert predictions_path.read_bytes() == (
            tmp_path
            / "docs/phase4/eval/dev-iterations/"
            "P4.2a-dev60-v1.7-r1.predictions.jsonl"
        ).read_bytes()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["success_count"] == 60
        assert manifest["failure_count"] == 0
        receipt = (
            tmp_path
            / "docs/phase4/eval/"
            "P4.2a-heldout-prediction-contract-freeze-v1.6.json"
        )
        _write_json(receipt, {"fixture": "candidate-freeze"})
        return receipt

    outcome_path, outcome = runner.run_v1_7_selection(
        project_root=tmp_path,
        design_path=runner.EVALUATION_DESIGN_PATH,
        contract_path=runner.ACTIVE_CONTRACT_PATH,
        clock=lambda: datetime(2026, 8, 4, 14, 0, tzinfo=UTC),
        load_design_fn=lambda *args, **kwargs: design,
        run_dev_iteration_fn=_fake_dev_call(
            tmp_path,
            design,
            rows,
            model_calls,
        ),
        freeze_prediction_contract_fn=freeze,
    )

    assert model_calls == ["v1.7-r1"]
    assert len(freeze_calls) == 1
    assert len(preflight_calls) == 2
    assert outcome_path.is_file()
    assert outcome["decision"] == "select_candidate"
    assert outcome["selected_model"] == "qwen3.7-flash"
    assert outcome["gates"]["all_passed"] is True
    assert outcome["candidate"]["metrics"]["success_count"] == 60
    assert outcome["candidate"]["metrics"]["failure_count"] == 0
    assert outcome["candidate"]["freeze_receipt"]["created"] is True
    assert outcome["selected_freeze_receipt"]["sha256"] == _sha256(
        freeze_calls[0][0].parent
        / "P4.2a-heldout-prediction-contract-freeze-v1.6.json"
    )
    assert len(outcome["per_item"]) == 60
    assert {
        item["news_item_id"] for item in outcome["per_item"]
    } == set(labels)
    first = outcome["per_item"][0]
    assert set(first["candidate"]["input_identity"]) == {
        "declared_frozen_input_sha256",
        "active_model_input_sha256",
        "text_sha256",
    }
    assert outcome["cost_comparison"]["monthly_calls"] == 15_000
    assert (
        outcome["cost_comparison"]["candidate"]["round_cost"]
        < outcome["cost_comparison"]["incumbent"]["round_cost"]
    )
    persisted = json.loads(outcome_path.read_text(encoding="utf-8"))
    assert persisted == outcome
    assert persisted["heldout_accessed"] is False
    assert persisted["production_writes"] == 0
    assert persisted["third_model_run"] is False


def test_v1_7_failed_gate_retains_incumbent_without_candidate_freeze(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    design = _fixture_design(tmp_path)
    labels = _labels()
    preflight_calls: list[str] = []
    _install_offline_fakes(monkeypatch, labels, preflight_calls)
    model_calls: list[str] = []
    freeze_calls: list[str] = []

    outcome_path, outcome = runner.run_v1_7_selection(
        project_root=tmp_path,
        clock=lambda: datetime(2026, 8, 4, 14, 0, tzinfo=UTC),
        load_design_fn=lambda *args, **kwargs: design,
        run_dev_iteration_fn=_fake_dev_call(
            tmp_path,
            design,
            _candidate_rows(design, pass_gates=False),
            model_calls,
        ),
        freeze_prediction_contract_fn=lambda *args, **kwargs: cast(
            Path,
            freeze_calls.append("unexpected"),
        ),
    )

    assert model_calls == ["v1.7-r1"]
    assert freeze_calls == []
    assert len(preflight_calls) == 2
    assert outcome_path.is_file()
    assert outcome["decision"] == "retain_incumbent"
    assert outcome["selected_model"] == "qwen3.6-plus"
    assert outcome["gates"]["all_passed"] is False
    assert outcome["gates"]["materiality_positive"]["passed"] is False
    assert outcome["candidate"]["freeze_receipt"] == {
        "path": (
            "docs/phase4/eval/"
            "P4.2a-heldout-prediction-contract-freeze-v1.6.json"
        ),
        "sha256": None,
        "created": False,
        "validated": False,
    }
    assert outcome["selected_freeze_receipt"]["sha256"] == (
        runner.EXPECTED_INCUMBENT_RECEIPT_SHA256
    )
    assert not (
        tmp_path / "docs/phase4/eval/P4.2a-dev60-final-predictions-v1.6.jsonl"
    ).exists()
    assert not (
        tmp_path
        / "docs/phase4/eval/P4.2a-dev60-final-predictions-v1.6.manifest.json"
    ).exists()


def test_v1_7_deadline_blocks_before_any_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    design = _fixture_design(tmp_path)
    labels = _labels()
    preflight_calls: list[str] = []
    _install_offline_fakes(monkeypatch, labels, preflight_calls)
    model_calls: list[str] = []

    with pytest.raises(runner.ModelSelectionError, match="deadline has passed"):
        runner.run_v1_7_selection(
            project_root=tmp_path,
            clock=lambda: runner.EXPECTED_DEADLINE_UTC,
            load_design_fn=lambda *args, **kwargs: design,
            run_dev_iteration_fn=_fake_dev_call(
                tmp_path,
                design,
                _candidate_rows(design, pass_gates=True),
                model_calls,
            ),
        )

    assert model_calls == []
    assert preflight_calls == ["checked"]
    assert not (
        tmp_path / "docs/phase4/eval/P4.2a-model-selection-v1.7.json"
    ).exists()


def test_v1_7_absolute_gates_use_raw_values_not_relative_ranking() -> None:
    design = load_event_evaluation_design(
        PROJECT_ROOT / "config/p4_event_evaluation_v1_6.yaml"
    )
    selection = cast(dict[str, Any], design.document["model_selection"])
    exact_boundary: dict[str, Any] = {
        "success_count": 60,
        "failure_count": 0,
        "failed_reference_positive_count": 0,
        "materiality_positive": {
            "predicted_positive_count": 5,
            "agreement": 0.80,
        },
        "symbol_exact_set": {
            "matches": 57,
            "denominator": 60,
            "agreement": 0.95,
        },
    }

    evidence = runner._gate_evidence(
        exact_boundary,
        selection,
        within_deadline=True,
    )
    assert evidence["all_passed"] is True

    below_materiality = copy.deepcopy(exact_boundary)
    below_materiality["materiality_positive"]["agreement"] = 0.799999999999
    below_materiality["symbol_exact_set"]["agreement"] = 1.0
    assert (
        runner._gate_evidence(
            below_materiality,
            selection,
            within_deadline=True,
        )["all_passed"]
        is False
    )

    zero_positive = copy.deepcopy(exact_boundary)
    zero_positive["materiality_positive"]["predicted_positive_count"] = 0
    zero_positive["materiality_positive"]["agreement"] = None
    assert (
        runner._gate_evidence(
            zero_positive,
            selection,
            within_deadline=True,
        )["all_passed"]
        is False
    )


@pytest.mark.parametrize(
    "field",
    ["declared_input_sha256", "input_sha256", "text_sha256"],
)
def test_v1_7_rejects_any_per_id_input_hash_difference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    design = _fixture_design(tmp_path)
    labels = _labels()
    preflight_calls: list[str] = []
    _install_offline_fakes(monkeypatch, labels, preflight_calls)
    model_calls: list[str] = []
    rows = _candidate_rows(design, pass_gates=True)
    rows[0][field] = "0" * 64

    with pytest.raises(
        runner.ModelSelectionError,
        match=rf"{field} differs",
    ):
        runner.run_v1_7_selection(
            project_root=tmp_path,
            clock=lambda: datetime(2026, 8, 4, 14, 0, tzinfo=UTC),
            load_design_fn=lambda *args, **kwargs: design,
            run_dev_iteration_fn=_fake_dev_call(
                tmp_path,
                design,
                rows,
                model_calls,
            ),
        )

    assert model_calls == ["v1.7-r1"]
    assert not (
        tmp_path / "docs/phase4/eval/P4.2a-model-selection-v1.7.json"
    ).exists()
    assert not (
        tmp_path / "docs/phase4/eval/P4.2a-dev60-final-predictions-v1.6.jsonl"
    ).exists()


def test_v1_7_freeze_failure_writes_fail_closed_incumbent_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    design = _fixture_design(tmp_path)
    labels = _labels()
    preflight_calls: list[str] = []
    _install_offline_fakes(monkeypatch, labels, preflight_calls)
    model_calls: list[str] = []
    freeze_calls: list[str] = []

    def fail_freeze(*args: object, **kwargs: object) -> Path:
        freeze_calls.append("attempted")
        raise runner.ModelSelectionError("fixture freeze validation failed")

    outcome_path, outcome = runner.run_v1_7_selection(
        project_root=tmp_path,
        clock=lambda: datetime(2026, 8, 4, 14, 0, tzinfo=UTC),
        load_design_fn=lambda *args, **kwargs: design,
        run_dev_iteration_fn=_fake_dev_call(
            tmp_path,
            design,
            _candidate_rows(design, pass_gates=True),
            model_calls,
        ),
        freeze_prediction_contract_fn=fail_freeze,
    )

    assert model_calls == ["v1.7-r1"]
    assert freeze_calls == ["attempted"]
    assert outcome_path.is_file()
    assert outcome["gates"]["all_passed"] is True
    assert outcome["decision"] == "retain_incumbent"
    assert outcome["selected_model"] == "qwen3.6-plus"
    assert outcome["operational_completion"] == {
        "status": "blocked_candidate_freeze_failed",
        "error_code": "fixture freeze validation failed",
        "model_calls_retried": 0,
        "selected_incumbent_fail_closed": True,
    }
    assert outcome["candidate"]["freeze_receipt"]["created"] is False
    assert outcome["candidate"]["freeze_receipt"]["validated"] is False
