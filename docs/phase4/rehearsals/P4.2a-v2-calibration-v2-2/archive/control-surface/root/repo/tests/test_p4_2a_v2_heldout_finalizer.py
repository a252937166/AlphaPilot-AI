from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn, TypeVar

import pytest
from scripts import build_p4_2a_gold_sample as gold_builder
from scripts import build_p4_2a_v2_heldout_adjudication_ui as heldout_ui
from scripts import finalize_p4_2a_v2_heldout_adjudication as finalizer
from scripts import prepare_p4_2a_v2_heldout as prepare
from scripts import rehearse_p4_2a_v2_heldout_full_path as v2_rehearsal
from scripts import run_p4_2a_v2_dev_calibration as dev_runner
from scripts import seal_p4_2a_v2_ai_draft as base_seal
from scripts import seal_p4_2a_v2_heldout_draft as heldout

from alphapilot.core.config import Settings

_EXTRA_CONTROL_PATHS = (
    Path(
        "docs/phase4/reports/"
        "P4.2a-v2-calibration-design-clarifications-20260809.json"
    ),
    Path(
        "docs/phase4/reports/"
        "P4.2a-v2-heldout-rehearsal-v2-1-scope-correction-owner-ruling-20260810.json"
    ),
    Path(
        "docs/phase4/reports/"
        "P4.2a-v2-1-control-plane-registry-expansion-authorization-20260811.json"
    ),
)

_TestCallable = TypeVar("_TestCallable", bound=Callable[..., object])
_parametrize: Callable[..., Callable[[_TestCallable], _TestCallable]] = (
    pytest.mark.parametrize
)


def _copy_control(root: Path, relative: Path) -> None:
    source = prepare.PROJECT_ROOT / relative
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _temporary_binding(tmp_path: Path) -> prepare.HeldoutBinding:
    root = tmp_path.resolve()
    source = prepare.load_binding()
    v2_rehearsal._copy_control_surface(prepare.PROJECT_ROOT, root)
    binding = replace(
        source,
        root=root,
        artifacts={
            name: root / path.relative_to(source.root)
            for name, path in source.artifacts.items()
        },
    )
    for relative in (
        prepare.PREREGISTRATION_PATH,
        prepare.SUCCESSOR_V2_1_PREREGISTRATION_PATH,
        prepare.SUCCESSOR_V2_1_BUNDLE_SCHEMA_PATH,
        prepare.SUCCESSOR_V2_1_RELEASE_SCHEMA_PATH,
        prepare.FRAME_AUTHORITY_PATH,
        prepare.SUCCESSOR_CODE_GATE_AUTHORITY_PATH,
        *_EXTRA_CONTROL_PATHS,
    ):
        _copy_control(root, relative)
    for relative in prepare._registered_successor_implementation_paths(
        prepare.PROJECT_ROOT
    ):
        _copy_control(root, relative)
    prepare._ensure_synthetic_control_surface(binding)
    database = root / "data/alphapilot.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    database.touch()
    loaded = prepare.load_binding(root)
    assert loaded.root == binding.root
    assert loaded.artifacts == binding.artifacts
    return loaded


def _safe_settings() -> Settings:
    return Settings(
        trading_mode="research",
        live_trading_enabled=False,
        paper_trading_enabled=False,
        paper_auto_trading_enabled=False,
        futu_enable_account_mutation=False,
        futu_enable_trade=False,
        llm_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        llm_api_key="test-only-key",
        llm_model="qwen3.6-plus",
    )


def _safe_snapshot() -> dev_runner.ProductionSnapshot:
    return dev_runner.ProductionSnapshot(
        sqlite_uri_mode="ro",
        pragma_query_only=1,
        connection_total_changes=0,
        llm_call_count=0,
        llm_call_max_id=None,
        trade_proposal_count=0,
        broker_order_count=0,
        non_simulate_order_count=0,
        news_events_table_exists=False,
        universe_symbols=frozenset({"600519"}),
    )


