#!/usr/bin/env python3
"""Advisory daily AI market brief → Obsidian. Interim bridge until P4.3.

Reads the local database strictly read-only, summarizes the last 24h of
audited news plus persisted market state through the project-configured LLM,
and writes a clearly-labeled advisory note into the owner's Obsidian vault.

Boundaries (deliberate): no database writes, no jobs-registry entry, no
news_events rows, no recommendations — this is the automated version of a
hand-written evening brief and is scheduled for retirement once the P4.3
annotated recommendation pipeline ships.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE = PROJECT_ROOT / "data" / "alphapilot.db"
VAULT_DIR = Path.home() / "Documents" / "Obsidian Vault" / "AI" / "每日研判"
SHANGHAI = ZoneInfo("Asia/Shanghai")
AUDITED_BAR_SOURCES = ("akshare", "baostock", "futu", "futu-close", "sina")
NEWS_WINDOW_HOURS = 24
MAX_NEWS_ROWS = 260
LLM_TIMEOUT_SECONDS = 90.0
LLM_MAX_TOKENS = 1800

SYSTEM_PROMPT = (
    "你是 AlphaPilot 的市场研判助理。仅依据用户提供的结构化数据撰写当日中文研判，"
    "禁止编造数据中不存在的数字、涨跌幅或消息。输出 Markdown，小节固定为："
    "## 市场底色 / ## 资讯主题聚类 / ## 个股事件（🟢正面 🔴负面 🟡中性存疑）/ "
    "## Top50 交叉观察 / ## 下一交易日关注 / ## 边界与免责。"
    "研判必须克制：给出解读与关注点，不给买卖指令；在边界小节明确本报告为"
    "advisory、事件未经评测门校验、因子模型的历史局限（composite-v3 未转正）。"
)


def _env() -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = PROJECT_ROOT / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip("'\"")
    return values


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _latest_trading_date(connection: sqlite3.Connection) -> str | None:
    placeholders = ",".join("?" * len(AUDITED_BAR_SOURCES))
    row = connection.execute(
        f"SELECT MAX(trade_date) FROM daily_bars WHERE source IN ({placeholders})",
        AUDITED_BAR_SOURCES,
    ).fetchone()
    return row[0] if row and row[0] else None


def _collect(connection: sqlite3.Connection) -> dict[str, object]:
    cutoff = (datetime.now(UTC) - timedelta(hours=NEWS_WINDOW_HOURS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    news = connection.execute(
        "SELECT source, symbol, title FROM news_items "
        "WHERE available_time >= ? ORDER BY available_time DESC LIMIT ?",
        (cutoff, MAX_NEWS_ROWS),
    ).fetchall()

    sentiment_row = connection.execute(
        "SELECT ts, score, label, details FROM market_sentiment ORDER BY id DESC LIMIT 1"
    ).fetchone()
    sentiment: dict[str, object] = {}
    if sentiment_row:
        details = json.loads(sentiment_row[3] or "{}")
        components = details.get("components", {})
        sentiment = {
            "as_of_utc": sentiment_row[0],
            "score": round(float(sentiment_row[1]), 1),
            "label": sentiment_row[2],
            "advancers": components.get("breadth", {}).get("advancers"),
            "decliners": components.get("breadth", {}).get("decliners"),
            "limit_up": components.get("limitup", {}).get("limit_up"),
            "broken_boards": components.get("limitup", {}).get("broken_boards"),
            "volatility_risk_percentile": components.get("volatility", {}).get(
                "risk_percentile"
            ),
        }

    regime_row = connection.execute(
        "SELECT symbol, regime, confidence, as_of FROM market_regime_states "
        "ORDER BY as_of DESC LIMIT 1"
    ).fetchone()
    regime = (
        {
            "symbol": regime_row[0],
            "regime": regime_row[1],
            "confidence": round(float(regime_row[2]), 2),
            "as_of": regime_row[3],
        }
        if regime_row
        else {}
    )

    screening_row = connection.execute(
        "SELECT id, created_at, model_version, candidates FROM screening_runs "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    screening: dict[str, object] = {}
    top_symbols: list[str] = []
    if screening_row:
        candidates = json.loads(screening_row[3])
        top_symbols = [str(item.get("symbol")) for item in candidates]
        screening = {
            "run_id": screening_row[0],
            "as_of_utc": screening_row[1],
            "model_version": screening_row[2],
            "top10": [
                {
                    "symbol": item.get("symbol"),
                    "score": round(float(item.get("score", 0.0)), 2),
                    "high_volatility": item.get("high_volatility"),
                    "low_liquidity": item.get("low_liquidity"),
                }
                for item in candidates[:10]
            ],
            "high_volatility_count": sum(
                1 for item in candidates if item.get("high_volatility")
            ),
            "low_liquidity_count": sum(
                1 for item in candidates if item.get("low_liquidity")
            ),
        }

    news_symbols = {row[1] for row in news if row[1]}
    overlap = [symbol for symbol in top_symbols if symbol in news_symbols]

    return {
        "news_window_hours": NEWS_WINDOW_HOURS,
        "news_count": len(news),
        "news": [
            {"source": row[0], "symbol": row[1], "title": row[2]} for row in news
        ],
        "sentiment": sentiment,
        "market_regime": regime,
        "screening": screening,
        "top50_news_overlap": overlap,
    }


def _llm_brief(env: dict[str, str], payload: dict[str, object]) -> str:
    base = env.get("ALPHAPILOT_LLM_BASE_URL", "").rstrip("/")
    model = env.get("ALPHAPILOT_LLM_MODEL", "")
    if not base or not model:
        raise RuntimeError("LLM base URL/model is not configured in .env")
    key = env.get("ALPHAPILOT_LLM_API_KEY", "")
    response = httpx.post(
        f"{base}/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "以下是截至当前的落库数据（JSON）。请输出当日研判：\n"
                        + json.dumps(payload, ensure_ascii=False)
                    ),
                },
            ],
            "max_tokens": LLM_MAX_TOKENS,
            "temperature": 0.3,
        },
        headers={"Authorization": f"Bearer {key}"} if key else {},
        timeout=LLM_TIMEOUT_SECONDS,
        trust_env=False,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LLM returned an empty brief")
    return content.strip()


def _write_note(
    brief_date: str,
    body: str,
    payload: dict[str, object],
    *,
    llm_error: str | None,
) -> Path:
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    target = VAULT_DIR / f"{brief_date}.md"
    generated_at = datetime.now(SHANGHAI).isoformat(timespec="seconds")
    screening = payload.get("screening") or {}
    lines = [
        "---",
        f"title: AI 每日研判 {brief_date}",
        f"date: {brief_date}",
        "tags: [alphapilot, ai-brief, advisory]",
        "advisory: true",
        f"generated_at: {generated_at}",
        "---",
        "",
        f"# AI 每日研判 · {brief_date}",
        "",
        "> [!warning] Advisory",
        "> 自动生成的参考研判：事件未经金标准评测门，不构成投资建议；",
        "> P4.3 带评测的推荐管道上线后本简报退役。",
        "",
    ]
    if llm_error is None:
        lines.append(body)
    else:
        lines.extend(
            [
                "## LLM 摘要生成失败（如实记录）",
                "",
                f"`{llm_error}`",
                "",
                "以下为当日原始数据附录，供人工判读。",
            ]
        )
    lines.extend(
        [
            "",
            "---",
            "",
            "## 数据附录",
            "",
            f"- 资讯窗口：近 {payload['news_window_hours']} 小时，共 "
            f"{payload['news_count']} 条（截断上限 {MAX_NEWS_ROWS}）",
            f"- 选股 run：#{screening.get('run_id', '—')}"
            f"（{screening.get('model_version', '—')}），高波动 "
            f"{screening.get('high_volatility_count', '—')}/50、薄流动 "
            f"{screening.get('low_liquidity_count', '—')}/50",
            f"- Top50 ∩ 资讯个股：{payload['top50_news_overlap'] or '无'}",
            f"- 大盘/情绪：{payload.get('market_regime')} / "
            f"{(payload.get('sentiment') or {}).get('label', '—')}"
            f"（{(payload.get('sentiment') or {}).get('score', '—')}）",
        ]
    )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the advisory daily AI brief.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even when today is not the latest audited trading date.",
    )
    arguments = parser.parse_args(argv)

    today = datetime.now(SHANGHAI).date().isoformat()
    with _connect() as connection:
        latest = _latest_trading_date(connection)
        if latest != today and not arguments.force:
            print(
                json.dumps(
                    {
                        "skipped": "non_trading_day",
                        "today": today,
                        "latest_audited_trade_date": latest,
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        payload = _collect(connection)

    llm_error: str | None = None
    body = ""
    try:
        body = _llm_brief(_env(), payload)
    except Exception as exc:  # honest degradation: keep the data, note the miss
        llm_error = f"{type(exc).__name__}: {exc}"

    target = _write_note(today, body, payload, llm_error=llm_error)
    print(
        json.dumps(
            {
                "written": str(target),
                "news_count": payload["news_count"],
                "llm_ok": llm_error is None,
                "llm_error": llm_error,
            },
            ensure_ascii=False,
        )
    )
    return 0 if llm_error is None else 2


if __name__ == "__main__":
    raise SystemExit(main())
