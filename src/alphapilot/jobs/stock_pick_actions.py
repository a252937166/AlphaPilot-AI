"""Trade-action sheet for the weekly stock-pick candidates, one paper account each.

Reads the frozen list files and daily bars, runs a simulated account per candidate
(``services.stock_pick_paper``) and writes a create-only JSON snapshot under
``<stock_pick_forward_test_dir>/paper/`` plus a Markdown note into
``settings.stock_pick_actions_note_dir`` when set (the owner's Obsidian folder).
No broker, no orders, no language model: every line is computed from lists and bars.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphapilot.core.config import get_settings
from alphapilot.db.engine import get_session
from alphapilot.db.models import DailyBar, DomainEvent, Security
from alphapilot.jobs.registry import JobSpec, register
from alphapilot.services.severe_disclosure import SEVERE_EVENT_TYPE
from alphapilot.services.stock_pick_paper import (
    INDEX_SYMBOL,
    Bars,
    benchmark_return,
    simulate,
)
from alphapilot.services.stock_picks import CANDIDATES, write_json_create_only

JOB_NAME = "stock_pick_actions"
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
LABELS = {
    "A": "A 低波+低PB+低PE",
    "A_ind": "A 行业中性",
    "B": "B 十信号",
    "B_ind": "B 行业中性",
}
WEEKDAYS = "一二三四五六日"


def _lists(root: Path) -> dict[str, list[dict[str, Any]]]:
    found: dict[str, list[dict[str, Any]]] = {}
    for name in CANDIDATES:
        paths = sorted((root / "lists" / name).glob(f"{name}-*.json"))
        found[name] = [json.loads(p.read_bytes()) for p in paths]
    return found


def _sessions(session: Session, start: date) -> list[date]:
    rows = session.execute(
        select(DailyBar.trade_date, func.count())
        .where(DailyBar.trade_date >= start)
        .group_by(DailyBar.trade_date)
    ).all()
    if not rows:
        return []
    busiest = max(count for _, count in rows)
    return sorted(day for day, count in rows if count >= max(1, busiest // 5))


def _bars(session: Session, symbols: list[str], start: date) -> Bars:
    sessions = _sessions(session, start)
    frame = pd.read_sql_query(
        select(
            DailyBar.symbol,
            DailyBar.trade_date,
            DailyBar.open,
            DailyBar.high,
            DailyBar.low,
            DailyBar.close,
        ).where(DailyBar.trade_date >= start, DailyBar.symbol.in_([*symbols, INDEX_SYMBOL])),
        session.connection(),
    )
    frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
    frame = frame.drop_duplicates(["symbol", "trade_date"])

    def wide(column: str) -> pd.DataFrame:
        return frame.pivot(index="trade_date", columns="symbol", values=column).reindex(sessions)

    return Bars(open=wide("open"), high=wide("high"), low=wide("low"), close=wide("close"))


def _flags(session: Session, holdings: dict[str, Any]) -> dict[str, str]:
    flags: dict[str, str] = {}
    for symbol, holding in holdings.items():
        floor = datetime(holding.since.year, holding.since.month, holding.since.day, tzinfo=UTC)
        title = session.scalar(
            select(DomainEvent.title)
            .where(
                DomainEvent.event_type == SEVERE_EVENT_TYPE,
                DomainEvent.symbol == symbol,
                DomainEvent.occurred_at >= floor,
            )
            .order_by(DomainEvent.occurred_at.desc())
            .limit(1)
        )
        if title:
            flags[symbol] = str(title)[:40]
    return flags


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:+.2f}%"


def _wan(value: float) -> str:
    return f"{value / 10_000:,.2f} 万"


def _day_label(day: date) -> str:
    return f"{day.month} 月 {day.day} 日周{WEEKDAYS[day.weekday()]}"


def render_note(report: dict[str, Any]) -> str:
    """Markdown for the owner's vault; plain facts, no advice."""

    names: dict[str, str] = report["names"]
    run_day = date.fromisoformat(report["run_date"])
    lines = [
        "---",
        f"title: 交易动作 {report['run_date']}",
        f"date: {report['run_date']}",
        "tags: [alphapilot, trade-actions, paper]",
        f"generated_at: {report['generated_at']}",
        "---",
        f"# 交易动作 · {_day_label(run_day)}",
        "",
        (
            "模拟账户，不下真实单，不用任何模型。"
            f"每个候选各 {report['capital'] / 10_000:g} 万元起步，"
            f"按名单前 {report['top_n']} 只等额买入，每周第一个交易日开盘换仓；已扣佣金、"
            "过户费、印花税和每边 0.05% 滑点。四个候选都在前瞻测试中，没有一个被证实有效，"
            "裁定在 11 月 6 日。"
        ),
        "",
        f"数据截至 {report['last_session']} 收盘，账户自 {report['first_entry']} 开盘起算。",
        "",
        "## 账户",
        "",
        "| 候选 | 净值 | 累计 | 同期上证 | 持仓 | 现金 |",
        "|---|---|---|---|---|---|",
    ]
    for name, acc in report["accounts"].items():
        lines.append(
            f"| {LABELS[name]} | {_wan(acc['value'])} | {_pct(acc['return'])} | "
            f"{_pct(report['index_return'])} | {len(acc['holdings'])} 只 | {_wan(acc['cash'])} |"
        )
    lines += ["", "## 下一步动作", ""]
    plans = {n: a["plan"] for n, a in report["accounts"].items() if a.get("plan")}
    if not plans:
        lines.append(
            "本周已按名单持仓，交易日之间没有动作。下一次换仓在下周第一个交易日开盘，"
            "新名单周六早上生成。"
        )
    for name, plan in plans.items():
        lines += [
            f"### {LABELS[name]}：下一个交易日开盘执行",
            "",
            f"名单按 {plan['list_as_of']} 收盘生成。卖出 {len(plan['sells'])} 只，"
            f"买入 {len(plan['buys'])} 只，不动 {len(plan['keeps'])} 只。",
            "",
        ]
        if plan["sells"]:
            lines.append("卖出，开盘市价：")
            lines.append("")
            for row in plan["sell_rows"]:
                lines.append(f"- {row['symbol']} {names.get(row['symbol'], '')} {row['shares']} 股")
            lines.append("")
        if plan["buys"]:
            lines.append("买入，开盘市价，股数按参考价估算：")
            lines.append("")
            for row in plan["buys"]:
                shares = row["shares_est"]
                note = f"约 {shares} 股" if shares else "资金不足一手，跳过"
                lines.append(
                    f"- {row['symbol']} {names.get(row['symbol'], '')} {note}，"
                    f"参考价 {row['ref_price']}"
                )
            lines.append("")
    lines += ["", "## 风险提示", ""]
    any_flag = False
    for name, acc in report["accounts"].items():
        for symbol, title in acc["flags"].items():
            any_flag = True
            lines.append(
                f"- {LABELS[name]} 持有的 {symbol} {names.get(symbol, '')}：{title}。"
                "规则本身不要求卖出。"
            )
    if not any_flag:
        lines.append("持仓里没有被回避筛标记的股票。")
    lines += ["", "## 持仓明细", ""]
    for name, acc in report["accounts"].items():
        lines += [
            f"### {LABELS[name]}",
            "",
            "| 代码 | 名称 | 股数 | 成本价 | 最新价 | 盈亏 | 占比 |",
            "|---|---|---|---|---|---|---|",
        ]
        for row in acc["holdings"]:
            lines.append(
                f"| {row['symbol']} | {names.get(row['symbol'], '')} | {row['shares']} | "
                f"{row['cost_price']:.2f} | {row['last']:.2f} | {_pct(row['pnl_pct'])} | "
                f"{row['weight'] * 100:.1f}% |"
            )
        if acc["skipped_last_rotation"]:
            skipped = "、".join(
                f"{s['symbol']}{names.get(s['symbol'], '')}（{s['reason']}）"
                for s in acc["skipped_last_rotation"]
            )
            lines.append("")
            lines.append(f"上次换仓没买成：{skipped}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_report(
    session: Session,
    root: Path,
    *,
    capital: float,
    top_n: int,
    now: datetime,
) -> dict[str, Any] | None:
    lists = _lists(root)
    docs = [doc for docs in lists.values() for doc in docs]
    if not docs:
        return None
    start = min(date.fromisoformat(doc["as_of"]) for doc in docs)
    symbols = sorted({m["symbol"] for doc in docs for m in doc["members"][:top_n]})
    bars = _bars(session, symbols, start)
    if not bars.sessions:
        return None
    last = bars.sessions[-1]
    securities = pd.read_sql_query(
        select(Security.symbol, Security.name).where(Security.symbol.in_(symbols)),
        session.connection(),
    ).drop_duplicates("symbol")
    names = {str(r.symbol): str(r.name or "") for r in securities.itertuples()}
    accounts: dict[str, Any] = {}
    first_entry: date | None = None
    for name, candidate_lists in lists.items():
        result = simulate(candidate_lists, bars, capital=capital, top_n=top_n)
        account = result["account"]
        if not account.curve:
            continue
        first_entry = account.curve[0][0] if first_entry is None else first_entry
        value = account.curve[-1][1]
        holdings = []
        for symbol, holding in sorted(account.holdings.items(), key=lambda kv: kv[0]):
            last_px = bars.last_close(last, symbol) or 0.0
            market = holding.shares * last_px
            holdings.append(
                {
                    "symbol": symbol,
                    "shares": holding.shares,
                    "since": holding.since.isoformat(),
                    "cost_price": holding.cost / holding.shares,
                    "last": last_px,
                    "pnl_pct": market / holding.cost - 1 if holding.cost else None,
                    "weight": market / value if value else 0.0,
                }
            )
        plan = result["plan"]
        if plan:
            plan["sell_rows"] = [
                {"symbol": s, "shares": account.holdings[s].shares} for s in plan["sells"]
            ]
        last_rotation = max((t["date"] for t in account.trades), default=None)
        accounts[name] = {
            "value": value,
            "return": value / capital - 1,
            "cash": account.cash,
            "holdings": holdings,
            "trades": account.trades,
            "curve": [(d.isoformat(), round(v, 2)) for d, v in account.curve],
            "pending_sells": sorted(account.pending_sells),
            "skipped_last_rotation": [s for s in account.skipped if s["date"] == last_rotation],
            "flags": _flags(session, account.holdings),
            "plan": plan,
        }
    if not accounts or first_entry is None:
        return None
    local = now.astimezone(MARKET_TIMEZONE)
    return {
        "run_date": local.date().isoformat(),
        "generated_at": local.isoformat(timespec="minutes"),
        "last_session": last.isoformat(),
        "first_entry": first_entry.isoformat(),
        "capital": capital,
        "top_n": top_n,
        "index_return": benchmark_return(bars, first_entry, last),
        "names": names,
        "accounts": accounts,
    }