def _offline_context(
    binding: prepare.HeldoutBinding,
) -> prepare._OfflineRehearsalCapability:
    def forbidden_fetch(
        _url: str,
        _policy: gold_builder.AnnouncementBodyPolicy,
    ) -> NoReturn:
        raise AssertionError("finalizer test attempted a network fetch")

    def forbidden_extract(
        _payload: bytes,
        _policy: gold_builder.AnnouncementBodyPolicy,
    ) -> NoReturn:
        raise AssertionError("finalizer test attempted PDF extraction")

    def monotonic() -> float:
        return 0.0

    def sleep(_seconds: float) -> None:
        return None

    def forbidden_model(
        _purpose: str,
        _system: str,
        _user: str,
        _schema: dict[str, Any],
        **_kwargs: Any,
    ) -> NoReturn:
        raise AssertionError("finalizer test attempted a model call")

    def snapshot_loader(_root: Path) -> dev_runner.ProductionSnapshot:
        return _safe_snapshot()

    def wall_clock() -> datetime:
        return datetime(2026, 8, 10, 12, 30, tzinfo=UTC)

    def execution_id_factory() -> str:
        return "00000000-0000-4000-8000-000000000001"

    def recorded_at_clock() -> str:
        return "2026-08-10T12:30:00Z"

    def monotonic_ns_clock() -> int:
        return 0

    implementation_commit = prepare._git(
        prepare.PROJECT_ROOT,
        "rev-parse",
        "HEAD",
    ).strip()
    return prepare._mint_v2_1_offline_rehearsal_capability(
        binding,
        database=binding.root / "data/alphapilot.db",
        pdf_fetcher=forbidden_fetch,
        pdf_text_extractor=forbidden_extract,
        monotonic=monotonic,
        sleep=sleep,
        inference_settings=_safe_settings(),
        chat_json_fn=forbidden_model,
        snapshot_loader=snapshot_loader,
        wall_clock=wall_clock,
        execution_id_factory=execution_id_factory,
        prediction_recorded_at_clock=recorded_at_clock,
        prediction_monotonic_ns_clock=monotonic_ns_clock,
        implementation_commit=implementation_commit,
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
    *,
    execution_context: prepare._OfflineRehearsalCapability,
) -> Path:
    prepare._write_synthetic_production_execution_fixture(
        binding,
        execution_context=execution_context,
        started_at_utc="2026-08-10T05:00:00Z",
        recorded_at_utc="2026-08-10T05:00:30Z",
        completed_at_utc="2026-08-10T05:01:00Z",
    )
    prepare.run_select_blind(binding, execution_context=execution_context)
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
    export: Path = binding.root / "owner-export-download.jsonl"
    export.write_bytes(base_seal.canonical_jsonl_bytes(owner_rows))
    return export


def test_heldout_finalizer_freezes_raw_human_and_completion_without_scoring(
    tmp_path: Path,
) -> None:
    binding = _temporary_binding(tmp_path)
    contract = _temporary_contract(binding)
    execution_context = _offline_context(binding)
    export = _prepare_owner_bundle(
        binding,
        contract,
        execution_context=execution_context,
    )

    summary, completion, hashes = finalizer.finalize_owner_export(
        contract=contract,
        owner_export_path=export,
        completed_at="2026-08-10T05:06:00Z",
        execution_context=execution_context,
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
            execution_context=execution_context,
        )
    assert {path: path.read_bytes() for path in before} == before


def test_heldout_finalizer_recomputes_owner_delta_before_any_output(
    tmp_path: Path,
) -> None:
    binding = _temporary_binding(tmp_path)
    contract = _temporary_contract(binding)
    execution_context = _offline_context(binding)
    export = _prepare_owner_bundle(
        binding,
        contract,
        execution_context=execution_context,
    )
    rows = [json.loads(line) for line in export.read_text(encoding="utf-8").splitlines()]
    rows[0]["adjudication"]["changed"] = True
    export.write_bytes(base_seal.canonical_jsonl_bytes(rows))

    with pytest.raises(base_seal.V2AdjudicationError, match="claimed delta"):
        finalizer.finalize_owner_export(
            contract=contract,
            owner_export_path=export,
            completed_at="2026-08-10T05:06:00Z",
            execution_context=execution_context,
        )
    for key in ("owner_export", "human_adjudicated", "owner_completion"):
        assert binding.artifacts[key].exists() is False


