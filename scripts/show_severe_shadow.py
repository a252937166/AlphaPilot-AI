from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROJECT_DIR = Path(__file__).resolve().parent.parent
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.2f}%"


def render(summary: dict[str, Any], moves: dict[str, Any], since: date, until: date) -> str:
    lines = [
        f"jev 影子分类 {since} 至 {until}：记录 {summary['records']} 条，"
        f"jev 已答 {summary['answered']} 条，出错 {summary['errors']} 条",
        f"两边都判严重 {summary['both_severe']} 条（子类相同 {summary['same_subtype']} 条）；"
        f"只有规则判严重 {summary['rule_only']} 条；只有 jev 判严重 {summary['jev_only']} 条",
        "jev 分类分布："
        + "，".join(f"{name} {count}" for name, count in summary["jev_choices"].items()),
        "",
        f"公告后的价格（首个可交易开盘入场，{moves['horizon']} 个交易日，超额相对全市场中位）：",
    ]
    names = {"both": "两边都判", "rule_only": "只有规则判", "jev_only": "只有 jev 判"}
    for key, name in names.items():
        part = moves[key]
        lines.append(
            f"  {name}：{part['events']} 家次，开盘跳空均值 {_pct(part['gap_mean'])}，"
            f"超额中位 {_pct(part['excess_median'])}（n={part['excess_n']}，"
            f"下跌占比 {_pct(part['excess_down_share']).lstrip('+')}）"
        )
    for key, name in (
        ("rule_only_examples", "只有规则判严重"),
        ("jev_only_examples", "只有 jev 判严重"),
    ):
        if summary[key]:
            lines += ["", f"{name}："]
            for item in summary[key]:
                when = datetime.fromisoformat(item["available_time"]).date()
                lines.append(
                    f"  {when} {item['symbol']} 规则 {item['rule'] or '-'} / jev {item['jev']}"
                    f"  {item['title'][:60]}"
                )
    return "\n".join(lines)


def main() -> int:
    os.chdir(PROJECT_DIR)
    today = datetime.now(MARKET_TIMEZONE).date()
    parser = argparse.ArgumentParser(
        description="Compare jev's shadow reading of CNInfo titles with the severe screen's rules."
    )
    parser.add_argument("--since", type=date.fromisoformat, default=date(2026, 9, 11))
    parser.add_argument("--until", type=date.fromisoformat, default=today)
    parser.add_argument("--horizon", type=int, default=5, help="Sessions after the entry open.")
    parser.add_argument("--examples", type=int, default=30)
    parser.add_argument("--json", action="store_true", help="Print the summary as JSON.")
    args = parser.parse_args()

    import pandas as pd
    from sqlalchemy import select

    from alphapilot.core.config import get_settings
    from alphapilot.db.engine import get_session, init_db
    from alphapilot.db.models import DailyBar
    from alphapilot.services.severe_shadow import drift, load_records, summarize

    init_db()
    records = load_records(Path(get_settings().severe_shadow_dir), args.since, args.until)
    summary = summarize(records, examples=args.examples)
    with get_session() as session:
        bars = pd.read_sql_query(
            select(DailyBar.symbol, DailyBar.trade_date, DailyBar.open, DailyBar.close).where(
                DailyBar.trade_date >= args.since - timedelta(days=10)
            ),
            session.connection(),
        )
    bars["trade_date"] = pd.to_datetime(bars["trade_date"]).dt.date
    bars = bars.drop_duplicates(["trade_date", "symbol"])
    opens = bars.pivot(index="trade_date", columns="symbol", values="open").sort_index()
    closes = bars.pivot(index="trade_date", columns="symbol", values="close").sort_index()
    moves = drift(records, opens, closes, horizon=args.horizon)
    if args.json:
        print(json.dumps({"summary": summary, "drift": moves}, ensure_ascii=False, indent=1))
    else:
        print(render(summary, moves, args.since, args.until))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
