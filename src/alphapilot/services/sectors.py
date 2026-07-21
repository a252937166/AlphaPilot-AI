from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import isfinite
from threading import Lock
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphapilot.core.timeutil import iso_utc
from alphapilot.db.engine import get_session
from alphapilot.db.models import DailyBar, SectorConstituent, SectorForecast, SectorSnapshot
from alphapilot.futu.client import FutuClient, FutuClientError

FALLBACK_MAX_PLATES = 10
MAX_CONSTITUENTS_PER_PLATE = 30
SNAPSHOT_BATCH_SIZE = 400
FRESH_CONSTITUENT_DAYS = 7
_plate_cache_lock = Lock()
_plate_constituents: dict[str, dict[str, Any]] = {}


class SectorServiceError(RuntimeError):
    pass


SECTOR_FORECAST_HORIZONS = (5, 10, 20)
SECTOR_LIFECYCLES = ("boom", "rising", "decline", "bottoming", "recovery")


def _utc(value: datetime) -> datetime:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).astimezone(UTC)


def _number(value: object, default: float = 0.0) -> float:
    try:
        result = float(str(value))
    except (TypeError, ValueError):
        return default
    return result if isfinite(result) else default


def _forecast_row(row: SectorForecast, rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "plate_code": row.plate_code,
        "plate_name": row.plate_name,
        "trade_date": row.trade_date.isoformat(),
        "horizon": row.horizon,
        "score": row.score,
        "expected_excess": row.expected_excess,
        "win_rate": row.win_rate,
        "lifecycle": row.lifecycle,
        "rsi14": row.rsi14,
        "reversal_score": row.reversal_score,
        "model_version": row.model_version,
    }


def get_sector_forecast_view(
    session: Session,
    *,
    horizon: int,
    view: str = "forecast",
) -> dict[str, Any]:
    """Return a current, persisted sector forecast view without fabricating flow data."""

    if horizon not in SECTOR_FORECAST_HORIZONS:
        raise ValueError(f"unsupported sector forecast horizon: {horizon}")
    latest_forecast = session.scalar(select(func.max(SectorForecast.trade_date)))
    if latest_forecast is None:
        raise SectorServiceError("板块预测尚未生成，请先运行 sector_forecast 任务。")
    latest_benchmark = session.scalar(
        select(func.max(DailyBar.trade_date)).where(DailyBar.symbol == "SH.000001")
    )
    if latest_benchmark is not None and latest_benchmark != latest_forecast:
        raise SectorServiceError(
            "板块预测尚未与最新交易日日线同步，请先运行 sector_forecast 任务。"
        )

    rows = session.scalars(
        select(SectorForecast)
        .where(
            SectorForecast.trade_date == latest_forecast,
            SectorForecast.horizon == horizon,
        )
        .order_by(SectorForecast.score.desc(), SectorForecast.plate_code)
    ).all()
    if not rows:
        raise SectorServiceError(f"板块预测缺少 {horizon} 日截面，请重新运行任务。")
    model_versions = {row.model_version for row in rows}
    if len(model_versions) != 1:
        raise SectorServiceError("板块预测截面混入多个模型版本，已拒绝展示。")
    model_version = next(iter(model_versions))
    no_flow = model_version.endswith("-no-flow")
    fixed_universe_reason = (
        "历史成分有效期尚未落库，胜率为固定当前成分股宇宙回测，不代表无前视偏差的 PIT 结果。"
    )
    degraded_reason = fixed_universe_reason
    if no_flow:
        degraded_reason += " 板块资金流历史不足，当前使用无资金流模型；未补零或复制历史值。"

    selected = list(rows)
    available = True
    reason: str | None = None
    if view == "lifecycle":
        selected.sort(key=lambda row: (row.lifecycle is None, row.lifecycle or "", -row.score))
    elif view == "overbought":
        selected = [row for row in rows if row.rsi14 is not None and row.rsi14 > 70]
        selected.sort(key=lambda row: (-float(row.rsi14 or 0.0), row.plate_code))
    elif view == "reversal":
        selected = [row for row in rows if row.reversal_score is not None]
        selected.sort(key=lambda row: (-float(row.reversal_score or 0.0), row.plate_code))
        if no_flow:
            available = False
            reason = "资金流历史尚不足以计算 flow_turn_z，反转排行将在数据积累完成后开放。"
    elif view != "forecast":
        raise ValueError(f"unsupported sector forecast view: {view}")

    payload: dict[str, Any] = {
        "as_of": latest_forecast.isoformat(),
        "horizon": horizon,
        "model_version": model_version,
        "flow_mode": "no-flow" if no_flow else "full",
        "backtest_scope": "fixed-current-membership",
        "degraded_reason": degraded_reason,
        "available": available,
        "count": len(selected),
        "rows": [_forecast_row(row, rank) for rank, row in enumerate(selected, 1)],
    }
    if reason is not None:
        payload["reason"] = reason
    if view == "lifecycle":
        counts = {name: 0 for name in SECTOR_LIFECYCLES}
        counts["unclassified"] = 0
        for row in rows:
            counts[row.lifecycle or "unclassified"] += 1
        payload["counts"] = counts
    return payload


