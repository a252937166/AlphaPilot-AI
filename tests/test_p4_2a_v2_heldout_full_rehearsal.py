from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from scripts import prepare_p4_2a_v2_heldout as prepare
from scripts import rehearse_p4_2a_v2_heldout_full_path as rehearsal

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _jsonl(path: Path) -> list[dict[str, object]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert all(isinstance(row, dict) for row in rows)
    return rows


def _fingerprint(directory: Path) -> dict[str, str]:
    if not directory.exists():
        return {}
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def test_full_path_rehearsal_is_temp_isolated_create_only_and_code_bound(
    tmp_path: Path,
) -> None:
    publish_directory = tmp_path / "published"
    workspace_parent = tmp_path / "ephemeral-workspaces"
    production_root = PROJECT_ROOT / "docs/phase4/eval/v2-calibration/heldout"
    production_before = _fingerprint(production_root)

    receipt_path = rehearsal.run_rehearsal(
        project_root=PROJECT_ROOT,
        publish_directory=publish_directory,
        workspace_parent=workspace_parent,
    )

    assert receipt_path == publish_directory / "pass-receipt.json"
    assert sorted(path.name for path in publish_directory.iterdir()) == [
        "contract.json",
        "expected.json",
        "inputs.jsonl",
        "pass-receipt.json",
    ]
    assert not any(workspace_parent.iterdir())
    assert _fingerprint(production_root) == production_before

    contract = _json(publish_directory / "contract.json")
    inputs = _jsonl(publish_directory / "inputs.jsonl")
    expected = _json(publish_directory / "expected.json")
    receipt = _json(receipt_path)

    assert contract["preregistration_sha256"] == rehearsal.PREREGISTRATION_SHA256
    assert contract["workspace_policy"] == "temporary_and_outside_registered_artifact_roots"
    assert contract["network_allowed"] is False
    assert contract["production_database_allowed"] is False
    assert len(inputs) == 80
    assert [row["news_item_id"] for row in inputs] == list(
        range(rehearsal.SYNTHETIC_ID_START, rehearsal.SYNTHETIC_ID_START + 80)
    )
    assert all(row["schema_version"] == "p4.2a-heldout-candidate-input-v1.1" for row in inputs)
    assert expected["materialized_candidate_count"] == 80
    assert expected["mock_model_call_count"] == 80
    assert expected["selection_counts"] == {
        "predicted_positive": 40,
        "predicted_negative": 20,
        "total": 60,
    }
    assert expected["formal_state_events"] == [
        "evaluation_started",
        "evaluation_completed",
    ]

    assert receipt["status"] == "passed"
    assert receipt["full_path_covered"] is True
    assert receipt["materialization_gate_unlock"] is True
    assert receipt["preregistration_sha256"] == rehearsal.PREREGISTRATION_SHA256
    assert receipt["production_writes"] is False
    assert receipt["production_heldout_artifacts_changed"] is False
    assert receipt["real_database_reads"] == 0
    assert receipt["real_network_calls"] == 0
    assert receipt["real_model_calls"] == 0
    assert receipt["mock_model_calls"] == 80
    assert receipt["real_heldout_metrics_computed"] is False
    assert receipt["real_metrics_disclosed"] is False
    assert receipt["temporary_workspace_removed"] is True
    assert receipt["selection_counts"] == {
        "predicted_positive": 40,
        "predicted_negative": 20,
        "total": 60,
    }
    assert receipt["owner_chain_count"] == 60
    assert receipt["formal_state_events"] == [
        "evaluation_started",
        "evaluation_completed",
    ]
    assert receipt["synthetic_report_status"] == "synthetic_rehearsal"
    with pytest.raises(
        prepare.HeldoutPreparationError,
        match="rehearsal v1 is permanently retired",
    ):
        rehearsal.validate_rehearsal_gate(
            publish_directory,
            project_root=PROJECT_ROOT,
        )
    published_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(publish_directory.iterdir())
    )
    assert "materiality_precision" not in published_text
    assert "materiality_false_omission_rate" not in published_text
    assert "symbol_exact_set_accuracy" not in published_text

    artifact_hashes = receipt["published_artifact_sha256"]
    assert isinstance(artifact_hashes, dict)
    assert artifact_hashes == {
        name: hashlib.sha256((publish_directory / name).read_bytes()).hexdigest()
        for name in ("contract.json", "inputs.jsonl", "expected.json")
    }
    tested_code = receipt["tested_code_sha256"]
    assert isinstance(tested_code, dict)
    assert set(tested_code) == set(rehearsal.TESTED_CODE_PATHS)
    assert tested_code == {
        relative: hashlib.sha256((PROJECT_ROOT / relative).read_bytes()).hexdigest()
        for relative in rehearsal.TESTED_CODE_PATHS
    }

    before_second_attempt = _fingerprint(publish_directory)
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        rehearsal.run_rehearsal(
            project_root=PROJECT_ROOT,
            publish_directory=publish_directory,
            workspace_parent=workspace_parent,
        )
    assert _fingerprint(publish_directory) == before_second_attempt
    assert not any(workspace_parent.iterdir())


def test_full_path_rehearsal_failure_publishes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish_directory = tmp_path / "must-stay-absent"
    workspace_parent = tmp_path / "ephemeral-workspaces"

    def fail_before_selection(*_args: object, **_kwargs: object) -> tuple[Path, Path]:
        raise rehearsal.RehearsalError("synthetic injected failure")

    monkeypatch.setattr(rehearsal, "run_select_blind", fail_before_selection)

    with pytest.raises(rehearsal.RehearsalError, match="synthetic injected failure"):
        rehearsal.run_rehearsal(
            project_root=PROJECT_ROOT,
            publish_directory=publish_directory,
            workspace_parent=workspace_parent,
        )
    assert not publish_directory.exists()
    assert not any(workspace_parent.iterdir())


def test_registered_rehearsal_directory_and_cli_have_no_output_override() -> None:
    assert rehearsal.registered_rehearsal_directory(PROJECT_ROOT) == (
        PROJECT_ROOT / "docs/phase4/rehearsals/P4.2a-v2-calibration"
    )
    parser = rehearsal._parser()
    assert "--output" not in parser.format_help()
    assert "--workspace" not in parser.format_help()
