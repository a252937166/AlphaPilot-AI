#!/usr/bin/env python3
# ruff: noqa: E501 -- the embedded HTML/CSS/JS template keeps its own line breaks.
"""Render the human-adjudication UI for an AI-drafted P4.2a label file.

Input: a blind sample JSONL (same schema as the frozen gold files) plus a
draft label JSONL produced by an AI annotator from that same blind file.
Output: a single offline HTML page where the human adjudicator reviews every
item — draft labels prefilled — and must explicitly confirm or amend each one
before export. The export records three identities per file (draft annotator,
human adjudicator, per-item changed/confirmed status) so the provenance of
``ai_drafted_human_adjudicated`` is machine-checkable.

Blindness: both inputs are rejected if they carry any model-prediction or
test-sampling field; the page performs no network requests.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import build_p4_2a_gold_sample as gold_builder  # noqa: E402

from alphapilot.llm.p4_news_eval import (  # noqa: E402
    EventEvaluationDesign,
    EventEvaluationDesignError,
    load_event_evaluation_design,
)

DEFAULT_EVALUATION_DESIGN = PROJECT_ROOT / "config/p4_event_evaluation_v1_6.yaml"
SIX_DIGIT_SYMBOL = re.compile(r"^[0-9]{6}$")
FORBIDDEN_KEYS = frozenset(
    {
        "prediction", "predictions", "predicted", "model_prediction",
        "model_predictions", "model_output", "extract_result", "confidence",
        "materiality_pred", "candidate_pool_membership", "eligible_pool",
        "eligible_pool_size", "holdout_label", "predicted_materiality",
        "prediction_artifact", "prediction_error", "prediction_status",
        "selection_basis", "selection_digest", "selection_rank",
        "selection_reason", "selection_score", "test_assignment",
        "test_sampling_basis", "test_selection_reason",
    }
)
GOLD_FIELDS = ("symbols", "event_type", "direction", "materiality", "evidence_span", "notes")
GOLD_FIELD_SET = frozenset(GOLD_FIELDS)
EVENT_TYPES = (
    "earnings_preannounce", "major_contract", "buyback_or_holder_change",
    "regulatory_action", "halt_resume", "ma_restructure", "policy_sector",
    "dividend", "other",
)


class AdjudicationUIError(ValueError):
    """The adjudication artifact failed a safety or integrity gate."""


def _reject_non_finite(value: str) -> None:
    raise AdjudicationUIError(f"non-finite JSON numeric constant is forbidden: {value}")


def _strict_json_object(line: str, *, label: str, line_number: int) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise AdjudicationUIError(
                    f"{label} line {line_number} contains duplicate JSON key {key}"
                )
            result[key] = value
        return result

    try:
        value: object = json.loads(
            line,
            object_pairs_hook=reject_duplicates,
            parse_constant=_reject_non_finite,
        )
    except json.JSONDecodeError as exc:
        raise AdjudicationUIError(
            f"{label} line {line_number} is not strict JSON"
        ) from exc
    if not isinstance(value, dict):
        raise AdjudicationUIError(f"{label} line {line_number} must be a JSON object")
    return value


def _find_forbidden(value: object, path: str = "$") -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            child = f"{path}.{key}"
            if key in FORBIDDEN_KEYS:
                return child
            found = _find_forbidden(nested, child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found = _find_forbidden(nested, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def _rows(path: Path, *, label: str) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise AdjudicationUIError(f"{label} must be one regular non-symlink JSONL file")
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise AdjudicationUIError(f"{label} must be UTF-8 JSONL") from exc
    for number, line in enumerate(lines, 1):
        if not line.strip():
            raise AdjudicationUIError(f"{label} line {number} is blank")
        row = _strict_json_object(line, label=label, line_number=number)
        forbidden = _find_forbidden(row)
        if forbidden is not None:
            raise AdjudicationUIError(f"{label} leaks blind-forbidden field at {forbidden}")
        rows.append(row)
    if not rows:
        raise AdjudicationUIError(f"{label} is empty")
    return rows


def _gold(
    value: object,
    *,
    original_text: str,
    taxonomy: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != GOLD_FIELD_SET:
        raise AdjudicationUIError(f"{label} gold fields drifted")
    symbols = value.get("symbols")
    if (
        not isinstance(symbols, list)
        or any(not isinstance(symbol, str) for symbol in symbols)
        or any(SIX_DIGIT_SYMBOL.fullmatch(symbol) is None for symbol in symbols)
        or symbols != sorted(set(symbols))
    ):
        raise AdjudicationUIError(f"{label} gold.symbols must be a sorted unique array")
    event_type = value.get("event_type")
    if not isinstance(event_type, str) or event_type not in taxonomy:
        raise AdjudicationUIError(f"{label} gold.event_type is invalid")
    direction = value.get("direction")
    if (
        isinstance(direction, bool)
        or not isinstance(direction, int)
        or direction not in {-1, 0, 1}
    ):
        raise AdjudicationUIError(f"{label} gold.direction is invalid")
    materiality = value.get("materiality")
    if (
        isinstance(materiality, bool)
        or not isinstance(materiality, int)
        or materiality not in {0, 1, 2, 3}
    ):
        raise AdjudicationUIError(f"{label} gold.materiality is invalid")
    evidence_span = value.get("evidence_span")
    if (
        not isinstance(evidence_span, str)
        or not evidence_span
        or evidence_span not in original_text
    ):
        raise AdjudicationUIError(
            f"{label} gold.evidence_span must be a contiguous source quote"
        )
    notes = value.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise AdjudicationUIError(f"{label} gold.notes is invalid")
    return {
        "symbols": list(symbols),
        "event_type": event_type,
        "direction": direction,
        "materiality": materiality,
        "evidence_span": evidence_span,
        "notes": notes,
    }


def _aware_datetime(value: object, *, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise AdjudicationUIError(f"{label} must be a timezone-aware ISO datetime")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdjudicationUIError(f"{label} must be a timezone-aware ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdjudicationUIError(f"{label} must be a timezone-aware ISO datetime")


def _indexed_rows(
    rows: list[dict[str, Any]],
    *,
    label: str,
) -> dict[int, dict[str, Any]]:
    indexed: dict[int, dict[str, Any]] = {}
    for row in rows:
        news_item_id = row.get("news_item_id")
        if (
            isinstance(news_item_id, bool)
            or not isinstance(news_item_id, int)
            or news_item_id <= 0
        ):
            raise AdjudicationUIError(f"{label} has invalid news_item_id")
        if news_item_id in indexed:
            raise AdjudicationUIError(f"{label} duplicates news_item_id={news_item_id}")
        indexed[news_item_id] = row
    return indexed


def _build(
    sample_path: Path,
    draft_path: Path,
    *,
    design: EventEvaluationDesign,
    expected_count: int,
) -> tuple[list[dict[str, Any]], str]:
    sample_rows = _rows(sample_path, label="sample")
    draft_rows = _rows(draft_path, label="draft")
    if len(sample_rows) != expected_count or len(draft_rows) != expected_count:
        raise AdjudicationUIError(
            f"sample and draft must each contain exactly {expected_count} rows"
        )
    samples = _indexed_rows(sample_rows, label="sample")
    drafts = _indexed_rows(draft_rows, label="draft")
    if list(samples) != list(drafts):
        raise AdjudicationUIError("draft IDs/order do not exactly match the blind sample")

    taxonomy_record = design.base_contract.document.get("taxonomy")
    if not isinstance(taxonomy_record, Mapping):
        raise AdjudicationUIError("annotation taxonomy contract is invalid")
    taxonomy_values = taxonomy_record.get("values")
    if not isinstance(taxonomy_values, list) or any(
        not isinstance(value, str) for value in taxonomy_values
    ):
        raise AdjudicationUIError("annotation taxonomy values are invalid")
    taxonomy = frozenset(taxonomy_values)

    immutable_fields = (
        gold_builder.ANNOTATION_ITEM_FIELDS - gold_builder.ANNOTATION_MUTABLE_FIELDS
    )
    draft_annotator: str | None = None

    items: list[dict[str, Any]] = []
    for expected_index, (nid, sample) in enumerate(samples.items(), start=1):
        draft = drafts[nid]
        try:
            gold_builder.validate_blind_record(sample, design.base_contract)
        except gold_builder.GoldSampleError as exc:
            raise AdjudicationUIError(str(exc)) from exc
        if sample.get("sample_index") != expected_index:
            raise AdjudicationUIError("sample order or sample_index drifted")
        if sample.get("sample_group") != "heldout40":
            raise AdjudicationUIError(f"sample news_item_id={nid} is not heldout40")
        if set(draft) != gold_builder.ANNOTATION_ITEM_FIELDS:
            raise AdjudicationUIError(f"draft news_item_id={nid} fields drifted")
        for field in immutable_fields:
            if draft.get(field) != sample.get(field):
                raise AdjudicationUIError(
                    f"draft news_item_id={nid} changed frozen field {field}"
                )
        if draft.get("annotation_status") not in {"annotated", "complete", "completed"}:
            raise AdjudicationUIError(f"draft news_item_id={nid} is not completed")
        owner = draft.get("annotation_owner")
        if not isinstance(owner, str) or not owner.strip():
            raise AdjudicationUIError(
                f"draft news_item_id={nid} has no AI annotator identity"
            )
        normalized_owner = owner.strip()
        if draft_annotator is None:
            draft_annotator = normalized_owner
        elif normalized_owner != draft_annotator:
            raise AdjudicationUIError("draft must use one consistent AI annotator identity")
        _aware_datetime(
            draft.get("annotated_at"),
            label=f"draft news_item_id={nid} annotated_at",
        )
        original_text = sample.get("original_text")
        if not isinstance(original_text, str) or not original_text:
            raise AdjudicationUIError(f"sample news_item_id={nid} has no original_text")
        draft_gold = _gold(
            draft.get("gold"),
            original_text=original_text,
            taxonomy=taxonomy,
            label=f"draft news_item_id={nid}",
        )
        if draft_gold["notes"] is not None:
            raise AdjudicationUIError(
                f"draft news_item_id={nid} must keep notes empty for blind adjudication"
            )
        items.append(
            {
                "news_item_id": nid,
                "sample_index": sample.get("sample_index"),
                "source": sample.get("source"),
                "ingested_symbol": sample.get("ingested_symbol"),
                "title": sample.get("title") or "",
                "text": sample.get("original_text") or "",
                "body_state": sample.get("body_state"),
                "available_time": sample.get("available_time"),
                "draft": draft_gold,
            }
        )
    if draft_annotator is None:
        raise AdjudicationUIError("draft annotator identity is unavailable")
    return items, draft_annotator


def _script_json(value: object) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _render(
    items: list[dict[str, Any]],
    *,
    draft_annotator: str,
    title: str,
    storage_key: str,
    download_name: str,
) -> str:
    data = _script_json(items)
    draft_annotator_json = _script_json(draft_annotator)
    storage_key_json = _script_json(storage_key)
    download_name_json = _script_json(download_name)
    event_types_json = _script_json(EVENT_TYPES)
    gold_fields_json = _script_json(GOLD_FIELDS)
    options = "".join(f'<option value="{name}">{name}</option>' for name in EVENT_TYPES)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ color-scheme: dark; --bg:#0b1220; --panel:#131c2e; --line:#243149; --ink:#e6edf7;
  --dim:#93a4bf; --cyan:#3fd0d8; --ok:#4ade80; --warn:#f5a524; --bad:#f4645f; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif; }}
header {{ position:sticky; top:0; z-index:5; background:#0b1220ee; backdrop-filter:blur(8px);
  border-bottom:1px solid var(--line); padding:10px 16px; display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
h1 {{ font-size:15px; margin:0; font-weight:600; }}
.bar {{ flex:1; min-width:120px; height:6px; background:#1d2942; border-radius:4px; overflow:hidden; }}
.bar i {{ display:block; height:100%; background:var(--ok); width:0; transition:width .2s; }}
button {{ font:inherit; color:var(--ink); background:#1b2740; border:1px solid var(--line);
  border-radius:8px; padding:6px 12px; cursor:pointer; }}
button.primary {{ background:var(--ok); color:#062412; border-color:var(--ok); font-weight:700; }}
main {{ max-width:1080px; margin:0 auto; padding:16px 16px 100px; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:18px; }}
.meta {{ display:flex; gap:8px; flex-wrap:wrap; font-size:12px; color:var(--dim); margin-bottom:8px; }}
.tag {{ background:#1b2740; border:1px solid var(--line); border-radius:999px; padding:2px 10px; }}
.tag.state-pending {{ border-color:var(--warn); color:var(--warn); }}
.tag.state-confirmed {{ border-color:var(--ok); color:var(--ok); }}
.tag.state-changed {{ border-color:var(--cyan); color:var(--cyan); }}
h2 {{ font-size:18px; margin:2px 0 10px; line-height:1.45; }}
.body {{ white-space:pre-wrap; max-height:300px; overflow:auto; background:#0e1728;
  border:1px solid var(--line); border-radius:10px; padding:12px 14px; font-size:14px; color:#cfe0f5; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; margin-top:14px; }}
label {{ display:block; font-size:12px; color:var(--dim); margin-bottom:5px; }}
select,input,textarea {{ width:100%; background:#0e1728; color:var(--ink); border:1px solid var(--line);
  border-radius:8px; padding:8px 10px; font:inherit; }}
.seg {{ display:flex; gap:6px; }}
.seg button {{ flex:1; padding:8px 4px; }}
.seg button.on {{ background:var(--cyan); color:#062024; border-color:var(--cyan); font-weight:700; }}
footer {{ position:fixed; bottom:0; left:0; right:0; background:#0b1220ee; backdrop-filter:blur(8px);
  border-top:1px solid var(--line); padding:10px 16px; display:flex; gap:10px; align-items:center; justify-content:center; }}
.hint {{ font-size:12px; color:var(--dim); }}
#confirmBtn {{ min-width:200px; }}
</style></head><body>
<header>
  <h1>P4.2a 人工裁定</h1>
  <span class="tag">草稿：{html.escape(draft_annotator)}</span>
  <span class="hint" id="counter">0 / 0</span>
  <span class="bar"><i id="prog"></i></span>
  <input id="adjudicator" placeholder="裁定人姓名（必填）" style="width:170px">
  <button id="exportBtn" class="primary">导出裁定结果</button>
</header>
<main>
  <div class="card">
    <div class="meta" id="meta"></div>
    <h2 id="title"></h2>
    <div class="body" id="text"></div>
    <div class="grid">
      <div><label>event_type</label>
        <select id="event_type">{options}</select></div>
      <div><label>direction（z / x / c）</label>
        <div class="seg" id="direction">
          <button data-v="-1">-1</button><button data-v="0">0</button><button data-v="1">+1</button>
        </div></div>
      <div><label>materiality（0–3；≥2 = 重磅）</label>
        <div class="seg" id="materiality">
          <button data-v="0">0</button><button data-v="1">1</button><button data-v="2">2</button><button data-v="3">3</button>
        </div></div>
      <div><label>symbols（逗号分隔，无明确主体则留空）</label>
        <input id="symbols"></div>
    </div>
    <div style="margin-top:12px"><label>evidence_span</label>
      <textarea id="evidence_span" rows="2"></textarea></div>
    <div style="margin-top:12px"><label>notes</label>
      <input id="notes"></div>
  </div>
</main>
<footer>
  <button id="prev">← 上一条</button>
  <button id="confirmBtn" class="primary">✓ 确认本条（Enter）</button>
  <button id="next">下一条 →</button>
  <span class="hint">改动会自动记为"修改"；确认后进入下一条</span>
</footer>
<script type="application/json" id="adjudication-items">{data}</script>
<script>
const ITEMS = JSON.parse(document.getElementById("adjudication-items").textContent);
const DRAFT_ANNOTATOR = {draft_annotator_json};
const KEY = {storage_key_json};
const DOWNLOAD_NAME = {download_name_json};
const EVENT_TYPES = {event_types_json};
const GOLD_FIELDS = {gold_fields_json};
let idx = 0;
let state = loadState();

const $ = (id) => document.getElementById(id);
const cur = () => ITEMS[idx];
const rec = () => (state[cur().news_item_id] ||= {{
  values: JSON.parse(JSON.stringify(cur().draft)),
  adjudicated: null, adjudicated_at: null,
}});
const save = () => {{ localStorage.setItem(KEY, JSON.stringify(state)); paint(); }};

function loadState() {{
  try {{
    const value = JSON.parse(localStorage.getItem(KEY) || "{{}}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {{}};
  }} catch (_error) {{
    return {{}};
  }}
}}
function paint() {{
  const done = ITEMS.filter(i => state[i.news_item_id]?.adjudicated).length;
  $("counter").textContent = `${{idx + 1}} / ${{ITEMS.length}} · 已裁定 ${{done}}`;
  $("prog").style.width = (done / ITEMS.length * 100) + "%";
}}
function paintSeg(id, value) {{
  [...$(id).children].forEach(b => b.classList.toggle("on", String(value) === b.dataset.v));
}}
function stateTag(r) {{
  if (!r.adjudicated) return ["state-pending", "待裁定"];
  return r.adjudicated === "confirmed" ? ["state-confirmed", "已确认"] : ["state-changed", "已修改"];
}}
function render() {{
  const it = cur(), r = rec();
  const [cls, label] = stateTag(r);
  const metaValues = [
    `#${{it.sample_index ?? idx + 1}}`, it.source, it.body_state || "",
    it.ingested_symbol ? `抓取标注 ${{it.ingested_symbol}}` : "无股票标注",
  ].filter(Boolean);
  const tags = metaValues.map((value) => {{
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = String(value);
    return tag;
  }});
  const stateElement = document.createElement("span");
  stateElement.className = `tag ${{cls}}`;
  stateElement.textContent = label;
  $("meta").replaceChildren(...tags, stateElement);
  $("title").textContent = it.title || "(无标题)";
  $("text").textContent = it.text || "(仅标题)";
  $("event_type").value = r.values.event_type || "other";
  $("symbols").value = Array.isArray(r.values.symbols) ? r.values.symbols.join(",") : "";
  $("evidence_span").value = r.values.evidence_span || "";
  $("notes").value = r.values.notes || "";
  paintSeg("direction", r.values.direction);
  paintSeg("materiality", r.values.materiality);
  paint();
  window.scrollTo(0, 0);
}}
function markDirty() {{ const r = rec(); if (r.adjudicated) {{ r.adjudicated = null; r.adjudicated_at = null; }} }}
function move(step) {{ idx = Math.min(ITEMS.length - 1, Math.max(0, idx + step)); render(); }}
function normalizedSymbols(value) {{
  const values = Array.isArray(value)
    ? value.map(String)
    : (typeof value === "string" ? value.split(/[,，\\s]+/).filter(Boolean) : []);
  return [...new Set(values.map(v => v.trim()).filter(Boolean))].sort();
}}
function normalizedGold(values) {{
  return {{
    symbols: normalizedSymbols(values.symbols),
    event_type: values.event_type,
    direction: values.direction,
    materiality: values.materiality,
    evidence_span: typeof values.evidence_span === "string"
      ? values.evidence_span.trim() : null,
    notes: typeof values.notes === "string" && values.notes.trim()
      ? values.notes.trim() : null,
  }};
}}
function goldError(item, gold) {{
  if (!gold.symbols.every(s => /^[0-9]{{6}}$/.test(s))) return "symbols 必须是 6 位代码";
  if (!EVENT_TYPES.includes(gold.event_type)) return "event_type 无效";
  if (![-1, 0, 1].includes(gold.direction)) return "direction 未完成";
  if (![0, 1, 2, 3].includes(gold.materiality)) return "materiality 未完成";
  if (!gold.evidence_span || !item.text.includes(gold.evidence_span)) {{
    return "evidence_span 必须是正文中的连续原文";
  }}
  return null;
}}

$("direction").onclick = (e) => {{ if (!e.target.dataset.v) return;
  markDirty(); rec().values.direction = Number(e.target.dataset.v); save(); paintSeg("direction", rec().values.direction); }};
$("materiality").onclick = (e) => {{ if (!e.target.dataset.v) return;
  markDirty(); rec().values.materiality = Number(e.target.dataset.v); save(); paintSeg("materiality", rec().values.materiality); }};
$("event_type").onchange = (e) => {{ markDirty(); rec().values.event_type = e.target.value; save(); }};
$("symbols").oninput = (e) => {{ markDirty(); const raw = e.target.value.trim();
  rec().values.symbols = raw ? raw.split(/[,，\\s]+/).filter(Boolean) : []; save(); }};
$("evidence_span").oninput = (e) => {{ markDirty(); rec().values.evidence_span = e.target.value.trim() || null; save(); }};
$("notes").oninput = (e) => {{ markDirty(); rec().values.notes = e.target.value.trim() || null; save(); }};

function confirmItem() {{
  const r = rec();
  const normalized = normalizedGold(r.values);
  const error = goldError(cur(), normalized);
  if (error !== null) {{ alert(`本条未完成：${{error}}`); return; }}
  r.values = normalized;
  const changed = JSON.stringify(normalized) !== JSON.stringify(cur().draft);
  r.adjudicated = changed ? "changed" : "confirmed";
  r.adjudicated_at = new Date().toISOString();
  save();
  if (idx < ITEMS.length - 1) move(1); else render();
}}
$("confirmBtn").onclick = confirmItem;
$("prev").onclick = () => move(-1);
$("next").onclick = () => move(1);

const ADJ_KEY = KEY + ":adjudicator";
$("adjudicator").value = localStorage.getItem(ADJ_KEY) || "";
$("adjudicator").oninput = (e) => localStorage.setItem(ADJ_KEY, e.target.value.trim());

document.onkeydown = (e) => {{
  if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
  if (e.key === "Enter") confirmItem();
  else if (e.key === "ArrowRight") move(1);
  else if (e.key === "ArrowLeft") move(-1);
  else if ("0123".includes(e.key)) {{ markDirty(); rec().values.materiality = Number(e.key); save(); paintSeg("materiality", rec().values.materiality); }}
  else if ("zxc".includes(e.key)) {{ markDirty(); rec().values.direction = {{z:-1, x:0, c:1}}[e.key]; save(); paintSeg("direction", rec().values.direction); }}
}};

$("exportBtn").onclick = () => {{
  const adjudicator = ($("adjudicator").value || "").trim();
  if (!adjudicator) {{ $("adjudicator").focus(); alert("请先填写裁定人姓名——导出需如实记录人工裁定者身份。"); return; }}
  if (adjudicator.toLocaleLowerCase() === DRAFT_ANNOTATOR.toLocaleLowerCase()) {{
    $("adjudicator").focus();
    alert("人工裁定者必须与 AI 起草者不同。");
    return;
  }}
  const pending = ITEMS.findIndex(i => !state[i.news_item_id]?.adjudicated);
  if (pending !== -1) {{ idx = pending; render(); alert(`第 ${{pending + 1}} 条尚未裁定。每一条都必须经人工确认或修改。`); return; }}
  const lines = ITEMS.map(it => {{
    const r = state[it.news_item_id];
    const gold = normalizedGold(r.values);
    const error = goldError(it, gold);
    if (error !== null) throw new Error(`news_item_id=${{it.news_item_id}}: ${{error}}`);
    const changed_fields = GOLD_FIELDS.filter(
      k => JSON.stringify(gold[k] ?? null) !== JSON.stringify(it.draft[k] ?? null));
    const adjudicatedChanged = changed_fields.length > 0;
    return JSON.stringify({{
      schema_version: "p4.2a-heldout-adjudication-export-v1",
      news_item_id: it.news_item_id,
      gold: gold,
      draft_gold: it.draft,
      annotation_status: "adjudicated",
      adjudication: {{
        method: "ai_drafted_human_adjudicated",
        draft_annotator: DRAFT_ANNOTATOR,
        adjudicator: adjudicator,
        adjudicated_changed: adjudicatedChanged,
        changed_fields: changed_fields,
        adjudicated_at: r.adjudicated_at,
      }},
    }});
  }});
  const blob = new Blob([lines.join("\\n") + "\\n"], {{type: "application/x-ndjson"}});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = DOWNLOAD_NAME;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 0);
}};

render();
</script></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, required=True, help="blind sample JSONL")
    parser.add_argument("--draft", type=Path, required=True, help="AI-drafted labels JSONL")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--evaluation-design",
        type=Path,
        default=DEFAULT_EVALUATION_DESIGN,
        help="Byte-frozen heldout evaluation design.",
    )
    arguments = parser.parse_args(argv)

    try:
        sample_candidate = arguments.sample.expanduser()
        draft_candidate = arguments.draft.expanduser()
        if sample_candidate.is_symlink() or draft_candidate.is_symlink():
            raise AdjudicationUIError("sample and draft must not be symlinks")
        sample_path = sample_candidate.resolve()
        draft_path = draft_candidate.resolve()
        design = load_event_evaluation_design(
            arguments.evaluation_design.expanduser().resolve(),
            project_root=PROJECT_ROOT,
        )
        if design.document.get("schema_version") == "p4.2a-evaluation-design-v2":
            raise AdjudicationUIError(
                "P4.2a v2 requires its dedicated dev45/heldout60 adjudication UI"
            )
        expected_sample = gold_builder.evaluation_artifact_path(
            design,
            "heldout_40_blind_sample_jsonl",
            project_root=PROJECT_ROOT,
        )
        if sample_path != expected_sample:
            raise AdjudicationUIError(
                "sample must be the heldout blind artifact bound by the evaluation design"
            )
        completion = design.document.get("owner_annotation_completion")
        if not isinstance(completion, Mapping):
            raise AdjudicationUIError("owner annotation completion contract is invalid")
        heldout = completion.get("heldout")
        if not isinstance(heldout, Mapping):
            raise AdjudicationUIError("heldout annotation completion contract is invalid")
        expected_count = heldout.get("required_row_count")
        if isinstance(expected_count, bool) or not isinstance(expected_count, int):
            raise AdjudicationUIError("heldout annotation row-count contract is invalid")
        items, draft_annotator = _build(
            sample_path,
            draft_path,
            design=design,
            expected_count=expected_count,
        )
        output_candidate = (
            arguments.output.expanduser()
            if arguments.output is not None
            else sample_path.with_suffix(".adjudication.html")
        )
        if output_candidate.is_symlink():
            raise FileExistsError("refusing to overwrite adjudication UI symlink")
        output = output_candidate.resolve()
        if output.parent.is_symlink() or not output.parent.is_dir():
            raise AdjudicationUIError("output parent must be an existing regular directory")
        sample_sha256 = hashlib.sha256(sample_path.read_bytes()).hexdigest()
        draft_sha256 = hashlib.sha256(draft_path.read_bytes()).hexdigest()
        rendered = _render(
            items,
            draft_annotator=draft_annotator,
            title=f"P4.2a 人工裁定 · {sample_path.stem}",
            storage_key=(
                f"p4.2a-adjudication:{sample_path.name}:"
                f"{sample_sha256}:{draft_sha256}"
            ),
            download_name=f"{sample_path.stem}.adjudicated.jsonl",
        )
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
    except (
        AdjudicationUIError,
        EventEvaluationDesignError,
        FileExistsError,
        gold_builder.GoldSampleError,
        OSError,
        UnicodeError,
    ) as exc:
        print(f"P4.2a adjudication UI failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "written": str(output),
                "items": len(items),
                "draft_annotator": draft_annotator,
                "blind": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