def _db_plate_constituents(session: Session) -> dict[str, dict[str, Any]]:
    cutoff = datetime.now(UTC) - timedelta(days=FRESH_CONSTITUENT_DAYS)
    latest = session.scalar(select(func.max(SectorConstituent.refreshed_at)))
    if not isinstance(latest, datetime):
        return {}
    if _utc(latest) < cutoff:
        return {}
    rows = session.scalars(
        select(SectorConstituent)
        .where(SectorConstituent.refreshed_at >= cutoff)
        .order_by(SectorConstituent.plate_code, SectorConstituent.symbol)
    ).all()
    plates: dict[str, dict[str, Any]] = {}
    for row in rows:
        info = plates.setdefault(
            row.plate_code,
            {"plate_name": row.plate_name, "constituents": []},
        )
        info["constituents"].append({"code": row.symbol, "name": row.name or row.symbol})
    return plates


def _load_plate_constituents(
    client: FutuClient,
    session: Session | None = None,
) -> dict[str, dict[str, Any]]:
    if session is not None:
        persisted = _db_plate_constituents(session)
    else:
        with get_session() as local_session:
            persisted = _db_plate_constituents(local_session)
    if persisted:
        return persisted

    with _plate_cache_lock:
        if _plate_constituents:
            return _plate_constituents
        plates = client.quote_call_raw("get_plate_list", args=["SH", "INDUSTRY"])
        if not isinstance(plates, pd.DataFrame) or plates.empty:
            raise SectorServiceError("Futu returned no industry plate list.")
        selected = plates.head(FALLBACK_MAX_PLATES)
        for record in selected.to_dict(orient="records"):
            plate_code = str(record.get("code"))
            plate_name = str(record.get("plate_name"))
            try:
                stocks = client.quote_call_raw("get_plate_stock", args=[plate_code])
            except FutuClientError:
                continue
            if not isinstance(stocks, pd.DataFrame) or stocks.empty:
                continue
            constituents = [
                {"code": str(row.get("code")), "name": str(row.get("stock_name"))}
                for row in stocks.head(MAX_CONSTITUENTS_PER_PLATE).to_dict(orient="records")
            ]
            _plate_constituents[plate_code] = {
                "plate_name": plate_name,
                "constituents": constituents,
            }
        if not _plate_constituents:
            raise SectorServiceError("No plate constituents could be loaded from Futu.")
        return _plate_constituents


def _sample_snapshot(client: FutuClient, codes: list[str]) -> pd.DataFrame:
    """Fetch snapshot batches and derive change from last/previous close."""

    unique_codes = list(dict.fromkeys(codes))
    frames: list[pd.DataFrame] = []
    for offset in range(0, len(unique_codes), SNAPSHOT_BATCH_SIZE):
        snapshot = client.quote_call_raw(
            "get_market_snapshot",
            args=[unique_codes[offset : offset + SNAPSHOT_BATCH_SIZE]],
        )
        if not isinstance(snapshot, pd.DataFrame) or snapshot.empty:
            raise SectorServiceError("Futu snapshot returned no rows for sampling.")
        frames.append(snapshot)
    if not frames:
        raise SectorServiceError("Futu snapshot returned no rows for sampling.")
    snapshot = pd.concat(frames, ignore_index=True)
    snapshot = snapshot.copy()
    if "prev_close_price" in snapshot.columns:
        last = pd.to_numeric(snapshot["last_price"], errors="coerce")
        prev_close = pd.to_numeric(snapshot["prev_close_price"], errors="coerce")
        snapshot["change_pct"] = (last / prev_close - 1) * 100
    else:
        snapshot["change_pct"] = 0.0
    return snapshot


