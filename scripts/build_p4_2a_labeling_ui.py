#!/usr/bin/env python3
# ruff: noqa: E501 -- the embedded HTML/CSS/JS template keeps its own line breaks.
"""Render a local, blind labeling UI for a P4.2a gold sample file.

The generated page is a single self-contained HTML file: it embeds the sample
items, keeps progress in localStorage, and exports a JSONL that matches the
frozen gold schema. Model predictions are never read, so blindness is
structural rather than a matter of discipline.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODEL_PREDICTION_KEYS = frozenset(
    {
        "prediction",
        "predictions",
        "predicted",
        "model_prediction",
        "model_predictions",
        "model_output",
        "extract_result",
        "confidence",
        "materiality_pred",
    }
)
TEST_SAMPLING_KEYS = frozenset(
    {
        "candidate_pool_membership",
        "eligible_pool",
        "eligible_pool_size",
        "holdout_label",
        "predicted_materiality",
        "prediction_artifact",
        "prediction_error",
        "prediction_status",
        "selection_basis",
        "selection_digest",
        "selection_rank",
        "selection_reason",
        "selection_score",
        "test_assignment",
        "test_sampling_basis",
        "test_selection_reason",
    }
)
FORBIDDEN_BLINDNESS_KEYS = MODEL_PREDICTION_KEYS | TEST_SAMPLING_KEYS
ANNOTATION_MUTABLE_FIELDS = frozenset(
    {
        "annotation_status",
        "annotation_owner",
        "annotated_at",
        "gold",
    }
)
ANNOTATION_ITEM_FIELDS = frozenset(
    {
        "schema_version",
        "sample_version",
        "contract_sha256",
        "sample_index",
        "sample_group",
        "trading_date",
        "stratum",
        "rank_sha256",
        "news_item_id",
        "source",
        "url",
        "title",
        "ingested_symbol",
        "published_at",
        "available_time",
        "original_text",
        "body_state",
        "content_hash",
        "text_sha256",
        "body_evidence",
        "annotation_status",
        "annotation_owner",
        "annotated_at",
        "gold",
        "input_sha256",
    }
)
GOLD_FIELDS = frozenset(
    {"symbols", "event_type", "direction", "materiality", "evidence_span", "notes"}
)
STRATUM_FIELDS = frozenset(
    {"source", "symbol_state", "require_announcement_body"}
)
BODY_EVIDENCE_FIELDS = frozenset(
    {
        "annotation_text_character_count",
        "body_characters_in_original_text",
        "full_text_character_count",
        "full_text_sha256",
        "pdf_persisted",
        "pdf_sha256",
        "required",
        "source",
        "text_truncated",
        "url",
    }
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SYMBOL_PATTERN = re.compile(r"^[0-9]{6}$")
EVENT_TYPES = (
    "earnings_preannounce",
    "major_contract",
    "buyback_or_holder_change",
    "regulatory_action",
    "halt_resume",
    "ma_restructure",
    "policy_sector",
    "dividend",
    "other",
)


class LabelingUIError(ValueError):
    """The blind owner-labeling artifact failed a safety or integrity gate."""


def _strict_json_object(line: str, *, line_number: int) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise LabelingUIError(
            f"sample line {line_number} contains non-standard JSON constant {value}"
        )

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise LabelingUIError(
                    f"sample line {line_number} contains duplicate JSON key {key}"
                )
            result[key] = value
        return result

    try:
        value: object = json.loads(
            line,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except json.JSONDecodeError as exc:
        raise LabelingUIError(f"sample line {line_number} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise LabelingUIError(f"sample line {line_number} must be a JSON object")
    return value


def _find_forbidden_key(value: object, *, path: str = "$") -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            child = f"{path}.{key}"
            if key in FORBIDDEN_BLINDNESS_KEYS:
                return child
            found = _find_forbidden_key(nested, path=child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found = _find_forbidden_key(nested, path=f"{path}[{index}]")
            if found is not None:
                return found
    return None


def _required_text(row: dict[str, Any], field: str, news_item_id: int) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value:
        raise LabelingUIError(
            f"sample news_item_id={news_item_id} has invalid {field}"
        )
    return value


def _validate_blind_item(
    row: dict[str, Any],
    *,
    expected_index: int,
    seen_ids: set[int],
) -> None:
    unexpected = set(row) - ANNOTATION_ITEM_FIELDS
    missing = ANNOTATION_ITEM_FIELDS - set(row)
    if unexpected or missing:
        raise LabelingUIError(
            "sample top-level fields drifted: "
            f"unexpected={sorted(unexpected)}, missing={sorted(missing)}"
        )
    forbidden = _find_forbidden_key(row)
    if forbidden is not None:
        raise LabelingUIError(f"sample contains blind-forbidden field at {forbidden}")

    news_item_id = row.get("news_item_id")
    if (
        isinstance(news_item_id, bool)
        or not isinstance(news_item_id, int)
        or news_item_id <= 0
    ):
        raise LabelingUIError("sample news_item_id must be a positive integer")
    if news_item_id in seen_ids:
        raise LabelingUIError(f"sample duplicates news_item_id={news_item_id}")
    seen_ids.add(news_item_id)
    if row.get("sample_index") != expected_index:
        raise LabelingUIError("sample order or sample_index drifted")
    if row.get("schema_version") != "p4.2a-gold-annotation-item-v1":
        raise LabelingUIError(f"sample news_item_id={news_item_id} schema drifted")
    if row.get("sample_version") != "p4.2a-gold-v1":
        raise LabelingUIError(f"sample news_item_id={news_item_id} version drifted")

    for field in (
        "contract_sha256",
        "rank_sha256",
        "content_hash",
        "text_sha256",
        "input_sha256",
    ):
        value = row.get(field)
        if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
            raise LabelingUIError(
                f"sample news_item_id={news_item_id} has invalid {field}"
            )
    original_text = _required_text(row, "original_text", news_item_id)
    observed_text_hash = hashlib.sha256(original_text.encode("utf-8")).hexdigest()
    if row.get("text_sha256") != observed_text_hash:
        raise LabelingUIError(f"sample news_item_id={news_item_id} text hash drifted")
    _required_text(row, "source", news_item_id)
    _required_text(row, "title", news_item_id)
    _required_text(row, "available_time", news_item_id)
    _required_text(row, "body_state", news_item_id)
    stratum = row.get("stratum")
    body_evidence = row.get("body_evidence")
    if (
        not isinstance(stratum, dict)
        or set(stratum) != STRATUM_FIELDS
        or not isinstance(body_evidence, dict)
        or set(body_evidence) != BODY_EVIDENCE_FIELDS
    ):
        raise LabelingUIError(
            f"sample news_item_id={news_item_id} frozen object evidence fields drifted"
        )
    ingested_symbol = row.get("ingested_symbol")
    if ingested_symbol is not None and (
        not isinstance(ingested_symbol, str)
        or SYMBOL_PATTERN.fullmatch(ingested_symbol) is None
    ):
        raise LabelingUIError(
            f"sample news_item_id={news_item_id} has invalid ingested_symbol"
        )
    if row.get("annotation_status") != "pending":
        raise LabelingUIError(f"sample news_item_id={news_item_id} is not pending")
    if row.get("annotation_owner") is not None or row.get("annotated_at") is not None:
        raise LabelingUIError(
            f"sample news_item_id={news_item_id} already has annotation provenance"
        )
    gold = row.get("gold")
    if not isinstance(gold, dict) or set(gold) != GOLD_FIELDS:
        raise LabelingUIError(
            f"sample news_item_id={news_item_id} gold fields drifted"
        )
    if any(value is not None for value in gold.values()):
        raise LabelingUIError(
            f"gold must be blank before labeling: news_item_id={news_item_id}"
        )


def _load_items(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise LabelingUIError("sample must be one regular non-symlink JSONL file")
    items: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise LabelingUIError("sample must be UTF-8 JSONL") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise LabelingUIError(f"sample line {line_number} is blank")
        row = _strict_json_object(line, line_number=line_number)
        _validate_blind_item(
            row,
            expected_index=len(items) + 1,
            seen_ids=seen_ids,
        )
        items.append(row)
    if not items:
        raise LabelingUIError("sample is empty")
    return items


def _script_json(value: object) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _render(items: list[dict[str, Any]], *, sample_path: Path, title: str) -> str:
    payload = _script_json(items)
    sample_name = _script_json(sample_path.name)
    download_name = _script_json(sample_path.stem + ".labels.jsonl")
    event_types = _script_json(EVENT_TYPES)
    forbidden_import_keys = _script_json(tuple(sorted(FORBIDDEN_BLINDNESS_KEYS)))
    options = "".join(f'<option value="{name}">{name}</option>' for name in EVENT_TYPES)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ color-scheme: dark; --bg:#0b1220; --panel:#131c2e; --line:#243149;
  --ink:#e6edf7; --dim:#93a4bf; --cyan:#3fd0d8; --warn:#f5a524; --bad:#f4645f; --ok:#4ade80; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",sans-serif; }}
header {{ position:sticky; top:0; z-index:5; background:#0b1220ee; backdrop-filter:blur(8px);
  border-bottom:1px solid var(--line); padding:10px 16px; display:flex; gap:14px; align-items:center; flex-wrap:wrap; }}
h1 {{ font-size:15px; margin:0; font-weight:600; letter-spacing:.3px; }}
.bar {{ flex:1; min-width:140px; height:6px; background:#1d2942; border-radius:4px; overflow:hidden; }}
.bar i {{ display:block; height:100%; background:var(--cyan); width:0; transition:width .2s; }}
button {{ font:inherit; color:var(--ink); background:#1b2740; border:1px solid var(--line);
  border-radius:8px; padding:6px 12px; cursor:pointer; }}
button:hover {{ border-color:var(--cyan); }}
button.primary {{ background:var(--cyan); color:#062024; border-color:var(--cyan); font-weight:600; }}
main {{ max-width:1080px; margin:0 auto; padding:18px 16px 90px; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:18px; }}
.meta {{ display:flex; gap:10px; flex-wrap:wrap; font-size:12px; color:var(--dim); margin-bottom:8px; }}
.tag {{ background:#1b2740; border:1px solid var(--line); border-radius:999px; padding:2px 10px; }}
h2 {{ font-size:19px; margin:2px 0 12px; line-height:1.45; }}
.body {{ white-space:pre-wrap; max-height:340px; overflow:auto; background:#0e1728;
  border:1px solid var(--line); border-radius:10px; padding:12px 14px; font-size:14px; color:#cfe0f5; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:14px; margin-top:16px; }}
label {{ display:block; font-size:12px; color:var(--dim); margin-bottom:5px; letter-spacing:.3px; }}
select,input,textarea {{ width:100%; background:#0e1728; color:var(--ink); border:1px solid var(--line);
  border-radius:8px; padding:8px 10px; font:inherit; }}
.seg {{ display:flex; gap:6px; }}
.seg button {{ flex:1; padding:8px 4px; }}
.seg button.on {{ background:var(--cyan); color:#062024; border-color:var(--cyan); font-weight:700; }}
.seg.dir button.on.neg {{ background:var(--bad); border-color:var(--bad); color:#2a0b0a; }}
.seg.dir button.on.pos {{ background:var(--ok); border-color:var(--ok); color:#062412; }}
footer {{ position:fixed; bottom:0; left:0; right:0; background:#0b1220ee; backdrop-filter:blur(8px);
  border-top:1px solid var(--line); padding:10px 16px; display:flex; gap:10px; align-items:center; justify-content:center; }}
.hint {{ font-size:12px; color:var(--dim); }}
.note {{ font-size:12px; color:var(--warn); margin-top:12px; }}
</style></head><body>
<header>
  <h1>P4.2a 盲标</h1>
  <span class="hint" id="counter">0 / 0</span>
  <span class="bar"><i id="prog"></i></span>
  <input id="annotator" placeholder="标注人姓名/标识（必填）" style="width:190px">
  <button id="importBtn">导入旧版进度</button>
  <input id="importInput" type="file" accept=".jsonl,application/x-ndjson" hidden>
  <button id="exportBtn" class="primary">导出 JSONL</button>
</header>
<main>
  <div class="card">
    <div class="meta" id="meta"></div>
    <h2 id="title"></h2>
    <div class="body" id="text"></div>
    <div class="grid">
      <div>
        <label>event_type（事件类型）</label>
        <select id="event_type"><option value="">— 未标 —</option>{options}</select>
      </div>
      <div>
        <label>direction（方向：利空 / 中性 / 利好）</label>
        <div class="seg dir" id="direction">
          <button data-v="-1" class="neg">-1</button><button data-v="0">0</button><button data-v="1" class="pos">+1</button>
        </div>
      </div>
      <div>
        <label>materiality（重要性 0–3；≥2 才算“重磅”）</label>
        <div class="seg" id="materiality">
          <button data-v="0">0</button><button data-v="1">1</button><button data-v="2">2</button><button data-v="3">3</button>
        </div>
      </div>
      <div>
        <label>symbols（涉及股票代码，逗号分隔；不确定留空）</label>
        <input id="symbols" placeholder="例如 002594,300750">
      </div>
    </div>
    <div style="margin-top:14px">
      <label>evidence_span（必填：必须是正文中的连续原文；可选中后点“取选中”）
        <button id="grab" style="margin-left:8px;padding:2px 10px;font-size:12px">取选中</button></label>
      <textarea id="evidence_span" rows="2"></textarea>
    </div>
    <div style="margin-top:14px">
      <label>notes（可选备注）</label>
      <input id="notes">
    </div>
    <p class="note">判断标准：materiality 反映“对该股价格的潜在影响强度”，纯行业/宏观资讯若无明确个股指向应给 0–1
      且 symbols 留空；页面上下文不能单独证明个股归属。</p>
  </div>
</main>
<footer>
  <button id="prev">← 上一条</button>
  <span class="hint">快捷键：←/→ 翻页 · 0–3 打 materiality · z/x/c 打方向</span>
  <button id="next" class="primary">下一条 →</button>
</footer>
<script id="annotation-items" type="application/json">{payload}</script>
<script>
const ITEMS = JSON.parse(document.getElementById("annotation-items").textContent);
const EVENT_TYPES = {event_types};
const FORBIDDEN_IMPORT_KEYS = new Set({forbidden_import_keys});
const KEY = "p4.2a-labels:" + {sample_name};
let idx = 0;
let labels = loadLabels();

const $ = (id) => document.getElementById(id);
const cur = () => ITEMS[idx];
const defaultLabel = () => ({{direction:null, event_type:null, evidence_span:null,
  materiality:null, notes:null, symbols:[]}});
const labelFor = (item) => (labels[item.news_item_id] ||= defaultLabel());
const lab = () => labelFor(cur());

function loadLabels() {{
  try {{
    const parsed = JSON.parse(localStorage.getItem(KEY) || "{{}}");
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {{}};
  }} catch (_error) {{
    return {{}};
  }}
}}

function normalizedSymbols(raw) {{
  if (raw === null || raw === undefined || raw === "") return [];
  const values = Array.isArray(raw)
    ? raw.map(String)
    : (typeof raw === "string" ? raw.split(/[,，\\s]+/).filter(Boolean) : null);
  if (values === null) return null;
  return [...new Set(values.map(v => v.trim()).filter(Boolean))].sort();
}}

function normalizedGold(item, label) {{
  return {{
    symbols: normalizedSymbols(label.symbols),
    event_type: label.event_type,
    direction: label.direction,
    materiality: label.materiality,
    evidence_span: typeof label.evidence_span === "string" ? label.evidence_span.trim() : null,
    notes: typeof label.notes === "string" && label.notes.trim() ? label.notes.trim() : null,
  }};
}}

function goldError(item, gold) {{
  if (!Array.isArray(gold.symbols)
      || gold.symbols.some(s => !/^[0-9]{{6}}$/.test(s))
      || gold.symbols.join(",") !== [...new Set(gold.symbols)].sort().join(",")) {{
    return "symbols 必须是排序去重后的 6 位股票代码";
  }}
  if (!EVENT_TYPES.includes(gold.event_type)) return "event_type 未完成";
  if (![-1, 0, 1].includes(gold.direction)) return "direction 未完成";
  if (![0, 1, 2, 3].includes(gold.materiality)) return "materiality 未完成";
  if (typeof gold.evidence_span !== "string" || !gold.evidence_span
      || !item.original_text.includes(gold.evidence_span)) {{
    return "evidence_span 必须是正文中的非空连续原文";
  }}
  if (gold.notes !== null && typeof gold.notes !== "string") return "notes 格式无效";
  return null;
}}

function containsForbiddenImportKey(value) {{
  if (Array.isArray(value)) return value.some(containsForbiddenImportKey);
  if (!value || typeof value !== "object") return false;
  return Object.entries(value).some(([key, nested]) =>
    FORBIDDEN_IMPORT_KEYS.has(key) || containsForbiddenImportKey(nested));
}}

function importedLabel(row) {{
  if (!row || typeof row !== "object" || Array.isArray(row)
      || containsForbiddenImportKey(row)) {{
    throw new Error("导入文件含预测、抽样依据或无效记录");
  }}
  const item = ITEMS.find(candidate => candidate.news_item_id === row.news_item_id);
  if (!item) throw new Error(`导入文件含未知 news_item_id=${{row.news_item_id}}`);
  const gold = row.gold;
  if (!gold || typeof gold !== "object" || Array.isArray(gold)
      || Object.keys(gold).some(key => ![
        "symbols", "event_type", "direction", "materiality", "evidence_span", "notes",
      ].includes(key))) {{
    throw new Error(`news_item_id=${{row.news_item_id}} 的 gold 格式无效`);
  }}
  return {{
    symbols: normalizedSymbols(gold.symbols),
    event_type: gold.event_type ?? null,
    direction: gold.direction ?? null,
    materiality: gold.materiality ?? null,
    evidence_span: gold.evidence_span ?? null,
    notes: gold.notes ?? null,
    annotated_at: typeof row.annotated_at === "string" ? row.annotated_at : null,
  }};
}}

function save() {{ localStorage.setItem(KEY, JSON.stringify(labels)); paintProgress(); }}

function paintProgress() {{
  const done = ITEMS.filter(i => {{
    const l = labels[i.news_item_id];
    return l && goldError(i, normalizedGold(i, l)) === null;
  }}).length;
  $("counter").textContent = `${{idx + 1}} / ${{ITEMS.length}} · 已完成 ${{done}}`;
  $("prog").style.width = (done / ITEMS.length * 100) + "%";
}}

function paintSeg(id, value) {{
  [...$(id).children].forEach(b => b.classList.toggle("on", String(value) === b.dataset.v));
}}

function render() {{
  const it = cur(), l = lab();
  const tags = [
    `#${{it.sample_index ?? idx + 1}}`, it.source, it.body_state || "",
    it.ingested_symbol ? `抓取标注 ${{it.ingested_symbol}}` : "无股票标注",
    it.available_time || "",
  ].filter(Boolean).map(text => {{
    const span = document.createElement("span");
    span.className = "tag";
    span.textContent = String(text);
    return span;
  }});
  $("meta").replaceChildren(...tags);
  $("title").textContent = it.title || "(无标题)";
  $("text").textContent = it.original_text || "(无正文，仅标题)";
  $("event_type").value = l.event_type || "";
  $("symbols").value = Array.isArray(l.symbols) ? l.symbols.join(",") : (l.symbols || "");
  $("evidence_span").value = l.evidence_span || "";
  $("notes").value = l.notes || "";
  paintSeg("direction", l.direction);
  paintSeg("materiality", l.materiality);
  paintProgress();
  window.scrollTo(0, 0);
}}

function move(step) {{ idx = Math.min(ITEMS.length - 1, Math.max(0, idx + step)); render(); }}

$("direction").onclick = (e) => {{ if (!e.target.dataset.v) return;
  lab().direction = Number(e.target.dataset.v); save(); paintSeg("direction", lab().direction); }};
$("materiality").onclick = (e) => {{ if (!e.target.dataset.v) return;
  lab().materiality = Number(e.target.dataset.v); save(); paintSeg("materiality", lab().materiality); }};
$("event_type").onchange = (e) => {{ lab().event_type = e.target.value || null; save(); }};
$("symbols").oninput = (e) => {{ const raw = e.target.value.trim();
  lab().symbols = raw ? raw.split(/[,，\\s]+/).filter(Boolean) : null; save(); }};
$("evidence_span").oninput = (e) => {{ lab().evidence_span = e.target.value.trim() || null; save(); }};
$("notes").oninput = (e) => {{ lab().notes = e.target.value.trim() || null; save(); }};
$("grab").onclick = () => {{ const sel = String(window.getSelection()).trim();
  if (sel) {{ $("evidence_span").value = sel; lab().evidence_span = sel; save(); }} }};
$("prev").onclick = () => move(-1);
$("next").onclick = () => move(1);
$("importBtn").onclick = () => $("importInput").click();
$("importInput").onchange = async (event) => {{
  const file = event.target.files?.[0];
  if (!file) return;
  try {{
    const rows = file.text
      ? (await file.text()).split(/\\r?\\n/).filter(line => line.trim())
        .map(line => JSON.parse(line))
      : [];
    if (!rows.length) throw new Error("导入文件为空");
    const seen = new Set();
    const imported = {{}};
    for (const row of rows) {{
      if (seen.has(row?.news_item_id)) throw new Error("导入文件含重复 news_item_id");
      seen.add(row?.news_item_id);
      imported[row.news_item_id] = importedLabel(row);
    }}
    labels = {{...labels, ...imported}};
    save();
    render();
    alert(`已导入 ${{rows.length}} 条进度；最终导出仍会执行完整性校验。`);
  }} catch (_error) {{
    alert("导入失败：仅接受本盲标样本导出的无预测 JSONL。");
  }} finally {{
    event.target.value = "";
  }}
}};

document.onkeydown = (e) => {{
  if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
  if (e.key === "ArrowRight") move(1);
  else if (e.key === "ArrowLeft") move(-1);
  else if ("0123".includes(e.key)) {{ lab().materiality = Number(e.key); save(); paintSeg("materiality", lab().materiality); }}
  else if ("zxc".includes(e.key)) {{ lab().direction = {{z:-1, x:0, c:1}}[e.key]; save(); paintSeg("direction", lab().direction); }}
}};

const ANNOTATOR_KEY = KEY + ":annotator";
$("annotator").value = localStorage.getItem(ANNOTATOR_KEY) || "";
$("annotator").oninput = (e) => localStorage.setItem(ANNOTATOR_KEY, e.target.value.trim());

$("exportBtn").onclick = () => {{
  // Provenance must be truthful: whoever labeled has to name themselves, and
  // the identity is never defaulted to the project owner.
  const annotator = ($("annotator").value || "").trim();
  if (!annotator) {{
    $("annotator").focus();
    alert("请先填写标注人姓名/标识，导出文件需要如实记录标注来源。");
    return;
  }}
  const prepared = ITEMS.map((item, index) => {{
    const label = labelFor(item);
    const gold = normalizedGold(item, label);
    return {{item, index, label, gold, error: goldError(item, gold)}};
  }});
  const firstInvalid = prepared.find(entry => entry.error !== null);
  if (firstInvalid) {{
    idx = firstInvalid.index;
    render();
    alert(`第 ${{firstInvalid.index + 1}} 条未完成：${{firstInvalid.error}}`);
    return;
  }}
  const completedAt = new Date().toISOString();
  const records = prepared.map((entry) => {{
    const annotatedAt = (typeof entry.label.annotated_at === "string"
      && !Number.isNaN(Date.parse(entry.label.annotated_at)))
      ? entry.label.annotated_at : completedAt;
    entry.label.annotated_at = annotatedAt;
    return {{...entry.item,
      annotation_status: "completed",
      annotation_owner: annotator,
      annotated_at: annotatedAt,
      gold: entry.gold,
    }};
  }});
  save();
  const lines = records.map(record => JSON.stringify(record));
  const blob = new Blob([lines.join("\\n") + "\\n"], {{type: "application/x-ndjson"}});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = {download_name};
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 0);
}};

render();
</script></body></html>
"""


