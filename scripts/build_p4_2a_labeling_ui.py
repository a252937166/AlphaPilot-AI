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
import ast
import html
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_KEYS = frozenset(
    {"prediction", "predicted", "model_output", "confidence", "model", "materiality_pred"}
)
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


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _load_items(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        leaked = sorted(FORBIDDEN_KEYS.intersection(row))
        if leaked:
            raise SystemExit(f"gold sample leaks model output fields: {leaked}")
        gold = _as_mapping(row.get("gold"))
        if any(value is not None for value in gold.values()):
            raise SystemExit(
                f"gold must be blank before labeling: news_item_id={row.get('news_item_id')}"
            )
        items.append(
            {
                "news_item_id": row.get("news_item_id"),
                "sample_index": row.get("sample_index"),
                "source": row.get("source"),
                "ingested_symbol": row.get("ingested_symbol"),
                "title": row.get("title") or "",
                "text": row.get("original_text") or "",
                "url": row.get("url") or "",
                "published_at": row.get("published_at"),
                "available_time": row.get("available_time"),
                "body_state": row.get("body_state"),
            }
        )
    return items


def _render(items: list[dict[str, Any]], *, sample_path: Path, title: str) -> str:
    payload = json.dumps(items, ensure_ascii=False)
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
      <label>evidence_span（可选：在正文选中一段后点“取选中”）
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
<script>
const ITEMS = {payload};
const KEY = "p4.2a-labels:" + {json.dumps(sample_path.name)};
let idx = 0;
let labels = JSON.parse(localStorage.getItem(KEY) || "{{}}");

const $ = (id) => document.getElementById(id);
const cur = () => ITEMS[idx];
const lab = () => (labels[cur().news_item_id] ||= {{direction:null, event_type:null,
  evidence_span:null, materiality:null, notes:null, symbols:null}});

function save() {{ localStorage.setItem(KEY, JSON.stringify(labels)); paintProgress(); }}

function paintProgress() {{
  const done = ITEMS.filter(i => {{
    const l = labels[i.news_item_id];
    return l && l.materiality !== null && l.event_type && l.direction !== null;
  }}).length;
  $("counter").textContent = `${{idx + 1}} / ${{ITEMS.length}} · 已完成 ${{done}}`;
  $("prog").style.width = (done / ITEMS.length * 100) + "%";
}}

function paintSeg(id, value) {{
  [...$(id).children].forEach(b => b.classList.toggle("on", String(value) === b.dataset.v));
}}

function render() {{
  const it = cur(), l = lab();
  $("meta").innerHTML = [
    `#${{it.sample_index ?? idx + 1}}`, it.source, it.body_state || "",
    it.ingested_symbol ? `抓取标注 ${{it.ingested_symbol}}` : "无股票标注",
    it.available_time || "",
  ].filter(Boolean).map(t => `<span class="tag">${{t}}</span>`).join("");
  $("title").textContent = it.title || "(无标题)";
  $("text").textContent = it.text || "(无正文，仅标题)";
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

document.onkeydown = (e) => {{
  if (["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) return;
  if (e.key === "ArrowRight") move(1);
  else if (e.key === "ArrowLeft") move(-1);
  else if ("0123".includes(e.key)) {{ lab().materiality = Number(e.key); save(); paintSeg("materiality", lab().materiality); }}
  else if ("zxc".includes(e.key)) {{ lab().direction = {{z:-1, x:0, c:1}}[e.key]; save(); paintSeg("direction", lab().direction); }}
}};

$("exportBtn").onclick = () => {{
  const lines = ITEMS.map(it => JSON.stringify({{
    news_item_id: it.news_item_id,
    gold: labels[it.news_item_id] || {{direction:null, event_type:null, evidence_span:null,
      materiality:null, notes:null, symbols:null}},
    annotation_status: (labels[it.news_item_id]?.materiality !== null
      && labels[it.news_item_id]?.event_type
      && labels[it.news_item_id]?.direction !== null) ? "annotated" : "pending",
  }}));
  const blob = new Blob([lines.join("\\n") + "\\n"], {{type: "application/x-ndjson"}});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = {json.dumps(sample_path.stem)} + ".labels.jsonl";
  a.click();
}};

render();
</script></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample",
        type=Path,
        default=PROJECT_ROOT / "docs/phase4/eval/P4.2a-gold-inventory60-v1.jsonl",
    )
    parser.add_argument("--output", type=Path, default=None)
    arguments = parser.parse_args(argv)

    sample_path = arguments.sample.expanduser().resolve()
    items = _load_items(sample_path)
    output = (
        arguments.output.expanduser().resolve()
        if arguments.output is not None
        else sample_path.with_suffix(".labeling.html")
    )
    output.write_text(
        _render(items, sample_path=sample_path, title=f"P4.2a 盲标 · {sample_path.stem}"),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"written": str(output), "items": len(items), "blind": True},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