def compute_sector_strength(
    client: FutuClient,
    session: Session | None = None,
) -> list[dict[str, Any]]:
    """Rank all cached industry plates using their 30 most-traded members."""

    plates = _load_plate_constituents(client, session)
    all_codes: list[str] = []
    for info in plates.values():
        all_codes.extend(item["code"] for item in info["constituents"])
    all_codes = list(dict.fromkeys(all_codes))

    snapshot = _sample_snapshot(client, all_codes)
    quotes = {str(row.get("code")): row for row in snapshot.to_dict(orient="records")}

    results: list[dict[str, Any]] = []
    for plate_code, info in plates.items():
        rows = [quotes[item["code"]] for item in info["constituents"] if item["code"] in quotes]
        rows.sort(key=lambda row: _number(row.get("turnover")), reverse=True)
        rows = rows[:MAX_CONSTITUENTS_PER_PLATE]
        if not rows:
            continue
        changes = [_number(row.get("change_pct")) for row in rows]
        turnovers = [_number(row.get("turnover")) for row in rows]
        up_count = sum(1 for value in changes if value > 0)
        leader = max(rows, key=lambda row: _number(row.get("change_pct")))
        avg_change = sum(changes) / len(changes)
        breadth = up_count / len(changes)
        # 0-10 heuristic strength blending move and breadth.
        strength = max(0.0, min(10.0, 5 + avg_change * 1.2 + (breadth - 0.5) * 4))
        results.append(
            {
                "plate_code": plate_code,
                "plate_name": info["plate_name"],
                "sampled": len(rows),
                "avg_change_pct": round(avg_change, 3),
                "up_ratio": round(breadth, 3),
                "turnover": sum(turnovers),
                "strength": round(strength, 2),
                "leader_code": str(leader.get("code")),
                "leader_name": str(leader.get("name") or leader.get("stock_name") or ""),
                "leader_change_pct": round(_number(leader.get("change_pct")), 3),
            }
        )
    results.sort(key=lambda item: item["strength"], reverse=True)
    for rank, item in enumerate(results, 1):
        item["rank"] = rank
    return results


def market_breadth_from_sample(client: FutuClient) -> dict[str, Any]:
    """Advance/decline breadth computed over the sector sample universe."""
    plates = _load_plate_constituents(client)
    codes: list[str] = []
    for info in plates.values():
        codes.extend(item["code"] for item in info["constituents"])
    codes = list(dict.fromkeys(codes))[:SNAPSHOT_BATCH_SIZE]
    snapshot = _sample_snapshot(client, codes)
    changes = pd.to_numeric(snapshot["change_pct"], errors="coerce").dropna()
    return {
        "sample_size": len(changes),
        "advancers": int((changes > 0).sum()),
        "decliners": int((changes < 0).sum()),
        "unchanged": int((changes == 0).sum()),
        "avg_change_pct": round(float(changes.mean()), 3),
        "note": "样本宽度：基于板块抽样股票池，非全市场统计。",
    }


def get_sector_strength(
    session: Session,
    client: FutuClient,
    *,
    max_age_seconds: int = 150,
    refresh: bool = False,
) -> dict[str, Any]:
    latest = session.scalars(
        select(SectorSnapshot).order_by(SectorSnapshot.as_of.desc()).limit(1)
    ).first()
    now = datetime.now(UTC)
    if (
        latest is not None
        and not refresh
        and latest.as_of.replace(tzinfo=latest.as_of.tzinfo or UTC)
        > now - timedelta(seconds=max_age_seconds)
    ):
        return {"as_of": iso_utc(latest.as_of), "cached": True, "sectors": latest.payload}

    try:
        sectors = compute_sector_strength(client, session)
    except (FutuClientError, SectorServiceError) as exc:
        if latest is not None:
            return {
                "as_of": iso_utc(latest.as_of),
                "cached": True,
                "stale": True,
                "error": str(exc),
                "sectors": latest.payload,
            }
        raise SectorServiceError(f"Sector strength unavailable: {exc}") from exc

    session.add(SectorSnapshot(as_of=now, payload=sectors, source="futu"))
    return {"as_of": now.isoformat(), "cached": False, "sectors": sectors}