def _write_create_only(path: Path, content: str) -> None:
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise LabelingUIError("output parent must be an existing regular directory")
    if path.exists() or path.is_symlink():
        raise FileExistsError("refusing to overwrite an existing labeling UI")
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample",
        type=Path,
        default=PROJECT_ROOT / "docs/phase4/eval/P4.2a-gold-inventory60-v1.jsonl",
    )
    parser.add_argument("--output", type=Path, default=None)
    arguments = parser.parse_args(argv)

    try:
        sample_candidate = arguments.sample.expanduser()
        if sample_candidate.is_symlink():
            raise LabelingUIError("sample must not be a symlink")
        sample_path = sample_candidate.resolve()
        items = _load_items(sample_path)
        output_candidate = (
            arguments.output.expanduser()
            if arguments.output is not None
            else sample_path.with_suffix(".labeling.html")
        )
        if output_candidate.is_symlink():
            raise FileExistsError("refusing to overwrite a labeling UI symlink")
        output = output_candidate.resolve()
        _write_create_only(
            output,
            _render(
                items,
                sample_path=sample_path,
                title=f"P4.2a 盲标 · {sample_path.stem}",
            ),
        )
    except (LabelingUIError, FileExistsError, OSError, UnicodeError):
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": "labeling_ui_safety_gate_failed",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {"written": str(output), "items": len(items), "blind": True},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
