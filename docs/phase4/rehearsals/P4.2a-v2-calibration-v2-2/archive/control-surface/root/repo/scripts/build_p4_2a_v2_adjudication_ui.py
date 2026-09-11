#!/usr/bin/env python3
# ruff: noqa: E501 -- the self-contained HTML/JavaScript template owns its line breaks.
"""Build the registered offline owner UI for P4.2a v2 dev45 adjudication."""

from __future__ import annotations

import argparse
import html
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import seal_p4_2a_v2_ai_draft as seal  # noqa: E402

from alphapilot.llm.p4_news_eval import (  # noqa: E402
    EVALUATION_DESIGN_V2_PATH,
    EventEvaluationDesignError,
)

EXPORT_SCHEMA = "p4.2a-v2-owner-adjudication-export-item-v1"


def _script_json(value: object) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def build_ui_items(
    blind_rows: Sequence[Mapping[str, Any]],
    draft_rows: Sequence[Mapping[str, Any]],
    *,
    contract: seal.V2AdjudicationContract,
) -> tuple[list[dict[str, Any]], str]:
    """Return the minimum recursively blind browser payload."""

    blind = seal.validate_blind_rows(blind_rows, contract=contract)
    drafts, drafter_id = seal.validate_sealed_draft_rows(blind, draft_rows, contract=contract)
    items: list[dict[str, Any]] = []
    for blind_row, draft in zip(blind, drafts, strict=True):
        item = {
            "sample_index": blind_row["sample_index"],
            "news_item_id": blind_row["news_item_id"],
            "source": blind_row["source"],
            "title": blind_row["title"],
            "ingested_symbol": blind_row["ingested_symbol"],
            "available_time": blind_row["available_time"],
            "original_text": blind_row["original_text"],
            "body_evidence": blind_row["body_evidence"],
            "input_sha256": blind_row["input_sha256"],
            "sealed_draft_item_sha256": seal.sha256_bytes(seal.canonical_json_bytes(draft)),
            "draft_label": draft["draft_label"],
        }
        leaked = seal._find_hidden_metadata(item)
        if leaked is not None:
            raise seal.V2AdjudicationError(f"UI payload leaks metadata at {leaked}")
        items.append(item)
    return items, drafter_id


