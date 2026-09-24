"""Weekly candidate J (jev picks from the A and B shortlists) and its scoring.

Weekend runs build the week's J list once both A and B lists exist; every run scores
matured J lists at 5 and 20 sessions and tallies the A2 verdict. Files are create-only
under ``<stock_pick_forward_test_dir>/lists/J`` and ``scores/J``.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger

from alphapilot.core.config import get_settings
from alphapilot.db.engine import get_session
from alphapilot.jobs.registry import JobSpec, register
from alphapilot.llm.typesafe import JevClient
from alphapilot.services.stock_pick_jev import (
    ask_all,
    build_j_list,
    build_states,
    j_tally,
    pool_from_lists,
)
from alphapilot.services.stock_picks import HORIZONS, score_pick_list, write_json_create_only

JOB_NAME = "stock_pick_jev"
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
FIRST_WEEK = "2026-W39"  # amendment A2: the forward window starts with ISO week 39


def _latest(root: Path, name: str) -> dict[str, dict[str, Any]]:
    docs = {}
    for path in sorted((root / "lists" / name).glob(f"{name}-*.json")):
        doc = json.loads(path.read_bytes())
        docs[doc["iso_week"]] = doc
    return docs


def run_stock_pick_jev(
    *,
    now: datetime | None = None,
    output_dir: str | Path | None = None,
    generate: bool | None = None,
    client: Any = None,
    first_week: str = FIRST_WEEK,
) -> dict[str, Any]:
    started = monotonic()
    settings = get_settings()
    current = (now or datetime.now(UTC)).astimezone(UTC)
    local = current.astimezone(MARKET_TIMEZONE)
    root = (
        Path(output_dir) if output_dir is not None else Path(settings.stock_pick_forward_test_dir)
    )
    do_generate = (local.weekday() >= 5) if generate is None else generate
    stats: dict[str, Any] = {"generated": None, "scored": [], "tally": None}
    a_lists, b_lists, j_lists = _latest(root, "A"), _latest(root, "B"), _latest(root, "J")
    if do_generate:
        weeks = sorted(w for w in a_lists if w in b_lists and w >= first_week)
        week = weeks[-1] if weeks else None
        if week is None:
            stats["generated"] = {"status": "no A and B lists for a J week yet"}
        elif week in j_lists:
            stats["generated"] = {"status": "exists", "iso_week": week}
        else:
            a_doc, b_doc = a_lists[week], b_lists[week]
            pool = pool_from_lists(a_doc, b_doc)
            owned = client is None
            asker = client or JevClient(settings.jev_api_key, settings.jev_model)
            try:
                with get_session() as session:
                    states = build_states(
                        session, date.fromisoformat(a_doc["as_of"]), pool, a_doc, b_doc
                    )
                answers = ask_all(asker, states)
            finally:
                if owned:
                    asker.close()
            doc = build_j_list(
                a_doc, b_doc, states, answers, model=asker.model, generated_at=current
            )
            path = root / "lists" / "J" / f"J-{week}-{doc['as_of'].replace('-', '')}.json"
            digest = write_json_create_only(path, doc)
            tokens = sum(int(a.get("usage", {}).get("input_tokens", 0)) for a in answers.values())
            stats["generated"] = {
                "status": "written",
                "iso_week": week,
                "path": str(path),
                "sha256": digest,
                "pool": len(pool),
                "missing": len(doc["missing"]),
                "input_tokens": tokens,
                "picks_top5": doc["top_decile_symbols"][:5],
            }
            j_lists[week] = doc
    with get_session() as session:
        for week, doc in sorted(j_lists.items()):
            for horizon in HORIZONS:
                stem = f"J-{week}-{doc['as_of'].replace('-', '')}"
                path = root / "scores" / "J" / f"{stem}-h{horizon}.json"
                if path.exists():
                    continue
                result = score_pick_list(session, doc, horizon)
                if result is None:
                    continue
                market = (
                    score_pick_list(session, a_lists[week], horizon) if week in a_lists else None
                )
                if market is not None:
                    picks = result["list_top_decile"]
                    result["market_relative"] = {
                        "universe_median_return": market["median_return"],
                        "picks_mean_return": picks["mean_return"],
                        "picks_excess_vs_market": round(
                            picks["mean_return"] - market["median_return"], 6
                        ),
                    }
                digest = write_json_create_only(path, result)
                stats["scored"].append({"path": str(path), "sha256": digest, "horizon": horizon})
    matured = [
        json.loads(p.read_bytes()) for p in sorted((root / "scores" / "J").glob("J-*-h5.json"))
    ]
    stats["tally"] = j_tally(matured)
    stats["duration_seconds"] = round(monotonic() - started, 2)
    return stats


def register_stock_pick_jev_job() -> None:
    register(
        JobSpec(
            name=JOB_NAME,
            func=run_stock_pick_jev,
            # After the 09:30 A/B list run at weekends; after the 20:00 scoring on trading days.
            trigger=OrTrigger(
                [
                    CronTrigger(day_of_week="sat,sun", hour=9, minute=45, timezone=MARKET_TIMEZONE),
                    CronTrigger(day_of_week="mon-fri", hour=20, minute=5, timezone=MARKET_TIMEZONE),
                ]
            ),
            enabled_key="stock_pick_jev_enabled",
            misfire_grace_time=3600,
        )
    )
