from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import cast

from scripts import run_p4_2a_heldout_predictions as runner

from alphapilot.llm.p4_news_eval import load_event_evaluation_design

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_CONTRACT_PATH = PROJECT_ROOT / "config/p4_event_extract_eval_v1.yaml"
BASE_PROMPT_PATH = PROJECT_ROOT / "config/prompts/p4_news_event_extract_v1.txt"
ACTIVE_CONTRACT_PATH = PROJECT_ROOT / "config/p4_event_extract_eval_v1_1.yaml"
ACTIVE_PROMPT_PATH = PROJECT_ROOT / "config/prompts/p4_news_event_extract_v1_1.txt"
ACTIVE_V1_2_CONTRACT_PATH = PROJECT_ROOT / "config/p4_event_extract_eval_v1_2.yaml"
ACTIVE_V1_2_PROMPT_PATH = PROJECT_ROOT / "config/prompts/p4_news_event_extract_v1_2.txt"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v1_1_active_contract_is_prompt_only_revision() -> None:
    design = load_event_evaluation_design()
    active = runner._load_active_contract(
        design,
        PROJECT_ROOT,
        ACTIVE_CONTRACT_PATH,
    )

    assert active.document["schema_version"] == "p4.2a-event-extract-eval-v1.1"
    assert (
        active.document["owner_spec_commit"]
        == "1e30c5a4812417a177617df03280a9f16290a00a"
    )
    assert active.document["pre_registered_at"] == "2026-08-04T06:44:40Z"
    assert active.path == ACTIVE_CONTRACT_PATH.resolve()
    assert active.prompt == ACTIVE_PROMPT_PATH.read_text(encoding="utf-8")

    normalized_active = copy.deepcopy(active.document)
    normalized_base = copy.deepcopy(design.base_contract.document)
    for key in ("schema_version", "owner_spec_commit", "pre_registered_at"):
        normalized_active[key] = normalized_base[key]
    active_files = cast(dict[str, object], normalized_active["contract_files"])
    base_files = cast(dict[str, object], normalized_base["contract_files"])
    active_files["prompt"] = copy.deepcopy(base_files["prompt"])

    assert normalized_active == normalized_base


def test_v1_1_prompt_encodes_stock_impact_and_pit_safe_calibration() -> None:
    prompt = ACTIVE_PROMPT_PATH.read_text(encoding="utf-8")

    assert "[P4_NEWS_EVENT_EXTRACT v1.1.0]" in prompt
    assert "对 symbols 所指明确标的股价的潜在影响" in prompt
    assert "若 symbols=[]" in prompt
    assert "宏观、政策、行业、市场或社会新闻的" in prompt
    assert "materiality 上限为 1" in prompt
    assert "半年报、年报等定期报告披露的" in prompt
    assert "实际业绩重大变化" in prompt
    assert "不得因为公告文体平实、标题程序化" in prompt
    assert "正文披露的底层实质事件及新增事实定级" in prompt
    assert "对“进展公告”只评价本次新增量" in prompt
    assert "direction 与 materiality 必须独立判断" in prompt
    assert "禁止凭行业、公司简称或常识猜代码" in prompt
    assert "媒体转述、戏剧化标题或未经正式来源确认的说法" in prompt
    assert "输出 materiality>=2 前必须自检" in prompt
    assert "本次新披露、与明确发行人" in prompt
    assert "应优先复制能直接证明底层事件" in prompt
    assert "只有标题可用时" in prompt


def test_v1_frozen_bytes_and_v1_1_prompt_hash_are_exact() -> None:
    assert (
        _sha256(BASE_CONTRACT_PATH)
        == "b3eb24c63816043edf0ef728d8d9778cd9083d720649d6fff3ae6289bba74300"
    )
    assert (
        _sha256(BASE_PROMPT_PATH)
        == "4474d61f17f6c8f9a6c909228423f17cc06083b5776f481c4044c0146efbde9d"
    )
    assert (
        _sha256(ACTIVE_PROMPT_PATH)
        == "0446af3fdf31e48afb86a2668d055b892e2c71618a93cf9084496aa17cc8fd0e"
    )


def test_v1_2_is_prompt_only_and_hardens_literal_evidence_copying() -> None:
    design = load_event_evaluation_design()
    active = runner._load_active_contract(
        design,
        PROJECT_ROOT,
        ACTIVE_V1_2_CONTRACT_PATH,
    )
    prompt = ACTIVE_V1_2_PROMPT_PATH.read_text(encoding="utf-8")

    assert active.document["schema_version"] == "p4.2a-event-extract-eval-v1.2"
    assert active.document["pre_registered_at"] == "2026-08-04T06:58:21Z"
    assert active.prompt == prompt
    assert "[P4_NEWS_EVENT_EXTRACT v1.2.0]" in prompt
    assert "一个尽量短、足以支持结论的**单行原文片段**" in prompt
    assert "不得删除、增加、改写或规范化空白" in prompt
    assert "不要选跨换行的完整长句" in prompt
    assert (
        _sha256(ACTIVE_V1_2_PROMPT_PATH)
        == "5080bdb2b373f6360527c79465da8645884fd33308c9e3d061120b0a1298fe05"
    )
