#!/usr/bin/env python3
"""Build the create-only offline owner UI for P4.2a v2 heldout60."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import build_p4_2a_v2_adjudication_ui as base_ui  # noqa: E402
from scripts import seal_p4_2a_v2_ai_draft as base_seal  # noqa: E402
from scripts import seal_p4_2a_v2_heldout_draft as heldout  # noqa: E402

from alphapilot.llm.p4_news_eval import (  # noqa: E402
    EVALUATION_DESIGN_V2_PATH,
    EventEvaluationDesignError,
)

_DEV_TITLE = "P4.2a v2 开发框人工裁定"
_HELDOUT_TITLE = "P4.2a v2 Held-out 60 人工裁定"
EXPECTED_ADJUDICATOR_ID = "ouyang"


def render_registered_ui_payload(
    blind_rows: Sequence[Mapping[str, Any]],
    draft_rows: Sequence[Mapping[str, Any]],
    *,
    contract: base_seal.V2AdjudicationContract,
    blind_payload: bytes,
    draft_payload: bytes,
    selection_payload: bytes | None = None,
) -> tuple[bytes, int]:
    """Render heldout UI bytes while reusing the already-audited dev UI logic."""

    items, drafter_id = base_ui.build_ui_items(
        blind_rows, draft_rows, contract=contract
    )
    if drafter_id != heldout.EXPECTED_DRAFTER_ID:
        raise base_seal.V2AdjudicationError("heldout UI drafter identity drifted")
    rendered = base_ui.render_ui(
        items,
        contract=contract,
        drafter_id=drafter_id,
        blind_sha256=base_seal.sha256_bytes(blind_payload),
        draft_sha256=base_seal.sha256_bytes(draft_payload),
        download_name=contract.artifacts["development_owner_raw_export_jsonl"].name,
    )
    if selection_payload is None:
        selection_path = contract.artifacts["development_private_selection_manifest"]
        if selection_path.is_symlink() or not selection_path.is_file():
            raise base_seal.V2AdjudicationError(
                "registered heldout selection is unavailable for UI binding"
            )
        selection_payload = selection_path.read_bytes()
    selection_sha256 = base_seal.sha256_bytes(selection_payload)
    base_storage_key = (
        f"p4.2a-v2-adjudication:{contract.design_ref['sha256']}:"
        f"{base_seal.sha256_bytes(blind_payload)}:"
        f"{base_seal.sha256_bytes(draft_payload)}"
    )
    if rendered.count(base_storage_key) != 1:
        raise base_seal.V2AdjudicationError("base UI storage binding marker drifted")
    rendered = rendered.replace(
        base_storage_key, f"{base_storage_key}:{selection_sha256}"
    )
    if rendered.count(_DEV_TITLE) != 2:
        raise base_seal.V2AdjudicationError("base UI title marker drifted")
    adjudicator_input = (
        '<input id="adjudicator" autocomplete="off" '
        'placeholder="人工裁定人（必填）">'
    )
    locked_adjudicator_input = (
        '<input id="adjudicator" autocomplete="off" value="ouyang" readonly '
        'aria-readonly="true" title="本轮注册人工裁定者：ouyang">'
    )
    if rendered.count(adjudicator_input) != 1:
        raise base_seal.V2AdjudicationError("base UI adjudicator input marker drifted")
    rendered = rendered.replace(adjudicator_input, locked_adjudicator_input)
    identity_marker = "const STORAGE_KEY = "
    if rendered.count(identity_marker) != 1:
        raise base_seal.V2AdjudicationError("base UI identity marker drifted")
    rendered = rendered.replace(
        identity_marker,
        f'const EXPECTED_ADJUDICATOR_ID = "{EXPECTED_ADJUDICATOR_ID}";\n'
        + identity_marker,
    )
    generic_identity_gate = (
        'if(!adjudicator){alert("必须填写人工裁定人身份");return;}\n'
        '  if(adjudicator.toLocaleLowerCase()===DRAFTER_ID.toLocaleLowerCase())'
        '{alert("人工裁定人与 AI 起草者必须不同");return;}'
    )
    exact_identity_gate = (
        'if(adjudicator!==EXPECTED_ADJUDICATOR_ID)'
        '{alert("本轮人工裁定人必须为 ouyang");return;}'
    )
    if rendered.count(generic_identity_gate) != 2:
        raise base_seal.V2AdjudicationError("base UI identity gate marker drifted")
    rendered = rendered.replace(generic_identity_gate, exact_identity_gate)
    return rendered.replace(_DEV_TITLE, _HELDOUT_TITLE).encode(), len(items)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-manifest", type=Path, default=None)
    parser.add_argument("--blind", type=Path, default=None)
    parser.add_argument("--draft", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--evaluation-design", type=Path, default=EVALUATION_DESIGN_V2_PATH)
    arguments = parser.parse_args(argv)
    try:
        _binding, stage_authority = heldout.prevalidate_stage_authority(
            PROJECT_ROOT,
            stage="build-adjudication-ui",
        )
        contract = heldout.load_registered_contract(arguments.evaluation_design)
        selection_path = base_seal._bound_input(
            arguments.selection_manifest
            or contract.artifacts["development_private_selection_manifest"],
            contract.artifacts["development_private_selection_manifest"],
            label="selection manifest",
        )
        blind_path = base_seal._bound_input(
            arguments.blind or contract.artifacts["development_owner_blind_jsonl"],
            contract.artifacts["development_owner_blind_jsonl"],
            label="blind",
        )
        draft_path = base_seal._bound_input(
            arguments.draft or contract.artifacts["development_ai_draft_jsonl"],
            contract.artifacts["development_ai_draft_jsonl"],
            label="draft",
        )
        output_path = base_seal._bound_input(
            arguments.output or contract.artifacts["development_adjudication_html"],
            contract.artifacts["development_adjudication_html"],
            label="output",
        )
        (
            blind_rows,
            blind_payload,
            selection_payload,
            inference_completed_at,
        ) = heldout.read_bound_blind_bundle(
            selection_path,
            blind_path,
            contract=contract,
            stage="build-adjudication-ui",
            prevalidated_authority=stage_authority,
            validated_stage="build-adjudication-ui",
        )
        draft_rows, draft_payload = base_seal.read_jsonl(
            draft_path, label="heldout sealed draft"
        )
        heldout.validate_draft_timeline(
            draft_rows,
            inference_completed_at=inference_completed_at,
        )
        rendered, row_count = render_registered_ui_payload(
            blind_rows,
            draft_rows,
            contract=contract,
            blind_payload=blind_payload,
            draft_payload=draft_payload,
            selection_payload=selection_payload,
        )
        digest = base_seal.write_create_only(output_path, rendered)
    except (
        EventEvaluationDesignError,
        base_seal.V2AdjudicationError,
        FileExistsError,
        OSError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "rendered",
                "frame_id": heldout.FRAME_ID,
                "output": str(output_path.relative_to(contract.project_root)),
                "sha256": digest,
                "row_count": row_count,
                "offline": True,
                "owner_export_created": False,
                "human_gold_created": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
