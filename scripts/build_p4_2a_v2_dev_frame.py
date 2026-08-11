from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.p4_2a_v2_dev_common import (  # noqa: E402
    DevelopmentFrameError,
    DevelopmentFrameResult,
    DevelopmentFrameSpec,
    FrozenFile,
    build_development_frame,
)

from alphapilot.llm.p4_news_eval import (  # noqa: E402
    EventEvaluationDesignError,
    load_event_evaluation_design,
)

DESIGN_RELATIVE_PATH = Path("config/p4_event_evaluation_v2.yaml")
EXPECTED_DESIGN_SHA256 = "18a2428a4ec04bfea6e4f4d70692f38ea82fbaee5a223f30f2465b895b238e21"
SOURCE_V1_7_DESIGN_SHA256 = "4c7964ad547820f5672631939af93978f11cb9f91e5921087770ac7d0d79bec1"
SOURCE_V1_7_CONTRACT_SHA256 = "68474e4bd4fd5c9c88711dd5e102898ad1ed75a0fb984045efbd14e51a6db701"
SOURCE_V1_7_MODEL = "qwen3.7-flash"


def load_development_frame_spec(
    *,
    project_root: Path = PROJECT_ROOT,
    design_path: Path | None = None,
) -> DevelopmentFrameSpec:
    root = project_root.resolve()
    path = design_path or (root / DESIGN_RELATIVE_PATH)
    expected_path = (root / DESIGN_RELATIVE_PATH).resolve()
    if path.resolve() != expected_path or path.is_symlink():
        raise DevelopmentFrameError(
            "design path must be the registered config/p4_event_evaluation_v2.yaml"
        )
    try:
        design = load_event_evaluation_design(path, project_root=root)
    except EventEvaluationDesignError as exc:
        raise DevelopmentFrameError("P4.2a v2 evaluation design is not trusted") from exc
    if design.sha256 != EXPECTED_DESIGN_SHA256:
        raise DevelopmentFrameError("P4.2a v2 evaluation design SHA-256 drifted")

    document = design.document
    source = _mapping(document.get("source_candidate_pool"), "source candidate pool")
    inputs = _mapping(source.get("candidate_inputs"), "candidate inputs")
    input_manifest = _mapping(source.get("candidate_input_manifest"), "candidate input manifest")
    predictions = _mapping(source.get("baseline_predictions"), "baseline predictions")
    partition = _mapping(source.get("audited_partition_before_retirement"), "audited partition")
    frames = _mapping(document.get("frames"), "frames")
    frame = _mapping(frames.get("development_frame_v2"), "development frame")
    strata = _mapping(frame.get("strata"), "development strata")
    positive = _mapping(strata.get("predicted_positive"), "positive stratum")
    negative = _mapping(strata.get("predicted_negative"), "negative stratum")
    failed = _mapping(strata.get("extract_failed"), "failed stratum")
    frozen = _mapping(document.get("frozen_history"), "frozen history")
    retired = _mapping(frozen.get("retired_heldout40_selection"), "retired selection")
    artifacts = _mapping(document.get("artifacts"), "artifacts")
    private_output = _mapping(
        artifacts.get("development_private_selection_manifest"),
        "private selection output",
    )
    blind_output = _mapping(artifacts.get("development_owner_blind_jsonl"), "owner blind output")
    if (
        document.get("schema_version") != "p4.2a-evaluation-design-v2"
        or frame.get("sampling_algorithm") != "sha256_rank_without_replacement_per_stratum_v1"
        or frame.get("retired_id_policy") != "exclude_before_ranking_and_assert_zero_intersection"
        or frame.get("total_selected_count") != 45
        or private_output.get("create_only") is not True
        or blind_output.get("create_only") is not True
        or failed.get("selected_count") != 0
    ):
        raise DevelopmentFrameError("development frame registration drifted")

    return DevelopmentFrameSpec(
        design_path="config/p4_event_evaluation_v2.yaml",
        design_sha256=design.sha256,
        frame_id=_string(frame.get("frame_id"), "frame ID"),
        sampling_seed=_string(frame.get("sampling_seed"), "sampling seed"),
        source_design_sha256=SOURCE_V1_7_DESIGN_SHA256,
        source_contract_sha256=SOURCE_V1_7_CONTRACT_SHA256,
        source_model=SOURCE_V1_7_MODEL,
        candidate_inputs=_frozen_file(inputs, "candidate inputs"),
        candidate_input_manifest=_frozen_file(input_manifest, "candidate manifest"),
        baseline_predictions=_frozen_file(predictions, "baseline predictions"),
        baseline_prediction_manifest=FrozenFile(
            path=_string(predictions.get("manifest_path"), "prediction manifest path"),
            sha256=_string(predictions.get("manifest_sha256"), "prediction manifest SHA-256"),
        ),
        retired_selection=_frozen_file(retired, "retired selection"),
        retired_count=_integer(retired.get("selected_count"), "retired count"),
        retired_sorted_ids_sha256=_string(
            retired.get("sorted_ids_compact_json_sha256"), "retired ID SHA-256"
        ),
        input_count=_integer(inputs.get("row_count"), "input count"),
        positive_count=_integer(
            _mapping(partition.get("predicted_positive"), "positive partition").get("count"),
            "positive count",
        ),
        negative_count=_integer(
            _mapping(partition.get("predicted_negative"), "negative partition").get("count"),
            "negative count",
        ),
        failed_count=_integer(
            _mapping(partition.get("extract_failed"), "failed partition").get("count"),
            "failed count",
        ),
        positive_available_after_retirement=_integer(
            positive.get("available_after_retirement"), "positive available"
        ),
        negative_available_after_retirement=_integer(
            negative.get("available_after_retirement"), "negative available"
        ),
        positive_selected=_integer(positive.get("selected_count"), "positive selected"),
        negative_selected=_integer(negative.get("selected_count"), "negative selected"),
        selection_manifest_path=_string(private_output.get("path"), "private output path"),
        owner_blind_path=_string(blind_output.get("path"), "blind output path"),
        artifact_root=_string(document.get("artifact_root"), "artifact root"),
    )


def run(*, project_root: Path = PROJECT_ROOT) -> DevelopmentFrameResult:
    spec = load_development_frame_spec(project_root=project_root)
    return build_development_frame(project_root, spec, publish=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the pre-registered P4.2a v2 development frame exactly once."
    )
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    try:
        result = run(project_root=args.project_root)
    except (DevelopmentFrameError, FileExistsError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "selection_manifest_path": str(result.selection_manifest_path),
                "selection_manifest_sha256": result.selection_manifest_sha256,
                "owner_blind_path": str(result.owner_blind_path),
                "owner_blind_sha256": result.owner_blind_sha256,
                "selected_count": result.selected_count,
                "selected_counts": {
                    "predicted_positive": result.positive_selected,
                    "predicted_negative": result.negative_selected,
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DevelopmentFrameError(f"{label} must be a mapping")
    return cast(Mapping[str, Any], value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise DevelopmentFrameError(f"{label} must be a non-empty string")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DevelopmentFrameError(f"{label} must be a non-negative integer")
    return value


def _frozen_file(value: Mapping[str, Any], label: str) -> FrozenFile:
    return FrozenFile(
        path=_string(value.get("path"), f"{label} path"),
        sha256=_string(value.get("sha256"), f"{label} SHA-256"),
    )


if __name__ == "__main__":
    raise SystemExit(main())