def run_stock_pick_actions(
    *,
    now: datetime | None = None,
    output_dir: str | Path | None = None,
    note_dir: str | Path | None = None,
    capital: float | None = None,
    top_n: int | None = None,
) -> dict[str, Any]:
    """Build the paper accounts, write the JSON snapshot and the vault note."""

    started = monotonic()
    settings = get_settings()
    current = (now or datetime.now(UTC)).astimezone(UTC)
    root = (
        Path(output_dir) if output_dir is not None else Path(settings.stock_pick_forward_test_dir)
    )
    notes = note_dir if note_dir is not None else settings.stock_pick_actions_note_dir
    capital = float(capital or settings.stock_pick_actions_capital)
    top_n = int(top_n or settings.stock_pick_actions_top_n)
    with get_session() as session:
        report = build_report(session, root, capital=capital, top_n=top_n, now=current)
    stats: dict[str, Any] = {"root": str(root), "note_dir": str(notes) if notes else None}
    if report is None:
        stats["skipped"] = "no lists or no bars yet"
        return stats
    json_path = root / "paper" / f"actions-{report['run_date']}.json"
    if json_path.exists():
        stats["json"] = {"path": str(json_path), "status": "exists"}
    else:
        stats["json"] = {
            "path": str(json_path),
            "sha256": write_json_create_only(json_path, report),
        }
    if notes:
        note_path = Path(notes) / f"{report['run_date']}.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text(render_note(report), encoding="utf-8")
        stats["note"] = str(note_path)
    stats["last_session"] = report["last_session"]
    stats["accounts"] = {
        name: {
            "value": round(acc["value"], 2),
            "return": round(acc["return"], 6),
            "holdings": len(acc["holdings"]),
            "plan": bool(acc["plan"]),
            "flags": len(acc["flags"]),
        }
        for name, acc in report["accounts"].items()
    }
    stats["index_return"] = report["index_return"]
    stats["duration_seconds"] = round(monotonic() - started, 2)
    return stats


def register_stock_pick_actions_job() -> None:
    register(
        JobSpec(
            name=JOB_NAME,
            func=run_stock_pick_actions,
            # After the 20:00 scoring on trading days; after the 09:30 list run at weekends.
            trigger=OrTrigger(
                [
                    CronTrigger(
                        day_of_week="mon-fri", hour=20, minute=30, timezone=MARKET_TIMEZONE
                    ),
                    CronTrigger(day_of_week="sat,sun", hour=10, minute=0, timezone=MARKET_TIMEZONE),
                ]
            ),
            enabled_key="stock_pick_actions_enabled",
            misfire_grace_time=3600,
        )
    )
