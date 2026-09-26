"""Weekly pre-registered stock-pick lists and their forward-test scores.

Files, not tables: every list and every score is a create-only JSON document
under ``settings.stock_pick_forward_test_dir`` (default ``data/stock_picks``),
so the forward record cannot be rewritten by a later run. A run generates the
week's lists only on Saturday or Sunday (Asia/Shanghai), when the week's last
session is known, or when an explicit ``as_of`` session is given; every run
scores whatever has matured and tallies the frozen verdict per candidate. The
rules themselves live in ``services.stock_picks`` and are frozen by evidence
files outside the repository.
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
from alphapilot.services.bar_coverage import horizon_ready, session_coverage
from alphapilot.services.stock_picks import (
    CANDIDATES,
    HORIZONS,
    build_pick_list,
    compute_signals,
    forward_test_tally,
    iso_week_key,
    latest_session,
    load_panel,
    score_pick_list,
    write_json_create_only,
)

JOB_NAME = "stock_pick_forward_test"
MARKET_TIMEZONE = ZoneInfo("Asia/Shanghai")
MAX_STALE_DAYS = 7


def _lists_dir(root: Path, name: str) -> Path:
    return root / "lists" / name


def _scores_dir(root: Path, name: str) -> Path:
    return root / "scores" / name


def run_stock_pick_forward_test(
    *,
    now: datetime | None = None,
    as_of: date | None = None,
    generate: bool | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Generate this week's lists (weekend or explicit as_of), score matured lists, tally."""

    started = monotonic()
    current = (now or datetime.now(UTC)).astimezone(UTC)
    local = current.astimezone(MARKET_TIMEZONE)
    root = (
        Path(output_dir)
        if output_dir is not None
        else Path(get_settings().stock_pick_forward_test_dir)
    )
    stats: dict[str, Any] = {
        "output_dir": str(root),
        "run_local_time": local.strftime("%Y-%m-%d %H:%M %Z"),
        "as_of": None,
        "iso_week": None,
        "generated": {},
        "generate_skipped": None,
        "scored": [],
        "deferred": [],
        "tallies": {},
    }
    coverage_cache: dict[date, tuple[bool, dict[str, Any]]] = {}
    with get_session() as session:
        if as_of is not None:
            target: date | None = as_of
            do_generate = True if generate is None else generate
        else:
            target = latest_session(session, local.date())
            do_generate = (local.weekday() >= 5) if generate is None else generate
            if target is None or (local.date() - target).days > MAX_STALE_DAYS:
                do_generate = False
                stats["generate_skipped"] = f"no session within the last {MAX_STALE_DAYS} days"
        if target is not None:
            stats["as_of"] = target.isoformat()
            stats["iso_week"] = iso_week_key(target)
        if do_generate and target is not None:
            complete, details = session_coverage(session, target, now=current)
            if not complete:
                # A list built on part of the market would be frozen that way; wait for the bars.
                do_generate = False
                stats["generate_skipped"] = {"reason": "incomplete bars", **details}
        if do_generate and target is not None:
            week = iso_week_key(target)
            pending = {}
            for name, spec in CANDIDATES.items():
                if list(_lists_dir(root, name).glob(f"{name}-{week}-*.json")):
                    stats["generated"][name] = {"status": "exists"}
                else:
                    pending[name] = spec
            if pending:
                panel = load_panel(session, target, now=current)
                signals = compute_signals(panel)
                for name, spec in pending.items():
                    document = build_pick_list(panel, spec, generated_at=current, signals=signals)
                    path = _lists_dir(root, name) / f"{name}-{week}-{target:%Y%m%d}.json"
                    digest = write_json_create_only(path, document)
                    stats["generated"][name] = {
                        "status": "written",
                        "path": str(path),
                        "sha256": digest,
                        "universe_n": document["universe_n"],
                        "top_decile_n": document["top_decile_n"],
                        "top5": [m["symbol"] for m in document["members"][:5]],
                    }
        elif stats["generate_skipped"] is None:
            stats["generate_skipped"] = (
                "lists are generated on weekend runs or with an explicit as_of"
            )
        for name in CANDIDATES:
            for list_path in sorted(_lists_dir(root, name).glob(f"{name}-*.json")):
                document = json.loads(list_path.read_bytes())
                for horizon in HORIZONS:
                    score_path = _scores_dir(root, name) / f"{list_path.stem}-h{horizon}.json"
                    if score_path.exists():
                        continue
                    ready, details = horizon_ready(
                        session,
                        date.fromisoformat(document["as_of"]),
                        horizon,
                        cache=coverage_cache,
                        now=current,
                    )
                    if ready is None:
                        continue
                    if not ready:
                        stats["deferred"].append(
                            {"list": list_path.stem, "horizon": horizon, **details}
                        )
                        continue
                    result = score_pick_list(session, document, horizon)
                    if result is None:
                        continue
                    digest = write_json_create_only(score_path, result)
                    stats["scored"].append(
                        {
                            "path": str(score_path),
                            "sha256": digest,
                            "candidate": name,
                            "as_of": document["as_of"],
                            "horizon": horizon,
                            "top_hit": result["top_bin"]["hit_rate"],
                            "top_excess": result["top_bin"]["mean_excess"],
                        }
                    )
        for name in CANDIDATES:
            matured = [
                json.loads(path.read_bytes())
                for path in sorted(_scores_dir(root, name).glob(f"{name}-*-h5.json"))
            ]
            stats["tallies"][name] = forward_test_tally(matured)
    changed = bool(stats["scored"]) or any(
        item.get("status") == "written" for item in stats["generated"].values()
    )
    if changed:
        summary_path = root / "summary" / f"summary-{current:%Y%m%dT%H%M%SZ}.json"
        write_json_create_only(summary_path, stats)
        stats["summary_path"] = str(summary_path)
    stats["duration_seconds"] = round(monotonic() - started, 2)
    return stats


def _trigger() -> OrTrigger:
    return OrTrigger(
        [
            CronTrigger(day_of_week="sat,sun", hour=9, minute=30, timezone=MARKET_TIMEZONE),
            CronTrigger(day_of_week="mon-fri", hour=20, minute=0, timezone=MARKET_TIMEZONE),
        ]
    )


def register_stock_pick_forward_test_job() -> None:
    register(
        JobSpec(
            name=JOB_NAME,
            func=run_stock_pick_forward_test,
            trigger=_trigger(),
            enabled_key="stock_pick_forward_test_enabled",
            misfire_grace_time=3600,
        )
    )