def render_ui(
    items: Sequence[Mapping[str, Any]],
    *,
    contract: seal.V2AdjudicationContract,
    drafter_id: str,
    blind_sha256: str,
    draft_sha256: str,
    download_name: str,
) -> str:
    """Render a network-free page with explicit confirmation on every item."""

    item_json = _script_json(list(items))
    taxonomy_json = _script_json(sorted(contract.taxonomy))
    design_json = _script_json(contract.design_ref)
    drafter_json = _script_json(drafter_id)
    frame_json = _script_json(contract.frame_id)
    download_json = _script_json(download_name)
    storage_json = _script_json(
        f"p4.2a-v2-adjudication:{contract.design_ref['sha256']}:{blind_sha256}:{draft_sha256}"
    )
    options = "".join(
        f'<option value="{html.escape(value)}">{html.escape(value)}</option>'
        for value in sorted(contract.taxonomy)
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>P4.2a v2 开发框人工裁定</title>
<style>
:root {{ color-scheme:dark; --bg:#09111f; --panel:#121d30; --line:#293752; --ink:#e8eef8;
 --muted:#94a5bf; --ok:#4ade80; --cyan:#4bd5dd; --warn:#f5b942; --bad:#fb7185; }}
* {{ box-sizing:border-box }} body {{ margin:0; background:var(--bg); color:var(--ink);
 font:15px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif }}
header {{ position:sticky; top:0; z-index:5; padding:10px 16px; display:flex; align-items:center;
 gap:10px; flex-wrap:wrap; background:#09111fee; border-bottom:1px solid var(--line) }}
h1 {{ margin:0; font-size:16px }} .grow {{ flex:1 }} .tag {{ border:1px solid var(--line);
 border-radius:999px; padding:3px 10px; font-size:12px; color:var(--muted) }}
input,textarea,select,button {{ font:inherit }} input,textarea,select {{ width:100%; color:var(--ink);
 background:#0b1526; border:1px solid var(--line); border-radius:8px; padding:8px }}
header input {{ width:190px }} button {{ cursor:pointer; color:var(--ink); background:#1a2942;
 border:1px solid var(--line); border-radius:8px; padding:7px 12px }} button.primary {{ background:var(--ok);
 border-color:var(--ok); color:#052412; font-weight:700 }}
main {{ max-width:1080px; margin:auto; padding:18px 16px 110px }} .card {{ padding:18px;
 border:1px solid var(--line); border-radius:14px; background:var(--panel) }}
.meta {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:8px }} h2 {{ font-size:19px; margin:7px 0 12px }}
.body {{ max-height:330px; overflow:auto; white-space:pre-wrap; background:#0b1526; border:1px solid var(--line);
 border-radius:10px; padding:13px; color:#d4e2f5 }} .grid {{ display:grid;
 grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:13px; margin-top:14px }}
label {{ display:block; color:var(--muted); font-size:12px; margin-bottom:4px }} .seg {{ display:flex; gap:5px }}
.seg button {{ flex:1 }} .seg button.on {{ background:var(--cyan); color:#032327; border-color:var(--cyan) }}
footer {{ position:fixed; bottom:0; left:0; right:0; display:flex; justify-content:center; align-items:center;
 gap:9px; padding:10px; background:#09111fee; border-top:1px solid var(--line) }}
#state.pending {{ color:var(--warn); border-color:var(--warn) }} #state.confirmed {{ color:var(--ok); border-color:var(--ok) }}
#state.changed {{ color:var(--cyan); border-color:var(--cyan) }}
</style></head><body>
<header><h1>P4.2a v2 开发框人工裁定</h1><span class="tag">AI 草稿：{html.escape(drafter_id)}</span>
<span class="tag" id="counter"></span><span class="grow"></span>
<input id="adjudicator" autocomplete="off" placeholder="人工裁定人（必填）">
<button class="primary" id="export">全部完成后导出</button></header>
<main><section class="card"><div class="meta" id="meta"></div><h2 id="title"></h2>
<div class="body" id="body"></div><div class="grid">
<div><label>event_type</label><select id="event_type">{options}</select></div>
<div><label>direction</label><div class="seg" id="direction"><button data-value="-1">-1</button><button data-value="0">0</button><button data-value="1">+1</button></div></div>
<div><label>materiality</label><div class="seg" id="materiality"><button data-value="0">0</button><button data-value="1">1</button><button data-value="2">2</button><button data-value="3">3</button></div></div>
<div><label>symbols（逗号分隔）</label><input id="symbols"></div></div>
<div style="margin-top:12px"><label>evidence_span（必须是原文连续片段）</label><textarea id="evidence" rows="2"></textarea></div>
<div style="margin-top:12px"><label>notes（人工可补充；AI 草稿未预填）</label><input id="notes"></div></section></main>
<footer><button id="previous">← 上一条</button><button class="primary" id="confirm">人工确认本条</button>
<button id="next">下一条 →</button><span class="tag pending" id="state">待确认</span></footer>
<script type="application/json" id="items">{item_json}</script>
<script>
"use strict";
const ITEMS = JSON.parse(document.getElementById("items").textContent);
const TAXONOMY = {taxonomy_json};
const DESIGN = {design_json};
const FRAME_ID = {frame_json};
const DRAFTER_ID = {drafter_json};
const STORAGE_KEY = {storage_json};
const DOWNLOAD_NAME = {download_json};
const LABEL_FIELDS = ["symbols","event_type","direction","materiality","evidence_span","notes"];
let index = 0;
let state = loadState();
const byId = (id) => document.getElementById(id);
const item = () => ITEMS[index];
function loadState() {{
  try {{ const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{{}}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {{}}; }}
  catch (_error) {{ return {{}}; }}
}}
function record() {{
  const key = String(item().news_item_id);
  if (!state[key]) state[key] = {{label:JSON.parse(JSON.stringify(item().draft_label)),confirmed:false,adjudicator_id:null,adjudicated_at:null}};
  return state[key];
}}
function save() {{ localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); paintStatus(); }}
function normalizedSymbols(raw) {{
  const values = Array.isArray(raw) ? raw.map(String) : String(raw || "").split(/[,，\\s]+/);
  return [...new Set(values.map(value => value.trim()).filter(Boolean))].sort();
}}
function normalizedLabel(raw) {{ return {{symbols:normalizedSymbols(raw.symbols),event_type:raw.event_type,
 direction:raw.direction,materiality:raw.materiality,evidence_span:String(raw.evidence_span || "").trim(),
 notes:String(raw.notes || "").trim() || null}}; }}
function labelError(current, label) {{
  if (!label.symbols.every(value => /^[0-9]{{6}}$/.test(value))) return "symbols 必须是 6 位数字代码";
  if (!TAXONOMY.includes(label.event_type)) return "event_type 无效";
  if (![-1,0,1].includes(label.direction)) return "direction 无效";
  if (![0,1,2,3].includes(label.materiality)) return "materiality 无效";
  if (!label.evidence_span || !current.original_text.includes(label.evidence_span)) return "evidence_span 必须是原文连续片段";
  return null;
}}
function changedFields(draft, human) {{ return LABEL_FIELDS.filter(field =>
  JSON.stringify(draft[field] ?? null) !== JSON.stringify(human[field] ?? null)); }}
function markEdited() {{ const value=record(); value.confirmed=false; value.adjudicator_id=null; value.adjudicated_at=null; save(); }}
function paintSegment(id,value) {{ [...byId(id).children].forEach(button => button.classList.toggle("on",String(value)===button.dataset.value)); }}
function paintStatus() {{
  const value=record(); const human=normalizedLabel(value.label); const changed=changedFields(item().draft_label,human).length>0;
  const completed=ITEMS.filter(current => state[String(current.news_item_id)]?.confirmed).length;
  byId("counter").textContent=`${{index+1}} / ${{ITEMS.length}} · 已人工确认 ${{completed}}`;
  byId("state").className=`tag ${{value.confirmed ? (changed ? "changed" : "confirmed") : "pending"}}`;
  byId("state").textContent=value.confirmed ? (changed ? "已确认并修改" : "已确认原草稿") : "待人工确认";
}}
function render() {{
  const current=item(), value=record();
  const bodyState=current.body_evidence.required ? "公告正文证据已固化" : "标题/摘要证据";
  const tags=[`#${{current.sample_index}}`,current.source,current.available_time || "",current.ingested_symbol || "无抓取代码",bodyState];
  byId("meta").replaceChildren(...tags.map(text => {{const node=document.createElement("span");node.className="tag";node.textContent=text;return node;}}));
  byId("title").textContent=current.title; byId("body").textContent=current.original_text;
  byId("event_type").value=value.label.event_type; byId("symbols").value=(value.label.symbols || []).join(",");
  byId("evidence").value=value.label.evidence_span || ""; byId("notes").value=value.label.notes || "";
  paintSegment("direction",value.label.direction); paintSegment("materiality",value.label.materiality); paintStatus(); window.scrollTo(0,0);
}}
function move(delta) {{ index=Math.max(0,Math.min(ITEMS.length-1,index+delta));render(); }}
byId("direction").onclick=(event)=>{{if(event.target.dataset.value===undefined)return;record().label.direction=Number(event.target.dataset.value);markEdited();render();}};
byId("materiality").onclick=(event)=>{{if(event.target.dataset.value===undefined)return;record().label.materiality=Number(event.target.dataset.value);markEdited();render();}};
byId("event_type").onchange=(event)=>{{record().label.event_type=event.target.value;markEdited();}};
byId("symbols").oninput=(event)=>{{record().label.symbols=event.target.value;markEdited();}};
byId("evidence").oninput=(event)=>{{record().label.evidence_span=event.target.value;markEdited();}};
byId("notes").oninput=(event)=>{{record().label.notes=event.target.value;markEdited();}};
byId("previous").onclick=()=>move(-1); byId("next").onclick=()=>move(1);
byId("confirm").onclick=()=>{{
  const adjudicator=byId("adjudicator").value.trim();
  if(!adjudicator){{alert("必须填写人工裁定人身份");return;}}
  if(adjudicator.toLocaleLowerCase()===DRAFTER_ID.toLocaleLowerCase()){{alert("人工裁定人与 AI 起草者必须不同");return;}}
  const value=record(); value.label=normalizedLabel(value.label); const error=labelError(item(),value.label);
  if(error){{alert(error);return;}} value.confirmed=true; value.adjudicator_id=adjudicator; value.adjudicated_at=new Date().toISOString(); save();
  const pending=ITEMS.findIndex((current,position)=>position>index && !state[String(current.news_item_id)]?.confirmed);
  if(pending>=0){{index=pending;render();}}else render();
}};
byId("adjudicator").oninput=()=>{{
  const identity=byId("adjudicator").value.trim(); let changed=false;
  for(const value of Object.values(state)){{if(value.confirmed && value.adjudicator_id!==identity){{value.confirmed=false;value.adjudicator_id=null;value.adjudicated_at=null;changed=true;}}}}
  if(changed)save();
}};
byId("export").onclick=()=>{{
  const adjudicator=byId("adjudicator").value.trim();
  if(!adjudicator){{alert("必须填写人工裁定人身份");return;}}
  if(adjudicator.toLocaleLowerCase()===DRAFTER_ID.toLocaleLowerCase()){{alert("人工裁定人与 AI 起草者必须不同");return;}}
  const pending=ITEMS.findIndex(current=>{{const value=state[String(current.news_item_id)];return !value?.confirmed || value.adjudicator_id!==adjudicator;}});
  if(pending>=0){{index=pending;render();alert(`第 ${{pending+1}} 条尚未由当前人工裁定人确认`);return;}}
  const lines=ITEMS.map(current=>{{
    const value=state[String(current.news_item_id)]; const human=normalizedLabel(value.label);
    const error=labelError(current,human); if(error)throw new Error(`news_item_id=${{current.news_item_id}}: ${{error}}`);
    const changed_fields=changedFields(current.draft_label,human);
    return JSON.stringify({{schema_version:{_script_json(EXPORT_SCHEMA)},design:DESIGN,frame_id:FRAME_ID,
      sample_index:current.sample_index,news_item_id:current.news_item_id,input_sha256:current.input_sha256,
      sealed_draft_item_sha256:current.sealed_draft_item_sha256,draft_label:current.draft_label,human_label:human,
      annotation_status:"adjudicated",adjudication:{{method:"ai_drafted_human_adjudicated",drafter_id:DRAFTER_ID,
      adjudicator_id:adjudicator,confirmed:true,changed:changed_fields.length>0,changed_fields:changed_fields,
      adjudicated_at:value.adjudicated_at}}}});
  }});
  const blob=new Blob([lines.join("\\n")+"\\n"],{{type:"application/x-ndjson"}});
  const anchor=document.createElement("a");anchor.href=URL.createObjectURL(blob);anchor.download=DOWNLOAD_NAME;anchor.click();
  setTimeout(()=>URL.revokeObjectURL(anchor.href),0);
}};
render();
</script></body></html>"""


def render_registered_ui_payload(
    blind_rows: Sequence[Mapping[str, Any]],
    draft_rows: Sequence[Mapping[str, Any]],
    *,
    contract: seal.V2AdjudicationContract,
    blind_payload: bytes,
    draft_payload: bytes,
) -> tuple[bytes, int]:
    """Deterministically render the exact registered UI bytes for later revalidation."""

    items, drafter_id = build_ui_items(blind_rows, draft_rows, contract=contract)
    rendered = render_ui(
        items,
        contract=contract,
        drafter_id=drafter_id,
        blind_sha256=seal.sha256_bytes(blind_payload),
        draft_sha256=seal.sha256_bytes(draft_payload),
        download_name=contract.artifacts["development_owner_raw_export_jsonl"].name,
    ).encode()
    return rendered, len(items)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection-manifest", type=Path, default=None)
    parser.add_argument("--blind", type=Path, default=None)
    parser.add_argument("--draft", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--evaluation-design", type=Path, default=EVALUATION_DESIGN_V2_PATH)
    arguments = parser.parse_args(argv)
    try:
        contract = seal.load_registered_contract(arguments.evaluation_design)
        selection_manifest_path = seal._bound_input(
            arguments.selection_manifest
            or contract.artifacts["development_private_selection_manifest"],
            contract.artifacts["development_private_selection_manifest"],
            label="selection manifest",
        )
        blind_path = seal._bound_input(
            arguments.blind or contract.artifacts["development_owner_blind_jsonl"],
            contract.artifacts["development_owner_blind_jsonl"],
            label="blind",
        )
        draft_path = seal._bound_input(
            arguments.draft or contract.artifacts["development_ai_draft_jsonl"],
            contract.artifacts["development_ai_draft_jsonl"],
            label="draft",
        )
        output_path = seal._bound_input(
            arguments.output or contract.artifacts["development_adjudication_html"],
            contract.artifacts["development_adjudication_html"],
            label="output",
        )
        blind_rows, blind_payload, _ = seal.read_bound_blind_bundle(
            selection_manifest_path,
            blind_path,
            contract=contract,
        )
        draft_rows, draft_payload = seal.read_jsonl(draft_path, label="sealed draft")
        rendered, row_count = render_registered_ui_payload(
            blind_rows,
            draft_rows,
            contract=contract,
            blind_payload=blind_payload,
            draft_payload=draft_payload,
        )
        digest = seal.write_create_only(output_path, rendered)
    except (
        EventEvaluationDesignError,
        seal.V2AdjudicationError,
        FileExistsError,
        OSError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "rendered",
                "output": str(output_path.relative_to(PROJECT_ROOT)),
                "sha256": digest,
                "row_count": row_count,
                "offline": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
