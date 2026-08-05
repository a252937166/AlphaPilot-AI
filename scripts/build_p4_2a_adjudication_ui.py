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
import ast
import html
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
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
EVENT_TYPES = (
    "earnings_preannounce", "major_contract", "buyback_or_holder_change",
    "regulatory_action", "halt_resume", "ma_restructure", "policy_sector",
    "dividend", "other",
)


class AdjudicationUIError(ValueError):
    """The adjudication artifact failed a safety or integrity gate."""


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
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise AdjudicationUIError(f"{label} line {number} must be a JSON object")
        forbidden = _find_forbidden(row)
        if forbidden is not None:
            raise AdjudicationUIError(f"{label} leaks blind-forbidden field at {forbidden}")
        rows.append(row)
    if not rows:
        raise AdjudicationUIError(f"{label} is empty")
    return rows


def _gold(value: object) -> dict[str, Any]:
    parsed = ast.literal_eval(value) if isinstance(value, str) and value.strip() else value
    if not isinstance(parsed, dict):
        raise AdjudicationUIError("draft gold must be an object")
    return {field: parsed.get(field) for field in GOLD_FIELDS}


def _build(sample_path: Path, draft_path: Path) -> tuple[list[dict[str, Any]], str]:
    samples = {row["news_item_id"]: row for row in _rows(sample_path, label="sample")}
    drafts = {row["news_item_id"]: row for row in _rows(draft_path, label="draft")}
    if set(samples) != set(drafts):
        raise AdjudicationUIError(
            "draft ids do not exactly cover the sample: "
            f"missing={sorted(set(samples) - set(drafts))[:5]}, "
            f"extra={sorted(set(drafts) - set(samples))[:5]}"
        )
    annotators = {str(row.get("annotation_owner") or "") for row in drafts.values()}
    annotators.discard("")
    if len(annotators) != 1:
        raise AdjudicationUIError(f"draft must declare exactly one annotation_owner, got {sorted(annotators)}")
    draft_annotator = annotators.pop()

    items: list[dict[str, Any]] = []
    for nid in sorted(samples, key=lambda i: samples[i].get("sample_index") or 0):
        sample, draft = samples[nid], drafts[nid]
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
                "draft": _gold(draft.get("gold")),
            }
        )
    return items, draft_annotator


def _render(items: list[dict[str, Any]], *, draft_annotator: str, title: str, storage_key: str) -> str:
    data = json.dumps(items, ensure_ascii=False)
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
const DRAFT_ANNOTATOR = {json.dumps(draft_annotator)};
const KEY = {json.dumps(storage_key)};
let idx = 0;
let state = JSON.parse(localStorage.getItem(KEY) || "{{}}");

const $ = (id) => document.getElementById(id);
const cur = () => ITEMS[idx];
const rec = () => (state[cur().news_item_id] ||= {{
  values: JSON.parse(JSON.stringify(cur().draft)),
  adjudicated: null, adjudicated_at: null,
}});
const save = () => {{ localStorage.setItem(KEY, JSON.stringify(state)); paint(); }};

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
  $("meta").innerHTML = [
    `#${{it.sample_index ?? idx + 1}}`, it.source, it.body_state || "",
    it.ingested_symbol ? `抓取标注 ${{it.ingested_symbol}}` : "无股票标注",
    `<span class="tag ${{cls}}">${{label}}</span>`,
  ].filter(Boolean).map(t => t.startsWith("<span") ? t : `<span class="tag">${{t}}</span>`).join("");
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

$("direction").onclick = (e) => {{ if (!e.target.dataset.v) return;
  markDirty(); rec().values.direction = Number(e.target.dataset.v); save(); paintSeg("direction", rec().values.direction); }};
$("materiality").onclick = (e) => {{ if (!e.target.dataset.v) return;
  markDirty(); rec().values.materiality = Number(e.target.dataset.v); save(); paintSeg("materiality", rec().values.materiality); }};
$("event_type").onchange = (e) => {{ markDirty(); rec().values.event_type = e.target.value; save(); }};
$("symbols").oninput = (e) => {{ markDirty(); const raw = e.target.value.trim();
  rec().values.symbols = raw ? raw.split(/[,，\\s]+/).filter(Boolean) : null; save(); }};
$("evidence_span").oninput = (e) => {{ markDirty(); rec().values.evidence_span = e.target.value.trim() || null; save(); }};
$("notes").oninput = (e) => {{ markDirty(); rec().values.notes = e.target.value.trim() || null; save(); }};

function confirmItem() {{
  const r = rec();
  const changed = JSON.stringify(r.values) !== JSON.stringify(cur().draft);
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
  const pending = ITEMS.findIndex(i => !state[i.news_item_id]?.adjudicated);
  if (pending !== -1) {{ idx = pending; render(); alert(`第 ${{pending + 1}} 条尚未裁定。每一条都必须经人工确认或修改。`); return; }}
  const lines = ITEMS.map(it => {{
    const r = state[it.news_item_id];
    const changed_fields = Object.keys(it.draft).filter(
      k => JSON.stringify(r.values[k] ?? null) !== JSON.stringify(it.draft[k] ?? null));
    return JSON.stringify({{
      news_item_id: it.news_item_id,
      gold: r.values,
      draft_gold: it.draft,
      annotation_status: "adjudicated",
      adjudication: {{
        method: "ai_drafted_human_adjudicated",
        draft_annotator: DRAFT_ANNOTATOR,
        adjudicator: adjudicator,
        status: r.adjudicated,
        changed_fields: changed_fields,
        adjudicated_at: r.adjudicated_at,
      }},
    }});
  }});
  const blob = new Blob([lines.join("\\n") + "\\n"], {{type: "application/x-ndjson"}});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = KEY.replace("p4.2a-adjudication:", "") + ".adjudicated.jsonl";
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
    arguments = parser.parse_args(argv)

    sample_path = arguments.sample.expanduser().resolve()
    draft_path = arguments.draft.expanduser().resolve()
    items, draft_annotator = _build(sample_path, draft_path)
    output = (
        arguments.output.expanduser().resolve()
        if arguments.output is not None
        else sample_path.with_suffix(".adjudication.html")
    )
    if output.exists() or output.is_symlink():
        raise SystemExit(f"refusing to overwrite existing output: {output}")
    output.write_text(
        _render(
            items,
            draft_annotator=draft_annotator,
            title=f"P4.2a 人工裁定 · {sample_path.stem}",
            storage_key=f"p4.2a-adjudication:{sample_path.name}",
        ),
        encoding="utf-8",
    )
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