def test_heldout_finalizer_rederives_ranks_before_any_owner_output(
    tmp_path: Path,
) -> None:
    binding = _temporary_binding(tmp_path)
    contract = _temporary_contract(binding)
    execution_context = _offline_context(binding)
    export = _prepare_owner_bundle(
        binding,
        contract,
        execution_context=execution_context,
    )
    selection_path = binding.artifacts["private_selection"]
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selection["selection"]["selected"][0]["owner_order_sha256"] = "e" * 64
    selection_path.write_bytes(base_seal.canonical_json_bytes(selection))

    with pytest.raises(base_seal.V2AdjudicationError, match="producer re-derivation"):
        finalizer.finalize_owner_export(
            contract=contract,
            owner_export_path=export,
            completed_at="2026-08-10T05:06:00Z",
            execution_context=execution_context,
        )
    for key in ("owner_export", "human_adjudicated", "owner_completion"):
        assert binding.artifacts[key].exists() is False


@_parametrize("adjudicator_id", [" Ouyang", "OUYANG", "ouyang "])
def test_heldout_finalizer_requires_exact_registered_owner_identity_before_output(
    tmp_path: Path,
    adjudicator_id: str,
) -> None:
    binding = _temporary_binding(tmp_path)
    contract = _temporary_contract(binding)
    execution_context = _offline_context(binding)
    export = _prepare_owner_bundle(
        binding,
        contract,
        execution_context=execution_context,
    )
    rows = [json.loads(line) for line in export.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        row["adjudication"]["adjudicator_id"] = adjudicator_id
    export.write_bytes(base_seal.canonical_jsonl_bytes(rows))

    with pytest.raises(base_seal.V2AdjudicationError, match="actor identity"):
        finalizer.finalize_owner_export(
            contract=contract,
            owner_export_path=export,
            completed_at="2026-08-10T05:06:00Z",
            execution_context=execution_context,
        )
    for key in ("owner_export", "human_adjudicated", "owner_completion"):
        assert binding.artifacts[key].exists() is False


@_parametrize(
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
) -> None:
    binding = _temporary_binding(tmp_path)
    contract = _temporary_contract(binding)
    execution_context = _offline_context(binding)
    export = _prepare_owner_bundle(
        binding,
        contract,
        execution_context=execution_context,
    )
    rows = [json.loads(line) for line in export.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        row["adjudication"]["adjudicated_at"] = adjudicated_at
    export.write_bytes(base_seal.canonical_jsonl_bytes(rows))

    with pytest.raises(base_seal.V2AdjudicationError, match=message):
        finalizer.finalize_owner_export(
            contract=contract,
            owner_export_path=export,
            completed_at=completed_at,
            execution_context=execution_context,
        )
    for key in ("owner_export", "human_adjudicated", "owner_completion"):
        assert binding.artifacts[key].exists() is False


def test_canonical_finalizer_remains_fixed_rejection_before_owner_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    binding = _temporary_binding(tmp_path)
    contract = _temporary_contract(binding)
    nonexistent_export = binding.root / "must-not-be-read.jsonl"

    with pytest.raises(
        base_seal.V2AdjudicationError,
        match=finalizer.FINALIZE_AUTHORITY_ERROR,
    ):
        finalizer.finalize_owner_export(
            contract=contract,
            owner_export_path=nonexistent_export,
            completed_at="2026-08-10T05:06:00Z",
        )
    assert nonexistent_export.exists() is False
    for key in ("owner_export", "human_adjudicated", "owner_completion"):
        assert binding.artifacts[key].exists() is False

    assert (
        finalizer.main(
            [
                "--owner-export",
                str(nonexistent_export),
                "--completed-at",
                "2026-08-10T05:06:00Z",
            ]
        )
        == 2
    )
    assert finalizer.FINALIZE_AUTHORITY_ERROR in capsys.readouterr().err
